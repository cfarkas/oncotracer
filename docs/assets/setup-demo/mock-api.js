/* Browser-only synthetic service. This replaces fetch; it never forwards requests. */
'use strict';
const demoState={scan:null,prepared:null,job:null,revision:0,requests:[],lastPayload:null};
const demoGiB=1073741824;
const demoPaths={illumina:'/demo/illumina/fastq',ont:'/demo/nanopore/fastq_pass'};
function demoFixture(platform){
  const names=['SYNTHETIC_TUMOR_01','SYNTHETIC_CONTROL_01','SYNTHETIC_TUMOR_02'];
  return names.map((name,id)=>{
    const barcode=platform==='ont'?'barcode'+String(id+1).padStart(2,'0'):'';
    const files=platform==='ont'?[1,2,3].map(n=>`${demoPaths.ont}/${barcode}/synthetic_batch_${n}.fastq.gz`):[1,2].map(n=>`${demoPaths.illumina}/${name}_R${n}.fastq.gz`);
    return {id,name,barcode,files,file_count:files.length,layout:platform==='ont'?'ONT batches':'paired-end'};
  });
}
function demoAssertPath(path){if(typeof path!=='string'||!(path==='/demo'||path.startsWith('/demo/')))throw Error('This demo only contains the fictional /demo folders. Load an example or browse its synthetic folders.');}
function demoYaml(payload){
  // Mirror WebState.prepare + setup._command_setup's flat native configuration.
  // No executable/resource inspection or file writing happens in this preview.
  const platform=demoState.scan.mode;
  const role=sample=>sample.type==='custom'?sample.role:sample.type==='normal'?'normal':'tumor';
  const selected=payload.samples.map(sample=>({...sample,analysis_role:role(sample),source:demoState.scan.samples.find(found=>found.id===sample.id)}));
  const study=selected.filter(sample=>sample.analysis_role==='tumor');
  const controls=selected.filter(sample=>sample.analysis_role==='normal');
  let referenceRoot=payload.project+'/reference';
  if(payload.reference==='reuse'){
    referenceRoot=payload.reference_path.replace(/\/$/,'');
    if(referenceRoot.endsWith('/references/samurai_hg38'))referenceRoot=referenceRoot.slice(0,-'/references/samurai_hg38'.length);
  }
  const projectParent=payload.project.slice(0,payload.project.lastIndexOf('/'));
  const cfg={mode:platform,lpwgs_root:referenceRoot,hg38_auto_download:payload.reference==='download',reference_download_cache:projectParent+'/.oncotracer-reference-downloads',outdir:payload.project+'/results',threads:payload.threads,force:false,run_cna_classifier:Boolean(payload.reports)&&payload.analysis!=='methylation',knowledge_web:false};
  if(platform==='illumina'){
    cfg.illumina_samplesheet=payload.project+'/config/samplesheet.csv';
    cfg.illumina_caller='qdnaseq';cfg.illumina_binsize_kb=payload.binsize;
  }else{
    cfg.ont_folder=demoState.scan.root;
    cfg.ont_barcodes=study.map(sample=>sample.source.barcode||'.').join(',');
    cfg.ont_sample_names=study.map(sample=>sample.name).join(',');
    cfg.ont_caller=controls.length?'qdnaseq':payload.caller;
    cfg.ont_binsize_kb=cfg.ont_caller==='ichorcna'?500:payload.binsize;
    if(controls.length){
      cfg.ont_normal_folder=demoState.scan.root;
      cfg.ont_normal_barcodes=controls.map(sample=>sample.source.barcode).join(',');
      cfg.ont_normal_sample_names=controls.map(sample=>sample.name).join(',');
    }
    if(cfg.ont_caller==='qdnaseq')cfg.ont_analysis_type='solid_biopsy';
  }
  if(cfg.run_cna_classifier){
    const online=payload.report_detail==='literature';
    Object.assign(cfg,{cna_classifier_sample_set:payload.report_context||'broad_cancer',cna_classifier_samples:selected.map(sample=>sample.name).join(','),knowledge_web:online,knowledge_literature_llm:online,knowledge_deep_literature:online,knowledge_deep_enable_llm_ranker:online,pathology_use_biomed_models:false,knowledge_catalog_llm:payload.report_detail==='models',run_gistic:Boolean(payload.gistic),gistic_required:Boolean(payload.gistic),knowledge_llm_threads:Math.min(payload.threads,4)});
  }
  cfg.run_variants=Boolean(payload.variants);
  if(payload.variants)for(const [key,value]of Object.entries(payload))if(key.startsWith('variant_')&&value!==undefined&&value!=='')cfg[key]=value;
  cfg.execution_backend=payload.backend;
  if(payload.backend==='docker'&&payload.docker_image)cfg.docker_image=payload.docker_image;
  if(payload.analysis!=='cna'){
    Object.assign(cfg,{methylation:true,methylation_only:payload.analysis==='methylation',methylation_classifier:payload.classifier,methylation_gpu:payload.device==='gpu'});
    cfg[payload.methylation_source==='pod5'?'methylation_pod5_dir':'methylation_modbam']=payload.methylation_path;
    Object.assign(cfg,payload.resource_paths||{});
    if(payload.classifier==='sturgeon'&&payload.accept_sturgeon_license)cfg.sturgeon_license_acknowledged=true;
  }
  cfg.sample_metadata=payload.project+'/config/sample_metadata.csv';
  const lines=['# ILLUSTRATIVE NATIVE CONFIGURATION — browser simulation only.','# Uses the real setup field names; no files have been written or validated.','# The paths, reads and resources are fictional and this is not a runnable project.'];
  if(payload.reference==='reuse')lines.push('# Reference choice: reuse the synthetic prepared reference parent.');
  else if(payload.reference==='build')lines.push('# Reference choice: build indexes locally at run time (not performed here).');
  else lines.push('# Reference choice: download prepared indexes at run time (not performed here).');
  if(payload.analysis!=='cna')lines.push('# Methylation resource hashes and interface identity require real resource checks.');
  for(const [key,value]of Object.entries(cfg))lines.push(key+': '+JSON.stringify(value));
  const csv=value=>'"'+String(value).replaceAll('"','""')+'"';
  if(platform==='illumina'){
    lines.push('','# Illustrative companion config/samplesheet.csv (not written):','# sample,fastq_1,fastq_2,status');
    for(const sample of selected)lines.push('# '+[sample.name,sample.source.files[0],sample.source.files[1]||'',sample.analysis_role].map(csv).join(','));
  }
  lines.push('','# Illustrative companion config/sample_metadata.csv (not written):','# sample,sample_type,analysis_role,fastq_files');
  for(const sample of selected)lines.push('# '+[sample.name,sample.type==='custom'?sample.label:sample.type,sample.analysis_role,JSON.stringify(sample.source.files)].map(csv).join(','));
  lines.push('# Controls are independent samples; this configuration does not create matched tumor-normal pairs.');
  return lines.join('\n')+'\n';
}
function demoCheck(payload){
  const errors=[];
  if(!demoState.scan||payload.scan_id!==demoState.scan.scan_id)errors.push('Discover a synthetic example first.');
  demoAssertPath(payload.project);
  if(!Number.isInteger(payload.threads)||payload.threads<1||payload.threads>16)errors.push('Choose between 1 and 16 simulated CPU threads.');
  const names=(payload.samples||[]).map(s=>s.name.trim());
  if(!names.length||names.some(n=>!n))errors.push('Assign at least one sample and give each sample a name.');
  if(new Set(names).size!==names.length)errors.push('Each sample needs a different name.');
  if(demoState.scan.mode==='ont'&&!(payload.samples||[]).some(s=>s.type==='cancer'||(s.type==='custom'&&s.role==='tumor')))errors.push('An ONT project needs at least one study sample; controls are analyzed independently.');
  if(payload.reference==='reuse'){demoAssertPath(payload.reference_path);}
  if(payload.variants&&payload.variant_specimen_type==='ffpe'&&demoState.scan.mode==='illumina'&&payload.variant_ffperase==='required'&&(!payload.variant_ffperase_root||!payload.variant_ffperase_models))errors.push('Choose synthetic FFPERASE source and model folders, or skip FFPERASE.');
  if(payload.variants&&payload.variant_varlociraptor==='required'&&!(Number(payload.variant_varlociraptor_fdr)>0&&Number(payload.variant_varlociraptor_fdr)<1))errors.push('Local false discovery rate must be greater than zero and less than one.');
  if(payload.backend==='docker'&&payload.analysis!=='cna')errors.push('Docker supports CNA with optional variants; methylation requires a supported host backend.');
  return {errors,warnings:['Simulated checks only: no files, tools, reference assembly, model compatibility or annotation database have been inspected.'],plan:{samples:names}};
}
function demoJobStatus(){
  if(!demoState.job)return {status:'idle',log:''};
  const job=demoState.job;
  const elapsed=(Date.now()-job.started)/1000;
  const stages=['Read synthetic configuration','Simulate alignment','Simulate copy-number analysis',...(demoState.lastPayload?.variants?['Simulate variant calling','Simulate evidence filtering and annotation']:[]),'Prepare illustrative results'];
  const step=Math.min(stages.length-1,Math.floor(elapsed/1.8));
  if(job.status==='running'&&elapsed>=stages.length*1.8)job.status='complete';
  const complete=job.status==='complete';
  const visited=complete?stages:stages.slice(0,step+1);
  return {status:job.status,project_id:demoState.prepared.id,project_path:demoState.prepared.project,can_remove:false,exit_code:complete?0:null,log_path:'Simulation only — no log file exists',results_url:complete?'#demo-results':null,log_truncated:false,
    log:'SIMULATION — no sequencing reads were processed.\n'+visited.map((s,i)=>`[DEMO ${i+1}/${stages.length}] ${s}`).join('\n')+(complete?'\nDEMO COMPLETE: all displayed results are illustrative.':''),
    progress:{elapsed_seconds:Math.round(elapsed),eta_seconds:complete?0:2,overall_eta_seconds:complete?0:Math.max(1,Math.ceil(stages.length*1.8-elapsed)),stage:complete?'Simulation complete':stages[step],percent:complete?100:Math.min(99,Math.floor(elapsed/(stages.length*1.8)*100)),note:'Animation timing is illustrative and is not an estimate of real analysis runtime.'}};
}
function demoBrowse(path){
  path=(path||'/demo').replace(/\/$/,'')||'/demo';
  if(path==='/')path='/demo';
  demoAssertPath(path);
  const tree={
    '/demo':['illumina','nanopore','projects','resources'],
    '/demo/illumina':['fastq'],
    '/demo/nanopore':['fastq_pass','pod5_pass','bam_pass'],
    '/demo/nanopore/fastq_pass':['barcode01','barcode02','barcode03'],
    '/demo/resources':['hg38','clair3-model','ffperase','annovar'],
    '/demo/resources/ffperase':['models'],
    '/demo/resources/annovar':['humandb']
  };
  const fastqs=[...demoFixture('illumina'),...demoFixture('ont')].flatMap(s=>s.files).filter(f=>f.slice(0,f.lastIndexOf('/'))===path);
  const files=path==='/demo/nanopore/bam_pass'?[{name:'SYNTHETIC_RUN.mod.bam',path:path+'/SYNTHETIC_RUN.mod.bam'}]:[];
  return {path,parent:path==='/demo'?'/demo':path.slice(0,path.lastIndexOf('/'))||'/demo',directories:(tree[path]||[]).map(name=>({name,path:path+'/'+name})),files,fastq_entries:fastqs.map(path=>({name:path.split('/').pop(),path})),fastq_files:fastqs.length,pod5_files:path.endsWith('/pod5_pass')?3:0,bam_files:files.length,truncated:false};
}
const demoInstallGuides=__VARIANT_INSTALL_GUIDES__;
function demoVariantResources(payload){
  const docker=payload.backend==='docker',values=payload.values||{},fields={},resources=[];
  const found=(id,label,key,path,status='found',detail='Synthetic example path; no files were inspected.')=>{fields[key]=values[key]||path;resources.push({id,label,status,path:fields[key],detail});};
  if(docker)resources.push({id:'variant_tools',label:'Caller tools in Docker',status:'unverified',path:payload.docker_image,detail:'The real app checks container tools during preflight. This demo does not run Docker.'});
  else found('variant_tools','Variant tool environment','variant_tool_prefix','/demo/tools/oncotracer-variants');
  if(payload.mode==='ont'&&payload.callers.includes('clair3'))found('clair3','Clair3 model','variant_clair3_model','/demo/resources/clair3-model','candidate','Check sequencing chemistry and basecaller compatibility; a folder name alone cannot verify a model.');
  if(payload.mode==='illumina'&&payload.specimen_type==='ffpe'&&values.variant_ffperase==='required'){
    found('ffperase_source','FFPERASE source','variant_ffperase_root','/demo/resources/ffperase');
    found('ffperase_models','FFPERASE models','variant_ffperase_models','/demo/resources/ffperase/models','candidate');
    if(!docker)found('ffperase_runtime','FFPERASE environment','variant_ffperase_prefix','/demo/tools/oncotracer-ffperase');
  }
  const guides=[];
  if(values.variant_annovar!=='off'){
    resources.push({id:'annovar',label:'ANNOVAR and local databases',status:'missing',detail:'This example deliberately leaves optional ANNOVAR unavailable so you can explore the installation codebox. Manually entered paths are kept.'});
    guides.push(...demoInstallGuides[docker?'docker':'host']);
  }
  return {backend:payload.backend,fields,resources,install_guides:guides,searched:['/demo/tools','/demo/resources'],notes:['Synthetic resource discovery only. Your computer has not been inspected.']};
}
function demoApi(path,payload){
  const url=new URL(path,location.href);
  switch(url.pathname){
    case '/api/system':return {hardware:{cpu_workers_available:16,ram_available_bytes:48*demoGiB,ram_total_bytes:64*demoGiB,gpus:[],gpu_note:'Fictional demo hardware; your computer has not been inspected.'},suggested_threads:8,start_dir:'/demo',qdnaseq_binsizes:[1,5,10,15,30,50,100,500,1000],locations:[{name:'Synthetic files',path:'/demo'},{name:'Illumina',path:demoPaths.illumina},{name:'Nanopore',path:demoPaths.ont},{name:'Resources',path:'/demo/resources'}],defaults:{reference:'reuse',reference_path:'/demo/resources/hg38',backend:'docker',image:'carlosfarkas/oncotracer:fastq-variants-20260921'}};
    case '/api/variant-resources':return demoVariantResources(payload);
    case '/api/browse':return demoBrowse(url.searchParams.get('path'));
    case '/api/scan':{
      demoAssertPath(payload.folder);
      if(!['ont','illumina'].includes(payload.mode))throw Error('Choose Illumina or Oxford Nanopore.');
      const fixture=demoFixture(payload.mode);
      demoState.scan={scan_id:'synthetic-scan-'+(++demoState.revision),mode:payload.mode,root:demoPaths[payload.mode],warnings:['Synthetic FASTQ inventory — no real folders were scanned.'],samples:fixture};
      return demoState.scan;
    }
    case '/api/ont-inputs':return {run:'/demo/nanopore',fastq:demoPaths.ont,pod5:'/demo/nanopore/pod5_pass',modbam:'/demo/nanopore/bam_pass'};
    case '/api/prepare':{
      const check=demoCheck(payload);demoState.lastPayload=structuredClone(payload);demoState.job=null;
      demoState.prepared={id:'synthetic-project-'+(++demoState.revision),project:payload.project,config_path:payload.project+'/config/run.yml (preview only)',config:demoYaml(payload),backend:payload.backend,valid:check.errors.length===0,check,outdir:payload.project+'/results'};
      return demoState.prepared;
    }
    case '/api/run':
      if(!demoState.prepared?.valid||payload.project_id!==demoState.prepared.id)throw Error('Preview and check your synthetic configuration first.');
      demoState.job={status:'running',started:Date.now()};return demoJobStatus();
    case '/api/status':return demoJobStatus();
    case '/api/stop':if(demoState.job)demoState.job.status='stopped';return demoJobStatus();
    case '/api/remove-project':throw Error('No project exists on disk. Use Reset demo to clear the page.');
    default:throw Error('This operation is not part of the browser-only demo.');
  }
}
window.fetch=async function demoFetch(input,options={}){
  const path=typeof input==='string'?input:input.url;
  demoState.requests.push(new URL(path,location.href).pathname);
  try{return new Response(JSON.stringify(demoApi(path,options.body?JSON.parse(options.body):{})),{status:200,headers:{'Content-Type':'application/json'}});}
  catch(error){return new Response(JSON.stringify({error:error.message}),{status:400,headers:{'Content-Type':'application/json'}});}
};
