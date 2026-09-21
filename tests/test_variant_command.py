"""Existing-BAM CLI ownership, validation, real calling and failure recovery."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import main, build_parser, execute_run
from oncotracer_cli.runtime import OncoTracerError, render_flat_yaml
from oncotracer_cli import variant_command
from tests.test_native_variants import SyntheticInput, vcf_rows


class VariantCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bam = self.root / 'sample.bam'
        self.bam.write_bytes(b'BAM path fixture; dry run does not decode')
        self.reference = self.root / 'reference.fa'
        self.reference.write_text('>chr1\nACGT\n')
        self.manifest = self.root / 'samples.tsv'
        self.manifest.write_text('sample\tbam\tstatus\nFIXTURE\tsample.bam\ttumor\n')
        self.outdir = self.root / 'results'
        self.config = self.root / 'variants.yml'
        self.values = {'mode':'illumina', 'outdir':'results', 'variant_reference':'reference.fa',
                       'variant_bam_manifest':'samples.tsv', 'run_variants':True, 'variant_specimen_type':'fresh',
                       'variant_callers':'bcftools', 'variant_annovar':'off', 'threads':1}
        self.save()

    def save(self, **updates):
        self.values.update(updates)
        self.config.write_text(render_flat_yaml(self.values))

    def cli(self, *flags):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(['variants','--config',str(self.config),*flags])
        return code, output.getvalue()

    def test_manual_variant_yaml_cannot_execute_in_unsupported_containers(self):
        for backend in ("singularity", "apptainer"):
            args=build_parser().parse_args(["run", "--config", str(self.config), "--backend", "docker"])
            args.backend=backend
            with (self.subTest(backend=backend),
                  patch("oncotracer_cli.cli._prepare_configured_hg38", side_effect=AssertionError("download attempted")),
                  patch("oncotracer_cli.cli._run_docker", side_effect=AssertionError("container started")),
                  patch("oncotracer_cli.cli._run_singularity", side_effect=AssertionError("container started")),
                  self.assertRaisesRegex(OncoTracerError, "variants branch requires backend")):
                execute_run(self.config,args)
            self.assertFalse(self.outdir.exists())

    def test_dry_run_resolves_relative_paths_without_tools_or_writes(self):
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(variant_command, 'preflight_variant_tools', side_effect=AssertionError('dry-run executed tool discovery')):
            code, output = self.cli('--dry-run','--threads','2')
        self.assertEqual(code,0,output)
        plan = json.loads(output)
        self.assertEqual(plan['samples'], [{'sample':'FIXTURE','bam':str(self.bam),'status':'tumor'}])
        self.assertEqual(plan['outdir'],str(self.outdir))
        self.assertEqual(plan['reference'],str(self.reference))
        self.assertEqual(plan['threads'],2)
        self.assertFalse(plan['nextflow_used'])
        self.assertFalse(self.outdir.exists())
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_bad_manifest_and_sample_duplicates_fail_before_writes(self):
        for data,message in [
            ('sample\tbam\nFIXTURE\tsample.bam\n','columns'),
            ('sample\tbam\tstatus\nFIXTURE\tsample.bam\tother\n','tumor or normal'),
            ('sample\tbam\tstatus\nFIXTURE\tsample.bam\ttumor\nFIXTURE\tsample.bam\tnormal\n','Duplicate sample'),
            ('sample\tbam\tstatus\n../unsafe\tsample.bam\ttumor\n','invalid sample'),
            ('sample\tbam\tstatus\nFIXTURE\tabsent.bam\ttumor\n','missing or empty'),
            ('sample\tbam\tstatus\nFIXTURE\treference.fa\ttumor\n','BAM file'),
        ]:
            with self.subTest(message=message):
                self.manifest.write_text(data)
                code,output = self.cli('--dry-run')
                self.assertEqual(code,2,output)
                self.assertIn(message,output)
                self.assertFalse(self.outdir.exists())

    def test_same_physical_bam_aliases_are_not_independent_samples(self):
        alias = self.root/'alias.bam';os.link(self.bam,alias)
        self.manifest.write_text('sample\tbam\tstatus\nA\tsample.bam\ttumor\nB\talias.bam\tnormal\n')
        code,output = self.cli('--dry-run')
        self.assertEqual(code,2,output)
        self.assertIn('physical BAM',output)
        self.assertFalse(self.outdir.exists())

    def test_overlap_and_foreign_output_are_protected_even_with_force(self):
        self.save(outdir=str(self.root))
        code,output = self.cli('--dry-run','--force')
        self.assertEqual(code,2,output);self.assertIn('overlap',output)
        self.save(outdir='results');self.outdir.mkdir();foreign=self.outdir/'keep.txt';foreign.write_text('unowned data')
        code,output = self.cli('--force')
        self.assertEqual(code,2,output)
        self.assertEqual(foreign.read_text(),'unowned data')
        self.assertEqual([p.name for p in self.outdir.iterdir()],['keep.txt'])

    def test_symlink_output_and_missing_tools_leave_output_untouched(self):
        external=self.root/'external';external.mkdir();self.outdir.symlink_to(external,target_is_directory=True)
        code,output=self.cli('--dry-run')
        self.assertEqual(code,2,output);self.assertIn('symlink',output)
        self.assertEqual(list(external.iterdir()),[])
        self.outdir.unlink()
        with patch.object(variant_command,'preflight_variant_tools',side_effect=OncoTracerError('missing caller')):
            code,output=self.cli()
        self.assertEqual(code,2,output);self.assertIn('missing caller',output)
        self.assertFalse(self.outdir.exists())

    @unittest.skipUnless(shutil.which('samtools') and shutil.which('bcftools'), 'samtools and bcftools required')
    def test_real_bcftools_exports_resume_failure_recovery_and_preserves_sources(self):
        fixture_root=self.root/'synthetic';fixture_root.mkdir()
        fixture=SyntheticInput(fixture_root)
        self.manifest.write_text(f'sample\tbam\tstatus\nFIXTURE\t{fixture.bam}\ttumor\n')
        self.save(variant_reference=str(fixture.reference))
        code,output=self.cli()
        self.assertEqual(code,0,output)
        summary=json.loads((self.outdir/'06_workflow_summary/workflow_summary.json').read_text())
        self.assertEqual(summary['analysis'],'variants')
        self.assertEqual(summary['cna_status'],'not_requested')
        self.assertEqual(summary['variant_status'],'complete')
        self.assertTrue((self.outdir/'index.html').is_file())
        status=json.loads((self.outdir/'08_variants/variant_status.json').read_text())
        caller=status['samples'][0]['callers'][0]
        self.assertTrue(any(row['key']==('chr1',120,'C','T') for row in vcf_rows(Path(caller['vcf']))))
        self.assertFalse((self.outdir/'.oncotracer-native/active-run.json').exists())
        fixture.assert_unchanged(self)
        code,output=self.cli()
        self.assertEqual(code,0,output)
        status=json.loads((self.outdir/'08_variants/variant_status.json').read_text())
        self.assertTrue(status['samples'][0]['callers'][0]['resumed'])
        with patch.object(variant_command,'run_variants',side_effect=OncoTracerError('deliberate caller failure')):
            code,output=self.cli('--force')
        self.assertEqual(code,2,output)
        summary=json.loads((self.outdir/'06_workflow_summary/workflow_summary.json').read_text())
        self.assertEqual(summary['workflow_status'],'failed')
        self.assertFalse((self.outdir/'.oncotracer-native/active-run.json').exists())
        self.assertTrue(Path(caller['vcf']).is_file())
        code,output=self.cli()
        self.assertEqual(code,0,output)
        fixture.assert_unchanged(self)
        # Authentic output ownership alone must not permit changing workflow type.
        (self.outdir/'01_samurai_illumina').mkdir()
        code,output=self.cli('--force')
        self.assertEqual(code,2,output)
        self.assertIn('separate outdir',output)


    def test_finalized_partial_returns_two_without_error_prefix(self):
        status = {'schema': 'oncotracer-native-variants-v1', 'overall_status': 'partial_failure',
                  'completed_samples': [], 'failed_samples': ['FIXTURE'],
                  'samples': [{'sample': 'FIXTURE', 'status': 'partial_failure', 'callers': [
                      {'caller': 'bcftools', 'status': 'partial_failure', 'annotation': {'status': 'complete'},
                       'assessments': {'ffperase': {'status': 'not_assessed',
                           'reason': 'Coverage is insufficient for the log-depth feature.'}}}]}]}
        with (patch.object(variant_command, 'preflight_variant_tools', return_value={}),
              patch.object(variant_command, 'run_variants', return_value=status)):
            code, output = self.cli()
        self.assertEqual(code, 2, output)
        self.assertIn('PARTIAL FAILURE: FFPERASE not assessed:', output)
        self.assertIn('Coverage is insufficient for the log-depth feature.', output)
        self.assertNotIn('ERROR:', output)
        self.assertTrue((self.outdir / 'index.html').is_file())
        manifest = json.loads((self.outdir / '06_workflow_summary/native_run_manifest.json').read_text())
        self.assertEqual(manifest['workflow_status'], 'partial_failure')
        self.assertEqual(manifest['variant_status'], 'partial_failure')

    def test_genuine_failure_still_uses_error_prefix(self):
        with (patch.object(variant_command, 'preflight_variant_tools', return_value={}),
              patch.object(variant_command, 'run_variants', side_effect=OncoTracerError('deliberate caller failure'))):
            code, output = self.cli()
        self.assertEqual(code, 2, output)
        self.assertIn('ERROR:', output)
        self.assertNotIn('PARTIAL FAILURE:', output)
        summary = json.loads((self.outdir / '06_workflow_summary/workflow_summary.json').read_text())
        self.assertEqual(summary['workflow_status'], 'failed')
        self.assertEqual(summary['variant_error'], 'deliberate caller failure')

    def test_partial_status_with_failed_publication_is_still_an_error(self):
        status = {'overall_status': 'partial_failure', 'samples': [],
                  'completed_samples': [], 'failed_samples': ['FIXTURE']}
        with (patch.object(variant_command, 'preflight_variant_tools', return_value={}),
              patch.object(variant_command, 'run_variants', return_value=status),
              patch('oncotracer_cli.results.write_results_index', side_effect=OncoTracerError('report publication failed'))):
            code, output = self.cli()
        self.assertEqual(code, 2, output)
        self.assertIn('ERROR: report publication failed', output)
        self.assertNotIn('PARTIAL FAILURE:', output)
        self.assertFalse((self.outdir / 'index.html').exists())

    def test_partial_cause_includes_annotation_and_caller_gaps(self):
        status = {'samples': [{'callers': [
            {'caller': 'bcftools', 'status': 'partial_failure',
             'annotation': {'status': 'failed', 'reason': 'Local database unavailable.'}},
            {'caller': 'freebayes', 'status': 'failed', 'error': 'Caller exited.'}]}]}
        message = variant_command._partial_failure_cause(status)
        self.assertIn('ANNOVAR failed: Local database unavailable.', message)
        self.assertIn('freebayes failed: Caller exited.', message)


if __name__=='__main__':
    unittest.main()
