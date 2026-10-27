"""Public GPU policy shared with the bundled Nextflow helpers."""
try:
    from .lpwgs.bin.gpu_resources import (POLICY, enabled, environment, inventory,
                                        lease, limit_torch, lock_directory, options)
except ModuleNotFoundError as error:
    if error.name != 'oncotracer_cli.lpwgs':
        raise
    # The standalone executable also embeds this exact stdlib-only helper as
    # importable code. Importing it must not extract the Nextflow payload or
    # create persistent state during --help and CPU dry runs.
    from ._gpu_policy import (POLICY, enabled, environment, inventory,
                             lease, limit_torch, lock_directory, options)

TORCH_PROBE = """import json,torch,transformers
assert torch.cuda.is_available(), 'CUDA PyTorch cannot access the GPU'
torch.cuda.set_per_process_memory_fraction(.04,0)
x=torch.ones((64,64),device='cuda'); y=x@x
assert y[0,0].item()==64
print(json.dumps(dict(framework='torch',version=torch.__version__,cuda=torch.version.cuda,device=str(y.device),success=True)))
"""
TF_PROBE = """import json,tensorflow as tf
devices=tf.config.list_physical_devices('GPU')
assert len(devices)==1, 'Expected one visible CUDA GPU'
tf.config.set_logical_device_configuration(devices[0],[tf.config.LogicalDeviceConfiguration(memory_limit=1024)])
x=tf.matmul(tf.ones((64,64)),tf.ones((64,64)))
assert 'GPU:0' in x.device and float(x[0,0])==64
print(json.dumps(dict(framework='tensorflow',version=tf.__version__,device=x.device,success=True)))
"""


def native_prefix(kind):
    import os
    from pathlib import Path
    variable = 'ONCOTRACER_' + kind.upper().replace('-', '_') + '_PREFIX'
    # The container image/host run controller supplies these after validation.
    if os.environ.get(variable):
        return Path(os.environ[variable])
    from .cli import _load_install_config
    from .install_safety import managed_gpu_runtime_lock
    with managed_gpu_runtime_lock(_load_install_config(), kind) as prefix:
        return prefix


def verify_caller(executable, caller):
    """Require the resource repairs before allowing a caller to see CUDA."""
    import hashlib
    import json
    import os
    from pathlib import Path
    from .runtime import OncoTracerError
    binary = Path(executable).resolve()
    if os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') == 'docker':
        marker = Path('/opt/micromamba/envs/clairs-to/bin/.oncotracer-gpu-policy.json')
    else:
        marker = binary.parent / '.oncotracer-gpu-policy.json'
    try:
        record = json.loads(marker.read_text())
        if record.get('policy') != POLICY or not record.get('files'):
            raise ValueError('unknown resource policy')
        if caller == 'clairs_to' and record.get('clairsto') != '0.4.4':
            raise ValueError('unverified ClairS-TO GPU runtime')
        for path, item in record['files'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != item['after_sha256']:
                raise ValueError('GPU caller source changed: ' + path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OncoTracerError('GPU caller runtime is not verified. Install current OncoTracer GPU tools/image before running: ' + str(error)) from error


def doctor_gpu(backend, install, *, image=None):
    """Execute small bounded CUDA computations; inventory alone is insufficient."""
    import contextlib
    import json
    import os
    from pathlib import Path
    import subprocess
    result = {'policy': POLICY, 'success': False, 'probes': {}}
    try:
        with contextlib.ExitStack() as stack:
            in_container = os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') == 'docker'
            if backend == 'docker' or (backend == 'host' and in_container):
                from .cli import DEFAULT_IMAGE
                image = image or install.get('image') or DEFAULT_IMAGE
                prefixes = {'gpu-reports': Path('/opt/oncotracer-envs/gpu-reports'),
                            'gpu-clair3': Path('/opt/oncotracer-gpu/clair3')}
            elif backend in {'conda', 'poetry', 'host'}:
                from .install_safety import managed_gpu_runtime_lock
                prefixes = {name: stack.enter_context(managed_gpu_runtime_lock(install, name))
                            for name in ('gpu-reports', 'gpu-clair3')}
            else:
                raise RuntimeError('Reserved GPU support requires Docker or native Conda/Poetry tools')
            device = stack.enter_context(lease('doctor', minimum_mb=1024, timeout=5))
            result['device'] = device
            for kind, code in (('gpu-reports', TORCH_PROBE), ('gpu-clair3', TF_PROBE)):
                env = {k: v for k, v in os.environ.items() if k not in
                       {'PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH', 'LD_PRELOAD'}}
                env.update(environment(device), PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
                prefix = prefixes[kind]
                command = [str(prefix / 'bin/python'), '-I', '-B', '-c', code]
                if backend == 'docker':
                    pre = ['docker', 'run', '--rm', '--cpus', '2', '--memory', '8g',
                           '--gpus', 'device=' + device['uuid'], '--env', 'CUDA_VISIBLE_DEVICES=' + device['uuid']]
                    if kind == 'gpu-clair3':
                        pre += ['--env', 'LD_LIBRARY_PATH=/opt/micromamba/envs/clairs-to/lib']
                    command = [*pre, '--entrypoint', command[0], str(image), *command[1:]]
                else:
                    env['LD_LIBRARY_PATH'] = ('/opt/micromamba/envs/clairs-to/lib'
                        if in_container and kind == 'gpu-clair3' else str(prefix / 'lib'))
                completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
                if completed.returncode:
                    raise RuntimeError(kind + ': ' + completed.stderr[-1800:])
                result['probes'][kind] = json.loads(completed.stdout.strip().splitlines()[-1])
            result['success'] = all(v.get('success') for v in result['probes'].values())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    return result


def docker_arguments(config, override=None, *, create=True):
    if not enabled(config, override):
        return []
    from .runtime import OncoTracerError
    devices = inventory()
    if not devices:
        raise OncoTracerError('GPU usage Yes requires a visible NVIDIA GPU and the NVIDIA Container Toolkit for Docker')
    path = lock_directory(create=create)
    # UUIDs are stable across host/container GPU enumeration. Share admission
    # locks with native runs instead of giving each container its own queue.
    return ['--gpus', '"device=' + ','.join(d['uuid'] for d in devices) + '"',
            '--env', 'NVIDIA_VISIBLE_DEVICES=' + ','.join(d['uuid'] for d in devices),
            '--env', 'ONCOTRACER_GPU_DEVICES=' + ','.join(d['uuid'] for d in devices),
            '--volume', f'{path}:{path}:rw', '--env', f'ONCOTRACER_GPU_LOCK_DIR={path}']
