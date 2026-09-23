"""Pinned Strelka2 short-read adapters; pairing is always explicit.

Official v2.9.10 user guide: https://github.com/Illumina/strelka/tree/v2.9.10/docs/userGuide
No reference genotype, matched sample, or allele fraction is invented.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import shutil

from .runtime import OncoTracerError, require_file

CALLERS = frozenset({'strelka2_germline', 'strelka2_somatic'})
SCRIPTS = {'strelka2_germline': 'configureStrelkaGermlineWorkflow.py',
           'strelka2_somatic': 'configureStrelkaSomaticWorkflow.py'}


def parse_matched_normals(value):
    if value is None or value == '':
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise OncoTracerError('variant_matched_normals must be a JSON object mapping tumor sample IDs to matched normal sample IDs') from exc
    if not isinstance(value, dict):
        raise OncoTracerError('variant_matched_normals must be a JSON object')
    if any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', s)
           for pair in value.items() for s in pair):
        raise OncoTracerError('Matched-normal mapping requires safe sample ID strings')
    if any(t == n for t, n in value.items()):
        raise OncoTracerError('A tumor cannot be its own matched normal')
    return dict(value)


def validate_samples(request, samples, statuses, *, paired=None):
    """Validate known sample assignments without opening BAMs or executing tools."""
    if request is None or not CALLERS.intersection(request.callers):
        return
    samples = set(samples)
    if request.mode != 'illumina':
        raise OncoTracerError('Strelka2 supports paired-end Illumina only')
    if paired is not None:
        missing = sorted(s for s in samples if not paired.get(s))
        if missing:
            raise OncoTracerError('Strelka2 requires paired-end Illumina reads: ' + ', '.join(missing))
    if 'strelka2_somatic' not in request.callers:
        return
    pairs = dict(request.matched_normals)
    tumors = {s for s in samples if statuses.get(s) == 'tumor'}
    if not tumors or any(statuses.get(s) not in {'tumor', 'normal'} for s in samples):
        raise OncoTracerError('Strelka2 somatic requires explicit tumor/normal sample roles')
    if set(pairs) != tumors:
        raise OncoTracerError('Strelka2 somatic requires one explicit matched normal for every tumor, and no extra tumor IDs; expected: ' + ', '.join(sorted(tumors)))
    for tumor, normal in pairs.items():
        if normal not in samples or statuses.get(normal) != 'normal' or tumor == normal:
            raise OncoTracerError(f'Strelka2 matched normal for {tumor} must name a different selected sample explicitly marked normal: {normal}')


def discover_runtime(prefix, callers):
    if platform.system() != 'Linux' or platform.machine().lower() not in {'x86_64', 'amd64'}:
        raise OncoTracerError('The supported Strelka2 binary requires Linux x86_64; use the amd64 Docker runtime')
    requested = [c for c in callers if c in CALLERS]
    result = {}
    if prefix:
        bindir = Path(prefix) / 'bin'
        python = bindir / 'python2.7'
        if not python.is_file() or not os.access(python, os.X_OK):
            raise OncoTracerError('Strelka2 requires a separate Python 2.7 runtime; configure variant_strelka_prefix using environments/native-strelka2.yml')
        result['strelka_python'] = str(python.resolve())
        for caller in requested:
            script = bindir / SCRIPTS[caller]
            if not script.is_file():
                raise OncoTracerError(f'Strelka2 configuration script missing: {script}')
            result[caller] = str(script.resolve())
    else:
        python = shutil.which('python2.7')
        if not python:
            raise OncoTracerError('Strelka2 requires Python 2.7; install environments/native-strelka2.yml and set variant_strelka_prefix')
        result['strelka_python'] = str(Path(python).resolve())
        for caller in requested:
            script = shutil.which(SCRIPTS[caller])
            if not script:
                raise OncoTracerError(f'Strelka2 caller unavailable: {SCRIPTS[caller]}; configure variant_strelka_prefix')
            result[caller] = str(Path(script).resolve())
    if 'strelka2_somatic' in requested:
        # This exact Bioconda noarch build's somatic ELF crashes in the loader
        # on tested Linux runtimes. Configuration-script help still succeeds.
        # Use the installed package marker, never infer a build from folder names.
        prefixes = {Path(result['strelka_python']).parent.parent}
        if prefix:
            prefixes.add(Path(prefix))
        if any((p / 'conda-meta/strelka-2.9.10-hdfd78af_2.json').is_file() for p in prefixes):
            raise OncoTracerError(
                'Installed Strelka2 noarch build hdfd78af_2 has an incompatible somatic executable. '
                'Create a separate environment with environments/native-strelka2.yml '
                '(strelka=2.9.10=h9ee0642_1), then set variant_strelka_prefix to that environment.'
            )
    return result


def validate_paired_bam(bam, header, lengths, directory, tools, runner, env, label):
    contigs = []
    for line in Path(header).read_text().splitlines():
        if line.startswith('@SQ\t'):
            values = dict(x.split(':', 1) for x in line.split('\t')[1:] if ':' in x)
            contigs.append((values.get('SN'), int(values.get('LN', '0'))))
    if contigs != list(lengths.items()):
        raise OncoTracerError('Strelka2 requires the same BAM/reference contigs, lengths and order')
    count_file = directory / (label + '.paired_reads.txt')
    with count_file.open('w') as out:
        runner.run('variant-' + label + '-paired-check', [tools['samtools'], 'view', '-c', '-f', '1', '-F', '2304', bam], env=env, stdout=out)
    try:
        count = int(count_file.read_text().strip())
    except ValueError as exc:
        raise OncoTracerError('Could not validate Strelka2 paired-end BAM evidence') from exc
    if count < 1:
        raise OncoTracerError('Strelka2 requires paired-end BAM records; no eligible paired reads found for ' + label)


def stage_normal(sample, source, directory, lengths, tools, runner, env, threads):
    source = require_file(source, 'Strelka2 matched normal BAM')
    runner.run('variant-normal-' + sample + '-quickcheck', [tools['samtools'], 'quickcheck', '-v', source], env=env)
    header = directory / 'matched-normal.header.sam'
    with header.open('w') as out:
        runner.run('variant-normal-' + sample + '-header', [tools['samtools'], 'view', '-H', source], env=env, stdout=out)
    names = {v[3:] for line in header.read_text().splitlines() if line.startswith('@RG\t') for v in line.split('\t') if v.startswith('SM:') and v[3:]}
    if len(names) != 1:
        raise OncoTracerError('Strelka2 matched normal BAM requires exactly one read-group sample SM')
    bam = directory / 'matched-normal.bam'
    # Always stage a private sorted BAM/index: never modify the original normal.
    runner.run('variant-normal-' + sample + '-sort', [tools['samtools'], 'sort', '-@', str(threads), '-o', bam, source], env=env)
    runner.run('variant-normal-' + sample + '-index', [tools['samtools'], 'index', '-@', str(threads), bam], env=env)
    validate_paired_bam(bam, header, lengths, directory, tools, runner, env, 'normal-' + sample)
    return bam


def allele_evidence(fmt, ref, alt):
    """Documented tier-1 counts/fraction, separate from unchanged native AD/AF."""
    result = {key: fmt.get(key, '.') for key in ('AU', 'CU', 'GU', 'TU', 'TAR', 'TIR')}
    keys = (ref + 'U', alt + 'U') if len(ref) == len(alt) == 1 else ('TAR', 'TIR')
    try:
        reference, alternate = (int(fmt[key].split(',')[0]) for key in keys)
        if min(reference, alternate) < 0:
            raise ValueError
    except (KeyError, ValueError):
        return result
    result.update(strelka_tier1_ref_count=reference, strelka_tier1_alt_count=alternate,
                  strelka_tier1_alt_fraction=(alternate / (reference + alternate)) if reference + alternate else '.')
    return result


def call(caller, sample, bam, normal_bam, reference, directory, tools, run, threads, env, targets):
    workflow = directory / 'strelka2'
    prefix = Path(tools['strelka_python']).parent
    runtime_env = {**env, 'PATH': str(prefix) + os.pathsep + env.get('PATH', os.environ.get('PATH', '')),
                   'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '', 'PYTHONHOME': str(prefix.parent), 'CONDA_PREFIX': str(prefix.parent)}
    command = [tools['strelka_python'], tools[caller], '--referenceFasta', reference, '--runDir', workflow]
    if caller == 'strelka2_somatic':
        if normal_bam is None:
            raise OncoTracerError('Strelka2 somatic cannot run without the explicit matched normal BAM')
        command += ['--normalBam', normal_bam, '--tumorBam', bam]
    else:
        command += ['--bam', bam]
    if targets:
        bed = directory / 'strelka.call-regions.bed.gz'
        with bed.open('wb') as out:
            run('regions-bgzip', [tools['bgzip'], '-c', targets], stdout=out)
        run('regions-index', [tools['tabix'], '-p', 'bed', bed])
        command += ['--callRegions', bed]
    run('configure', command, env=runtime_env)
    run('workflow', [tools['strelka_python'], workflow / 'runWorkflow.py', '-m', 'local', '-j', str(threads)], env=runtime_env)
    results = workflow / 'results/variants'
    raw = directory / 'raw.vcf.gz'
    if caller == 'strelka2_somatic':
        parts = []
        for kind in ('snvs', 'indels'):
            original = require_file(results / ('somatic.' + kind + '.vcf.gz'), 'Strelka2 original paired VCF')
            shutil.copy2(original, directory / ('strelka_original.' + kind + '.vcf.gz'))
            selected = directory / ('strelka.tumor.' + kind + '.vcf.gz')
            run('select-' + kind, [tools['bcftools'], 'view', '-s', 'TUMOR', '-Oz', '-o', selected, original])
            run('index-' + kind, [tools['bcftools'], 'index', '-t', selected])
            parts.append(selected)
        combined = directory / 'strelka.tumor.combined.vcf.gz'
        run('concat', [tools['bcftools'], 'concat', '-a', '--no-version', '-Oz', '-o', combined, *parts])
        names = directory / 'strelka.sample-name.txt'
        names.write_text(sample + '\n')
        run('sample-name', [tools['bcftools'], 'reheader', '-s', names, '-o', raw, combined])
    else:
        original = require_file(results / 'variants.vcf.gz', 'Strelka2 germline VCF')
        shutil.copy2(original, raw)
    return raw
