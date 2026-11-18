"""Pinned Rockfish resources and execution; no implicit fallback to MM/ML calls."""
from __future__ import annotations
import json
from pathlib import Path
from .runtime import OncoTracerError, sha256_file


def assets():
    return json.loads(Path(__file__).with_name('rockfish_assets.json').read_text())


def resolve_caller(value=None):
    caller = str(value or 'rockfish').strip().lower()
    if caller not in {'rockfish', 'modkit'}:
        raise OncoTracerError('methylation_caller must be rockfish (default) or modkit')
    return caller


def validate_assets(source, model):
    pinned = assets()
    if sha256_file(model) != pinned['model_sha256']:
        raise OncoTracerError('Rockfish model differs from the pinned R10.4.1 5kHz checkpoint')
    expected = pinned['source_files']
    found = {str(p.relative_to(source)) for p in (source / 'rockfish').rglob('*.py')}
    if found != set(expected):
        raise OncoTracerError('Rockfish source inventory differs from the pinned release')
    for relative, digest in expected.items():
        if sha256_file(source / relative) != digest:
            raise OncoTracerError('Rockfish source differs from the pinned release: ' + relative)
    return {'rockfish_model': pinned['model_sha256'], 'rockfish_source_archive': pinned['source_sha256']}


def run_sample(root, request, sample, bam, reference, bed, extract, runner, threads, accelerate):
    from .methylation import _accelerator_environment
    sources = (next(g.roots for g in request.sample_signals if g.sample == sample)
               if request.sample_signals else (request.pod5_dir,))
    workers = min(4, max(0, threads // 4))
    tensor_threads = max(1, threads - workers)
    command = [request.rockfish_python, root / 'bin/scripts/native_rockfish.py',
               '--sample', sample, '--bam', bam, '--reference', reference,
               '--model', request.rockfish_model, '--source', request.rockfish_source,
               '--workers', str(workers), '--batch-size', '256',
               '--bedmethyl', bed, '--extract', extract,
               '--output', bed.parent / (sample + '.rockfish')]
    for source in sources:
        command.extend(['--pod5', source])
    if request.gpu:
        command.append('--gpu')
    env = _accelerator_environment(request.gpu)
    env.update(PYTHONPATH=str(request.rockfish_source), PYTHONDONTWRITEBYTECODE='1',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS=str(tensor_threads), OMP_THREAD_LIMIT=str(tensor_threads), MKL_NUM_THREADS='1',
               WANDB_MODE='disabled', MPLBACKEND='Agg')
    accelerate(request, runner, 'methylation-rockfish-' + sample, command, env=env)
