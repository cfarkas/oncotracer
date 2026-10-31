"""QUILT2 diploid SNP imputation, isolated from observed caller evidence.

The public GRCh38 1000 Genomes panel is checksum pinned. Preparation is shared
across samples, with independent integrity receipts for every prepared chunk.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from contextlib import contextmanager
import csv
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from glimpse_download import download, validate_file, PANEL_EXPECTATIONS, PANEL_BASE

VERSION = '2.0.4'
MAP_COMMIT = '7feea70daefbe6903d98b074762cf339f8903350'
SCHEMA = 'oncotracer-quilt2-v1'
# This exact worker differs only in output-path handling. Its committed chunks
# remain scientifically identical; require the old input signature, SHA-256s,
# sample identity and VCF validation before reusing them after this repair.
PATH_COMPATIBLE_WORKERS = ('693ce20838931fa8b2c8cb1e100a54ef8351609a28e1eb77088fbb9b6319dffc',)
STOP = threading.Event()
LIMITATION = ('Research-only diploid population-panel SNP imputation. These are germline-style '
              'hypotheses, not observed calls or evidence of inherited origin. Tumor admixture, '
              'copy-number changes, ancestry mismatch and sparse coverage can invalidate diploid '
              'assumptions. Indels, sex chromosomes and structural variants are not imputed. '
              'GP is a model posterior, not a clinically calibrated probability of truth.')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024**2),b''):h.update(block)
    return h.hexdigest()


def atomic(path,data):
    path=Path(path)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,prefix='.'+path.name,delete=False) as stream:
        json.dump(data,stream,indent=2);stream.write('\n');temporary=Path(stream.name)
    os.replace(temporary,path)


def identity(path):
    p=Path(path).resolve();s=p.stat()
    return dict(path=str(p),bytes=s.st_size,mtime_ns=s.st_mtime_ns,device=s.st_dev,inode=s.st_ino)


def call(argv, *, capture=False, cwd=None):
    command=list(map(str,argv))
    print('QUILT2'+(f' [cwd={cwd}]' if cwd else '')+': '+' '.join(command),flush=True)
    if STOP.is_set():raise RuntimeError('QUILT2 stopped after another reference task failed')
    with subprocess.Popen(command,text=True,stdout=subprocess.PIPE if capture else None,cwd=cwd) as child:
        while True:
            try:
                output,_=child.communicate(timeout=.5)
                break
            except subprocess.TimeoutExpired:
                if STOP.is_set():
                    child.terminate()
                    try:child.communicate(timeout=5)
                    except subprocess.TimeoutExpired:child.kill();child.communicate()
                    raise RuntimeError('QUILT2 cancelled after another reference task failed')
        if child.returncode:
            STOP.set()
            raise subprocess.CalledProcessError(child.returncode,command)
    return output if capture else None


def parallel_map(function,items,workers):
    def guarded(item):
        if STOP.is_set():raise RuntimeError('QUILT2 reference preparation cancelled')
        try:return function(item)
        except BaseException:
            STOP.set()
            raise
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(guarded,item) for item in items]
        done,_=wait(futures,return_when=FIRST_EXCEPTION)
        failure=next((f.exception() for f in done if f.exception() is not None),None)
        if failure:
            STOP.set()
            for f in futures:f.cancel()
            raise failure
        return [f.result() for f in futures]


@contextmanager
def lock(path):
    with Path(path).open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def owned_directory(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('QUILT2 output/cache must not be a symlink')
    path.mkdir(parents=True,exist_ok=True)
    marker=path/'.oncotracer-quilt2.json'
    if marker.exists():
        if json.loads(marker.read_text()).get('schema')!=SCHEMA:raise ValueError('QUILT2 ownership mismatch')
    elif any(path.iterdir()):raise ValueError('QUILT2 requires an empty or previously owned directory: '+str(path))
    else:atomic(marker,dict(schema=SCHEMA))
    return path


def valid_receipt(path, signature):
    try:
        receipt=json.loads(Path(path).read_text())
        return (receipt['signature']==signature and bool(receipt['files']) and
                all((Path(path).parent/name).is_file() and sha(Path(path).parent/name)==digest
                    for name,digest in receipt['files'].items()))
    except (OSError,ValueError,KeyError):return False


def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def prepare(config):
    cache=owned_directory(config['cache'])
    fasta=Path(config['reference']).resolve()
    lengths={f[0]:int(f[1]) for line in Path(str(fasta)+'.fai').read_text().splitlines() if (f:=line.split('\t'))}
    if lengths.get('chr1')!=248956422:
        raise ValueError('QUILT2 public panel requires GRCh38 with chr-prefixed contigs')
    chromosomes=config.get('chromosomes') or [f'chr{i}' for i in range(1,23)]
    if any(c not in PANEL_EXPECTATIONS or c not in lengths for c in chromosomes):
        raise ValueError('QUILT2 requires matching autosomal GRCh38 contigs')
    reference_hash=sha(fasta)
    generation=signature(dict(schema=SCHEMA,version=VERSION,reference_sha256=reference_hash,
                              samples=sorted(config['samples']),maps=MAP_COMMIT))[:24]
    cache=cache/'panels'/generation;cache.mkdir(parents=True,exist_ok=True)
    tools=config['tools'];bc=tools['bcftools'];manifest=[]
    parallel_chromosomes=min(4,max(1,int(config.get('threads',1))),len(chromosomes))
    chunk_threads=max(1,min(8,int(config.get('threads',1)))//parallel_chromosomes)
    def prepare_chromosome(chrom):
        prepared=[]
        directory=cache/chrom;directory.mkdir(exist_ok=True)
        with lock(directory/'.prepare.lock'):
            name=f'CCDG_14151_B01_GRM_WGS_2020-08-05_{chrom}.filtered.shapeit2-duohmm-phased.vcf.gz'
            supplied=config.get('reference_dir')
            if supplied:
                panel=Path(supplied)/name
                integrity=validate_file(panel,expected=PANEL_EXPECTATIONS[chrom],compressed=True)
            else:
                panel=download(PANEL_BASE+name,cache/name,expected=PANEL_EXPECTATIONS[chrom])
                integrity=json.loads(Path(str(panel)+'.source.json').read_text())
            mapname=f'CEU-{chrom}-final.b38.txt.gz'
            genetic=download(f'https://raw.githubusercontent.com/rwdavies/QUILT/{MAP_COMMIT}/maps/hg38/{mapname}',cache/mapname)
            # Decompress before handing the map to R; no runtime package install.
            localmap=directory/f'{chrom}.map.txt'
            with gzip.open(genetic,'rt') as src:localmap.write_text(src.read())
            panel_samples=call([bc,'query','-l',panel],capture=True).splitlines()
            exclude=sorted(set(config['samples']) & set(panel_samples))
            sig=signature(dict(version=VERSION,panel={k:integrity[k] for k in ('sha256','bytes','md5')},reference_sha256=reference_hash,map_sha256=sha(genetic),
                               exclude=exclude,method='diploid',filter='biallelic SNP MAC>=2',preparation_contract=SCHEMA))
            normalized=directory/f'{chrom}.panel.bcf'
            normal_receipt=directory/'panel.complete.json'
            if not valid_receipt(normal_receipt,sig):
                with tempfile.TemporaryDirectory(dir=directory,prefix='.prepare-') as tmp:
                    tmp=Path(tmp);selected=tmp/'selected.bcf';checked=tmp/'checked.bcf'
                    args=[bc,'view','-m2','-M2','-v','snps','-c','2:minor','-Ob','-o',selected]
                    if exclude:args+=['-s','^'+','.join(exclude)]
                    args.append(panel);call(args)
                    header=call([bc,'view','-h',selected],capture=True)
                    if not re.search(r'##contig=<ID='+chrom+r'[,>]',header):
                        raise ValueError('QUILT2 panel contig does not match '+chrom)
                    call([bc,'norm','-f',fasta,'-c','e','-Ob','-o',tmp/normalized.name,selected])
                    call([bc,'index',tmp/normalized.name])
                    for suffix in ('','.csi'):os.replace(tmp/(normalized.name+suffix),Path(str(normalized)+suffix))
                atomic(normal_receipt,dict(signature=sig,files={p.name:sha(p) for p in (normalized,Path(str(normalized)+'.csi'))}))
            chunk_table=directory/'chunks.tsv'
            script='a<-commandArgs(TRUE);x<-QUILT::quilt_chunk_map(a[1],a[2]);write.table(x,a[3],sep="\\t",row.names=FALSE,quote=FALSE)'
            call([tools['Rscript'],'--vanilla','-e',script,chrom,localmap,chunk_table])
            chunks=[]
            with chunk_table.open() as stream:
                for row in csv.DictReader(stream,delimiter='\t'):
                    match=re.fullmatch(re.escape(chrom)+r':(\d+)-(\d+)',row['region'])
                    if not match:raise ValueError('Invalid QUILT2 chunk coordinates')
                    start,end=map(int,match.groups())
                    chunks.append(dict(chr=chrom,regionStart=start,regionEnd=min(end,lengths[chrom]),buffer=500000,nGen=100))
            if not chunks:raise ValueError('QUILT2 produced no reference chunks')
            chunks[-1]['regionEnd']=lengths[chrom]
            if config.get('_test_region'):
                start,end=config['_test_region'];chunks=[dict(chr=chrom,regionStart=start,regionEnd=end,buffer=500000,nGen=100)]
            def build(chunk):
                label=f'{chrom}.{chunk["regionStart"]}.{chunk["regionEnd"]}'
                dest=directory/(label+'.RData');receipt=directory/(label+'.complete.json')
                chunk_sig=signature([sig,chunk])
                with lock(directory/(label+'.lock')):
                    if not valid_receipt(receipt,chunk_sig):
                        with tempfile.TemporaryDirectory(dir=directory,prefix='.chunk-') as tmp:
                            tmp=Path(tmp);output=tmp/dest.name
                            call([tools['QUILT2_prepare_reference.R'],f'--reference_vcf_file={normalized}',
                                  f'--genetic_map_file={localmap}',f'--outputdir={tmp}',f'--output_file={output}',
                                  f'--tempdir={tmp}',*[f'--{k}={v}' for k,v in chunk.items()]])
                            if not output.is_file() or output.stat().st_size==0:raise ValueError('QUILT2 did not prepare '+label)
                            os.replace(output,dest)
                        atomic(receipt,dict(signature=chunk_sig,files={dest.name:sha(dest)}))
                    digest=json.loads(receipt.read_text())['files'][dest.name]
                return {**chunk,'prepared_reference':str(dest),'sha256':digest,'reference_signature':sig,'excluded_samples':exclude}
            prepared.extend(parallel_map(build,chunks,chunk_threads))
        return prepared
    for prepared in parallel_map(prepare_chromosome,chromosomes,parallel_chromosomes):manifest.extend(prepared)
    destination=Path(config.get('manifest_output') or cache/'panel.json')
    atomic(destination,dict(schema=SCHEMA,version=VERSION,assembly='GRCh38',panel='1000G high coverage 3202 phased July2021',
                            map_commit=MAP_COMMIT,reference_sha256=reference_hash,contig_lengths={c:lengths[c] for c in chromosomes},
                            chunks=manifest,limitation=LIMITATION))
    return destination


def validate_vcf(path,sample):
    import pysam
    total=missing=0
    with pysam.VariantFile(str(path)) as source:
        if list(source.header.samples)!=[sample]:raise ValueError('QUILT2 output sample identity mismatch')
        for record in source:
            total+=1;call_=record.samples[sample];gt=call_.get('GT')
            if gt is None or any(g is None for g in gt):missing+=1;continue
            if not call_.phased or len(gt)!=2:raise ValueError('QUILT2 emitted an unphased/non-diploid genotype')
            gp=call_.get('GP');ds=call_.get('DS')
            if gp is None or len(gp)!=3 or any(x is None or not 0<=x<=1 for x in gp) or abs(sum(gp)-1)>.01:
                raise ValueError('QUILT2 emitted invalid GP probabilities')
            if isinstance(ds,tuple):ds=ds[0] if len(ds)==1 else None
            if ds is None or not 0<=ds<=2:raise ValueError('QUILT2 emitted invalid dosage')
    if not total:raise ValueError('QUILT2 emitted an empty imputation VCF')
    return dict(records=total,missing_genotypes=missing,imputed_genotypes=total-missing)


def standardize_probability_header(path,bc):
    # QUILT 2.0.4 declares GP as Number=3. VCF requires Number=G;
    # only the declaration changes, never posterior values or calls.
    header=call([bc,'view','-h',path],capture=True)
    corrected=header.replace('##FORMAT=<ID=GP,Number=3,','##FORMAT=<ID=GP,Number=G,')
    if corrected!=header:
        path=Path(path);header_path=path.parent/'standard-header.txt'
        header_path.write_text(corrected)
        updated=path.parent/'standard-header.vcf.gz'
        call([bc,'reheader','-h',header_path,'-o',updated,path])
        os.replace(updated,path)


def imputation_signature(bam,chunk,min_base_quality,code):
    return signature(dict(bam=bam,chunk=chunk,seed=17,bqFilter=min_base_quality,version=VERSION,code=code))


def reusable_imputation_chunk(receipt,final,bam,chunk,min_base_quality,sample,code):
    try:
        previous=json.loads(Path(receipt).read_text())
        if set(previous['files'])!={final.name,final.name+'.tbi'}:return False
        for worker in (code,*PATH_COMPATIBLE_WORKERS):
            sig=imputation_signature(bam,chunk,min_base_quality,worker)
            if valid_receipt(receipt,sig):
                validate_vcf(final,sample)
                return True
    except (OSError,ValueError,KeyError,TypeError):pass
    return False


def run_imputation_chunk(tools,chunk,bamlist,names,temporary,threads,min_base_quality):
    # QUILT 2.0.4 uses gsub(".gz", "", output_filename), removing matches
    # anywhere in an absolute path (including tempfile suffixes and sample names).
    # Keep all R output paths relative and fixed, with only subprocess-local cwd.
    # External, sample-prefixed filenames are assigned after output validation.
    temporary=Path(temporary).absolute()
    args=[tools['QUILT2.R'],f'--prepared_reference_filename={Path(chunk["prepared_reference"]).absolute()}',
          f'--bamlist={Path(bamlist).absolute()}',f'--sampleNames_file={Path(names).absolute()}',
          '--method=diploid','--outputdir=.','--output_filename=imputed.vcf.gz','--tempdir=.',
          f'--nCores={threads}',f'--bqFilter={min_base_quality}','--seed=17',
          *[f'--{k}={chunk[k]}' for k in ('chr','regionStart','regionEnd','buffer','nGen')]]
    call(args,cwd=temporary)
    return temporary/'imputed.vcf.gz'


def impute(config):
    import pysam
    sample=config['sample']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',sample):raise ValueError('Unsafe sample identifier')
    root=owned_directory(config['output']).absolute();tools=config['tools'];bc=tools['bcftools']
    bam=Path(config['bam']).resolve();before=identity(bam);pysam.quickcheck(str(bam))
    with pysam.AlignmentFile(str(bam),'rb') as source:
        if not source.has_index() or source.header.to_dict().get('HD',{}).get('SO')!='coordinate':
            raise ValueError('QUILT2 requires a completed coordinate-sorted BAM and index')
        bam_lengths=dict(zip(source.references,source.lengths))
        samples={r['SM'] for r in source.header.to_dict().get('RG',[]) if r.get('SM')}
        if samples and samples!={sample}:raise ValueError('QUILT2 BAM SM does not match sample: '+str(samples))
    manifest=json.loads(Path(config['manifest']).read_text())
    if manifest.get('schema')!=SCHEMA or manifest.get('version')!=VERSION:raise ValueError('QUILT2 panel version mismatch')
    expected=set(config.get('expected_chromosomes') or [c['chr'] for c in manifest['chunks']])
    if {c['chr'] for c in manifest['chunks']}!=expected:raise ValueError('QUILT2 panel does not cover the requested chromosomes')
    if any(bam_lengths.get(c)!=length for c,length in manifest.get('contig_lengths',{}).items()):
        raise ValueError('QUILT2 BAM and reference contig lengths differ')
    bamlist=root/f'{sample}.bamlist.txt';bamlist.write_text(str(bam)+'\n')
    names=root/f'{sample}.sample_names.txt';names.write_text(sample+'\n')
    chromosomes={};counts={};reused_chunks=computed_chunks=0;code=sha(__file__)
    for chunk in manifest['chunks']:
        chrom=chunk['chr'];label=f'{sample}.quilt2.{chrom}.{chunk["regionStart"]}.{chunk["regionEnd"]}'
        dest=root/'chunks'/label;dest.mkdir(parents=True,exist_ok=True)
        final=dest/(label+'.vcf.gz');receipt=dest/(label+'.complete.json')
        if sha(chunk['prepared_reference'])!=chunk['sha256']:raise ValueError('Prepared QUILT2 reference checksum mismatch')
        sig=imputation_signature(before,chunk,config['min_base_quality'],code)
        if config.get('force') or not reusable_imputation_chunk(receipt,final,before,chunk,config['min_base_quality'],sample,code):
            with tempfile.TemporaryDirectory(dir=dest,prefix='.impute-') as tmp:
                tmp=Path(tmp)
                raw=run_imputation_chunk(tools,chunk,bamlist,names,tmp,config['threads'],config['min_base_quality'])
                standardize_probability_header(raw,bc);validate_vcf(raw,sample)
                call([bc,'index','-t','-f',raw])
                for suffix in ('','.tbi'):os.replace(Path(str(raw)+suffix),Path(str(final)+suffix))
            atomic(receipt,dict(signature=sig,producer_worker_sha256=code,files={p.name:sha(p) for p in (final,Path(str(final)+'.tbi'))}))
            computed_chunks+=1
        else:reused_chunks+=1
        chromosomes.setdefault(chrom,[]).append(final)
    outputs=[]
    for chrom,paths in chromosomes.items():
        final=root/f'{sample}.quilt2.{chrom}.vcf.gz'
        with tempfile.TemporaryDirectory(dir=root,prefix='.ligate-') as tmp:
            tmp=Path(tmp);filelist=tmp/'vcfs.txt';filelist.write_text(''.join(str(p)+'\n' for p in paths))
            joined=tmp/'joined.vcf.gz'
            call([bc,'concat','--ligate','-f',filelist,'-Oz','-o',joined])
            # bcftools updates phased GT but not QUILT's haplotype dosage HD.
            # Remove HD after ligation; GP and DS are invariant to a haplotype swap.
            call([bc,'annotate','-x','FORMAT/HD','-Oz','-o',tmp/final.name,joined])
            counts[chrom]=validate_vcf(tmp/final.name,sample)
            call([bc,'index','-t',tmp/final.name])
            for suffix in ('','.tbi'):os.replace(tmp/(final.name+suffix),Path(str(final)+suffix))
        outputs.append(final)
    final=root/f'{sample}.quilt2.imputed.vcf.gz'
    with tempfile.TemporaryDirectory(dir=root,prefix='.combine-') as tmp:
        tmp=Path(tmp);filelist=tmp/'chromosomes.txt';filelist.write_text(''.join(str(p)+'\n' for p in outputs))
        call([bc,'concat','-f',filelist,'-Oz','-o',tmp/final.name]);validate_vcf(tmp/final.name,sample)
        call([bc,'index','-t',tmp/final.name])
        for suffix in ('','.tbi'):os.replace(tmp/(final.name+suffix),Path(str(final)+suffix))
    if identity(bam)!=before:raise ValueError('QUILT2 input BAM changed during imputation; no completion receipt was published')
    summary=dict(schema=SCHEMA,status='complete',sample=sample,version=VERSION,method='diploid',
                 vcf=str(final),chromosomes=counts,panel_manifest_sha256=sha(config['manifest']),
                 inputs=dict(bam=identity(bam)),limitation=LIMITATION,
                 reused_chunks=reused_chunks,computed_chunks=computed_chunks,
                 hd_removed_after_ligation=True,observed_calls_modified=False,germline_probability=None,
                 assessment_status='not_assessable' if not sum(c['imputed_genotypes'] for c in counts.values()) else 'research_diploid_hypotheses')
    atomic(root/f'{sample}.quilt2.summary.json',summary)
    atomic(root/f'{sample}.quilt2.complete.json',dict(signature=signature(summary),files={p.name:sha(p) for p in [final,Path(str(final)+'.tbi'),root/f'{sample}.quilt2.summary.json']}))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--prepare',action='store_true')
    a=p.parse_args();config=json.loads(Path(a.config).read_text())
    prefix=str(Path(config['tools']['Rscript']).parent.parent)
    for key in ('PYTHONPATH','R_LIBS','R_LIBS_USER','R_LIBS_SITE','R_HOME','LD_PRELOAD','LD_LIBRARY_PATH'):
        os.environ.pop(key,None)
    os.environ.update(PATH=str(Path(prefix)/'bin')+os.pathsep+os.defpath,CONDA_PREFIX=prefix,
                      OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    print(json.dumps(str(prepare(config)) if a.prepare else impute(config)))


if __name__=='__main__':main()
