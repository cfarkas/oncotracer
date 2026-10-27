"""Launch the OncoTracer browser or configured analysis from a Nextflow entry.

This gateway owns no analysis cache. The full GLIMPSE2 workflow owns task reuse;
keeping the gateway outside a cached process means browser sessions are never
mistaken for completed sequencing analyses.
"""
from __future__ import annotations

from .runtime import OncoTracerError
from .sample_resources import parallel_value, parallel_argument

import argparse
import os
from pathlib import Path
import subprocess
import sys

from .source_runtime import python_module


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-dir', default=str(Path(__file__).resolve().parents[1]), help=argparse.SUPPRESS)
    p.add_argument('--launch-dir', default=str(Path.cwd()), help=argparse.SUPPRESS)
    p.add_argument('--interactive', action='store_true', help='open the local OncoTracer visual browser setup')
    device = p.add_mutually_exclusive_group()
    device.add_argument('--gpu', action='store_true', default=None, help='use reserved GPU memory for supported stages')
    device.add_argument('--cpu', dest='gpu', action='store_false', help='CPU only')
    p.add_argument('--ffpe', action='store_true', help='with --interactive, preselect Illumina FFPE variants and FFPErase')
    p.add_argument('--ont_named_samples', '--ont-named-samples', action='store_true',
                   help='with --interactive, select ONT setup with named library folders')
    p.add_argument('--no_browser', '--no-browser', action='store_true', help='print the browser URL without opening it automatically')
    p.add_argument('--project', help='project folder to prefill in browser setup')
    p.add_argument('--input_folder', '--input-folder', help='FASTQ folder to prefill in browser setup')
    p.add_argument('--cpg_from', '--cpg-from', help='reuse a saved ONT project in simplified CpG browser setup')
    p.add_argument('--classifier', choices=('marlin', 'sturgeon'), help='preselect the CpG classifier group')
    p.add_argument('--resources', help='existing methylation tool/model resource YAML')
    p.add_argument('--image', help='Docker image to prefill in browser setup')
    for name in ('variant_tool_prefix', 'variant_ffperase_root', 'variant_ffperase_models',
                 'variant_ffperase_sif', 'variant_ffperase_prefix'):
        p.add_argument('--' + name, '--' + name.replace('_', '-'), help='existing local setup resource path')
    p.add_argument('--analysis', choices=('variants', 'full'), default='variants',
                   help='variants reuses aligned BAMs; full runs the configured OncoTracer analysis')
    p.add_argument('--config', help='OncoTracer YAML configuration; required without --interactive')
    p.add_argument('--port', type=int, help='fixed browser port; default: 8888 or an available port')
    p.add_argument('--start_dir', '--start-dir', help='initial browser folder; defaults to the Nextflow launch directory')
    p.add_argument('--backend', choices=('host', 'conda', 'poetry', 'docker', 'singularity'))
    p.add_argument('--threads', type=int, help='total CPU budget shared across concurrent samples')
    p.add_argument('--parallel_samples', '--parallel-samples', type=parallel_argument,
                   help='maximum concurrent samples: auto or a positive integer; total CPU/RAM budgets are shared')
    p.add_argument('--dry_run', '--dry-run', action='store_true', help='validate and show the configured analysis without executing it')
    return p


def resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def command(args: argparse.Namespace) -> tuple[list[str], Path, dict[str, str]]:
    """Build literal argv and a local source environment; never invoke a shell."""
    launch = Path(args.launch_dir).expanduser().resolve()
    source = Path(args.project_dir).expanduser().resolve()
    if not source.is_dir() or not (source / 'oncotracer_cli/cli.py').is_file():
        raise ValueError(f'OncoTracer source folder is incomplete: {source}')
    if not launch.is_dir():
        raise ValueError(f'Nextflow launch directory does not exist: {launch}')
    if args.threads is not None and args.threads < 1:
        raise ValueError('--threads must be positive')
    if args.parallel_samples is not None:
        try:
            parallel_value(args.parallel_samples)
        except OncoTracerError as error:
            raise ValueError(str(error)) from error
    if args.port is not None and not 1 <= args.port <= 65535:
        raise ValueError('--port must be between 1 and 65535')
    config = resolve_path(args.config, launch) if args.config else None
    if config is not None and not config.is_file():
        raise ValueError(f'Configuration file does not exist: {config}')
    setup_paths = ('project', 'input_folder', 'cpg_from', 'resources', 'variant_tool_prefix', 'variant_ffperase_root',
                   'variant_ffperase_models', 'variant_ffperase_sif', 'variant_ffperase_prefix')
    if not args.interactive and (args.ffpe or args.ont_named_samples or args.no_browser or args.image or args.classifier or
                                 any(getattr(args, key) for key in setup_paths)):
        raise ValueError('--ffpe and browser setup options require --interactive; no analysis was started')
    if args.ont_named_samples and (args.ffpe or config is not None or args.cpg_from):
        raise ValueError('--ont_named_samples selects new named ONT inputs; omit --ffpe, --config and --cpg_from')
    if args.ffpe and config is not None:
        raise ValueError('--ffpe presets a new FASTQ setup; omit --config and review existing configurations separately')
    if args.cpg_from and (config is not None or args.ffpe or args.input_folder):
        raise ValueError('--cpg_from reuses an ONT project; omit --config, --ffpe and --input_folder')
    argv = python_module('oncotracer_cli.cli', source=source)
    cwd = launch
    if args.interactive:
        if args.dry_run:
            raise ValueError('--dry_run cannot be combined with --interactive; use the browser review before Run')
        if config is not None and args.analysis != 'variants':
            raise ValueError('Browser loading of --config currently supports --analysis variants; omit --config for full visual setup')
        cwd = resolve_path(args.start_dir, launch) if args.start_dir else launch
        if not cwd.is_dir():
            raise ValueError(f'Browser start directory does not exist: {cwd}')
        argv.append('--interactive')
        if args.port is not None:
            argv += ['--port', str(args.port)]
        if args.ffpe:
            argv.append('--ffpe')
        if args.no_browser:
            argv.append('--no-browser')
        for key in setup_paths:
            if getattr(args, key):
                argv += ['--' + key.replace('_', '-'), str(resolve_path(getattr(args, key), launch))]
        if args.image:
            argv += ['--image', args.image]
        if args.classifier:
            argv += ['--classifier', args.classifier]
        if config:
            argv += ['--variant-config', str(config)]
        elif args.ont_named_samples:
            argv += ['--mode', 'ont', '--ont-named-samples']
        elif not args.cpg_from:
            argv += ['--mode', 'illumina', '--variants']
        if args.backend:
            argv += ['--backend', args.backend]
    else:
        if config is None:
            raise ValueError('Provide --config /path/to/run.yml or use --interactive for visual setup')
        argv += ['variants' if args.analysis == 'variants' else 'run', '--config', str(config)]
        if args.backend:
            if args.analysis == 'variants' and args.backend != 'host':
                raise ValueError('Existing-BAM variants use the local Nextflow runtime; omit --backend or use host')
            if args.analysis == 'full':
                argv += ['--backend', args.backend]
        if args.dry_run:
            argv.append('--dry-run')
    if args.threads is not None:
        argv += ['--threads', str(args.threads)]
    if args.parallel_samples is not None:
        argv += ['--parallel-samples', str(args.parallel_samples)]
    if args.gpu is not None:
        argv.append('--gpu' if args.gpu else '--cpu')
    env = os.environ.copy()
    old = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(source) + (os.pathsep + old if old else '')
    env['PYTHONUNBUFFERED'] = '1'
    return argv, cwd, env


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        invocation, cwd, env = command(args)
        result = subprocess.run(invocation, cwd=cwd, env=env, check=False)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (ValueError, OSError) as error:
        print(f'OncoTracer: {error}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
