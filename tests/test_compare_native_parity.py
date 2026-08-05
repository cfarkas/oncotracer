#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = ROOT / "tests" / "compare_native_parity.py"
PROFILE_RELATIVE = Path(
    "02_bam_refinement/illumina_qdnaseq_100kb/01_tables/refined_bins.tsv.gz"
)


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
    with gzip.open(root / PROFILE_RELATIVE, "wt") as handle:
        writer = csv.DictWriter(handle, ["sample", "chrom", "start", "end", "log2"], delimiter="\t")
        writer.writeheader()
        for index, value in enumerate((0.1, 0.2, 0.3, 0.4)):
            writer.writerow({"sample": "S1", "chrom": "chr1", "start": index * 100, "end": (index + 1) * 100, "log2": value})
    if native:
        (root / ".oncotracer-native").mkdir()
        (root / ".oncotracer-native/trace.tsv").write_text("stage\tcommand\nrun\tRscript native.R\n")


def comparator_command(
    v1: Path,
    v2: Path,
    report: Path,
    *,
    expected_samples: str | None = "S1",
) -> list[str | Path]:
    command: list[str | Path] = [
        sys.executable,
        COMPARATOR,
        "--v1",
        v1,
        "--v2",
        v2,
        "--outdir",
        report,
        "--label",
        "test",
    ]
    if expected_samples is not None:
        command.extend(["--expected-samples", expected_samples])
    return command


def run_comparator(
    v1: Path,
    v2: Path,
    report: Path,
    *,
    expected_samples: str | None = "S1",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        comparator_command(v1, v2, report, expected_samples=expected_samples),
        check=False,
        capture_output=True,
        text=True,
    )


def report_payload(report: Path) -> dict[str, object]:
    return json.loads((report / "parity_report.json").read_text(encoding="utf-8"))


def empty_event_table(root: Path) -> None:
    with (root / "03_cna_codification/cna_events.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            ["sample", "state", "chrom", "start", "end", "mean_log2"],
            delimiter="\t",
        )
        writer.writeheader()


class ParityComparatorTests(unittest.TestCase):
    def test_identical_semantic_outputs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((report / "parity_report.json").is_file())
            self.assertTrue((report / "SHA256SUMS").is_file())
            payload = report_payload(report)
            self.assertEqual(payload["expected_samples"], ["S1"])
            self.assertEqual(
                payload["thresholds"],
                {
                    "minimum_event_overlap": 0.80,
                    "minimum_event_recall": 0.90,
                    "minimum_event_precision": 0.90,
                    "minimum_profile_correlation": 0.98,
                    "maximum_profile_median_absolute_difference": 0.08,
                    "minimum_shared_bin_fraction": 0.95,
                },
            )
            self.assertTrue(payload["checks"]["sample_set_expected_v1"])
            self.assertTrue(payload["checks"]["sample_set_expected_v2"])
            self.assertTrue(payload["checks"]["event_sets_nonempty"])
            self.assertTrue(payload["checks"]["event_overlap"])
            self.assertEqual(payload["events"]["minimum_reciprocal_overlap"], 1.0)
            self.assertEqual(payload["events"]["median_reciprocal_overlap"], 1.0)
            for version, run_root in (("v1", v1), ("v2", v2)):
                profile = run_root / PROFILE_RELATIVE
                record = payload["profile_inputs"][version]
                self.assertEqual(record["path"], str(profile.resolve()))
                self.assertEqual(record["bytes"], profile.stat().st_size)
                self.assertEqual(
                    record["sha256"], hashlib.sha256(profile.read_bytes()).hexdigest()
                )

    def test_expected_samples_argument_is_required_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)

            missing = run_comparator(v1, v2, report, expected_samples=None)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("--expected-samples", missing.stderr)

            empty = run_comparator(v1, v2, report, expected_samples="")
            self.assertEqual(empty.returncode, 2)
            self.assertIn("must contain at least one sample", empty.stderr)

            duplicate = run_comparator(v1, v2, report, expected_samples="S1,S1")
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("duplicate sample IDs: S1", duplicate.stderr)

    def test_equal_observed_sets_fail_when_the_expected_set_differs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            completed = run_comparator(v1, v2, report, expected_samples="S2")

            self.assertEqual(completed.returncode, 1, completed.stderr)
            payload = report_payload(report)
            self.assertFalse(payload["passed"])
            self.assertTrue(payload["checks"]["sample_set_equal"])
            self.assertFalse(payload["checks"]["sample_set_expected_v1"])
            self.assertFalse(payload["checks"]["sample_set_expected_v2"])
            self.assertEqual(payload["expected_samples"], ["S2"])

    def test_empty_mode_and_dataset_cannot_pass_by_equality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            for run_root in (v1, v2):
                summary = run_root / "06_workflow_summary/workflow_summary.txt"
                text = summary.read_text(encoding="utf-8")
                summary.write_text(
                    text.replace("mode=illumina", "mode=").replace(
                        "dataset=illumina_qdnaseq_100kb", "dataset="
                    ),
                    encoding="utf-8",
                )

            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            payload = report_payload(report)
            self.assertFalse(payload["passed"])
            self.assertFalse(payload["checks"]["mode_nonempty"])
            self.assertFalse(payload["checks"]["dataset_nonempty"])
            self.assertTrue(payload["checks"]["mode_equal"])
            self.assertTrue(payload["checks"]["dataset_equal"])

    def test_empty_event_tables_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            empty_event_table(v1)
            empty_event_table(v2)

            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            payload = report_payload(report)
            self.assertFalse(payload["passed"])
            self.assertFalse(payload["checks"]["event_sets_nonempty"])
            self.assertFalse(payload["checks"]["event_overlap"])
            self.assertFalse(payload["checks"]["event_recall"])
            self.assertFalse(payload["checks"]["event_precision"])
            self.assertEqual(payload["events"]["v1_events"], 0)
            self.assertEqual(payload["events"]["v2_events"], 0)
            self.assertEqual(payload["events"]["minimum_reciprocal_overlap"], 0.0)


if __name__ == "__main__":
    unittest.main()
