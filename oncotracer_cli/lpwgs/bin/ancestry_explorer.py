#!/usr/bin/env python3
"""Broad reference-mixture projection, maternal lineage, and archaic-marker evidence."""
import argparse
import csv
import gzip
import json
import math
import re
import subprocess
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import pysam
from scipy.optimize import minimize
from extension_common import dump,posterior,runtime_signature,table,unique_inputs

COMPONENTS=['European','East Asian','American','African','South Asian','Oceanian']
LABELS=['European reference','East Asian reference','Indigenous American reference','African reference','South Asian reference','Oceanian reference']


def fit_mixture(freq,dosage):
    """Constrained fixed-frequency binomial projection; dosage counts the explicitly defined model frequency allele."""
    freq=np.asarray(freq,dtype=float);dosage=np.asarray(dosage,dtype=float)
    if freq.ndim!=2 or len(freq)!=len(dosage) or not len(freq):raise ValueError('Empty/incompatible ancestry input')
    if not np.isfinite(freq).all() or not np.isfinite(dosage).all() or np.any((freq<0)|(freq>1)) or np.any((dosage<0)|(dosage>2)):raise ValueError('Invalid ancestry frequencies/dosages')
    def fun(q):
        x=np.clip(freq@q,1e-8,1-1e-8)
        return -np.mean(dosage*np.log(x)+(2-dosage)*np.log1p(-x))
    def jac(q):
        x=np.clip(freq@q,1e-8,1-1e-8)
        return -(freq.T@(dosage/x-(2-dosage)/(1-x)))/len(x)
    n=freq.shape[1]
    result=minimize(fun,np.full(n,1/n),jac=jac,bounds=[(0,1)]*n,
        constraints={'type':'eq','fun':lambda q:q.sum()-1,'jac':lambda q:np.ones(n)},
        method='SLSQP',options={'ftol':1e-11,'maxiter':500})
    if not result.success:raise ValueError('Ancestry optimization failed: '+result.message)
    q=np.clip(result.x,0,1);q/=q.sum()
    return q


def project(rows,total):
    counts=Counter(r['chrom'] for r in rows)
    base={'matched_markers':len(rows),'reference_markers':total,'chromosome_counts':dict(counts),
          'genotype_gp_min':.95,'site_info_min':.8,'components':[],
          'method':'Fixed K=6 reference-frequency binomial projection of GLIMPSE2 dosages; no training on this sample.',
          'scope':'Exploratory broad reference composition. Not a validated consumer ancestry test, ethnicity, nationality, family tree or local-ancestry chromosome painting. Reference choice and imputation can bias estimates.',
          'interval_scope':'95% leave-one-chromosome-out jackknife intervals describe chromosome variation only; they omit reference/model and imputation uncertainty.'}
    if len(rows)<3000 or len(counts)<18:
        return {**base,'status':'insufficient_data','reason':'Need at least 3,000 matched markers across 18 autosomes; missing evidence is not zero ancestry.'}
    freq=np.array([r['frequencies'] for r in rows]);dose=np.array([r['model_allele_dosage'] for r in rows]);chrom=np.array([r['chrom'] for r in rows])
    q=fit_mixture(freq,dose);replicas=np.array([fit_mixture(freq[chrom!=ch],dose[chrom!=ch]) for ch in sorted(counts)])
    m=len(replicas);se=np.sqrt((m-1)/m*np.sum((replicas-replicas.mean(axis=0))**2,axis=0))
    base.update(status='exploratory_estimate',components=[{'component':label,'fraction':float(v),
        'lower':float(max(0,v-1.96*s)),'upper':float(min(1,v+1.96*s))} for label,v,s in zip(LABELS,q,se)],
        jackknife={ch:[float(v) for v in values] for ch,values in zip(sorted(counts),replicas)})
    return base


def load_markers(files,reference):
    targets=defaultdict(dict);counts=Counter();fasta=pysam.FastaFile(reference)
    contigs={x.removeprefix('chr'):x for x in fasta.references}
    with open(files['mixture']) as f:
        for r in csv.DictReader(f,delimiter='\t'):
            ch=contigs.get(r['chr']);pos=int(r['pos']);a1,a2=r['a1'],r['a2']
            if not ch or len(a1)!=1 or len(a2)!=1 or {a1,a2} in [{'A','T'},{'C','G'}]:continue
            ref=fasta.fetch(ch,pos-1,pos).upper()
            if ref not in {a1,a2}:raise ValueError(f'Ancestry model/FASTA allele mismatch: {ch}:{pos}')
            alt=a2 if ref==a1 else a1;key=(pos,ref,alt)
            if 'mixture' in targets[ch].get(key,{}):raise ValueError('Duplicate ancestry model marker')
            targets[ch].setdefault(key,{})['mixture']={'frequency_allele':a2,'frequencies':[float(r[k]) for k in COMPONENTS],'rsid':r['snp_id']}
            counts['mixture']+=1
    with open(files['archaic']) as f:
        for r in csv.DictReader(f,delimiter='\t'):
            # Only SNPs with a derived ALT shared with a sequenced Neanderthal.
            if r['derived']!='alternative' or not ({'Altai','Vindija'} & set(r['foundin'].split('_'))):continue
            if len(r['ref'])!=1 or len(r['alt'])!=1:continue
            ch=contigs.get(r['chrom'].removeprefix('chr'));pos=int(r['pos'])
            if not ch:continue
            key=(pos,r['ref'],r['alt'])
            if 'archaic' in targets[ch].get(key,{}):continue
            if fasta.fetch(ch,pos-1,pos).upper()!=r['ref']:raise ValueError(f'Archaic catalog/FASTA mismatch: {ch}:{pos}')
            targets[ch].setdefault(key,{})['archaic']={'foundin':r['foundin'],'derived_allele':r['alt']}
            counts['archaic']+=1
    fasta.close();return targets,counts


def nuclear(config,sample,paths,files,folder):
    targets,counts=load_markers(files,config['reference'])
    inputs=unique_inputs(paths,sample) if paths else {}
    audits=[];mixture=[];archaic=[];excluded=Counter();seen=set()
    bc=config['tools']['bcftools'];settings={**config,'imputation_min_gp':.95,'imputation_min_info':.8}
    for chrom,variants in sorted(targets.items()):
        if chrom not in inputs:continue
        bed=folder/(chrom+'.markers.bed');bed.write_text(''.join(f'{chrom}\t{p-1}\t{p}\n' for p in sorted({k[0] for k in variants})))
        subset=folder/(chrom+'.markers.bcf')
        subprocess.run([bc,'view','--regions-overlap','0','-R',str(bed),'-Ob','-o',str(subset),inputs[chrom]],check=True)
        subprocess.run([bc,'index','-f',str(subset)],check=True)
        records=defaultdict(list)
        with pysam.VariantFile(subset) as vcf:
            if list(vcf.header.samples)!=[sample]:raise ValueError('Ancestry input sample mismatch')
            for rec in vcf:
                records[rec.pos].append(rec.copy())
        for pos,recs in records.items():
            # Multiallelic/split sites lack a joint genotype posterior; withhold.
            if len(recs)!=1:excluded['duplicate_or_multiallelic_position']+=1;continue
            rec=recs[0];key=(rec.pos,rec.ref,(rec.alts or (None,))[0]);target=variants.get(key)
            if not target:excluded['allele_mismatch']+=1;continue
            g=posterior(rec,sample,settings)
            if g['status']!='imputed' or g['INFO']=='.':excluded[g['status'] if g['status']!='imputed' else 'missing_INFO']+=1;continue
            token=(chrom,*key)
            if token in seen:raise ValueError('Duplicate ancestry allele input')
            seen.add(token)
            row={'chrom':chrom,'position':rec.pos,'ref':rec.ref,'alt':rec.alts[0],
                'GT':g['genotype'],'GP':g['GP'],'INFO':g['INFO'],'alt_dosage':g['DS']}
            if 'mixture' in target:
                item=target['mixture'];dose=g['DS'] if item['frequency_allele']==rec.alts[0] else 2-g['DS']
                mixture.append({**row,**item,'model_allele_dosage':dose})
            if 'archaic' in target:
                archaic.append({**row,**target['archaic'],'derived_copies':sum(a==rec.alts[0] for a in g['alleles'])})
        subset.unlink();Path(str(subset)+'.csi').unlink();bed.unlink()
    result=project(mixture,counts['mixture'])
    result['exclusions']=dict(excluded)
    table(folder/(sample+'.ancestry_markers.tsv.gz'),['chrom','position','ref','alt','rsid','GT','GP','INFO','frequency_allele','model_allele_dosage'],mixture)
    table(folder/(sample+'.neanderthal_markers.tsv.gz'),['chrom','position','ref','alt','GT','GP','INFO','derived_allele','derived_copies','alt_dosage','foundin'],archaic)
    archaic_result={'status':'marker_evidence' if archaic else 'insufficient_data','catalog_markers':counts['archaic'],
        'callable_markers':len(archaic),'carried_markers':sum(r['derived_copies']>0 for r in archaic),
        'derived_allele_copies':sum(r['derived_copies'] for r in archaic),'expected_derived_copies':sum(r['alt_dosage'] for r in archaic),
        'genome_percentage':None,'population_percentile':None,'genotype_gp_min':.95,'site_info_min':.8,
        'scope':'Alleles shared with Altai and/or Vindija Neanderthals at published archaic-associated GRCh38 SNPs. This is a marker tally, not an estimate of the fraction of this genome inherited from Neanderthals. Markers are correlated; some are shared with Denisovans or modern humans. The Icelandic discovery panel and imputation reference introduce ascertainment bias.',
        'source':'https://www.nature.com/articles/s41586-020-2225-9','source_dataset':'Supplementary dataset 2; derived=alternative; foundin includes Altai or Vindija.'}
    return result,archaic_result


def callable_ranges(positions):
    ranges=[]
    for p in sorted(set(positions)):
        if ranges and p==ranges[-1][1]+1:ranges[-1][1]=p
        else:ranges.append([p,p])
    return ';'.join(str(a) if a==b else f'{a}-{b}' for a,b in ranges)


def maternal_background(haplogroup):
    if haplogroup.startswith('B2i2'):
        return {'text':'The B2i2 branch was documented in maternal lineages from Chile and Argentina in a complete-mitogenome study. This gives this estimated lineage a broad Southern Cone connection. It does not establish a recent birthplace, nationality or membership of a particular Indigenous community.',
            'source':'https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0051311','reviewed':'2026-10-11'}
    if haplogroup.startswith('B2'):
        return {'text':'B2 is one of the mitochondrial founding lineages described across Indigenous populations of the Americas. This is broad phylogeographic context for one maternal branch; it does not identify a recent country or community.',
            'source':'https://pmc.ncbi.nlm.nih.gov/articles/PMC2258150/','reviewed':'2026-10-11'}
    return {'text':'The lineage is classified against the mitochondrial tree. No separately reviewed regional-origin paragraph is available for this haplogroup in this release; a recent country of origin is not assigned.','source':'https://github.com/genepi/phylotree-rcrs-17'}


def mitochondrial(config,sample,bam,files,folder):
    with pysam.AlignmentFile(bam,'rb') as source:
        ch=next((x for x in source.references if x in {'chrM','MT','M'}),None)
        if ch is None:return {'status':'unavailable','reason':'No mitochondrial contig in alignment'}
        length=source.get_reference_length(ch)
    if length!=16569:return {'status':'unavailable','reason':'Mitochondrial reference length is not rCRS 16,569 bases'}
    fasta=pysam.FastaFile(config['reference']);ref=fasta.fetch(ch).upper();fasta.close()
    rcrs=''.join(line.strip() for line in (files['tree_dir']/'rcrs.fasta').read_text().splitlines() if not line.startswith('>')).upper()
    if ref!=rcrs:raise ValueError('Mitochondrial FASTA is not the rCRS sequence used by the selected tree')
    calls={};counts={};variants=[];mixed=0;indel_mask=set()
    with pysam.AlignmentFile(bam,'rb') as source:
        for col in source.pileup(ch,0,length,truncate=True,stepper='samtools',min_base_quality=20,min_mapping_quality=30,max_depth=10000,ignore_overlaps=True,ignore_orphans=True,compute_baq=False):
            p=col.reference_pos+1;alleles=Counter();insertions=Counter()
            for read in col.pileups:
                if read.is_refskip:continue
                a=read.alignment
                if a.is_secondary or a.is_supplementary or a.is_duplicate or a.is_qcfail or a.mapping_quality<30:continue
                if read.is_del:alleles['d']+=1;continue
                q=read.query_position
                if q is None or a.query_qualities[q]<20:continue
                base=a.query_sequence[q].upper()
                if base not in 'ACGT':continue
                alleles[base]+=1
                if read.indel>0:
                    ins=a.query_sequence[q+1:q+1+read.indel].upper()
                    if len(ins)==read.indel and all(v>=20 for v in a.query_qualities[q+1:q+1+read.indel]):insertions[ins]+=1
            dp=sum(alleles.values());counts[p]=dp
            if dp>=10 and (alleles.get('d',0)+sum(insertions.values()))/dp>=.1:
                indel_mask.update(range(max(1,p-12),min(length,p+12)+1))
            if dp<10:continue
            allele,n=alleles.most_common(1)[0]
            if n/dp<.9:mixed+=1;continue
            if ref[p-1] not in 'ACGT':continue
            calls[p]=allele
            if allele!=ref[p-1]:variants.append({'position':p,'ref':ref[p-1],'allele':allele,'depth':dp,'fraction':n/dp,'notation':str(p)+allele})
            if insertions:
                ins,n=insertions.most_common(1)[0]
                if n/dp>=.9:
                    for i,base in enumerate(ins,1):variants.append({'position':p,'ref':'-','allele':base,'depth':dp,'fraction':n/dp,'notation':f'{p}.{i}{base}'})
    # HSD indels are alignment-sensitive in mtDNA repeats. Use a conservative
    # SNP-only classification and exclude affected neighborhoods from its range.
    calls={p:a for p,a in calls.items() if p not in indel_mask and a in 'ACGT'}
    variants=[r for r in variants if r['position'] in calls and r['allele'] in 'ACGT' and r['ref']!='-']
    coverage=len(calls)/length
    table(folder/(sample+'.mitochondrial_variants.tsv'),['position','ref','allele','depth','fraction','notation'],variants)
    table(folder/(sample+'.mitochondrial_coverage.tsv'),['position','depth','callable'],({'position':p,'depth':counts.get(p,0),'callable':p in calls} for p in range(1,length+1)))
    base={'callable_bases':len(calls),'length':length,'callable_fraction':coverage,'mean_depth':sum(counts.values())/length,
          'indel_neighborhood_bases_masked':len(indel_mask),'classification_markers':'SNPs only; indel neighborhoods masked by 12 bases at >=10% indel support',
          'mixed_or_ambiguous_positions':mixed,'depth_min':10,'major_allele_fraction_min':.9,'tree':'phylotree-rcrs@17.3','haplogrep_version':'3.3.2',
          'scope':'One maternal line only, not overall ancestry. Low-depth and mixed sites are excluded. This analysis is not a clinical heteroplasmy, contamination or NUMT assay; circular-boundary and mapping artifacts can remain.'}
    if coverage<.9:return {**base,'status':'insufficient_data','reason':'Less than 90% of mtDNA passed depth and major-allele thresholds; lineage withheld.'}
    hsd=folder/(sample+'.mt.hsd');hsd.write_text(sample+'\t'+callable_ranges(calls)+'\t\t'+'\t'.join(r['notation'] for r in variants)+'\n')
    out=folder/(sample+'.haplogrep.tsv')
    subprocess.run(['java','-Xmx2G','-jar',str(files['haplogrep_jar']),'classify','--tree','phylotree-rcrs@17.3','--input',str(hsd.resolve()),'--output',str(out.resolve()),'--hits','3','--extend-report'],check=True)
    with out.open() as handle:rows=list(csv.DictReader(handle,delimiter='\t'))
    if not rows:raise ValueError('Haplogrep produced no classification')
    hit=rows[0];quality=float(hit.get('Quality','0'));quality=quality/100 if quality>1 else quality
    group=hit.get('Haplogroup','')
    if not group:raise ValueError('Haplogrep output lacks Haplogroup')
    return {**base,'status':'estimated' if quality>=.9 else 'low_quality_lineage','haplogroup':group if quality>=.9 else None,
            'candidate_haplogroup':group,'classification_quality':quality,'hits':rows,
            'geographic_context':maternal_background(group) if quality>=.9 else {'text':'Lineage quality was insufficient for a regional interpretation.'},
            'source':'https://pmc.ncbi.nlm.nih.gov/articles/PMC10320078/'}


def run(a,c):
    if a.sample!=c['sample']:raise ValueError('Sample/config mismatch')
    folder=Path(a.sample+'.ancestry_explorer');folder.mkdir(exist_ok=True)
    from ancestry_resources import prepare
    files,provenance=prepare(c)
    print('Calling mitochondrial lineage from filtered reads.',flush=True)
    mito=mitochondrial(c,a.sample,a.bam,files,folder)
    dump(folder/(a.sample+'.mitochondrial.json'),mito)
    print('Matching imputed autosomes to public population and archaic markers.',flush=True)
    admixture,archaic=nuclear(c,a.sample,a.imputed,files,folder)
    result={'sample':a.sample,'runtime_signature':runtime_signature(c),'qc':json.loads(Path(a.qc).read_text()),
            'admixture':admixture,'mitochondrial':mito,'neanderthal':archaic,
            'reference_similarity':json.loads(Path(a.ancestry).read_text()),'resources':provenance}
    from population_detail import run as regional_comparison
    result['regional_similarity']=regional_comparison(a.sample,folder,getattr(a,'references',[]),c)
    dump(folder/(a.sample+'.ancestry_explorer.json'),result)
    from ancestry_visuals import render
    render(folder,result)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['config','sample','bam','qc','ancestry']:p.add_argument('--'+key,required=True)
    p.add_argument('--imputed',nargs='*',default=[]);p.add_argument('--references',nargs='*',default=[])
    a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
