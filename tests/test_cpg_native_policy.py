"""Regression tests for native probe identity, reference context and resume."""
import json
import tempfile
import unittest
from pathlib import Path
from oncotracer_cli.methylation import _load_reusable_sample_record, _seal_sample_record, _validate_preserved_reference, _marlin_probe_coverage, resolve_methylation_request
from oncotracer_cli.runtime import OncoTracerError
from tests.test_native_methylation import Fixture
import tests.test_native_methylation as methylation_tests

class CpGPolicyTests(unittest.TestCase):
    def test_valid_unmethylated_calls_are_measured_not_missing(self):
        calls="chr1\t100\t101\tm\t0\t+\t100\t101\t0,0,0\t10\t0.0\t0\t10\t0\t0\t0\t0\t0\n"
        temporary,_,_,_,status=methylation_tests.NativeMethylationTests()._run_fixture(calls,gpu=False)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(status["overall_status"],"complete")
        self.assertEqual(status["samples"][0]["modified_cpg_calls"],0)
        self.assertEqual(status["samples"][0]["covered_cpg_rows"],1)
    def test_altered_prediction_cannot_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'calls.bed').write_text('calls');(p/'prediction.tsv').write_text('0.92454')
            record=dict(sample='S',status='complete',bedmethyl='calls.bed',classification='prediction.tsv')
            _seal_sample_record(record,p);(p/'record.json').write_text(json.dumps(record))
            self.assertIsNotNone(_load_reusable_sample_record(p/'record.json',p,'S'))
            (p/'prediction.tsv').write_text('0.99999')
            self.assertIsNone(_load_reusable_sample_record(p/'record.json',p,'S'))
    def test_reference_preservation_requires_matching_dictionary(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'ref.fai').write_text('chr1\t1000\t6\t60\t61\n');(p/'bam.sam').write_text('@SQ\tSN:chr1\tLN:1001\n')
            with self.assertRaises(OncoTracerError):_validate_preserved_reference(p/'bam.sam',p/'ref.fai')
            (p/'bam.sam').write_text('@SQ\tSN:chr1\tLN:1000\n');_validate_preserved_reference(p/'bam.sam',p/'ref.fai')
    def test_preserved_modbam_does_not_require_dorado(self):
        with tempfile.TemporaryDirectory() as d:
            f=Fixture(Path(d),'marlin');cfg=f.config();cfg.pop('methylation_pod5_dir');cfg['methylation_modbam']=str(f._asset('input.bam','bam'));cfg['methylation_modbam_alignment']='preserve';cfg['methylation_dorado_executable']='/missing/dorado'
            self.assertIsNone(resolve_methylation_request(cfg,mode='ont').dorado)
    def test_probe_coverage_is_unique_and_uses_exact_coordinates(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);probes=p/'probes';probes.write_text('chr1\t100\t101\tp1\nchr1\t101\t102\tp1\nchr1\t200\t202\tp2\n')
            calls=p/'calls';calls.write_text(''.join(f'chr1\t{pos}\t{pos+1}\tm\t0\t+\t0\t0\t0\t1\t0\t0\n' for pos in [100,101,201]))
            self.assertEqual(_marlin_probe_coverage(calls,probes),1)

if __name__=='__main__':unittest.main()
