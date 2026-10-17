"""Conda and Docker must use the same caller and linked-library contract."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT/'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import bcftools_compat as caller
import launch
import patient_metadata
import import_alignment
from oncotracer_cli import glimpse_readiness as ready
from oncotracer_cli.runtime import OncoTracerError

VERSION = 'bcftools 1.23.1\nUsing htslib 1.24\nCopyright fixture\n'
HELP = '--multiallelic-caller --keep-alts --ploidy --format-fields --keep-unseen-allele'


class BcftoolsRuntimeTests(unittest.TestCase):
    def test_exact_caller_and_linked_library_are_required(self):
        info = caller.require_version(VERSION, '/chosen/bin/bcftools')
        self.assertEqual(info, {'bcftools':'1.23.1', 'htslib':'1.24', 'executable':'/chosen/bin/bcftools'})
        for output in ('bcftools 1.17\nUsing htslib 1.17\n',
                       'bcftools 1.23.1\nUsing htslib 1.23\n',
                       'bcftools 1.23.1\n', '', 'bcftools 1.24\nUsing htslib 1.24\n'):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'requires bcftools 1.23.1 linked to HTSlib 1.24') as error:
                caller.require_version(output, '/chosen/bin/bcftools')
            self.assertIn('/chosen/bin/bcftools', str(error.exception))
            self.assertIn('oncotracer install --conda', str(error.exception))
            self.assertIn('select it automatically', str(error.exception))
            self.assertIn('variant_tool_prefix', str(error.exception))
            self.assertIn('Dockerfile', str(error.exception))

    def test_shared_environment_pins_the_runtime_pair(self):
        environment = (ROOT/'environments/native-variants.yml').read_text().splitlines()
        for pin in ('bcftools='+caller.REQUIRED_BCFTOOLS, 'htslib='+caller.REQUIRED_HTSLIB, 'samtools=1.23.1'):
            self.assertIn('  - '+pin, environment)
        self.assertIn('native-variants.yml', (ROOT/'Dockerfile').read_text())

    def test_controller_rejects_a_mismatch_before_other_runtime_checks(self):
        tools = {'bcftools':'/selected/bin/bcftools', 'nextflow':'nextflow'}
        for version in ('bcftools 1.17\nUsing htslib 1.17\n', 'bcftools 1.23.1\nUsing htslib 1.23\n'):
            with self.subTest(version=version), patch.object(ready, 'probe', return_value=version) as probe, \
                 patch.object(ready, 'analysis_python') as scientific:
                with self.assertRaisesRegex(OncoTracerError, 'selected /selected/bin/bcftools reports'):
                    ready.check(None, tools, None, BIN.parent)
            self.assertEqual(probe.call_count, 1)
            scientific.assert_not_called()

    def test_explicit_missing_caller_does_not_fall_back_to_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fallback = root/'bcftools'; fallback.write_text('#!/bin/sh\nexit 0\n'); fallback.chmod(0o755)
            with patch.object(launch, 'LOCAL', root), patch.object(launch.shutil, 'which', return_value=str(fallback)):
                with self.assertRaisesRegex(ValueError, 'Missing executable bcftools'):
                    launch.find_tool('bcftools', root/'missing-selected-prefix')

    def workflow_fixture(self, root):
        reference = root/'genome.fa'
        reference.write_text('>chr1\nAAAA\n')
        Path(str(reference)+'.fai').write_text('chr1\t4\t6\t4\t5\n')
        reference.with_suffix('.dict').write_text('@SQ\tSN:chr1\tLN:4\n')
        return launch.parser().parse_args(['--bam',str(root/'sample.bam'),'--sample','SAMPLE',
                                          '--reference',str(reference),'--output',str(root/'output'),
                                          '--allow-test-reference'])

    def test_bundled_preflight_rejects_old_caller_before_alignment_or_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); args = self.workflow_fixture(root)
            def version(command, **kwargs):
                name = Path(command[0]).name
                text = 'bcftools 1.17\nUsing htslib 1.17\n' if name == 'bcftools' else name+' fixture\n'
                return subprocess.CompletedProcess(command, 0, text)
            with patch.object(launch, 'find_tool', side_effect=lambda name,prefix: str(root/'bin'/name)), \
                 patch.object(launch.subprocess, 'run', side_effect=version), \
                 patch.object(patient_metadata, 'prepare'), \
                 patch.object(import_alignment, 'validate_bam') as alignment, \
                 patch.object(launch, 'choose_annotation') as annotation:
                with self.assertRaisesRegex(ValueError, 'bcftools 1.17, HTSlib 1.17'):
                    launch.prepare(args)
            alignment.assert_not_called(); annotation.assert_not_called()
            self.assertFalse((root/'output/pipeline_info/runtime.json').exists())

    def test_bundled_preflight_records_linked_library_and_prints_selected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); args = self.workflow_fixture(root); captured = []
            def version(command, **kwargs):
                name = Path(command[0]).name
                text = (HELP if command[1:3] == ['call','--help'] else VERSION) if name == 'bcftools' else name+' fixture\n'
                return subprocess.CompletedProcess(command, 0, text)
            terminal = io.StringIO()
            with patch.object(launch, 'find_tool', side_effect=lambda name,prefix: str(root/'bin'/name)), \
                 patch.object(launch.subprocess, 'run', side_effect=version), \
                 patch.object(patient_metadata, 'prepare', side_effect=captured.append), \
                 patch.object(import_alignment, 'validate_bam', side_effect=RuntimeError('fixture stops before input work')), \
                 contextlib.redirect_stdout(terminal):
                with self.assertRaisesRegex(RuntimeError, 'fixture stops'):
                    launch.prepare(args)
            self.assertEqual(captured[0]['versions']['bcftools_htslib'], '1.24')
            self.assertEqual(captured[0]['versions']['bcftools'], 'bcftools 1.23.1')
            self.assertIn(str(root/'bin/bcftools'), terminal.getvalue())
            self.assertIn('linked HTSlib 1.24', terminal.getvalue())

    def test_library_changes_invalidate_each_variant_reuse_signature(self):
        import continuation
        import analysis_continuation
        import glimpse_continuation
        old = {'versions':{'bcftools':'bcftools 1.23.1','bcftools_htslib':'1.23'},'resources':[]}
        current = {'versions':{'bcftools':'bcftools 1.23.1','bcftools_htslib':'1.24'},'resources':[]}
        self.assertNotEqual(continuation.signature(old), continuation.signature(current))
        self.assertNotEqual(glimpse_continuation.analysis_config(old), glimpse_continuation.analysis_config(current))
        for stage in analysis_continuation.CODE:
            self.assertNotEqual(analysis_continuation.signature(old,stage), analysis_continuation.signature(current,stage))


if __name__ == '__main__':
    unittest.main()
