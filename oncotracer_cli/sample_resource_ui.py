"""Offer sample jobs × cores per sample for both new and continued analyses."""


def _controls(prefix, *, resume=False):
    cpu = (f'<label for="{prefix}-threads">Total CPU thread budget (shared by sample jobs)</label>'
           f'<input id="{prefix}-threads" type="number" min="1" step="1">') if resume else ''
    return f'''<section id="{prefix}-resources" {'hidden' if resume else ''}>
<h3>Choose parallel sample jobs</h3>
<p class="hint">Each job processes one sample. Choose the number of samples running at once and see the cores and RAM allocated to each. Completed outputs are validated before reuse.</p>
{cpu}<div class="grid"><div><label for="{prefix}-memory">Total RAM budget for all sample jobs</label><input id="{prefix}-memory" value="auto" placeholder="auto or 64GB"></div>
<div><label for="{prefix}-parallel">Parallel samples × cores per sample</label><select id="{prefix}-parallel"><option value="auto">Automatic · check available resources at Run</option></select></div></div>
<p id="{prefix}-resource-note" class="hint" role="status">Detect resources to compare sample-job combinations. Cores here mean CPU threads available to this process; other analyses share this server.</p>
<button id="{prefix}-detect-resources" type="button">Detect sample-job options</button>
{('<button id="resume-apply-resources" type="button">Apply resources and check</button><p class="hint">Run analysis also applies and checks these choices before starting.</p>') if resume else ''}
</section>'''


def add_sample_resource_ui(page):
    page = page.replace('<label class="check" id="reports-field">', _controls('sample') + '<label class="check" id="reports-field">', 1)
    # Keep continuation choices next to Run, including projects found via New analysis.
    page = page.replace('<div class="actions"><button id="run"', _controls('resume', resume=True) + '<div class="actions"><button id="run"', 1)
    page = page.replace('</style></head>', '#variant-parallel-controls{display:none!important}</style></head>', 1)
    page = page.replace("$('run').disabled=!prepared?.valid;", "$('run').disabled=!prepared?.valid||Boolean(prepared?.resource_choices&&!resumeResourcesReady);")
    page = page.replace("function invalidate(){", "function invalidate(){resourceRequestVersion.sample++;resourceRequestVersion.resume++;show('resume-resources',false);")
    script = r'''
const resourceRequestVersion={sample:0,resume:0};
let resumeResourcesReady=false;
function resourceSelection(prefix){
  const raw=$(prefix+'-parallel').value;
  return {threads:Number($(prefix==='sample'?'threads':'resume-threads').value),parallel_samples:raw==='auto'?'auto':Number(raw),memory:$(prefix+'-memory').value.trim()||'auto'};
}
function showResourceChoices(prefix,result,initial=false){
  if(!result){show(prefix+'-resources',false);return;}
  show(prefix+'-resources',true);
  if(prefix==='resume')resumeResourcesReady=!result.error;
  if(result.error){$(prefix+'-resource-note').textContent=result.error;return;}
  const select=$(prefix+'-parallel'),prior=initial?String(result.selected_parallel_samples??'auto'):select.value;
  select.replaceChildren();
  const label=a=>a.parallel_samples+' sample job'+(a.parallel_samples===1?'':'s')+' × '+a.threads_per_sample+' cores each · '+a.memory_gb_per_sample.toFixed(1)+' GiB RAM each';
  const recommended=node('option','Automatic · suggested now: '+label(result.recommended));recommended.value='auto';select.append(recommended);
  for(const choice of result.choices){const option=node('option',label(choice));option.value=String(choice.parallel_samples);select.append(option);}
  select.value=[...select.options].some(o=>o.value===prior)?prior:'auto';
  if(prefix==='resume')$('resume-threads').value=result.threads;
  $(prefix+'-resource-note').textContent='Total task budget: '+result.threads+' CPU threads / '+result.recommended.total_memory_gb.toFixed(1)+' GiB. '+result.note;
}
async function refreshResourceChoices(prefix){
  const version=++resourceRequestVersion[prefix];
  const selection=resourceSelection(prefix);
  const context=prefix==='resume'?{project:$('resume-project').value}:{scan_id:scan?.scan_id,variants:$('variants').checked};
  if(prefix==='resume'){resumeResourcesReady=false;$('run').disabled=true;}
  $(prefix+'-resource-note').textContent='Checking sample-job options…';
  try{
    const result=await api('/api/resource-options',{...context,threads:selection.threads||undefined,memory:selection.memory});
    if(version!==resourceRequestVersion[prefix])return;
    showResourceChoices(prefix,result);
    if(prefix==='resume'){show('error',false);$('run').disabled=!prepared?.valid||!resumeResourcesReady;}
  }catch(error){
    if(version!==resourceRequestVersion[prefix])return;
    $(prefix+'-resource-note').textContent=error.message;
    throw error;
  }
}
for(const prefix of ['sample','resume']){
  $(prefix+'-detect-resources').onclick=()=>busy($(prefix+'-detect-resources'),()=>refreshResourceChoices(prefix));
  for(const id of [prefix+'-memory',prefix+'-parallel',prefix==='sample'?'threads':'resume-threads']){
    $(id).addEventListener('change',()=>{
      if(prefix==='sample'){invalidate();$('run').disabled=true;}
      else $('check-badge').textContent='Resources changed · checked again at Run';
      if(id!==prefix+'-parallel'&&(prefix==='resume'||scan))refreshResourceChoices(prefix).catch(failure);
    });
  }
}
$('variants').addEventListener('change',()=>{if(scan)refreshResourceChoices('sample').catch(failure);});
async function applyResumeResources(){
  const project=$('resume-project').value;
  const resources=resourceSelection('resume');
  await loadResume(project,{backend:$('resume-backend').value,
    image:$('resume-backend').value==='conda'?'':$('resume-image').value.trim(),resources});
}
$('resume-apply-resources').onclick=()=>busy($('resume-apply-resources'),applyResumeResources);
'''
    page = page.replace("(async()=>{try{if(!token)", script + "\n(async()=>{try{if(!token)", 1)
    page = page.replace("threads:Number($('threads').value),backend:", "threads:Number($('threads').value),resources_selection:resourceSelection('sample'),backend:", 1)
    page = page.replace("show('settings-card',true);$('fastq-preview').scrollIntoView", "show('settings-card',true);await refreshResourceChoices('sample');$('fastq-preview').scrollIntoView", 1)
    page = page.replace("$('run').disabled=!prepared.valid;$('run').hidden=false;", """const choices=prepared.resource_choices;
  $('resume-threads').value=choices?.threads||prepared.reviewed_summary.config.threads||1;
  $('resume-memory').value=choices?.memory||'auto';
  showResourceChoices('resume',choices,true);
  $('run').disabled=!prepared.valid||Boolean(choices&&!resumeResourcesReady);$('run').hidden=false;""", 1)
    page = page.replace("await api('/api/run',{project_id:prepared.id});", """if(prepared.kind==='existing_project'&&prepared.resource_choices){
    if(!resumeResourcesReady)throw Error('Correct the sample-job settings before Run.');
    await applyResumeResources();
    if(!prepared?.valid||!resumeResourcesReady)throw Error('Resolve the checks below before Run.');
  }
  await api('/api/run',{project_id:prepared.id});""", 1)
    page = page.replace("function showReviewedRun(job){", "function showReviewedRun(job){show('resume-resources',false);", 1)
    return page
