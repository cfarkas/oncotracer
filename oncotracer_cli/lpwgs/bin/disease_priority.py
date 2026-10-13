"""Auditable condition review order, never a disease-probability estimate."""
import re
import math
from collections import defaultdict

METHOD = ('Priority / 100 is the maximum of an eligible clinical variant review priority, '
          'an exactly matched disease-model review priority, or a small GWAS evidence score '
          '(10 direct, 8 imputed, 3 exploratory, 0 unresolved). Counts of studies do not add '
          'points. Carrier deductions and clinical holds are retained. This is review order, '
          'not severity, a diagnosis or a probability of disease. Condition links use normalized '
          'exact labels; near-synonyms are not silently treated as equivalent.')
FIELDS = ['review_rank','review_priority_score','priority_basis','priority_variant','priority_model']
EMPTY = {'gwas_rows':0,'gwas_called':0,'gwas_imputed':0,'gwas_exploratory':0,
         'gwas_unresolved':0,'clinical_rows':0,'germline_review_candidates':0,
         'somatic_review_candidates':0,'clinical_low_evidence':0,'classification_counts':{}}

def normalized(label):
    return re.sub(r'[^a-z0-9]+',' ',str(label).casefold()).strip()

def number(value):
    try:
        n=float(value)
        return min(100.,max(0.,n)) if math.isfinite(n) else 0.
    except (ValueError,TypeError):return 0.

def prioritize(conditions,interpretation=None,polygenic=None):
    interpretation=interpretation or {};polygenic=polygenic or {}
    variants=defaultdict(list);models=defaultdict(list)
    for r in interpretation.get('priority_findings',[]):
        # An old annotation mismatch must never acquire priority through its label.
        if r.get('interpretation_status') in {'annotation_hold','flagged_assertion','identity_unverified'} or str(r.get('priority_group','')).startswith('5_'):continue
        if number(r.get('review_priority_score'))<=0:continue
        for label in str(r.get('condition','')).split('|'):
            key=normalized(label)
            if key not in {'','not provided','not specified','not verified'}:variants[key].append(r)
    result=[dict(r) for r in conditions];keys={normalized(r['condition']) for r in result}
    for m in polygenic.get('results',interpretation.get('models',[])):
        if m.get('category') not in {'Common diseases','Cardiomyopathy'}:continue
        key=normalized(m['trait']);models[key].append(m)
        if key not in keys:
            result.append({**EMPTY,'condition':m['trait'],'category':m['category'],'sources':['PGS Catalog']})
            keys.add(key)
    for r in result:
        key=normalized(r['condition']);vs=variants.get(key,[]);ms=models.get(key,[])
        choices=[(0.,'No prioritized genotype or disease-model evidence','','')]
        for v in vs:choices.append((number(v.get('review_priority_score')),'Clinical variant review: '+str(v.get('genetic_context') or v.get('interpretation_status')),v['variant'],''))
        for m in ms:choices.append((number(m.get('review_priority_score')),'Disease model review; exploratory reference comparison; '+str(m.get('applicability_status','')), '',m['pgs_id']))
        for column,points,label in [('gwas_called',10,'Direct GWAS genotype'),('gwas_imputed',8,'Imputed GWAS genotype'),('gwas_exploratory',3,'Exploratory GWAS genotype')]:
            if r.get(column,0):choices.append((float(points),label+'; risk direction unresolved','',''))
        score,basis,variant,model=max(choices,key=lambda x:x[0])
        r.update(review_priority_score=score,priority_basis=basis,priority_variant=variant,priority_model=model)
        if ms:r['sources']=sorted(set(r.get('sources',[]))|{'PGS Catalog'})
    result.sort(key=lambda r:(-r['review_priority_score'],str(r['condition']).casefold(),str(r.get('category',''))))
    for n,r in enumerate(result,1):r['review_rank']=n
    return result
