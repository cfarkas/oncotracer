#!/usr/bin/env python3
"""Validate beginner copy/paste paths and release-blocker source contracts."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = chr(96) * 3
BASH_BLOCK_RE = re.compile(re.escape(FENCE) + r"bash[ \t]*\n(.*?)" + re.escape(FENCE), re.DOTALL)
ANALYSES_CD = "cd /path/to/my/analyses_dir/"
TUTORIAL_SVGS = (
    "docs/assets/tutorial/quickstart_flow.svg",
    "docs/assets/tutorial/full_tutorial_flow.svg",
    "docs/assets/tutorial/full_tutorial_download_checkpoint.svg",
    "docs/assets/tutorial/full_tutorial_setup_checkpoint.svg",
    "docs/assets/tutorial/full_tutorial_verify_checkpoint.svg",
)


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


def first_command(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


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
    # The published examples download public data, then analyze it with the same
    # `oncotracer run` command a reader uses on their own FASTQs.
    quickstart1 = find_block(
        "docs/quick_start.md",
        "oncotracer install --conda",
        "oncotracer quickstart 1",
        "--download-only",
        "oncotracer run --backend conda",
        "configs/illumina.quickstart.yml",
        "configs/ont.quickstart.yml",
    )
    require_order(
        quickstart1,
        (
            ANALYSES_CD,
            "oncotracer install --conda",
            "oncotracer quickstart 1",
            "oncotracer run --backend conda",
        ),
        "QuickStart 1",
    )

    quickstart2 = find_block(
        "docs/public_cohort.md",
        "oncotracer install --conda",
        "oncotracer quickstart 2",
        "--download-only",
        "oncotracer run --backend conda",
        "configs/hcc1143_lpwgs/illumina.auto.yml",
    )
    require_order(
        quickstart2,
        (
            ANALYSES_CD,
            "oncotracer install --conda",
            "oncotracer quickstart 2",
            "oncotracer run --backend conda",
        ),
        "QuickStart 2",
    )

    readme = find_block(
        "README.md",
        "oncotracer quickstart 1",
        "--download-only",
        "oncotracer run --backend conda",
    )
    require_order(
        readme,
        (ANALYSES_CD, "oncotracer quickstart 1", "oncotracer run --backend conda"),
        "README public QuickStarts",
    )

    for label, block in (
        ("QuickStart 1", quickstart1),
        ("QuickStart 2", quickstart2),
        ("README public QuickStarts", readme),
    ):
        require(first_command(block) == ANALYSES_CD, f"{label} must enter the analyses directory first")
        require("git clone" not in block, f"{label} must run from the installed executable")
        require("cd oncotracer" not in block, f"{label} must not depend on a checkout")
        require("nextflow" not in block.casefold(), f"{label} must not invoke Nextflow")


def check_every_pwd_quickstart_block_enters_analysis_directory() -> None:
    markdown_files = [ROOT / "README.md"]
    markdown_files.extend(sorted((ROOT / "docs").rglob("*.md")))
    markdown_files.extend(sorted((ROOT / "examples").rglob("*.md")))
    violations: list[str] = []
    for path in markdown_files:
        relative_path = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for number, block in enumerate(BASH_BLOCK_RE.findall(text), start=1):
            uses_pwd_quickstart = any(
                token in block
                for token in (
                    "$PWD/oncotracer-quickstart1",
                    "$PWD/oncotracer-quickstart2",
                )
            )
            if uses_pwd_quickstart and first_command(block) != ANALYSES_CD:
                violations.append(
                    f"{relative_path} Bash block {number}: first command is "
                    f"{first_command(block)!r}"
                )
    require(
        not violations,
        "the following $PWD QuickStart blocks do not begin with "
        f"{ANALYSES_CD!r}:\n  " + "\n  ".join(violations),
    )


def check_tutorial_figures_are_native_and_beginner_safe() -> None:
    for path in sorted((ROOT / "docs" / "assets").rglob("*.svg")):
        relative_path = path.relative_to(ROOT).as_posix()
        if any(part in {"legacy", "migration"} for part in path.parts):
            continue
        folded = path.read_text(encoding="utf-8").casefold()
        require(
            "nextflow" not in folded and "main.nf" not in folded,
            "obsolete workflow wording remains in non-legacy SVG "
            f"{relative_path}",
        )

    for relative_path in TUTORIAL_SVGS:
        text = read(relative_path)
        folded = text.casefold()
        require("nextflow" not in folded, f"obsolete Nextflow wording remains in {relative_path}")
        require("main.nf" not in folded, f"obsolete main.nf wording remains in {relative_path}")
        require(
            ANALYSES_CD in text,
            f"beginner tutorial figure does not show the analyses-directory first step: {relative_path}",
        )
    require(
        "Clone OncoTracer" not in read("docs/assets/tutorial/quickstart_flow.svg"),
        "QuickStart figure must start from the installed executable, not a clone",
    )


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
    release_section, poetry_section = installation.split("## 1. Install the stable copied executable", 1)[1].split(marker, 1)
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

    readme = read("README.md")
    non_source_readme = readme.split("## Install current source", 1)[0] + readme.split("## Set up your analysis", 1)[1]
    require("git clone" not in non_source_readme, "README clone belongs only in the labeled source-install section")
    for relative_path in (
        "docs/index.md",
        "docs/quick_start.md",
        "docs/public_cohort.md",
        "docs/auto_params.md",
        "docs/six_tumor_four_normal.md",
        "docs/running.md",
        "docs/containers.md",
    ):
        require(
            "git clone" not in read(relative_path),
            f"native user path unexpectedly requires a checkout in {relative_path}",
        )


def check_native_qdnaseq_uses_called_object_for_seg_exports() -> None:
    script = read("bin/scripts/native_qdnaseq.R")
    require(
        'called <- QDNAseq::callBins(segmented, method = "cutoff")' in script,
        "native qDNAseq must call segmented bins before SEG export",
    )
    require(
        'Biobase::assayDataElement(object, "calls")' in script,
        "native qDNAseq SEG writer must use the actual QDNAseq calls assay",
    )
    require(
        "export_samurai_seg(called[, i], segment_files[[i]], sample, include_calls = FALSE)" in script,
        "no-call SEG export must use the called QDNAseq object",
    )
    require(
        "export_samurai_seg(called[, i], call_files[[i]], sample, include_calls = TRUE)" in script,
        "called SEG export must use the called QDNAseq object",
    )
    require(
        "export_samurai_seg(segmented[, i]" not in script,
        "uncalled segmented objects cannot be passed to the QDNAseq SEG representation",
    )


def write_trace(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["task_id", "hash", "name", "status", "exit", "container"])
        for task_id, name in enumerate(names, start=1):
            writer.writerow([task_id, f"hash-{task_id}", name, "COMPLETED", "0", "example/image:1"])


def check_resumed_nested_trace_combiner() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "nested"
        write_trace(
            root / "attempt-1" / "pipeline_info" / "execution_trace_1.txt",
            [f"SAMURAI:PROCESS_{number} (SAMPLE_A)" for number in range(1, 6)],
        )
        write_trace(
            root / "attempt-2" / "pipeline_info" / "execution_trace_2.txt",
            [f"SAMURAI:PROCESS_{number} (SAMPLE_A)" for number in range(6, 11)],
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tests" / "combine_nested_samurai_traces.py"),
                "--root",
                str(root),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        output = root / ".oncotracer-parity" / "pipeline_info"
        combined = output / "execution_trace_oncotracer_combined.txt"
        manifest = output / "execution_trace_oncotracer_sources.tsv"
        require(combined.is_file(), "resumed nested trace combiner did not create its trace")
        require(manifest.is_file(), "resumed nested trace combiner did not create its source manifest")
        with combined.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        with manifest.open(newline="", encoding="utf-8") as handle:
            sources = list(csv.DictReader(handle, delimiter="\t"))
        require(len(rows) == 10, f"resumed nested trace combiner produced {len(rows)} rows, expected 10")
        require(len(sources) == 2, f"resumed nested trace source manifest has {len(sources)} rows, expected 2")
        require(all(row["status"] == "COMPLETED" for row in rows), "combined trace contains a non-completed row")


def main() -> None:
    # The landing page installs current source for setup/check; the stable
    # executable and its full checksum verification remain in installation.md.
    readme = read("README.md")
    require("not in the v2.0.0 release executable" in readme, "new commands need a release availability note")
    require("oncotracer setup --project" in readme, "landing page must teach project setup")
    check_release_install("docs/installation.md")
    check_native_quickstarts()
    check_every_pwd_quickstart_block_enters_analysis_directory()
    check_tutorial_figures_are_native_and_beginner_safe()
    check_automatic_setup_paths()
    check_checkout_is_only_for_source_development()
    check_native_qdnaseq_uses_called_object_for_seg_exports()
    check_resumed_nested_trace_combiner()
    print(
        "PASS: native v2 release, QuickStart, figures, Automatic Setup, qDNAseq, "
        "and resumed nested-trace contracts are beginner-safe and release-ready"
    )


if __name__ == "__main__":
    main()
