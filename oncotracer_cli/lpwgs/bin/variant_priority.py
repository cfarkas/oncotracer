"""Transparent research support scores; no disease probabilities or new calls."""
import csv
import gzip
import math
import re
from collections import Counter
from pathlib import Path

from extension_common import Excel, table
from sample_variant_status import FIELDS as STATUS_FIELDS

VERSION='sequencing-imputation-support-v1'
METHOD={
 'version':VERSION,'range':[0,100],
 'sequencing_depth':'15 * min(filtered or caller DP / 10, 1), only with alternate-read support',
 'sequencing_alternate_reads':'20 * min(alternate reads / 3, 1)',
 'sequencing_genotype_quality':'15 * min(germline GQ / 30, 1) * min(DP / 10, 1), only with alternate reads and a nonreference read genotype',
 'sequencing_balance':'10 * min(DP / 10, 1) when a read-based diploid heterozygous call has alternate fraction 0.2..0.8, or a homozygous-alternate call has fraction >=0.85',
 'imputation':'30 * min(called-genotype GP, site INFO), only for unambiguous nonreference imputation passing configured GP/INFO thresholds',
 'agreement':'10 when a read-based nonreference diploid genotype (DP>=4, GQ>=10, >=2 alternate reads) agrees exactly with passing imputation',
 'discordance':'A reliable read genotype disagreeing with passing imputation caps total support at 20 and requires review.',
 'missing_values':'Unknown evidence receives no points; this is not evidence for a reference genotype. Imputation-only scores cannot exceed 30.',
 'scope':'Heuristic ranking, not a calibrated probability, ACMG classification, diagnosis or clinical validation. Read and imputation evidence are correlated. Clinical relevance and annotation holds are separate from genotype support. Scores for different variant types are not equivalent.',
 'read_audit':'For pathogenic-label SNPs only, BAM reads are counted at MQ>=configured threshold and BQ>=configured threshold. Duplicate, secondary, supplementary, QC-fail and unmapped reads are excluded. Overlapping mates are counted once by read name. This targeted check does not apply BAQ or call genotypes. Indels retain caller AD/DP.'}
FIELDS=STATUS_FIELDS+['carrier_status','carrier_label','carrier_reason','inheritance_context','carrier_genotype','carrier_genotype_source','genetic_context','patient_meaning','inheritance_priority_adjustment','review_rank','review_priority_score','review_priority_reason','priority_group','support_score','support_band','variant','gene','rsid','interpretation_status','clinical_category',
        'current_classification','snapshot_classification','clinical_review','condition','read_source','read_genotype','read_DP','read_ALT','read_GQ','read_ALT_fraction',
        'imputed_genotype','imputed_GP','imputed_INFO','imputation_passing','sequencing_points','imputation_points','agreement_points','discordance_penalty','support_flags','clinical_note','sources']


def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError):return None


def genotype(value):
    parts=re.split(r'[/|]',str(value))
    return tuple(sorted(parts)) if len(parts)==2 and all(re.fullmatch('[ACGT]+',x) for x in parts) else None


def key(row):return ':'.join(['chr'+str(row['chrom']).removeprefix('chr')]+[str(row[x]) for x in ['pos','ref','alt']])


def rows(path):
    with (gzip.open(path,'rt') if str(path).endswith('.gz') else open(path)) as f:yield from csv.DictReader(f,delimiter='\t')


def audit_reads(bam,variants,config):
    import pysam
    result={}
    with pysam.AlignmentFile(str(bam),'rb') as align:
        samples={r.get('SM') for r in align.header.to_dict().get('RG',[]) if r.get('SM')}
        if samples and config.get('sample') and samples!={config['sample']}:raise ValueError('BAM read groups do not match report sample')
        for v in variants:
            ch,p,ref,alt=v.split(':');p=int(p)
            if len(ref)!=1 or len(alt)!=1:continue
            bam_chrom=ch if ch in align.references else ch.removeprefix('chr')
            if bam_chrom not in align.references:continue
            molecules={}
            for read in align.fetch(bam_chrom,p-1,p):
                if read.is_unmapped or read.is_secondary or read.is_supplementary or read.is_duplicate or read.is_qcfail or read.mapping_quality<config['min_mapping_quality']:continue
                for qpos,rpos in read.get_aligned_pairs(matches_only=True):
                    if rpos!=p-1 or read.query_qualities is None or read.query_qualities[qpos]<config['min_base_quality']:continue
                    base=read.query_sequence[qpos];qual=read.query_qualities[qpos]
                    molecule=(read.get_tag('RG') if read.has_tag('RG') else '',read.query_name)
                    previous=molecules.get(molecule)
                    if previous is None or qual>previous[0]:molecules[molecule]=(qual,base)
                    elif qual==previous[0] and base!=previous[1]:molecules[molecule]=(qual,'ambiguous')
            counts=Counter(base for qual,base in molecules.values() if base in 'ACGT' and len(base)==1)
            result[v]={'DP':sum(counts.values()),'ref_reads':counts[ref],'alt_reads':counts[alt],'base_counts':dict(counts),'source':'targeted_filtered_BAM_reads'}
    return result


def calculate(read_gt,dp,ad,gq,imputed_gt,gp,info,passing,ref,alt,ambiguous=False):
    dp,ad,gq,gp,info=map(number,[dp,ad,gq,gp,info]);rg=genotype(read_gt);ig=genotype(imputed_gt)
    seq=0.;imp=0.;agree=0.;flags=[]
    if ad is not None and dp is not None and ad>dp:raise ValueError('Alternate read count exceeds depth')
    if dp and ad:
        seq=15*min(dp/10,1)+20*min(ad/3,1)+(15*min((gq or 0)/30,1)*min(dp/10,1) if rg and alt in rg else 0)
        fraction=ad/dp
        if rg and ((rg==(tuple(sorted([ref,alt]))) and .2<=fraction<=.8) or (rg==(alt,alt) and fraction>=.85)):seq+=10*min(dp/10,1)
    else:flags.append('no_direct_alternate_read_support')
    passing=bool(passing and not ambiguous)
    reliable=bool(rg and dp is not None and dp>=4 and ad is not None and gq is not None and gq>=10 and (ad>=2 if alt in rg else rg==(ref,ref)))
    if passing and not ambiguous and gp is not None and info is not None and ig and alt in ig:imp=30*min(gp,info,1)
    if ambiguous:flags.append('ambiguous_imputation')
    discordant=bool(reliable and passing and ig and rg!=ig)
    if reliable and passing and ig==rg and imp>0:agree=10
    if rg and ig and rg!=ig:flags.append('reliable_genotype_discordance' if discordant else 'low_evidence_genotype_discordance')
    if gq is None:flags.append('read_genotype_quality_unavailable')
    if dp is not None and dp<4:flags.append('low_read_depth')
    if ad is not None and ad<2:flags.append('fewer_than_two_alternate_reads')
    total=seq+imp+agree
    if discordant:total=min(total,20)
    band='discordant_review' if discordant else 'strong_combined' if total>=80 else 'strong_sequencing' if seq>=45 and not discordant else 'mixed_support' if seq>0 and imp>0 else 'imputation_only' if imp>0 else 'limited_read_support' if seq>0 else 'unresolved'
    return {'support_score':round(total,1),'support_band':band,'sequencing_points':round(seq,1),'imputation_points':round(imp,1),'agreement_points':agree,'discordance_penalty':round(seq+imp+agree-total,1),
            'support_flags':'; '.join(flags),'read_ALT_fraction':round(ad/dp,4) if dp and ad is not None else None}


def clinical_status(explanation):
    if not explanation:return 'snapshot_only','No current public classification review was requested for this record.'
    if explanation.get('category')=='annotation_mismatch':return 'annotation_hold','Clinical interpretation withheld: public GRCh38 allele differs.'
    current=explanation.get('public_classification','').lower()
    if 'no classifications from unflagged' in current:return 'flagged_assertion','Historical pathogenic assertion is excluded from the current aggregate classification.'
    if explanation.get('public_allele_match')!='matched':return 'identity_unverified','Current public exact-allele identity is unresolved.'
    classes={x.strip() for x in re.split(r'[/|;]',current.replace('_',' '))}
    if not classes or not classes.issubset({'pathogenic','likely pathogenic'}):
        if classes & {'pathogenic','likely pathogenic'} and not any('conflict' in x or 'uncertain' in x for x in classes):return 'limited_clinical_evidence','Condition-specific or complex-trait assertions require separate clinical review; a combined label is not a Mendelian diagnosis.'
        return ('uncertain_current_assertion','Current classification is conflicting or uncertain; no disease conclusion is assigned.') if any('conflict' in x or 'uncertain' in x for x in classes) else ('downgraded_current_assertion','The current public classification does not support the historical pathogenic interpretation.')
    review=explanation.get('public_review','').lower().replace('_',' ')
    if 'no assertion criteria' in review or not any(x in review for x in ['criteria provided','reviewed by expert panel','practice guideline']):
        return 'limited_clinical_evidence','Database assertion has limited review; no Mendelian diagnosis is established.'
    if explanation.get('category')!='germline_supported':return 'unconfirmed_genotype','Genotype evidence is insufficient; this is not an established finding.'
    return 'confirmation_candidate','Current allele classification has supporting review; the sample genotype still requires independent confirmation.'



def prioritize(observed_path,imputed_paths,explanations,bam,config):
    merged={key(r):{'observed':r} for r in rows(observed_path)} if observed_path else {}
    for path in imputed_paths:
        for r in rows(path):
            k=key(r)
            # Include reference imputation only for an observed candidate to expose discordance.
            if r.get('genotype_state')!='nonreference' and k not in merged:continue
            if k in merged and merged[k].get('imputed'):
                merged[k]['ambiguous']=True
            else:merged.setdefault(k,{})['imputed']=r
    by_variant={r['variant']:r for r in explanations['clinical']}
    audits=audit_reads(bam,by_variant,config)
    result=[]
    for variant,v in merged.items():
        o=v.get('observed',{});i=v.get('imputed',{});base=o or i;e=by_variant.get(variant);ch,pos,ref,alt=variant.split(':')
        source='germline_caller';rg=o.get('germline_GT','.');dp=o.get('germline_DP');gq=o.get('germline_GQ');ad=o.get('germline_AD')
        if number(dp) is None:
            source='somatic_caller';rg=o.get('somatic_GT','.');dp=o.get('somatic_DP');ad=o.get('somatic_AD');gq=None
        ad=number(str(ad).split(',')[1]) if ad is not None and len(str(ad).split(','))==2 else None
        if variant in audits:
            a=audits[variant];source=a['source'];dp=a['DP'];ad=a['alt_reads']
        ig=i.get('genotype',o.get('imputed_GT','.'));gp=i.get('called_GP',o.get('imputed_GP'));info=i.get('INFO',o.get('imputed_INFO'))
        gpnum,infonum=number(gp),number(info)
        passing=bool(gpnum is not None and infonum is not None and gpnum>=config['imputation_min_gp'] and infonum>=config['imputation_min_info'] and
                     (i.get('evidence_status')=='supported_imputed' if i else o.get('imputation_state') in {'alternate_supported','reference_supported'}))
        score=calculate(rg,dp,ad,gq,ig,gp,info,passing,ref,alt,v.get('ambiguous',False))
        status,note=clinical_status(e)
        if status=='confirmation_candidate' and ('reliable_genotype_discordance' in score['support_flags'] or v.get('ambiguous')):
            status='unconfirmed_genotype';note='Conflicting or ambiguous genotype evidence; independent confirmation is required.'
        label=base.get('clinvar_classification','.')
        if status=='confirmation_candidate':group='1_confirm_and_review'
        elif status=='unconfirmed_genotype':group='2_unconfirmed_clinical_candidate'
        elif status in {'annotation_hold','flagged_assertion','identity_unverified','downgraded_current_assertion'}:group='5_annotation_or_assertion_hold'
        elif status=='uncertain_current_assertion':group='3_uncertain_or_conflicting_assertion'
        elif status=='limited_clinical_evidence':group='4_limited_clinical_evidence'
        else:group='3_uncertain_or_conflicting_assertion' if ('uncertain' in label.lower() or 'conflict' in label.lower()) else '6_other_clinvar_annotations'
        result.append({'variant':variant,'gene':e.get('gene',base.get('gene','.')) if e else re.sub(r':\d+(?=[|;]|$)','',base.get('gene','.')),'rsid':e.get('rsid','.') if e else i.get('rsid','.'),
            'priority_group':group,'interpretation_status':status,'clinical_category':e.get('category','snapshot_only') if e else 'snapshot_only',
            'current_classification':e.get('public_classification','not_reviewed') if e else 'not_reviewed',
            'snapshot_classification':label,'clinical_review':e.get('public_review','.') if e else base.get('clinvar_review','.'),'condition':base.get('condition','.'),
            'read_source':source,'read_genotype':rg,'read_DP':number(dp),'read_ALT':ad,'read_GQ':number(gq),
            'imputed_genotype':ig,'imputed_GP':gpnum,'imputed_INFO':infonum,'imputation_passing':passing,**score,'clinical_note':note,'sources':e.get('sources','') if e else base.get('clinvar_url','')})
    from review_priority import rank_variants
    return rank_variants(result,explanations['clinical']),audits


def exports(folder,sample,priorities,explanations):
    folder=Path(folder)
    table(folder/(sample+'.variant_priority.tsv.gz'),FIELDS,priorities)
    selected=set(r['variant'] for r in explanations['clinical'])
    from review_priority import VARIANT_METHOD
    book=Excel(folder/(sample+'.variant_priority.xlsx'),[['Review priority',str(VARIANT_METHOD)]]+[['Purpose','Prioritize review of ClinVar-matched variants; no score confirms disease or genotype.'],['Support score','0..100 research heuristic; sequencing <=60, imputation <=30, reliable agreement <=10.'],['Clinical relevance','Shown separately from support. Annotation holds are excluded from clinical findings.'],['Formula',str(METHOD)],['Scope','Union of observed ClinVar matches and imputed ClinVar nonreference SNPs; not every imputed reference site.']])
    for name,predicate in [('Priority_findings',lambda r:r['variant'] in selected),('All_ClinVar_variants',lambda r:True),('Annotation_holds',lambda r:r['interpretation_status'] in {'annotation_hold','flagged_assertion','identity_unverified'})]:
        book.create(name,FIELDS)
        for r in priorities:
            if predicate(r):book.add(name,r)
    book.create('Highest_genotype_support',FIELDS)
    for r in sorted(priorities,key=lambda r:(-r['support_score'],r['variant'])):book.add('Highest_genotype_support',r)
    book.close()
