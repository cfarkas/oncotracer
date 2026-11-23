import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('study_benchmark',ROOT/'scripts/export_study_benchmark.py')
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)


class StudyBenchmarkTests(unittest.TestCase):
    def test_physical_cores_do_not_count_smt_siblings_twice(self):
        topology={'cpus':[{'cpu':0,'core':0,'socket':0},{'cpu':64,'core':0,'socket':0},{'cpu':1,'core':1,'socket':0},{'cpu':65,'core':1,'socket':0}]}
        self.assertEqual(benchmark.core_allocation('0-1,64-65',topology),(4,2))
        self.assertEqual(benchmark.core_allocation('0-1,64-65',None),(4,None))
        with self.assertRaises(ValueError):benchmark.core_allocation('0-2',topology)
        with self.assertRaises(ValueError):benchmark.cpu_list('3-1')

    def test_parallel_intervals_are_not_added_as_wall_time(self):
        items=[dict(stage='calling',start=0,end=10,returncode=0),dict(stage='calling',start=5,end=15,returncode=0),dict(stage='calling',start=20,end=25,returncode=1)]
        result=benchmark.aggregate_intervals('cohort','native',items,0)[0]
        self.assertAlmostEqual(result['active_union_hours']*3600,20,places=2)
        self.assertAlmostEqual(result['summed_command_hours']*3600,25,places=2)
        self.assertAlmostEqual(result['span_hours']*3600,25,places=2)
        self.assertEqual(result['nonzero_returncodes'],1)

    def test_negative_interval_is_rejected(self):
        with self.assertRaises(ValueError):benchmark.interval_union([(10,2)])

    def test_missing_measurements_remain_missing(self):
        self.assertIsNone(benchmark.memory_bytes('-'));self.assertIsNone(benchmark.seconds(''))
        self.assertEqual(benchmark.memory_bytes('0'),0)
        self.assertEqual(benchmark.memory_bytes('1.5 GB'),1.5*1024**3)
        self.assertEqual(benchmark.seconds('1h 2m 3s'),3723)
        self.assertEqual(benchmark.seconds('200ms'),.2)

    def test_sample_names_are_removed_before_aggregation(self):
        samples=['CASE-01','CASE-01.1']
        self.assertEqual(benchmark.normalize_stage('variant-CASE-01.1-clair3-call',samples),'variant-clair3-call')
        self.assertEqual(benchmark.normalize_stage('ont-align-CASE-01',samples),'ont-align')
        with self.assertRaises(ValueError):benchmark.normalize_stage('stage /home/private',samples)

    def test_invalid_validation_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'study';recording=root/'recording';recording.mkdir(parents=True)
            (root/'validation').mkdir()
            (recording/'run_manifest.json').write_text(json.dumps(dict(source_commit='a'*40)))
            (root/'validation/final_status.json').write_text(json.dumps(dict(status='validation_failed',source_commit='a'*40)))
            with self.assertRaisesRegex(ValueError,'passed validation'):benchmark.export(root,recording,Path(d)/'public')
            self.assertFalse((Path(d)/'public').exists())

    def test_bundle_is_deterministic_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'data';data.mkdir();(data/'summary.json').write_text('{"validated":true}\n')
            benchmark.bundle(data,root/'first.tar.gz');benchmark.bundle(data,root/'second.tar.gz')
            self.assertEqual((root/'first.tar.gz').read_bytes(),(root/'second.tar.gz').read_bytes())
            (data/'outside.json').symlink_to(root/'first.tar.gz')
            with self.assertRaisesRegex(ValueError,'symlinks'):benchmark.bundle(data,root/'bad.tar.gz')


if __name__=='__main__':unittest.main()
