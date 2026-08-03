
#!/usr/bin/env python3
"""Regression checks for the public documentation and example commands."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = [ROOT / "README.md", ROOT / "mkdocs.yml"]
DOC_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
DOC_FILES.extend(sorted((ROOT / "examples").rglob("README.md")))

FORBIDDEN_PHRASES = (
    "Every OncoTracer command starts with Nextflow",
    "Illumina controls are never silently ignored",
    "PoN output contract",
    "Current main fixes a fail-open",
    "The first analysis is much larger than the example reads",
    "QuickStart Example 3",
    "GNU Screen",
    "screen -S",
    "screen -r",
)

ROUTE_FILES = (
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/auto_params.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
)
ROUTES = ("--docker", "--singularity", "poetry run oncotracer", "--conda")
BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)

HCC_URLS = (
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz",
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz",
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz",
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz",
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz",
    "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PHRASES:
            if phrase.casefold() in text.casefold():
                fail(f"obsolete phrase in {path.relative_to(ROOT)}: {phrase}")

    readme = read("README.md")
    for required in (
        "## Four installation and execution methods",
        "params/illumina.minimal.yml",
        "params/ont.minimal.yml",
        "### Installation and execution through Docker",
        "### Installation and execution through Singularity or Apptainer",
        "### Installation and execution through Poetry",
        "### Installation and execution through Conda",
        "git clone https://github.com/cfarkas/oncotracer.git",
        "cd oncotracer",
    ):
        if required not in readme:
            fail(f"README.md is missing: {required}")

    for path in ROUTE_FILES:
        text = read(path)
        for route in ROUTES:
            if route not in text:
                fail(f"missing execution route in {path}: {route}")

    hcc_text = read("docs/public_cohort.md") + read("examples/hcc1143_lpwgs/README.md")
    for url in HCC_URLS:
        if url not in hcc_text:
            fail(f"missing HCC1143 URL: {url}")

    for path in DOC_FILES:
        if path.suffix != ".md":
            continue
        for number, block in enumerate(
            BASH_BLOCK_RE.findall(path.read_text(encoding="utf-8")), start=1
        ):
            first = next((line.strip() for line in block.splitlines() if line.strip()), "")
            if not first.startswith("#"):
                fail(f"Bash block {number} in {path.relative_to(ROOT)} must begin with #")
            checked = subprocess.run(
                ["bash", "-n"], input=block, text=True, capture_output=True, check=False
            )
            if checked.returncode:
                fail(
                    f"invalid Bash block {number} in {path.relative_to(ROOT)}: "
                    f"{checked.stderr.strip()}"
                )

    print("PASS: simplified documentation, execution routes, downloads, and Bash syntax")


if __name__ == "__main__":
    main()
