#!/usr/bin/env python3
"""Audit the wording and command boxes in user-facing documentation examples."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXAMPLE_FILES = [
    "README.md",
    "docs/index.md",
    "docs/installation.md",
    "docs/quick_start.md",
    "docs/public_cohort.md",
    "docs/auto_params.md",
    "docs/full_tutorial.md",
    "docs/six_tumor_four_control.md",
    "docs/configuration.md",
    "docs/running.md",
    "docs/containers.md",
    "docs/configuration/illumina.md",
    "docs/configuration/ont.md",
    "docs/configuration/yaml_basics.md",
    "docs/configuration/pathology.md",
    "docs/configuration/refinement.md",
    "docs/outputs.md",
    "docs/developer_guide.md",
    "examples/hcc1143_lpwgs/README.md",
    "examples/prjna754199/README.md",
]

PROHIBITED_MARKDOWN_PHRASES = [
    "QuickStart Example 3",
    "GNU Screen",
    "screen -S",
    "screen -r",
    "PoN output contract",
    "fail-open",
    "Every OncoTracer command starts with Nextflow",
    "The first analysis is much larger than the example reads",
    "Illumina controls are never silently ignored",
    "Nextflow launches every container",
]

ADMONITION_PREFIXES = (
    "!!! important",
    "!!! warning",
    "!!! danger",
    "!!! note",
)

BASH_BLOCK = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing documentation file: {relative_path}")
    return path.read_text(encoding="utf-8")


def audit_bash_blocks(relative_path: str, text: str) -> None:
    for index, match in enumerate(BASH_BLOCK.finditer(text), start=1):
        block = match.group(1).strip("\n")
        nonempty = [line for line in block.splitlines() if line.strip()]
        if not nonempty:
            fail(f"empty Bash block in {relative_path}, block {index}")
        if not nonempty[0].lstrip().startswith("#"):
            fail(
                f"Bash block must begin with a brief # explanation: "
                f"{relative_path}, block {index}"
            )
        if not any(line.lstrip().startswith("#") for line in nonempty):
            fail(f"Bash block lacks a # explanation: {relative_path}, block {index}")


def main() -> None:
    all_markdown = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    all_markdown += list((ROOT / "examples").rglob("*.md"))

    for path in all_markdown:
        text = path.read_text(encoding="utf-8")
        for phrase in PROHIBITED_MARKDOWN_PHRASES:
            if phrase in text:
                fail(f"prohibited phrase {phrase!r} remains in {path.relative_to(ROOT)}")

    for relative_path in EXAMPLE_FILES:
        text = read(relative_path)
        audit_bash_blocks(relative_path, text)
        lowered = text.lower()
        for prefix in ADMONITION_PREFIXES:
            if prefix in lowered:
                fail(f"remove decorative admonition {prefix!r} from {relative_path}")

    readme = read("README.md")
    if "/home/student" in readme:
        fail("README.md must use generic /path/to/my/directory paths")
    if "/path/to/my/directory" not in readme:
        fail("README.md is missing the generic /path/to/my/directory example")

    docker_url = "https://hub.docker.com/r/carlosfarkas/oncotracer"
    for relative_path in ("README.md", "docs/index.md", "docs/installation.md"):
        if docker_url not in read(relative_path):
            fail(f"Docker Hub URL is missing from {relative_path}")

    installation = read("docs/installation.md")
    for option in ("--docker", "--singularity"):
        if option not in installation:
            fail(f"installation page is missing {option}")

    other_example = read("docs/six_tumor_four_control.md")
    required_other_example_text = [
        "# Other Example Run:",
        "not a QuickStart",
        "does not contain FASTQs",
        "will stop until you provide all 20",
        "ONCO001,TUMOR",
        "CTRL004,NORMAL",
    ]
    for expected in required_other_example_text:
        if expected not in other_example:
            fail(f"six-tumor example is missing: {expected!r}")

    mkdocs = read("mkdocs.yml")
    if "Other Example Runs:" not in mkdocs:
        fail("mkdocs.yml must place user-data templates under Other Example Runs")
    if "QuickStart Example 3" in mkdocs:
        fail("mkdocs.yml still labels the user-data template as a QuickStart")

    expected_tables = {
        "docs/public_cohort.md": [
            "HCC1143_DMSO,TUMOR",
            "HCC1143_BEZ235,TUMOR",
            "HCC1143_TRAMETINIB,TUMOR",
        ],
        "docs/full_tutorial.md": [
            "DDLPS_1a,TUMOR",
            "WDLPS_3,TUMOR",
        ],
        "docs/auto_params.md": [
            "Sample_A,TUMOR",
            "Control_2,NORMAL",
            "barcode01,Sample_A,TUMOR",
        ],
        "docs/configuration/pathology.md": [
            "I7738,2023-07738",
            "V480,2024-00480",
        ],
    }
    for relative_path, rows in expected_tables.items():
        text = read(relative_path)
        for row in rows:
            if row not in text:
                fail(f"expected example row {row!r} is missing from {relative_path}")

    print("PASS: documentation examples are concise, commented, and consistently labeled")


if __name__ == "__main__":
    main()
