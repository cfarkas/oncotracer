"""Recover publisher-pinned resources damaged by embedded upstream error bodies.

Some interrupted EBI responses put <Error> XML inside a binary byte stream.
Resuming at its apparent length then leaves a small corrupt interval. Recovery
uses saved copies or bounded HTTP ranges, but accepts only an exact publisher
MD5/size match AND the normal compressed-file integrity checks.
"""
from contextlib import closing
import hashlib
from http.client import HTTPException
from pathlib import Path
import re
import shutil
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RANGE_BYTES = 1024*1024
MAX_RANGES = 64
MAX_COPIES = 3
ERROR_XML = re.compile(rb'<Error><Code>[A-Za-z0-9_.:-]{1,128}</Code>.{0,4096}?</Error>', re.DOTALL)


class RangeError(ValueError):
    pass


def damaged_ranges(path, size):
    """Locate error messages, including messages crossing read/range boundaries."""
    intervals = set()
    carry = b''
    offset = 0
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4*1024*1024), b''):
            data = carry+block
            base = offset-len(carry)
            for match in ERROR_XML.finditer(data):
                first, last = base+match.start(), base+match.end()-1
                for start in range(first//RANGE_BYTES*RANGE_BYTES, last//RANGE_BYTES*RANGE_BYTES+1, RANGE_BYTES):
                    intervals.add((start, min(start+RANGE_BYTES, size)-1))
                    if len(intervals) > MAX_RANGES:
                        return []
            offset += len(block)
            carry = data[-8192:]
    return sorted(intervals)


def matching_md5(path, expected):
    path = Path(path)
    if path.stat().st_size != expected['bytes']:
        return False
    digest = hashlib.md5(usedforsecurity=False)
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest() == expected['md5']


def fetch_range(url, start, end, total, *, attempts=4, expected=None):
    """Never write a response with the wrong range, length, status or error XML."""
    expected_header = f'bytes {start}-{end}/{total}'
    length = end-start+1
    if start < 0 or end < start or end >= total or length > RANGE_BYTES:
        raise ValueError('Invalid repair byte range')
    from glimpse_download import reference_sources
    sources = reference_sources(url, expected)
    last = None
    for attempt in range(1, attempts+1):
        source = sources[(attempt-1) % len(sources)]
        try:
            request = Request(source, headers={'Range': f'bytes={start}-{end}',
                                            'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
            with closing(urlopen(request, timeout=30)) as response:
                if response.status != 206 or response.headers.get('Content-Range') != expected_header:
                    raise RangeError('Server did not return the exact requested Content-Range')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise RangeError('Unexpected Content-Encoding in byte-range response')
                data = response.read(length+1)
                if len(data) != length:
                    raise RangeError(f'Incomplete or oversized range: received {len(data)}; expected {length}')
                if ERROR_XML.search(data):
                    raise RangeError('Upstream error XML found in repair response')
                return data
        except HTTPError as error:
            if error.code not in {408, 425, 429, 500, 502, 503, 504}:
                raise RangeError(f'Repair request failed with HTTP {error.code}') from error
            last = error
        except (URLError, HTTPException, TimeoutError, OSError, RangeError) as error:
            last = error
        if attempt < attempts:
            print(f'Repair range {start}-{end}: attempt {attempt}/{attempts} failed; retrying ({last})', flush=True)
            time.sleep(min(2**attempt, 10))
    raise RangeError(f'Could not retrieve verified byte range {start}-{end}: {last}')


def candidates(destination, url, expected, read_receipt):
    """Only consider this managed resource's preserved full-length copies."""
    p = Path(destination)
    found = []
    for name in [p.name, p.name+'.part']:
        for source in (p.parent/'quarantine').glob('*/'+name):
            if source.is_symlink() or not source.is_file() or source.stat().st_size != expected['bytes']:
                continue
            receipt = read_receipt(str(source)+'.source.json')
            if receipt.get('url') != url:
                continue
            found.append(source)
    return sorted(found, key=lambda f: f.stat().st_mtime_ns, reverse=True)[:MAX_COPIES]


def recover(url, destination, expected, *, validate_file, read_receipt, allow_network=True):
    """Recover into a new managed file, leaving every source copy unchanged."""
    if not expected:
        return None
    p = Path(destination)
    if p.exists() or Path(str(p)+'.part').exists() or Path(str(p)+'.part.aria2').exists():
        return None
    copies = candidates(p, url, expected, read_receipt)
    plans = [(damaged_ranges(source, expected['bytes']), source) for source in copies]
    plans = sorted(((ranges, source) for ranges, source in plans if ranges), key=lambda x: len(x[0]))
    if not plans:
        return None
    temporary = Path(str(p)+'.repair.part')
    compressed = p.suffix in {'.gz', '.bgz', '.bgzf', '.bcf'}
    try:
        for ranges, source in plans:
            print(f'Recovering {p.name} from preserved copy: {len(ranges)} damaged byte range(s); '
                  'the full publisher checksum is still required.', flush=True)
            shutil.copyfile(source, temporary)
            repaired = []
            with temporary.open('r+b') as out:
                for start, end in ranges:
                    for donor in copies:
                        if donor == source:
                            continue
                        with donor.open('rb') as handle:
                            handle.seek(start)
                            block = handle.read(end-start+1)
                        if len(block) != end-start+1 or ERROR_XML.search(block):
                            continue
                        out.seek(start); out.write(block)
                        repaired.append({'start': start, 'end': end, 'source': str(donor)})
                        break
            if not matching_md5(temporary, expected):
                if not allow_network:
                    continue
                print(f'Requesting {len(ranges)} small range(s) from the source to repair {p.name}.', flush=True)
                repaired = []
                try:
                    with temporary.open('r+b') as out:
                        for start, end in ranges:
                            block = fetch_range(url, start, end, expected['bytes'], expected=expected)
                            out.seek(start); out.write(block)
                            repaired.append({'start': start, 'end': end, 'source': url})
                except RangeError as error:
                    print(f'Range recovery could not finish: {error}', flush=True)
                    continue
            if not matching_md5(temporary, expected):
                print('Recovered copy still fails publisher checksum; it will not be used.', flush=True)
                continue
            # The caller also performs CRC validation. Matching size or fragment
            # agreement alone can never authorize use as an imputation reference.
            integrity = validate_file(temporary, expected=expected, compressed=compressed)
            temporary.replace(p)
            print(f'Recovered and verified {p.name}; avoided a full reference download.', flush=True)
            return {**integrity, 'recovery': {'method': 'verified_byte_ranges',
                    'base_copy': str(source), 'ranges': repaired, 'publisher_checksum_matched': True}}
    finally:
        temporary.unlink(missing_ok=True)
    return None
