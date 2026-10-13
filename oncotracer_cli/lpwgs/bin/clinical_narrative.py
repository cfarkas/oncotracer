"""Plain-language variant articles built from the actual allele and sample evidence."""
import re


def readable(value):
    words=[]
    for part in re.split(r'[|;]',str(value or '')):
        part=part.replace('_',' ').strip()
        if part.lower() not in {'','.', 'not provided','not specified','not supplied','none'} and part not in words:words.append(part)
    return '; '.join(words) or 'No specific condition supplied'


def article(row,priority,clinical):
    from variant_priority import genotype
    from carrier_context import assess
    inheritance=row.get('inheritance_context','Inheritance has not been established for this exact allele and condition.')
    if inheritance.startswith('Inheritance is not established'):
        inheritance='The mode of inheritance for this exact allele and condition has not been reviewed in the available sources.'
    curated=row.get('explanation','').split('Curated literature review',1)
    background=curated[1].split(': ',1)[-1] if len(curated)>1 else ''
    # Interpretation belongs in the sample section; retain biological background here.
    background=re.split(r'\b(?:This gene-level|If an isolated|Confirm the exact genotype)',background,maxsplit=1)[0].strip()
    status=priority.get('interpretation_status','identity_unverified')
    context=assess(priority,row,clinical)
    gt=genotype(context['carrier_genotype'])
    copies=gt.count(row['variant'].split(':')[-1]) if gt else None
    reviewed=status in {'confirmation_candidate','unconfirmed_genotype'} and not any(x in priority.get('support_flags','') for x in ['reliable_genotype_discordance','ambiguous_imputation'])
    recessive='autosomal recessive' in inheritance.lower() and 'dominant' not in inheritance.lower()
    other=context['carrier_status']=='recessive_genotype_unresolved' and copies!=2
    if reviewed and recessive and copies==1 and not other:
        meaning='Possible carrier — confirmation needed'
        personal='One altered copy is suggested. If this heterozygous genotype is independently confirmed, it would be consistent with carrier status for the recessive condition described here. A single altered copy alone is generally not expected to cause that recessive disorder. Low-pass sequencing cannot reliably exclude a second disease-causing allele. Reproductive implications depend on the other parent’s genotype and clinical review.'
    elif reviewed and recessive and (copies==2 or other):
        meaning='Recessive finding — genotype and phase unresolved'
        personal='The available results require review of both gene copies and, where multiple variants are present, whether they occur on the same or opposite copies. Carrier or affected status cannot be assigned from these unconfirmed calls.'
    elif reviewed and 'autosomal dominant' in inheritance.lower() and 'recessive' not in inheritance.lower() and copies in (1,2):
        meaning='Potential disease-compatible genotype — unconfirmed'
        personal='For the autosomal dominant condition described here, one disease-causing altered copy can be relevant. This makes the allele a candidate for follow-up, rather than an isolated recessive carrier result. The genotype remains unconfirmed in this sample. Its presence would not by itself establish symptoms, disease severity or age of onset; those depend on the condition and clinical context.'
    elif status in {'annotation_hold','identity_unverified','flagged_assertion','downgraded_current_assertion'}:
        meaning='Clinical interpretation withheld'
        personal=priority.get('clinical_note','The exact allele or current assertion is unresolved.')+' No carrier or disease-risk conclusion is assigned.'
    else:
        meaning='Clinical significance in this sample remains unresolved'
        personal='The database classification describes the allele. It does not establish that the sample carries a clinically confirmed genotype or that the associated condition is present. '+priority.get('clinical_note','')
    read_gt=priority.get('read_genotype')
    evidence='Reported read genotype: '+str(read_gt if read_gt not in (None,'','.') else 'not called')+'.'
    if priority.get('read_DP') is not None:evidence+=' Sequencing read depth '+str(priority['read_DP'])+'; alternate-supporting reads '+str(priority.get('read_ALT','unavailable'))+'.'
    if context['zygosity_source']=='GLIMPSE2 imputation':
        evidence+=' GLIMPSE2 estimates '+str(priority.get('imputed_genotype'))+' with called-genotype posterior '+str(priority.get('imputed_GP'))+'. This estimate uses the same sequence data and is not independent confirmation.'
    elif priority.get('imputed_genotype') not in (None,'','.'):
        evidence+=' An imputed call is recorded, but it does not resolve this genotype; consult the source status fields.'
    else:evidence+=' No passing imputed genotype is available.'
    sections=[{'title':'Overview','text':row.get('gene','Unassigned gene')+' '+row.get('rsid','')+' is reported at '+row['variant']+'. Current exact-allele review: '+row.get('public_classification','unavailable')+'.'},
              {'title':'What is known','text':background or 'Reported condition context: '+readable(priority.get('condition'))+'. Gene function and a variant-specific mechanism are not established by the available curated sources.'},
              {'title':'Inheritance','text':inheritance},
              {'title':'What this means for this sample','text':personal},
              {'title':'Evidence in this sample','text':evidence},
              {'title':'Genotype, carrier and affected status','text':priority.get('status_summary','Status unavailable')+'. '+priority.get('zygosity_evidence','')+'.'}]
    return {'variant':row['variant'],'gene':row.get('gene','.'),'title':meaning,'sections':sections,'sources':row.get('sources','').split('; ')}
