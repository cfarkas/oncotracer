#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
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

    def test_event_gate_uses_state_specific_coverage_not_fragment_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)

            def write_events(run_root: Path, intervals: list[tuple[int, int]]) -> None:
                with (run_root / "03_cna_codification/cna_events.tsv").open("w", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        ["sample", "state", "chrom", "start", "end", "mean_log2"],
                        delimiter="	",
                    )
                    writer.writeheader()
                    for start, end in intervals:
                        writer.writerow(
                            {
                                "sample": "S1",
                                "state": "gain",
                                "chrom": "1",
                                "start": start,
                                "end": end,
                                "mean_log2": 0.5,
                            }
                        )

            reference = [(0, 1000), (2000, 3000), (4000, 5000), (6000, 7000), (8000, 8001)]
            candidate = reference[:-1]
            write_events(v1, reference)
            write_events(v2, candidate)
            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = report_payload(report)
            self.assertEqual(payload["events"]["recall"], 0.8)
            self.assertGreater(payload["events"]["coverage_recall"], 0.99)
            self.assertTrue(payload["checks"]["event_recall"])

    def test_profile_gate_uses_original_coordinates_and_input_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)

            def write_profile(run_root: Path, shift: int, reverse_final: bool) -> None:
                values = [0.1, 0.2, 0.3, 0.4]
                with gzip.open(run_root / PROFILE_RELATIVE, "wt") as handle:
                    writer = csv.DictWriter(
                        handle,
                        [
                            "sample", "chrom", "start", "end",
                            "original_bin_start", "original_bin_end",
                            "input_log2", "final_log2",
                        ],
                        delimiter="	",
                    )
                    writer.writeheader()
                    finals = list(reversed(values)) if reverse_final else values
                    for index, (input_value, final_value) in enumerate(zip(values, finals, strict=True)):
                        original_start = index * 100
                        original_end = (index + 1) * 100
                        writer.writerow(
                            {
                                "sample": "S1",
                                "chrom": "chr1",
                                "start": original_start + shift,
                                "end": original_end + shift,
                                "original_bin_start": original_start,
                                "original_bin_end": original_end,
                                "input_log2": input_value,
                                "final_log2": final_value,
                            }
                        )

            write_profile(v1, shift=0, reverse_final=False)
            write_profile(v2, shift=7, reverse_final=True)
            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = report_payload(report)
            self.assertEqual(payload["profiles"]["shared_bins"], 4)
            self.assertAlmostEqual(payload["profiles"]["pearson"], 1.0)

    def test_combined_trace_accepts_contract_rows_and_ignores_unrelated_tasks(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "verify_nested_samurai", ROOT / "tests" / "verify_nested_samurai.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "tests"))
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.tsv"
            rows = [
                {
                    "task_id": "1",
                    "hash": "a",
                    "name": "DINCALCILAB_SAMURAI:SAMURAI:SAMTOOLS_INDEX (S1)",
                    "status": "COMPLETED",
                    "exit": "0",
                    "container": "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
                },
                {
                    "task_id": "2",
                    "hash": "b",
                    "name": "DINCALCILAB_SAMURAI:SAMURAI:FASTA_INDEX_DNA:BWAMEM1_INDEX (genome.fa)",
                    "status": "COMPLETED",
                    "exit": "0",
                    "container": "unrelated/image:latest",
                },
            ]
            with trace.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, rows[0].keys(), delimiter="	")
                writer.writeheader()
                writer.writerows(rows)
            image = "quay.io/biocontainers/samtools:1.22.1--h96c455f_0"
            contract = module.Contract(
                label="test",
                root_arg="root",
                expected_rows=1,
                processes=frozenset({"SAMURAI:SAMTOOLS_INDEX"}),
                images=frozenset({image}),
            )
            ok, reason, selected, images = module.evaluate_trace(
                trace, contract, {image: "sha256:" + "0" * 64}
            )
            self.assertTrue(ok, reason)
            self.assertEqual(len(selected), 1)
            self.assertEqual(images, {image})

    def test_sealed_audit_accepts_only_the_exact_ont_resume_contract(self) -> None:
        verify_spec = importlib.util.spec_from_file_location(
            "verify_nested_samurai", ROOT / "tests" / "verify_nested_samurai.py"
        )
        self.assertIsNotNone(verify_spec)
        self.assertIsNotNone(verify_spec.loader)
        verify_module = importlib.util.module_from_spec(verify_spec)
        sys.path.insert(0, str(ROOT / "tests"))
        sys.modules[verify_spec.name] = verify_module
        try:
            verify_spec.loader.exec_module(verify_module)
            audit_spec = importlib.util.spec_from_file_location(
                "parity_audit", ROOT / "tests" / "parity_audit.py"
            )
            self.assertIsNotNone(audit_spec)
            self.assertIsNotNone(audit_spec.loader)
            audit_module = importlib.util.module_from_spec(audit_spec)
            sys.modules[audit_spec.name] = audit_module
            audit_spec.loader.exec_module(audit_module)
        finally:
            sys.modules.pop("parity_audit", None)
            sys.modules.pop(verify_spec.name, None)
            sys.path.pop(0)

        full_contract = verify_module.CONTRACTS["quickstart1"][1]
        process_rows = (
            (
                "SAMURAI:SAMTOOLS_INDEX",
                "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
            ),
            (
                "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
                "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
            ),
            (
                "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
                "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
                "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
            ),
        )
        pins = {image: "sha256:" + format(index + 1, "064x") for index, image in enumerate(full_contract.images)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact = root / "exact.tsv"
            with exact.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    ["task_id", "hash", "name", "status", "exit", "container"],
                    delimiter="\t",
                )
                writer.writeheader()
                for index, (process, image) in enumerate(process_rows, start=1):
                    writer.writerow(
                        {
                            "task_id": index,
                            "hash": f"hash-{index}",
                            "name": f"DINCALCILAB_SAMURAI:{process} (DRR165691)",
                            "status": "COMPLETED",
                            "exit": "0",
                            "container": image,
                        }
                    )
            self.assertEqual(
                audit_module.verify_trace(exact, full_contract, pins),
                "exact-ont-final-resume-trace",
            )

            incomplete = root / "incomplete.tsv"
            rows = list(csv.DictReader(exact.open(newline=""), delimiter="\t"))[:-1]
            with incomplete.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, rows[0].keys(), delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(audit_module.AuditError):
                audit_module.verify_trace(incomplete, full_contract, pins)


if __name__ == "__main__":
    unittest.main()
