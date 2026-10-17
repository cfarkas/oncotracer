"""PEP 517 builds with exact source provenance and verified distribution inputs."""
from __future__ import annotations

import base64
import contextlib
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile

SOURCE_DEFINITION = 'sha256(git -c tar.umask=0002 archive --format=tar COMMIT)'
BUILD_METADATA = 'oncotracer_cli/_build_metadata.py'
WHEEL_MANIFEST = 'oncotracer_cli/_distribution_files.json'
SDIST_MANIFEST = 'oncotracer-source-manifest.json'
PROVENANCE = 'provenance/native-v2-sources.json'
TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def _safe_name(name):
    if not isinstance(name, str) or not name or name.startswith('/') or '\\' in name or any(p in {'', '.', '..'} for p in name.split('/')):
        raise ValueError(f'Unsafe distribution path: {name!r}')
    return name


def _identity(value):
    if not isinstance(value, dict) or not re.fullmatch(r'[0-9a-f]{40}', str(value.get('source_commit', ''))) or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('source_sha256', ''))) or value.get('source_tree_dirty') is not False:
        raise ValueError('Distribution requires an exact clean source identity')
    return {key: value[key] for key in ('source_commit', 'source_sha256', 'source_tree_dirty')}


def _file_record(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def _metadata(identity, provenance, manifest_sha=None):
    values = {
        'BUILD_METADATA_SCHEMA': 'oncotracer-build-metadata-v1',
        'SOURCE_SHA256_DEFINITION': SOURCE_DEFINITION,
        'SOURCE_COMMIT': identity['source_commit'],
        'SOURCE_SHA256': identity['source_sha256'],
        'SOURCE_TREE_DIRTY': False,
        'SOURCE_METADATA_ORIGIN': 'embedded',
        'ONCOTRACER_SOURCE_COMMIT': identity['source_commit'],
        'ONCOTRACER_SOURCE_SHA256': identity['source_sha256'],
        'PROVENANCE_PAYLOAD_PATH': 'payload/' + PROVENANCE,
        'PROVENANCE_PAYLOAD_SHA256': hashlib.sha256(provenance).hexdigest(),
        'DISTRIBUTION_MANIFEST_SHA256': manifest_sha,
    }
    return ('"""Generated source identity for this distribution."""\n\n' + ''.join(f'{key} = {value!r}\n' for key, value in values.items())).encode()


def _tar_files(path):
    files = {}; modes = {}; prefixes = set()
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            name = _safe_name(member.name.rstrip('/'))
            parts = name.split('/'); prefixes.add(parts[0])
            if member.isdir():
                continue
            if len(parts) < 2 or not member.isfile():
                raise ValueError(f'Unsafe source distribution member: {name}')
            relative = '/'.join(parts[1:])
            if relative in files:
                raise ValueError(f'Duplicate source distribution member: {relative}')
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f'Unreadable source distribution member: {relative}')
            files[relative] = handle.read(); modes[relative] = member.mode & 0o777
    if len(prefixes) != 1:
        raise ValueError('Source distribution must have one root directory')
    return prefixes.pop(), files, modes


def stamp_sdist(path, identity):
    identity = _identity(identity)
    prefix, files, modes = _tar_files(path)
    files.pop(SDIST_MANIFEST, None)
    files[BUILD_METADATA] = _metadata(identity, files[PROVENANCE])
    manifest = {'schema': 'oncotracer-sdist-source-v1', **identity,
                'files': {name: _file_record(data) for name, data in sorted(files.items())}}
    files[SDIST_MANIFEST] = _json(manifest)
    temporary = path.with_name(path.name + '.stamped')
    with temporary.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(prefix + '/' + name)
            entry.mode = modes.get(name, 0o644); entry.size = len(data); entry.mtime = 0
            archive.addfile(entry, io.BytesIO(data))
    os.replace(temporary, path)


def stamp_wheel(path, identity):
    identity = _identity(identity)
    files = {}; modes = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            if name in files or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f'Unsafe wheel member: {name}')
            files[name] = archive.read(info); modes[name] = info.external_attr
    records = [name for name in files if name.endswith('.dist-info/RECORD')]
    if len(records) != 1:
        raise ValueError('Wheel must contain exactly one RECORD')
    record_path = records[0]
    for name in (record_path, BUILD_METADATA, WHEEL_MANIFEST):
        files.pop(name, None)
    manifest = {'schema': 'oncotracer-wheel-files-v1', **identity,
                'files': {name: _file_record(data) for name, data in sorted(files.items())}}
    manifest_bytes = _json(manifest)
    files[WHEEL_MANIFEST] = manifest_bytes
    files[BUILD_METADATA] = _metadata(identity, files[PROVENANCE], hashlib.sha256(manifest_bytes).hexdigest())
    record = io.StringIO(newline=''); writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + digest, str(len(data))))
    writer.writerow((record_path, '', '')); files[record_path] = record.getvalue().encode()
    temporary = path.with_name(path.name + '.stamped')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, TIMESTAMP); info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = modes.get(name, (stat.S_IFREG | 0o644) << 16)
            archive.writestr(info, data)
    os.replace(temporary, path)


def _stage_sdist(root, stage):
    manifest = json.loads((root / SDIST_MANIFEST).read_text())
    if manifest.get('schema') != 'oncotracer-sdist-source-v1':
        raise ValueError('Unknown source-distribution manifest')
    identity = _identity(manifest); expected = manifest.get('files')
    if not isinstance(expected, dict) or BUILD_METADATA not in expected:
        raise ValueError('Source-distribution file manifest is incomplete')
    for name, record in expected.items():
        source = root / _safe_name(name)
        if any((root.joinpath(*Path(name).parts[:length])).is_symlink() for length in range(1, len(Path(name).parts))):
            raise ValueError(f"Source-distribution input has a symlink parent: {name}")
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Source-distribution input is missing or unsafe: {name}')
        data = source.read_bytes()
        if _file_record(data) != record:
            raise ValueError(f'Source-distribution input changed: {name}')
        target = stage / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data); target.chmod(stat.S_IMODE(source.stat().st_mode))
    for source in root.rglob('*'):
        relative = source.relative_to(root)
        if source.is_dir() or '__pycache__' in relative.parts or relative.parts[0] in {'dist', 'build'} or source.suffix in {'.pyc', '.whl'}:
            continue
        name = relative.as_posix()
        if name not in expected and name != SDIST_MANIFEST:
            raise ValueError(f'Unrecorded source-distribution input: {name}')
    return identity


@contextlib.contextmanager
def staged_source():
    root = Path.cwd().resolve()
    with tempfile.TemporaryDirectory(prefix='oncotracer-build-') as directory:
        stage = Path(directory)
        try:
            probe = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
        except FileNotFoundError:
            probe = None
        if probe is not None and probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == root:
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args])
            if git('status', '--porcelain', '--untracked-files=all').strip():
                raise ValueError('Distribution builds require a clean Git checkout; commit changes before building, or install the published PyPI wheel/sdist.')
            commit = git('rev-parse', 'HEAD').decode().strip()
            payload = git('-c', 'tar.umask=0002', 'archive', '--format=tar', commit)
            identity = _identity({'source_commit': commit, 'source_sha256': hashlib.sha256(payload).hexdigest(), 'source_tree_dirty': False})
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
                for member in archive:
                    name = _safe_name(member.name.rstrip('/')); target = stage / name
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        handle = archive.extractfile(member)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(handle.read()); target.chmod(member.mode & 0o777)
                    else:
                        raise ValueError(f'Unsafe source archive member: {name}')
        elif (root / SDIST_MANIFEST).is_file():
            identity = _stage_sdist(root, stage)
        else:
            raise ValueError('Source identity is unavailable. Install the published PyPI wheel/sdist, or build from an exact clean Git checkout.')
        yield stage, identity


@contextlib.contextmanager
def _cwd(path):
    previous = Path.cwd(); os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _api():
    from poetry.core.masonry import api
    return api


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    destination = Path(wheel_directory).resolve(); destination.mkdir(parents=True, exist_ok=True)
    if metadata_directory is not None:
        metadata_directory = str(Path(metadata_directory).resolve())
    with staged_source() as (stage, identity), _cwd(stage):
        name = _api().build_wheel(str(destination), config_settings, metadata_directory)
    stamp_wheel(destination / name, identity)
    return name


def build_sdist(sdist_directory, config_settings=None):
    destination = Path(sdist_directory).resolve(); destination.mkdir(parents=True, exist_ok=True)
    with staged_source() as (stage, identity), _cwd(stage):
        name = _api().build_sdist(str(destination), config_settings)
    stamp_sdist(destination / name, identity)
    return name


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _api().build_editable(wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _api().prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _api().prepare_metadata_for_build_editable(metadata_directory, config_settings)


def get_requires_for_build_wheel(config_settings=None):
    return _api().get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _api().get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    return _api().get_requires_for_build_editable(config_settings)
