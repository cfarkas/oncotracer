"""Shared background installer controls for FASTQ and existing-BAM setup."""

PANEL = '''<div class="variant-detection">
<div class="variant-block-heading"><div><strong>Required tools</strong><p class="hint">Detect local tools, or install the missing caller environments.</p></div></div>
<label for="variant-tools-folder">Tools folder to search (optional)</label><div class="variant-path-row"><input id="variant-tools-folder" placeholder="Choose a folder containing tools or Conda environments"><button id="variant-tools-browse" type="button">Choose tools folder</button></div>
<div class="actions"><button id="variant-autodetect" type="button" class="primary variant-detect-glow">detect_tools</button><button id="variant-resource-review" type="button" hidden>Review resource details</button></div>
<p id="variant-detection-status" class="hint" role="status" aria-live="polite">Check common installation folders on the computer running OncoTracer.</p>
<div id="variant-install-choices" class="variant-install-choices" hidden><p class="hint">Missing tools detected. Choose how to install them.</p><div class="actions"><button id="variant-install-conda" data-variant-install="conda" type="button" class="primary">Install with Conda</button><button id="variant-install-docker" data-variant-install="docker" type="button" class="primary">Install with Docker</button></div></div>
<p id="variant-install-overview" class="hint" role="status" aria-live="polite" hidden></p><button id="variant-install-review" type="button" hidden>View installation progress</button></div>'''

DIALOG = '''<section id="variant-install-panel" class="variant-install-panel" hidden>
<h3>Install caller tools</h3><p class="hint">Installation runs in the background. Keep the OncoTracer terminal open, then save and check your analysis settings when the tools are ready.</p>
<label for="variant-install-method">Installation method</label><select id="variant-install-method"><option value="auto">Choose an available method</option></select>
<p id="variant-install-plan-note" class="hint"></p><ul id="variant-install-steps"></ul>
<details id="variant-install-command-details"><summary>Installation commands and destination</summary><pre id="variant-install-commands"></pre></details>
<div class="actions"><button id="variant-install-start" type="button" class="primary" disabled>Install selected tools</button><button id="variant-install-stop" type="button" hidden>Stop installation</button><button id="variant-install-apply" type="button" hidden>Use installed tools</button></div>
<p id="variant-install-status" role="status" aria-live="polite"></p><p id="variant-install-error" class="error" role="alert" hidden></p>
<progress id="variant-install-progress" aria-label="Installation steps" hidden></progress>
<details id="variant-install-log-details" hidden><summary>Installation log</summary><pre id="variant-install-log" class="logs"></pre><p id="variant-install-log-path" class="folderpath"></p></details>
</section>'''

STATE = "let variantInstallPlan=null,variantInstallJob=null,variantInstallActive=false,variantInstallTimer=null,variantInstallSnapshot=null,variantInstallApplied=null,variantInstallPlanning=0;"

STYLE = '''.variant-install-panel{border:1px solid var(--line);border-radius:8px;padding:18px;margin:18px 0}.variant-install-panel h3{margin-top:0}.variant-install-panel progress{width:100%;accent-color:var(--accent)}.variant-install-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.variant-install-panel .logs{max-height:280px;overflow:auto}.variant-detection .actions{margin-top:12px}.variant-detection .variant-path-row{flex-wrap:wrap}.variant-detection .variant-path-row input{min-width:min(260px,100%)}'''
STYLE += '''
.variant-detect-glow{box-shadow:0 0 0 3px #007c7826,0 0 16px #007c7845;animation:variant-detect-glow 2.4s ease-in-out infinite}.variant-detect-glow:disabled{animation:none;box-shadow:none}.variant-detect-glow:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.variant-install-choices{margin-top:16px}.variant-install-choices>.hint{margin-bottom:8px}.variant-install-choices .actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}
@keyframes variant-detect-glow{0%,100%{box-shadow:0 0 0 3px #007c7826,0 0 12px #007c7833}50%{box-shadow:0 0 0 5px #007c781a,0 0 23px #007c7866}}
@media(prefers-reduced-motion:reduce){.variant-detect-glow{animation:none}}
'''

SCRIPT = r'''
function variantInstallError(error){const field=$('variant-install-error');field.textContent=error.message||String(error);field.hidden=false;}
function variantInstallFingerprint(){return JSON.stringify(variantResourcePayload());}
function hasMissingVariantTools(resources){return resources.some(resource=>resource.status==='missing'&&['variant_tool_prefix','variant_strelka_prefix','variant_ffperase_prefix','variant_clairsto_sif'].includes(variantResourceField(resource)));}
function syncVariantInstallChoices(resources=[]){
  $('variant-install-choices').hidden=variantInstallActive||!hasMissingVariantTools(resources);
  for(const button of document.querySelectorAll('[data-variant-install]'))button.disabled=variantInstallActive;
}
function openVariantInstall(method){
  if(variantInstallActive){$('variant-install-panel').hidden=false;openVariantResourceDialog();return;}
  return planVariantInstall(method);
}
function lockVariantInstallActions(){
  if(variantInstallActive){$('prepare').disabled=true;$('run').disabled=true;}
  else if(typeof lockSettings==='function')lockSettings();
  else {$('prepare').disabled=false;$('run').disabled=!prepared?.valid;}
}
function renderVariantInstallPlan(plan){
  variantInstallPlan=plan;const select=$('variant-install-method');select.replaceChildren();
  for(const option of plan.options||[]){const item=node('option',option.label+(option.available?'':' · unavailable'));item.value=option.method;item.disabled=!option.available;select.append(item);}
  select.value=plan.method;select.disabled=variantInstallActive;
  $('variant-install-plan-note').textContent=plan.reason||plan.note||'';
  const steps=$('variant-install-steps');steps.replaceChildren();for(const step of plan.steps||[])steps.append(node('li',step.label));
  $('variant-install-commands').textContent=(plan.directory?'New installation folder: '+plan.directory+'\n\n':'')+(plan.steps||[]).map(step=>step.command.map(value=>JSON.stringify(value)).join(' ')).join('\n');
  $('variant-install-start').disabled=variantInstallActive||!plan.available;
  $('variant-install-start').textContent=plan.method==='docker'?'Install with Docker':'Install with Conda';
  $('variant-install-start').hidden=false;
}
async function planVariantInstall(method='auto'){
  const sequence=++variantInstallPlanning;
  $('variant-install-panel').hidden=false;$('variant-install-error').hidden=true;
  $('variant-install-status').textContent='';$('variant-install-log-details').hidden=true;$('variant-install-apply').hidden=true;
  $('variant-install-start').disabled=true;$('variant-install-plan-note').textContent='Checking installation options…';
  $('variant-resource-title').textContent='Required tools';openVariantResourceDialog();
  try{const plan=await api('/api/variant-install/plan',{...variantResourcePayload(),method});if(sequence===variantInstallPlanning)renderVariantInstallPlan(plan);}
  catch(error){if(sequence===variantInstallPlanning){variantInstallPlan=null;$('variant-install-plan-note').textContent='';variantInstallError(error);}}
}
function renderVariantInstallJob(job){
  variantInstallJob=job;variantInstallActive=['running','stopping'].includes(job.status);
  syncVariantInstallChoices(variantResourceLastResult?.resources||[]);
  $('variant-install-panel').dataset.state=job.status;
  $('variant-install-panel').hidden=false;
  const message=(job.status==='complete'?'Tools installed. ':job.status==='failed'?'Installation failed. ':job.status==='cancelled'?'Installation stopped. ':'')+(job.stage||'')+(job.elapsed_seconds!==undefined?' · '+job.elapsed_seconds+'s':'');
  $('variant-install-status').textContent=message;$('variant-install-overview').textContent=message;
  $('variant-install-overview').hidden=false;$('variant-install-review').hidden=false;
  $('variant-install-progress').hidden=!variantInstallActive;
  $('variant-install-progress').max=job.total_steps||1;$('variant-install-progress').value=job.completed_steps||0;
  $('variant-install-start').disabled=variantInstallActive;
  $('variant-install-start').hidden=variantInstallActive||job.status==='complete';
  $('variant-install-method').disabled=variantInstallActive;
  $('variant-install-stop').hidden=!variantInstallActive;$('variant-install-stop').disabled=job.status==='stopping';
  const incompatible=job.fields?.backend==='docker'&&!document.getElementById('backend');
  $('variant-install-apply').hidden=job.status!=='complete'||variantInstallApplied===job.id||incompatible;
  if(incompatible)$('variant-install-status').textContent='Docker tools are ready for FASTQ setup. The existing-BAM form uses native caller environments.';
  if(job.error)variantInstallError(new Error(job.error));
  const log=$('variant-install-log'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<50;
  log.textContent=(job.log_truncated?'[Latest output; full log saved below.]\n':'')+(job.log||'');if(atEnd)log.scrollTop=log.scrollHeight;
  $('variant-install-log-details').hidden=false;$('variant-install-log-path').textContent=job.log_path?'Full log: '+job.log_path:'';
  if(job.status==='failed')$('variant-install-log-details').open=true;
  lockVariantInstallActions();
}
async function applyVariantInstallation(){
  const job=variantInstallJob;if(!job||job.status!=='complete'||variantInstallActive)return;
  const fields=job.fields||{};
  if(fields.backend==='docker'&&!document.getElementById('backend'))return;
  if(fields.backend&&document.getElementById('backend')){
    if(fields.docker_image)$('docker_image').value=fields.docker_image;$('backend').value=fields.backend;
    $('backend').dispatchEvent(new Event('change',{bubbles:true}));if(fields.docker_image)$('docker_image').dispatchEvent(new Event('input',{bubbles:true}));
  }
  for(const [key,value] of Object.entries(fields))if(variantPathKeys.includes(key))variantApplyPath(key,value,true);
  variantInstallApplied=job.id;$('variant-install-apply').hidden=true;invalidate();
  $('variant-install-status').textContent='Installed tools selected. Save and check your analysis settings to continue.';
  try{await detectVariantResources(variantResourcePayload(),'all');}catch(error){variantInstallError(error);}
  lockVariantInstallActions();
}
async function pollVariantInstall(){
  clearTimeout(variantInstallTimer);
  try{
    const job=await api('/api/variant-install/status');if(job.status==='idle')return;
    if(!variantInstallPlan)renderVariantInstallPlan(job.plan);
    renderVariantInstallJob(job);
    if(job.status==='complete'&&variantInstallApplied!==job.id&&variantInstallSnapshot){
      let unchanged=false;try{unchanged=variantInstallFingerprint()===variantInstallSnapshot;}catch(error){}
      if(unchanged)await applyVariantInstallation();
      else $('variant-install-status').textContent='Tools installed. Settings changed during installation; review the paths above before choosing Use installed tools.';
      variantInstallSnapshot=null;
    }
    if(variantInstallActive)variantInstallTimer=setTimeout(pollVariantInstall,1200);
  }catch(error){variantInstallError(error);if(variantInstallActive)variantInstallTimer=setTimeout(pollVariantInstall,2500);}
}
for(const button of document.querySelectorAll('[data-variant-install]'))button.onclick=()=>openVariantInstall(button.dataset.variantInstall);
$('variant-install-method').onchange=()=>planVariantInstall($('variant-install-method').value);
$('variant-install-start').onclick=async()=>{
  if(variantInstallActive)return;
  // Refresh server-owned commands for the current form before each explicit
  // start, including retries. Never execute the copyable recipe text.
  $('variant-install-start').disabled=true;$('variant-install-error').hidden=true;
  try{
    const method=variantInstallPlan?.method||'auto',payload=variantResourcePayload();
    const plan=await api('/api/variant-install/plan',{...payload,method});renderVariantInstallPlan(plan);
    if(!plan.available)throw Error(plan.reason);
    $('variant-install-start').disabled=true;
    variantInstallSnapshot=JSON.stringify(payload);variantInstallApplied=null;
    const job=await api('/api/variant-install/start',{plan_id:plan.id});renderVariantInstallJob(job);invalidate();
    $('variant-install-log-details').open=true;await pollVariantInstall();
  }catch(error){variantInstallError(error);$('variant-install-start').disabled=false;}
};
$('variant-install-stop').onclick=async()=>{try{renderVariantInstallJob(await api('/api/variant-install/stop',{job_id:variantInstallJob.id}));await pollVariantInstall();}catch(error){variantInstallError(error);}};
$('variant-install-apply').onclick=()=>applyVariantInstallation();
$('variant-install-review').onclick=()=>{$('variant-install-panel').hidden=false;openVariantResourceDialog();$('variant-install-panel').scrollIntoView({block:'start'});};
$('variant-tools-browse').onclick=async()=>{try{const result=await api('/api/pick-path',{kind:'folder',path:$('variant-tools-folder').value.trim()||$('project-parent')?.value||(typeof system!=='undefined'?system?.start_dir:'')||'/'});if(!result.cancelled){$('variant-tools-folder').value=result.path;resetVariantResourceResults();}}catch(error){$('variant-detection-status').textContent=error.message;}};
$('variant-tools-folder').oninput=()=>resetVariantResourceResults();
pollVariantInstall();
'''
