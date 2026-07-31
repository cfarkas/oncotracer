#!/usr/bin/env python3
"""Regression checks for the public documentation and example commands."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOC_TEXT_FILES = [ROOT / "README.md", ROOT / "mkdocs.yml"]
DOC_TEXT_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
DOC_TEXT_FILES.extend(sorted((ROOT / "examples").rglob("README.md")))

FORBIDDEN_PHRASES = (
    "Every OncoTracer command starts with Nextflow",
    "The user always starts OncoTracer with Nextflow",
    "Illumina controls are never silently ignored",
    "PoN output contract",
    "Current main fixes a fail-open",
    "Current main corrects an earlier fail-open",
    "The first analysis is much larger than the example reads",
    "QuickStart Example 3",
    "GNU Screen",
    "screen -S",
    "screen -r",
    "Enter a screen session",
    "Start OncoTracer through Nextflow",
    "Start containers only through Nextflow",
    "Use container runtimes only through Nextflow",
)

FORBIDDEN_PATHS = (
    "/home/student/oncotracer",
    "/home/user/oncotracer",
    "/absolute/path/oncotracer",
    "/data/study42",
)

STANDARD_REPOSITORY_PATH = "/path/to/my/directory/oncotracer"

COMMENTED_BASH_FILES = (
    "README.md",
    "docs/installation.md",
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "docs/configuration.md",
    "docs/running.md",
    "docs/containers.md",
    "docs/programs.md",
    "docs/developer_guide.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)

GENERIC_PATH_FILES = (
    "README.md",
    "docs/installation.md",
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "docs/configuration.md",
    "docs/running.md",
    "docs/containers.md",
    "docs/programs.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)

REQUIRED_TEXT = {
    "README.md": (
        "https://hub.docker.com/r/carlosfarkas/oncotracer",
        STANDARD_REPOSITORY_PATH,
        "## Other Example Runs",
        "does **not** include or download",
        "cat > \"$REPO_DIR/test/public/hcc1143_lpwgs/samples.csv\" <<'CSV'",
        "https://www.htslib.org/download/",
        "https://github.com/lh3/bwa",
        "https://github.com/lh3/minimap2",
    ),
    "docs/index.md": (
        "Other Example Run: six tumors and four controls",
        "**Not included**; the user must provide all 20 FASTQs",
        "## Estimated time for the first analysis",
    ),
    "docs/installation.md": (
        "--docker",
        "--singularity",
        "https://hub.docker.com/r/carlosfarkas/oncotracer",
        "Other Example Run: six tumors and four controls",
        "https://adoptium.net/temurin/releases/?version=17",
        "https://www.python.org/downloads/",
        "https://zlib.net/pigz/",
    ),
    "docs/quick_start.md": (
        "sample_name,status\nERR12341627,TUMOR",
        "barcode,sample_name,status\nbarcode01,DRR165691,TUMOR",
        "## Estimated time for this analysis",
    ),
    "docs/public_cohort.md": (
        "sample_name,status\nHCC1143_DMSO,TUMOR",
        "HCC1143_BEZ235,TUMOR",
        "HCC1143_TRAMETINIB,TUMOR",
        "cat > \"$READS_DIR/samples.csv\" <<'CSV'",
        STANDARD_REPOSITORY_PATH,
    ),
    "docs/full_tutorial.md": (
        "sample_name,status\nDDLPS_1a,TUMOR",
        "WDLPS_3,TUMOR",
        "## Estimated time and resources",
    ),
    "docs/six_tumor_four_control.md": (
        "# Other Example Run: Six Illumina Tumors and Four Controls",
        "does **not** include or download",
        "Nothing on this page can run until you provide all 20 paired-end files",
        "ONCO001,TUMOR",
        "CTRL004,NORMAL",
    ),
    "docs/auto_params.md": (
        "sample_name,status\nTUMOR_01,TUMOR",
        "CONTROL_02,NORMAL",
        "barcode,sample_name,status\nbarcode01,TUMOR_01,TUMOR",
    ),
    "mkdocs.yml": (
        "Other Example Runs:",
        "Six Tumors + Four Controls (User Data Required)",
    ),
}

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing documentation file: {relative_path}")
    return path.read_text(encoding="utf-8")


def check_forbidden_phrases() -> None:
    for path in DOC_TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PHRASES:
            if phrase.casefold() in text.casefold():
                fail(f"obsolete phrase in {path.relative_to(ROOT)}: {phrase}")


def check_generic_paths() -> None:
    for path in DOC_TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        for legacy_path in FORBIDDEN_PATHS:
            if legacy_path in text:
                fail(f"legacy example path in {path.relative_to(ROOT)}: {legacy_path}")
    for relative_path in GENERIC_PATH_FILES:
        if STANDARD_REPOSITORY_PATH not in read(relative_path):
            fail(
                f"missing standard repository path in {relative_path}: "
                f"{STANDARD_REPOSITORY_PATH}"
            )


def check_required_text() -> None:
    for relative_path, snippets in REQUIRED_TEXT.items():
        text = read(relative_path)
        for snippet in snippets:
            if snippet not in text:
                fail(f"missing required text in {relative_path}: {snippet}")


def check_commented_bash_blocks() -> None:
    for relative_path in COMMENTED_BASH_FILES:
        text = read(relative_path)
        blocks = BASH_BLOCK_RE.findall(text)
        if not blocks:
            fail(f"expected at least one Bash command block in {relative_path}")
        for index, block in enumerate(blocks, start=1):
            first_line = next(
                (line.strip() for line in block.splitlines() if line.strip()),
                "",
            )
            if not first_line.startswith("#"):
                fail(
                    f"Bash block {index} in {relative_path} must begin with a brief # comment"
                )


def main() -> None:
    check_forbidden_phrases()
    check_generic_paths()
    check_required_text()
    check_commented_bash_blocks()
    print("PASS: streamlined documentation, generic paths, and commented commands")


if __name__ == "__main__":
    main()
