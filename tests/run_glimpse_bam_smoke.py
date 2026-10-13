#!/usr/bin/env python3
"""Real Nextflow+GLIMPSE2 smoke using only the pre-existing synthetic fixtures.

Usage: python3 tests/run_glimpse_bam_smoke.py --fixture-workflow /path/workflow_update
       --output /new/writable/smoke
Requires the completed tests/comprehensive_integration fixture, local Nextflow,
GLIMPSE2, samtools and bcftools. Does not download data or run a patient sample.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-workflow', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    prior = a.fixture_workflow.resolve(); fixture = prior/'tests/fixture'
    completed = prior/'tests/comprehensive_integration'
    smoke = a.output.resolve()
    smoke.mkdir(parents=True, exist_ok=False)
    launch = smoke/'launch'; launch.mkdir()
    bundle = smoke/'synthetic_workflow'
    shutil.copytree(ROOT/'oncotracer_cli/lpwgs', bundle, ignore=shutil.ignore_patterns('__pycache__', 'resources'))
    # Exercise the statistical stage with explicit synthetic population metadata;
    # these artificial haplotypes must never receive biological population labels.
    (bundle/'data/1000G.reference_population.panel').write_text('sample\tpop\tsuper_pop\tgender\n'+
        ''.join(f'REF{i:03d}\tSYNTHETIC\tSYNTHETIC\tunknown\n' for i in range(100)))
    bam = completed/'alignment/SYNTHETIC.bam'
    inputs = [bam, completed/'variants/germline/SYNTHETIC.germline.vcf.gz',
              completed/'variants/somatic_candidates/SYNTHETIC.somatic.vcf.gz']
    before = {str(path): sha(path) for path in inputs}
    params = {'bam': str(bam), 'sample': 'SYNTHETIC', 'germline_vcf': str(inputs[1]), 'somatic_vcf': str(inputs[2]),
        'reference': str(fixture/'genome.fa'), 'clinvar': str(fixture/'clinvar.vcf.gz'),
        'traits_panel': str(fixture/'traits.tsv'), 'gwas': True, 'gwas_catalog': str(fixture/'gwas.tsv.gz'),
        'no_ancestry': True, 'allow_test_reference': True, 'threads': 2, 'memory': '4GB', 'llm_threads': 2,
        'glimpse2': True, 'glimpse_chromosomes': '1', 'glimpse_reference_dir': str(fixture/'imputation'),
        'glimpse_bin': str(prior/'resources/glimpse2/bin'), 'imputed_clinvar': True, 'polygenic': True,
        'pgs_manifest': str(fixture/'pgs/manifest.json'), 'pgs_cache': str(smoke/'resources/pgs'),
        'literature_offline': True, 'literature_cache': str(smoke/'resources/literature'),
        'pgs_reference_stats': True, 'interpretation_report': True, 'report_llm': False,
        'report_pdf': True, 'explain_findings': True, 'homozygosity_report': True, 'cancer_report': True,
        'cardiomyopathy_report': True, 'unified_vcf': True, 'output': str(smoke/'output'),
        'resource_dir': str(smoke/'resources'), 'continue': True}
    config = smoke/'params.json'; config.write_text(json.dumps(params, indent=2)+'\n')
    (smoke/'SYNTHETIC_ONLY.txt').write_text('Artificial chromosome, sequencing reads, reference haplotypes and PGS; no patient data.\n'
        'The copied workflow differs only in synthetic population metadata matching REF000..099.\n'
        'Ancestry reference projection, real gene annotation and LLM inference are not assessed by this smoke.\n')
    command = [shutil.which('nextflow') or 'nextflow', '-log', str(smoke/'nextflow.log'), 'run',
               str(bundle/'main.nf'), '-ansi-log', 'false', '-params-file', str(config)]
    with (smoke/'run.log').open('w') as log:
        result = subprocess.run(command, cwd=launch, env={**os.environ, 'NXF_OFFLINE':'true', 'NXF_DISABLE_CHECK_LATEST':'true'},
                                stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        print((smoke/'run.log').read_text()[-12000:], file=sys.stderr)
        return result.returncode
    if {str(path): sha(path) for path in inputs} != before:
        raise AssertionError('An original synthetic BAM/VCF changed')
    report = smoke/'output/START_HERE.html'
    vcf = smoke/'output/cohort_vcf/SYNTHETIC.cohort_vcf/SYNTHETIC.annotated.vcf.gz'
    for path in (report, vcf, Path(str(vcf)+'.tbi')):
        if not path.is_file():
            raise AssertionError('Missing completed output: '+str(path))
    validation = {'status':'passed','synthetic_only':True,'input_sha256':before,'command':command,
                  'report':str(report),'annotated_vcf':str(vcf),'clinical_ancestry_llm_validation':False}
    (smoke/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
