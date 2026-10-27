#!/usr/bin/env python3
"""Apply resource-only repairs to the pinned Clair3 1.2 / ClairS-TO 0.4.4.

No model, threshold or variant logic is changed. Reject unfamiliar source;
record before/after hashes for review and native/Docker installation checks.
"""
import argparse
import hashlib
import json
from pathlib import Path


def repair(clair3, clairsto=None):
    changed = {}
    def patch(path, old, new, count=1):
        original = path.read_text()
        if old not in original and new in original:
            return
        if original.count(old) != count:
            raise RuntimeError('Unsupported caller source; cannot apply GPU resource repair: ' + str(path))
        value = original.replace(old, new)
        path.write_text(value)
        changed[str(path)] = {'before_sha256': changed.get(str(path), {}).get('before_sha256', hashlib.sha256(original.encode()).hexdigest()),
                              'after_sha256': hashlib.sha256(value.encode()).hexdigest()}
    patch(clair3 / 'run_clair3.sh', 'MAX_THREADS=$(nproc)',
          'MAX_THREADS=$(env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc)')
    gpu = clair3 / 'clair3/CallVariantsFromCffiGPU.py'
    text = gpu.read_text()
    start, end = text.index('def check_gpu_memory('), text.index('\ndef Run(')
    old = text[start:end]
    if 'ONCOTRACER_GPU_MEMORY_MB' not in old:
        if 'gpu_threads = free_mem // memory' not in old:
            raise RuntimeError('Unrecognized Clair3 GPU allocator')
        patch(gpu, old, '''def check_gpu_memory(memory, device_ids=None, print_log=True):
    # OncoTracer reserved-vram-v1: the controller admits one visible GPU.
    import tensorflow as tf
    budget = int(os.environ.get("ONCOTRACER_GPU_MEMORY_MB", "0"))
    uuid = os.environ.get("ONCOTRACER_GPU_UUID", "")
    if budget <= 0 or not uuid or os.environ.get("CUDA_VISIBLE_DEVICES") != uuid:
        raise RuntimeError("GPU calling requires an OncoTracer GPU reservation")
    gpus = tf.config.list_physical_devices('GPU')
    if len(gpus) != 1:
        raise RuntimeError("Expected one reserved CUDA GPU; verify the GPU TensorFlow installation")
    free = get_gpu_memory(uuid)[0]
    reserve = int(os.environ["ONCOTRACER_GPU_RESERVE_MB"])
    # Leave room for CUDA contexts as well as the desktop reserve.
    usable = max(0, min(budget, free - reserve) - 1024)
    workers = min(int(os.environ.get("ONCOTRACER_SAMPLE_THREADS", "1")), usable // memory)
    if workers < 1:
        raise RuntimeError("Insufficient reserved GPU memory for Clair3 inference")
    if print_log:
        print(f"OncoTracer GPU: {workers} inference workers x {memory} MiB; budget {budget} MiB")
    return [0] * workers
''')
    patch(clair3 / 'clair3/CallVariantsFromCffi.py',
          'os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id) if args.gpu_id else ""',
          '# Keep the reserved UUID mask; gpu_id is a logical index within it.\n        if str(args.gpu_id) not in {"0", "None"}:\n            raise RuntimeError("Unexpected GPU index outside the OncoTracer reservation")')
    patch(clair3 / 'clair3/CallVariantsFromCffi.py',
          "    elif args.use_gpu:\n        gpus = tf.config.experimental.list_physical_devices('GPU')",
          "    elif args.use_gpu:\n        # Preserve float32 precision for scientific inference on Ampere GPUs.\n        tf.config.experimental.enable_tensor_float_32_execution(False)\n        gpus = tf.config.experimental.list_physical_devices('GPU')")
    if clairsto:
        entry = clairsto / 'run_clairs_to'
        patch(entry, "p_predict_command += ' -j ' + str(args.threads)",
              "p_predict_command += ' -j ' + str(min(args.threads, max(1, (int(os.environ.get('ONCOTRACER_GPU_MEMORY_MB', '0')) - 1024) // 4096)) if args.use_gpu else args.threads)", count=2)
        patch(clairsto / 'clairs/predict.py',
              '''    if use_gpu and not torch.cuda.is_available():
        print("[WARNING] --use_gpu is enabled, but cuda is not found")
        use_gpu = False
    if use_gpu:
        device = 'cuda' '''.rstrip(),
              '''    if use_gpu and not torch.cuda.is_available():
        raise RuntimeError("GPU requested but CUDA PyTorch is unavailable")
    if use_gpu:
        if int(os.environ.get('ONCOTRACER_GPU_MEMORY_MB', '0')) < 8192:
            raise RuntimeError("ClairS-TO requires an OncoTracer GPU reservation")
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(.95, 4096 * 1024**2 / total), 0)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        device = 'cuda' '''.rstrip())
        # The indel branch constructs CPU modules before loading a CUDA state
        # dictionary. Loading weights alone does not move those modules.
        patch(clairsto / 'clairs/predict.py',
              '    model_aff.eval()\n    model_neg.eval()',
              '    model_aff.to(device).eval()\n    model_neg.to(device).eval()')
    marker = clair3 / '.oncotracer-gpu-policy.json'
    previous = json.loads(marker.read_text()) if marker.exists() else {'files': {}}
    previous['files'].update(changed)
    previous.update(policy='reserved-vram-v1', clair3='1.2.0', clairsto='0.4.4' if clairsto else None)
    marker.write_text(json.dumps(previous, indent=2) + '\n')
    return previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clair3-bin', type=Path, required=True)
    parser.add_argument('--clairsto-bin', type=Path)
    args = parser.parse_args()
    print(json.dumps(repair(args.clair3_bin, args.clairsto_bin), indent=2))
