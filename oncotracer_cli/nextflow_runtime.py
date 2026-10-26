"""Prepare immutable Nextflow distributions before starting parallel JVMs."""
from contextlib import contextmanager
import fcntl
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib

from .runtime import OncoTracerError, atomic_write_json


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _check_jar(path):
    """Read every member: a valid ZIP directory alone does not prove completeness."""
    try:
        with zipfile.ZipFile(path) as archive:
            if 'META-INF/MANIFEST.MF' not in archive.namelist():
                raise ValueError('incomplete JAR archive')
            # Nextflow 26.04.2's published shadow JAR has duplicate central
            # directory records for the SAME local entry. Python's testzip()
            # mistakes these for overlapping members. Deduplicate identical
            # records only; still enforce boundaries and CRCs for every byte.
            unique = {}
            for entry in archive.infolist():
                signature = (entry.filename, entry.CRC, entry.compress_size,
                             entry.file_size, entry.compress_type, entry.flag_bits)
                if entry.header_offset in unique:
                    if unique[entry.header_offset][0] != signature:
                        raise ValueError('conflicting JAR directory records')
                else:
                    unique[entry.header_offset] = (signature, copy.copy(entry))
            boundary = archive.start_dir
            for offset, (_, entry) in sorted(unique.items(), reverse=True):
                entry._end_offset = boundary
                boundary = offset
                with archive.open(entry) as member:
                    while member.read(1024 * 1024):
                        pass
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as error:
        raise OncoTracerError(f'Nextflow distribution is incomplete: {path}: {error}') from error


def _prepared_environment(directory, receipt):
    environment = {'NXF_DIST': str(directory / 'framework'),
                   'NXF_DISABLE_CHECK_LATEST': 'true'}
    if receipt.get('bin'):
        environment['NXF_BIN'] = str(directory / receipt['bin'])
    return environment


def prepare_nextflow_environment(executable, environment=None, *, timeout=300):
    """Bootstrap once in the *worker's* environment, before launching samples.

    Nextflow's launcher moves its download from NXF_TEMP to NXF_DIST. Across
    filesystems this is a copy, exposing partial bytes to other JVMs. A lock,
    private staging directory and same-filesystem rename prevent that race.
    Published generations are never overwritten (a running JVM may use them).
    An interrupted download has no ready receipt and is never reused.
    """
    env = dict(os.environ if environment is None else environment)
    launcher = Path(executable).resolve()
    try:
        identity = {'launcher_sha256': _sha256(launcher)}
        for name in ('NXF_VER', 'NXF_DIST', 'NXF_BIN', 'NXF_JAR', 'NXF_URL',
                     'NXF_BASE', 'NXF_ORG', 'NXF_PROT'):
            identity[name] = env.get(name, '')
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        home = Path(env.get('HOME', str(Path.home())))
        cache = Path(env.get('XDG_CACHE_HOME', str(home / '.cache')))
        root = cache / 'oncotracer' / 'nextflow' / key
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (root / '.prepare.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                receipt = json.loads((root / 'ready.json').read_text())
                directory = root / receipt['directory']
                # Only owned generations within this cache may be reused.
                if (directory.parent == root and directory.name.startswith('runtime-')
                        and not directory.is_symlink() and receipt['jars']
                        and all(not (directory / name).is_symlink()
                                and (directory / name).resolve().is_relative_to(directory.resolve())
                                and _sha256(directory / name) == digest
                                for name, digest in receipt['jars'].items())):
                    return _prepared_environment(directory, receipt)
            except (OSError, ValueError, KeyError, TypeError):
                pass

            print('Preparing and verifying Nextflow runtime before parallel samples…', flush=True)
            with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as temporary:
                staging = Path(temporary)
                framework = staging / 'framework'; framework.mkdir()
                overrides = {'NXF_DIST': str(framework), 'NXF_DISABLE_CHECK_LATEST': 'true'}
                explicit_bin = env.get('NXF_BIN')
                if explicit_bin:
                    source = Path(explicit_bin)
                    _check_jar(source)
                    destination = staging / 'selected.jar'
                    shutil.copyfile(source, destination)
                    overrides['NXF_BIN'] = str(destination)
                else:
                    # Reuse a preinstalled distribution (including read-only
                    # Docker layers) without modifying its shared cache.
                    old_home = Path(env.get('NXF_HOME', str(home / '.nextflow')))
                    old_dist = Path(env.get('NXF_DIST', str(old_home / 'framework')))
                    version = env.get('NXF_VER')
                    if not version:
                        match = re.search(r"^NXF_VER=\$\{NXF_VER:-['\"]?([^'\"}]+)",
                                          launcher.read_text(errors='replace'), re.M)
                        version = match.group(1) if match else None
                    sources = ((old_dist / version).glob('*.jar') if version
                               else old_dist.glob('*/*.jar'))
                    for source in sources:
                        try:
                            _check_jar(source)
                        except OncoTracerError:
                            # A corrupt legacy cache is not a valid seed. Let
                            # the selected launcher fetch into private staging.
                            continue
                        destination = framework / source.relative_to(old_dist)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                child = {**env, **overrides}
                with isolated_nextflow_environment(child) as scratch:
                    result = subprocess.run([str(launcher), '-version'], cwd=staging,
                                            env={**child, **scratch}, text=True,
                                            capture_output=True, timeout=timeout, check=False)
                output = (result.stdout or '') + '\n' + (result.stderr or '')
                if result.returncode or not re.search(r'nextflowversion\d', re.sub(r'\s+', '', output), re.I):
                    raise OncoTracerError('Nextflow runtime preparation failed before sample launch: '
                                         + output.strip()[-2000:])
                jars = [staging / 'selected.jar'] if explicit_bin else list(framework.rglob('*.jar'))
                if not jars:
                    raise OncoTracerError('Nextflow preparation produced no verifiable distribution JAR')
                for jar in jars:
                    _check_jar(jar)
                    jar.chmod(0o444)
                receipt = {'schema': 'oncotracer-nextflow-runtime-v1', **identity,
                           'directory': 'runtime-' + staging.name.removeprefix('.staging-'),
                           'jars': {str(j.relative_to(staging)): _sha256(j) for j in jars},
                           'bin': 'selected.jar' if explicit_bin else None,
                           'version_output': output.strip()}
                directory = root / receipt['directory']
                # Staging and final are siblings: rename cannot become a copy.
                os.replace(staging, directory)
                atomic_write_json(root / 'ready.json', receipt)
                return _prepared_environment(directory, receipt)
    except (OSError, subprocess.SubprocessError) as error:
        raise OncoTracerError(f'Nextflow runtime preparation failed before sample launch: {error}') from error


@contextmanager
def isolated_nextflow_environment(environment=None):
    """Yield child-only overrides; keep persistent work and resume caches intact.

    Older Nextflow distributions bundle a HawtJNI loader which truncates a fixed
    libjansi filename in java.io.tmpdir. Different JVMs must never share that
    extraction directory, including readiness probes and separate projects.
    NXF_TEMP selects java.io.tmpdir without changing global environment variables.
    """
    env = os.environ if environment is None else environment
    parent = env.get('NXF_TEMP') or env.get('TMPDIR') or tempfile.gettempdir()
    parent = Path(parent).expanduser().resolve()
    # Older launchers lose quoting while expanding the Capsule trampoline.
    # Keep the short-lived JVM directory whitespace-free even for spaced projects.
    if any(character.isspace() for character in str(parent)):
        parent = Path('/tmp')
    with tempfile.TemporaryDirectory(prefix='oncotracer-nextflow-', dir=parent) as scratch:
        overrides = {'NXF_TEMP': scratch}
        # Some launchers expand the -jar path without preserving quotes. Keep
        # the immutable files in the requested cache, with child-only aliases
        # whose names remain safe for that launcher. Each alias dies with its JVM.
        for variable, name in (('NXF_DIST', 'framework'), ('NXF_BIN', 'selected.jar')):
            value = env.get(variable)
            if value and any(character.isspace() for character in value):
                alias = Path(scratch) / name
                alias.symlink_to(Path(value).resolve())
                overrides[variable] = str(alias)
        yield overrides
