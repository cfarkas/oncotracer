"""Live, bounded ONT chunk scheduling without changing scientific worker inputs.

Samples keep their running task settings. Freed sample slots increase admission
for remaining samples. GNU Parallel gets a constant numeric ceiling; live limits
are enforced by process leases, never by rewriting a GNU Parallel jobs file.
"""
from __future__ import annotations
import fcntl,json,math,os,shlex,shutil,subprocess,sys,threading,time,uuid
from pathlib import Path
from .runtime import OncoTracerError,atomic_write_json
from .source_runtime import python_code,package_bootstrap
from .system_check import GIB,inspect_hardware


def process_identity(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return fields[19] if fields[0]!='Z' else None
    except (OSError,ValueError,IndexError):return None


def atomic_text(path,text):
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    temporary.write_text(text);os.replace(temporary,path)


def bounds(allocation,remaining,active,hardware):
    capacity=max(1,min(allocation['parallel_samples'],remaining))
    cpu=min(allocation['total_threads'],max(1,int(hardware['cpu_workers_available'])))
    memory=allocation['total_memory_gb']
    available=hardware.get('ram_available_bytes')
    if available is not None:memory=min(memory,max(0,available/GIB*.8))
    # Retain the existing 8 GiB ONT admission floor for each concurrent chunk.
    memory_workers=math.floor(memory/capacity/8)
    ordered=sorted(active)
    return {sample:{'cpu_threads':cpu//capacity if cpu>=capacity else int(i<cpu),
                    'memory_gb':memory/capacity,
                    'worker_limit':max(0,min(cpu//capacity if cpu>=capacity else int(i<cpu),memory_workers))}
            for i,sample in enumerate(ordered)}


def safe_allocation(allocation):
    slots=min(allocation['parallel_samples'],allocation['total_threads'],int(allocation['total_memory_gb']//8))
    if slots<1:raise OncoTracerError('Dynamic ONT scheduling requires at least 8 GiB within the analysis memory budget')
    if slots==allocation['parallel_samples']:return allocation
    cpu=max(1,allocation['total_threads']//slots);mb=int(allocation['total_memory_gb']*1024)//slots
    return {**allocation,'parallel_samples':slots,'threads_per_sample':cpu,'memory_per_sample':str(mb)+'MB',
            'memory_gb_per_sample':mb/1024,'allocated_threads':slots*cpu,'allocated_memory_gb':slots*mb/1024,
            'unallocated_threads':allocation['total_threads']-slots*cpu,
            'warnings':[f"Dynamic scheduling limited sample concurrency to {slots} within CPU/RAM limits; the requested maximum was {allocation['parallel_samples']}."]}


class DynamicResources:
    def __init__(self,root,samples,allocation,*,interval=5,hardware=inspect_hardware):
        self.allocation=dict(allocation);self.remaining=set(samples);self.active=set()
        self.interval=interval;self.hardware=hardware;self.lock=threading.RLock();self.stop=threading.Event()
        self.directory=Path(root)/'resource_scheduler'/uuid.uuid4().hex
        if self.directory.parent.is_symlink():raise OncoTracerError('Resource scheduler directory must not be a symlink')
        self.directory.mkdir(parents=True)
        self.samples={s:self.directory/s for s in samples}
        for path in self.samples.values():path.mkdir()
        self.owner={'pid':os.getpid(),'start':process_identity(os.getpid())}
        self.thread=None;self.last={};self.error=None

    def start(self):
        self.refresh()
        self.thread=threading.Thread(target=self.watch,name='oncotracer-resources',daemon=True);self.thread.start()
        return self

    def watch(self):
        while not self.stop.wait(self.interval):
            try:self.refresh()
            except Exception as error:
                self.error=str(error);self.stop.set()
                for path in self.samples.values():
                    try:atomic_write_json(path/'budget.json',{'state':'failed','error':self.error,'owner':self.owner})
                    except OSError:pass

    def update(self,sample,state):
        with self.lock:
            if state=='running':self.active.add(sample)
            else:self.active.discard(sample);self.remaining.discard(sample)
            self.refresh()

    def refresh(self):
        with self.lock:
            if self.error:
                raise OncoTracerError('Dynamic resource controller failed: '+self.error)
            values=bounds(self.allocation,len(self.remaining),self.active,self.hardware())
            for sample,path in self.samples.items():
                value=values.get(sample,{'cpu_threads':0,'memory_gb':0,'worker_limit':0})
                budget={'state':'running' if sample in self.remaining else 'complete',
                        'owner':self.owner,'updated_at':time.time(),
                        'maximum_workers':max(1,min(self.allocation['total_threads'],int(self.allocation['total_memory_gb']//8))),**value}
                atomic_write_json(path/'budget.json',budget)
            self.last={'state':'running','updated_at':time.time(),'remaining_samples':len(self.remaining),
                       'active_samples':sorted(self.active),'allocations':values,'owner':self.owner,
                       'maximum_threads':self.allocation['total_threads'],'maximum_memory_gb':self.allocation['total_memory_gb']}
            atomic_write_json(self.directory/'status.json',self.last)

    def sample_directory(self,sample):return self.samples[sample]

    def parallel_wrapper(self,sample):
        path=self.samples[sample]/'oncotracer-parallel'
        source=Path(__file__).resolve().parents[1]
        code=package_bootstrap(source)+'from oncotracer_cli.resource_pool import parallel_main\nparallel_main('+repr(str(self.samples[sample]))+','+str(self.allocation['threads_per_sample'])+')\n'
        path.write_text('#!/bin/sh\nexec '+shlex.join([sys.executable,'-c',code])+' "$@"\n');path.chmod(0o755)
        return path

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=max(10,self.interval*2))
        with self.lock:
            for path in self.samples.values():
                atomic_write_json(path/'budget.json',{'state':'stopped','owner':self.owner})
            atomic_write_json(self.directory/'status.json',{**self.last,'state':'stopped','error':self.error,'updated_at':time.time()})


def read_budget(directory):
    value=json.loads((Path(directory)/'budget.json').read_text())
    owner=value['owner']
    if value.get('state')!='running' or process_identity(owner['pid'])!=owner['start']:
        raise OncoTracerError('Dynamic resource controller stopped; retain unfinished work and resume the analysis')
    if time.time()-value['updated_at']>60:raise OncoTracerError('Dynamic resource controller heartbeat expired')
    return value


def claim_worker(directory,initial_jobs=1,initial_threads=1):
    """Acquire a bounded process lease; never signal a recorded process ID.

    PID plus start time detects stale leases across PID reuse. Counting and
    claiming are serialized. Shrinking budgets let running work finish and
    delay admission until active work falls below the new allowance.
    """
    path=Path(directory)
    with (path/'admission.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=read_budget(path)
        limit=min(value['worker_limit'],max(1,math.floor(value['cpu_threads']*initial_jobs/initial_threads)))
        live=0
        for lease in path.glob('worker-*.json'):
            owner=json.loads(lease.read_text())
            if owner.get('start') is not None and process_identity(owner['pid'])==owner['start']:live+=1
            else:lease.unlink()
        if live>=limit:return None
        lease=path/('worker-'+str(os.getpid())+'-'+uuid.uuid4().hex+'.json')
        atomic_write_json(lease,{'pid':os.getpid(),'start':process_identity(os.getpid()),'started_at':time.time()})
        return lease


def task_main(directory,argv,initial_jobs=1,initial_threads=1):
    if not argv:raise OncoTracerError('No task supplied to the resource admission gate')
    lease=None
    try:
        while lease is None:
            lease=claim_worker(directory,initial_jobs,initial_threads)
            if lease is None:time.sleep(.2)
        # exec preserves the lease identity. A subsequent admission removes the
        # lease after the worker exits, including signal-induced termination.
        os.execvp(argv[0],argv)
    finally:
        if lease is not None:lease.unlink(missing_ok=True)


def supervise_parallel(argv,directory,*,idle_timeout=120,poll_interval=.2):
    """Fail a runner that remains alive with no worker children, not a busy task."""
    from .sample_execution import SampleRunner
    process=subprocess.Popen(argv,start_new_session=True)
    empty_since=None
    try:
        while process.poll() is None:
            read_budget(directory)
            try:
                children=Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().strip()
            except FileNotFoundError:
                if process.poll() is not None:break
                raise
            if children:empty_since=None
            elif empty_since is None:empty_since=time.monotonic()
            elif time.monotonic()-empty_since>=idle_timeout:
                raise OncoTracerError('GNU Parallel stalled with no worker processes; unfinished work retained for recovery')
            time.sleep(poll_interval)
        return process.wait()
    finally:
        if process.poll() is None:SampleRunner._stop_group(process)


def parallel_main(directory,initial_threads):
    argv=sys.argv[1:];real=shutil.which('parallel')
    if not real:raise OncoTracerError('GNU Parallel is required by Clair3')
    if '-j' in argv:
        index=argv.index('-j');initial=argv[index+1];span=2
    else:
        index=next((i for i,x in enumerate(argv) if x.startswith('-j') and x[2:].isdigit()),None)
        initial=argv[index][2:] if index is not None else None;span=1
    if index is None or not str(initial).isdigit() or '::::' not in argv or '--pipe' in argv:
        os.execv(real,[real,*argv])
    template=argv.index('::::')-1
    if template<=index:os.execv(real,[real,*argv])
    path=Path(directory)
    value=read_budget(path)
    ceiling=max(1,int(value['maximum_workers']))
    prefix=python_code('from oncotracer_cli.resource_pool import task_main; import sys; task_main(sys.argv[1],sys.argv[2:],'+str(int(initial))+','+str(initial_threads)+')')
    argv[template]=shlex.join([*prefix,str(path)])+' '+argv[template]
    # GNU Parallel 20250822 retains stale probe PIDs when jobs files change.
    # Use a numeric ceiling; process leases enforce changing admission limits.
    argv[index:index+span]=['-j',str(ceiling)]
    raise SystemExit(supervise_parallel([real,*argv],path))
