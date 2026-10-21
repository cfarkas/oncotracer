"""Offer resource-based sample counts for both new and continued analyses."""


def _controls(prefix, *, resume=False):
    cpu = (f'<label for="{prefix}-threads">Total CPU threads</label>'
           f'<input id="{prefix}-threads" type="number" min="1" step="1">') if resume else ''
    return f'''<section id="{prefix}-resources" {'hidden' if resume else ''}>
<h3>Choose how many samples run together</h3>
<p class="hint">Alignment and variant calling share the CPU and RAM budgets below. Completed outputs are validated before reuse.</p>
{cpu}<div class="grid"><div><label for="{prefix}-memory">Total RAM budget</label><input id="{prefix}-memory" value="auto" placeholder="auto or 64GB"></div>
<div><label for="{prefix}-parallel">Concurrent samples</label><select id="{prefix}-parallel"><option value="auto">Automatic · check available resources at Run</option></select></div></div>
<p id="{prefix}-resource-note" class="hint" role="status">Detect available resources to compare the options. Other analyses share this server.</p>
<button id="{prefix}-detect-resources" type="button">Detect available resources</button>
{('<button id="resume-apply-resources" type="button">Apply resources and check</button>') if resume else ''}
</section>'''


def add_sample_resource_ui(page):
    page = page.replace('<label class="check" id="reports-field">', _controls('sample') + '<label class="check" id="reports-field">', 1)
    page = page.replace('<div class="actions"><button id="resume-project-button">', _controls('resume', resume=True) + '<div class="actions"><button id="resume-project-button">', 1)
    # The common control supplies both alignment and variant limits in this UI.
    # Existing-BAM variant setup continues to use its own resource fields.
    page = page.replace('</style></head>', '#variant-parallel-controls{display:none!important}</style></head>', 1)
    page = page.replace("$('run').disabled=!prepared?.valid;", "$('run').disabled=!prepared?.valid||Boolean(prepared?.resource_choices&&!prepared?.resources_confirmed);")
    page = page.replace("function resumeSettingsChanged(){", "function resumeSettingsChanged(){show('resume-resources',false);")
    script = r'''
const resourceRequestVersion={sample:0,resume:0};
function resourceSelection(prefix){
  const raw=$(prefix+'-parallel').value;
  return {threads:Number($(prefix==='sample'?'threads':'resume-threads').value),parallel_samples:raw==='auto'?'auto':Number(raw),memory:$(prefix+'-memory').value.trim()||'auto'};
}
function showResourceChoices(prefix,result){
  if(!result)return;
  show(prefix+'-resources',true);
  if(result.error){$(prefix+'-resource-note').textContent=result.error;return;}
  const select=$(prefix+'-parallel'),prior=select.value;
  select.replaceChildren();
  const label=a=>a.parallel_samples+' sample'+(a.parallel_samples===1?'':'s')+' · '+a.threads_per_sample+' CPUs / '+a.memory_gb_per_sample.toFixed(1)+' GiB each';
  const recommended=node('option','Automatic · recommended now: '+label(result.recommended));recommended.value='auto';select.append(recommended);
  for(const choice of result.choices){const option=node('option',label(choice));option.value=String(choice.parallel_samples);select.append(option);}
  select.value=[...select.options].some(o=>o.value===prior)?prior:'auto';
  if(prefix==='resume')$('resume-threads').value=result.threads;
  $(prefix+'-resource-note').textContent='Detected '+result.hardware.cpu_workers_available+' CPU workers. Total task budget: '+result.threads+' CPUs / '+result.recommended.total_memory_gb.toFixed(1)+' GiB. '+result.note;
}
async function refreshResourceChoices(prefix){
  const version=++resourceRequestVersion[prefix];
  const selection=resourceSelection(prefix);
  const context=prefix==='resume'?{project:$('resume-project').value}:{scan_id:scan?.scan_id,variants:$('variants').checked};
  const result=await api('/api/resource-options',{...context,threads:selection.threads||undefined,memory:selection.memory});
  if(version===resourceRequestVersion[prefix])showResourceChoices(prefix,result);
}
for(const prefix of ['sample','resume']){
  $(prefix+'-detect-resources').onclick=()=>busy($(prefix+'-detect-resources'),()=>refreshResourceChoices(prefix));
  for(const id of [prefix+'-memory',prefix+'-parallel',prefix==='sample'?'threads':'resume-threads']){
    $(id).addEventListener('change',()=>{invalidate();$('run').disabled=true;
      if(id!==prefix+'-parallel'&&(prefix==='resume'||scan))refreshResourceChoices(prefix).catch(failure);});
  }
}
$('variants').addEventListener('change',()=>{if(scan)refreshResourceChoices('sample').catch(failure);});
$('resume-apply-resources').onclick=()=>busy($('resume-apply-resources'),()=>loadResume($('resume-project').value,{
  backend:$('resume-backend').value,image:$('resume-backend').value==='conda'?'':$('resume-image').value.trim(),resources:resourceSelection('resume')
}));
'''
    page = page.replace("(async()=>{try{if(!token)", script + "\n(async()=>{try{if(!token)", 1)
    page = page.replace('threads:Number($(\'threads\').value),backend:', "threads:Number($('threads').value),resources_selection:resourceSelection('sample'),backend:", 1)
    page = page.replace("show('settings-card',true);$('fastq-preview').scrollIntoView", "show('settings-card',true);await refreshResourceChoices('sample');$('fastq-preview').scrollIntoView", 1)
    page = page.replace("$('run').disabled=!prepared.valid;$('run').hidden=false;", """$('resume-project').value=prepared.project;
  showResourceChoices('resume',prepared.resource_choices);
  if(prepared.resource_choices)$('resume-memory').value=prepared.resource_choices.memory;
  $('run').disabled=!prepared.valid||Boolean(prepared.resource_choices&&!prepared.resources_confirmed);$('run').hidden=false;
  if(prepared.resource_choices&&!prepared.resources_confirmed)$('resume-note').textContent='Saved settings loaded. Choose concurrent samples above, then Apply resources and check before Run.';""", 1)
    return page
