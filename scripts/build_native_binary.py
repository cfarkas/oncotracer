#!/usr/bin/env python3
"""Build the auditable single-file OncoTracer zipapp release executable."""

from __future__ import annotations

import argparse
import compileall
import os
import shutil
import tempfile
import zipapp
from pathlib import Path


def copy_payload(root: Path, staging: Path) -> None:
    shutil.copytree(root / "oncotracer_cli", staging / "oncotracer_cli", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    payload = staging / "payload"
    payload_ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.nf", "nextflow.config", ".nextflow*", "work"
    )
    for name in ("bin", "examples", "params", "environments"):
        source = root / name
        if not source.exists():
            raise SystemExit(f"required payload path is missing: {source}")
        shutil.copytree(source, payload / name, ignore=payload_ignore)
    (staging / "__main__.py").write_text(
        "from oncotracer_cli.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("dist/oncotracer"))
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oncotracer-zipapp-") as directory:
        staging = Path(directory)
        copy_payload(root, staging)
        if not compileall.compile_dir(staging / "oncotracer_cli", quiet=1, force=True):
            raise SystemExit("Python compilation failed")
        zipapp.create_archive(
            staging,
            target=output,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    output.chmod(output.stat().st_mode | 0o755)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
