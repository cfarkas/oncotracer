/* User-selected folder metadata only. No file contents are read or uploaded. */
const demoComputerRoot='/computer';
let demoComputerFolders=new Map();
function demoComputerListing(path){
  return {path,parent:path===demoComputerRoot?demoComputerRoot:path.slice(0,path.lastIndexOf('/')),
    breadcrumb_root:demoComputerRoot,local_preview:true,directories:[],files:[],fastq_entries:[],
    fastq_files:0,pod5_files:0,bam_files:0,truncated:false};
}
function demoComputerFile(listing,name){
  let entries;
  if(/\.(fastq|fq)(\.gz)?$/i.test(name)){listing.fastq_files++;entries=listing.fastq_entries;}
  else if(/\.pod5$/i.test(name)){listing.pod5_files++;entries=listing.files;}
  else if(/\.bam$/i.test(name)){listing.bam_files++;entries=listing.files;}
  else return;
  if(entries.length<1000)entries.push({name,path:listing.path+'/'+name});else listing.truncated=true;
}
function demoComputerDirectory(folders,path,handle=null){
  if(folders.has(path))return folders.get(path);
  const listing=demoComputerListing(path);folders.set(path,{listing,handle});
  if(path!==demoComputerRoot){
    const parent=demoComputerDirectory(folders,listing.parent).listing;
    if(parent.directories.length<1000)parent.directories.push({name:path.slice(path.lastIndexOf('/')+1),path});
    else parent.truncated=true;
  }
  return folders.get(path);
}
async function demoBrowseComputerPath(path){
  const folders=demoComputerFolders,entry=folders.get(path);
  if(!entry)throw Error('Folder is not available. Choose Browse computer and select it again.');
  if(entry.handle){
    // Enumerate only the opened directory, so large runs remain navigable.
    const listing=demoComputerListing(path);
    for await(const child of entry.handle.values()){
      if(child.kind==='directory'){
        if(listing.directories.length<1000){
          const childPath=path+'/'+child.name;
          listing.directories.push({name:child.name,path:childPath});
          folders.set(childPath,{listing:demoComputerListing(childPath),handle:child});
        }else listing.truncated=true;
      }else demoComputerFile(listing,child.name);
    }
    entry.listing=listing;
  }
  for(const key of ['directories','files','fastq_entries'])entry.listing[key].sort((a,b)=>a.name.localeCompare(b.name));
  return entry.listing;
}
async function demoComputerFiles(files){
  // Copy names only; do not retain File objects or pretend these are absolute paths.
  const folders=new Map();let selected=null;
  for(const file of files){
    const parts=file.webkitRelativePath.split('/');
    if(parts.length<2||parts.some(part=>!part||part==='.'||part==='..'))continue;
    selected=selected||demoComputerRoot+'/'+parts[0];
    const name=parts.pop(),path=demoComputerRoot+'/'+parts.join('/');
    demoComputerFile(demoComputerDirectory(folders,path).listing,name);
  }
  if(!selected){
    // An empty FileList exposes no folder name; never reuse a previous listing.
    browseSequence++;currentFolder=null;parentFolder=null;demoComputerFolders.clear();
    $('folders').replaceChildren();$('browser-breadcrumbs').replaceChildren();$('browser-path').textContent='';$('browser-location').value='';
    $('folder-summary').textContent='0 sequencing files in the selected folder';$('use-folder').disabled=true;
    failure('No sequencing files found. Check your paths.','browser-error');return;
  }
  demoComputerFolders=folders;await browse(selected);
}
function demoInstallComputerBrowser(){
  const input=node('input');input.type='file';input.id='browser-computer-input';input.hidden=true;
  input.setAttribute('webkitdirectory','');input.multiple=true;$('browser').append(input);
  input.onchange=async()=>{try{await demoComputerFiles(input.files);}catch(error){failure(error,'browser-error');}finally{input.value='';}};
  $('browser-computer').onclick=async()=>{
    if(!window.showDirectoryPicker){input.value='';input.click();return;}
    const sequence=++browseSequence;
    try{
      const handle=await window.showDirectoryPicker({mode:'read'});
      if(sequence!==browseSequence||!$('browser').open)return;
      const folders=new Map(),path=demoComputerRoot+'/'+handle.name;
      demoComputerDirectory(folders,path,handle);demoComputerFolders=folders;await browse(path);
    }catch(error){
      if(sequence!==browseSequence||error.name==='AbortError')return;
      if(error.name==='SecurityError'){input.value='';input.click();return;}
      failure('Cannot open this folder. Check your paths and folder permissions.','browser-error');
    }
  };
}
document.addEventListener('DOMContentLoaded',demoInstallComputerBrowser);
