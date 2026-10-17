"""Validate ClinVar against a FASTA and repair absent contig declarations.

NCBI releases can omit ##contig lines. Discover their actual record chromosomes
before adding header metadata; never change alleles, records or the source file.
"""
import argparse
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def text_open(path):
    with Path(path).open('rb') as handle:
        compressed = handle.read(2) == b'\x1f\x8b'
    return gzip.open(path, 'rt') if compressed else Path(path).open()


def chromosome_name(chrom, lengths):
    if chrom in lengths:
        return chrom
    alt = chrom[3:] if chrom.startswith('chr') else 'chr' + chrom
    if alt in lengths:
        return alt
    if chrom in {'M', 'MT', 'chrM', 'chrMT'}:
        return next((name for name in ('chrM', 'MT', 'M', 'chrMT') if name in lengths), None)
    return None


def inspect(source, reference, *, allow_test_reference=False):
    lengths = {line.split('\t')[0]: int(line.split('\t')[1])
               for line in Path(str(reference) + '.fai').read_text().splitlines()}
    header = []
    names = []
    with text_open(source) as handle:
        for line in handle:
            if not line.startswith('#'):
                raise ValueError('ClinVar is missing its #CHROM header')
            header.append(line)
            if line.startswith('#CHROM\t'):
                break
        else:
            raise ValueError('ClinVar is missing its #CHROM header')
        text = ''.join(header)
        if not allow_test_reference and not re.search(r'GRCh38|hg38', text, re.I):
            raise ValueError('ClinVar header does not identify GRCh38/hg38')
        names = re.findall(r'##contig=<ID=([^,>]+)', text)
        missing = not names
        if missing:
            observed = set()
            for line in handle:
                if not line.strip() or line.startswith('#'):
                    continue
                chrom, separator, rest = line.partition('\t')
                if not separator:
                    raise ValueError('Malformed ClinVar VCF record')
                if chrom not in observed:
                    observed.add(chrom)
                    names.append(chrom)
    mapping = {name: chromosome_name(name, lengths) for name in names}
    usable = {name: target for name, target in mapping.items() if target}
    if not usable:
        raise ValueError('ClinVar and FASTA have no matching contigs '
                         f'(ClinVar: {names[:5]}; FASTA: {list(lengths)[:5]})')
    return header, names, usable, lengths, missing


def prepare(source, reference, bcftools, cache, *, allow_test_reference=False):
    source, reference, cache = Path(source).resolve(), Path(reference).resolve(), Path(cache)
    identity = {'source': str(source), 'source_sha256': digest(source),
                'fai_sha256': digest(str(reference) + '.fai'),
                'code_sha256': digest(__file__), 'allow_test_reference': allow_test_reference}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = cache / key
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = directory / 'complete.json'
        try:
            saved = json.loads(receipt.read_text())
            result = directory / saved['file']
            if result.parent == directory and saved['identity'] == identity and digest(result) == saved['sha256']:
                return result
        except (OSError, ValueError, KeyError):
            pass
        header, names, mapping, lengths, missing = inspect(
            source, reference, allow_test_reference=allow_test_reference)
        if not missing:
            return source
        declarations = [f'##contig=<ID={name}' +
                        (f',length={lengths[mapping[name]]}' if name in mapping else '') + '>\n'
                        for name in names]
        # Temporary files live on the same filesystem for atomic publication.
        with tempfile.TemporaryDirectory(prefix='header-', dir=directory) as temporary:
            temporary = Path(temporary)
            header_path = temporary / 'header.vcf'
            header_path.write_text(''.join(header[:-1] + declarations + header[-1:]))
            with source.open('rb') as handle:
                compressed = handle.read(2) == b'\x1f\x8b'
            name = 'clinvar.with_contigs.vcf' + ('.gz' if compressed else '')
            output = temporary / name
            subprocess.run([str(bcftools), 'reheader', '-h', str(header_path),
                            '-o', str(output), str(source)], check=True)
            # Verify compatibility on the actual emitted file before publication.
            inspect(output, reference, allow_test_reference=allow_test_reference)
            record = {'identity': identity, 'file': name, 'sha256': digest(output),
                      'header_contigs_added': names, 'matching_contigs': mapping}
            output.replace(directory / name)
            pending = temporary / 'complete.json'
            pending.write_text(json.dumps(record, indent=2) + '\n')
            pending.replace(receipt)
        return directory / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('clinvar', 'reference', 'bcftools', 'cache', 'result'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = prepare(args.clinvar, args.reference, args.bcftools, args.cache)
    Path(args.result).write_text(json.dumps({'clinvar': str(result)}) + '\n')


if __name__ == '__main__':
    main()
