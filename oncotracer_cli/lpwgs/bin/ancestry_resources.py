"""Version-pinned public resources, downloaded locally without uploading genotypes."""
import fcntl
import json
import subprocess
import tarfile
from pathlib import Path
from extension_common import sha256
from glimpse_download import download


def prepare(config):
    manifest=json.loads(Path(config['ancestry_explorer_manifest']).read_text())
    if manifest['genome_build']!='GRCh38':raise ValueError('Ancestry resources must use GRCh38')
    if manifest.get('frequency_allele_column')!='a2':raise ValueError('This ancestry model requires A2 frequency orientation')
    cache=Path(config['ancestry_explorer_cache']);cache.mkdir(parents=True,exist_ok=True)
    files={}
    for name,item in manifest['resources'].items():
        path=cache/item['filename']
        if not path.exists():download(item['url'],path)
        if sha256(path)!=item['sha256']:raise ValueError('Pinned ancestry resource checksum mismatch: '+str(path))
        files[name]=path
    home=cache/'haplogrep3-linux'
    with open(cache/'haplogrep.install.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not (home/'haplogrep3.jar').is_file():
            home.mkdir(exist_ok=True)
            with tarfile.open(files['haplogrep']) as archive:archive.extractall(home,filter='data')
        # Explicit local tree; never invoke the web upload service.
        tree=home/'trees/phylotree-rcrs/17.3'
        if not (tree/'tree.xml').is_file():
            tree.mkdir(parents=True,exist_ok=True)
            import zipfile
            with zipfile.ZipFile(files['mitotree']) as archive:
                for entry in archive.infolist():
                    target=(tree/entry.filename).resolve()
                    if not target.is_relative_to(tree.resolve()):raise ValueError('Unsafe tree archive path')
                archive.extractall(tree)
        (home/'haplogrep3.yaml').write_text(json.dumps({'repositories':[], 'phylotrees':[str((tree/'tree.yaml').resolve())]}))
    files['haplogrep_jar']=home/'haplogrep3.jar';files['tree_dir']=tree
    if sha256(files['haplogrep_jar'])!=manifest['haplogrep_jar_sha256']:raise ValueError('Installed Haplogrep JAR checksum mismatch')
    import zipfile,hashlib
    with zipfile.ZipFile(files['mitotree']) as archive:
        for entry in archive.infolist():
            if not entry.is_dir() and sha256(tree/entry.filename)!=hashlib.sha256(archive.read(entry)).hexdigest():
                raise ValueError('Installed mitochondrial tree checksum mismatch: '+entry.filename)
    # Tree and jar provenance includes the bytes actually executed/read.
    manifest['installed']={'jar_sha256':sha256(files['haplogrep_jar']),
        'tree_sha256':{p.name:sha256(p) for p in sorted(tree.iterdir()) if p.is_file()}}
    return files,manifest
