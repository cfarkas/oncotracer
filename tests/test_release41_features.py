"""Mixed tissue routing, all-sample admission and actual cohort-figure contracts."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oncotracer_cli import methylation_routing as routing
from oncotracer_cli import cohort_figure as figures
from oncotracer_cli.glimpse_pipeline import resource_allocation, validate_options
from oncotracer_cli.runtime import OncoTracerError, atomic_write_json, sha256_file
from tests.resource_fixture import ResourceFixtureCase


class RoutingTests(unittest.TestCase):
    def test_explicit_tissues_only(self):
        for tissue in ('Peripheral Blood','bone_marrow','PBMC',' marrow '):
            self.assertEqual(routing.classifier_for_tissue(tissue),'marlin')
        for tissue in ('CNS','brain','central nervous system','CSF'):
            self.assertEqual(routing.classifier_for_tissue(tissue),'sturgeon')
        for tissue in ('','unknown','lymph node','skin','CNS1','AML','ALL','blood clot'):
            self.assertIsNone(routing.classifier_for_tissue(tissue))

    def test_exact_metadata_sample_mapping_and_distinct_bams(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); metadata=root/'samples.tsv'
            for name in ('A','B'):(root/(name+'.bam')).write_bytes(b'validation fixture')
            metadata.write_text('sample\ttissue\tmodbam\nA\tmarrow\tA.bam\nB\tCNS\tB.bam\nC\tother\t\n')
            samples=[SimpleNamespace(sample=s) for s in ('A','B','C')]
            configs=[]
            def resolve(config,**kwargs):
                configs.append(config);return SimpleNamespace(classifier=config['methylation_classifier'],gpu=False)
            with patch('oncotracer_cli.engine.parse_ont_samples',return_value=samples), patch('oncotracer_cli.methylation.resolve_methylation_request',side_effect=resolve):
                result=routing.resolve({'methylation_sample_metadata':str(metadata)},mode='ont')
                self.assertEqual([r.classifier for _,r in result.requests],['marlin','sturgeon'])
                self.assertEqual(result.assignments[2]['status'],'not_applicable')
                self.assertTrue(all(c['methylation_modbam_alignment']=='preserve' and not c['ont_named_samples'] for c in configs))
                metadata.write_text(metadata.read_text().replace('B.bam','A.bam'))
                with self.assertRaisesRegex(OncoTracerError,'multiple'):routing.resolve({'methylation_sample_metadata':str(metadata)},mode='ont')
                metadata.write_text('sample\ttissue\tmodbam\nA\tmarrow\tA.bam\n')
                with self.assertRaisesRegex(OncoTracerError,'exactly one'):routing.resolve({'methylation_sample_metadata':str(metadata)},mode='ont')

    def test_unknown_tissue_is_not_a_failed_classifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);m=root/'metadata';m.write_text('fixture')
            request=routing.TissueMethylationRequest(({'sample':'A','tissue':'skin','classifier':None,'status':'not_applicable'},),(),m,sha256_file(m))
            result=routing.publish(root,request,list(request.assignments))
            self.assertEqual(result['overall_status'],'complete')
            self.assertEqual(result['not_applicable_samples'],['A'])
            self.assertEqual(result['failed_samples'],[])


class SchedulingTests(ResourceFixtureCase):
    def test_all_26_and_11_samples_receive_exactly_one_cpu(self):
        for count,memory in ((26,'416GB'),(11,'176GB')):
            request=SimpleNamespace(glimpse_config=validate_options({'variant_memory':memory,'variant_one_cpu_per_sample':True}))
            allocation=resource_allocation(request,count,count)
            self.assertEqual(allocation['parallel_samples'],count)
            self.assertEqual(allocation['threads_per_sample'],1)
            self.assertEqual(allocation['allocated_threads'],count)
            self.assertLessEqual(8*count+24*allocation['report_memory_slots'],allocation['total_memory_gb'])
            with self.assertRaisesRegex(OncoTracerError,'available CPU'):resource_allocation(request,count-1,count)
        for value in ('true',1,None):
            with self.assertRaises(OncoTracerError):validate_options({'variant_one_cpu_per_sample':value})

    def test_shared_report_memory_is_released_after_sigkill(self):
        module=Path(__file__).parents[1]/'oncotracer_cli/lpwgs/bin/cohort_memory.py'
        script=('import sys,importlib.util,time; s=importlib.util.spec_from_file_location("memory",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
                'lease=m.report_slot({"llm_memory_pool":sys.argv[2],"llm_memory_slots":1});'
                'lease.__enter__();open(sys.argv[3],"w").write("admitted");time.sleep(30)')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);children=[]
            try:
                for index in range(2):
                    children.append(subprocess.Popen([sys.executable,'-c',script,str(module),str(root/'pool'),str(root/str(index))],stdout=subprocess.DEVNULL))
                    deadline=time.monotonic()+5
                    if index==0:
                        while not (root/'0').exists() and time.monotonic()<deadline:time.sleep(.02)
                        self.assertTrue((root/'0').exists())
                time.sleep(.35);self.assertFalse((root/'1').exists())
                children[0].kill();children[0].wait(timeout=5)
                deadline=time.monotonic()+5
                while not (root/'1').exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((root/'1').exists())
            finally:
                for child in children:
                    if child.poll() is None:child.kill()
                    child.wait(timeout=5)


class CohortFigureTests(unittest.TestCase):
    def test_single_sample_never_starts_plotting(self):
        self.assertIsNone(figures.publish(None,{},[SimpleNamespace(sample='A')],None,None,None,None))

    def test_render_preserves_na_zero_depth_and_exploratory_assignments(self):
        import pysam
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fai=root/'reference.fai';fai.write_text(''.join(f'chr{c}\t2000000\t0\t0\t0\n' for c in range(1,23)))
            bam=root/'reads.bam'
            with pysam.AlignmentFile(bam,'wb',header={'HD':{'VN':'1.6'},'SQ':[{'SN':f'chr{c}','LN':2000000} for c in range(1,23)]}) as output:
                for flag,mapq in ((0,20),(1024,60),(0,19),(256,60),(2048,60)):
                    r=pysam.AlignedSegment();r.query_name=f'r{flag}_{mapq}';r.query_sequence='A'*100;r.flag=flag;r.reference_id=0;r.reference_start=100;r.mapping_quality=mapq;r.cigartuples=[(0,100)];output.write(r)
            sheet=root/'bams.csv';sheet.write_text(f'sample,bam\nA,{bam}\nB,{bam}\n')
            summary=root/'06_workflow_summary';summary.mkdir()
            (summary/'cna_assessment.tsv').write_text('sample\tassessment\tbin_size_kb\nA\tassessable\t50\nB\tnot_assessable\t50\n')
            atomic_write_json(summary/'experimental_broad_cna/screen.json',{'samples':[{'sample':'B','status':'experimental_screen_available','regions':[{'region':'1p','status':'candidate_broad_gain','start':1,'end':1000000,'log2_ratio':.6}]}]})
            spec=dict(samples=['A','B'],outdir=str(root),reference_fai=str(fai),bam_samplesheet=str(sheet),title='Synthetic cohort',technology='FFPE')
            path=root/'spec.json';atomic_write_json(path,spec);result=figures.build(path)
            self.assertEqual(result['status'],'complete');self.assertEqual(result['exploratory'],1)
            data=figures.read_table(root/'09_cohort_figure/02_samples.tsv')
            self.assertEqual(data[0]['accepted_events'],'0');self.assertEqual(data[1]['accepted_events'],'')
            self.assertEqual(data[1]['assignments'],'1p gain');self.assertEqual(int(data[0]['aligned_bases']),100)
            self.assertAlmostEqual(float(data[0]['mean_depth_x']),100/44000000)
            self.assertTrue((root/'09_cohort_figure/04_cohort_figure.pdf').read_bytes().startswith(b'%PDF'))

class DockerMethylationBindings(unittest.TestCase):
    def test_modbams_indexes_and_external_classifiers_are_read_only(self):
        from oncotracer_cli.docker_runtime import docker_mounts
        from oncotracer_cli.runtime import render_flat_yaml
        from tests.test_native_methylation import Fixture
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources=root/'resources';resources.mkdir();fixture=Fixture(resources)
            bam=root/'source.bam';bam.write_bytes(b'fixture');Path(str(bam)+'.bai').write_bytes(b'index')
            metadata=root/'tissues.tsv';metadata.write_text('sample\ttissue\tmodbam\nSNC_F\tCNS\tsource.bam\n')
            values=fixture.config();values.update(outdir=str(root/'output'),lpwgs_root=str(root/'project'));values.pop('methylation_pod5_dir');values.update(methylation_classifier='tissue',methylation_sample_metadata=str(metadata))
            config=root/'run.yml';config.write_text(render_flat_yaml(values))
            with patch.object(Path,'cwd',return_value=root):mounts=dict(docker_mounts(config,environment={}))
            for path in (bam,Path(str(bam)+'.bai'),metadata,fixture.assets['model'],fixture.assets['probes'],fixture.bin.parent):
                self.assertEqual(mounts[path],'ro')

    def test_mixed_workers_keep_sample_identity_and_native_receipts(self):
        from oncotracer_cli.runtime import CommandRunner, StageLedger
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);metadata=root/'metadata';metadata.write_text('fixed metadata')
            samples=[SimpleNamespace(sample=s) for s in ('A','B','C')]
            assignments=tuple(dict(sample=s,tissue=t,classifier=c,status='pending' if c else 'not_applicable') for s,t,c in [('A','marrow','marlin'),('B','CNS','sturgeon'),('C','unknown',None)])
            request=routing.TissueMethylationRequest(assignments,(('A',SimpleNamespace(classifier='marlin')),('B',SimpleNamespace(classifier='sturgeon'))),metadata,sha256_file(metadata))
            def run(source,child,selected,reference,output,runner,ledger,**options):
                self.assertEqual(len(selected),1);self.assertEqual(options['threads'],1)
                name=selected[0].sample;path=output/'07_methylation'/('prediction.'+('tsv' if child.classifier=='marlin' else 'csv'));path.parent.mkdir(parents=True)
                path.write_text('fixture score vector')
                return {'samples':[dict(sample=name,status='complete',classification=str(path.relative_to(output)),output_sha256={'classification':sha256_file(path)})]}
            with patch('oncotracer_cli.methylation.run_methylation',side_effect=run), patch('oncotracer_cli.methylation.methylation_plan',side_effect=lambda child: {'classifier':child.classifier}):
                result=routing.run(None,request,samples,None,root,CommandRunner(root/'trace.tsv'),StageLedger(root/'stages.json'),threads=2,force=False)
            self.assertEqual(result['overall_status'],'complete');self.assertEqual(result['completed_samples'],['A','B'])
            for row in result['samples'][:2]:
                self.assertTrue((root/row['classification']).is_file());self.assertIn('/'+row['sample']+'/',row['native_receipt'])
