"""Native Varlociraptor evidence assessment and local FDR control.

See https://varlociraptor.github.io/docs/calling/ and /docs/filtering/.
The default single-sample model assesses presence, not somatic origin.
Original GT/FORMAT fields remain in the caller VCF; Varlociraptor BCFs are
published separately because their allele fractions are not genotypes.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from .runtime import OncoTracerError, atomic_write_json, sha256_file
from .variant_filters import add_assessment, key, records


def _comparison_key(fields):
    """Match Varlociraptor's serialization without changing caller evidence.

    Varlociraptor uppercases DNA and writes deletions longer than 50 bases as
    <DEL>/SVLEN. Contig identifiers and breakend ALT strings are case-sensitive.
    """
    chrom, pos, ref, alt = key(fields)
    ref = ref.upper() if ref and set(ref.upper()) <= set('ACGTN') else ref
    alt = alt.upper() if alt and set(alt.upper()) <= set('ACGTN') else alt
    if alt != '<DEL>':
        return chrom, pos, ref, alt
    info = dict(part.split('=', 1) for part in fields[7].split(';') if '=' in part)
    try:
        length = -int(info['SVLEN'])
        valid = (length > 0 and len(ref) == 1 and ref in 'ACGTN'
                 and info.get('SVTYPE', 'DEL') == 'DEL'
                 and ('END' not in info or int(info['END']) == pos + length))
    except (KeyError, ValueError):
        valid = False
    if not valid:
        raise OncoTracerError('Varlociraptor symbolic deletion has missing or inconsistent length metadata')
    return chrom, pos, ref, alt, length


def _deletion_alias(k):
    if len(k) == 5:  # A validated symbolic deletion.
        return k
    chrom, pos, ref, alt = k
    if len(ref) > 1 and len(alt) == 1 and ref.startswith(alt) and set(ref) <= set('ACGTN'):
        return chrom, pos, alt, '<DEL>', len(ref) - 1
    return None


def _candidate_index(source):
    candidates, deletions, symbolic = {}, {}, set()
    for _, fields in records(source):
        if fields is None:
            continue
        original, comparison = key(fields), _comparison_key(fields)
        if comparison in candidates or (len(comparison) == 5 and original in symbolic):
            raise OncoTracerError('Ambiguous or duplicate candidate allele for Varlociraptor')
        candidates[comparison] = original
        if len(comparison) == 5:
            symbolic.add(original)
        deletion = _deletion_alias(comparison)
        if deletion is not None:
            if deletion in deletions:
                raise OncoTracerError('Ambiguous candidate deletion for Varlociraptor')
            deletions[deletion] = original
    return candidates, deletions


def _original_allele(fields, candidates, deletions):
    comparison = _comparison_key(fields)
    original = candidates.get(comparison)
    if original is None:
        deletion = _deletion_alias(comparison)
        candidate = deletions.get(deletion)
        # Length equivalence is sufficient only when one side is symbolic.
        # Literal deletions must still match their full reference sequence.
        if candidate is not None and (len(comparison) == 5 or candidate[3] == '<DEL>'):
            original = candidate
    if original is None:
        raise OncoTracerError('Varlociraptor emitted an unexpected allele')
    return original


def discover(prefix=None):
    path = Path(prefix) / 'bin/varlociraptor' if prefix else None
    found = str(path) if path and path.is_file() else shutil.which('varlociraptor') if not prefix else None
    if not found:
        raise OncoTracerError('Varlociraptor requested but unavailable; install environments/native-variants.yml or set variant_tool_prefix')
    return str(Path(found).resolve())


def assess(source, destination, *, bam, reference, directory, run, tools,
           platform='illumina', fdr=0.05, scenario=None, events=('PRESENT',), sample_name='sample'):
    directory.mkdir(exist_ok=True, parents=True)
    binary = tools['varlociraptor']
    if not 0 < fdr < 1 or not math.isfinite(fdr):
        raise OncoTracerError('Varlociraptor FDR must be between 0 and 1')
    scenario_file = directory / 'varlociraptor.scenario.yaml'
    if scenario:
        shutil.copy2(scenario, scenario_file)
    else:
        scenario_file.write_text('samples:\n  sample:\n    universe: "[0.0,1.0]"\n    resolution: 0.01\nevents:\n  present: "sample:]0.0,1.0]"\n')
    result = {'status': 'complete', 'fdr': fdr, 'fdr_mode': 'local-smart',
              'events': list(events), 'scenario_sha256': sha256_file(scenario_file),
              'interpretation': 'Presence assessment; no somatic/germline claim' if not scenario else 'User-supplied scenario',
              'score_scale': 'PHRED posterior probability of ARTIFACT; not raw probability'}
    table = directory / 'varlociraptor.evidence.tsv'
    candidates, deletions = _candidate_index(source)
    if not candidates:
        result.update(status='not_applicable', reason='Empty candidate set')
        result['counts'] = add_assessment(source, destination, {}, tag='VARLOCIRAPTOR', description='Varlociraptor local FDR assessment', table=table)
        return result
    properties = directory / 'varlociraptor.alignment-properties.json'
    observations = directory / 'varlociraptor.observations.bcf'
    calls = directory / 'varlociraptor.calls.bcf'
    selected = directory / 'varlociraptor.selected.bcf'
    with properties.open('w') as output:
        run('varlociraptor-estimate', [binary, 'estimate', 'alignment-properties', reference, '--bams', bam], stdout=output)
    with observations.open('wb') as output:
        command = [binary, 'preprocess', 'variants', reference, '--bam', bam,
                   '--alignment-properties', properties, '--candidates', source, '--atomic-candidate-variants']
        if platform == 'ont':
            command += ['--pairhmm-mode', 'homopolymer']
        run('varlociraptor-preprocess', command, stdout=output)
    with calls.open('wb') as output:
        run('varlociraptor-call', [binary, 'call', 'variants', 'generic', '--scenario', scenario_file,
                                 '--obs', f'{sample_name}={observations}'], stdout=output)
    with selected.open('wb') as output:
        run('varlociraptor-fdr', [binary, 'filter-calls', 'control-fdr', calls, '--mode', 'local-smart',
                                '--events', *events, '--fdr', str(fdr)], stdout=output)
    all_vcf, selected_vcf = directory / 'varlociraptor.calls.vcf', directory / 'varlociraptor.selected.vcf'
    for bcf, vcf in ((calls, all_vcf), (selected, selected_vcf)):
        run('varlociraptor-view', [tools['bcftools'], 'view', '-Ov', '-o', vcf, bcf])
    kept = set()
    for _, fields in records(selected_vcf):
        if fields is None:
            continue
        k = _original_allele(fields, candidates, deletions)
        if k in kept:
            raise OncoTracerError('Varlociraptor selected a duplicate allele')
        kept.add(k)
    assessed = {}
    for _, fields in records(all_vcf):
        if not fields:
            continue
        k = _original_allele(fields, candidates, deletions)
        if k in assessed:
            raise OncoTracerError('Varlociraptor emitted a duplicate allele')
        info = dict(part.split('=', 1) for part in fields[7].split(';') if '=' in part)
        score = float(info['PROB_ARTIFACT']) if info.get('PROB_ARTIFACT', '.') != '.' else None
        if score is not None and (not math.isfinite(score) or score < 0):
            score = None
        assessed[k] = ('REAL' if k in kept else 'REJECTED', score, 'Selected by local FDR' if k in kept else 'Not selected by local FDR')
    if not kept <= assessed.keys():
        raise OncoTracerError('Varlociraptor selected alleles absent from assessment')
    result['counts'] = add_assessment(source, destination, assessed, tag='VARLOCIRAPTOR',
                                     description='Varlociraptor local FDR assessment', table=table)
    # Unsupported/dropped candidates never become passing assessments.
    result['unassessed_records'] = result['counts'].get('NOT_EVALUATED', 0)
    atomic_write_json(directory / 'varlociraptor.summary.json', result)
    return result
