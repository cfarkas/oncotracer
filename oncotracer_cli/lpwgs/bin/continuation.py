"""Verified reuse of finished core stages across a trait-only workflow upgrade."""
import ast
import csv
import hashlib
import json
from pathlib import Path

CORE_KEYS = ['versions','sample','inputs','reference','bwa_index','tools','annotation','clinvar',
             'pon','germline_resource','contamination_sites','regions','min_depth',
             'min_gq','min_mapping_quality','min_base_quality','test_reads',
             'test_mode','single_end','library','no_ancestry','ancestry_min_sites']
EXCLUDED = {'traits','read_gwas','report','main'}

def core_digest(path):
    tree = ast.parse(Path(path).read_text())
    tree.body = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) or n.name not in EXCLUDED]
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()

def signature(config):
    data = {k: config.get(k) for k in CORE_KEYS}
    skip = {config.get('gwas_catalog'), config.get('traits_panel'),config.get('pgs_manifest'),config.get('explanation_evidence'),config.get('ancestry_explorer_manifest')}
    data['resources'] = [r for r in config.get('resources',[]) if r['path'] not in skip]
    data['core_code'] = config.get('core_pipeline_sha256')
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

def files_for(stage, sample=None, lane=None, kind=None):
    return {
        'align': [f'{lane}.bam'],
        'merge': [f'{sample}.bam',f'{sample}.bam.bai',f'{sample}.qc.json'],
        'germline': [f'{sample}.germline.vcf.gz',f'{sample}.germline.vcf.gz.tbi'],
        'normal_mutect': [f'{sample}.pon.unfiltered.vcf.gz',f'{sample}.pon.unfiltered.vcf.gz.tbi'],
        'create_pon': ['somatic_pon.hg38.vcf.gz','somatic_pon.hg38.vcf.gz.tbi','somatic_pon.provenance.json'],
        'somatic': [f'{sample}.somatic.vcf.gz',f'{sample}.somatic.vcf.gz.tbi',f'{sample}.somatic.json'],
        'prepare_clinvar': ['clinvar.norm.vcf.gz','clinvar.norm.vcf.gz.tbi'],
        'annotate': [f'{sample}.{kind}.annotation.json',f'{sample}.{kind}.pathogenic_candidates.tsv'],
        'ancestry': [f'{sample}.ancestry.json'],
    }.get(stage, [])

def key(stage, sample=None, lane=None, kind=None):
    return ':'.join(str(x) for x in [stage,sample,lane,kind] if x)

def metadata(path):
    p = Path(path).absolute(); st = p.stat()
    return {'path':str(p),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns}

def record(args, config):
    files = files_for(args.stage,args.sample,args.lane,args.kind)
    if not files: return
    receipt = {'signature':signature(config),'key':key(args.stage,args.sample,args.lane,args.kind),
               'files':[metadata(f) for f in files]}
    Path('.resume.json').write_text(json.dumps(receipt,indent=2)+'\n')

def valid_files(work, names):
    return ((work/'.exitcode').is_file() and (work/'.exitcode').read_text().strip() == '0'
            and all((work/n).is_file() and (work/n).stat().st_size > 0 for n in names))

def active_work(output):
    root = (Path(output)/'work').resolve()
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            cwd = (proc/'cwd').resolve(); cmd = (proc/'cmdline').read_bytes()
            if cwd.is_relative_to(root) and b'.command.run' in cmd: return int(proc.name)
        except (OSError,RuntimeError): pass
    return None

def collect(config, previous, root, output, work_dir=None):
    work_root = Path(work_dir or Path(output)/'work')
    result = {}; sig = signature(config)
    for f in sorted(work_root.glob('*/*/.resume.json'), key=lambda x:x.stat().st_mtime):
        try:
            r=json.loads(f.read_text())
            if r['signature'] != sig: continue
            names=[Path(x['path']).name for x in r['files']]
            if not valid_files(f.parent,names): continue
            if any(metadata(x['path']) != x for x in r['files']): continue
            result[r['key']]=[x['path'] for x in r['files']]
        except (OSError,ValueError,KeyError): continue
    legacy_path=Path(output)/'pipeline_info/continuation_legacy.json'
    known=json.loads((Path(root)/'data/resume_compatible.json').read_text())
    if not legacy_path.exists() and previous and previous.get('pipeline_source_sha256') in known:
        trace=Path(output)/'pipeline_info/trace.tsv'
        if trace.exists():
            old=dict(previous); old['core_pipeline_sha256']=known[previous['pipeline_source_sha256']]
            legacy_path.write_text(json.dumps({'runtime':old,'trace':list(csv.DictReader(trace.open(),delimiter='\t'))},indent=2)+'\n')
    if legacy_path.exists():
        legacy=json.loads(legacy_path.read_text())
        if signature(legacy['runtime']) == sig:
            for row in legacy['trace']:
                if row['status'] not in {'COMPLETED','CACHED'} or row['exit'] != '0': continue
                title=row['name']; stage=title.split(' ')[0].lower().replace('merge_markdup','merge'); sample=lane=kind=None
                if '(' in title:
                    parts=title.split('(',1)[1].rstrip(')').split(':'); sample=parts[0]
                    if stage=='align': lane=parts[1]
                    if stage=='annotate': kind=parts[1]
                names=files_for(stage,sample,lane,kind)
                if not names: continue
                found=list(work_root.glob(row['hash']+'*'))
                if len(found)!=1 or not valid_files(found[0],names): continue
                result.setdefault(key(stage,sample,lane,kind),[str(found[0]/n) for n in names])
    return result
