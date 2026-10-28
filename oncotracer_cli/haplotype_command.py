"""Opt-in post-calling haplotype evidence through the normal OncoTracer command."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys

from .runtime import OncoTracerError, require_file, runtime_root


VALUE_OPTIONS = ('bam','vcf','reference','sample','outdir','threads','simulations','seed',
                 'max_distance','max_pairs','min_fragments','min_mapq','min_baseq',
                 'build','max_online_variants','llm_model')
BOOL_OPTIONS = ('ffpe','varsome','llm_report','gpu')


def worker_arguments(args):
    result = []
    for name in VALUE_OPTIONS:
        value = getattr(args,name,None)
        if value is not None:
            result += ['--'+name.replace('_','-'),str(value)]
    result += ['--'+name.replace('_','-') for name in BOOL_OPTIONS if getattr(args,name,False)]
    return result


def command_haplotype(args):
    from .cli import (_load_install_config, _backend_from, _native_environment,
                      _managed_conda_base, DEFAULT_IMAGE)
    from .install_safety import managed_conda_runtime_lock, managed_gpu_runtime_lock
    from .gpu_runtime import docker_arguments
    install = _load_install_config()
    backend = _backend_from(args)
    if not 1 <= args.threads <= 128:
        raise OncoTracerError('--threads must be between 1 and 128')
    if args.gpu and not args.llm_report:
        raise OncoTracerError('--gpu applies to --llm-report; the statistical assessment uses CPU')
    if args.llm_report and not args.varsome:
        raise OncoTracerError('--llm-report requires --varsome; it selects sourced annotation facts only')
    for name in ('bam','vcf','reference'):
        setattr(args,name,str(require_file(Path(getattr(args,name)).expanduser(),name).resolve()))
    require_file(Path(args.reference+'.fai'),'Indexed reference FASTA (.fai)')
    args.outdir = str(Path(args.outdir).expanduser().absolute())
    out = Path(args.outdir)
    if out.is_symlink() or (out.exists() and any(out.iterdir())):
        raise OncoTracerError('Choose an empty --outdir; existing results are preserved')
    if any(out.resolve() == Path(getattr(args,n)) or out.resolve() in Path(getattr(args,n)).parents
           for n in ('bam','vcf','reference')):
        raise OncoTracerError('Output directory must not contain an input file')
    if backend == 'docker':
        out.mkdir(parents=True,exist_ok=True)
        files = {Path(getattr(args,n)) for n in ('bam','vcf','reference')}
        for p in tuple(files):
            files.update(q for q in (Path(str(p)+s) for s in ('.bai','.csi','.fai','.gzi','.tbi')) if q.is_file())
        alternate_index = Path(args.bam).with_suffix('.bai')
        if alternate_index.is_file():
            files.add(alternate_index)
        command = ['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
                   '--cpus',str(args.threads),'--memory','24g',
                   '--env','PYTHONDONTWRITEBYTECODE=1','--env','XDG_CACHE_HOME=/tmp/oncotracer-cache']
        if not args.varsome:
            command += ['--network','none']
        elif os.environ.get('ONCOTRACER_VARSOME_API_KEY'):
            command += ['--env','ONCOTRACER_VARSOME_API_KEY']
        command += docker_arguments({'gpu_usage':args.gpu})
        for path in sorted(files):
            command += ['--volume',f'{path}:{path}:ro']
        command += ['--volume',f'{out}:{out}:rw']
        if args.llm_report:
            cache = Path(os.environ.get('HF_HOME',Path.home()/'.cache/huggingface')).resolve()
            if cache.is_dir():
                command += ['--volume',f'{cache}:{cache}:ro','--env',f'HF_HOME={cache}']
            if args.llm_model and Path(args.llm_model).expanduser().is_dir():
                args.llm_model = str(Path(args.llm_model).expanduser().resolve())
                command += ['--volume',f'{args.llm_model}:{args.llm_model}:ro']
        command += [args.image or install.get('image') or DEFAULT_IMAGE,
                    'haplotype-evidence','--backend','host',*worker_arguments(args)]
        return subprocess.run(command,check=False).returncode
    if backend not in ('conda','poetry','host'):
        raise OncoTracerError('Haplotype evidence supports Conda/Poetry, host and Docker')
    with contextlib.ExitStack() as stack:
        environment = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','LD_PRELOAD','LD_LIBRARY_PATH')}
        if backend in ('conda','poetry'):
            base = _managed_conda_base(install,require_poetry=backend == 'poetry')
            stack.enter_context(managed_conda_runtime_lock(base,require_poetry=backend == 'poetry'))
            environment.update(_native_environment(install))
        core = environment.get('ONCOTRACER_CORE_PREFIX')
        python = str(Path(core)/'bin/python') if core else sys.executable
        options = worker_arguments(args)
        if args.llm_report:
            if args.gpu and backend in ('conda','poetry'):
                prefix = stack.enter_context(managed_gpu_runtime_lock(install,'gpu-reports'))
            else:
                prefix = environment.get('ONCOTRACER_GPU_REPORTS_PREFIX' if args.gpu else 'ONCOTRACER_CLASSIFIER_PREFIX')
            if prefix:
                options += ['--llm-python',str(Path(prefix)/'bin/python')]
        environment.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        script = runtime_root()/'oncotracer_cli/lpwgs/bin/haplotype_evidence.py'
        return subprocess.run([python,'-B',str(script),*options],env=environment,check=False).returncode


def add_haplotype_command(subparsers):
    p = subparsers.add_parser('haplotype-evidence',help='Assess read-backed linkage after calling (experimental; not germline probability)')
    for flag in ('bam','vcf','reference','sample','outdir'):
        p.add_argument('--'+flag,required=True)
    p.add_argument('--backend',choices=('conda','poetry','docker','host'))
    p.add_argument('--image')
    p.add_argument('--threads',type=int,default=2,help='BAM decompression/local model CPU budget')
    p.add_argument('--simulations',type=int,default=10000)
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--max-distance',type=int,default=5000)
    p.add_argument('--max-pairs',type=int,default=100000)
    p.add_argument('--min-fragments',type=int,default=6)
    p.add_argument('--min-mapq',type=int,default=30)
    p.add_argument('--min-baseq',type=int,default=20)
    p.add_argument('--ffpe',action='store_true',help='Retain explicit deamination-context warnings')
    p.add_argument('--build',choices=('hg38','hg19'),default='hg38')
    p.add_argument('--varsome',action='store_true',help='Opt in to online coordinate/allele queries; never sends names or reads')
    p.add_argument('--max-online-variants',type=int,default=20,help='Explicit request cap; API access may incur provider charges')
    p.add_argument('--llm-report',action='store_true',help='Local LLM selects literal online annotation facts; cannot generate scores or prose')
    p.add_argument('--llm-model',help='Existing local Safetensors model directory or pinned cached model')
    p.add_argument('--gpu',action='store_true',help='Use the reserved GPU for local annotation selection')
    p.set_defaults(func=command_haplotype)
