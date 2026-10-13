#!/usr/bin/env python3
"""Published GRCh38 PGS models: explicit allele matching, dosage scoring and coverage audits."""
import argparse
from collections import Counter,defaultdict
import csv
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

import pysam
from extension_common import Excel,dump,page,posterior,sha256,table,unique_inputs,runtime_signature
from glimpse_download import download

METHOD_VERSION='dosage-v1'
BASES=set('ACGT');COMPLEMENT=str.maketrans('ACGT','TGCA')
WEIGHT_FIELDS=['line','chrom','pos','rsid','effect_allele','other_allele','weight','model','issue']
AUDIT_FIELDS=[*WEIGHT_FIELDS,'status','matched_ref','matched_alt','orientation','effect_dosage','GP','INFO','contribution']
RESULT_FIELDS=['pgs_id','name','trait','category','status','raw_score','partial_score','expected_variants','scored_variants',
 'variant_coverage','absolute_weight_coverage','sample_ancestry_status','nearest_reference_label','population_interpretation',
 'development_ancestry','evaluation_ancestry','publication_doi','catalog_url','interpretation']
INTERPRETATION=('Scores are weighted sums of matched imputed allele dosages, in each publication\'s score scale. '
 'They are not predicted phenotype measurements, disease probabilities or diagnoses. Scores from different models are not directly comparable. '
 'Missing, ambiguous or unreliable variants are excluded and quantified; they are never silently counted as reference. '
 'No personal percentile, risk category or phenotype value is assigned without suitable external calibration. '
 'Sex, age and disease-context applicability must also be reviewed.')


def load_manifest(path):
    path=Path(path);data=json.loads(path.read_text())
    if data.get('genome_build')!='GRCh38' or not data.get('models'):raise ValueError('PGS manifest requires GRCh38 and nonempty models')
    seen=set()
    for model in data['models']:
        id=model.get('id','')
        if not re.fullmatch(r'PGS\d{6}',id) or id in seen:raise ValueError('PGS IDs must be unique PGS###### values')
        seen.add(id)
        if not isinstance(model.get('variants_number'),int) or model['variants_number']<1:raise ValueError(f'Missing expected variant count: {id}')
        if model.get('scoring_file'):
            source=Path(model['scoring_file']);source=source if source.is_absolute() else path.parent/source
            if not source.is_file():raise ValueError(f'Missing scoring file: {source}')
            model['scoring_file']=str(source.resolve())
        elif model.get('scoring_url')!=f'https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{id}/ScoringFiles/Harmonized/{id}_hmPOS_GRCh38.txt.gz':
            raise ValueError(f'Expected official harmonized GRCh38 scoring URL for {id}; use scoring_file for custom/local input')
    return data


def score_rows(path):
    with gzip.open(path,'rt') as handle:
        header={}
        for line in handle:
            if line.startswith('#'):
                if '=' in line and not line.startswith('##'):
                    key,value=line.lstrip('#').rstrip('\n').split('=',1);header[key]=value
            else:
                fields=line.rstrip('\n').split('\t');break
        else:raise ValueError('Empty PGS scoring file')
        if header.get('HmPOS_build')!='GRCh38':raise ValueError('PGS scoring file is not harmonized to GRCh38')
        required={'hm_chr','hm_pos','effect_allele','effect_weight'}
        if not required.issubset(fields):raise ValueError('Missing harmonized PGS columns')
        yield header
        for n,row in enumerate(csv.DictReader(handle,fieldnames=fields,delimiter='\t'),1):
            if None in row or any(v is None for v in row.values()):raise ValueError(f'Malformed PGS row {n}')
            yield n,row


def normalize_row(number,row):
    chrom=row.get('hm_chr','').removeprefix('chr');pos=row.get('hm_pos','')
    ea=row.get('effect_allele','').upper();oa=(row.get('other_allele') or row.get('reference_allele') or row.get('hm_inferOtherAllele') or '').upper()
    issue='';model='additive'
    if not pos.isdigit() or int(pos)<1 or chrom not in {str(x) for x in range(1,23)}:issue='unmapped_or_non_autosomal'
    if row.get('hm_match_chr','').lower()=='false' or row.get('hm_match_pos','').lower()=='false':issue='harmonization_conflict'
    if ea not in BASES or oa not in BASES or ea==oa:issue='unsupported_or_unspecified_alleles'
    if {ea,oa} in ({'A','T'},{'C','G'}):issue='palindromic_strand_ambiguous'
    if any(row.get(k,'').lower()=='true' for k in ['is_haplotype','is_diplotype','is_interaction']):issue='unsupported_complex_term'
    if any(row.get(k) not in (None,'','.','NA') for k in ['dosage_0_weight','dosage_1_weight','dosage_2_weight','inclusion_criteria']):issue='unsupported_complex_term'
    dominant=row.get('is_dominant','').lower()=='true';recessive=row.get('is_recessive','').lower()=='true'
    if dominant and recessive:issue='unsupported_complex_term'
    elif dominant:model='dominant'
    elif recessive:model='recessive'
    try:
        weight=float(row['effect_weight'])
        if not math.isfinite(weight):raise ValueError('nonfinite')
    except (ValueError,TypeError):raise ValueError(f'Invalid effect weight in scoring row {number}')
    return {'line':number,'chrom':chrom,'pos':int(pos) if pos.isdigit() else 0,
        'rsid':row.get('hm_rsID') or row.get('rsID','.'),'effect_allele':ea,'other_allele':oa,
        'weight':weight,'model':model,'issue':issue}


def prepare(a,c):
    manifest=load_manifest(c['pgs_manifest']);model=next(m for m in manifest['models'] if m['id']==a.pgs_id)
    if model.get('scoring_file'):source=Path(model['scoring_file'])
    else:source=download(model['scoring_url'],Path(c['pgs_cache'])/f'{a.pgs_id}_hmPOS_GRCh38.txt.gz')
    checksum=sha256(source)
    if model.get('scoring_sha256') and model['scoring_sha256']!=checksum:raise ValueError('Scoring file SHA256 differs from pinned model')
    raw=score_rows(source);header=next(raw)
    if header.get('pgs_id')!=a.pgs_id:raise ValueError('PGS file ID does not match requested model')
    rows=[normalize_row(number,row) for number,row in raw]
    if len(rows)!=model['variants_number'] or int(header.get('variants_number',-1))!=len(rows):raise ValueError('PGS variant count differs from pinned manifest/header')
    weight_type=header.get('weight_type','').lower()
    if weight_type in {'or','hr','or/hr','odds ratio','hazard ratio'}:raise ValueError('Multiplicative OR/HR weights require explicit author-defined transformation; unsupported score')
    locus_counts=Counter((r['chrom'],r['pos']) for r in rows if r['pos']>0)
    for row in rows:
        if locus_counts[(row['chrom'],row['pos'])]>1:row['issue']='duplicate_scoring_locus'
    rows.sort(key=lambda r:(int(r['chrom']) if r['chrom'].isdigit() else 99,r['pos'],r['line']))
    if not any(r['weight']!=0 for r in rows):raise ValueError('PGS model has no nonzero weights')
    target=f'{a.pgs_id}.weights.tsv.gz';table(target,WEIGHT_FIELDS,rows)
    dump(f'{a.pgs_id}.model.json',{'model':model,'header':header,'scoring_sha256':checksum,
        'normalized_weights_sha256':sha256(target),'manifest_sha256':sha256(c['pgs_manifest']),
        'total_rows':len(rows),'preparation_issues':dict(Counter(r['issue'] for r in rows if r['issue'])),
        'total_absolute_weight':math.fsum(abs(r['weight']) for r in rows),
        'method_version':METHOD_VERSION,'source':str(source.resolve()),'test_mode':c['test_mode']})


def match_alleles(effect,other,ref,alt):
    if {effect,other} in ({'A','T'},{'C','G'}):return None,'palindromic_strand_ambiguous'
    if {effect,other}=={ref,alt}:return effect,'forward'
    e,o=effect.translate(COMPLEMENT),other.translate(COMPLEMENT)
    if {e,o}=={ref,alt}:return e,'complement'
    return None,'allele_mismatch'


def contribution(row,records,sample,c,fasta):
    if not records:return {'status':'not_in_imputed_panel'}
    # Separate biallelic posteriors do not define a multi-allelic joint genotype.
    if len(records)>1:return {'status':'ambiguous_imputed_locus'}
    rec=records[0]
    if len(rec.alleles)!=2 or any(x not in BASES for x in rec.alleles):return {'status':'unsupported_imputed_alleles'}
    if fasta.fetch(rec.contig,rec.pos-1,rec.pos).upper()!=rec.ref:raise ValueError(f'Imputed REF disagrees with FASTA: {rec.contig}:{rec.pos}')
    effect,orientation=match_alleles(row['effect_allele'],row['other_allele'],rec.ref,rec.alts[0])
    if effect is None:return {'status':orientation}
    call=posterior(rec,sample,c)
    if call['status']!='imputed':return {'status':call['status'],'GP':call['GP'],'INFO':call['INFO']}
    fmt=rec.samples[sample];ds=float(call['DS']);gp=fmt['GP'];is_alt=effect==rec.alts[0]
    dosage=ds if is_alt else 2-ds
    if row['model']=='additive':value=dosage
    elif row['model']=='dominant':value=1-(gp[0] if is_alt else gp[2])
    elif row['model']=='recessive':value=gp[2] if is_alt else gp[0]
    else:raise ValueError('Unrecognized inheritance model')
    return {'status':'scored','matched_ref':rec.ref,'matched_alt':rec.alts[0],'orientation':orientation,
        'effect_dosage':dosage,'GP':call['GP'],'INFO':call['INFO'],'contribution':float(row['weight'])*value}


def population_context(ancestry,model):
    status=ancestry.get('status','not_available');nearest=ancestry.get('nearest_reference_label','not_available')
    if status!='exploratory':
        text='Sample population assignment is uncertain or unavailable; score transferability is undetermined. '
    else:text='The sample has only an exploratory reference-similarity label; this does not validate score portability. '
    text+='Development and evaluation populations are reported per model. No suitable ancestry-specific score calibration is supplied; personal risk and percentiles are withheld.'
    return {'sample_ancestry_status':status,'nearest_reference_label':nearest,'population_interpretation':text}


def score(a,c):
    prepared=json.loads(Path(a.model).read_text());model=prepared['model']
    if model['id']!=a.pgs_id or sha256(a.weights)!=prepared['normalized_weights_sha256']:raise ValueError('Prepared score provenance mismatch')
    with gzip.open(a.weights,'rt') as f:rows=list(csv.DictReader(f,delimiter='\t'))
    for r in rows:r['pos']=int(r['pos']);r['weight']=float(r['weight']);r['line']=int(r['line'])
    if len(rows)!=prepared['total_rows']:raise ValueError('Prepared score row count mismatch')
    inputs=unique_inputs(a.imputed,a.sample)
    if sorted(inputs)!=sorted(c['glimpse_chromosomes']):raise ValueError('Imputed chromosome set differs from configured set')
    by_chrom=defaultdict(list)
    for row in rows:
        if not row['issue']:by_chrom[c.get('chrom_prefix','chr')+row['chrom']].append(row)
    decisions={};bc=c['tools']['bcftools']
    with pysam.FastaFile(c['reference']) as fasta:
        for chrom,targets in by_chrom.items():
            if chrom not in inputs:continue
            bed=Path(f'{a.pgs_id}.{chrom}.bed');bed.write_text(''.join(f'{chrom}\t{r["pos"]-1}\t{r["pos"]}\n' for r in targets))
            subset=f'{a.pgs_id}.{chrom}.targets.bcf'
            subprocess.run([bc,'view','--regions-overlap','0','-R',str(bed),'-Ob','-o',subset,inputs[chrom]],check=True)
            lookup={r['pos']:r for r in targets}
            with pysam.VariantFile(subset) as variants:
                for (contig,pos),records in itertools.groupby(variants,key=lambda r:(r.contig,r.pos)):
                    if contig!=chrom or pos not in lookup:raise ValueError('Unexpected score target returned by bcftools')
                    row=lookup[pos];decisions[row['line']]=contribution(row,list(records),a.sample,c,fasta)
            Path(subset).unlink();bed.unlink()
    counts=Counter();sum_terms=[];matched_abs=[];mask=hashlib.sha256()
    with gzip.open(f'{a.sample}.{a.pgs_id}.variant_audit.tsv.gz','wt',compresslevel=1,newline='') as f:
        writer=csv.DictWriter(f,fieldnames=AUDIT_FIELDS,delimiter='\t',extrasaction='ignore');writer.writeheader()
        for row in rows:
            result={'status':row['issue']} if row['issue'] else decisions.get(row['line'],{'status':'not_in_imputed_panel'})
            counts[result['status']]+=1;writer.writerow({**row,**result})
            if result['status']=='scored':
                sum_terms.append(result['contribution']);matched_abs.append(abs(row['weight']))
                mask.update(json.dumps({k:row[k] for k in ['line','chrom','pos','effect_allele','other_allele','weight','model']},sort_keys=True).encode()+b'\n')
    n=len(rows);n_scored=counts['scored'];coverage=n_scored/n
    weight_coverage=math.fsum(matched_abs)/prepared['total_absolute_weight'] if prepared['total_absolute_weight'] else 0
    if n_scored==n:status='complete_uncalibrated'
    elif coverage>=c['pgs_min_coverage'] and weight_coverage>=c['pgs_min_weight_coverage']:status='partial_above_coverage_threshold'
    else:status='insufficient_coverage'
    if counts['unsupported_complex_term']:status='unsupported_model_terms'
    partial=math.fsum(sum_terms) if n_scored else None
    ancestry=json.loads(Path(a.ancestry).read_text())
    result={'sample':a.sample,'pgs_id':a.pgs_id,'trait':model['trait'],'category':model.get('category','Custom'),
        'status':status,'raw_score':partial if status=='complete_uncalibrated' else None,'partial_score':partial if n_scored<n else None,
        'expected_variants':n,'scored_variants':n_scored,'variant_coverage':coverage,'absolute_weight_coverage':weight_coverage,
        'counts':dict(counts),'scoring_sha256':prepared['scoring_sha256'],'variant_mask_sha256':mask.hexdigest(),
        'method_version':METHOD_VERSION,'test_mode':c['test_mode'],'genome_build':'GRCh38',
        **{k:model.get(k,'') for k in ['name','development_ancestry','evaluation_ancestry','publication_doi','catalog_url','license']},
        **population_context(ancestry,model),'interpretation':INTERPRETATION,
        'thresholds':{k:c[k] for k in ['pgs_min_coverage','pgs_min_weight_coverage','imputation_min_gp','imputation_min_info']},
        'model_metadata':prepared}
    if c['test_mode']:result['interpretation']='SYNTHETIC TEST ONLY. '+result['interpretation']
    dump(f'{a.sample}.{a.pgs_id}.score.json',result)


def report(a,c):
    from report_layout import model_page, CALIBRATION_NOTE
    from review_priority import rank_models,PGS_FIELDS,PGS_METHOD
    fields=PGS_FIELDS+RESULT_FIELDS
    from pgs_reference_stats import attach
    from patient_metadata import resolve
    results=rank_models(attach([json.loads(Path(p).read_text()) for p in a.scores],getattr(a,'reference_stats',[])),c.get('pgs_priority_focus',''),resolve(c,getattr(a,'coverage',None) or Path(c['output'])/'alignment'/(a.sample+'.coverage.txt')))
    expected={m['id'] for m in load_manifest(c['pgs_manifest'])['models']}
    if len(results)!=len(expected) or {r['pgs_id'] for r in results}!=expected:raise ValueError('PGS report does not include exactly the configured models')
    if any(r['sample']!=a.sample for r in results):raise ValueError('Mixed samples in PGS results')
    summary={'sample':a.sample,'runtime_signature':runtime_signature(c),'models':len(results),'trait_labels':len({r['trait'] for r in results}),
        'status_counts':dict(Counter(r['status'] for r in results)),'test_mode':c['test_mode'],
        'interpretation':INTERPRETATION,'calibration_note':CALIBRATION_NOTE,'priority_method':PGS_METHOD,'priority_focus':c.get('pgs_priority_focus',''),'results':results}
    dump(f'{a.sample}.polygenic.json',summary);table(f'{a.sample}.polygenic_scores.tsv',fields,results)
    book=Excel(f'{a.sample}.polygenic_scores.xlsx',[[a.sample+' · published polygenic and trait models'],
        ['Interpretation',INTERPRETATION],['Review priority',str(PGS_METHOD)],['Raw score','Only populated when every published term is scored.'],
        ['Partial score','Weighted sum over retained terms only; not the complete published model. No missing-data mean replacement or rescaling.'],
        ['Coverage','Counts and absolute-effect-weight coverage are both reported. The configurable thresholds are technical completeness flags, not clinical validity cutoffs.'],
        ['Allele matching','GRCh38 position and both alleles, with unambiguous complements allowed; ambiguous palindromes and multi-allelic loci withheld.'],
        ['Population','The existing ancestry result is read without inventing admixture proportions. Development/evaluation ancestry is recorded for every model. Absolute-risk calibration is absent. Optional matched-marker reference percentiles are exploratory comparisons, not disease probabilities.'],
        ['Age and sex calibration',CALIBRATION_NOTE],['Test mode',str(c['test_mode'])]])
    for name in ['All_models','Physical_traits','Common_diseases','Cardiomyopathy']:book.create(name,fields)
    for row in results:
        book.add('All_models',row)
        name={'Physical traits':'Physical_traits','Common diseases':'Common_diseases','Cardiomyopathy':'Cardiomyopathy'}.get(row['category'])
        if name:book.add(name,row)
    book.close()
    model_page(f'{a.sample}.polygenic.html',a.sample+' · broad traits and polygenic scores',INTERPRETATION+' Review priority: '+PGS_METHOD['formula']+' '+PGS_METHOD['topic_points']+' '+PGS_METHOD['meaning'],
        results,['review_rank','review_priority_score','review_priority_group','priority_reason','reference_population','reference_percentile','reference_z','reference_n','reference_percentile_low','reference_percentile_high','calibration_status','pgs_id','name','trait','category','status','raw_score','partial_score','variant_coverage','absolute_weight_coverage','development_ancestry','evaluation_ancestry','population_interpretation'],
        [('Excel',f'{a.sample}.polygenic_scores.xlsx'),('TSV',f'{a.sample}.polygenic_scores.tsv')])


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='stage',required=True)
    for stage in ['prepare','score','report']:
        q=sub.add_parser(stage);q.add_argument('--config',required=True)
        if stage!='report':q.add_argument('--pgs-id',required=True)
        if stage!='prepare':q.add_argument('--sample',required=True)
        if stage=='score':
            q.add_argument('--model',required=True);q.add_argument('--weights',required=True);q.add_argument('--imputed',nargs='+',required=True);q.add_argument('--ancestry',required=True)
        if stage=='report':
            q.add_argument('--scores',nargs='+',required=True);q.add_argument('--reference-stats',nargs='*',default=[]);q.add_argument('--coverage')
    a=p.parse_args();c=json.loads(Path(a.config).read_text());globals()[a.stage](a,c)
if __name__=='__main__':main()
