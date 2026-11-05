import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from oncotracer_cli.glimpse_resume import completed_export, identical_bundle, retain_memory_budget


class MaintenanceResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_relocated_bundle_requires_every_code_and_data_byte_to_match(self):
        a, b = self.root/'new', self.root/'old'
        for root in (a, b):
            (root/'bin').mkdir(parents=True)
            (root/'main.nf').write_text('workflow {}')
            (root/'bin/task.py').write_text('print(1)')
        runtime = {'A': {'root': str(b)}}
        self.assertEqual(identical_bundle(a, runtime), b)
        (b/'bin/task.py').write_text('print(2)')
        self.assertEqual(identical_bundle(a, runtime), a)
        (b/'bin/task.py').write_text('print(1)')
        (b/'bin/extra.py').write_text('print(2)')
        self.assertEqual(identical_bundle(a, runtime), a)
        (b/'bin/extra.py').unlink()
        (b/'bin/task.py').unlink()
        (b/'bin/task.py').symlink_to(a/'bin/task.py')
        self.assertEqual(identical_bundle(a, runtime), a)

    def test_previous_memory_never_exceeds_current_allocation(self):
        params = {'memory': '16GB', 'threads': 2}
        retain_memory_budget(params, {'memory': 12.5, 'threads': 2})
        self.assertEqual(params['memory'], '12800MB')
        params['memory'] = '8GB'
        retain_memory_budget(params, {'memory': 12.5, 'threads': 2})
        self.assertEqual(params['memory'], '8GB')

    def test_complete_workflow_reuse_requires_success_runtime_and_output_checksum(self):
        folder = self.root/'cohort_vcf/A.cohort_vcf'
        folder.mkdir(parents=True)
        vcf = folder/'A.annotated.vcf.gz'
        vcf.write_bytes(b'completion-checksum-fixture')
        Path(str(vcf)+'.tbi').write_bytes(b'index')
        (self.root/'START_HERE.html').write_text('complete report')
        runtime = {'sample': 'A', 'reference': 'verified.fa', 'root': '/identical/bundle'}
        summary = {'sample': 'A', 'runtime_signature': hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                   'vcf_sha256': hashlib.sha256(vcf.read_bytes()).hexdigest()}
        (folder/'A.cohort_vcf.json').write_text(json.dumps(summary))
        row = {'status': 'complete', 'annotated_vcf': str(vcf)}
        check = {'rejected': [], 'ligated': 22}
        args = (self.root, row, runtime, copy.deepcopy(runtime), check)
        self.assertEqual(completed_export(*args), vcf)
        changed = {**runtime, 'reference': 'different.fa'}
        self.assertIsNone(completed_export(self.root, row, runtime, changed, check))
        for status in ('running', 'failed', 'interrupted'):
            self.assertIsNone(completed_export(self.root, {**row, 'status': status}, runtime, runtime, check))
        self.assertIsNone(completed_export(self.root, row, runtime, runtime, {'rejected': ['corrupt']}))
        vcf.write_bytes(b'corrupted')
        self.assertIsNone(completed_export(*args))
