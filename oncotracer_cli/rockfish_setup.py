"""Provision an isolated, checksum-pinned Rockfish runtime during explicit setup."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request
from pathlib import Path
from .rockfish import assets, validate_assets
from .runtime import OncoTracerError, sha256_file, atomic_write_json, render_flat_yaml

DEPENDENCIES = ['setuptools==75.8.2', 'lightning==2.5.1', 'pytorch-lightning==2.5.1',
                'jsonargparse[signatures]==4.38.0', 'mappy==2.28', 'ont-fast5-api==4.1.3',
                'gdown==5.2.0', 'pod5==0.3.23', 'polars==0.19.19', 'wandb==0.19.9',
                'einops==0.8.1', 'numpy==1.26.4', 'pysam==0.22.1', 'torchmetrics==1.9.0']


def checked_download(url, path, digest):
    if path.exists():
        if sha256_file(path) != digest:
            raise OncoTracerError('Existing download has the wrong checksum: ' + str(path))
        return
    temporary = path.with_suffix(path.suffix + '.part')
    try:
        urllib.request.urlretrieve(url, temporary)
        if sha256_file(temporary) != digest:
            raise OncoTracerError('Downloaded Rockfish resource has the wrong checksum')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(args):
    base = Path(args.directory).expanduser().resolve()
    if base == Path('/') or base == Path.home():
        raise OncoTracerError('Choose a dedicated Rockfish runtime directory')
    base.mkdir(parents=True, exist_ok=True)
    pinned = assets(); source = base / 'source'; model = base / 'rf_5kHz.ckpt'
    python = base / 'env/bin/python'
    env = dict(os.environ, PIP_CACHE_DIR=str(base / 'cache/pip'), XDG_CACHE_HOME=str(base / 'cache'),
               PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
               MKL_NUM_THREADS='1', WANDB_MODE='disabled', TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1')
    commands = []
    def run(argv, **kwargs):
        command = [str(v) for v in argv]; commands.append(command)
        return subprocess.run(command, env=env, check=True, **kwargs)
    if not args.verify_only:
        archive = base / ('source-' + pinned['source_commit'] + '.tar.gz')
        checked_download('https://codeload.github.com/lbcb-sci/rockfish/tar.gz/' + pinned['source_commit'], archive, pinned['source_sha256'])
        if not source.exists():
            with tempfile.TemporaryDirectory(dir=base, prefix='unpack-') as temporary:
                with tarfile.open(archive) as tar:
                    for member in tar.getmembers():
                        parts = Path(member.name).parts
                        if len(parts) < 2: continue
                        relative = Path(*parts[1:])
                        if relative.is_absolute() or '..' in relative.parts or not (member.isdir() or member.isfile()):
                            raise OncoTracerError('Unsafe entry in Rockfish archive')
                        member.name = str(relative)
                        tar.extract(member, temporary, filter='data')
                Path(temporary).rename(source)
        if not python.exists():
            interpreter = args.python or shutil.which('python3.11') or shutil.which('python3')
            if not interpreter: raise OncoTracerError('Python 3.10–3.12 is required for the Rockfish runtime')
            run([interpreter, '-c', 'import sys;assert (3,10)<=sys.version_info[:2]<(3,13), "Rockfish requires Python 3.10–3.12"'])
            run([interpreter, '-m', 'venv', base / 'env'])
        index = 'https://download.pytorch.org/whl/' + ('cu124' if args.cuda else 'cpu')
        run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--index-url', index,
             'torch==2.6.0', 'torchvision==0.21.0', 'torchaudio==2.6.0'])
        run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', *DEPENDENCIES])
        run([python, '-m', 'pip', 'install', '--no-deps', source])
        if not model.exists():
            temporary = model.with_suffix('.part')
            run([python, '-c', 'import gdown,sys;gdown.download(id=sys.argv[1],output=sys.argv[2],use_cookies=False)',
                 pinned['model_gdrive_id'], temporary])
            if sha256_file(temporary) != pinned['model_sha256']:
                raise OncoTracerError('Downloaded Rockfish model has the wrong checksum')
            temporary.replace(model)
    checksums = validate_assets(source, model)
    env['PYTHONPATH'] = str(source)
    run([python, '-m', 'rockfish', 'inference', '--help'], stdout=subprocess.DEVNULL)
    smoke = run([python, '-c',
        'import sys,torch,json;from rockfish.model.inference import load_model;'
        'model,block=load_model(sys.argv[1],"cpu",None);model.eval();'
        'print(json.dumps({"torch":torch.__version__,"block_size":block,"parameters":sum(p.numel() for p in model.parameters())}))',
        model], capture_output=True, text=True)
    config = {'methylation_caller': 'rockfish', 'methylation_rockfish_python': str(python),
              'methylation_rockfish_source': str(source), 'methylation_rockfish_model': str(model),
              'methylation_rockfish_model_sha256': pinned['model_sha256']}
    (base / 'resources.yml').write_text(render_flat_yaml(config))
    atomic_write_json(base / 'setup.json', dict(status='verified', checksums=checksums, commands=commands,
                      runtime=json.loads(smoke.stdout.strip().splitlines()[-1]), source_commit=pinned['source_commit']))
    print('Rockfish runtime verified; use --resources ' + str(base / 'resources.yml'))
    return 0


def add_command(subparsers):
    parser = subparsers.add_parser('prepare-rockfish', help='Download and verify the isolated Rockfish caller and model')
    parser.add_argument('--directory', required=True, help='Dedicated runtime directory')
    parser.add_argument('--python', help='Python 3.10–3.12 used to create the private runtime')
    parser.add_argument('--cuda', action='store_true', help='Install CUDA 12.4 PyTorch wheels; CPU is the default')
    parser.add_argument('--verify-only', action='store_true', help='Verify existing assets without downloads or installation')
    parser.set_defaults(func=prepare)
