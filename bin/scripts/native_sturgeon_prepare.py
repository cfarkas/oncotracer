#!/usr/bin/env python3
"""Bounded Sturgeon conversion with mandatory reference-CpG context filtering."""
import sys,time
import numpy as np
import pandas as pd
from sturgeon.utils import read_probes_file

def streaming_modkit_file_to_bed(input_file,probes_file,margin=25,neg_threshold=.3,pos_threshold=.7,fivemc_code='m',chunksize=250000):
 probes=read_probes_file(probes_file);blocks={}
 for chrom in np.unique(probes['chr']):
  block=probes[(probes['chr']==chrom)&(probes['start']>-1)].sort_values('start').reset_index(drop=True).copy()
  blocks['chr'+str(chrom.item())]=[block,np.zeros(len(block),dtype=np.int64),np.zeros(len(block),dtype=np.int64)]
 columns=['chrom','ref_position','mod_qual','mod_code','canonical_base','modified_primary_base','ref_kmer','ref_mod_strand']
 dtypes={k:'category' for k in ['chrom','mod_code','canonical_base','modified_primary_base','ref_kmer','ref_mod_strand']};dtypes.update(ref_position='int64',mod_qual='float64')
 processed=0;last=time.monotonic()
 with pd.read_csv(input_file,sep='\t',usecols=columns,dtype=dtypes,chunksize=chunksize) as reader:
  for chunk in reader:
   processed+=len(chunk)
   chunk=chunk[(chunk.mod_code==fivemc_code)&(chunk.canonical_base=='C')&(chunk.modified_primary_base=='C')&(chunk.ref_position!=-1)]
   chunk=chunk[chunk.chrom.isin(blocks)]
   if (chunk.ref_kmer=='.').any():raise ValueError('Reference context missing: regenerate modkit extract with --cpg --reference; unrestricted implicit cytosines are unsafe for CpG classification')
   context=chunk.ref_kmer.str.upper()
   cpg=((chunk.ref_mod_strand=='+')&(context.str.slice(2,4)=='CG'))|((chunk.ref_mod_strand=='-')&(context.str.slice(1,3)=='CG'))
   chunk=chunk[cpg]
   for chrom,part in chunk.groupby('chrom',observed=True):
    if chrom not in blocks:continue
    block,positive,negative=blocks[chrom];starts=block.start.to_numpy()
    for mask,counts in [(part.mod_qual>pos_threshold,positive),(part.mod_qual<neg_threshold,negative)]:
     positions=np.sort(part.loc[mask,'ref_position'].to_numpy())
     # Match locked upstream inclusive query bounds exactly, including start+margin+1.
     counts+=np.searchsorted(positions,starts+margin+1,side='right')-np.searchsorted(positions,starts-margin,side='left')
   if time.monotonic()-last>=20:
    print(f'Streamed {processed:,} modkit rows; retained bounded probe-count arrays',flush=True);last=time.monotonic()
 result=[]
 for block,positive,negative in blocks.values():
  # Median of all non-neutral +/-1 calls: majority, with ties remaining unmeasured.
  block['methylation_calls']=(positive>negative).astype('int64')
  block['unmethylation_calls']=(negative>positive).astype('int64')
  block['total_calls']=block.methylation_calls+block.unmethylation_calls
  result.append(block)
 print(f'Streaming conversion completed: {processed:,} rows',flush=True)
 return pd.concat(result)

if __name__ == '__main__':
 import argparse
 from pathlib import Path
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('-i',required=True);p.add_argument('-o',required=True);p.add_argument('--probes-file',required=True)
 a=p.parse_args()
 counts=streaming_modkit_file_to_bed(a.i,a.probes_file)
 measured=counts[counts.total_calls>0]
 bed=pd.DataFrame(dict(chrom=measured.chr,chromStart=measured.start,chromEnd=measured.end,
                       methylation_call=measured.methylation_calls,probe_id=measured.ID_REF))
 if bed.probe_id.duplicated().any():raise ValueError('Duplicate Sturgeon probe IDs')
 output=Path(a.o);output.parent.mkdir(parents=True,exist_ok=True)
 temporary=output.with_suffix('.tmp');bed.to_csv(temporary,sep='\t',index=False);temporary.replace(output)
