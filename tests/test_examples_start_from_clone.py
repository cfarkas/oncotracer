
#!/usr/bin/env python3
"""Require simple, relative, copy/paste-ready documentation commands."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/cfarkas/oncotracer.git"
CLONE_COMMENT = "# Clone OncoTracer into a given directory."
CLONE_COMMAND = f"git clone {REPOSITORY_URL}"
CD_COMMAND = "cd oncotracer"

PUBLIC_FILES = [ROOT / "README.md"]
PUBLIC_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
PUBLIC_FILES.extend(sorted((ROOT / "examples").rglob("README.md")))
PUBLIC_FILES.extend([ROOT / "params/illumina.minimal.yml", ROOT / "params/ont.minimal.yml"])

END_TO_END_FILES = (
    "README.md",
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def check_public_paths() -> None:
    forbidden = (
        "REPO_DIR",
        "/path/to/my/directory/oncotracer",
        "/home/student/oncotracer",
        'git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"',
    )
    for path in PUBLIC_FILES:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            if value in text:
                fail(f"obsolete path convention in {path.relative_to(ROOT)}: {value}")


def check_clone_blocks() -> None:
    for relative_path in END_TO_END_FILES:
        text = read(relative_path)
        blocks = BASH_BLOCK_RE.findall(text)
        if not blocks:
            fail(f"no Bash blocks in {relative_path}")
        executable = "\n".join(blocks)
        clone_index = executable.find(CLONE_COMMAND)
        run_positions = [
            position
            for token in ("nextflow run", "poetry run oncotracer")
            if (position := executable.find(token)) >= 0
        ]
        if clone_index < 0:
            fail(f"missing simple clone command in {relative_path}")
        if run_positions and clone_index > min(run_positions):
            fail(f"pipeline command appears before clone in {relative_path}")
        clone_block = next(block for block in blocks if CLONE_COMMAND in block)
        expected = f"{CLONE_COMMENT}\n\n{CLONE_COMMAND}\n{CD_COMMAND}"
        if expected not in clone_block:
            fail(f"clone block is not the simple two-command form in {relative_path}")


def check_bash_blocks() -> None:
    for path in PUBLIC_FILES:
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        if text.count("```") % 2:
            fail(f"unbalanced code fences in {path.relative_to(ROOT)}")
        for number, block in enumerate(BASH_BLOCK_RE.findall(text), start=1):
            first_line = next((line.strip() for line in block.splitlines() if line.strip()), "")
            if not first_line.startswith("#"):
                fail(f"Bash block {number} in {path.relative_to(ROOT)} must begin with #")
            completed = subprocess.run(
                ["bash", "-n"], input=block, text=True, capture_output=True, check=False
            )
            if completed.returncode:
                detail = completed.stderr.strip() or "unknown Bash parse error"
                fail(f"invalid Bash block {number} in {path.relative_to(ROOT)}: {detail}")


def main() -> None:
    check_public_paths()
    check_clone_blocks()
    check_bash_blocks()
    print("PASS: public examples use git clone, cd oncotracer, and relative paths")


if __name__ == "__main__":
    main()
