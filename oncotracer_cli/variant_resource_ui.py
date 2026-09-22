"""Focused resource discovery and a compact shared variant setup layout."""

PANEL = '''<div class="variant-detection">
<div class="variant-block-heading"><div><strong>Find installed resources</strong><p class="hint">Fill empty paths for the selected callers. Paths you entered are kept.</p></div><button id="variant-autodetect" type="button" class="primary">Autodetect resources</button></div>
<p id="variant-detection-status" class="hint" role="status" aria-live="polite">Check common installation folders on the computer running OncoTracer.</p>
<button id="variant-resource-review" type="button" hidden>Review resource details</button></div>'''

DIALOG = '''<dialog id="variant-resource-dialog" aria-labelledby="variant-resource-title">
<div class="variant-dialog-heading"><div><span class="eyebrow">Tools, models and annotation</span><h2 id="variant-resource-title">Detected resources</h2></div><button id="variant-resource-close" type="button" autofocus>Close</button></div>
<p id="variant-resource-dialog-status" class="hint" role="status" aria-live="polite"></p><div id="variant-detection-results" hidden></div></dialog>'''

STYLE = '''
.variant-form{margin:20px 0 30px;min-width:0}.variant-nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}.variant-nav button{border:0;font-size:12px;text-decoration:none;color:var(--accent);background:var(--wash);border-radius:5px;padding:7px 10px}.variant-section{border:1px solid var(--line);border-radius:10px;padding:22px;margin:16px 0;scroll-margin-top:20px;min-width:0}.variant-section-heading{display:flex;gap:12px;align-items:flex-start;margin-bottom:18px}.variant-section-heading h3{margin:0;font-size:19px}.variant-section-heading p{margin:3px 0 0}.variant-step{border:1px solid #b7d7d0;background:var(--wash);color:var(--accent);border-radius:7px;padding:2px 9px;font-size:14px;font-weight:700}.variant-section h4{font-size:15px;margin:16px 0 10px}.variant-specimen{display:grid;grid-template-columns:1fr 1fr;gap:12px}.variant-specimen .platform{min-height:0;padding:12px 16px}.variant-specimen .platform[aria-pressed=true]{padding:11px 15px}.variant-specimen .platform strong{font-size:17px;margin:0}.variant-specimen .platform span{font-size:12px;margin-top:3px}.variant-caller-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.variant-caller-list label{display:flex;align-items:center;gap:10px;margin:0;padding:12px;border:1px solid var(--line);border-radius:7px;background:white;font-size:13px;line-height:1.5;cursor:pointer}.variant-caller-list label:has(input:checked){border-color:var(--accent);background:var(--wash)}.variant-caller-list input{flex:none;margin:0}.variant-explainer{margin-bottom:0}.variant-path-details{border-top:1px solid var(--line);padding-top:12px;margin:14px 0 0}.variant-path-details>summary{font-weight:600;font-size:13px;padding:3px 0}.variant-path-details[open]>summary{margin-bottom:14px}.variant-path{margin:14px 0;min-width:0}.variant-path-row{display:flex;gap:10px;align-items:center;min-width:0}.variant-path-row>input{flex:1;min-width:0;font-size:13px}.variant-path-actions{display:flex;gap:6px;flex:none}.variant-path-actions button,.variant-tool-block button,.variant-detection button{font-size:13px;padding:8px 12px}.variant-path-summary{font-size:12px;font-weight:400;color:var(--muted);margin-left:6px}.variant-path-status:empty{display:none}.variant-path-status{margin:6px 0 0}.variant-path-status[data-state=missing],.variant-required-summary[data-missing=true]{color:#886219}.variant-path-status[data-state=found]{color:var(--accent)}.variant-required-summary{font-size:13px;margin:8px 0}#variant-annotation-section .variant-block-heading{margin-top:14px}.variant-tool-block{margin:18px 0;padding:0 0 10px}.variant-block-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.variant-block-heading h4{margin:0}.variant-block-heading p{margin:3px 0}.variant-detection{padding:16px;border-radius:8px;background:var(--wash);margin:12px 0}.variant-detection>p{margin:10px 0 0}.variant-detection #variant-resource-review{margin-top:10px;background:transparent}.variant-short{max-width:210px;margin-top:18px}#variant-resource-dialog{width:min(860px,calc(100vw - 28px));max-height:85vh;padding:24px;overflow:auto}.variant-dialog-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding-bottom:14px}.variant-dialog-heading h2{font-size:22px;margin-top:4px}.variant-dialog-heading>button{font-size:13px}.resource-list{padding:0;list-style:none}.resource-list li{padding:12px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.resource-list strong{margin-right:10px}.resource-list .resource-detail{margin:5px 0;font-size:13px;color:var(--muted)}.resource-list [data-state=missing],.resource-list [data-state=candidate]{background:#fff1d7;color:#805817}.resource-install{margin:14px 0;border:1px solid var(--line);padding:12px;border-radius:8px}.resource-install summary{font-weight:600;cursor:pointer}.resource-install pre{white-space:pre;overflow:auto;max-height:270px;padding:14px;background:#162d36;color:#edf5f3;border-radius:7px;font:12px/1.6 ui-monospace,monospace}.resource-install a{color:var(--accent);margin-right:14px;font-size:13px}.resource-install .actions{gap:12px;margin:10px 0}.resource-install p{font-size:13px}.resource-path{display:block;font:12px/1.5 ui-monospace,monospace;overflow-wrap:anywhere}.resource-candidate{padding:12px;margin:8px 0;border:1px solid var(--line);border-radius:7px}.resource-candidate button{font-size:12px;padding:6px 12px}.resource-candidate p{font-size:13px;margin:5px 0 8px}.resource-choice-label{font-weight:650;font-size:14px}.resource-help-actions{margin:8px 0}.resource-help-actions button{font-size:12px;padding:6px 10px}body:has(#variant-resource-dialog[open]){overflow:hidden}
@media(max-width:720px){.variant-section{padding:16px}.variant-path-row{flex-wrap:wrap}.variant-path-row>input{flex-basis:100%}.variant-path-actions{width:100%}.variant-path-actions button{flex:1}.variant-nav button{border:0;font-size:11px}.variant-caller-list{grid-template-columns:1fr}.variant-specimen{gap:8px}.variant-specimen .platform{padding:10px}.variant-specimen .platform[aria-pressed=true]{padding:9px}#variant-resource-dialog{padding:16px}.variant-dialog-heading h2{font-size:19px}.variant-section-heading h3{font-size:17px}.variant-block-heading>button{max-width:100%;white-space:normal}}
'''

SCRIPT = r'''
let variantDetectionApplying=false,variantResourceLastResult=null,variantResourceLastScope='all';
const variantPathKeys=['variant_tool_prefix','variant_clair3_model','variant_clairsto_sif','variant_ffperase_root','variant_ffperase_models','variant_ffperase_prefix','variant_ffperase_sif','variant_annovar_dir','variant_annovar_db'];
function variantScopeFields(scope){
  if(scope==='all')return variantPathKeys;
  if(scope==='ffperase')return variantPathKeys.filter(key=>key.startsWith('variant_ffperase'));
  if(scope==='annotation')return ['variant_annovar_dir','variant_annovar_db'];
  return [scope];
}
function variantFieldLabel(key){return document.querySelector('label[for="'+key+'"]')?.textContent||key.replace(/^variant_/,'').replaceAll('_',' ');}
function syncVariantLayout(){
  const docker=document.getElementById('backend')?.value==='docker',runtime=document.getElementById('variant-ffperase-runtime');
  if(!runtime)return;
  if(!runtime.dataset.chosen)runtime.value=document.getElementById('variant_ffperase_sif').value.trim()?'sif':'native';
  show('variant-tool-prefix-fields',!docker);show('variant-clairsto-sif-fields',!docker);show('variant-ffperase-runtime-fields',!docker);
  show('variant-ffperase-prefix-fields',!docker&&runtime.value==='native');show('variant-ffperase-sif-fields',!docker&&runtime.value==='sif');
  const ffpePaths=document.getElementById('variant-ffperase-resource-fields')||document.getElementById('ffperase-resource-fields');
  ffpePaths.hidden=document.getElementById('variant_ffperase').value!=='required';
  for(const summary of document.querySelectorAll('[data-path-summary]')){
    const keys=summary.dataset.pathSummary.split(','),count=keys.filter(key=>document.getElementById(key)?.value.trim()).length;
    const required=summary.dataset.requiredPath==='true';
    summary.textContent=required?(count===keys.length?'Paths selected · check resource details before running':(keys.length-count)+' required path'+(keys.length-count===1?'':'s')+' to select · use Autodetect'):(count?count+' path'+(count===1?'':'s')+' selected':'Automatic search · no manual override');
    summary.dataset.missing=String(required&&count<keys.length);
  }
}
function filterVariantRuntime(data,blank=false){
  const other=document.getElementById('variant-ffperase-runtime').value==='sif'?'variant_ffperase_prefix':'variant_ffperase_sif';
  if(blank)data[other]='';else delete data[other];
  return data;
}
function resetVariantResourceResults(){
  if(variantDetectionApplying)return;
  const result=document.getElementById('variant-detection-results');
  if(result&&!result.hidden){result.hidden=true;document.getElementById('variant-detection-status').textContent='Settings changed. Autodetect again to refresh the resource list.';}
  const review=document.getElementById('variant-resource-review');if(review)review.hidden=true;
  for(const status of document.querySelectorAll('.variant-path-status')){status.textContent='';delete status.dataset.state;}
}
function variantResourceValues(){
  const keys=[...variantPathKeys,'variant_clairsto_platform','variant_ffperase','variant_varlociraptor','variant_annovar'];
  const values=Object.fromEntries(keys.map(key=>[key,document.getElementById(key).value.trim()]));
  // Keep inactive alternative paths in the form, but don't use them for this check.
  const other=document.getElementById('variant-ffperase-runtime').value==='sif'?'variant_ffperase_prefix':'variant_ffperase_sif';values[other]='';
  return values;
}
function variantResourceField(resource){
  if(resource.id==='ffperase_runtime')return 'variant_ffperase_prefix';
  if(resource.field==='docker_image'||resource.id==='docker_runtime')return 'variant_tool_prefix';
  if(resource.field)return resource.field;
  const aliases={variant_tools:'variant_tool_prefix',samtools:'variant_tool_prefix',bcftools:'variant_tool_prefix',gatk:'variant_tool_prefix',mutect2:'variant_tool_prefix',freebayes:'variant_tool_prefix',varlociraptor:'variant_tool_prefix',clair3:'variant_tool_prefix',clairs_to:'variant_tool_prefix',docker_image:'variant_tool_prefix',clair3_model:'variant_clair3_model',clairsto_model:'variant_clairsto_platform',clairsto_sif:'variant_clairsto_sif',ffperase_root:'variant_ffperase_root',ffperase_source:'variant_ffperase_root',ffperase_models:'variant_ffperase_models',ffperase_prefix:'variant_ffperase_prefix',ffperase_runtime:'variant_ffperase_prefix',ffperase_sif:'variant_ffperase_sif',annovar_dir:'variant_annovar_dir',annovar_db:'variant_annovar_db'};
  return aliases[resource.id]||'';
}
function variantGuideMatches(guide,scope){
  if(scope==='all')return true;
  const fields=variantScopeFields(scope);
  const map={variant_tools:['variant_tool_prefix'],docker_image:['variant_tool_prefix','variant_ffperase_prefix','variant_ffperase_sif','variant_clairsto_sif'],clair3_caller:['variant_tool_prefix'],clair3_model:['variant_clair3_model'],clairsto_caller:['variant_tool_prefix','variant_clairsto_sif'],clairsto_model:['variant_clairsto_platform'],ffperase_source:['variant_ffperase_root'],ffperase_models:['variant_ffperase_models'],ffperase_runtime:['variant_ffperase_prefix','variant_ffperase_sif'],annovar:['variant_annovar_dir','variant_annovar_db'],container_runtime:['variant_ffperase_sif','variant_clairsto_sif']};
  return (map[guide.id]||[]).some(field=>fields.includes(field));
}
function variantApplyPath(key,value,replace=false){
  const field=document.getElementById(key);if(!variantPathKeys.includes(key)||!field||typeof value!=='string'||!value||(!replace&&field.value.trim()))return false;
  const runtime=document.getElementById('variant-ffperase-runtime');
  if(!replace&&runtime.dataset.chosen==='user'&&((key==='variant_ffperase_sif'&&runtime.value!=='sif')||(key==='variant_ffperase_prefix'&&runtime.value!=='native')))return false;
  variantDetectionApplying=true;
  try{
    if(key==='variant_ffperase_sif'||key==='variant_ffperase_prefix'){const choice=document.getElementById('variant-ffperase-runtime');choice.value=key.endsWith('_sif')?'sif':'native';choice.dataset.chosen='selected';}
    field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));invalidate();syncVariantLayout();
  }finally{variantDetectionApplying=false;}
  return true;
}
function renderVariantResources(result,scope='all'){
  const container=document.getElementById('variant-detection-results');container.replaceChildren();
  const fields=variantScopeFields(scope),matches=resource=>scope==='all'||fields.includes(variantResourceField(resource))||(resource.id==='annovar'&&fields.some(field=>field.startsWith('variant_annovar')));
  const resources=(result.resources||[]).filter(resource=>(resource.status!=='not_needed'||scope!=='all')&&matches(resource));
  const list=node('ul',undefined,'resource-list');
  const labels={found:'Found',missing:'Not found',candidate:'Review candidate',unverified:'Not verified',not_needed:'Not needed'};
  for(const resource of resources){
    const item=node('li'),badge=node('span',labels[resource.status]||resource.status,'badge');badge.dataset.state=resource.status;item.append(node('strong',resource.label),badge);
    if(resource.path)item.append(node('code',resource.path,'resource-path'));
    if(resource.detail)item.append(node('p',resource.detail,'resource-detail'));
    list.append(item);
    const key=variantResourceField(resource),status=document.getElementById(key+'-status');
    if(status){status.textContent=(labels[resource.status]||resource.status)+(resource.detail?' · '+resource.detail:'');status.dataset.state=resource.status;}
  }
  if(resources.length)container.append(list);
  const candidates=(result.candidates||[]).filter(candidate=>fields.includes(candidate.field));
  if(candidates.length){container.append(node('h3','Choose an existing path'));container.append(node('p','Review the matching folders below. Use path selects that candidate for its field. Model compatibility still needs confirmation.','hint'));}
  for(const candidate of candidates){
    const box=node('div',undefined,'resource-candidate'),label=node('div',variantFieldLabel(candidate.field),'resource-choice-label');box.append(label,node('code',candidate.path,'resource-path'));
    if(candidate.detail)box.append(node('p',candidate.detail,'hint'));
    const button=node('button','Use path');button.type='button';button.dataset.candidateField=candidate.field;button.dataset.candidatePath=candidate.path;
    button.onclick=()=>{if(variantApplyPath(candidate.field,candidate.path,true)){document.getElementById('variant-resource-dialog-status').textContent=variantFieldLabel(candidate.field)+': selected '+candidate.path;for(const item of container.querySelectorAll('[data-candidate-field]')){const selected=document.getElementById(item.dataset.candidateField)?.value===item.dataset.candidatePath;item.textContent=selected?'Selected':'Use path';item.disabled=selected;}document.getElementById('variant-detection-status').textContent='Selected a resource path. Review and save the updated settings.';}};
    const selected=document.getElementById(candidate.field)?.value===candidate.path;button.disabled=selected;button.textContent=selected?'Selected':'Use path';box.append(button);container.append(box);
  }
  const guides=(result.install_guides||[]).filter(guide=>variantGuideMatches(guide,scope));
  if(guides.length)container.append(node('h3','Install missing resources'));
  for(const guide of guides){
    const detail=node('details',undefined,'resource-install');detail.open=scope!=='all';detail.append(node('summary',guide.title));
    if(guide.reason)detail.append(node('p',guide.reason));
    const command=Array.isArray(guide.commands)?guide.commands.join('\n'):String(guide.commands||''),code=node('code',command),pre=node('pre');pre.append(code);pre.tabIndex=0;pre.setAttribute('aria-label',guide.title+' commands');detail.append(pre);
    const actions=node('div',undefined,'actions'),copy=node('button','Copy commands'),status=node('span','','hint');copy.type='button';status.setAttribute('role','status');
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(command);status.textContent='Copied';}catch(error){const range=document.createRange();range.selectNodeContents(code);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);status.textContent='Commands selected. Press Ctrl+C or Command+C to copy.';}};
    actions.append(copy,status);detail.append(actions);
    for(const link of guide.links||[]){try{const url=new URL(link.url);if(!['http:','https:'].includes(url.protocol))continue;const anchor=node('a',link.label||'Installation documentation');anchor.href=url.href;anchor.target='_blank';anchor.rel='noopener noreferrer';detail.append(anchor);}catch(error){}}
    container.append(detail);
  }
  if(guides.length)container.append(node('p','Run the commands you need in a terminal, then Autodetect again. This check never installs software.','hint'));
  if(!resources.length&&!candidates.length&&!guides.length)container.append(node('p','No matching resource was returned for this field. Use Browse to select an existing path.','hint'));
  const audit=node('details');audit.append(node('summary','Search details and limitations'));
  for(const note of result.notes||[])audit.append(node('p',note,'hint'));
  if(result.searched?.length)audit.append(node('pre',result.searched.join('\n')));container.append(audit);
  container.hidden=false;
}
function openVariantResourceDialog(){
  const dialog=document.getElementById('variant-resource-dialog');if(!dialog.open)dialog.showModal();dialog.scrollTop=0;
}
async function detectVariantResources(payload,scope='all'){
  const status=document.getElementById('variant-detection-status');document.getElementById('variant-resource-review').hidden=true;
  status.textContent='Checking '+(scope==='all'?'selected resources':scope==='annotation'?'ANNOVAR':scope==='ffperase'?'FFPERASE':variantFieldLabel(scope))+'…';
  try{
    const result=await api('/api/variant-resources',payload),fields=variantScopeFields(scope);let filled=0;
    for(const [key,value]of Object.entries(result.fields||{}))if(fields.includes(key)&&variantApplyPath(key,value))filled++;
    variantResourceLastResult=result;variantResourceLastScope=scope;renderVariantResources(result,scope);
    const missing=(result.resources||[]).filter(resource=>resource.status==='missing'&&(scope==='all'||fields.includes(variantResourceField(resource)))).length;
    status.textContent='Check complete · '+filled+' empty path'+(filled===1?'':'s')+' filled'+(missing?' · missing resources have setup help':'')+'.';
    document.getElementById('variant-resource-title').textContent=scope==='all'?'Detected resources':scope==='annotation'?'ANNOVAR resources':scope==='ffperase'?'FFPERASE resources':variantFieldLabel(scope);
    document.getElementById('variant-resource-dialog-status').textContent='Only empty fields were filled. Choose a candidate explicitly to change an entered path.';
    document.getElementById('variant-resource-review').hidden=false;openVariantResourceDialog();
  }catch(error){status.textContent='Resource check could not finish. Your entered paths were kept.';throw error;}
}
for(const field of document.querySelectorAll('[id^="variant_"],#backend,#docker_image'))for(const event of ['input','change'])field.addEventListener(event,()=>{resetVariantResourceResults();syncVariantLayout();});
document.getElementById('variant-ffperase-runtime').addEventListener('input',()=>{document.getElementById('variant-ffperase-runtime').dataset.chosen='user';},true);
document.getElementById('variant-ffperase-runtime').onchange=()=>{document.getElementById('variant-ffperase-runtime').dataset.chosen='user';resetVariantResourceResults();syncVariantLayout();invalidate();};
for(const button of document.querySelectorAll('[data-variant-section]'))button.onclick=()=>{const section=document.getElementById(button.dataset.variantSection),heading=section.querySelector('h3');section.scrollIntoView({behavior:'smooth',block:'start'});heading.tabIndex=-1;heading.focus({preventScroll:true});};
document.getElementById('variant-resource-close').onclick=()=>document.getElementById('variant-resource-dialog').close();
document.getElementById('variant-resource-review').onclick=()=>{if(variantResourceLastResult){renderVariantResources(variantResourceLastResult,variantResourceLastScope);openVariantResourceDialog();}};
'''


def add_resource_ui(page: str, *, existing_bam: bool = False) -> str:
    if page.count('<!-- VARIANT_RESOURCE_PANEL -->') != 1:
        raise ValueError('Variant resource panel anchor changed')
    page = page.replace('<!-- VARIANT_RESOURCE_PANEL -->', PANEL, 1)
    page = page.replace('<dialog id="browser">', DIALOG + '\n<dialog id="browser">', 1)
    if not existing_bam:
        page = page.replace('</style>', STYLE + '</style>', 1)
    callback = r'''
function runVariantDetection(button,scope){return busy(async()=>{
  if(!loaded)throw Error('Load aligned samples first.');
  await detectVariantResources({mode,backend:'host',callers:callers(),specimen_type:specimen||'',values:{...variantResourceValues(),variant_reference_build:loaded.config.variant_reference_build||'hg38'}},scope);
});}
''' if existing_bam else r'''
function runVariantDetection(button,scope){return busy(button,async()=>{
  if(!mode)throw Error('Choose a sequencing platform first.');
  await detectVariantResources({mode,backend:$('backend').value,docker_image:$('docker_image').value.trim(),callers:selectedVariantCallers(),specimen_type:variantSpecimen||'',values:variantResourceValues()},scope);
});}
'''
    callback += r'''
$('variant-autodetect').onclick=()=>runVariantDetection($('variant-autodetect'),'all');
for(const button of document.querySelectorAll('[data-variant-detect]'))button.onclick=()=>runVariantDetection(button,button.dataset.variantDetect);
syncVariantLayout();
'''
    # Functions above are hoisted; state used by early page initialization must
    # exist before the page's async bootstrap and default-setting calls.
    page = page.replace("'use strict';", "'use strict';\nlet variantDetectionApplying=false,variantResourceLastResult=null,variantResourceLastScope='all';", 1)
    script = SCRIPT.replace("let variantDetectionApplying=false,variantResourceLastResult=null,variantResourceLastScope='all';", '', 1)
    return page.replace('</script>', script + callback + '\n</script>', 1)
