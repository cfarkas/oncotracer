"""Self-contained integrated HTML and matched, searchable PDF reports."""
import argparse
import html
import json
from clinical_narrative import readable
from pathlib import Path
from report_layout import WIDE_CSS, CALIBRATION_NOTE, kind, display

CSS='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f5f6f3;color:#16312f;font:16px/1.65 system-ui,Segoe UI,sans-serif}a{color:#056b69}header{padding:48px max(24px,calc((100vw - 1180px)/2));background:#123d3a;color:#fff}header a{color:#a8e5db}h1{font-size:2.5rem;line-height:1.15;margin:10px 0 18px}h2{font-size:1.6rem;line-height:1.3}h3{line-height:1.35}.eyebrow{text-transform:uppercase;letter-spacing:.13em;font-size:.8rem}.sub{color:#c1d7d2;max-width:900px}.wrap{max-width:1240px;margin:auto;display:grid;grid-template-columns:210px minmax(0,1fr);gap:30px;padding:30px}nav{position:sticky;top:20px;height:max-content;max-height:90vh;overflow:auto;font-size:.85rem}nav a{display:block;padding:7px;border-left:2px solid #d5dfd9;text-decoration:none}main{min-width:0}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card,section{background:white;border:1px solid #dce5df;border-radius:14px;padding:24px;margin-bottom:24px}.card strong{font-size:1.8rem;display:block}.caption,.meta{font-size:.84rem;color:#536762}.conclusion{padding:16px 20px;background:#eaf4ef;border-left:4px solid #137d6e;border-radius:5px}.hold{background:#fff6e5;border-color:#c48324}.badge{font-size:.72rem;display:inline-block;padding:3px 9px;background:#edf2ef;border-radius:12px}.facts{border-left:2px solid #cedad2;padding-left:17px}.facts p{font-size:.88rem;white-space:pre-wrap;overflow-wrap:anywhere}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.83rem}th,td{padding:10px;border-bottom:1px solid #dde6df;text-align:left;vertical-align:top}th{background:#edf3ef;position:sticky;top:0;cursor:pointer}td{min-width:100px;max-width:380px;overflow-wrap:anywhere}.score{width:100px;display:flex;background:#e8ece9;height:8px;border-radius:4px;overflow:hidden;margin-top:5px}.seq{background:#12675c}.imp{background:#50a8c1}.agr{background:#727dce}.bar{height:17px;background:#e6eee8;border-radius:4px;overflow:hidden}.bar span{display:block;height:100%;background:#278678}.ancestryline{display:grid;grid-template-columns:210px 1fr 65px;gap:12px;margin:12px 0;align-items:center}.sources{font-size:.8rem;overflow-wrap:anywhere}.search{width:100%;padding:12px;border:1px solid #bacfc6;border-radius:7px;margin:8px 0 18px;font:inherit}details{border-top:1px solid #dce5df;padding:13px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f4;padding:15px;font-size:.8rem}.links a{display:inline-block;margin:5px 16px 5px 0}footer{padding:30px;color:#536762;text-align:center}@media(max-width:900px){.wrap{display:block}nav{position:static;max-height:none;display:flex;flex-wrap:wrap;gap:5px;margin-bottom:20px}.cards{grid-template-columns:1fr}.ancestryline{grid-template-columns:145px 1fr 60px}h1{font-size:2rem}}@media print{body{background:white;font-size:10pt}nav,.search,.no-print{display:none}.wrap{display:block;padding:0}header{padding:20px;background:white;color:#16312f}.sub,header a{color:#16312f}.card,section{border:0;padding:12px 0;break-inside:auto}h2,h3{break-after:avoid}table{font-size:8pt}thead{display:table-header-group}.scroll{overflow:visible}a{color:#16312f}tr{break-inside:avoid}.cards{display:flex}details{display:block}}'''

CSS += WIDE_CSS + '''#priority-table{width:2070px;min-width:2070px;table-layout:fixed}#priority-table th,#priority-table td{min-width:0;max-width:none;word-break:normal;overflow-wrap:break-word;white-space:normal}@media print{#priority-table{width:100%;min-width:0;table-layout:auto}#priority-table col{width:auto!important}}'''

def e(x):return html.escape(str(x if x is not None else 'Not available'),quote=True)


def table(records,columns,id=None):
    header=''.join('<th class="'+kind(k)+'" data-kind="'+('number' if kind(k)=='number' else 'text')+'">'+e(t)+'</th>' for t,k in columns)
    body=''.join('<tr>'+''.join('<td class="'+kind(k)+'">'+e(display(r.get(k)))+'</td>' for t,k in columns)+'</tr>' for r in records)
    return '<p class="table-hint">Scroll horizontally to view every column.</p><div class="scroll" tabindex="0"><table class="readable"'+(' id="'+id+'"' if id else '')+'><thead><tr>'+header+'</tr></thead><tbody>'+body+'</tbody></table></div>'


def bars(data):
    return ''.join('<div class="ancestryline"><span>'+e(r['component'])+'</span><div class="bar"><span style="width:'+str(100*r['fraction'])+'%"></span></div><b>'+f"{100*r['fraction']:.1f}%"+'</b></div>' for r in data.get('ancestry',{}).get('admixture',{}).get('components',[]))


def priority_table(data):
    parts=['<label>Sort variants: <select data-sort-table="priority-table"><option value="0">Review priority — highest first</option><option value="3">Genotype support — highest first</option><option value="2">Gene / variant</option></select></label><input class="search" id="variant-filter" placeholder="Find a gene, variant, condition or evidence status" aria-label="Filter variants"><p class="table-hint">Scroll horizontally to view genotype, carrier and affected status alongside the evidence.</p><div class="scroll" tabindex="0" aria-label="Variant review table"><table id="priority-table"><colgroup><col style="width:155px"><col style="width:145px"><col style="width:220px"><col style="width:155px"><col style="width:155px"><col style="width:170px"><col style="width:245px"><col style="width:260px"><col style="width:175px"><col style="width:390px"></colgroup><thead><tr>']
    parts += ['<th data-kind="'+('number' if i in {0,3} else 'text')+'">'+x+'</th>' for i,x in enumerate(['Review priority / 100','Review group','Variant / gene','Genotype support / 100','Sequencing','GLIMPSE2','Reported zygosity','Carrier status','Affected status','Clinical interpretation'])];parts.append('</tr></thead><tbody>')
    for r in data['priority_findings']:
        parts.append('<tr><td data-value="'+str(r['review_priority_score'])+'"><b>'+str(r['review_priority_score'])+'</b><br><small>'+e(r.get('genetic_context',''))+'</small></td><td>'+e(r['priority_group'].replace('_',' '))+'</td><td><b>'+e(r['gene'])+'</b><br>'+e(r['rsid'])+'<br>'+e(r['variant'])+'</td>')
        parts.append('<td data-value="'+str(r['support_score'])+'"><b>'+str(r['support_score'])+'</b><div class="score"><span class="seq" style="width:'+str(r['support_score'])+'%"></span></div><small>'+e(r['support_band'].replace('_',' '))+'<br>Agreement: '+str(r['agreement_points'])+'<br>Discordance penalty: '+str(r['discordance_penalty'])+'</small></td>')
        parts.append('<td>'+e(r['read_genotype'])+'<br>DP '+e(r['read_DP'])+' · ALT '+e(r['read_ALT'])+'<br>GQ '+e(r['read_GQ'])+'<br>'+e(r['sequencing_points'])+' points</td>')
        parts.append('<td>'+e(r['imputed_genotype'])+'<br>GP '+e(r['imputed_GP'])+' · INFO '+e(r['imputed_INFO'])+'<br>'+e(r['imputation_points'])+' points</td>')
        parts.append('<td><b>'+e(r.get('zygosity_label','Unresolved'))+'</b><br>'+e(r.get('status_genotype','.'))+'<p>'+e(r.get('zygosity_source',''))+'</p><small>'+e(r.get('zygosity_evidence',''))+'</small></td>')
        parts.append('<td><b>'+e(r.get('carrier_label','Unresolved'))+'</b><p>'+e(r.get('carrier_reason','Inheritance not reviewed.'))+'</p></td>')
        parts.append('<td>'+e(r.get('affected_label','Affected status not established'))+'</td>')
        parts.append('<td><span class="badge">'+e(r['interpretation_status'].replace('_',' '))+'</span><p><b>'+e(r['current_classification'])+'</b><br>Annotated condition: '+e(readable(r['condition']))+'</p><p>'+e(r.get('patient_meaning',r['clinical_note']))+'</p><small>'+e(r['support_flags'].replace('_',' '))+'</small></td></tr>')
    parts.append('</tbody></table></div><p class="caption">Sorted by review priority, highest first. Use the selector to order by genotype support. The Excel contains all '+f"{data['scored_clinvar_variants']:,}"+' ClinVar-matched variant records in the scoring scope. The bar shows total support; separate columns retain the sequencing and imputation contributions. Disagreements can reduce the total.</p>')
    return ''.join(parts)


def render_html(d,folder):
    folder=Path(folder);sample=d['sample'];gen=d['generation'];refs={r['url']:r['id'] for r in d['sources']}
    candidates=sum(r['interpretation_status']=='confirmation_candidate' for r in d['priority_findings'])
    holds=sum(r['interpretation_status']=='annotation_hold' for r in d['priority_findings'])
    def sources(urls):return ''
    header='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+e(sample)+' · Integrated interpretation</title><style>'+CSS+'</style></head><body>'
    header+='<header><div class="eyebrow">Low-pass whole-genome sequencing · GRCh38</div><h1>'+e(sample)+'<br>Results and interpretation</h1><p class="sub">An integrated reading of the most relevant clinical candidates, ancestry, maternal lineage, traits and polygenic models.</p><p class="meta sub">Generated '+e(d['generated_utc'])+' · '+('SYNTHETIC TEST — not biological results' if d.get('test_mode') else 'Research report')+'</p><div class="links"><a href="../../START_HERE.html">All results</a><a href="'+sample+'.interpretation_summary.html">Short interpretation</a><a href="'+sample+'.variant_priority.xlsx">Sortable variant Excel</a>'
    if (folder/(sample+'.interpretation.pdf')).exists() or d.get('pdf_expected'):header+='<a href="'+sample+'.interpretation.pdf">Full PDF</a>'
    header+='</div></header>'
    parts=[header,'<div class="wrap"><nav>']
    for s in d['sections']:parts.append('<a href="#'+s['id']+'">'+e(s['title'])+'</a>')
    parts+=['<a href="#priority">Variant ranking</a><a href="#clinical-appendix">Clinical detail</a><a href="#trait-appendix">All trait markers</a><a href="#model-appendix">All polygenic models</a><a href="#sources">Methods and sources</a></nav><main>']
    parts.append('<div class="cards"><div class="card"><strong>'+str(candidates)+'</strong>confirmation candidate(s)</div><div class="card"><strong>'+str(holds)+'</strong>annotation hold(s)</div><div class="card"><strong>'+str(len(d['models']))+'</strong>polygenic models</div></div>')
    parts.append('<p class="conclusion">'+e(d['note'])+'</p>')
    patient=d.get('patient',{});parts.append('<p class="meta">Sample '+e(patient.get('name',sample))+' · Age: '+e(patient.get('age_display','Not available'))+' · Sex: '+e(patient.get('sex','unknown'))+' ('+e(patient.get('sex_source','not available'))+')</p>')
    parts.append('<section id="priority"><h2>Variant evidence and review priority</h2><p>Review priority combines clinical context and genotype evidence. Possible recessive carriers receive fewer priority points than genotypes compatible with disease. Genotype support is shown separately; annotation holds remain excluded.</p>'+priority_table(d)+'</section>')
    for n,s in enumerate(d['sections'],1):
        parts.append('<section id="'+s['id']+'"><span class="eyebrow">'+f'{n:02d}'+'</span><h2>'+e(s['title'])+'</h2><p class="conclusion">'+e(s['conclusion'])+'</p>')
        if s['id']=='ancestry':
            parts.append(bars(d))
            regional=d['ancestry'].get('regional_similarity',{})
            if regional.get('populations'):parts.append('<h3>Detailed reference similarity</h3><p>'+e(regional['scope'])+'</p>'+table(regional['populations'][:5],[('Rank','rank'),('Reference population','population_name'),('Distance, lower is closer','distance'),('Reference N','reference_n')])+'<a href="../../ancestry_explorer/'+sample+'.ancestry_explorer/'+sample+'.ancestry_explorer.html#population">See all reference populations</a>')
        if s['id']=='maternal':parts.append('<h3>Estimated maternal lineage: '+e(d['ancestry'].get('mitochondrial',{}).get('haplogroup') or 'Unresolved')+'</h3>')
        for p in s['paragraphs']:parts.append('<p>'+e(p['text'])+'</p>')
        parts.append('</section>')
    parts.append('<section id="clinical-appendix"><h2>All pathogenic-label records reviewed</h2><p>This includes unconfirmed calls and historical labels. It is not a list of diagnosed diseases.</p>')
    for a in d.get('clinical_articles',[]):
        parts.append('<details><summary>'+e(a['gene'])+' · '+e(a['variant'])+' · '+e(a['title'])+'</summary>')
        for para in a['sections']:parts.append('<h3>'+e(para['title'])+'</h3><p>'+e(para['text'])+'</p>')
        parts.append('<p class="sources">'+' · '.join('<a href="'+e(u)+'">'+('ClinVar allele record' if 'clinvar' in u else 'Research publication')+'</a>' for u in a['sources'] if u.startswith('https://'))+'</p></details>')
    parts.append('</section><section id="trait-appendix"><h2>All curated trait markers</h2>'+table(d['traits'],[('Trait','trait'),('Marker / genotype','rsid'),('Genotype','genotype'),('Reported zygosity','zygosity_label'),('Carrier status','carrier_label'),('Affected status','affected_label'),('Genotype confidence','confidence'),('Interpretation','sample_explanation'),('Limitations','limitations')])+'</section>')
    modelrows=[dict(m,coverage_percent=round(100*m['variant_coverage'],2),weight_percent=round(100*m['absolute_weight_coverage'],2),score=display(m.get('raw_score') if m.get('raw_score') is not None else m.get('partial_score'))) for m in d['models']]
    parts.append('<section id="model-appendix"><h2>All polygenic and broad trait models</h2><p>'+e(CALIBRATION_NOTE)+'</p><p>Models are ordered by review priority, highest first. The priority method is explained below. Raw weighted sums are on different scales and do not determine personal disease probabilities. Reference percentiles compare exactly the retained markers with unrelated 1000 Genomes individuals in the displayed group. The 95% sampling interval excludes genotype, model and ancestry uncertainty. The comparison panel also supplied the imputation reference; these are exploratory rankings without independent risk calibration.</p>'+table(modelrows,[('Review priority / 100','review_priority_score'),('Trait','trait'),('Model','pgs_id'),('Reason for priority','priority_reason'),('Status','status'),('Weighted sum','score'),('Markers retained %','coverage_percent'),('Weight retained %','weight_percent'),('Development population','development_ancestry'),('Reference group','reference_population'),('Reference N','reference_n'),('Reference percentile','reference_percentile'),('Percentile sampling interval, lower','reference_percentile_low'),('Percentile sampling interval, upper','reference_percentile_high'),('Reference z score','reference_z')],id='model-table')+'</section>')
    parts.append('<section><h2>Additional annotated variants</h2><p><a href="../../special_annotations/'+sample+'.special_annotations/'+sample+'.special_annotations.html">Mitochondrial variants, carried Neanderthal markers and somatic candidates: Excel, TSV and VCF</a></p></section>')
    parts.append('<section id="sources"><h2>Methods and sources</h2>')
    for title,method in [('Variant review priority',d['priority_methods']['variants']),('Polygenic review priority',d['priority_methods']['polygenic']),('Genotype support',d['score_method'])]:
        parts.append('<details><summary>'+e(title)+'</summary>')
        for key,value in method.items():
            if key!='version':parts.append('<p><b>'+e(key.replace('_',' ').capitalize())+':</b> '+e(value)+'</p>')
        parts.append('</details>')
    parts.append('<p>Complete methods, provenance and generation records are available in the methods-and-sources folder.</p>')
    for r in d['sources']:parts.append('<p class="sources"><a href="'+e(r['url'])+'">'+e(r['url'])+'</a></p>')
    parts.append('</section></main></div><footer>Local evidence-based reporting · '+e(sample)+' · '+e(d['generated_utc'])+'</footer>')
    parts.append("""<script>
function sortTable(id,i,ascending){const t=document.getElementById(id);if(!t)return;const b=t.tBodies[0];const numeric=t.tHead.rows[0].cells[i].dataset.kind==='number';const rows=[...b.rows];rows.sort((a,z)=>{let x=a.cells[i].dataset.value??a.cells[i].textContent,y=z.cells[i].dataset.value??z.cells[i].textContent;if(numeric){const missing=v=>v.trim()===''||v==='Not available'||v==='None'||v==='.';if(missing(x)||missing(y))return missing(x)-missing(y);x=Number(x);y=Number(y);return(ascending?1:-1)*(x-y)}return(ascending?1:-1)*x.localeCompare(y)});rows.forEach(r=>b.append(r))}
document.querySelectorAll('table[id] th').forEach(h=>{let asc=h.dataset.kind!=='number';h.addEventListener('click',()=>{sortTable(h.closest('table').id,h.cellIndex,asc);asc=!asc})});
document.querySelectorAll('[data-sort-table]').forEach(s=>s.addEventListener('change',()=>sortTable(s.dataset.sortTable,Number(s.value),s.value==='2')));
const filter=document.getElementById('variant-filter');filter.addEventListener('input',()=>{const q=filter.value.toLowerCase();document.querySelectorAll('#priority-table tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))});
window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>d.open=true));</script></body></html>""")
    (folder/(sample+'.interpretation.html')).write_text(''.join(parts))
    short=[header,'<main style="max-width:980px;margin:30px auto;padding:20px"><h2>Key interpretation</h2><p class="conclusion">'+e(d['note'])+'</p>']
    for s in d['sections']:
        if s['id'] in {'overview','clinical','cardiac','ancestry','maternal','assertions','polygenic'}:
            short.append('<section><h2>'+e(s['title'])+'</h2><p>'+e(s['conclusion'])+'</p>')
            if s['id']=='ancestry':short.append(bars(d))
            if s['id']=='maternal':short.append('<p>Estimated haplogroup: <b>'+e(d['ancestry'].get('mitochondrial',{}).get('haplogroup') or 'Unresolved')+'</b>. '+e(d['ancestry'].get('mitochondrial',{}).get('geographic_context',{}).get('text',''))+'</p>')
            if s['paragraphs']:short.append('<p>'+e(s['paragraphs'][0]['text'])+'</p>')
            short.append('<a href="'+sample+'.interpretation.html#'+s['id']+'">Full evidence and sources</a></section>')
    short.append('<a href="'+sample+'.interpretation.html">Open the complete interpretation report</a></main></body></html>')
    (folder/(sample+'.interpretation_summary.html')).write_text(''.join(short))
    lines=[sample+' — Integrated genome interpretation',d['note']]
    for s in d['sections']:
        lines+=['\n'+s['title'].upper(),s['conclusion']]+[p['text'] for p in s['paragraphs']]
    (folder/(sample+'.interpretation.txt')).write_text('\n\n'.join(lines)+'\n')


def render_pdf(d,folder):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,KeepTogether
    fonts=Path('/usr/share/fonts/truetype/dejavu');pdfmetrics.registerFont(TTFont('ReportSans',str(fonts/'DejaVuSans.ttf')));pdfmetrics.registerFont(TTFont('ReportSans-Bold',str(fonts/'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily('ReportSans',normal='ReportSans',bold='ReportSans-Bold',italic='ReportSans',boldItalic='ReportSans-Bold')
    styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName='ReportSans';style.textColor=colors.HexColor('#16312f')
    styles['BodyText'].fontSize=9;styles['BodyText'].leading=14;styles['BodyText'].spaceAfter=8
    styles['Title'].fontSize=27;styles['Title'].leading=34;styles['Heading1'].fontSize=16;styles['Heading1'].leading=21;styles['Heading2'].fontSize=12;styles['Heading2'].leading=16
    small=ParagraphStyle('Evidence',parent=styles['BodyText'],fontSize=7,leading=10)
    def para(x,style='BodyText'):return Paragraph(e(x).replace('\n','<br/>'),styles[style])
    def table_pdf(records,cols,widths):
        cells=[[Paragraph('<b>'+e(t)+'</b>',small) for t,k in cols]]
        cells += [[Paragraph(e(r.get(k)),small) for t,k in cols] for r in records]
        t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e6f0ea')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d5dfd9'))]));return t
    def footer(canvas,doc):
        canvas.setFont('ReportSans',7);canvas.setFillColor(colors.HexColor('#536762'));canvas.drawString(40,25,d['sample']+' · Research interpretation');canvas.drawRightString(A4[0]-40,25,str(doc.page))
    for is_short,name in [(True,'interpretation_summary'),(False,'interpretation')]:
        patient=d.get('patient',{})
        context='Sample '+str(patient.get('name',d['sample']))+'; age '+str(patient.get('age_display','Not available'))+'; sex '+str(patient.get('sex','unknown'))+' ('+str(patient.get('sex_source','not available'))+').'
        story=[para(d['sample'],'Title'),para(context),para('Genome results and interpretation','Heading1'),para('Generated '+d['generated_utc']),para(d['note']),Spacer(1,14)]
        for sec in d['sections']:
            if is_short and sec['id'] not in {'overview','clinical','cardiac','assertions','ancestry','maternal','polygenic'}:continue
            story += [para(sec['title'],'Heading1'),para(sec['conclusion'])]
            for p in sec['paragraphs'][:1 if is_short else 99]:story.append(para(p['text']))
            if sec['id']=='ancestry':
                comps=[{'component':r['component'],'value':f"{100*r['fraction']:.1f}%",'range':f"{100*r['lower']:.1f}–{100*r['upper']:.1f}%"} for r in d['ancestry'].get('admixture',{}).get('components',[])]
                story.append(table_pdf(comps,[('Reference component','component'),('Estimate','value'),('Chromosome jackknife range','range')],[230,90,190]))
                regional=d['ancestry'].get('regional_similarity',{})
                if regional.get('populations'):
                    story += [para('Detailed reference similarity','Heading2'),para(regional['scope'])]
                    comparisons=[dict(r,distance=f"{r['distance']:.4f}") for r in regional['populations'][:5]]
                    story.append(table_pdf(comparisons,[('Rank','rank'),('Reference population','population_name'),('Distance','distance'),('Reference N','reference_n')],[36,284,85,105]))
            if sec['id']=='maternal':story.append(para('Estimated maternal haplogroup: '+(d['ancestry'].get('mitochondrial',{}).get('haplogroup') or 'Unresolved')))
            story.append(Spacer(1,10))
        if not is_short:
            story += [PageBreak(),para('Variant review priorities','Heading1'),para('See the sortable Excel for all ClinVar-matched variants. Scores are support heuristics, not disease probabilities.')]
            ranked=[dict(r,label=r['gene']+'\n'+r['variant'],points=str(r['support_score'])+' / 100',read='DP '+str(r['read_DP'])+'; ALT '+str(r['read_ALT']),imputation=r['imputed_genotype']+'; GP '+str(r['imputed_GP'])) for r in d['priority_findings']]
            story.append(table_pdf(ranked,[('Gene / allele','label'),('Priority','review_priority_score'),('Support','points'),('Reads','read'),('Imputation','imputation'),('Zygosity / carrier / affected status','status_summary')],[117,48,45,60,100,140]))
            story += [PageBreak(),para('Clinical findings: evidence and literature','Heading1')]
            for a in d.get('clinical_articles',[]):
                story += [para(a['gene']+' · '+a['variant'],'Heading2'),para(a['title'])]
                for item in a['sections']:story += [para(item['title'],'Heading2'),para(item['text'])]
            story += [PageBreak(),para('All curated trait markers','Heading1')]
            for row in d['traits']:
                story += [para(row['trait']+' · '+row['rsid'],'Heading2'),para(row.get('status_summary','Status unavailable')),para(row['sample_explanation']),para(row['relevance']),para('Limitations: '+row['limitations'])]
            story += [PageBreak(),para('Polygenic and broad trait models','Heading1')]
            records=[dict(m,coverage=f"{100*m['variant_coverage']:.1f}%",weight=f"{100*m['absolute_weight_coverage']:.1f}%",score=display(m.get('raw_score') if m.get('raw_score') is not None else m.get('partial_score'))) for m in d['models']]
            groups=sorted({str(m.get('reference_population'))+' (N='+str(m.get('reference_n'))+')' for m in d['models'] if m.get('reference_n')})
            story.append(para(CALIBRATION_NOTE))
            story.append(para('Exploratory comparison groups: '+(', '.join(groups) or 'unavailable')+'. Percentiles compare the same retained score terms. They are not disease probabilities. This comparison reuses the imputation panel and is not independently calibrated for the individual. The HTML and Excel include reference sampling intervals; those intervals exclude genotype, model and ancestry uncertainty.'))
            story.append(table_pdf(records,[('Priority','review_priority_score'),('Trait','trait'),('ID','pgs_id'),('Markers','coverage'),('Weight','weight'),('Sum','score'),('Reference percentile','reference_percentile')],[50,140,65,48,48,66,93]))
            story += [PageBreak(),para('Scoring methods and sources','Heading1')]
            for k,v in d['score_method'].items():story.append(para(k+': '+str(v)))
            for method in d['priority_methods'].values():
                for key,value in method.items():story.append(para(key.replace('_',' ')+': '+str(value)))
            for source in d['sources']:story.append(Paragraph('<link href="'+e(source['url'])+'">'+e(source['url'])+'</link>',small))
        path=Path(folder)/(d['sample']+'.'+name+'.pdf')
        SimpleDocTemplate(str(path),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=40,bottomMargin=42,title=d['sample']+' genome interpretation',author='LPWGS Nextflow pipeline').build(story,onFirstPage=footer,onLaterPages=footer)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pdf',required=True);a=p.parse_args();path=Path(a.pdf);d=json.loads(path.read_text());render_pdf(d,path.parent)
