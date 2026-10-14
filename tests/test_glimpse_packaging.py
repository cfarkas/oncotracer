"""Execute the GLIMPSE2 workflow resolver from a real standalone zipapp."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from oncotracer_cli import glimpse_pipeline as pipeline
from scripts import build_native_binary as builder

ROOT = Path(__file__).resolve().parents[1]


class GlimpsePackagingTests(unittest.TestCase):
    def test_source_bundle_does_not_need_payload_extraction(self):
        with patch.object(pipeline, 'runtime_root', side_effect=AssertionError('unexpected extraction')):
            self.assertEqual(pipeline.workflow_bundle(), pipeline.BUNDLE)
            self.assertEqual(pipeline.workflow_bundle(pipeline.BUNDLE), pipeline.BUNDLE.resolve())

    def test_real_zipapp_extracts_validated_workflow_and_reads_module_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root/'staging'
            builder.copy_payload_from_tree(ROOT, staging)
            builder.validate_python_sources(staging)
            main = staging/'__main__.py'
            main.write_text('''import json
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli import glimpse_pipeline as pipeline
from oncotracer_cli.glimpse_joint import _implementation_digest
from oncotracer_cli.variants import resolve_variant_request
request = resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh'}, mode='illumina')
with patch('oncotracer_cli.variants.preflight_variant_tools', return_value={'bcftools':'/fixture/bcftools'}), patch.object(pipeline.shutil, 'which', side_effect=lambda name:'/fixture/'+name), patch('oncotracer_cli.glimpse_readiness.check', side_effect=lambda request,tools,toolchain,bundle:tools):
    tools = pipeline.preflight(request)
bundle = pipeline.workflow_bundle()
print(json.dumps({'bundle':str(bundle), 'main_exists':(bundle/'main.nf').is_file(), 'launch_exists':(bundle/'bin/launch.py').is_file(), 'digest':_implementation_digest(), 'nextflow':tools['nextflow']}))
''')
            executable = root/'oncotracer.pyz'
            builder.write_deterministic_zipapp(staging, executable)
            with zipfile.ZipFile(executable) as archive:
                self.assertIn('payload/oncotracer_cli/lpwgs/main.nf', archive.namelist())
                self.assertIn('payload/oncotracer_cli/lpwgs/nextflow.config', archive.namelist())
                self.assertIn('oncotracer_cli/glimpse_pipeline.py', archive.namelist())
                self.assertNotIn('oncotracer_cli/lpwgs/main.nf', archive.namelist())
                self.assertFalse(any('__pycache__' in name for name in archive.namelist()))
            env = {**os.environ, 'ONCOTRACER_PAYLOAD_CACHE':str(root/'cache'), 'ONCOTRACER_ROOT':'', 'PYTHONPATH':''}
            def invoke():
                result = subprocess.run([sys.executable, str(executable)], cwd=root, env=env,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            first = invoke()
            self.assertTrue(first['main_exists']); self.assertTrue(first['launch_exists'])
            self.assertEqual(first['nextflow'], '/fixture/nextflow')
            self.assertEqual(first['digest'], hashlib.sha256((ROOT/'oncotracer_cli/glimpse_joint.py').read_bytes()).hexdigest())
            bundle = Path(first['bundle'])
            self.assertEqual(bundle, root/'cache/oncotracer_cli/lpwgs')
            original = (bundle/'main.nf').read_bytes()
            stamp = (bundle/'main.nf').stat().st_mtime_ns
            self.assertEqual(invoke(), first)
            self.assertEqual((bundle/'main.nf').stat().st_mtime_ns, stamp)
            (bundle/'main.nf').write_text('tampered workflow')
            self.assertEqual(invoke(), first)
            self.assertEqual((bundle/'main.nf').read_bytes(), original)
            help_result = subprocess.run([sys.executable, str(bundle/'bin/launch.py'), '--help'],
                                         cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn('--glimpse2', help_result.stdout)

    def test_builder_compiles_workflow_helpers_after_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            source = staging/'payload/oncotracer_cli/lpwgs/bin/broken.py'
            source.parent.mkdir(parents=True)
            source.write_text('def broken(:\n')
            with self.assertRaisesRegex(SystemExit, 'Python compilation failed'):
                builder.validate_python_sources(staging)


if __name__ == '__main__':
    unittest.main()
