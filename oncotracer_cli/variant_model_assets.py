"""Pinned public variant resources, prepared only by the analysis runner.

Catalog provenance: Clair3 v1.2.0 README and the official nanoporetech/rerio
model stubs. Archive SHA256/lengths were independently measured from those
HTTPS URLs on 2026-09-22; they are not claimed as upstream-published checksums.
No caller, model loader, installer or shell is executed here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .runtime import OncoTracerError, atomic_write_json, sha256_file

CLAIR3_SOURCE = 'https://github.com/HKU-BAL/Clair3/tree/v1.2.0#pre-trained-models'
CLAIR3_BASE = 'https://cdn.oxfordnanoportal.com/software/analysis/models/clair3/'
CLAIR3_FILES = ('pileup.index', 'pileup.data-00000-of-00001',
                'full_alignment.index', 'full_alignment.data-00000-of-00001')
CLAIR3_PROFILES = {
    'r1041_e82_400bps_sup_v500': {'label': 'R10.4.1 E8.2 / Dorado SUP v5.0.0 / 400 bps / 5 kHz',
        'size': 74586126, 'sha256': '01c05768661bdd7de611e6bae1043c43b7523a54b223e029c683bfac0db7a678'},
    'r1041_e82_400bps_hac_v500': {'label': 'R10.4.1 E8.2 / Dorado HAC v5.0.0 / 400 bps / 5 kHz',
        'size': 74676065, 'sha256': 'facf39a89aa11c6b92d807961f31365cbc49cbef4e53edadc13c98a1cc6d26a0'},
    'r1041_e82_400bps_sup_v420': {'label': 'R10.4.1 E8.2 / Dorado SUP v4.2.0 / 400 bps / 5 kHz',
        'size': 74872512, 'sha256': 'e90d37d1d277adbfe1bc4cddee98c63d693b93a6c44f3311d8130c76893c72ee'},
    'r1041_e82_400bps_sup_v410': {'label': 'R10.4.1 E8.2 / Dorado SUP v4.1.0 / 400 bps / 4 kHz',
        'size': 75090727, 'sha256': '81841dc0a41c3dbb8c88cceabb182273c60ba1bf12c37bf99a821ae3d729008d'},
    'r1041_e82_400bps_hac_v410': {'label': 'R10.4.1 E8.2 / Dorado HAC v4.1.0 / 400 bps / 4 kHz',
        'size': 74269472, 'sha256': 'e82e42a25bc441da9af06ab7457e8d12f73901abcf80703bfa7cfa1996e5c890'},
}
_CLAIR3_FILE_SHA256 = {'r1041_e82_400bps_hac_v410': {'full_alignment.data-00000-of-00001': 'd730ee132f9e25b0219063a2d80be03200b986e570116c59d96adad170c32f96',
                               'full_alignment.index': 'e134a777f7cec76182c32824dcf1bfe4df9292b5f5850462353350ca4cdbef6d',
                               'pileup.data-00000-of-00001': 'a7089b548ae6a450fe51ed17e42fec5c54a87303588eff7f923e4180a28961c2',
                               'pileup.index': '762d3cf58cccebe7097182b4a190bb850271fc12fb8f3ee8c425d8a80bf5b7cc'},
 'r1041_e82_400bps_hac_v500': {'full_alignment.data-00000-of-00001': 'ed29567769b6499e58ab1610ecedd4041f89e979f3cc35c00ad296a4bb5da352',
                               'full_alignment.index': '9e757441cbc05623a67a30429610772fa7c864f9798fc0f43c3a96992e4d7c7e',
                               'pileup.data-00000-of-00001': 'c83c53343e708a187614523a2084a081dc7ba79e4a3420267cd1d95446f37d15',
                               'pileup.index': 'd121b0a8b2f6f4e52ede0da8cbdc07e56e79a0173ad9605d66ff5b076b449c3e'},
 'r1041_e82_400bps_sup_v410': {'full_alignment.data-00000-of-00001': 'c7f8ba78253fd7db57ecd00db3688b51c2f893fd264ebfc772124bb9b732283a',
                               'full_alignment.index': 'c979d60ae4d39e30634e80ec103673df804b26bc10f192648408c9f5484aae61',
                               'pileup.data-00000-of-00001': 'b2f18578f2df8b51277e7ac3b159432e19fca436c8c8d4e8246b8dd4c069678f',
                               'pileup.index': 'd90af28b86e0403551b9b31afd2145d365f6e180ece2b0b165200d833bae2be8'},
 'r1041_e82_400bps_sup_v420': {'full_alignment.data-00000-of-00001': '96393313e3f700c832fb039f19c721dc9a00b3a09ac73731cf71c1fd8322a5f1',
                               'full_alignment.index': '48d8bf2449d2395343ff72d34df685dd1c37542d43455c0f368955836ec68965',
                               'pileup.data-00000-of-00001': 'f99eba0028bfd2443e94af1147adb2c3223ad290ded85884d50151feae32ee24',
                               'pileup.index': 'd57b3f14f96d9e7055809bbc3dfba961b720b1013c416e9a24372513bb0a255c'},
 'r1041_e82_400bps_sup_v500': {'full_alignment.data-00000-of-00001': '89261654d2c9d5abb5f15dd385012a8b2ce213e7ed15ee617f3d4297712e23f1',
                               'full_alignment.index': '1e702f3605e1979efd8d22c7f43a91a66d5e28cc33c2188c921d9229e994d000',
                               'pileup.data-00000-of-00001': 'c1b02a6b8e0fc5ef7067f82dabecd3fbcc0422f45ee3335117093242fd498988',
                               'pileup.index': '2c28334119b24395dad45d1c6f06158f1d30abfff8b1c1c5824cd1d48b3fff4b'}}
for _profile, _metadata in CLAIR3_PROFILES.items():
    _metadata.update(url=CLAIR3_BASE + _profile + '.tar.gz', caller_version='1.2.0',
                     source=CLAIR3_SOURCE, files=list(CLAIR3_FILES), archive_root=_profile,
                     file_sha256=_CLAIR3_FILE_SHA256[_profile])

# These presets are bundled with the tested v0.4.4 Docker/SIF distribution.
# Keep custom ont_* strings supported for independently installed caller versions.
CLAIRSTO_PLATFORMS = {
    'ont_r10_dorado_sup_5khz_ssrs': 'R10.4.1 / Dorado SUP v4.2.0 / 5 kHz / synthetic + real training',
    'ont_r10_dorado_sup_5khz_ss': 'R10.4.1 / Dorado SUP v4.2.0 / 5 kHz / synthetic training',
    'ont_r10_dorado_sup_5khz': 'R10.4.1 / Dorado SUP v4.2.0 / 5 kHz / legacy preset',
    'ont_r10_dorado_sup_4khz': 'R10.4.1 / Dorado SUP v4.1.0 / 4 kHz',
    'ont_r10_dorado_hac_4khz': 'R10.4.1 / Dorado HAC v4.1.0 / 4 kHz',
    'ont_r10_guppy_sup_4khz': 'R10.4.1 / Guppy SUP v6.1.5 / 4 kHz',
    'ont_r10_guppy_hac_5khz': 'R10.4.1 / Guppy HAC v6.5.7 / 5 kHz',
}
AUTO_PATH_FIELDS = frozenset({'variant_clair3_model', 'variant_ffperase_root', 'variant_ffperase_models'})


FFPERASE_REVISION = 'b0dd56cbd0a939896a966b9ce30c4d719b158170'
FFPERASE_MODEL_REVISION = 'dc4a9ab71bde34d084c4cc91d0ec291dc1f04258'
FFPERASE_LICENSE = 'https://github.com/papaemmelab/nf-ffperase/blob/' + FFPERASE_REVISION + '/LICENSE'
# Source digests checked against the pinned Git tree; model SHA256 values are
# official Hugging Face LFS OIDs for the pinned revision. No joblib is loaded.
FFPERASE_SOURCE_FILES = {
    'LICENSE': {'size': 8566, 'sha256': '3572531ff65731a0bbe427066067c89c2bf61fae09f9b8e4127814688e1aa5a7'},
    'bin/annotate_w_pileup': {'size': 2457944, 'sha256': 'c19fe11555e51f414bd9684a221a72a2449fc6a765f53790582a10ef0fefd3ba', 'executable': True},
    'bin/annotate_variants.py': {'size': 8111, 'sha256': '6481da23ac39f06d90096f0972e83bca91f1913fbf81debec6b1cb9016e026a4'},
    'bin/classify_w_random_forest.py': {'size': 4173, 'sha256': '2a5ca69e3ea9ccc8e0ef78a70ecf837eb7cd8b42abe212ac3a6e08272469b71d'},
    'bin/microrep_python3.py': {'size': 10839, 'sha256': '1d728993db0cd023bbc5d8486d8387d42c1af1fdd100aea416914a5f4e21d5d2'},
}
for _name, _metadata in FFPERASE_SOURCE_FILES.items():
    _metadata['url'] = 'https://raw.githubusercontent.com/papaemmelab/nf-ffperase/' + FFPERASE_REVISION + '/' + _name
FFPERASE_MODEL_FILES = {
    'model.snvs.joblib': {'size': 183875, 'sha256': '162d229c2c1f9d0ef701d9043ccd1850ec937c426c003dbc22e81e5a01efb2ad'},
    'model.indels.joblib': {'size': 67045446, 'sha256': '2829910374fb33c7fe7d26675ce4491cffbb7a08ad9f5ba40d22b829a82cef4a'},
}
for _name, _metadata in FFPERASE_MODEL_FILES.items():
    _metadata['url'] = 'https://huggingface.co/papaemmelab/ffperase/resolve/' + FFPERASE_MODEL_REVISION + '/' + _name


def ffperase_asset_paths(request):
    """Honor explicit paths and environment before considering a download."""
    source = request.ffperase_root or os.environ.get('ONCOTRACER_FFPERASE_ROOT')
    models = request.ffperase_models or os.environ.get('ONCOTRACER_FFPERASE_MODELS')
    if not source:
        existing = Path.home() / '.nextflow/assets/.repos/papaemmelab/nf-ffperase/clones' / FFPERASE_REVISION
        if existing.is_dir():
            source = existing
    return (Path(source).expanduser().resolve() if source else None,
            Path(models).expanduser().resolve() if models else None)


def ffperase_pending(request):
    if request.ffperase == 'off' or not request.download_resources:
        return []
    paths = ffperase_asset_paths(request)
    missing = []
    for kind, path, assets in zip(('ffperase_source', 'ffperase_models'), paths,
                                  (FFPERASE_SOURCE_FILES, FFPERASE_MODEL_FILES)):
        if path is not None:
            # Existing installations need the scientific interfaces, not a
            # separately copied license. An explicit invalid path is never replaced.
            required = [name for name in assets if name != 'LICENSE']
            if not path.is_dir() or any(not (path / name).is_file() or (path / name).stat().st_size == 0 for name in required):
                raise OncoTracerError(f'Explicit {kind} path is incomplete: {path}. Correct it or select auto; it will not be overwritten.')
        else:
            missing.append({'resource': kind, 'status': 'prepare_at_run',
                            'download_bytes': sum(item['size'] for item in assets.values()),
                            'license': FFPERASE_LICENSE,
                            'files': {name: dict(item) for name, item in assets.items()}})
    if missing and not request.accept_ffperase_license:
        raise OncoTracerError('Automatic FFPERASE preparation requires variant_accept_ffperase_license: true after reviewing '
                             + FFPERASE_LICENSE + '. Existing supplied source/models do not require this download acknowledgment.')
    if any(item['resource'] == 'ffperase_source' for item in missing) and (platform.system() != 'Linux' or platform.machine().lower() not in {'x86_64', 'amd64'}):
        raise OncoTracerError('The pinned FFPERASE binary supports Linux x86_64 only; supply a compatible custom installation.')
    return missing


def preflight_ffperase_runtime(request):
    """Read-only runtime validation before optional downloads, never an install."""
    sif = request.ffperase_sif or os.environ.get('ONCOTRACER_FFPERASE_SIF')
    prefix = request.ffperase_prefix or os.environ.get('ONCOTRACER_FFPERASE_PREFIX')
    if sif:
        if not Path(sif).is_file() or not (shutil.which('apptainer') or shutil.which('singularity')):
            raise OncoTracerError('Automatic FFPERASE resources still require an existing SIF and Apptainer/Singularity')
    elif not prefix or not (Path(prefix) / 'bin/python').is_file():
        raise OncoTracerError('Automatic FFPERASE resources still require a compatible variant_ffperase_prefix runtime; no tools are installed automatically')


def is_auto_resource(key, value):
    return key in AUTO_PATH_FIELDS and str(value).strip().lower() == 'auto'


def validate_profile(profile):
    if profile not in CLAIR3_PROFILES:
        raise OncoTracerError('Automatic Clair3 resources require an explicit supported variant_ont_profile: '
                             + ', '.join(CLAIR3_PROFILES) + '. Use a custom model directory for other basecallers.')
    return CLAIR3_PROFILES[profile]


def resource_download_plan(request):
    """Describe deferred preparation with read-only path checks; no network or writes."""
    result = []
    if request.clair3_auto and 'clair3' in request.callers:
        asset = validate_profile(request.ont_profile)
        result.append({'resource': 'clair3_model', 'profile': request.ont_profile,
                       'status': 'prepare_at_run', 'url': asset['url'], 'sha256': asset['sha256'],
                       'download_bytes': asset['size'], 'caller_version': asset['caller_version']})
    result.extend(ffperase_pending(request))
    return result


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith('https://'):
            raise OncoTracerError('Variant resource download refused a non-HTTPS redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(asset, destination):
    """Bounded HTTPS stream, published only after exact size and SHA256 checks."""
    if not asset['url'].startswith('https://'):
        raise OncoTracerError('Variant resource downloads require HTTPS')
    digest, count, started = hashlib.sha256(), 0, time.monotonic()
    try:
        opener = urllib.request.build_opener(_HTTPSRedirect())
        with opener.open(asset['url'], timeout=60) as response, destination.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > asset['size'] or time.monotonic() - started > 1800:
                    raise OncoTracerError('Variant resource download exceeded its size/time bound')
                digest.update(chunk)
                output.write(chunk)
        if count != asset['size'] or digest.hexdigest() != asset['sha256']:
            raise OncoTracerError('Variant resource download failed size/SHA256 verification; no model was installed')
    except (OSError, urllib.error.URLError) as error:
        raise OncoTracerError(f'Variant resource download failed: {error}; rerun to retry') from error


def _extract_model(archive, destination, asset):
    """Extract only named regular model files: no links, traversal or executables."""
    expected = set(asset['files'])
    seen, total = set(), 0
    with tarfile.open(archive, 'r:gz') as handle:
        members = handle.getmembers()
        if len(members) > len(expected) + 1:
            raise OncoTracerError('Unexpected entries in variant model archive')
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.isdir() and member.name.rstrip('/') == asset['archive_root']:
                continue
            if (not member.isfile() or len(parts) != 2 or parts[0] != asset['archive_root']
                    or parts[1] not in expected or parts[1] in seen or member.size <= 0):
                raise OncoTracerError('Unsafe or unexpected entry in variant model archive')
            total += member.size
            if total > 256 * 1024 * 1024:
                raise OncoTracerError('Variant model archive exceeds the extracted-size bound')
            seen.add(parts[1])
            with handle.extractfile(member) as source, (destination / parts[1]).open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        if seen != expected:
            raise OncoTracerError('Variant model archive is missing required checkpoint files')


def _cached(directory, asset):
    if directory.is_symlink():
        raise OncoTracerError('Variant resource cache must not be a symlink')
    if not directory.exists():
        return False
    try:
        marker = directory / 'resource.json'
        if marker.is_symlink():
            raise ValueError('symlink manifest')
        record = json.loads(marker.read_text())
        if record['url'] != asset['url'] or record['sha256'] != asset['sha256']:
            raise ValueError('different asset')
        expected = set(asset['files'])
        observed = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
        if any(p.is_symlink() for p in directory.rglob('*')) or set(record['files']) != expected or observed != expected | {'resource.json'}:
            raise ValueError('unexpected cache contents')
        for name in expected:
            if asset.get('file_sha256', {}).get(name, record['files'][name]) != record['files'][name]:
                raise ValueError('unrecognized model digest')
            file = directory / name
            if file.is_symlink() or not file.is_file() or sha256_file(file) != record['files'][name]:
                raise ValueError('changed model file')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OncoTracerError(f'Variant model cache is incomplete or changed: {directory}. '
                             'Move that cache folder aside before retrying; custom resources are never overwritten.') from error
    return True


def ensure_clair3_model(profile, cache_root):
    """Called at RUN only; same output cache is verified and reused on resume."""
    asset = validate_profile(profile)
    cache_root = Path(cache_root)
    if cache_root.is_symlink():
        raise OncoTracerError('Variant resource cache must not be a symlink')
    cache_root.mkdir(parents=True, exist_ok=True)
    directory = cache_root / profile
    lock = cache_root / ('.' + profile + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OncoTracerError('Variant model cache is being prepared by another run; retry after it finishes') from error
        if _cached(directory, asset):
            return directory
        print(f"Preparing verified Clair3 model {profile} ({asset['size'] / 1024**2:.1f} MiB) at run time", flush=True)
        with tempfile.TemporaryDirectory(prefix='.' + profile + '-', dir=cache_root) as temporary:
            staging = Path(temporary)
            archive = staging / 'download.tar.gz'
            _download(asset, archive)
            model = staging / 'model'
            model.mkdir()
            try:
                _extract_model(archive, model, asset)
            except (tarfile.TarError, OSError) as error:
                raise OncoTracerError(f'Cannot unpack verified variant model: {error}') from error
            atomic_write_json(model / 'resource.json', {
                'url': asset['url'], 'sha256': asset['sha256'], 'download_bytes': asset['size'],
                'profile': profile, 'caller_version': asset['caller_version'], 'source': asset['source'],
                'files': {name: sha256_file(model / name) for name in asset['files']},
            })
            model.rename(directory)
        return directory


def ensure_file_bundle(name, files, cache_root):
    """Prepare a fixed, audited set of files without cloning or unpacking code."""
    asset = {'url': 'pinned-bundle:' + name,
             'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
             'files': list(files), 'file_sha256': {key: item['sha256'] for key, item in files.items()}}
    cache_root = Path(cache_root)
    if cache_root.is_symlink():
        raise OncoTracerError('Variant resource cache must not be a symlink')
    cache_root.mkdir(parents=True, exist_ok=True)
    destination = cache_root / name
    fd = os.open(cache_root / ('.' + name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OncoTracerError('Variant resource cache is being prepared by another run') from error
        if _cached(destination, asset):
            return destination
        print(f'Preparing checksum-verified {name} at run time', flush=True)
        with tempfile.TemporaryDirectory(prefix='.' + name + '-', dir=cache_root) as temporary:
            stage = Path(temporary) / 'files'
            stage.mkdir()
            for relative, item in files.items():
                parts = PurePosixPath(relative)
                if parts.is_absolute() or '..' in parts.parts or not parts.parts:
                    raise OncoTracerError('Unsafe public resource path')
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                _download(item, target)
                if item.get('executable'):
                    target.chmod(0o755)
            atomic_write_json(stage / 'resource.json', {**asset, 'files': asset['file_sha256'],
                                                       'assets': files, 'license': FFPERASE_LICENSE})
            stage.rename(destination)
    return destination


def prepare_variant_resources(request, root):
    """Return a resolved request; custom paths and the input YAML stay unchanged."""
    pending = ffperase_pending(request)
    if pending:
        preflight_ffperase_runtime(request)
        source, models = ffperase_asset_paths(request)
        if source is None:
            source = ensure_file_bundle('ffperase-source-' + FFPERASE_REVISION, FFPERASE_SOURCE_FILES, Path(root) / 'resources')
        if models is None:
            models = ensure_file_bundle('ffperase-models-' + FFPERASE_MODEL_REVISION, FFPERASE_MODEL_FILES, Path(root) / 'resources')
        request = replace(request, ffperase_root=source, ffperase_models=models)
    if request.clair3_auto and 'clair3' in request.callers:
        model = ensure_clair3_model(request.ont_profile, Path(root) / 'resources')
        request = replace(request, clair3_model=model)
    return request
