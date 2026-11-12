"""Podman must use the selected engine without changing data or ownership."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import cli, container_runtime, docker_runtime, gpu_runtime, project_resume
from oncotracer_cli.runtime import OncoTracerError, render_flat_yaml, load_flat_yaml


class PodmanBackendTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.fastq = self.root / 'reads.fastq.gz'; self.fastq.write_bytes(b'input fixture')
        self.sheet = self.project / 'samples.csv'
        self.sheet.write_text(f'sample,fastq_1,fastq_2,status\nS,{self.fastq},,tumor\n')
        self.model = self.root / 'model'; self.model.mkdir()
        self.output = self.project / 'results'
        self.path = self.project / 'run.yml'
        self.config = dict(mode='illumina', lpwgs_root=str(self.project), outdir=str(self.output),
                           illumina_samplesheet=str(self.sheet), run_variants=True,
                           variant_specimen_type='fresh', variant_callers='bcftools',
                           variant_annovar='off', variant_llm_model=str(self.model),
                           variant_tool_prefix='/host-only/missing', execution_backend='podman',
                           docker_image='localhost/oncotracer:test')
        self.path.write_text(render_flat_yaml(self.config))
        for item in (patch.object(cli, '_load_install_config', return_value={}),
                     patch.object(Path, 'cwd', return_value=self.root),
                     patch.dict(os.environ, {'ONCOTRACER_CONTAINER_RUNTIME': ''}),
                     patch.object(container_runtime.os, 'geteuid', return_value=1000),
                     patch.object(container_runtime.os, 'getuid', return_value=1000),
                     patch.object(container_runtime.os, 'getgid', return_value=1000)):
            item.start(); self.addCleanup(item.stop)

    def args(self, *more):
        return cli.build_parser().parse_args(['run', '--config', str(self.path), '--backend', 'podman', *more])

    def test_rootless_preserves_uid_and_rootful_does_not_remap(self):
        arguments = container_runtime.user_arguments('podman')
        self.assertIn('--userns=keep-id', arguments)
        self.assertEqual(arguments[arguments.index('--user') + 1], '1000:1000')
        self.assertNotIn('--privileged', arguments)
        with patch.object(container_runtime.os, 'geteuid', return_value=0):
            self.assertNotIn('--userns=keep-id', container_runtime.user_arguments('podman'))
        self.assertNotIn('--userns=keep-id', container_runtime.user_arguments('docker'))

    def test_install_checks_selected_local_image_without_docker_or_pull(self):
        args = cli.build_parser().parse_args(['install', '--podman', '--no-pull', '--image', 'localhost/app:test'])
        with patch.object(cli.shutil, 'which', return_value='/usr/bin/podman'), \
             patch.object(cli, '_run') as run, patch.object(cli, '_save_install_config') as save:
            result = cli._install_podman(args)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ['/usr/bin/podman', 'info'])
        self.assertEqual(commands[1], ['/usr/bin/podman', 'image', 'inspect', 'localhost/app:test'])
        self.assertEqual(commands[2][-4:], ['localhost/app:test', 'doctor', '--backend', 'host'])
        self.assertIn('--userns=keep-id', commands[2])
        self.assertTrue(all(command[0] == '/usr/bin/podman' for command in commands))
        self.assertEqual(result, {'backend': 'podman', 'image': 'localhost/app:test'})
        save.assert_called_once_with(result)

    def test_failed_install_does_not_save_or_fall_back(self):
        args = cli.build_parser().parse_args(['install', '--podman'])
        with patch.object(cli.shutil, 'which', return_value='/usr/bin/podman'), \
             patch.object(cli, '_run', side_effect=OncoTracerError('engine unavailable')) as run, \
             patch.object(cli, '_save_install_config') as save:
            with self.assertRaisesRegex(OncoTracerError, 'engine unavailable'):
                cli._install_podman(args)
        self.assertEqual(run.call_count, 1)
        save.assert_not_called()

    def test_default_remote_image_is_qualified_and_local_tag_is_retained(self):
        self.assertEqual(container_runtime.image_reference(None, 'podman'), 'docker.io/' + cli.DEFAULT_IMAGE)
        self.assertEqual(container_runtime.image_reference('localhost/app:checked', 'podman'), 'localhost/app:checked')
        self.assertEqual(container_runtime.image_reference(None, 'docker'), cli.DEFAULT_IMAGE)

    def test_variant_preflight_and_run_share_mounts_image_and_uid(self):
        original = self.path.read_bytes()
        with patch.object(cli.shutil, 'which', return_value='/usr/bin/podman'), patch.object(cli, '_run') as run:
            cli._run_podman(self.path, self.args('--dry-run', '--storage-mode', 'compact', '--resource-mode', 'dynamic'))
        commands = [list(map(str, call.args[0])) for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[:3], ['/usr/bin/podman', 'run', '--rm'])
            for item in ('--userns=keep-id', '1000:1000', 'ONCOTRACER_CONTAINER_RUNTIME=podman',
                         f'{self.fastq}:{self.fastq}:ro', f'{self.output}:{self.output}:rw',
                         'localhost/oncotracer:test'):
                self.assertIn(item, command)
            self.assertNotIn('/host-only/missing', command)
            self.assertFalse(any(str(item).endswith((':U', ':z', ':Z')) for item in command))
        self.assertIn('--entrypoint', commands[0])
        self.assertIn('internal-run', commands[1])
        self.assertIn('compact', commands[1]); self.assertIn('dynamic', commands[1])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.output.exists())

    def test_public_run_selects_podman_and_keeps_container_methylation_guard(self):
        with patch.object(cli, '_run_podman') as podman, patch.object(cli, '_run_docker') as docker, \
             patch.object(cli, '_run_host', return_value=self.output):
            self.assertEqual(cli._execute_run(self.path, self.args('--dry-run')), self.output)
        podman.assert_called_once(); docker.assert_not_called()
        with patch.object(cli, '_run_podman') as podman:
            with self.assertRaisesRegex(OncoTracerError, 'methylation branch requires'):
                cli._execute_run(self.path, self.args('--dry-run', '--methylation'))
        podman.assert_not_called()

    def test_image_preflight_failure_never_starts_analysis(self):
        with patch.object(cli.shutil, 'which', return_value='/usr/bin/podman'), \
             patch.object(cli, '_run', side_effect=OncoTracerError('caller absent')) as run:
            with self.assertRaisesRegex(OncoTracerError, 'caller absent'):
                cli._run_podman(self.path, self.args('--dry-run'))
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('internal-run', run.call_args.args[0])

    def test_resume_identity_comes_from_selected_podman_image(self):
        from oncotracer_cli.output_safety import _IDENTITY_KEYS
        identity = {key: 'a' * 64 for key in _IDENTITY_KEYS}
        identity['oncotracer_version'] = '4.0.0'
        response = subprocess.CompletedProcess([], 0, json.dumps(identity), '')
        with patch.object(project_resume.subprocess, 'run', return_value=response) as run:
            self.assertEqual(project_resume._runtime('podman', self.config), identity)
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'podman')
        self.assertIn('--userns=keep-id', command)
        self.assertEqual(command[-3:], ['localhost/oncotracer:test', 'provenance', '--runtime-identity'])
        with patch.object(project_resume.subprocess, 'run', return_value=subprocess.CompletedProcess([], 125, '', 'permission denied')):
            with self.assertRaisesRegex(OncoTracerError, 'Podman image'):
                project_resume._runtime('podman', self.config)

    def test_doctor_checks_selected_engine_and_saved_image(self):
        args = cli.build_parser().parse_args(['doctor', '--backend', 'podman', '--json'])
        captured = io.StringIO()
        with patch.object(cli, '_load_install_config', return_value={'backend': 'podman', 'image': 'localhost/app:checked'}), \
             patch.object(cli, 'get_provenance', return_value={'source_commit': 'a'*40, 'source_sha256': 'b'*64, 'source_tree_dirty': False}), \
             patch.object(cli, 'require_command', return_value='/usr/bin/podman'), \
             patch.object(cli, '_check_process', return_value={'success': True, 'returncode': 0}) as probe, \
             contextlib.redirect_stdout(captured):
            self.assertEqual(cli._command_doctor(args), 0)
        result = json.loads(captured.getvalue())
        self.assertTrue(result['success'])
        self.assertIn('podman', result); self.assertNotIn('docker', result)
        self.assertEqual(probe.call_args.args[0][0], '/usr/bin/podman')
        self.assertIn('localhost/app:checked', probe.call_args.args[0])
        self.assertIn('--userns=keep-id', probe.call_args.args[0])

    def test_image_tools_replace_host_only_prefixes_under_podman(self):
        with patch.dict(os.environ, {'ONCOTRACER_CONTAINER_RUNTIME': 'podman'}):
            selected = docker_runtime.container_variant_config(self.config)
        self.assertEqual(selected['variant_tool_prefix'], '/opt/oncotracer-envs/variants')
        self.assertEqual(selected['variant_nextflow'], '/usr/local/bin/nextflow')
        self.assertEqual(self.config['variant_tool_prefix'], '/host-only/missing')

    def test_gpu_uses_selected_cdi_devices_and_shared_admission_locks(self):
        devices = [{'uuid': 'GPU-one', 'index': 2}, {'uuid': 'GPU-two', 'index': 4}]
        with patch.object(gpu_runtime, 'inventory', return_value=devices), \
             patch.object(gpu_runtime, 'lock_directory', return_value=self.root / 'locks'):
            arguments = gpu_runtime.docker_arguments({'gpu_usage': True}, create=False, backend='podman')
        self.assertNotIn('--gpus', arguments)
        self.assertNotIn('--privileged', arguments)
        self.assertIn('nvidia.com/gpu=2', arguments)
        self.assertIn('nvidia.com/gpu=4', arguments)
        self.assertIn('NVIDIA_VISIBLE_DEVICES=void', arguments)
        self.assertIn('ONCOTRACER_GPU_DEVICES=GPU-one,GPU-two', arguments)
        self.assertIn(f'{self.root}/locks:{self.root}/locks:rw', arguments)
        with patch.object(gpu_runtime, 'inventory', side_effect=AssertionError('CPU run probed GPU')):
            self.assertEqual(gpu_runtime.docker_arguments({'gpu_usage': False}, backend='podman'), [])

    def test_visual_installer_offers_podman_and_selects_it_without_native_tools(self):
        from oncotracer_cli.variant_installer import VariantInstaller
        installer = VariantInstaller(self.root, install_root=self.root / 'install')
        self.addCleanup(installer.close)
        before = set(self.root.rglob('*'))
        with patch('oncotracer_cli.variant_installer._executable', side_effect=lambda name: '/usr/bin/podman' if name == 'podman' else None):
            plan = installer.plan(dict(mode='illumina', backend='podman', flow='fastq', specimen_type='fresh',
                                       callers=['bcftools'], values={'variant_annovar': 'off'}))
        self.assertEqual(plan['method'], 'podman'); self.assertTrue(plan['available'], plan)
        self.assertEqual(plan['fields']['backend'], 'podman')
        self.assertTrue(all(step['command'][0] == '/usr/bin/podman' for step in plan['steps']))
        self.assertIn('--userns=keep-id', plan['steps'][-1]['command'])
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_browser_preparation_saves_podman_and_preserves_reads(self):
        from tests.test_web import WebTests
        fixture = WebTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        reads = [fixture.fastq(f'reads/S_R{mate}.fastq.gz') for mate in (1, 2)]
        originals = [path.read_bytes() for path in reads]
        prepared = fixture.prepare(backend='podman', docker_image='localhost/app:test')
        self.assertTrue(prepared['valid'], prepared['check'])
        config = load_flat_yaml(Path(prepared['config_path']))
        self.assertEqual(config['execution_backend'], 'podman')
        self.assertEqual(config['docker_image'], 'localhost/app:test')
        self.assertEqual(originals, [path.read_bytes() for path in reads])
        self.assertFalse((fixture.root / 'project/results').exists())


if __name__ == '__main__':
    unittest.main()
