import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.cli import parse_arguments
from oncotracer_cli.launch import command_launch,project_plan,write_project
from oncotracer_cli.runtime import OncoTracerError,load_flat_yaml
from oncotracer_cli.sample_manifest import read_manifest
from oncotracer_cli.docker_runtime import docker_host_preview,docker_mounts
from oncotracer_cli.methylation import _configured_executable,_executable_sha256
from oncotracer_cli.managed_resources import _installed_tree


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.inputs=self.root/'input';self.inputs.mkdir()
        for name in ('one','two'):
            (self.inputs/name/'fastq_pass').mkdir(parents=True)
            (self.inputs/name/'fastq_pass/read.fastq').write_text('@'+name+'\nACGT\n+\nIIII\n')
            (self.inputs/name/'pod5_pass').mkdir()
            (self.inputs/name/'pod5_pass/signal.pod5').write_bytes(b'fixture')
        self.table=self.inputs/'samples.csv'
        self.table.write_text('sample,library,status,tissue\nCASE,one,tumor,blood\nCONTROL,two,normal,CNS\n')

    def args(self,*extra):
        return parse_arguments(['launch','--samples',str(self.table),'--project',str(self.root/'project'),
                                '--cache',str(self.root/'cache'),'--specimen','fresh','--ont-profile','r1041_e82_400bps_sup_v500',*extra])

    def test_dry_run_is_offline_and_does_not_create_outputs_or_caches(self):
        args=self.args('--methylation','--dry-run')
        with patch('oncotracer_cli.launch._docker_image',side_effect=AssertionError('Docker touched')),contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(command_launch(args),0)
        result=json.loads(stream.getvalue());self.assertEqual(result['samples'],2)
        self.assertFalse((self.root/'project').exists());self.assertFalse((self.root/'cache').exists())
        self.assertLessEqual(result['allocation']['total_threads'],16)

    def test_generated_tables_keep_roles_and_inputs_and_use_single_ont_table(self):
        args=self.args('--analysis','cna','--threads','4');plan=project_plan(args)
        config=write_project(args,plan,{})
        values=load_flat_yaml(config)
        self.assertEqual(values['threads'],4);self.assertTrue(values['ichorcna_fallback'])
        self.assertNotIn('ont_barcodes',values);self.assertNotIn('ont_folder',values)
        rows=read_manifest(Path(values['ont_samplesheet']))
        self.assertEqual([(r.sample,r.status) for r in rows],[('CASE','tumor'),('CONTROL','normal')])
        mounts=dict(docker_mounts(config,environment={}))
        self.assertEqual(mounts[self.inputs/'one/fastq_pass'],'ro')
        self.assertEqual(mounts[Path(values['ont_samplesheet'])],'ro')
        self.assertTrue(project_plan(args)['resume'])

    def test_clinical_annotations_are_retained_in_generated_metadata(self):
        import csv
        self.table.write_text('sample,library,status,tissue,diagnosis,sample_type\nCASE,one,tumor,bone marrow,AML,AML\n')
        args=self.args('--analysis','cna');write_project(args,project_plan(args),{})
        with (self.root/'project/config/sample_metadata.csv').open() as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual((row['sample'],row['diagnosis'],row['sample_type'],row['analysis_role']),('CASE','AML','AML','tumor'))

    def test_repeat_refuses_changed_input_table_or_generated_config(self):
        args=self.args('--analysis','cna');config=write_project(args,project_plan(args),{})
        self.table.write_text(self.table.read_text().replace('CASE','NEW'))
        with self.assertRaisesRegex(OncoTracerError,'different inputs'):project_plan(args)
        self.table.write_text(self.table.read_text().replace('NEW','CASE'))
        config.write_text(config.read_text()+'# edited\n')
        with self.assertRaisesRegex(OncoTracerError,'edited'):project_plan(args)

    def test_repeat_refuses_changed_derived_table(self):
        args=self.args('--analysis','cna');write_project(args,project_plan(args),{})
        table=self.root/'project/config/samples.csv';table.write_text(table.read_text().replace('tumor','normal'))
        with self.assertRaisesRegex(OncoTracerError,'sample tables changed'):project_plan(args)

    def test_unknown_nonempty_destination_is_preserved(self):
        project=self.root/'project';project.mkdir();(project/'important').write_text('keep')
        with self.assertRaisesRegex(OncoTracerError,'not empty'):project_plan(self.args())
        self.assertEqual((project/'important').read_text(),'keep')

    def test_resource_methylation_routing_and_cpu_choice_are_saved(self):
        args=self.args('--methylation','--cpu');plan=project_plan(args)
        self.assertEqual(plan['classifiers'],['marlin','sturgeon'])
        config=write_project(args,plan,{'marlin_python':'bundled','sturgeon_license_acknowledged':True})
        values=load_flat_yaml(config)
        self.assertEqual(values['methylation_classifier'],'tissue')
        self.assertFalse(values['methylation_gpu_usage']);self.assertEqual(values['methylation_caller'],'rockfish')
        self.assertTrue(values['run_variants']);self.assertFalse(values['variant_merge_vcfs'])
        self.assertIn('CASE\tblood\t',(self.root/'project/config/methylation.tsv').read_text())

    def test_license_error_occurs_before_docker_or_downloads(self):
        with patch('oncotracer_cli.launch._docker_image',side_effect=AssertionError('Docker touched')):
            with self.assertRaisesRegex(OncoTracerError,'Sturgeon'):command_launch(self.args('--methylation'))
        self.assertFalse((self.root/'cache').exists())

    def test_only_allowlisted_bundled_executables_can_be_deferred(self):
        with docker_host_preview():
            path=_configured_executable({'marlin_python':'bundled'},'marlin_python','python','Python')
            self.assertEqual(str(path),'/opt/oncotracer-envs/marlin/bin/python')
            self.assertEqual(_executable_sha256(path),'')
            with self.assertRaisesRegex(OncoTracerError,'No bundled executable'):
                _configured_executable({'sturgeon_python':'bundled'},'sturgeon_python','python','Python')
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(OncoTracerError,'backend docker'):
                _configured_executable({'marlin_python':'bundled'},'marlin_python','python','Python')

    def test_resource_installation_is_atomic_reusable_and_detects_edits(self):
        root=self.root/'runtime';calls=[]
        def install(stage):
            calls.append(1);stage.mkdir();(stage/'binary').write_bytes(b'original')
        _installed_tree(root,'v1',install);_installed_tree(root,'v1',install)
        self.assertEqual(len(calls),1)
        (root/'binary').write_bytes(b'changed')
        with self.assertRaisesRegex(OncoTracerError,'changed'):_installed_tree(root,'v1',install)

    def test_failed_resource_installation_can_be_retried(self):
        root=self.root/'runtime'
        def failing(stage):
            stage.mkdir();(stage/'partial').write_bytes(b'incomplete');raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):_installed_tree(root,'v1',failing)
        self.assertFalse(root.exists())
        def success(stage):stage.mkdir();(stage/'binary').write_bytes(b'complete')
        _installed_tree(root,'v1',success)
        self.assertEqual((root/'binary').read_bytes(),b'complete')

if __name__=='__main__':unittest.main()
