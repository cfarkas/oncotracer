#!/usr/bin/env python3
"""Validate concise, copy/paste-ready repository examples."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE_COMMENT = "# Clone OncoTracer into a given directory."
CLONE_COMMAND = "git clone https://github.com/cfarkas/oncotracer.git"
CD_COMMAND = "cd oncotracer"
EXPECTED_CLONE_BLOCK = f"{CLONE_COMMENT}\n\n{CLONE_COMMAND}\n{CD_COMMAND}"

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

PUBLIC_TEXT_FILES = [ROOT / "README.md"]
PUBLIC_TEXT_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
PUBLIC_TEXT_FILES.extend(sorted((ROOT / "examples").rglob("README.md")))

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{2,6}\s")

FORBIDDEN_VERBOSE_TEXT = (
    "REPO_DIR=",
    "$REPO_DIR",
    "pwd\nls main.nf",
    "Skip the clone command when the repository already exists.",
    "Run the commands from the cloned `oncotracer` directory.",
    "# Run this command from the oncotracer directory.",
    "# Run this step from the cloned oncotracer directory.",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing example file: {relative_path}")
    return path.read_text(encoding="utf-8")


def check_public_text() -> None:
    for path in PUBLIC_TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_VERBOSE_TEXT:
            if phrase in text:
                fail(f"verbose setup remains in {path.relative_to(ROOT)}: {phrase}")


def check_file(relative_path: str) -> None:
    text = read(relative_path)
    if text.count("```") % 2:
        fail(f"unbalanced Markdown code fences in {relative_path}")

    blocks = BASH_BLOCK_RE.findall(text)
    if not blocks:
        fail(f"no Bash command blocks found in {relative_path}")

    clone_blocks = [block.strip() for block in blocks if CLONE_COMMAND in block]
    if not clone_blocks:
        fail(f"no clone command found in {relative_path}")
    for clone_block in clone_blocks:
        if clone_block != EXPECTED_CLONE_BLOCK:
            fail(
                f"clone block in {relative_path} must contain only the exact "
                "comment, git clone, and cd oncotracer commands"
            )

    for block_number, block in enumerate(blocks, start=1):
        first_line = next(
            (line.strip() for line in block.splitlines() if line.strip()),
            "",
        )
        if not first_line.startswith("#"):
            fail(
                f"Bash block {block_number} in {relative_path} must begin with #"
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


def check_hcc1143_validation() -> None:
    for relative_path in (
        "docs/public_cohort.md",
        "examples/hcc1143_lpwgs/README.md",
    ):
        text = read(relative_path)
        required = (
            'CHECKSUMS="$(pwd)/examples/hcc1143_lpwgs/checksums.md5"',
            'md5sum -c "$CHECKSUMS"',
            '(\n  cd "$READS_DIR"',
            'cat > "$READS_DIR/samples.csv" <<\'CSV\'',
        )
        for snippet in required:
            if snippet not in text:
                fail(f"broken HCC1143 validation example in {relative_path}: {snippet}")


def main() -> None:
    check_public_text()
    for relative_path in EXAMPLE_FILES:
        check_file(relative_path)
    check_hcc1143_validation()
    print(
        "PASS: examples use the exact minimal clone block and valid copy/paste commands"
    )


if __name__ == "__main__":
    main()
