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
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

MIB = 1024 ** 2
POLICY = 'reserved-vram-v1'


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
                note='GPU stages share one queue per GPU across this user\'s projects. '
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


def _open_lock(directory, uuid):
    fd = os.open(directory / (uuid + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise RuntimeError('GPU lock is not a private regular file')
    return fd


@contextmanager
def lease(label, *, minimum_mb=8192, cancel=None, poll_seconds=1, timeout=86400):
    """Wait for a compatible GPU, recheck free VRAM under its lock, then reserve.

    A request never silently switches to CPU. Timeouts and cancellation are
    explicit failures, allowing the workflow to retain validated completed work.
    """
    directory = lock_directory()
    started = time.monotonic()
    last_notice = -60
    acquired = None
    try:
        while acquired is None:
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Stopped while waiting for GPU: ' + label)
            devices = inventory()
            if not devices:
                raise RuntimeError('GPU requested but no visible NVIDIA GPU is available; choose GPU usage No or repair the driver/runtime')
            if not any(d['total_mb'] - d['reserve_mb'] >= minimum_mb for d in devices):
                raise RuntimeError(f'{label} requires {minimum_mb / 1024:g} GiB of GPU memory after the desktop reserve')
            for device in sorted(devices, key=lambda d: (-d['budget_mb'], d['uuid'])):
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
            if elapsed >= timeout:
                raise RuntimeError('Timed out waiting for reserved GPU memory: ' + label)
            if elapsed - last_notice >= 60:
                print(f'GPU queue [{label}]: waiting for another GPU stage or available VRAM; CPU work can continue.', file=sys.stderr, flush=True)
                last_notice = elapsed
            if cancel is not None:
                cancel.wait(poll_seconds)
            else:
                time.sleep(poll_seconds)
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

