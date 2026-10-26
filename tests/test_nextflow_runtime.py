"""Concurrent controllers must not overwrite each other's extracted JNI files."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

from oncotracer_cli.nextflow_runtime import isolated_nextflow_environment, prepare_nextflow_environment, _check_jar
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli import glimpse_readiness


class NextflowRuntimeTests(unittest.TestCase):
    def test_duplicate_shadow_jar_records_are_valid_but_corrupt_payload_is_not(self):
        with tempfile.TemporaryDirectory() as parent:
            jar = Path(parent)/'shadow.jar'
            with zipfile.ZipFile(jar,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('META-INF/MANIFEST.MF','Main-Class: nextflow.cli.Launcher\n')
                archive.writestr('Launcher.class',b'fixture bytecode'*1000)
                entry = archive.filelist[-1]
                # Same local member, duplicated central record, as upstream.
                archive.filelist.append(entry)
            _check_jar(jar)
            with jar.open('r+b') as handle:
                handle.seek(entry.header_offset+30+len(entry.filename.encode()))
                original=handle.read(1);handle.seek(-1,1)
                handle.write(bytes([original[0]^255]))
            with self.assertRaisesRegex(OncoTracerError,'distribution is incomplete'):
                _check_jar(jar)

    def bootstrap_fixture(self, parent):
        launcher = Path(parent) / 'nextflow'
        launcher.write_text(f'#!{sys.executable}\n' + '''
import os
from pathlib import Path
import sys
import zipfile
root=Path(os.environ['NXF_DIST']); jar=root/'26.04.2/nextflow-26.04.2-one.jar'
jar.parent.mkdir(parents=True,exist_ok=True)
with open(os.environ['FIXTURE_CALLS'],'a') as f:f.write('bootstrap\\n')
if os.environ.get('FIXTURE_FAIL'):
 jar.write_bytes(b'partial download'); sys.exit(7)
if not jar.exists():
 with zipfile.ZipFile(jar,'w') as f:
  f.writestr('META-INF/MANIFEST.MF','Main-Class: nextflow.cli.Launcher\\n')
  f.writestr('nextflow/cli/Launcher.class','synthetic unit-test distribution')
print('N E X T F L O W\\nversion 26.04.2 build fixture')
''')
        launcher.chmod(0o755)
        env = dict(os.environ, HOME=str(Path(parent)/'private-home'),
                   XDG_CACHE_HOME=str(Path(parent)/'private-cache'),
                   FIXTURE_CALLS=str(Path(parent)/'calls'), NXF_VER='26.04.2')
        for key in ('NXF_BIN', 'NXF_HOME', 'NXF_DIST', 'NXF_TEMP'):
            env.pop(key, None)
        return launcher, env

    def test_simultaneous_processes_publish_one_verified_cold_runtime(self):
        with tempfile.TemporaryDirectory() as parent:
            launcher, env = self.bootstrap_fixture(parent)
            code = ('import json,sys; from oncotracer_cli.nextflow_runtime import prepare_nextflow_environment; '
                    'print(json.dumps(prepare_nextflow_environment(sys.argv[1])))')
            children = [subprocess.Popen([sys.executable, '-B', '-c', code, str(launcher)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
            prepared = []
            for child in children:
                stdout, stderr = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, stderr)
                prepared.append(json.loads(stdout.splitlines()[-1]))
            self.assertEqual(len({p['NXF_DIST'] for p in prepared}), 1)
            self.assertEqual(Path(env['FIXTURE_CALLS']).read_text().splitlines(), ['bootstrap'])
            self.assertIn(env['XDG_CACHE_HOME'], prepared[0]['NXF_DIST'])
            self.assertFalse(list(Path(env['XDG_CACHE_HOME']).rglob('.staging-*')))

    def test_failed_download_never_publishes_and_next_attempt_recovers(self):
        with tempfile.TemporaryDirectory() as parent:
            launcher, env = self.bootstrap_fixture(parent)
            with self.assertRaisesRegex(OncoTracerError, 'before sample launch'):
                prepare_nextflow_environment(launcher, {**env, 'FIXTURE_FAIL':'yes'})
            self.assertFalse(list(Path(env['XDG_CACHE_HOME']).rglob('ready.json')))
            result = prepare_nextflow_environment(launcher, env)
            self.assertTrue(Path(result['NXF_DIST']).is_dir())
            self.assertEqual(len(Path(env['FIXTURE_CALLS']).read_text().splitlines()), 2)

    def test_corrupt_generation_is_replaced_without_overwriting_active_files(self):
        with tempfile.TemporaryDirectory() as parent:
            launcher, env = self.bootstrap_fixture(parent)
            original = prepare_nextflow_environment(launcher, env)
            jar = next(Path(original['NXF_DIST']).rglob('*.jar'))
            jar.chmod(0o644); jar.write_bytes(b'interrupted or corrupted')
            repaired = prepare_nextflow_environment(launcher, env)
            self.assertNotEqual(original['NXF_DIST'], repaired['NXF_DIST'])
            self.assertEqual(jar.read_bytes(), b'interrupted or corrupted')
            self.assertEqual(prepare_nextflow_environment(launcher, env), repaired)
            self.assertEqual(len(Path(env['FIXTURE_CALLS']).read_text().splitlines()), 2)

    def test_read_only_preinstalled_distribution_is_copied_and_unchanged(self):
        with tempfile.TemporaryDirectory() as parent:
            launcher, env = self.bootstrap_fixture(parent)
            first = prepare_nextflow_environment(launcher, env)
            jar = next(Path(first['NXF_DIST']).rglob('*.jar')); content = jar.read_bytes()
            second = prepare_nextflow_environment(launcher, {**env, 'NXF_DIST':first['NXF_DIST'],
                'HOME':str(Path(parent)/'other-home'), 'XDG_CACHE_HOME':str(Path(parent)/'other-cache')})
            self.assertNotEqual(first['NXF_DIST'], second['NXF_DIST'])
            self.assertEqual(jar.read_bytes(), content)
            self.assertEqual(next(Path(second['NXF_DIST']).rglob('*.jar')).read_bytes(), content)

    def test_corrupt_explicit_distribution_fails_before_any_launch(self):
        with tempfile.TemporaryDirectory() as parent:
            launcher, env = self.bootstrap_fixture(parent)
            jar = Path(parent)/'selected.jar'; jar.write_bytes(b'incomplete')
            with self.assertRaisesRegex(OncoTracerError, 'distribution is incomplete'):
                prepare_nextflow_environment(launcher, {**env, 'NXF_BIN':str(jar)})
            self.assertFalse(Path(env['FIXTURE_CALLS']).exists())

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

    def test_whitespace_distribution_aliases_are_private_and_do_not_move_cache(self):
        with tempfile.TemporaryDirectory(prefix='nextflow cache with spaces ') as parent:
            distribution=Path(parent)/'framework';distribution.mkdir()
            jar=Path(parent)/'selected.jar';jar.write_bytes(b'fixture')
            env={'NXF_DIST':str(distribution),'NXF_BIN':str(jar)}
            with isolated_nextflow_environment(env) as overrides:
                for key in env:
                    self.assertFalse(any(c.isspace() for c in overrides[key]))
                    self.assertEqual(Path(overrides[key]).resolve(),Path(env[key]))
                scratch=Path(overrides['NXF_TEMP'])
            self.assertFalse(scratch.exists())
            self.assertEqual(jar.read_bytes(),b'fixture')

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
