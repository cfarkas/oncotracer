"""Prepare and run a project from one sample table, without hand-written YAML."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess

from .runtime import (OncoTracerError, atomic_write_json, atomic_write_text,
                      exclusive_lock, load_flat_yaml, render_flat_yaml, sha256_file)
from .sample_manifest import read_manifest
from .sample_resources import allocate, memory_gb, parallel_argument
from .system_check import inspect_hardware


def _table(fields, rows, delimiter=','):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=delimiter, lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return buffer.getvalue()


def cache_directory(value=None):
    base = Path(value).expanduser() if value else Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'oncotracer/v6'
    return base.absolute()


def _docker_image(image):
    from .container_runtime import image_reference
    docker = shutil.which('docker')
    if not docker:
        raise OncoTracerError('Docker is required for launch. Install Docker Engine, start it, then repeat this command. Existing Conda projects can use oncotracer run --config.')
    probe = subprocess.run([docker,'info','--format','{{.ServerVersion}}'],capture_output=True,text=True)
    if probe.returncode:
        raise OncoTracerError('Cannot access Docker. Start Docker and check that docker info works for your user, then repeat this command.\n'+probe.stderr.strip())
    selected = image_reference(image, 'docker')
    probe = subprocess.run([docker,'image','inspect',selected],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if probe.returncode:
        print('Downloading the OncoTracer runtime: '+selected,flush=True)
        result=subprocess.run([docker,'pull',selected])
        if result.returncode:raise OncoTracerError('Docker image download failed. Repeat launch to retry; completed image layers are retained.')
    probe=subprocess.run([docker,'run','--rm',selected,'--version'],capture_output=True,text=True)
    from . import __version__
    if probe.returncode or ('OncoTracer '+__version__) not in probe.stdout:
        raise OncoTracerError('launch requires the matching OncoTracer '+__version__+' image; select it with --image. '+probe.stderr.strip())
    return selected


def _gpu_for_launch(args, plan):
    if not plan['request']['methylation'] or plan['resume']:return
    from .gpu_runtime import inventory
    requested = bool(inventory()) if args.gpu is None else bool(args.gpu)
    plan['gpu_selected']=requested
    if requested:
        result=subprocess.run(['docker','run','--rm','--gpus','all','--entrypoint','nvidia-smi',args.image,'-L'],capture_output=True,text=True)
        if result.returncode:
            raise OncoTracerError('Docker cannot access the NVIDIA GPU. Configure NVIDIA Container Toolkit, or repeat with --cpu. '+result.stderr.strip())


def _request(args, samples, source, cache):
    return dict(schema='oncotracer-launch-v1', samples_sha256=sha256_file(source),
                samples_file=str(source), mode=samples[0].mode, specimen=args.specimen,
                analysis=args.analysis, methylation=bool(args.methylation or args.analysis=='methylation'),
                methylation_caller=args.methylation_caller, cache=str(cache),
                threads=args.threads, memory=args.memory, parallel_samples=args.parallel_samples,
                gpu=getattr(args,'gpu',None), image=args.image, ont_profile=args.ont_profile)


def project_plan(args):
    """Validate user intent and input identity before downloads or project writes."""
    source = Path(args.samples).expanduser().resolve()
    methylation = bool(args.methylation or args.analysis=='methylation')
    samples = read_manifest(source, methylation=methylation, caller=args.methylation_caller)
    mode = samples[0].mode
    if mode=='ont' and args.specimen=='ffpe':
        raise OncoTracerError('The managed ONT profile supports fresh R10.4.1 input. Use the advanced setup for a separately validated ONT FFPE protocol.')
    project = Path(args.project).expanduser().absolute()
    cache = cache_directory(args.cache)
    if any(p.is_symlink() for path in (project,cache) for p in (path,*path.parents)) or project in (Path('/'), Path.home()) or cache in (Path('/'),Path.home()):
        raise OncoTracerError('Choose dedicated physical project/cache directories')
    if project==cache or project in cache.parents or cache in project.parents:
        raise OncoTracerError('Use separate project and shared cache directories')
    outdir = project/'results'
    for sample in samples:
        for path in (*sample.fastq_1,*sample.fastq_2,sample.fastq_dir,sample.library,sample.pod5,sample.modbam):
            if path is None:continue
            if path==project or project in path.parents or (path.is_dir() and path in project.parents):
                raise OncoTracerError('The project must be separate from input files and library folders: '+str(path))
            if path==cache or cache in path.parents or (path.is_dir() and path in cache.parents):
                raise OncoTracerError('The shared cache must be separate from input files and library folders: '+str(path))
    request = _request(args,samples,source,cache)
    receipt = project/'config/launch.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved.get('request')!=request:
            raise OncoTracerError('This project was created with different inputs or options. Choose a new --project, or resume its saved run.yml with oncotracer run --config.')
        config = project/'config/run.yml'
        if not config.is_file() or sha256_file(config)!=saved.get('config_sha256'):
            raise OncoTracerError('The generated run.yml was edited. Run that saved YAML directly with oncotracer run --config, or choose a new project.')
        for relative, expected in saved.get('generated_files',{}).items():
            target=project/relative
            if not target.is_file() or sha256_file(target)!=expected:
                raise OncoTracerError('Generated sample tables changed. Restore the project or choose a new --project: '+relative)
        return dict(project=project,cache=cache,samples=samples,request=request,resume=True,config=config)
    if project.exists() and any(p.name!='.launch.lock' for p in project.iterdir()):
        raise OncoTracerError('Project directory is not empty and has no managed launch record. Choose a new --project; existing files are preserved.')
    ont_profile = None
    if mode=='ont' and args.analysis=='full':
        from .launch_ont_profile import choose_profile
        ont_profile=choose_profile(samples,args.ont_profile)
    hardware=inspect_hardware()
    threads=args.threads if args.threads is not None else min(16,hardware['cpu_workers_available'])
    variants=args.analysis=='full'
    allocation=allocate(threads,len(samples),parallel=args.parallel_samples,memory=args.memory,
                        minimum_gb=32 if mode=='illumina' and variants else 8,hardware=hardware)
    from .methylation_routing import classifier_for_tissue
    classifiers=sorted({classifier_for_tissue(s.tissue) for s in samples}) if methylation else []
    return dict(project=project,cache=cache,samples=samples,request=request,resume=False,
                config=project/'config/run.yml',allocation=allocation,classifiers=classifiers,ont_profile=ont_profile)


def write_project(args, plan, resources):
    """Commit generated tables and configuration; the receipt is written last."""
    project,cache,samples=plan['project'],plan['cache'],plan['samples']
    if plan['resume']:return plan['config']
    mode=samples[0].mode; allocation=plan['allocation']; methylation=plan['request']['methylation']
    variants=args.analysis=='full'
    values=dict(mode=mode,lpwgs_root=str(cache/'reference'),hg38_auto_download=True,
                reference_download_cache=str(cache/'reference-downloads'),outdir=str(project/'results'),
                threads=allocation['total_threads'],parallel_samples=args.parallel_samples,
                analysis_memory=args.memory,execution_backend='docker',docker_image=args.image,
                force=False,cna_profile='sensitive',ichorcna_fallback=True,ichorcna_fallback_workers=1,
                run_cna_classifier=variants,run_gistic=variants,gistic_required=False,knowledge_web=False,
                cohort_figure=True,gpu_usage=False,run_variants=variants)
    files={}
    if mode=='ont':
        fields=['sample','fastq_dir','status']
        files['samples.csv']=_table(fields,[dict(sample=s.sample,fastq_dir=str(s.fastq_dir),status=s.status) for s in samples])
        values.update(ont_samplesheet=str(project/'config/samples.csv'),ont_caller='qdnaseq',ont_binsize_kb=50,
                      ont_analysis_type='solid_biopsy')
    else:
        fields=['sample','fastq_1','fastq_2','status']
        def reads(paths):return json.dumps([str(p) for p in paths]) if len(paths)>1 else str(paths[0]) if paths else ''
        files['samples.csv']=_table(fields,[dict(sample=s.sample,fastq_1=reads(s.fastq_1),fastq_2=reads(s.fastq_2),status=s.status) for s in samples])
        values.update(illumina_samplesheet=str(project/'config/samples.csv'),illumina_caller='qdnaseq',illumina_binsize_kb=50)
    metadata_fields=['sample','sample_type','analysis_role','tissue']
    for sample in samples:
        metadata_fields.extend(key for key in sample.annotations if key not in metadata_fields)
    metadata=[]
    for sample in samples:
        row={**sample.annotations,'sample':sample.sample,'analysis_role':sample.status,'tissue':sample.tissue}
        row['sample_type']=row.get('sample_type') or ('normal' if sample.status=='normal' else 'cancer')
        metadata.append(row)
    files['sample_metadata.csv']=_table(metadata_fields,metadata)
    values['sample_metadata']=str(project/'config/sample_metadata.csv')
    if variants:
        values.update(variant_specimen_type=args.specimen,variant_annovar='auto',variant_download_resources=True,
                      variant_parallel_samples=args.parallel_samples,variant_memory=args.memory,
                      variant_resource_mode='dynamic' if mode=='ont' else 'fixed',variant_storage_mode='compact',
                      variant_merge_vcfs=False,variant_monte_carlo_linkage=False)
        if mode=='ont':
            values.update(variant_callers='clairs_to,clair3',variant_ont_profile=plan['ont_profile']['profile'],
                          variant_clair3_model='auto',variant_clairsto_platform=plan['ont_profile']['clairsto_platform'],
                          variant_ont_imputation='quilt2',variant_quilt2_cache=str(cache/'quilt2'))
        else:
            values.update(variant_engine='glimpse2',variant_callers='bcftools,mutect2',
                          variant_ffperase='required' if args.specimen=='ffpe' else 'off',
                          variant_accept_ffperase_license=bool(args.accept_ffperase_license),
                          variant_resource_dir=str(cache/'variants'))
    if methylation:
        key='pod5' if args.methylation_caller=='rockfish' else 'modbam'
        files['methylation.tsv']=_table(['sample','tissue',key],[
            dict(sample=s.sample,tissue=s.tissue,**{key:str(getattr(s,key))}) for s in samples],delimiter='\t')
        values.update(resources)
        values.update(methylation=True,methylation_only=args.analysis=='methylation',methylation_caller=args.methylation_caller,
                      methylation_classifier='tissue',methylation_modbam_alignment='realign',
                      methylation_sample_metadata=str(project/'config/methylation.tsv'))
        if 'gpu_selected' in plan:values['methylation_gpu_usage']=plan['gpu_selected']
        elif args.gpu is None:
            from .gpu_runtime import inventory
            values['methylation_gpu_usage']=bool(inventory())
        else:values['methylation_gpu_usage']=bool(args.gpu)
    header='# Generated by oncotracer launch. Your original sample table is unchanged.\n'
    files['run.yml']=header+render_flat_yaml(values)
    folder=project/'config';folder.mkdir(parents=True,exist_ok=True)
    for name,text in files.items():atomic_write_text(folder/name,text)
    atomic_write_json(folder/'launch.json',dict(request=plan['request'],config_sha256=sha256_file(folder/'run.yml'),
        generated_files={f'config/{name}':sha256_file(folder/name) for name in files if name!='run.yml'},
        resource_allocation=allocation,ont_profile=plan.get('ont_profile')))
    return folder/'run.yml'


def command_launch(args):
    plan=project_plan(args)
    if args.dry_run:
        summary={'action':'resume' if plan['resume'] else 'prepare_and_run','project':str(plan['project']),
                 'cache':str(plan['cache']),'mode':plan['samples'][0].mode,'samples':len(plan['samples']),
                 'analysis':args.analysis,'methylation':plan['request']['methylation'],'downloads_started':False,
                 'files_written':False,'allocation':plan.get('allocation'),'ont_profile':plan.get('ont_profile')}
        print(json.dumps(summary,indent=2));return 0
    if args.specimen=='ffpe' and args.analysis=='full' and not args.accept_ffperase_license and not plan['resume']:
        from .variant_model_assets import FFPERASE_LICENSE
        raise OncoTracerError('FFPE calling uses FFPErase. Review '+FFPERASE_LICENSE+' then repeat with --accept-ffperase-license; no inputs or project files have been changed.')
    # Use a fixed requested image in the receipt; defaults are version-specific.
    if plan['request']['methylation'] and not plan['resume'] and 'sturgeon' in plan['classifiers'] and not args.accept_sturgeon_license:
        from .sturgeon_resources import LICENSE_URL
        raise OncoTracerError('Review the Sturgeon license: '+LICENSE_URL+' then repeat with --accept-sturgeon-license for CNS classification.')
    _docker_image(args.image)
    _gpu_for_launch(args,plan)
    resources={}
    if plan['request']['methylation'] and not plan['resume']:
        from .managed_resources import prepare_for_launch
        resources=prepare_for_launch(plan['cache'],plan['classifiers'],args)
    project=plan['project'];project.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(project/'.launch.lock'):
        refreshed=project_plan(args)
        if 'gpu_selected' in plan:refreshed['gpu_selected']=plan['gpu_selected']
        config=write_project(args,refreshed,resources)
    print('Project ready: '+str(config),flush=True)
    if args.prepare_only:
        print('Run: oncotracer run --config '+str(config)+' --backend docker',flush=True)
        return 0
    from .cli import parse_arguments,command_run
    run=parse_arguments(['run','--config',str(config),'--backend','docker','--image',args.image])
    return command_run(run)


def add_command(subparsers):
    from .cli import DEFAULT_IMAGE
    parser=subparsers.add_parser('launch',help='Prepare and run from one sample table; no manual YAML required')
    parser.add_argument('--samples',required=True,help='CSV/TSV with sample,status and Illumina FASTQs or ONT library folders')
    parser.add_argument('--project',required=True,help='New project directory; repeat the same command to resume')
    parser.add_argument('--specimen',choices=('fresh','ffpe'),required=True,help='Preservation from specimen records')
    parser.add_argument('--analysis',choices=('full','cna','methylation'),default='full')
    from .launch_ont_profile import PROFILES
    parser.add_argument('--ont-profile',choices=('auto',*PROFILES),default='auto',help='ONT variant models; default reads FASTQ model metadata; required explicitly when headers lack it')
    parser.add_argument('--methylation',action='store_true',help='Include ONT methylation; provide tissue in the sample table')
    parser.add_argument('--methylation-caller',choices=('rockfish','modkit'),default='rockfish')
    parser.add_argument('--cache',help='Shared reference/model cache; default ~/.cache/oncotracer/v6')
    parser.add_argument('--threads',type=int,help='Total threads; default detects up to 16 available CPUs')
    def memory_argument(value):
        try:memory_gb(value)
        except OncoTracerError as error:raise argparse.ArgumentTypeError(str(error)) from error
        return value
    parser.add_argument('--memory',default='auto',type=memory_argument,help='Total RAM budget, e.g. 64GB; default detects available RAM')
    parser.add_argument('--parallel-samples',type=parallel_argument,default='auto')
    parser.add_argument('--image',default=DEFAULT_IMAGE)
    devices=parser.add_mutually_exclusive_group()
    devices.add_argument('--gpu',action='store_true',default=None,help='Enable methylation GPU execution')
    devices.add_argument('--cpu',dest='gpu',action='store_false',help='Use CPUs for methylation too')
    parser.add_argument('--accept-sturgeon-license',action='store_true')
    parser.add_argument('--accept-ffperase-license',action='store_true')
    parser.add_argument('--prepare-only',action='store_true',help='Prepare methylation resources and save the project; genome/variant references are fetched when analysis starts')
    parser.add_argument('--dry-run',action='store_true',help='Validate the sample table and show budgets; no downloads or writes')
    parser.set_defaults(func=command_launch)
