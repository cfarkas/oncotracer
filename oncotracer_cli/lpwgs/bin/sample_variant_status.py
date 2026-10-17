"""Orthogonal genotype, carrier and phenotype labels; never infer affected status."""
import math
import re

VERSION='sample-variant-status-v1'
FIELDS=['zygosity','zygosity_label','status_genotype','zygosity_source','zygosity_evidence','read_zygosity','imputed_zygosity','affected_status','affected_label','status_summary']
LABELS={'heterozygous':'Heterozygous','homozygous_alternate':'Homozygous alternate','homozygous_reference':'Homozygous reference','homozygous':'Homozygous; reference/alternate orientation unavailable','haploid_alternate':'Haploid alternate call','haploid_reference':'Haploid reference call','haploid':'Haploid call','mitochondrial_alternate':'Mitochondrial alternate call; homoplasmy not assessed','mitochondrial_reference':'Mitochondrial reference call; heteroplasmy not assessed','unresolved':'Unresolved','unresolved_conflict':'Unresolved: conflicting genotypes'}
METHOD={'version':VERSION,'zygosity':'Describes a reported genotype, independently of pathogenicity. Read and imputed labels remain separate; partial calls are unresolved. VCF ZYG describes primary GT; RZYG describes the reported candidate genotype.', 'carrier':'Possible recessive carrier requires a reviewed, current pathogenic/likely pathogenic allele, compatible inheritance and an interpretable heterozygous genotype. Unknown does not mean non-carrier.', 'affected':'Not established from this genetic pipeline. ClinVar submitter affected status concerns people in a source submission and is never assigned to the current patient.', 'mitochondrial':'A dominant mitochondrial allele is not labeled homozygous or proven homoplasmic. Heteroplasmy requires a separate validated assessment.'}

def finite(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value>=0 else None
    except (ValueError,TypeError):return None

def truth(value):return value is True or str(value).lower() in {'true','yes','1'}

def alleles(value,ref=None,alt=None):
    if value is None:return None
    parts=list(value) if isinstance(value,(list,tuple)) else re.split(r'[/|]',str(value).upper())
    if not parts or len(parts)>2 or any(v is None or str(v) in {'','.','-','None'} for v in parts):return None
    result=[]
    for value in parts:
        if str(value).isdigit():
            if ref is None or alt is None or int(value) not in {0,1}:return None
            value=(ref,alt)[int(value)]
        value=str(value).upper()
        if not re.fullmatch('[ACGT]+',value):return None
        if ref is not None and alt is not None and value not in {ref.upper(),alt.upper()}:return None
        result.append(value)
    return tuple(result)

def zygosity(value,ref=None,alt=None,chrom=''):
    gt=alleles(value,ref,alt)
    if not gt:return 'unresolved'
    if str(chrom).removeprefix('chr').upper() in {'M','MT'}:
        if len(set(gt))>1:return 'unresolved'
        return 'mitochondrial_reference' if ref and gt[0]==ref.upper() else 'mitochondrial_alternate'
    if len(gt)==1:
        return 'haploid_reference' if ref and gt[0]==ref.upper() else 'haploid_alternate' if alt and gt[0]==alt.upper() else 'haploid'
    if gt[0]!=gt[1]:return 'heterozygous'
    return 'homozygous_reference' if ref and gt[0]==ref.upper() else 'homozygous_alternate' if alt and gt[0]==alt.upper() else 'homozygous'

def pure_plp(value):
    parts={p.strip().lower().replace('_',' ') for p in re.split(r'[/;|]',value or '')}
    return bool(parts) and parts <= {'pathogenic','likely pathogenic'}

def pure_benign(value):
    parts={p.strip().lower().replace('_',' ') for p in re.split(r'[/;|]',value or '')}
    return bool(parts) and parts <= {'benign','likely benign'}

def summarize(genotype,ref=None,alt=None,chrom='',source='not_available',evidence='not_available',read=None,imputed=None,conflict=False):
    state='unresolved_conflict' if conflict else zygosity(genotype,ref,alt,chrom)
    return {'zygosity':state,'zygosity_label':LABELS[state],'status_genotype':genotype if alleles(genotype,ref,alt) and not conflict else '.',
      'zygosity_source':source,'zygosity_evidence':evidence,'read_zygosity':zygosity(read,ref,alt,chrom),'imputed_zygosity':zygosity(imputed,ref,alt,chrom),
      'affected_status':'not_established','affected_label':'Affected status not established','status_summary':LABELS[state]+'; affected status not established'}

def from_priority(row):
    chrom,pos,ref,alt=row['variant'].split(':');rg=row.get('read_genotype');ig=row.get('imputed_genotype');flags=row.get('support_flags','')
    conflict=any(x in flags for x in ['reliable_genotype_discordance','ambiguous_imputation','conflicting_records'])
    passing=truth(row['imputation_passing']) if 'imputation_passing' in row else (finite(row.get('imputation_points')) or 0)>0
    if passing and alleles(ig,ref,alt):
        gt=ig;source='GLIMPSE2 imputation';evidence='Imputed genotype; independent confirmation unavailable'
        if alleles(rg,ref,alt) and sorted(alleles(rg,ref,alt))!=sorted(alleles(ig,ref,alt)):
            evidence+='; read-call genotype disagrees'
    else:
        gt=rg;source='Somatic caller' if row.get('read_source')=='somatic_caller' else 'Sequencing call'
        evidence='Reported read genotype; not clinical confirmation'
        if (finite(row.get('read_DP')) or 0)<4 or (alt in (alleles(rg,ref,alt) or ()) and (finite(row.get('read_ALT')) or 0)<2):evidence='Unconfirmed read call; limited sequence support'
    if not alleles(gt,ref,alt):source='Not available';evidence='No interpretable genotype'
    if conflict:evidence='Conflicting or ambiguous evidence; original calls retained';source='Conflicting sources'
    return summarize(gt,ref,alt,chrom,source,evidence,rg,ig,conflict)

def for_marker(row,kind='trait'):
    chrom=row.get('chrom','');ref=row.get('ref');alt=row.get('alt');gt=row.get('genotype',row.get('GT','.'))
    if kind in {'trait','gwas'} and row.get('status') not in {'called','imputed','exploratory'}:gt='.'
    source={'mitochondrial':'Mitochondrial dominant allele','neanderthal':'GLIMPSE2 imputation','somatic':'Somatic caller'}.get(kind,row.get('genotype_source','Reported marker genotype'))
    evidence={'mitochondrial':'Dominant allele fraction; heteroplasmy not comprehensively assessed','neanderthal':'Imputed marker; not independent confirmation','somatic':'Unpaired somatic candidate; acquired origin unconfirmed'}.get(kind,str(row.get('status','unresolved')))
    result=summarize(gt,ref,alt,chrom,source,evidence,gt if kind in {'mitochondrial','somatic'} else None,gt if kind=='neanderthal' else None)
    result.update(carrier_status='unknown',carrier_label='Carrier status not assessed',carrier_reason='No reviewed Mendelian carrier assessment is available for this marker.')
    if kind in {'mitochondrial','somatic'}:result.update(carrier_status='not_applicable',carrier_label='Mendelian carrier label not applicable to this evidence',carrier_reason='This table describes '+('dominant mitochondrial alleles' if kind=='mitochondrial' else 'unpaired somatic candidates')+'; it does not establish a recessive germline carrier result.')
    result['status_summary']=result['zygosity_label']+'; '+result['carrier_label']+'; affected status not established'
    return result

def explanation_priority(entry):
    """Adapt structured observations, never parse narrative text or ClinVar affected status."""
    from variant_priority import calculate,clinical_status
    observations=entry.get('observations',[]);ref,alt=entry['variant'].split(':')[-2:]
    observed=next((r for r in observations if r.get('source')=='observed_call_union'),{})
    rg=observed.get('germline_GT','.');dp=observed.get('germline_DP');ad=observed.get('germline_AD');gq=observed.get('germline_GQ');source='germline_caller'
    if finite(dp) is None:rg=observed.get('somatic_GT','.');dp=observed.get('somatic_DP');ad=observed.get('somatic_AD');gq=None;source='somatic_caller'
    ad=str(ad).split(',');ad=finite(ad[1]) if len(ad)==2 else None
    imputed=[r for r in observations if r.get('source')=='imputed_all_sites']
    i=next((r for r in imputed if r.get('evidence_status')=='supported_imputed'),imputed[0] if imputed else {})
    ig=i.get('genotype',observed.get('imputed_GT','.'));gp=i.get('called_GP',observed.get('imputed_GP'));info=i.get('INFO',observed.get('imputed_INFO'))
    passing=(i.get('evidence_status')=='supported_imputed' if i else observed.get('imputation_state') in {'alternate_supported','reference_supported'}) and finite(gp) is not None and finite(info) is not None
    ambiguous=len({tuple(sorted(alleles(r.get('genotype'),ref,alt) or ())) for r in imputed if r.get('evidence_status')=='supported_imputed'})>1
    score=calculate(rg,dp,ad,gq,ig,gp,info,passing,ref,alt,ambiguous)
    status,note=clinical_status(entry)
    return dict(variant=entry['variant'],read_genotype=rg,read_DP=dp,read_ALT=ad,read_GQ=gq,read_source=source,imputed_genotype=ig,imputed_GP=gp,imputed_INFO=info,imputation_passing=passing,interpretation_status=status,clinical_note=note,clinical_category=entry.get('category'),current_classification=entry.get('public_classification'),snapshot_classification=entry.get('observed_classification'),**score)
