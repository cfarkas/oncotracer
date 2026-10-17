#!/usr/bin/env python3
"""One normalized, annotated VCF per sample; all evidence states are FORMAT fields."""
import argparse,csv,gzip,heapq,itertools,json,math,re,subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import quote
import pysam
from extension_common import dump,sha256,runtime_signature
from variant_priority import rows,number
from sample_variant_status import zygosity,from_priority
VERSION='unified-evidence-v4'
FORMATS={
 'ZYG':(1,'String','Zygosity of selected primary GT; unresolved when GT is missing'),
 'RZYG':(1,'String','Reported candidate zygosity; may be an unconfirmed read call; see ZYG and ZYG_EVID'),
 'GZYG':(1,'String','Zygosity of original germline caller genotype; does not imply genotype reliability'),
 'SZYG':(1,'String','Zygosity of original somatic caller genotype; does not establish acquired origin'),
 'IZYG':(1,'String','Zygosity of original imputed genotype; see posterior and imputation state'),
 'ZYG_SRC':(1,'String','Source used for reported candidate zygosity; percent-encoded'),
 'ZYG_EVID':(1,'String','Evidence qualification for reported zygosity; percent-encoded'),
 'AFFSTAT':(1,'String','Affected status: not_established; phenotype is never inferred from genotype or copied from ClinVar submitters'),
 'GT':(1,'String','Selected diploid genotype; missing if evidence does not resolve it'),
 'FT':(1,'String','Sample evidence filter: PASS means pipeline genotype support only'),
 'EVCLASS':(1,'String','Evidence category: germline_supported, somatic_candidate, unconfirmed; not proven biological origin'),
 'ET':(1,'String','Evidence tier'), 'ER':(1,'String','Reason for evidence category'),
 'SRC':(1,'String','Available callers and imputation'), 'GTS':(1,'String','Source of selected GT'),
 'GGT':(1,'String','Original germline genotype in REF/ALT allele letters'),
 'GDP':(1,'Integer','Germline caller read depth'),'GAD':('R','Integer','Germline caller allele depths'),
 'GGQ':(1,'Float','Germline caller genotype quality'),'GFT':(1,'String','Original germline caller FILTER'),
 'SGT':(1,'String','Original somatic genotype in REF/ALT allele letters'),
 'SDP':(1,'Integer','Somatic caller read depth'),'SAD':('R','Integer','Somatic caller allele depths'),
 'SVAF':('A','Float','Somatic caller alternate read fraction'),'SFT':(1,'String','Original somatic caller FILTER'),
 'STLOD':('A','Float','Mutect2 tumor log odds; not a germline genotype quality'),
 'IGT':(1,'String','Original imputed genotype in REF/ALT allele letters'),
 'IGP':(1,'Float','Imputation posterior for the called genotype; not a full Number=G probability vector'),
 'IDS':('A','Float','Imputed alternate allele dosage'),'IINFO':(1,'Float','Sample imputation-site quality, retained per sample for merging'),
 'IST':(1,'String','Imputation evidence state'),
 'SUP':(1,'Float','Genotype support heuristic 0-100 where reviewed; missing elsewhere'),
 'PRI':(1,'Float','Clinical review priority heuristic 0-100 where reviewed'),
 'CSTAT':(1,'String','Current exact-allele clinical review status; may withhold historical ClinVar assertions'),
 'CCLASS':(1,'String','Current public clinical classification from the reviewed exact allele'),
 'CARRIER':(1,'String','Sample carrier context: possible_recessive_carrier, dominant_disease_candidate, recessive_genotype_unresolved, allele_not_supported, not_applicable, unknown, withheld; never clinical confirmation'),
 'CARGT':(1,'String','Allele-letter genotype used for carrier-context review; may be unconfirmed'),
 'CARWHY':(1,'String','Carrier-context rationale; percent-encoded'),
 'INHERIT':(1,'String','Reviewed inheritance context; percent-encoded'),
 'NDC':(1,'Integer','Carried derived-allele copies at a published Neanderthal-associated marker; not a genome ancestry fraction'),
 'FLAGS':(1,'String','Additional evidence limitations and clinical holds; percent-encoded')}
ANNOTATIONS={'Func.refGene':'ANN_FUNC','Gene.refGene':'ANN_GENE','GeneDetail.refGene':'ANN_DETAIL','ExonicFunc.refGene':'ANN_EXONIC','AAChange.refGene':'ANN_AA'}

def clean(value):
 if value is None or str(value) in {'','.','None'}:return None
 return quote(str(value),safe='._-|/')

def locus(r,order):return order[r['chrom']],int(r['pos'])
def canonical_row(row):
 if row['ref']==row['ref'].upper() and row['alt']==row['alt'].upper():return row
 row=dict(row)
 for key in ['ref','alt','genotype','germline_GT','somatic_GT','imputed_GT']:
  if key in row:row[key]=row[key].upper()
 if 'variant' in row:row['variant']=f"{row['chrom']}:{row['pos']}:{row['ref']}:{row['alt']}"
 return row

def uppercase_vcf(source,destination):
 changed=0
 with pysam.VariantFile(source) as reader,pysam.VariantFile(destination,'wb',header=reader.header) as writer:
  for record in reader:
   alleles=tuple(a.upper() for a in record.alleles)
   if alleles!=record.alleles:record.alleles=alleles;changed+=1
   writer.write(record)
 return changed

def groups(iterator,kind,order):
 last=None
 for key,group in itertools.groupby(map(canonical_row,iterator),key=lambda r:locus(r,order)):
  if last is not None and key<last:raise ValueError('Unsorted '+kind+' input')
  last=key;yield key,kind,list(group)

def annotation_rows(path,sample):
 with pysam.VariantFile(path) as v:
  if list(v.header.samples)!=[sample]:raise ValueError('Annotation VCF sample mismatch')
  for r in v:
   if len(r.alts or ())!=1:raise ValueError('Annotation input must be biallelic')
   out={'chrom':r.contig,'pos':r.pos,'ref':r.ref,'alt':r.alts[0],'rsid':r.id or '.'}
   for old,new in ANNOTATIONS.items():
    val=r.info.get(old) if old in v.header.info else None
    if val:out[new]='|'.join(map(str,val)) if isinstance(val,tuple) else str(val)
   if 'TLOD' in r.info:out['STLOD']=r.info['TLOD']
   yield out

def imputed_rows(paths,bcfs,sample,c):
 if paths:
  for path in paths:yield from rows(path)
 else:
  from imputed_clinvar import make_row
  for path in bcfs:
   with pysam.VariantFile(path) as v:
    if list(v.header.samples)!=[sample]:raise ValueError('Imputation sample mismatch')
    for _,records in itertools.groupby(v,key=lambda x:(x.contig,x.pos)):
     records=list(records)
     for r in records:
      gt=r.samples[sample].get('GT') or ()
      if len(gt)==2 and None not in gt and any(x>0 for x in gt):yield make_row(r,sample,c,len(records)>1)

def gt_indices(value,ref,alt):
 parts=re.split(r'[/|]',str(value))
 if len(parts)!=2 or any(x not in {ref,alt} for x in parts):return (None,None)
 return tuple(0 if x==ref else 1 for x in parts)

def integer(v):
 n=number(v)
 if n is None:return None
 if not n.is_integer():raise ValueError('Noninteger read depth '+str(v))
 return int(n)

def fill(r,observed,imp,annotations,priority,sample):
 o=observed or {};i=imp or {};f=r.samples[sample];ref,alt=r.alleles
 category=o.get('category') or ('germline_supported' if i.get('evidence_status')=='supported_imputed' else 'unconfirmed')
 tier=o.get('evidence_tier') or ('imputed_support' if category=='germline_supported' else 'insufficient_evidence')
 ist=o.get('imputation_state') or i.get('evidence_status','not_available')
 ig=i.get('genotype',o.get('imputed_GT','.'));gg=o.get('germline_GT','.');sg=o.get('somatic_GT','.')
 source='unresolved';gt=(None,None)
 if category=='germline_supported':
  if tier in {'direct_reads','reads_and_imputation'}:gt=gt_indices(gg,ref,alt);source='germline_reads'
  elif tier=='imputed_support':gt=gt_indices(ig,ref,alt);source='imputation'
 elif category=='somatic_candidate':gt=gt_indices(sg,ref,alt);source='somatic_reads'
 if None in gt:category='unconfirmed';source='unresolved'
 f['GT']=gt;f.phased=source=='imputation' and '|' in ig
 f['FT']='PASS' if category=='germline_supported' else 'SomaticCandidate' if category=='somatic_candidate' else 'Unconfirmed'
 values={'EVCLASS':category,'ET':tier,'ER':o.get('reason',i.get('reason','imputed_nonreference_site')),'SRC':o.get('called_by','')+('+imputed' if i or o.get('imputed_GT','.')!='.' else ''),'GTS':source,
         'GGT':gg,'GFT':o.get('germline_filter'),'SGT':sg,'SFT':o.get('somatic_filter'),'IGT':ig,'IST':ist}
 values['CARRIER']='not_applicable' if category=='somatic_candidate' else 'unknown'
 reported=dict(priority) if priority else {'variant':f'{r.contig}:{r.pos}:{ref}:{alt}','read_genotype':sg if category=='somatic_candidate' else gg,'read_source':'somatic_caller' if category=='somatic_candidate' else 'germline_caller','read_DP':o.get('somatic_DP') if category=='somatic_candidate' else o.get('germline_DP'),'imputed_genotype':ig,'imputation_passing':ist in {'supported_imputed','alternate_supported','reference_supported'}}
 if not priority:
  prefix='somatic' if category=='somatic_candidate' or (not gg or gg=='.') else 'germline'
  depths=str(o.get(prefix+'_AD','.')).split(',')
  reported.update(read_genotype=o.get(prefix+'_GT','.'),read_source=prefix+'_caller',read_DP=o.get(prefix+'_DP'),read_ALT=number(depths[1]) if len(depths)==2 else None)
  reported['support_flags']={'discordant_evidence':'reliable_genotype_discordance','ambiguous_imputation':'ambiguous_imputation','conflicting_records':'conflicting_records'}.get(tier,'')
 status=from_priority(reported)
 values.update(ZYG=zygosity(gt,ref,alt,r.contig),RZYG=status['zygosity'],GZYG=zygosity(gg,ref,alt,r.contig),SZYG=zygosity(sg,ref,alt,r.contig),IZYG=zygosity(ig,ref,alt,r.contig),ZYG_SRC=status['zygosity_source'],ZYG_EVID=status['zygosity_evidence'],AFFSTAT='not_established')
 if priority:values.update(CSTAT=priority.get('interpretation_status'),CCLASS=priority.get('current_classification'),FLAGS=priority.get('support_flags'),CARRIER=priority.get('carrier_status','unknown'),CARGT=priority.get('carrier_genotype'),CARWHY=priority.get('carrier_reason'),INHERIT=priority.get('inheritance_context'))
 for tag,val in values.items():
  if clean(val):f[tag]=clean(val).lstrip('+')
 for tag,val in [('GDP',o.get('germline_DP')),('SDP',o.get('somatic_DP'))]:
  val=integer(val)
  if val is not None:f[tag]=val
 for tag,val in [('GGQ',o.get('germline_GQ')),('SVAF',o.get('somatic_VAF')),('IGP',i.get('called_GP',o.get('imputed_GP'))),('IDS',i.get('DS',o.get('imputed_DS'))),('IINFO',i.get('INFO',o.get('imputed_INFO'))),('SUP',priority.get('support_score') if priority else None),('PRI',priority.get('review_priority_score') if priority else None)]:
  val=number(val)
  if val is not None:f[tag]=(val,) if FORMATS[tag][0]=='A' else val
 for tag,key in [('GAD','germline_AD'),('SAD','somatic_AD')]:
  val=o.get(key,'.').split(',')
  if len(val)==2 and all(integer(x) is not None for x in val):f[tag]=tuple(integer(x) for x in val)
 for annotation in annotations:
  for tag,val in annotation.items():
   if tag.startswith('ANN_') and clean(val):
    previous=r.info.get(tag)
    if not previous:r.info[tag]=clean(val)
    elif previous!=clean(val):r.info[tag]='|'.join(sorted(set(previous.split('|')+[clean(val)])))
   elif tag=='STLOD':
    vals=val if isinstance(val,tuple) else (val,)
    if len(vals)==1 and vals[0] is not None and math.isfinite(float(vals[0])):f[tag]=(float(vals[0]),)
 if not r.info.get('ANN_GENE') and clean(o.get('gene',i.get('gene'))):r.info['ANN_GENE']=clean(o.get('gene',i.get('gene')))
 return category

def validate(path,sample,reference):
 counts=Counter();previous=None;seen=set()
 with pysam.VariantFile(path) as v,pysam.FastaFile(reference) as fa:
  if list(v.header.samples)!=[sample]:raise ValueError('Final VCF has wrong sample columns')
  order={x:n for n,x in enumerate(v.header.contigs)}
  for r in v:
   k=(order[r.contig],r.pos)
   if previous is not None and k<previous:raise ValueError('Final VCF is not coordinate sorted')
   if k!=previous:seen=set();previous=k
   allele=(r.ref,r.alts)
   if allele in seen:raise ValueError('Duplicate normalized allele: '+str(r))
   seen.add(allele)
   if len(r.alts or ())!=1:raise ValueError('Final VCF is not biallelic')
   if fa.fetch(r.contig,r.start,r.start+len(r.ref)).upper()!=r.ref:raise ValueError('Final REF mismatch')
   f=r.samples[sample];gt=f.get('GT')
   if gt is None or len(gt)!=2 or any(x not in (0,1,None) for x in gt):raise ValueError('Invalid GT')
   if f['EVCLASS']=='unconfirmed' and any(x is not None for x in gt):raise ValueError('Unconfirmed record must retain missing primary GT')
   counts[f['EVCLASS']]+=1;counts['records']+=1
   if None in gt:counts['missing_primary_GT']+=1
   if 'NEAND_MARKER' in r.info:counts['neanderthal_marker_records']+=1
   if f.get('ZYG')!=zygosity(gt,r.ref,r.alts[0],r.contig):raise ValueError('Zygosity does not match selected GT')
   if f.get('AFFSTAT')!='not_established':raise ValueError('Genetic evidence must not establish affected status')
   counts['zygosity_'+f['ZYG']]+=1;counts['affected_status_not_established']+=1
   if f.get('CARRIER','unknown') not in {'unknown','not_applicable'}:counts['carrier_context_reviewed']+=1
   if f.get('CARRIER')=='possible_recessive_carrier':counts['possible_recessive_carriers']+=1
   if 'CLNSIG' in r.info or 'ALLELEID' in r.info:counts['clinvar_matched']+=1
  for chrom in v.header.contigs:
   if v.header.contigs[chrom].length!=fa.get_reference_length(chrom):raise ValueError('Reference dictionary mismatch')
 return dict(counts)

def run(a,c):
 out=Path(a.sample+'.cohort_vcf');out.mkdir(exist_ok=True)
 with pysam.FastaFile(c['reference']) as fa:contigs=list(zip(fa.references,fa.lengths))
 order={ch:n for n,(ch,_) in enumerate(contigs)}
 priorities={}
 special={};special_annotations={}
 for directory in getattr(a,'special',[]):
  p=Path(directory)/(a.sample+'.neanderthal.annotated.tsv.gz')
  if p.is_file():special.update(((r['chrom'],int(r['pos']),r['ref'],r['alt']),r) for r in rows(p))
  v=Path(directory)/(a.sample+'.neanderthal.annotated.vcf.gz')
  if v.is_file():special_annotations.update(((r['chrom'],r['pos'],r['ref'],r['alt']),r) for r in annotation_rows(str(v),a.sample))
 for directory in a.interpretation:
  p=Path(directory)/(a.sample+'.variant_priority.tsv.gz')
  priorities.update((':'.join(r['variant'].split(':')[:2]+[x.upper() for x in r['variant'].split(':')[2:]]),r) for r in rows(p))
 header=pysam.VariantHeader();header.add_meta('source',value=VERSION);header.add_meta('reference',value=c['reference'])
 reference_sha256=sha256(c['reference'])
 header.add_meta('LPWGSReferenceSequenceSHA256',value=reference_sha256)
 header.add_meta('LPWGSReferenceDictionarySHA256',value=sha256(c['reference']+'.fai'))
 header.add_meta('LPWGSClinVarSHA256',value=sha256(a.clinvar));header.add_meta('LPWGSSchema',value=VERSION)
 for ch,length in contigs:header.contigs.add(ch,length=length)
 for tag,(n,t,description) in FORMATS.items():header.formats.add(tag,n,t,description)
 for tag in ANNOTATIONS.values():header.info.add(tag,1,'String','Gene/transcript annotation; percent-encoded; exact allele from available caller annotation')
 header.add_meta('FILTER',items=[('ID','Unconfirmed'),('Description','Sample evidence is unresolved; see FORMAT fields')])
 header.add_meta('FILTER',items=[('ID','SomaticCandidate'),('Description','Unpaired somatic candidate; acquired origin not established')])
 header.info.add('ANN_EFFECT',1,'String','Available molecular effect when no separate exonic consequence applies; percent-encoded')
 header.info.add('NEAND_MARKER',0,'Flag','Exact allele is in the configured published Neanderthal-associated marker panel; not proof of archaic origin for this individual')
 header.add_sample(a.sample)
 observed=next(p for p in a.evidence_tables if Path(p).name==a.sample+'.all_variants.tsv.gz')
 def by_chr(path):
  name=Path(path).name;m=re.search(r'\.chr(\d+)\.',name)
  return int(m[1]) if m else 999
 imp=imputed_rows(sorted(a.imputed_tables,key=by_chr),sorted(a.imputed,key=by_chr),a.sample,c)
 streams=[groups(rows(observed),'observed',order),groups(imp,'imputed',order),groups(annotation_rows(a.germline,a.sample),'annotation',order),groups(annotation_rows(a.somatic,a.sample),'annotation',order)]
 archaic=[dict(r,chrom=k[0],pos=k[1],ref=k[2],alt=k[3],genotype=r['genotype'],evidence_status='supported_imputed',called_GP=r['GP'],INFO=r['INFO'],DS=r['alt_dosage'],reason='carried_archaic_marker_genotype') for k,r in sorted(special.items(),key=lambda x:(order[x[0][0]],x[0][1],x[0][2:]))]
 if archaic:streams.append(groups(iter(archaic),'archaic',order))
 raw=out/'union.bcf';counts=Counter()
 with pysam.VariantFile(raw,'wb',header=header) as v:
  for _,group in itertools.groupby(heapq.merge(*streams,key=lambda r:r[0]),key=lambda r:r[0]):
   alleles={}
   for _,kind,rs in group:
    for row in rs:
     k=(row['chrom'],int(row['pos']),row['ref'],row['alt']);parts=alleles.setdefault(k,{'annotation':[]})
     if kind=='annotation':parts[kind].append(row)
     elif kind in parts:
      if parts[kind]!=row:raise ValueError('Conflicting duplicate allele in '+kind+': '+str(k))
     else:parts[kind]=row
   for (ch,pos,ref,alt),parts in sorted(alleles.items()):
    if not any(k in parts for k in ['observed','imputed','archaic']):continue
    if not re.fullmatch('[ACGTN]+',ref) or not re.fullmatch('[ACGTN]+',alt):raise ValueError(f'Unsupported nonsequence allele: {ch}:{pos}:{ref}:{alt}')
    rec=v.new_record(contig=ch,start=pos-1,alleles=(ref,alt))
    identifiers={v for row in [*parts['annotation'],parts.get('imputed',{})] for v in re.findall(r'rs[0-9]+',row.get('rsid',''))}
    if identifiers:rec.id=';'.join(sorted(identifiers))
    if (ch,pos,ref,alt) in special_annotations:parts['annotation'].append(special_annotations[(ch,pos,ref,alt)])
    category=fill(rec,parts.get('observed'),parts.get('imputed',parts.get('archaic')),parts['annotation'],priorities.get(f'{ch}:{pos}:{ref}:{alt}'),a.sample)
    marker=special.get((ch,pos,ref,alt))
    if marker:
     rec.info['NEAND_MARKER']=True;rec.samples[a.sample]['NDC']=int(marker['derived_copies'])
     for tag,key in [('ANN_GENE','gene'),('ANN_EFFECT','consequence'),('ANN_AA','protein_change')]:
      if not rec.info.get(tag) and clean(marker.get(key)):rec.info[tag]=clean(marker[key])
    v.write(rec);counts[category]+=1
    if sum(counts.values())%500000==0:print('VCF records written:',sum(counts.values()),flush=True)
 bc=c['tools']['bcftools'];norm=out/'normalized.bcf';upper=out/'uppercase.bcf';sort=out/'sorted.bcf';final=out/(a.sample+'.annotated.vcf.gz')
 subprocess.run([bc,'norm','-f',c['reference'],'-c','e','-m','-any','-Ob','-o',str(norm),str(raw)],check=True)
 case_changes=uppercase_vcf(norm,upper)
 subprocess.run([bc,'sort','-m','1G','-Ob','-o',str(sort),str(upper)],check=True)
 subprocess.run([bc,'index','-f',str(sort)],check=True)
 with pysam.VariantFile(a.clinvar) as cv:columns=['INFO/'+x for x in cv.header.info if x.startswith(('CLN','ONC','SCI')) or x in {'ALLELEID','GENEINFO','MC','RS','ORIGIN'}]
 subprocess.run([bc,'annotate','--pair-logic','exact','-a',a.clinvar,'-c',','.join(columns),'-Oz','--threads',str(a.threads),'-o',str(final),str(sort)],check=True)
 subprocess.run([bc,'index','-f','-t',str(final)],check=True)
 validated=validate(final,a.sample,c['reference'])
 if validated['records']!=sum(counts.values()):raise ValueError('Record count changed during normalization/annotation')
 with (out/(a.sample+'.bcftools.stats.txt')).open('w') as f:subprocess.run([bc,'stats',str(final)],stdout=f,check=True)
 summary={'sample':a.sample,'schema':VERSION,'reference_sequence_sha256':reference_sha256,'runtime_signature':runtime_signature(c),'reference_dictionary_sha256':sha256(c['reference']+'.fai'),'clinvar_sha256':sha256(a.clinvar),'vcf_sha256':sha256(final),'counts':validated,'normalization_case_changes':case_changes,'validation':'all records parsed; REF checked against FASTA; biallelic; coordinate sorted; unique normalized allele; one sample; primary GT uncertainty retained; index generated',
          'scope':'Union of sequence-caller alleles, imputed nonreference alleles and carried archaic-marker sites (including 0/0 when the derived allele is REF). Sparse site VCF: absent positions are unknown, not reference genotypes. All sample categories and filters are FORMAT fields.'}
 dump(out/(a.sample+'.cohort_vcf.json'),summary)
 import shutil
 shutil.copyfile(Path(__file__).with_name('merge_cohort_vcfs.py'),out/'merge_cohort_vcfs.py')
 (out/'README.txt').write_text('One annotated, normalized GRCh38 sample VCF and tabix index.\n'+summary['scope']+'\nZYG describes selected GT; unresolved primary calls stay unresolved. RZYG is the reported candidate genotype, including low-support calls. GZYG/SZYG/IZYG describe the original source genotypes. ZYG_SRC/ZYG_EVID identify source and limitations. AFFSTAT=not_established means the pipeline does not determine clinical affected status; it never imports affected status from ClinVar patients.\nCARRIER/CARGT/CARWHY/INHERIT retain sample-specific carrier context, including uncertainty. Unknown does not mean not a carrier. NEAND_MARKER marks published archaic-associated loci; NDC retains this sample derived-copy count. ANN_* fields contain available exact-allele ANNOVAR annotations. Mitochondrial haploid calls remain in the dedicated mitochondrial annotated VCF.\nSomatic variants retain gene/transcript annotations, exact-allele ClinVar annotations, original Mutect2 filters, VAF and TLOD. Somatic origin is not proven without matched-normal validation.\nPrimary GT: supported read-based germline call, otherwise passing imputation, otherwise supported somatic candidate; unresolved calls use ./., with original caller/imputed genotypes retained in GGT/SGT/IGT.\nFT=PASS means pipeline genotype support, not pathogenicity. Site FILTER and QUAL are unset; read and imputation quality are sample-specific fields.\nSUP/PRI/CSTAT are available for the ClinVar review scope; missing values elsewhere do not mean zero. Percent-encoded annotation strings can be URL-decoded.\nMerge with unique sample names and the same reference, annotation release and schema:\n  python3 merge_cohort_vcfs.py --bcftools /path/to/bcftools --output cohort.vcf.gz sample1.annotated.vcf.gz sample2.annotated.vcf.gz\nNever use --missing-to-ref for these variant-only files. A cohort allele-frequency analysis needs joint site genotyping/callability to distinguish reference calls from absent records.\n')
 for p in [raw,norm,upper,sort,Path(str(sort)+'.csi')]:p.unlink()
 print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for key in ['config','sample','germline','somatic','clinvar']:p.add_argument('--'+key,required=True)
 for key in ['evidence-tables','imputed-tables','imputed','interpretation','special']:p.add_argument('--'+key,nargs='*',default=[])
 p.add_argument('--threads',type=int,default=2);a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
