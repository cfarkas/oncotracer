#!/usr/bin/env python3
"""Fetch the pinned public QuickStart reads using ENA's FTP and HTTPS services."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from oncotracer_cli.cli import QS1_FILES
from oncotracer_cli.runtime import OncoTracerError, atomic_write_json, download, sha256_file


def sources(url: str) -> tuple[str, str]:
    """Only the public ENA FASTQ host has a documented anonymous FTP alternative."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'ftp.sra.ebi.ac.uk'
            or not re.fullmatch(r'/vol1/fastq/[A-Za-z0-9_./-]+\.fastq\.gz', parsed.path)
            or '..' in parsed.path.split('/') or parsed.query or parsed.fragment):
        raise ValueError('Expected a canonical public ENA FASTQ URL')
    return urlunsplit(('ftp', parsed.netloc, parsed.path, '', '')), url


def inputs(suite: str) -> list[dict]:
    if suite == 'quickstart1':
        return [dict(url=url, relative_path=path, bytes=size, md5=md5)
                for url, path, size, md5 in QS1_FILES]
    if suite != 'quickstart2':
        raise ValueError('Unknown QuickStart suite')
    with (REPO / 'examples/hcc1143_lpwgs/manifest.tsv').open(newline='') as stream:
        return [dict(url=row['url'], relative_path='public/hcc1143_lpwgs/' + row['filename'],
                     bytes=int(row['bytes']), md5=row['md5']) for row in csv.DictReader(stream, delimiter='\t')]


def valid(path: Path, spec: dict) -> bool:
    if not path.is_file() or path.stat().st_size != spec['bytes']:
        return False
    digest = hashlib.md5()  # Publisher's file identity, not authentication.
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == spec['md5']


def copy_verified(source: Path, target: Path, spec: dict) -> bool:
    """Publish an independent copy after checking source and copy in full."""
    if not valid(source, spec):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        if not valid(temporary, spec):
            raise OncoTracerError('Cached public input changed while copying')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def reuse_previous_input(suite: str, spec: dict, target: Path, reuse_root: Path | None) -> str | None:
    if reuse_root is None:
        return None
    reuse_root = reuse_root.expanduser().resolve()
    for run in sorted(reuse_root.glob('oncotracer-parity-*-*-' + suite), reverse=True):
        if not re.fullmatch(r'oncotracer-parity-[0-9]+-[0-9]+-' + suite, run.name):
            continue
        source = run / 'input' / spec['relative_path']
        if source.resolve() == target.resolve() or not source.resolve().is_relative_to(reuse_root):
            continue
        if not valid(source, spec):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.' + target.name + '.cache-' + str(os.getpid()))
        try:
            shutil.copyfile(source, temporary)
            if not valid(temporary, spec):
                raise OncoTracerError('Cached public input changed while copying')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        print('Reused checksum-verified public input:', target.name, flush=True)
        return str(source.relative_to(reuse_root))
    return None


def fetch_inputs(suite: str, root: Path, receipt: Path | None = None,
                 reuse_root: Path | None = None, cache_root: Path | None = None) -> list[dict]:
    root = root.expanduser().resolve()
    cache_root = cache_root.expanduser().resolve() if cache_root else None
    records = []
    for spec in inputs(suite):
        choices = sources(spec['url'])
        if not isinstance(spec['bytes'], int) or spec['bytes'] <= 0 or not re.fullmatch('[0-9a-f]{32}', spec['md5']):
            raise ValueError('Public input must have a frozen size and MD5')
        relative = Path(spec['relative_path'])
        target = root / relative
        if relative.is_absolute() or '..' in relative.parts or not target.resolve().is_relative_to(root):
            raise ValueError('Public input destination escapes the test directory')
        errors = []
        cache_errors = []
        cached = cache_root / spec['md5'] / relative.name if cache_root else None
        if cached and not cached.resolve().is_relative_to(cache_root):
            raise ValueError('Public input cache destination escapes its directory')
        transport = 'verified_existing'
        reused_from = None
        if cached and not valid(target, spec):
            try:
                if copy_verified(cached, target, spec):
                    transport = 'verified_cache'
                    reused_from = str(cached)
                    print('Reused checksum-verified public cache:', target.name, flush=True)
            except (OSError, OncoTracerError) as error:
                cache_errors.append(str(error))
        if not valid(target, spec):
            reused_from = reuse_previous_input(suite, spec, target, reuse_root)
            if reused_from:
                transport = 'verified_previous_benchmark'
        if not valid(target, spec):
            for url in choices:
                print('Fetching pinned public input:', target.name, 'via', urlsplit(url).scheme, flush=True)
                try:
                    download(url, target, expected_bytes=spec['bytes'], expected_md5=spec['md5'], retries=2)
                    # Recheck even if a downloader erroneously returns success.
                    if not valid(target, spec):
                        raise OncoTracerError('Public input failed final size/MD5 verification')
                    transport = url
                    break
                except OncoTracerError as error:
                    errors.append(dict(url=url, error=str(error)))
                    print('Source failed; trying the next verified transport:', str(error), file=sys.stderr, flush=True)
            else:
                raise OncoTracerError('All ENA transports failed for ' + target.name + ': ' + json.dumps(errors))
        cache_saved = False
        if cached:
            try:
                cache_saved = valid(cached, spec) or copy_verified(target, cached, spec)
            except (OSError, OncoTracerError) as error:
                cache_errors.append(str(error))
        records.append(dict(**spec, transport=transport, sha256=sha256_file(target), failed_sources=errors,
                            reused_from=reused_from, cache_saved=cache_saved, cache_errors=cache_errors))
        if receipt:
            atomic_write_json(receipt, dict(suite=suite, complete=False, files=records))
    if receipt:
        atomic_write_json(receipt, dict(suite=suite, complete=True, files=records))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['quickstart1', 'quickstart2'])
    parser.add_argument('--test-root', required=True, type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--reuse-root', type=Path, help='Reuse checksum-verified public FASTQs from prior benchmark input directories')
    parser.add_argument('--cache-root', type=Path, help='Persistent public FASTQ cache, keyed by publisher MD5; fully checked on every reuse')
    args = parser.parse_args()
    files = fetch_inputs(args.suite, args.test_root, args.receipt, args.reuse_root, args.cache_root)
    print('Verified', len(files), 'public FASTQs;', sum(row['bytes'] for row in files), 'bytes', flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
