#!/usr/bin/env python3
"""Verify the small public Illumina and ONT QuickStart result sets."""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_OUTPUTS = {
    "Illumina": (
        "runs/illumina/06_workflow_summary/workflow_summary.txt",
        "runs/illumina/03_cna_codification/cna_events.tsv",
        "runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf",
    ),
    "ONT": (
        "runs/ont/06_workflow_summary/workflow_summary.txt",
        "runs/ont/03_cna_codification/cna_events.tsv",
        "runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf",
    ),
}

SUMMARY_MARKERS = {
    "Illumina": ("mode=illumina", "dataset=illumina_qdnaseq_100kb"),
    "ONT": ("mode=ont", "dataset=ONT_ichorcna_500kb"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the required outputs from QuickStart Example 1."
    )
    parser.add_argument(
        "--test-root",
        type=Path,
        required=True,
        help="Folder supplied to --test_root when the QuickStart data were prepared.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    test_root = args.test_root.expanduser().resolve()
    problems: list[str] = []

    for label, relative_paths in REQUIRED_OUTPUTS.items():
        for relative_path in relative_paths:
            output = test_root / relative_path
            if not output.is_file() or output.stat().st_size == 0:
                problems.append(f"missing or empty: {output}")

        summary = test_root / relative_paths[0]
        if summary.is_file() and summary.stat().st_size > 0:
            try:
                summary_lines = set(summary.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeError) as error:
                problems.append(f"could not read {summary}: {error}")
                continue
            for marker in SUMMARY_MARKERS[label]:
                if marker not in summary_lines:
                    problems.append(f"{summary} does not contain the line {marker!r}")

    if problems:
        print("ERROR: QuickStart output verification failed.")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    illumina_summary = test_root / REQUIRED_OUTPUTS["Illumina"][0]
    ont_summary = test_root / REQUIRED_OUTPUTS["ONT"][0]
    print("SUCCESS: both QuickStart workflows completed and required outputs were found.")
    print(f"Illumina summary: {illumina_summary}")
    print(f"ONT summary:      {ont_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
