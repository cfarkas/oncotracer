"""Complete, compulsory GLIMPSE2 short-variant backend for Illumina OncoTracer.

Samples execute sequentially, each with an isolated native Nextflow work/cache.
This bounds total CPU use and keeps proven per-sample continuation semantics.
"""
from __future__ import annotations
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Mapping
from .runtime import OncoTracerError, atomic_write_json, require_file, runtime_root, sha256_file, utc_now

SCHEMA='oncotracer-glimpse2-v1'
BUNDLE=Path(__file__).parent/'lpwgs'
OPTION_KEYS=('glimpse_reference_dir','glimpse_bin','resource_dir','confidence_gwas','patients','reuse_from','reference_reuse_from','nextflow','workflow_source','clinvar','memory','pgs_cache','ancestry_explorer_cache','literature_cache','llm_model','llm_python','patient_database','germline_resource','panel_of_normals','contamination_sites')
PATH_KEYS={'glimpse_reference_dir','glimpse_bin','resource_dir','patients','reuse_from','reference_reuse_from','clinvar','pgs_cache','ancestry_explorer_cache','literature_cache','llm_python','patient_database','germline_resource','panel_of_normals','contamination_sites'}


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
    result.setdefault('memory','32GB')
    result.setdefault('nextflow','nextflow')
    return result


def plan(request):
    return {**request.as_dict(),'engine':'nextflow-glimpse2','nextflow_used':True,'glimpse2_required':True,
        'stages':['reuse-validated-bams','germline-and-somatic-candidates','glimpse2-reference-phase-ligate',
                  'clinvar-and-functional-annotation','germline-somatic-unconfirmed-evidence','traits-gwas-polygenic-models',
                  'ancestry-mitochondria-neanderthal','homozygosity-roh','cancer-and-cardiomyopathy',
                  'literature-and-integrated-reports','annotated-vcf',*(['ffperase-and-evidence-adjudication'] if request.specimen_type=='ffpe' else []),'cohort-vcf-merge'],
        'execution':'Samples run sequentially with isolated per-sample Nextflow caches; tasks within a sample share the CPU/memory budget.',
        'interpretation':'Imputation supports germline hypotheses; it does not prove somatic origin or FFPE artifact status. Votes reuse sequencing evidence and are not independent tests.',
        'ffpe_handling':'FFPErase flagged variants excluded from accepted set unless stringent germline rescue criteria pass; all remain in the audit VCF.'}


def preflight(request,toolchain=None):
    # Preserve native dependency discovery without entering this dispatcher again.
    from dataclasses import replace
    from .variants import preflight_variant_tools
    tools=preflight_variant_tools(replace(request,engine='native'),toolchain)
    nf=str((request.glimpse_config or {}).get('nextflow','nextflow'))
    resolved=shutil.which(nf)
    if not resolved:raise OncoTracerError('Nextflow is required for the complete GLIMPSE2 workflow: '+nf)
    tools['nextflow']=str(Path(resolved).resolve())
    if not shutil.which('java'):raise OncoTracerError('Nextflow requires Java on PATH')
    workflow_bundle((request.glimpse_config or {}).get('workflow_source'))
    return tools


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


def sample_parameters(request,sample,bam,reference,output,resources,threads,verified_imports=None):
    options=request.glimpse_config or {}
    params={'python':sys.executable,'bam':str(Path(bam).resolve()),'sample':sample,'reference':str(reference),'output':str(output),
        'threads':threads,'memory':options.get('memory','32GB'),'continue':True,'glimpse2':True,
        'annovar':True,'gwas':True,'imputed_clinvar':True,'polygenic':True,'explain_findings':True,
        'ancestry_explorer':True,'interpretation_report':True,'report_llm':True,'report_pdf':True,
        'homozygosity_report':True,'cancer_report':True,'cardiomyopathy_report':True,'unified_vcf':True,
        'pgs_reference_stats':True,'confidence_gwas':options.get('confidence_gwas','medium'),
        'resource_dir':str(resources),'llm_threads':min(threads,4),
        'pgs_cache':str(resources/'pgs'),'ancestry_explorer_cache':str(resources/'ancestry_explorer'),
        'literature_cache':str(resources/'literature'),'clinvar':str(resources/'clinvar/clinvar_GRCh38.vcf.gz'),
        'min_mapping_quality':request.min_mapping_quality,'min_base_quality':request.min_base_quality}
    for key in OPTION_KEYS:
        if key in options and key not in {'reuse_from','nextflow','workflow_source','memory'}:params[key]=options[key]
    if request.tool_prefix:params['tool_prefix']=str(request.tool_prefix)
    if request.annovar_dir:params['annovar_dir']=str(request.annovar_dir)
    if request.annovar_db:params['annovar_db']=str(request.annovar_db)
    if request.targets_bed:params['regions']=str(request.targets_bed)
    imported,audit=verified_imports if verified_imports is not None else verified_calls(options.get('reuse_from'),sample,bam)
    params.update(imported)
    return params,audit


def ensure_resources(params,runner,bundle=None):
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
    if flags:runner.run('glimpse-public-resources',[sys.executable,bundle/'bin/prepare_resources.py','--directory',resources,*flags])
    clinical=Path(params['clinvar'])
    if not clinical.is_file():
        # Reuse the resumable, integrity-checking downloader, never patient uploads.
        script="import sys;sys.path.insert(0,sys.argv[1]);from glimpse_download import download;from pathlib import Path;download(sys.argv[2],Path(sys.argv[3]))"
        runner.run('glimpse-clinvar-resource',[sys.executable,'-c',script,str(bundle/'bin'),'https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz',clinical])


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
    (root/'START_HERE.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8"><title>OncoTracer · variantes</title><style>body{font:17px system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#173c48}table{border-collapse:collapse;width:100%}td,th{padding:14px;border-bottom:1px solid #ddd;text-align:left}a{color:#006d77}</style><h1>OncoTracer · análisis completo de variantes</h1><p>GLIMPSE2, evidencia de lecturas, anotación, ascendencia y revisión de enfermedades. Los resultados de imputación no confirman origen somático.</p><table><tr><th>Muestra</th><th>Estado</th><th>Resultados</th></tr>'+''.join(rows)+'</table><h2>VCF conjunto</h2><ul>'+links+'</ul><p>El archivo poblacional conserva genotipos faltantes y excluye por muestra evidencia somática o no confirmada. Es una fusión de llamadas, no joint calling.</p></html>')



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


def run(request,bams,reference,outdir,runner,ledger,*,threads=1,force=False,sample_statuses=None,toolchain=None):
    if not bams or threads<1:raise OncoTracerError('Supply BAMs and positive threads')
    if len(set(Path(p).resolve() for p in bams.values()))!=len(bams):raise OncoTracerError('One physical BAM cannot represent multiple individuals')
    for tumor,normal in request.matched_normals:
        if tumor not in bams or normal not in bams or (sample_statuses or {}).get(tumor)!='tumor' or (sample_statuses or {}).get(normal)!='normal':
            raise OncoTracerError('Matched normal mapping requires selected tumor/normal samples with explicit roles')
    tools=preflight(request,toolchain)
    reference=require_file(Path(reference['fasta'] if isinstance(reference,Mapping) else reference),'GRCh38 FASTA').resolve()
    base,root=_claim_output_root(outdir,bams)
    options=request.glimpse_config or {};resources=Path(options.get('resource_dir') or root/'resources').resolve()
    source=options.get('workflow_source')
    bundle=workflow_bundle(source)
    if source and not (bundle/'main.nf').is_file():raise OncoTracerError('variant_workflow_source must be a local complete workflow folder. For remote execution use nextflow run repository -r revision at the top-level entry point.')
    # Read legacy donor status before publishing this run's status, including when
    # the explicitly selected donor is this same output directory.
    imported_calls={sample:verified_calls(options.get('reuse_from'),sample,bam) for sample,bam in sorted(bams.items())}
    status={'schema':SCHEMA,'engine':'nextflow-glimpse2','nextflow_used':True,'requested':plan(request),'started_at':utc_now(),'samples':[],'completed_samples':[],'failed_samples':[],'overall_status':'running','status_file':str(base/'variant_status.json')}
    exports={};imputed={}
    atomic_write_json(Path(status['status_file']),status)
    for sample,bam in sorted(bams.items()):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',sample):raise OncoTracerError('Unsafe sample identifier')
        sample_root=root/'samples'/sample;sample_root.mkdir(parents=True,exist_ok=True)
        params,audit=sample_parameters(request,sample,bam,reference,sample_root,resources,threads,verified_imports=imported_calls[sample])
        normal=dict(request.matched_normals).get(sample)
        if normal:
            params.update(matched_normal_bam=str(Path(bams[normal]).resolve()),matched_normal_sample=normal)
            params.pop('somatic_vcf',None) # Recompute with the explicit matched normal instead of importing tumor-only calls.
            audit=[x for x in audit if x['kind']!='somatic']
        row={'sample':sample,'input_bam':str(Path(bam).resolve()),'status':'running','sample_role':(sample_statuses or {}).get(sample,'unknown'),'output':str(sample_root),'reused_calls':audit,'callers':[]};status['samples'].append(row)
        launch=root/'launch'/sample;launch.mkdir(parents=True,exist_ok=True)
        params_path=launch/'params.json';atomic_write_json(params_path,params)
        try:
            ensure_resources(params,runner,bundle)
            atomic_write_json(params_path,params)
            command=[tools['nextflow'],'run',bundle/'main.nf','-params-file',params_path,'-work-dir',sample_root/'work','--continue']
            if force:command=[x for x in command if x!='--continue'];params['continue']=False;atomic_write_json(params_path,params)
            row['command']=list(map(str,command));atomic_write_json(Path(status['status_file']),status)
            with (launch/'nextflow.log').open('a') as log:
                runner.run('complete-glimpse2-'+sample,command,cwd=launch,stdout=log,stderr=log)
            vcf=sample_root/'cohort_vcf'/f'{sample}.cohort_vcf'/f'{sample}.annotated.vcf.gz'
            require_file(vcf,'Completed annotated VCF')
            paths=sorted((sample_root/'imputation').rglob('*.imputed.bcf'))
            if not paths:raise OncoTracerError('No completed GLIMPSE2 chromosome BCFs found')
            if request.specimen_type=='ffpe':
                from .glimpse_ffpe import assess_and_vote
                from .ffperase import discover
                from .variant_model_assets import prepare_variant_resources
                resolved=prepare_variant_resources(request,root/'ffperase_resources')
                detection=discover(resolved.ffperase_root,resolved.ffperase_models,resolved.ffperase_sif,resolved.ffperase_prefix)
                vote=assess_and_vote(vcf,sample_root/'ffpe_review',sample,imputed_paths=paths,bam=Path(bam),reference=reference,detection=detection,tools=tools)
                vcf=Path(vote['all_vcf']);row['ffpe_review']=vote
                from .glimpse_ffpe_report import render_ffpe_review
                row['ffpe_report']=render_ffpe_review(sample_root,sample,vote)
            exports[sample]=vcf;imputed[sample]=paths
            row.update(status='complete',annotated_vcf=str(vcf));status['completed_samples'].append(sample)
        except (OSError,ValueError,subprocess.SubprocessError,OncoTracerError,RuntimeError) as error:
            row.update(status='failed',error=str(error));status['failed_samples'].append(sample)
        atomic_write_json(Path(status['status_file']),status);_page(root,status)
    # A partial cohort must never masquerade as the requested population dataset.
    if not status['failed_samples'] and exports:
        from .glimpse_joint import merge_cohort
        try:
            status['cohort']=merge_cohort(exports,root/'cohort',tools['bcftools'],reference,threads=threads,imputed_by_sample=imputed,sample_roles=sample_statuses or {},population_excluded_samples={t:'matched_normal_from_same_individual_included' for t,n in request.matched_normals})
            status['cohort']['sample_roles']=sample_statuses or {}
        except (OSError,ValueError,subprocess.SubprocessError,OncoTracerError,RuntimeError) as error:
            status['cohort_error']=str(error)
    status.update(overall_status='partial_failure' if status.get('cohort_error') else 'partial_failure' if status['failed_samples'] and exports else 'failed' if status['failed_samples'] else 'complete',finished_at=utc_now())
    atomic_write_json(Path(status['status_file']),status);_page(root,status)
    return status
