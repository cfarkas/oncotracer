"""Fail before downloads and route scientific work to a verified interpreter."""
from dataclasses import replace
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oncotracer_cli import glimpse_readiness as ready, glimpse_pipeline as pipeline
from oncotracer_cli.variants import resolve_variant_request
from oncotracer_cli.runtime import OncoTracerError


class GlimpseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh'}, mode='illumina')
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME':str(self.root/'config')})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_real_failed_binary_reports_problem_without_accepting_path_presence(self):
        with self.assertRaisesRegex(OncoTracerError, 'broken tool.*exit 4.*missing library'):
            ready.probe([sys.executable, '-c', 'import sys;print("missing library");sys.exit(4)'], 'broken tool')

    def test_timeout_is_a_readiness_failure(self):
        with patch.object(ready.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 60)):
            with self.assertRaisesRegex(OncoTracerError, 'not ready'):
                ready.probe(['tool'], 'tool')

    def test_lightweight_controller_falls_back_to_configured_scientific_python(self):
        core = self.root/'core'; (core/'bin').mkdir(parents=True)
        interpreter = core/'bin/python'; interpreter.write_text('fixture')
        folder = self.root/'config/oncotracer';folder.mkdir(parents=True)
        (folder/'config.json').write_text(json.dumps({'core_prefix':str(core)}))
        commands = []
        def check(command, *args, **kwargs):
            commands.append(command)
            if command[0] == sys.executable:
                raise OncoTracerError('missing numpy')
            self.assertEqual(command[0], str(interpreter))
        with patch.object(ready, 'probe', side_effect=check):
            found = ready.analysis_python(self.request)
        self.assertEqual(found, str(interpreter))
        self.assertEqual(commands[1][1:4], ['-I','-B','-c'])
        self.assertIn('import_module', commands[1][4])
        self.assertNotIn('find_spec', commands[1][4])

    def test_missing_dependencies_stop_with_actionable_error(self):
        with patch.object(ready, 'probe', side_effect=OncoTracerError('missing pysam')):
            with self.assertRaisesRegex(OncoTracerError, 'No ready GLIMPSE2 analysis Python.*pysam'):
                ready.analysis_python(self.request)

    def test_offline_report_check_preserves_exact_model_and_interpreter(self):
        bundle = self.root/'bundle'; (bundle/'bin').mkdir(parents=True)
        shutil.copyfile(pipeline.BUNDLE/'bin/bcftools_compat.py', bundle/'bin/bcftools_compat.py')
        (bundle/'bin/llm_reporting.py').write_text(
            'def prepare(c):\n c["llm_python"]="/report/python"\n c["llm_model_directory"]="/local/model"\n')
        tools = {'samtools':'samtools','bcftools':'bcftools','gatk':'gatk','nextflow':'nextflow'}
        child = {'HOME':'/private/worker/home', 'XDG_CACHE_HOME':'/private/worker/cache'}
        with patch.object(ready,'analysis_python',return_value='/scientific/python'), \
             patch.object(ready,'prepare_nextflow_environment',return_value={'NXF_DIST':'/prepared/framework'}) as prepare, \
             patch.object(ready,'probe',return_value='bcftools 1.23.1\nUsing htslib 1.24\n') as probe:
            result = ready.check(self.request, tools, None, bundle, environment=child)
        prepare.assert_called_once_with('nextflow', child)
        self.assertEqual(result['nextflow_environment']['NXF_DIST'], '/prepared/framework')
        nf_probe = next(c for c in probe.call_args_list if c.args[0] == ['nextflow','-version'])
        self.assertEqual(nf_probe.kwargs['env']['HOME'], child['HOME'])
        self.assertEqual(nf_probe.kwargs['env']['NXF_DIST'], '/prepared/framework')
        self.assertEqual(result['python'], '/scientific/python')
        self.assertEqual(result['llm_python'], '/report/python')
        self.assertEqual(result['llm_model'], '/local/model')
        command = probe.call_args.args[0]
        self.assertIn('local_files_only=True', command[4])
        self.assertIn('trust_remote_code=False', command[4])
        self.assertEqual(command[-1], '/local/model')
        self.assertEqual(ready._environment()['HF_HUB_OFFLINE'], '1')

    def test_invalid_report_resources_fail_in_preflight(self):
        bundle = self.root/'bundle';(bundle/'bin').mkdir(parents=True)
        (bundle/'bin/llm_reporting.py').write_text('def prepare(c):\n raise ValueError("model missing")\n')
        with patch.object(ready,'analysis_python',return_value='/scientific/python'), \
             patch.object(ready,'prepare_nextflow_environment',return_value={'NXF_DIST':'/prepared/framework'}),patch.object(ready,'probe'):
            with self.assertRaisesRegex(OncoTracerError, 'Local report resources are not ready.*model missing'):
                ready.check(self.request, {'nextflow':'nextflow'}, None, bundle)

    def test_resource_and_ffpe_children_use_selected_scientific_python(self):
        resources=self.root/'resources'
        params={'resource_dir':str(resources),'clinvar':str(resources/'clinvar.vcf.gz'),
                'python':'/scientific/python','report_llm':False,'reference':'/fixture/ref.fa'}
        calls=[]
        def run(*args,**kwargs):
            calls.append(args)
            if args[0]=='glimpse-clinvar-preflight':
                command=args[1]
                Path(command[command.index('--result')+1]).write_text(
                    json.dumps({'clinvar':str(resources/'clinvar.vcf.gz')}))
        runner=SimpleNamespace(run=run)
        pipeline.ensure_resources(params,runner,pipeline.BUNDLE,bcftools='/scientific/bcftools')
        self.assertEqual([call[1][0] for call in calls], ['/scientific/python']*3)
        request=SimpleNamespace(ffperase_root=None,ffperase_models=None,ffperase_prefix=None,ffperase_sif=None)
        cmd=pipeline._ffpe_command(request,'calls.vcf',self.root,'SAMPLE',[],'sample.bam','ref.fa',
                                   {'python':'/scientific/python','samtools':'samtools','gatk':'gatk'})
        self.assertEqual(cmd[0], '/scientific/python')
        self.assertIn('oncotracer_cli.glimpse_ffpe', cmd[2])


if __name__ == '__main__': unittest.main()
