#!/usr/bin/env python3
"""Run actual FFPE models and artifact extraction on artificial paired reads."""
import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oncotracer_cli import ffperase


def fixture(root,pairs):
    import pysam
    root.mkdir(parents=True,exist_ok=False)
    rng=random.Random(42);bases=[rng.choice('ACGT') for _ in range(2000)];bases[999]='A'
    sequence=''.join(bases);reference=root/'ref.fa';reference.write_text('>chr1\n'+sequence+'\n');pysam.faidx(str(reference))
    bam=root/'SYNTHETIC.bam';reads=[]
    for i in range(pairs):
        left=850+i%60;right=left+200
        for mate,start,flag in [(1,left,99),(2,right,147)]:
            r=pysam.AlignedSegment();r.query_name='synthetic_'+str(i);seq=list(sequence[start:start+150]);mismatch=0
            if i%2==0 and start<=999<start+150:seq[999-start]='G';mismatch=1
            cigar='150M'
            if i%2==0 and start<=1099<start+150:
                offset=1100-start;seq.insert(offset,'T');cigar=str(offset)+'M1I'+str(150-offset)+'M';mismatch+=1
            r.query_sequence=''.join(seq);r.flag=flag;r.reference_id=0;r.reference_start=start
            r.mapping_quality=60;r.cigarstring=cigar;r.query_qualities=pysam.qualitystring_to_array('I'*len(seq))
            r.next_reference_id=0;r.next_reference_start=right if mate==1 else left;r.template_length=350 if mate==1 else -350
            r.set_tag('RG','r');r.set_tag('NM',mismatch);reads.append(r)
    with pysam.AlignmentFile(str(bam),'wb',header={'HD':{'VN':'1.6','SO':'coordinate'},'SQ':[{'SN':'chr1','LN':2000}],'RG':[{'ID':'r','SM':'SYNTHETIC','LB':'synthetic','PL':'ILLUMINA','PU':'synthetic'}]}) as out:
        for r in sorted(reads,key=lambda r:r.reference_start):out.write(r)
    pysam.index(str(bam));vcf=root/'candidates.vcf'
    vcf.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=2000>\n##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC\nchr1\t1000\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n')
    with vcf.open('a') as handle:handle.write('chr1\t1100\t.\t'+bases[1099]+'\t'+bases[1099]+'T\t60\tPASS\t.\tGT\t0/1\n')
    return reference,bam,vcf


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','models','samtools','gatk'):p.add_argument('--'+key,required=True)
    p.add_argument('--sif');p.add_argument('--prefix');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    detection=ffperase.discover(args.source,args.models,args.sif,args.prefix)
    probe=ffperase.preflight_models(detection);stages=[]
    def run(stage,command,**kwargs):
        stages.append(stage)
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',_JAVA_OPTIONS='-Xmx1g -XX:ActiveProcessorCount=2')
        subprocess.run(list(map(str,command)),check=True,env=env,**kwargs)
    results={}
    for name,pairs in [('low',1),('adequate',60)]:
        reference,bam,vcf=fixture(root/name,pairs)
        output=root/name/'assessment';result=ffperase.assess(vcf,root/name/'assessed.vcf',detection=detection,bam=bam,reference=reference,directory=output,run=run,tools={'samtools':args.samtools,'gatk':args.gatk})
        results[name]={k:v for k,v in result.items() if k!='resources'}
        if name=='low':
            assert result['status']=='not_assessed',result
            assert not any('classify' in s or 'features' in s for s in stages),stages
        else:
            assert result['status']=='complete',result
            assert all('ffperase-'+kind+'-classify' in stages for kind in ('snvs','indels')),stages
    report={'synthetic_only':True,'passed':True,'model_probe':probe,'assessments':results,'stages':stages}
    (root/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
