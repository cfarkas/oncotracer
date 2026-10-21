"""Browser controls for explicit project continuation and licensed model setup."""


def add_resume_ui(page):
    choice = '''<section class="card" id="analysis-choice" aria-label="Choose how to start"><div class="grid">
<button class="platform" id="choose-new-analysis" aria-pressed="false"><strong>1. New analysis</strong><span>Select reads, assign samples and choose analysis settings.</span></button>
<button class="platform" id="choose-existing-analysis" aria-pressed="false"><strong>2. Continue an existing analysis</strong><span>Load a saved project and reuse verified completed work.</span></button>
</div></section>'''
    page = page.replace('<main inert', '<main id="analysis-root" data-analysis-entry="choose" inert')
    page = page.replace('</style></head>', '''
main:not([data-analysis-entry="new"]) #platform-card,
main:not([data-analysis-entry="new"]) #specimen-card,
main:not([data-analysis-entry="new"]) #input-card,
main:not([data-analysis-entry="new"]) #samples-card,
main:not([data-analysis-entry="new"]) #settings-card,
main:not([data-analysis-entry="new"]) #existing-bam-link,
main:not([data-analysis-entry="resume"]) #resume-card{display:none!important}
</style></head>''')
    card = '''<section class="card" id="resume-card"><h2>Continue an existing analysis</h2>
<p>Choose your saved analysis output project containing <code>config/run.yml</code> and <code>results</code>, including after moving it. Stop the project's current run first.</p>
<label for="resume-project">Existing project folder</label><div class="row"><input id="resume-project" placeholder="/path/to/oncotracer_outputs/ONT_fresh"><button data-browse="resume-project">Browse</button></div>
<div class="grid" style="margin-top:18px"><div><label for="resume-backend">Analysis tools</label><select id="resume-backend"><option value="">Use saved project backend</option value="conda">Conda</option><option value="docker">Docker</option></select></div>
<div id="resume-image-field"><label for="resume-image">Docker image (optional)</label><div class="row"><input id="resume-image"><button id="resume-current-image" type="button">Use current image</button></div><p class="hint">Leave blank to use the saved project's image. Correct an unavailable tag here before loading.</p></div></div>
<div class="actions"><button id="resume-project-button">Load saved settings and check</button></div><p id="resume-note" class="hint">Saved samples and analysis settings are retained. Analysis starts only after review and Run. Compatible completed stages are checked before reuse.</p></section>'''
    page = page.replace('<div id="error" role="alert" class="error" hidden></div>',
                        '<div id="error" role="alert" class="error" hidden></div>' + choice + card)
    page = page.replace('Use existing project · overwrite settings', 'Continue existing project · retain settings')
    page = page.replace('Save to a new or existing project. Saving replaces its configuration and sample assignments, with previous settings kept in config/backups/.',
                        'A new project saves the settings above. An existing project loads its saved samples and settings for review and resume.')
    model = '''<div id="sturgeon-download"><p>Use the pretrained general CNS model and matching hg38 probes, or supply your own paths below.</p>
<p><a href="https://github.com/UMCUGenetics/sturgeon/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">Read the Sturgeon license</a>. Download and use require your acknowledgement. OncoTracer does not grant a license or redistribute the model.</p>
<button id="prepare-sturgeon" type="button">Download / verify Sturgeon general model</button><p id="sturgeon-download-status" role="status"></p></div>'''
    page = page.replace('<div id="sturgeon-fields" hidden><h3>Sturgeon · CNS tumours</h3>',
                        '<div id="sturgeon-fields" hidden><h3>Sturgeon · CNS tumours</h3>' + model)
    page = page.replace('I obtained Sturgeon and accepted its applicable license.',
                        'I have read and accepted the applicable Sturgeon license for my intended use.')
    page = page.replace("$('cpg-license').append($('license').closest('label'));",
                        "$('cpg-license').append($('license').closest('label'),$('sturgeon-download'));show('resume-card',false);")
    page = page.replace("$('prepare').onclick=()=>busy($('prepare'),async()=>{if(!scan)",
                        "$('prepare').onclick=()=>busy($('prepare'),async()=>{if($('project-mode').value==='existing')return loadResume(projectDestination(),{backend:$('backend').value,image:$('backend').value==='docker'?$('docker_image').value.trim():''});if(!scan)")
    script = r'''
let analysisEntry='choose',entryDefaults={};
function setAnalysisEntry(kind){
  analysisEntry=kind;$('analysis-root').dataset.analysisEntry=kind;
  $('choose-new-analysis').setAttribute('aria-pressed',String(kind==='new'));
  $('choose-existing-analysis').setAttribute('aria-pressed',String(kind==='resume'));
  show('analysis-choice',kind!=='active'&&kind!=='cpg');
}
async function chooseAnalysisEntry(kind){
  if(operationBusy)return;
  if(activeJob&&['running','stopping'].includes(activeJob.status))throw Error('Stop the active analysis before changing workflows.');
  invalidate();show('error',false);$('run').disabled=true;
  show('reviewed-run',false);$('config-preview').parentElement.hidden=false;
  setAnalysisEntry(kind);
  if(kind==='new'){
    show('platform-card',true);
    show('workflow',Boolean(mode&&variantSpecimen));
    if(!scan)await restoreRunOrDiscover(entryDefaults);
  }else show('workflow',false);
}
$('choose-new-analysis').onclick=()=>chooseAnalysisEntry('new').catch(failure);
$('choose-existing-analysis').onclick=()=>chooseAnalysisEntry('resume').catch(failure);
function resumeSettingsChanged(){
  invalidate();show('error',false);$('run').disabled=true;
  $('resume-note').textContent='Load saved settings and check again before Run.';
  show('resume-image-field',$('resume-backend').value!=='conda');
}
for(const id of ['resume-project','resume-backend','resume-image']){
  $(id).addEventListener('input',resumeSettingsChanged);
  $(id).addEventListener('change',resumeSettingsChanged);
}
$('resume-current-image').onclick=()=>{
  $('resume-backend').value='docker';$('resume-image').value=$('resume-image').dataset.current;
  resumeSettingsChanged();
};
async function loadResume(project,options=null){
  invalidate();$('run').disabled=true;
  if(!project.trim())throw Error('Choose the project folder containing config/run.yml and results.');
  options=options||{backend:$('resume-backend').value,image:$('resume-backend').value==='conda'?'':$('resume-image').value.trim()};
  prepared=await api('/api/resume',{project,...options});
  showResumedProject();
}
function showResumedProject(){
  setAnalysisEntry('resume');
  $('resume-project').value=prepared.project;
  $('resume-backend').value=prepared.backend;
  $('resume-image').value=prepared.docker_image||'';
  $('config-preview').textContent=prepared.config;
  $('saved-path').textContent='Saved project: '+prepared.project;
  $('check-badge').textContent=prepared.valid?'Resume checked':'Needs attention';
  $('check-messages').replaceChildren();
  for(const message of prepared.check.errors||[])$('check-messages').append(node('p',message,'error'));
  for(const message of prepared.check.warnings||[])$('check-messages').append(node('p',message,'notice'));
  const samples=prepared.reviewed_summary.samples;
  $('check-messages').append(node('p','Retained '+samples.length+' samples: '+samples.map(s=>s.name+' ('+s.sample_type+')').join(', ')));
  $('resume-note').textContent='Loaded '+prepared.project+'. Review saved settings below, then Run to continue.';
  $('run').disabled=!prepared.valid;$('run').hidden=false;
  show('workflow',true);show('review-card',true);$('review-card').scrollIntoView({behavior:'smooth'});
}
$('resume-project-button').onclick=()=>busy($('resume-project-button'),()=>loadResume($('resume-project').value));
$('prepare-sturgeon').onclick=()=>busy($('prepare-sturgeon'),async()=>{
  if(!$('license').checked)throw Error('Read the linked Sturgeon license and explicitly acknowledge it before downloading.');
  $('sturgeon-download-status').textContent='Downloading or verifying the pretrained general model and hg38 probes. Keep this page open…';
  try{
    const resources=await api('/api/sturgeon-resources',{accept_sturgeon_license:true});
    for(const key of ['sturgeon_model','sturgeon_probes','sturgeon_executable'])if(resources[key])$(key).value=resources[key];
    if(cpgContinuation)for(const key of ['sturgeon_model','sturgeon_probes','sturgeon_executable'])if(resources[key]){
      cpgContinuation.missing_resources.sturgeon=(cpgContinuation.missing_resources.sturgeon||[]).filter(item=>item!==key);
    }
    $('sturgeon-download-status').textContent='Model archive and hg38 probes verified. Paths are filled in; Save/check validates the complete analysis before Run.';
    updateCpgRequirements();invalidate();
  }catch(error){$('sturgeon-download-status').textContent='Preparation failed; no analysis started. '+error.message;throw error;}
});
'''
    page = page.replace("(async()=>{try{if(!token)", script + "\n(async()=>{try{if(!token)")
    page = page.replace("prepared=await api('/api/prepare',data);", "prepared=await api('/api/prepare',data);if(prepared.kind==='existing_project'){showResumedProject();return;}", 1)
    page = page.replace('const defaults=system.defaults||{};', 'const defaults=system.defaults||{};entryDefaults=defaults;')
    page = page.replace('if(defaults.mode&&variantSpecimen){', "if(analysisEntry==='new'&&defaults.mode&&variantSpecimen){")
    page = page.replace('function showReviewedRun(job){', "function showReviewedRun(job){\n  setAnalysisEntry('active');")
    page = page.replace('function initializeCpgContinuation(value){', "function initializeCpgContinuation(value){\n  setAnalysisEntry('cpg');")
    page = page.replace('function returnToSetup(){', "function returnToSetup(){\n  setAnalysisEntry(cpgContinuation?'cpg':'new');")
    page = page.replace("if(system.cpg)initializeCpgContinuation(system.cpg);",
                        "$('resume-image').dataset.current=system.default_docker_image||'';$('resume-image').placeholder=system.default_docker_image||'';if(['conda','docker'].includes(defaults.backend))$('resume-backend').value=defaults.backend;if(defaults.image)$('resume-image').value=defaults.image;show('resume-image-field',$('resume-backend').value!=='conda');if(system.resume_project){$('resume-project').value=system.resume_project;$('resume-note').textContent='Existing project detected. Load its saved settings above to continue.';}if(system.cpg)initializeCpgContinuation(system.cpg);")
    return page
