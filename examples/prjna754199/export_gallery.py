#!/usr/bin/env python3
"""Export a validated, deterministic PRJNA754199 documentation gallery.

This exporter is intentionally fail-closed.  It validates the complete public
12-run source set and all selected downstream artifacts before writing anything
to ``--assets-dir``.  The displayed sample is fixed to DDLPS_1b; the script
never substitutes a different sample based on the observed result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_ALIASES = (
    "DDLPS_1a",
    "DDLPS_1b",
    "DDLPS_1c",
    "DDLPS_2",
    "DDLPS_3a",
    "DDLPS_3b",
    "WDLPS_1a",
    "WDLPS_1b",
    "WDLPS_1c",
    "WDLPS_1d",
    "WDLPS_2",
    "WDLPS_3",
)
SELECTED_SAMPLE = "DDLPS_1b"

SEGMENT_PDF_ASSET = "prjna754199_samurai_ddlps1b_segment_plot.pdf"
SEGMENT_PNG_ASSET = "prjna754199_samurai_ddlps1b_segment_plot.png"
REFINEMENT_CSV_ASSET = "prjna754199_refinement_summary.csv"
REFINEMENT_PNG_ASSET = "prjna754199_refinement_summary.png"
INTERPRETATION_PDF_ASSET = "prjna754199_cna_interpretation.pdf"
INTERPRETATION_PNG_ASSET = "prjna754199_cna_interpretation.png"
PROVENANCE_ASSET = "gallery_provenance.tsv"


class ExportError(RuntimeError):
    """A source validation or export operation failed."""


def require_nonempty_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ExportError(f"missing or empty {label}: {path}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_table(path: Path, delimiter: str, label: str) -> tuple[list[str], list[dict[str, str]]]:
    require_nonempty_file(path, label)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise ExportError(f"{label} has no header: {path}")
    return fieldnames, rows


def validate_manifest(manifest: Path) -> tuple[str, ...]:
    fieldnames, rows = read_table(manifest, "\t", "frozen archive manifest")
    if "sample_alias" not in fieldnames:
        raise ExportError(f"manifest lacks sample_alias column: {manifest}")
    aliases = tuple((row.get("sample_alias") or "").strip() for row in rows)
    if aliases != EXPECTED_ALIASES:
        raise ExportError(
            "manifest aliases/order do not match the frozen 12-run public archive: "
            f"expected {EXPECTED_ALIASES}, found {aliases}"
        )
    if len(set(aliases)) != len(EXPECTED_ALIASES):
        raise ExportError("manifest contains duplicate sample aliases")
    return aliases


def parse_nonnegative_int(row: dict[str, str], column: str, sample: str) -> int:
    value = (row.get(column) or "").strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ExportError(
            f"refinement value is not an integer for {sample}, {column}: {value!r}"
        ) from exc
    if parsed < 0:
        raise ExportError(
            f"refinement value is negative for {sample}, {column}: {parsed}"
        )
    return parsed


def validate_refinement_summary(
    path: Path, aliases: tuple[str, ...]
) -> list[dict[str, Any]]:
    required_columns = {
        "sample",
        "n_prior_boundaries_evaluated",
        "n_boundaries_refined",
        "n_boundaries_kept_original",
        "n_boundaries_with_poor_bam_resolution",
    }
    fieldnames, rows = read_table(path, ",", "refinement summary")
    missing = required_columns - set(fieldnames)
    if missing:
        raise ExportError(
            f"refinement summary lacks column(s): {', '.join(sorted(missing))}"
        )
    if len(rows) != len(aliases):
        raise ExportError(
            f"refinement summary must contain exactly {len(aliases)} rows; found {len(rows)}"
        )

    by_sample: dict[str, dict[str, str]] = {}
    for row in rows:
        sample = (row.get("sample") or "").strip()
        if not sample:
            raise ExportError("refinement summary contains an empty sample ID")
        if sample in by_sample:
            raise ExportError(f"refinement summary contains duplicate sample: {sample}")
        by_sample[sample] = row

    if set(by_sample) != set(aliases):
        missing_samples = sorted(set(aliases) - set(by_sample))
        extra_samples = sorted(set(by_sample) - set(aliases))
        raise ExportError(
            "refinement sample set differs from manifest; "
            f"missing={missing_samples}, extra={extra_samples}"
        )

    validated: list[dict[str, Any]] = []
    for sample in aliases:
        row = by_sample[sample]
        evaluated = parse_nonnegative_int(
            row, "n_prior_boundaries_evaluated", sample
        )
        refined = parse_nonnegative_int(row, "n_boundaries_refined", sample)
        retained = parse_nonnegative_int(
            row, "n_boundaries_kept_original", sample
        )
        poor = parse_nonnegative_int(
            row, "n_boundaries_with_poor_bam_resolution", sample
        )
        if evaluated != refined + retained:
            raise ExportError(
                f"refinement invariant failed for {sample}: evaluated={evaluated}, "
                f"refined={refined}, retained={retained}"
            )
        if poor > retained:
            raise ExportError(
                f"refinement invariant failed for {sample}: poor_resolution={poor} "
                f"exceeds retained={retained}"
            )
        validated.append(
            {
                "sample": sample,
                "evaluated": evaluated,
                "refined": refined,
                "retained": retained,
                "poor": poor,
            }
        )
    return validated


def require_sample_once(
    path: Path,
    delimiter: str,
    label: str,
    sample: str,
) -> dict[str, str]:
    fieldnames, rows = read_table(path, delimiter, label)
    if "sample" not in fieldnames:
        raise ExportError(f"{label} lacks sample column: {path}")
    matches = [row for row in rows if (row.get("sample") or "").strip() == sample]
    if len(matches) != 1:
        raise ExportError(
            f"{label} must contain {sample} exactly once; found {len(matches)} row(s)"
        )
    return matches[0]


def verify_pdf(path: Path, label: str) -> int:
    require_nonempty_file(path, label)
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        raise ExportError("required command not found: pdfinfo")
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    completed = subprocess.run(
        [pdfinfo, str(path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ExportError(f"pdfinfo rejected {label} {path}: {detail}")
    pages_value = ""
    for line in completed.stdout.splitlines():
        if line.startswith("Pages:"):
            pages_value = line.split(":", 1)[1].strip()
            break
    try:
        pages = int(pages_value)
    except ValueError as exc:
        raise ExportError(f"pdfinfo did not report a valid page count for {path}") from exc
    if pages < 1:
        raise ExportError(f"PDF has no pages: {path}")
    return pages


def rasterize_first_page(source_pdf: Path, output_png: Path) -> None:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise ExportError("required command not found: pdftoppm")
    output_prefix = output_png.with_suffix("")
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    completed = subprocess.run(
        [
            pdftoppm,
            "-f",
            "1",
            "-l",
            "1",
            "-r",
            "144",
            "-png",
            "-singlefile",
            str(source_pdf),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ExportError(f"pdftoppm failed for {source_pdf}: {detail}")
    require_nonempty_file(output_png, "rasterized first-page PNG")


def render_refinement_summary(rows: list[dict[str, Any]], output_png: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ExportError("matplotlib is required to render refinement statistics") from exc

    samples = [str(row["sample"]) for row in rows]
    refined = [int(row["refined"]) for row in rows]
    retained = [int(row["retained"]) for row in rows]
    poor = [int(row["poor"]) for row in rows]
    evaluated = [int(row["evaluated"]) for row in rows]
    y_positions = list(range(len(samples)))

    style = {
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
    }
    with plt.rc_context(style):
        figure, (decisions_ax, poor_ax) = plt.subplots(
            1,
            2,
            figsize=(13.5, 7.6),
            sharey=True,
            gridspec_kw={"width_ratios": [4.5, 1.25], "wspace": 0.08},
        )
        decisions_ax.barh(
            y_positions,
            refined,
            color="#2f6f9f",
            edgecolor="white",
            linewidth=0.5,
            label="BAM-supported refined boundary",
        )
        decisions_ax.barh(
            y_positions,
            retained,
            left=refined,
            color="#aeb8c4",
            edgecolor="white",
            linewidth=0.5,
            label="Original boundary retained",
        )
        decisions_ax.set_yticks(y_positions, labels=samples)
        decisions_ax.invert_yaxis()
        decisions_ax.set_xlabel("Evaluated prior boundaries")
        decisions_ax.set_title("Boundary decision counts")
        decisions_ax.grid(axis="x", color="#dfe3e8", linewidth=0.7)
        decisions_ax.set_axisbelow(True)
        decisions_ax.spines[["top", "right"]].set_visible(False)
        decisions_ax.legend(loc="lower right", frameon=False)
        for y_position, total in zip(y_positions, evaluated):
            decisions_ax.text(
                total,
                y_position,
                f"  n={total}",
                va="center",
                ha="left",
                fontsize=7.5,
                color="#30343b",
            )
        left_max = max(evaluated, default=0)
        decisions_ax.set_xlim(0, max(1, left_max * 1.18 + 1))

        poor_ax.barh(
            y_positions,
            poor,
            color="#d95f02",
            edgecolor="white",
            linewidth=0.5,
        )
        poor_ax.set_xlabel("Count")
        poor_ax.set_title("Poor BAM resolution\n(subset of retained)")
        poor_ax.grid(axis="x", color="#dfe3e8", linewidth=0.7)
        poor_ax.set_axisbelow(True)
        poor_ax.spines[["top", "right", "left"]].set_visible(False)
        poor_ax.tick_params(axis="y", left=False, labelleft=False)
        poor_ax.set_xlim(0, max(1, max(poor, default=0) * 1.2 + 0.5))
        for y_position, value in zip(y_positions, poor):
            poor_ax.text(
                value,
                y_position,
                f"  {value}",
                va="center",
                ha="left",
                fontsize=7.5,
                color="#30343b",
            )

        figure.suptitle(
            "PRJNA754199 BAM-supported CNA boundary refinement",
            fontsize=14,
            fontweight="bold",
        )
        figure.text(
            0.5,
            0.012,
            "Poor BAM resolution is included within retained boundaries and is shown "
            "separately; it is not an additional outcome.",
            ha="center",
            va="bottom",
            fontsize=8.5,
            color="#30343b",
        )
        figure.tight_layout(rect=(0.0, 0.045, 1.0, 0.95))
        figure.savefig(
            output_png,
            dpi=180,
            facecolor="white",
            metadata={"Software": "OncoTracer export_gallery.py"},
        )
        plt.close(figure)
    require_nonempty_file(output_png, "refinement summary PNG")


def relative_to(path: Path, root: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ExportError(f"{label} is outside its required root: {path}") from exc


def write_provenance(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    fieldnames = [
        "asset_path",
        "asset_sha256",
        "source_path",
        "source_sha256",
        "transformation",
        "sample",
        "manifest_path",
        "manifest_sha256",
        "validation",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a completed 12-run PRJNA754199 workflow and export its "
            "documentation gallery."
        )
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        required=True,
        help="Completed OncoTracer PRJNA754199 output directory.",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        required=True,
        help="Destination directory for validated documentation assets.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outdir = args.outdir.expanduser().resolve()
    if not outdir.is_dir():
        raise ExportError(f"--outdir is not a directory: {outdir}")
    assets_dir = args.assets_dir.expanduser().resolve()

    script_dir = Path(__file__).resolve().parent
    repository_root = script_dir.parents[1]
    manifest = script_dir / "manifest.tsv"
    aliases = validate_manifest(manifest)

    plot_dir = outdir / "01_samurai_illumina" / "qdnaseq" / "plots"
    segment_pdfs: dict[str, Path] = {}
    for sample in aliases:
        segment_pdfs[sample] = require_nonempty_file(
            plot_dir / f"{sample}_segment_plot.pdf",
            f"SAMURAI fitted segment PDF for {sample}",
        )

    refinement_csv = (
        outdir
        / "02_bam_refinement"
        / "illumina_qdnaseq_100kb"
        / "01_tables"
        / "sample_refinement_summary.csv"
    )
    refinement_rows = validate_refinement_summary(refinement_csv, aliases)

    classifier_table = (
        outdir
        / "05_cna_classifier"
        / "02_classification"
        / "cna_patient_classification.tsv"
    )
    require_sample_once(
        classifier_table,
        "\t",
        "classifier table",
        SELECTED_SAMPLE,
    )

    clinician_dir = (
        outdir / "05_cna_classifier" / "03_report" / "clinician_reports"
    )
    clinician_index = clinician_dir / "clinician_report_index.tsv"
    clinician_row = require_sample_once(
        clinician_index,
        "\t",
        "clinician report index",
        SELECTED_SAMPLE,
    )
    expected_clinician_name = f"{SELECTED_SAMPLE}_clinical_driver_summary.pdf"
    indexed_pdf = (clinician_row.get("pdf") or "").strip()
    if indexed_pdf != expected_clinician_name:
        raise ExportError(
            f"clinician index PDF for {SELECTED_SAMPLE} must be "
            f"{expected_clinician_name!r}; found {indexed_pdf!r}"
        )
    clinician_pdf = require_nonempty_file(
        clinician_dir / expected_clinician_name,
        f"concise clinician PDF for {SELECTED_SAMPLE}",
    )

    selected_segment_pdf = segment_pdfs[SELECTED_SAMPLE]
    verify_pdf(selected_segment_pdf, "selected SAMURAI fitted segment PDF")
    verify_pdf(clinician_pdf, "selected concise clinician PDF")

    # All validations above finish before the destination directory is changed.
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gallery-export-", dir=assets_dir) as temp:
        staging = Path(temp)
        staged_segment_pdf = staging / SEGMENT_PDF_ASSET
        staged_segment_png = staging / SEGMENT_PNG_ASSET
        staged_refinement_csv = staging / REFINEMENT_CSV_ASSET
        staged_refinement_png = staging / REFINEMENT_PNG_ASSET
        staged_interpretation_pdf = staging / INTERPRETATION_PDF_ASSET
        staged_interpretation_png = staging / INTERPRETATION_PNG_ASSET
        staged_provenance = staging / PROVENANCE_ASSET

        shutil.copyfile(selected_segment_pdf, staged_segment_pdf)
        rasterize_first_page(staged_segment_pdf, staged_segment_png)
        shutil.copyfile(refinement_csv, staged_refinement_csv)
        render_refinement_summary(refinement_rows, staged_refinement_png)
        shutil.copyfile(clinician_pdf, staged_interpretation_pdf)
        rasterize_first_page(staged_interpretation_pdf, staged_interpretation_png)

        source_specs = [
            (
                staged_segment_pdf,
                selected_segment_pdf,
                "byte-for-byte copy",
                SELECTED_SAMPLE,
            ),
            (
                staged_segment_png,
                selected_segment_pdf,
                "pdftoppm first page, PNG, 144 dpi",
                SELECTED_SAMPLE,
            ),
            (
                staged_refinement_csv,
                refinement_csv,
                "byte-for-byte copy",
                "",
            ),
            (
                staged_refinement_png,
                refinement_csv,
                "matplotlib two-panel summary, 180 dpi",
                "",
            ),
            (
                staged_interpretation_pdf,
                clinician_pdf,
                "byte-for-byte copy",
                SELECTED_SAMPLE,
            ),
            (
                staged_interpretation_png,
                clinician_pdf,
                "pdftoppm first page, PNG, 144 dpi",
                SELECTED_SAMPLE,
            ),
        ]
        manifest_relative = relative_to(
            manifest, repository_root, "archive manifest"
        )
        manifest_digest = sha256(manifest)
        provenance_rows: list[dict[str, str]] = []
        for staged_asset, source, transformation, sample in source_specs:
            require_nonempty_file(staged_asset, "staged gallery asset")
            provenance_rows.append(
                {
                    "asset_path": staged_asset.name,
                    "asset_sha256": sha256(staged_asset),
                    "source_path": relative_to(source, outdir, "workflow source"),
                    "source_sha256": sha256(source),
                    "transformation": transformation,
                    "sample": sample,
                    "manifest_path": manifest_relative,
                    "manifest_sha256": manifest_digest,
                    "validation": "12_manifest_aliases_and_segment_pdfs_verified",
                }
            )
        write_provenance(staged_provenance, provenance_rows)
        require_nonempty_file(staged_provenance, "gallery provenance TSV")

        outputs = [
            staged_segment_pdf,
            staged_segment_png,
            staged_refinement_csv,
            staged_refinement_png,
            staged_interpretation_pdf,
            staged_interpretation_png,
            staged_provenance,
        ]
        for staged_asset in outputs:
            os.replace(staged_asset, assets_dir / staged_asset.name)

    print("Validated and exported PRJNA754199 gallery assets:")
    for name in (
        SEGMENT_PDF_ASSET,
        SEGMENT_PNG_ASSET,
        REFINEMENT_CSV_ASSET,
        REFINEMENT_PNG_ASSET,
        INTERPRETATION_PDF_ASSET,
        INTERPRETATION_PNG_ASSET,
        PROVENANCE_ASSET,
    ):
        print(f"  {assets_dir / name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
