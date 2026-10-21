"""Partition one analysis budget after checking CPU affinity and available RAM."""
from __future__ import annotations

import math
import re
import sys

from .runtime import OncoTracerError
from .system_check import GIB, inspect_hardware


def parallel_value(value):
    if value == 'auto':
        return 'auto'
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise OncoTracerError('parallel_samples must be auto or a positive integer')
    return int(value)


def parallel_argument(value):
    # argparse validates spelling; handlers report non-positive values uniformly.
    return 'auto' if value == 'auto' else int(value)


def memory_gb(value):
    if value == 'auto':
        return 'auto'
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(GB|G|GIB|MB|M|MIB|TB|T|TIB)?\s*', str(value), re.I)
    if not match:
        raise OncoTracerError('memory must be auto or a total budget such as 64GB or 65536MB')
    unit = (match.group(2) or 'GB').upper()
    amount = float(match.group(1)) * (1024 if unit.startswith('T') else 1/1024 if unit.startswith('M') else 1)
    if not math.isfinite(amount) or amount <= 0:
        raise OncoTracerError('memory must be a positive total budget')
    return amount


def allocate(threads, sample_count, *, parallel='auto', memory='auto', minimum_gb=8,
             preferred_threads=8, hardware=None):
    if type(threads) is not int or threads < 1 or sample_count < 1:
        raise OncoTracerError('Supply samples and positive integer threads')
    requested = parallel_value(parallel)
    requested_memory = memory_gb(memory)
    hardware = inspect_hardware() if hardware is None else hardware
    available_cpu = max(1, int(hardware['cpu_workers_available']))
    cpu = min(threads, available_cpu)
    available_bytes = hardware.get('ram_available_bytes')
    # Leave space for the OS, filesystem cache, and workflow controllers.
    available_gb = available_bytes / GIB * .8 if available_bytes is not None else None
    if requested_memory == 'auto':
        # A 20-CPU project on a 128-CPU server should not claim all host RAM.
        fair_share = available_gb * cpu / available_cpu if available_gb is not None else minimum_gb
        budget = max(minimum_gb, fair_share)
    else:
        budget = requested_memory
    if available_gb is not None:
        budget = min(budget, available_gb)
    budget = math.floor(budget * 1024) / 1024
    if budget < minimum_gb:
        raise OncoTracerError(
            f'This stage needs at least {minimum_gb}GiB per sample; only {budget:g}GiB '
            'is available within the requested budget after reserving RAM for the system. '
            'Wait for other workloads to finish or increase an explicitly smaller memory budget.'
        )
    desired = max(1, cpu // preferred_threads) if requested == 'auto' else requested
    slots = min(desired, sample_count, cpu, int(budget // minimum_gb))
    per_cpu = cpu // slots
    per_mb = int(budget * 1024) // slots
    return {
        'requested_parallel_samples': requested, 'parallel_samples': slots,
        'requested_threads': threads, 'requested_memory': memory,
        'sample_count': sample_count, 'total_threads': cpu, 'total_memory_gb': budget,
        'threads_per_sample': per_cpu, 'memory_per_sample': str(per_mb) + 'MB',
        'memory_gb_per_sample': per_mb / 1024, 'allocated_threads': slots * per_cpu,
        'allocated_memory_gb': slots * per_mb / 1024,
        'unallocated_threads': cpu - slots * per_cpu,
        'detected_cpu_workers': available_cpu, 'detected_available_memory_bytes': available_bytes,
        'minimum_memory_gb_per_sample': minimum_gb,
        'scope': 'Per-analysis task budgets. 20% of available RAM is reserved; helper processes may use additional CPU. Independent analyses do not reserve CPU against each other.',
    }


def announce(stage, allocation):
    a = allocation
    print(f"Resources [{stage}]: detected {a['detected_cpu_workers']} CPU workers; "
          f"budget {a['total_threads']} CPUs / {a['total_memory_gb']:.1f} GiB; "
          f"{a['parallel_samples']} sample(s) at once, {a['threads_per_sample']} CPUs / "
          f"{a['memory_gb_per_sample']:.1f} GiB each.", file=sys.stderr, flush=True)


def worker_environment(allocation):
    cpu = allocation['threads_per_sample']
    memory = allocation['memory_gb_per_sample']
    return {**{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS')},
            'LOKY_MAX_CPU_COUNT': str(cpu),
            'ONCOTRACER_SAMPLE_MEMORY_MB': str(int(memory * 1024)),
            '_JAVA_OPTIONS': f'-Xmx{max(1, int(memory * .7))}g -XX:ActiveProcessorCount={cpu}'}


def offers(config, sample_count, *, threads=None, memory=None, hardware=None):
    """Read-only choices for the browser and terminal, using the same allocator."""
    hardware = inspect_hardware() if hardware is None else hardware
    threads = config.get('threads', min(8, hardware['cpu_workers_available'])) if threads is None else threads
    if type(threads) is not int or threads < 1:
        raise OncoTracerError('CPU threads must be a positive integer')
    memory = memory if memory is not None else config.get('analysis_memory', 'auto')
    minimum = 32 if config.get('run_variants') and config.get('mode') == 'illumina' else 8
    recommended = allocate(threads, sample_count, memory=memory, minimum_gb=minimum, hardware=hardware)
    maximum = allocate(threads, sample_count, parallel=sample_count, memory=memory,
                       minimum_gb=minimum, hardware=hardware)['parallel_samples']
    choices = [allocate(threads, sample_count, parallel=n, memory=memory,
                       minimum_gb=minimum, hardware=hardware) for n in range(1, maximum + 1)]
    return {'hardware': hardware, 'recommended': recommended, 'choices': choices,
            'memory': memory, 'threads': recommended['total_threads'],
            'note': 'These are planning budgets. Resources are checked again at Run; available RAM can reduce concurrency. Existing valid outputs are checked before reuse.'}


def selected_config(config, sample_count, selection):
    if not isinstance(selection, dict) or set(selection) - {'threads', 'parallel_samples', 'memory'}:
        raise OncoTracerError('Select only CPU threads, parallel samples and memory in resource settings')
    choices = offers(config, sample_count, threads=selection.get('threads'), memory=selection.get('memory'))
    parallel = parallel_value(selection.get('parallel_samples', 'auto'))
    requested_memory = selection.get('memory', choices['memory'])
    # Keep the user's ceiling, even if a later run has less available RAM.
    allocate(choices['threads'], sample_count, parallel=parallel, memory=requested_memory,
             minimum_gb=choices['recommended']['minimum_memory_gb_per_sample'], hardware=choices['hardware'])
    return {'threads': choices['threads'], 'parallel_samples': parallel,
            'analysis_memory': requested_memory, 'variant_parallel_samples': parallel,
            'variant_memory': requested_memory}
