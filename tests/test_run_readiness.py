"""Runtime failures must be detected before a reference transfer or sample work."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import cli
from oncotracer_cli.runtime import OncoTracerError


class RunReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'run.yml'
        self.config.write_text('mode: illumina\n')
        self.install = {name + '_prefix': str(self.root / 'envs' / name)
                        for name in ('core', 'qdnaseq', 'ichorcna', 'classifier', 'gistic')}
        self.install['backend'] = 'conda'
        self.args = cli.build_parser().parse_args(['run', '--backend', 'conda', '--config', str(self.config)])

    def test_managed_failure_prevents_reference_and_analysis(self):
        with patch.object(cli, '_load_install_config', return_value=self.install), \
             patch.object(cli, 'managed_conda_runtime_lock', side_effect=OncoTracerError('foreign entries')) as check, \
             patch.object(cli, '_prepare_configured_hg38') as download, \
             patch.object(cli, '_run_host') as analysis:
            with self.assertRaisesRegex(OncoTracerError, 'foreign entries'):
                cli.execute_run(self.config, self.args)
        check.assert_called_once_with(self.root / 'envs', require_poetry=False, semantic=True)
        download.assert_not_called()
        analysis.assert_not_called()

    def test_semantic_readiness_precedes_download_and_analysis(self):
        events = []
        @contextlib.contextmanager
        def ready(*args, **kwargs):
            self.assertTrue(kwargs['semantic'])
            events.append('managed-imports')
            yield {}
        with patch.object(cli, '_load_install_config', return_value=self.install), \
             patch.object(cli, 'managed_conda_runtime_lock', side_effect=ready), \
             patch.object(cli, '_probe_core', side_effect=lambda *a, **kw: events.append('core-tools') or {'success': True}) as core, \
             patch.object(cli, '_prepare_configured_hg38', side_effect=lambda *a: events.append('reference')), \
             patch.object(cli, '_run_host', side_effect=lambda *a: events.append('analysis')), \
             contextlib.redirect_stdout(io.StringIO()):
            cli.execute_run(self.config, self.args)
        self.assertEqual(events, ['managed-imports', 'core-tools', 'reference', 'analysis'])
        self.assertEqual(core.call_args.kwargs['names'], {'bwa', 'samtools', 'pigz', 'picard'})

    def test_broken_selected_variant_tool_prevents_reference_and_alignment(self):
        self.args.backend = 'host'
        before = os.environ.copy()
        with patch.object(cli, '_load_install_config', return_value={}), \
             patch.object(cli, '_probe_core', return_value={'success': True}), \
             patch('oncotracer_cli.variants.resolve_variant_request', return_value=object()), \
             patch('oncotracer_cli.variants.preflight_variant_tools', side_effect=OncoTracerError('gatk unavailable')), \
             patch.object(cli, '_prepare_configured_hg38') as download, \
             patch.object(cli, '_run_host') as analysis, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(OncoTracerError, 'gatk unavailable'):
                cli.execute_run(self.config, self.args)
        download.assert_not_called()
        analysis.assert_not_called()
        self.assertEqual(dict(os.environ), before)

    def test_broken_core_tool_prevents_reference_and_alignment(self):
        self.args.backend = 'host'
        with patch.object(cli, '_load_install_config', return_value={}), \
             patch.object(cli, '_probe_core', return_value={'success': False, 'probes': {'bwa': {'success': False}}}), \
             patch.object(cli, '_prepare_configured_hg38') as download, \
             patch.object(cli, '_run_host') as analysis, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(OncoTracerError, 'core tool.*bwa'):
                cli.execute_run(self.config, self.args)
        download.assert_not_called()
        analysis.assert_not_called()

    def test_dry_run_skips_runtime_probes(self):
        self.args.dry_run = True
        with patch.object(cli, 'managed_conda_runtime_lock') as check, \
             patch.object(cli, '_probe_core') as core, \
             patch('oncotracer_cli.variants.preflight_variant_tools') as variant, \
             patch.object(cli, '_prepare_configured_hg38'), \
             patch.object(cli, '_run_host'):
            cli.execute_run(self.config, self.args)
        check.assert_not_called()
        core.assert_not_called()
        variant.assert_not_called()
