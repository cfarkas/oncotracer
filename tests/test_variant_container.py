"""Contract tests for the explicit, local ClairS-TO container adapter."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import variants
from oncotracer_cli.runtime import OncoTracerError


class VariantContainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='variant-container-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sif = self.root / 'caller.sif'
        self.sif.write_bytes(b'synthetic SIF placeholder, never executed')
        self.prefix = self.root / 'host tools'
        (self.prefix / 'bin').mkdir(parents=True)
        for name in ('samtools', 'bcftools'):
            path = self.prefix / 'bin' / name
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        self.config = dict(run_variants=True, variant_specimen_type='fresh', variant_callers='clairs_to',
                           variant_clairsto_platform='ont_r10_dorado_sup_5khz',
                           variant_clairsto_sif=str(self.sif), variant_tool_prefix=str(self.prefix))

    def request(self):
        return variants.resolve_variant_request(self.config, mode='ont')

    def test_explicit_sif_keeps_host_core_tools_and_records_image_identity(self):
        request = self.request()
        with patch.object(variants.shutil, 'which', side_effect=lambda name: '/usr/bin/apptainer' if name == 'apptainer' else None):
            tools = variants.preflight_variant_tools(request)
        self.assertEqual(tools['samtools'], str(self.prefix / 'bin/samtools'))
        self.assertEqual(tools['bcftools'], str(self.prefix / 'bin/bcftools'))
        self.assertEqual(tools['clairs_to_sif'], str(self.sif))
        self.assertNotIn('clairs_to', tools)
        self.assertEqual(request.as_dict()['clairsto_sif'], str(self.sif))
        self.assertGreater(variants._identity(Path(tools['clairs_to_sif']))['size'], 0)

    def test_missing_container_or_runtime_fails_explicitly(self):
        self.sif.unlink()
        with self.assertRaises(OncoTracerError):
            self.request()
        self.sif.write_bytes(b'image')
        with patch.object(variants.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(OncoTracerError, 'Apptainer or Singularity'):
                variants.preflight_variant_tools(self.request())

    def test_sif_cannot_be_silently_ignored_for_another_caller(self):
        self.config['variant_callers'] = 'bcftools'
        with self.assertRaisesRegex(OncoTracerError, 'requires selecting'):
            variants.resolve_variant_request(self.config, mode='illumina')

    def test_container_binds_private_work_and_resolved_inputs_cpu_only(self):
        work = self.root / 'private work'
        directory = work / 'clairs_to'
        directory.mkdir(parents=True)
        inputs = self.root / 'read only inputs'
        inputs.mkdir()
        for name in ('sample.bam', 'reference.fa'):
            (inputs / name).write_text('fixture')
            (work / name).symlink_to(inputs / name)
        tools = dict(clairs_to_runtime='/usr/bin/apptainer', clairs_to_sif=str(self.sif), bcftools='/host/bcftools')
        request = self.request()
        command = variants._clairsto_command_prefix(request, tools, directory, work/'sample.bam', work/'reference.fa')
        self.assertIn('--cleanenv', command)
        self.assertIn('CUDA_VISIBLE_DEVICES=', command)
        self.assertNotIn('--nv', command)
        self.assertIn(f'{inputs}:{inputs}:ro', command)
        self.assertIn(f'{work}:{work}:rw', command)
        self.assertEqual(command[-2:], [str(self.sif), '/opt/bin/run_clairs_to'])
        self.assertNotIn('--conda_prefix', command)

    def test_actual_caller_contract_uses_explicit_nondefault_vcf_prefixes(self):
        work = self.root / 'work'
        directory = work / 'call'
        directory.mkdir(parents=True)
        bam, ref, bed = work/'sample.bam', work/'reference.fa', work/'targets.bed'
        calls = []
        class Recorder:
            def run(self, label, argv, **kwargs):
                calls.append((label, [str(x) for x in argv]))
        tools = dict(clairs_to_runtime='/usr/bin/apptainer', clairs_to_sif=str(self.sif), bcftools='/host/bcftools')
        with patch.object(variants, 'validate_vcf', return_value=1), patch.object(variants, '_evidence', return_value=1):
            variants._call(self.request(), 'clairs_to', 'FIXTURE', bam, ref, directory, tools, Recorder(), 2, {}, bed)
        command = calls[0][1]
        self.assertEqual(command[command.index('--snv_output_prefix')+1], 'oncotracer_snv')
        self.assertEqual(command[command.index('--indel_output_prefix')+1], 'oncotracer_indel')
        self.assertEqual(command[command.index('--sample_name')+1], 'FIXTURE')
        self.assertEqual(command[command.index('--bed_fn')+1], str(bed))
        self.assertEqual(command[command.index('--platform')+1], 'ont_r10_dorado_sup_5khz')
        self.assertIn('--disable_verdict', command)
        self.assertNotIn('--conda_prefix', command)
        concat = next(argv for _, argv in calls if 'concat' in argv)
        self.assertTrue(any(x.endswith('/oncotracer_snv.vcf.gz') for x in concat))
        self.assertTrue(any(x.endswith('/oncotracer_indel.vcf.gz') for x in concat))

    def test_ambiguous_bind_paths_are_rejected(self):
        work = self.root / 'comma,work'
        directory = work / 'call'
        directory.mkdir(parents=True)
        with self.assertRaisesRegex(OncoTracerError, 'bind paths'):
            variants._clairsto_command_prefix(self.request(), {'clairs_to_runtime': '/usr/bin/apptainer'},
                                              directory, work/'input.bam', work/'reference.fa')


if __name__ == '__main__':
    unittest.main()
