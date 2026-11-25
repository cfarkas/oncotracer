"""Startup dependency review and approved background installation."""

PANEL = '''<section class="card" id="dependency-card" aria-labelledby="dependency-title">
<div class="sectionhead"><span class="step">✓</span><h2 id="dependency-title">Check installed tools</h2></div>
<p id="dependency-versions" class="hint"></p>
<p id="dependency-status" role="status" aria-live="polite">Checking installed dependencies…</p>
<div class="grid"><div><label for="dependency-backend">Use analysis tools from</label><select id="dependency-backend"><option value="docker">Docker</option><option value="conda">Conda</option><option value="host">Existing host tools</option><option value="poetry">Poetry</option><option value="singularity">Apptainer / Singularity</option></select></div><div id="dependency-image-field"><label for="dependency-image">Docker image</label><input id="dependency-image" spellcheck="false" autocomplete="off"></div></div>
<div class="actions"><button id="dependency-recheck" type="button">Check again</button></div>
<div id="dependency-error" role="alert" class="error" hidden></div>
<div id="dependency-warnings" class="hint"></div>
<ul id="dependency-environments"></ul>
<div id="dependency-actions" class="actions"></div>
<details id="dependency-inventory"><summary>Installed tools and paths</summary><p class="hint">Locations are listed separately for the host and Docker image. A found executable is not the same as a verified environment.</p><div style="overflow:auto"><table><thead><tr><th>Tool</th><th>Runtime</th><th>Location</th><th>Status</th></tr></thead><tbody id="dependency-tools"></tbody></table></div></details>
<div id="dependency-plan" hidden><h3>Review installation</h3><p id="dependency-plan-note"></p><ol id="dependency-plan-steps"></ol><p id="dependency-plan-destination" class="hint"></p><details><summary>Installation commands</summary><pre id="dependency-commands" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details><div class="actions"><button id="dependency-confirm" class="primary" type="button">Install and verify</button><button id="dependency-dismiss" type="button">Keep current installation</button></div></div>
<div id="dependency-installation" hidden><p id="dependency-install-status" role="status" aria-live="polite"></p><progress id="dependency-progress" style="width:100%"></progress><button id="dependency-stop" type="button">Stop installation</button><details id="dependency-log-details"><summary>Installation log</summary><pre id="dependency-log" style="max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere"></pre><p id="dependency-log-path" class="hint"></p></details></div>
</section>'''

SCRIPT = r'''
let dependencyInstallActive=false;
const dependencyUi={check:null,plan:null,job:null,checking:false,timer:null,installTimer:null,queued:false,applied:null,initialized:false,syncing:false,locked:new Map()};
function dependencyError(error){const field=$('dependency-error');field.textContent=error.message||String(error);field.hidden=false;}
function dependencySelection(){return {backend:$('dependency-backend').value,image:$('dependency-image').value.trim()};}
function dependencyImageVisibility(){$('dependency-image-field').hidden=$('dependency-backend').value!=='docker';}
function dependencyApplySelection(fields){
  dependencyUi.syncing=true;
  try{
    if(fields.backend)$('dependency-backend').value=fields.backend;
    const image=fields.docker_image||fields.image;if(image)$('dependency-image').value=image;
    const backend=document.getElementById('backend'),imageField=document.getElementById('docker_image');
    if(backend&&fields.backend)backend.value=fields.backend;
    if(imageField&&image)imageField.value=image;
    if(backend&&fields.backend)backend.dispatchEvent(new Event('change',{bubbles:true}));
    if(imageField&&image)imageField.dispatchEvent(new Event('input',{bubbles:true}));
    dependencyImageVisibility();
  }finally{dependencyUi.syncing=false;}
}
function dependencyLock(){
  for(const id of ['dependency-backend','dependency-image','dependency-recheck','dependency-confirm','dependency-dismiss','backend','docker_image']){
    const field=document.getElementById(id);if(!field)continue;
    if(dependencyInstallActive){if(!dependencyUi.locked.has(field))dependencyUi.locked.set(field,field.disabled);field.disabled=true;}
    else if(dependencyUi.locked.has(field)){field.disabled=dependencyUi.locked.get(field);dependencyUi.locked.delete(field);}
  }
  if(typeof lockVariantInstallActions==='function')lockVariantInstallActions();
}
function renderDependencyReport(check){
  dependencyUi.check=check;const report=check.report;
  $('dependency-status').textContent=check.stage||check.status;
  if(check.error)dependencyError(new Error(check.error));
  if(!report)return;
  const release=report.release||{};
  $('dependency-versions').textContent='Launcher '+report.launcher_version+(release.version?' · Latest stable '+release.version:' · Online update check unavailable')+(report.image?.version?' · Installed image '+report.image.version:'');
  $('dependency-status').textContent=(report.ready?'Selected analysis tools verified.':'Some dependencies need attention.')+(report.image?.update_status==='update_available'?' An official image update is available.':'');
  const warnings=$('dependency-warnings');warnings.replaceChildren();for(const text of report.warnings||[])warnings.append(node('p',text));
  const environments=$('dependency-environments');environments.replaceChildren();for(const item of report.environments||[])environments.append(node('li',item.name+': '+item.status+(item.message?' — '+item.message:'')));
  const tools=$('dependency-tools');tools.replaceChildren();for(const item of [...(report.tools||[]),...(report.container_tools||[])]){const row=node('tr');row.append(node('td',item.name),node('td',item.scope||'Host'),node('td',item.path||'—'),node('td',item.status==='found'?'Found':'Not found'));tools.append(row);}
  const actions=$('dependency-actions');actions.replaceChildren();for(const action of report.actions||[]){const button=node('button',action.label,'primary');button.type='button';button.onclick=()=>reviewDependencyPlan(action.id);button.disabled=dependencyInstallActive;actions.append(button);}
}
async function pollDependencyCheck(){
  clearTimeout(dependencyUi.timer);
  try{
    const check=await api('/api/dependencies/status');renderDependencyReport(check);
    dependencyUi.checking=check.status==='checking';
    if(dependencyUi.checking)dependencyUi.timer=setTimeout(pollDependencyCheck,1000);
    else if(dependencyUi.queued){dependencyUi.queued=false;await checkDependencies();}
  }catch(error){dependencyUi.checking=false;dependencyError(error);}
}
async function checkDependencies(){
  if(dependencyInstallActive)return;
  if(dependencyUi.checking){dependencyUi.queued=true;return;}
  dependencyUi.plan=null;$('dependency-plan').hidden=true;$('dependency-error').hidden=true;$('dependency-actions').replaceChildren();
  $('dependency-status').textContent='Checking installed dependencies…';dependencyUi.checking=true;
  try{const check=await api('/api/dependencies/check',dependencySelection());renderDependencyReport(check);await pollDependencyCheck();}
  catch(error){dependencyUi.checking=false;dependencyError(error);}
}
async function reviewDependencyPlan(actionId){
  if(dependencyInstallActive||dependencyUi.checking)return;
  $('dependency-error').hidden=true;
  try{
    const plan=await api('/api/dependencies/plan',{check_id:dependencyUi.check.id,action_id:actionId});dependencyUi.plan=plan;
    $('dependency-plan-note').textContent=plan.note;
    $('dependency-plan-destination').textContent=plan.fields?.docker_image?'Verified image: '+plan.fields.docker_image:'New environment folder: '+plan.directory;
    const list=$('dependency-plan-steps');list.replaceChildren();for(const step of plan.steps||[])list.append(node('li',step.label));
    $('dependency-commands').textContent=(plan.steps||[]).map(step=>step.command.map(value=>JSON.stringify(value)).join(' ')).join('\n');
    $('dependency-plan').hidden=false;$('dependency-confirm').focus();
  }catch(error){dependencyError(error);}
}
function renderDependencyInstall(job){
  dependencyUi.job=job;dependencyInstallActive=['running','stopping'].includes(job.status);
  $('dependency-installation').hidden=job.status==='idle';
  $('dependency-install-status').textContent=job.status+': '+(job.stage||'')+(job.elapsed_seconds!==undefined?' · '+job.elapsed_seconds+' s':'');
  $('dependency-progress').max=job.total_steps||1;$('dependency-progress').value=job.completed_steps||0;
  $('dependency-stop').hidden=!dependencyInstallActive;$('dependency-stop').disabled=job.status==='stopping';
  $('dependency-log').textContent=job.log||'';$('dependency-log-path').textContent=job.log_path||'';
  if(job.error){dependencyError(new Error(job.error));$('dependency-log-details').open=true;}
  dependencyLock();
}
async function pollDependencyInstall(){
  clearTimeout(dependencyUi.installTimer);
  try{
    const job=await api('/api/dependencies/install/status');renderDependencyInstall(job);
    if(dependencyInstallActive)dependencyUi.installTimer=setTimeout(pollDependencyInstall,1000);
    else if(job.status==='complete'&&dependencyUi.applied!==job.id){
      dependencyUi.applied=job.id;dependencyApplySelection(job.fields||{});if(typeof invalidate==='function')invalidate();
      $('dependency-plan').hidden=true;await checkDependencies();scheduleAutomaticVariantCheck();
    }
  }catch(error){dependencyError(error);if(dependencyInstallActive)dependencyUi.installTimer=setTimeout(pollDependencyInstall,2000);}
}
$('dependency-confirm').onclick=async()=>{
  if(!dependencyUi.plan||dependencyInstallActive)return;
  $('dependency-error').hidden=true;dependencyInstallActive=true;dependencyLock();
  try{const job=await api('/api/dependencies/install',{plan_id:dependencyUi.plan.id,confirmed:true});renderDependencyInstall(job);await pollDependencyInstall();}
  catch(error){dependencyInstallActive=false;dependencyError(error);dependencyLock();}
};
$('dependency-dismiss').onclick=()=>{$('dependency-plan').hidden=true;dependencyUi.plan=null;};
$('dependency-stop').onclick=async()=>{try{renderDependencyInstall(await api('/api/dependencies/stop',{job_id:dependencyUi.job.id}));}catch(error){dependencyError(error);}};
$('dependency-recheck').onclick=()=>checkDependencies();
$('dependency-backend').onchange=()=>{dependencyApplySelection(dependencySelection());checkDependencies();};
$('dependency-image').onchange=()=>{dependencyApplySelection(dependencySelection());checkDependencies();};
let automaticVariantTimer=null;
function scheduleAutomaticVariantCheck(){
  clearTimeout(automaticVariantTimer);automaticVariantTimer=setTimeout(async()=>{
    if(dependencyInstallActive||(typeof variantInstallActive!=='undefined'&&variantInstallActive))return;
    try{const payload=variantResourcePayload();if(!payload.specimen_type||!payload.callers?.length)return;await detectVariantResources(payload,'all',false);}
    catch(error){const status=document.getElementById('variant-detection-status');if(status)status.textContent=error.message||String(error);}
  },350);
}
for(const id of ['variant-fresh','variant-ffpe','specimen-fresh','specimen-ffpe']){const button=document.getElementById(id);if(button)button.addEventListener('click',scheduleAutomaticVariantCheck);}
for(const id of ['backend','docker_image','analysis']){const field=document.getElementById(id);if(field)field.addEventListener('change',()=>{
  if(dependencyUi.syncing)return;
  if(id==='backend')$('dependency-backend').value=field.value;
  if(id==='docker_image')$('dependency-image').value=field.value;
  dependencyImageVisibility();if(id!=='analysis'&&dependencyUi.initialized)checkDependencies();scheduleAutomaticVariantCheck();
});}
async function setupDependencies(system){
  const initial={...(system.dependency_selection||{backend:'conda',image:system.default_docker_image})};
  if(!document.getElementById('backend')){
    if(['docker','singularity'].includes(initial.backend))initial.backend='conda';
    for(const option of $('dependency-backend').options)if(['docker','singularity'].includes(option.value))option.disabled=true;
  }
  dependencyApplySelection(initial);dependencyUi.initialized=true;
  $('dependency-versions').textContent='Launcher '+(system.launcher_version||'unknown');
  try{
    const job=await api('/api/dependencies/install/status');
    if(job.status!=='idle'){renderDependencyInstall(job);if(dependencyInstallActive){await pollDependencyInstall();return;}}
    await checkDependencies();
  }catch(error){dependencyError(error);}
}
'''


def add_dependency_ui(page):
    anchor = '<div id="error" role="alert" class="error" hidden></div>'
    if anchor not in page:
        raise ValueError('Setup error panel is missing')
    page = page.replace(anchor, anchor + PANEL, 1)
    page = page.replace('</script>', SCRIPT + '\n</script>', 1)
    for assignment in ("const system=await api('/api/system');", "system=await api('/api/system');"):
        if assignment in page:
            page = page.replace(assignment, assignment + 'await setupDependencies(system);', 1)
            break
    else:
        raise ValueError('Setup initialization is missing')
    return page
