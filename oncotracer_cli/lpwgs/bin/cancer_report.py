#!/usr/bin/env python3
"""Sample-agnostic cancer-gene review; classification is separate from genotype evidence."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from extension_common import dump, table, Excel, runtime_signature, sha256
from variant_priority import rows, number
from sample_variant_status import FIELDS as STATUS_FIELDS
from clinical_narrative import readable
from genome_report_ui import write, section, cards, esc, table as html_table

VERSION = 'cancer-gene-review-v1'
SOURCES = {
 'brca': ('BRCA results and their meaning · NCI', 'https://www.cancer.gov/about-cancer/causes-prevention/genetics/brca-fact-sheet'),
 'breast': ('Breast and gynecologic cancer genetics · NCI', 'https://www.cancer.gov/types/breast/hp/breast-ovarian-genetics-pdq'),
 'colorectal': ('Colorectal cancer genetics · NCI', 'https://www.cancer.gov/types/colorectal/hp/colorectal-genetics-pdq'),
 'bap1': ('BAP1 tumor predisposition · GeneReviews', 'https://www.ncbi.nlm.nih.gov/books/NBK390611/'),
 'renal': ('Inherited renal cancer · NCI', 'https://www.cancer.gov/types/kidney/hp/renal-cell-carcinoma-genetics'),
 'endocrine': ('Endocrine tumor genetics · NCI', 'https://www.cancer.gov/types/thyroid/hp/medullary-thyroid-genetics-pdq'),
 'msh3': ('Biallelic MSH3 and polyposis · Adam et al.', 'https://pubmed.ncbi.nlm.nih.gov/27476653/'),
 'mutyh': ('MUTYH polyposis · GeneReviews', 'https://www.ncbi.nlm.nih.gov/books/NBK107219/'),
 'tsc': ('Tuberous sclerosis · GeneReviews', 'https://www.ncbi.nlm.nih.gov/books/NBK1220/'),
 'phox2b': ('PHOX2B and familial neuroblastoma · original study', 'https://pubmed.ncbi.nlm.nih.gov/17637745/'),
}
# Focused review scope, not a clinical panel, gene validity ranking or coverage assay.
GROUPS = [
 ('Breast, ovarian and pancreatic susceptibility', 'BRCA1 BRCA2 PALB2 ATM CHEK2 BARD1 BRIP1 RAD51C RAD51D', 'breast'),
 ('Cancer predisposition affecting several organs', 'TP53 PTEN CDH1 STK11', 'breast'),
 ('Colorectal cancer and polyposis', 'APC MUTYH NTHL1 MSH3 MLH1 MSH2 MSH6 PMS2 EPCAM POLE POLD1 BMPR1A SMAD4', 'colorectal'),
 ('Renal tumors', 'VHL FH FLCN MET', 'renal'),
 ('BAP1 tumor predisposition', 'BAP1', 'bap1'),
 ('Endocrine tumors and paraganglioma', 'RET MEN1 CDKN1B SDHA SDHB SDHC SDHD SDHAF2 MAX TMEM127', 'endocrine'),
 ('Tuberous sclerosis and associated tumors', 'TSC1 TSC2', 'tsc'),
 ('Neuroblastoma susceptibility', 'PHOX2B', 'phox2b'),
]
GENES = {g: {'group': group, 'source': source} for group, genes, source in GROUPS for g in genes.split()}
HOLDS = {'identity_unverified', 'annotation_hold', 'flagged_assertion'}
CANDIDATES = {'confirmation_candidate', 'unconfirmed_genotype'}
BUCKET_LABELS = {
 'reviewed_plp': 'Current P/LP allele; sample genotype needs confirmation',
 'held': 'Annotation or identity hold; excluded from positive findings',
 'snapshot_plp': 'Snapshot P/LP label; current review not established',
 'uncertain': 'Uncertain or conflicting classification',
 'limited': 'Limited or condition-specific clinical evidence',
 'benign': 'Benign / likely benign annotation',
 'other': 'Other / unspecified annotation',
}
FIELDS = STATUS_FIELDS+['review_priority_score','review_bucket','cancer_genes','gene','variant','rsid','current_classification','snapshot_classification','interpretation_status','clinical_category','carrier_label','support_score','read_genotype','read_DP','read_ALT','read_GQ','imputed_genotype','imputed_GP','imputed_INFO','condition','patient_meaning','sources']
GENE_FIELDS = ['gene','group','result','records','reviewed_plp','snapshot_plp','held','uncertain','limited','benign','other','somatic_candidates','coverage_status']
SOMATIC_FIELDS = STATUS_FIELDS+['gene','chrom','pos','ref','alt','consequence','protein_change','clinvar_classification','somatic_DP','somatic_AD','somatic_VAF','TLOD','category']


def gene_names(value):
    return {re.sub(r':\d+$', '', v.strip()) for v in re.split(r'[|;,/]', value or '')} - {'', '.'}


def pure_plp(value):
    terms = {v.strip().lower().replace('_', ' ') for v in re.split(r'[/;|]', value or '')}
    return bool(terms) and terms <= {'pathogenic','likely pathogenic'}


def bucket(row):
    status = row.get('interpretation_status')
    if status in HOLDS: return 'held'
    current = row.get('current_classification', '')
    if status in CANDIDATES and pure_plp(current): return 'reviewed_plp'
    if status == 'limited_clinical_evidence': return 'limited'
    # A reviewed current classification always takes precedence over the original snapshot.
    label = current if current.lower() not in {'', '.', 'not_reviewed', 'not_verified'} else row.get('snapshot_classification','')
    if pure_plp(label): return 'snapshot_plp'
    if any(x in label.lower() for x in ['uncertain','conflict']): return 'uncertain'
    if 'benign' in label.lower(): return 'benign'
    return 'other'


def gene_result(counts):
    if counts['reviewed_plp']: return 'P/LP allele candidate detected; genotype unconfirmed'
    if counts['snapshot_plp']: return 'P/LP snapshot label requires review'
    if counts['held']: return 'Held annotation requires resolution; no accepted P/LP candidate'
    if not counts['records']: return 'No ClinVar-matched records in this review scope'
    return 'No P/LP label detected in the available records'


def summarize(selected, somatic):
    summaries = []
    for gene, context in GENES.items():
        rr = [r for r in selected if gene in gene_names(r['gene'])]
        counts = Counter(r['bucket'] for r in rr)
        counts['records'] = len(rr)
        counts['somatic_candidates'] = sum(gene in gene_names(r.get('gene','')) for r in somatic)
        summaries.append(dict(gene=gene,group=context['group'],result=gene_result(counts),coverage_status='Gene-wide sensitivity not assessed; low-pass screen',**{key:counts[key] for key in GENE_FIELDS if key in {'records','somatic_candidates',*BUCKET_LABELS}}))
    return summaries


def evidence_sentence(row):
    depth = number(row.get('read_DP')); alt = number(row.get('read_ALT'))
    text = f"Observed genotype {row.get('read_genotype') or 'unavailable'}; "
    text += f'{alt:g} alternate-supporting read(s) among {depth:g} reads.' if depth is not None and alt is not None else 'read support is not available.'
    if number(row.get('imputed_GP')) is None: text += ' No passing imputed genotype is available.'
    else: text += f" Imputed genotype {row.get('imputed_genotype')}; posterior {row.get('imputed_GP')}."
    return text


def context_text(gene):
    texts = {
      'ATM': ('ATM helps cells respond to DNA damage. A confirmed cancer-predisposing ATM variant in one copy can increase breast cancer susceptibility; it must not be dismissed as a harmless recessive carrier result. Ataxia-telangiectasia generally requires disease-causing variants in both copies. The present candidate alone does not establish either a cancer risk estimate or that recessive disorder.', ['breast']),
      'MSH3': ('The established polyposis mechanism involves disease-causing variants in both MSH3 copies. A single heterozygous candidate does not establish MSH3-associated polyposis. The condition field for an individual allele may be unspecified; gene-level knowledge does not supply missing allele-specific evidence or prove a second affected copy.', ['msh3']),
      'MUTYH': ('MUTYH-associated polyposis involves disease-causing variants in both gene copies. Identity or annotation holds must be resolved before assigning a carrier or affected-genotype interpretation. An unresolved allele is not a positive polyposis finding.', ['mutyh']),
      'TSC1': ('A confirmed disease-causing TSC1 variant can be relevant in one copy. Tuberous sclerosis commonly causes benign growths in several organs and can also involve malignant tumors. A TSC1 label is not a diagnosis of cancer or tuberous sclerosis; the genotype and clinical features need to fit.', ['tsc']),
      'TSC2': ('A confirmed disease-causing TSC2 variant can be relevant in one copy. Tuberous sclerosis includes benign growths and, less commonly, malignant tumors. The gene annotation alone does not establish either condition.', ['tsc']),
      'PHOX2B': ('Some PHOX2B variants are associated with inherited neuroblastoma susceptibility. Effects depend on the specific allele, and the gene is also involved in other developmental disorders. A broad hereditary cancer annotation does not establish cancer, penetrance or a personal probability of disease.', ['phox2b']),
    }
    return texts.get(gene, ('This gene is included in the focused review set. Inheritance, the exact allele, the associated condition and genotype support must be considered together; a gene overlap alone does not establish a cancer predisposition.', [GENES[gene]['source']]))


def source_links(keys):
    return ' · '.join('<a href="'+esc(SOURCES[k][1])+'">'+esc(SOURCES[k][0])+'</a>' for k in keys)


def run(a, config):
    folder = Path(a.interpretation)
    info = folder/(a.sample+'.interpretation.json'); priority = folder/(a.sample+'.variant_priority.tsv.gz')
    if not info.is_file() or not priority.is_file(): raise ValueError('Cancer review requires the complete interpretation and variant priority table')
    d = json.loads(info.read_text())
    if d['sample'] != a.sample: raise ValueError('Cancer report sample mismatch')
    if d.get('runtime_signature') != runtime_signature(config): raise ValueError('Cancer report interpretation/runtime provenance mismatch')
    selected=[]; total=0
    for original in rows(priority):
        total += 1
        hits = gene_names(original.get('gene','')) & GENES.keys()
        if not hits: continue
        r = dict(original, cancer_genes='; '.join(sorted(hits)), bucket=bucket(original))
        r['review_bucket'] = BUCKET_LABELS[r['bucket']]; r['condition'] = readable(r.get('condition',''))
        for key in ['review_priority_score','support_score','read_DP','read_ALT','read_GQ','imputed_GP','imputed_INFO']: r[key] = number(r.get(key))
        selected.append(r)
    selected.sort(key=lambda r:(-(r['review_priority_score'] or 0),-(r['support_score'] or 0),r['variant']))
    special = Path(a.special); manifest=special/(a.sample+'.special_annotations.json')
    sm = json.loads(manifest.read_text())
    if sm.get('sample') != a.sample or sm.get('runtime_signature') != runtime_signature(config): raise ValueError('Cancer report somatic input provenance mismatch')
    somatic_path = special/(a.sample+'.somatic.annotated.tsv.gz')
    if not somatic_path.is_file(): raise ValueError('Missing annotated somatic candidate table')
    somatic=[r for r in rows(somatic_path) if gene_names(r.get('gene','')) & GENES.keys()]
    for r in somatic:
        for key in ['pos','somatic_DP','somatic_VAF','TLOD']: r[key]=number(r.get(key))
    summaries=summarize(selected,somatic); brca=[r for r in summaries if r['gene'] in {'BRCA1','BRCA2'}]
    candidates=[r for r in selected if r['bucket']=='reviewed_plp']
    holds=[r for r in selected if r['bucket'] in {'held','snapshot_plp','limited'}]
    brca_other=[r for r in selected if gene_names(r['gene']) & {'BRCA1','BRCA2'} and r['bucket'] not in {'benign','other'}]
    brca_status='; '.join(r['gene']+': '+r['result'] for r in brca)+'.'
    conclusion=brca_status+f' {len(candidates)} current P/LP allele candidate(s) in the focused gene set remain unconfirmed in this sample.'
    counts=Counter(r['bucket'] for r in selected)
    out=Path(a.sample+'.cancer_review'); out.mkdir(exist_ok=True)
    cols=[('Review priority','review_priority_score'),('Gene','gene'),('Variant','variant'),('Current classification','current_classification'),('Snapshot classification','snapshot_classification'),('Clinical review status','interpretation_status'),('Reported zygosity','zygosity_label'),('Carrier status','carrier_label'),('Affected status','affected_label'),('Genotype support / 100','support_score'),('Read genotype','read_genotype'),('Depth','read_DP'),('Alternate reads','read_ALT'),('Imputed genotype','imputed_genotype'),('GP','imputed_GP'),('Condition annotation','condition')]
    body=cards([(len(GENES),'genes in the focused review set'),(len(candidates),'current P/LP allele candidates'),(len(holds),'held or unresolved clinical labels'),(len(somatic),'separate somatic candidates in these genes')])
    body+=section('BRCA1 and BRCA2 results','<p class="note">'+esc(brca_status)+'</p>'+html_table(brca,[('Gene','gene'),('Result','result'),('ClinVar-matched records','records'),('Current P/LP candidates','reviewed_plp'),('Snapshot P/LP labels','snapshot_plp'),('Held annotations','held'),('Uncertain / conflicting','uncertain'),('Benign / likely benign','benign'),('Other annotation','other')])+'<p>The counts describe available ClinVar-matched variant records, not the fraction of the gene tested. No detected P/LP label in a low-pass genome is not a negative clinical BRCA test. Uncertain or conflicting labels do not establish an increased cancer risk. Age and sex provide context here; no absolute cancer risk has been calculated.</p><p>'+source_links(['brca'])+'</p>', 'brca')
    if brca_other: body+=section('BRCA annotations that need context',html_table(brca_other,cols)+'<p>Uncertainty and conflicting classifications are kept visible, without treating them as pathogenic findings.</p>')
    body+=section('Other cancer-gene candidates','<p>These alleles have a current P/LP classification in the reused exact-allele review. Their sequencing or imputation support does not establish a clinically confirmed genotype. The priority score helps order review; it is not a cancer probability.</p>'+(html_table(candidates,cols) if candidates else '<p>No current P/LP allele candidate was present in this review scope.</p>'), 'candidates')
    if candidates or holds:
        parts=[]
        for gene in dict.fromkeys(g for r in candidates+holds for g in sorted(gene_names(r['gene']) & GENES.keys())):
            text,keys=context_text(gene)
            parts.append('<article><h3>'+esc(gene)+'</h3><p>'+esc(text)+'</p>')
            for r in candidates+holds:
                if gene not in gene_names(r['gene']): continue
                parts.append('<p><b>'+esc(r['variant'])+'</b> · '+esc(r['review_bucket'])+'. '+esc(evidence_sentence(r))+'</p>')
                urls=[u.strip() for u in r.get('sources','').split(';') if u.strip().startswith('https://www.ncbi.nlm.nih.gov/clinvar/')]
                if urls: parts.append('<p><a href="'+esc(urls[0])+'">Exact-allele ClinVar record</a></p>')
            parts.append('<p>'+source_links(keys)+'</p></article>')
        body+=section('What these findings could mean',''.join(parts))
    body+=section('Held and unresolved annotations','<p>These records are excluded from accepted current P/LP candidates. An identity hold, historical label or limited clinical assertion cannot be promoted to a positive finding by a high genotype-support score.</p>'+(html_table(holds,cols+[('Reason','patient_meaning')]) if holds else '<p>No held or unresolved P/LP annotations in this gene set.</p>'))
    body+=section('Gene-by-gene inventory','<p>Every gene in the focused review set is listed, including genes with no available ClinVar-matched records. The groups identify review topics; each gene has its own condition spectrum and inheritance. No gene-wide coverage or exclusion result is inferred from these counts.</p>'+html_table(summaries,[('Gene','gene'),('Review group','group'),('Result','result'),('Available records','records'),('Current P/LP','reviewed_plp'),('Snapshot P/LP','snapshot_plp'),('Held','held'),('Uncertain / conflicting','uncertain'),('Limited assertion','limited'),('Benign / likely benign','benign'),('Other','other'),('Somatic candidates','somatic_candidates')]), 'genes')
    body+=section('Somatic candidates in the focused genes','<p>Somatic candidates are reviewed separately from inherited predisposition. This is an unpaired analysis; the classification does not prove a tumor origin or diagnose cancer.</p>'+(html_table(somatic,[(k.replace('_',' ').capitalize(),k) for k in SOMATIC_FIELDS]) if somatic else '<p>No reconciled somatic candidate overlaps the focused genes in the available annotated somatic table.</p>'), 'somatic')
    body+=section('Complete focused annotation table',html_table(selected,[('Evidence category','review_bucket')]+cols), 'annotations')
    body+=section('Scope, limitations and sources','<p>This module reuses the complete ClinVar priority table, current exact-allele reviews and the annotated somatic candidate table. Snapshot-only classifications retain their original status and are not presented as newly reviewed. Imputation is derived from the sequence reads, so agreement is not independent confirmation.</p><p>A low-pass screen can miss rare alleles, poorly covered regions, copy-number changes, structural variants, repeats and variants absent from the reference panel. Absence of a candidate does not establish absence of hereditary cancer susceptibility. A clinically relevant candidate can be evaluated with validated testing and personal and family history; neither a database label nor this report establishes a cancer diagnosis.</p><p>Gene context is curated from the sources below; it is not an allele-specific penetrance estimate or a comprehensive clinical panel. Polygenic models, where available, are reported separately with their reference-population and calibration limitations.</p><ul>'+''.join('<li>'+source_links([k])+'</li>' for k in SOURCES)+'</ul>', 'methods')
    links=[('All results','../../START_HERE.html'),('Cancer review Excel',a.sample+'.cancer_review.xlsx'),('Gene summary TSV',a.sample+'.cancer_genes.tsv'),('All focused variants',a.sample+'.cancer_variants.tsv')]
    if config.get('polygenic'): links.append(('Polygenic models','../../polygenic/'+a.sample+'.polygenic.html'))
    write(out/(a.sample+'.cancer_review.html'),a.sample+' · BRCA and cancer-gene review','BRCA1/BRCA2 findings, other cancer-related candidates and a transparent gene-by-gene inventory.',body,links)
    table(out/(a.sample+'.cancer_genes.tsv'),GENE_FIELDS,summaries); table(out/(a.sample+'.cancer_variants.tsv'),FIELDS,selected)
    table(out/(a.sample+'.cancer_somatic.tsv'),SOMATIC_FIELDS,somatic)
    book=Excel(out/(a.sample+'.cancer_review.xlsx'),[['Conclusion',conclusion],['Scope','Available ClinVar-matched records; not a negative clinical panel.'],['P/LP','Pathogenic / likely pathogenic allele classification, distinct from sample genotype confidence.'],['Sensitivity','Gene-wide sensitivity not assessed; low-pass WGS can miss clinically important variants.']])
    for name,records,fields in [('Gene_inventory',summaries,GENE_FIELDS),('Current_PLP_candidates',candidates,FIELDS),('BRCA_uncertain_and_review',brca_other,FIELDS),('Held_or_unresolved',holds,FIELDS),('All_focused_variants',selected,FIELDS),('Somatic_candidates',somatic,SOMATIC_FIELDS)]:
        book.create(name,fields)
        for r in records: book.add(name,r)
    book.close()
    result={'sample':a.sample,'version':VERSION,'generated_utc':datetime.now(timezone.utc).isoformat(),'runtime_signature':runtime_signature(config),'module_sha256':sha256(__file__),'conclusion':conclusion,'brca_conclusion':brca_status,'candidate_count':len(candidates),'held_or_unresolved_count':len(holds),'gene_count':len(GENES),'focused_annotation_count':len(selected),'total_clinvar_records':total,'bucket_counts':dict(counts),'genes':summaries,'candidates':candidates,'held_or_unresolved':holds,'somatic_candidates':somatic,'somatic_candidate_count':len(somatic),'patient':d.get('patient',{}),'sources':SOURCES,'source_review_date':'2026-10-13','inputs':{p.name:sha256(p) for p in [info,priority,manifest,somatic_path]}}
    dump(out/(a.sample+'.cancer_review.json'),result)
    (out/(a.sample+'.cancer_review.txt')).write_text(conclusion+'\n\n'+ '\n'.join(r['gene']+': '+r['result'] for r in summaries)+'\n\nCandidate evidence\n'+'\n'.join(r['gene']+' '+r['variant']+' '+evidence_sentence(r) for r in candidates)+'\n\nLow-pass WGS does not exclude hereditary cancer susceptibility.\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--config',required=True); p.add_argument('--sample',required=True); p.add_argument('--interpretation',required=True); p.add_argument('--special',required=True)
    args=p.parse_args(); run(args,json.loads(Path(args.config).read_text()))
