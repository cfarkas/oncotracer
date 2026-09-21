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
    if not any(fields for _, fields in records(source)):
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
    kept = {key(fields) for _, fields in records(selected_vcf) if fields}
    candidates = {key(fields) for _, fields in records(source) if fields}
    assessed = {}
    for _, fields in records(all_vcf):
        if not fields:
            continue
        k = key(fields)
        if k not in candidates or k in assessed:
            raise OncoTracerError('Varlociraptor emitted an unexpected or duplicate allele')
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
