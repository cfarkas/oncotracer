"""Dynamic chunk scheduling must reclaim idle capacity without changing results."""
from pathlib import Path
import csv,json,os,shlex,shutil,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from oncotracer_cli.resource_pool import DynamicResources,bounds,safe_allocation,read_budget
from oncotracer_cli.resource_options import validate_mode
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.quilt_parallel import bounded_map


def allocation(cpu=4,slots=4,memory=64):
    return dict(total_threads=cpu,parallel_samples=slots,total_memory_gb=memory,
        threads_per_sample=max(1,cpu//slots),memory_gb_per_sample=memory/slots)

def hardware(cpu=4,memory=80):return dict(cpu_workers_available=cpu,ram_available_bytes=int(memory*1024**3))

class ResourceBoundsTests(unittest.TestCase):
    def test_finished_samples_return_capacity_and_limits_are_live(self):
        a=allocation();names=['a','b','c','d']
        self.assertEqual({x['worker_limit'] for x in bounds(a,4,names,hardware()).values()},{1})
        self.assertEqual(bounds(a,1,['a'],hardware())['a']['worker_limit'],4)
        self.assertEqual(bounds(a,1,['a'],hardware(cpu=2))['a']['worker_limit'],2)
        self.assertEqual(bounds(a,1,['a'],hardware(memory=5))['a']['worker_limit'],0)
        self.assertLessEqual(sum(x['worker_limit'] for x in bounds(a,4,names,hardware(cpu=2)).values()),2)
        self.assertEqual(safe_allocation(allocation(cpu=2,slots=8))['parallel_samples'],2)
        with self.assertRaises(OncoTracerError):safe_allocation(allocation(memory=4))

    def test_mode_and_scientific_settings_do_not_change_checkpoint_identity(self):
        from dataclasses import replace
        from oncotracer_cli.variants import VariantRequest
        self.assertEqual(validate_mode(None),'dynamic')
        self.assertEqual(validate_mode('fixed'),'fixed')
        for value in ['auto',1,False,[],{}]:
            with self.assertRaises(OncoTracerError):validate_mode(value)
        request=VariantRequest(mode='ont',specimen_type='fresh',callers=('clair3',))
        self.assertEqual(request.as_dict(include_runtime_diagnostics=False),replace(request,resource_mode='fixed').as_dict(include_runtime_diagnostics=False))

    def test_controller_stops_admission_and_stale_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=DynamicResources(folder,['a'],allocation(slots=1),hardware=lambda:hardware()).start()
            try:
                pool.update('a','running');path=pool.sample_directory('a')
                self.assertEqual(read_budget(path)['worker_limit'],4)
                p=path/'budget.json';record=json.loads(p.read_text());record['owner']['start']='wrong';p.write_text(json.dumps(record))
                with self.assertRaises(OncoTracerError):read_budget(path)
            finally:pool.close()
            with self.assertRaises(OncoTracerError):read_budget(path)

    def test_bounded_map_adapts_and_stops_new_work_after_failure(self):
        stop=threading.Event();active=peak=0;lock=threading.Lock();started=threading.Event();release=threading.Event();limit=[1]
        def work(i):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            started.set();release.wait(3);time.sleep(.03)
            with lock:active-=1
            return i*i
        with patch('oncotracer_cli.quilt_parallel.read_budget',side_effect=lambda _:dict(worker_limit=limit[0])):
            answer=[];thread=threading.Thread(target=lambda:answer.extend(bounded_map(work,range(12),'unused',4,stop)));thread.start()
            self.assertTrue(started.wait(2));self.assertEqual(peak,1);limit[0]=4
            time.sleep(.3);release.set();thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(sorted(answer),[i*i for i in range(12)]);self.assertEqual(peak,4)
        stop.clear();calls=[]
        def fail(i):calls.append(i);raise RuntimeError('fixture failure')
        with patch('oncotracer_cli.quilt_parallel.read_budget',return_value=dict(worker_limit=1)),self.assertRaisesRegex(RuntimeError,'fixture failure'):
            bounded_map(fail,range(20),'unused',4,stop)
        self.assertTrue(stop.is_set());self.assertEqual(calls,[0])

    @unittest.skipUnless(shutil.which('parallel'),'GNU Parallel required')
    def test_real_parallel_grows_after_other_samples_finish_without_replay(self):
        with tempfile.TemporaryDirectory(prefix='dynamic pool ') as folder:
            root=Path(folder);events=root/'events';worker=root/'worker.py'
            worker.write_text("import os,sys,time,json\nfrom pathlib import Path\np=Path(sys.argv[1]);n=sys.argv[2]\nfd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)\nos.write(fd,(json.dumps(['start',n,time.time()])+'\\n').encode());time.sleep(.25)\nos.write(fd,(json.dumps(['end',n,time.time()])+'\\n').encode());os.close(fd)\n")
            pool=DynamicResources(root,['a','b','c','d'],allocation(),interval=.02,hardware=lambda:hardware()).start()
            try:
                for sample in ['a','b','c','d']:pool.update(sample,'running')
                wrapper=pool.parallel_wrapper('a');inputs=root/'inputs';inputs.write_text(''.join(str(i)+'\n' for i in range(16)))
                argv=[str(wrapper),'--joblog',str(root/'jobs'),'-j','1',shlex.join([sys.executable,str(worker),str(events)])+' {}','::::',str(inputs)]
                process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                deadline=time.monotonic()+10
                while not events.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.03)
                self.assertTrue(events.exists())
                for sample in ['b','c','d']:pool.update(sample,'finished')
                stdout,stderr=process.communicate(timeout=20);self.assertEqual(process.returncode,0,stderr)
                running=peak=0;started=[]
                for kind,n,stamp in map(json.loads,events.read_text().splitlines()):
                    if kind=='start':running+=1;started.append(n);peak=max(peak,running)
                    else:running-=1
                self.assertEqual(len(started),16);self.assertEqual(len(set(started)),16);self.assertEqual(running,0);self.assertEqual(peak,4)
                self.assertEqual(len(list(csv.DictReader((root/'jobs').read_text().splitlines(),delimiter='\t'))),16)
            finally:pool.close()

if __name__=='__main__':unittest.main()
