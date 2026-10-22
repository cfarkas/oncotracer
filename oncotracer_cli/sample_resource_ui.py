"""Offer sample jobs × cores per sample for both new and continued analyses."""


def _controls(prefix, *, resume=False):
    cpu = (f'<label for="{prefix}-threads">Total CPU thread budget (shared by sample jobs)</label>'
           f'<input id="{prefix}-threads" type="number" min="1" step="1">') if resume else ''
    return f'''<section id="{prefix}-resources" {'hidden' if resume else ''}>
<h3>Choose parallel sample jobs</h3>
<p class="hint">Each job processes one sample. Choose the number of samples running at once and see the cores and RAM allocated to each. Completed outputs are validated before reuse.</p>
{cpu}<div class="grid"><div><label for="{prefix}-memory">Total RAM budget for all sample jobs</label><input id="{prefix}-memory" value="auto" placeholder="auto or 64GB"></div>
<div><label for="{prefix}-parallel">Parallel samples × cores per sample</label><select id="{prefix}-parallel"><option value="auto">Automatic · check available resources at Run</option></select></div></div>
<div id="{prefix}-manual-field" hidden><label for="{prefix}-manual-parallel">Manual number of parallel samples</label><input id="{prefix}-manual-parallel" type="number" min="1" step="1" value="1"><p class="hint">This overrides the suggested fits. The count is limited only by the number of samples. Too many jobs may slow processing or exhaust memory.</p></div>
<p id="{prefix}-manual-note" class="hint" role="status" hidden></p>
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
const resourcePlans={sample:null,resume:null};
let resumeResourcesReady=false;
function resourceSelection(prefix){
  const raw=$(prefix+'-parallel').value;
  return {threads:Number($(prefix==='sample'?'threads':'resume-threads').value),parallel_samples:raw==='auto'?'auto':Number(raw==='manual'?$(prefix+'-manual-parallel').value:raw),memory:$(prefix+'-memory').value.trim()||'auto'};
}
function updateManualResources(prefix){
  const manual=$(prefix+'-parallel').value==='manual',result=resourcePlans[prefix];
  show(prefix+'-manual-field',manual);show(prefix+'-manual-note',manual);
  const n=resourceSelection(prefix).parallel_samples,valid=n==='auto'||(Number.isSafeInteger(n)&&n>0);
  let note='Enter a positive whole number of parallel samples.';
  if(manual&&valid&&result&&!result.error){
    const count=Math.min(n,result.sample_count),cores=Math.max(1,Math.floor(result.threads/count)),ram=result.total_memory_gb/count;
    note=count+' sample job'+(count===1?'':'s')+' × '+cores+' cores each · '+ram.toFixed(1)+' GiB RAM each.';
    if(n>count)note+=' Limited to the '+count+' samples in this analysis.';
    if(count>result.threads||ram<result.minimum_memory_gb_per_sample)note+=' Exceeds the suggested fit; jobs may slow down or fail. Your manual count will be kept.';
  }
  $(prefix+'-manual-note').textContent=note;
  if(prefix==='resume'){
    resumeResourcesReady=Boolean(result&&!result.error&&valid&&(n!=='auto'||result.recommended));
    $('run').disabled=!prepared?.valid||!resumeResourcesReady;
  }
}
function showResourceChoices(prefix,result,initial=false){
  resourcePlans[prefix]=result;
  if(!result){show(prefix+'-resources',false);return;}
  show(prefix+'-resources',true);
  if(prefix==='resume')resumeResourcesReady=!result.error;
  if(result.error){$(prefix+'-resource-note').textContent=result.error;return;}
  const select=$(prefix+'-parallel'),prior=initial?String(result.selected_parallel_samples??'auto'):select.value;
  select.replaceChildren();
  const label=a=>a.parallel_samples+' sample job'+(a.parallel_samples===1?'':'s')+' × '+a.threads_per_sample+' cores each · '+a.memory_gb_per_sample.toFixed(1)+' GiB RAM each';
  const recommended=node('option',result.recommended?'Automatic · suggested now: '+label(result.recommended):'Automatic · no recommended fit with this RAM budget');recommended.value='auto';select.append(recommended);
  for(const choice of result.choices){const option=node('option',label(choice));option.value=String(choice.parallel_samples);select.append(option);}
  const manual=node('option','Manual · enter the number of parallel samples');manual.value='manual';select.append(manual);
  if([...select.options].some(o=>o.value===prior))select.value=prior;
  else if(/^[1-9][0-9]*$/.test(prior)){select.value='manual';$(prefix+'-manual-parallel').value=prior;}
  else select.value='auto';
  if(prefix==='resume')$('resume-threads').value=result.threads;
  $(prefix+'-resource-note').textContent='Total task budget: '+result.threads+' CPU threads / '+result.total_memory_gb.toFixed(1)+' GiB. '+(result.recommendation_error?result.recommendation_error+' ':'')+result.note;
  updateManualResources(prefix);
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
  for(const id of [prefix+'-memory',prefix+'-parallel',prefix+'-manual-parallel',prefix==='sample'?'threads':'resume-threads']){
    $(id).addEventListener('change',()=>{
      if(prefix==='sample'){invalidate();$('run').disabled=true;}
      else $('check-badge').textContent='Resources changed · checked again at Run';
      updateManualResources(prefix);
      if(![prefix+'-parallel',prefix+'-manual-parallel'].includes(id)&&(prefix==='resume'||scan))refreshResourceChoices(prefix).catch(failure);
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
