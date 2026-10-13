"""Launch the OncoTracer browser or configured analysis from a Nextflow entry.

This gateway owns no analysis cache. The full GLIMPSE2 workflow owns task reuse;
keeping the gateway outside a cached process means browser sessions are never
mistaken for completed sequencing analyses.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-dir', default=str(Path(__file__).resolve().parents[1]), help=argparse.SUPPRESS)
    p.add_argument('--launch-dir', default=str(Path.cwd()), help=argparse.SUPPRESS)
    p.add_argument('--interactive', action='store_true', help='open the local OncoTracer visual browser setup')
    p.add_argument('--analysis', choices=('variants', 'full'), default='variants',
                   help='variants reuses aligned BAMs; full runs the configured OncoTracer analysis')
    p.add_argument('--config', help='OncoTracer YAML configuration; required without --interactive')
    p.add_argument('--port', type=int, default=8888, help='local browser port (default: 8888)')
    p.add_argument('--start_dir', '--start-dir', help='initial browser folder; defaults to the Nextflow launch directory')
    p.add_argument('--backend', choices=('host', 'conda', 'poetry', 'docker', 'singularity'))
    p.add_argument('--threads', type=int, help='optional CPU override')
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
    if not 1 <= args.port <= 65535:
        raise ValueError('--port must be between 1 and 65535')
    config = resolve_path(args.config, launch) if args.config else None
    if config is not None and not config.is_file():
        raise ValueError(f'Configuration file does not exist: {config}')
    argv = [sys.executable, '-m', 'oncotracer_cli.cli']
    cwd = launch
    if args.interactive:
        if args.dry_run:
            raise ValueError('--dry_run cannot be combined with --interactive; use the browser review before Run')
        if config is not None and args.analysis != 'variants':
            raise ValueError('Browser loading of --config currently supports --analysis variants; omit --config for full visual setup')
        cwd = resolve_path(args.start_dir, launch) if args.start_dir else launch
        if not cwd.is_dir():
            raise ValueError(f'Browser start directory does not exist: {cwd}')
        argv += ['setup', '--port', str(args.port)]
        if config:
            argv += ['--variant-config', str(config)]
        else:
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
