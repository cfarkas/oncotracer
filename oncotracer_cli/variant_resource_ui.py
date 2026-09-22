"""Shared resource discovery controls for the two local variant setup forms."""

PANEL = '''<section class="variant-detection" aria-labelledby="variant-detection-title">
<div class="actions"><h3 id="variant-detection-title">Tools and model paths</h3><button id="variant-autodetect" type="button">Autodetect resources</button></div>
<p class="hint">Check common installation folders on the computer running OncoTracer. Empty paths are filled when found; paths you entered are kept. Missing resources include installation commands.</p>
<p id="variant-detection-status" class="hint" role="status" aria-live="polite"></p>
<div id="variant-detection-results" hidden></div></section>'''

STYLE = '''
.variant-detection{margin:22px 0;padding:18px;border:1px solid var(--line);border-radius:9px;background:#f8fbfa}.variant-detection h3{margin:0 auto 0 0}.variant-detection .actions{margin-top:0}.resource-list{padding:0;list-style:none}.resource-list li{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.resource-list strong{margin-right:10px}.resource-list .resource-detail{margin:5px 0;font-size:13px;color:var(--muted)}.resource-install{margin:14px 0}.resource-install summary{font-weight:600;cursor:pointer}.resource-install pre{white-space:pre;overflow:auto;max-height:300px;padding:14px;background:#162d36;color:#edf5f3;border-radius:7px;font:12px/1.6 ui-monospace,monospace}.resource-install a{color:var(--accent);margin-right:14px}.resource-install .actions{gap:12px}.resource-install p{font-size:13px}.resource-path{display:block;font:12px/1.5 ui-monospace,monospace;overflow-wrap:anywhere}
'''

SCRIPT = r'''
function resetVariantResourceResults(){
  const result=document.getElementById('variant-detection-results');
  if(result&&!result.hidden){result.hidden=true;document.getElementById('variant-detection-status').textContent='Settings changed. Autodetect again to refresh the resource list.';}
}
function variantResourceValues(){
  const keys=['variant_tool_prefix','variant_clair3_model','variant_clairsto_sif','variant_clairsto_platform','variant_ffperase_root','variant_ffperase_models','variant_ffperase_prefix','variant_ffperase_sif','variant_ffperase','variant_varlociraptor','variant_annovar','variant_annovar_dir','variant_annovar_db'];
  return Object.fromEntries(keys.filter(key=>document.getElementById(key)).map(key=>[key,document.getElementById(key).value.trim()]));
}
function renderVariantResources(result){
  const container=document.getElementById('variant-detection-results');container.replaceChildren();
  const list=node('ul',undefined,'resource-list');
  const labels={found:'Found',missing:'Not found',candidate:'Candidate · review compatibility',unverified:'Check at run time',not_needed:'Not needed'};
  for(const resource of result.resources||[]){
    const item=node('li');item.append(node('strong',resource.label),node('span',labels[resource.status]||resource.status,'badge'));
    if(resource.path)item.append(node('code',resource.path,'resource-path'));
    if(resource.detail)item.append(node('p',resource.detail,'resource-detail'));
    list.append(item);
  }
  container.append(list);
  for(const note of result.notes||[])container.append(node('p',note,'hint'));
  for(const guide of result.install_guides||[]){
    const detail=node('details',undefined,'resource-install');detail.open=true;detail.append(node('summary',guide.title));
    if(guide.reason)detail.append(node('p',guide.reason));
    const command=Array.isArray(guide.commands)?guide.commands.join('\n'):String(guide.commands||'');
    const code=node('code',command),pre=node('pre');pre.append(code);pre.tabIndex=0;pre.setAttribute('aria-label',guide.title+' commands');detail.append(pre);
    const actions=node('div',undefined,'actions'),copy=node('button','Copy commands'),status=node('span','','hint');copy.type='button';status.setAttribute('role','status');
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(command);status.textContent='Copied';}catch(error){const range=document.createRange();range.selectNodeContents(code);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);status.textContent='Commands selected. Press Ctrl+C or Command+C to copy.';}};
    actions.append(copy,status);detail.append(actions);
    for(const link of guide.links||[]){try{const url=new URL(link.url);if(!['http:','https:'].includes(url.protocol))continue;const anchor=node('a',link.label||'Installation documentation');anchor.href=url.href;anchor.target='_blank';anchor.rel='noopener noreferrer';detail.append(anchor);}catch(error){/* Ignore malformed links. */}}
    container.append(detail);
  }
  if(result.install_guides?.length)container.append(node('p','Run the commands you need in a terminal, then select Autodetect resources again. Nothing is installed by this button.','hint'));
  if(result.searched?.length){const details=node('details');details.append(node('summary','Folders checked'),node('pre',result.searched.join('\n')));container.append(details);}
  container.hidden=false;
}
async function detectVariantResources(payload){
  const status=document.getElementById('variant-detection-status');
  document.getElementById('variant-detection-results').hidden=true;status.textContent='Checking tools and model folders…';
  try{
    const result=await api('/api/variant-resources',payload);
    // A local path is a suggestion, never permission to replace an entered path.
    const permitted=new Set(['variant_tool_prefix','variant_clair3_model','variant_clairsto_sif','variant_ffperase_root','variant_ffperase_models','variant_ffperase_prefix','variant_ffperase_sif','variant_annovar_dir','variant_annovar_db']);
    let filled=0;
    for(const [key,value]of Object.entries(result.fields||{})){
      const field=document.getElementById(key);
      if(!permitted.has(key)||!field||field.value.trim()||typeof value!=='string'||!value)continue;
      field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));filled++;
    }
    if(filled)invalidate();
    renderVariantResources(result);status.textContent='Resource check finished. '+filled+' empty path'+(filled===1?'':'s')+' filled. Review candidates and installation help below.';
  }catch(error){status.textContent='Resource check could not finish. Your entered paths were kept.';throw error;}
}
for(const field of document.querySelectorAll('[id^="variant_"],#backend,#docker_image'))for(const event of ['input','change'])field.addEventListener(event,resetVariantResourceResults);
'''


def add_resource_ui(page: str, *, existing_bam: bool = False) -> str:
    anchor = '<h3>Variant callers</h3>' if existing_bam else '<div id="variant-fields" hidden>'
    if page.count(anchor) != 1:
        raise ValueError('Variant resource panel anchor changed')
    page = page.replace(anchor, PANEL + anchor if existing_bam else anchor + PANEL, 1)
    if not existing_bam:
        page = page.replace('</style>', STYLE + '</style>', 1)
    callback = r'''
$('variant-autodetect').onclick=()=>busy(async()=>{
  if(!loaded)throw Error('Load aligned samples first.');
  await detectVariantResources({mode,backend:'host',callers:callers(),specimen_type:specimen||'',values:{...variantResourceValues(),variant_reference_build:loaded.config.variant_reference_build||'hg38'}});
});
''' if existing_bam else r'''
$('variant-autodetect').onclick=()=>busy($('variant-autodetect'),async()=>{
  if(!mode)throw Error('Choose a sequencing platform first.');
  await detectVariantResources({mode,backend:$('backend').value,docker_image:$('docker_image').value.trim(),callers:selectedVariantCallers(),specimen_type:variantSpecimen||'',values:variantResourceValues()});
});
'''
    return page.replace('</script>', SCRIPT + callback + '\n</script>', 1)
