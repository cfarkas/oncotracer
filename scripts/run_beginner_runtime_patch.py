#!/usr/bin/env python3
"""Run the guarded beginner patcher with repository-specific bootstrap guards."""

from __future__ import annotations

from pathlib import Path

patcher = Path(__file__).with_name("apply_beginner_runtime_patch.py")
text = patcher.read_text(encoding="utf-8")
old = '''    replace_exact(
        CLI,
        '    docker = require_command("docker")\\n',
        '    docker = shutil.which("docker") or ("docker" if args.dry_run else require_command("docker"))\\n',
        count=2,
    )
'''
new = '''    replace_exact(
        CLI,
        'def _install_docker(args: argparse.Namespace) -> dict[str, object]:\\n'
        '    docker = require_command("docker")\\n',
        'def _install_docker(args: argparse.Namespace) -> dict[str, object]:\\n'
        '    docker = shutil.which("docker") or ("docker" if args.dry_run else require_command("docker"))\\n',
    )
    replace_exact(
        CLI,
        'def _run_docker(config_path: Path, args: argparse.Namespace) -> None:\\n'
        '    docker = require_command("docker")\\n',
        'def _run_docker(config_path: Path, args: argparse.Namespace) -> None:\\n'
        '    docker = shutil.which("docker") or ("docker" if args.dry_run else require_command("docker"))\\n',
    )
'''
if text.count(old) != 1:
    raise SystemExit("beginner patcher Docker bootstrap block is missing or ambiguous")
text = text.replace(old, new, 1)
namespace = {"__name__": "__main__", "__file__": str(patcher)}
exec(compile(text, str(patcher), "exec"), namespace)
