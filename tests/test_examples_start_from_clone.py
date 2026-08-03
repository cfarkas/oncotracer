#!/usr/bin/env python3
"""Validate concise, copy/paste-ready repository examples."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE_COMMAND = "git clone https://github.com/cfarkas/oncotracer.git"
CD_COMMAND = "cd oncotracer"

EXAMPLE_FILES = (
    "README.md",
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "docs/installation.md",
    "docs/developer_guide.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)
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
    if "REPO_DIR=" in text or "$REPO_DIR" in text:
        fail(f"verbose REPO_DIR setup remains in {relative_path}")
    if 'git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"' in text:
        fail(f"target-path clone remains in {relative_path}")

    blocks = BASH_BLOCK_RE.findall(text)
    if not blocks:
        fail(f"no Bash command blocks found in {relative_path}")

    clone_blocks = [block for block in blocks if CLONE_COMMAND in block]
    if not clone_blocks:
        fail(f"no clone command found in {relative_path}")
    first_clone = clone_blocks[0]
    if CD_COMMAND not in first_clone:
        fail(f"clone block does not enter oncotracer in {relative_path}")
    if first_clone.index(CLONE_COMMAND) > first_clone.index(CD_COMMAND):
        fail(f"cd oncotracer appears before clone in {relative_path}")

    for block_number, block in enumerate(blocks, start=1):
        first_line = next(
            (line.strip() for line in block.splitlines() if line.strip()),
            "",
        )
        if not first_line.startswith("#"):
            fail(
                f"Bash block {block_number} in {relative_path} must begin with #"
            )
        if "REPO_DIR=" in block or "$REPO_DIR" in block:
            fail(
                f"REPO_DIR remains in Bash block {block_number} of {relative_path}"
            )
        for line in block.splitlines():
            if MARKDOWN_HEADING_RE.match(line):
                fail(
                    f"Markdown heading leaked into Bash block {block_number} "
                    f"of {relative_path}"
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
            fail(
                f"invalid Bash block {block_number} in {relative_path}: {detail}"
            )


def main() -> None:
    for relative_path in EXAMPLE_FILES:
        check_file(relative_path)
    print(
        "PASS: examples use git clone followed by cd oncotracer "
        "and valid relative commands"
    )


if __name__ == "__main__":
    main()
