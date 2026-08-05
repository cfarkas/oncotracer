#!/usr/bin/env python3
"""Validate checkout-independent native examples and explicit legacy boundaries."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE_COMMAND = "git clone https://github.com/cfarkas/oncotracer.git"
FENCE = chr(96) * 3
BASH_BLOCK_RE = re.compile(re.escape(FENCE) + r"bash[ \t]*\n(.*?)" + re.escape(FENCE), re.DOTALL)
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{2,6}\s")
CHECKOUT_CD_RE = re.compile(r"(?m)^\s*cd\s+oncotracer\s*(?:#.*)?$")

NATIVE_COMMAND_FILES = {
    "README.md": ("oncotracer quickstart 1", "oncotracer quickstart 2"),
    "docs/index.md": ("oncotracer install --conda", "oncotracer run --config"),
    "docs/quick_start.md": ("oncotracer quickstart 1",),
    "docs/public_cohort.md": ("oncotracer quickstart 2",),
    "docs/auto_params.md": ("oncotracer auto", "oncotracer run --backend conda"),
    "docs/six_tumor_four_control.md": (
        "oncotracer auto --mode illumina",
        "oncotracer run --backend conda",
    ),
    "docs/running.md": ("oncotracer run --backend conda",),
    "docs/containers.md": ("oncotracer install --conda",),
    "docs/troubleshooting.md": ("oncotracer doctor",),
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing example file: {relative_path}")
    return path.read_text(encoding="utf-8")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    files.extend(sorted((ROOT / "examples").rglob("*.md")))
    return files


def check_bash_blocks() -> None:
    for path in markdown_files():
        relative_path = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if text.count(FENCE) % 2:
            fail(f"unbalanced Markdown code fences in {relative_path}")
        for block_number, block in enumerate(BASH_BLOCK_RE.findall(text), start=1):
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


def check_native_examples_need_no_checkout() -> None:
    for relative_path, commands in NATIVE_COMMAND_FILES.items():
        text = read(relative_path)
        for command in commands:
            if command not in text:
                fail(f"missing native command in {relative_path}: {command}")
        if CLONE_COMMAND in text:
            fail(f"native user example unexpectedly requires cloning in {relative_path}")
        if CHECKOUT_CD_RE.search(text):
            fail(f"native user example unexpectedly enters a checkout in {relative_path}")
        if "nextflow run" in text.casefold():
            fail(f"native user example invokes Nextflow in {relative_path}")


def check_installation_separates_release_and_source_routes() -> None:
    text = read("docs/installation.md")
    marker = "### Poetry"
    if marker not in text:
        fail("installation guide lacks the Poetry development section")
    release_section, source_section = text.split(marker, 1)
    for required in (
        "gh release download v2.0.0",
        "sha256sum -c SHA256SUMS",
        "sudo install -m 0755 oncotracer /usr/local/bin/oncotracer",
    ):
        if required not in release_section:
            fail(f"release installation is missing: {required}")
    if CLONE_COMMAND in release_section:
        fail("release installation incorrectly requires a source checkout")
    if CLONE_COMMAND not in source_section or "cd oncotracer" not in source_section:
        fail("Poetry source-development route lacks an explicit checkout")
    if source_section.index(CLONE_COMMAND) > source_section.index("cd oncotracer"):
        fail("Poetry source-development route enters the checkout before cloning")


def check_legacy_nextflow_examples_are_labeled() -> None:
    migration = ROOT / "docs/migration_v1_to_v2.md"
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        if "nextflow run" not in text.casefold():
            continue
        relative_path = path.relative_to(ROOT)
        if path == migration:
            for required in (
                "# v1.1",
                "# v2",
                "nextflow run main.nf --conda -params-file run.yml -resume",
                "oncotracer run --backend conda --config run.yml",
                "It never forwards that command to Nextflow",
            ):
                if required not in text:
                    fail(f"migration example is missing boundary text: {required}")
            continue
        opening = "\n".join(text.splitlines()[:8])
        if "Legacy v1.1" not in opening:
            fail(f"unlabeled legacy Nextflow command in {relative_path}")


def check_clone_examples_are_source_or_legacy_only() -> None:
    installation = ROOT / "docs/installation.md"
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        if CLONE_COMMAND not in text or path == installation:
            continue
        opening = "\n".join(text.splitlines()[:8])
        if "Legacy v1.1" not in opening:
            fail(
                "mandatory-clone example is neither source development nor legacy: "
                f"{path.relative_to(ROOT)}"
            )


def main() -> None:
    check_bash_blocks()
    check_native_examples_need_no_checkout()
    check_installation_separates_release_and_source_routes()
    check_legacy_nextflow_examples_are_labeled()
    check_clone_examples_are_source_or_legacy_only()
    print(
        "PASS: native examples use the installed executable; source checkouts and "
        "Nextflow commands are confined to labeled development or legacy routes"
    )


if __name__ == "__main__":
    main()
