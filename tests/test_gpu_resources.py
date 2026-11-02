"""GPU reservations, visibility, cancellation and user resource choices."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import types
import sys
import unittest
from unittest.mock import patch

from oncotracer_cli.lpwgs.bin import gpu_resources as gpu
from oncotracer_cli import gpu_runtime
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.sample_resources import selected_config, worker_environment


DEVICE = dict(index=0, uuid='GPU-test-123', name='Test GPU', total_mb=24576,
              free_mb=23000, reserve_mb=4916, budget_mb=18084)


class GPUResourcesTests(unittest.TestCase):
    def test_gpu_run_dispatch_does_not_enable_methylation(self):
        from oncotracer_cli import cli, docker_runtime
        from oncotracer_cli.methylation import resolve_methylation_request
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'run.yml'
            for mode in ('ont','illumina'):
                for flags,saved in ((['--gpu'],False),([],True),(['--cpu'],True)):
                    with self.subTest(mode=mode,flags=flags,saved_gpu=saved):
                        config.write_text(f'mode: {mode}\ngpu_usage: {str(saved).lower()}\n')
                        args=cli.build_parser().parse_args(['run','--config',str(config),
                            '--backend','docker','--dry-run',*flags])
                        self.assertFalse(cli._methylation_requested(config,args))
                        self.assertIsNone(resolve_methylation_request({'gpu_usage':saved},
                            mode=mode,gpu_override=args.gpu))
                        with patch.object(docker_runtime,'output_runtime_identity',return_value={}), \
                             patch.object(docker_runtime,'validate_docker_variants'), \
                             patch.object(cli,'_prepare_configured_hg38'), \
                             patch.object(cli,'_run_host',return_value=Path(directory)/'results'), \
                             patch.object(cli,'_run_docker') as launch:
                            cli.execute_run(config,args)
                            launch.assert_called_once_with(config,args)
            args=cli.build_parser().parse_args(['run','--config',str(config),
                '--backend','docker','--dry-run','--gpu','--methylation'])
            with self.assertRaisesRegex(OncoTracerError,'optional methylation branch'):
                cli.execute_run(config,args)

    def test_doctor_inside_docker_uses_image_prefixes_and_bounded_probes(self):
        def probe(command, **kwargs):
            self.assertEqual(command[1:4], ['-I', '-B', '-c'])
            self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'], DEVICE['uuid'])
            if '/clair3/' in command[0]:
                self.assertIn('memory_limit=1024', command[4])
                self.assertEqual(kwargs['env']['LD_LIBRARY_PATH'], '/opt/micromamba/envs/clairs-to/lib')
            else:
                self.assertIn('set_per_process_memory_fraction', command[4])
            return subprocess.CompletedProcess(command, 0, json.dumps({'success': True}), '')
        with patch.dict(os.environ, {'ONCOTRACER_CONTAINER_RUNTIME': 'docker'}), \
             patch.object(gpu_runtime, 'lease', return_value=contextlib.nullcontext(DEVICE)), \
             patch('subprocess.run', side_effect=probe) as calls:
            result = gpu_runtime.doctor_gpu('host', {})
        self.assertTrue(result['success'], result)
        self.assertEqual(calls.call_count, 2)

    def inventory(self, text, env=None, code=0):
        with patch.dict(os.environ, env or {}, clear=True), \
             patch.object(gpu.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
             patch.object(gpu.subprocess, 'run', return_value=subprocess.CompletedProcess([],code,text,'')):
            return gpu.inventory()

    def test_inventory_reserves_memory_on_top_of_current_use(self):
        devices=self.inventory('0, GPU-test-123, Test GPU, 24576, 23000\n')
        self.assertEqual(devices,[DEVICE])
        self.assertGreaterEqual(devices[0]['reserve_mb'],4096)

    def test_visibility_masks_and_uuid_prefix_are_respected(self):
        text='0, GPU-first-1, First, 24576, 23000\n1, GPU-second-2, Second, 16384, 14000\n'
        self.assertEqual(self.inventory(text,{'CUDA_VISIBLE_DEVICES':''}),[])
        self.assertEqual(self.inventory(text,{'NVIDIA_VISIBLE_DEVICES':'void'}),[])
        result=self.inventory(text,{'CUDA_VISIBLE_DEVICES':'GPU-second','NVIDIA_VISIBLE_DEVICES':'1'})
        self.assertEqual([d['uuid'] for d in result],['GPU-second-2'])

    def test_failed_or_malformed_probe_does_not_offer_gpu(self):
        self.assertEqual(self.inventory('0,GPU-a,A,20,30\n'),[])
        self.assertEqual(self.inventory('0,GPU-a,A,N/A,N/A\n'),[])
        self.assertEqual(self.inventory('error',code=1),[])

    def test_cdi_void_does_not_hide_explicitly_granted_container_gpu(self):
        env={'ONCOTRACER_CONTAINER_RUNTIME':'docker','NVIDIA_VISIBLE_DEVICES':'void',
             'ONCOTRACER_GPU_DEVICES':'GPU-second-2'}
        text='0, GPU-first-1, First, 24576, 23000\n1, GPU-second-2, Second, 16384, 14000\n'
        self.assertEqual([d['uuid'] for d in self.inventory(text,env)],['GPU-second-2'])
        self.assertEqual(self.inventory(text,{**env,'CUDA_VISIBLE_DEVICES':''}),[])

    @contextlib.contextmanager
    def reservations(self):
        with tempfile.TemporaryDirectory() as name, \
             patch.dict(os.environ,{'ONCOTRACER_GPU_LOCK_DIR':name}), \
             patch.object(gpu,'inventory',return_value=[dict(DEVICE)]), \
             contextlib.redirect_stderr(io.StringIO()):
            yield Path(name)

    def test_projects_serialize_on_the_same_gpu_and_release_on_failure(self):
        with self.reservations():
            entered=threading.Event(); acquired=threading.Event(); errors=[]
            def second():
                try:
                    entered.set()
                    with gpu.lease('project two',poll_seconds=.01,timeout=2):
                        acquired.set()
                except BaseException as error:errors.append(error)
            with gpu.lease('project one'):
                worker=threading.Thread(target=second);worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(acquired.wait(.05))
            worker.join(2)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertTrue(acquired.is_set())
            with self.assertRaises(ValueError):
                with gpu.lease('failure'):raise ValueError('simulated caller failure')
            with gpu.lease('recovery',timeout=.1):pass

    def test_busy_gpu_can_be_cancelled_without_starting_a_worker(self):
        with self.reservations():
            stop=threading.Event();stop.set()
            with self.assertRaises(InterruptedError):
                with gpu.lease('cancelled',cancel=stop):self.fail('started GPU work')

    def test_free_memory_is_rechecked_under_lock(self):
        with self.reservations(), patch.object(gpu,'inventory',side_effect=[
            [dict(DEVICE)], [dict(DEVICE)], [{**DEVICE,'budget_mb':0}]]):
            with self.assertRaisesRegex(RuntimeError,'Timed out'):
                with gpu.lease('other application grew',timeout=0):self.fail('overcommitted GPU')

    def test_waiting_more_than_24_hours_does_not_fail_normal_analysis(self):
        real_time=time.monotonic;offset=[0];entered=threading.Event();errors=[];admitted=[]
        clock=types.SimpleNamespace(monotonic=lambda:real_time()+offset[0],sleep=time.sleep)
        original=gpu._ticket
        @contextlib.contextmanager
        def ticket(*args):
            with original(*args) as path:
                if args[1]=='long wait':entered.set()
                yield path
        with self.reservations(),patch.object(gpu,'time',clock),patch.object(gpu,'_ticket',ticket):
            def waiter():
                try:
                    with gpu.lease('long wait',poll_seconds=.01):admitted.append(True)
                except BaseException as error:errors.append(error)
            with gpu.lease('active caller'):
                worker=threading.Thread(target=waiter);worker.start()
                self.assertTrue(entered.wait(2));offset[0]=2*86400
                time.sleep(.06)
                self.assertTrue(worker.is_alive());self.assertFalse(errors);self.assertFalse(admitted)
            worker.join(2)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertEqual(admitted,[True])

    def test_fifo_prevents_later_samples_from_overtaking(self):
        order=[];errors=[];ready={n:threading.Event() for n in ('first','second','third')}
        original=gpu._ticket
        @contextlib.contextmanager
        def ticket(*args):
            with original(*args) as path:
                if args[1] in ready:ready[args[1]].set()
                yield path
        with self.reservations() as directory,patch.object(gpu,'_ticket',ticket):
            def waiter(label):
                try:
                    with gpu.lease(label,poll_seconds=.005,timeout=3):
                        order.append(label);time.sleep(.02)
                except BaseException as error:errors.append(error)
            threads=[]
            with gpu.lease('active caller'):
                for name in ready:
                    t=threading.Thread(target=waiter,args=(name,));threads.append(t);t.start()
                    self.assertTrue(ready[name].wait(2))
            for t in threads:t.join(4)
            self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
            self.assertEqual(order,list(ready));self.assertEqual(list(directory.glob('wait-*.json')),[])

    def test_default_gpu_queue_deadline_is_120_hours(self):
        real_time=time.monotonic;offset=[0];entered=threading.Event();errors=[]
        clock=types.SimpleNamespace(monotonic=lambda:real_time()+offset[0],sleep=time.sleep)
        original=gpu._ticket
        @contextlib.contextmanager
        def ticket(*args):
            with original(*args) as path:
                if args[1]=='deadline':entered.set()
                yield path
        with self.reservations() as directory,patch.object(gpu,'time',clock),patch.object(gpu,'_ticket',ticket):
            def waiter():
                try:
                    with gpu.lease('deadline',poll_seconds=.005):self.fail('admitted twice')
                except BaseException as error:errors.append(error)
            with gpu.lease('active caller'):
                worker=threading.Thread(target=waiter);worker.start();self.assertTrue(entered.wait(2))
                offset[0]=119*3600;time.sleep(.03)
                self.assertTrue(worker.is_alive());self.assertEqual(errors,[])
                offset[0]=121*3600;worker.join(2)
                self.assertFalse(worker.is_alive());self.assertEqual(len(errors),1)
                self.assertIsInstance(errors[0],RuntimeError);self.assertIn('Timed out',str(errors[0]))
            self.assertEqual(list(directory.glob('wait-*.json')),[])

    def test_cancelled_and_expired_requests_do_not_block_the_queue(self):
        with self.reservations() as directory:
            with gpu.lease('active caller'):
                with self.assertRaisesRegex(RuntimeError,'Timed out'):
                    with gpu.lease('diagnostic',timeout=.02,poll_seconds=.005):self.fail('admitted twice')
                stop=threading.Event();threading.Timer(.02,stop.set).start()
                with self.assertRaises(InterruptedError):
                    with gpu.lease('cancel',cancel=stop,poll_seconds=.005):self.fail('admitted twice')
            self.assertEqual(list(directory.glob('wait-*.json')),[])
            with gpu.lease('recovery',timeout=.1):pass

    def test_dead_process_ticket_is_reclaimed_without_pid_namespace_assumptions(self):
        with self.reservations() as directory:
            code=('import runpy,sys,time;g=runpy.run_path(sys.argv[1]);'
                  'd=g["lock_directory"]();c=g["_ticket"](d,"crashed",[__import__("json").loads(sys.argv[2])],8192);'
                  'p=c.__enter__();print(p,flush=True);time.sleep(60)')
            child=subprocess.Popen([sys.executable,'-B','-c',code,gpu.__file__,json.dumps(DEVICE)],stdout=subprocess.PIPE,text=True)
            try:
                path=Path(child.stdout.readline().strip());self.assertTrue(path.is_file())
                child.kill();child.wait(timeout=5)
                with gpu.lease('after crash',timeout=.2):pass
                self.assertFalse(path.exists());self.assertEqual(list(directory.glob('wait-*.json')),[])
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=5)
                child.stdout.close()

    def test_incompatible_older_waiter_does_not_block_another_gpu(self):
        small={**DEVICE,'uuid':'GPU-small','total_mb':12288,'reserve_mb':4096,'budget_mb':8192}
        with self.reservations() as directory,patch.object(gpu,'inventory',return_value=[dict(DEVICE),small]):
            with gpu._ticket(directory,'large request',[DEVICE],18000):
                with gpu.lease('smaller request',minimum_mb=8192,timeout=.1) as device:
                    self.assertEqual(device['uuid'],'GPU-small')

    def test_insufficient_capacity_fails_without_silent_cpu_fallback(self):
        with self.reservations(),patch.object(gpu,'inventory',return_value=[{**DEVICE,'total_mb':8192,'reserve_mb':4096}]):
            with self.assertRaisesRegex(RuntimeError,'after the desktop reserve'):
                with gpu.lease('too small'):self.fail('overcommitted GPU')

    def test_lock_symlinks_are_refused(self):
        with self.reservations() as directory:
            target=directory/'foreign';target.write_text('retain')
            (directory/(DEVICE['uuid']+'.lock')).symlink_to(target)
            with self.assertRaises(OSError):
                with gpu.lease('symlink'):self.fail('followed symlink')
            self.assertEqual(target.read_text(),'retain')

    def test_docker_exposes_only_visible_devices_and_shares_host_locks(self):
        with self.reservations() as directory,patch.object(gpu_runtime,'inventory',return_value=[DEVICE]):
            args=gpu_runtime.docker_arguments({'gpu_usage':True})
            self.assertIn('"device=GPU-test-123"',args)
            self.assertIn(f'{directory}:{directory}:rw',args)
            self.assertEqual(gpu_runtime.docker_arguments({'gpu_usage':True},False),[])

    def test_manual_cpu_jobs_do_not_override_gpu_admission(self):
        hardware={'cpu_workers_available':8,'ram_available_bytes':64*1024**3}
        with patch('oncotracer_cli.sample_resources.inspect_hardware',return_value=hardware), \
             patch.object(gpu_runtime,'options',return_value={'detected':True}):
            result=selected_config({'mode':'ont'},22,{'threads':8,'parallel_samples':22,'gpu_usage':True})
            self.assertEqual(result['parallel_samples'],22)
            self.assertTrue(result['gpu_usage'])
            self.assertTrue(result['methylation_gpu'])
        with patch('oncotracer_cli.sample_resources.inspect_hardware',return_value=hardware), \
             patch.object(gpu_runtime,'options',return_value={'detected':False}):
            with self.assertRaisesRegex(OncoTracerError,'visible NVIDIA'):
                selected_config({'mode':'ont'},2,{'gpu_usage':True})

    def test_boolean_choice_is_strict_and_cpu_budget_is_explicit(self):
        with self.assertRaises(ValueError):gpu.enabled({'gpu_usage':'yes'})
        env=worker_environment({'threads_per_sample':8,'memory_gb_per_sample':16})
        self.assertEqual(env['ONCOTRACER_SAMPLE_THREADS'],'8')
        self.assertEqual(env['OPENBLAS_NUM_THREADS'],'1')


if __name__=='__main__':unittest.main()
