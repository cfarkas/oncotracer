#!/usr/bin/env python3
"""Merge compatible evidence VCFs without converting absent sites to reference."""
import argparse,re,subprocess
from pathlib import Path

def merge(files,output,bcftools='bcftools'):
 if len(files)<2:raise ValueError('At least two sample VCFs are required')
 samples=set();identity=None
 for file in files:
  if not any(Path(str(file)+suffix).is_file() for suffix in ['.tbi','.csi']):raise ValueError('Missing index: '+str(file))
  header=subprocess.run([bcftools,'view','-h',str(file)],check=True,text=True,capture_output=True).stdout
  names=next(l for l in header.splitlines() if l.startswith('#CHROM')).split('\t')[9:]
  if len(names)!=1 or names[0] in samples:raise ValueError('Each input requires one unique sample name')
  samples.update(names)
  metadata=[l for l in header.splitlines() if l.startswith(('##LPWGSReferenceSequenceSHA256=','##LPWGSReferenceDictionarySHA256=','##LPWGSClinVarSHA256=','##LPWGSSchema='))]
  if len(metadata)!=4:raise ValueError('Missing normalized export provenance: '+str(file))
  schema=sorted(l for l in header.splitlines() if l.startswith(('##FORMAT=','##contig=')))
  signature=(sorted(metadata),schema)
  if identity is None:identity=signature
  elif signature!=identity:raise ValueError('Reference, annotation release, or FORMAT schema differs')
 if Path(output).exists():raise ValueError('Output already exists: '+str(output))
 subprocess.run([bcftools,'merge','--merge','none','--info-rules','-','-Oz','-o',str(output),*map(str,files)],check=True)
 subprocess.run([bcftools,'index','-f','-t',str(output)],check=True)
 return sorted(samples)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bcftools',default='bcftools');p.add_argument('--output',required=True);p.add_argument('files',nargs='+');a=p.parse_args();print('Merged samples:',', '.join(merge(a.files,a.output,a.bcftools)))
