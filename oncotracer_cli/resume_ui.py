"""Browser controls for explicit project continuation and licensed model setup."""


def add_resume_ui(page):
    card = '''<section class="card" id="resume-card"><h2>Continue an existing analysis</h2>
<p>Choose the complete project folder containing <code>config/run.yml</code> and <code>results</code>, including after moving it. Saved samples and settings are retained. Stop its current run first.</p>
<label for="resume-project">Existing project folder</label><div class="row"><input id="resume-project"><button data-browse="resume-project">Browse</button><button id="resume-project-button">Load saved settings and check</button></div><p id="resume-note" class="hint">Analysis starts only after review and Run. Compatible completed stages are checked before reuse.</p></section>'''
    page = page.replace('<div id="error" role="alert" class="error" hidden></div>',
                        '<div id="error" role="alert" class="error" hidden></div>' + card)
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
                        "$('prepare').onclick=()=>busy($('prepare'),async()=>{if($('project-mode').value==='existing')return loadResume(projectDestination());if(!scan)")
    script = r'''
async function loadResume(project){
  if(!project.trim())throw Error('Choose the project folder containing config/run.yml and results.');
  prepared=await api('/api/resume',{project});
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
    page = page.replace("if(system.cpg)initializeCpgContinuation(system.cpg);",
                        "if(system.resume_project){$('resume-project').value=system.resume_project;$('resume-note').textContent='Existing project detected. Load its saved settings above to continue.';}if(system.cpg)initializeCpgContinuation(system.cpg);")
    return page
