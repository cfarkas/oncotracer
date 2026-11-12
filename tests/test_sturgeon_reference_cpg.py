import sys,tempfile,unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bin/scripts'))
STURGEON_AVAILABLE=True
try:
 from native_sturgeon_prepare import streaming_modkit_file_to_bed
 from sturgeon.callmapping import modkit_file_to_bed
except ModuleNotFoundError as error:
 if error.name != 'sturgeon' and not str(error.name).startswith('sturgeon.'):
  raise
 STURGEON_AVAILABLE=False
@unittest.skipUnless(STURGEON_AVAILABLE,'Run converter parity in the user-licensed Sturgeon environment')
class StreamingParityTests(unittest.TestCase):
 def test_exact_parity_across_chunks_ties_thresholds_and_boundaries(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);probes=d/'probes.bed';pd.DataFrame({'chr':[1,1,1,2,2],'start':[100,200,-1,50,100],'end':[101,201,0,51,101],'ID_REF':['a','b','bad','c','d']}).to_csv(probes,sep=' ',index=False)
   rows=[]
   for chrom,pos,q,code,base in [('chr1',74,.9,'m','C'),('chr1',75,.9,'m','C'),('chr1',126,.1,'m','C'),('chr1',127,.9,'m','C'),('chr1',100,.7,'m','C'),('chr1',101,.3,'m','C'),('chr1',200,.8,'m','C'),('chr1',205,.9,'m','C'),('chr1',201,.2,'m','C'),('chr2',50,.1,'m','C'),('chr2',100,.9,'h','C'),('chr2',101,.9,'m','A'),('chr1',-1,.99,'m','C'),('.',100,.99,'m','C')]:
    rows.append({'read_id':str(len(rows)),'chrom':chrom,'ref_position':pos,'mod_qual':q,'mod_code':code,'canonical_base':base,'modified_primary_base':base,'ref_kmer':'AACGT','ref_mod_strand':'+'})
   f=d/'extract.txt';pd.DataFrame(rows).to_csv(f,sep='\t',index=False)
   expected=modkit_file_to_bed(str(f),str(probes)).sort_values('ID_REF').reset_index(drop=True)
   for size in (1,3,100):
    observed=streaming_modkit_file_to_bed(str(f),str(probes),chunksize=size).sort_values('ID_REF').reset_index(drop=True)
    pd.testing.assert_frame_equal(expected,observed)
 def test_randomized_parity_with_repeated_probes_and_reads(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);rng=np.random.default_rng(18);probes=d/'probes.bed';starts=rng.integers(10,500,35)
   pd.DataFrame({'chr':[1]*35,'start':starts,'end':starts+1,'ID_REF':['p'+str(i) for i in range(35)]}).to_csv(probes,sep=' ',index=False)
   x=pd.DataFrame({'read_id':['x']*2000,'chrom':['chr1']*2000,'ref_position':rng.integers(-1,600,2000),'mod_qual':rng.random(2000),'mod_code':['m']*2000,'canonical_base':['C']*2000,'modified_primary_base':['C']*2000,'ref_kmer':['AACGT']*2000,'ref_mod_strand':['+']*2000});f=d/'random.txt';x.to_csv(f,sep='\t',index=False)
   expected=modkit_file_to_bed(str(f),str(probes)).sort_values('ID_REF').reset_index(drop=True)
   actual=streaming_modkit_file_to_bed(str(f),str(probes),chunksize=17).sort_values('ID_REF').reset_index(drop=True)
   pd.testing.assert_frame_equal(expected,actual)
 def test_non_cpg_implicit_cytosines_do_not_swamp_observed_cpg(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);probes=d/'probes.bed';pd.DataFrame({'chr':[1],'start':[100],'end':[101],'ID_REF':['a']}).to_csv(probes,sep=' ',index=False)
   rows=[{'read_id':str(i),'chrom':'chr1','ref_position':90+i%20,'mod_qual':.0,'mod_code':'m','canonical_base':'C','modified_primary_base':'C','ref_kmer':'AACAT','ref_mod_strand':'+'} for i in range(100)]
   rows.append(dict(rows[0],ref_position=100,mod_qual=.99,ref_kmer='AACGT'))
   # Lowercase reverse-strand reference context must also survive.
   rows.append(dict(rows[0],ref_position=101,mod_qual=.99,ref_kmer='acgta',ref_mod_strand='-'))
   f=d/'extract.txt';pd.DataFrame(rows).to_csv(f,sep='\t',index=False)
   actual=streaming_modkit_file_to_bed(str(f),str(probes),chunksize=7)
   self.assertEqual(int(actual.methylation_calls.sum()),1)
   self.assertEqual(int(actual.unmethylation_calls.sum()),0)
 def test_unverified_reference_context_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);probes=d/'probes.bed';pd.DataFrame({'chr':[1],'start':[100],'end':[101],'ID_REF':['a']}).to_csv(probes,sep=' ',index=False)
   f=d/'extract.txt';pd.DataFrame([{'read_id':'a','chrom':'chr1','ref_position':100,'mod_qual':0,'mod_code':'m','canonical_base':'C','modified_primary_base':'C','ref_kmer':'.','ref_mod_strand':'+'}]).to_csv(f,sep='\t',index=False)
   with self.assertRaisesRegex(ValueError,'Reference context missing'):streaming_modkit_file_to_bed(str(f),str(probes))

if __name__=='__main__':unittest.main()
