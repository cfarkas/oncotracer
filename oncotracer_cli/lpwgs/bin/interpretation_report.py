#!/usr/bin/env python3
"""Build an integrated interpretation from completed evidence, with local LLM drafts."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import statistics
import subprocess
import sys

from extension_common import dump, runtime_signature, sha256
import llm_reporting
import variant_priority as vp
from review_priority import rank_models,PGS_METHOD,VARIANT_METHOD
from clinical_narrative import article
from sample_variant_status import METHOD as STATUS_METHOD

NOTE='Research interpretation of low-pass whole-genome data. A support score is not a disease probability. Imputation uses the same sequencing reads and is not independent confirmation. Disease diagnoses, calibrated personal risk and clinical exclusion tests are not established by this report.'


def load(path,sample=None):
    x=json.loads(Path(path).read_text())
    if sample and x.get('sample') not in {None,sample}:raise ValueError('Mixed sample input: '+str(path))
    return x


def compact_clinical(row,priority):
    return {'variant':row['variant'],'gene':row['gene'],'genotype_status':row['category'],
            'public_allele_match':row.get('public_allele_match','not_verified'),'classification':row['public_classification'],
            'review':row['public_review'],'public_conditions':sorted({c for a in (row.get('public_record') or {}).get('assertions',[]) if not a.get('flagged') for c in a.get('conditions',[])}) if row.get('public_allele_match')=='matched' else [],'inheritance':row['inheritance_context'],'interpretation':priority.get('clinical_note',''),
            'support_score':priority.get('support_score'),'read_DP':priority.get('read_DP'),'read_ALT':priority.get('read_ALT'),
            'read_support_description':('Targeted BAM audit: '+str(priority['read_ALT'])+' alternate reads among '+str(priority['read_DP'])+' filtered reads. These counts are not independent confirmation.') if priority.get('read_source')=='targeted_filtered_BAM_reads' else 'Read counts are caller-derived or unavailable; inspect the evidence table.',
            'imputed_genotype':priority.get('imputed_genotype'),'support_flags':priority.get('support_flags'),
            'carrier_context':priority.get('patient_meaning','Inheritance context unresolved'),
            'variant_status':{k:priority.get(k) for k in ['zygosity_label','status_genotype','zygosity_source','zygosity_evidence','carrier_status','carrier_label','affected_status']},
            'curated_context':row['explanation'][-1600:] if 'Curated literature review' in row['explanation'] else 'Structured clinical record; no manually reviewed mechanism is supplied.',
            'sources':row['sources'].split('; ')}


def scoped_paragraphs(section,paragraphs):
    """Keep ancestry prose on its supplied topic; preserve edits in the audit."""
    if section['id'] not in {'ancestry','maternal','archaic'}:return paragraphs
    result=[];removed=[]
    for paragraph in paragraphs:
        kept=[]
        for sentence in re.split(r'(?<=[.!?])\s+',paragraph['text']):
            if re.search(r'\b(?:carriers?|diseases?|pathogenic|recessive|dominant|clinical|risk|somalier)\b',sentence,re.I):removed.append(sentence)
            else:kept.append(sentence)
        if kept:result.append({**paragraph,'text':' '.join(kept)})
    if removed:section['scope_cleanup']={'reason':'Clinical commentary is outside this ancestry section; its specific limitations remain in the conclusion.','removed_sentences':removed}
    return result


def build_sections(c,ex,anc,qc,evidence,models,priorities):
    ps={r['variant']:r for r in priorities};clin=ex['clinical'];sections=[]
    def section(id,title,facts,conclusion,sources=(),generate=True):
        facts=[f for f in facts if f]
        records=[{'id':'E'+str(n+1),'text':fact} for n,fact in enumerate(facts)]
        records.append({'id':'E'+str(len(records)+1),'text':conclusion})
        sections.append({'id':id,'title':title,'evidence':records,'conclusion':conclusion,'sources':list(dict.fromkeys(sources)),
                         'generate':generate,'paragraphs':[],'generation_status':'deterministic_evidence'})
    confirmed=[r for r in priorities if r['interpretation_status']=='confirmation_candidate']
    holds=[r for r in priorities if r['interpretation_status']=='annotation_hold']
    labels=[r for r in priorities if r['variant'] in {a['variant'] for a in clin}]
    leading=[r for r in labels if r.get('review_priority_score',0)>0][:5]
    leading_text='; '.join(r['gene']+' '+r['variant']+' (review priority '+str(r['review_priority_score'])+'/100; genotype support '+str(r['support_score'])+'/100; '+r.get('genetic_context','unresolved')+')' for r in leading)
    carrier_findings=[r for r in labels if r.get('genetic_context','').startswith('Possible carrier')]
    section('overview','The most relevant results',[
        f"{len(labels)} local pathogenic-label alleles received an exact-allele review. {len(holds)} annotation mismatch(es) remain withheld. Review priority and genotype support are separate measures.",
        'Highest review priorities: '+(leading_text or 'No clinical candidates meet the current review criteria.'),
        'Possible recessive carrier findings receive a lower review priority. '+('; '.join(r['gene']+' '+r['variant']+' — '+r['patient_meaning'] for r in carrier_findings) or 'No isolated recessive carrier interpretation was assigned.'),
        'Mean filtered autosomal depth: '+str(round(qc.get('mean_autosomal_depth_after_filters',0),2))+' times.'
    ],'The highest review priorities identify candidates to investigate, not confirmed disease-causing genotypes. Database pathogenicity describes an allele; sequencing and imputation support the sample genotype. No diagnosis or clinical-grade negative result is established.',generate=False)
    sections[-1]['generation_status']='structured_review_summary'
    sections[-1]['paragraphs']=[{'text':f"The exact-allele review covers {len(labels)} variants with a local pathogenic label. The highest review priorities are "+(leading_text or 'unresolved')+'. '+sections[-1]['conclusion'],'evidence_ids':['E1','E2','E5']}]
    if carrier_findings:
        sections[-1]['paragraphs'].append({'text':'Possible recessive carrier findings are ranked lower because one altered copy alone generally does not cause the relevant recessive disorder. '+ '; '.join(r['gene']+' '+r['variant']+': '+r['patient_meaning'] for r in carrier_findings),'evidence_ids':['E3']})
    selected=[r for r in clin if ps.get(r['variant'],{}).get('interpretation_status')=='confirmation_candidate']
    section('clinical','Germline findings and possible carrier status',[
        json.dumps(compact_clinical(r,ps[r['variant']]),ensure_ascii=False) for r in selected
    ] or ['No variant met the current public-identity, classification and sample-evidence criteria for prioritization.'],
        'For a recessive condition, one heterozygous pathogenic allele is a possible carrier finding if independently confirmed. This dataset does not exclude an undetected second allele. Rare imputation remains uncertain even with a high model posterior. No phenotype or disease is diagnosed.',
        [u for r in selected for u in r['sources'].split('; ')],generate=False)
    sections[-1]['generation_status']='structured_clinical_interpretation'
    sections[-1]['fallback_paragraphs']=[{'text':r['gene']+' '+r['variant']+'. '+' '.join(p['text'] for p in article(r,ps[r['variant']],clin)['sections'][1:]),'evidence_ids':['E'+str(i+1)]} for i,r in enumerate(selected)]
    sections[-1]['paragraphs']=sections[-1]['fallback_paragraphs']
    cardiac=[r for r in clin if any(t in re.split(r'[|;,: /]+',r['gene'].upper()) for t in ['TTN','RYR2','MYH7','MYBPC3','LMNA','DSP','TNNT2','TNNI3']) or any('cardiomyopath' in str(o.get('condition','')).lower() for o in r['observations'])]
    cardio_models=[r for r in models if 'cardiomyopath' in r['trait'].lower()]
    section('cardiac','Cardiomyopathy and cardiac findings',[
        json.dumps(compact_clinical(r,ps.get(r['variant'],{})),ensure_ascii=False) for r in cardiac
    ]+['Cardiomyopathy score models: '+json.dumps([{'id':m['pgs_id'],'trait':m['trait'],'marker_coverage_percent':round(100*m['variant_coverage'],2),'status':m['status']} for m in cardio_models])],
        'Polygenic marker coverage describes included model terms, not a personal genetic risk signal. Unconfirmed cardiac-gene candidates must not be treated as established cardiac findings. Optional reference percentiles compare the retained score terms with reference individuals; they are not calibrated disease risks. These data cannot confirm or exclude cardiomyopathy or inherited arrhythmia.',
        [u for r in cardiac for u in r['sources'].split('; ')]+[r.get('catalog_url','') for r in cardio_models],generate=False)
    sections[-1]['generation_status']='structured_cardiac_interpretation'
    sections[-1]['paragraphs']=[{'text':r['gene']+' '+r['variant']+'. '+' '.join(p['text'] for p in article(r,ps[r['variant']],clin)['sections'][1:]),'evidence_ids':['E'+str(i+1)]} for i,r in enumerate(cardiac)]
    rejected=[r for r in labels if r['interpretation_status'] in {'annotation_hold','flagged_assertion','limited_clinical_evidence','downgraded_current_assertion','uncertain_current_assertion'}]
    section('assertions','Why a pathogenic label may not be a clinical finding',[
        json.dumps({k:r[k] for k in ['variant','gene','interpretation_status','current_classification','clinical_review','clinical_note']}) for r in rejected
    ],'A disease label in an annotation table does not establish disease in the sample. Public allele-identity mismatches are withheld. Flagged assertions are excluded from clinical conclusions. Complex-trait associations and weak-review assertions are not treated as Mendelian diagnoses.',
        [u for r in rejected for u in r['sources'].split('; ')])
    adm=anc.get('admixture',{});mt=anc.get('mitochondrial',{});neand=anc.get('neanderthal',{})
    composition=[{'reference_component':r['component'],'percent':round(100*r['fraction'],2),'jackknife_lower_percent':round(100*r['lower'],2),'jackknife_upper_percent':round(100*r['upper'],2)} for r in adm.get('components',[])]
    section('ancestry','Broad population composition',[
        'Exploratory genetic composition for this one individual sample, not population frequencies: '+json.dumps(composition),
        'Status: '+str(adm.get('status','not_available'))+'. This is a projection against broad reference groups, not a validated consumer ancestry test.'
    ],'The largest components describe broad genetic similarity. Small components are particularly uncertain. Chromosome jackknife ranges do not capture all model, reference and imputation errors. This cannot establish nationality, tribal membership or recent genealogy.',
        [anc.get('resources',{}).get('model_source','')])
    section('maternal','Maternal haplogroup and geographic context',[
        'Estimated mitochondrial haplogroup: '+str(mt.get('haplogroup') or 'unresolved')+'. Classification quality metric: '+str(mt.get('classification_quality','unavailable'))+'. This metric is not a probability of geographic origin.',
        'Mitochondrial mean depth: '+str(round(mt.get('mean_depth',0),2))+' times. Callable bases: '+str(mt.get('callable_bases',0))+'. Callable percent: '+str(round(100*mt.get('callable_fraction',0),2))+'.',
        mt.get('geographic_context',{}).get('text','No separately reviewed regional origin context is available for this lineage.')+' Geographic context refers only to the branch explicitly named in that source; it does not establish that the exact terminal subclade was observed in the cited study.'
    ],'Mitochondrial DNA follows one maternal line. It does not summarize all ancestors. Geographic associations of a lineage cannot prove a recent birthplace or membership of a particular community. This is a lineage analysis, not a clinical mitochondrial disease or heteroplasmy assessment.',
        [mt.get('geographic_context',{}).get('source',''),mt.get('source','')])
    section('archaic','Neanderthal-associated marker evidence',[
        'Carried markers: '+str(neand.get('carried_markers','unavailable'))+'. Callable markers: '+str(neand.get('callable_markers','unavailable'))+'. Derived allele copies: '+str(neand.get('derived_allele_copies','unavailable'))+'.',
        neand.get('scope','Archaic-marker analysis is unavailable.')
    ],'The marker tally is not a genome-wide Neanderthal percentage or a population percentile. Correlated markers and ascertainment bias prevent converting this count into an ancestry fraction or a personal health interpretation.',[neand.get('source','')])
    traits=ex['traits'];specific=[r for r in traits if 'HIrisPlex' not in r['trait']]
    rule_counts=dict(Counter(r.get('model','unknown') for r in traits))
    section('traits','Traits that can be discussed from the available markers',['Marker interpretation rules in this panel: '+json.dumps(rule_counts)+'. Treat each marker separately: a genotype rule and an association-only marker are different evidence types.']+[
        json.dumps({k:r[k] for k in ['rsid','trait','genotype','status','confidence','model','sample_explanation','limitations']}) for r in specific[:12]
    ],'Use the explicit interpretation for each marker. Do not describe genotype-rule entries as association-only markers without a direction. Genotype confidence and phenotype predictability are different. A marker association cannot predict a whole phenotype. Lactase-persistence markers do not diagnose digestive symptoms. Pigmentation markers need a validated joint model for phenotype probabilities. Association-only rows carry no individual effect direction.',[u for r in specific for u in r['sources'].split('; ')])
    # Individual trait interpretations already follow explicit allele rules.
    # Preserve them verbatim instead of asking a small language model to merge
    # dominant/recessive rules with association-only markers.
    sections[-1]['generate']=False
    sections[-1]['generation_status']='structured_trait_interpretation'
    sections[-1]['paragraphs']=[{'text':r['sample_explanation'],'evidence_ids':['E'+str(i+2)]} for i,r in enumerate(specific[:12]) if r.get('model')!='association_only']
    cover=[m['variant_coverage'] for m in models]
    section('polygenic','Common diseases and polygenic models',[
        f"Polygenic and trait models calculated: {len(models)}. Distinct trait labels: {len(set(m['trait'] for m in models))}. Status counts: {dict(Counter(m['status'] for m in models))}.",
        'Median retained marker percentage: '+str(round(100*statistics.median(cover),2) if cover else 'unavailable')+'. This is the fraction of model terms retained, not a measurement of genotype accuracy or data fidelity. Missing terms remain missing; partial sums are not rescaled.',
        'Exploratory reference comparisons available for '+str(sum(m.get('reference_percentile') is not None for m in models))+' models. Reference percentiles use the exact retained model terms; they compare score position, not disease probability. The imputation reference is also the comparison panel, so this is not independent clinical validation.',
        'Model categories: '+', '.join(sorted(set(m.get('category','Unclassified') for m in models)))
    ],'The model panel provides research weighted sums, not established personal disease-risk estimates. Broad admixture proportions alone do not calibrate a score for an admixed individual. Sex, age, phenotype, training population and independent validation still matter. Scores across different models are not directly comparable.',
        ['https://www.pgscatalog.org/']+[m.get('catalog_url','') for m in cardio_models])
    # Keep model/trait counts and coverage on their actual scales; generated
    # prose must not turn retained terms into accuracy or personal risk.
    sections[-1]['generate']=False
    sections[-1]['generation_status']='structured_polygenic_interpretation'
    sections[-1]['paragraphs']=[{'text':f"{len(models)} published polygenic/trait models cover {len(set(m['trait'] for m in models))} distinct trait labels in this panel. "+('The median fraction of retained model terms is '+str(round(100*statistics.median(cover),2))+' percent. ' if cover else 'No model scores are available. ')+"The tables include exploratory reference percentiles when calculated, using the same retained terms in the sample and reference individuals. A 90th percentile means a score above approximately 90% of that reference group, not a 90% probability of disease. This comparison reuses the imputation panel and does not fully adjust for admixture. Marker retention is not a measure of genotype accuracy or individual disease risk. Missing terms are not rescaled. "+sections[-1]['conclusion'],'evidence_ids':[r['id'] for r in sections[-1]['evidence']]}]
    counts=evidence.get('category_counts',{})
    section('somatic','Germline, somatic candidates and unresolved calls',[
        'Numbers of variants in this one sample (not numbers of people or cases), by observed-call union evidence category: '+json.dumps(counts)+'. Confirmed somatic variants: '+str(evidence.get('confirmed_somatic_variants',0))+'.',
        evidence.get('scope','')
    ],'Germline_supported is a pipeline evidence category, not independent confirmation of every genotype. A somatic caller label does not prove acquired biological origin. Without a matched normal and sufficient sequence evidence, somatic candidates remain unconfirmed. Imputation can support a population-compatible germline genotype; failure to impute a variant does not establish somatic origin.',generate=False)
    sections[-1]['generation_status']='structured_variant_counts'
    sections[-1]['paragraphs']=[{'text':'The observed-call union contains '+f"{counts.get('germline_supported',0):,}"+' germline-supported variants, '+f"{counts.get('somatic_candidate',0):,}"+' somatic candidates and '+f"{counts.get('unconfirmed',0):,}"+' unresolved variants. These are counts of variant records in one sample. '+sections[-1]['conclusion'],'evidence_ids':['E1','E2','E3']}]
    section('quality','Sequencing quality and the limits of this dataset',[
        'Mean filtered autosomal depth: '+str(round(qc.get('mean_autosomal_depth_after_filters',0),2))+' times. This is sparse low-pass nuclear coverage. Fraction of autosomal bases covered: '+str(round(100*qc.get('autosomal_fraction_covered',0),2))+' percent.',
        'Read filters: mapping quality at least '+str(c['min_mapping_quality'])+'; base quality at least '+str(c['min_base_quality'])+'.',
        'GLIMPSE2 estimates autosomal SNP genotypes represented in its reference panel, including some rare SNPs. Unrepresented alleles cannot be imputed; rare-variant accuracy is limited. Rare variants, indels, structural variants and mitochondrial disease are not comprehensively excluded.'
    ],'Low depth can miss alternate alleles and misestimate zygosity. Mitochondrial depth is separate from nuclear depth and cannot compensate for it. High imputation posterior confidence does not turn this dataset into a clinical high-depth sequencing assay.',generate=False)
    sections[-1]['generation_status']='structured_sequencing_quality'
    sections[-1]['paragraphs']=[{'text':' '.join(r['text'] for r in sections[-1]['evidence'][:3])+' The sequence callers also evaluate variants outside the imputation panel; a missing imputation result does not mean the variant is absent. '+sections[-1]['conclusion'],'evidence_ids':['E1','E2','E3','E4']}]
    section('prioritization','How to use review priority and genotype support',[
        json.dumps(vp.METHOD),json.dumps(VARIANT_METHOD),json.dumps(PGS_METHOD),
        'The initial variant order is descending review priority, with genotype support used to break ties. Possible isolated recessive carriers receive a deduction; known disease-compatible inheritance patterns receive a bonus. Annotation and assertion holds remain excluded. The support-only view is available separately.'
    ],'Review priority organizes follow-up; genotype support describes the underlying evidence. Neither is a disease probability. Polygenic models are ordered using their topic, retained terms and exploratory reference percentile, never raw weighted-sum magnitude.',generate=False)
    sections[-1]['generation_status']='structured_priority_method'
    sections[-1]['paragraphs']=[{'text':sections[-1]['evidence'][3]['text']+' '+sections[-1]['conclusion'],'evidence_ids':['E1','E2','E3','E4','E5']}]
    return sections


def run(a,c):
    if c['sample']!=a.sample:raise ValueError('Sample/config mismatch')
    out=Path(a.sample+'.interpretation');out.mkdir(exist_ok=True)
    ex_path=Path(a.explanations)/(a.sample+'.explanations.json');ex=load(ex_path,a.sample)
    if ex.get('runtime_signature')!=runtime_signature(c):raise ValueError('Explanation report is stale for this run')
    anc_paths=[Path(x)/(a.sample+'.ancestry_explorer.json') for x in a.ancestry]
    anc=load(anc_paths[0],a.sample) if anc_paths else {}
    if anc and anc.get('runtime_signature')!=runtime_signature(c):raise ValueError('Ancestry report is stale for this run')
    qc=load(a.qc);evidence=load(a.evidence_summary,a.sample)
    from pgs_reference_stats import attach
    from patient_metadata import resolve
    patient=resolve(c,getattr(a,'coverage',None) or Path(c['output'])/'alignment'/(a.sample+'.coverage.txt'))
    models=rank_models(attach([load(p,a.sample) for p in a.scores],getattr(a,'reference_stats',[])),c.get('pgs_priority_focus',''),patient)
    observed=next((p for p in a.evidence_tables if Path(p).name==a.sample+'.clinvar.tsv.gz'),None)
    priorities,audits=vp.prioritize(observed,a.imputed_clinvar_tables,ex,a.bam,c)
    vp.exports(out,a.sample,priorities,ex)
    sections=build_sections(c,ex,anc,qc,evidence,models,priorities)
    public_source_ids={}
    for sec in sections:
        sec['sources']=[u for u in sec['sources'] if u.startswith('https://')]
        for u in sec['sources']:
            if u not in public_source_ids:public_source_ids[u]='R'+str(len(public_source_ids)+1)
    packet={'config':{k:c.get(k) for k in llm_reporting.KEYS},'sections':sections}
    request=out/'llm_request.json';response=out/'llm_audit.json';dump(request,packet)
    generation={'requested':bool(c.get('report_llm')),'status':'not_requested','generated_sections':0,'total_sections':len(sections),'requested_sections':sum(s.get('generate',True) for s in sections),
                'validation_scope':'Format and unsupported numerical values are checked. Citations identify the section evidence supplied to the model; they do not establish claim-level support or scientific truth. Draft prose is displayed alongside deterministic facts and conclusions.'}
    if c.get('report_llm'):
        env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([c['llm_python'],str(Path(__file__).with_name('llm_reporting.py')),str(request),str(response)],env=env)
        if result.returncode:raise RuntimeError('Local LLM generation failed; analysis outputs remain available. See the report process log.')
        draft=load(response);trials={r['section']:r for r in draft['trials']}
        for section in sections:
            if not section.get('generate',True):continue
            trial=trials.get(section['id'],{})
            if trial.get('status')=='generated_draft':
                section['paragraphs']=scoped_paragraphs(section,trial['paragraphs']);section['generation_status']='local_llm_draft';generation['generated_sections']+=1
            else:
                section['generation_status']='deterministic_fallback'
                section['paragraphs']=section.get('fallback_paragraphs',[])
        generation['status']='generated_draft' if generation['generated_sections']==generation['requested_sections'] else 'partial_draft' if generation['generated_sections'] else 'deterministic_fallback'
        generation['model']=draft['metadata']
    else:dump(response,{'metadata':{'status':'not_requested'},'trials':[]})
    selected={r['variant'] for r in ex['clinical']}
    paths=[ex_path,a.qc,a.evidence_summary,*anc_paths,*a.scores,*([observed] if observed else []),*a.imputed_clinvar_tables,*getattr(a,'reference_stats',[]),*([a.coverage] if getattr(a,'coverage',None) else [])]
    summary={'sample':a.sample,'runtime_signature':runtime_signature(c),'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'pdf_expected':bool(c.get('report_pdf',True)),'method':'integrated-interpretation-v2','test_mode':c.get('test_mode',False),'note':NOTE,'generation':generation,'patient':patient,'score_method':{**vp.METHOD,'active_thresholds':{k:c[k] for k in ['min_mapping_quality','min_base_quality','imputation_min_gp','imputation_min_info']}},
             'variant_status_method':STATUS_METHOD,'priority_methods':{'polygenic':PGS_METHOD,'variants':VARIANT_METHOD},'priority_focus':c.get('pgs_priority_focus',''),'sections':sections,'priority_findings':[r for r in priorities if r['variant'] in selected],
             'priority_counts':dict(Counter(r['priority_group'] for r in priorities)),'scored_clinvar_variants':len(priorities),
             'read_audit':audits,'ancestry':anc,'qc':qc,'variant_evidence':evidence,'traits':ex['traits'],'clinical':ex['clinical'],
             'models':[dict(m,model_metadata=None) for m in models],
             'clinical_articles':[article(r,next((p for p in priorities if p['variant']==r['variant']),{}),ex['clinical']) for r in ex['clinical']],
             'sources':[{'id':v,'url':k} for k,v in public_source_ids.items()],
             'input_provenance':[{'file':str(Path(p).resolve()),'bytes':Path(p).stat().st_size,'sha256':sha256(p)} for p in paths],
             'bam_provenance':{'path':str(Path(a.bam).resolve()),'bytes':Path(a.bam).stat().st_size,'mtime_ns':Path(a.bam).stat().st_mtime_ns}}
    path=out/(a.sample+'.interpretation.json');dump(path,summary)
    from interpretation_render import render_html
    render_html(summary,out)
    if c.get('report_pdf',True):
        subprocess.run([c['llm_python'],str(Path(__file__).with_name('interpretation_render.py')),'--pdf',str(path)],check=True)
    print(json.dumps({'sample':a.sample,'scored_clinvar_variants':len(priorities),'generation':{k:v for k,v in generation.items() if k!='model'},'output':str(out)},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ['config','sample','explanations','bam','qc','evidence-summary']:p.add_argument('--'+flag,required=True)
    for flag in ['ancestry','scores','evidence-tables','imputed-clinvar-tables','reference-stats']:p.add_argument('--'+flag,nargs='*',default=[])
    p.add_argument('--coverage')
    a=p.parse_args();run(a,load(a.config))
