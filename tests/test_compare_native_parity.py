#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import os
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


def write_profile_values(root: Path, values: list[float]) -> None:
    with gzip.open(root / PROFILE_RELATIVE, "wt") as handle:
        writer = csv.DictWriter(
            handle,
            ["sample", "chrom", "start", "end", "log2"],
            delimiter="\t",
        )
        writer.writeheader()
        for index, value in enumerate(values):
            writer.writerow(
                {
                    "sample": "S1",
                    "chrom": "chr1",
                    "start": index * 100,
                    "end": (index + 1) * 100,
                    "log2": value,
                }
            )


class ParityComparatorTests(unittest.TestCase):
    def test_audit_manifest_uses_posix_lexicographic_path_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "tree"
            (root / "prefix").mkdir(parents=True)
            (root / "prefix/child.txt").write_text("child\n", encoding="utf-8")
            (root / "prefix.txt").write_text("peer\n", encoding="utf-8")
            manifest = workspace / "manifest.tsv"

            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "tests/parity_audit.py",
                    "manifest",
                    root,
                    manifest,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with manifest.open(newline="", encoding="utf-8") as handle:
                paths = [row["path"] for row in csv.DictReader(handle, delimiter="\t")]
            self.assertEqual(paths, sorted(paths))

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
            self.assertTrue(payload["checks"]["profile_usable_bins"])
            self.assertEqual(payload["profiles"]["shared_bins"], 4)
            self.assertEqual(payload["profiles"]["usable_shared_bins"], 4)
            self.assertEqual(payload["profiles"]["excluded_ieee_log2_floor_bins"], 0)
            self.assertEqual(
                (report / "profile_floor_exclusions.tsv").read_text(encoding="utf-8"),
                "\n",
            )
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

    def test_exact_qdnaseq_zero_floor_does_not_poison_pearson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            write_profile_values(v1, [-1022.0000000001, 0.2, 0.3, 0.4])
            write_profile_values(v2, [0.1, 0.2, 0.3, 0.4])

            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = report_payload(report)
            profiles = payload["profiles"]
            self.assertEqual(profiles["shared_bins"], 4)
            self.assertEqual(profiles["usable_shared_bins"], 3)
            self.assertEqual(profiles["excluded_ieee_log2_floor_bins"], 1)
            self.assertEqual(profiles["discordant_ieee_log2_floor_bins"], 1)
            self.assertAlmostEqual(profiles["pearson"], 1.0)
            self.assertTrue(payload["checks"]["profile_correlation"])
            with (report / "profile_floor_exclusions.tsv").open(newline="") as handle:
                exclusions = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(exclusions), 1)
            self.assertEqual(exclusions[0]["sample"], "S1")
            self.assertEqual(exclusions[0]["v1_ieee_log2_floor"], "True")
            self.assertEqual(exclusions[0]["v2_ieee_log2_floor"], "False")

    def test_broad_finite_profile_discordance_still_fails_correlation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            write_profile_values(v1, [0.1, 0.2, 0.3, 0.4])
            write_profile_values(v2, [0.1, 0.3, 0.2, 0.4])

            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            payload = report_payload(report)
            profiles = payload["profiles"]
            self.assertEqual(profiles["excluded_ieee_log2_floor_bins"], 0)
            self.assertAlmostEqual(profiles["pearson"], 0.8)
            self.assertFalse(payload["checks"]["profile_correlation"])
            self.assertTrue(payload["checks"]["profile_median_difference"])

    def test_no_usable_bins_fails_with_a_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1, v2, report = root / "v1", root / "v2", root / "report"
            write_run(v1, native=False)
            write_run(v2, native=True)
            write_profile_values(v1, [-1022.0] * 4)
            write_profile_values(v2, [-1022.0] * 4)

            completed = run_comparator(v1, v2, report)
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            payload = report_payload(report)
            profiles = payload["profiles"]
            self.assertEqual(profiles["shared_bins"], 4)
            self.assertEqual(profiles["usable_shared_bins"], 0)
            self.assertEqual(profiles["excluded_ieee_log2_floor_bins"], 4)
            self.assertIsNone(profiles["pearson"])
            self.assertFalse(payload["checks"]["profile_usable_bins"])
            self.assertFalse(payload["checks"]["profile_correlation"])
            self.assertFalse(payload["checks"]["profile_median_difference"])
            self.assertIn(
                "Pearson correlation of corrected input log2 signal: not available",
                (report / "parity_report.md").read_text(encoding="utf-8"),
            )

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
                    "hash": "aa/000001",
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

    def test_incomplete_ont_trace_with_valid_marker_is_rejected_and_complete_trace_binds_marker(
        self,
    ) -> None:
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

        contract = verify_module.CONTRACTS["quickstart1"][1]
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
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:ICHORCNA_RUN",
                "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:AGGREGATE_ICHORCNA_TABLE",
                "quay.io/einar_rainhart/pandas-pandera:1.5.3",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CORRECT_LOGR_ICHORCNA",
                "community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:PLOT_ICHORCNA",
                "community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CONCATENATE_BIN_PLOTS",
                "docker.io/t0shy/qpdf-docker:11.3.0",
            ),
            (
                "SAMURAI:MULTIQC",
                "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
            ),
        )
        pins = {
            image: "sha256:" + format(index + 1, "064x")
            for index, image in enumerate(contract.images)
        }

        def write_trace(path: Path, rows: tuple[tuple[str, str], ...]) -> None:
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    ["task_id", "hash", "name", "status", "exit", "container"],
                    delimiter="\t",
                )
                writer.writeheader()
                for index, (process, image) in enumerate(rows, start=1):
                    writer.writerow(
                        {
                            "task_id": index,
                            "hash": f"aa/{index:06x}",
                            "name": f"DINCALCILAB_SAMURAI:{process} (DRR165691)",
                            "status": "COMPLETED",
                            "exit": "0",
                            "container": image,
                        }
                    )

        marker_text = (
            "key\tvalue\n"
            "schema\toncotracer-ichorcna-plot-compat-v1\n"
            "status\tpatched\n"
            "target_quantile_calls\t2\n"
            "zero_median_plot_guard\tplaceholder\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_marker = (
                root
                / "work"
                / "ff"
                / "ffffff-stale"
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            stale_marker.parent.mkdir(parents=True)
            stale_marker.write_text(marker_text, encoding="utf-8")

            four_row_trace = root / "four-row.tsv"
            write_trace(four_row_trace, process_rows[:4])
            ok, reason, four_rows, _ = verify_module.evaluate_trace(
                four_row_trace, contract, pins
            )
            self.assertFalse(ok, reason)
            with self.assertRaises(audit_module.AuditError):
                audit_module.verify_trace(four_row_trace, contract, pins)
            with self.assertRaises(SystemExit):
                verify_module.find_compat_marker(root, four_rows)

            complete_trace = root / "complete.tsv"
            write_trace(complete_trace, process_rows)
            ok, reason, selected_rows, images = verify_module.evaluate_trace(
                complete_trace, contract, pins
            )
            self.assertTrue(ok, reason)
            self.assertEqual(images, set(contract.images))
            self.assertEqual(
                audit_module.verify_trace(complete_trace, contract, pins),
                "complete-combined-trace",
            )

            ichor_hash = verify_module.require_ichorcna_task_hash(selected_rows)
            prefix, suffix = ichor_hash.split("/", 1)
            full_suffix = suffix + "a" * (30 - len(suffix))
            bound_marker = (
                root
                / "work"
                / prefix
                / full_suffix
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            bound_marker.parent.mkdir(parents=True)
            bound_marker.write_text(marker_text, encoding="utf-8")
            marker, metadata, observed_hash, relative = (
                verify_module.find_compat_marker(root, selected_rows)
            )
            self.assertEqual(marker, bound_marker)
            self.assertEqual(metadata["status"], "patched")
            self.assertEqual(observed_hash, ichor_hash)
            self.assertTrue(
                verify_module.marker_path_matches_task_hash(relative, ichor_hash)
            )

            marker_name = "nested-v1-ont-ichorcna-plot-compat.tsv"
            marker_copy = root / marker_name
            marker_copy.write_text(marker_text, encoding="utf-8")
            marker_row = [
                contract.label + "-ichorcna-compat",
                "",
                "",
                f"task-hash:{ichor_hash};marker:{relative.as_posix()}",
                marker_name,
                audit_module.sha256(marker_copy),
            ]
            metadata = audit_module.verify_compat_selection(
                root,
                complete_trace,
                contract,
                {marker_row[0]: marker_row},
            )
            self.assertEqual(metadata["status"], "patched")

            wrong_hash_row = list(marker_row)
            wrong_hash_row[3] = (
                f"task-hash:ff/ffffff;marker:{relative.as_posix()}"
            )
            with self.assertRaises(audit_module.AuditError):
                audit_module.verify_compat_selection(
                    root,
                    complete_trace,
                    contract,
                    {wrong_hash_row[0]: wrong_hash_row},
                )

    def test_combiner_rejects_newer_failed_attempt_and_recovers_on_newer_success(
        self,
    ) -> None:
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

        image = "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4"
        contract = module.Contract(
            label="ichorcna-latest-attempt",
            root_arg="root",
            expected_rows=1,
            processes=frozenset({module.ICHORCNA_RUN_PROCESS}),
            images=frozenset({image}),
            require_ichorcna_compat=True,
        )
        pins = {image: "sha256:" + "1" * 64}
        marker_text = (
            "key\tvalue\n"
            "schema\toncotracer-ichorcna-plot-compat-v1\n"
            "status\tpatched\n"
            "target_quantile_calls\t2\n"
            "zero_median_plot_guard\tplaceholder\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_dir = root / "results" / "pipeline_info"
            trace_dir.mkdir(parents=True)

            def write_attempt(
                name: str,
                task_hash: str,
                status: str,
                exit_code: str,
                mtime: int,
            ) -> Path:
                trace = trace_dir / f"execution_trace_{name}.txt"
                with trace.open("w", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        ["task_id", "hash", "name", "status", "exit", "container"],
                        delimiter="\t",
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "task_id": "1",
                            "hash": task_hash,
                            "name": (
                                "DINCALCILAB_SAMURAI:"
                                f"{module.ICHORCNA_RUN_PROCESS} (DRR165691)"
                            ),
                            "status": status,
                            "exit": exit_code,
                            "container": image,
                        }
                    )
                os.utime(trace, (mtime, mtime))
                return trace

            def write_marker(task_hash: str, fill: str) -> Path:
                prefix, suffix = task_hash.split("/", 1)
                full_suffix = suffix + fill * (30 - len(suffix))
                marker = (
                    root
                    / "work"
                    / prefix
                    / full_suffix
                    / ".oncotracer-ichorcna-plot-compat.tsv"
                )
                marker.parent.mkdir(parents=True)
                marker.write_text(marker_text, encoding="utf-8")
                return marker

            old_hash = "aa/000001"
            failed_hash = "bb/000002"
            recovered_hash = "cc/000003"
            write_attempt("old", old_hash, "COMPLETED", "0", 1_700_000_000)
            write_marker(old_hash, "a")
            write_attempt("failed", failed_hash, "FAILED", "1", 1_700_000_100)
            write_marker(failed_hash, "b")

            combined, _manifest, _ = module.combine_root(root)
            ok, reason, selected_rows, _images = module.evaluate_trace(
                combined, contract, pins
            )
            self.assertFalse(ok)
            self.assertIn("failed, nonzero", reason)
            self.assertEqual(selected_rows[0]["hash"], failed_hash)
            self.assertEqual(selected_rows[0]["status"], "FAILED")
            with self.assertRaises(SystemExit):
                module.find_compat_marker(root, selected_rows)

            write_attempt(
                "recovered",
                recovered_hash,
                "COMPLETED",
                "0",
                1_700_000_200,
            )
            recovered_marker = write_marker(recovered_hash, "c")
            combined, _manifest, _ = module.combine_root(root)
            ok, reason, selected_rows, _images = module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            marker, _metadata, task_hash, _relative = module.find_compat_marker(
                root, selected_rows
            )
            self.assertEqual(task_hash, recovered_hash)
            self.assertEqual(marker, recovered_marker)

    def test_compat_marker_requires_exact_regular_nextflow_work_path(self) -> None:
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

        task_hash = "3a/6efce2"
        full_suffix = "6efce2" + "a" * 24
        rows = [
            {
                "hash": task_hash,
                "name": module.ICHORCNA_RUN_PROCESS,
                "status": "COMPLETED",
                "exit": "0",
                "container": "image",
            }
        ]
        cached_rows = [dict(rows[0], status="CACHED")]
        with self.assertRaisesRegex(SystemExit, "freshly COMPLETED"):
            module.require_ichorcna_task_hash(cached_rows)
        marker_text = (
            "key\tvalue\n"
            "schema\toncotracer-ichorcna-plot-compat-v1\n"
            "status\tpatched\n"
            "target_quantile_calls\t2\n"
            "zero_median_plot_guard\tplaceholder\n"
        )

        def write_marker(path: Path) -> Path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(marker_text, encoding="utf-8")
            return path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact_relative = (
                Path("work")
                / "3a"
                / full_suffix
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            exact = write_marker(root / exact_relative)
            marker, _metadata, _hash, relative = module.find_compat_marker(root, rows)
            self.assertEqual(marker, exact)
            self.assertEqual(relative, exact_relative)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spoof_relative = (
                Path("arbitrary")
                / "3a"
                / full_suffix
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            write_marker(root / spoof_relative)
            self.assertFalse(
                module.marker_path_matches_task_hash(spoof_relative, task_hash)
            )
            with self.assertRaises(SystemExit):
                module.find_compat_marker(root, rows)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nonhex_relative = (
                Path("work")
                / "3a"
                / "6efce2spoofed-work-directory"
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            write_marker(root / nonhex_relative)
            self.assertFalse(
                module.marker_path_matches_task_hash(nonhex_relative, task_hash)
            )
            with self.assertRaises(SystemExit):
                module.find_compat_marker(root, rows)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = write_marker(
                root / "elsewhere" / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            symlink = (
                root
                / "work"
                / "3a"
                / full_suffix
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            symlink.parent.mkdir(parents=True)
            symlink.symlink_to(target)
            self.assertTrue(symlink.is_symlink())
            with self.assertRaises(SystemExit):
                module.find_compat_marker(root, rows)


if __name__ == "__main__":
    unittest.main()
