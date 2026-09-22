/* Guided controls around the actual local-app interface. All values are fictional. */
function demoMessage(message){document.getElementById('demo-status').textContent=message;}
function demoJump(id){const section=document.getElementById(id);if(section&&!section.hidden)section.scrollIntoView({behavior:'smooth',block:'start'});}
function demoRestoreControls(){
  clearTimeout(pollTimer);demoState.job=null;activeJob=null;cleanupShown=null;
  for(const field of document.querySelectorAll('main button,main input,main select'))field.disabled=false;
  for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
  document.querySelector('main').inert=false;
  for(const id of ['review-card','run-progress','logs','new-analysis','stop','demo-results'])show(id,false);
  $('run').hidden=false;
}
async function demoLoad(platform){
  if(operationBusy)return;
  demoRestoreControls();choose(platform);
  $('input-folder').value=demoPaths[platform];$('project-parent').value='/demo/projects';$('project-name').value=platform==='illumina'?'synthetic-illumina-ffpe':'synthetic-nanopore-fresh';
  $('threads').value='8';$('backend').value='docker';$('docker_image').value='carlosfarkas/oncotracer:fastq-variants-20260922';$('reference').value='reuse';$('reference-path').value='/demo/resources/hg38';$('reference').onchange();
  $('variants').checked=true;variantSpecimen=platform==='illumina'?'ffpe':'fresh';variantSettings();
  const selected=platform==='illumina'?['mutect2','bcftools']:['clairs_to'];
  for(const field of document.querySelectorAll('[data-variant-caller]'))field.checked=selected.includes(field.value);
  $('variant_clair3_model').value='auto';$('variant_ont_profile').selectedIndex=0;$('variant-clairsto-preset').selectedIndex=0;$('variant_clairsto_platform').value=$('variant-clairsto-preset').value;
  $('variant_download_resources').checked=true;$('variant_accept_ffperase_license').checked=false;$('variant-scenario-mode').value='standard';$('variant_varlociraptor_scenario').value='';
  $('variant_ffperase').value='required';$('variant_ffperase_root').value='/demo/resources/ffperase';$('variant_ffperase_models').value='/demo/resources/ffperase/models';
  $('variant_varlociraptor').value='required';$('variant_varlociraptor_fdr').value='0.05';$('variant_annovar').value='auto';$('variant_annovar_dir').value='';$('variant_annovar_db').value='';
  $('reports').checked=false;$('gistic').checked=false;methylationSettings();
  await $('scan').onclick();
  demoMessage((platform==='illumina'?'Illumina FFPE':'Nanopore Fresh')+' example loaded. Assign cards with the type dropdown or drag them into Normal and Cancer.');
  demoJump('samples-card');
}
function demoAssign(){
  if(!scan){demoMessage('Load an Illumina or Nanopore example first.');return;}
  demoRestoreControls();
  for(const row of sampleRows())moveSample(row,row.querySelector('.sample-name').value.includes('CONTROL')?'normal':'cancer');
  demoMessage('Example groups assigned: two synthetic cancer samples and one synthetic control. You can change every assignment.');
}
function demoRenderResults(){
  const payload=demoState.lastPayload;if(!payload)return;
  const list=$('demo-result-summary');list.replaceChildren();
  for(const [term,value]of [['Platform',demoState.scan.mode==='illumina'?'Illumina':'Oxford Nanopore'],['Samples',payload.samples.map(s=>s.name+' ('+s.type+')').join(', ')],['Preservation',payload.variant_specimen_type||'Not selected'],['Backend',payload.backend],['Variant callers',payload.variants?payload.variant_callers:'Not selected'],['Artifact assessment',payload.variant_ffperase==='required'?'FFPERASE selected; no model was run':'Not selected'],['Evidence filtering',payload.variant_varlociraptor==='required'?'Varlociraptor selected; no evidence was calculated':'Not selected'],['Annotation',payload.variant_annovar==='auto'?'ANNOVAR selected; no installation was inspected':'Not selected']]){list.append(node('dt',term),node('dd',value));}
  show('demo-results',true);
}
document.addEventListener('DOMContentLoaded',()=>{
  $('demo-load-illumina').onclick=()=>demoLoad('illumina');$('demo-load-ont').onclick=()=>demoLoad('ont');
  $('demo-reset').onclick=()=>{demoRestoreControls();demoState.scan=null;demoState.prepared=null;demoState.lastPayload=null;$('new-analysis').onclick();demoMessage('Demo reset. Load a synthetic example to begin.');demoJump('platform-card');};
  $('demo-assign').onclick=()=>{demoAssign();demoJump('samples-card');};
  for(const button of document.querySelectorAll('[data-demo-stage]'))button.onclick=()=>{if(button.dataset.demoStage!=='platform-card'&&!scan){demoMessage('Load a synthetic example to reveal these stages.');return;}demoJump(button.dataset.demoStage);};
  $('demo-review').onclick=async()=>{if(!scan){demoMessage('Load a synthetic example first.');return;}if(!selectedRows().length)demoAssign();await $('prepare').onclick();if(prepared?.valid){$('config-preview').closest('details').open=true;demoMessage('Configuration preview ready. The checks and Run button are simulated.');}};
  $('existing-bam-link').textContent='Learn about calling variants from existing BAMs →';$('existing-bam-link').href='../../variants/';
  $('open-results').removeAttribute('target');$('open-results').onclick=event=>{event.preventDefault();demoRenderResults();demoJump('demo-results');$('demo-results-heading').focus({preventScroll:true});};
  $('remove-project').hidden=true;
  const simulated=new MutationObserver(()=>{if(demoState.job?.status==='complete')demoRenderResults();});simulated.observe($('job-status'),{childList:true,characterData:true,subtree:true});
  $('choose-illumina').addEventListener('click',()=>{$('input-folder').value=demoPaths.illumina;});
  $('choose-ont').addEventListener('click',()=>{$('input-folder').value=demoPaths.ont;});
});
