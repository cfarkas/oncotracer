#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = ROOT / "tests" / "compare_native_parity.py"


def write_run(root: Path, *, native: bool) -> None:
    for relative in (
        "06_workflow_summary",
        "03_cna_codification",
        "04_cna_custom_plots",
        "02_bam_refinement/illumina_qdnaseq_100kb/01_tables",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    summary = [
        "mode=illumina",
        "dataset=illumina_qdnaseq_100kb",
        f"engine={'native' if native else 'nextflow'}",
        f"nextflow_used={'false' if native else 'true'}",
    ]
    (root / "06_workflow_summary/workflow_summary.txt").write_text("\n".join(summary) + "\n")
    with (root / "03_cna_codification/cna_events.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, ["sample", "state", "chrom", "start", "end", "mean_log2"], delimiter="\t")
        writer.writeheader()
        writer.writerow({"sample": "S1", "state": "gain", "chrom": "1", "start": 0, "end": 100, "mean_log2": 0.5})
    with (root / "03_cna_codification/cna_cytogenomic_notation.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, ["sample", "n_cna_events", "molecular_cytogenomic_notation", "cna_shorthand", "caller"], delimiter="\t")
        writer.writeheader()
        writer.writerow({"sample": "S1", "n_cna_events": 1, "molecular_cytogenomic_notation": "x", "cna_shorthand": "x", "caller": "qdnaseq"})
    for name in ("cna_per_sample_pages.pdf", "cna_log2_ratio_profiles_all_samples.pdf"):
        (root / "04_cna_custom_plots" / name).write_bytes(b"%PDF-1.4\n")
    with gzip.open(root / "02_bam_refinement/illumina_qdnaseq_100kb/01_tables/refined_bins.tsv.gz", "wt") as handle:
        writer = csv.DictWriter(handle, ["sample", "chrom", "start", "end", "log2"], delimiter="\t")
        writer.writeheader()
        for index, value in enumerate((0.1, 0.2, 0.3, 0.4)):
            writer.writerow({"sample": "S1", "chrom": "chr1", "start": index * 100, "end": (index + 1) * 100, "log2": value})
    if native:
        (root / ".oncotracer-native").mkdir()
        (root / ".oncotracer-native/trace.tsv").write_text("stage\tcommand\nrun\tRscript native.R\n")


class ParityComparatorTests(unittest.TestCase):
    def test_identical_semantic_outputs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            completed = subprocess.run(
                [sys.executable, COMPARATOR, "--v1", v1, "--v2", v2, "--outdir", report, "--label", "test"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((report / "parity_report.json").is_file())
            self.assertTrue((report / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
