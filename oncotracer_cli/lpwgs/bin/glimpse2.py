#!/usr/bin/env python3
"""Local GLIMPSE2 execution and resumable public GRCh38 reference provisioning."""
import argparse
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
from glimpse_download import download, validate_file, PANEL_EXPECTATIONS

VERSION='v2.0.0'
PANEL_BASE='https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/working/20201028_3202_phased'
TOOLS=['GLIMPSE2_chunk','GLIMPSE2_split_reference','GLIMPSE2_phase','GLIMPSE2_ligate']

def run(args, **kwargs):
    print('+ '+' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=True,**kwargs)

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def tool(c,name):
    choices=[]
    if c.get('glimpse_bin'):choices.append(Path(c['glimpse_bin'])/name)
    choices += [Path(c['resource_dir'])/'glimpse2/bin'/name]
    if shutil.which(name):choices.append(Path(shutil.which(name)))
    for p in choices:
        if p.is_file() and os.access(p,os.X_OK):return str(p)
        if Path(str(p)+'_static').is_file():return str(p)+'_static'
    p=choices[0]
    if os.uname().machine!='x86_64':raise ValueError('Automatic GLIMPSE2 binaries require Linux x86_64; supply --glimpse-bin')
    download(f'https://github.com/odelaneau/GLIMPSE/releases/download/{VERSION}/{name}_static',p)
    p.chmod(0o755)
    run([p,'--help'],stdout=subprocess.DEVNULL)
    return str(p)

def validate_panel_contig(header, chrom, expected):
    """VCF contig lengths are optional; reject contradictions, not absence."""
    canonical='chr'+chrom.removeprefix('chr')
    contigs={}
    for body in re.findall(r'^##contig=<(.+)>\s*$',header,re.MULTILINE):
        fields={k:v.strip().strip('"') for k,v in
                re.findall(r'(?:^|,)\s*(ID|length)\s*=\s*("[^"]*"|[^,>]*)',body)}
        if 'ID' not in fields:continue
        length=None
        if 'length' in fields:
            try:length=int(fields['length'])
            except ValueError:raise ValueError(f"Invalid declared contig length for {fields['ID']}: {fields['length']}") from None
            if length<1:raise ValueError(f"Invalid declared contig length for {fields['ID']}: {length}")
        if fields['ID'] in contigs and contigs[fields['ID']]!=length:
            raise ValueError(f"Conflicting contig declarations for {fields['ID']}")
        contigs[fields['ID']]=length
    if chrom not in expected:
        raise ValueError(f'Requested chromosome {chrom} is absent from the reference FASTA index')
    aliases=list(dict.fromkeys([chrom,canonical,canonical.removeprefix('chr')]))
    panel_chrom=next((name for name in aliases if name in contigs),None)
    if panel_chrom is None:
        raise ValueError(f'Reference panel has no contig declaration for {canonical}')
    length=contigs[panel_chrom]
    if length is not None and length!=expected[chrom]:
        raise ValueError(f'Reference panel chromosome length does not match FASTA/GRCh38: '
                         f'{panel_chrom} declares {length}, FASTA {chrom} has {expected[chrom]}')
    if length is None:
        print(f'{panel_chrom}: panel header omits length; using FASTA length {expected[chrom]} '
              'and requiring REF-allele agreement at every retained SNP.',flush=True)
    return panel_chrom,length

def reference(a,c):
    chrom=a.chrom;canonical='chr'+chrom.removeprefix('chr')
    cache=Path(c['resource_dir'])/'glimpse2';cache.mkdir(parents=True,exist_ok=True)
    custom=Path(c['glimpse_reference_dir']) if c.get('glimpse_reference_dir') else None
    filename=f'CCDG_14151_B01_GRM_WGS_2020-08-05_{canonical}.filtered.shapeit2-duohmm-phased.vcf.gz'
    system_refs=Path(c['reference']).parent.parent
    search=[p for p in [custom,cache,Path(c['resource_dir'])/'imputation',system_refs/'glimpse2',system_refs/'imputation',system_refs/'1000genomes'] if p]
    if custom:search=[custom]
    panel=None
    for directory in search:
        for name in [f'{canonical}.bcf',f'{canonical}.vcf.gz',filename]:
            if (directory/name).is_file():panel=directory/name;break
        if panel:break
    if panel is None and not custom:
        matches=sorted(system_refs.rglob(filename)) if system_refs.is_dir() else []
        if matches:panel=matches[0]
    if panel is None and custom:raise ValueError(f'No {canonical} reference panel in {custom}; expected chrN.bcf or chrN.vcf.gz')
    if panel is None or (not custom and panel==cache/filename):
        panel=download(f'{PANEL_BASE}/{filename}',cache/filename,expected=PANEL_EXPECTATIONS[canonical])
        panel_integrity=json.loads(Path(str(panel)+'.source.json').read_text())
    else:
        # Custom/discovered references are read-only: fail on corruption, never replace them.
        with panel.open('rb') as handle: uncompressed_bcf=panel.suffix=='.bcf' and handle.read(3)==b'BCF'
        panel_integrity=validate_file(panel,compressed=not uncompressed_bcf)
    # Explicit assembly validation, then normalization against the actual FASTA.
    bc=c['tools']['bcftools']
    header=run([bc,'view','-h',panel],capture_output=True,text=True).stdout
    fai_lines=Path(c['reference']+'.fai').read_text().splitlines()
    expected={x.split('\t')[0]:int(x.split('\t')[1]) for x in fai_lines}
    panel_chrom,declared_length=validate_panel_contig(header,chrom,expected)
    mapfile=None
    for directory in search:
        for name in [f'{canonical}.b38.gmap.gz',f'{canonical}.b38.gmap',f'{canonical}.gmap']:
            if (directory/name).is_file():mapfile=directory/name;break
        if mapfile:break
    if mapfile is None or (not custom and mapfile==cache/f'{canonical}.b38.gmap.gz'):
        mapfile=download(f'https://raw.githubusercontent.com/odelaneau/GLIMPSE/{VERSION}/maps/genetic_maps.b38/{canonical}.b38.gmap.gz',cache/f'{canonical}.b38.gmap.gz')
    localmap=Path(f'map.{chrom}.txt')
    with (gzip.open(mapfile,'rt') if mapfile.suffix=='.gz' else open(mapfile)) as src:localmap.write_text(src.read())
    # A biallelic SNP reference avoids ambiguous indel/multiallelic interpretation.
    selected=Path(f'selected.{chrom}.bcf');ref=Path(f'reference.{chrom}.bcf')
    command=[bc,'view','-t',panel_chrom,'-m2','-M2','-v','snps','-c','1:minor','-Ob','-o',selected,panel]
    samples=run([bc,'query','-l',panel],capture_output=True,text=True).stdout.splitlines()
    if c['sample'] in samples:command[2:2]=['-s','^'+c['sample']]
    run(command)
    if panel_chrom!=chrom:
        Path('rename.tsv').write_text(f'{panel_chrom}\t{chrom}\n')
        run([bc,'annotate','--rename-chrs','rename.tsv','-Ob','-o','renamed.bcf',selected]);Path('renamed.bcf').replace(selected)
    # Give downstream BCF/GLIMPSE tools an explicit contig length. A conflicting
    # source length has already failed validation; REF checking still follows.
    local_fai=Path(f'reference.{chrom}.fai')
    local_fai.write_text(next(line for line in fai_lines if line.split('\t')[0]==chrom)+'\n')
    reheadered=Path(f'reheadered.{chrom}.bcf')
    run([bc,'reheader','--fai',local_fai,'-o',reheadered,selected]);reheadered.replace(selected)
    run([bc,'norm','-f',c['reference'],'-c','e','-Ob','-o',ref,selected]);selected.unlink()
    run([bc,'index','-f',ref])
    # Catch an unphased/missing-GT panel early without materializing all genotypes.
    proc=subprocess.Popen([bc,'query','-f','[%GT\t]\n',str(ref)],stdout=subprocess.PIPE,text=True)
    phased=False; count=0
    try:
        for line in proc.stdout:
            count+=1; gt=line.strip().split('\t')
            if any('/' in x or '.' in x for x in gt):raise ValueError('GLIMPSE2 reference must contain complete phased genotypes')
            phased |= any('|' in x for x in gt)
            if count>=128:break
    finally:
        proc.stdout.close();proc.terminate();proc.wait()
    if not phased:raise ValueError('No phased diploid reference genotypes found')
    chunks=f'chunks.{chrom}.tsv'
    windows=[]
    if c.get('allow_test_reference') and expected[chrom]<1000000:
        windows=['--window-cm','0.005','--window-mb','0.002','--window-count','100','--buffer-cm','0.001','--buffer-mb','0.0005','--buffer-count','10']
    else:
        windows=['--window-count','20000']
    run([tool(c,'GLIMPSE2_chunk'),'--input',ref,'--map',localmap,'--region',f'{chrom}:1-{expected[chrom]}','--sequential',
         *windows,'--output',chunks,'--threads',a.threads])
    if not Path(chunks).read_text().strip():raise ValueError('GLIMPSE2 produced no chunks')
    Path(f'reference.{chrom}.provenance.json').write_text(json.dumps({'assembly':'GRCh38','panel':str(panel.resolve()),
        'panel_url':f'{PANEL_BASE}/{filename}' if panel.parent==cache and panel.name==filename else None,
        'genetic_map':str(mapfile.resolve()),'version':VERSION,'variants':'biallelic SNPs','samples':len(samples),
        'reference_fasta':c['reference'],'source_contig':panel_chrom,
        'source_contig_length':declared_length,'fasta_contig_length':expected[chrom],
        'length_metadata_source':'FASTA index' if declared_length is None else 'panel header matched to FASTA index',
        'ref_allele_validation':'bcftools norm -c e: every retained SNP must match the FASTA',
        'panel_integrity':panel_integrity},indent=2)+'\n')

def phase(a,c):
    prefix='split'
    run([tool(c,'GLIMPSE2_split_reference'),'--reference',a.reference,'--map',a.map,
         '--input-region',a.input_region,'--output-region',a.output_region,'--output',prefix,'--threads',a.threads])
    bins=list(Path('.').glob('split*.bin'))
    if len(bins)!=1:raise ValueError(f'Expected one binary reference chunk, found {len(bins)}')
    count=len(run([c['tools']['bcftools'],'query','-l',a.reference],capture_output=True,text=True).stdout.splitlines())
    if count<2:raise ValueError('At least two phased reference individuals are required')
    states=['--Kinit',min(1000,2*count),'--Kpbwt',min(2000,count)]
    if c.get('allow_test_reference'):states+=['--pbwt-modulo-cm','0.001']
    out=f'{c["sample"]}.{a.chrom}.{a.chunk}.bcf'
    run([tool(c,'GLIMPSE2_phase'),'--bam-file',a.bam,'--ind-name',c['sample'],'--reference',bins[0],
         '--output',out,'--threads',a.threads,*states,'--mapq',c['min_mapping_quality'],'--baseq',c['min_base_quality'],'--contigs-fai',c['reference']+'.fai'])
    run([c['tools']['bcftools'],'index','-f',out])

def ligate(a,c):
    import re
    ordered=sorted(a.inputs,key=lambda p:int(re.search(r'\.(\d+)\.bcf$',p).group(1)))
    Path('chunks.list').write_text('\n'.join(ordered)+'\n')
    out=f'{c["sample"]}.{a.chrom}.imputed.bcf'
    run([tool(c,'GLIMPSE2_ligate'),'--input','chunks.list','--output',out,'--threads',a.threads])
    run([c['tools']['bcftools'],'index','-f',out])

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['reference','phase','ligate'])
    p.add_argument('--config',required=True);p.add_argument('--chrom',required=True);p.add_argument('--threads',type=int,default=2)
    p.add_argument('--reference');p.add_argument('--map');p.add_argument('--bam');p.add_argument('--chunk')
    p.add_argument('--input-region');p.add_argument('--output-region');p.add_argument('--inputs',nargs='+')
    a=p.parse_args();c=json.loads(Path(a.config).read_text());globals()[a.stage](a,c)
if __name__=='__main__':main()
