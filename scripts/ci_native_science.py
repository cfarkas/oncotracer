#!/usr/bin/env python3
"""Run public FASTQs through a pinned native baseline and the exact candidate."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tests'))
from native_science_audit import BASELINE,BASELINE_SOURCE,SCHEMA,SAMPLES,digest,read,write,verify,require



def allocated_threads(environ):
    """Use this runner's CPU allocation, capped by an optional job budget."""
    available = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    requested = environ.get('ONCOTRACER_CI_THREADS', '').strip()
    try:
        budget = int(requested) if requested else available
    except ValueError as error:
        raise ValueError('ONCOTRACER_CI_THREADS must be a positive integer') from error
    require(budget > 0, 'ONCOTRACER_CI_THREADS must be positive')
    return min(budget, available)


def reference_cache_location(root, environ, build_identities):
    """Keep immutable reference indexes separate from fresh sample outputs."""
    require(set(build_identities) == {'bwa', 'minimap2'}, 'Incomplete reference tool identities')
    key = hashlib.sha256(json.dumps(build_identities, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    configured = environ.get('ONCOTRACER_CI_REFERENCE_CACHE', '').strip()
    if not configured and environ.get('RUNNER_TOOL_CACHE'):
        configured = str(Path(environ['RUNNER_TOOL_CACHE']) / 'oncotracer-reference-cache')
    if not configured:
        return root / 'reference', key, False
    cache = Path(configured).expanduser()
    require(cache.is_absolute(), 'The CI reference cache must use an absolute path')
    return cache / ('hg38-' + key), key, True


def command(argv,**kwargs):
    print('+ '+' '.join(map(str,argv)),flush=True)
    return subprocess.check_output(list(map(str,argv)),text=True,**kwargs).strip()


def build(source,target,commit):
    archive=subprocess.check_output(['git','-c','tar.umask=0002','archive','--format=tar',commit],cwd=source)
    sha=hashlib.sha256(archive).hexdigest()
    if commit==BASELINE:require(sha==BASELINE_SOURCE,'Frozen source archive differs')
    subprocess.run([sys.executable,str(source/'scripts/build_native_binary.py'),'--output',str(target),
                    '--source-commit',commit,'--source-sha256',sha],cwd=source,check=True)
    value=json.loads(command([target,'provenance','--json']))
    require(value['source_commit']==commit and value['source_sha256']==sha and value['source_tree_dirty'] is False,'Unclean or mismatched executable')
    return value


def copy_evidence(source,target):
    # Public tables, plots, QC and command receipts are portable. Large BAMs and
    # raw FASTQs stay on the runner; their input identities are recorded below.
    for item in ('03_cna_codification','04_cna_custom_plots','06_workflow_summary','09_cohort_figure'):
        path=source/item
        if path.is_dir():shutil.copytree(path,target/item)
    trace=target/'.oncotracer-native/trace.tsv';trace.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source/'.oncotracer-native/trace.tsv',trace)
    for path in (source/'02_bam_refinement').glob('*/01_tables/refined_bins.tsv.gz'):
        relative=path.relative_to(source);(target/relative).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target/relative)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('suite',choices=SAMPLES);args=parser.parse_args()
    env=os.environ.copy();suite=args.suite
    threads=allocated_threads(env)
    workspace=Path(env['GITHUB_WORKSPACE']);temp=Path(env['RUNNER_TEMP'])
    stamp=env['GITHUB_RUN_ID']+'-'+env['GITHUB_RUN_ATTEMPT']
    root=workspace/('oncotracer-parity-'+stamp+'-'+suite)
    require(not root.exists() and not root.is_symlink(),'Refusing an existing scientific test output')
    root.mkdir();audit=root/'audit';audit.mkdir();reports=root/'reports';reports.mkdir()
    candidate=command(['git','rev-parse','HEAD'],cwd=REPO)
    require(candidate==env['CANDIDATE_SHA'] and not command(['git','status','--porcelain'],cwd=REPO),'Candidate checkout is not exact and clean')
    baseline=workspace/'baseline'
    require(command(['git','rev-parse','HEAD'],cwd=baseline)==BASELINE and not command(['git','status','--porcelain'],cwd=baseline),'Baseline checkout differs')
    config_home=temp/('native-science-config-'+stamp+'-'+suite);env['XDG_CONFIG_HOME']=str(config_home)
    env['PYTHONDONTWRITEBYTECODE']='1';env['OPENBLAS_NUM_THREADS']='1';env['OMP_NUM_THREADS']='1'
    prefix=temp/('native-science-envs-'+stamp+'-'+suite)
    require(not prefix.exists() and not prefix.is_symlink(),'Refusing existing environment prefix')
    prefix.mkdir();inventory=audit/'environments';inventory.mkdir()
    for name in ('core','qdnaseq','ichorcna'):
        if suite=='quickstart2' and name=='ichorcna':continue
        definition=REPO/'environments'/('native-'+name+'.yml')
        require((baseline/'environments'/definition.name).read_bytes()==definition.read_bytes(),'Scientific environment changed; review the comparator before proceeding')
        destination=prefix/name
        subprocess.run(['conda','env','create','--yes','--prefix',str(destination),'--file',str(definition)],env=env,check=True)
        shutil.copy2(definition,inventory/(name+'.yml'))
        (inventory/(name+'.explicit.txt')).write_text(command(['conda','list','--explicit','--prefix',destination]))
        env['ONCOTRACER_'+name.upper()+'_PREFIX']=str(destination)
    sys.path.insert(0, str(REPO))
    from oncotracer_cli import engine
    toolchain = engine.Toolchain(core_prefix=prefix / 'core')
    contracts = {kind: engine._index_build_contract(kind, engine.HG38_ASSETS['genome.fa'],
                 engine.HG38_ASSETS['genome.fa.fai'], toolchain.executable('core', kind))
                 for kind in ('bwa', 'minimap2')}
    identities = {kind: engine._index_build_identity(contract) for kind, contract in contracts.items()}
    reference_root, reference_key, shared_reference = reference_cache_location(root, env, identities)
    write(audit / 'reference_inputs.json', dict(schema='oncotracer-ci-reference-inputs-v1',
          reference_root=str(reference_root), cache_key=reference_key, persistent_cache=shared_reference,
          genome_sha256=engine.HG38_ASSETS, index_build_identities=identities))
    write(audit / 'runtime_resources.json', dict(schema='oncotracer-ci-resources-v1',
          threads=threads, requested_threads=env.get('ONCOTRACER_CI_THREADS') or None,
          affinity_cpus=sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
          gpu=False))
    print(f'Scientific CPU budget: {threads}; reference inputs: {reference_root}', flush=True)
    for name,source,commit in [('baseline',baseline,BASELINE),('candidate',REPO,candidate)]:
        target=root/(name+'-oncotracer');identity=build(source,target,commit)
        (audit/name).mkdir();write(audit/name/'provenance.json',identity)
    binary=root/'candidate-oncotracer';input_root=root/'input'
    # Public ENA FTP avoids the short HTML responses seen on its HTTPS endpoint.
    # Both transports retain the manifest's full byte count and publisher MD5.
    fetch_command=[sys.executable,str(REPO/'scripts/fetch_native_science_inputs.py'),suite,
                    '--test-root',str(input_root),'--receipt',str(audit/'input_downloads.json'),
                    '--reuse-root',str(workspace)]
    input_cache=env.get('ONCOTRACER_CI_INPUT_CACHE') or (str(Path(env['RUNNER_TOOL_CACHE'])/'oncotracer-public-inputs') if env.get('RUNNER_TOOL_CACHE') else None)
    if input_cache:
        fetch_command.extend(['--cache-root',input_cache])
    subprocess.run(fetch_command,env=env,check=True)
    subprocess.run([str(binary),'quickstart','1' if suite=='quickstart1' else '2','--test-root',str(input_root),'--download-only'],env=env,check=True)
    files=sorted(p for p in input_root.rglob('*') if p.is_file() and p.name.endswith(('.fastq.gz','.fq.gz')))
    require(files,'No public FASTQs downloaded')
    write(audit/'inputs.json',[dict(path=str(p.relative_to(input_root)),bytes=p.stat().st_size,sha256=digest(p)) for p in files])
    record=dict(schema=SCHEMA,suite=suite,datasets={},input_manifest_sha256=digest(audit/'inputs.json'))
    outputs=[]
    try:
        for dataset,names in SAMPLES[suite].items():
            record['datasets'][dataset]={}
            source_config=input_root/'configs'/('hcc1143_lpwgs/illumina.auto.yml' if dataset=='hcc1143' else dataset+'.quickstart.yml')
            if not source_config.is_file() and dataset=='hcc1143':
                configs=list((input_root/'configs').rglob('*hcc*.yml'))
                require(len(configs)==1,'Cannot identify public HCC1143 config');source_config=configs[0]
            for side in ('baseline','candidate'):
                destination=root/side/dataset;outputs.append(destination)
                config=root/(side+'-'+dataset+'.yml')
                subprocess.run([sys.executable,str(REPO/'tests/make_parity_config.py'),'--source',str(source_config),'--destination',str(config),
                                '--lpwgs-root',str(reference_root),'--outdir',str(destination)],check=True)
                # Baseline and candidate have the same scientific policy. The
                # candidate additionally exercises automatic cohort rendering.
                with config.open('a') as handle:handle.write('\nrun_variants: false\ncohort_figure: true\n')
                with (reports/(side+'-'+dataset+'.log')).open('w') as log:
                    result=subprocess.run([str(root/(side+'-oncotracer')),'run','--backend','host','--config',str(config),'--threads',str(threads)],env=env,stdout=log,stderr=subprocess.STDOUT)
                record['datasets'][dataset][side+'_exit_code']=result.returncode
                require(result.returncode in (0,2),'Scientific run failed: '+side+'/'+dataset+'; inspect its complete log')
                copy_evidence(destination,audit/side/dataset)
                shutil.copy2(config,audit/side/(dataset+'.yml'))
        record['files']={str(p.relative_to(audit)):digest(p) for p in sorted(audit.rglob('*')) if p.is_file()}
        write(audit/'native_science.json',record)
        identity=read(audit/'candidate/provenance.json')
        verify(audit,suite,candidate,identity['source_sha256'],identity['binary_sha256'],write_summary=True)
        # Include generated comparisons and top-level records in the archive seal.
        subprocess.run([sys.executable,str(REPO/'tests/parity_audit.py'),'checksums',str(audit)],check=True)
        verify(audit,suite,candidate,identity['source_sha256'],identity['binary_sha256'])
        print('PUBLIC_NATIVE_SCIENCE_REGRESSION_PASSED',suite,flush=True)
    finally:
        sys.path.insert(0,str(REPO))
        from oncotracer_cli.fontconfig_safety import prepare_output_runtime_removal
        for destination in outputs:prepare_output_runtime_removal(destination)
    return 0

if __name__=='__main__':raise SystemExit(main())
