"""Standalone, searchable scientific report components (no remote scripts)."""
import html,json
from pathlib import Path

def esc(value):return html.escape(str(value if value is not None else 'Not available'),quote=True)
CSS='''*{box-sizing:border-box}body{margin:0;background:#f3f6f4;color:#183b36;font:16px/1.65 system-ui,sans-serif}header{background:#123f3c;color:white;padding:36px max(24px,calc((100vw - 1280px)/2))}header a{color:#b5ebdf}header p{max-width:1000px}h1{font-size:34px;line-height:1.2}h2{line-height:1.3}main{max-width:1330px;margin:auto;padding:26px}section,.card{padding:25px;background:white;border:1px solid #d7e3dc;border-radius:14px;margin-bottom:22px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px}.card strong{display:block;font-size:28px}.muted{color:#536d66;font-size:14px}a{color:#006e68}.note{background:#e9f3ed;border-left:4px solid #368574;padding:16px}.scroll{overflow:auto;max-width:100%}table{border-collapse:collapse;width:max-content;min-width:100%;font-size:14px}td,th{padding:12px;vertical-align:top;text-align:left;border-bottom:1px solid #dce6df;min-width:140px;max-width:360px;white-space:normal;word-break:normal;overflow-wrap:normal}th{background:#eaf2ed;cursor:pointer}td.numeric{font-variant-numeric:tabular-nums;white-space:nowrap}input,select,button{font:inherit;padding:9px 12px;border:1px solid #b9cec3;border-radius:7px;background:white;margin:4px}input[type=search]{width:min(100%,520px)}button{cursor:pointer}button:disabled{opacity:.4}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}svg{max-width:100%;height:auto}svg text{font-family:system-ui,sans-serif;fill:#244c42}.eyebrow{text-transform:uppercase;letter-spacing:.13em;font-size:12px}details{padding:10px 0}summary{cursor:pointer;font-weight:600}@media(max-width:700px){main{padding:14px}section{padding:18px}h1{font-size:27px}}@media print{.toolbar,.pager,header a{display:none}body{background:white}header{background:white;color:#183b36;padding:16px}main{padding:0}section{break-inside:auto;border:0}tr{break-inside:avoid}.scroll{overflow:visible}table{width:100%;font-size:8pt}th,td{min-width:0;max-width:none;padding:5px}}'''
JS=r'''
document.querySelectorAll('.data-table').forEach(w=>{
 const data=JSON.parse(w.querySelector('script[type="application/json"]').textContent), cols=data.columns;
 let page=0, sortKey=null, ascending=true, records=data.rows;
 const q=w.querySelector('input'), only=w.querySelector('select'), body=w.querySelector('tbody'), count=w.querySelector('.count'), prev=w.querySelector('.prev'),next=w.querySelector('.next');
 function value(r,k){return r[k]===null||r[k]===undefined?'Not available':String(r[k]);}
 function update(all=false){
  const query=q.value.toLowerCase();records=data.rows.filter(r=>(!query||Object.values(r).some(v=>String(v).toLowerCase().includes(query)))&&(!only||only.value!=='clinvar'||(r.clinvar_allele_id&&r.clinvar_allele_id!=='.')));
  if(sortKey)records.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];if(x===null||x===undefined||x==='.')return 1;if(y===null||y===undefined||y==='.')return -1;return (ascending?1:-1)*(typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true}));});
  const pages=Math.max(1,Math.ceil(records.length/50));page=Math.min(page,pages-1);body.replaceChildren();
  (all?records:records.slice(page*50,page*50+50)).forEach(r=>{const tr=document.createElement('tr');cols.forEach(c=>{const td=document.createElement('td');let v=value(r,c[1]);if(c[1]==='clinvar_url'&&v.startsWith('https://www.ncbi.nlm.nih.gov/clinvar/')){const a=document.createElement('a');a.href=v;a.textContent='Open ClinVar';td.append(a);}else td.textContent=v;if(typeof r[c[1]]==='number')td.className='numeric';tr.append(td)});body.append(tr)});
  count.textContent=records.length.toLocaleString()+' records · page '+(page+1)+' of '+pages;prev.disabled=page===0;next.disabled=page===pages-1;
 }
 q.addEventListener('input',()=>{page=0;update()});if(only)only.addEventListener('change',()=>{page=0;update()});prev.onclick=()=>{page--;update()};next.onclick=()=>{page++;update()};w.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{ascending=sortKey===cols[i][1]?!ascending:true;sortKey=cols[i][1];update()});window.addEventListener('beforeprint',()=>update(true));window.addEventListener('afterprint',()=>update());update();
});'''

def table(rows,columns,clinvar_filter=False):
    payload=json.dumps({'rows':rows,'columns':columns},ensure_ascii=True,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    headers=''.join('<th scope="col" tabindex="0">'+esc(c[0])+'</th>' for c in columns)
    only='<select aria-label="ClinVar filter"><option value="all">All variants</option><option value="clinvar">ClinVar matches only</option></select>' if clinvar_filter else ''
    return '<div class="data-table"><div class="toolbar"><input type="search" aria-label="Search table" placeholder="Search genes, variants, conditions or effects">'+only+'</div><div class="scroll" tabindex="0"><table><thead><tr>'+headers+'</tr></thead><tbody></tbody></table></div><div class="toolbar pager"><button class="prev">Previous</button><span class="count"></span><button class="next">Next</button></div><noscript>JavaScript displays the searchable table. Complete data are available in the linked TSV or Excel.</noscript><script type="application/json">'+payload+'</script></div>'

def section(title,body,id=''):return '<section'+(' id="'+esc(id)+'"' if id else '')+'><h2>'+esc(title)+'</h2>'+body+'</section>'
def cards(values):return '<div class="cards">'+''.join('<div class="card"><strong>'+esc(n)+'</strong>'+esc(label)+'</div>' for n,label in values)+'</div>'
def write(path,title,introduction,body,links=()):
    nav=' · '.join('<a href="'+esc(url)+'">'+esc(label)+'</a>' for label,url in links)
    Path(path).write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+CSS+'</style></head><body><header><div class="eyebrow">Genome evidence · GRCh38</div><h1>'+esc(title)+'</h1><p>'+esc(introduction)+'</p><nav>'+nav+'</nav></header><main>'+body+'</main><script>'+JS+'</script></body></html>')
