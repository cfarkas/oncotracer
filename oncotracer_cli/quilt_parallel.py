"""Parallel prefetch with the unchanged, verified QUILT2 chunk implementation.

The original worker performs final validation, ligation and publication. Its
chunk signatures remain valid because inputs and producer code are unchanged.
"""
from __future__ import annotations
import argparse,importlib.util,json,os,re,sys,tempfile,time
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from .runtime import runtime_root,sha256_file,atomic_write_json
from .resource_pool import read_budget

SUPPORTED_WORKER = 'e059a3ef8fbd783c34edc32c68c8912e7a7064e4f515c5b7b779fa54c775fe99'


def load_worker(root=None):
    # Standalone children import from the zipapp, but argv[0] is this module.
    # Use the parent's verified, extracted payload instead of guessing its path.
    path=(Path(root) if root is not None else runtime_root())/'oncotracer_cli/lpwgs/bin/quilt2_worker.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('_oncotracer_quilt_worker',path)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    return worker,path


def identity_cached_sha(digest,paths):
    """Share full hashes of stable reference files only within this process."""
    allowed={str(Path(path).resolve()) for path in paths}
    def identity(path):
        path=Path(path).resolve();stat=path.stat()
        return (str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    @lru_cache(maxsize=4096)
    def checked(key):
        value=digest(Path(key[0]))
        if identity(key[0])!=key:raise ValueError('QUILT2 file changed while hashing: '+key[0])
        return value
    def sha(path):
        key=identity(path)
        # Recent writes may share one filesystem clock tick. Never memoize
        # those files, patient outputs, or paths outside the prepared panel.
        stable=key[0] in allowed and max(key[-2:]) < time.time_ns()-1_000_000_000
        value=checked(key) if stable else digest(Path(key[0]))
        if identity(path)!=key:raise ValueError('QUILT2 file changed while validating: '+str(path))
        return value
    return sha


def bounded_map(function,items,directory,maximum,stop,*,on_complete=None):
    pending=iter(items);results=[];exhausted=False
    with ThreadPoolExecutor(max_workers=maximum,thread_name_prefix='quilt-chunk') as pool:
        active=set()
        try:
            while active or not exhausted:
                if stop.is_set():raise RuntimeError('QUILT2 chunk execution cancelled')
                count=min(maximum,read_budget(directory)['worker_limit'])
                while not exhausted and len(active)<count:
                    item=next(pending,None)
                    if item is None:exhausted=True;break
                    active.add(pool.submit(function,item))
                if not active:
                    time.sleep(.2);continue
                done,_=wait(active,timeout=.2,return_when=FIRST_COMPLETED)
                for future in done:
                    active.remove(future);results.append(future.result())
                    if on_complete is not None:on_complete(results[-1])
        except BaseException:
            stop.set()
            for future in active:future.cancel()
            raise
    return results


def prefetch(config,directory,maximum,worker):
    import pysam
    sample=config['sample']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',sample):raise ValueError('Unsafe QUILT2 sample identifier')
    root=worker.owned_directory(config['output']).absolute()
    bam=Path(config['bam']).resolve();before=worker.identity(bam);pysam.quickcheck(str(bam))
    with pysam.AlignmentFile(str(bam),'rb') as stream:
        if not stream.has_index() or stream.header.to_dict().get('HD',{}).get('SO')!='coordinate':raise ValueError('QUILT2 requires a sorted, indexed BAM')
        lengths=dict(zip(stream.references,stream.lengths));samples={x['SM'] for x in stream.header.to_dict().get('RG',[]) if x.get('SM')}
        if samples and samples!={sample}:raise ValueError('QUILT2 sample name mismatch')
    manifest=json.loads(Path(config['manifest']).read_text())
    if manifest.get('schema')!=worker.SCHEMA or manifest.get('version')!=worker.VERSION:raise ValueError('QUILT2 panel version mismatch')
    expected=set(config.get('expected_chromosomes') or [x['chr'] for x in manifest['chunks']])
    if {x['chr'] for x in manifest['chunks']}!=expected:raise ValueError('QUILT2 panel does not cover requested chromosomes')
    if any(lengths.get(c)!=length for c,length in manifest.get('contig_lengths',{}).items()):raise ValueError('QUILT2 reference contig lengths differ')
    keys=[(c['chr'],c['regionStart'],c['regionEnd']) for c in manifest['chunks']]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate QUILT2 chunk targets')
    bamlist=root/f'{sample}.bamlist.txt';bamlist.write_text(str(bam)+'\n')
    names=root/f'{sample}.sample_names.txt';names.write_text(sample+'\n')
    producer=worker.sha(worker.__file__);bc=config['tools']['bcftools']
    def run(chunk):
        label=f'{sample}.quilt2.{chunk["chr"]}.{chunk["regionStart"]}.{chunk["regionEnd"]}'
        dest=root/'chunks'/label
        if dest.is_symlink() or dest.parent.is_symlink():raise ValueError('QUILT2 chunk directory must not be a symlink')
        dest.mkdir(parents=True,exist_ok=True)
        final=dest/(label+'.vcf.gz');receipt=dest/(label+'.complete.json')
        with worker.lock(dest/'.impute.lock'):
            if worker.sha(chunk['prepared_reference'])!=chunk['sha256']:raise ValueError('QUILT2 prepared-reference checksum mismatch')
            if not config.get('force') and worker.reusable_imputation_chunk(receipt,final,before,chunk,config['min_base_quality'],sample,producer):return 'reused'
            with tempfile.TemporaryDirectory(dir=dest,prefix='.impute-') as temp:
                raw=worker.run_imputation_chunk(config['tools'],chunk,bamlist,names,temp,1,config['min_base_quality'])
                worker.standardize_probability_header(raw,bc);worker.validate_vcf(raw,sample)
                worker.call([bc,'index','-t','-f',raw])
                for suffix in ('','.tbi'):os.replace(Path(str(raw)+suffix),Path(str(final)+suffix))
            worker.atomic(receipt,dict(signature=worker.imputation_signature(before,chunk,config['min_base_quality'],producer),
                producer_worker_sha256=producer,execution_controller='dynamic-prefetch-v1',
                files={p.name:worker.sha(p) for p in (final,Path(str(final)+'.tbi'))}))
            return 'computed'
    total=len(manifest['chunks']);finished={'reused':0,'computed':0};last_progress=time.monotonic()
    print(f'[QUILT2 {sample}] Checking {total} chunks; up to {maximum} worker(s).',flush=True)
    def progress(result):
        nonlocal last_progress
        finished[result]+=1;done=sum(finished.values());now=time.monotonic()
        if done==total or now-last_progress>=30:
            print(f'[QUILT2 {sample}] Chunks verified {done}/{total}: '
                  f'{finished["reused"]} reused, {finished["computed"]} computed.',flush=True)
            last_progress=now
    counts=bounded_map(run,manifest['chunks'],directory,maximum,worker.STOP,on_complete=progress)
    if worker.identity(bam)!=before:raise ValueError('QUILT2 BAM changed during chunk execution')
    atomic_write_json(root/f'{sample}.quilt2.scheduling.json',{'mode':'dynamic','computed_chunks':counts.count('computed'),
        'reused_chunks':counts.count('reused'),'producer_worker_sha256':producer,'chunk_threads':1,'maximum_workers':maximum})


def configure_environment(config):
    # Match the original worker's entry point before invoking its functions.
    # R scripts use /usr/bin/env, so an ambient R installation must not win.
    prefix = str(Path(config['tools']['Rscript']).parent.parent)
    for key in ('PYTHONPATH', 'R_LIBS', 'R_LIBS_USER', 'R_LIBS_SITE', 'R_HOME',
                'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        os.environ.pop(key, None)
    os.environ.update(PATH=str(Path(prefix)/'bin')+os.pathsep+os.defpath,
                      CONDA_PREFIX=prefix, OMP_NUM_THREADS='1',
                      OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                      PYTHONDONTWRITEBYTECODE='1')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--resources',type=Path,required=True);parser.add_argument('--maximum-workers',type=int,required=True);parser.add_argument('--runtime-root',type=Path);args=parser.parse_args()
    config=json.loads(args.config.read_text());configure_environment(config);worker,path=load_worker(args.runtime_root)
    if sha256_file(path)==SUPPORTED_WORKER:
        # The final worker rechecks the prepared references. Reuse their full
        # digests only while identity is stable within this invocation. Patient
        # output hashes and every receipt/header/index check still run in full.
        chunks=json.loads(Path(config['manifest']).read_text())['chunks']
        worker.sha=identity_cached_sha(worker.sha,[c['prepared_reference'] for c in chunks])
        prefetch(config,args.resources,args.maximum_workers,worker)
        # --force has already recomputed every chunk. The unchanged worker
        # verifies them and then performs its usual final output transaction.
        config={**config,'force':False}
    print(f'[QUILT2 {config["sample"]}] Final validation, ligation and publication.',flush=True)
    worker.impute(config)
    print(f'[QUILT2 {config["sample"]}] Final outputs verified and published.',flush=True)

if __name__=='__main__':main()
