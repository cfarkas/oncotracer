"""Public GPU policy shared with the bundled Nextflow helpers."""
try:
    from .lpwgs.bin.gpu_resources import (POLICY, enabled, environment, inventory,
                                        lease, limit_torch, lock_directory, options,
                                        wait_hours, add_wait_argument, queue_wait)
except ModuleNotFoundError as error:
    if error.name != 'oncotracer_cli.lpwgs':
        raise
    # The standalone executable also embeds this exact stdlib-only helper as
    # importable code. Importing it must not extract the Nextflow payload or
    # create persistent state during --help and CPU dry runs.
    from ._gpu_policy import (POLICY, enabled, environment, inventory,
                             lease, limit_torch, lock_directory, options,
                             wait_hours, add_wait_argument, queue_wait)

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
    if os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') in {'docker', 'podman'}:
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
            in_container = os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') in {'docker', 'podman'}
            if backend in {'docker', 'podman'} or (backend == 'host' and in_container):
                from .container_runtime import image_reference, user_arguments
                image = image_reference(image or install.get('image'), backend)
                prefixes = {'gpu-reports': Path('/opt/oncotracer-envs/gpu-reports'),
                            'gpu-clair3': Path('/opt/oncotracer-gpu/clair3')}
            elif backend in {'conda', 'poetry', 'host'}:
                from .install_safety import managed_gpu_runtime_lock
                prefixes = {name: stack.enter_context(managed_gpu_runtime_lock(install, name))
                            for name in ('gpu-reports', 'gpu-clair3')}
            else:
                raise RuntimeError('Reserved GPU support requires Docker, Podman or native Conda/Poetry tools')
            device = stack.enter_context(lease('doctor', minimum_mb=1024, timeout=5))
            result['device'] = device
            for kind, code in (('gpu-reports', TORCH_PROBE), ('gpu-clair3', TF_PROBE)):
                env = {k: v for k, v in os.environ.items() if k not in
                       {'PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH', 'LD_PRELOAD'}}
                env.update(environment(device), PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
                prefix = prefixes[kind]
                command = [str(prefix / 'bin/python'), '-I', '-B', '-c', code]
                if backend in {'docker', 'podman'}:
                    pre = [backend, 'run', '--rm', *(user_arguments(backend) if backend == 'podman' else []),
                           '--cpus', '2', '--memory', '8g',
                           *device_arguments(backend, [device]),
                           '--env', 'CUDA_VISIBLE_DEVICES=' + device['uuid']]
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


def device_arguments(backend, devices):
    """Grant exactly the selected devices; Podman uses NVIDIA CDI, not --gpus."""
    from .runtime import OncoTracerError
    uuids = [device['uuid'] for device in devices]
    if backend == 'podman':
        # NVIDIA's default CDI names use the host NVML index. The UUID mask
        # inside the image and shared lease still enforce the selected identity.
        if any(type(device.get('index')) is not int or device['index'] < 0 for device in devices):
            raise OncoTracerError('Podman CDI requires a verified host GPU index')
        return [item for device in devices for item in ('--device', 'nvidia.com/gpu=' + str(device['index']))] + [
            '--env', 'NVIDIA_VISIBLE_DEVICES=void',
            '--env', 'ONCOTRACER_GPU_DEVICES=' + ','.join(uuids)]
    if backend != 'docker':
        raise OncoTracerError(f'Unsupported container backend: {backend}')
    selected = 'device=' + ','.join(uuids)
    return ['--gpus', '"' + selected + '"' if len(uuids) > 1 else selected]


def docker_arguments(config, override=None, *, create=True, backend='docker'):
    if not enabled(config, override):
        return []
    from .runtime import OncoTracerError
    devices = inventory()
    if not devices:
        raise OncoTracerError('GPU usage Yes requires a visible NVIDIA GPU and the NVIDIA Container Toolkit for the selected container engine')
    path = lock_directory(create=create)
    # UUIDs are stable across host/container GPU enumeration. Share admission
    # locks with native runs instead of giving each container its own queue.
    uuids = [d['uuid'] for d in devices]
    access = device_arguments(backend, devices) if backend == 'podman' else [
        '--gpus', '"device=' + ','.join(uuids) + '"',
        '--env', 'NVIDIA_VISIBLE_DEVICES=' + ','.join(uuids),
        '--env', 'ONCOTRACER_GPU_DEVICES=' + ','.join(uuids)]
    return [*access, '--volume', f'{path}:{path}:rw', '--env', f'ONCOTRACER_GPU_LOCK_DIR={path}']
