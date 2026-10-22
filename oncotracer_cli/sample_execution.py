"""Cancellable sample workers with independent process groups and traces."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time

from .runtime import CommandRunner, CommandResult, OncoTracerError, utc_now
from .sample_resources import worker_environment

class SampleCancelled(Exception):
    pass


class SampleRunner(CommandRunner):
    """Independent trace and child process group, cancellable without a shell."""
    def __init__(self,parent,trace,stop):
        super().__init__(trace,validators=parent.validators,dry_run=parent.dry_run,echo=parent.echo,
                         environment=parent.environment,protected_environment=parent.protected_environment)
        self.stop=stop
        self.parent_runner = None
        self.progress = parent.progress

    def _record(self, stage, started, finished, returncode, cwd, command):
        super()._record(stage, started, finished, returncode, cwd, command)
        if self.parent_runner is not None:
            self.parent_runner._record(stage, started, finished, returncode, cwd, command)

    @staticmethod
    def _stop_group(process):
        # Nextflow handles SIGINT by terminating its local tasks and preserving work.
        for sig,timeout in ((signal.SIGINT,10),(signal.SIGTERM,5),(signal.SIGKILL,5)):
            try:os.killpg(process.pid,sig)
            except ProcessLookupError:return
            deadline = time.monotonic() + timeout
            leader_exited = False
            while time.monotonic() < deadline:
                process.poll()
                if process.returncode is not None and not leader_exited:
                    leader_exited = True
                    deadline = min(deadline, time.monotonic() + 1)
                # A child may outlive its group leader. Do not return merely
                # because Popen.wait completed; terminate the entire group.
                live = False
                for entry in Path('/proc').glob('[0-9]*/stat'):
                    try:
                        fields = entry.read_text().rsplit(')', 1)[1].split()
                        if fields[0] != 'Z' and int(fields[2]) == process.pid:
                            live = True
                            break
                    except (OSError, ValueError, IndexError):
                        continue
                if not live:
                    return
                time.sleep(.1)

    def run(self,stage,command,*,cwd=None,env=None,containment=None,stdout=None,stderr=None,stdin=None,check=True):
        if self.stop.is_set():raise SampleCancelled('Stopped before launching '+stage)
        if self.dry_run:return super().run(stage,command,cwd=cwd,env=env,containment=containment,stdout=stdout,stderr=stderr,stdin=stdin,check=check)
        argv=tuple(map(str,command));started=utc_now();process=None
        self.progress_task(stage)
        if self.echo:print(f'[{stage}] {shlex.join(argv)}',file=sys.stderr,flush=True)
        self.validate_environment()
        try:
            process=subprocess.Popen(argv,cwd=cwd,env=self.child_environment(env,containment=containment),
                                     stdin=stdin,stdout=stdout,stderr=stderr,start_new_session=True)
            while process.poll() is None:
                if self.stop.wait(.2):
                    self._stop_group(process)
                    raise SampleCancelled('Analysis interrupted; completed work and partial downloads retained')
            if self.stop.is_set():raise SampleCancelled('Analysis interrupted')
            if check and process.returncode:
                raise OncoTracerError(f'stage {stage!r} failed with exit code {process.returncode}: {shlex.join(argv)}')
            return CommandResult(process.returncode,argv,started,utc_now())
        finally:
            if process is not None:
                if process.poll() is None:self._stop_group(process)
                self._record(stage,started,utc_now(),process.returncode,cwd,argv)
            self.validate_environment()

    def pipeline(self, stage, left, right, *, cwd=None, env=None, containment=None):
        if self.stop.is_set():
            raise SampleCancelled('Stopped before launching ' + stage)
        if self.dry_run:
            return super().pipeline(stage, left, right, cwd=cwd, env=env, containment=containment)
        left, right = tuple(map(str, left)), tuple(map(str, right))
        argv = (*left, '|', *right)
        started = utc_now()
        self.progress_task(stage)
        if self.echo:
            print(f'[{stage}] {shlex.join(argv)}', file=sys.stderr, flush=True)
        self.validate_environment()
        processes = []
        try:
            environment = self.child_environment(env, containment=containment)
            upstream = subprocess.Popen(left, cwd=cwd, env=environment, stdout=subprocess.PIPE, start_new_session=True)
            processes.append(upstream)
            try:
                downstream = subprocess.Popen(right, cwd=cwd, env=environment, stdin=upstream.stdout, start_new_session=True)
                processes.append(downstream)
            finally:
                upstream.stdout.close()
            while any(p.poll() is None for p in processes):
                if self.stop.wait(.2):
                    raise SampleCancelled('Analysis interrupted; completed outputs retained')
                # A failed pipe consumer must not leave its producer running.
                if downstream.poll() not in (None, 0):
                    break
            if self.stop.is_set():
                raise SampleCancelled('Analysis interrupted')
            if any(p.returncode not in (None, 0) for p in processes):
                raise OncoTracerError(f'pipeline stage {stage!r} failed (left={upstream.returncode}, right={downstream.returncode})')
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    self._stop_group(process)
            if processes:
                code = next((p.returncode for p in processes if p.returncode), 0)
                self._record(stage, started, utc_now(), code, cwd, argv)
            self.validate_environment()


def sample_runner(parent,trace,stop):
    # Normal execution always uses CommandRunner. Preserve simple injected test runners.
    return SampleRunner(parent,trace,stop) if type(parent) is CommandRunner else parent


@contextmanager
def cancellation():
    stop = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda signum, frame: stop.set())
    try:
        yield stop
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_jobs(jobs, execute, allocation, parent, trace_dir, *, update=None):
    """Only the controller publishes status; workers own separate sample paths.

    jobs contains (sample name, payload). execute(payload, runner) returns a
    result. Ordinary per-sample failures may be returned by execute; raised
    exceptions stop siblings and leave queued work untouched.
    """
    jobs = list(jobs)
    pending = iter(jobs)
    results = {}
    progress = getattr(parent, 'progress', None)
    if progress is not None:
        phase = 'Alignment' if Path(trace_dir).name == 'alignment_logs' else 'Small variants'
        progress.queue(phase, (name for name, _ in jobs), allocation)
    with cancellation() as stop:
        with ThreadPoolExecutor(max_workers=allocation['parallel_samples'], thread_name_prefix='oncotracer-sample') as pool:
            active = {}
            def work(name, payload):
                worker = sample_runner(parent, Path(trace_dir) / (name + '.tsv'), stop)
                if isinstance(worker, SampleRunner):
                    worker.parent_runner = parent
                    worker.sample_name = name
                if isinstance(worker, CommandRunner):
                    worker.environment.update(worker_environment(allocation))
                if stop.is_set():
                    raise SampleCancelled('Stopped before launching sample')
                result = execute(payload, worker)
                if stop.is_set():
                    raise SampleCancelled('Analysis interrupted')
                return result
            def fill():
                while not stop.is_set() and len(active) < allocation['parallel_samples']:
                    job = next(pending, None)
                    if job is None:
                        break
                    name, payload = job
                    if update:
                        update(name, 'running', None)
                    if progress is not None:
                        progress.state(name, 'running')
                    active[pool.submit(work, name, payload)] = name
            try:
                fill()
                while active:
                    done, _ = wait(active, timeout=.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        name = active.pop(future)
                        try:
                            result = future.result()
                        except BaseException as error:
                            stop.set()
                            state = 'interrupted' if isinstance(error, (SampleCancelled, KeyboardInterrupt)) else 'failed'
                            if progress is not None:
                                progress.state(name, state)
                            if update:
                                update(name, state, error)
                            raise
                        results[name] = result
                        if update:
                            update(name, 'finished', result)
                        if progress is not None:
                            progress.state(name, result.get('status', 'complete') if isinstance(result, dict) else 'complete')
                    if stop.is_set():
                        raise SampleCancelled('Analysis interrupted; completed outputs retained')
                    if done:
                        fill()
            except BaseException:
                # Set before executor shutdown: workers poll this while waiting
                # for children, including grandchildren created by shell tools.
                stop.set()
                if progress is not None:
                    progress.interrupted()
                raise
    return results

