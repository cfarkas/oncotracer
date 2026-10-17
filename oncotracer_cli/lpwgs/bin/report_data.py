"""Read published results and summarize evidence without reclassifying variants."""
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ASSIGNED = {'called', 'exploratory', 'imputed'}
CATEGORIES = [
    ('Cardiomyopathy', r'cardiomyopath'),
    ('Cancer and neoplasms', r'cancer|carcinoma|lymphoma|leuk[ae]mia|myeloma|melanoma|sarcoma|glioma|neoplasm|tumou?r|mesothelioma|neuroblastoma'),
    ('Cardiovascular and vascular disease', r'coronary|heart (?:disease|failure|attack)|myocardial infarct|atherosclero|stroke|atrial fibrillation|arrhythmia|hypertension|aortic|aneurysm|thrombo|embolism|cardiac|brugada|long qt|carditis|peripheral arter'),
    ('Diabetes, endocrine and metabolic disease', r'diabet|obesity|metabolic syndrome|thyroid|hyperlipid|hypercholester|dyslipid|hypertriglycer|gout|phenylketonuria|ha?emochromatosis|adrenal|cushing|addison'),
    ('Neurological and neurodevelopmental conditions', r'alzheimer|dementia|parkinson|epilep|seizure|multiple sclerosis|amyotrophic|motor neuron|neuropath|ataxia|huntington|migraine|autism|neurodevelopment|intellectual disability|muscular dystrophy|myasthenia|encephal'),
    ('Mental health and behavioral conditions', r'depress|bipolar|schizophren|anxiety|panic disorder|obsessive|attention deficit|adhd|anorexia|bulimia|addiction|substance use|alcohol dependence|post.?traumatic|psychosis|psychiatric'),
    ('Immune, inflammatory and allergic disease', r'rheumatoid|lupus|systemic sclerosis|scleroderma|sj.gren|autoimmun|allerg|atop|psoria|ankylosing|vasculitis|immunodeficien|sarcoido|inflammatory disease'),
    ('Respiratory disease', r'asthma|chronic obstructive|copd|pulmonary fibrosis|interstitial lung|bronchiect|cystic fibrosis|sleep apn|emphysema'),
    ('Digestive, liver and pancreatic disease', r'crohn|ulcerative colitis|inflammatory bowel|coeliac|celiac|cirrhosis|fatty liver|steatohepatitis|pancreatitis|gallstone|cholelith|irritable bowel|gastro.?esophageal|peptic ulcer|hepatitis'),
    ('Kidney and urinary disease', r'kidney disease|renal (?:disease|failure)|nephrop|glomerulo|nephrotic|polycystic kidney|kidney stone|urolith|urinary incontinence'),
    ('Blood and coagulation disorders', r'an[ae]mia|thalass[ae]mia|sickle cell|ha?emophilia|bleeding disorder|coagulation disorder|thrombocytopen|von willebrand|polycyth[ae]mia'),
    ('Eye, hearing and sensory disease', r'glaucoma|macular degeneration|retinitis|retinopathy|cataract|blindness|hearing loss|deafness|otosclero|keratoconus'),
    ('Bone, joint and connective tissue disease', r'osteopor|osteoarth|scoliosis|osteogenesis|ehlers|marfan|paget|bone disease|intervertebral disc|dupuytren'),
    ('Skin and hair disease', r'eczema|dermatitis|vitiligo|alopecia|acne|rosacea|hidradenitis|skin disease'),
    ('Infectious disease susceptibility', r'covid|sars.cov|tuberculosis|malaria|hiv|influenza|infection|leprosy|sepsis'),
    ('Reproductive and pregnancy conditions', r'endometriosis|polycystic ovary|infertility|preeclampsia|pre.eclampsia|pregnancy loss|miscarriage|uterine fibroid|leiomyoma|prostate hyperplasia'),
]
PATTERNS = [(name, re.compile(pattern, re.I)) for name, pattern in CATEGORIES]
PLACEHOLDERS = {'', '.', '-', 'not provided', 'not specified', 'not available', 'none', 'unknown'}


def read_json(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.is_file() else None


def rows(path):
    p = Path(path)
    if not p.is_file():
        return
    opener = gzip.open if p.suffix == '.gz' else open
    with opener(p, 'rt', newline='') as handle:
        yield from csv.DictReader(handle, delimiter='\t')


def write_rows(path, fields, records):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if p.suffix == '.gz' else open
    with opener(p, 'wt', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)


def label(value):
    return re.sub(r'\s+', ' ', str(value).replace('_', ' ')).strip()


def category(value, clinical=False):
    for name, pattern in PATTERNS:
        if pattern.search(value):
            return name
    return 'Other database-labeled conditions' if clinical else None


def condition_record(records, name, group):
    key = label(name).casefold()
    if key not in records:
        records[key] = {'condition': label(name), 'category': group,
                        'gwas_rows': 0, 'gwas_called': 0, 'gwas_imputed': 0,
                        'gwas_exploratory': 0, 'gwas_unresolved': 0,
                        'clinical_rows': 0, 'germline_review_candidates': 0,
                        'somatic_review_candidates': 0, 'clinical_low_evidence': 0,
                        'classification_counts': Counter(), 'sources': set()}
    return records[key]


def scan_gwas(path, folder, conditions):
    p = Path(path)
    if not p.is_file():
        write_rows(folder/'08_disease_review/gwas_disease_associations.tsv.gz', ['report_category', 'trait', 'status'], [])
        write_rows(folder/'06_gwas/association_counts_by_trait.tsv', ['trait', 'association_rows'], [])
        return {'available': False}
    totals = Counter()
    positions, identifiers, traits = set(), set(), set()
    supported_positions = {status: set() for status in ASSIGNED}
    supported_ids = {status: set() for status in ASSIGNED}
    supported_traits = {status: set() for status in ASSIGNED}
    per_trait = defaultdict(Counter)
    preview = []
    disease_count = 0
    destination = folder/'08_disease_review/gwas_disease_associations.tsv.gz'
    first = True
    with gzip.open(destination, 'wt', newline='') as handle:
        writer = None
        for row in rows(p):
            status = row.get('status', 'unknown')
            name = row.get('trait', 'Unlabeled phenotype')
            totals[status] += 1
            pos = (row.get('chrom', '').removeprefix('chr'), row.get('pos', ''))
            ids = re.findall(r'rs\d+', row.get('rsid', ''))
            positions.add(pos); identifiers.update(ids); traits.add(name)
            per_trait[name][status] += 1
            if status in ASSIGNED:
                supported_positions[status].add(pos)
                supported_ids[status].update(ids)
                supported_traits[status].add(name)
                if len(preview) < 25:
                    preview.append(row)
            group = category(name+' '+row.get('mapped_trait', ''))
            if first:
                writer = csv.DictWriter(handle, fieldnames=['report_category', *row], delimiter='\t')
                writer.writeheader(); first = False
            if group:
                disease_count += 1
                entry = condition_record(conditions, name, group)
                entry['gwas_rows'] += 1
                entry['gwas_'+(status if status in ASSIGNED else 'unresolved')] += 1
                entry['sources'].add('GWAS Catalog')
                writer.writerow({'report_category': group, **row})
        if first:
            handle.write('report_category\ttrait\tstatus\n')
    status_names = sorted(totals)
    write_rows(folder/'06_gwas/association_counts_by_trait.tsv', ['trait', 'association_rows', *status_names],
               ({'trait': name, 'association_rows': sum(counts.values()), **counts}
                for name, counts in sorted(per_trait.items())))
    return {'available': True, 'association_rows': sum(totals.values()), 'by_status': dict(totals),
            'unique_positions': len(positions), 'unique_rsids': len(identifiers), 'trait_labels': len(traits),
            'unique_positions_by_status': {k: len(v) for k, v in supported_positions.items()},
            'unique_rsids_by_status': {k: len(v) for k, v in supported_ids.items()},
            'trait_labels_by_status': {k: len(v) for k, v in supported_traits.items()},
            'disease_association_rows': disease_count, 'preview': preview}


CLINICAL_FIELDS = ['analysis_kind', 'chrom', 'pos', 'ref', 'alt', 'gene', 'consequence', 'genotype',
                   'DP', 'GQ', 'AD', 'filter', 'genotype_status', 'clinvar_classification',
                   'review_stars', 'condition', 'review_candidate', 'origin', 'clinvar_allele_id']


def scan_clinical(output, sample, folder, conditions):
    summaries = {}
    candidate_rows = []
    tagged_counts = Counter()
    available_tables = []
    evidence_path = folder/'08_disease_review/clinical_condition_variants.tsv.gz'
    cardio_path = folder/'08_disease_review/cardiomyopathy_variants.tsv'
    with gzip.open(evidence_path, 'wt', newline='') as ef, cardio_path.open('w', newline='') as cf:
        writer = csv.DictWriter(ef, fieldnames=CLINICAL_FIELDS, delimiter='\t', extrasaction='ignore')
        cardiac = csv.DictWriter(cf, fieldnames=CLINICAL_FIELDS, delimiter='\t', extrasaction='ignore')
        writer.writeheader(); cardiac.writeheader()
        for kind in ['germline', 'somatic']:
            prefix = output/'annotation'/kind/f'{sample}.{kind}'
            summary = read_json(str(prefix)+'.annotation.json')
            summaries[kind] = summary
            path = Path(str(prefix)+'.all_variants.tsv')
            if path.is_file():
                available_tables.append(kind)
            for row in rows(path):
                names = sorted({label(n) for n in row.get('condition', '').split('|')
                                if label(n).casefold() not in PLACEHOLDERS})
                if not names:
                    continue
                row = {'analysis_kind': kind, **row}
                writer.writerow(row); tagged_counts[kind] += 1
                if 'cardiomyopath' in row.get('condition', '').lower():
                    cardiac.writerow(row)
                for name in names:
                    entry = condition_record(conditions, name, category(name, clinical=True))
                    entry['sources'].add('ClinVar')
                    entry['clinical_rows'] += 1
                    entry['classification_counts'][row.get('clinvar_classification', '.')] += 1
                    if row.get('review_candidate') == 'yes':
                        entry[kind+'_review_candidates'] += 1
                    if row.get('genotype_status') not in {'called', 'somatic_candidate'}:
                        entry['clinical_low_evidence'] += 1
            candidate_file = Path(str(prefix)+'.pathogenic_candidates.tsv')
            for row in rows(candidate_file):
                candidate_rows.append({'analysis_kind': kind, **row})
    write_rows(folder/'08_disease_review/all_review_candidates.tsv', CLINICAL_FIELDS, candidate_rows)
    return {'summaries': summaries, 'available_tables': available_tables,
            'condition_tagged_rows': dict(tagged_counts), 'candidates': candidate_rows}


def stats(path):
    result = {}
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            fields = line.split('\t')
            if len(fields) >= 4 and fields[0] == 'SN':
                result[fields[2].rstrip(':')] = int(fields[3])
    return result


def current_extension(path,config):
    value=read_json(path)
    if value is None:return None
    signature=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return value if value.get('runtime_signature')==signature else None


def collect(output, folder, status, run_name=''):
    config = read_json(output/'pipeline_info/runtime.json') or {}
    sample = config.get('sample', output.name)
    qc = read_json(output/'alignment'/f'{sample}.qc.json')
    traits = read_json(output/'traits_gwas'/f'{sample}.traits_gwas.json')
    ancestry = read_json(output/'ancestry'/f'{sample}.ancestry.json')
    conditions = {}
    gwas = scan_gwas(output/'traits_gwas'/f'{sample}.gwas.tsv', folder, conditions)
    clinical = scan_clinical(output, sample, folder, conditions)
    condition_rows = []
    for item in conditions.values():
        item['sources'] = sorted(item['sources'])
        item['classification_counts'] = dict(item['classification_counts'])
        item['anchor'] = 'disease-'+hashlib.sha256(item['condition'].casefold().encode()).hexdigest()[:16]
        condition_rows.append(item)
    from disease_priority import prioritize, FIELDS as PRIORITY_FIELDS
    interpretation=current_extension(output/'interpretation'/f'{sample}.interpretation'/f'{sample}.interpretation.json',config)
    polygenic=current_extension(output/'polygenic'/f'{sample}.polygenic.json',config)
    condition_rows=prioritize(condition_rows,interpretation,polygenic)
    fields = PRIORITY_FIELDS + ['condition', 'category', 'sources', 'gwas_rows', 'gwas_called', 'gwas_imputed', 'gwas_exploratory',
              'gwas_unresolved', 'clinical_rows', 'germline_review_candidates', 'somatic_review_candidates',
              'clinical_low_evidence', 'classification_counts']
    write_rows(folder/'08_disease_review/disease_index.tsv', fields,
               ({**x, 'sources': '; '.join(x['sources']), 'classification_counts': json.dumps(x['classification_counts'], sort_keys=True)} for x in condition_rows))
    coverage = list(rows(output/'alignment'/f'{sample}.coverage.txt'))
    coverage = [x for x in coverage if x.get('#rname', '').removeprefix('chr') in {str(i) for i in range(1, 23)}]
    traces = sorted((output/'pipeline_info').glob('trace*.tsv'), key=lambda p: p.stat().st_mtime)
    trace = list(rows(traces[-1])) if traces else []
    lanes = []
    for p in sorted((output/'qc/lanes').glob('*.json')):
        value = read_json(p) or {}
        if 'summary' in value:
            before = value['summary'].get('before_filtering', {})
            after = value['summary'].get('after_filtering', {})
            lanes.append({'lane': p.stem, 'input_reads': before.get('total_reads'), 'retained_reads': after.get('total_reads'),
                          'q30_rate': after.get('q30_rate'), 'gc_content': after.get('gc_content')})
    imputed = sorted((output/'imputation').glob(f'{sample}.*.imputed.bcf'))
    references = [read_json(p) for p in sorted((output/'imputation/reference_provenance').glob('*.json'))]
    return {'sample': sample, 'workflow_status': status, 'run_name': run_name, 'test_mode': bool(config.get('test_mode')),
            'config': config, 'qc': qc, 'traits_gwas': traits, 'ancestry': ancestry, 'gwas': gwas,
            'clinical': clinical, 'diseases': condition_rows, 'coverage': coverage, 'lane_qc': lanes,
            'germline_statistics': stats(output/'variants/germline'/f'{sample}.germline.stats.txt'),
            'somatic_statistics': stats(output/'variants/somatic_candidates'/f'{sample}.somatic.stats.txt'),
            'trace': trace, 'trace_file': str(traces[-1].relative_to(output)) if traces else None,
            'continuation': read_json(output/'pipeline_info/continuation.json'),
            'clinvar_provenance': read_json(output/'pipeline_info/clinvar.provenance.json'),
            'imputed_clinvar': current_extension(output/'imputed_clinvar'/f'{sample}.imputed_clinvar.json',config) if config.get('imputed_clinvar') else None,
            'ancestry_explorer': current_extension(output/'ancestry_explorer'/f'{sample}.ancestry_explorer'/f'{sample}.ancestry_explorer.json',config) if config.get('ancestry_explorer') else None,
            'cohort_vcf': current_extension(output/'cohort_vcf'/f'{sample}.cohort_vcf'/f'{sample}.cohort_vcf.json',config) if config.get('unified_vcf') else None,
            'integrated_report': interpretation if config.get('interpretation_report') else None,
            'explanations': current_extension(output/'explanations'/f'{sample}.explanations'/f'{sample}.explanations.json',config) if config.get('explain_findings') else None,
            'polygenic': polygenic if config.get('polygenic') else None,
            'homozygosity_report': current_extension(output/'homozygosity'/f'{sample}.homozygosity'/f'{sample}.homozygosity.json',config) if config.get('homozygosity_report') else None,
            'cancer_report': current_extension(output/'cancer_review'/f'{sample}.cancer_review'/f'{sample}.cancer_review.json',config) if config.get('cancer_report',True) and config.get('interpretation_report') else None,
            'cardiomyopathy_report': current_extension(output/'cardiomyopathy'/f'{sample}.cardiomyopathy'/f'{sample}.cardiomyopathy.json',config) if config.get('cardiomyopathy_report') else None,
            'special_annotations': current_extension(output/'special_annotations'/f'{sample}.special_annotations'/f'{sample}.special_annotations.json',config),
            'variant_evidence': read_json(output/'variant_evidence'/f'{sample}.variant_evidence.json'),
            'imputation': {'enabled': bool(config.get('glimpse2')), 'chromosome_files': [p.name for p in imputed],
                           'references': references}}
