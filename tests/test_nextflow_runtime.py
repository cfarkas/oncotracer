"""Concurrent controllers must not overwrite each other's extracted JNI files."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from oncotracer_cli.nextflow_runtime import isolated_nextflow_environment
from oncotracer_cli import glimpse_readiness


class NextflowRuntimeTests(unittest.TestCase):
    def test_parallel_native_extraction_is_private_and_cleaned(self):
        with tempfile.TemporaryDirectory(prefix='nextflow parent with spaces ') as parent:
            environment = {'NXF_TEMP': parent, 'NXF_OPTS': '-Xmx1g'}
            barrier = threading.Barrier(8)
            def extract(index):
                with isolated_nextflow_environment(environment) as overrides:
                    directory = Path(overrides['NXF_TEMP'])
                    self.assertFalse(any(c.isspace() for c in str(directory)))
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                    library = directory / 'libjansi-64.so'
                    library.write_text(str(index))
                    barrier.wait(timeout=10)
                    self.assertEqual(library.read_text(), str(index))
                    return directory
            with ThreadPoolExecutor(max_workers=8) as pool:
                directories = list(pool.map(extract, range(8)))
            self.assertEqual(len(set(directories)), 8)
            self.assertTrue(all(not p.exists() for p in directories))
            self.assertEqual(environment, {'NXF_TEMP': parent, 'NXF_OPTS': '-Xmx1g'})

    def test_failure_cleans_only_temporary_files_and_preserves_work(self):
        with tempfile.TemporaryDirectory() as parent:
            work = Path(parent) / 'work'; work.mkdir()
            result = work / 'valid.bcf'; result.write_bytes(b'completed output')
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                with isolated_nextflow_environment({'TMPDIR': parent}) as env:
                    scratch = Path(env['NXF_TEMP'])
                    self.assertEqual(scratch.parent, Path(parent).resolve())
                    (scratch / 'libjansi-64.so').write_bytes(b'fixture')
                    raise RuntimeError('interrupted')
            self.assertFalse(scratch.exists())
            self.assertEqual(result.read_bytes(), b'completed output')

    def test_readiness_probe_receives_private_environment(self):
        with tempfile.TemporaryDirectory() as parent:
            with isolated_nextflow_environment({'TMPDIR': parent}) as overrides:
                with patch.object(glimpse_readiness.subprocess, 'run') as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = 'nextflow version fixture'
                    run.return_value.stderr = ''
                    glimpse_readiness.probe(['nextflow', '-version'], 'Nextflow', env=overrides)
                    env = run.call_args.kwargs['env']
                    self.assertEqual(env['NXF_TEMP'], overrides['NXF_TEMP'])
                    self.assertTrue(Path(env['NXF_TEMP']).is_dir())
                    self.assertEqual(env['HF_HUB_OFFLINE'], '1')


if __name__ == '__main__':
    unittest.main()
