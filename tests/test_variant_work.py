"""Interrupted and failed callers retain scratch without touching source inputs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.variant_work import sample_work


class VariantWorkTests(unittest.TestCase):
    def test_interruption_preserves_partial_files_and_input_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'raw.bam'
            raw.write_bytes(b'original input')
            result = {'status': 'running'}
            with self.assertRaises(KeyboardInterrupt):
                with sample_work(root, 'S1', result) as work:
                    (work / 'sample.bam').symlink_to(raw)
                    (work / 'partial.vcf').write_text('partial evidence')
                    raise KeyboardInterrupt()
            self.assertEqual((work / 'partial.vcf').read_text(), 'partial evidence')
            self.assertEqual(raw.read_bytes(), b'original input')
            self.assertEqual(result['retained_work_dir'], str(work))
            receipt = json.loads((work / 'retained_work.json').read_text())
            self.assertIn('KeyboardInterrupt', receipt['reason'])
            self.assertFalse(receipt['automatic_partial_resume'])

    def test_caught_caller_failure_preserves_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {'status': 'running'}
            with sample_work(Path(tmp), 'S1', result) as work:
                (work / 'caller.log').write_text('caller failed')
                result['status'] = 'partial_failure'
            self.assertTrue((work / 'caller.log').exists())
            self.assertEqual(json.loads((work / 'retained_work.json').read_text())['reason'], 'partial_failure')

    def test_success_only_removes_scratch_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'raw.bam'
            raw.write_bytes(b'original input')
            result = {'status': 'running'}
            with sample_work(root, 'S1', result) as work:
                (work / 'sample.bam').symlink_to(raw)
                result['status'] = 'complete'
            self.assertFalse(work.exists())
            self.assertEqual(raw.read_bytes(), b'original input')
            self.assertNotIn('retained_work_dir', result)


class CompatibleStageSignatureTests(unittest.TestCase):
    def test_maintenance_release_reuses_only_reviewed_signature_version(self):
        from oncotracer_cli import runtime
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'reads.bam'
            source.write_bytes(b'unchanged alignment')
            with patch.object(runtime, '__version__', '2.3.0'):
                previous = runtime.StageLedger.signature('alignment', ['samtools', 'sort'], [source])
            with patch.object(runtime, '__version__', '2.3.1rc3'):
                current = runtime.StageLedger.signature('alignment', ['samtools', 'sort'], [source])
                self.assertEqual(previous, current)
                self.assertNotEqual(current, runtime.StageLedger.signature('alignment', ['samtools', 'different'], [source]))
                source.write_bytes(b'changed alignment content')
                self.assertNotEqual(current, runtime.StageLedger.signature('alignment', ['samtools', 'sort'], [source]))
            with patch.object(runtime, '__version__', '2.4.0'):
                self.assertNotEqual(previous, runtime.StageLedger.signature('alignment', ['samtools', 'sort'], [source]))
