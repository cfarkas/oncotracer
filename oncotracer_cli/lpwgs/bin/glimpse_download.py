"""Integrity-checked, resumable downloads for public GLIMPSE2 resources.

Official panels use the publisher's byte count and MD5 in addition to gzip CRC.
A local SHA-256 receipt alone is not proof that a download was originally valid.
"""
import fcntl
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

INTEGRITY_VERSION = 2
PANEL_MANIFEST_URL = ('https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/'
                      '1000G_2504_high_coverage/working/20201028_3202_phased/'
                      'phased-manifest_July2021.tsv')
PANEL_MANIFEST_SHA256 = 'd0a7927b06588a01ed10cb763cb4e8bc891e2af7bdc592c3e318b391fd3f1fc4'
# The provider omits leading zeroes in some MD5 strings; restore 32 hex digits.
PANEL_EXPECTATIONS = {'chr1': {'bytes': 2788726752, 'md5': '8d286b08ec8979c7d977724faf2ecf27'}, 'chr2': {'bytes': 2927628316, 'md5': '4e7ec2cb793a03337c34fe4a347b2b39'}, 'chr3': {'bytes': 2440760504, 'md5': 'ca2f9b4ee4e441debf25e64ef1d66d49'}, 'chr4': {'bytes': 2422981659, 'md5': '8eb36436c8b1dc7f53e21a0a3ef07c5a'}, 'chr5': {'bytes': 2212310094, 'md5': '8ea6ddbfd7f03698505256d57f3ee1bd'}, 'chr6': {'bytes': 2175031647, 'md5': '13f6dddb290883b926f147bde1533225'}, 'chr7': {'bytes': 2037578036, 'md5': '14d0eba84ce07fe54833734421b56a9d'}, 'chr8': {'bytes': 1885031386, 'md5': '38014c6362ecfd2111c137cca81893d4'}, 'chr9': {'bytes': 1538944815, 'md5': '52a1f3a6832db74002f1552eb791eb52'}, 'chr10': {'bytes': 1734834612, 'md5': '3164712a612107af3b6846b82a58bf79'}, 'chr11': {'bytes': 1680229702, 'md5': 'd4b5dad3aac770b07840af19d01f25c6'}, 'chr12': {'bytes': 1647441510, 'md5': '29bf540e16267ab91310dd0537a089a4'}, 'chr13': {'bytes': 1246175297, 'md5': 'c8aea983f2ed4accf1b9d9ab1f72a52c'}, 'chr14': {'bytes': 1124374896, 'md5': 'c4e0d9eaceb2d4a10c1749ccbfad71b6'}, 'chr15': {'bytes': 1031430702, 'md5': 'fcf9f0363253a49a7460b4f2d647d740'}, 'chr16': {'bytes': 1124615117, 'md5': '7933b3977d670abee92ac98e547a6fe5'}, 'chr17': {'bytes': 1004514408, 'md5': '06ef514d7b8ce9d73edbad54435418e9'}, 'chr18': {'bytes': 969211065, 'md5': 'f854768a4f1f6b0635fb38019118c25c'}, 'chr19': {'bytes': 832705170, 'md5': '459c7d0846798aa0612fb04a71e56b4e'}, 'chr20': {'bytes': 800816850, 'md5': '521d2011fbf06517f34edd181b6283e7'}, 'chr21': {'bytes': 496155724, 'md5': '80e99e81f0bc739e6e36843a9c5c46df'}, 'chr22': {'bytes': 519930289, 'md5': 'aaf19d9c7ffcd86b34275899ddc898e7'}}


PANEL_BASE = PANEL_MANIFEST_URL.rsplit('/', 1)[0]+'/'
PANEL_ALTERNATE_BASE = ('https://ftp.ebi.ac.uk/1000g/ftp/data_collections/'
                        '1000G_2504_high_coverage/working/20201028_3202_phased/')


def reference_sources(url, expected=None):
    """Alternate transport only for the exact, publisher-pinned public panel.

    Both official EBI endpoints serve the same release. Keep the canonical URL
    as cache identity, and require the full published checksum after any transfer.
    Unknown/custom resources are never guessed or redirected to another panel.
    """
    if not expected or not url.startswith(PANEL_BASE):
        return [url]
    name = url[len(PANEL_BASE):]
    for chrom, pinned in PANEL_EXPECTATIONS.items():
        filename = f'CCDG_14151_B01_GRM_WGS_2020-08-05_{chrom}.filtered.shapeit2-duohmm-phased.vcf.gz'
        if name == filename and all(expected.get(k) == pinned[k] for k in ('bytes', 'md5')):
            return [url, PANEL_ALTERNATE_BASE+name]
    return [url]


def resource_receipt(url, evidence, previous=None):
    """Retain transport/recovery provenance when validating a completed file."""
    previous = previous or {}
    provenance = {k: previous[k] for k in ('transport_urls', 'last_transport_url', 'recovery') if k in previous}
    return {**provenance, 'url': url, **evidence}


class IntegrityError(ValueError):
    """A completed file cannot be used as a reference resource."""


def read_receipt(path):
    try:
        result = json.loads(Path(path).read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp.'+uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, indent=2)+'\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_gzip(path):
    """Read every gzip/BGZF member, checking CRC and truncation; write no data."""
    path = Path(path)
    with path.open('rb') as handle:
        if handle.read(2) != b'\x1f\x8b':
            raise IntegrityError(f'Not a gzip/BGZF file: {path}')
    executable = shutil.which('gzip')
    if executable:
        result = subprocess.run([executable, '-t', '--', str(path)], capture_output=True, text=True)
        if result.returncode:
            raise IntegrityError(f'Compressed-file integrity failed for {path}: {result.stderr.strip()}')
    else:
        try:
            with gzip.open(path, 'rb') as handle:
                for _ in iter(lambda: handle.read(4*1024*1024), b''):
                    pass
        except (gzip.BadGzipFile, EOFError, zlib.error) as error:
            raise IntegrityError(f'Compressed-file integrity failed for {path}: {error}') from error


def validate_file(path, *, expected=None, receipt=None, compressed=False):
    path = Path(path)
    size = path.stat().st_size
    if not size:
        raise IntegrityError(f'Empty resource: {path}')
    if expected and size != expected['bytes']:
        raise IntegrityError(f'Publisher size mismatch for {path.name}: {size} bytes; expected {expected["bytes"]}')
    digest = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False) if expected else None
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4*1024*1024), b''):
            digest.update(block)
            if md5 is not None:
                md5.update(block)
    checksum = digest.hexdigest()
    if expected and md5.hexdigest() != expected['md5']:
        raise IntegrityError(f'Publisher MD5 mismatch for {path.name}: {md5.hexdigest()}; expected {expected["md5"]}')
    old = receipt or {}
    if not expected and old.get('sha256') and old['sha256'] != checksum:
        raise IntegrityError(f'Cached SHA-256 mismatch for {path.name}')
    trusted_crc = (old.get('integrity_version') == INTEGRITY_VERSION
                   and old.get('gzip_crc_validated') is True and old.get('sha256') == checksum)
    if compressed and not trusted_crc:
        print(f'Checking every compressed block: {path.name}', flush=True)
        validate_gzip(path)
    result = {'bytes': size, 'sha256': checksum, 'integrity_version': INTEGRITY_VERSION,
              'gzip_crc_validated': bool(compressed)}
    if expected:
        result.update(md5=md5.hexdigest(), expected_bytes=expected['bytes'], expected_md5=expected['md5'],
                      checksum_source=expected.get('source', PANEL_MANIFEST_URL))
    return result


def quarantine(path, reason):
    """Preserve unusable managed downloads and their receipts, never sample data."""
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = path.parent/'quarantine'/f'{stamp}-{uuid.uuid4().hex[:8]}-{path.name}'
    directory.mkdir(parents=True)
    saved = []
    for source in [path, Path(str(path)+'.source.json'), Path(str(path)+'.tbi'), Path(str(path)+'.csi'), Path(str(path)+'.aria2')]:
        if source.exists():
            source.rename(directory/source.name)
            saved.append(source.name)
    atomic_json(directory/'reason.json', {'reason': str(reason), 'original_path': str(path),
                                          'saved_files': saved, 'time_utc': stamp})
    print(f'Resource failed validation: {reason}\nPreserved invalid file in {directory}', flush=True)
    return directory


def sequential_prefix_unchanged(partial, state):
    if state.get('engine') != 'aria2c' or not partial.exists():
        return True
    st = partial.stat()
    return state.get('sequential_prefix') == {'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns}


def transfer(url, partial, *, engine='auto', network_attempts=8, retry_delay=5):
    """Bounded network retries, preserving sequential bytes or aria2 piece state.

    A new curl invocation recalculates the resume offset after each disconnect.
    aria2's .aria2 sidecar is essential: a segmented file's length is NOT its
    downloaded byte count, and must never be used as a curl resume offset.
    """
    if network_attempts < 1 or engine not in {'auto', 'aria2c', 'curl'}:
        raise ValueError('Invalid download engine or network attempt count')
    partial = Path(partial).absolute()
    control = Path(str(partial)+'.aria2')
    aria = shutil.which('aria2c') if engine != 'curl' else None
    use_aria = bool(aria and urlsplit(url).scheme in {'http', 'https', 'ftp'})
    if engine == 'aria2c' and not use_aria:
        raise ValueError('aria2c is unavailable or the URL scheme is unsupported')
    if control.exists() and not use_aria:
        raise ValueError(f'aria2c is needed to resume {partial.name}; keep the .part and .aria2 files together')
    state_path = Path(str(partial)+'.source.json')
    state = read_receipt(state_path)
    sources = reference_sources(url, state.get('expected'))
    if not control.exists() and not sequential_prefix_unchanged(partial, state):
        raise IntegrityError(f'Missing aria2 progress file for {partial.name}; cannot infer completed pieces from file length')
    if use_aria:
        command = [aria, '--no-conf=true', '--no-netrc=true', '--continue=true',
                   '--always-resume=true', '--auto-file-renaming=false', '--allow-overwrite=false',
                   '--file-allocation=none', '--split=4', '--max-connection-per-server=4',
                   '--min-split-size=16M', '--auto-save-interval=5', '--max-tries=5',
                   '--retry-wait=5', '--max-file-not-found=1', '--connect-timeout=30',
                   '--timeout=120', '--lowest-speed-limit=1024', '--check-certificate=true',
                   '--enable-color=false', '--summary-interval=30', '--console-log-level=warn',
                   '--download-result=hide', '--dir', str(partial.parent), '--out', partial.name]
        transient = {1, 2, 5, 6, 19, 22, 29}
    else:
        command = ['curl', '--disable', '--fail', '--location', '--retry', '0',
                   '--connect-timeout', '30', '--speed-time', '120', '--speed-limit', '1024',
                   '--continue-at', '-', '--output', str(partial), '--write-out', '%{http_code}']
        transient = {5, 6, 7, 16, 18, 28, 35, 52, 55, 56, 92, 95}
    if use_aria and partial.exists() and not control.exists():
        st = partial.stat()
        state['sequential_prefix'] = {'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns}
    atomic_json(state_path, {**state, 'url': url, 'engine': 'aria2c' if use_aria else 'curl'})
    for attempt in range(1, network_attempts+1):
        source = sources[(attempt-1) % len(sources)]
        state = read_receipt(state_path)
        transports = list(dict.fromkeys(state.get('transport_urls', [])+[source]))
        atomic_json(state_path, {**state, 'url': url, 'transport_urls': transports,
                                 'last_transport_url': source})
        invocation = command+[source]
        progress = ('saved aria2 pieces' if control.exists() else
                    f'{partial.stat().st_size if partial.exists() else 0} sequential bytes')
        print(f'Network attempt {attempt}/{network_attempts} using {"aria2c" if use_aria else "curl"}; '
              f'resuming {progress}; source {urlsplit(source).netloc or source}', flush=True)
        print('+ '+' '.join(invocation), flush=True)
        result = subprocess.run(invocation, stdout=None if use_aria else subprocess.PIPE, text=True)
        retry = result.returncode in transient
        if not use_aria and result.returncode == 22:
            retry = (result.stdout or '').strip() in {'408', '425', '429', '500', '502', '503', '504'}
        if not result.returncode or not retry:
            return result
        if use_aria and not control.exists() and not sequential_prefix_unchanged(partial, read_receipt(state_path)):
            print('aria2 stopped without a progress file; retaining data for validation on restart.', flush=True)
            return result
        if attempt < network_attempts:
            delay = min(retry_delay * 2**(attempt-1), 30)
            action = (f'Switching to {urlsplit(sources[attempt % len(sources)]).netloc}'
                      if len(sources) > 1 else 'Retrying the same source')
            print(f'Temporary download failure (exit {result.returncode}); saved progress retained. '
                  f'{action} in {delay}s.', flush=True)
            time.sleep(delay)
    print(f'Network retry limit reached; saved download progress retained at {partial}', flush=True)
    return result



def recover_quarantined(url, destination, expected):
    # Imported lazily to keep download/repair responsibilities separate.
    from glimpse_repair import recover
    evidence = recover(url, destination, expected, validate_file=validate_file, read_receipt=read_receipt)
    if evidence:
        atomic_json(str(destination)+'.source.json', {'url': url, **evidence})
        return Path(destination)
    return None


def download(url, destination, *, expected=None, attempts=3):
    """Resume intact partials; quarantine corrupt files and retry from byte zero.

    Only this function's managed destination may be replaced. User-supplied panel
    paths are validated separately and never repaired or overwritten implicitly.
    """
    if attempts < 1:
        raise ValueError('Download attempts must be at least one')
    p = Path(destination)
    p.parent.mkdir(parents=True, exist_ok=True)
    compressed = p.suffix in ('.gz', '.bgz', '.bgzf', '.bcf')
    receipt_path = Path(str(p)+'.source.json')
    partial = Path(str(p)+'.part')
    partial_receipt = Path(str(partial)+'.source.json')
    with open(str(p)+'.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if p.is_file():
            old = read_receipt(receipt_path)
            try:
                if old.get('url') not in (None, url) and not expected:
                    raise IntegrityError(f'Cached source URL changed for {p.name}')
                evidence = validate_file(p, expected=expected, receipt=old, compressed=compressed)
            except IntegrityError as error:
                quarantine(p, error)
            else:
                atomic_json(receipt_path, resource_receipt(url, evidence, old))
                print(f'Using verified cached resource: {p.name}', flush=True)
                return p
        old_partial = read_receipt(partial_receipt)
        if (partial.exists() or Path(str(partial)+'.aria2').exists()) and old_partial.get('url') not in (None, url):
            quarantine(partial, 'Partial download belongs to a different URL')
        recovered = recover_quarantined(url, p, expected)
        if recovered:
            return recovered
        last_error = None
        for attempt in range(1, attempts+1):
            # A transfer interrupted after its last byte can be promoted safely
            # only with the independent published size/checksum and a CRC pass.
            if (partial.exists() and expected and partial.stat().st_size >= expected['bytes']
                    and not Path(str(partial)+'.aria2').exists()):
                try:
                    evidence = validate_file(partial, expected=expected, compressed=compressed)
                except IntegrityError as error:
                    quarantine(partial, error)
                    recovered = recover_quarantined(url, p, expected)
                    if recovered:
                        return recovered
                else:
                    partial.replace(p)
                    atomic_json(receipt_path, resource_receipt(url, evidence, read_receipt(partial_receipt)))
                    partial_receipt.unlink(missing_ok=True)
                    return p
            # A lost segmented-progress file cannot safely become a sequential prefix.
            state = read_receipt(partial_receipt)
            if not Path(str(partial)+'.aria2').exists() and not sequential_prefix_unchanged(partial, state):
                quarantine(partial, 'Missing aria2 progress file; incomplete pieces cannot be recovered safely')
                state = {}
            atomic_json(partial_receipt, {**state, 'url': url, 'expected': expected})
            print(f'Download attempt {attempt}/{attempts}: {p.name}; '
                  f'{"resuming saved pieces" if Path(str(partial)+".aria2").exists() else "resuming sequential prefix"}', flush=True)
            result = transfer(url, partial)
            if result.returncode:
                # A late disconnect can happen after the final byte (and a
                # subsequent resume can return 416). Accept only independent
                # size/checksum/CRC proof, never a segmented file's length alone.
                if (expected and partial.exists() and partial.stat().st_size == expected['bytes']
                        and not Path(str(partial)+'.aria2').exists()):
                    try:
                        evidence = validate_file(partial, expected=expected, compressed=compressed)
                    except IntegrityError as error:
                        last_error = error
                        quarantine(partial, error)
                        recovered = recover_quarantined(url, p, expected)
                        if recovered:
                            return recovered
                        continue
                    partial.replace(p)
                    atomic_json(receipt_path, resource_receipt(url, evidence, read_receipt(partial_receipt)))
                    partial_receipt.unlink(missing_ok=True)
                    return p
                # curl 33 / HTTP 416 mean the existing range cannot be resumed.
                # Network retries happen in transfer(); exhaustion retains progress.
                is_aria = Path(result.args[0]).name == 'aria2c'
                range_rejected = (result.returncode == 8 if is_aria else
                                  result.returncode == 33 or (result.stdout or '').strip() == '416')
                if partial.exists() and range_rejected:
                    last_error = IntegrityError(f'Server rejected resume range for {p.name}')
                    quarantine(partial, last_error)
                    continue
                raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout)
            try:
                if Path(str(partial)+'.aria2').exists():
                    raise IntegrityError(f'Download reported success but still has incomplete aria2 pieces: {p.name}')
                evidence = validate_file(partial, expected=expected, compressed=compressed)
            except IntegrityError as error:
                last_error = error
                quarantine(partial, error)
                recovered = recover_quarantined(url, p, expected)
                if recovered:
                    return recovered
                continue
            partial.replace(p)
            atomic_json(receipt_path, resource_receipt(url, evidence, read_receipt(partial_receipt)))
            partial_receipt.unlink(missing_ok=True)
            return p
        raise IntegrityError(f'Could not obtain an intact {p.name} after {attempts} download attempts. '
                             f'No invalid reference was accepted. Last error: {last_error}')
