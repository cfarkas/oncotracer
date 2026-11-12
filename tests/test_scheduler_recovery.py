"""Regression coverage for stale process IDs, worker death, and cohort cancellation."""
import json,os,shlex,shutil,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from oncotracer_cli.resource_pool import DynamicResources,claim_worker,process_identity,supervise_parallel
from oncotracer_cli.sample_execution import run_jobs
from oncotracer_cli.runtime import CommandRunner,OncoTracerError
from tests.test_dynamic_resources import allocation,hardware

class SchedulerRecoveryTests(unittest.TestCase):
    def test_reused_pid_is_reaped_as_a_lease_without_signalling_live_process(self):
        with tempfile.TemporaryDirectory() as d:
            pool=DynamicResources(d,['a'],allocation(cpu=1,slots=1),hardware=lambda:hardware()).start()
            sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
            try:
                pool.update('a','running');path=pool.sample_directory('a')
                stale=path/'worker-reused.json';stale.write_text(json.dumps({'pid':sentinel.pid,'start':'old-process-start'}))
                lease=claim_worker(path)
                self.assertIsNotNone(lease);self.assertFalse(stale.exists());self.assertIsNone(sentinel.poll())
                self.assertIsNone(claim_worker(path))
            finally:pool.close();sentinel.terminate();sentinel.wait(timeout=5)

    def test_alive_but_workerless_runner_fails_and_is_reaped(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pool=DynamicResources(root,['a'],allocation(cpu=1,slots=1),hardware=lambda:hardware()).start()
            try:
                pool.update('a','running');pidfile=root/'runner.pid'
                script='from pathlib import Path;import os,sys,time;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)'
                with self.assertRaisesRegex(OncoTracerError,'stalled with no worker'):
                    supervise_parallel([sys.executable,'-c',script,str(pidfile)],pool.sample_directory('a'),idle_timeout=.3,poll_interval=.02)
                self.assertIsNone(process_identity(int(pidfile.read_text())))
            finally:pool.close()

    def test_reduced_budget_waits_for_running_leases_to_finish(self):
        with tempfile.TemporaryDirectory() as d:
            pool=DynamicResources(d,['a'],allocation(cpu=2,slots=1),hardware=lambda:hardware()).start()
            children=[subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']) for _ in range(2)]
            try:
                pool.update('a','running');path=pool.sample_directory('a')
                for i,p in enumerate(children):(path/f'worker-{i}.json').write_text(json.dumps({'pid':p.pid,'start':process_identity(p.pid)}))
                pool.hardware=lambda:hardware(cpu=1);pool.refresh()
                self.assertIsNone(claim_worker(path))
                children[0].terminate();children[0].wait(timeout=5)
                self.assertIsNone(claim_worker(path))
                children[1].terminate();children[1].wait(timeout=5)
                lease=claim_worker(path);self.assertIsNotNone(lease)
            finally:
                pool.close()
                for p in children:
                    if p.poll() is None:p.terminate()
                    p.wait(timeout=5)

    @unittest.skipUnless(shutil.which('parallel'),'GNU Parallel required')
    def test_killed_worker_has_bounded_retries_and_no_jobs_file_refresh(self):
        with tempfile.TemporaryDirectory(prefix='worker death ') as d:
            root=Path(d);events=root/'events';worker=root/'worker.py'
            worker.write_text("import os,sys,signal,time\nfrom pathlib import Path\nfd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)\nos.write(fd,(sys.argv[2]+'\\n').encode());os.close(fd)\nif sys.argv[2]=='bad':os.kill(os.getpid(),signal.SIGKILL)\ntime.sleep(.05)\n")
            pool=DynamicResources(root,['a'],allocation(cpu=2,slots=1),interval=.02,hardware=lambda:hardware()).start();process=None
            try:
                pool.update('a','running');inputs=root/'inputs';inputs.write_text('good1\nbad\ngood2\ngood3\n')
                argv=[str(pool.parallel_wrapper('a')),'--retries','2','--joblog',str(root/'joblog'),'-j','1',shlex.join([sys.executable,str(worker),str(events)])+' {}','::::',str(inputs)]
                process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                out,err=process.communicate(timeout=15)
                self.assertNotEqual(process.returncode,0)
                calls=events.read_text().splitlines()
                self.assertEqual(calls.count('bad'),2)
                for name in ['good1','good2','good3']:self.assertEqual(calls.count(name),1)
                self.assertFalse(list(pool.sample_directory('a').glob('*.jobs')))
                rows=[line.split('\t',8) for line in (root/'joblog').read_text().splitlines()[1:]]
                killed=[row for row in rows if row[-1].endswith(' bad')]
                self.assertEqual(len(killed),1)
                # Shells can encode SIGKILL as exit 137 or a separate signal 9.
                self.assertEqual(killed[0][6],'137')
                self.assertIn(killed[0][7],{'0','9'})
                self.assertEqual(sum(row[6:8]==['0','0'] for row in rows),3)
            finally:
                pool.close()
                if process is not None and process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=5)

    def test_returned_failure_cancels_sibling_and_leaves_queued_work_unstarted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);marker=root/'started';started=[];states=[]
            parent=CommandRunner(root/'trace.tsv',echo=False)
            def execute(name,runner):
                started.append(name)
                if name=='bad':
                    deadline=time.monotonic()+5
                    while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
                    if not marker.exists():raise AssertionError('Sibling did not start')
                    return {'status':'failed','error':'injected worker failure'}
                runner.run('sibling',[sys.executable,'-c','from pathlib import Path;import sys,time;Path(sys.argv[1]).write_text("started");time.sleep(30)',str(marker)])
                return {'status':'complete'}
            start=time.monotonic()
            with self.assertRaisesRegex(OncoTracerError,'injected worker failure'):
                run_jobs([(n,n) for n in ['bad','slow','queued']],execute,allocation(cpu=2,slots=2),parent,root/'traces',update=lambda n,s,r:states.append((n,s)),stop_on_failure=True)
            self.assertLess(time.monotonic()-start,8);self.assertNotIn('queued',started)
            self.assertIn(('bad','finished'),states)

if __name__=='__main__':unittest.main()
