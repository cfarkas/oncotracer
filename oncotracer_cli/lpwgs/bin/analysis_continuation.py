"""Reuse completed downstream analyses after a reporting-only workflow change."""
import ast
import hashlib
import json
from pathlib import Path
import shlex
from reuse_outputs import link_file
from llm_reporting import KEYS as REPORT_KEYS

EXPLANATION_KEYS={'ancestry_explorer','ancestry_explorer_manifest','ancestry_explorer_cache','explain_findings','explanation_evidence','literature_cache','literature_release','literature_offline'}
PGS_KEYS={'imputed_clinvar','polygenic','pgs_manifest','pgs_cache','pgs_ids','pgs_source_files','pgs_min_coverage','pgs_min_weight_coverage'}
CODE={
 'traits':['pipeline.py','traits_lpwgs.py','gwas_confidence.py'],
 'evidence':['pipeline.py','traits_lpwgs.py','variant_evidence.py'],
 'imputed_clinvar':['pipeline.py','traits_lpwgs.py','variant_evidence.py','extension_common.py','imputed_clinvar.py'],
 'pgs_prepare':['polygenic.py','glimpse_download.py','extension_common.py','traits_lpwgs.py','pipeline.py'],
 'pgs_score':['polygenic.py','extension_common.py','traits_lpwgs.py','pipeline.py'],
}


def signature(config,stage):
    skip=EXPLANATION_KEYS|REPORT_KEYS|{'pipeline_source_sha256','root','output'}
    if stage!='traits':skip.add('confidence_gwas')
    resources={config.get('explanation_evidence'),config.get('ancestry_explorer_manifest')}
    if stage in {'traits','evidence','imputed_clinvar'}:
        skip |= PGS_KEYS;resources.add(config.get('pgs_manifest'))
    value={k:v for k,v in config.items() if k not in skip}
    if value.get('confidence_gwas') is None:value.pop('confidence_gwas',None)
    value['resources']=[r for r in value.get('resources',[]) if r['path'] not in resources]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def analytic_code(path,stage='traits'):
    """Normalize only documented presentation changes from the previous release."""
    raw=Path(path).read_text();name=Path(path).name
    if name=='traits_lpwgs.py':
        raw='\n'.join(line for line in raw.splitlines() if not line.startswith('    broader=') and '.traits_report.html\").write_text' not in line and ".traits_report.html').write_text" not in line)
    if name=='variant_evidence.py':
        raw=raw.replace("if has_clinvar(row):", "if row['clinvar_classification']!='.':")
    tree=ast.parse(raw)
    if name=='polygenic.py' and stage in {'pgs_prepare','pgs_score'}:
        tree.body=[n for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name in {'report','main'})]
    if name=='pipeline.py':tree.body=[n for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name=='main')]
    if name=='traits_lpwgs.py' and stage!='traits':
        tree.body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or (isinstance(n,ast.FunctionDef) and n.name in {'imputed_call','add_imputed_record'})]
    if name=='variant_evidence.py':
        tree.body=[n for n in tree.body if not (isinstance(n,ast.ClassDef) and n.name=='Workbook') and not (isinstance(n,ast.FunctionDef) and n.name=='has_clinvar')]
    return ast.dump(tree,include_attributes=False)


def describe(work):
    args=shlex.split((work/'.command.sh').read_text(),comments=True)
    if len(args)<3:return None
    module=Path(args[1]).name;tail=args[2:]
    if module=='pipeline.py' and tail[0]=='traits':stage='traits';tail=tail[1:]
    elif module=='variant_evidence.py':stage='evidence'
    elif module=='imputed_clinvar.py' and tail[0]=='annotate':stage='imputed_clinvar';tail=tail[1:]
    elif module=='polygenic.py' and tail[0] in {'prepare','score'}:stage='pgs_'+tail[0];tail=tail[1:]
    else:return None
    options={};key=None
    for x in tail:
        if x.startswith('--'):key=x;options[key]=[]
        elif key:options[key].append(x)
        else:return None
    if len(options.get('--config',[]))!=1:return None
    return stage,options,work/args[1]


def outputs(work,stage,sample,tag):
    if stage=='traits':names=[f'{sample}.{n}' for n in ['traits_gwas.json','traits.tsv','gwas.tsv','gwas_trait_summary.tsv','imputation_site_review.tsv','traits_report.html']];folder='traits_gwas'
    elif stage=='evidence':names=[f'{sample}.{n}' for n in ['variant_evidence.json','variant_evidence.html','variant_review.xlsx','all_variants.tsv.gz','clinvar.tsv.gz','pathogenic_labels.tsv.gz','germline_supported.tsv.gz','somatic_candidate.tsv.gz','unconfirmed.tsv.gz']];folder='variant_evidence'
    elif stage=='imputed_clinvar':names=[f'{sample}.{tag}.{n}' for n in ['imputed_clinvar.json','clinvar_all_sites.tsv.gz','clinvar.bcf','clinvar.bcf.csi','all_nonreference.tsv.gz','clinvar_nonreference.tsv.gz','pathogenic_labels.tsv.gz']];folder='imputed_clinvar/chromosomes'
    elif stage=='pgs_prepare':names=[tag+'.weights.tsv.gz',tag+'.model.json'];folder='polygenic/models'
    else:names=[f'{sample}.{tag}.score.json',f'{sample}.{tag}.variant_audit.tsv.gz'];folder='polygenic/scores'
    return names,folder


def collect(config,root,outputs_roots,core,imputation):
    result={stage:{} for stage in CODE}
    expected_chroms=set(config['glimpse_chromosomes']) if config.get('glimpse2') else set()
    if expected_chroms-set(imputation.get('ligated',{})):return result
    required={f'merge:{config["sample"]}',f'annotate:{config["sample"]}:germline',f'annotate:{config["sample"]}:somatic'}
    if not required.issubset(core):return result
    for output_root in outputs_roots:
        for command in sorted((Path(output_root)/'work').glob('*/*/.command.sh'),key=lambda p:p.stat().st_mtime):
            work=command.parent
            try:
                desc=describe(work)
                if not desc:continue
                stage,options,old_code=desc
                end=work/'.exitcode';begin=work/'.command.begin'
                if not end.is_file() or end.read_text().strip()!='0' or not begin.is_file():continue
                previous=json.loads((work/options['--config'][0]).read_text())
                if signature(previous,stage)!=signature(config,stage):continue
                if any(analytic_code(old_code.parent/name,stage)!=analytic_code(Path(root)/'bin'/name,stage) for name in CODE[stage]):continue
                # Every staged biological input must be unchanged since task start.
                biological=[p for p in work.iterdir() if p.is_symlink() and p.name!='bin']
                if any(p.is_file() and p.stat().st_mtime_ns>begin.stat().st_mtime_ns for p in biological):continue
                tag=(options.get('--chrom') or options.get('--pgs-id') or [config['sample']])[0]
                if stage=='imputed_clinvar' and tag not in expected_chroms:continue
                if stage.startswith('pgs_') and tag not in config.get('pgs_ids',[]):continue
                names,folder=outputs(work,stage,config['sample'],tag)
                if any(not (work/n).is_file() or (work/n).stat().st_size==0 or (work/n).stat().st_mtime_ns>end.stat().st_mtime_ns for n in names):continue
                paths=[str(work/n) for n in names]
                # A prior validated export correction changed presentation/ClinVar subsets only.
                refresh=Path(output_root)/folder/(config['sample']+'.export_refresh.json')
                if stage=='evidence' and refresh.is_file():
                    receipt=json.loads(refresh.read_text());published=Path(output_root)/folder
                    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
                    if (receipt.get('variant_evidence_implementation_sha256')==digest(Path(root)/'bin/variant_evidence.py') and
                        receipt.get('updated_summary_sha256')==digest(published/(config['sample']+'.variant_evidence.json')) and
                        all((published/n).is_file() for n in names)):
                        paths=[str(published/n) for n in names]
                result[stage][tag]={'files':paths,'source_work':str(work),'signature':signature(config,stage),'folder':folder}
            except (OSError,ValueError,KeyError,SyntaxError):continue
    for items in result.values():
        for value in items.values():
            for p in value['files']:link_file(p,Path(config['output'])/value['folder']/Path(p).name)
    return result


def trait_inputs(config,root,output_roots,core,imputation):
    if set(config.get('glimpse_chromosomes',[]))-set(imputation.get('ligated',{})):return []
    if f"merge:{config['sample']}" not in core:return []
    for output in reversed(output_roots):
        for script in sorted((Path(output)/'work').glob('*/*/.command.sh'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                work=script.parent;desc=describe(work)
                if not desc or desc[0]!='traits':continue
                _,options,old_code=desc;end=work/'.exitcode'
                if not end.exists() or end.read_text().strip()!='0':continue
                previous=json.loads((work/options['--config'][0]).read_text())
                # Confidence affects interpretation only; all other input settings must match.
                left=dict(previous);right=dict(config)
                for value in [left,right]:
                    for key in ['confidence_gwas','trait_mode','trait_min_depth','trait_min_gq']:value.pop(key,None)
                if signature(left,'traits')!=signature(right,'traits'):continue
                if analytic_code(old_code.parent/'pipeline.py','evidence')!=analytic_code(Path(root)/'bin/pipeline.py','evidence'):continue
                # Extraction code is locked to known validated versions or this implementation.
                allowed=json.loads((Path(root)/'data/trait_extraction_compatible.json').read_text())
                old_digest=hashlib.sha256((old_code.parent/'traits_lpwgs.py').read_bytes()).hexdigest()
                current_digest=hashlib.sha256((Path(root)/'bin/traits_lpwgs.py').read_bytes()).hexdigest()
                if old_digest not in allowed and old_digest!=current_digest:continue
                names=['trait_gwas.bed',config['sample']+'.target_genotypes.vcf.gz']
                targets=sorted(work.glob('imputed_targets.*.vcf.gz'))
                if len(targets)!=len(options.get('--imputed-vcfs',[])):continue
                files=[work/n for n in names]+targets
                if any(not p.is_file() or p.stat().st_size==0 or p.stat().st_mtime_ns>end.stat().st_mtime_ns for p in files):continue
                begin=(work/'.command.begin').stat().st_mtime_ns
                if any(p.is_symlink() and p.is_file() and p.stat().st_mtime_ns>begin for p in work.iterdir()):continue
                return [str(p) for p in files]
            except (OSError,ValueError,KeyError):continue
    return []
