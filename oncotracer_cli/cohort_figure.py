"""Figure-1-style cohort CNA, depth and tissue-eligible methylation summaries."""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import shlex
import textwrap

from .runtime import atomic_write_json, atomic_write_text, OncoTracerError


def table(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fields, delimiter='\t', extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)


def read_table(path, delimiter='\t'):
    with Path(path).open(newline='') as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def windows_for(lengths):
    windows, offsets = [], {}
    for chrom, length in lengths.items():
        offsets[chrom] = len(windows)
        for start in range(0, length, 1000000):
            windows.append((chrom, start, min(start + 1000000, length)))
    return windows, offsets


def profile_from_bed(path, windows, offsets):
    import numpy as np
    values, weights = collections.defaultdict(list), collections.defaultdict(list)
    with Path(path).open() as handle:
        for line in handle:
            if not line.strip() or line.startswith(('track', 'browser', '#')): continue
            fields = line.split(); chrom = fields[0].removeprefix('chr')
            if chrom not in offsets: continue
            lo, hi, value = int(fields[1]), int(fields[2]), float(fields[4])
            if not math.isfinite(value) or hi <= lo: continue
            for position in range(lo // 1000000, (hi-1) // 1000000 + 1):
                index = offsets[chrom] + position
                if index >= len(windows) or windows[index][0] != chrom:
                    raise ValueError('CNA interval outside the reference')
                _, start, end = windows[index]
                overlap = max(0, min(hi, end) - max(lo, start))
                if overlap: values[index].append(value); weights[index].append(overlap)
    result = np.full(len(windows), np.nan)
    for index, vals in values.items():
        span = windows[index][2] - windows[index][1]
        if sum(weights[index]) > span: raise ValueError('Overlapping source bins would bias the profile')
        if sum(weights[index]) >= span / 2:
            order = np.argsort(vals, kind='stable'); w = np.asarray(weights[index])[order]
            result[index] = np.asarray(vals)[order][np.searchsorted(np.cumsum(w), w.sum()/2, side='left')]
    return result


def measure_depth(path, lengths):
    import pysam
    aligned = retained = 0
    with pysam.AlignmentFile(str(path), 'rb', threads=1) as bam:
        tids = {i: name.removeprefix('chr') for i, name in enumerate(bam.references)
                if name.removeprefix('chr') in lengths}
        if set(tids.values()) != set(lengths): raise ValueError('BAM does not contain the requested autosomes')
        if any(bam.lengths[i] != lengths[chrom] for i, chrom in tids.items()): raise ValueError('BAM reference lengths differ')
        for read in bam.fetch(until_eof=True):
            if read.reference_id not in tids or read.flag & 3844 or read.mapping_quality < 20: continue
            aligned += sum(n for op, n in (read.cigartuples or []) if op in (0, 7, 8)); retained += 1
    return dict(aligned_bases=aligned, retained_reads=retained, autosomal_reference_bp=sum(lengths.values()),
                mean_depth_x=aligned/sum(lengths.values()))


def broad_profile(row, windows, offsets):
    import numpy as np
    profile = np.full(len(windows), np.nan)
    regions = [r for r in row.get('regions', []) if r.get('status') != 'not_assessable' and 'log2_ratio' in r]
    # Use arms when available, avoiding double weighting a chromosome and its arms.
    arms = {r['region'].rstrip('pq') for r in regions if r['region'].endswith(('p','q'))}
    for r in regions:
        chrom = r['region'].rstrip('pq')
        if chrom not in offsets or (chrom in arms and not r['region'].endswith(('p','q'))): continue
        lo, hi = int(r['start'])-1, int(r['end'])
        for pos in range(lo//1000000, (hi-1)//1000000+1):
            i = offsets[chrom]+pos
            if i < len(windows) and windows[i][0] == chrom:
                profile[i] = float(r['log2_ratio'])
    return profile


def render(data, matrix, windows, destination, title, technology):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.5,'text.color':'black',
                         'axes.labelcolor':'black','xtick.color':'black','ytick.color':'black',
                         'axes.linewidth':1.2,'pdf.fonttype':42,'svg.fonttype':'none',
                         'svg.hashsalt':'oncotracer-cohort-4.1','savefig.facecolor':'white'})
    has_methylation = any(r.get('methylation_model') for r in data)
    widths = [1.6, 6.4, 1.25, .8, 1.5] + ([3.5, 1.15] if has_methylation else [])
    figure, axes = plt.subplots(1, len(widths), figsize=(sum(widths), max(3.7, len(data)*.40+1.8)),
                               gridspec_kw={'width_ratios':widths, 'wspace':.05})
    sample_ax, genome_ax, depth_ax, count_ax, method_ax = axes[:5]
    cmap = LinearSegmentedColormap.from_list('CNA', ['#2166ac','#f7f7f7','#b2182b']).with_extremes(bad='#d9d9d9')
    heatmap = genome_ax.imshow(matrix, cmap=cmap, vmin=-1, vmax=1, aspect='auto', interpolation='nearest')
    positions = np.arange(len(data))
    for ax in axes:
        ax.set_ylim(len(data)-.5,-.5); ax.set_yticks([])
        ax.spines[['top','left','right']].set_visible(False)
    for ax in [sample_ax, count_ax, method_ax, *axes[5:6]]:
        ax.set_xticks([]); ax.spines['bottom'].set_visible(False)
    sample_ax.set_xlim(0,1)
    for i, row in enumerate(data):
        sample_ax.text(.98, i, row['sample'], ha='right', va='center', fontsize=10, color='black')
        count_ax.text(.5, i, str(row['accepted_events']) if row['accepted_events'] is not None else '—',
                      ha='center', va='center', fontweight='bold', color='black')
        method_ax.text(0, i, row['method_label'], va='center', fontsize=9.4, color='black')
    chroms = list(dict.fromkeys(w[0] for w in windows)); ticks=[]
    for chrom in chroms:
        indexes=[i for i,w in enumerate(windows) if w[0]==chrom]; ticks.append((indexes[0]+indexes[-1])/2)
        if indexes[0]: genome_ax.axvline(indexes[0]-.5, color='black', lw=.35)
    genome_ax.set_xticks(ticks,chroms,fontsize=9); genome_ax.set_xlabel('Chromosome (GRCh38)',fontsize=11)
    genome_ax.tick_params(axis='x',length=4,width=1.2); genome_ax.set_yticks([])
    depths=np.array([r['mean_depth_x'] for r in data]); color={'FFPE':'#2878B5','ONT':'#2E8B57','Fresh Illumina':'#E68632'}.get(technology,'#2878B5')
    depth_ax.barh(positions, depths, color=color, height=.68)
    depth_ax.set_xlim(0,max(.02,float(depths.max())*1.5)); depth_ax.set_xlabel('Depth (×)',fontsize=11)
    depth_ax.locator_params(axis='x',nbins=3); depth_ax.tick_params(axis='x',labelsize=9,length=4,width=1.2)
    for i,value in enumerate(depths):
        depth_ax.annotate('<0.01' if 0<value<.005 else f'{value:.2f}', (value,i), xytext=(3,0),
                          textcoords='offset points', va='center', fontsize=9.5,fontweight='bold',color='black')
    titles=['Sample','Genome-wide CNA','Coverage','Events','CNA method']
    if has_methylation:
        class_ax, score_ax=axes[5:]; class_ax.set_xlim(0,1); score_ax.set_xlim(0,1.28)
        score_ax.set_xticks([0,.5,1]); score_ax.set_xlabel('Model score',fontsize=11)
        for i,row in enumerate(data):
            label=row.get('methylation_class') or row.get('methylation_status','Not assayed')
            if row.get('methylation_model'): label=row['methylation_model'].upper()+': '+label
            class_ax.text(0,i,textwrap.fill(label,42),va='center',fontsize=8.7,color='black')
            score=row.get('methylation_score')
            if score is not None:
                score_ax.barh(i,score,height=.68,color='#5A4C9C' if row['methylation_model']=='sturgeon' else '#007F82')
                score_ax.text(score+.025,i,f'{score:.3f}',va='center',fontsize=9,fontweight='bold',color='black')
        titles+=['Methylation class','Score']
    for ax,label in zip(axes,titles):ax.set_title(label,fontsize=11,fontweight='bold',pad=12,color='black')
    figure.suptitle(title,fontsize=14,fontweight='bold',x=.03,ha='left',y=.985)
    figure.subplots_adjust(left=.025,right=.99,bottom=.14,top=.88)
    cax=figure.add_axes([.24,.045,.24,.016]);cb=figure.colorbar(heatmap,cax=cax,orientation='horizontal',ticks=[-1,0,1]);cb.set_label('CNA log₂ ratio',fontsize=10);cb.ax.tick_params(labelsize=9)
    for extension in ('pdf','svg','png'):
        figure.savefig(destination/('04_cohort_figure.'+extension),dpi=300,bbox_inches='tight')
    plt.close(figure)


def build(spec_path):
    import numpy as np
    spec=json.loads(Path(spec_path).read_text());root=Path(spec['outdir']);dest=root/'09_cohort_figure'
    names=spec['samples']
    if len(names)<2:return {'status':'not_applicable','reason':'A cohort figure requires more than one sample'}
    dest.mkdir(exist_ok=True)
    with Path(spec['reference_fai']).open() as handle:
        raw={f[0].removeprefix('chr'):int(f[1]) for line in handle if (f:=line.split())}
    lengths={str(c):raw[str(c)] for c in range(1,23) if str(c) in raw}
    if len(lengths)!=22:raise ValueError('Cohort figure requires all 22 GRCh38 autosomes')
    windows,offsets=windows_for(lengths)
    primary={r['sample']:r for r in read_table(root/'06_workflow_summary/cna_assessment.tsv')}
    event_path=root/'03_cna_codification/cna_events.tsv';events=read_table(event_path) if event_path.is_file() else []
    bed_path=root/'03_cna_codification/input_cna_files.tsv';beds={r['sample']:r['input_file'] for r in read_table(bed_path)} if bed_path.is_file() else {}
    rescue_path=root/'06_workflow_summary/selected_cna_assessment.json'
    rescue={r['sample']:r for r in json.loads(rescue_path.read_text()).get('samples',[])} if rescue_path.is_file() else {}
    broad_path=root/'06_workflow_summary/experimental_broad_cna/screen.json'
    broad={r['sample']:r for r in json.loads(broad_path.read_text()).get('samples',[])} if broad_path.is_file() else {}
    methylation_path=root/'07_methylation/methylation_status.json'
    methylation=json.loads(methylation_path.read_text()) if methylation_path.is_file() else {}
    methylation_rows={r['sample']:r for r in methylation.get('samples',[])}
    bam_rows=read_table(spec['bam_samplesheet'],',');bams={r['sample']:Path(r['bam']) for r in bam_rows}
    rows,matrix,exported=[],[],[]
    for name in names:
        a=primary.get(name,{});r=rescue.get(name,{});b=broad.get(name,{})
        p=np.full(len(windows),np.nan);selected=[];count=None;method='Not assessable';evidence='none'
        if a.get('assessment')=='assessable':
            method=f"{a.get('bin_size_kb','50')} kb";evidence='primary';selected=[e for e in events if e['sample']==name];count=len(selected)
            if name in beds:p=profile_from_bed(beds[name],windows,offsets)
        elif r.get('status')=='assessable_coarse_relative_profile':
            method='ichorCNA 500 kb';evidence='coarse_relative';selected=read_table(r['events_tsv']);count=len(selected)
            p=profile_from_bed(r['profile_bed'],windows,offsets)
        elif b.get('status')=='experimental_screen_available':
            method='Chr/arm screen';evidence='exploratory';p=broad_profile(b,windows,offsets)
            selected=[dict(sample=name,state=x['status'].removeprefix('candidate_broad_'),chrom=x['region'].rstrip('pq'),
                           start=x['start'],end=x['end'],mean_log2=x['log2_ratio'],region=x['region'])
                      for x in b.get('regions',[]) if x.get('status','').startswith('candidate_broad_')]
        for event in selected:
            exported.append({**event,'sample':name,'evidence':evidence,'assignment':event.get('region') or
                             'chr'+str(event['chrom']).removeprefix('chr')+':'+str(event['start'])+'-'+str(event['end'])})
        if name not in bams:raise ValueError('Missing exact coverage BAM for '+name)
        depth=measure_depth(bams[name],lengths)
        row=dict(sample=name,primary_assessment=a.get('assessment','not_assessable'),selected_status=r.get('status',''),
                 method_label=method,evidence=evidence,accepted_events=count,candidate_regions=len(selected) if evidence=='exploratory' else 0,
                 assignments='; '.join(e.get('region','chr'+str(e['chrom']).removeprefix('chr')+':'+str(e['start'])+'-'+str(e['end']))+' '+e['state'] for e in selected),
                 **depth,methylation_model='',methylation_class='',methylation_score=None,methylation_status='Not assayed')
        m=methylation_rows.get(name)
        if m:
            row.update(methylation_model=m.get('classifier') or methylation.get('classifier',''),methylation_status=m['status'])
            if m['status']=='complete' and m.get('classification'):
                path=root/m['classification'];values=read_table(path,',' if path.suffix=='.csv' else '\t')[0]
                scores={k:float(v) for k,v in values.items() if k not in {'sample','time','cov_cpgs','number_probes'}}
                top=max(scores,key=scores.get);row.update(methylation_class=top,methylation_score=scores[top])
        rows.append(row);matrix.append(p)
    table(dest/'02_samples.tsv',rows,list(rows[0]))
    table(dest/'03_events.tsv',exported,['sample','evidence','assignment','state','chrom','start','end','mean_log2'])
    with gzip.open(dest/'01_profiles.tsv.gz','wt') as handle:
        writer=csv.writer(handle,delimiter='\t');writer.writerow(['sample']+[f'chr{c}:{lo+1}-{hi}' for c,lo,hi in windows])
        for name,profile in zip(names,matrix):writer.writerow([name]+['NA' if not np.isfinite(v) else format(v,'.8g') for v in profile])
    render(rows,np.asarray(matrix),windows,dest,spec['title'],spec['technology'])
    legend=('Genome-wide CNA profiles, measured autosomal coverage, event counts and applicable methylation classifications for every selected sequencing library. '
            'Each row represents one library. Profiles use 1 Mb display windows, overlap-weighted medians and at least 50% finite coverage. '
            'Blue/red indicate negative/positive log2 ratios; grey indicates missing data. Primary accepted events retain their original calling thresholds. '
            'QDNAseq QC failures undergo ichorCNA at 500 kb and 1 Mb. Supported coarse relative regions remain distinct from primary events and do not establish absolute ploidy or tumour fraction. '
            'Chr/arm screen identifies exploratory chromosome/arm profiles; candidate gain/loss assignments are exported separately and never promoted to accepted events. '
            'A dash denotes no assessable accepted-event count; zero denotes an assessable profile with no accepted events. '
            'Coverage is primary MAPQ>=20 aligned CIGAR M/= /X bases divided by complete autosomal lengths; unmapped, secondary, supplementary, QC-failed and duplicate reads are excluded. '
            'MARLIN is assigned to explicitly recorded peripheral blood/marrow; Sturgeon to CNS. Other or unknown tissues are unassigned. '
            'Corrected reference-CpG inputs are derived from original modified-base BAMs. Native model scores do not establish diagnoses; Sturgeon scores below 0.8 remain inconclusive. '
            'No cross-platform pooling, cohort VCF merge or pathology inference is performed by this figure.\n')
    atomic_write_text(dest/'05_figure_legend.txt',legend)
    report={'status':'complete','sample_count':len(names),'samples':names,'title':spec['title'],
            'primary_assessable':sum(r['evidence']=='primary' for r in rows),
            'coarse_relative':sum(r['evidence']=='coarse_relative' for r in rows),
            'exploratory':sum(r['evidence']=='exploratory' for r in rows),
            'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file() and p.name!='06_manifest.json'}}
    atomic_write_json(dest/'06_manifest.json',report)
    return report


def publish(root, config, samples, reference, outdir, runner, toolchain):
    enabled=config.get('cohort_figure',True)
    if type(enabled) is not bool:raise OncoTracerError('cohort_figure must be true or false')
    if not enabled or len(samples)<2:return None
    mode=config['mode'];technology='ONT' if mode=='ont' else 'FFPE' if str(config.get('variant_specimen_type','')).lower()=='ffpe' else 'Fresh Illumina'
    directory=outdir/'09_cohort_figure';directory.mkdir(exist_ok=True)
    spec={'outdir':str(outdir),'samples':[s.sample for s in samples],'reference_fai':str(reference['fai']),
          'bam_samplesheet':str(outdir/('01_samurai_ont' if mode=='ont' else '01_samurai_illumina')/'input/native.bam.samplesheet.csv'),
          'technology':technology,'title':str(config.get('cohort_title') or technology+' cohort')}
    path=directory/'00_inputs.json';atomic_write_json(path,spec)
    from .source_runtime import python_module
    argv=toolchain.wrap('core',['python',*python_module('oncotracer_cli.cohort_figure')[1:],'--spec',path])
    runner.run('cohort-figure',argv,containment=toolchain.environment('core'))
    if not (directory/'06_manifest.json').is_file():raise OncoTracerError('Cohort figure did not produce its completion manifest')
    return json.loads((directory/'06_manifest.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--spec',required=True)
    print(json.dumps(build(parser.parse_args().spec),indent=2))
