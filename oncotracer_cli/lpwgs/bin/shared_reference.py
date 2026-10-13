"""Read-only cross-sample reuse of completed GLIMPSE reference preparation.

Sample-dependent phases and ligations are never reused here. Reference panels
can exclude a target sample, so the old/new exclusion sets must match exactly.
"""
import ast
import gzip
import hashlib
import json
import os
import re
import shlex
import subprocess
from functools import lru_cache
from pathlib import Path

from continuation import metadata
from glimpse_continuation import (chunk_regions, completed_files, executable,
                                  reference_outputs, unchanged_before, usable_tool)
from glimpse_download import INTEGRITY_VERSION, PANEL_EXPECTATIONS, validate_file
from glimpse2 import VERSION


def reference_code_digest(path):
    tree = ast.parse(Path(path).read_text())
    functions = {'run', 'sha256', 'tool', 'validate_panel_contig', 'reference'}
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'VERSION', 'PANEL_BASE', 'TOOLS'} for t in node.targets):
            keep.append(node)
    if {n.name for n in keep if isinstance(n, ast.FunctionDef)} != functions:
        raise ValueError('Reference implementation is incomplete')
    return hashlib.sha256(ast.dump(ast.Module(body=keep, type_ignores=[]), include_attributes=False).encode()).hexdigest()


def _identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=128)
def _digest_cached(path, size, mtime, ctime):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(8*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(path):
    return _digest_cached(*_identity(path))


@lru_cache(maxsize=64)
def _validate_panel(path, size, mtime, ctime, expected_json, receipt_json):
    return validate_file(path, expected=json.loads(expected_json),
                         receipt=json.loads(receipt_json), compressed=True)


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _command(work):
    tokens = shlex.split((work/'.command.sh').read_text(), comments=True)
    if len(tokens) != 9 or Path(tokens[1]).name != 'glimpse2.py' or tokens[2] != 'reference':
        return None
    options = dict(zip(tokens[3::2], tokens[4::2]))
    if set(options) != {'--config', '--chrom', '--threads'} or not options['--threads'].isdigit() or int(options['--threads']) < 1:
        return None
    path = work/options['--config']
    raw = path.read_bytes()
    if path.stem != hashlib.sha256(raw).hexdigest():
        raise ValueError('Task runtime checksum mismatch')
    return options, json.loads(raw), work/tokens[1]


def _candidates(donors):
    seen = set()
    for directory in donors:
        donor = Path(directory).resolve()
        # Published provenance symlinks retain the actual historical work path.
        for proof in sorted((donor/'imputation').rglob('reference.*.provenance.json')):
            work = proof.resolve().parent
            if work not in seen:
                seen.add(work); yield work
        for base in (donor/'work', donor):
            for script in sorted(base.glob('*/*/.command.sh')):
                if script.parent not in seen:
                    seen.add(script.parent); yield script.parent


def _tool_match(old, current, name, begin, finish):
    a, b = executable(old, name), executable(current, name)
    if not unchanged_before(a, begin):
        if name != 'GLIMPSE2_chunk' or not usable_tool(old, name, begin, finish):
            raise ValueError(f'Historical {name} changed after execution')
    if not _same_file(a, b) and digest(a) != digest(b):
        raise ValueError(f'{name} binary differs')
    if name == 'bcftools' and old.get('versions', {}).get(name) != current.get('versions', {}).get(name):
        raise ValueError('bcftools version differs')
    return {'path': str(b.resolve()), 'sha256': digest(b)}


def _output_contig(bcftools, reference, chrom, expected_length):
    text = subprocess.run([str(bcftools), 'index', '-s', str(reference)],
                          check=True, capture_output=True, text=True).stdout
    rows = [line.split('\t') for line in text.splitlines() if line.strip()]
    if len(rows) != 1 or rows[0][0] != chrom or int(rows[0][1]) != expected_length or int(rows[0][2]) < 1:
        raise ValueError('Prepared BCF index contig/length differs or reference is empty')


def _samples(bcftools, path):
    return set(subprocess.run([str(bcftools), 'query', '-l', str(path)], check=True,
                              capture_output=True, text=True).stdout.splitlines())


def _verify(work, config, code_digest):
    description = _command(work)
    if description is None:
        return None
    options, old, code = description
    chrom = options['--chrom']
    if chrom not in config.get('glimpse_chromosomes', []):
        return None
    if not completed_files(work, []):
        raise ValueError('Reference task did not finish successfully')
    if reference_code_digest(code) != code_digest:
        raise ValueError('Reference-stage code differs')
    if not _same_file(old['reference'], config['reference']):
        raise ValueError('Reference FASTA is not the identical file (symlinks/hardlinks allowed)')
    original_fasta = next((r for r in old.get('resources', []) + old.get('inputs', [])
                           if _same_file(r['path'], old['reference'])), None)
    if original_fasta is None or metadata(original_fasta['path']) != original_fasta:
        raise ValueError('Historical reference FASTA fingerprint is missing or changed')
    if bool(old.get('allow_test_reference')) != bool(config.get('allow_test_reference')):
        raise ValueError('Reference chunking mode differs')
    custom_old, custom_new = old.get('glimpse_reference_dir'), config.get('glimpse_reference_dir')
    if bool(custom_old) != bool(custom_new) or (custom_old and Path(custom_old).resolve() != Path(custom_new).resolve()):
        raise ValueError('Custom reference-panel selection differs')
    begin = (work/'.command.begin').stat().st_mtime_ns
    finish = (work/'.exitcode').stat().st_mtime_ns
    if not unchanged_before(old['reference']+'.fai', begin):
        raise ValueError('Historical FASTA index changed')
    if digest(old['reference']+'.fai') != digest(config['reference']+'.fai'):
        raise ValueError('Reference FASTA index differs')
    tools = {name: _tool_match(old, config, name, begin, finish) for name in ('bcftools', 'GLIMPSE2_chunk')}
    # Existing validator requires original literal FASTA path in provenance.
    checking = dict(config, reference=old['reference'])
    files = reference_outputs(work, chrom, checking, begin)
    if not files:
        raise ValueError('Reference outputs/provenance failed continuation validation')
    proof = json.loads((work/f'reference.{chrom}.provenance.json').read_text())
    if proof.get('assembly') != 'GRCh38' or proof.get('version') != VERSION or proof.get('variants') != 'biallelic SNPs':
        raise ValueError('Reference build/version/variant type differs')
    lengths = {f[0]: int(f[1]) for line in Path(config['reference']+'.fai').read_text().splitlines() if (f := line.split('\t'))}
    expected_length = lengths[chrom]
    if proof.get('fasta_contig_length') != expected_length or proof.get('source_contig') not in {chrom, chrom.removeprefix('chr'), 'chr'+chrom.removeprefix('chr')}:
        raise ValueError('Reference provenance contig/length differs')
    receipt = proof['panel_integrity']
    if receipt.get('integrity_version') != INTEGRITY_VERSION or not receipt.get('sha256'):
        raise ValueError('Reference panel lacks current integrity receipt')
    expected = PANEL_EXPECTATIONS['chr'+chrom.removeprefix('chr')] if proof.get('panel_url') else None
    panel = _validate_panel(*_identity(proof['panel']), json.dumps(expected, sort_keys=True), json.dumps(receipt, sort_keys=True))
    if panel['sha256'] != receipt['sha256']:
        raise ValueError('Reference-panel SHA256 changed')
    bcftools = executable(config, 'bcftools')
    panel_samples = _samples(bcftools, proof['panel'])
    old_excluded = {old['sample']} & panel_samples
    new_excluded = {config['sample']} & panel_samples
    if old_excluded != new_excluded:
        raise ValueError('Target-dependent panel sample exclusion differs')
    if _samples(bcftools, files[1]) != panel_samples - old_excluded:
        raise ValueError('Prepared reference sample membership differs')
    _output_contig(bcftools, files[1], chrom, expected_length)
    for regions in chunk_regions(files[0]).values():
        for region in regions:
            match = re.fullmatch(r'([^:]+):(\d+)-(\d+)', region)
            if not match or match[1] != chrom or not 1 <= int(match[2]) <= int(match[3]) <= expected_length:
                raise ValueError('Invalid chunk region/contig')
    map_path = Path(proof['genetic_map'])
    with (gzip.open(map_path, 'rb') if map_path.suffix == '.gz' else map_path.open('rb')) as src:
        if src.read() != Path(files[3]).read_bytes():
            raise ValueError('Prepared genetic map differs from its source')
    evidence = {'work': str(work), 'donor_sample': old['sample'], 'requested_sample': config['sample'],
                'chromosome': chrom, 'version': VERSION, 'reference_code_sha256': code_digest,
                'reference_fasta': str(Path(config['reference']).resolve()), 'panel': panel,
                'excluded_reference_samples': sorted(old_excluded), 'tools': tools,
                'outputs': [{'path': p, 'sha256': digest(p), **metadata(p)} for p in files]}
    return chrom, files, evidence


def collect_shared_references(config, root, donors):
    """Return {'references': chromosome->paths, 'audit': accepted/rejected}.

    Only supplied donors are searched. Historical outputs/snapshots are never
    modified. Hashes are cached within this process, keyed by file identity.
    """
    result = {'references': {}, 'audit': {'scope': 'reference_only', 'accepted': [], 'rejected': []}}
    if not config.get('glimpse2'):
        return result
    code_digest = reference_code_digest(Path(root)/'bin/glimpse2.py')
    for work in _candidates(donors):
        try:
            verified = _verify(work, config, code_digest)
            if verified:
                chrom, files, evidence = verified
                if chrom not in result['references']:
                    result['references'][chrom] = files
                    result['audit']['accepted'].append(evidence)
        except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError) as error:
            result['audit']['rejected'].append({'work': str(work), 'reason': str(error)})
    return result
