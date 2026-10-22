"""Bounded sample scheduling, budget partition and real child cancellation."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from tests.resource_fixture import ResourceFixtureCase
from unittest.mock import patch
from oncotracer_cli import glimpse_pipeline as pipeline
from oncotracer_cli.runtime import CommandRunner, OncoTracerError
from oncotracer_cli.variants import resolve_variant_request


class ParallelSamplesTests(ResourceFixtureCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.reference=self.root/'ref.fa';self.reference.write_text('>chr1\nAAAA\n')
        self.request=resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh',
            'variant_parallel_samples':2,'variant_memory':'64GB'},mode='illumina')
        self.bams={sample:self.root/(sample+'.bam') for sample in ('SAMPLE_A','SAMPLE_B','SAMPLE_C')}
        for path in self.bams.values():path.write_bytes(b'fixture')

    def run_pipeline(self,runner,**kwargs):
        with patch.object(pipeline,'preflight',return_value={'nextflow':'nextflow','bcftools':'bcftools'}), \
             patch.object(pipeline,'preflight_jobs',return_value=[]), \
             patch.object(pipeline,'ensure_resources') as bootstrap, \
             patch('oncotracer_cli.glimpse_joint.merge_cohort',return_value={'outputs':{}}) as cohort:
            result=pipeline.run(self.request,self.bams,self.reference,self.root/'output',runner,None,threads=8,**kwargs)
        return result,bootstrap,cohort

    @staticmethod
    def complete(command):
        params=json.loads(Path(command[command.index('-params-file')+1]).read_text())
        root=Path(params['output']);sample=params['sample']
        variant=root/'cohort_vcf'/f'{sample}.cohort_vcf'/f'{sample}.annotated.vcf.gz'
        variant.parent.mkdir(parents=True,exist_ok=True);variant.write_bytes(b'VCF fixture')
        chromosome=root/'imputation'/f'{sample}.chr1.imputed.bcf'
        chromosome.parent.mkdir(parents=True,exist_ok=True);chromosome.write_bytes(b'BCF fixture')
        return params

    def test_default_validation_and_total_budget(self):
        self.assertEqual(pipeline.validate_options({})['parallel_samples'],'auto')
        for value in (False,True,0,-1,1.5,'2.0','zero',None):
            with self.subTest(value=value),self.assertRaises(OncoTracerError):
                pipeline.resource_allocation(replace(self.request,glimpse_config={'parallel_samples':value}),8,3)
        for memory in ('7.9GB','31GB','nan','inf','-8GB',{},True,'1MB'):
            with self.subTest(memory=memory),self.assertRaises(OncoTracerError):
                pipeline.validate_options({'variant_memory':memory})
        request=replace(self.request,glimpse_config={'parallel_samples':4,'memory':'256GB'})
        allocation=pipeline.resource_allocation(request,64,10)
        self.assertEqual((allocation['parallel_samples'],allocation['threads_per_sample'],allocation['memory_gb_per_sample']),(4,16,64))
        limited=pipeline.resource_allocation(replace(request,glimpse_config={'parallel_samples':9,'memory':'80GB'}),7,10)
        self.assertEqual(limited['parallel_samples'],9)
        self.assertEqual(limited['allocated_threads'],9)
        self.assertEqual(len(limited['warnings']),2)
        self.assertLessEqual(limited['allocated_memory_gb'],80)
        self.assertEqual(pipeline.resource_allocation(request,2,10)['parallel_samples'],4)
        self.assertEqual(pipeline.resource_allocation(request,64,1)['parallel_samples'],1)

    def test_manual_variant_jobs_overlap_beyond_recommended_memory_fit(self):
        self.request=replace(self.request,glimpse_config={'parallel_samples':3,'memory':'32GB'})
        owner=self;barrier=threading.Barrier(3);seen=[];lock=threading.Lock()
        class Runner:
            def run(self,stage,command,**kwargs):
                with lock:seen.append(stage)
                barrier.wait(timeout=3)
                params=owner.complete(command)
                owner.assertEqual(params['threads'],2)
                owner.assertEqual(params['memory'],'10922MB')
        result,_,cohort=self.run_pipeline(Runner())
        self.assertEqual(len(seen),3)
        self.assertEqual(result['overall_status'],'complete')
        self.assertEqual(result['resource_allocation']['parallel_samples'],3)
        self.assertTrue(result['resource_allocation']['warnings'])
        cohort.assert_called_once()

    def test_overlap_respects_limit_all_statuses_visible_and_merge_after_all(self):
        owner=self;active=0;peak=0;lock=threading.Lock();barrier=threading.Barrier(2);calls=[]
        class Runner:
            def run(self,stage,command,**kwargs):
                nonlocal active,peak
                with lock:active+=1;peak=max(peak,active);calls.append(stage)
                snapshot=json.loads((owner.root/'output/08_variants/variant_status.json').read_text())
                self_count=len(snapshot['samples'])
                if self_count!=3:raise AssertionError('Queued samples absent from status')
                if len(calls)<=2:barrier.wait(timeout=2)
                time.sleep(.05)
                params=owner.complete(command)
                if params['threads']!=4 or params['memory']!='32768MB':raise AssertionError('Per-sample allocation escaped total budget')
                with lock:active-=1
        result,bootstrap,cohort=self.run_pipeline(Runner())
        self.assertEqual(peak,2);self.assertEqual(len(calls),3)
        self.assertEqual(result['overall_status'],'complete')
        self.assertEqual(set(result['completed_samples']),set(self.bams))
        bootstrap.assert_called_once();cohort.assert_called_once()
        self.assertEqual(len(cohort.call_args.args[0]),3)
        self.assertEqual(cohort.call_args.kwargs['threads'],8)
        self.assertIn('Muestras simultáneas: 2',(self.root/'output/08_variants/glimpse2/START_HERE.html').read_text())

    def test_one_failure_keeps_other_samples_and_suppresses_partial_cohort(self):
        owner=self
        class Runner:
            def run(self,stage,command,**kwargs):
                if stage.endswith('SAMPLE_B'):raise OncoTracerError('fixture failure')
                owner.complete(command)
        result,_,cohort=self.run_pipeline(Runner())
        self.assertEqual(result['overall_status'],'partial_failure')
        self.assertEqual(result['failed_samples'],['SAMPLE_B'])
        self.assertEqual(len(result['completed_samples']),2);cohort.assert_not_called()

    def test_sigterm_prevents_queued_launches_and_restores_signal_handlers(self):
        owner=self;barrier=threading.Barrier(2);launched=[];lock=threading.Lock()
        before=signal.getsignal(signal.SIGTERM)
        class Runner:
            def run(self,stage,command,**kwargs):
                with lock:launched.append(stage)
                barrier.wait(timeout=2)
                if stage.endswith('SAMPLE_A'):os.kill(os.getpid(),signal.SIGTERM)
                time.sleep(.1);owner.complete(command)
        result,_,cohort=self.run_pipeline(Runner())
        self.assertEqual(signal.getsignal(signal.SIGTERM),before)
        self.assertEqual(result['overall_status'],'interrupted')
        self.assertEqual(len(launched),2)
        self.assertEqual(result['samples'][2]['status'],'not_started')
        self.assertEqual(len(result['interrupted_samples']),2);cohort.assert_not_called()

    def test_bootstrap_failure_is_failed_without_launches(self):
        with patch.object(pipeline,'preflight',return_value={'nextflow':'nextflow'}), \
             patch.object(pipeline,'ensure_resources',side_effect=OncoTracerError('resource failed')):
            result=pipeline.run(self.request,self.bams,self.reference,self.root/'output',None,None,threads=8)
        self.assertEqual(result['overall_status'],'failed')
        self.assertTrue(all(row['status']=='not_started' for row in result['samples']))
        self.assertEqual(json.loads(Path(result['status_file']).read_text())['overall_status'],'failed')

    def test_same_sample_reuse_mapping_never_imports_another_genotype(self):
        donor=self.root/'donor/08_variants/glimpse2/samples/SAMPLE_A'
        (donor/'pipeline_info').mkdir(parents=True)
        (donor/'pipeline_info/runtime.json').write_text(json.dumps({'sample':'SAMPLE_A'}))
        request=replace(self.request,glimpse_config={'analysis_reuse_from':str(self.root/'donor')})
        for sample in ('SAMPLE_A','SAMPLE_B'):
            params,_=pipeline.sample_parameters(request,sample,self.bams[sample],self.reference,self.root/sample,self.root/'resources',4)
            if sample=='SAMPLE_A':self.assertEqual(params['reuse_from'],str(donor))
            else:self.assertNotIn('reuse_from',params)
        self.assertNotIn('parallel_samples',params)
        self.assertNotIn('analysis_reuse_from',params)

    def test_independent_runner_environment_and_real_orphan_child_cancellation(self):
        trace=self.root/'parent.tsv'
        parent=CommandRunner(trace,echo=False,environment={'TEST_SHARED':'original'},protected_environment={'TEST_PROTECTED':'yes'})
        stop=threading.Event();runner=pipeline._sample_runner(parent,self.root/'sample.tsv',stop)
        runner.environment['TEST_SHARED']='sample'
        self.assertEqual(parent.environment['TEST_SHARED'],'original')
        self.assertEqual(runner.child_environment()['TEST_PROTECTED'],'yes')
        ids=self.root/'child.json'
        code=('import json,os,subprocess,sys,time;'
              'child=subprocess.Popen([sys.executable,"-c","import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)"]);'
              'open(sys.argv[1],"w").write(json.dumps({"child":child.pid,"leader":os.getpid()}));time.sleep(120)')
        errors=[]
        def invoke():
            try:
                with (self.root/'children.log').open('w') as log:
                    runner.run('cancel-fixture',[sys.executable,'-c',code,ids],stdout=log,stderr=log)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=invoke);thread.start()
        deadline=time.monotonic()+5
        while not ids.is_file() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(ids.is_file());processes=json.loads(ids.read_text())
        time.sleep(.1);stop.set();thread.join(timeout=7)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],pipeline._SampleCancelled)
        for pid in processes.values():
            stat=Path(f'/proc/{pid}/stat')
            deadline=time.monotonic()+2
            while stat.exists() and stat.read_text().split()[2]!='Z' and time.monotonic()<deadline:time.sleep(.02)
            if stat.exists():self.assertEqual(stat.read_text().split()[2],'Z','Child survived cancellation')
        self.assertIn('cancel-fixture',(self.root/'sample.tsv').read_text())
        self.assertNotIn('cancel-fixture',trace.read_text())

    def test_ffpe_resource_child_roundtrips_paths_and_request_without_download(self):
        # Fresh specimen with no pending resources exercises the real subprocess
        # transport without a model download or changing any scientific decision.
        request=replace(self.request,ffperase_root=self.root/'tools with spaces')
        runner=CommandRunner(self.root/'controller.tsv',echo=False)
        result=pipeline._prepare_ffpe_resources(request,self.root,runner,threading.Event())
        self.assertEqual(result.ffperase_root,request.ffperase_root)
        self.assertEqual(result.callers,request.callers)
        self.assertEqual(result.glimpse_config,request.glimpse_config)

    def test_unexpected_worker_error_is_failed_and_interrupts_other_work(self):
        class Runner:
            def run(self,*args,**kwargs):raise KeyError('unexpected fixture')
        result,_,cohort=self.run_pipeline(Runner())
        self.assertEqual(result['overall_status'],'failed')
        self.assertFalse(any(row['status']=='running' for row in result['samples']))
        cohort.assert_not_called()


if __name__=='__main__':unittest.main()
