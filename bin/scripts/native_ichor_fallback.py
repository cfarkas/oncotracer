#!/usr/bin/env python3
"""Native two-resolution ichorCNA rescue. Raw HMM calls never bypass evidence QC.

Runs in the native core environment (NumPy), with separately pinned ichorCNA R.
No pathology-specific rules, no unmatched normal panel, no absolute-ploidy calls.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, fcntl, hashlib, json, math, os, re, subprocess
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
CHRS = ['chr'+str(i) for i in range(1,23)]
INPUT_POLICY = dict(minimum_usable_autosomal_reads=5000, minimum_median_reads_per_valid_bin=5,
                    maximum_zero_fraction=.5, mapq=20, excluded_flags=3844, minimum_mappability=.75,
                    resolutions_kb=[500,1000], normal_panel=None)

def read_table(path):
    with Path(path).open() as handle: return list(csv.DictReader(handle,delimiter='\t'))

def table(path, rows, fields):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t');writer.writeheader();writer.writerows(rows)

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def identity(path):
    path=Path(path).resolve();s=path.stat()
    return dict(path=str(path),size=s.st_size,mtime_ns=s.st_mtime_ns)

def dump(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)

def wig(path):
    chrom=None
    for line in Path(path).open():
        if line.startswith('fixedStep'):
            fields=dict(x.split('=',1) for x in line.split()[1:]);chrom=fields['chrom'];start=int(fields['start']);step=int(fields['step'])
        elif chrom and line.strip() and not line.startswith(('track','#')):
            yield chrom,start,float(line.strip());start+=step

POLICY = {
    'version': 'ichor-two-resolution-relative-v1',
    'maximum_adjacent_log2_difference_median': .35,
    'minimum_finite_bin_fraction': .90,
    'minimum_region_bp': 10000000,
    'minimum_region_finite_bins': 20,
    'minimum_region_coverage_fraction': .8,
    'minimum_abs_log2': .2,
    'block_size_bins': 5,
    'bootstrap_iterations': 2000,
    'bootstrap_seed': 2026,
    'minimum_blocks': 4,
    'interval_percent': 95,
    'second_resolution_bp': 1000000,
    'second_resolution_required_for_figure_fallback': True,
    'second_resolution_minimum_coverage_fraction': .8,
    'second_resolution_minimum_abs_log2': .2,
    'second_resolution_minimum_finite_bins': 8,
    'merge_bookended_same_direction_regions': True,
    'interpretation': 'Exploratory broad relative-to-sample-baseline CNA support. The 1-Mb check reuses the same reads; it is technical resolution sensitivity, not independent validation. No absolute ploidy or diagnostic accuracy claim. Same rules for tumor and control inputs.'
}
FIELDS = ['library_id','sample','state','chrom','start','end','mean_log2','size_mb','native_segments','second_resolution_checked']

def support(values, expected_bins, start, end):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    out = dict(finite_bins=n, covered_fraction=n/max(1,expected_bins),
               mean_log2=float(np.median(values)) if n else None,
               ci_low=None, ci_high=None, odd_median=None, even_median=None,
               retained=False, reason='')
    if end-start+1 < POLICY['minimum_region_bp'] or n < POLICY['minimum_region_finite_bins']:
        out['reason'] = 'too_short_or_few_bins'; return out
    if out['covered_fraction'] < POLICY['minimum_region_coverage_fraction']:
        out['reason'] = 'insufficient_genomic_coverage'; return out
    if abs(out['mean_log2']) < POLICY['minimum_abs_log2']:
        out['reason'] = 'below_relative_log2_threshold'; return out
    size = POLICY['block_size_bins']
    blocks = np.array([np.median(values[i:i+size]) for i in range(0,n-size+1,size)])
    if len(blocks) < POLICY['minimum_blocks']:
        out['reason'] = 'too_few_blocks'; return out
    rng = np.random.default_rng(POLICY['bootstrap_seed'])
    boot = np.median(rng.choice(blocks,(POLICY['bootstrap_iterations'],len(blocks))),axis=1)
    lo, hi = np.quantile(boot,[.025,.975])
    out.update(ci_low=float(lo),ci_high=float(hi),odd_median=float(np.median(values[::2])),even_median=float(np.median(values[1::2])))
    sign = np.sign(out['mean_log2'])
    if lo <= 0 <= hi or np.sign(out['odd_median']) != sign or np.sign(out['even_median']) != sign:
        out['reason'] = 'unstable_between_bins_or_blocks'; return out
    out.update(retained=True,reason='consistent_broad_relative_depth_change')
    return out

def read_depth(path):
    rows = read_table(path)
    bychr = collections.defaultdict(list)
    profile = []
    for r in rows:
        try: value = float(r['log2_TNratio_corrected'])
        except ValueError: value = float('nan')
        chrom, start, end = r['chr'], int(r['start']), int(r['end'])
        bychr[chrom].append((start,end,value))
        if math.isfinite(value): profile.append((chrom,start-1,end,value))
    for values in bychr.values():
        values.sort()
        if len({(a,b) for a,b,v in values}) != len(values): raise ValueError('Duplicate normalized depth bins')
    delta = [abs(b[2]-a[2]) for values in bychr.values() for a,b in zip(values,values[1:])
             if b[0] == a[1]+1 and math.isfinite(a[2]) and math.isfinite(b[2])]
    finite = len(profile)/max(1,len(rows))
    noise = float(np.median(delta)) if delta else None
    passed = finite >= POLICY['minimum_finite_bin_fraction'] and noise is not None and math.isfinite(noise) and noise <= POLICY['maximum_adjacent_log2_difference_median']
    return bychr, profile, dict(finite_bin_fraction=finite,adjacent_log2_difference_median=noise,passed=passed)

def sensitivity(values, start, end, sign):
    # Use complete 1-Mb bins wholly inside the original 500-kb region; a boundary
    # shift cannot let one large bin dominate a short segment.
    rows = [(a,b,v) for a,b,v in values if start <= a and b <= end and math.isfinite(v)]
    array = np.array([v for a,b,v in rows]); covered = sum(b-a+1 for a,b,v in rows)/(end-start+1)
    median = float(np.median(array)) if len(array) else None
    odds = float(np.median(array[::2])) if len(array) else None
    evens = float(np.median(array[1::2])) if len(array)>1 else None
    passed = bool(len(array)>=POLICY['second_resolution_minimum_finite_bins'] and covered>=POLICY['second_resolution_minimum_coverage_fraction']
                  and abs(median)>=POLICY['second_resolution_minimum_abs_log2'] and np.sign(median)==sign
                  and np.sign(odds)==sign and np.sign(evens)==sign)
    return dict(second_finite_bins=len(array),second_covered_fraction=covered,second_median_log2=median,second_odd_median=odds,second_even_median=evens,second_passed=passed)

def merge_events(events, bychr):
    merged = []
    for e in sorted(events,key=lambda v:(int(v['chrom']),int(v['start']))):
        e = dict(e,native_segments=1)
        if merged and e['chrom']==merged[-1]['chrom'] and e['state']==merged[-1]['state'] and int(e['start'])==int(merged[-1]['end'])+1:
            merged[-1]['end']=e['end']; merged[-1]['native_segments']+=1
        else: merged.append(e)
    for e in merged:
        values = [v for a,b,v in bychr['chr'+e['chrom']] if e['start']<=a and b<=e['end'] and math.isfinite(v)]
        e.update(mean_log2=float(np.median(values)),size_mb=(e['end']-e['start']+1)/1e6)
    return merged

def evaluate(sample, directory):
    first=directory/'500kb/fit';second=directory/'1000kb/fit'
    bychr,profile,metrics=read_depth(first/(sample+'.correctedDepth.txt'))
    other,_,second_metrics=read_depth(second/(sample+'.correctedDepth.txt'))
    quality=dict(profile_QC=metrics,second_resolution_QC=second_metrics)
    if not metrics['passed'] or not second_metrics['passed']:
        return dict(status='not_assessable',reason='Normalized-depth noise or missingness fails at one or both resolutions',**quality)
    events=[];decisions=[]
    for seg in read_table(first/(sample+'.seg.txt')):
        chrom,lo,hi=seg['chrom'],int(seg['start']),int(seg['end'])
        values=[v for a,b,v in bychr[chrom] if lo<=a and b<=hi]
        evidence=support(values,math.ceil((hi-lo+1)/500000),lo,hi)
        extra=dict(second_finite_bins=None,second_covered_fraction=None,second_median_log2=None,second_odd_median=None,second_even_median=None,second_passed=False)
        if evidence['retained']:
            extra=sensitivity(other[chrom],lo,hi,np.sign(evidence['mean_log2']))
            if not extra['second_passed']:evidence.update(retained=False,reason='not_stable_at_1Mb_resolution')
        decisions.append(dict(chrom=chrom,start=lo,end=hi,native_copy_number=seg['copy.number'],native_call=seg['call'],**evidence,**extra))
        if evidence['retained']:
            events.append(dict(library_id=sample,sample=sample,state='gain' if evidence['mean_log2']>0 else 'loss',chrom=chrom.removeprefix('chr'),start=lo,end=hi,mean_log2=evidence['mean_log2'],size_mb=(hi-lo+1)/1e6,second_resolution_checked=True))
    events=merge_events(events,bychr)
    bed=directory/'profile.bed';bed.write_text(''.join(f'{c}\t{a}\t{b}\t{sample}\t{v}\n' for c,a,b,v in profile))
    ev=directory/'supported_relative_events.tsv';table(ev,events,FIELDS)
    if decisions:table(directory/'segment_support.tsv',decisions,list(decisions[0]))
    return dict(status='assessable_coarse_relative_profile',reason='500-kb profile and 1-Mb sensitivity pass; broad relative events only',
                accepted_events=len(events),profile_bed=str(bed),events_tsv=str(ev),**quality)


def verified_receipt(path, fingerprint):
    try:
        receipt=json.loads(path.read_text())
        return receipt if receipt['fingerprint']==fingerprint and receipt['result']['status']!='failed' and all(
            (path.parent/rel).is_file() and sha(path.parent/rel)==digest for rel,digest in receipt['outputs'].items()
        ) else None
    except (OSError,ValueError,KeyError,TypeError):return None


def validate_manifest(rows):
    names=set()
    for row in rows:
        sample=row.get('sample','')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',sample) or sample in names:raise ValueError('Invalid or duplicate sample ID')
        names.add(sample)
        if row.get('primary_assessment') not in {'assessable','not_assessable'}:raise ValueError('Explicit primary assessment required')
        if row['primary_assessment']=='not_assessable' and not row.get('bam'):raise ValueError('Fallback requires the exact BAM for '+sample)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--outdir',required=True,type=Path)
    parser.add_argument('--assets',required=True,type=Path,help='Installed ichorCNA extdata (hg38 500/1000-kb)')
    parser.add_argument('--samtools',required=True);parser.add_argument('--read-counter',required=True);parser.add_argument('--rscript',required=True)
    parser.add_argument('--threads',type=int,default=4);parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--force',action='store_true');parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if not 1<=args.workers<=4 or args.threads<args.workers:parser.error('Use 1..4 workers within the total thread budget')
    manifest=json.loads(args.manifest.read_text());validate_manifest(manifest)
    args.outdir.mkdir(parents=True,exist_ok=True)
    with (args.outdir/'.run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return execute(args,manifest)


def execute(args, manifest):
    spec=json.loads((HERE/'ichor_fallback_assets.json').read_text());assets={}
    required=[r for r in manifest if r['primary_assessment']=='not_assessable']
    if required:
        for kb,records in spec.items():
            assets[kb]={}
            for key,item in records.items():
                path=args.assets/item['filename']
                if not path.is_file() or sha(path)!=item['sha256']:raise ValueError('Missing or changed pinned ichorCNA asset: '+str(path))
                assets[kb][key]=path
    tools={name:dict(path=str(Path(value).resolve()),sha256=sha(value)) for name,value in
           [('samtools',args.samtools),('readCounter',args.read_counter),('Rscript',args.rscript)]} if required else {}
    scripts={p.name:sha(p) for p in [Path(__file__),HERE/'native_ichorcna.R',HERE/'ichorcna_plot_compat.R',HERE/'ichor_fallback_assets.json']}
    cores=max(1,args.threads//args.workers)
    env=dict(os.environ,OMP_NUM_THREADS=str(cores),OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    def process(row):
        sample=row['sample']
        base=dict(sample=sample,sample_role=row.get('sample_role','unknown'),primary_assessment=row['primary_assessment'],primary_reason=row.get('primary_reason',''))
        if row['primary_assessment']=='assessable':
            return dict(base,status='primary_retained',selected_method='qdnaseq',reason='Primary quality checks passed; zero accepted events do not trigger fallback')
        directory=args.outdir/sample;directory.mkdir(exist_ok=True)
        bam=Path(row['bam']).resolve();fp=dict(bam=identity(bam),input_policy=INPUT_POLICY,output_policy=POLICY,assets=spec,tools=tools,scripts=scripts)
        receipt_path=directory/'receipt.json'
        cached=verified_receipt(receipt_path,fp)
        if cached and not args.force:
            print(sample,'verified native ichorCNA reused',flush=True);return dict(base,**cached['result'])
        if args.verify_only:raise ValueError('Verified native ichorCNA output unavailable: '+sample)
        # A failed recomputation cannot expose a prior accepted event table.
        for name in ['profile.bed','supported_relative_events.tsv','segment_support.tsv','receipt.json']:(directory/name).unlink(missing_ok=True)
        result=dict(status='failed',selected_method='none',reason='Incomplete ichorCNA execution')
        commands=[]
        def run(command, log, stdout=None):
            command=[str(x) for x in command];commands.append(command)
            with (directory/log).open('w') as handle:
                handle.write('COMMAND '+json.dumps(command)+'\n');handle.flush()
                if stdout:
                    with Path(stdout).open('w') as output:subprocess.run(command,stdout=output,stderr=handle,check=True,env=env)
                else:subprocess.run(command,stdout=handle,stderr=subprocess.STDOUT,check=True,env=env)
        try:
            # Count from indexed alignments; exclude unmapped, secondary, QC-fail,
            # duplicate and supplementary reads identically in tumors and controls.
            count=directory/'usable_reads.txt'
            run([args.samtools,'view','-c','-q','20','-F','3844',bam,*CHRS],'count.log',count)
            usable=int(count.read_text().strip());result['usable_reads']=usable
            if usable<5000:
                result.update(status='not_assessable',reason='Fewer than 5000 usable autosomal reads; no HMM fit attempted')
            else:
                filtered=directory/'primary_mapq20.bam'
                run([args.samtools,'view','-@',str(max(1,cores-1)),'-b','-q','20','-F','3844','-o',filtered,bam,*CHRS],'filter.log')
                run([args.samtools,'index','-@',str(max(1,cores-1)),filtered],'index.log')
                input_metrics={};failed=False
                for kb in ['500','1000']:
                    dest=directory/(kb+'kb');dest.mkdir(exist_ok=True);w=dest/(sample+'.wig');asset=assets[kb]
                    run([args.read_counter,'--window',str(int(kb)*1000),'--quality','20','--chromosome',','.join(CHRS),filtered],kb+'kb/readCounter.log',w)
                    gc={(c,a):v for c,a,v in wig(asset['gc'])};maps={(c,a):v for c,a,v in wig(asset['map'])}
                    values=np.array([v for c,a,v in wig(w) if gc.get((c,a),-1)>=0 and maps.get((c,a),-1)>=.75])
                    if len(values)<1000 or not np.isfinite(values).all():raise ValueError('Malformed or insufficient readCounter bins')
                    metric=dict(valid_bins=len(values),median_reads_per_valid_bin=float(np.median(values)),zero_bin_fraction=float(np.mean(values==0)))
                    input_metrics[kb]=metric
                    if metric['median_reads_per_valid_bin']<5 or metric['zero_bin_fraction']>.5:
                        result.update(status='not_assessable',reason='Insufficient read support at '+kb+' kb');failed=True;break
                    fit=dest/'fit';fit.mkdir(exist_ok=True)
                    # Remove native outputs before fitting, so missing new outputs cannot reuse old fits.
                    for prior in fit.glob(sample+'.*'):
                        if prior.is_file():prior.unlink()
                    print(sample,kb+'kb','fitting native ichorCNA',flush=True)
                    run([args.rscript,HERE/'native_ichorcna.R','--wig',w,'--sample',sample,'--outdir',fit,
                         '--gc-wig',asset['gc'],'--map-wig',asset['map'],'--centromere',asset['centromere'],
                         '--reptime',asset['reptime'],'--cores',str(cores)],kb+'kb/ichorCNA.log')
                    for suffix in ['.RData','.seg.txt','.cna.seg','.correctedDepth.txt','.params.txt']:
                        f=fit/(sample+suffix)
                        if not f.is_file() or not f.stat().st_size:raise ValueError('Missing native ichorCNA output: '+str(f))
                result['input_QC']=input_metrics
                if not failed:result.update(evaluate(sample,directory))
                if result['status']=='assessable_coarse_relative_profile':result['selected_method']='ichorcna_coarse_relative'
            if identity(bam)!=fp['bam']:raise ValueError('Source BAM changed during ichorCNA analysis')
        except (OSError,ValueError,subprocess.CalledProcessError) as error:
            result.update(status='failed',selected_method='none',reason=str(error))
        outputs={p.relative_to(directory).as_posix():sha(p) for p in directory.rglob('*') if p.is_file() and p.name!='receipt.json' and not p.name.endswith(('.bam','.bai','.log'))}
        dump(receipt_path,dict(fingerprint=fp,result=result,commands=commands,outputs=outputs))
        print(sample,result['status'],result.get('accepted_events',''),flush=True)
        return dict(base,**result)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:results=list(pool.map(process,manifest))
    summary=dict(schema='oncotracer-ichor-rescue-v1',input_policy=INPUT_POLICY,output_policy=POLICY,samples=results)
    dump(args.outdir/'summary.json',summary)
    fields=['sample','sample_role','primary_assessment','primary_reason','selected_method','status','reason','accepted_events','profile_bed','events_tsv']
    table(args.outdir/'summary.tsv',[{k:r.get(k,'') for k in fields} for r in results],fields)
    return 2 if any(r['status']=='failed' for r in results) else 0

if __name__=='__main__':raise SystemExit(main())
