#!/usr/bin/env python3
"""Check that simplified documentation preserves the repository directory."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE = "git clone https://github.com/cfarkas/oncotracer.git"
CD = "cd oncotracer"
BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def check_hcc1143_validation(relative_path: str) -> None:
    text = read(relative_path)
    require(
        '(\n  cd "$READS_DIR"' in text,
        f"{relative_path} must validate the FASTQs in a subshell",
    )
    require(
        'md5sum -c "$ROOT/examples/hcc1143_lpwgs/checksums.md5"' in text,
        f"{relative_path} must use the repository-root checksum path",
    )
    require(
        '\ncd "$READS_DIR"\n' not in text,
        f"{relative_path} must not change the parent shell working directory",
    )


def check_readme_quickstart2() -> None:
    text = read("README.md")
    heading = "## QuickStart Example 2: three public HCC1143 libraries"
    require(heading in text, "README QuickStart 2 heading is missing")
    section = text.split(heading, 1)[1]
    blocks = BASH_BLOCK_RE.findall(section)
    require(blocks, "README QuickStart 2 needs a Bash block")
    first = blocks[0]
    require(CLONE in first, "README QuickStart 2 must clone OncoTracer first")
    require(CD in first, "README QuickStart 2 must enter the oncotracer directory")
    require(
        first.index(CLONE) < first.index(CD),
        "README QuickStart 2 must clone before cd oncotracer",
    )
    require(
        first.index(CD) < first.index("READS_DIR="),
        "README QuickStart 2 must enter the clone before deriving paths",
    )


def main() -> None:
    check_hcc1143_validation("docs/public_cohort.md")
    check_hcc1143_validation("examples/hcc1143_lpwgs/README.md")
    check_readme_quickstart2()
    print("PASS: simplified examples preserve the oncotracer working directory")


if __name__ == "__main__":
    main()
