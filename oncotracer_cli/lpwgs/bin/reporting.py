#!/usr/bin/env python3
"""Create a long, evidence-based report and an organized view of published outputs."""
import argparse
import csv
import hashlib
import html
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import textwrap
from urllib.parse import quote

from report_data import collect, write_rows, CATEGORIES

GROUPS = {
    '00_report': 'Start here: detailed HTML, plain-text and JSON reports. HTML supports printing to PDF.',
    '01_sequencing_qc': 'Read QC, alignment metrics and chromosome coverage. Coverage gaps remain unknown.',
    '02_ancestry': 'Reference similarity estimates and their supporting files; scores are not ancestry percentages.',
    '03_germline_variants': 'Autosomal germline calls, annotations and review candidates. LowEvidence records remain in the VCF.',
    '04_somatic_candidates': 'Unpaired somatic candidates and panel-of-normals files; somatic origin is not established.',
    '05_traits': 'Trait marker evidence with direct, exploratory, imputed and unresolved results distinguished.',
    '06_gwas': 'Published association annotations and summaries; these are not personal risk scores.',
    '07_imputation': 'GLIMPSE2 chromosome BCFs and reference provenance when available.',
    '08_disease_review': 'Full disease index, printable compendium and evidence tables, including cardiomyopathy.',
    '09_provenance': 'File manifest, run configuration, input records, execution history and resource provenance.',
}
SOURCES = [
    ('GLIMPSE2 method and limitations', 'https://www.nature.com/articles/s41588-023-01438-3'),
    ('GLIMPSE2 public-reference tutorial', 'https://odelaneau.github.io/GLIMPSE/docs/tutorials/getting_started/'),
    ('ClinVar review status', 'https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/'),
    ('GWAS Catalog documentation', 'https://www.ebi.ac.uk/gwas/docs/faq/'),
    ('ESC cardiomyopathy guidance', 'https://academic.oup.com/eurheartj/article/44/37/3503/7246608'),
    ('ClinGen gene and disease evidence', 'https://search.clinicalgenome.org/'),
]
CSS = '''
:root{--ink:#183145;--muted:#536a78;--line:#d7e1e7;--accent:#12697b}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{font:15px/1.65 system-ui,sans-serif;margin:0;background:#f3f6f8;color:var(--ink)}
header{background:#123d51;color:white;padding:38px max(5vw,20px)}header h1{color:white;margin:0;font-size:30px}header p{margin:.5em 0}
main{max-width:1280px;margin:auto;padding:24px}section{background:white;border:1px solid var(--line);border-radius:8px;padding:25px;margin:22px 0}
h2{font-size:23px;color:#12465b;margin:0 0 16px}h3{font-size:18px;color:#12465b}a{color:var(--accent)}nav{columns:2;column-gap:30px;background:#e8f0f4;padding:18px 25px;border-radius:8px}
nav a{display:block;padding:4px 0;break-inside:avoid}.banner{background:#fff0ca;border-left:5px solid #a76505;padding:15px;margin:18px 0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:16px 0}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#e8f1f5}tbody tr:nth-child(even){background:#f7fafb}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f7;padding:15px}
.note{color:var(--muted)}.metric{font-size:18px;font-weight:650}input{padding:12px;width:100%;border:1px solid #769aa8;border-radius:5px;font:inherit}
footer{padding:25px;text-align:center;color:var(--muted)}@media(max-width:650px){main{padding:10px}section{padding:15px}nav{columns:1}}
@media print{@page{size:A4;margin:15mm}body{background:white;font-size:10pt}header{background:white;color:#183145;padding:0}header h1{color:#183145}main{padding:0;max-width:none}section{border:0;border-radius:0;padding:0;break-before:page}section:first-of-type{break-before:auto}nav,input,.screen-only{display:none}.scroll{overflow:visible}table{font-size:8pt;table-layout:auto}th,td{padding:4px}thead{display:table-header-group}tr{break-inside:avoid}a{color:#183145;text-decoration:none}}
'''


def esc(value):
    return html.escape(str(value if value is not None else 'Not available'))


def fmt(value):
    if value is None:
        return 'Not available'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, int):
        return f'{value:,}'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


class Document:
    def __init__(self, title, subtitle, banner):
        self.title, self.subtitle, self.banner = title, subtitle, banner
        self.parts, self.text, self.toc = [], [title, subtitle, banner], []
        self.open_section = False

    def section(self, title, anchor=None):
        anchor = anchor or f'section-{len(self.toc)+1}'
        if self.open_section:
            self.parts.append('</section>')
        self.parts.append(f'<section id="{esc(anchor)}"><h2>{esc(title)}</h2>')
        self.text.extend(['', title, '='*len(title)])
        self.toc.append((anchor, title)); self.open_section = True

    def p(self, text, banner=False):
        self.parts.append(f'<p class="{"banner" if banner else ""}">{esc(text)}</p>')
        self.text.extend(['', textwrap.fill(text, width=110)])

    def link(self, title, target):
        self.parts.append(f'<p><a href="{esc(quote(str(target), safe="/:#?=&%"))}">{esc(title)}</a></p>')
        self.text.append(f'{title}: {target}')

    def table(self, records, fields, empty='No entries available.', search=False):
        records = list(records)
        if not records:
            self.p(empty); return
        self.parts.append('<div class="scroll"><table'+(' class="searchable"' if search else '')+'><thead><tr>'+''.join(f'<th>{esc(title)}</th>' for title, _ in fields)+'</tr></thead><tbody>')
        self.text.append(' | '.join(title for title, _ in fields))
        for row in records:
            anchor = f' id="{esc(row["anchor"])}"' if row.get('anchor') else ''
            self.parts.append('<tr'+anchor+'>'+''.join(f'<td>{esc(fmt(row.get(key)))}</td>' for _, key in fields)+'</tr>')
            self.text.append(' | '.join(fmt(row.get(key)) for _, key in fields))
        self.parts.append('</tbody></table></div>')

    def render(self):
        toc = '<nav aria-label="Report contents">'+''.join(f'<a href="#{esc(anchor)}">{esc(title)}</a>' for anchor, title in self.toc)+'</nav>'
        body = ''.join(self.parts)+('</section>' if self.open_section else '')
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{esc(self.title)}</title><style>{CSS}</style></head><body><header><h1>{esc(self.title)}</h1>'
                f'<p>{esc(self.subtitle)}</p></header><main><div class="banner">{esc(self.banner)}</div>{toc}{body}</main>'
                '<footer>Generated locally from the listed analysis files. Keep the evidence and provenance with the report.</footer></body></html>')


def inventory(output, folder, sample):
    patterns = [
        ('01_sequencing_qc', f'alignment/{sample}.*'), ('01_sequencing_qc', 'qc/lanes/*.json'), ('01_sequencing_qc', 'qc/lanes/*.html'),
        ('02_ancestry', f'ancestry/{sample}.*'),
        ('03_germline_variants', f'variants/germline/{sample}.*'), ('03_germline_variants', f'annotation/germline/{sample}.*'),
        ('04_somatic_candidates', f'variants/somatic_candidates/{sample}.*'), ('04_somatic_candidates', f'annotation/somatic/{sample}.*'),
        ('04_somatic_candidates', 'panel_of_normals/somatic_pon.*'), ('04_somatic_candidates', 'panel_of_normals/calls/*.vcf.gz*'),
        ('04_somatic_candidates', 'panel_of_normals/alignment/*.qc.json'),
        ('05_traits', f'traits_gwas/{sample}.traits*'), ('05_traits', f'traits_gwas/{sample}.target_genotypes.*'),
        ('06_gwas', f'traits_gwas/{sample}.gwas*'),
        ('07_imputation', f'imputation/{sample}.*'), ('07_imputation', 'imputation/reference_provenance/*.json'),
        ('09_provenance', 'pipeline_info/*.json'), ('09_provenance', 'pipeline_info/*.tsv'), ('09_provenance', 'pipeline_info/*.html'),
    ]
    entries, seen = [], set()
    for group, pattern in patterns:
        for source in sorted(output.glob(pattern)):
            if not source.is_file() or source in seen:
                continue
            seen.add(source)
            rel = source.relative_to(output)
            organized = Path(group)/rel
            destination = folder/organized
            destination.parent.mkdir(parents=True, exist_ok=True)
            target = os.path.relpath(source, destination.parent)
            if source.suffix=='.html':
                marker='<!-- generated report redirect -->'
                if destination.is_symlink():destination.unlink()
                elif destination.exists() and not destination.read_text().startswith(marker):
                    raise ValueError(f'Refusing to replace a regular file: {destination}')
                href=esc(quote(target,safe='/'))
                destination.write_text(marker+'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+href+'"><a href="'+href+'">Open report</a>')
            elif destination.is_symlink():
                if os.readlink(destination) != target:
                    destination.unlink(); destination.symlink_to(target)
            elif destination.exists():
                raise ValueError(f'Refusing to replace a regular file with a link: {destination}')
            else:
                destination.symlink_to(target)
            st = source.stat()
            checksum = hashlib.sha256(source.read_bytes()).hexdigest() if st.st_size <= 2_000_000 else ''
            entries.append({'category': group, 'source_relative_to_output': str(rel), 'organized_path': str(organized),
                            'bytes': st.st_size, 'modified_utc': datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                            'sha256_small_file': checksum})
    fields = ['category', 'source_relative_to_output', 'organized_path', 'bytes', 'modified_utc', 'sha256_small_file']
    write_rows(folder/'09_provenance/file_manifest.tsv', fields, entries)
    return entries


def evidence_label(row):
    if row.get('priority_model') and not row.get('germline_review_candidates') and not row.get('somatic_review_candidates'):
        return 'Disease model with exploratory reference comparison; personal risk uncalibrated'
    if row['germline_review_candidates']:
        return 'Germline candidate for confirmation and review'
    if row['somatic_review_candidates']:
        return 'Unpaired somatic candidate; origin unconfirmed'
    if row['gwas_called'] or row['gwas_imputed']:
        return 'GWAS genotype evidence; risk direction not established'
    if row['gwas_exploratory']:
        return 'Exploratory GWAS genotype evidence only'
    if row['clinical_rows']:
        return 'Database annotations; no qualifying candidate in these records'
    return 'GWAS genotype evidence unresolved'


def disease_document(data, folder):
    doc = Document(f'{data["sample"]} · disease evidence compendium',
                   f'{len(data["diseases"]):,} database condition/association labels · {data["generated_utc"]}',
                   'RESEARCH EVIDENCE. A disease name here is not a diagnosis, a positive screen, or a measured probability of disease.')
    doc.section('How to read this compendium')
    doc.p('Every available ClinVar condition label, excluding empty placeholders, is represented. GWAS phenotypes are included when their reported or mapped label matches the documented medical-term categories. Categories are navigation aids based on text matching, not an ontology-based clinical screen. Related studies can use different names for the same disease, and a label can describe family history, a biomarker or a disease subtype. The number of labels is therefore not a number of independent diseases tested.')
    doc.p('Clinical columns count variant-condition annotations, including benign, uncertain and filtered records. A germline review candidate is a non-reference call passing the existing evidence rules and carrying a qualifying ClinVar aggregate pathogenic/likely-pathogenic assertion. ClinVar conditions and the aggregate classification are not automatically disease-specific assertions. Interpretation must check the exact variant-condition evidence, inheritance and phenotype. Somatic review candidates are from an unpaired sample and are kept separate.')
    doc.p('GWAS columns count published association rows, not unique variants or affected people. Direct, imputed, exploratory and unresolved evidence are reported separately. Reported effect alleles have not been fully harmonized; the GWAS row counts do not assign an effect-allele dosage, individual odds ratio, disease probability or protection. Separately scored and allele-matched disease models are linked by exact phenotype label where available. A zero count in this table is not a negative genetic test. The complete source association table includes phenotypes not recognized by the navigation categories.')
    doc.link('Return to the main report', f'../00_report/{data["sample"]}.report.html')
    doc.link('Download the complete disease index', 'disease_index.tsv')
    doc.link('All clinical condition annotations, including low evidence (compressed TSV)', 'clinical_condition_variants.tsv.gz')
    doc.link('All categorized disease-related GWAS associations (compressed TSV)', 'gwas_disease_associations.tsv.gz')
    doc.parts.append('<p class="screen-only"><label for="disease-search">Find a disease or condition</label><input id="disease-search" type="search" placeholder="Search the tables, e.g. cardiomyopathy, diabetes, asthma"></p>')
    from disease_priority import METHOD
    doc.p(METHOD)
    doc.p('Sorted by priority, highest first. The score uses existing variant and disease-model reviews; a high score selects a topic to examine and does not establish that the condition is present.')
    records=[{**r,'evidence':evidence_label(r)} for r in data['diseases']]
    doc.section('Prioritized disease evidence')
    doc.table(records, [('Rank','review_rank'),('Priority / 100','review_priority_score'),('Condition / study phenotype','condition'),
        ('Category','category'),('Why it is prioritized','priority_basis'),('Leading variant','priority_variant'),('Model','priority_model'),
        ('Clinical rows','clinical_rows'),('GWAS direct','gwas_called'),('GWAS imputed','gwas_imputed'),
        ('GWAS exploratory','gwas_exploratory'),('GWAS unresolved','gwas_unresolved'),('Evidence interpretation','evidence')],search=True)
    doc.section('All variant review candidates')
    doc.p('This table is not truncated. The TSV retains additional fields and can be joined to the complete annotated variant tables. Repeated disease labels for one variant do not represent independent discoveries. These are database and genotype review candidates, not newly assigned ACMG classifications.')
    doc.table(data['clinical']['candidates'], [('Analysis', 'analysis_kind'), ('Chromosome', 'chrom'), ('Position', 'pos'),
              ('REF', 'ref'), ('ALT', 'alt'), ('Gene', 'gene'), ('Genotype', 'genotype'), ('DP', 'DP'), ('GQ', 'GQ'),
              ('ClinVar assertion', 'clinvar_classification'), ('Review stars', 'review_stars'), ('Condition labels', 'condition')],
              empty='No candidate rows are available. Check annotation availability in the main report before interpreting this absence.')
    doc.link('Complete candidate table', 'all_review_candidates.tsv')
    doc.section('Sources and scope')
    for title, url in SOURCES[2:]:
        doc.link(title, url)
    doc.p('This analysis does not establish complete coverage of any disease gene set. This condition index does not assess copy-number variants, structural variants, repeat expansions or sex-chromosome variants. Dominant mitochondrial SNP annotations are exported separately; they do not constitute mitochondrial disease screening. A broader database expands the annotation space but does not create missing sequencing evidence.')
    rendered = doc.render().replace('</style>', 'table{min-width:1900px}th,td{min-width:100px;word-break:normal;overflow-wrap:normal}th:nth-child(3),td:nth-child(3){min-width:280px}th:nth-child(5),td:nth-child(5){min-width:360px}</style>').replace('</body>', '<script>document.getElementById("disease-search").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll("table.searchable tbody tr").forEach(r=>{r.hidden=!r.textContent.toLowerCase().includes(q);});});</script></body>')
    (folder/'08_disease_review/disease_review.html').write_text(rendered)
    (folder/'08_disease_review/disease_review.txt').write_text('\n'.join(doc.text)+'\n')


def main_document(d):
    c = d['config']; qc = d['qc'] or {}; tr = d['traits_gwas'] or {}; an = d['ancestry'] or {}
    gwas = d['gwas']; clinical = d['clinical']; traits = tr.get('traits', [])
    banner = ('SYNTHETIC / SMOKE TEST — NOT A PATIENT RESULT' if d['test_mode'] else
              'Research findings require independent validation. This report does not establish or exclude a diagnosis.')
    if d['workflow_status'] != 'completed':
        banner = 'INCOMPLETE SNAPSHOT — '+banner
    doc = Document(f'{d["sample"]} · whole-genome research report',
                   f'Workflow: {d["workflow_status"]} · Generated {d["generated_utc"]}', banner)
    doc.section('1. Findings at a glance', 'findings')
    depth, covered = qc.get('mean_autosomal_depth_after_filters'), qc.get('autosomal_fraction_covered')
    if depth is not None and covered is not None:
        doc.p(f'The available alignment has a mean filtered autosomal depth of {depth:.3f}×. At least one retained read covers {covered:.2%} of autosomal reference bases; {1-covered:.2%} have no retained coverage. This is low-pass sequencing. A position with no alternate read cannot generally be assumed to be homozygous reference, and an uncovered position is unknown.')
    else:
        doc.p('A sequencing coverage summary is not available. Read-level confidence cannot be inferred from the presence of a VCF or a database match.')
    status_counts = Counter(x.get('status', 'unknown') for x in traits)
    doc.p(f'The available trait result contains {len(traits):,} marker rows. Their recorded evidence states are: '+
          (', '.join(f'{key}: {value:,}' for key, value in sorted(status_counts.items())) or 'no trait result available')+'. These counts describe the actual result file; a planned larger panel is not counted until its analysis has produced results.')
    if gwas['available']:
        doc.p(f'The GWAS table contains {gwas["association_rows"]:,} published association rows, spanning {gwas["unique_positions"]:,} positions and {gwas["unique_rsids"]:,} rs identifiers. {gwas["by_status"].get("called", 0):,} association rows meet the direct-call evidence rules, {gwas["by_status"].get("imputed", 0):,} use accepted imputed genotypes and {gwas["by_status"].get("exploratory", 0):,} use exploratory read evidence. Repeated publications and phenotypes explain why association counts are larger than variant counts.')
    available = clinical['available_tables']
    if available:
        doc.p('Clinical annotation tables available: '+', '.join(available)+f'. The candidate tables contain {len(clinical["candidates"]):,} rows across those analyses. Candidate status means that the variant needs genotype, database, inheritance and phenotype review; it does not establish a disease.')
    else:
        doc.p('Clinical variant annotation tables are not available yet. Cardiomyopathy and other clinical variant findings therefore remain unassessed in this snapshot. The absence of a candidate list must not be reported as a negative result.', banner=True)
    doc.p(f'The disease compendium indexes {len(d["diseases"]):,} available condition/study labels. It includes common disease categories and all named ClinVar conditions in the available annotations, with a full evidence index. These labels can overlap and are not independent disease tests. Disease-associated genotypes, when supported, are different from a diagnosis or a validated estimate of personal risk.')
    doc.link('Open the complete, searchable disease compendium', '../08_disease_review/disease_review.html')
    doc.p('The report is deliberately evidence-first: measured coverage and recorded genotypes are described separately from imputation, public database assertions and disease interpretation. Missing data, low-confidence calls and analyses that have not run remain visible throughout the document.')

    origin=d.get('variant_evidence') or {}
    if origin:
        doc.section('Variant evidence categories', 'variant-evidence')
        doc.p(origin['scope'])
        doc.table([{'category':key,'variants':value,'clinvar':origin['clinvar_by_category'].get(key,0)} for key,value in origin['category_counts'].items()],
                  [('Evidence category','category'),('Unique observed variants','variants'),('ClinVar matched','clinvar')])
        doc.p(origin['interpretation'])
        doc.p('ClinVar counts in this comparison include AlleleID matches without a clinical-significance assertion. Those records remain in the workbook with a blank classification; missing classification is not interpreted as benign or pathogenic. Original caller annotation summaries count records with a clinical-significance field, so their ClinVar totals use a different definition.')
        doc.p('Germline-supported variants have passing direct diploid read evidence or exact-allele imputation support. Imputed support may rescue a low-depth caller record but is not independent confirmation. Somatic candidates require Mutect2 PASS, the configured minimum depth, and at least three ALT reads, without supporting germline evidence. Missing imputation alone never makes a somatic candidate. Discordant or ambiguous records remain unconfirmed. These categories do not change ClinVar classifications or clinical confirmation requirements.')
        doc.link('ClinVar variant workbook, grouped by evidence', f'../../variant_evidence/{d["sample"]}.variant_review.xlsx')
        doc.link('Complete variant evidence rules and counts', f'../../variant_evidence/{d["sample"]}.variant_evidence.json')

    doc.section('2. Analysis availability and completion', 'availability')
    stage_rows = [
        {'analysis': 'Sequencing QC', 'state': 'Available' if d['qc'] else 'Not available', 'basis': 'Filtered autosomal depth and coverage summary'},
        {'analysis': 'Germline calls', 'state': 'Available' if d['germline_statistics'] else 'Not available', 'basis': 'Autosomal SNVs and short indels; includes filtered records'},
        {'analysis': 'Germline annotation', 'state': 'Available' if clinical['summaries']['germline'] else 'Not available', 'basis': 'ClinVar and configured gene annotation'},
        {'analysis': 'Somatic annotation', 'state': 'Available' if clinical['summaries']['somatic'] else 'Not available', 'basis': 'Unpaired candidate variants; origin remains unconfirmed'},
        {'analysis': 'Ancestry comparison', 'state': an.get('status', 'Not available'), 'basis': 'Similarity to the selected reference panel'},
        {'analysis': 'Trait markers', 'state': 'Available' if d['traits_gwas'] else 'Not available', 'basis': f'{len(traits)} marker result rows'},
        {'analysis': 'GWAS associations', 'state': 'Available' if gwas['available'] else 'Not available', 'basis': 'Published SNP-phenotype annotations'},
        {'analysis': 'GLIMPSE2', 'state': f'{len(d["imputation"]["chromosome_files"])} chromosome BCFs present', 'basis': 'Enabled in runtime: '+str(d['imputation']['enabled'])},
        {'analysis': 'Validated disease screening / PRS', 'state': 'Not performed', 'basis': 'No diagnostic gene-panel coverage validation or calibrated polygenic score'},
    ]
    doc.table(stage_rows, [('Analysis', 'analysis'), ('Availability', 'state'), ('What it represents', 'basis')])
    doc.p('Availability means a published file was found, not that every analysis is complete or clinically validated. During an incomplete or stopped run, published files may be retained from an earlier execution. The file manifest records sizes, modification times and checksums for small files so the evidence behind this snapshot can be audited. The report does not silently convert missing analyses into zero findings.')
    doc.p('Normal samples used in a panel of normals support somatic artifact filtering. They are not a matched normal specimen from the analyzed individual, and they are not family controls for inherited disease analysis. The germline and somatic branches answer different questions and their findings are not pooled into one disease-positive list.')

    doc.section('3. Sequencing quality and coverage', 'quality')
    doc.table([{'metric': key, 'value': value} for key, value in qc.items()], [('Metric', 'metric'), ('Value', 'value')])
    doc.table(d['lane_qc'], [('Lane', 'lane'), ('Input reads', 'input_reads'), ('Retained reads', 'retained_reads'), ('Q30 fraction', 'q30_rate'), ('GC fraction', 'gc_content')], empty='Per-lane fastp summaries are not available in the published outputs.')
    doc.p('Mean depth is an average across reference bases. It does not mean every position was read that many times. The fraction covered measures bases with at least one retained read; it is not the fraction at a clinically adequate depth. Neither this fraction nor a chromosome average establishes complete coverage of a cardiomyopathy gene or any other disease gene. Exon-level and variant-level review would be required for that purpose.')
    doc.p('Mapping-quality and base-quality filters reduce unreliable read observations. Duplicate marking and alignment QC address other properties of the data, but good mapping or Q30 values do not compensate for low depth. A plausible alternate allele can still be missed, and a small number of supporting reads can still be insufficient to distinguish a true heterozygous genotype from error.')
    doc.table(d['coverage'], [('Chromosome', '#rname'), ('Reference bases', 'endpos'), ('Reads', 'numreads'), ('Covered bases', 'covbases'), ('Covered %', 'coverage'), ('Mean depth', 'meandepth'), ('Mean base quality', 'meanbaseq'), ('Mean mapping quality', 'meanmapq')], empty='Per-chromosome coverage is not available.')

    doc.section('4. Germline variants and clinical annotations', 'germline')
    doc.table([{'metric': key, 'count': value} for key, value in d['germline_statistics'].items()], [('VCF statistic', 'metric'), ('Count', 'count')], empty='Germline VCF statistics are not available.')
    doc.p('VCF summary counts describe stored records, including records labeled LowEvidence. They must not be described as high-confidence variants or pathogenic findings. SNP counts and short-indel counts summarize sequence differences; most sequence differences do not cause disease. Clinical interpretation starts only after examining the exact alleles, genotype evidence, annotation and disease context.')
    annotations = [value for value in clinical['summaries'].values() if value]
    doc.table(annotations, [('Kind', 'kind'), ('Engine', 'engine'), ('Status', 'annotation_status'), ('Variant records', 'total'), ('ClinVar matches', 'clinvar_matches'), ('Review candidates', 'pathogenic_review_candidates')], empty='Annotation has not produced a summary yet.')
    doc.p('ClinVar matching uses the normalized chromosome, position, reference and alternate allele. Gene annotation adds the reported gene and consequence information from the configured local annotation engine. This workflow retains database assertions; it does not perform a new ACMG/AMP classification, evaluate all published evidence, establish penetrance or determine whether a variant explains an individual phenotype.')
    doc.p('ClinVar review stars describe the review supporting an assertion. They are not genotype quality scores, probability of pathogenicity or a measure of personal risk. Conflicting assertions and variants of uncertain significance are not promoted to pathogenic review candidates. A database classification can change, so the resource date and exact assertion should be reviewed before clinical use.')
    doc.table(clinical['candidates'][:100], [('Kind', 'analysis_kind'), ('Chromosome', 'chrom'), ('Position', 'pos'), ('Gene', 'gene'), ('Genotype', 'genotype'), ('DP', 'DP'), ('GQ', 'GQ'), ('ClinVar assertion', 'clinvar_classification'), ('Review stars', 'review_stars'), ('Condition', 'condition')], empty='No candidate rows are available. Refer to the annotation availability above; this does not exclude genetic disease.')
    if len(clinical['candidates']) > 100:
        doc.p(f'This overview shows the first 100 of {len(clinical["candidates"]):,} candidate rows. The disease compendium and candidate TSV contain every candidate, without this overview limit.')
    doc.link('All clinical review candidates', '../08_disease_review/all_review_candidates.tsv')

    doc.section('5. Cardiomyopathy and broad disease review', 'disease')
    groups = Counter(x['category'] for x in d['diseases'])
    doc.table([{'category': name, 'labels': count} for name, count in sorted(groups.items())], [('Disease navigation category', 'category'), ('Condition / study labels', 'labels')], empty='No disease-related annotations are available yet.')
    doc.p('The broader review includes cardiovascular disease, diabetes and metabolic conditions, cancer, neurological and mental-health conditions, immune and inflammatory disease, respiratory disease, digestive and liver disease, kidney disease, blood disorders, sensory conditions, musculoskeletal disease, skin disease, infection susceptibility and reproductive conditions when relevant database rows are present. Categories organize the evidence; a category listing does not mean that all diseases within it were tested.')
    cardiac = [{**x, 'evidence': evidence_label(x)} for x in d['diseases'] if x['category'] == 'Cardiomyopathy']
    doc.table(cardiac, [('Cardiomyopathy-related label', 'condition'), ('Clinical rows', 'clinical_rows'), ('Germline review candidates', 'germline_review_candidates'), ('GWAS direct', 'gwas_called'), ('GWAS imputed', 'gwas_imputed'), ('GWAS exploratory', 'gwas_exploratory'), ('GWAS unresolved', 'gwas_unresolved'), ('Evidence', 'evidence')], empty='No cardiomyopathy-labeled evidence is available in the current annotations. This is not a negative cardiomyopathy screen.')
    doc.p('For cardiomyopathy, this is a review of condition-labeled records, not a validated cardiomyopathy gene panel. There is no gene-by-gene coverage clearance. Rare pathogenic variants, including variants private to a family, can be missed at low depth and may be absent from an imputation reference. The current imputation integration is restricted to SNPs; it does not assess cardiomyopathy-associated indels, copy-number changes or structural variation through imputation.')
    doc.p('A clinically meaningful cardiomyopathy assessment integrates phenotype, family history, validated genetic testing and specialist interpretation. A relevant genetic finding needs confirmation and assessment of the appropriate gene-disease relationship and inheritance pattern. Even a negative clinical genetic test does not necessarily exclude cardiomyopathy or a genetic cause. The same distinction between research evidence and clinical assessment applies to the other diseases in this report.')
    doc.p('The disease index preserves benign and uncertain annotations as well as low-confidence evidence, rather than hiding those records or treating their absence as reassurance. It separately counts germline review candidates, unpaired somatic candidates and GWAS genotypes. This prevents a weak association, a tumor-related database assertion or a low-depth read from being presented as a confirmed inherited condition.')
    doc.link('Full disease compendium: every indexed label and all candidate rows', '../08_disease_review/disease_review.html')
    doc.link('Cardiomyopathy condition-tagged variants', '../08_disease_review/cardiomyopathy_variants.tsv')

    doc.section('6. Somatic candidates and the panel of normals', 'somatic')
    pon = c.get('pon') or {}
    doc.table([{'setting': 'Panel mode', 'value': pon.get('mode', 'Not recorded')},
               {'setting': 'Independent normal samples', 'value': len(pon.get('normals', [])) if pon.get('mode') == 'build' else 'Not recorded here'},
               {'setting': 'Population AF resource configured', 'value': bool(c.get('germline_resource'))},
               {'setting': 'Contamination estimation configured', 'value': bool(c.get('contamination_sites'))}], [('Setting', 'setting'), ('Value', 'value')])
    doc.p('Mutect2 and the panel of normals identify and filter candidate signals in the unpaired sample. A panel can remove recurrent technical artifacts, but it cannot prove the origin of a remaining variant. The report therefore uses unpaired somatic candidate language and does not infer cancer, clonal hematopoiesis or a constitutional inherited condition from that branch alone.')
    doc.p('Low-pass sequencing also limits the chance of observing low-frequency alternate alleles. A small panel of normals, an absent population allele-frequency resource or missing contamination estimates further limit interpretation; the recorded run warnings below identify which of those conditions apply. Somatic and germline evidence should be reviewed independently before considering any biological conclusion.')

    doc.section('7. Trait marker evidence', 'traits')
    doc.p('The expanded configuration contains a larger marker panel, but the rows below come only from the available sample result. A marker association can be informative without being a complete phenotype model. Most expanded markers are association annotations rather than standalone predictions. The report does not calculate a complete HIrisPlex-S pigmentation probability model or substitute a list of associated SNPs for a validated multi-variant predictor.')
    doc.table(traits, [('Trait', 'trait'), ('rsID', 'rsid'), ('Genotype', 'genotype'), ('Source', 'genotype_source'), ('Confidence', 'confidence'), ('DP', 'DP'), ('GQ', 'GQ'), ('GP', 'GP'), ('Status', 'status'), ('Interpretation', 'interpretation'), ('Marker limitation', 'limitation')], empty='No trait marker result is available.')
    doc.p('Direct calls use sequence-read evidence. Exploratory calls pass the intentionally relaxed trait thresholds and must not be treated as clinically reliable genotypes. Imputed calls use reference haplotypes and posterior probabilities. An unresolved marker remains unresolved even if its literature association is well established. Strong read calls that disagree with accepted imputation are retained with a disagreement flag for review.')
    doc.p('Missing provenance columns in an older result mean that the previous version did not record those fields. They do not imply that imputation was performed. This is particularly relevant to a snapshot created before restarting the expanded workflow: existing trait results are described as existing results rather than relabeled under thresholds that have not been applied to them.')

    if c.get('imputed_clinvar'):
        doc.section('Complete ClinVar annotation of imputed SNPs', 'imputed-clinvar')
        ic=d.get('imputed_clinvar') or {}
        doc.p(ic.get('scope','The comprehensive imputed ClinVar stage is configured, but its combined report is not yet available.'))
        doc.table([{'metric':k,'count':v} for k,v in ic.get('counts',{}).items()],[('Metric','metric'),('Records','count')])
        if ic:
            doc.p('These counts describe imputed records separately from the observed caller union. They overlap and must not be added as unique variants. Reference genotypes with pathogenic ClinVar annotations are not positive findings. All imputed SNP records are retained in the annotated BCFs, including low-confidence genotypes.')
            doc.link('Full imputed ClinVar report',f'../../imputed_clinvar/{d["sample"]}.imputed_clinvar.html')
            doc.link('Imputed ClinVar Excel: nonreference, pathogenic labels, and reference/missing genotypes',f'../../imputed_clinvar/{d["sample"]}.imputed_clinvar.xlsx')
    if c.get('interpretation_report'):
        doc.section('Integrated interpretation and variant priorities', 'interpretation')
        ir=d.get('integrated_report')
        if ir:
            doc.p('The integrated report combines clinical review, ancestry, maternal lineage, traits, disease scores and transparent sequencing/imputation support scores. Local LLM prose is identified separately from structured evidence.')
            doc.link('Read the integrated interpretation',f'../../interpretation/{d["sample"]}.interpretation/{d["sample"]}.interpretation.html')
            doc.link('Download sortable variant priorities',f'../../interpretation/{d["sample"]}.interpretation/{d["sample"]}.variant_priority.xlsx')
        else:doc.p('The integrated interpretation is configured but has not completed for this run.')
    if c.get('explain_findings'):
        doc.section('Findings explained with literature', 'explanations')
        ex=d.get('explanations') or {}
        if ex:
            doc.p(f"The explanation module covers {ex['trait_markers']} trait markers, {ex['gwas_rows']:,} GWAS associations, {ex['polygenic_models']} polygenic models, and {ex['pathogenic_label_alleles']} unique alleles with pathogenic/likely pathogenic labels in the analysis snapshot. {ex['changed_classifications']} public classifications differ from that snapshot. Labels and genotype evidence remain distinct.")
            doc.link('Full explanations, variant appendices and literature',f'../../explanations/{d["sample"]}.explanations/{d["sample"]}.explanations.html')
        else:doc.p('Explanation report is pending or unavailable for this run.')
    if c.get('polygenic'):
        doc.section('Broad trait models and polygenic scoring', 'polygenic')
        pg=d.get('polygenic') or {}
        doc.p(pg.get('interpretation','Published score calculation is configured, but its combined report is not yet available.'))
        doc.p('The additional models cover physical and quantitative traits and common diseases. Each model keeps its published effect weights and population metadata. Raw scores are reported only when every term is present. A partial score is a separately labeled sum over retained terms, without silently replacing missing variants by reference genotypes or rescaling the model.')
        doc.p('Matched-marker reference percentiles, where available, compare against the displayed 1000 Genomes population group. They are exploratory and reuse the imputation panel; they are not calibrated disease probabilities. Sex is used for applicability and age is recorded as context. Age- and sex-specific risk calibration has not been performed.')
        doc.table(pg.get('results',[]),[('PGS ID','pgs_id'),('Model','name'),('Trait','trait'),('Status','status'),('Complete raw score','raw_score'),('Partial score','partial_score'),('Variant coverage','variant_coverage'),('Absolute weight coverage','absolute_weight_coverage'),('Development ancestry','development_ancestry'),('Evaluation ancestry','evaluation_ancestry')],empty='Polygenic model results are not available.')
        if pg:
            doc.link('Complete model, allele matching and population report',f'../../polygenic/{d["sample"]}.polygenic.html')
            doc.link('Polygenic and broad trait Excel',f'../../polygenic/{d["sample"]}.polygenic_scores.xlsx')

    doc.section('8. GWAS Catalog: what the table actually says', 'gwas')
    if gwas['available']:
        total = gwas['association_rows']
        doc.table([{'status': status, 'rows': count, 'fraction': f'{count/max(total,1):.3%}'} for status, count in sorted(gwas['by_status'].items())], [('Recorded genotype evidence', 'status'), ('Association rows', 'rows'), ('Share of rows', 'fraction')])
        doc.table([{'status': status, 'positions': gwas['unique_positions_by_status'][status], 'rsids': gwas['unique_rsids_by_status'][status], 'traits': gwas['trait_labels_by_status'][status]} for status in ['called', 'imputed', 'exploratory']], [('Evidence tier', 'status'), ('Distinct positions', 'positions'), ('Distinct rsIDs', 'rsids'), ('Study phenotype labels', 'traits')])
    else:
        doc.p('A GWAS association table is not available for this report.')
    doc.p(f'{d["sample"]}.gwas.tsv is an annotation join between published GWAS Catalog associations and sample genotype evidence. It is not a GWAS performed on one individual. One rs identifier can appear in many rows because different studies, populations and phenotypes report the same locus. Conversely, different identifiers can refer to the same genomic position. Counts of association rows, positions, identifiers and phenotype labels answer different questions.')
    doc.p('The published p-value describes evidence for an association in the original study. It is not the probability that this sample has the disease. The reported odds ratio or beta belongs to that study model, its phenotype definition, units and population. This workflow retains the catalog effect-allele text without complete strand and allele harmonization, so it does not infer effect-allele dosage or combine effects into a personal risk estimate.')
    doc.p('A reference homozygous genotype can still be attached to a catalog association row. The presence of that row does not establish that the individual carries an effect allele, is protected, or has an elevated disease risk. Imputation may increase the number of usable genotype observations, but it does not by itself resolve these interpretation requirements. Population transferability and calibration would also be required for a validated risk model.')
    doc.link('Association counts for every study phenotype', '../06_gwas/association_counts_by_trait.tsv')
    doc.link('Disease-related association evidence', '../08_disease_review/gwas_disease_associations.tsv.gz')
    doc.table(gwas.get('preview', []), [('rsID', 'rsid'), ('Study phenotype', 'trait'), ('Genotype', 'genotype'), ('Status', 'status'), ('Published p', 'pvalue'), ('Study', 'study'), ('Population', 'study_population')], empty='No association rows with accepted direct, imputed or exploratory genotypes are available.')
    doc.p('The preview is a small sample of recorded associations in source order, not a ranking of medical importance or personal disease risk. Complete evidence remains in the full association table and the disease-related extract; no preview limit removes records from those files.')

    doc.section('9. Ancestry and maternal lineage', 'ancestry')
    if d.get('ancestry_explorer'):
        doc.link('Open the visual ancestry and QC explorer',f'../../ancestry_explorer/{d["sample"]}.ancestry_explorer/{d["sample"]}.ancestry_explorer.html')
        doc.p('The explorer includes broad reference-mixture estimates, mitochondrial haplogroup evidence and published Neanderthal-associated marker counts. Marker counts are not a genome-wide archaic ancestry percentage.')
    explorer=d.get('ancestry_explorer') or {}
    components=[{'component':r['component'],'percent':round(100*r['fraction'],2)} for r in explorer.get('admixture',{}).get('components',[])]
    doc.table(components,[('Reference component','component'),('Estimated fraction %','percent')],empty='Broad composition is unavailable.')
    regional=explorer.get('regional_similarity',{})
    if regional.get('populations'):
        doc.p(regional['scope'])
        doc.table(regional['populations'],[('Rank','rank'),('Reference population','population_name'),('Distance, lower is closer','distance'),('Reference N','reference_n')])
    if d.get('special_annotations'):
        doc.link('Mitochondrial, carried Neanderthal markers and somatic annotations',f'../../special_annotations/{d["sample"]}.special_annotations/{d["sample"]}.special_annotations.html')

    doc.section('10. GLIMPSE2 imputation and confidence rules', 'imputation')
    imp = d['imputation']
    doc.p(f'GLIMPSE2 enabled in the saved runtime: {imp["enabled"]}. Published chromosome BCFs found: {len(imp["chromosome_files"])}. Imputed target sites recorded in the trait summary: {fmt(tr.get("imputed_target_sites"))}. File presence and a configured option are reported separately; a planned imputation run is not described as a completed analysis.')
    doc.table([{'file': name} for name in imp['chromosome_files']], [('Published chromosome BCF', 'file')], empty='No published imputed chromosome BCFs are available.')
    doc.p('GLIMPSE2 combines low-coverage genotype likelihoods with a phased reference panel. It can improve genotype inference at variants represented in that panel. It does not create additional sequence reads, eliminate coverage gaps in the original data or discover a private alternate allele missing from the reference. Reference representation and allele frequency influence accuracy. The current integration prepares biallelic autosomal SNPs; short indels and other variant classes are not imputed by this implementation.')
    thresholds = tr.get('thresholds') or {}
    settings = [('Clinical/direct minimum DP', c.get('min_depth', 10)), ('Clinical/direct minimum GQ', c.get('min_gq', 20)),
                ('Trait mode in available result', tr.get('trait_mode', 'Legacy strict result or not recorded')),
                ('Exploratory trait minimum DP', thresholds.get('trait_min_depth', 'Not recorded in result')),
                ('Exploratory trait minimum GQ', thresholds.get('trait_min_gq', 'Not recorded in result')),
                ('Minimum called-genotype GP', thresholds.get('imputation_min_gp', 'Not recorded in result')),
                ('Minimum INFO/R2 when provided', thresholds.get('imputation_min_info', 'Not recorded in result'))]
    doc.table([{'setting': k, 'value': v} for k, v in settings], [('Evidence setting', 'setting'), ('Recorded value', 'value')])
    doc.p('The updated trait defaults accept exploratory read calls at DP ≥2 and GQ ≥5, while retaining the original stricter clinical evidence rules. GQ 5 corresponds to an approximate modeled genotype-error probability of 32%, which is why exploratory labels are essential. Imputed trait calls require the posterior probability of the reported genotype to meet the configured cutoff; the default GP cutoff is 0.90, with INFO/R2 ≥0.30 when that field is available. An absent INFO value is not a measured high-quality score.')
    doc.p('Those posterior and trait filters are research selection rules, not clinical validation. They must not be used to turn a low-pass disease-associated genotype into a confirmed pathogenic result. The clinical branch continues to use direct variant calls and its existing filters. Automatic public-reference setup downloads missing tools, maps and reference files to the configured cache; sample sequence data are processed locally.')

    doc.section('11. Reproducibility, execution and limitations', 'methods')
    doc.p('The workflow starts with input and reference checks, read QC and alignment, then duplicate marking and sequencing QC. The germline branch calls and normalizes autosomal variants before clinical and gene annotation. The somatic branch builds or loads a panel of normals, calls candidate variants and filters/annotates them. Ancestry uses the configured public reference. The optional GLIMPSE2 branch prepares references, phases chunks and ligates each chromosome before trait/GWAS annotation. Report generation reads the published outputs after the analysis finishes or stops.')
    doc.p('The --continue option reuses verified completed core tasks whose inputs, relevant settings, implementation and output files remain compatible. An interrupted analysis task cannot be resumed halfway through its internal computation and must restart. New trait/imputation/reporting work does not require discarding completed alignment or germline results. Keep the original work directory and Nextflow metadata to preserve that reuse.')
    doc.table([{'tool': k, 'version': v} for k, v in sorted(c.get('versions', {}).items())], [('Tool', 'tool'), ('Recorded version', 'version')])
    doc.table([{'field': k, 'value': v} for k, v in (d['clinvar_provenance'] or {}).items()], [('ClinVar provenance', 'field'), ('Value', 'value')], empty='ClinVar preparation provenance is not available.')
    warnings = list(c.get('warnings', []))
    if qc.get('warning'):
        warnings.append(qc['warning'])
    for warning in dict.fromkeys(warnings):
        doc.p(warning, banner=True)
    doc.table(d['trace'], [('Task', 'name'), ('Status', 'status'), ('Exit', 'exit'), ('Submitted', 'submit'), ('Duration', 'duration'), ('Peak memory', 'peak_rss')], empty='No execution trace is available.')
    if d.get('continuation'):
        doc.p(f'Verified completed core tasks listed for reuse: {len(d["continuation"].get("reused", {}))}. The latest trace describes the latest recorded execution; reused tasks from earlier runs may instead be listed in continuation.json.')
    doc.p('Scope exclusions remain explicit: no validated disease risk prediction, no diagnosis, no disease exclusion, no comprehensive gene-panel coverage assessment, and no interpretation of sex chromosomes, mitochondrial variants, CNVs/SVs or repeat expansions. Absence from a database is not evidence of benignity, and absence from a low-pass call set is not evidence that a variant is absent from the sample. A broader report increases the visibility of available evidence, not the analytical sensitivity of the sequencing experiment.')

    doc.section('12. Output guide and evidence files', 'files')
    doc.table([{'folder': k, 'purpose': v} for k, v in GROUPS.items()], [('Folder', 'folder'), ('Contents', 'purpose')])
    doc.p('The numbered directories are an organized view of published results. Existing analysis paths and the Nextflow work cache remain in place. Large files are linked rather than duplicated. Keep the complete sample output tree together: links to BAMs, VCFs and supporting files depend on that tree. The report text and embedded tables remain readable offline, and the browser print function can save them as PDF.')
    doc.p('The main report is an overview with full trait and coverage tables. The disease compendium contains every indexed condition label and every clinical review candidate, with no fixed page limit. Compressed TSV extracts preserve the underlying disease-related rows. All source GWAS phenotypes remain available in the complete catalog annotation, including measurements that are not diseases or did not match a navigation category.')
    doc.link('Complete file manifest with timestamps and small-file checksums', '../09_provenance/file_manifest.tsv')
    doc.link('Machine-readable report', f'{d["sample"]}.report.json')
    doc.link('Plain-text report', f'{d["sample"]}.report.txt')
    doc.link('Output start page', '../index.html')
    doc.table([{'category': e['category'], 'path': e['source_relative_to_output'], 'bytes': e['bytes']} for e in d['files']], [('Category', 'category'), ('Source path under sample output', 'path'), ('Bytes', 'bytes')])

    doc.section('13. Glossary and references', 'references')
    doc.table([
        {'term': 'DP', 'definition': 'Read depth recorded for the genotype; not an imputation-confidence measure.'},
        {'term': 'GQ', 'definition': 'Phred-scaled modeled genotype quality. A high value alone does not override coverage and other evidence checks.'},
        {'term': 'AD', 'definition': 'Allele-specific read counts, usually reference followed by alternate allele counts.'},
        {'term': 'GP', 'definition': 'Posterior probabilities of possible genotypes from imputation; the reported genotype has its own probability.'},
        {'term': 'DS', 'definition': 'Expected alternate-allele dosage from imputation; not automatically the dosage of a published risk allele.'},
        {'term': 'rsID', 'definition': 'A public variant identifier. It is not a disease label and is not equivalent to a unique study association.'},
        {'term': 'VUS', 'definition': 'Variant of uncertain significance. It is not a confirmed explanation for disease.'},
        {'term': 'Review candidate', 'definition': 'A record meeting the workflow evidence and database rules for further review, not a new clinical classification.'},
        {'term': 'Unresolved', 'definition': 'The available evidence does not meet the applicable rules. It is not a reference genotype or a negative result.'},
    ], [('Term', 'term'), ('Meaning in this report', 'definition')])
    for title, url in SOURCES:
        doc.link(title, url)
    return doc


def build(output, destination=None, status='snapshot', run_name=''):
    output = Path(output).resolve()
    folder = Path(destination).resolve() if destination else output/'deliverables'
    folder.mkdir(parents=True, exist_ok=True)
    for name, description in GROUPS.items():
        p = folder/name; p.mkdir(exist_ok=True)
        (p/'README.txt').write_text(description+'\nMissing source files mean the analysis may be pending, disabled or incomplete; they do not mean a negative result.\n')
    print('Collecting published evidence for the detailed report...', flush=True)
    d = collect(output, folder, status, run_name)
    d['generated_utc'] = datetime.now(timezone.utc).isoformat()
    d['files'] = inventory(output, folder, d['sample'])
    d['interpretation'] = 'Research evidence only. No disease diagnosis, exclusion, calibrated risk estimate or clinical reclassification.'
    prefix = folder/'00_report'/f'{d["sample"]}.report'
    old = Path(str(prefix)+'.json')
    if old.is_file():
        archive = folder/'00_report/history'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        archive.mkdir(parents=True)
        for extension in ['html', 'json', 'txt']:
            p = Path(str(prefix)+'.'+extension)
            if p.is_file():
                shutil.copy2(p, archive/p.name)
    disease_document(d, folder)
    doc = main_document(d)
    rendered = doc.render()
    Path(str(prefix)+'.html').write_text(rendered)
    Path(str(prefix)+'.txt').write_text('\n'.join(doc.text)+'\n')
    Path(str(prefix)+'.json').write_text(json.dumps(d, indent=2, ensure_ascii=False)+'\n')
    index = Document(f'{d["sample"]} · organized results', f'{status} · {d["generated_utc"]}',
                     'Incomplete snapshot: analyses may still be pending.' if status != 'completed' else 'Research outputs; interpret findings with the evidence limits in the report.')
    index.section('Reports')
    index.link('Read the detailed genome report', f'00_report/{d["sample"]}.report.html')
    index.link('Read the complete disease compendium', '08_disease_review/disease_review.html')
    index.section('Analysis files')
    for group, description in GROUPS.items():
        index.p(group+' — '+description)
        for entry in d['files']:
            if entry['category'] == group:
                index.link(entry['source_relative_to_output'], entry['organized_path'])
    records = folder/'09_provenance/all_pipeline_records'
    if (output/'pipeline_info').is_dir():
        target = os.path.relpath(output/'pipeline_info', records.parent)
        if records.is_symlink():
            if os.readlink(records) != target:
                records.unlink(); records.symlink_to(target, target_is_directory=True)
        elif not records.exists():
            records.symlink_to(target, target_is_directory=True)
        index.link('Complete pipeline records, including native execution reports', '09_provenance/all_pipeline_records/')
    index.link('File manifest', '09_provenance/file_manifest.tsv')
    (folder/'index.html').write_text(index.render())
    (folder/'README.txt').write_text('Open index.html, then the detailed report and disease compendium.\n'
                                  'Existing source outputs and the work cache were not moved. Numbered folders link to those outputs.\n'
                                  'HTML is self-contained and printable. Linked data require the complete output tree.\n'
                                  'Historical main-report snapshots are retained under 00_report/history. Data links refer to live published files.\n')
    if destination is None:
        landing = Document(f'{d["sample"]} · start here', status, 'Open the report to review evidence quality and analysis completeness.')
        landing.section('Results')
        landing.link('Organized outputs and reports', 'deliverables/index.html')
        landing.link('Detailed genome report', f'deliverables/00_report/{d["sample"]}.report.html')
        landing.link('Full disease compendium', 'deliverables/08_disease_review/disease_review.html')
        (output/'START_HERE.html').write_text(landing.render())
        # Preserve the original report location as a compatibility entry point.
        report_dir = output/'report'; report_dir.mkdir(exist_ok=True)
        redirect = Document(f'{d["sample"]} · report', status, 'The organized report contains the full results.')
        redirect.section('Detailed report')
        redirect.link('Open the complete report', f'../deliverables/00_report/{d["sample"]}.report.html')
        (report_dir/f'{d["sample"]}.report.html').write_text(redirect.render())
        (report_dir/f'{d["sample"]}.report.json').write_text(json.dumps(d, indent=2, ensure_ascii=False)+'\n')
    if destination is None:
        import organized_results
        organized_results.build(output,d)
    print(f'Report ready: {prefix}.html; {len(d["diseases"]):,} disease/study labels indexed.', flush=True)
    return d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Existing sample output tree, read as the source of findings')
    parser.add_argument('--destination', type=Path, help='Optional independent snapshot directory; default <output>/deliverables')
    parser.add_argument('--workflow-status', choices=['completed', 'incomplete', 'stopped', 'snapshot'], default='snapshot')
    parser.add_argument('--run-name', default='')
    args = parser.parse_args()
    build(args.output, args.destination, args.workflow_status, args.run_name)


if __name__ == '__main__':
    main()
