#!/usr/bin/env python3
"""Verify the complete PRJNA754199 tutorial output against its frozen manifest."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Require exact PRJNA754199 samples and non-empty tutorial outputs."
    )
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().with_name("manifest.tsv"),
    )
    return parser.parse_args()


def require_nonempty(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"ERROR: expected output is missing or empty: {path}")
    return path


def require_exact(label: str, expected: set[str], observed: set[str]) -> None:
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        raise SystemExit(
            f"ERROR: {label} sample mismatch; "
            f"missing={missing}; unexpected={unexpected}"
        )
    print(f"VERIFIED: {label} contains all {len(observed)} manifest samples")


def require_one_per_sample(
    label: str, expected: set[str], observed_rows: list[str]
) -> None:
    counts = Counter(observed_rows)
    duplicates = sorted(sample for sample, count in counts.items() if count != 1)
    if duplicates:
        raise SystemExit(
            f"ERROR: {label} must contain one row per sample; "
            f"non-unique={duplicates}"
        )
    require_exact(label, expected, set(observed_rows))


def main() -> None:
    args = parse_args()
    outdir = args.outdir.resolve()
    manifest = args.manifest.resolve()
    require_nonempty(manifest)
    if not outdir.is_dir():
        raise SystemExit(f"ERROR: output directory not found: {outdir}")

    with manifest.open(newline="") as handle:
        manifest_rows = list(csv.DictReader(handle, delimiter="\t"))
    ordered_samples = [row["sample_alias"] for row in manifest_rows]
    expected = set(ordered_samples)
    if len(ordered_samples) != 12 or len(expected) != 12:
        raise SystemExit("ERROR: frozen manifest must contain 12 unique sample aliases")

    segment_table = require_nonempty(
        outdir / "01_samurai_illumina" / "qdnaseq" / "all_segments.seg"
    )
    refinement_summary = require_nonempty(
        outdir
        / "02_bam_refinement"
        / "illumina_qdnaseq_100kb"
        / "01_tables"
        / "sample_refinement_summary.csv"
    )
    classification_table = require_nonempty(
        outdir
        / "05_cna_classifier"
        / "02_classification"
        / "cna_patient_classification.tsv"
    )
    report_root = outdir / "05_cna_classifier" / "03_report"
    clinician_dir = report_root / "clinician_reports"
    clinician_index = require_nonempty(clinician_dir / "clinician_report_index.tsv")

    required_outputs = [
        outdir
        / "02_bam_refinement"
        / "illumina_qdnaseq_100kb"
        / "04_final_results"
        / "final_segments.tsv",
        outdir / "03_cna_codification" / "cna_events.tsv",
        outdir / "03_cna_codification" / "cna_cytogenomic_notation.tsv",
        outdir / "04_cna_custom_plots" / "cna_per_sample_pages.pdf",
        outdir
        / "04_cna_custom_plots"
        / "cna_log2_ratio_profiles_all_samples.pdf",
        report_root / "cna_classifier_report.html",
        outdir / "06_workflow_summary" / "workflow_summary.txt",
    ]
    for path in required_outputs:
        require_nonempty(path)

    bam_dir = outdir / "01_samurai_illumina" / "alignment"
    bam_paths = list(bam_dir.glob("*.bam"))
    for path in bam_paths:
        require_nonempty(path)
    require_exact("BAM outputs", expected, {path.stem for path in bam_paths})

    with segment_table.open(newline="") as handle:
        segment_ids = {
            row["ID"] for row in csv.DictReader(handle, delimiter="\t")
        }
    segment_samples: set[str] = set()
    for value in segment_ids:
        matches = [
            sample
            for sample in expected
            if value == sample or value.startswith(sample + "_")
        ]
        if not matches:
            raise SystemExit(f"ERROR: unrecognized SAMURAI segment ID: {value}")
        segment_samples.add(max(matches, key=len))
    require_exact("SAMURAI segments", expected, segment_samples)

    plot_dir = outdir / "01_samurai_illumina" / "qdnaseq" / "plots"
    plot_suffix = "_segment_plot.pdf"
    plot_samples = {
        path.name[: -len(plot_suffix)]
        for path in plot_dir.glob("*_segment_plot.pdf")
        if path.stat().st_size > 0
    }
    require_exact("SAMURAI fitted plots", expected, plot_samples)

    with refinement_summary.open(newline="") as handle:
        refinement_rows = list(csv.DictReader(handle))
    require_one_per_sample(
        "refinement summary",
        expected,
        [row["sample"] for row in refinement_rows],
    )

    with classification_table.open(newline="") as handle:
        classification_rows = list(csv.DictReader(handle, delimiter="\t"))
    require_one_per_sample(
        "classifier table",
        expected,
        [row["sample"] for row in classification_rows],
    )

    with clinician_index.open(newline="") as handle:
        clinician_rows = list(csv.DictReader(handle, delimiter="\t"))
    require_one_per_sample(
        "clinician report index",
        expected,
        [row["sample"] for row in clinician_rows],
    )
    for row in clinician_rows:
        if row.get("agreement_call") != "PATHOLOGY_NOT_PROVIDED":
            raise SystemExit(
                "ERROR: this pathology-free tutorial expected "
                f"PATHOLOGY_NOT_PROVIDED for {row['sample']}"
            )
        require_nonempty(clinician_dir / row["html"])
        require_nonempty(clinician_dir / row["pdf"])

    print("SUCCESS: complete PRJNA754199 tutorial outputs are verified.")
    print(f"Summary: {outdir / '06_workflow_summary' / 'workflow_summary.txt'}")
    print(f"Classifier report: {report_root / 'cna_classifier_report.html'}")


if __name__ == "__main__":
    main()
