"""QUILT2 imputation and legacy standalone linkage helpers."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .runtime import OncoTracerError, atomic_write_json, runtime_root, sha256_file


def core_python(toolchain=None):
    if toolchain is not None:
        try:return toolchain.executable('core','python')
        except OncoTracerError:pass
    prefix=os.environ.get('ONCOTRACER_CORE_PREFIX')
    return str(Path(prefix)/'bin/python') if prefix else sys.executable


def quilt_tools(request):
    # GPU caller environments intentionally do not own imputation tools.
    prefix=os.environ.get('ONCOTRACER_VARIANTS_PREFIX')
    if not prefix and os.environ.get('ONCOTRACER_CONTAINER_RUNTIME')!='docker':
        from .cli import _load_install_config
        install=_load_install_config()
        if install.get('backend') in ('conda','poetry'):
            from .install_safety import managed_variants_prefix
            prefix=str(managed_variants_prefix(install,semantic=False))
    prefix=Path(prefix) if prefix else request.tool_prefix
    tools={}
    for name in ('QUILT2.R','QUILT2_prepare_reference.R','Rscript','bcftools','samtools'):
        found=str(prefix/'bin'/name) if prefix else shutil.which(name)
        if not found or not Path(found).is_file() or not os.access(found,os.X_OK):
            raise OncoTracerError('QUILT2 requires the checked variant environment; run oncotracer install --conda or rebuild the current Dockerfile. Missing: '+name)
        tools[name]=found
    return tools


def preflight(request,toolchain=None):
    if request.ont_imputation=='quilt2':
        tools=quilt_tools(request)
        environment=os.environ.copy()
        for key in ('R_HOME','R_LIBS','R_LIBS_USER','R_LIBS_SITE','LD_LIBRARY_PATH','LD_PRELOAD'):environment.pop(key,None)
        prefix=str(Path(tools['Rscript']).parent.parent)
        environment.update(CONDA_PREFIX=prefix,PATH=str(Path(prefix)/'bin')+os.pathsep+os.defpath)
        result=subprocess.run([tools['Rscript'],'--vanilla','-e',
            'suppressPackageStartupMessages(library(QUILT));library(optparse);'
            'stopifnot(as.character(packageVersion("QUILT"))=="2.0.4");'
            'stopifnot(normalizePath(R.home())==normalizePath(file.path(Sys.getenv("CONDA_PREFIX"),"lib/R")));'
            'stopifnot(startsWith(normalizePath(find.package("QUILT")),paste0(normalizePath(Sys.getenv("CONDA_PREFIX")),"/")));cat("QUILT2_OK")'],
            capture_output=True,text=True,env=environment,timeout=60)
        if result.returncode or 'QUILT2_OK' not in result.stdout:
            raise OncoTracerError('QUILT2 2.0.4 failed its installation probe: '+result.stderr[-1000:])


def prepare_quilt(request,bams,fasta,root,runner,threads,toolchain=None):
    if request.ont_imputation!='quilt2':return None
    launch=root/'quilt2_launch';launch.mkdir(parents=True,exist_ok=True)
    cache=request.quilt2_cache or root/'quilt2_resources'
    config=dict(cache=str(cache),reference=str(fasta),samples=sorted(bams),threads=min(8,threads),
                reference_dir=str(request.quilt2_reference_dir) if request.quilt2_reference_dir else None,
                tools=quilt_tools(request),manifest_output=str(launch/'panel.json'))
    path=launch/'panel-parameters.json';atomic_write_json(path,config)
    runner.run('quilt2-reference-prepare',[core_python(toolchain),'-B',runtime_root()/'oncotracer_cli/lpwgs/bin/quilt2_worker.py','--prepare','--config',path])
    manifest=launch/'panel.json'
    if not manifest.is_file():raise OncoTracerError('QUILT2 preparation did not produce a reference manifest')
    return manifest


def impute_quilt(request,sample,bam,manifest,root,runner,threads,toolchain=None,*,force=False,resources=None,maximum_workers=1):
    launch=root/'quilt2_launch';launch.mkdir(parents=True,exist_ok=True)
    output=root/'samples'/sample/'quilt2'
    config=dict(manifest=str(manifest),sample=sample,bam=str(bam),output=str(output),threads=threads,force=force,expected_chromosomes=[f'chr{i}' for i in range(1,23)],
                min_base_quality=max(request.min_base_quality,request.min_mapping_quality),tools=quilt_tools(request))
    path=launch/f'{sample}.parameters.json';atomic_write_json(path,config)
    if resources is not None:
        from .source_runtime import python_module
        command=python_module('oncotracer_cli.quilt_parallel');command[0]=core_python(toolchain)
        command += ['--config',str(path),'--resources',str(resources),'--maximum-workers',str(maximum_workers),'--runtime-root',str(runtime_root())]
    else:command=[core_python(toolchain),'-B',runtime_root()/'oncotracer_cli/lpwgs/bin/quilt2_worker.py','--config',path]
    runner.run('quilt2-'+sample,command)
    return json.loads((output/f'{sample}.quilt2.summary.json').read_text())


def linkage(request,sample,bam,vcfs,fasta,root,runner,threads,toolchain=None,*,force=False):
    if not request.monte_carlo_linkage:return {}
    directory=runtime_root()/'oncotracer_cli/lpwgs/bin'
    code={name:sha256_file(directory/name) for name in ('haplotype_evidence.py','haplotype_genome.py')}
    def stat(path):
        p=Path(path).resolve();s=p.stat()
        return dict(path=str(p),size=s.st_size,mtime_ns=s.st_mtime_ns,inode=s.st_ino,device=s.st_dev)
    results={}
    for caller,vcf in vcfs.items():
        dest=Path(root)/sample/caller
        if dest.is_symlink():raise OncoTracerError('Linkage output must not be a symlink')
        receipt=dest/f'{sample}.linkage.complete.json'
        identity=dict(bam=stat(bam),reference=stat(fasta),vcf_sha256=sha256_file(vcf),code=code,sample=sample,
                      ffpe=request.specimen_type=='ffpe',parameters=dict(simulations=10000,seed=17,max_distance=5000,
                      min_fragments=6,min_mapq=30,min_baseq=20,multiple_testing='BY across whole caller VCF'))
        intent=dest.parent/f'.{sample}.{caller}.linkage-intent.json'
        if not receipt.exists() and dest.exists() and any(dest.iterdir()) and intent.is_file():
            try:known=json.loads(intent.read_text()).get('identity')==identity
            except (OSError,ValueError):known=False
            if known:
                manifest_path=dest/f'{sample}.manifest.json'
                try:
                    manifest=json.loads(manifest_path.read_text())
                    verified=bool(manifest['files']) and all((dest/f).is_file() and sha256_file(dest/f)==digest for f,digest in manifest['files'].items())
                except (OSError,ValueError,KeyError):verified=False
                if verified:atomic_write_json(receipt,dict(identity=identity,files=manifest['files']))
                else:os.replace(dest,dest.with_name(dest.name+'.superseded.'+uuid.uuid4().hex))
        if receipt.is_file():
            try:previous=json.loads(receipt.read_text())
            except (OSError,ValueError):previous={}
            if not force and previous.get('identity')==identity and previous.get('files') and all(
                (dest/f).is_file() and sha256_file(dest/f)==digest for f,digest in previous['files'].items()):
                results[caller]={**json.loads((dest/f'{sample}.haplotype_evidence.json').read_text()),'resumed':True,'report':str(dest/f'{sample}.haplotype_evidence.html')}
                continue
            os.replace(dest,dest.with_name(dest.name+'.superseded.'+uuid.uuid4().hex))
        elif dest.exists() and any(dest.iterdir()):
            raise OncoTracerError('Unverified linkage files are preserved; choose a new stage directory: '+str(dest))
        dest.parent.mkdir(parents=True,exist_ok=True)
        atomic_write_json(intent,dict(identity=identity))
        command=[core_python(toolchain),'-B',directory/'haplotype_evidence.py','--genome-wide',
                 '--bam',bam,'--vcf',vcf,'--reference',fasta,'--sample',sample,'--outdir',dest,
                 '--threads',str(threads),'--simulations','10000','--seed','17']
        if request.specimen_type=='ffpe':command.append('--ffpe')
        runner.run('monte-carlo-'+sample+'-'+caller,command)
        manifest=json.loads((dest/f'{sample}.manifest.json').read_text())
        atomic_write_json(receipt,dict(identity=identity,files=manifest['files']))
        results[caller]={**json.loads((dest/f'{sample}.haplotype_evidence.json').read_text()),'report':str(dest/f'{sample}.haplotype_evidence.html')}
    return results
