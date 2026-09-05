#!/usr/bin/env python3
"""Validate one installation route and ordinary, checkout-independent analyses."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = chr(96) * 3
BASH_BLOCK_RE = re.compile(re.escape(FENCE) + r"bash[ \t]*\n(.*?)" + re.escape(FENCE), re.DOTALL)
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{2,6}\s")
CHECKOUT_CD_RE = re.compile(r"(?m)^\s*cd\s+oncotracer\s*(?:#.*)?$")
NATIVE_COMMAND_FILES = {
    "docs/index.md": ("oncotracer setup --project", "oncotracer run --config"),
    "docs/quick_start.md": ("oncotracer setup", "oncotracer check", "oncotracer run"),
    "docs/public_cohort.md": ("oncotracer setup", "oncotracer check", "oncotracer run"),
    "docs/setup.md": ("--samplesheet", "--barcodes barcode01,barcode02", "oncotracer run"),
    "docs/auto_params.md": ("oncotracer auto", "oncotracer run --backend conda"),
    "docs/six_tumor_four_normal.md": ("oncotracer auto", "oncotracer run"),
    "docs/running.md": ("oncotracer run --backend conda",),
    "docs/containers.md": ("oncotracer install --conda",),
    "docs/troubleshooting.md": ("oncotracer doctor",),
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def markdown_files() -> list[Path]:
    return [
        ROOT / "README.md", ROOT / "bin/cna_classifier_nf/README.md",
        *sorted((ROOT / "docs").rglob("*.md")),
        *sorted((ROOT / "examples").rglob("*.md")),
    ]


def check_bash_blocks() -> None:
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        if text.count(FENCE) % 2:
            fail(f"unbalanced Markdown code fences in {path.relative_to(ROOT)}")
        for number, block in enumerate(BASH_BLOCK_RE.findall(text), start=1):
            if any(MARKDOWN_HEADING_RE.match(line) for line in block.splitlines()):
                fail(f"Markdown heading leaked into Bash block {number} of {path}")
            result = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
            if result.returncode:
                fail(f"invalid Bash block {number} in {path}: {result.stderr.strip()}")


def check_native_examples_need_no_checkout() -> None:
    for relative, commands in NATIVE_COMMAND_FILES.items():
        text = read(relative)
        for command in commands:
            if command not in text:
                fail(f"missing native command in {relative}: {command}")
        if "git clone" in text or CHECKOUT_CD_RE.search(text):
            fail(f"analysis example unexpectedly requires a checkout in {relative}")


def check_installation_is_explicit() -> None:
    for relative in ("README.md", "docs/installation.md"):
        text = read(relative)
        required = (
            "git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src",
            "python3 -m venv oncotracer-env",
            "oncotracer-env/bin/python -m pip install -e ./oncotracer-src",
            "source oncotracer-env/bin/activate",
        )
        positions = [text.find(command) for command in required]
        if -1 in positions or positions != sorted(positions):
            fail(f"incomplete or out-of-order installation in {relative}")
        if "gh release download v2.0.0" in text:
            fail(f"competing old executable installation in {relative}")
    advanced = read("docs/installation_details.md").split("### Poetry", 1)[1]
    clone = "git clone https://github.com/cfarkas/oncotracer.git"
    if clone not in advanced or advanced.index(clone) > advanced.index("cd oncotracer"):
        fail("Poetry development requires an explicit source checkout before use")


def check_no_retired_public_entrypoints() -> None:
    installation_pages = {"README.md", "docs/installation.md", "docs/installation_details.md"}
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        for forbidden in ("nextflow run", "run_QS1.sh", "run_QS2.sh", "run_example.sh"):
            if forbidden.casefold() in text.casefold():
                fail(f"retired public entry point in {relative}: {forbidden}")
        if "git clone" in text and relative not in installation_pages:
            fail(f"checkout instructions belong in installation, not {relative}")
        if re.search(r"(?:oncotracer|\$BINARY|\$ONCOTRACER_DEV)\"? quickstart [12]", text):
            fail(f"tutorial must teach ordinary setup/check/run, not a helper: {relative}")


def main() -> None:
    check_bash_blocks()
    check_native_examples_need_no_checkout()
    check_installation_is_explicit()
    check_no_retired_public_entrypoints()
    print("PASS: explicit installation, ordinary native examples, valid Bash, and no retired public launchers")


if __name__ == "__main__":
    main()
