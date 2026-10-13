"""Inheritance-aware, sample-specific carrier labels; no clinical confirmation inferred."""
from variant_priority import genotype,number
from sample_variant_status import from_priority,pure_plp,pure_benign
import re

FIELDS=['carrier_status','carrier_label','carrier_reason','inheritance_context','carrier_genotype','carrier_genotype_source']

def assess(priority,explanation=None,clinical=()):
    e=explanation or {};inheritance=e.get('inheritance_context','Inheritance not reviewed for this allele and condition.')
    zyg=from_priority(priority)
    value=zyg['status_genotype'];source=zyg['zygosity_source']
    gt=genotype(value);alt=priority['variant'].split(':')[-1];copies=gt.count(alt) if gt else None
    status=priority.get('interpretation_status');mode=inheritance.lower()
    code='unknown';label='Carrier status undetermined';reason='Carrier status requires a reviewed recessive condition and an interpretable genotype.'
    if status in {'annotation_hold','identity_unverified','flagged_assertion','downgraded_current_assertion'}:
        code='withheld';label='Not assigned — annotation hold';reason='The exact allele or current clinical assertion does not support a carrier interpretation.'
    elif zyg['zygosity']=='unresolved_conflict':
        reason='Conflicting or ambiguous genotype evidence prevents a carrier assignment.'
    elif pure_benign(priority.get('current_classification')) or (status=='snapshot_only' and pure_benign(priority.get('snapshot_classification'))):
        code='not_applicable';label='No pathogenic carrier assertion for this allele';reason='The available benign/likely benign classification does not support a disease-carrier label. This does not exclude other alleles in the gene.'
    elif priority.get('clinical_category')=='somatic_candidate':
        code='not_applicable';label='Unpaired somatic candidate; germline carrier status not assigned';reason='A somatic candidate does not establish an inherited carrier result.'
    elif status in {'confirmation_candidate','unconfirmed_genotype'} and gt:
        if 'autosomal recessive' in mode and 'dominant' not in mode:
            genes=lambda v:{re.sub(r':\d+$','',g.strip()) for g in re.split(r'[|;/]',v or '')} - {'.',''}
            other=any(x['variant']!=priority['variant'] and genes(e.get('gene')) & genes(x.get('gene')) and x.get('public_allele_match')=='matched' and pure_plp(x.get('public_classification')) for x in clinical)
            if copies==1 and not other:
                code='possible_recessive_carrier';label='Possible carrier — one altered copy';reason='If confirmed, one altered copy is consistent with carrier status for the reviewed recessive condition. A second disease-causing allele has not been excluded.'
            elif copies==2 or other:
                code='recessive_genotype_unresolved';label='Two-copy / phase review needed';reason='A homozygous call or another candidate in this gene prevents a simple carrier assignment. Genotypes and, for multiple alleles, phase remain unresolved.'
            elif copies==0:
                code='allele_not_supported';label='Altered allele not supported';reason='The available genotype does not support this alternate allele; this is not a negative gene-level carrier screen.'
        elif 'autosomal dominant' in mode and 'recessive' not in mode and copies in (1,2):
            code='dominant_disease_candidate';label='Dominant candidate — not a simple carrier result';reason='One altered copy can be relevant to the reviewed dominant condition. The candidate genotype does not establish disease or penetrance.'
    result=dict(zip(FIELDS,[code,label,reason,inheritance,value or '.',source]));result.update(zyg)
    result['status_summary']=zyg['zygosity_label']+'; '+label+'; affected status not established'
    return result
