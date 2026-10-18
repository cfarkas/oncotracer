"""Complete, compulsory GLIMPSE2 short-variant backend for Illumina OncoTracer.

Samples execute in bounded slots with isolated Nextflow work/cache and traces.
A single total CPU/memory budget is partitioned across concurrently active samples.
"""
from __future__ import annotations
import csv
import html
import importlib.util
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import math
import signal
import shlex
import threading
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Mapping
from .source_runtime import python_code, python_module
from .runtime import CommandRunner, CommandResult, OncoTracerError, atomic_write_json, require_file, runtime_root, sha256_file, utc_now

SCHEMA='oncotracer-glimpse2-v1'
MIN_SAMPLE_MEMORY_GB=32 # Complete workflow includes the default CPU float32 local LLM.
BUNDLE=Path(__file__).parent/'lpwgs'
OPTION_KEYS=('parallel_samples','analysis_reuse_from','glimpse_reference_dir','glimpse_bin','resource_dir','confidence_gwas','patients','reuse_from','reference_reuse_from','nextflow','workflow_source','clinvar','memory','pgs_cache','ancestry_explorer_cache','literature_cache','llm_model','llm_python','patient_database','germline_resource','panel_of_normals','contamination_sites')
PATH_KEYS={'workflow_source','analysis_reuse_from','glimpse_reference_dir','glimpse_bin','resource_dir','patients','reuse_from','reference_reuse_from','clinvar','pgs_cache','ancestry_explorer_cache','literature_cache','llm_python','patient_database','germline_resource','panel_of_normals','contamination_sites'}


def workflow_bundle(source=None):
    """Locate a filesystem workflow in a source/wheel or verified zipapp payload."""
    if source:
        bundle = Path(source).expanduser().resolve()
    elif (BUNDLE/'main.nf').is_file():
        bundle = BUNDLE
    else:
        bundle = runtime_root()/'oncotracer_cli/lpwgs'
    if not (bundle/'main.nf').is_file() or not (bundle/'bin/launch.py').is_file():
        raise OncoTracerError('Complete GLIMPSE2 workflow is missing: ' + str(bundle))
    return bundle


def validate_patient_table(path):
    """Check optional TSV context without creating outputs or running tools."""
    if not path:
        return
    try:
        source=workflow_bundle()/'bin/patient_metadata.py'
        spec=importlib.util.spec_from_file_location('_oncotracer_patient_validation',source)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.load_rows(path)
    except (OSError,ValueError,csv.Error) as error:
        raise OncoTracerError('variant_patients: '+str(error)) from error


def validate_options(config):
    result={key:config['variant_'+key] for key in OPTION_KEYS if config.get('variant_'+key) not in (None,'')}
    confidence=result.setdefault('confidence_gwas','medium')
    if confidence not in {'strict','medium','exploratory'}:raise OncoTracerError('variant_confidence_gwas must be strict, medium or exploratory')
    for key in PATH_KEYS & result.keys():
        if not isinstance(result[key],str):raise OncoTracerError('variant_'+key+' must be a path')
        if any(c in result[key] for c in "\n\r\t'\x00"):raise OncoTracerError('Unsupported control/quote in variant_'+key)
        result[key]=str(Path(result[key]).expanduser().resolve())
    if str(config.get('variant_reference_build','hg38'))!='hg38':raise OncoTracerError('The complete GLIMPSE2 workflow requires hg38/GRCh38')
    if config.get('variant_glimpse2') is False:raise OncoTracerError('GLIMPSE2 is compulsory for Illumina short variants')
    result['parallel_samples']=_positive_slots(result.get('parallel_samples',1))
    result.setdefault('memory','32GB')
    _memory_gb(result['memory'])
    result.setdefault('nextflow','nextflow')
    validate_patient_table(result.get('patients'))
    return result


def plan(request):
    return {**request.as_dict(),'engine':'nextflow-glimpse2','nextflow_used':True,'glimpse2_required':True,
        'stages':['reuse-validated-bams','germline-and-somatic-candidates','glimpse2-reference-phase-ligate',
                  'clinvar-and-functional-annotation','germline-somatic-unconfirmed-evidence','traits-gwas-polygenic-models',
                  'ancestry-mitochondria-neanderthal','homozygosity-roh','cancer-and-cardiomyopathy',
                  'literature-and-integrated-reports','annotated-vcf',*(['ffperase-and-evidence-adjudication'] if request.specimen_type=='ffpe' else []),'cohort-vcf-merge'],
        'execution':'Samples run in bounded parallel slots with isolated Nextflow caches. threads and variant_memory are total budgets divided across active slots.',
        'resource_policy':{'requested_parallel_samples':_positive_slots((request.glimpse_config or {}).get('parallel_samples',1)), 'total_memory_gb':_memory_gb((request.glimpse_config or {}).get('memory','32GB')), 'minimum_memory_gb_per_sample':MIN_SAMPLE_MEMORY_GB, 'minimum_reason':'The complete workflow includes local float32 LLM reporting as well as genomic analysis.', 'allocation':'At execution: slots=min(requested, sample count, CPUs, floor(memory GB / 32)); each slot receives floor(CPUs / slots) and an equal memory share.'},
        'interpretation':'Imputation supports germline hypotheses; it does not prove somatic origin or FFPE artifact status. Votes reuse sequencing evidence and are not independent tests.',
        'ffpe_handling':'FFPErase flagged variants excluded from accepted set unless stringent germline rescue criteria pass; all remain in the audit VCF.'}


def preflight(request,toolchain=None):
    validate_patient_table((request.glimpse_config or {}).get('patients'))
    # Preserve native dependency discovery without entering this dispatcher again.
    from dataclasses import replace
    from .variants import preflight_variant_tools
    tools=preflight_variant_tools(replace(request,engine='native'),toolchain)
    if request.ffperase!='off':
        from .variant_model_assets import ffperase_pending,preflight_ffperase_runtime
        from . import ffperase
        preflight_ffperase_runtime(request)
        if not ffperase_pending(request):
            ffperase.preflight_models(ffperase.discover(request.ffperase_root,request.ffperase_models,request.ffperase_sif,request.ffperase_prefix))
    nf=str((request.glimpse_config or {}).get('nextflow','nextflow'))
    resolved=shutil.which(nf)
    if not resolved:raise OncoTracerError('Nextflow is required for the complete GLIMPSE2 workflow: '+nf)
    tools['nextflow']=str(Path(resolved).resolve())
    if not shutil.which('java'):raise OncoTracerError('Nextflow requires Java on PATH')
    bundle=workflow_bundle((request.glimpse_config or {}).get('workflow_source'))
    from .glimpse_readiness import check
    return check(request,tools,toolchain,bundle)


def _source_folder(path):
    path=Path(path)
    return path/'results' if (path/'results/08_variants').is_dir() else path


def verified_calls(source,sample,bam=None):
    """Use explicit donor callsets only after completion hash verification.

    Imported evidence keeps original filters; a different pipeline signature is
    documented, never misrepresented as an identical invocation.
    """
    if not source:return {},[]
    root=_source_folder(source); result={};audit=[]
    if bam is not None:
        try:
            snapshot=json.loads((root/'08_variants/variant_status.json').read_text())
            source_sample=next(x for x in snapshot['samples'] if x['sample']==sample)
            if not Path(source_sample['input_bam']).samefile(Path(bam)):return {},[]
        except (OSError,ValueError,KeyError,StopIteration):return {},[]
    for kind,caller in [('germline','bcftools'),('somatic','mutect2')]:
        directory=root/'08_variants/samples'/sample/caller
        receipt=directory/'complete.json'
        if not receipt.is_file():continue
        try:
            stamp=receipt.stat().st_mtime_ns
            if bam is not None and Path(bam).stat().st_mtime_ns>stamp:continue
            data=json.loads(receipt.read_text());name=data['vcf'];candidate=directory/name
            if Path(name).name!=name or data.get('result',{}).get('status')!='complete':continue
            # Verify the imported VCF and its index, not unrelated old PDFs/plots.
            digest=data['outputs'][name]
            if not candidate.is_file() or sha256_file(candidate)!=digest:continue
            indexes=[k for k in data['outputs'] if k in {name+'.tbi',name+'.csi'}]
            if not indexes or not all(sha256_file(directory/i)==data['outputs'][i] for i in indexes):continue
            if receipt.stat().st_mtime_ns != stamp:continue
            result[kind+'_vcf']=str(candidate.resolve())
            audit.append({'kind':kind,'caller':caller,'vcf':str(candidate.resolve()),'sha256':digest,'receipt':str(receipt),'original_signature':data.get('signature'),'reuse':'explicit imported evidence; original filters preserved'})
        except (ValueError,KeyError,OSError):continue
    return result,audit


def sample_parameters(request,sample,bam,reference,output,resources,threads,verified_imports=None,memory=None):
    options=request.glimpse_config or {}
    params={'python':sys.executable,'bam':str(Path(bam).resolve()),'sample':sample,'reference':str(reference),'output':str(output),
        'threads':threads,'memory':memory or options.get('memory','32GB'),'continue':True,'glimpse2':True,
        'annovar':True,'gwas':True,'imputed_clinvar':True,'polygenic':True,'explain_findings':True,
        'ancestry_explorer':True,'interpretation_report':True,'report_llm':True,'report_pdf':True,
        'homozygosity_report':True,'cancer_report':True,'cardiomyopathy_report':True,'unified_vcf':True,
        'pgs_reference_stats':True,'confidence_gwas':options.get('confidence_gwas','medium'),
        'resource_dir':str(resources),'llm_threads':min(threads,4),
        'pgs_cache':str(resources/'pgs'),'ancestry_explorer_cache':str(resources/'ancestry_explorer'),
        'literature_cache':str(resources/'literature'),'clinvar':str(resources/'clinvar/clinvar_GRCh38.vcf.gz'),
        'min_mapping_quality':request.min_mapping_quality,'min_base_quality':request.min_base_quality}
    for key in OPTION_KEYS:
        if key in options and key not in {'parallel_samples','analysis_reuse_from','reuse_from','nextflow','workflow_source','memory'}:params[key]=options[key]
    if request.tool_prefix:params['tool_prefix']=str(request.tool_prefix)
    if request.annovar_dir:params['annovar_dir']=str(request.annovar_dir)
    if request.annovar_db:params['annovar_db']=str(request.annovar_db)
    if request.targets_bed:params['regions']=str(request.targets_bed)
    imported,audit=verified_imports if verified_imports is not None else verified_calls(options.get('reuse_from'),sample,bam)
    params.update(imported)
    if options.get('analysis_reuse_from'):
        source=_analysis_source(options['analysis_reuse_from'],sample)
        if source is not None:params['reuse_from']=str(source)
    return params,audit


def ensure_resources(params,runner,bundle=None,*,bcftools=None):
    # Independent projects may explicitly share this cache. Legacy bootstrap
    # writers use fixed temporary names, so serialize preparation at this root.
    if getattr(runner, 'dry_run', False):
        return
    from .engine import _physical_directory_lock
    resources=Path(params['resource_dir'])
    resources.mkdir(parents=True,exist_ok=True)
    with _physical_directory_lock(resources, 'shared variant resource directory'):
        return _ensure_resources_locked(params,runner,bundle,bcftools=bcftools)


def _ensure_resources_locked(params,runner,bundle=None,*,bcftools=None):
    bundle=workflow_bundle() if bundle is None else Path(bundle)
    # Resolve reporting resources before CommandRunner isolates HOME/font caches.
    # Pass absolute paths to the child so installed local models remain visible.
    if params.get('report_llm'):
        import importlib.util
        spec=importlib.util.spec_from_file_location('_oncotracer_local_report_config',bundle/'bin/llm_reporting.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        resolved=dict(params);module.prepare(resolved)
        params['llm_python']=resolved['llm_python']
        params['llm_model']=resolved['llm_model_directory']
    resources=Path(params['resource_dir']);resources.mkdir(parents=True,exist_ok=True)
    flags=[]
    if not (resources/'gwas/associations.GRCh38.tsv.gz').is_file():flags.append('--gwas')
    if not (resources/'ancestry/reference/complete.json').is_file():flags.append('--ancestry')
    if flags:runner.run('glimpse-public-resources',[params.get('python',sys.executable),bundle/'bin/prepare_resources.py','--directory',resources,*flags])
    clinical=Path(params['clinvar'])
    if not clinical.is_file():
        # Reuse the resumable, integrity-checking downloader, never patient uploads.
        script="import sys;sys.path.insert(0,sys.argv[1]);from glimpse_download import download;from pathlib import Path;download(sys.argv[2],Path(sys.argv[3]))"
        runner.run('glimpse-clinvar-resource',[params.get('python',sys.executable),'-c',script,str(bundle/'bin'),'https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz',clinical])
    # Validate shared clinical input before spending hours preparing sample panels.
    # The original download may legitimately lack ##contig metadata.
    result=resources/'clinvar/prepared_clinvar.json'
    result.parent.mkdir(parents=True,exist_ok=True)
    bcftools=bcftools or ((Path(params['tool_prefix'])/'bin/bcftools') if params.get('tool_prefix') else shutil.which('bcftools'))
    if not bcftools:raise OncoTracerError('bcftools is required to validate ClinVar')
    runner.run('glimpse-clinvar-preflight',[params.get('python',sys.executable),bundle/'bin/clinvar_resource.py',
        '--clinvar',clinical,'--reference',params['reference'],'--bcftools',bcftools,
        '--cache',resources/'clinvar/header_cache','--result',result])
    params['clinvar']=json.loads(result.read_text())['clinvar']


def _page(root,status):
    rows=[]
    for s in status['samples']:
        rel=os.path.relpath(Path(s['output'])/'START_HERE.html',root)
        link=f'<a href="{html.escape(rel,quote=True)}">Informe completo</a>' if s['status']=='complete' else html.escape(s.get('error','Pendiente'))
        if s.get('ffpe_report'):
            review=os.path.relpath(s['ffpe_report']['index'],root)
            link=f'<a href="{html.escape(review,quote=True)}">Decisiones finales FFPE</a> · '+link
        rows.append(f'<tr><td>{html.escape(s["sample"])}</td><td>{html.escape(s["status"])}</td><td>{link}</td></tr>')
    cohort=status.get('cohort',{}).get('outputs',{})
    links=''.join(f'<li><a href="{html.escape(os.path.relpath(str(v),root),quote=True)}">{html.escape(k)}</a></li>' for k,v in cohort.items() if isinstance(v,str) and Path(v).is_file())
    allocation=status.get('resource_allocation',{})
    budget=('<p><strong>Muestras simultáneas: '+str(allocation.get('parallel_samples','1'))+' / solicitadas: '+str(allocation.get('requested_parallel_samples','1'))+'</strong> · '+str(allocation.get('threads_per_sample','?'))+' CPU y '+str(allocation.get('memory_gb_per_sample','?'))+' GB por muestra. Presupuesto total: '+str(allocation.get('total_threads','?'))+' CPU / '+str(allocation.get('total_memory_gb','?'))+' GB.</p>') if allocation else ''
    (root/'START_HERE.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8"><title>OncoTracer · variantes</title><style>body{font:17px system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#173c48}table{border-collapse:collapse;width:100%}td,th{padding:14px;border-bottom:1px solid #ddd;text-align:left}a{color:#006d77}</style><h1>OncoTracer · análisis completo de variantes</h1><p>GLIMPSE2, evidencia de lecturas, anotación, ascendencia y revisión de enfermedades. Los resultados de imputación no confirman origen somático.</p>'+budget+'<table><tr><th>Muestra</th><th>Estado</th><th>Resultados</th></tr>'+''.join(rows)+'</table><h2>VCF conjunto</h2><ul>'+links+'</ul><p>El archivo poblacional conserva genotipos faltantes y excluye por muestra evidencia somática o no confirmada. Es una fusión de llamadas, no joint calling.</p></html>')



def _claim_output_root(outdir, samples):
    """Only create or resume an owned namespace, before changing any status file."""
    base = Path(outdir) / '08_variants'
    root = base / 'glimpse2'
    samples = tuple(samples)
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', sample) for sample in samples):
        raise OncoTracerError('Unsafe sample identifier')
    directories = [Path(outdir), base, root, root/'samples', root/'launch', root/'cohort', root/'ffperase_resources']
    directories += [root/kind/sample for kind in ('samples', 'launch') for sample in samples]
    for directory in directories:
        if directory.is_symlink():
            raise OncoTracerError('Refusing a symlink variant output directory: ' + str(directory))
        if directory.exists() and not directory.is_dir():
            raise OncoTracerError('Variant output directory is not a directory: ' + str(directory))
    marker = root/'owner.json'
    if marker.is_symlink():
        raise OncoTracerError('GLIMPSE2 owner marker must not be a symlink')
    for target in [root/'START_HERE.html', *[root/'launch'/sample/'nextflow.log' for sample in samples]]:
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.stat().st_nlink != 1)):
            raise OncoTracerError('Refusing linked or non-regular variant output file: ' + str(target))
    if marker.exists():
        if not marker.is_file() or marker.stat().st_nlink != 1:
            raise OncoTracerError('GLIMPSE2 owner marker must be one regular, non-hard-linked file')
        try:
            owner = json.loads(marker.read_text())
        except (ValueError, OSError) as error:
            raise OncoTracerError('Invalid GLIMPSE2 output owner marker') from error
        if not isinstance(owner, dict) or owner.get('schema') != SCHEMA:
            raise OncoTracerError('GLIMPSE2 output ownership mismatch')
        if owner.get('canonical_path', str(root.resolve())) != str(root.resolve()):
            raise OncoTracerError('GLIMPSE2 output owner path mismatch')
        # Existing markers, including the initial schema-only format, remain intact.
    else:
        if root.exists() and any(root.iterdir()):
            raise OncoTracerError('Refusing a nonempty, unowned GLIMPSE2 output directory; preserve it and choose another outdir')
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, {'schema': SCHEMA, 'canonical_path': str(root.resolve())})
    return base, root


def _positive_slots(value):
    if isinstance(value,bool) or not re.fullmatch(r'[1-9][0-9]*',str(value)):
        raise OncoTracerError('variant_parallel_samples must be a positive integer')
    return int(value)


def _memory_gb(value):
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(GB|G|GIB|MB|M|MIB|TB|T|TIB)?\s*',str(value),re.I)
    if not match:raise OncoTracerError('variant_memory must be a total budget such as 64GB, 64 or 65536MB')
    unit=(match.group(2) or 'GB').upper()
    gb=float(match.group(1))*(1024 if unit.startswith('T') else 1/1024 if unit.startswith('M') else 1)
    if not math.isfinite(gb) or gb<MIN_SAMPLE_MEMORY_GB:
        raise OncoTracerError('Complete GLIMPSE2 analysis with local LLM reports requires at least 32GB total memory and 32GB per concurrent sample')
    return gb


def resource_allocation(request,threads,sample_count):
    if isinstance(threads,bool) or not isinstance(threads,int) or threads<1 or sample_count<1:
        raise OncoTracerError('Supply BAMs and positive integer threads')
    options=request.glimpse_config or {}
    requested=_positive_slots(options.get('parallel_samples',1))
    memory=_memory_gb(options.get('memory','32GB'))
    slots=min(requested,sample_count,threads,int(memory//MIN_SAMPLE_MEMORY_GB))
    per_cpu=threads//slots
    # Round down to MiB so displayed allocations never exceed the requested total.
    per_mb=int(memory*1024)//slots
    return {'requested_parallel_samples':requested,'parallel_samples':slots,
            'sample_count':sample_count,'total_threads':threads,'total_memory_gb':memory,
            'threads_per_sample':per_cpu,'memory_per_sample':str(per_mb)+'MB',
            'memory_gb_per_sample':per_mb/1024,'allocated_threads':slots*per_cpu,
            'allocated_memory_gb':slots*per_mb/1024,
            'unallocated_threads':threads-slots*per_cpu,
            'scope':'CPU/memory task budgets; Nextflow/controller and filesystem cache overhead are additional.'}


def _analysis_source(base,sample):
    base=Path(base).expanduser().resolve()
    if not base.is_dir():raise OncoTracerError('variant_analysis_reuse_from must be an existing analysis folder')
    candidates=[base/'08_variants/glimpse2/samples'/sample,base/'results/08_variants/glimpse2/samples'/sample,
                base/'samples'/sample,base/sample,base]
    for source in candidates:
        runtime=source/'pipeline_info/runtime.json'
        if not runtime.is_file():continue
        try:metadata=json.loads(runtime.read_text())
        except (OSError,ValueError) as error:raise OncoTracerError('Invalid analysis reuse runtime: '+str(runtime)) from error
        if metadata.get('sample')!=sample:
            if source==base and source.name==sample:raise OncoTracerError('Analysis reuse sample identity mismatch: '+sample)
            continue
        return source
    return None


class _SampleCancelled(Exception):
    pass


class _SampleRunner(CommandRunner):
    """Independent trace and child process group, cancellable without a shell."""
    def __init__(self,parent,trace,stop):
        super().__init__(trace,validators=parent.validators,dry_run=parent.dry_run,echo=parent.echo,
                         environment=parent.environment,protected_environment=parent.protected_environment)
        self.stop=stop

    @staticmethod
    def _stop_group(process):
        # Nextflow handles SIGINT by terminating its local tasks and preserving work.
        for sig,timeout in ((signal.SIGINT,10),(signal.SIGTERM,5),(signal.SIGKILL,5)):
            try:os.killpg(process.pid,sig)
            except ProcessLookupError:return
            try:process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:pass

    def run(self,stage,command,*,cwd=None,env=None,containment=None,stdout=None,stderr=None,stdin=None,check=True):
        if self.stop.is_set():raise _SampleCancelled('Stopped before launching '+stage)
        if self.dry_run:return super().run(stage,command,cwd=cwd,env=env,containment=containment,stdout=stdout,stderr=stderr,stdin=stdin,check=check)
        argv=tuple(map(str,command));started=utc_now();process=None
        if self.echo:print(f'[{stage}] {shlex.join(argv)}',file=sys.stderr,flush=True)
        self.validate_environment()
        try:
            process=subprocess.Popen(argv,cwd=cwd,env=self.child_environment(env,containment=containment),
                                     stdin=stdin,stdout=stdout,stderr=stderr,start_new_session=True)
            while process.poll() is None:
                if self.stop.wait(.2):
                    self._stop_group(process)
                    raise _SampleCancelled('Analysis interrupted; completed work and partial downloads retained')
            if self.stop.is_set():raise _SampleCancelled('Analysis interrupted')
            if check and process.returncode:
                raise OncoTracerError(f'stage {stage!r} failed with exit code {process.returncode}: {shlex.join(argv)}')
            return CommandResult(process.returncode,argv,started,utc_now())
        finally:
            if process is not None:
                if process.poll() is None:self._stop_group(process)
                self._record(stage,started,utc_now(),process.returncode,cwd,argv)
            self.validate_environment()


def _sample_runner(parent,trace,stop):
    # Normal execution always uses CommandRunner. Preserve simple injected test runners.
    return _SampleRunner(parent,trace,stop) if isinstance(parent,CommandRunner) else parent


def _prepare_ffpe_resources(request,root,runner,stop):
    from dataclasses import fields,replace
    from .variant_model_assets import prepare_variant_resources
    if not isinstance(runner,CommandRunner):return prepare_variant_resources(request,root/'ffperase_resources')
    spec=root/'ffperase_parameters.json';result=root/'ffperase_resources.json'
    path_fields=[field.name for field in fields(request) if isinstance(getattr(request,field.name),Path)]
    atomic_write_json(spec,{'request':request.as_dict(),'path_fields':path_fields,'root':str(root/'ffperase_resources')})
    code=('import json,sys;from pathlib import Path;from oncotracer_cli.variants import VariantRequest;'
          'from oncotracer_cli.variant_model_assets import prepare_variant_resources;'
          'from oncotracer_cli.runtime import atomic_write_json;'
          'c=json.loads(Path(sys.argv[1]).read_text());v=c["request"];'
          'v.update({k:Path(v[k]) for k in c["path_fields"]});'
          'v["matched_normals"]=tuple(v["matched_normals"].items());'
          'v["callers"]=tuple(v["callers"]);'
          'r=prepare_variant_resources(VariantRequest(**v),Path(c["root"]));'
          'atomic_write_json(Path(sys.argv[2]),{k:str(getattr(r,k)) if getattr(r,k) else None for k in ("ffperase_root","ffperase_models","ffperase_prefix","ffperase_sif")})')
    env={'PYTHONPATH':os.pathsep.join(filter(None,[str(Path(__file__).parent.parent),os.environ.get('PYTHONPATH','')]))}
    with (root/'ffperase_resources.log').open('a') as log:
        _sample_runner(runner,root/'ffperase_resource_commands.tsv',stop).run('ffperase-public-resources',
            [*python_code(code),spec,result],env=env,stdout=log,stderr=log)
    return replace(request,**{k:Path(v) if v else None for k,v in json.loads(result.read_text()).items()})


def _ffpe_command(resolved,vcf,sample_root,sample,paths,bam,reference,tools):
    command=[tools.get('python',sys.executable),*python_module('oncotracer_cli.glimpse_ffpe')[1:],'--run-ffperase','--vcf',vcf,
             '--outdir',sample_root/'ffpe_review','--sample',sample,'--bam',bam,'--reference',reference,
             '--samtools',tools['samtools'],'--gatk',tools['gatk']]
    for name in ('root','models','prefix','sif'):
        value=getattr(resolved,'ffperase_'+name)
        if value:command.extend(['--ffperase-'+name,value])
    for path in paths:command.extend(['--imputed',path])
    return command


def _cohort(request,exports,root,tools,reference,threads,imputed,sample_statuses,runner,stop):
    from .glimpse_joint import merge_cohort
    options={'threads':threads,'imputed_by_sample':{sample:list(map(str,paths)) for sample,paths in imputed.items()},
             'sample_roles':sample_statuses or {},
             'population_excluded_samples':{t:'matched_normal_from_same_individual_included' for t,n in request.matched_normals}}
    if not isinstance(runner,CommandRunner):
        return merge_cohort(exports,root/'cohort',tools['bcftools'],reference,**options)
    spec=root/'cohort_parameters.json';result=root/'cohort_result.json'
    atomic_write_json(spec,{'inputs':{s:str(v) for s,v in exports.items()},'output':str(root/'cohort'),
                           'bcftools':str(tools['bcftools']),'reference':str(reference),'options':options})
    code=('import json,sys;from oncotracer_cli.glimpse_joint import merge_cohort;'
          'from oncotracer_cli.runtime import atomic_write_json;from pathlib import Path;'
          'c=json.loads(Path(sys.argv[1]).read_text());'
          'r=merge_cohort(c["inputs"],c["output"],c["bcftools"],c["reference"],**c["options"]);'
          'atomic_write_json(Path(sys.argv[2]),r)')
    environment={'PYTHONPATH':os.pathsep.join(filter(None,[str(Path(__file__).parent.parent),os.environ.get('PYTHONPATH','')]))}
    with (root/'cohort.log').open('a') as log:
        _sample_runner(runner,root/'cohort_commands.tsv',stop).run('glimpse-cohort',[tools.get('python',sys.executable),*python_code(code)[1:],spec,result],
            env=environment,stdout=log,stderr=log)
    return json.loads(result.read_text())


def preflight_jobs(jobs,runner,bundle,*,force=False):
    """Check every selected sample and print reuse before scheduling any sample."""
    checks=[];requests=[]
    print('Checking completed outputs for every sample before analysis…',flush=True)
    for sample,bam,sample_root,launch,params,row in jobs:
        if force:
            params['continue']=False
            params.pop('reuse_from',None)
        requests.append({'parameters':{**params,'preflight_only':True}})
    if not jobs:return checks
    launch=jobs[0][3].parent
    request_path=launch/'preflight_request.json';response_path=launch/'preflight_response.json'
    atomic_write_json(request_path,{'requests':requests})
    with request_path.open() as request_file,response_path.open('w') as response_file:
        runner.run('glimpse-resume-preflight',
                   [jobs[0][4].get('python',sys.executable),'-B',bundle/'bin/nextflow_entry.py'],
                   stdin=request_file,stdout=response_file,cwd=launch)
    response=json.loads(response_path.read_text())
    if len(response.get('samples',[]))!=len(jobs):raise OncoTracerError('Incomplete cohort preflight response')
    for job,prepared in zip(jobs,response['samples']):
        sample,bam,sample_root,launch,params,row=job
        if prepared.get('run') is not False:
            raise OncoTracerError('Expected a preflight-only response for '+sample)
        row['reuse_validation']=prepared['reuse_validation']
        checks.append(row['reuse_validation'])
    print('All sample resume checks passed. Starting the remaining work.',flush=True)
    return checks


def run(request,bams,reference,outdir,runner,ledger,*,threads=1,force=False,sample_statuses=None,toolchain=None):
    allocation=resource_allocation(request,threads,len(bams))
    if allocation['parallel_samples'] < min(allocation['requested_parallel_samples'],len(bams)):
        print(f"Requested {allocation['requested_parallel_samples']} parallel samples; "
              f"the {allocation['total_memory_gb']:g}GB / {threads}-CPU total budget allows "
              f"{allocation['parallel_samples']}. Each concurrent sample requires at least "
              f"{MIN_SAMPLE_MEMORY_GB}GB.",file=sys.stderr,flush=True)
    if len(set(Path(p).resolve() for p in bams.values()))!=len(bams):raise OncoTracerError('One physical BAM cannot represent multiple individuals')
    for tumor,normal in request.matched_normals:
        if tumor not in bams or normal not in bams or (sample_statuses or {}).get(tumor)!='tumor' or (sample_statuses or {}).get(normal)!='normal':
            raise OncoTracerError('Matched normal mapping requires selected tumor/normal samples with explicit roles')
    tools=preflight(request,toolchain)
    reference=require_file(Path(reference['fasta'] if isinstance(reference,Mapping) else reference),'GRCh38 FASTA').resolve()
    base,root=_claim_output_root(outdir,bams)
    options=request.glimpse_config or {};resources=Path(options.get('resource_dir') or root/'resources').resolve()
    bundle=workflow_bundle(options.get('workflow_source'))
    imported_calls={sample:verified_calls(options.get('reuse_from'),sample,bam) for sample,bam in sorted(bams.items())}
    status={'schema':SCHEMA,'engine':'nextflow-glimpse2','nextflow_used':True,'requested':plan(request),
            'resource_allocation':allocation,'started_at':utc_now(),'samples':[], 'completed_samples':[],
            'failed_samples':[],'interrupted_samples':[],'overall_status':'running','status_file':str(base/'variant_status.json')}
    jobs=[];exports={};imputed={};stop=threading.Event()
    for sample,bam in sorted(bams.items()):
        sample_root=root/'samples'/sample;sample_root.mkdir(parents=True,exist_ok=True)
        params,audit=sample_parameters(request,sample,bam,reference,sample_root,resources,
            allocation['threads_per_sample'],verified_imports=imported_calls[sample],memory=allocation['memory_per_sample'])
        normal=dict(request.matched_normals).get(sample)
        if normal:
            params.update(matched_normal_bam=str(Path(bams[normal]).resolve()),matched_normal_sample=normal)
            params.pop('somatic_vcf',None)
            audit=[x for x in audit if x['kind']!='somatic']
        row={'sample':sample,'input_bam':str(Path(bam).resolve()),'status':'queued',
             'sample_role':(sample_statuses or {}).get(sample,'unknown'),'output':str(sample_root),
             'reused_calls':audit,'callers':[],'threads':allocation['threads_per_sample'],'memory':allocation['memory_per_sample']}
        status['samples'].append(row)
        launch=root/'launch'/sample;launch.mkdir(parents=True,exist_ok=True)
        for key in ('python','llm_python','llm_model'):
            if key in tools:params[key]=tools[key]
        jobs.append((sample,bam,sample_root,launch,params,row))

    def publish():
        # Only the controller thread calls this: no competing atomic temp paths.
        atomic_write_json(Path(status['status_file']),status);_page(root,status)

    previous_handlers={}
    def interrupted(signum,frame):
        stop.set()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous_handlers[sig]=signal.getsignal(sig);signal.signal(sig,interrupted)
    publish()
    try:
        # Public bootstrap has a few unguarded legacy writers; run it once before
        # any sample. Later panel/PGS/literature downloads use cross-process flocks.
        first=jobs[0][4]
        bootstrap=_sample_runner(runner,root/'resource_commands.tsv',stop)
        ensure_resources(first,bootstrap,bundle,bcftools=tools.get('bcftools'))
        for job in jobs[1:]:
            for key in ('llm_python','llm_model','clinvar'):
                if key in first:job[4][key]=first[key]
        status['reuse_validation']=preflight_jobs(jobs,bootstrap,bundle,force=force)
        publish()
        resolved_ffpe=None
        if request.specimen_type=='ffpe':
            resolved_ffpe=_prepare_ffpe_resources(request,root,runner,stop)
            from . import ffperase
            ffperase.preflight_models(ffperase.discover(resolved_ffpe.ffperase_root,resolved_ffpe.ffperase_models,resolved_ffpe.ffperase_sif,resolved_ffpe.ffperase_prefix))

        def execute(job):
            sample,bam,sample_root,launch,params,row=job
            sample_runner=_sample_runner(runner,launch/'commands.tsv',stop)
            if isinstance(sample_runner,CommandRunner):
                # Nextflow schedules many one-CPU tasks: numerical libraries must
                # not each discover the entire host. Explicit tool threads remain.
                sample_runner.environment.update({key:'1' for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')})
                sample_runner.environment['LOKY_MAX_CPU_COUNT']=str(allocation['threads_per_sample'])
            params_path=launch/'params.json'
            if force:params['continue']=False;params.pop('reuse_from',None)
            command=[tools['nextflow'],'run',bundle/'main.nf','-params-file',params_path,'-work-dir',sample_root/'work']
            if not force:command.append('--continue')
            result={'command':list(map(str,command))}
            try:
                atomic_write_json(params_path,params)
                if stop.is_set():raise _SampleCancelled('Stopped before launching sample')
                with (launch/'nextflow.log').open('a') as log:
                    sample_runner.run('complete-glimpse2-'+sample,command,cwd=launch,stdout=log,stderr=log)
                if stop.is_set():raise _SampleCancelled('Stopped after sample workflow')
                vcf=sample_root/'cohort_vcf'/f'{sample}.cohort_vcf'/f'{sample}.annotated.vcf.gz'
                require_file(vcf,'Completed annotated VCF')
                paths=sorted((sample_root/'imputation').rglob('*.imputed.bcf'))
                if not paths:raise OncoTracerError('No completed GLIMPSE2 chromosome BCFs found')
                if resolved_ffpe is not None:
                    package_path=str(Path(__file__).parent.parent)
                    env={'PYTHONPATH':os.pathsep.join(filter(None,[package_path,os.environ.get('PYTHONPATH','')]))}
                    # FFPErase runs Picard outside Nextflow; cap this sample's JVM.
                    env['_JAVA_OPTIONS']=f'-Xmx{max(1,int(allocation["memory_gb_per_sample"]*.7))}g -XX:ActiveProcessorCount={allocation["threads_per_sample"]}'
                    with (launch/'ffperase.log').open('a') as log:
                        sample_runner.run('ffpe-glimpse2-'+sample,_ffpe_command(resolved_ffpe,vcf,sample_root,sample,paths,bam,reference,tools),
                                          cwd=launch,env=env,stdout=log,stderr=log)
                    vote=json.loads((sample_root/'ffpe_review'/f'{sample}.ffpe_glimpse.summary.json').read_text())
                    vcf=Path(vote['all_vcf']);result['ffpe_review']=vote
                    from .glimpse_ffpe_report import render_ffpe_review
                    result['ffpe_report']=render_ffpe_review(sample_root,sample,vote)
                return {**result,'status':'complete','annotated_vcf':str(vcf)},vcf,paths
            except _SampleCancelled as error:
                return {**result,'status':'interrupted','error':str(error)},None,None
            except (OSError,ValueError,subprocess.SubprocessError,OncoTracerError,RuntimeError) as error:
                return {**result,'status':'failed','error':str(error)},None,None

        pending=iter(jobs);active={}
        with ThreadPoolExecutor(max_workers=allocation['parallel_samples'],thread_name_prefix='glimpse-sample') as pool:
            def fill_slots():
                while not stop.is_set() and len(active)<allocation['parallel_samples']:
                    job=next(pending,None)
                    if job is None:break
                    row=job[5];row.update(status='running',started_at=utc_now())
                    active[pool.submit(execute,job)]=job
                publish()
            try:
                fill_slots()
                while active:
                    completed,_=wait(active,timeout=.2,return_when=FIRST_COMPLETED)
                    for future in completed:
                        job=active.pop(future);sample=job[0];row=job[5]
                        result,vcf,paths=future.result();row.update(result,finished_at=utc_now())
                        if row['status']=='complete':exports[sample]=vcf;imputed[sample]=paths;status['completed_samples'].append(sample)
                        elif row['status']=='failed':status['failed_samples'].append(sample)
                        else:status['interrupted_samples'].append(sample)
                    if completed:fill_slots()
            except BaseException:
                stop.set()
                raise
        if stop.is_set():
            for row in status['samples']:
                if row['status']=='queued':row['status']='not_started';row['reason']='Analysis interrupted before a sample slot became available'
        # Full cohort only after every selected sample succeeds.
        if not stop.is_set() and len(exports)==len(bams):
            status['cohort_status']='running';publish()
            try:
                status['cohort']=_cohort(request,exports,root,tools,reference,threads,imputed,sample_statuses,runner,stop)
                status['cohort']['sample_roles']=sample_statuses or {};status['cohort_status']='complete'
            except (OSError,ValueError,subprocess.SubprocessError,OncoTracerError,RuntimeError) as error:status['cohort_error']=str(error)
        status['overall_status']='interrupted' if stop.is_set() else 'partial_failure' if status.get('cohort_error') or status['failed_samples'] and exports else 'failed' if status['failed_samples'] else 'complete'
    except (_SampleCancelled,KeyboardInterrupt):
        stop.set();status['overall_status']='interrupted'
        if status.get('cohort_status')=='running':status['cohort_status']='interrupted'
        for row in status['samples']:
            if row['status']=='queued':row['status']='not_started'
    except Exception as error:
        stop.set();status.update(overall_status='failed',error=str(error))
        for row in status['samples']:
            if row['status']=='queued':row['status']='not_started'
            elif row['status']=='running':row.update(status='interrupted',error='Stopped after controller failure')
    finally:
        for sig,handler in previous_handlers.items():signal.signal(sig,handler)
        status['finished_at']=utc_now();publish()
    return status
