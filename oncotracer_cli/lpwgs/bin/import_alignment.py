#!/usr/bin/env python3
"""Validated reuse of an existing alignment or callset; never rewrites inputs."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import continuation


def validate_bam(bam, sample, reference, samtools):
    subprocess.run([samtools,'quickcheck','-v',str(bam)],check=True)
    header=subprocess.check_output([samtools,'view','-H',str(bam)],text=True)
    lengths={f[0]:int(f[1]) for line in Path(str(reference)+'.fai').read_text().splitlines() if (f:=line.split('\t'))}
    names=set(); sorted_bam=False; sequence_count=0
    for line in header.splitlines():
        fields={s.split(':',1)[0]:s.split(':',1)[1] for s in line.split('\t')[1:] if ':' in s}
        if line.startswith('@HD'): sorted_bam=fields.get('SO')=='coordinate'
        if line.startswith('@RG') and fields.get('SM'): names.add(fields['SM'])
        if line.startswith('@SQ'):
            sequence_count+=1
            if lengths.get(fields.get('SN'))!=int(fields.get('LN',0)):raise ValueError('Imported BAM/reference contig mismatch')
    if names!={sample}:raise ValueError(f'BAM SM must equal {sample!r}; found {sorted(names)}')
    if not sorted_bam or not sequence_count:raise ValueError('Imported BAM must be coordinate sorted with a sequence dictionary')
    return {'sample':sample,'sequence_count':sequence_count}


def import_bam(a,c):
    st=c['tools']['samtools'];validate_bam(a.input,a.sample,c['reference'],st)
    bam=Path(a.sample+'.bam');bam.symlink_to(Path(a.input).resolve())
    subprocess.run([st,'index','-@',str(max(0,a.threads-1)),str(bam)],check=True)
    path=Path(a.sample+'.coverage.txt')
    with path.open('w') as f:
        subprocess.run([st,'coverage','--ff','0xF04','-q',str(c['min_mapping_quality']),'-Q',str(c['min_base_quality']),str(bam)],stdout=f,check=True)
    with path.open() as f:rows=[r for r in csv.DictReader(f,delimiter='\t') if r['#rname'].removeprefix('chr') in {str(i) for i in range(1,23)}]
    total=sum(int(r['endpos'])-int(r['startpos'])+1 for r in rows)
    qc={'mean_autosomal_depth_after_filters':sum(float(r['meandepth'])*(int(r['endpos'])-int(r['startpos'])+1) for r in rows)/max(1,total),'autosomal_fraction_covered':sum(int(r['covbases']) for r in rows)/max(1,total),'mapping_quality_min':c['min_mapping_quality'],'base_quality_min':c['min_base_quality'],'test_mode':c['test_mode'],'imported_bam':str(Path(a.input).resolve()),'alignment_reused':True,'duplicate_handling':'Existing BAM duplicate flags retained; no re-alignment or duplicate marking'}
    Path(a.sample+'.qc.json').write_text(json.dumps(qc,indent=2)+'\n')
    continuation.record(SimpleNamespace(stage='merge',sample=a.sample,lane=None,kind=None),c)


def import_calls(a,c):
    target=f'{a.sample}.{a.kind}.vcf.gz'
    # Nextflow stages inputs as symlinks. Never let a same-named output follow
    # such a symlink and overwrite the donor callset (or an existing result).
    output=Path(target)
    if output.exists() or output.is_symlink() or output.resolve()==Path(a.input).resolve():
        raise ValueError('Imported VCF output already exists or aliases the source; source callsets must never be overwritten')
    bc=c['tools']['bcftools'];samples=subprocess.check_output([bc,'query','-l',a.input],text=True).splitlines()
    if samples!=[a.sample]:raise ValueError('Imported VCF must contain exactly the expected sample')
    # Preserve existing filter and allele evidence. Explicit imports are provenance,
    # not a claim of equivalent caller/settings or independently validated biology.
    subprocess.run([bc,'norm','-m','-any','--multi-overlaps','.','--check-ref','e','-f',c['reference'],'-Oz','-o',target,a.input],check=True)
    subprocess.run([bc,'index','-t',target],check=True)
    Path(f'{a.sample}.{a.kind}.import.json').write_text(json.dumps({'input':str(Path(a.input).resolve()),'kind':a.kind,'filters_preserved':True,'genotypes':'caller evidence retained','caller_equivalence_verified':False},indent=2)+'\n')
    if a.kind=='somatic':Path(f'{a.sample}.somatic.json').write_text(json.dumps({'status':'imported_somatic_candidates','matched_normal':False,'interpretation':'Imported caller results remain candidates; imputation cannot establish somatic origin.'})+'\n')
    continuation.record(SimpleNamespace(stage=a.kind,sample=a.sample,lane=None,kind=None),c)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['bam','calls']);p.add_argument('--input',required=True);p.add_argument('--sample',required=True);p.add_argument('--config',required=True);p.add_argument('--kind',choices=['germline','somatic']);p.add_argument('--threads',type=int,default=1)
    a=p.parse_args();c=json.loads(Path(a.config).read_text());(import_bam if a.stage=='bam' else import_calls)(a,c)
if __name__=='__main__':main()
