"""Exploratory regional reference similarity using exact, spaced autosomal SNPs."""
import csv,gzip,json,hashlib,subprocess,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import numpy as np
import pysam
from extension_common import dump,table,sha256
VERSION='regional-frequency-distance-v1'
NAMES={'ACB':'African Caribbeans in Barbados','ASW':'African ancestry in southwest USA','ESN':'Esan in Nigeria','GWD':'Gambian in Western Division','LWK':'Luhya in Webuye, Kenya','MSL':'Mende in Sierra Leone','YRI':'Yoruba in Ibadan, Nigeria','CLM':'Colombians from Medellin, Colombia','MXL':'Mexican ancestry in Los Angeles, USA','PEL':'Peruvians from Lima, Peru','PUR':'Puerto Ricans from Puerto Rico','CDX':'Chinese Dai in Xishuangbanna, China','CHB':'Han Chinese in Beijing, China','CHS':'Southern Han Chinese','JPT':'Japanese in Tokyo, Japan','KHV':'Kinh in Ho Chi Minh City, Vietnam','CEU':'Utah residents with northern/western European ancestry','FIN':'Finnish in Finland','GBR':'British in England and Scotland','IBS':'Iberian populations in Spain','TSI':'Toscani in Italy','BEB':'Bengali in Bangladesh','GIH':'Gujarati Indian in Houston, USA','ITU':'Indian Telugu in the UK','PJL':'Punjabi in Lahore, Pakistan','STU':'Sri Lankan Tamil in the UK'}
NOTE=('Relative similarity to sampled reference groups, not percentages of ancestry, probabilities of geographic origin or nationality. '
      'The closest group can approximate a mixture of ancestors and need not be an ancestral source. '
      'Chile and individual Indigenous communities are not separate groups in this reference. '
      'Genotypes were imputed with the same panel, so this comparison is not independent validation.')

def distances(dosages,frequencies):
    d=np.asarray(dosages,float);f=np.asarray(frequencies,float)
    if f.ndim!=2 or len(d)!=len(f) or not np.all(np.isfinite(d)) or not np.all(np.isfinite(f)):raise ValueError('Invalid comparison arrays')
    if np.any((d<0)|(d>2)) or np.any((f<0)|(f>1)):raise ValueError('Invalid dosage/frequency range')
    # Balanced population weighting avoids favoring the largest reference group.
    global_af=f.mean(axis=1);w=global_af*(1-global_af)
    keep=(global_af>=.05)&(global_af<=.95)
    return ((d[keep,None]/2-f[keep])**2/w[keep,None]),keep

def compare(rows,pops,counts):
    d=np.array([r['dosage'] for r in rows]);f=np.array([[r[p] for p in pops] for r in rows]);errors,keep=distances(d,f)
    chroms=np.array([r['chrom'] for r in rows])[keep];unique=sorted(set(chroms))
    if len(errors)<1000 or len(unique)<18:return {'status':'insufficient_data','markers':len(errors),'scope':NOTE,'populations':[]}
    mean=errors.mean(axis=0);jack=[];best=defaultdict(int)
    for chrom in unique:
        v=errors[chroms!=chrom].mean(axis=0);jack.append(v);best[pops[int(np.argmin(v))]]+=1
    jack=np.array(jack);se=np.sqrt((len(unique)-1)/len(unique)*((jack-jack.mean(axis=0))**2).sum(axis=0))
    order=np.argsort(mean,kind='stable');records=[]
    for rank,i in enumerate(order,1):
        records.append({'rank':rank,'population':pops[i],'population_name':NAMES.get(pops[i],pops[i]),'reference_n':counts[pops[i]],'distance':float(mean[i]),'jackknife_se':float(se[i]),'first_in_chromosome_jackknifes':best[pops[i]]})
    return {'status':'exploratory_similarity','version':VERSION,'markers':len(errors),'chromosomes':len(unique),'minimum_spacing_bp':250000,'populations':records,'scope':NOTE,'method':'Mean squared allele-frequency distance, standardized by balanced reference frequency variance; lower is closer. Chromosome jackknife measures stability, not an ancestry confidence interval.','source':'https://www.internationalgenome.org/data-portal/population'}

def run(sample,folder,references,c,threads=4):
    folder=Path(folder);metadata=Path(c['root'])/'data/1000G.reference_population.panel'
    if not references or c.get('test_mode'):return {'status':'reference_comparison_unavailable','scope':NOTE,'populations':[]}
    with metadata.open() as f:meta={r['sample']:r['pop'] for r in csv.DictReader(f,delimiter='\t')}
    refs={};common=set(meta)
    for path in references:
        if not str(path).endswith('.bcf'):continue
        with pysam.VariantFile(str(path)) as v:
            chrom=next(v).contig
            if chrom in refs:raise ValueError('Duplicate regional reference chromosome')
            refs[chrom]=str(path);common&=set(v.header.samples)
    if sample in common:raise ValueError('Target is part of comparison panel')
    samples=sorted(common);pops=sorted({meta[s] for s in samples});groups={p:[i for i,s in enumerate(samples) if meta[s]==p] for p in pops};counts={p:len(g) for p,g in groups.items()}
    if len(pops)<20 or min(counts.values(),default=0)<20:return {'status':'insufficient_reference_groups','scope':NOTE,'populations':[]}
    targets=defaultdict(dict);last={}
    with gzip.open(folder/(sample+'.ancestry_markers.tsv.gz'),'rt') as f:
        for r in csv.DictReader(f,delimiter='\t'):
            ch=r['chrom'];pos=int(r['position']);ref=r['ref'].upper();alt=r['alt'].upper();fa=r['frequency_allele'].upper()
            if ch not in refs or pos-last.get(ch,-250000)<250000:continue
            if fa not in {ref,alt} or len(ref)!=1 or len(alt)!=1:continue
            ds=float(r['model_allele_dosage']);ds=ds if fa==alt else 2-ds
            targets[ch][(pos,ref,alt)]=ds;last[ch]=pos
    cache=Path(c['ancestry_explorer_cache'])/'regional_frequencies';cache.mkdir(parents=True,exist_ok=True)
    def chromosome(ch):
        src=Path(refs[ch]);keys=sorted(targets[ch]);signature=hashlib.sha256(json.dumps([VERSION,sha256(__file__),sha256(metadata),str(src.resolve()),src.stat().st_size,src.stat().st_mtime_ns,keys,samples]).encode()).hexdigest()
        destination=cache/(signature+'.tsv.gz')
        if not destination.exists():
            bed=folder/(ch+'.regional.bed');bed.write_text(''.join(f'{ch}\t{k[0]-1}\t{k[0]}\n' for k in keys));subset=folder/(ch+'.regional.bcf')
            subprocess.run([c['tools']['bcftools'],'view','-R',str(bed),'-Ob','-o',str(subset),str(src)],check=True,stdout=subprocess.DEVNULL)
            subprocess.run([c['tools']['bcftools'],'index','-f',str(subset)],check=True)
            values=[];seen=set()
            with pysam.VariantFile(str(subset)) as v:
                v.subset_samples(samples)
                for rec in v:
                    if len(rec.alts or ())!=1:continue
                    key=(rec.pos,rec.ref.upper(),rec.alts[0].upper())
                    if key not in targets[ch]:continue
                    if key in seen:raise ValueError('Duplicate regional reference allele')
                    seen.add(key);gts=[rec.samples[s].get('GT') for s in samples]
                    if any(g is None or len(g)!=2 or any(a not in (0,1) for a in g) for g in gts):continue
                    ds=np.array([sum(g) for g in gts]);values.append({'chrom':ch,'pos':key[0],'ref':key[1],'alt':key[2],**{p:float(ds[ix].mean()/2) for p,ix in groups.items()}})
            temporary=cache/(signature+'.'+uuid.uuid4().hex+'.tmp.gz');table(temporary,['chrom','pos','ref','alt']+pops,values);temporary.replace(destination);subset.unlink();Path(str(subset)+'.csi').unlink();bed.unlink()
        with gzip.open(destination,'rt') as f:rows=list(csv.DictReader(f,delimiter='\t'))
        for r in rows:r.update(dosage=targets[ch][(int(r['pos']),r['ref'],r['alt'])],**{p:float(r[p]) for p in pops})
        return rows
    with ThreadPoolExecutor(max_workers=min(4,max(1,threads))) as pool:rows=[r for part in pool.map(chromosome,sorted(targets)) for r in part]
    summary=compare(rows,pops,counts);summary.update(metadata_sha256=sha256(metadata),unrelated_reference_samples=len(samples))
    table(folder/(sample+'.population_similarity.tsv'),['rank','population','population_name','reference_n','distance','jackknife_se','first_in_chromosome_jackknifes'],summary['populations'])
    dump(folder/(sample+'.population_similarity.json'),summary)
    return summary
