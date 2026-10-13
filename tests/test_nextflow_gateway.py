"""The local/remote Nextflow entry opens the real visual setup without a shell."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import nextflow_gateway as gateway
from oncotracer_cli.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


class NextflowGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'sample $(touch SHOULD_NOT_EXIST).yml'
        self.config.write_text('mode: illumina\n')

    def args(self, *values):
        return gateway.parser().parse_args(['--project-dir', str(ROOT), '--launch-dir', str(self.root), *values])

    def test_visual_entry_routes_to_browser_setup_with_glimpse_selected(self):
        argv, cwd, env = gateway.command(self.args('--interactive', '--port', '8765'))
        self.assertEqual(argv[3:], ['setup', '--port', '8765', '--mode', 'illumina', '--variants'])
        args = build_parser().parse_args(argv[3:])
        self.assertFalse(args.terminal)
        self.assertFalse(args.non_interactive)
        self.assertTrue(args.variants)
        self.assertEqual(cwd, self.root)
        self.assertEqual(env['PYTHONPATH'].split(os.pathsep)[0], str(ROOT))

    def test_existing_bam_interactive_entry_uses_review_screen(self):
        argv, _, _ = gateway.command(self.args('--interactive', '--config', self.config.name))
        parsed = build_parser().parse_args(argv[3:])
        self.assertEqual(parsed.variant_config, str(self.config))
        self.assertFalse(parsed.terminal)

    def test_variants_and_full_commands_preserve_literal_paths(self):
        for kind, expected in [('variants', 'variants'), ('full', 'run')]:
            argv, _, _ = gateway.command(self.args('--analysis', kind, '--config', self.config.name,
                                                   '--threads', '3', '--dry_run'))
            self.assertEqual(argv[3:6], [expected, '--config', str(self.config)])
            self.assertIn('--dry-run', argv)
            self.assertEqual(build_parser().parse_args(argv[3:]).threads, 3)
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_missing_config_and_invalid_runtime_inputs_are_rejected(self):
        for values in [[], ['--interactive', '--port', '0'], ['--interactive', '--threads', '0'],
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

    @unittest.skipUnless(shutil.which('nextflow'), 'Nextflow required for native entry test')
    def test_real_nextflow_visual_routing_and_failed_analysis_propagation(self):
        # Only the CLI child is replaced. Real Nextflow compiles main.nf and runs
        # the actual gateway; no server, genomic data or download is started.
        record = self.root / 'command.json'
        stub = self.root / 'python stub'
        stub.write_text("#!/usr/bin/python3\n"
            "import json,sys\nfrom pathlib import Path\nfrom unittest.mock import patch\n"
            "from subprocess import CompletedProcess\nfrom oncotracer_cli.nextflow_gateway import main\n"
            "def run(argv, **kwargs):\n"
            f" Path({str(record)!r}).write_text(json.dumps({{'argv':argv,'cwd':str(kwargs['cwd']),'source':kwargs['env']['PYTHONPATH']}}))\n"
            " return CompletedProcess(argv, 0 if 'setup' in argv else 7)\n"
            "with patch('oncotracer_cli.nextflow_gateway.subprocess.run', side_effect=run):\n"
            " raise SystemExit(main(sys.argv[3:]))\n")
        stub.chmod(0o755)
        env = {**os.environ, 'NXF_OFFLINE': 'true'}
        argv = [shutil.which('nextflow'), 'run', str(ROOT / 'main.nf'), '--python', str(stub)]
        visual = subprocess.run([*argv, '--interactive', '--port', '8877', '--start_dir', str(self.root)],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(visual.returncode, 0, visual.stdout + visual.stderr)
        payload = json.loads(record.read_text())
        self.assertEqual(payload['argv'][3:], ['setup', '--port', '8877', '--mode', 'illumina', '--variants'])
        self.assertEqual(payload['cwd'], str(self.root))
        self.assertEqual(payload['source'].split(os.pathsep)[0], str(ROOT))
        failed = subprocess.run([*argv, '--analysis', 'full', '--config', self.config.name],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=90)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('OncoTracer exited with status 7', failed.stdout + failed.stderr)
        self.assertEqual(json.loads(record.read_text())['argv'][3:6], ['run', '--config', str(self.config)])
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())


if __name__ == '__main__':
    unittest.main()
