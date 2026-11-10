"""Automatic, auditable coarse relative CNA rescue after QDNAseq QC failure.

Primary segmentation and clinical-style classifiers keep their original scope.
Rescue provides explicitly selected coarse profiles and supported relative events.
"""
from __future__ import annotations
import csv
import html
import json
from pathlib import Path
from .runtime import OncoTracerError, atomic_write_json, atomic_write_text, require_file


def _read_bams(path):
    with path.open() as handle:
        rows=list(csv.DictReader(handle))
    result={}
    for row in rows:
        name=row.get('sample')
        if not name or name in result or not row.get('bam'):
            raise OncoTracerError('BAM manifest requires unique sample,bam columns')
        result[name]=Path(row['bam']).expanduser().resolve()
    return result


def publish_rescue(root, config, assessment, bam_manifest, outdir, runner, toolchain, *, force=False):
    if not assessment:
        return None
    enabled=config.get('ichorcna_fallback',True)
    if enabled not in (True,False):
        raise OncoTracerError('ichorcna_fallback must be true or false')
    directory=outdir/'08_ichorcna_fallback'
    directory.mkdir(parents=True,exist_ok=True)
    bams=_read_bams(bam_manifest) if bam_manifest.is_file() else {}
    rows=[]
    for row in assessment['samples']:
        sample=row['sample']
        rows.append(dict(sample=sample,bam=str(bams.get(sample,'')),primary_assessment=row['assessment'],
                         primary_reason=row.get('reason',''),sample_role=row.get('sample_role','unknown')))
    manifest=directory/'input_manifest.json';atomic_write_json(manifest,rows)
    failed=[r for r in rows if r['primary_assessment']!='assessable']
    if failed and enabled:
        try:
            if any(not row['bam'] for row in failed):
                raise OncoTracerError('Exact source BAM mapping is unavailable for a QDNAseq failure')
            rscript=Path(toolchain.executable('ichorcna','Rscript'))
            extdata=Path(str(config['ichorcna_fallback_assets'])) if config.get('ichorcna_fallback_assets') else rscript.resolve().parents[1]/'lib/R/library/ichorCNA/extdata'
            # In Conda Rscript may itself be a symlink into lib/R/bin.
            if not extdata.is_dir() and toolchain.ichorcna_prefix:
                extdata=toolchain.ichorcna_prefix/'lib/R/library/ichorCNA/extdata'
            threads=max(1,int(config.get('threads',4)))
            workers=min(4,threads,int(config.get('ichorcna_fallback_workers',1)))
            if workers<1:raise OncoTracerError('ichorcna_fallback_workers must be 1..4')
            command=toolchain.wrap('core',['python',root/'bin/scripts/native_ichor_fallback.py',
                     '--manifest',manifest,'--outdir',directory,'--assets',extdata,
                     '--samtools',toolchain.executable('core','samtools'),
                     '--read-counter',toolchain.executable('ichorcna','readCounter'),'--rscript',rscript,
                     '--threads',str(threads),'--workers',str(workers)])
            if force:command.append('--force')
            (directory/'summary.json').unlink(missing_ok=True)
            runner.run('ichorcna-fallback',command,containment=toolchain.environment('ichorcna'))
        except (OncoTracerError,OSError,ValueError) as error:
            # A functioning primary cohort remains available. The rescue failure
            # is explicit and cannot be interpreted as a negative CNA result.
            records=[dict(r,status='failed' if r in failed else 'primary_retained',
                          selected_method='none' if r in failed else 'qdnaseq',reason=str(error)) for r in rows]
            if not (directory/'summary.json').is_file():
                atomic_write_json(directory/'summary.json',dict(schema='oncotracer-ichor-rescue-v1',samples=records,error=str(error)))
    else:
        records=[dict(r,status='disabled' if r in failed else 'primary_retained',
                      selected_method='none' if r in failed else 'qdnaseq',
                      reason='Fallback explicitly disabled' if r in failed else 'Primary quality checks passed') for r in rows]
        atomic_write_json(directory/'summary.json',dict(schema='oncotracer-ichor-rescue-v1',samples=records))
    data=json.loads((directory/'summary.json').read_text())
    summary=outdir/'06_workflow_summary';summary.mkdir(parents=True,exist_ok=True)
    atomic_write_json(summary/'selected_cna_assessment.json',data)
    atomic_write_text(directory/'index.html','<!doctype html><meta charset="utf-8"><title>ichorCNA rescue</title>'+rescue_html(data))
    return data


def rescue_summary(data):
    if not data:return {}
    return dict(cna_selected_assessment='06_workflow_summary/selected_cna_assessment.json',
                cna_rescue_sample_statuses={r['sample']:r['status'] for r in data['samples']},
                cna_rescued_samples=[r['sample'] for r in data['samples'] if r['status']=='assessable_coarse_relative_profile'])


def rescue_html(data):
    if not data:return ''
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(row.get(k,'')))+'</td>' for k in
                ('sample','sample_role','selected_method','status','accepted_events','reason'))+'</tr>' for row in data['samples'])
    return ('<section><h2>Selected CNA method and ichorCNA rescue</h2>'
            '<p>QDNAseq quality failures are checked with unpaired ichorCNA at 500 kb and 1 Mb. '
            'Accepted results describe broad changes relative to the sample baseline. '
            'Resolution sensitivity reuses the same reads; it is not independent validation. '
            'Absolute ploidy, tumor fraction and genotype are not established by these rescue calls. '
            'The primary QDNAseq status and its classifier/GISTIC eligibility remain recorded separately.</p>'
            '<table><thead><tr><th>Sample</th><th>Role</th><th>Selected method</th><th>Status</th>'
            '<th>Supported regions</th><th>Reason</th></tr></thead><tbody>'+rows+'</tbody></table></section>')


def command_rescue(args):
    from .engine import Toolchain
    from .cna_quality import publish_assessment
    from .cna_options import resolve_policy
    from .runtime import CommandRunner, runtime_root
    from .output_safety import claim_output_run
    if args.threads < 1 or not 1 <= args.workers <= min(4,args.threads):
        raise OncoTracerError('Use 1..4 rescue workers within a positive thread budget')
    output=Path(args.outdir).resolve()
    root=runtime_root(Path(args.root) if args.root else None)
    config=dict(cna_profile=args.cna_profile,threads=args.threads,ichorcna_fallback_workers=args.workers)
    if args.assets:config['ichorcna_fallback_assets']=args.assets
    # An independent rescue writes only to its requested output; source inputs stay read-only.
    primary=require_file(Path(args.primary_status),'QDNAseq sample status')
    bams=require_file(Path(args.bam_manifest),'BAM manifest')
    with claim_output_run(output,config_path=primary,runtime_root_path=root):
        assessment=publish_assessment(output,primary,args.binsize,policy=resolve_policy(config))
        if not assessment:raise OncoTracerError('Primary sample assessment is empty')
        data=publish_rescue(root,config,assessment,bams,output,CommandRunner(output/'trace.tsv'),Toolchain.from_environment(),force=args.force)
    print('Native CNA rescue: '+str(output/'08_ichorcna_fallback/index.html'))
    return 2 if any(r['status']=='failed' for r in data['samples']) else 0


def add_rescue_command(subparsers):
    parser=subparsers.add_parser('rescue-cna',help='Reassess QDNAseq quality failures with native two-resolution ichorCNA')
    parser.add_argument('--primary-status',required=True)
    parser.add_argument('--bam-manifest',required=True,help='CSV with unique sample,bam,status columns')
    parser.add_argument('--outdir',required=True);parser.add_argument('--root')
    parser.add_argument('--assets',help='Read-only ichorCNA hg38 extdata directory')
    parser.add_argument('--cna-profile',choices=['standard','sensitive'],default='standard')
    parser.add_argument('--binsize',type=int,default=50);parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--workers',type=int,default=1);parser.add_argument('--force',action='store_true')
    parser.set_defaults(func=command_rescue)
