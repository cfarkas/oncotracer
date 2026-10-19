"""The local/remote Nextflow entry opens the real visual setup without a shell."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import nextflow_gateway as gateway
from oncotracer_cli.cli import parse_arguments

ROOT = Path(__file__).resolve().parents[1]


class NextflowGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Reproduce launching from a stale checkout which shadows PYTHONPATH.
        shadow = self.root / 'oncotracer_cli'
        shadow.mkdir()
        (shadow / '__init__.py').write_text("raise RuntimeError('OLD_CHECKOUT_IMPORTED')\n")
        self.config = self.root / 'sample $(touch SHOULD_NOT_EXIST).yml'
        self.config.write_text('mode: illumina\n')

    def args(self, *values):
        return gateway.parser().parse_args(['--project-dir', str(ROOT), '--launch-dir', str(self.root), *values])


    def test_default_port_remains_automatic_but_explicit_default_is_forwarded(self):
        argv, _, _ = gateway.command(self.args('--interactive'))
        self.assertNotIn('--port', argv)
        self.assertIsNone(parse_arguments(argv[3:]).port)
        explicit, _, _ = gateway.command(self.args('--interactive', '--port', '8888'))
        self.assertEqual(explicit[explicit.index('--port') + 1], '8888')
        self.assertEqual(parse_arguments(explicit[3:]).port, 8888)

    def test_visual_entry_routes_to_browser_setup_with_glimpse_selected(self):
        argv, cwd, env = gateway.command(self.args('--interactive', '--port', '8765'))
        self.assertEqual(argv[3:], ['--interactive', '--port', '8765', '--mode', 'illumina', '--variants'])
        args = parse_arguments(argv[3:])
        self.assertFalse(args.terminal)
        self.assertFalse(args.non_interactive)
        self.assertTrue(args.variants)
        self.assertEqual(cwd, self.root)
        self.assertEqual(env['PYTHONPATH'].split(os.pathsep)[0], str(ROOT))

    def test_existing_bam_interactive_entry_uses_review_screen(self):
        argv, _, _ = gateway.command(self.args('--interactive', '--config', self.config.name))
        parsed = parse_arguments(argv[3:])
        self.assertEqual(parsed.variant_config, str(self.config))
        self.assertFalse(parsed.terminal)

    def test_cpg_continuation_preserves_literal_project_and_has_no_variant_preset(self):
        source = 'ONT project $(touch SHOULD_NOT_EXIST)'
        argv, cwd, _ = gateway.command(self.args(
            '--interactive', '--cpg_from', source, '--classifier', 'sturgeon',
            '--resources', 'methylation resources.yml', '--backend', 'conda'))
        parsed = parse_arguments(argv[3:])
        self.assertEqual(parsed.cpg_from, str(self.root / source))
        self.assertEqual(parsed.classifier, 'sturgeon')
        self.assertEqual(parsed.resources, str(self.root / 'methylation resources.yml'))
        self.assertEqual(parsed.backend, 'conda')
        self.assertFalse(parsed.variants)
        self.assertFalse(parsed.run)
        self.assertNotIn('--mode', argv)
        self.assertEqual(cwd, self.root)
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_named_ont_aliases_select_only_the_ont_setup_defaults(self):
        for flag in ('--ont_named_samples', '--ont-named-samples'):
            with self.subTest(flag=flag):
                argv, cwd, _ = gateway.command(self.args('--interactive', flag,
                    '--input_folder', 'ONT $(touch SHOULD_NOT_EXIST)', '--backend', 'conda'))
                parsed = parse_arguments(argv[3:])
                self.assertEqual(parsed.mode, 'ont')
                self.assertTrue(parsed.ont_named_samples)
                self.assertFalse(parsed.variants)
                self.assertFalse(parsed.run)
                self.assertEqual(parsed.input_folder, str(self.root / 'ONT $(touch SHOULD_NOT_EXIST)'))
                self.assertEqual(cwd, self.root)
                self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_named_ont_rejects_other_input_routes_before_dispatch(self):
        for extra in (['--ffpe'], ['--config', self.config.name], ['--cpg_from', 'ONT project']):
            with self.subTest(extra=extra), patch.object(gateway.subprocess, 'run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as error:
                code = gateway.main(['--project-dir', str(ROOT), '--launch-dir', str(self.root),
                                     '--interactive', '--ont_named_samples', *extra])
                self.assertEqual(code, 2)
                self.assertIn('selects new named ONT inputs', error.getvalue())
                run.assert_not_called()

    def test_cpg_continuation_rejects_competing_input_routes(self):
        for extra in (['--config', self.config.name], ['--ffpe'], ['--input_folder', 'reads']):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'reuses an ONT project'):
                gateway.command(self.args('--interactive', '--cpg-from', 'ONT project', *extra))

    def test_variants_and_full_commands_preserve_literal_paths(self):
        for kind, expected in [('variants', 'variants'), ('full', 'run')]:
            argv, _, _ = gateway.command(self.args('--analysis', kind, '--config', self.config.name,
                                                   '--threads', '3', '--dry_run'))
            self.assertEqual(argv[3:6], [expected, '--config', str(self.config)])
            self.assertIn('--dry-run', argv)
            self.assertEqual(parse_arguments(argv[3:]).threads, 3)
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_parallel_sample_override_reaches_visual_variants_and_full_cli(self):
        for flags in [('--interactive',), ('--analysis','variants','--config',self.config.name),
                      ('--analysis','full','--config',self.config.name)]:
            argv, _, _ = gateway.command(self.args(*flags, '--parallel_samples', '4', '--threads', '32'))
            parsed = parse_arguments(argv[3:])
            self.assertEqual(parsed.threads, 32)
            field = 'variant_parallel_samples' if parsed.command == 'setup' else 'parallel_samples'
            self.assertEqual(getattr(parsed, field), 4)
            if parsed.command == 'setup':
                self.assertFalse(parsed.terminal)
                self.assertFalse(parsed.non_interactive)

    def test_ffpe_browser_forwards_the_same_native_setup_options(self):
        for backend in ('conda', 'docker'):
            with self.subTest(backend=backend):
                argv, cwd, _ = gateway.command(self.args(
                    '--interactive', '--ffpe', '--backend', backend, '--no_browser',
                    '--project', 'new project', '--input_folder', 'reads $(touch SHOULD_NOT_EXIST)',
                    '--variant_tool_prefix', 'variants', '--variant_ffperase_root', 'ffperase',
                    '--variant_ffperase_models', 'models', '--variant_ffperase_sif', 'ffpe.sif',
                    '--variant_ffperase_prefix', 'ffpe-env',
                    *(['--image', 'oncotracer:local'] if backend == 'docker' else [])))
                args = parse_arguments(argv[3:])
                self.assertEqual(args.command, 'setup')
                self.assertEqual(args.backend, backend)
                self.assertEqual(args.project, str(self.root / 'new project'))
                self.assertEqual(args.input_folder, str(self.root / 'reads $(touch SHOULD_NOT_EXIST)'))
                self.assertEqual(args.variant_tool_prefix, str(self.root / 'variants'))
                self.assertEqual(args.variant_ffperase_root, str(self.root / 'ffperase'))
                self.assertEqual(args.variant_ffperase_models, str(self.root / 'models'))
                self.assertEqual(args.variant_ffperase_sif, str(self.root / 'ffpe.sif'))
                self.assertEqual(args.variant_ffperase_prefix, str(self.root / 'ffpe-env'))
                self.assertEqual(args.variant_specimen_type, 'ffpe')
                self.assertEqual(args.variant_ffperase, 'required')
                self.assertTrue(args.variants)
                self.assertTrue(args.no_browser)
                self.assertFalse(args.run)
                self.assertIsNone(args.variant_accept_ffperase_license)
                self.assertEqual(cwd, self.root)
                self.assertFalse((self.root / 'new project').exists())
                self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_setup_only_options_never_dispatch_an_analysis(self):
        for flags in (['--ffpe'], ['--ont_named_samples'], ['--project', 'project'], ['--input_folder', 'reads'],
                      ['--cpg_from', 'ONT project'], ['--classifier', 'marlin'], ['--resources', 'resources.yml'],
                      ['--image', 'local:image'], ['--no_browser'],
                      ['--variant_ffperase_models', 'models'], ['--variant_tool_prefix', 'tools']):
            with self.subTest(flags=flags), patch.object(gateway.subprocess, 'run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as error:
                code = gateway.main(['--project-dir', str(ROOT), '--launch-dir', str(self.root),
                                     '--config', str(self.config), *flags])
                self.assertEqual(code, 2)
                self.assertIn('require --interactive', error.getvalue())
                run.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'new FASTQ setup'):
            gateway.command(self.args('--interactive', '--ffpe', '--config', str(self.config)))

    def test_missing_config_and_invalid_runtime_inputs_are_rejected(self):
        for values in [[], ['--interactive', '--port', '0'], ['--interactive', '--threads', '0'], ['--interactive', '--parallel_samples', '0'],
                       ['--interactive', '--start_dir', 'missing'], ['--interactive', '--dry_run']]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                gateway.command(self.args(*values))

    def test_exit_status_and_no_shell_execution(self):
        with patch.object(gateway.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as run:
            code = gateway.main(['--project-dir', str(ROOT), '--launch-dir', str(self.root),
                                 '--config', str(self.config)])
        self.assertEqual(code, 7)
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)

    def test_real_cli_child_ignores_old_checkout_and_keeps_visual_options(self):
        argv, cwd, env = gateway.command(self.args('--interactive', '--parallel_samples', '4', '--threads', '64'))
        # argparse help exits before the setup can open a browser or start work.
        result = subprocess.run([*argv, '--help'], cwd=cwd, env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--parallel-samples', result.stdout)
        self.assertIn('--variant-config', result.stdout)
        self.assertNotIn('OLD_CHECKOUT_IMPORTED', result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('nextflow'), 'Nextflow required for native entry test')
    def test_real_nextflow_help_ignores_old_checkout(self):
        result = subprocess.run(
            [shutil.which('nextflow'), 'run', str(ROOT / 'main.nf'), '--python', sys.executable, '--help'],
            cwd=self.root, env={**os.environ, 'NXF_OFFLINE': 'true'},
            text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--parallel_samples', result.stdout)
        self.assertNotIn('OLD_CHECKOUT_IMPORTED', result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('nextflow'), 'Nextflow required for native entry test')
    def test_real_nextflow_visual_routing_and_failed_analysis_propagation(self):
        # Only the CLI child is replaced. Real Nextflow compiles main.nf and runs
        # the actual gateway; no server, genomic data or download is started.
        record = self.root / 'command.json'
        stub = self.root / 'python stub'
        stub.write_text("#!/usr/bin/python3\n"
            "import json,sys\nfrom pathlib import Path\nfrom unittest.mock import patch\n"
            "from subprocess import CompletedProcess\n"
            "def run(argv, **kwargs):\n"
            f" Path({str(record)!r}).write_text(json.dumps({{'argv':argv,'cwd':str(kwargs['cwd']),'source':kwargs['env']['PYTHONPATH']}}))\n"
            " return CompletedProcess(argv, 0 if '--interactive' in argv else 7)\n"
            "code = sys.argv[2]\nsys.argv = ['-c', *sys.argv[3:]]\n"
            "with patch('subprocess.run', side_effect=run):\n"
            " exec(code, {'__name__': '__main__'})\n")
        stub.chmod(0o755)
        env = {**os.environ, 'NXF_OFFLINE': 'true'}
        argv = [shutil.which('nextflow'), 'run', str(ROOT / 'main.nf'), '--python', str(stub)]
        visual = subprocess.run([*argv, '--interactive', '--port', '8877', '--start_dir', str(self.root), '--parallel_samples', '3'],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(visual.returncode, 0, visual.stdout + visual.stderr)
        payload = json.loads(record.read_text())
        self.assertEqual(payload['argv'][3:], ['--interactive', '--port', '8877', '--mode', 'illumina', '--variants', '--parallel-samples', '3'])
        self.assertEqual(payload['cwd'], str(self.root))
        self.assertEqual(payload['source'].split(os.pathsep)[0], str(ROOT))
        ffpe = subprocess.run([*argv, '--interactive', '--ffpe', '--backend', 'docker',
                               '--project', 'FFPE project', '--input_folder', 'FFPE reads',
                               '--image', 'oncotracer:local', '--no_browser',
                               '--variant_tool_prefix', 'variant tools',
                               '--variant_ffperase_root', 'ffperase', '--variant_ffperase_models', 'models',
                               '--variant_ffperase_sif', 'ffpe.sif', '--variant_ffperase_prefix', 'ffpe-env'],
                              cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(ffpe.returncode, 0, ffpe.stdout + ffpe.stderr)
        parsed = parse_arguments(json.loads(record.read_text())['argv'][3:])
        self.assertEqual(parsed.variant_specimen_type, 'ffpe')
        self.assertEqual(parsed.variant_ffperase, 'required')
        self.assertEqual(parsed.backend, 'docker')
        self.assertEqual(parsed.image, 'oncotracer:local')
        self.assertEqual(parsed.project, str(self.root / 'FFPE project'))
        self.assertEqual(parsed.input_folder, str(self.root / 'FFPE reads'))
        self.assertEqual(parsed.variant_tool_prefix, str(self.root / 'variant tools'))
        self.assertEqual(parsed.variant_ffperase_root, str(self.root / 'ffperase'))
        self.assertEqual(parsed.variant_ffperase_models, str(self.root / 'models'))
        self.assertEqual(parsed.variant_ffperase_sif, str(self.root / 'ffpe.sif'))
        self.assertEqual(parsed.variant_ffperase_prefix, str(self.root / 'ffpe-env'))
        self.assertTrue(parsed.no_browser)
        self.assertFalse(parsed.run)
        self.assertFalse((self.root / 'FFPE project').exists())
        cpg = subprocess.run([*argv, '--interactive', '--cpg_from', 'ONT project',
                              '--classifier', 'marlin', '--backend', 'conda'],
                             cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(cpg.returncode, 0, cpg.stdout + cpg.stderr)
        parsed = parse_arguments(json.loads(record.read_text())['argv'][3:])
        self.assertEqual(parsed.cpg_from, str(self.root / 'ONT project'))
        self.assertEqual(parsed.classifier, 'marlin')
        self.assertFalse(parsed.variants)
        self.assertFalse(parsed.run)
        for flag in ('--ont_named_samples', '--ont-named-samples'):
            named = subprocess.run([*argv, '--interactive', flag, '--input_folder', 'named ONT',
                                    '--backend', 'conda', '--no_browser'],
                                   cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
            self.assertEqual(named.returncode, 0, named.stdout + named.stderr)
            parsed = parse_arguments(json.loads(record.read_text())['argv'][3:])
            self.assertEqual(parsed.mode, 'ont', flag + ': ' + named.stdout + named.stderr)
            self.assertTrue(parsed.ont_named_samples)
            self.assertEqual(parsed.input_folder, str(self.root / 'named ONT'))
            self.assertFalse(parsed.variants)
            self.assertFalse(parsed.run)
        before = record.read_bytes()
        conflict = subprocess.run([*argv, '--interactive', '--ont_named_samples', '--ffpe'],
                                  cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('selects new named ONT inputs', conflict.stdout + conflict.stderr)
        self.assertEqual(record.read_bytes(), before)
        no_arguments = subprocess.run(argv, cwd=self.root, env=env,
                                      text=True, capture_output=True, timeout=90)
        self.assertNotEqual(no_arguments.returncode, 0)
        self.assertIn('Provide --config', no_arguments.stdout + no_arguments.stderr)
        self.assertEqual(record.read_bytes(), before)
        no_interactive = subprocess.run([*argv, '--ffpe', '--config', self.config.name],
                                       cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertNotEqual(no_interactive.returncode, 0)
        self.assertIn('require --interactive', no_interactive.stdout + no_interactive.stderr)
        self.assertEqual(record.read_bytes(), before)
        failed = subprocess.run([*argv, '--analysis', 'full', '--config', self.config.name],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('OncoTracer exited with status 7', failed.stdout + failed.stderr)
        self.assertEqual(json.loads(record.read_text())['argv'][3:6], ['run', '--config', str(self.config)])
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())


if __name__ == '__main__':
    unittest.main()
