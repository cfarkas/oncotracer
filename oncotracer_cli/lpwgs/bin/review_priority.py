"""Deterministic review ordering; independent of raw PGS magnitude."""
import math
VERSION='review-order-v1'
PGS_METHOD={
 'version':VERSION,
 'meaning':'A report navigation heuristic, not disease risk, severity, predictive accuracy or clinical actionability.',
 'formula':'Topic points + 20 times the smaller coverage fraction + up to 40 reference-percentile points for disease models.',
 'topic_points':'Requested focus 40; disease models 30; blood/metabolic biomarkers 10; physical traits 0; unclassified 0.',
 'statistics':'Reference-percentile points = 0.4 times the selected reference percentile for disease models only. This orders higher published scores; it is not probability of disease. Physical-trait magnitudes do not imply disease importance.',
 'eligibility':'Failed or unsupported models receive 0 and require technical review.',
 'calibration':'Raw sum magnitude and sign never affect priority. Personal risk calibration and ancestry, age, sex and phenotype applicability remain unestablished.'}
VARIANT_METHOD={
 'version':VERSION,'meaning':'Review order only, not disease risk, severity or an ACMG classification.',
 'formula':'Clinical review group base + 0.2 times genotype support + 10 for a known disease-compatible inheritance pattern, minus 35 for a possible isolated recessive carrier.',
 'group_points':'Confirmation candidates 80; unconfirmed clinical candidates 60; uncertain/conflicting assertions 40; limited clinical evidence 20.',
 'inheritance':'The inheritance context must be explicit and exact-allele review must support a pathogenic/likely pathogenic assertion. Genotype patterns remain unconfirmed unless independently established. Unknown inheritance receives no bonus or carrier penalty.',
 'exclusions':'Annotation/assertion holds and other annotations receive 0. High genotype support never removes a clinical hold.'}
PGS_FIELDS=['review_rank','review_priority_score','review_priority_group','priority_topic_points','priority_completeness_points','priority_statistics_points','reference_population','reference_percentile','reference_z','reference_n','reference_percentile_low','reference_percentile_high','priority_reason','calibration_status','applicability_status']

def fraction(value):
 try:
  value=float(value)
  return value if math.isfinite(value) and 0<=value<=1 else None
 except (TypeError,ValueError):return None

def rank_models(models,focus='',patient=None):
 terms=[x.strip().casefold() for x in str(focus or '').split(',') if x.strip()];result=[]
 for model in models:
  r=dict(model);marker,weight=fraction(r.get('variant_coverage')),fraction(r.get('absolute_weight_coverage'))
  match=[t for t in terms if t in str(r.get('trait','')).casefold() or t in str(r.get('category','')).casefold()];category=r.get('category')
  if match:base,group=40,'Requested focus'
  elif category in {'Common diseases','Cardiomyopathy'}:base,group=30,'Disease models'
  elif category=='Blood and metabolic traits':base,group=10,'Biomarker models'
  elif category=='Physical traits':base,group=0,'Physical trait models'
  else:base,group=0,'Unclassified models'
  valid=r.get('status') in {'complete_uncalibrated','partial_above_coverage_threshold'} and marker is not None and weight is not None
  points=round(20*min(marker,weight),2) if valid else 0
  percentile=fraction(float(r['reference_percentile'])/100) if r.get('reference_percentile') is not None else None
  statistical=round(40*percentile,2) if percentile is not None and category in {'Common diseases','Cardiomyopathy'} else 0
  not_applicable=(patient or {}).get('sex')=='female' and ('prostate' in r.get('trait','').lower() or '(male)' in r.get('trait','').lower())
  r.update(priority_statistics_points=statistical,review_priority_score=round(base+points+statistical,2) if valid and not not_applicable else 0,review_priority_group=group if valid else 'Technical review required',priority_topic_points=base if valid else 0,priority_completeness_points=points,
   priority_reason=(f'{group}: {base} topic points; {points:.2f} completeness points; {statistical:.2f} reference-percentile points.' if valid else 'Technical review required: incomplete, unsupported or unavailable score.')+(' Focus: '+', '.join(match)+'.' if match else ''),
   calibration_status='Personal risk uncalibrated',applicability_status='Ancestry, age, sex and phenotype applicability not established')
  if not_applicable:r['applicability_status']='Not prioritized with recorded female sex; review anatomical applicability';r['priority_reason']+=' Sex-specific applicability requires review.'
  result.append(r)
 result.sort(key=lambda r:(-r['review_priority_score'],str(r.get('trait','')),str(r.get('pgs_id',''))))
 for n,r in enumerate(result,1):r['review_rank']=n
 return result

def rank_variants(rows,clinical=()):
 from clinical_narrative import article
 from carrier_context import assess
 from variant_priority import genotype
 by_variant={r['variant']:r for r in clinical}
 bases={'1_confirm_and_review':80,'2_unconfirmed_clinical_candidate':60,'3_uncertain_or_conflicting_assertion':40,'4_limited_clinical_evidence':20};result=[]
 for original in rows:
  r=dict(original);base=bases.get(r['priority_group'])
  e=by_variant.get(r['variant']);r.update(genetic_context='Inheritance unresolved',patient_meaning=r.get('clinical_note',''),inheritance_priority_adjustment=0)
  r.update(assess(r,e,clinical))
  if e:
   a=article(e,r,clinical);r['patient_meaning']=a['sections'][3]['text'];r['genetic_context']=a['title']
   inheritance=e.get('inheritance_context','').lower();gt=genotype(r.get('status_genotype'));copies=gt.count(r['variant'].split(':')[-1]) if gt else None
   if r['carrier_status']=='possible_recessive_carrier':r['inheritance_priority_adjustment']=-35
   elif r['interpretation_status'] in {'confirmation_candidate','unconfirmed_genotype'} and ((copies==1 and 'autosomal dominant' in inheritance and 'recessive' not in inheritance) or (copies==2 and 'autosomal recessive' in inheritance and 'dominant' not in inheritance)):
    r['inheritance_priority_adjustment']=10;r['genetic_context']='Potential disease-compatible genotype; unconfirmed'
  r.update(assess(r,e,clinical))
  r['review_priority_score']=round(min(100,max(0,base+.2*r['support_score']+r['inheritance_priority_adjustment'])),2) if base is not None else 0.0
  r['review_priority_reason']=f'{base} clinical review group points + 20% of genotype support; inheritance adjustment {r["inheritance_priority_adjustment"]}.' if base is not None else 'Excluded from clinical prioritization; see assertion status.'
  result.append(r)
 result.sort(key=lambda r:(-r['review_priority_score'],-r['support_score'],r['variant']))
 for n,r in enumerate(result,1):r['review_rank']=n
 return result
