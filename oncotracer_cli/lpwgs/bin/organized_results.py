"""Publish one ordered result view without moving analysis inputs or work caches."""
import csv
import datetime
import fcntl
import html
import json
import os
from pathlib import Path
import shutil
import tarfile
from urllib.parse import quote
import uuid

VERSION='ordered-results-v6'
FOLDERS={
 '00_reports':'Start with the integrated interpretation or its short summary. The technical report retains detailed methods and analysis counts.',
 '01_health_review':'BRCA and cancer-gene review, cardiomyopathy report and disease compendium. A listed disease is not a diagnosis or a completed exclusion test.',
 '02_prioritized_variants':'Sortable evidence-support scores and clinical review priorities. A score is not a disease probability. Historical and unconfirmed pathogenic labels remain explicitly identified.',
 '03_ancestry':'Broad population composition, mitochondrial maternal lineage and archaic-marker evidence, with uncertainty and supporting data.',
 '04_traits_and_scores':'Trait interpretations, GWAS summaries and polygenic model results. Uncalibrated sums are not personal risk estimates.',
 '05_homozygosity':'Chromosome-level panel SNP homozygosity, exploratory runs of homozygosity, a schematic SVG, Excel and complete segment tables.',
 '06_special_annotations':'Annotated mitochondrial SNPs, carried Neanderthal-associated markers and read-supported somatic candidates. Open the Excel workbook for three separate tabs, or use the indexed annotated VCFs.',
 '07_cohort_vcf':'One normalized, annotated sample VCF with a tabix index, validation results and a merge helper. Somatic, germline and unresolved evidence stay in sample-specific fields.',
 '90_complete_tables':'Full annotated variant tables, caller records, imputed BCFs and scoring audits for detailed review.',
 '98_quality':'Sequencing depth, coverage and alignment quality. Nuclear and mitochondrial evidence are distinct.',
 '99_methods_and_sources':'Evidence rules, literature, analysis settings, input provenance and the complete file inventory.',
}
MAPPINGS=[
 ('01_health_review','cancer_review/{s}.cancer_review/*','cancer_report'),
 ('01_health_review','cardiomyopathy/{s}.cardiomyopathy/*','cardiomyopathy_report'),
 ('05_homozygosity','homozygosity/{s}.homozygosity/*','homozygosity_report'),
 ('00_reports','interpretation/{s}.interpretation/{s}.interpretation*.html','integrated_report'),
 ('00_reports','interpretation/{s}.interpretation/{s}.interpretation*.pdf','integrated_report'),
 ('00_reports','interpretation/{s}.interpretation/{s}.interpretation.txt','integrated_report'),
 ('00_reports','deliverables/00_report/{s}.report.*',None),
 ('02_prioritized_variants','interpretation/{s}.interpretation/{s}.variant_priority.*','integrated_report'),
 ('02_prioritized_variants','explanations/{s}.explanations/{s}.findings_explained.xlsx','explanations'),
 ('02_prioritized_variants','explanations/{s}.explanations/{s}.explanations.html','explanations'),
 ('02_prioritized_variants','explanations/{s}.explanations/{s}.clinical_explanations.tsv','explanations'),
 ('03_ancestry','ancestry_explorer/{s}.ancestry_explorer/*','ancestry_explorer'),
 ('99_methods_and_sources/reference_similarity','ancestry/{s}.*.tsv',None),
 ('99_methods_and_sources/reference_similarity','ancestry/{s}.ancestry.json',None),
 ('06_special_annotations','special_annotations/{s}.special_annotations/*','special_annotations'),
 ('04_traits_and_scores','traits_gwas/{s}.traits*',None),
 ('04_traits_and_scores','traits_gwas/{s}.gwas_trait_summary.tsv',None),
 ('04_traits_and_scores','polygenic/{s}.*','polygenic'),
 ('01_health_review','deliverables/08_disease_review/*',None),
 ('98_quality','alignment/*.qc.json',None),
 ('98_quality','alignment/*.coverage.txt',None),
 ('99_methods_and_sources','interpretation/{s}.interpretation/*.json','integrated_report'),
 ('99_methods_and_sources','explanations/{s}.explanations/source_manifest.json','explanations'),
 ('99_methods_and_sources','explanations/{s}.explanations/{s}.explanations.json','explanations'),
 ('99_methods_and_sources','variant_evidence/{s}.variant_evidence.json',None),
 ('99_methods_and_sources','traits_gwas/{s}.imputation_site_review.tsv',None),
 ('99_methods_and_sources','pipeline_info/continuation.json',None),
 ('99_methods_and_sources','pipeline_info/runtime.json',None),
 ('99_methods_and_sources','pipeline_info/preflight.json',None),
 ('99_methods_and_sources','pipeline_info/reused_output_links.json',None),
 ('07_cohort_vcf','cohort_vcf/{s}.cohort_vcf/*','cohort_vcf'),
 ('99_methods_and_sources','pipeline_info/patients.tsv',None),
 ('99_methods_and_sources/reference_statistics','polygenic/reference_statistics/*','polygenic'),
 ('90_complete_tables/observed_variants','variant_evidence/{s}.*',None),
 ('90_complete_tables/imputed_clinvar','imputed_clinvar/{s}.*','imputed_clinvar'),
 ('90_complete_tables/imputed_clinvar/chromosomes','imputed_clinvar/chromosomes/*','imputed_clinvar'),
 ('90_complete_tables/imputation','imputation/{s}.*',None),
 ('90_complete_tables/germline_calls','annotation/germline/*.annotated.vcf.gz*',None),
 ('90_complete_tables/somatic_calls','annotation/somatic/*.annotated.vcf.gz*',None),
 ('90_complete_tables/polygenic/models','polygenic/models/*','polygenic'),
 ('90_complete_tables/polygenic/scores','polygenic/scores/*','polygenic'),
 ('90_complete_tables/explanations','explanations/{s}.explanations/*.tsv.gz','explanations'),
 ('90_complete_tables/gwas','traits_gwas/{s}.gwas.tsv',None),
]


def link(path,label):
 return '<a href="'+html.escape(quote(str(path),safe='/#'))+'">'+html.escape(str(label))+'</a>'


def start_page(data,entries):
 sample=data['sample'];report=data.get('integrated_report');groups=[]
 def card(title,description,path,label):
  return '<article><h3>'+html.escape(title)+'</h3><p>'+html.escape(description)+'</p>'+link(path,label)+'</article>'
 def files(folders):
  sections=[]
  for folder in folders:
   selected=[e for e in entries if e['folder']==folder or e['folder'].startswith(folder+'/')]
   items=''.join('<li>'+link(e['path'],e['file'] if e['folder']==folder else e['folder'][len(folder)+1:]+'/'+e['file'])+'</li>' for e in selected)
   sections.append('<details><summary>'+html.escape(folder.replace('_',' '))+f' <span>{len(selected)} files</span></summary><p>'+html.escape(FOLDERS[folder])+'</p><ul>'+items+'</ul></details>')
  return ''.join(sections)
 def group(anchor,title,description,content,folders=()):
  groups.append((anchor,title,'<section id="'+anchor+'"><div class="section-title"><h2>'+html.escape(title)+'</h2><p>'+html.escape(description)+'</p></div>'+('<div class="cards">'+''.join(content)+'</div>' if content else '')+files(folders)+'</section>'))
 base=f'interpretation/{sample}.interpretation/'
 overview=[]
 if report:
  overview=[card('Full interpretation','Clinical review priorities, ancestry, maternal lineage and trait findings, with the evidence behind them.',base+sample+'.interpretation.html','Read the full report'),card('Key findings','A shorter overview for a first reading, with links to the detailed results.',base+sample+'.interpretation_summary.html','Read the summary')]
 elif data.get('explanations'):
  overview=[card('Findings and literature','Variant and trait explanations with supporting sources.',f'explanations/{sample}.explanations/{sample}.explanations.html','Read the explanations')]
 group('overview','Start with the findings','Choose a summary or the full interpretation.',overview,['00_reports'])
 health=[]
 if data.get('cancer_report'):
  c=data['cancer_report'];health.append(card('BRCA1, BRCA2 and cancer genes',c['brca_conclusion']+f" {c['candidate_count']} candidate allele(s) in the wider gene set need confirmation.",f'cancer_review/{sample}.cancer_review/{sample}.cancer_review.html','Open the cancer-gene review'))
 if data.get('cardiomyopathy_report'):
  health.append(card('Cardiomyopathy and related findings',data['cardiomyopathy_report']['conclusion'],f'cardiomyopathy/{sample}.cardiomyopathy/{sample}.cardiomyopathy.html','Open the cardiac report'))
 if any(e['file']=='disease_review.html' for e in entries):
  health.append(card('Disease evidence compendium','Explore disease-associated evidence by review priority. A listed condition is not a diagnosis.', 'deliverables/08_disease_review/disease_review.html','Browse the disease compendium'))
 group('health','Health and disease review','Focused clinical questions, candidate alleles and condition-specific context.',health,['01_health_review'])
 ancestry=[]
 if data.get('ancestry_explorer'):
  ancestry.append(card('Ancestry and maternal lineage','Population reference composition, mitochondrial haplogroup and Neanderthal-associated marker evidence.',f'ancestry_explorer/{sample}.ancestry_explorer/{sample}.ancestry_explorer.html','Explore ancestry'))
 if data.get('polygenic'):
  ancestry.append(card('Polygenic scores','Models ordered for review, with marker coverage and reference-population comparisons. Percentiles are not disease probabilities.',f'polygenic/{sample}.polygenic.html','Explore polygenic models'))
 trait_html=next((e for e in entries if e['folder']=='04_traits_and_scores' and e['file']==sample+'.traits.html'),None)
 if trait_html:ancestry.append(card('Traits and associations','Trait interpretations with direct sequencing, imputed and exploratory evidence distinguished.',trait_html['path'],'Explore trait findings'))
 group('ancestry-traits','Ancestry and traits','Population history, phenotype associations and polygenic evidence.',ancestry,['03_ancestry','04_traits_and_scores'])
 evidence=[]
 if report:evidence.append(card('Prioritized variant workbook','Sort by clinical review priority, carrier context, sequencing support and imputation confidence.',base+sample+'.variant_priority.xlsx','Open variant Excel'))
 if data.get('homozygosity_report'):
  evidence.append(card('Chromosomes and homozygosity','Per-chromosome homozygosity and a schematic view of candidate runs and unassessed regions.',f'homozygosity/{sample}.homozygosity/{sample}.homozygosity.html','Explore chromosomes and ROH'))
 if data.get('special_annotations'):
  evidence.append(card('Mitochondrial, archaic and somatic variants','Search annotated variants, gene effects and ClinVar matches in each evidence category.',f'special_annotations/{sample}.special_annotations/{sample}.special_annotations.html','Open annotated variant tables'))
 if data.get('cohort_vcf'):
  evidence.append(card('Annotated VCF for cohort merging','Normalized alleles, ClinVar and gene annotations, carrier context and sample-specific evidence flags.',f'cohort_vcf/{sample}.cohort_vcf/{sample}.annotated.vcf.gz','Download the annotated VCF'))
 group('variant-data','Variant and chromosome evidence','Inspect the evidence or take the annotated files into another analysis.',evidence,['02_prioritized_variants','05_homozygosity','06_special_annotations','07_cohort_vcf'])
 group('complete-data','Complete analysis tables','Full variant tables, imputed chromosomes and scoring audits.',[],['90_complete_tables'])
 group('quality','Sequencing quality','Depth, chromosome coverage and alignment metrics.',[],['98_quality'])
 group('methods','Methods and sources','Analysis settings, evidence rules, provenance and the complete file inventory.',[card('Technical analysis report','Detailed analysis counts, methods and workflow status.',f'deliverables/00_report/{sample}.report.html','Read the technical report')],['99_methods_and_sources'])
 nav=''.join('<a href="#'+a+'">'+html.escape(t)+'</a>' for a,t,_ in groups)
 return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+html.escape(sample)+''' results</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f4f6f2;color:#183b36;font:16px/1.65 system-ui,sans-serif}main{max-width:1240px;margin:auto;padding:32px}header{padding:24px 0}h1{font-size:42px;line-height:1.2;margin:8px 0}h2{font-size:27px;line-height:1.3;margin:0}h3{font-size:21px;line-height:1.35;margin-top:0}a{color:#056b69}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(295px,1fr));gap:18px}article,details{background:white;border:1px solid #d8e2d9;border-radius:13px;padding:23px}article{display:flex;flex-direction:column}article p{flex:1;margin-top:0}article a{font-weight:650}details{margin:13px 0}summary{font-weight:650;cursor:pointer}summary span{float:right;font-size:12px;color:#536d62}.note{background:#e7f1e8;border-left:4px solid #38826f;padding:18px;margin:20px 0}li{overflow-wrap:anywhere}.meta,footer{color:#557068;font-size:13px}code{overflow-wrap:anywhere}section{margin:40px 0;scroll-margin-top:20px}.section-title{border-bottom:2px solid #cbdcd1;margin-bottom:20px}.section-title p{color:#536d62;margin:8px 0 17px}nav{display:flex;gap:9px;flex-wrap:wrap}nav a{background:#e2ece4;padding:7px 12px;border-radius:7px;font-size:14px;text-decoration:none}@media(max-width:680px){main{padding:18px}.cards{grid-template-columns:1fr}h1{font-size:34px}summary span{float:none;display:block}}@media print{nav{display:none}section{break-inside:auto}article{break-inside:avoid}body{background:white}}</style></head><body><main><header><div class="eyebrow">Genome results · GRCh38</div><h1>'''+html.escape(sample)+'''</h1><p>Findings first. Supporting evidence and downloadable files below.</p><p class="meta">Analysis workflow: '''+html.escape(data['workflow_status'])+' · '+html.escape(data['generated_utc'])+'</p><nav aria-label="Results sections">'+nav+'</nav></header><p class="note">Review priorities combine clinical relevance and evidence support. They are not disease probabilities. Low-pass sequencing and imputation can leave clinically important variants unresolved.</p>'+''.join(body for _,_,body in groups)+'<footer><p>The ordered view is in <code>results/</code>. '+link('results/99_methods_and_sources/files.tsv','Complete file inventory')+'.</p></footer></main></body></html>'

def build(output,data):
 output=Path(output).absolute();root=output/'results';sample=data['sample'];info=output/'pipeline_info';info.mkdir(exist_ok=True,parents=True)
 with (info/'results_layout.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if root.is_symlink():raise ValueError('Refusing to replace a results directory that is a symlink')
  stage=output/('.results_build.'+uuid.uuid4().hex);stage.mkdir()
  try:
   for folder,description in FOLDERS.items():
    (stage/folder).mkdir();(stage/folder/'README.txt').write_text(description+'\n')
   entries=[];used={}
   for folder,pattern,required in MAPPINGS:
    if required and not data.get(required):continue
    for source in sorted(output.glob(pattern.format(s=sample))):
     if not source.is_file() or source.name=='README.txt':continue
     rel=Path(folder)/source.name;dest=stage/rel
     if rel in used:
      if used[rel]!=source:raise ValueError('Conflicting output names: '+str(rel))
      continue
     used[rel]=source;dest.parent.mkdir(exist_ok=True,parents=True)
     target=os.path.relpath(source,dest.parent)
     if source.suffix=='.html':
      url=html.escape(quote(target,safe='/'),quote=True)
      dest.write_text('<!-- generated by organized_results --><!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+url+'"><a href="'+url+'">Open report</a>')
     else:dest.symlink_to(target)
     entries.append({'folder':folder,'file':source.name,'path':str(Path('results')/rel),'source':str(source.resolve()),'bytes':source.stat().st_size})
   with (stage/'99_methods_and_sources/files.tsv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['folder','file','path','source','bytes'],delimiter='\t');w.writeheader();w.writerows(entries)
   (stage/'README.txt').write_text('Open ../START_HERE.html.\n00_reports contains the integrated interpretation and short summary.\n02_prioritized_variants contains the sortable support-score workbook.\n90_complete_tables contains full tables, imputed BCFs and detailed scoring audits.\nSymlinks and HTML redirects preserve original analysis paths. The support score is a research heuristic, not a disease probability.\n')
   (stage/'99_methods_and_sources/layout.json').write_text(json.dumps({'version':VERSION,'sample':sample,'files':len(entries)},indent=2)+'\n')
   if root.exists():
    archive=info/'layout_history';archive.mkdir(exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    target=archive/(stamp+'.results.tar.gz');tmp=target.with_suffix('.tmp')
    with tarfile.open(tmp,'w:gz',dereference=False) as tar:tar.add(root,arcname='results')
    tmp.replace(target)
    (archive/'README.txt').write_text('Previous generated result views, archived before replacement. Analysis sources were not moved. Each tar archive contains the old results/ tree, including its original relative symlinks. Extract into a separate review location or restore at the original output root when needed.\n')
    previous=output/('.results_previous.'+stamp);root.rename(previous)
    try:stage.rename(root)
    except BaseException:previous.rename(root);raise
    shutil.rmtree(previous)
   else:stage.rename(root)
   start=output/('.START_HERE.'+uuid.uuid4().hex+'.html');start.write_text(start_page(data,entries));start.replace(output/'START_HERE.html')
  finally:
   if stage.exists():shutil.rmtree(stage)
