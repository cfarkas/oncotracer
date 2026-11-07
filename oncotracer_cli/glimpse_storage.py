"""Retire only task-local GLIMPSE split binaries after verified phase completion.

The scientific workflow bundle, staged inputs, BCF/CSI outputs and Nextflow cache
are untouched. Split binaries are not declared task outputs or continuation
inputs. A failed, running, changed, custom or unverifiable task is left intact.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import threading

from .runtime import atomic_write_json, utc_now
from .storage_options import validate_mode

BGZF_EOF = bytes.fromhex('1f8b08040000000000ff0600424302001b0003000000000000000000')
PHASE_FLAGS = {'--config', '--chrom', '--threads', '--chunk', '--input-region',
               '--output-region', '--reference', '--map', '--bam'}


def _identity(path):
    value = Path(path).lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError('not an exclusive regular file')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns)


def _ordinary_directory(path, root):
    path, root = Path(path), Path(root)
    if root.is_symlink() or not root.is_dir():
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or not current.is_dir():
            return False
    return True


def _validate_bcf(path, sample):
    # A success receipt and index alone do not establish that stored output is
    # still readable. Check BGZF termination, BCF/sample header and every CRC.
    with Path(path).open('rb') as stream:
        stream.seek(-len(BGZF_EOF), os.SEEK_END)
        if stream.read() != BGZF_EOF:
            raise ValueError('BCF is not a complete BGZF stream')
    with gzip.open(path, 'rb') as stream:
        start = stream.read(9)
        if len(start) != 9 or start[:5] != b'BCF\x02\x02':
            raise ValueError('not BCF2')
        length = struct.unpack('<I', start[5:])[0]
        if not 1 <= length <= 16 * 1024 * 1024:
            raise ValueError('invalid BCF header length')
        header = stream.read(length)
        if len(header) != length or not header.endswith(b'\x00'):
            raise ValueError('incomplete BCF header')
        columns = [line for line in header[:-1].decode().splitlines() if line.startswith('#CHROM\t')]
        if len(columns) != 1 or columns[0].split('\t')[9:] != [sample]:
            raise ValueError('BCF sample does not match completed task')
        while stream.read(1024 * 1024):
            pass
    index = Path(str(path) + '.csi')
    with gzip.open(index, 'rb') as stream:
        if stream.read(4) != b'CSI\x01':
            raise ValueError('not a CSI index')
        if len(stream.read(12)) != 12:
            raise ValueError('incomplete CSI index')
        while stream.read(1024 * 1024):
            pass


class PhaseScratchCompactor:
    """One controller-owned sample; read-only audit is available with scan(False)."""

    def __init__(self, sample_root, sample, bundle, *, mode='full', interval=30, cancel=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sample):
            raise ValueError("Unsafe sample identifier")
        self.root = Path(sample_root).absolute()
        self.sample = sample
        self.bundle = Path(bundle)
        self.mode = validate_mode(mode)
        self.interval = interval
        self.audit_path = self.root / 'pipeline_info/storage_compaction.json'
        self.stop = threading.Event()
        self.cancel = cancel or threading.Event()
        self.thread = None
        self.errors = []
        self._finished = set()
        self._guard = threading.Lock()
        self.summary = {'schema': 'oncotracer-storage-v1', 'mode': self.mode,
                        'sample': sample, 'removed_files': 0, 'removed_bytes': 0,
                        'reclaimed_allocated_bytes': 0, 'tasks': []}
        self.code = (self.bundle / 'bin/glimpse2.py').read_bytes() if self.mode == 'compact' else b''
        self.workflow = (self.bundle / 'main.nf').read_bytes() if self.mode == 'compact' else b''

    def _task(self, work):
        if not _ordinary_directory(work, self.root):
            raise ValueError('work directory is not ordinary')
        end, begin, script = (work / name for name in ('.exitcode', '.command.begin', '.command.sh'))
        proof = {p: _identity(p) for p in (end, begin, script)}
        if end.read_text().strip() != '0':
            raise ValueError('task has not succeeded')
        started, finished = proof[begin][3], proof[end][3]
        if finished < started:
            raise ValueError('task completion predates its start')
        args = shlex.split(script.read_text(), comments=True)
        if len(args) != 3 + 2 * len(PHASE_FLAGS) or args[2] != 'phase' or Path(args[1]).name != 'glimpse2.py':
            raise ValueError('not the supported GLIMPSE phase command')
        flags = dict(zip(args[3::2], args[4::2]))
        if set(flags) != PHASE_FLAGS or len(flags) != len(PHASE_FLAGS):
            raise ValueError('unexpected phase arguments')
        if (work / args[1]).read_bytes() != self.code:
            raise ValueError('custom or changed GLIMPSE worker')
        runtime_path = work / flags['--config']
        raw = runtime_path.read_bytes()
        if runtime_path.stem != hashlib.sha256(raw).hexdigest():
            raise ValueError('runtime checksum mismatch')
        runtime = json.loads(raw)
        if (Path(runtime['root']) / 'main.nf').read_bytes() != self.workflow:
            raise ValueError('custom or changed GLIMPSE workflow')
        chrom, chunk = flags['--chrom'], flags['--chunk']
        if runtime.get('sample') != self.sample or chrom not in runtime.get('glimpse_chromosomes', []):
            raise ValueError('task sample or chromosome mismatch')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', chrom) or not chunk.isdecimal():
            raise ValueError('unsafe phase identifiers')
        out = work / f'{self.sample}.{chrom}.{chunk}.bcf'
        for path in (out, Path(str(out) + '.csi')):
            proof[path] = _identity(path)
            if not started <= proof[path][3] <= finished or proof[path][2] == 0:
                raise ValueError('output is missing, changed or outside task lifetime')
        binaries = list(work.glob('split*.bin'))
        if len(binaries) != 1 or not re.fullmatch(r'split_[A-Za-z0-9_.-]+_[0-9]+_[0-9]+\.bin', binaries[0].name):
            raise ValueError('not one recognized split-reference scratch file')
        path = binaries[0]
        proof[path] = _identity(path)
        if not started <= proof[path][3] <= finished or proof[path][2] == 0:
            raise ValueError('scratch is changed or outside task lifetime')
        _validate_bcf(out, self.sample)
        if any(_identity(p) != value for p, value in proof.items()):
            raise ValueError('task changed during validation')
        return path, proof

    def scan(self, apply=False, stop_event=None):
        if apply and self.mode != 'compact':
            raise ValueError('Scratch retirement requires compact storage mode')
        with self._guard:
            result = {'candidate_files': 0, 'candidate_bytes': 0, 'skipped': Counter()}
            work_root = self.root / 'work'
            if not _ordinary_directory(work_root, self.root):
                return result
            for end in sorted(work_root.glob('*/*/.exitcode')):
                if self.cancel.is_set() or (stop_event is not None and stop_event.is_set()):
                    break
                work = end.parent
                if work in self._finished or not any(work.glob('split*.bin')):
                    continue
                try:
                    path, proof = self._task(work)
                    result['candidate_files'] += 1
                    result['candidate_bytes'] += proof[path][2]
                    if not apply:
                        continue
                    receipt = {'task': str(work.relative_to(self.root)),
                               'scratch': path.name, 'bytes': proof[path][2],
                               'allocated_bytes': path.stat().st_blocks * 512,
                               'output': next(p.name for p in proof if p.suffix == '.bcf'),
                               'validated_at': utc_now(), 'state': 'prepared'}
                    journal = work / '.oncotracer-storage.json'
                    if journal.is_symlink():
                        raise ValueError('storage receipt is a symlink')
                    # Record the intended removal before unlinking. A crash can
                    # leave a prepared receipt, never an unrecorded deletion.
                    atomic_write_json(journal, receipt)
                    if not _ordinary_directory(work, self.root) or any(_identity(p) != value for p, value in proof.items()):
                        raise ValueError('task changed before scratch retirement')
                    descriptor = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        current = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != proof[path]:
                            raise ValueError('scratch changed before removal')
                        os.unlink(path.name, dir_fd=descriptor)
                    finally:
                        os.close(descriptor)
                    receipt['state'] = 'removed'
                    atomic_write_json(journal, receipt)
                    self.summary['tasks'].append(receipt)
                    self.summary['removed_files'] += 1
                    self.summary['removed_bytes'] += receipt['bytes']
                    self.summary['reclaimed_allocated_bytes'] += receipt['allocated_bytes']
                    self._finished.add(work)
                except (OSError, ValueError, TypeError, KeyError, EOFError, struct.error) as error:
                    result['skipped'][str(error)] += 1
            if apply and _ordinary_directory(self.root / 'pipeline_info', self.root):
                self.summary.update(observed_at=utc_now(), skipped=dict(result['skipped']), errors=self.errors[-10:])
                if self.audit_path.is_symlink():
                    raise ValueError('storage summary is a symlink')
                atomic_write_json(self.audit_path, self.summary)
            return result

    def _scan_safely(self, interruptible=False):
        try:
            self.scan(apply=True, stop_event=self.stop if interruptible else None)
        except Exception as error:
            # Storage optimization must never turn successful science into a
            # failure; unverifiable files stay in place and errors are surfaced.
            self.errors.append(str(error))
            self.summary["errors"] = self.errors[-10:]

    def _watch(self):
        while not self.stop.is_set() and not self.cancel.is_set():
            self._scan_safely(interruptible=True)
            if self.stop.wait(self.interval):
                break

    def __enter__(self):
        if self.mode == 'compact':
            self.thread = threading.Thread(target=self._watch, name='glimpse-storage-' + self.sample, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_):
        if self.thread is not None:
            self.stop.set()
            self.thread.join()
            self._scan_safely()
        return False
