"""Mixed classifier routing preserves sample identities and all non-methylation work."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_web as web_tests
from tests.test_native_methylation import Fixture, ROOT
from oncotracer_cli.engine import parse_ont_samples
from oncotracer_cli.methylation import resolve_methylation_request, methylation_plan
from oncotracer_cli import methylation_assignments as assignments
from oncotracer_cli import methylation_routing as routing
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, StageLedger
from oncotracer_cli.setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES


class MixedMethylationTests(unittest.TestCase):
    setUp = web_tests.WebTests.setUp
    fastq = web_tests.WebTests.fastq
    prepare = web_tests.WebTests.prepare

    def resources(self):
        resources = {}
        for classifier in ('marlin', 'sturgeon'):
            folder = self.root / ('resources-' + classifier)
            folder.mkdir()
            fixture = Fixture(folder, classifier)
            allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[classifier])
            resources.update({key: value for key, value in fixture.config().items() if key in allowed})
        return resources

    def mixed_project(self, analysis='both'):
        for name in ('A', 'B', 'C'):
            self.fastq(f'named/{name}/fastq_pass/barcode01/reads.fastq.gz')
            if name != 'C':
                for barcode in ('barcode01', 'barcode02'):
                    signal = self.root / f'named/{name}/pod5_pass/{barcode}/signal.pod5'
                    signal.parent.mkdir(parents=True)
                    signal.write_bytes(b'synthetic signal')
        return self.prepare(mode='ont', folder='named', analysis=analysis, classifier='assigned',
            samples=[{'id':0,'name':'marrow','type':'cancer'},
                     {'id':1,'name':'brain','type':'cancer'}, {'id':2,'name':'control','type':'normal'}],
            methylation_assignments=[{'id':0,'classifier':'marlin'},
                                    {'id':1,'classifier':'sturgeon'}, {'id':2,'classifier':''}],
            methylation_caller='modkit', methylation_source='pod5', methylation_path=str(self.root/'named'),
            resource_paths=self.resources(), accept_sturgeon_license=True)

    def test_save_routes_both_models_and_keeps_skipped_control_without_signal(self):
        prepared = self.mixed_project()
        self.assertTrue(prepared['valid'], prepared['check'])
        config = load_flat_yaml(Path(prepared['config_path']))
        self.assertEqual(config['methylation_classifier'], 'assigned')
        self.assertFalse(config['methylation_only'])
        self.assertEqual([(s.sample,s.status) for s in parse_ont_samples(config)],
                         [('marrow','tumor'),('brain','tumor'),('control','normal')])
        self.assertEqual(assignments.read(config['methylation_sample_metadata'], ['marrow','brain','control']),
                         {'marrow':'marlin','brain':'sturgeon','control':''})
        request = resolve_methylation_request(config, mode='ont')
        self.assertEqual([(name, child.classifier) for name,child in request.requests],
                         [('marrow','marlin'),('brain','sturgeon')])
        for (name, child), folder in zip(request.requests, ('A','B')):
            self.assertEqual([g.sample for g in child.sample_signals],[name])
            self.assertEqual(child.pod5_files,(self.root/f'named/{folder}/pod5_pass/barcode01/signal.pod5',))
            self.assertEqual(child.excluded_samples,())
        self.assertEqual(methylation_plan(request)['classifier'],'assigned')
        self.assertEqual([r['methylation_classifier'] for r in prepared['reviewed_summary']['samples']],
                         ['marlin','sturgeon','skip'])
        self.assertEqual(prepared['reviewed_summary']['methylation_excluded_samples'],['control'])
        self.assertFalse(any('not queued' in w for w in prepared['check']['warnings']))

    def test_rockfish_uses_the_same_sample_scoping_in_the_container(self):
        from oncotracer_cli.docker_runtime import docker_host_preview
        prepared=self.mixed_project();config=load_flat_yaml(Path(prepared['config_path']))
        config['methylation_caller']='rockfish'
        with docker_host_preview():request=resolve_methylation_request(config,mode='ont')
        self.assertTrue(all(child.rockfish_resources_deferred for _,child in request.requests))
        self.assertEqual([len(child.sample_signals) for _,child in request.requests],[1,1])
        self.assertEqual([name for name,_ in request.requests],['marrow','brain'])

    def test_methylation_only_can_save_both_groups_in_one_project(self):
        prepared=self.mixed_project('methylation');config=load_flat_yaml(Path(prepared['config_path']))
        self.assertTrue(config['methylation_only'])
        self.assertFalse(config['run_variants'])
        self.assertEqual(config['methylation_classifier'],'assigned')
        self.assertEqual(len(prepared['reviewed_summary']['samples']),3)

    def test_assignment_edit_after_review_blocks_launch(self):
        prepared=self.mixed_project();config=load_flat_yaml(Path(prepared['config_path']))
        path=Path(config['methylation_sample_metadata'])
        path.write_text(path.read_text().replace('marrow\tmarlin','marrow\tsturgeon'))
        with self.assertRaisesRegex(OncoTracerError,'changed'):
            self.state.run({'project_id':prepared['id']})
        self.assertIsNone(self.state.job)

    def test_runner_dispatches_each_assigned_sample_once_and_skips_control(self):
        prepared=self.mixed_project();config=load_flat_yaml(Path(prepared['config_path']))
        request=resolve_methylation_request(config,mode='ont');samples=parse_ont_samples(config)
        calls=[]
        def process(root,child,selected,reference,directory,runner,ledger,**kwargs):
            self.assertEqual(len(selected),1)
            calls.append((selected[0].sample,child.classifier))
            return {'samples':[{'sample':selected[0].sample,'status':'complete','classification':None}]}
        runner=SimpleNamespace(environment={},validators={},protected_environment={},dry_run=True,echo=False)
        output=self.root/'routing-output'
        with patch('oncotracer_cli.methylation.run_methylation',side_effect=process):
            result=routing.run(ROOT,request,samples,{},output,runner,StageLedger(output/'stages.json'),threads=2,force=False)
        self.assertEqual(sorted(calls),[('brain','sturgeon'),('marrow','marlin')])
        self.assertEqual(result['excluded_samples'],['control'])
        self.assertEqual(result['failed_samples'],[])
        self.assertEqual(result['classifier'],'assigned')

    def test_assignments_reject_unknown_missing_duplicate_and_all_skipped_samples(self):
        path=self.root/'invalid.tsv'
        for content in ('sample\tclassifier\nA\tmarlin\n',
                        'sample\tclassifier\nA\tmarlin\nA\tsturgeon\n',
                        'sample\tclassifier\nA\tguess\nB\tsturgeon\n',
                        'sample\tclassifier\nA\tskip\nB\tskip\n'):
            path.write_text(content)
            with self.subTest(content=content),self.assertRaises(OncoTracerError):assignments.read(path,['A','B'])


if __name__ == '__main__':unittest.main()
