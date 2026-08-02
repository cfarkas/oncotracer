#!/usr/bin/env python3
"""Launch the versioned OncoTracer Nextflow workflow from Poetry."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

BACKEND_FLAGS = {
    "docker": "--docker",
    "singularity": "--singularity",
    "conda": "--conda",
}
RUNTIME_FLAGS = tuple(BACKEND_FLAGS.values())

HELP = """\
Usage:
  poetry run oncotracer [launcher options] -- <Nextflow/OncoTracer arguments>
  poetry run oncotracer [launcher options] <Nextflow/OncoTracer arguments>

Launcher options:
  --repo-dir PATH       Repository clone containing main.nf [current directory]
  --backend NAME        docker, singularity, or conda [docker]
  --print-command       Print the resolved command without executing it
  -h, --help            Show this help

Examples:
  poetry run oncotracer --backend docker --make_test --test_root /path/to/test
  poetry run oncotracer --backend docker -params-file /path/to/run.yml -resume
  poetry run oncotracer --backend singularity -params-file /path/to/run.yml -resume
  poetry run oncotracer --backend conda -params-file /path/to/run.yml -resume

Poetry manages the Python launcher. The selected backend still supplies the
scientific command-line and R software used by the Nextflow workflow.
"""


class UsageError(ValueError):
    """Raised when launcher arguments are invalid."""


def _pop_value(arguments: list[str], option: str, default: str) -> str:
    """Remove one launcher option from *arguments* and return its value."""
    value = default
    index = 0
    seen = False
    while index < len(arguments):
        item = arguments[index]
        if item == option:
            if seen:
                raise UsageError(f"{option} may be provided only once")
            if index + 1 >= len(arguments):
                raise UsageError(f"{option} requires a value")
            value = arguments[index + 1]
            del arguments[index : index + 2]
            seen = True
            continue
        prefix = f"{option}="
        if item.startswith(prefix):
            if seen:
                raise UsageError(f"{option} may be provided only once")
            value = item[len(prefix) :]
            del arguments[index]
            seen = True
            continue
        index += 1
    return value


def _remove_flag(arguments: list[str], option: str) -> bool:
    found = False
    while option in arguments:
        if found:
            raise UsageError(f"{option} may be provided only once")
        arguments.remove(option)
        found = True
    return found


def _repository_main(repo_dir: str, cwd: Path) -> Path:
    candidate = Path(repo_dir).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    candidate = candidate.resolve()
    main_nf = candidate / "main.nf"
    if not main_nf.is_file():
        package_root = Path(__file__).resolve().parents[1]
        fallback = package_root / "main.nf"
        if repo_dir == "." and fallback.is_file():
            return fallback
        raise UsageError(f"main.nf was not found under --repo-dir: {candidate}")
    return main_nf


def build_command(argv: Sequence[str], *, cwd: Path | None = None) -> tuple[list[str], bool]:
    """Resolve launcher arguments into an executable Nextflow command."""
    arguments = list(argv)
    if "--" in arguments:
        arguments.remove("--")

    repo_dir = _pop_value(arguments, "--repo-dir", ".")
    backend = _pop_value(arguments, "--backend", "docker").strip().lower()
    print_only = _remove_flag(arguments, "--print-command")

    if backend not in BACKEND_FLAGS:
        raise UsageError(
            f"unsupported --backend {backend!r}; choose docker, singularity, or conda"
        )

    explicit_runtime = [flag for flag in RUNTIME_FLAGS if flag in arguments]
    if len(explicit_runtime) > 1:
        raise UsageError("use only one runtime flag: --docker, --singularity, or --conda")
    if explicit_runtime and explicit_runtime[0] != BACKEND_FLAGS[backend]:
        raise UsageError(
            f"--backend {backend} conflicts with explicit {explicit_runtime[0]}"
        )
    if not explicit_runtime:
        arguments.insert(0, BACKEND_FLAGS[backend])

    nextflow = shutil.which("nextflow")
    if not nextflow:
        raise UsageError("nextflow is not available on PATH")

    current = cwd or Path.cwd()
    main_nf = _repository_main(repo_dir, current)
    return [nextflow, "run", str(main_nf), *arguments], print_only


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values in (["-h"], ["--help"]):
        print(HELP)
        return 0

    try:
        command, print_only = build_command(values)
    except UsageError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("Run 'poetry run oncotracer --help' for usage.", file=sys.stderr)
        return 2

    rendered = shlex.join(command)
    print(f"OncoTracer command: {rendered}", file=sys.stderr)
    if print_only:
        print(rendered)
        return 0

    completed = subprocess.run(command, env=os.environ.copy(), check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
