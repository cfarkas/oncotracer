#!/usr/bin/env python3
"""Dedicated, sample-agnostic cardiomyopathy review of existing evidence."""
import argparse,json,re
from pathlib import Path
from extension_common import dump,table,Excel,runtime_signature
from variant_priority import rows,number
from sample_variant_status import FIELDS as STATUS_FIELDS
from clinical_narrative import readable
from genome_report_ui import write,section,cards,esc,table as html_table
VERSION='cardiomyopathy-review-v1'
# A transparent focused review set, not a declaration that every gene/allele has equal validity.
GENES={'hypertrophic':set('MYBPC3 MYH7 TNNT2 TNNI3 TPM1 ACTC1 MYL2 MYL3 TNNC1 ACTN2 CSRP3 ALPK3'.split()),'dilated':set('BAG3 DES FLNC LMNA MYH7 PLN RBM20 SCN5A TNNC1 TNNT2 TTN DSP'.split()),'arrhythmogenic':set('PKP2 DSP DSG2 DSC2 JUP TMEM43 PLN DES'.split())}
RELATED=set('FBN1 TGFBR1 TGFBR2 SMAD3 TGFB2 TGFB3 RYR2 CASQ2 KCNQ1 KCNH2 SCNN1B'.split())
SOURCES=['https://pubmed.ncbi.nlm.nih.gov/33947203/','https://www.ncbi.nlm.nih.gov/books/NBK1768/','https://pubmed.ncbi.nlm.nih.gov/37929589/']
FIELDS=STATUS_FIELDS+['review_priority_score','review_scope','variant','gene','current_classification','snapshot_classification','interpretation_status','carrier_label','support_score','read_genotype','read_DP','read_ALT','read_GQ','imputed_genotype','imputed_GP','imputed_INFO','condition','patient_meaning','sources']

def classify(row):
    genes={re.sub(r':\d+$','',s.strip()) for s in re.split(r'[|;/]',row.get('gene',''))};condition=row.get('condition','').lower().replace('_',' ')
    if 'cardiomyopath' in condition:return 'Cardiomyopathy condition annotation'
    hits=[name for name,gg in GENES.items() if genes & gg]
    if hits:return 'Cardiomyopathy gene overlap; condition-specific review needed'
    if genes & RELATED or any(x in condition for x in ['aortic aneurysm','aortic dissection','ventricular tachycardia','long qt','brugada']):return 'Related cardiac condition; separate from cardiomyopathy'
    return None

def run(a,c):
    folder=Path(a.interpretation);d=json.loads((folder/(a.sample+'.interpretation.json')).read_text())
    if d['sample']!=a.sample:raise ValueError('Cardiomyopathy report sample mismatch')
    out=Path(a.sample+'.cardiomyopathy');out.mkdir(exist_ok=True);selected=[]
    for original in rows(folder/(a.sample+'.variant_priority.tsv.gz')):
        scope=classify(original)
        if not scope:continue
        r=dict(original,review_scope=scope);r['condition']=readable(r['condition'])
        for key in ['review_priority_score','support_score','read_DP','read_ALT','read_GQ','imputed_GP','imputed_INFO']:r[key]=number(r.get(key))
        selected.append(r)
    selected.sort(key=lambda r:(-(r['review_priority_score'] or 0),-(r['support_score'] or 0),r['variant']))
    candidates=[r for r in selected if r['review_scope']=='Cardiomyopathy condition annotation' and r['interpretation_status'] in {'confirmation_candidate','unconfirmed_genotype'}]
    related=[r for r in selected if r['review_scope'].startswith('Related') and r['interpretation_status'] in {'confirmation_candidate','unconfirmed_genotype'}]
    models=[dict(m,marker_percent=round(100*m['variant_coverage'],2),weight_percent=round(100*m['absolute_weight_coverage'],2)) for m in d['models'] if 'cardiomyopath' in m.get('trait','').lower()]
    patient=d.get('patient',{});conclusion=(f'{len(candidates)} unconfirmed candidate allele(s) have a cardiomyopathy condition annotation.' if candidates else 'No currently reviewed pathogenic/likely pathogenic candidate with an explicit cardiomyopathy condition annotation was found in this review scope.')+' This pipeline has not established a cardiomyopathy diagnosis or excluded an inherited predisposition.'
    cols=[('Review priority','review_priority_score'),('Gene','gene'),('Variant','variant'),('Current ClinVar review','current_classification'),('Reported zygosity','zygosity_label'),('Carrier status','carrier_label'),('Affected status','affected_label'),('Genotype support','support_score'),('Read genotype','read_genotype'),('Depth','read_DP'),('Alternate reads','read_ALT'),('GLIMPSE2 genotype','imputed_genotype'),('GP','imputed_GP'),('Condition','condition'),('Interpretation','patient_meaning')]
    body=cards([(len(candidates),'unconfirmed cardiomyopathy candidates'),(len(models),'cardiomyopathy polygenic models'),(len(related),'other cardiac candidates')])
    body+=section('What was found','<p class="note">'+esc(conclusion)+'</p><p>Sample '+esc(patient.get('name',a.sample))+' · age '+esc(patient.get('age_display','Not available'))+' · recorded or inferred sex '+esc(patient.get('sex','unknown'))+'.</p>'+html_table(candidates,cols))
    by_article={r['variant']:r for r in d.get('clinical_articles',[])}
    if candidates:
        text=[]
        for r in candidates:
            article=by_article.get(r['variant'],{})
            text.append('<h3>'+esc(r['gene'])+' · '+esc(r['variant'])+'</h3>')
            for para in article.get('sections',[]):text.append('<h4>'+esc(para['title'])+'</h4><p>'+esc(para['text'])+'</p>')
            text.append('<p>'+' · '.join('<a href="'+esc(u)+'">ClinVar or supporting publication</a>' for u in article.get('sources',[]) if u.startswith('https://'))+'</p>')
        body+=section('Candidate interpretation and literature',''.join(text))
    modelcols=[('Priority','review_priority_score'),('Trait','trait'),('Model','pgs_id'),('Status','status'),('Markers retained %','marker_percent'),('Weight retained %','weight_percent'),('Reference population','reference_population'),('Reference N','reference_n'),('Reference percentile','reference_percentile'),('Percentile lower','reference_percentile_low'),('Percentile upper','reference_percentile_high')]
    body+=section('Polygenic evidence','<p>The percentiles compare retained model markers with the displayed reference group. A percentile is a position in that distribution, not a probability of developing cardiomyopathy. Different models may give different rankings. These scores are not calibrated for this patient’s age, sex, family history or absolute disease risk; the imputation and comparison panels are shared.</p>'+html_table(models,modelcols))
    body+=section('Other cardiovascular findings','<p>Arrhythmia and aortic disease annotations are displayed separately because they do not establish a cardiomyopathy finding.</p>'+html_table(related,cols))
    body+=section('All focused gene and condition annotations','<p>This includes benign, uncertain, historical and unconfirmed ClinVar labels. A gene overlap alone does not make an allele disease-causing. Original snapshot annotations remain visible alongside current review status.</p>'+html_table(selected,[('Review scope','review_scope')]+cols+[('Snapshot classification','snapshot_classification'),('Review status','interpretation_status')]))
    body+=section('Scope and next review steps','<p>This report reuses existing sequence calls, GLIMPSE2 genotypes and exact-allele ClinVar annotation. Rare variants with one supporting alternate read can be sequencing or alignment errors. Candidate alleles need targeted clinical confirmation and condition-specific assessment. A negative result from this low-pass dataset cannot exclude variants absent from the imputation panel, structural changes, copy-number variants, difficult loci or poorly covered exons.</p><p>The focused gene sets are shown below for reproducibility; they are not a comprehensive negative clinical panel. Gene-level validity does not establish the effect of a particular allele. Even a confirmed pathogenic genotype can show incomplete penetrance, so genotype alone does not establish symptoms or age of onset. A clinical cardiomyopathy assessment combines family history, cardiac evaluation and appropriately validated genetic testing.</p>'+''.join('<p><b>'+esc(k.capitalize())+':</b> '+esc(', '.join(sorted(v)))+'</p>' for k,v in GENES.items())+'<p>'+ ' · '.join('<a href="'+esc(u)+'">'+label+'</a>' for label,u in zip(['DCM gene evidence','HCM clinical overview','HCM penetrance study'],SOURCES))+'</p>')
    links=[('All results','../../START_HERE.html'),('Integrated interpretation','../../interpretation/'+a.sample+'.interpretation/'+a.sample+'.interpretation.html'),('Cardiomyopathy Excel',a.sample+'.cardiomyopathy.xlsx'),('Variant TSV',a.sample+'.cardiac_variants.tsv')]
    write(out/(a.sample+'.cardiomyopathy.html'),a.sample+' · cardiomyopathy review','A focused reading of inherited cardiomyopathy candidates, polygenic evidence and related cardiovascular findings.',body,links)
    table(out/(a.sample+'.cardiac_variants.tsv'),FIELDS,selected);book=Excel(out/(a.sample+'.cardiomyopathy.xlsx'),[['Conclusion',conclusion],['Scope','Existing ClinVar review scope; incomplete rare-variant detection in low-pass WGS; not a negative clinical gene panel.'],['Sources','; '.join(SOURCES)]])
    for name,records in [('Cardiomyopathy_candidates',candidates),('Other_cardiac_candidates',related),('All_focused_annotations',selected)]:
        book.create(name,FIELDS)
        for r in records:book.add(name,r)
    mf=[k for _,k in modelcols];book.create('Polygenic_models',mf)
    for m in models:book.add('Polygenic_models',m)
    book.close();dump(out/(a.sample+'.cardiomyopathy.json'),{'sample':a.sample,'version':VERSION,'runtime_signature':runtime_signature(c),'conclusion':conclusion,'candidate_count':len(candidates),'related_candidate_count':len(related),'focused_annotation_count':len(selected),'candidates':candidates,'models':models,'gene_sets':{k:sorted(v) for k,v in GENES.items()},'sources':SOURCES})
    (out/(a.sample+'.cardiomyopathy.txt')).write_text(conclusion+'\n\n'+'\n\n'.join(r['gene']+' '+r['variant']+'\n'+r['patient_meaning'] for r in candidates)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--sample',required=True);p.add_argument('--interpretation',required=True);a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
