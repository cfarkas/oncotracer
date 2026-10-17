#!/usr/bin/env python3
"""Panel-SNP homozygosity and exploratory ROH from complete imputed genotypes."""
import argparse,itertools,json,math,subprocess
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pysam
from extension_common import dump,table,Excel,runtime_signature
from genome_report_ui import write,section,cards,esc,table as html_table

VERSION='imputed-roh-v1'
METHOD={'min_gp':0.99,'min_info':0.8,'min_panel_maf':0.05,'marker_spacing_bp':10000,'max_gap_bp':250000,'min_roh_bp':1000000,'min_roh_markers':100,'genotype_error_phred':20,'recombination_rate_per_bp':1e-8,'window_bp':1000000}
SCOPE=('Homozygosity is measured among assessed reference-panel SNPs, including homozygous-reference calls; it is not the percentage of all genome bases that are homozygous. ROH are exploratory candidates inferred from imputed genotypes. The HMM uses pooled reference-panel allele frequencies, spaced common markers and an assumed genotype-error model. Imputation and linkage disequilibrium can affect segment boundaries. These results do not diagnose recessive disease, uniparental disomy or parental relatedness. X, Y and mitochondrial DNA are outside this autosomal analysis.')
CHROM_FIELDS=['chrom','length_bp','panel_snp_records','excluded_duplicate_position_records','passing_snps','homozygous_reference','heterozygous','homozygous_alternate','panel_snp_homozygosity_percent','common_passing_snps','common_homozygosity_percent','roh_markers','assessed_span_bp','roh_segments','roh_total_bp','roh_reference_span_percent','status']
SEG_FIELDS=['chrom','start','end','length_bp','markers','heterozygous_markers','mean_hmm_quality','status']


def scalar(v):
    if isinstance(v,tuple):v=v[0] if len(v)==1 else None
    try:return float(v) if math.isfinite(float(v)) else None
    except (ValueError,TypeError):return None


def scan(task):
    path,sample,method=task;counts=Counter();windows=defaultdict(Counter);selected=[];last_selected=0;chrom=None
    with pysam.VariantFile(path) as v:
        if list(v.header.samples)!=[sample]:raise ValueError('Homozygosity input sample mismatch')
        for (ch,pos),group in itertools.groupby(v,key=lambda r:(r.contig,r.pos)):
            rs=list(group)
            if ch.removeprefix('chr') not in {str(n) for n in range(1,23)}:continue
            if chrom is None:chrom=ch
            if chrom!=ch:raise ValueError('Expected one chromosome per imputed input')
            if len(rs)>1:counts['excluded_duplicate_position_records']+=len(rs);continue
            r=rs[0]
            if len(r.alleles)!=2 or any(len(x)!=1 or x not in 'ACGT' for x in r.alleles):continue
            counts['panel_snp_records']+=1;f=r.samples[sample];gt=f.get('GT');gp=f.get('GP');info=scalar(r.info.get('INFO'));raf=scalar(r.info.get('RAF'))
            if not gt or len(gt)!=2 or any(x not in (0,1) for x in gt) or not gp or len(gp)!=3:continue
            gp=[scalar(x) for x in gp]
            if any(x is None or not 0<=x<=1 for x in gp) or abs(sum(gp)-1)>.02:continue
            if gp[sum(gt)]<method['min_gp']-1e-7 or info is None or info<method['min_info']-1e-7:continue
            counts['passing_snps']+=1;kind=['homozygous_reference','heterozygous','homozygous_alternate'][sum(gt)];counts[kind]+=1
            window=windows[(pos-1)//method['window_bp']];window['passing_snps']+=1;window[kind]+=1
            if raf is None or not method['min_panel_maf']<=raf<=1-method['min_panel_maf']:continue
            counts['common_passing_snps']+=1;counts['common_hom']+=int(gt[0]==gt[1]);window['common_passing_snps']+=1;window['common_hom']+=int(gt[0]==gt[1])
            if pos-last_selected<method['marker_spacing_bp']:continue
            selected.append((pos,r.ref,r.alts[0],gt,raf));last_selected=pos
    return chrom,dict(counts),dict(windows),selected


def blocks(markers,max_gap):
    result=[];current=[]
    for marker in markers:
        if current and marker[0]-current[-1][0]>max_gap:result.append(current);current=[]
        current.append(marker)
    if current:result.append(current)
    return result


def segments(states,marker_lookup,block_map,method):
    """Read ST output, never bridge HMM contig blocks or non-autozygous states."""
    runs=[];current=[];previous=None
    def finish():
        if not current:return
        block=current[0][0];start=current[0][1];end=current[-1][1];length=end-start+1
        if len(current)>=method['min_roh_markers'] and length>=method['min_roh_bp']:
            runs.append({'chrom':block_map[block],'start':start,'end':end,'length_bp':length,'markers':len(current),'heterozygous_markers':sum(marker_lookup[(block,x[1])] for x in current),'mean_hmm_quality':round(sum(x[2] for x in current)/len(current),2),'status':'exploratory_imputed_ROH'})
        current.clear()
    for block,pos,state,quality in states:
        if previous and (block!=previous[0] or pos-previous[1]>method['max_gap_bp']):finish()
        if state==1:current.append((block,pos,quality))
        else:finish()
        previous=block,pos
    finish();return runs


def schematic(chromosomes,segments,windows):
    width=1200;height=130+len(chromosomes)*48;left=100;track=740;max_len=max((r['length_bp'] for r in chromosomes),default=1)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">Autosomal homozygosity and candidate runs</title><desc id="desc">Each chromosome is scaled to its GRCh38 length. Window colors show common-panel SNP homozygosity; gold bars show exploratory ROH.</desc><rect width="100%" height="100%" fill="white"/><text x="100" y="24" font-size="18" font-weight="bold">Homozygosity across the autosomes</text><text x="890" y="24" font-size="14">Common SNPs · candidate ROH</text>']
    for tick in range(0,251,50):
        x=left+track*tick*1e6/max_len;parts.append(f'<line x1="{x}" x2="{x}" y1="57" y2="{height-60}" stroke="#e6eeea"/><text x="{x}" y="48" font-size="11">{tick} Mb</text>')
    for n,row in enumerate(chromosomes):
        y=76+n*48;ch=row['chrom'];length=row['length_bp'];scale=track/max_len
        parts.append(f'<text x="22" y="{y+8}" font-size="14">{esc(ch)}</text><rect x="{left}" y="{y-7}" width="{length*scale}" height="18" rx="7" fill="#e0e7e4"/>')
        for w in windows:
            if w['chrom']!=ch or w['common_passing_snps']<20:continue
            h=w['common_homozygosity_percent']/100;rgb=(int(230-205*h),int(242-116*h),int(232-117*h));color='rgb'+str(rgb)
            x=left+(w['start']-1)*scale;ww=(w['end']-w['start']+1)*scale
            parts.append(f'<rect x="{x}" y="{y-7}" width="{ww}" height="18" fill="{color}"><title>{esc(ch)}:{w["start"]}-{w["end"]}; {h*100:.1f}% homozygous among {w["common_passing_snps"]} common SNPs</title></rect>')
        for r in segments:
            if r['chrom']==ch:parts.append(f'<rect x="{left+(r["start"]-1)*scale}" y="{y+15}" width="{max(1,r["length_bp"]*scale)}" height="6" rx="2" fill="#bf7a23"><title>Candidate ROH {esc(ch)}:{r["start"]}-{r["end"]}; {r["length_bp"]/1e6:.2f} Mb; {r["markers"]} markers</title></rect>')
        h=row['common_homozygosity_percent'];label=f'{h:.1f}%' if h is not None else 'Not assessed'
        parts.append(f'<text x="890" y="{y+8}" font-size="13">{label} · {row["roh_segments"]} runs · {row["roh_total_bp"]/1e6:.2f} Mb</text>')
    parts.append(f'<text x="100" y="{height-27}" font-size="12">Green: common-SNP homozygosity (darker = higher) · Gray: fewer than 20 assessed SNPs/Mb · Gold: candidate ROH</text></svg>')
    return ''.join(parts)


def run(a,c):
    out=Path(a.sample+'.homozygosity');out.mkdir(exist_ok=True);method={**METHOD};bc=c['tools']['bcftools']
    with pysam.FastaFile(c['reference']) as fa:lengths=dict(zip(fa.references,fa.lengths))
    if len(set(map(str,a.imputed)))!=len(a.imputed):raise ValueError('Duplicate imputation inputs')
    tasks=[(p,a.sample,method) for p in a.imputed]
    if len(tasks)>1 and a.threads>1:
        with ProcessPoolExecutor(max_workers=min(a.threads,8)) as pool:scanned=list(pool.map(scan,tasks))
    else:scanned=list(map(scan,tasks))
    data={}
    for chrom,counts,windows,markers in scanned:
        if chrom is None:continue
        if chrom in data:raise ValueError('Duplicate imputed chromosome')
        data[chrom]=(counts,windows,markers)
    header=pysam.VariantHeader();header.formats.add('GT',1,'String','High-posterior imputed genotype, not independent sequencing confirmation');header.info.add('RAF',1,'Float','Pooled GLIMPSE2 reference-panel allele frequency');header.add_sample(a.sample)
    block_map={};marker_lookup={};grouped={};assessed={}
    for ch,(_,_,markers) in data.items():
        bb=blocks(markers,method['max_gap_bp']);assessed[ch]=sum(x[-1][0]-x[0][0]+1 for x in bb if len(x)>1)
        for n,block in enumerate(bb,1):
            name=ch+'_block'+str(n);block_map[name]=ch;grouped[name]=block;header.contigs.add(name,length=lengths[ch])
    marker_file=out/(a.sample+'.roh_markers.vcf.gz')
    with pysam.VariantFile(str(marker_file),'wz',header=header) as v:
        for block,markers in grouped.items():
            for pos,ref,alt,gt,raf in markers:
                r=v.new_record(contig=block,start=pos-1,alleles=(ref,alt));r.samples[a.sample]['GT']=gt;r.info['RAF']=raf;v.write(r);marker_lookup[(block,pos)]=int(gt[0]!=gt[1])
    raw=out/(a.sample+'.roh_hmm.txt');log=out/(a.sample+'.roh_hmm.log')
    command=[bc,'roh','-G',str(method['genotype_error_phred']),'--AF-tag','RAF','-M',str(method['recombination_rate_per_bp']),'-o',str(raw),str(marker_file)]
    if marker_lookup:
        with log.open('w') as f:subprocess.run(command,stderr=f,check=True)
    else:raw.write_text('# No eligible common SNPs; ROH not assessed.\n');log.write_text('No eligible markers\n')
    states=[]
    for line in raw.read_text().splitlines():
        if line.startswith('ST\t'):
            x=line.split('\t');states.append((x[2],int(x[3]),int(x[4]),float(x[5])))
    if marker_lookup and len(states)!=len(marker_lookup):raise ValueError('ROH HMM did not report every eligible marker; inspect roh_hmm.log')
    runs=segments(states,marker_lookup,block_map,method);chromosomes=[];window_rows=[]
    for ch in sorted((x for x in lengths if x.removeprefix('chr') in {str(i) for i in range(1,23)}),key=lambda x:int(x.removeprefix('chr'))):
        count,windows,markers=data.get(ch,({}, {}, []));row={k:count.get(k,0) for k in CHROM_FIELDS};passing=count.get('passing_snps',0);common=count.get('common_passing_snps',0);these=[r for r in runs if r['chrom']==ch]
        row.update(chrom=ch,length_bp=lengths[ch],panel_snp_homozygosity_percent=round(100*(passing-count.get('heterozygous',0))/passing,4) if passing else None,common_homozygosity_percent=round(100*count.get('common_hom',0)/common,4) if common else None,roh_markers=len(markers),assessed_span_bp=assessed.get(ch,0),roh_segments=len(these),roh_total_bp=sum(r['length_bp'] for r in these),status='assessed' if len(markers)>=method['min_roh_markers'] else 'insufficient_markers' if ch in data else 'not_imputed')
        row['roh_reference_span_percent']=round(100*row['roh_total_bp']/lengths[ch],5) if row['status']=='assessed' else None;chromosomes.append(row)
        for bin,cnt in sorted(windows.items()):
            common=cnt.get('common_passing_snps',0);window_rows.append({'chrom':ch,'start':bin*method['window_bp']+1,'end':min(lengths[ch],(bin+1)*method['window_bp']),'passing_snps':cnt['passing_snps'],'common_passing_snps':common,'common_homozygosity_percent':round(100*cnt.get('common_hom',0)/common,3) if common else None})
    for name,fields,records in [('chromosomes',CHROM_FIELDS,chromosomes),('roh_segments',SEG_FIELDS,runs),('windows',['chrom','start','end','passing_snps','common_passing_snps','common_homozygosity_percent'],window_rows)]:table(out/(a.sample+'.'+name+'.tsv'),fields,records)
    book=Excel(out/(a.sample+'.homozygosity.xlsx'),[['Scope',SCOPE],['Method',json.dumps(method)],['Coordinates','1-based inclusive. ROH HMM input uses independent chromosome blocks to prevent bridging gaps >250 kb.']])
    for name,fields,records in [('Chromosomes',CHROM_FIELDS,chromosomes),('ROH_segments',SEG_FIELDS,runs)]:
        book.create(name,fields)
        for r in records:book.add(name,r)
    book.close();svg=schematic(chromosomes,runs,window_rows);(out/(a.sample+'.chromosome_homozygosity.svg')).write_text(svg)
    total=sum(r['roh_total_bp'] for r in chromosomes);assessed_n=sum(r['status']=='assessed' for r in chromosomes)
    summary={'sample':a.sample,'version':VERSION,'runtime_signature':runtime_signature(c),'scope':SCOPE,'method':method,'chromosomes':chromosomes,'segments':runs,'candidate_roh_count':len(runs),'candidate_roh_bp':total,'assessed_chromosomes':assessed_n,'block_to_chromosome':block_map,'command':command,'input_provenance':[{'path':str(Path(p).resolve()),'bytes':Path(p).stat().st_size,'mtime_ns':Path(p).stat().st_mtime_ns} for p in a.imputed],'sources':['https://samtools.github.io/bcftools/howtos/roh-calling.html','https://doi.org/10.1093/bioinformatics/btw044']}
    dump(out/(a.sample+'.homozygosity.json'),summary)
    body=cards([(str(assessed_n)+' / '+str(len(chromosomes)),'autosomes with sufficient markers'),(len(runs),'candidate ROH ≥1 Mb and ≥100 markers'),(f'{total/1e6:.2f} Mb','total candidate ROH span')])+section('Chromosome overview','<p>Hover over the tracks for SNP-window and segment details. Gray intervals are unassessed or sparse; they are not evidence of homozygosity.</p>'+svg)
    body+=section('Homozygosity by chromosome',html_table(chromosomes,[(k.replace('_',' ').capitalize(),k) for k in CHROM_FIELDS]))+section('Candidate runs of homozygosity',('<p>No segments met both length and marker thresholds.</p>' if not runs else '')+html_table(runs,[(k.replace('_',' ').capitalize(),k) for k in SEG_FIELDS]))
    body+=section('How to read these results','<p>'+esc(SCOPE)+'</p><p>Common-SNP percentages use panel MAF ≥5%; the full-panel percentage includes rare SNPs and will usually be much higher. Both use GP ≥0.99 and INFO ≥0.80. ROH markers are spaced ≥10 kb. Independent HMM blocks split gaps >250 kb; a segment requires ≥1 Mb and ≥100 markers. A constant recombination rate of 10⁻⁸ per bp and genotype error Phred 20 are analysis assumptions, not measured error rates. No posterior probability is treated as a sequencing likelihood. The span percentage uses the full GRCh38 chromosome length; unassessed sequence is included in that denominator.</p><p><a href="https://samtools.github.io/bcftools/howtos/roh-calling.html">BCFtools ROH method</a> · <a href="https://doi.org/10.1093/bioinformatics/btw044">Method publication</a></p>')
    write(out/(a.sample+'.homozygosity.html'),a.sample+' · homozygosity and chromosome runs','An autosomal view of complete GLIMPSE2 genotypes, with reference calls included and candidate homozygous segments shown separately.',body,[('All results','../../START_HERE.html'),('Excel',a.sample+'.homozygosity.xlsx'),('Chromosome SVG',a.sample+'.chromosome_homozygosity.svg'),('Segments TSV',a.sample+'.roh_segments.tsv'),('Chromosome TSV',a.sample+'.chromosomes.tsv')])
    print(json.dumps({'assessed_chromosomes':assessed_n,'roh_count':len(runs),'roh_bp':total}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--sample',required=True);p.add_argument('--imputed',nargs='+',required=True);p.add_argument('--threads',type=int,default=2);a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
