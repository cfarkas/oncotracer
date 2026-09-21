"""Existing-BAM browser review, input protection and subprocess lifecycle."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml
from oncotracer_cli.web import WebState
from tests.test_web import HARDWARE


class VariantBrowserTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bam = self.root / 'sample.bam'
        self.bam.write_bytes(b'BAM fixture; checked at execution')
        self.reference = self.root / 'reference.fa'
        self.reference.write_text('>chr1\nACGT\n')
        self.manifest = self.root / 'samples.tsv'
        self.manifest.write_text('sample\tbam\tstatus\nFIXTURE\tsample.bam\ttumor\n')
        self.config = self.root / 'variants.yml'
        self.values = {'mode': 'illumina', 'outdir': 'previous', 'variant_reference': 'reference.fa',
                       'variant_bam_manifest': 'samples.tsv', 'run_variants': True,
                       'variant_specimen_type': 'fresh', 'variant_callers': 'bcftools',
                       'variant_annovar': 'off', 'threads': 1}
        self.config.write_text(render_flat_yaml(self.values))
        self.state = WebState(self.root)
        self.state.hardware = HARDWARE
        self.tool = self.root / 'tool'
        self.tool.write_text('fixture tool; never executed')
        self.mock_tools = {'samtools': str(self.tool), 'bcftools': str(self.tool)}

    def load(self):
        return self.state.variant_load({'config_path': str(self.config)})

    def prepare(self, loaded=None, **overrides):
        loaded = loaded or self.load()
        payload = {'load_id': loaded['load_id'], 'project': str(self.root / 'new-project'), 'threads': 2}
        payload.update(overrides)
        with patch('oncotracer_cli.variants.preflight_variant_tools', return_value=self.mock_tools):
            return self.state.variant_prepare(payload)

    def start(self, prepared):
        process = Mock(pid=12345)
        process.wait.return_value = 0
        with patch('oncotracer_cli.web.subprocess.Popen', return_value=process) as popen, patch('oncotracer_cli.web.threading.Thread'):
            status = self.state.run({'project_id': prepared['id']})
        return process, popen, status

    def test_load_is_read_only_and_resolves_paths_without_tools(self):
        before = {str(p): p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with patch('oncotracer_cli.variants.preflight_variant_tools', side_effect=AssertionError('unexpected tool discovery')), patch('subprocess.Popen', side_effect=AssertionError('unexpected execution')):
            loaded = self.load()
        self.assertEqual(loaded['config']['variant_reference'], str(self.reference))
        self.assertEqual(loaded['samples'], [{'sample': 'FIXTURE', 'bam': str(self.bam), 'status': 'tumor'}])
        self.assertFalse(Path(loaded['suggested_project']).exists())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.iterdir() if p.is_file()})

    def test_prepare_preserves_server_inputs_and_validates_real_options(self):
        prepared = self.prepare(mode='ont', variant_reference='/not/accepted', variant_bam_manifest='/not/accepted',
                                variant_varlociraptor='required', variant_varlociraptor_fdr='0.10')
        config = load_flat_yaml(Path(prepared['config_path']))
        self.assertTrue(prepared['valid'], prepared['check'])
        self.assertEqual(config['mode'], 'illumina')
        self.assertEqual(config['variant_reference'], str(self.reference))
        self.assertEqual(config['variant_bam_manifest'], str(self.manifest))
        self.assertEqual(float(config['variant_varlociraptor_fdr']), 0.1)
        self.assertEqual(config['outdir'], str(self.root / 'new-project/results'))
        self.assertFalse(Path(config['outdir']).exists())
        self.assertFalse(config['force'])

    def test_empty_override_clears_loaded_optional_fields(self):
        scenario = self.root / 'scenario.yml'; scenario.write_text('scenario fixture')
        self.values['variant_varlociraptor_scenario'] = str(scenario)
        self.config.write_text(render_flat_yaml(self.values))
        prepared = self.prepare(variant_varlociraptor_scenario='')
        self.assertNotIn('variant_varlociraptor_scenario', load_flat_yaml(Path(prepared['config_path'])))

    def test_inputs_changed_between_load_and_prepare_are_rejected(self):
        loaded = self.load()
        self.bam.write_bytes(b'changed BAM')
        with self.assertRaisesRegex(OncoTracerError, 'changed'):
            self.prepare(loaded)
        self.assertFalse((self.root / 'new-project').exists())

    def test_missing_tools_save_invalid_review_but_cannot_run(self):
        loaded = self.load()
        with patch('oncotracer_cli.variants.preflight_variant_tools', side_effect=OncoTracerError('missing caller')):
            prepared = self.state.variant_prepare({'load_id': loaded['load_id'], 'project': str(self.root / 'new-project'), 'threads': 1})
        self.assertFalse(prepared['valid'])
        self.assertIn('missing caller', prepared['check']['errors'][0])
        with self.assertRaisesRegex(OncoTracerError, 'validate'):
            self.state.run({'project_id': prepared['id']})

    def test_ffperase_resources_preflight_is_required(self):
        with patch('oncotracer_cli.ffperase.discover', side_effect=OncoTracerError('missing models')) as discovery:
            prepared = self.prepare(variant_specimen_type='ffpe', variant_ffperase='required')
        discovery.assert_called_once()
        self.assertFalse(prepared['valid'])
        self.assertIn('missing models', prepared['check']['errors'][0])

    def test_existing_project_and_resource_overlap_are_rejected(self):
        with self.assertRaisesRegex(OncoTracerError, 'existing folders'):
            self.prepare(project=str(self.root))
        resources = self.root / 'model'; resources.mkdir(); (resources / 'model.joblib').write_bytes(b'model')
        with self.assertRaisesRegex(OncoTracerError, 'overlap'):
            self.prepare(project=str(resources / 'project'), variant_ffperase_models=str(resources))
        self.assertFalse((resources / 'project').exists())

    def test_run_uses_variants_command_and_preserves_lifecycle(self):
        prepared = self.prepare()
        process, popen, status = self.start(prepared)
        self.assertEqual(popen.call_args.args[0][-3:], ['variants', '--config', prepared['config_path']])
        self.assertNotIn('--run', popen.call_args.args[0])
        self.assertEqual(status['status'], 'running')
        out = Path(prepared['outdir']); out.mkdir(); (out / 'index.html').write_text('result')
        self.state._wait(self.state.job)
        final = self.state.status()
        self.assertEqual(final['status'], 'complete')
        self.assertIn('/results/', final['results_url'])

    def test_run_rejects_changed_inputs_tools_and_saved_configuration(self):
        prepared = self.prepare()
        original = self.tool.read_bytes()
        self.tool.write_bytes(b'changed tool')
        with self.assertRaisesRegex(OncoTracerError, 'changed'):
            self.state.run({'project_id': prepared['id']})
        self.tool.write_bytes(original)
        Path(prepared['config_path']).write_text('changed: true\n')
        with self.assertRaisesRegex(OncoTracerError, 'configuration changed'):
            self.state.run({'project_id': prepared['id']})

    def test_partial_failure_requires_exit_two_and_fresh_status(self):
        prepared = self.prepare()
        process, _, _ = self.start(prepared)
        process.wait.return_value = 2
        status_file = Path(prepared['outdir']) / '08_variants/variant_status.json'
        status_file.parent.mkdir(parents=True)
        status_file.write_text(json.dumps({'overall_status': 'partial_failure'}))
        self.publish_partial_finalization(prepared)
        self.state._wait(self.state.job)
        self.assertEqual(self.state.status()['status'], 'partial_failure')
        self.state.job['_prior_variant_status_mtime'] = status_file.stat().st_mtime_ns
        self.state._wait(self.state.job)
        self.assertEqual(self.state.status()['status'], 'failed')
        self.state.job['_prior_variant_status_mtime'] = None
        process.wait.return_value = 1
        self.state._wait(self.state.job)
        self.assertEqual(self.state.status()['status'], 'failed')

    def test_web_argument_defaults_do_not_require_setup_namespace(self):
        from argparse import Namespace
        state = WebState(self.root, Namespace(variant_config=str(self.config)))
        state.hardware = HARDWARE
        self.assertEqual(state.system()['defaults']['variant_config'], str(self.config))

    def test_editing_owned_unstarted_review_replaces_config_and_invalidates_old_id(self):
        loaded = self.load()
        first = self.prepare(loaded)
        second = self.prepare(loaded, variant_varlociraptor='required')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotIn(first['id'], self.state.projects)
        self.assertEqual(load_flat_yaml(Path(second['config_path']))['variant_varlociraptor'], 'required')
        with self.assertRaisesRegex(OncoTracerError, 'validate'):
            self.state.run({'project_id': first['id']})

    def test_started_or_foreign_files_prevent_reusing_owned_project(self):
        loaded = self.load()
        first = self.prepare(loaded)
        foreign = Path(first['project']) / 'keep.txt'; foreign.write_text('preserve me')
        with self.assertRaisesRegex(OncoTracerError, 'started or changed'):
            self.prepare(loaded, variant_varlociraptor='required')
        self.assertEqual(foreign.read_text(), 'preserve me')

    def publish_partial_finalization(self, prepared):
        from oncotracer_cli.runtime import sha256_file
        outdir = Path(prepared['outdir'])
        status_path = outdir / '08_variants/variant_status.json'
        summary = outdir / '06_workflow_summary/workflow_summary.json'
        summary.parent.mkdir()
        summary.write_text(json.dumps({'analysis': 'variants', 'workflow_status': 'partial_failure', 'variant_status': 'partial_failure'}))
        manifest = {'schema': 'oncotracer-native-run-manifest-v1', 'workflow_status': 'partial_failure',
                    'variant_status': 'partial_failure', 'config_sha256': sha256_file(Path(prepared['config_path'])),
                    'files': [{'path': str(p.relative_to(outdir)), 'sha256': sha256_file(p)} for p in (status_path, summary)]}
        (summary.parent / 'native_run_manifest.json').write_text(json.dumps(manifest))
        (outdir / 'index.html').write_text('published results')

    def test_exit_two_with_partial_status_but_no_finalization_is_failed(self):
        prepared = self.prepare()
        process, _, _ = self.start(prepared)
        process.wait.return_value = 2
        status_file = Path(prepared['outdir']) / '08_variants/variant_status.json'
        status_file.parent.mkdir(parents=True)
        status_file.write_text(json.dumps({'overall_status': 'partial_failure'}))
        self.state._wait(self.state.job)
        self.assertEqual(self.state.status()['status'], 'failed')
        self.assertNotIn('results_url', self.state.status())

    def test_partial_status_with_inconsistent_final_manifest_is_failed(self):
        prepared = self.prepare()
        process, _, _ = self.start(prepared)
        process.wait.return_value = 2
        status_file = Path(prepared['outdir']) / '08_variants/variant_status.json'
        status_file.parent.mkdir(parents=True)
        status_file.write_text(json.dumps({'overall_status': 'partial_failure'}))
        self.publish_partial_finalization(prepared)
        summary = Path(prepared['outdir']) / '06_workflow_summary/workflow_summary.json'
        summary.write_text(json.dumps({'analysis': 'variants', 'workflow_status': 'failed'}))
        self.state._wait(self.state.job)
        self.assertEqual(self.state.status()['status'], 'failed')
