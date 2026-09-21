"""An exit code never substitutes for a verified published partial report."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from oncotracer_cli.run_completion import published_native_partial
from oncotracer_cli.runtime import sha256_file
from oncotracer_cli.web import WebState


class NativePartialCompletionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.outdir = self.root / 'results'
        self.config = self.root / 'config.yml'
        self.config.write_text('mode: illumina\nrun_variants: true\n')
        self.config_hash = sha256_file(self.config)
        self.summary_path = self.outdir / '06_workflow_summary/workflow_summary.json'
        self.manifest_path = self.summary_path.with_name('native_run_manifest.json')
        self.status_path = self.outdir / '08_variants/variant_status.json'
        self.index = self.outdir / 'index.html'
        self.summary_path.parent.mkdir(parents=True)
        self.status_path.parent.mkdir()

    def publish(self, variant_status='partial_failure', *, include_status=True):
        if include_status:
            self.status_path.write_text(json.dumps({'overall_status': variant_status}))
        summary = {'engine': 'native', 'mode': 'illumina', 'workflow_status': 'partial_failure',
                   'cna_status': 'complete', 'variant_status': variant_status}
        self.summary_path.write_text(json.dumps(summary))
        paths = [self.summary_path] + ([self.status_path] if include_status else [])
        manifest = dict(summary, schema='oncotracer-native-run-manifest-v1', config_sha256=self.config_hash,
                        files=[{'path': str(p.relative_to(self.outdir)), 'sha256': sha256_file(p)} for p in paths])
        self.manifest_path.write_text(json.dumps(manifest))
        self.index.write_text('Published results')

    def verified(self, prior=None):
        return published_native_partial(self.outdir, self.config_hash, prior)

    def test_fresh_published_partial_passes_and_old_manifest_fails(self):
        self.publish()
        self.assertTrue(self.verified())
        self.assertFalse(self.verified(self.manifest_path.stat().st_mtime_ns))

    def test_successful_other_branch_can_preserve_a_failed_variant_stage(self):
        self.publish('failed', include_status=False)
        self.assertTrue(self.verified())

    def test_matching_summary_and_manifest_required(self):
        self.publish()
        self.assertFalse(published_native_partial(self.outdir, 'different configuration'))
        summary = json.loads(self.summary_path.read_text())
        summary['variant_status'] = 'failed'
        self.summary_path.write_text(json.dumps(summary))
        self.assertFalse(self.verified())

    def test_missing_index_or_required_variant_status_fails(self):
        self.publish()
        self.index.unlink()
        self.assertFalse(self.verified())
        self.index.write_text('Published results')
        self.status_path.unlink()
        self.assertFalse(self.verified())

    def test_status_mutation_after_publication_fails(self):
        self.publish()
        self.status_path.write_text(json.dumps({'overall_status': 'complete'}))
        self.assertFalse(self.verified())

    def test_browser_uses_verified_partial_only_for_exit_two(self):
        self.publish()
        state = WebState(self.root)
        state.projects['test'] = {'id': 'test', 'outdir': str(self.outdir), 'backend': 'docker',
                                 'config_path': str(self.config), 'fingerprint': {str(self.config): self.config_hash}}
        process = Mock()
        job = {'project_id': 'test', 'process': process, 'status': 'running'}
        process.wait.return_value = 2
        state._wait(job)
        self.assertEqual(job['status'], 'partial_failure')
        self.assertIn('results_url', job)
        process.wait.return_value = 1
        state._wait(job)
        self.assertEqual(job['status'], 'failed')
        process.wait.return_value = 2
        self.index.unlink()
        state._wait(job)
        self.assertEqual(job['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
