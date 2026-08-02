#!/usr/bin/env python3
"""Verify the complete HCC1143 QuickStart Example 2 output set."""
from __future__ import annotations
import argparse
from pathlib import Path

EXPECTED = {"HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True, type=Path)
    outdir = parser.parse_args().outdir.expanduser().resolve()
    required = (
        outdir / "01_samurai_illumina/qdnaseq/all_segments.seg",
        outdir / "03_cna_codification/cna_events.tsv",
        outdir / "03_cna_codification/cna_cytogenomic_notation.tsv",
        outdir / "04_cna_custom_plots/cna_per_sample_pages.pdf",
        outdir / "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
        outdir / "06_workflow_summary/workflow_summary.txt",
    )
    problems: list[str] = []
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"missing or empty: {path}")
    bam_dir = outdir / "01_samurai_illumina/alignment"
    bams = {path.stem for path in bam_dir.glob("*.bam") if path.stat().st_size > 0}
    if bams != EXPECTED:
        problems.append(f"expected BAMs {sorted(EXPECTED)}, found {sorted(bams)}")
    if required[0].is_file() and required[0].stat().st_size > 0:
        text = required[0].read_text(encoding="utf-8", errors="replace")
        for sample in EXPECTED:
            if sample not in text:
                problems.append(f"sample absent from qDNAseq segments: {sample}")
    if required[-1].is_file() and required[-1].stat().st_size > 0:
        lines = set(required[-1].read_text(encoding="utf-8").splitlines())
        for marker in ("mode=illumina", "dataset=illumina_qdnaseq_100kb"):
            if marker not in lines:
                problems.append(f"workflow summary is missing {marker!r}")
    if problems:
        print("ERROR: HCC1143 verification failed.")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("SUCCESS: all three HCC1143 libraries produced the required outputs.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
