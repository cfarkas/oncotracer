"""Parallel resume checks retain sample identity and the all-sample barrier."""
import contextlib
import io
import json
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest

from oncotracer_cli import glimpse_pipeline as pipeline
from oncotracer_cli.runtime import CommandRunner, OncoTracerError


class ParallelPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.jobs=[]
        for index in range(5):
            sample='S'+str(index);launch=self.root/'launch'/sample;launch.mkdir(parents=True)
            params={'sample':sample,'threads':2,'memory':'8GB','continue':True,
                    'reuse_from':'/verified/prior/'+sample,'min_mapping_quality':30}
            self.jobs.append((sample,self.root/(sample+'.bam'),self.root/sample,launch,params,{}))

    def runner(self, *, invalid=None, stop=None):
        test=self
        class Runner:
            active=0;peak=0;calls=0
            lock=threading.Lock();events=[];parallel_pair=threading.Event()
            def run(self,stage,command,**kwargs):
                requests=json.load(kwargs['stdin'])['requests']
                samples=[r['parameters']['sample'] for r in requests]
                with self.lock:
                    self.active+=1;self.peak=max(self.peak,self.active);self.calls+=1
                    self.events.append(('start',tuple(samples)))
                    if self.active==2:self.parallel_pair.set()
                try:
                    for request in requests:
                        test.assertTrue(request['parameters']['preflight_only'])
                        test.assertEqual(request['parameters']['threads'],2)
                        test.assertEqual(request['parameters']['min_mapping_quality'],30)
                    if len(samples)==1 and samples[0]!='S0':
                        test.assertTrue(self.parallel_pair.wait(2),'independent validations did not overlap')
                        time.sleep(.03 if samples[0]=='S1' else .01)
                    rows=[{'sample':sample,'run':False,'reuse_validation':{'validated_sample':sample}} for sample in samples]
                    if invalid and invalid[0] in samples:
                        invalid[1](rows[samples.index(invalid[0])])
                    json.dump({'run':False,'samples':rows},kwargs['stdout']);kwargs['stdout'].flush()
                    if stop is not None and samples==['S0']:stop.set()
                finally:
                    with self.lock:self.active-=1;self.events.append(('end',tuple(samples)))
        return Runner()

    def test_bounds_overlap_order_and_barrier(self):
        runner=self.runner();before=[dict(j[4]) for j in self.jobs];output=io.StringIO()
        with contextlib.redirect_stdout(output):
            checks=pipeline.preflight_jobs(self.jobs,runner,self.root,parallel_samples=2)
        self.assertEqual(runner.peak,2)
        self.assertEqual(runner.events[:2],[('start',('S0',)),('end',('S0',))])
        self.assertEqual([r['validated_sample'] for r in checks],['S0','S1','S2','S3','S4'])
        self.assertEqual([j[4] for j in self.jobs],before)
        self.assertEqual([j[5]['reuse_validation'] for j in self.jobs],checks)
        response=json.loads((self.root/'launch/preflight_response.json').read_text())
        self.assertEqual([r['sample'] for r in response['samples']],['S0','S1','S2','S3','S4'])
        self.assertIn('Resume validation 5/5 passed:',output.getvalue())
        self.assertIn('All 5 sample resume checks passed.',output.getvalue())

    def test_serial_mode_keeps_one_interpreter_batch(self):
        runner=self.runner()
        pipeline.preflight_jobs(self.jobs,runner,self.root,parallel_samples=1)
        self.assertEqual(runner.calls,1)
        self.assertEqual(runner.peak,1)
        self.assertEqual(runner.events[0],('start',('S0','S1','S2','S3','S4')))

    def test_wrong_sample_or_incomplete_proof_never_publishes_cohort_success(self):
        for change in (lambda r:r.update(sample='FOREIGN'),lambda r:r.update(run=True),lambda r:r.pop('reuse_validation')):
            with self.subTest(change=change):
                stop=threading.Event();runner=self.runner(invalid=('S1',change))
                (self.root/'launch/preflight_response.json').write_text(json.dumps({'run':False,'samples':[{'sample':'STALE'}]}))
                with self.assertRaises(OncoTracerError):
                    pipeline.preflight_jobs(self.jobs,runner,self.root,parallel_samples=2,cancel=stop)
                self.assertTrue(stop.is_set())
                self.assertFalse(any('reuse_validation' in j[5] for j in self.jobs))
                self.assertFalse((self.root/'launch/preflight_response.json').exists())

    def test_cancellation_after_warmup_admits_no_other_sample(self):
        stop=threading.Event();runner=self.runner(stop=stop)
        with self.assertRaises(pipeline._SampleCancelled):
            pipeline.preflight_jobs(self.jobs,runner,self.root,parallel_samples=2,cancel=stop)
        self.assertEqual(runner.calls,1)
        self.assertFalse(any('reuse_validation' in j[5] for j in self.jobs))

    def test_force_removes_donor_reuse_and_keeps_quality_thresholds(self):
        runner=self.runner()
        pipeline.preflight_jobs(self.jobs,runner,self.root,force=True,parallel_samples=1)
        requests=json.loads((self.root/'launch/preflight_request.json').read_text())['requests']
        for request in requests:
            self.assertIs(request['parameters']['continue'],False)
            self.assertNotIn('reuse_from',request['parameters'])
            self.assertEqual(request['parameters']['min_mapping_quality'],30)

    def test_real_worker_failure_cancels_its_sibling_process_group(self):
        bundle=self.root/'bundle';(bundle/'bin').mkdir(parents=True)
        (bundle/'bin/nextflow_entry.py').write_text("""
import json, pathlib, subprocess, sys, time
sample=json.load(sys.stdin)['requests'][0]['parameters']['sample']
root=pathlib.Path.cwd().parent
if sample=='S1':
    deadline=time.monotonic()+5
    while not (root/'S2/children.json').exists():
        if time.monotonic()>deadline:raise RuntimeError('sibling did not start')
        time.sleep(.02)
    raise SystemExit(31)
if sample=='S2':
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (pathlib.Path.cwd()/'children.json').write_text(json.dumps([child.pid]))
    try:time.sleep(60)
    except KeyboardInterrupt:raise SystemExit(130)
json.dump({'run':False,'samples':[{'sample':sample,'run':False,'reuse_validation':{'sample':sample}}]},sys.stdout)
""")
        for job in self.jobs:job[4]['python']=sys.executable
        runner=CommandRunner(self.root/'parent.tsv',echo=False)
        stop=threading.Event();start=time.monotonic()
        with self.assertRaisesRegex(OncoTracerError,'exit code 31'):
            pipeline.preflight_jobs(self.jobs,runner,bundle,parallel_samples=2,cancel=stop)
        self.assertLess(time.monotonic()-start,12)
        self.assertTrue(stop.is_set())
        self.assertFalse(any('reuse_validation' in job[5] for job in self.jobs))
        self.assertFalse((self.root/'launch/preflight_response.json').exists())
        self.assertFalse((self.jobs[3][3]/'preflight_commands.tsv').exists())
        for job in self.jobs[:3]:
            self.assertIn('glimpse-resume-preflight-'+job[0],(job[3]/'preflight_commands.tsv').read_text())
        for pid in json.loads((self.jobs[2][3]/'children.json').read_text()):
            stat=Path('/proc')/str(pid)/'stat'
            if stat.exists():self.assertEqual(stat.read_text().rsplit(')',1)[1].split()[0],'Z','Cancelled worker left a live child')

    def test_invalid_concurrency_fails_before_request_files(self):
        for value in (0,-1,True,'auto'):
            with self.subTest(value=value),self.assertRaises(OncoTracerError):
                pipeline.preflight_jobs(self.jobs,self.runner(),self.root,parallel_samples=value)
        self.assertFalse((self.root/'launch/preflight_request.json').exists())


if __name__=='__main__':unittest.main()
