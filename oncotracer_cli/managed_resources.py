"""Pinned Docker resources, with licensed software kept in the user's cache."""
from __future__ import annotations

import argparse
import gzip
import json
from importlib.resources import files
from functools import lru_cache
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile

from .runtime import (OncoTracerError, atomic_write_json, atomic_write_text, download,
                      exclusive_lock, sha256_file, utc_now)

BUNDLED_EXECUTABLES = {
    'methylation_samtools_executable': '/opt/conda/bin/samtools',
    'methylation_modkit_executable': '/opt/oncotracer-envs/marlin/bin/modkit',
    'marlin_rscript': '/opt/oncotracer-envs/marlin/bin/Rscript',
    'marlin_python': '/opt/oncotracer-envs/marlin/bin/python',
}
STURGEON_PYTHON = '/opt/oncotracer-envs/sturgeon-deps/bin/python'
MARLIN_BUNDLE = Path('/opt/oncotracer-resources/marlin')
MARLIN_PROBE_SHA = 'e595243efb5d9aac1a448bf930a4dec9db5585f2b652b36c79d2ff54099f16ca'


def assets():
    return json.loads(files('oncotracer_cli').joinpath('managed_assets.json').read_text(encoding='utf-8'))['assets']


def bundled_executable(key):
    from .docker_runtime import is_host_preview
    if key not in BUNDLED_EXECUTABLES:
        raise OncoTracerError('No bundled executable is defined for '+key)
    path=Path(BUNDLED_EXECUTABLES[key])
    if is_host_preview():
        return path
    if os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') != 'docker':
        raise OncoTracerError(key+': bundled tools require --backend docker. Use launch or configure an external executable for this backend.')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise OncoTracerError('Selected image lacks '+key+'. Use the OncoTracer v6 image or rebuild its managed runtimes.')
    return path


def _safe_directory(path):
    path=Path(path).expanduser().absolute()
    if path in (Path('/'), Path.home()) or any(p.is_symlink() for p in (path,*path.parents)):
        raise OncoTracerError('Use a dedicated physical resource cache: '+str(path))
    path.mkdir(parents=True,exist_ok=True)
    return path


PARALLEL_DOWNLOAD_MIN_BYTES = 64 * 1024 * 1024
BUNDLED_DOWNLOADER = Path('/opt/oncotracer-downloader/bin/aria2c')


def _parallel_fetch(item, target):
    """Resume large pinned assets with bounded connections when aria2 is present."""
    size=item.get('bytes');expected=item.get('sha256')
    if not size or size < PARALLEL_DOWNLOAD_MIN_BYTES or not expected:return False
    executable=str(BUNDLED_DOWNLOADER) if BUNDLED_DOWNLOADER.is_file() else shutil.which('aria2c')
    if executable is None:return False
    def verified(path):
        return path.is_file() and path.stat().st_size==size and sha256_file(path)==expected
    if verified(target):return True
    # Separate chunk state by the complete pinned digest. A model update cannot
    # resume chunks from an earlier asset with the same filename.
    stage=target.with_name('.'+target.name+'.parts-'+expected)
    candidate=stage/'payload';control=stage/'payload.aria2';lock=stage/'.lock'
    if any(p.is_symlink() for p in (stage,candidate,control,lock)):
        raise OncoTracerError('Parallel download state must not contain symlinks: '+str(stage))
    stage.mkdir(mode=0o700,exist_ok=True)
    with exclusive_lock(lock):
        if verified(target):return True
        command=[executable,'--no-conf=true','--check-certificate=true',
            '--auto-file-renaming=false','--allow-overwrite=true','--continue=true',
            '--file-allocation=none','--max-connection-per-server=8','--split=8',
            '--min-split-size=4M','--max-tries=5','--retry-wait=3',
            '--connect-timeout=20','--timeout=60','--lowest-speed-limit=1K',
            '--auto-save-interval=5','--enable-color=false','--show-console-readout=false','--summary-interval=30',
            '--console-log-level=warn','--download-result=hide',
            '--checksum=sha-256='+expected,'--dir='+str(stage),'--out=payload',item['url']]
        print('Downloading '+target.name+' with up to eight connections',flush=True)
        try:result=subprocess.run(command,check=False)
        except OSError as error:
            print('Parallel downloader unavailable; using the standard downloader: '+str(error),flush=True)
            return False
        # Independently verify bytes; a successful external exit is insufficient.
        if result.returncode==0 and verified(candidate):
            os.replace(candidate,target);control.unlink(missing_ok=True)
            return True
        if result.returncode==0:
            candidate.unlink(missing_ok=True);control.unlink(missing_ok=True)
        # Interrupted chunks stay in the isolated directory for the next retry.
        # The standard downloader has a separate contiguous-prefix partial file.
        print('Parallel download did not verify; using the standard downloader',flush=True)
        return False


def _clear_parallel_partial(item, target):
    stage=target.with_name('.'+target.name+'.parts-'+item['sha256'])
    candidate=stage/'payload';control=stage/'payload.aria2';lock=stage/'.lock'
    if not stage.exists():return
    if any(p.is_symlink() for p in (stage,candidate,control,lock)):
        raise OncoTracerError('Parallel download state must not contain symlinks: '+str(stage))
    with exclusive_lock(lock):
        candidate.unlink(missing_ok=True);control.unlink(missing_ok=True)


def _fetch(name, folder, *, bundled=None):
    item=assets()[name]; target=folder/item['filename']
    if target.is_symlink():raise OncoTracerError('Resource file must not be a symlink: '+str(target))
    if bundled is not None and (bundled/item['filename']).is_file() and not target.exists():
        original=bundled/item['filename']
        if sha256_file(original)!=item['sha256']:
            raise OncoTracerError('Bundled resource failed checksum verification: '+name)
        temporary=target.with_name('.'+target.name+'.copy')
        shutil.copyfile(original,temporary);os.replace(temporary,target)
    print('Checking resource: '+name,flush=True)
    if _parallel_fetch(item,target):
        partial=target.with_name('.'+target.name+'.part')
        if partial.is_file() and not partial.is_symlink():partial.unlink()
        return target
    stopped=threading.Event()
    def progress():
        temporary=target.with_name('.'+target.name+'.part')
        while not stopped.wait(30):
            try:received=temporary.stat().st_size/(1024*1024)
            except FileNotFoundError:received=None
            if received is not None:
                total=item.get('bytes')
                detail=f"{received:.0f} MiB staged"+(f" / {total/(1024*1024):.0f} MiB" if total else "")
                print('Preparing '+name+': '+detail,flush=True)
            else:print('Preparing '+name+': verifying cache or waiting for the source',flush=True)
    thread=threading.Thread(target=progress,daemon=True);thread.start()
    try:
        verified=download(item['url'],target,expected_bytes=item.get('bytes'),expected_sha256=item['sha256'])
    finally:stopped.set();thread.join()
    partial=target.with_name('.'+target.name+'.part')
    if partial.is_file() and not partial.is_symlink():partial.unlink()
    _clear_parallel_partial(item,target)
    return verified



def _extract(archive, destination):
    """Extract authenticated archives without absolute paths or escaping links."""
    def relative(name):
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise OncoTracerError('Unsafe archive member: '+name)
        return path
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as stream:
            for member in stream.infolist():
                relative(member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise OncoTracerError('Symlinks are not accepted in model ZIP archives')
            stream.extractall(destination)
    else:
        with tarfile.open(archive) as stream:
            members=stream.getmembers();links=set()
            for member in members:
                path=relative(member.name)
                if not (member.isfile() or member.isdir() or member.issym()):
                    raise OncoTracerError('Unsupported archive member: '+member.name)
                if member.issym():
                    link=PurePosixPath(member.linkname)
                    # Upstream Dorado uses links within lib/, never parent traversals.
                    relative(member.linkname)
                    links.add(path)
            for member in members:
                if any(p in links for p in relative(member.name).parents):
                    raise OncoTracerError('Archive member is nested below a symbolic link')
            stream.extractall(destination, members=members, filter='data')


def _tree(root):
    result={}
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts or path.suffix=='.pyc' or path.name=='.oncotracer-install.json':continue
        relative=path.relative_to(root).as_posix()
        if path.is_symlink():result[relative]={'link':os.readlink(path)}
        elif path.is_file():result[relative]={'sha256':sha256_file(path)}
    return result


def _installed_tree(root, identity, install):
    """Publish a complete immutable installation; reuse only verified contents."""
    receipt=root/'.oncotracer-install.json'
    if root.is_symlink() or receipt.is_symlink():raise OncoTracerError('Managed installation cannot be a symlink')
    if receipt.is_file():
        record=json.loads(receipt.read_text())
        if record.get('identity')!=identity or not root.is_dir() or _tree(root)!=record.get('files'):
            raise OncoTracerError('Managed installation changed: '+str(root)+'. Restore it or choose a new cache.')
        return root
    if root.exists():raise OncoTracerError('Unverified installation exists: '+str(root)+'. Choose a new cache; existing files are preserved.')
    with tempfile.TemporaryDirectory(prefix='.'+root.name+'-',dir=root.parent) as temporary:
        stage=Path(temporary)/'installation'
        install(stage)
        manifest=_tree(stage)
        if not manifest:raise OncoTracerError('Resource installation produced no files')
        atomic_write_json(stage/'.oncotracer-install.json',dict(identity=identity,files=manifest,prepared_at=utc_now()))
        os.replace(stage,root)
    return root


@lru_cache(maxsize=32)
def validate_installation(manifest_path, expected_sha256, asset_name):
    """Authenticate an immutable managed installation once per planning/run process."""
    manifest=Path(manifest_path)
    if manifest.is_symlink() or not manifest.is_file() or sha256_file(manifest)!=expected_sha256:
        raise OncoTracerError('Managed runtime receipt changed: '+str(manifest))
    record=json.loads(manifest.read_text())
    if record.get('identity')!=assets()[asset_name]['sha256'] or _tree(manifest.parent)!=record.get('files'):
        raise OncoTracerError('Managed runtime files changed: '+str(manifest.parent)+'. Restore the recorded installation or prepare a new cache.')
    return expected_sha256


def _executable(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    atomic_write_text(path,text);path.chmod(0o755)
    return str(path)


def prepare_marlin(root, *, bundled=MARLIN_BUNDLE):
    root=_safe_directory(root)
    values={}
    for key in ('marlin_model','marlin_features','marlin_class_annotations'):
        path=_fetch(key,root,bundled=bundled)
        values[key]=str(path);values[key+'_sha256']=assets()[key]['sha256']
    compressed=_fetch('marlin_probe_gz',root,bundled=bundled)
    probes=root/'marlin_v1.probes_hg38.bed'
    if not probes.is_file() or sha256_file(probes)!=MARLIN_PROBE_SHA:
        temporary=probes.with_suffix('.bed.partial')
        with gzip.open(compressed,'rb') as source, temporary.open('wb') as output:shutil.copyfileobj(source,output)
        if sha256_file(temporary)!=MARLIN_PROBE_SHA:raise OncoTracerError('MARLIN decompressed probes failed checksum verification')
        os.replace(temporary,probes)
    values.update(marlin_probe_bed=str(probes),marlin_probe_bed_sha256=MARLIN_PROBE_SHA,
                  marlin_rscript='bundled',marlin_python='bundled')
    return values


def prepare_dorado(root, *, modbase=False):
    root=_safe_directory(root);archives=_safe_directory(root/'downloads')
    archive=_fetch('dorado',archives)
    def install(stage):
        stage.mkdir()
        _extract(archive,stage)
    tree=_installed_tree(root/'runtime',assets()['dorado']['sha256'],install)
    binary=tree/'dorado-1.1.1-linux-x64/bin/dorado'
    if sha256_file(binary)!=assets()['dorado']['executable_sha256']:
        raise OncoTracerError('Dorado executable does not match the pinned public release')
    wrapper=_executable(root/'bin/dorado','#!/bin/sh\nset -eu\n'
        'if [ "${ONCOTRACER_CONTAINER_RUNTIME:-}" != docker ]; then\n'
        '  echo "This managed Dorado runtime requires the OncoTracer Docker image" >&2; exit 2\nfi\n'
        'export LD_PRELOAD="/opt/conda/lib/libstdc++.so.6${LD_PRELOAD:+:$LD_PRELOAD}"\n'
        'exec '+shlex.quote(str(binary))+' "$@"\n')
    version=subprocess.run([wrapper,'--version'],check=True,capture_output=True,text=True)
    if assets()['dorado']['version'] not in version.stdout+version.stderr:
        raise OncoTracerError('Unexpected public Dorado build identifier')
    model_archive=_fetch('dorado_model',archives)
    def model_install(stage):
        stage.mkdir();_extract(model_archive,stage)
    model_tree=_installed_tree(root/'models',assets()['dorado_model']['sha256'],model_install)
    model=model_tree/'dna_r10.4.1_e8.2_400bps_sup@v5.2.0'
    from .methylation import directory_sha256
    receipt=tree/'.oncotracer-install.json'
    values=dict(methylation_dorado_executable=wrapper,methylation_dorado_model=str(model),
                methylation_dorado_runtime_manifest=str(receipt),methylation_dorado_runtime_manifest_sha256=sha256_file(receipt),
                methylation_dorado_model_sha256=directory_sha256(model))
    if modbase:
        mod_archive=_fetch('dorado_modbase_model',archives)
        def modified_install(stage):
            stage.mkdir();_extract(mod_archive,stage)
        tree=_installed_tree(root/'modified-models',assets()['dorado_modbase_model']['sha256'],modified_install)
        model=tree/'dna_r10.4.1_e8.2_400bps_sup@v5.2.0_5mCG_5hmCG@v2'
        values.update(methylation_dorado_modbase_model=str(model),methylation_dorado_modbase_model_sha256=directory_sha256(model))
    return values


def prepare_private_sturgeon(root, *, acknowledged):
    from .sturgeon_resources import LICENSE_URL,LICENSE_TEXT_URL,validate_model
    if not acknowledged:
        raise OncoTracerError('Review '+LICENSE_URL+' and repeat with --accept-sturgeon-license to prepare CNS classification.')
    root=_safe_directory(root); downloads=_safe_directory(root/'downloads')
    source=_fetch('sturgeon_source',downloads)
    model=_fetch('sturgeon_model',root);validate_model(model)
    probes=_fetch('sturgeon_probes',root)
    license_file=download(LICENSE_TEXT_URL,root/'LICENSE.txt')
    def install(stage):
        with tempfile.TemporaryDirectory(prefix='.source-',dir=root) as temporary:
            _extract(source,Path(temporary))
            directory=Path(temporary)/'sturgeon-4c742ddea49b0077a8f8ff3d99daafb238d00706'
            subprocess.run([STURGEON_PYTHON,'-m','pip','install','--no-deps','--no-build-isolation',
                            '--no-compile','--target',str(stage),str(directory)],check=True)
    software=_installed_tree(root/'software',assets()['sturgeon_source']['sha256'],install)
    prefix='#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nexport PYTHONPATH='+shlex.quote(str(software))+'\n'
    python=_executable(root/'bin/python',prefix+'exec '+STURGEON_PYTHON+' "$@"\n')
    executable=_executable(root/'bin/sturgeon',prefix+'exec '+STURGEON_PYTHON+' -c '+shlex.quote('from sturgeon.main import run; run()')+' "$@"\n')
    # The private runtime must import without borrowing any host environment.
    subprocess.run([python,'-c','import sturgeon.utils, onnxruntime; print("STURGEON_RUNTIME_OK")'],check=True)
    receipt=root/'license-acknowledgement.json'
    if not receipt.exists():
        atomic_write_json(receipt,dict(license_url=LICENSE_URL,license_sha256=sha256_file(license_file),acknowledged=True,acknowledged_at=utc_now()))
    runtime_manifest=software/'.oncotracer-install.json'
    return dict(sturgeon_executable=executable,sturgeon_python=python,sturgeon_model=str(model),
                sturgeon_runtime_manifest=str(runtime_manifest),sturgeon_runtime_manifest_sha256=sha256_file(runtime_manifest),
                sturgeon_probes=str(probes),sturgeon_model_sha256=assets()['sturgeon_model']['sha256'],
                sturgeon_probes_sha256=assets()['sturgeon_probes']['sha256'],sturgeon_license_acknowledged=True,
                sturgeon_license_url=LICENSE_URL,sturgeon_resource_receipt=str(receipt))


def prepare(cache,classifiers,*,caller='rockfish',accept_sturgeon_license=False):
    if os.environ.get('ONCOTRACER_CONTAINER_RUNTIME')!='docker':
        raise OncoTracerError('Managed resource preparation runs inside the OncoTracer v6 image')
    if not set(classifiers).issubset({'marlin','sturgeon'}) or not classifiers:
        raise OncoTracerError('Select marlin and/or sturgeon')
    if caller not in ('rockfish','modkit'):raise OncoTracerError('Choose rockfish or modkit')
    root=_safe_directory(Path(cache)/'methylation')
    from .sturgeon_resources import LICENSE_URL
    if 'sturgeon' in classifiers and not accept_sturgeon_license:
        raise OncoTracerError('Review '+LICENSE_URL+' then pass --accept-sturgeon-license. No licensed files were downloaded.')
    with exclusive_lock(root/'.prepare.lock'):
        values={'methylation_samtools_executable':'bundled'}
        # Dorado also realigns existing modified BAMs for the Modkit branch.
        values.update(prepare_dorado(root/'dorado',modbase=caller=='modkit'))
        if caller=='modkit':values['methylation_modkit_executable']='bundled'
        if 'marlin' in classifiers:values.update(prepare_marlin(root/'marlin'))
        if 'sturgeon' in classifiers:values.update(prepare_private_sturgeon(root/'sturgeon',acknowledged=accept_sturgeon_license))
        atomic_write_json(root/'resources.json',dict(schema='oncotracer-managed-resources-v1',classifiers=sorted(classifiers),caller=caller,values=values))
        return values


def prepare_for_launch(cache,classifiers,args):
    from .sturgeon_resources import LICENSE_URL
    if 'sturgeon' in classifiers and not args.accept_sturgeon_license:
        raise OncoTracerError('CNS samples require the Sturgeon license: '+LICENSE_URL+'. Repeat with --accept-sturgeon-license after reviewing it.')
    cache=_safe_directory(cache)
    command=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
             '--mount',f'type=bind,source={cache},target={cache}',
             '--env','PYTHONDONTWRITEBYTECODE=1','--env','PIP_CACHE_DIR=/tmp/pip-cache',
             '--env','HOME=/tmp','--entrypoint','/opt/conda/bin/python',args.image,
             '-B','-m','oncotracer_cli.managed_resources','--cache',str(cache),
             '--classifiers',','.join(classifiers),'--caller',args.methylation_caller]
    if args.accept_sturgeon_license:command.append('--accept-sturgeon-license')
    result=subprocess.run(command)
    if result.returncode:
        raise OncoTracerError('Resource preparation failed. Review the reported file/tool error and repeat the command; verified assets and partial downloads are retained.')
    receipt=json.loads((cache/'methylation/resources.json').read_text())
    if receipt.get('schema')!='oncotracer-managed-resources-v1' or receipt.get('classifiers')!=sorted(classifiers) or receipt.get('caller')!=args.methylation_caller:
        raise OncoTracerError('Resource preparation did not produce the requested configuration')
    return receipt['values']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--classifiers',required=True)
    parser.add_argument('--caller',choices=('rockfish','modkit'),default='rockfish')
    parser.add_argument('--accept-sturgeon-license',action='store_true')
    args=parser.parse_args()
    prepare(args.cache,args.classifiers.split(','),caller=args.caller,accept_sturgeon_license=args.accept_sturgeon_license)


if __name__=='__main__':
    try:main()
    except (OncoTracerError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('Resource preparation failed: '+str(error))
