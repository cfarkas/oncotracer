#!/usr/bin/env python3
"""Explain completed sample findings using exact-allele evidence and cited sources."""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import html
import json
import re
from pathlib import Path
import pysam

from extension_common import Excel, dump, flat, runtime_signature, sha256, table
import finding_sources as sources
from variant_evidence import pathogenic_label

from sample_variant_status import FIELDS as STATUS_FIELDS, METHOD as STATUS_METHOD, explanation_priority, for_marker

METHOD = 'finding-explanations-v3'
NOTE = ('This report explains research evidence. ClinVar classifies alleles for particular conditions; a database label does not confirm a genotype or diagnose disease. '
        'GLIMPSE2 uses the same sequence reads, so imputation is not independent confirmation. '
        'Missing or unresolved genotypes are not negative genetic tests. No calibrated personal risk, percentile, penetrance or treatment recommendation is inferred.')
CLINICAL_FIELDS = STATUS_FIELDS+['carrier_status','carrier_label','carrier_reason']+['variant','gene','rsid','category','observed_classification','local_review','public_classification','public_review',
    'public_allele_match','public_record_date','public_retrieved_utc','classification_changed','flagged_submissions','genotype_evidence','inheritance_context',
    'explanation','literature_scope','sources']
TRAIT_FIELDS = STATUS_FIELDS+['carrier_status','carrier_label','carrier_reason']+['rsid','trait','genotype','status','genotype_source','confidence','model','relevance','sample_explanation','limitations','sources']
PGS_FIELDS = ['pgs_id','name','trait','status','score_explanation','coverage_explanation','population_explanation','sources']


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt',newline='') as handle:
        yield from csv.DictReader(handle,delimiter='\t')


def ids(value):
    return re.findall(r'\b\d{5,9}\b',str(value or ''))


def allele_key(row):
    return f"chr{str(row['chrom']).removeprefix('chr')}:{int(row['pos'])}:{row['ref']}:{row['alt']}"


def pub_urls(pmids):
    return ['https://pubmed.ncbi.nlm.nih.gov/'+str(x)+'/' for x in pmids]


def urls(note):
    return list(dict.fromkeys(note.get('sources',[])+pub_urls(note.get('pmids',[]))))


def canonical_class(value):
    return ';'.join(sorted(x.strip() for x in re.split(r'[|/;]',re.sub(r'[_\s]+',' ',str(value)).strip().lower())))


def merge_clinical(paths):
    merged = {}
    for path in paths:
        origin = 'imputed_all_sites' if '.chr' in Path(path).name else 'observed_call_union'
        for row in rows(path):
            if row.get('pathogenic_label') != 'yes' or not pathogenic_label(row.get('clinvar_classification','.')): continue
            if row.get('genotype_state') in {'reference','missing_genotype'}: continue
            key = allele_key(row)
            if key not in merged: merged[key] = {'variant':key,'observations':[]}
            merged[key]['observations'].append({**row,'source_table':Path(path).name,'source':origin})
    return merged


def local_clinvar(merged,path):
    result = {}
    with pysam.VariantFile(str(path)) as vcf:
        release = [x.split('=',1)[1] for x in str(vcf.header).splitlines() if x.startswith('##fileDate=')]
        for key,entry in merged.items():
            ch,pos,ref,alt = key.split(':');pos = int(pos)
            contig = ch if ch in vcf.header.contigs else ch.removeprefix('chr')
            if contig not in vcf.header.contigs: continue
            matches = [r for r in vcf.fetch(contig,pos-1,pos) if r.pos==pos and r.ref==ref and r.alts==(alt,)]
            if len(matches) != 1: continue
            rec = matches[0]; info = {k:flat(v) for k,v in rec.info.items()}
            expected = {r.get('clinvar_allele_id') for r in entry['observations']} - {None,'','.'}
            if expected and expected != {info.get('ALLELEID')}: raise ValueError('ClinVar AlleleID mismatch for '+key)
            result[key] = {'variation_id':rec.id,'allele_id':info.get('ALLELEID'), 'rsid':'rs'+info['RS'] if info.get('RS') else '.',
                           'info':info,'file_date':release}
    return result


def clinical_explanation(entry, local, public, note):
    observations = entry['observations']; first = observations[0]
    if public and str(public['allele_id']) != str(local.get('allele_id')):
        raise ValueError('Public ClinVar AlleleID does not match local exact allele')
    # Allele IDs alone can persist across corrected archive coordinates. Never
    # transfer an assertion to a different GRCh38 allele, even when the ID agrees.
    from clinical_identity import compare_public_allele
    identity = compare_public_allele(entry['variant'], local, public)
    if identity['status'] == 'mismatch':
        first = observations[0]
        return {'variant':entry['variant'],'gene':first.get('gene','.'),'rsid':local.get('rsid','.'),
            'category':'annotation_mismatch','public_allele_match':'mismatch',
            'identity_check':identity,'observed_classification':first.get('clinvar_classification','.'),
            'local_review':first.get('clinvar_review','.'),'public_classification':'withheld_annotation_mismatch',
            'public_review':'not applicable to this allele','public_record_date':public.get('updated',''),
            'public_retrieved_utc':public.get('retrieved_utc',''),'classification_changed':False,'flagged_submissions':0,
            'genotype_evidence':'Local genotype observations are retained, but the disease assertion does not match the current public GRCh38 allele.',
            'inheritance_context':'Withheld: the clinical record refers to a different allele.',
            'explanation':identity['message']+' Do not use this annotation to infer disease or carrier status. Original source rows are retained for traceability.',
            'literature_scope':'Public GRCh38 allele identity check; clinical interpretation withheld.',
            'sources':public.get('url',''),'observations':observations,'public_record':public,'local_record':local}
    if note and (note['allele_id'] != local.get('allele_id') or note['variation_id'] != local.get('variation_id')):
        note = {}  # A coordinate alone is insufficient for a curated clinical paragraph.
    evidence=[]; supported=False
    for row in observations:
        if row['source']=='observed_call_union':
            supported |= row.get('category')=='germline_supported'
            evidence.append(f"Observed call: {row.get('germline_GT','.')} at DP={row.get('germline_DP','.')}, AD={row.get('germline_AD','.')}, filter={row.get('germline_filter','.')}; "
                            f"imputation: {row.get('imputed_GT','.')}, GP={row.get('imputed_GP','.')}, INFO={row.get('imputed_INFO','.')}, state={row.get('imputation_state','.')}. "
                            f"Evidence category: {row.get('category','unconfirmed')}.")
            readgt=set(re.split(r'[/|]',row.get('germline_GT','.')))
            impgt=set(re.split(r'[/|]',row.get('imputed_GT','.')))
            if readgt and impgt and '.' not in readgt|impgt and sorted(re.split(r'[/|]',row['germline_GT']))!=sorted(re.split(r'[/|]',row['imputed_GT'])):
                evidence.append('The read-call genotype and imputed genotype disagree; zygosity is unresolved without independent confirmation.')
        else:
            supported |= row.get('evidence_status')=='supported_imputed'
            evidence.append(f"Imputed allele: {row.get('genotype','.')} with called GP={row.get('called_GP','.')}, INFO={row.get('INFO','.')}, evidence={row.get('evidence_status','.')}. No independent read-depth confirmation is supplied by this imputed table.")
    old=first.get('clinvar_classification','.')
    current=public.get('classification','') if public else ''
    assertions=public.get('assertions',[]) if public else []
    flagged=sum(x['flagged'] for x in assertions)
    inheritance=sorted({x for a in assertions for x in a.get('inheritance',[])})
    context=note.get('inheritance') or ('Submitter-reported inheritance: '+'; '.join(inheritance) if inheritance else 'Inheritance is not established by the available structured evidence. A heterozygous label alone does not establish recessive disease; phase, second alleles and clinical context require review.')
    classification_changed=bool(current and canonical_class(old)!=canonical_class(current))
    explanation=f"The local annotation reports {old.replace('_',' ')} for {first.get('condition','.').replace('_',' ')}. "
    if public:
        explanation+=f"The public record {public['accession']} retrieved {public['retrieved_utc']} reports {current}; review status: {public['review']}. "
        if classification_changed: explanation+='The public classification differs from the analysis snapshot; both are retained for traceability. '
        if flagged: explanation+='One or more submissions are flagged. Flagged assertions must not be treated as current unflagged pathogenic evidence. '
        if assertions:
            description=[]
            for a in assertions:
                description.append(a['accession']+' ('+a.get('classification_domain','germline')+'): '+(a['classification'] or 'classification unavailable')+' for '+('; '.join(a['conditions']) or 'condition not supplied')+(' [flagged]' if a['flagged'] else '')+'; '+a['review'])
            explanation+='Condition-specific assertions: '+' | '.join(description)+'. '
    else:
        explanation+='A current public record could not be verified in this run; classification changes and condition-specific assertions are unresolved. '
    if note:explanation+='Curated literature review (2026-10-11): '
    explanation+=note.get('text','No manually reviewed variant-specific literature paragraph is available for this allele. Linked ClinVar citations identify papers to review; citation metadata alone is not proof of causality. ')
    explanation+=' '+note.get('clinical_message','Confirm the exact allele and genotype before reviewing inheritance, phenotype, penetrance, or clinical action.')
    links=urls(note)
    if public: links += [public['url']]+pub_urls(public.get('pmids',[]))
    elif local.get('variation_id','').isdigit(): links += ['https://www.ncbi.nlm.nih.gov/clinvar/variation/'+local['variation_id']+'/']
    if first.get('clinvar_url') not in {None,'','.'}:links.append(first['clinvar_url'])
    return {'variant':entry['variant'],'gene':note.get('gene',first.get('gene','.')),'rsid':local.get('rsid',note.get('rsid','.')),
        'category':'germline_supported' if supported else 'unconfirmed_or_candidate',
        'public_allele_match':identity['status'],'identity_check':identity,
        'observed_classification':old,'local_review':first.get('clinvar_review','.'),
        'public_classification':current or 'not_verified','public_review':public.get('review','not_verified') if public else 'not_verified',
        'public_record_date':public.get('updated','') if public else '', 'public_retrieved_utc':public.get('retrieved_utc','') if public else '',
        'classification_changed':classification_changed,'flagged_submissions':flagged,
        'genotype_evidence':' '.join(evidence),'inheritance_context':context,'explanation':explanation,
        'literature_scope':note.get('evidence_scope','Record-based explanation; linked papers have not been individually reviewed.'),
        'sources':'; '.join(dict.fromkeys(links)),'observations':observations,'public_record':public,'local_record':local}


def trait_explanation(row,bundle):
    note=bundle.get('traits',{}).get(row['trait'],{})
    resolved=row.get('status') in {'called','imputed','exploratory'}
    observed=f"{row['rsid']} at GRCh38 {row['chrom']}:{row['pos']} has reported genotype {row.get('genotype','.')}; source={row.get('genotype_source','.')}, status={row.get('status','.')}, confidence={row.get('confidence','.')}. "
    if resolved:
        observed+=row.get('interpretation','No directional interpretation supplied.')
        if row.get('model')=='association_only':observed+=' This is an association marker; no individual effect direction or phenotype probability is assigned.'
    else:observed+='The genotype is unresolved; an effect-allele absence or phenotype cannot be inferred.'
    links=urls(note)+pub_urls(ids(row.get('pubmed','')))
    if row.get('source_url','').startswith('https://') and 'snpedia' not in row['source_url']:links.append(row['source_url'])
    return {**{k:row.get(k,'.') for k in TRAIT_FIELDS},'relevance':note.get('text','Published marker annotation; no individually curated biological mechanism is available.'),
        'sample_explanation':observed,'limitations':row.get('limitation',''),'sources':'; '.join(dict.fromkeys(links)),**for_marker(row)}


def pgs_explanation(row):
    raw=row.get('raw_score');partial=row.get('partial_score')
    score=f"{row['pgs_id']} ({row.get('name','')}) models {row['trait']}. "
    score+=('The complete uncalibrated weighted sum is '+str(raw)+'. ' if raw is not None else
            'The reported partial weighted sum is '+str(partial)+'. Missing terms were not filled or rescaled. ')
    score+='The numerical score has no individual high/low, percentile or absolute-risk interpretation without suitable calibration.'
    return {**{k:row.get(k,'.') for k in PGS_FIELDS},'score_explanation':score,
        'coverage_explanation':f"{row['scored_variants']} of {row['expected_variants']} terms were scored; variant coverage={row['variant_coverage']:.3f}; absolute-weight coverage={row['absolute_weight_coverage']:.3f}. Status={row['status']}. Each included/excluded term is explained in the compressed variant appendix.",
        'population_explanation':str(row.get('population_interpretation',''))+' Development populations: '+str(row.get('development_ancestry',''))+'. Evaluation populations: '+str(row.get('evaluation_ancestry',''))+'.',
        'sources':'; '.join(x for x in [row.get('catalog_url'),('https://doi.org/'+row['publication_doi']) if row.get('publication_doi') else ''] if x)}


def explain_term(row,model):
    if row.get('status')=='scored':
        result=f"Matched {row.get('matched_ref')}>{row.get('matched_alt')} in {row.get('orientation')} orientation. Effect allele {row['effect_allele']} has model dosage {row['effect_dosage']} and coefficient {row['weight']}; contribution={row['contribution']} to {model['pgs_id']}. "
        result+='This contribution is in the published model scale and is not an individual disease probability or a causal claim.'
    else:result=f"Excluded from {model['pgs_id']}: {row.get('status') or row.get('issue')}. The term is missing from the computed sum; it is not evidence for a homozygous-reference genotype."
    return result


def write_gwas(path,target):
    count=0; pmids=set(); traits=defaultdict(Counter)
    fields=None
    with gzip.open(target,'wt',compresslevel=1,newline='') as handle:
        writer=None
        for row in rows(path):
            if writer is None:
                fields=list(dict.fromkeys([*row,*STATUS_FIELDS,'carrier_status','carrier_label','carrier_reason','relevance_explanation','publication_url']));writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t');writer.writeheader()
            citation=ids(row.get('pubmed',''));pmids.update(citation)
            state=row.get('status','unresolved');traits[row['trait']][state]+=1
            text=f"{row.get('rsid','.')} is a catalogued association with {row['trait']} in study {row.get('study','.')}; reported P={row.get('pvalue','.')}. "
            text+=f"This sample has genotype {row.get('genotype','.')} with evidence status {state}. "
            text+='The catalog effect allele is not fully harmonized to this genotype; the row cannot establish an individual risk direction, protection, or disease probability. '
            text+='The study population and reported effect estimate are preserved as study metadata.'
            if state not in {'called','imputed','exploratory'}:text+=' Unresolved evidence does not establish the presence or absence of the reported allele.'
            writer.writerow({**row,**for_marker(row,'gwas'),'relevance_explanation':text,'publication_url':'; '.join(pub_urls(citation))});count+=1
        if writer is None:handle.write('relevance_explanation\tpublication_url\n')
    table(str(target).replace('.gwas_variant_relevance.tsv.gz','.gwas_trait_index.tsv.gz'),['trait','association_rows','evidence_status_counts'],
          ({'trait':k,'association_rows':sum(v.values()),'evidence_status_counts':json.dumps(v,sort_keys=True)} for k,v in sorted(traits.items())))
    return count,len(traits),pmids


def html_report(path,sample,summary,clinical,traits,models,files):
    e=lambda x:html.escape(str(x),quote=True)
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(sample)+' · Findings explained</title>',
      '<style>body{font:17px/1.65 system-ui;color:#203445;margin:0 auto;padding:2rem;max-width:1100px}h1,h2{color:#15445b}article{padding:1.2rem;margin:1rem 0;border:1px solid #ccdbe1;border-radius:8px}nav{padding:1rem;background:#eff5f7}a{color:#075e90}code{overflow-wrap:anywhere}.note{padding:1rem;background:#fff1d5}input{padding:.7rem;width:95%}dd{margin:0 0 .7rem;overflow-wrap:anywhere}dt{font-weight:bold}@media print{input,button{display:none}article{break-inside:avoid}body{font-size:11pt;padding:0}h2{break-before:page}a{color:inherit}}</style>',
      '<h1>'+e(sample)+' — Findings explained</h1><p>'+e(summary['generated_utc'])+'</p><p class="note">'+e(('SYNTHETIC TEST ONLY. ' if summary['test_mode'] else '')+NOTE)+'</p>',
      '<nav><a href="#clinical">ClinVar findings</a> · <a href="#traits">Trait markers</a> · <a href="#scores">Polygenic models</a> · <a href="#appendices">All variant explanations</a></nav>',
      '<p>'+e(f"{len(clinical)} unique alleles with local pathogenic/likely pathogenic labels; {summary['supported_pathogenic_labels']} have germline-compatible genotype evidence. These counts are not counts of diagnosed conditions.")+'</p>',
      '<p>'+e(f"{summary['changed_classifications']} public classification updates; {summary['flagged_variants']} alleles with flagged submissions. Every available trait/GWAS/PGS row is retained in the corresponding explanation appendix.")+'</p>',
      '<p>'+e('Evidence retrieval: '+str(summary['public_requests_failed'])+' unavailable requests. Unverified sources are identified; previous analysis files are unchanged.')+'</p>',
      '<label for="filter">Find a gene, variant, trait or model</label><input id="filter" type="search">']
    for anchor,title,records,fields in [('clinical','ClinVar findings',clinical,CLINICAL_FIELDS),('traits','Trait markers',traits,TRAIT_FIELDS),('scores','Published polygenic and trait models',models,PGS_FIELDS)]:
        parts.append('<h2 id="'+anchor+'">'+title+'</h2>')
        if not records:parts.append('<p>No records are available from the supplied analysis outputs for this section.</p>')
        for row in records:
            name=row.get('variant') or row.get('rsid') or row.get('pgs_id')
            parts.append('<article><h3>'+e(name)+' · '+e(row.get('gene') or row.get('trait',''))+'</h3><dl>')
            names={'zygosity_label':'Genotype status','status_genotype':'Reported genotype','zygosity_source':'Genotype source','zygosity_evidence':'Genotype support','affected_label':'Affected status','carrier_label':'Carrier status'}
            for field in fields:
                if field in {'zygosity','affected_status','carrier_status','status_summary'}:continue
                if field=='sources':
                    links=[u for u in str(row.get(field,'')).split('; ') if u.startswith('https://')]
                    value=' · '.join('<a href="'+e(u)+'">'+e(u)+'</a>' for u in links) or 'No verified source supplied.'
                elif field in {'read_zygosity','imputed_zygosity'}:
                    from sample_variant_status import LABELS
                    value=e(LABELS.get(row.get(field),row.get(field,'.')))
                else:value=e(row.get(field,'.'))
                parts.append('<dt>'+e(names.get(field,field.replace('_',' ').capitalize()))+'</dt><dd>'+value+'</dd>')
            parts.append('</dl></article>')
    parts.append('<h2 id="appendices">Complete explanation appendices</h2><p>'+e(f"The GWAS appendix contains {summary['gwas_rows']:,} association rows across {summary['gwas_trait_labels']:,} reported trait labels. The PGS appendices contain {summary['pgs_variant_terms']:,} model terms. Rows are not omitted to fit the report or Excel.")+'</p><ul>')
    parts.extend('<li><a href="'+e(name)+'">'+e(name)+'</a></li>' for name in files)
    parts.append('</ul><p>Bibliographic metadata and exact ClinVar snapshots are recorded in the sources JSON. Only paragraphs explicitly marked as curated represent a manual review; other explanations describe the supplied result and its linked evidence.</p>')
    parts.append('<script>document.getElementById("filter").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll("article").forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(q));});window.addEventListener("beforeprint",()=>document.querySelectorAll("article").forEach(a=>a.hidden=false));</script></html>')
    path.write_text(''.join(parts))


def run(a,c):
    if a.sample!=c['sample']:raise ValueError('Sample/config mismatch')
    bundle=json.loads(Path(c['explanation_evidence']).read_text())
    if bundle.get('genome_build')!='GRCh38':raise ValueError('Expected a GRCh38 evidence bundle')
    output=Path(f'{a.sample}.explanations');output.mkdir(exist_ok=True)
    prefix=output/a.sample
    merged=merge_clinical(a.clinical)
    local=local_clinvar(merged,a.clinvar)
    print(f'Explaining {len(merged)} unique local pathogenic-label alleles for {a.sample}',flush=True)
    public,requests=sources.clinvar_records([r['variation_id'] for r in local.values()],c)
    clinical=[];pmids=set()
    for key,entry in merged.items():
        record=local.get(key,{})
        current=public.get(record.get('variation_id'),{})
        clinical.append(clinical_explanation(entry,record,current,bundle.get('variants',{}).get(key,{})))
        pmids.update(current.get('pmids',[]))
    from carrier_context import assess
    for entry in clinical:entry.update(assess(explanation_priority(entry),entry,clinical))
    clinical.sort(key=lambda r:(r['category']!='germline_supported',r['variant']))
    trait_rows=list(rows(a.traits));traits=[trait_explanation(row,bundle) for row in trait_rows]
    for row in trait_rows:
        pmids.update(ids(row.get('pubmed')));pmids.update(bundle.get('traits',{}).get(row['trait'],{}).get('pmids',[]))
    for note in bundle.get('variants',{}).values():pmids.update(note.get('pmids',[]))
    n_gwas,n_traits,gwas_pmids=write_gwas(a.gwas,str(prefix)+'.gwas_variant_relevance.tsv.gz');pmids.update(gwas_pmids)
    score_results=[]
    for path in a.scores:
        value=json.loads(Path(path).read_text())
        if value['sample']!=a.sample:raise ValueError('Mixed samples in score inputs')
        score_results.append(value)
    models=[pgs_explanation(r) for r in sorted(score_results,key=lambda r:(r['trait'],r['pgs_id']))]
    score_map={r['pgs_id']:r for r in score_results}
    if len(score_map)!=len(score_results):raise ValueError('Duplicate score models')
    terms=0;seen=set();audit_outputs=[]
    for path in a.audits:
        match=re.fullmatch(re.escape(a.sample)+r'\.(PGS\d{6})\.variant_audit\.tsv\.gz',Path(path).name)
        if not match or match[1] not in score_map or match[1] in seen:raise ValueError('Unknown/duplicate model audit: '+str(path))
        model=score_map[match[1]];seen.add(match[1]);count=0
        target=output/(a.sample+'.'+match[1]+'.variant_relevance.tsv.gz');audit_outputs.append(target.name)
        def explained():
            nonlocal count
            for row in rows(path):
                count+=1
                yield {**row,**for_marker(dict(row,genotype='.',genotype_source='Dosage-only model audit'),'pgs'),'pgs_id':match[1],'trait':model['trait'],'relevance_explanation':explain_term(row,model),
                       'source_url':model.get('catalog_url','')}
        from polygenic import AUDIT_FIELDS
        table(target,[*AUDIT_FIELDS,*STATUS_FIELDS,'carrier_status','carrier_label','carrier_reason','pgs_id','trait','relevance_explanation','source_url'],explained())
        if count!=model['expected_variants']:raise ValueError('Model audit row count differs from published terms')
        terms+=count
    if seen!=set(score_map):raise ValueError('Each score needs its full term audit')
    print(f'Literature metadata: {len(pmids)} public PMIDs; {n_gwas} GWAS rows; {terms} PGS terms',flush=True)
    bibliography,queries=sources.publications(pmids,c);requests+=queries
    dump(str(prefix)+'.sources.json',{'curated_evidence':bundle,'clinvar':public,'publications':bibliography,'requests':requests,
                                    'local_clinvar_file':str(Path(a.clinvar).resolve()),'literature_release':c['literature_release']})
    table(str(prefix)+'.clinvar_explanations.tsv',CLINICAL_FIELDS,clinical)
    table(str(prefix)+'.trait_explanations.tsv',TRAIT_FIELDS,traits)
    table(str(prefix)+'.model_explanations.tsv',PGS_FIELDS,models)
    table(str(prefix)+'.bibliography.tsv',['pmid','title','authors','year','journal','url','status','retrieved_utc','scope'],bibliography.values())
    book=Excel(str(prefix)+'.findings_explained.xlsx',[[a.sample+' · findings explained'],['Interpretation',NOTE],
        ['Complete variants','All GWAS and PGS terms are in compressed appendices; Excel contains the clinical, curated trait and model explanations.'],
        ['Public record updates','Original classifications are preserved beside current public review status.'],['Test mode',str(c['test_mode'])]])
    for name,fields,records in [('ClinVar_findings',CLINICAL_FIELDS,clinical),('Trait_markers',TRAIT_FIELDS,traits),('Polygenic_models',PGS_FIELDS,models)]:
        book.create(name,fields)
        for row in records:book.add(name,row)
    book.close()
    summary={'sample':a.sample,'method':METHOD,'variant_status_method':STATUS_METHOD,'test_mode':c['test_mode'],'generated_utc':sources.now(),'runtime_signature':runtime_signature(c),
        'pathogenic_label_alleles':len(clinical),'supported_pathogenic_labels':sum(r['category']=='germline_supported' for r in clinical),
        'annotation_mismatches':sum(r['category']=='annotation_mismatch' for r in clinical),
        'changed_classifications':sum(r['classification_changed'] for r in clinical),'flagged_variants':sum(bool(r['flagged_submissions']) for r in clinical),
        'trait_markers':len(traits),'gwas_rows':n_gwas,'gwas_trait_labels':n_traits,'polygenic_models':len(models),'pgs_variant_terms':terms,
        'public_requests_failed':sum(r['status']!='retrieved' for r in requests),'publications':len(bibliography),
        'curated_evidence_sha256':sha256(c['explanation_evidence']),'clinical':clinical,'traits':traits,'models':models,
        'input_provenance':[{'path':str(Path(p).resolve()),'bytes':Path(p).stat().st_size,'sha256':sha256(p)} for p in [a.traits,a.gwas,*a.clinical,*a.scores,*a.audits]],
        'interpretation':NOTE}
    dump(str(prefix)+'.explanations.json',summary)
    files=sorted(p.name for p in output.iterdir() if p.is_file())
    html_report(Path(str(prefix)+'.explanations.html'),a.sample,summary,clinical,traits,models,files)
    text=[a.sample+' — Findings explained',NOTE]
    for heading,records,fields in [('CLINVAR',clinical,CLINICAL_FIELDS),('TRAIT MARKERS',traits,TRAIT_FIELDS),('POLYGENIC MODELS',models,PGS_FIELDS)]:
        text.append('\n'+heading)
        for row in records:text.append('\n'+'\n'.join(k.replace('_',' ')+': '+str(row.get(k,'.')) for k in fields))
    text.append('\nComplete appendices:\n'+'\n'.join(files));Path(str(prefix)+'.explanations.txt').write_text('\n'.join(text)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'clinical','traits','models','input_provenance'}},indent=2),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ['config','sample','traits','gwas','clinvar']:p.add_argument('--'+flag,required=True)
    for flag in ['clinical','scores','audits']:p.add_argument('--'+flag,nargs='*',default=[])
    a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
if __name__=='__main__':main()
