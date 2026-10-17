#!/usr/bin/env python3
"""Empirical score distributions using exactly the sample's retained model terms."""
import argparse,csv,gzip,hashlib,json,math,subprocess,shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pysam
from extension_common import dump,sha256
VERSION='matched-mask-reference-v3'
NOTE=('Exploratory reference-panel comparison, not a disease probability or clinical risk calibration. '
      'The reference panel was also used for imputation and is not an independent clinical validation cohort. '
      'Broad population labels do not fully match an admixed individual. Percentile uncertainty covers reference sampling only, not genotype, model or ancestry uncertainty.')

def stats(values,score):
 values=np.asarray(values,dtype=float);n=len(values)
 if n<2 or not np.all(np.isfinite(values)) or not math.isfinite(score):raise ValueError('Invalid reference scores')
 mean=float(values.mean());sd=float(values.std(ddof=1));p=float(((values<score).sum()+.5*(values==score).sum())/n)
 z=1.959963984540054;denom=1+z*z/n;mid=(p+z*z/(2*n))/denom;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
 return {'n':n,'mean':mean,'sd':sd,'z':(score-mean)/sd if sd>0 else None,'percentile':100*p,'percentile_low':100*max(0,mid-half),'percentile_high':100*min(1,mid+half)}

def selected_population(a,c):
 pop=c.get('pgs_reference_population','auto')
 if pop!='auto':return pop
 path=getattr(a,'ancestry',None)
 if path:
  d=json.loads(Path(path).read_text());label=d.get('nearest_reference_label')
  if label in {'AMR','AFR','EUR','EAS','SAS'}:return label
 return 'unresolved'

def run(a,c):
 models=[json.loads(Path(p).read_text()) for p in a.scores];ids=[r['pgs_id'] for r in models];index={id:n for n,id in enumerate(ids)}
 if len(ids)!=len(set(ids)):raise ValueError('Repeated model in statistical comparison')
 reference={}
 for path in a.references:
  if not str(path).endswith('.bcf'):continue
  with pysam.VariantFile(path) as v:
   r=next(v);chrom=r.contig
   if chrom in reference:raise ValueError('Duplicate reference chromosome')
   reference[chrom]=str(path)
 panel=Path(c['root'])/'data/1000G.reference_population.panel'
 fingerprint={'version':VERSION,'code':sha256(__file__),'population_metadata':sha256(panel),
  'sample':a.sample,'scores':[{'id':m['pgs_id'],'mask':m['variant_mask_sha256'],'raw':m.get('raw_score'),'partial':m.get('partial_score'),'terms':m['scored_variants']} for m in models],
  'inputs':[{'path':str(Path(p).resolve()),'bytes':Path(p).stat().st_size,'mtime_ns':Path(p).stat().st_mtime_ns} for p in sorted([*reference.values(),*a.audits],key=lambda p:str(Path(p).resolve()))]}
 signature=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()
 name=a.sample+'.pgs_reference_stats.json';matrix=a.sample+'.reference_scores.tsv.gz'
 cache=Path(c['output'])/'polygenic/reference_statistics';old=cache/name
 if old.is_file() and (cache/matrix).is_file():
  saved=json.loads(old.read_text())
  if saved.get('input_signature')==signature and saved.get('reference_scores_sha256')==sha256(cache/matrix):
   saved['reference_population']=selected_population(a,c);dump(name,saved);shutil.copyfile(cache/matrix,matrix)
   print('Reused verified matched-marker reference statistics for',len(models),'models',flush=True);return
 with panel.open() as f:population={r['sample']:r['super_pop'] for r in csv.DictReader(f,delimiter='\t')}
 common=set(population)
 for path in reference.values():
  with pysam.VariantFile(path) as v:common &= set(v.header.samples)
 samples=sorted(common)
 if len(samples)<50 and not c.get('test_mode'):raise ValueError('Too few unrelated reference samples')
 if a.sample in samples:raise ValueError('Sample cannot be part of its own comparison reference')
 samplesfile=Path('reference_samples.txt');samplesfile.write_text('\n'.join(samples)+'\n')
 by_chrom=defaultdict(dict);counts=[0]*len(models);sample_sums=[[] for _ in models];mask=[hashlib.sha256() for _ in models]
 for path in a.audits:
  model=next((id for id in ids if '.'+id+'.' in Path(path).name),None)
  if model is None:raise ValueError('Unknown audit model: '+str(path))
  m=index[model]
  with gzip.open(path,'rt') as f:
   for row in csv.DictReader(f,delimiter='\t'):
    if row['status']!='scored':continue
    chrom=c.get('chrom_prefix','chr')+row['chrom'].removeprefix('chr');key=(int(row['pos']),row['matched_ref'],row['matched_alt'])
    if row['effect_allele'] not in {row['matched_ref'],row['matched_alt']}:
     # The audit records matched orientation; complement the original effect allele when required.
     effect=row['effect_allele'].translate(str.maketrans('ACGT','TGCA'))
    else:effect=row['effect_allele']
    if effect not in key[1:]:raise ValueError('Unresolved effect allele in audit')
    by_chrom[chrom].setdefault(key,[]).append((m,float(row['weight']),effect==key[2],row['model']))
    counts[m]+=1;sample_sums[m].append(float(row['contribution']))
    term={k:row[k] for k in ['line','chrom','pos','effect_allele','other_allele','weight','model']}
    term.update(line=int(term['line']),pos=int(term['pos']),weight=float(term['weight']))
    mask[m].update(json.dumps(term,sort_keys=True).encode()+b'\n')
 for n,m in enumerate(models):
  if mask[n].hexdigest()!=m['variant_mask_sha256']:raise ValueError('Retained allele/weight mask mismatch: '+ids[n])
  if counts[n]!=m['scored_variants']:raise ValueError('Audit term count mismatch: '+ids[n])
  expected=m.get('raw_score') if m.get('raw_score') is not None else m.get('partial_score')
  if expected is None and counts[n]==0:continue
  if expected is None or not math.isclose(math.fsum(sample_sums[n]),expected,rel_tol=1e-9,abs_tol=1e-8):raise ValueError('Audit weighted sum mismatch: '+ids[n])
 totals=np.zeros((len(models),len(samples)),dtype=np.float64);matched=[0]*len(models)
 def score_chrom(item):
  chrom,targets=item;chrom_totals=np.zeros_like(totals);chrom_matched=[0]*len(models)
  if chrom not in reference:raise ValueError('Reference chromosome unavailable: '+chrom)
  bed=Path(chrom+'.score_sites.bed');bed.write_text(''.join(f'{chrom}\t{p-1}\t{p}\n' for p in sorted({k[0] for k in targets})))
  cmd=[c['tools']['bcftools'],'query','-S',str(samplesfile),'-R',str(bed),'-f','%POS\t%REF\t%ALT[\t%GT]\n',reference[chrom]]
  print('Reference scoring',chrom,len(targets),'matched allele targets;',len(samples),'unrelated reference individuals',flush=True)
  seen=set()
  with subprocess.Popen(cmd,stdout=subprocess.PIPE,text=True,bufsize=1024*1024) as proc:
   for line in proc.stdout:
    pos,ref,alt,gts=line.rstrip('\n').split('\t',3);key=(int(pos),ref,alt)
    if key not in targets:continue
    if key in seen:raise ValueError('Duplicate exact allele in reference')
    seen.add(key)
    # Biallelic diploid GTs have a fixed four-byte representation including tab/newline.
    encoded=np.frombuffer((gts+'\t').encode('ascii'),dtype=np.uint8)
    if len(encoded)!=len(samples)*4:raise ValueError('Reference genotype cardinality mismatch')
    x=encoded.reshape(-1,4)
    if not np.all((x[:,0]>=48)&(x[:,0]<=49)&(x[:,2]>=48)&(x[:,2]<=49)&np.isin(x[:,1],[47,124])):raise ValueError('Missing or unsupported reference genotype; no mean replacement is permitted')
    dose=x[:,0].astype(np.float64)+x[:,2]-96
    for m,weight,is_alt,model in targets[key]:
     value=dose if is_alt else 2-dose
     if model=='dominant':value=(value>=1)
     elif model=='recessive':value=(value==2)
     elif model!='additive':raise ValueError('Unsupported scoring term')
     chrom_totals[m]+=weight*value;chrom_matched[m]+=1
   if proc.wait()!=0:raise RuntimeError('Reference genotype extraction failed')
  if len(seen)!=len(targets):raise ValueError('Reference does not contain every retained target allele for '+chrom)
  bed.unlink()
  return chrom_totals,chrom_matched
 workers=max(1,min(8,int(getattr(a,'threads',2))//2))
 with ThreadPoolExecutor(max_workers=workers) as executor:
  for values,terms in executor.map(score_chrom,sorted(by_chrom.items(),key=lambda x:int(x[0].removeprefix('chr')))):
   totals+=values;matched=[a+b for a,b in zip(matched,terms)]
 if matched!=counts:raise ValueError('Reference and sample masks differ')
 result=[]
 for n,m in enumerate(models):
  score=m.get('raw_score') if m.get('raw_score') is not None else m['partial_score'];popstats={}
  for pop in sorted(set(population[s] for s in samples)):
   indices=[i for i,s in enumerate(samples) if population[s]==pop]
   if len(indices)>=2 and score is not None:popstats[pop]=stats(totals[n,indices],score)
  result.append({'pgs_id':m['pgs_id'],'matched_terms':matched[n],'variant_mask_sha256':m['variant_mask_sha256'],'sample_score':score,'populations':popstats})
 out={'sample':a.sample,'method':VERSION,'note':NOTE,'reference_population':selected_population(a,c),'population_metadata_sha256':sha256(panel),'reference_samples':len(samples),'models':result,
      'references':[{'file':p,'bytes':Path(p).stat().st_size,'mtime_ns':Path(p).stat().st_mtime_ns} for p in reference.values()]}
 with gzip.open(a.sample+'.reference_scores.tsv.gz','wt') as f:
  writer=csv.writer(f,delimiter='\t');writer.writerow(['reference_sample','population',*ids])
  for n,s in enumerate(samples):writer.writerow([s,population[s],*totals[:,n]])
 out.update(input_signature=signature,reference_scores_sha256=sha256(matrix));dump(name,out)
 print('Reference statistics computed for',len(models),'models',flush=True)

def attach(models,paths):
 if not paths:return models
 data=json.loads(Path(paths[0]).read_text());mapping={r['pgs_id']:r for r in data['models']};out=[]
 for model in models:
  r=dict(model);value=mapping.get(r['pgs_id'])
  if r.get('sample')!=data.get('sample'):raise ValueError('Reference statistics belong to a different sample')
  if not value or value['variant_mask_sha256']!=r['variant_mask_sha256']:raise ValueError('Stale reference statistics')
  st=value['populations'].get(data['reference_population'])
  r.update(reference_population=data['reference_population'],reference_statistics_note=data['note'],reference_population_statistics=value['populations'],population_interpretation='Exploratory exact-marker reference comparison available. '+NOTE)
  if st:r.update(reference_percentile=round(st['percentile'],2),reference_z=round(st['z'],3) if st['z'] is not None else None,reference_n=st['n'],reference_percentile_low=round(st['percentile_low'],2),reference_percentile_high=round(st['percentile_high'],2))
  out.append(r)
 return out

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--sample',required=True);p.add_argument('--ancestry');p.add_argument('--threads',type=int,default=2)
 for key in ['references','scores','audits']:p.add_argument('--'+key,nargs='+',required=True)
 a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
