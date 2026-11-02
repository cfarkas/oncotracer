"""GPU admission shared by native workers, Nextflow helpers and Docker.

Standard-library only: importing this file never initializes CUDA. A per-user,
per-UUID file lock serializes GPU stages across projects. CPU work remains
parallel. Framework-specific limits must also be applied by the caller.
"""
from contextlib import contextmanager
import csv
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MIB = 1024 ** 2
POLICY = 'reserved-vram-v1'
DEFAULT_QUEUE_TIMEOUT = 120 * 60 * 60
_DEFAULT_TIMEOUT = object()


def wait_hours(value):
    """A finite positive duration; fractions of an hour are supported."""
    try:
        hours = float(value)
    except (TypeError, ValueError, OverflowError):
        hours = float('nan')
    if isinstance(value, bool) or not math.isfinite(hours * 3600) or hours <= 0:
        raise ValueError('gpu_wait_hours must be a finite positive number of hours')
    return hours


def add_wait_argument(parser):
    parser.add_argument('--gpu-wait-hours', type=wait_hours, metavar='HOURS',
                        help='maximum wait for GPU admission per stage (default: 120 hours; accepts fractions); does not limit running tasks')


@contextmanager
def queue_wait(config, override=None):
    """Apply this run's queue limit to workers, including child processes."""
    hours = wait_hours(config.get('gpu_wait_hours', 120) if override is None else override)
    key = 'ONCOTRACER_GPU_WAIT_HOURS'
    previous = os.environ.get(key)
    os.environ[key] = format(hours, '.17g')
    try:
        yield hours
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def enabled(config, override=None):
    value = config.get('gpu_usage', config.get('methylation_gpu', False)) if override is None else override
    if type(value) is not bool:
        raise ValueError('gpu_usage must be true or false')
    return value


def inventory():
    executable = shutil.which('nvidia-smi')
    if not executable:
        return []
    try:
        result = subprocess.run([executable, '--query-gpu=index,uuid,name,memory.total,memory.free',
                                 '--format=csv,noheader,nounits'], capture_output=True,
                                text=True, timeout=3, check=False)
        if result.returncode:
            return []
        devices = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if not row:
                continue
            index, uuid, name, total, free = [v.strip() for v in row]
            if not re.fullmatch(r'GPU-[A-Za-z0-9-]+', uuid):
                continue
            total, free = int(total), int(free)
            if total <= 0 or not 0 <= free <= total:
                continue
            visible = True
            for key in ('CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES', 'ONCOTRACER_GPU_DEVICES'):
                if key not in os.environ or os.environ[key] == 'all':
                    continue
                # CDI injects the selected devices, then sets this legacy
                # injection variable to void to prevent a second injection.
                # CUDA's mask and our explicit UUID selection still apply.
                if (key == 'NVIDIA_VISIBLE_DEVICES' and os.environ[key] == 'void'
                        and os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') == 'docker'):
                    continue
                ids = [v.strip() for v in os.environ[key].split(',')]
                visible &= index in ids or any(uuid.startswith(v) for v in ids if v.startswith('GPU-'))
            if not visible:
                continue
            reserve = max(4096, (total + 4) // 5)
            # Reserve space on top of existing use (desktop and other applications).
            budget = max(0, free - reserve)
            devices.append(dict(index=int(index), uuid=uuid, name=name,
                                total_mb=total, free_mb=free, reserve_mb=reserve, budget_mb=budget))
        return devices
    except (OSError, subprocess.SubprocessError, ValueError, csv.Error):
        return []


def options():
    devices = inventory()
    return dict(detected=bool(devices), devices=devices, policy=POLICY,
                queue_timeout_hours=DEFAULT_QUEUE_TIMEOUT // 3600,
                note='GPU stages share one queue per GPU across this user\'s projects. '
                     'Requests use arrival order and wait up to 120 hours by default. '
                     'At least 4 GiB or 20% of VRAM is reserved in addition to current use. '
                     'CPU alignment, CNA, GLIMPSE2 and FFPErase remain on CPU.')


def lock_directory(*, create=True):
    path = Path(os.environ.get('ONCOTRACER_GPU_LOCK_DIR', f'/tmp/oncotracer-gpu-{os.getuid()}'))
    if not path.is_absolute():
        raise RuntimeError('GPU lock directory must be absolute')
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('GPU lock directory must be a private directory owned by the current user')
    return path


def _open_private(path, flags=os.O_RDWR | os.O_CREAT):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise RuntimeError('GPU lock is not a private regular file')
    return fd


def _open_lock(directory, uuid):
    return _open_private(directory / (uuid + '.lock'))


@contextmanager
def _queue_guard(directory):
    fd = _open_lock(directory, 'queue')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _ticket(directory, label, devices, minimum_mb):
    """A locked ticket survives PID namespaces and is reclaimable after a crash."""
    path = fd = None
    try:
        with _queue_guard(directory) as counter:
            raw = os.read(counter, 128).strip()
            if raw and not raw.isdigit():
                raise RuntimeError('Invalid GPU queue sequence')
            existing = [int(p.name.split('-')[1]) for p in directory.glob('wait-*.json')
                        if re.fullmatch(r'wait-\d{20}-.+\.json', p.name)]
            sequence = max([int(raw or b'0'), *existing]) + 1
            if sequence >= 10**20:
                raise RuntimeError('GPU queue sequence exhausted')
            os.lseek(counter, 0, os.SEEK_SET)
            os.ftruncate(counter, 0)
            os.write(counter, str(sequence).encode())
            fd, name = tempfile.mkstemp(prefix=f'wait-{sequence:020d}-', suffix='.json', dir=directory)
            path = Path(name)
            fcntl.flock(fd, fcntl.LOCK_EX)
            record = dict(label=label, minimum_mb=minimum_mb,
                          devices=[d['uuid'] for d in devices if d['total_mb']-d['reserve_mb'] >= minimum_mb])
            os.write(fd, json.dumps(record).encode())
        yield path
    finally:
        if fd is not None:
            try:
                with _queue_guard(directory):
                    path.unlink(missing_ok=True)
            finally:
                os.close(fd)


def _older_waiters(directory, ticket, uuid):
    """Called under queue.lock; a released ticket lock proves its owner exited."""
    ahead = 0
    for path in sorted(directory.glob('wait-*.json')):
        if path.name >= ticket.name:
            break
        fd = _open_private(path, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                record = json.loads(os.read(fd, 65536))
                if uuid in record['devices']:
                    ahead += 1
            else:
                path.unlink()  # Dead waiter; never trust host/container PID numbers.
        finally:
            os.close(fd)
    return ahead


@contextmanager
def lease(label, *, minimum_mb=8192, cancel=None, poll_seconds=1, timeout=_DEFAULT_TIMEOUT):
    """Wait for a compatible GPU, recheck free VRAM under its lock, then reserve.

    FIFO among compatible requests. Normal analysis defaults to 120 hours;
    bounded diagnostic callers can explicitly supply a shorter timeout. Cancellation,
    unavailable hardware and insufficient total capacity remain explicit.
    """
    if timeout is _DEFAULT_TIMEOUT:
        timeout = wait_hours(os.environ.get('ONCOTRACER_GPU_WAIT_HOURS', 120)) * 3600
    directory = lock_directory()
    started = time.monotonic()
    last_notice = -60
    acquired = None
    try:
        devices = inventory()
        if not devices:
            raise RuntimeError('GPU requested but no visible NVIDIA GPU is available; choose GPU usage No or repair the driver/runtime')
        if not any(d['total_mb'] - d['reserve_mb'] >= minimum_mb for d in devices):
            raise RuntimeError(f'{label} requires {minimum_mb / 1024:g} GiB of GPU memory after the desktop reserve')
        with _ticket(directory, label, devices, minimum_mb) as ticket:
            while acquired is None:
              if cancel is not None and cancel.is_set():
                  raise InterruptedError('Stopped while waiting for GPU: ' + label)
              devices = inventory()
              if not devices:
                  raise RuntimeError('GPU requested but no visible NVIDIA GPU is available; choose GPU usage No or repair the driver/runtime')
              if not any(d['total_mb'] - d['reserve_mb'] >= minimum_mb for d in devices):
                  raise RuntimeError(f'{label} requires {minimum_mb / 1024:g} GiB of GPU memory after the desktop reserve')
              ahead = 0
              with _queue_guard(directory):
                  for device in sorted(devices, key=lambda d: (-d['budget_mb'], d['uuid'])):
                      if device['total_mb'] - device['reserve_mb'] < minimum_mb:
                          continue
                      older = _older_waiters(directory, ticket, device['uuid'])
                      ahead = max(ahead, older)
                      if older:
                          continue
                      fd = _open_lock(directory, device['uuid'])
                      try:
                          fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                          current = next((d for d in inventory() if d['uuid'] == device['uuid']), None)
                          if current and current['budget_mb'] >= minimum_mb:
                              acquired = (fd, current)
                              break
                      except BlockingIOError:
                          pass
                      except BaseException:
                          os.close(fd)
                          raise
                      if acquired is None:
                          os.close(fd)
              if acquired is not None:
                  break
              elapsed = time.monotonic() - started
              if timeout is not None and elapsed >= timeout:
                    raise RuntimeError(f'Timed out waiting for GPU admission after {elapsed:.1f} seconds: ' + label)
              if elapsed - last_notice >= 60:
                  deadline = f'limit {timeout/3600:g} h' if timeout is not None else 'no deadline'
                  print(f'GPU queue [{label}]: waiting {elapsed/60:.1f} min; {ahead} earlier compatible request(s); '
                        f'{deadline}; '
                        'waiting for the active stage or reserved VRAM. CPU work can continue.', file=sys.stderr, flush=True)
                  last_notice = elapsed
              if cancel is not None:
                  cancel.wait(poll_seconds)
              else:
                  time.sleep(poll_seconds)
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Stopped while waiting for GPU: ' + label)
        fd, device = acquired
        record = dict(device, label=label, pid=os.getpid(), policy=POLICY)
        os.ftruncate(fd, 0)
        os.write(fd, (json.dumps(record) + '\n').encode())
        print(f"GPU [{label}]: {device['name']} ({device['uuid']}), budget {device['budget_mb']/1024:.1f} GiB, "
                f"reserve {device['reserve_mb']/1024:.1f} GiB plus existing use.", file=sys.stderr, flush=True)
        yield device
    finally:
        if acquired is not None:
            os.close(acquired[0])


def environment(device):
    return {'CUDA_VISIBLE_DEVICES': device['uuid'],
            'NVIDIA_VISIBLE_DEVICES': device['uuid'],
            'ONCOTRACER_GPU_UUID': device['uuid'],
            'ONCOTRACER_GPU_ENABLED': '1',
            'ONCOTRACER_GPU_MEMORY_MB': str(device['budget_mb']),
            'ONCOTRACER_GPU_RESERVE_MB': str(device['reserve_mb']),
            'TF_FORCE_GPU_ALLOW_GROWTH': 'true'}


def limit_torch(torch, device):
    if not torch.cuda.is_available():
        raise RuntimeError('GPU usage Yes requires a CUDA-enabled PyTorch runtime; installed PyTorch cannot access CUDA')
    torch.cuda.set_per_process_memory_fraction(min(.95, device['budget_mb'] / device['total_mb']), 0)

