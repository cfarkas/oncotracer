#!/usr/bin/env python3
"""Exercise the real trait extraction with a caller selected by --bcftools."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'oncotracer_cli/lpwgs/bin'))
from bcftools_compat import trait_call_options
import traits_lpwgs


def main():
    import pysam
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bcftools',required=True)
    parser.add_argument('--samtools',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    reference=root/'ref.fa';reference.write_text('>chr1\n'+'A'*1000+'\n');pysam.faidx(str(reference))
    bam=root/'SYNTHETIC.bam'
    with pysam.AlignmentFile(str(bam),'wb',header={'HD':{'VN':'1.6','SO':'coordinate'},'SQ':[{'SN':'chr1','LN':1000}],'RG':[{'ID':'r','SM':'SYNTHETIC'}]}) as out:
        for pos,alternate in [(100,False),(300,True)]:
            for i in range(30):
                read=pysam.AlignedSegment();read.query_name=f'synthetic_{pos}_{i}'
                seq=list('A'*100)
                if alternate:seq[49]='G'
                read.query_sequence=''.join(seq);read.flag=0;read.reference_id=0;read.reference_start=pos-50
                read.mapping_quality=60;read.cigarstring='100M';read.query_qualities=pysam.qualitystring_to_array('I'*100)
                read.set_tag('RG','r');out.write(read)
    pysam.index(str(bam))
    panel=root/'traits.tsv'
    fields=['chrom','pos','rsid','ref','alt','effect_allele','trait','model','association','limitation','pubmed']
    with panel.open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t');writer.writeheader()
        for pos in (100,300,700):
            writer.writerow(dict(zip(fields,['chr1',pos,f'synthetic{pos}','A','G','G','synthetic','dominant','synthetic only','not biological','.'])))
    config={'reference':str(reference),'traits_panel':str(panel),'gwas':False,'test_mode':True,
            'tools':{'bcftools':args.bcftools,'samtools':args.samtools},'min_mapping_quality':30,'min_base_quality':20,
            'min_depth':10,'min_gq':20,'trait_mode':'strict','chrom_prefix':'chr','regions':None}
    os.chdir(root)
    traits_lpwgs.run(SimpleNamespace(sample='SYNTHETIC',bam=str(bam),imputed_vcfs=[],cached_trait_data=None),config)
    rows={int(r['pos']):r for r in csv.DictReader((root/'SYNTHETIC.traits.tsv').open(),delimiter='\t')}
    assert rows[300]['genotype']=='G/G' and rows[300]['status']=='called', rows[300]
    assert rows[700]['status'] not in traits_lpwgs.ASSIGNED and int(rows[700]['DP'])==0,rows[700]
    # Old bcftools can omit hom-ref likelihoods. Never manufacture GQ to resolve it.
    if rows[100]['status'] in traits_lpwgs.ASSIGNED:
        assert rows[100]['genotype']=='A/A' and float(rows[100]['GQ'])>=20,rows[100]
    result={'synthetic_only':True,'passed':True,'version':subprocess.check_output([args.bcftools,'--version'],text=True).splitlines()[0],
            'options':trait_call_options(args.bcftools),'sites':{str(k):{f:r[f] for f in ('genotype','status','DP','GQ')} for k,r in rows.items()}}
    (root/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
