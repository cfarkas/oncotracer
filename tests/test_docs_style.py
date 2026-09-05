#!/usr/bin/env python3
"""Standalone regression checks for the native v2 public documentation."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = chr(96) * 3
BASH_BLOCK_RE = re.compile(re.escape(FENCE) + r"bash[ \t]*\n(.*?)" + re.escape(FENCE), re.DOTALL)

MARKDOWN_FILES = [ROOT / "README.md"]
MARKDOWN_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
MARKDOWN_FILES.extend(sorted((ROOT / "examples").rglob("*.md")))

ACTIVE_NATIVE_FILES = (
    "README.md",
    "docs/index.md",
    "docs/installation.md",
    "docs/quick_start.md",
    "docs/auto_params.md",
    "docs/public_cohort.md",
    "docs/six_tumor_four_normal.md",
    "docs/running.md",
    "docs/configuration_v2.md",
    "docs/containers.md",
    "docs/outputs.md",
    "docs/gallery.md",
    "docs/native_architecture.md",
    "docs/parity_release.md",
    "docs/citation_research_use.md",
    "docs/troubleshooting.md",
    "docs/developer_guide.md",
)

REQUIRED_TEXT = {
    "README.md": (
        "copy-number changes",
        "not in the v2.0.0 release executable",
        "git clone --branch improve/beginner-setup-methylation https://github.com/cfarkas/oncotracer.git oncotracer-src",
        "pip install -e ./oncotracer-src",
        "oncotracer setup --project",
        "oncotracer check --config",
        "oncotracer install --conda",
        "oncotracer quickstart 1",
        "--download-only",
        "oncotracer run --backend conda",
        "run_cna_classifier: true",
        "release-provenance.json",
    ),
    "docs/index.md": (
        "oncotracer setup --project",
        "oncotracer check --config",
        "oncotracer run --config",
        "v2.0.0 executable does not include",
        "native_architecture.md",
        "parity_release.md",
    ),
    "docs/installation.md": (
        "It does not require a Git clone after installation",
        "Five isolated, versioned environments are created",
        "core alignment",
        "qDNAseq with its pinned R 4.1 stack",
        "ichorCNA/HMMcopy with its pinned R 4.4 stack",
        "optional CNA classifier/report stack",
        "GISTIC2 for the optional recurrence branch",
        "oncotracer doctor --backend conda",
        "oncotracer doctor --backend docker",
        "oncotracer doctor --backend singularity",
        "$XDG_CACHE_HOME/oncotracer/2.0.0/<executable-sha256>/payload",
        "does not populate the persistent cache",
        "never adopts a populated Conda directory",
        "same-directory transaction",
        "backend-specific",
    ),
    "docs/quick_start.md": (
        "complete native analysis",
        "approximately 225 MB",
        "oncotracer quickstart 1",
        "--backend conda",
        "--backend docker",
        "--backend singularity",
        "03_cna_codification/cna_events.tsv",
        ".oncotracer-native/trace.tsv",
        "--download-only",
    ),
    "docs/public_cohort.md": (
        "all six paired-end FASTQs",
        "validates each exact size and MD5 checksum",
        "HCC1143_DMSO",
        "HCC1143_BEZ235",
        "HCC1143_TRAMETINIB",
        "SRR7085656",
        "SRR7085655",
        "SRR7085657",
        "oncotracer quickstart 2",
        "## Resume",
    ),
    "docs/auto_params.md": (
        "does not start the scientific analysis",
        "oncotracer auto",
        "oncotracer run --backend conda",
        "Normal rows are ordinary, independently analyzed qDNAseq samples",
        "does not pool them",
        "generated YAML contains no local-panel settings",
    ),
    "docs/configuration/ont.md": (
        "ont_normal_folder",
        "ont_normal_barcodes",
        "ont_normal_sample_names",
        "ont_analysis_type: solid_biopsy",
        "ont_caller: qdnaseq",
        "ont_binsize_kb: 100",
        "never pools, averages, or subtracts the NORMAL group",
        "version-selected upstream HD_ULP ichorCNA reference object",
    ),
    "docs/six_tumor_four_normal.md": (
        "runs ten paired-end Illumina samples in one analysis",
        "does not invoke Nextflow",
        "checksum-validated public-data",
        "oncotracer install --conda",
        "oncotracer doctor --backend conda",
        "ONCO001",
        "ONCO006",
        "CTRL001",
        "CTRL004",
        "Every sample is aligned, normalized, segmented, called, and reported",
        "does **not** pool the four `CTRL` samples",
        "--mode illumina",
        "--reads-folder",
        "--sample-table",
        "--config-dir",
        "--outdir",
        "Run or resume the one native analysis",
        "qdnaseq_sample_status.json",
        "completed_samples",
        "nextflow_used",
    ),
    "docs/running.md": (
        "Running the native workflow",
        "direct qDNAseq or direct HMMcopy/ichorCNA",
        "BAM-supported boundary refinement",
        "run_cna_classifier: true",
        "argument arrays rather than shell strings",
        "fails if a Nextflow invocation appears",
    ),
    "docs/configuration_v2.md": (
        "mode: illumina",
        "mode: ont",
        "run_cna_classifier: true",
        "run_gistic: true",
        "gistic_required: false",
        "knowledge_web: false",
        "Nested YAML is deliberately rejected",
    ),
    "docs/containers.md": (
        "All backends use the same native stage graph",
        "ghcr.io/cfarkas/oncotracer:2.0.0",
        "The five Conda groups are",
        "core",
        "qdnaseq",
        "ichorcna",
        "classifier",
        "gistic",
        "semantic tool/package probes",
        "login shell's",
    ),
    "docs/native_architecture.md": (
        "BWA and Picard for Illumina",
        "qDNAseq in its pinned R 4.1 environment",
        "HMMcopy and ichorCNA 0.5.1",
        ".nf",
        "file is present in the release executable or container",
        "exact Git commit and deterministic",
        "Normal v2 execution does not invoke Nextflow",
        "canonical path, file type, normalized mode, size, and SHA-256",
        "process-scoped temporary payload",
        "Installer ownership boundary",
        "authenticated rollback journal",
        "created directly at its final canonical prefix",
        "exact file inventory",
    ),
    "docs/parity_release.md": (
        "Illumina ERR12341627",
        "ONT DRR165691",
        "all three HCC1143 libraries",
        "six checksum-validated FASTQs",
        "same exact current",
        "oncotracer-v2.0.0-parity-audit.tar.gz",
        "SHA256SUMS",
    ),
    "docs/migration_v1_to_v2.md": (
        "nextflow run main.nf --conda -params-file run.yml -resume",
        "oncotracer run --backend conda --config run.yml",
        "never forwards that command to Nextflow",
        "immutable",
        "v1.1",
        "git -c tar.umask=0002 archive --format=tar <exact-commit>",
    ),
    "docs/citation_research_use.md": (
        "version 2.0.0",
        "oncotracer provenance --json",
        "not a standalone diagnostic system",
        "input file checksums and source accessions",
        "immutable image digest",
    ),
    "docs/troubleshooting.md": (
        "all five configured prefixes",
        "whether Nextflow is required",
        "false",
        "R_HOME",
        "R_LIBS_USER",
        "exact",
        "Rscript",
        "do not substitute a login-shell",
        "unset ONCOTRACER_PAYLOAD_CACHE",
        "preserves them rather than recursively deleting",
    ),
}

PARITY_REQUIREMENTS = (
    "identical analysis mode, dataset name, and sample set",
    "engine=native",
    "nextflow_used=false",
    "at least 0.80 reciprocal interval overlap",
    "state-specific CNA genomic-coverage recall and precision of at least 0.90",
    "at least 0.95 of the original corrected-bin coordinate grid shared exactly",
    "corrected input log₂-signal Pearson correlation of at least 0.98",
    "no greater than 0.08",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing documentation file: {relative_path}")
    return path.read_text(encoding="utf-8")


def check_required_native_content() -> None:
    for relative_path, snippets in REQUIRED_TEXT.items():
        text = read(relative_path)
        for snippet in snippets:
            if snippet not in text:
                fail(f"missing native v2 requirement in {relative_path}: {snippet}")


def check_native_runtime_boundary() -> None:
    forbidden = (
        "nextflow run",
        "carlosfarkas/oncotracer:latest",
        "docker://carlosfarkas/oncotracer:latest",
        "Every OncoTracer command starts with Nextflow",
        "Start OncoTracer through Nextflow",
    )
    for relative_path in ACTIVE_NATIVE_FILES:
        text = read(relative_path)
        for phrase in forbidden:
            if phrase.casefold() in text.casefold():
                fail(f"obsolete v1 runtime instruction in {relative_path}: {phrase}")

    installation_release = read("docs/installation.md").split("## 1. Install the stable copied executable", 1)[1].split("### Poetry", 1)[0]
    if "git clone" in installation_release:
        fail("global v2 release installation must not require a source checkout")

    removed_local_panel_interface = (
        "illumina_build_pon",
        "illumina_pon_",
        "ont_build_pon",
        "qdnaseq_local_pon",
        "normal_panel",
    )
    active_markdown = [
        str(path.relative_to(ROOT))
        for path in MARKDOWN_FILES
        if path.name not in {"legacy_v1.md", "migration_v1_to_v2.md"}
    ]
    public_files = sorted(
        {
            *active_markdown,
            "params/illumina.example.yml",
            "params/ont.example.yml",
        }
    )
    for relative_path in public_files:
        text = read(relative_path)
        for token in removed_local_panel_interface:
            if token.casefold() in text.casefold():
                fail(f"removed local-panel interface remains in {relative_path}: {token}")


def check_parity_contract() -> None:
    text = read("docs/parity_release.md")
    for requirement in PARITY_REQUIREMENTS:
        if requirement not in text:
            fail(f"parity contract is missing or changed: {requirement}")


def check_hcc1143_manifest() -> None:
    manifest = ROOT / "examples/hcc1143_lpwgs/manifest.tsv"
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 6:
        fail(f"HCC1143 manifest must contain six FASTQs, observed {len(rows)}")

    expected = {
        "HCC1143_DMSO": "SRR7085656",
        "HCC1143_BEZ235": "SRR7085655",
        "HCC1143_TRAMETINIB": "SRR7085657",
    }
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["sample_name"]].append(row)
        if row["run_accession"] != expected.get(row["sample_name"]):
            fail(f"unexpected sample/run mapping in HCC1143 manifest: {row}")
        if row["read"] not in {"R1", "R2"}:
            fail(f"unexpected read end in HCC1143 manifest: {row}")
        if not re.fullmatch(r"[0-9a-f]{32}", row["md5"]):
            fail(f"invalid MD5 in HCC1143 manifest: {row['filename']}")
        if not row["bytes"].isdigit() or int(row["bytes"]) <= 0:
            fail(f"invalid byte count in HCC1143 manifest: {row['filename']}")
        if not row["url"].startswith("https://ftp.sra.ebi.ac.uk/"):
            fail(f"non-ENA URL in HCC1143 manifest: {row['filename']}")
    if set(grouped) != set(expected):
        fail("HCC1143 manifest sample set changed")
    for sample_name, sample_rows in grouped.items():
        if {row["read"] for row in sample_rows} != {"R1", "R2"}:
            fail(f"{sample_name} does not have exactly one R1 and one R2")


def check_navigation() -> None:
    text = read("mkdocs.yml")
    for entry in (
        "Home: index.md",
        "QuickStart 1 — Illumina + ONT: quick_start.md",
        "QuickStart 2 — HCC1143: public_cohort.md",
        "Mock cohort — six tumors + four independent normals: six_tumor_four_normal.md",
        "Native architecture: native_architecture.md",
        "Parity and release gate: parity_release.md",
        "Migration from v1.1: migration_v1_to_v2.md",
        "Legacy v1.1: legacy_v1.md",
    ):
        if entry not in text:
            fail(f"mkdocs navigation is missing: {entry}")


def check_markdown_and_bash_syntax() -> None:
    for path in MARKDOWN_FILES:
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(ROOT)
        if text.count(FENCE) % 2:
            fail(f"unbalanced Markdown fences in {relative_path}")
        for index, block in enumerate(BASH_BLOCK_RE.findall(text), start=1):
            completed = subprocess.run(
                ["bash", "-n"],
                input=block,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or "unknown Bash parse error"
                fail(f"invalid Bash block {index} in {relative_path}: {detail}")


def main() -> None:
    check_required_native_content()
    check_native_runtime_boundary()
    check_parity_contract()
    check_hcc1143_manifest()
    check_navigation()
    check_markdown_and_bash_syntax()
    print(
        "PASS: native v2 docs preserve runtime isolation, public-input provenance, "
        "scientific parity thresholds, release identity, and valid commands"
    )


if __name__ == "__main__":
    main()
