#!/usr/bin/env python3
"""Verify the public Illumina and ONT QuickStart result sets."""
from __future__ import annotations
import argparse
from pathlib import Path

SUMMARY_MARKERS = {
    "Illumina": ("mode=illumina", "dataset=illumina_qdnaseq_100kb"),
    "ONT": ("mode=ont", "dataset=ONT_ichorcna_500kb"),
}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check required outputs from QuickStart Example 1.")
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--illumina-outdir", type=Path)
    parser.add_argument("--ont-outdir", type=Path)
    return parser.parse_args()

def verify(label: str, outdir: Path) -> list[str]:
    required = (
        outdir / "06_workflow_summary/workflow_summary.txt",
        outdir / "03_cna_codification/cna_events.tsv",
        outdir / "03_cna_codification/cna_cytogenomic_notation.tsv",
        outdir / "04_cna_custom_plots/cna_per_sample_pages.pdf",
        outdir / "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
    )
    problems: list[str] = []
    for output in required:
        if not output.is_file() or output.stat().st_size == 0:
            problems.append(f"missing or empty: {output}")
    summary = required[0]
    if summary.is_file() and summary.stat().st_size > 0:
        try:
            lines = set(summary.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError) as error:
            problems.append(f"could not read {summary}: {error}")
        else:
            for marker in SUMMARY_MARKERS[label]:
                if marker not in lines:
                    problems.append(f"{summary} does not contain {marker!r}")
    return problems

def main() -> int:
    args = parse_args()
    root = args.test_root.expanduser().resolve()
    illumina = (args.illumina_outdir or root / "runs/illumina").expanduser().resolve()
    ont = (args.ont_outdir or root / "runs/ont").expanduser().resolve()
    problems = verify("Illumina", illumina) + verify("ONT", ont)
    if problems:
        print("ERROR: QuickStart output verification failed.")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("SUCCESS: both QuickStart workflows completed and required outputs were found.")
    print(f"Illumina summary: {illumina / '06_workflow_summary/workflow_summary.txt'}")
    print(f"ONT summary:      {ont / '06_workflow_summary/workflow_summary.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
