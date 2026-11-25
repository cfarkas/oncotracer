from pathlib import Path
from types import SimpleNamespace
import tempfile, unittest
from oncotracer_cli.launch_ont_profile import choose_profile
from oncotracer_cli.runtime import OncoTracerError

class LaunchOntProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sample=SimpleNamespace(sample='CASE',fastq_dir=self.root)
    def reads(self,model='',name='reads.fastq'):
        (self.root/name).write_text('@read'+(' basecall_model_version_id='+model if model else '')+'\nACGT\n+\nIIII\n')
    def test_sup520_metadata_selects_matching_model(self):
        self.reads('dna_r10.4.1_e8.2_400bps_sup@v5.2.0')
        result=choose_profile([self.sample]);self.assertEqual(result['profile'],'r1041_e82_400bps_sup_v520')
        self.assertEqual(result['selection'],'fastq_metadata')
    def test_missing_metadata_requires_explicit_recorded_profile(self):
        self.reads()
        with self.assertRaisesRegex(OncoTracerError,'Supply --ont-profile'):choose_profile([self.sample])
        self.assertEqual(choose_profile([self.sample],'r1041_e82_400bps_sup_v500')['selection'],'explicit')
    def test_override_cannot_mask_conflicting_header(self):
        self.reads('dna_r10.4.1_e8.2_400bps_sup@v5.2.0')
        with self.assertRaisesRegex(OncoTracerError,'conflicts'):choose_profile([self.sample],'r1041_e82_400bps_sup_v500')
    def test_mixed_protocols_and_unsupported_models_are_rejected(self):
        self.reads('dna_r10.4.1_e8.2_400bps_sup@v5.2.0')
        self.reads('dna_r10.4.1_e8.2_400bps_sup@v5.0.0','second.fastq')
        with self.assertRaisesRegex(OncoTracerError,'different basecalling'):choose_profile([self.sample])
        self.reads('dna_r9.4.1_450bps_hac@v3.3')
        with self.assertRaisesRegex(OncoTracerError,'No managed full-analysis'):choose_profile([self.sample])
    def test_one_missing_file_prevents_automatic_cohort_assignment(self):
        self.reads('dna_r10.4.1_e8.2_400bps_sup@v5.0.0')
        self.reads(name='second.fastq')
        with self.assertRaisesRegex(OncoTracerError,'Supply --ont-profile'):choose_profile([self.sample])
if __name__=='__main__':unittest.main()
