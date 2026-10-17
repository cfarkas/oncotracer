#!/usr/bin/env python3
"""Annotate every imputed autosomal SNP with exact-allele ClinVar assertions."""
import argparse
from collections import Counter
import csv
import gzip
import itertools
import json
import math
from pathlib import Path
import subprocess

import pysam
from extension_common import Excel,dump,flat,page,posterior,sha256,unique_inputs,runtime_signature
from variant_evidence import pathogenic_label,review_stars

FIELDS=['chrom','pos','ref','alt','rsid','genotype','GT','GP','called_GP','DS','INFO','filter',
        'genotype_state','evidence_status','clinvar_matched','clinvar_allele_id','gene',
        'consequence','clinvar_reported_origin','clinvar_classification','clinvar_review','review_stars','condition','condition_ids',
        'clinvar_url','pathogenic_label','clinical_confirmation','explanation','clinvar_info_json']
NOTE=('All imputed records are annotated without a quality prefilter. The full chromosome BCFs include reference and missing genotypes. '
      'A ClinVar match describes an allele, not an established finding in this sample. Imputed alternate genotypes are research evidence; '
      'pathogenic labels require independent clinical confirmation. This covers the imputed autosomal SNPs, not indels, structural variants or sex chromosomes.')


def make_row(rec,sample,c,ambiguous=False):
    info=dict(rec.info);fmt=rec.samples[sample];gt=fmt.get('GT') or ()
    state='missing_genotype' if len(gt)!=2 or any(x is None for x in gt) else ('nonreference' if any(x>0 for x in gt) else 'reference')
    call=posterior(rec,sample,c)
    status='ambiguous_site' if ambiguous else ('supported_imputed' if call['status']=='imputed' else call['status'])
    sig=flat(info.get('CLNSIG'));review=flat(info.get('CLNREVSTAT'));aid=flat(info.get('ALLELEID'))
    matched=bool(info.get('CV_MATCH'))
    p_label=state=='nonreference' and pathogenic_label(sig)
    explanation=('ClinVar allele assertion; '+ ('the imputed genotype contains the alternate allele. ' if state=='nonreference' else 'the alternate allele is not established in this genotype. ')
        +'Genotype evidence: '+status+'. This does not establish a diagnosis, inheritance, penetrance or somatic origin.')
    if p_label:explanation+=' The pathogenic/likely pathogenic assertion applies to the listed condition; review inheritance, review status and independent confirmatory testing.'
    separator='|' if fmt.phased else '/'
    return {'chrom':rec.contig,'pos':rec.pos,'ref':rec.ref,'alt':','.join(rec.alts or []),'rsid':rec.id or '.',
        'GT':separator.join('.' if x is None else str(x) for x in gt),
        'genotype':separator.join('.' if x is None else rec.alleles[x] for x in gt),
        'GP':flat(fmt.get('GP')),'called_GP':call.get('GP','.'),'DS':flat(fmt.get('DS')),'INFO':call.get('INFO','.'),
        'filter':';'.join(rec.filter.keys()) or '.', 'genotype_state':state,'evidence_status':status,
        'clinvar_matched':'yes' if matched else 'no','clinvar_allele_id':aid,'gene':flat(info.get('GENEINFO')),
        'consequence':flat(info.get('MC')),'clinvar_reported_origin':flat(info.get('ORIGIN')),'clinvar_classification':sig,'clinvar_review':review,'review_stars':review_stars(review),
        'condition':flat(info.get('CLNDN')),'condition_ids':flat(info.get('CLNDISDB')),
        'clinvar_url':f'https://www.ncbi.nlm.nih.gov/clinvar/?term={aid}[AlleleID]' if aid!='.' else '.',
        'pathogenic_label':'yes' if p_label else 'no','clinical_confirmation':'required' if p_label else 'not_confirmed',
        'explanation':explanation,'clinvar_info_json':json.dumps({k:info[k] for k in info if k.startswith(('CLN','ONC','SCI')) or k in {'ALLELEID','GENEINFO','MC','RS','DBVARID','ORIGIN','SSR','AF_ESP','AF_EXAC','AF_TGP'}},sort_keys=True)}


def annotate(a,c):
    chromosomes=unique_inputs([a.imputed],a.sample)
    if list(chromosomes)!=[a.chrom]:raise ValueError('Imputed chromosome identity differs from task tag')
    bc=c['tools']['bcftools'];prefix=f'{a.sample}.{a.chrom}'
    with pysam.VariantFile(a.clinvar) as cv:
        columns=[f'INFO/{k}' for k in cv.header.info if k.startswith(('CLN','ONC','SCI')) or k in {'ALLELEID','GENEINFO','MC','RS','DBVARID','ORIGIN','SSR','AF_ESP','AF_EXAC','AF_TGP'}]
        date=[line.split('=',1)[1] for line in str(cv.header).splitlines() if line.startswith('##fileDate=')]
        if 'INFO/ALLELEID' not in columns and 'INFO/CLNSIG' not in columns:raise ValueError('No ClinVar allele/classification INFO fields')
    target=f'{prefix}.clinvar.bcf'
    subprocess.run([bc,'annotate','--pair-logic','exact','-a',a.clinvar,'-c',','.join(columns),'-m','+CV_MATCH',
                    '--threads',str(max(0,a.threads-1)),'-Ob','-o',target,a.imputed],check=True)
    subprocess.run([bc,'index','-f',target],check=True)
    handles={k:gzip.open(f'{prefix}.{k}.tsv.gz','wt',compresslevel=1,newline='') for k in ['all_nonreference','clinvar_all_sites','clinvar_nonreference','pathogenic_labels']}
    writers={k:csv.DictWriter(h,fieldnames=FIELDS,delimiter='\t') for k,h in handles.items()}
    for w in writers.values():w.writeheader()
    counts=Counter();evidence=Counter()
    with pysam.FastaFile(c['reference']) as fasta, pysam.VariantFile(target) as records:
        try:
            for (chrom,pos),group in itertools.groupby(records,key=lambda r:(r.contig,r.pos)):
                variants=list(group)
                ambiguous=len(variants)>1
                for rec in variants:
                    if chrom!=a.chrom:raise ValueError(f'Unexpected chromosome {chrom} in {a.chrom} input')
                    if len(rec.alleles)!=2 or any(len(x)!=1 or x not in 'ACGT' for x in rec.alleles):raise ValueError('Expected biallelic imputed SNPs')
                    if fasta.fetch(chrom,pos-1,pos).upper()!=rec.ref:raise ValueError(f'GRCh38 REF mismatch {chrom}:{pos}')
                    row=make_row(rec,a.sample,c,ambiguous);counts['all_imputed_records']+=1;counts[row['genotype_state']]+=1
                    evidence[row['evidence_status']]+=1
                    if row['genotype_state']=='nonreference':writers['all_nonreference'].writerow(row)
                    if row['clinvar_matched']=='yes':
                        writers['clinvar_all_sites'].writerow(row);counts['clinvar_all_sites']+=1
                        if row['genotype_state']=='nonreference':
                            writers['clinvar_nonreference'].writerow(row);counts['clinvar_nonreference']+=1
                    if row['pathogenic_label']=='yes':writers['pathogenic_labels'].writerow(row);counts['pathogenic_labels']+=1
        finally:
            for h in handles.values():h.close()
    expected=int(subprocess.check_output([bc,'index','--nrecords',a.imputed],text=True).strip())
    if counts['all_imputed_records']!=expected:raise ValueError('Imputed annotation record count changed')
    dump(f'{prefix}.imputed_clinvar.json',{'sample':a.sample,'chrom':a.chrom,'counts':dict(counts),'evidence':dict(evidence),
        'source_imputed':str(Path(a.imputed).resolve()),'clinvar':str(Path(a.clinvar).resolve()),
        'clinvar_sha256':sha256(a.clinvar),'clinvar_file_date':date,'annotated_fields':columns,
        'match':'normalized exact CHROM/POS/REF/ALT; GRCh38 REF checked for every record',
        'test_mode':c['test_mode'],'scope':NOTE,'thresholds':{k:c[k] for k in ['imputation_min_gp','imputation_min_info']}})


def report(a,c):
    summaries=[json.loads(Path(x).read_text()) for x in a.summaries]
    if any(x['sample']!=a.sample for x in summaries):raise ValueError('Mixed samples in imputed ClinVar report')
    if sorted(x['chrom'] for x in summaries)!=sorted(c['glimpse_chromosomes']):raise ValueError('Imputed ClinVar chromosome set incomplete or duplicated')
    if len({x['clinvar_sha256'] for x in summaries})!=1:raise ValueError('Mixed ClinVar snapshots')
    counts=Counter();evidence=Counter()
    for d in summaries:counts.update(d['counts']);evidence.update(d['evidence'])
    summary={'sample':a.sample,'runtime_signature':runtime_signature(c),'test_mode':c['test_mode'],'counts':dict(counts),'evidence':dict(evidence),
        'chromosomes':[x['chrom'] for x in summaries],'clinvar_file_date':summaries[0]['clinvar_file_date'],
        'clinvar_sha256':summaries[0]['clinvar_sha256'],'scope':NOTE}
    book=Excel(f'{a.sample}.imputed_clinvar.xlsx',[[a.sample+' · complete imputation ClinVar review'],
        ['Scope',NOTE],['Pathogenic_LP','Only imputed nonreference genotypes carrying a pathogenic/likely pathogenic ClinVar label. Includes uncertain genotypes; these are not confirmed findings.'],
        ['Reference_or_missing','ClinVar annotations where an alternate genotype is not established. These are not positive disease findings.'],
        ['Genotype evidence','supported_imputed means passing GP, dosage consistency and INFO when supplied. Multiple alleles at one position are flagged ambiguous.'],
        ['ClinVar source date',','.join(summary['clinvar_file_date'])],['Test mode',str(c['test_mode'])]])
    for name in ['Imputed_nonreference','Pathogenic_LP','Reference_or_missing']:book.create(name,FIELDS)
    totals=Counter()
    for source in sorted(a.tables):
        with gzip.open(source,'rt') as handle:
            for row in csv.DictReader(handle,delimiter='\t'):
                for k in ['pos','called_GP','review_stars','DS','INFO']:
                    if row[k]!='.':
                        try:
                            number=float(row[k]) if k not in ['pos','review_stars'] else int(row[k])
                            if math.isfinite(number):row[k]=number
                        except ValueError:pass
                name='Imputed_nonreference' if row['genotype_state']=='nonreference' else 'Reference_or_missing'
                book.add(name,row);totals['clinvar_all_sites']+=1
                if name=='Imputed_nonreference':totals['clinvar_nonreference']+=1
                if row['pathogenic_label']=='yes':book.add('Pathogenic_LP',row);totals['pathogenic_labels']+=1
    for key in ['clinvar_all_sites','clinvar_nonreference','pathogenic_labels']:
        if totals[key]!=counts[key]:raise ValueError(f'Imputed ClinVar export mismatch: {key}')
    book.close();dump(f'{a.sample}.imputed_clinvar.json',summary)
    page(f'{a.sample}.imputed_clinvar.html',a.sample+' · ClinVar across all imputed SNPs',NOTE,
        [{'count':k,'records':v} for k,v in counts.items()],['count','records'],
        [('Excel review',f'{a.sample}.imputed_clinvar.xlsx')])


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='stage',required=True)
    for stage in ['annotate','report']:
        p=sub.add_parser(stage);p.add_argument('--config',required=True);p.add_argument('--sample',required=True)
        if stage=='annotate':
            p.add_argument('--chrom',required=True);p.add_argument('--imputed',required=True);p.add_argument('--clinvar',required=True);p.add_argument('--threads',type=int,default=1)
        else:
            p.add_argument('--summaries',nargs='+',required=True);p.add_argument('--tables',nargs='+',required=True)
    a=parser.parse_args();c=json.loads(Path(a.config).read_text());globals()[a.stage](a,c)
if __name__=='__main__':main()
