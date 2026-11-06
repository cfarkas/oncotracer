"""Public merge choices and strict YAML validation."""
import contextlib
import io
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from argparse import Namespace
from oncotracer_cli.cli import parse_arguments, _run_docker, _run_host
from oncotracer_cli.glimpse_pipeline import validate_options
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.setup import _variant_values


class OptionalCohortTests(unittest.TestCase):
    def test_all_cli_entrypoints_preserve_unset_enable_and_disable(self):
        entries=[['run','--config','run.yml'],['internal-run','--config','run.yml'],
                 ['variants','--config','run.yml'],['setup'],['--interactive']]
        for entry in entries:
            self.assertIsNone(parse_arguments(entry).variant_merge_vcfs)
            for flag,expected in [('--merge-vcfs',True),('--no-merge-vcfs',False)]:
                with self.subTest(entry=entry,flag=flag):
                    self.assertIs(parse_arguments([*entry,flag]).variant_merge_vcfs,expected)
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parse_arguments([*entry,'--merge-vcfs','--no-merge-vcfs'])

    def test_yaml_requires_an_actual_boolean_and_defaults_to_enabled(self):
        self.assertIs(validate_options({})['merge_vcfs'],True)
        for value in [True,False]:
            self.assertIs(validate_options({'variant_merge_vcfs':value})['merge_vcfs'],value)
        for value in [None,'',0,1,'false','true',[],{}]:
            with self.subTest(value=value),self.assertRaisesRegex(OncoTracerError,'variant_merge_vcfs must be true or false'):
                validate_options({'variant_merge_vcfs':value})

    def test_terminal_can_decline_merge_and_explicit_cli_choice_skips_prompt(self):
        for selected in [None,False]:
            prompts=[]
            def answer(prompt):
                prompts.append(prompt)
                return 'no' if 'Merge sample VCFs' in prompt else ''
            args=Namespace(variants=True,analysis='cna',variant_specimen_type='fresh',backend='host',variant_merge_vcfs=selected)
            with patch('builtins.input',side_effect=answer),patch('oncotracer_cli.variants.resolve_variant_request'),contextlib.redirect_stdout(io.StringIO()):
                values=_variant_values(args,'illumina',interactive=True)
            self.assertIs(values['variant_merge_vcfs'],False)
            self.assertEqual(any('Merge sample VCFs' in p for p in prompts),selected is None)

    def test_docker_forwards_explicit_merge_choice_to_internal_run(self):
        config=Path('/data/study/config/run.yml')
        for flag,enabled in [('--merge-vcfs',True),('--no-merge-vcfs',False)]:
            args=parse_arguments(['run','--config',str(config),'--backend','docker','--dry-run',flag])
            with patch('oncotracer_cli.cli._load_install_config',return_value={}),patch('oncotracer_cli.cli._project_mounts',return_value=[config.parent]),patch('oncotracer_cli.cli.load_flat_yaml',return_value={}),patch('oncotracer_cli.cli._run') as execute:
                _run_docker(config,args)
            argv=execute.call_args.args[0]
            internal=argv[argv.index('internal-run'):]
            self.assertIs(parse_arguments(list(map(str,internal))).variant_merge_vcfs,enabled)

    def test_host_forwards_merge_override_without_changing_saved_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'run.yml'
            config.write_text('mode: illumina\nrun_variants: false\nvariant_merge_vcfs: true\n')
            before=config.read_bytes()
            args=parse_arguments(['run','--config',str(config),'--backend','host','--dry-run','--no-merge-vcfs'])
            with patch('oncotracer_cli.cli._load_install_config',return_value={}),patch('oncotracer_cli.cli.run_native',return_value=Path(d)/'results') as native:
                _run_host(config,args)
            self.assertIs(native.call_args.kwargs['merge_vcfs'],False)
            self.assertEqual(config.read_bytes(),before)
