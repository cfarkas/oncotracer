"""Task reservations must reach real tool heaps and preserve scientific settings."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

BIN = Path(__file__).resolve().parents[1] / 'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
from task_resources import for_task
import continuation
spec = importlib.util.spec_from_file_location('glimpse_budget_pipeline', BIN / 'pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class TaskResourcesTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(threads=8, memory=48, memory_gb=48, sample='S',
                           tools={'gatk':'/fixture/gatk'}, resources=[], min_depth=10,
                           min_mapping_quality=30, min_base_quality=20,
                           germline_resource='population.vcf.gz', panel_of_normals='pon.vcf.gz')

    def test_somatic_heap_and_threads_use_task_reservation(self):
        original = copy.deepcopy(self.config)
        task = for_task(self.config, threads=2, memory_gb=16)
        command = pipeline.gatk(task, ['Mutect2'])
        self.assertEqual(command, ['/fixture/gatk', '--java-options',
                                   '-Xmx11g -XX:ActiveProcessorCount=2', 'Mutect2'])
        self.assertEqual(self.config, original)
        self.assertEqual(continuation.signature(task), continuation.signature(original))
        self.assertEqual(task['min_mapping_quality'], 30)

    def test_fractional_budget_is_not_rounded_up(self):
        task = for_task(self.config, threads=1, memory_gb=3.75)
        self.assertEqual(task['memory_gb'], 3.75)
        self.assertEqual(task['memory'], 3.75)
        self.assertIn('-Xmx2g -XX:ActiveProcessorCount=1', pipeline.gatk(task, ['Mutect2']))

    def test_without_memory_override_keeps_original_limit(self):
        task = for_task(self.config, threads=1)
        self.assertEqual(task['memory_gb'], 48)
        self.assertEqual(task['threads'], 1)

    def test_rejects_invalid_or_excessive_task_budgets(self):
        for threads in (0, -1, True, 1.5, 9):
            with self.subTest(threads=threads), self.assertRaises(ValueError):
                for_task(self.config, threads=threads, memory_gb=16)
        for memory in (0, -1, True, float('nan'), float('inf'), 49):
            with self.subTest(memory=memory), self.assertRaises(ValueError):
                for_task(self.config, threads=1, memory_gb=memory)


if __name__ == '__main__':
    unittest.main()
