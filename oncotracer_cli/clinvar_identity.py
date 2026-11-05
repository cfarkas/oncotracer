"""Verify normalized ClinVar content without mistaking tool timestamps for data.

Original compressed-file hashes remain the provenance binding. Only bcftools
command-history header lines are excluded from the separate content identity;
release metadata, tool versions, definitions and every record byte are retained.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re

POLICY = 'clinvar-vcf-content-v1'
_COMMAND = re.compile(rb'##bcftools_[A-Za-z0-9_]+Command=')


def identity(path, expected_sha256):
    path = Path(path).resolve()
    before = path.stat()
    raw = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            raw.update(block)
    if raw.hexdigest() != expected_sha256:
        raise ValueError('ClinVar resource does not match the sample VCF checksum: ' + str(path))
    content = hashlib.sha256()
    records = 0
    skipped = []
    columns = False
    with gzip.open(path, 'rb') as stream:
        for number, line in enumerate(stream, 1):
            if number == 1 and not line.startswith(b'##fileformat=VCFv4.'):
                raise ValueError('Expected a normalized ClinVar VCF: ' + str(path))
            if not columns and _COMMAND.match(line):
                skipped.append(line.decode('utf-8').rstrip('\r\n'))
                continue
            if line.startswith(b'#CHROM\t'):
                if columns:
                    raise ValueError('Duplicate ClinVar VCF columns')
                columns = True
            elif not line.startswith(b'#'):
                if not columns or len(line.rstrip(b'\r\n').split(b'\t')) < 8:
                    raise ValueError('Malformed ClinVar record: ' + str(path))
                records += 1
            elif columns:
                raise ValueError('Unexpected ClinVar header after records')
            content.update(line)
    if not columns or not records:
        raise ValueError('Empty normalized ClinVar VCF: ' + str(path))
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('ClinVar changed during validation: ' + str(path))
    return {'policy': POLICY, 'path': str(path), 'raw_sha256': raw.hexdigest(),
            'content_sha256': content.hexdigest(), 'records': records,
            'excluded_command_headers': skipped}


def cohort_provenance(headers, sources):
    """Bind every source to its exported raw hash before accepting equivalence."""
    if not sources:
        return None
    if set(sources) != set(headers):
        raise ValueError('ClinVar provenance is required for every cohort sample')
    result = {}
    cache = {}
    for sample, header in headers.items():
        hashes = [line.split('=', 1)[1] for line in str(header).splitlines()
                  if line.startswith('##LPWGSClinVarSHA256=')]
        if len(hashes) != 1 or not re.fullmatch('[0-9a-f]{64}', hashes[0]):
            raise ValueError('Missing or ambiguous ClinVar checksum for ' + sample)
        key = (str(Path(sources[sample]).resolve()), hashes[0])
        if key not in cache:
            cache[key] = identity(*key)
        result[sample] = cache[key]
    if len({v['content_sha256'] for v in result.values()}) != 1:
        raise ValueError('Incompatible ClinVar annotation content or release across cohort samples')
    return {'policy': POLICY, 'content_sha256': next(iter(result.values()))['content_sha256'],
            'samples': result}


def sample_sources(sample_roots):
    """Find preserved normalization inputs in the completed annotation receipts.

    These paths are hints only: cohort_provenance checks the actual file bytes
    against the exported VCF header. No result or checksum is rewritten.
    """
    sources = {}
    for sample, root in sample_roots.items():
        receipts = sorted((Path(root) / 'imputed_clinvar/chromosomes').glob('*.imputed_clinvar.json'))
        for receipt in receipts:
            value = json.loads(receipt.read_text())
            if value.get('sample') != sample:
                raise ValueError('ClinVar receipt sample mismatch: ' + str(receipt))
            path = value.get('clinvar')
            if path and Path(path).is_file():
                sources[sample] = str(Path(path).resolve())
                break
    # Without retained normalization evidence keep the strict raw-hash check.
    return sources if len(sources) == len(sample_roots) else {}
