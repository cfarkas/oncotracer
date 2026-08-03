#!/usr/bin/env python3
"""Require copy/paste-ready, start-from-clone documentation examples."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD_REPOSITORY_PATH = "/path/to/my/directory/oncotracer"
REPO_ASSIGNMENT = f"REPO_DIR={STANDARD_REPOSITORY_PATH}"
CLONE_COMMAND = 'git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"'
CD_COMMAND = 'cd "$REPO_DIR"'

EXAMPLE_FILES = (
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)
PLACEHOLDER_PATH_RE = re.compile(r"/path/to/[A-Za-z0-9_./-]+")
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{2,6}\s")


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing example file: {relative_path}")
    return path.read_text(encoding="utf-8")


def check_file(relative_path: str) -> None:
    text = read(relative_path)

    if text.count("```") % 2:
        fail(f"unbalanced Markdown code fences in {relative_path}")

    blocks = BASH_BLOCK_RE.findall(text)
    if not blocks:
        fail(f"no Bash command blocks found in {relative_path}")

    # The first executable block must be sufficient to create a fresh clone.
    # Explanatory prose may mention `nextflow run` before this block; only
    # command boxes are relevant to copy/paste ordering.
    first_block = blocks[0]
    for snippet in (REPO_ASSIGNMENT, CLONE_COMMAND, CD_COMMAND):
        if snippet not in first_block:
            fail(
                f"the first Bash block in {relative_path} must start from a fresh clone: {snippet}"
            )

    first_pipeline_block = next(
        (
            block_number
            for block_number, block in enumerate(blocks, start=1)
            if "nextflow run" in block or "poetry run oncotracer" in block
        ),
        None,
    )
    if first_pipeline_block == 1 and CLONE_COMMAND not in first_block:
        fail(f"a pipeline command appears before git clone in {relative_path}")

    for placeholder in PLACEHOLDER_PATH_RE.findall(text):
        if not placeholder.startswith(STANDARD_REPOSITORY_PATH):
            fail(
                f"additional editable placeholder in {relative_path}: {placeholder}; "
                f"derive paths from {STANDARD_REPOSITORY_PATH}"
            )

    for block_number, block in enumerate(blocks, start=1):
        first_line = next(
            (line.strip() for line in block.splitlines() if line.strip()),
            "",
        )
        if not first_line.startswith("#"):
            fail(
                f"Bash block {block_number} in {relative_path} must begin with a brief # comment"
            )

        for line in block.splitlines():
            if MARKDOWN_HEADING_RE.match(line):
                fail(
                    f"Markdown heading leaked into Bash block {block_number} in "
                    f"{relative_path}: {line.strip()}"
                )

        completed = subprocess.run(
            ["bash", "-n"],
            input=block,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "unknown Bash parse error"
            fail(f"invalid Bash block {block_number} in {relative_path}: {detail}")


def main() -> None:
    for relative_path in EXAMPLE_FILES:
        check_file(relative_path)
    print(
        "PASS: every example starts from a fresh clone, uses one editable repository path, "
        "and contains valid copy/paste Bash blocks"
    )


if __name__ == "__main__":
    main()
