#!/usr/bin/env python3
"""Validate copy/paste path handling for the installed native v2 executable."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = chr(96) * 3
BASH_BLOCK_RE = re.compile(re.escape(FENCE) + r"bash[ \t]*\n(.*?)" + re.escape(FENCE), re.DOTALL)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def find_block(relative_path: str, *fragments: str) -> str:
    blocks = BASH_BLOCK_RE.findall(read(relative_path))
    for block in blocks:
        if all(fragment in block for fragment in fragments):
            return block
    fail(f"{relative_path} has no Bash block containing: {', '.join(fragments)}")
    raise AssertionError("unreachable")


def require_order(block: str, fragments: tuple[str, ...], label: str) -> None:
    positions = [block.find(fragment) for fragment in fragments]
    require(all(position >= 0 for position in positions), f"{label} is incomplete")
    require(positions == sorted(positions), f"{label} commands are out of order")


def check_release_install(relative_path: str) -> None:
    block = find_block(
        relative_path,
        "gh release download v2.0.0",
        "--dir oncotracer-v2.0.0",
        "sha256sum -c SHA256SUMS",
        "chmod",
        "oncotracer --version",
        "oncotracer provenance --json",
    )
    require_order(
        block,
        (
            "gh release download v2.0.0",
            "--dir oncotracer-v2.0.0",
            "cd oncotracer-v2.0.0",
            "sha256sum -c SHA256SUMS",
            "chmod",
        ),
        f"{relative_path} release installation",
    )
    require(
        "sudo install -m 0755 oncotracer /usr/local/bin/oncotracer" in block,
        f"{relative_path} must install the verified copied executable",
    )
    require("git clone" not in block, f"{relative_path} release install must not clone")
    require("nextflow" not in block.casefold(), f"{relative_path} release install invoked Nextflow")


def check_native_quickstarts() -> None:
    quickstart1 = find_block(
        "docs/quick_start.md",
        "oncotracer install --conda",
        "oncotracer quickstart 1",
        "--backend conda",
        '--test-root "$PWD/oncotracer-quickstart1"',
    )
    require_order(
        quickstart1,
        ("oncotracer install --conda", "oncotracer quickstart 1"),
        "QuickStart 1",
    )

    quickstart2 = find_block(
        "docs/public_cohort.md",
        "oncotracer install --conda",
        "oncotracer quickstart 2",
        "--backend conda",
        '--test-root "$PWD/oncotracer-quickstart2"',
    )
    require_order(
        quickstart2,
        ("oncotracer install --conda", "oncotracer quickstart 2"),
        "QuickStart 2",
    )

    readme = find_block(
        "README.md",
        "oncotracer quickstart 1",
        "oncotracer quickstart 2",
    )
    require_order(
        readme,
        ("oncotracer quickstart 1", "oncotracer quickstart 2"),
        "README public QuickStarts",
    )

    for label, block in (
        ("QuickStart 1", quickstart1),
        ("QuickStart 2", quickstart2),
        ("README public QuickStarts", readme),
    ):
        require("git clone" not in block, f"{label} must run from the installed executable")
        require("cd oncotracer" not in block, f"{label} must not depend on a checkout")
        require("nextflow" not in block.casefold(), f"{label} must not invoke Nextflow")


def check_automatic_setup_paths() -> None:
    block = find_block(
        "docs/auto_params.md",
        "oncotracer auto",
        "oncotracer run --backend conda",
    )
    for path in (
        '"$PWD/project/input/fastq"',
        '"$PWD/project/input/samples.csv"',
        '"$PWD/project/config"',
        '"$PWD/project/results"',
        '"$PWD/project/config/illumina.auto.yml"',
    ):
        require(path in block, f"Automatic Setup is missing copy/paste path {path}")
    require_order(
        block,
        ("oncotracer auto", "oncotracer run --backend conda"),
        "Automatic Setup",
    )
    require("cd " not in block, "Automatic Setup must preserve the caller's working directory")


def check_checkout_is_only_for_source_development() -> None:
    installation = read("docs/installation.md")
    marker = "### Poetry"
    require(marker in installation, "installation guide is missing the Poetry source route")
    release_section, poetry_section = installation.split(marker, 1)
    require("git clone" not in release_section, "global release installation must not require a clone")
    require(
        "does not require a Git clone after installation" in release_section,
        "global executable checkout independence is not documented",
    )
    require_order(
        poetry_section,
        (
            "git clone https://github.com/cfarkas/oncotracer.git",
            "cd oncotracer",
            "./oncotracer install --poetry",
        ),
        "Poetry source-development route",
    )

    for relative_path in (
        "README.md",
        "docs/index.md",
        "docs/quick_start.md",
        "docs/public_cohort.md",
        "docs/auto_params.md",
        "docs/six_tumor_four_control.md",
        "docs/running.md",
        "docs/containers.md",
    ):
        require(
            "git clone" not in read(relative_path),
            f"native user path unexpectedly requires a checkout in {relative_path}",
        )


def main() -> None:
    check_release_install("README.md")
    check_release_install("docs/installation.md")
    check_native_quickstarts()
    check_automatic_setup_paths()
    check_checkout_is_only_for_source_development()
    print("PASS: native v2 release, QuickStart, and Automatic Setup paths are copy/paste safe")


if __name__ == "__main__":
    main()
