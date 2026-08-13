#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
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
    (root / "06_workflow_summary/workflow_summary.txt").write_text(
        "\n".join(summary) + "\n"
    )
    with (root / "03_cna_codification/cna_events.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            ["sample", "state", "chrom", "start", "end", "mean_log2"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample": "S1",
                "state": "gain",
                "chrom": "1",
                "start": 0,
                "end": 100,
                "mean_log2": 0.5,
            }
        )
    with (root / "03_cna_codification/cna_cytogenomic_notation.tsv").open(
        "w", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            [
                "sample",
                "n_cna_events",
                "molecular_cytogenomic_notation",
                "cna_shorthand",
                "caller",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample": "S1",
                "n_cna_events": 1,
                "molecular_cytogenomic_notation": "x",
                "cna_shorthand": "x",
                "caller": "qdnaseq",
            }
        )
    for name in ("cna_per_sample_pages.pdf", "cna_log2_ratio_profiles_all_samples.pdf"):
        (root / "04_cna_custom_plots" / name).write_bytes(b"%PDF-1.4\n")
    with gzip.open(root / PROFILE_RELATIVE, "wt") as handle:
        writer = csv.DictWriter(
            handle, ["sample", "chrom", "start", "end", "log2"], delimiter="\t"
        )
        writer.writeheader()
        for index, value in enumerate((0.1, 0.2, 0.3, 0.4)):
            writer.writerow(
                {
                    "sample": "S1",
                    "chrom": "chr1",
                    "start": index * 100,
                    "end": (index + 1) * 100,
                    "log2": value,
                }
            )
    if native:
        (root / ".oncotracer-native").mkdir()
        (root / ".oncotracer-native/trace.tsv").write_text(
            "stage\tcommand\nrun\tRscript native.R\n"
        )


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
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
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
                with (run_root / "03_cna_codification/cna_events.tsv").open(
                    "w", newline=""
                ) as handle:
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

            reference = [
                (0, 1000),
                (2000, 3000),
                (4000, 5000),
                (6000, 7000),
                (8000, 8001),
            ]
            candidate = reference[:-1]
            write_events(v1, reference)
            write_events(v2, candidate)
            completed = run_comparator(v1, v2, report)
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
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
                            "sample",
                            "chrom",
                            "start",
                            "end",
                            "original_bin_start",
                            "original_bin_end",
                            "input_log2",
                            "final_log2",
                        ],
                        delimiter="	",
                    )
                    writer.writeheader()
                    finals = list(reversed(values)) if reverse_final else values
                    for index, (input_value, final_value) in enumerate(
                        zip(values, finals, strict=True)
                    ):
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
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
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
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
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
            self.assertEqual(
                completed.returncode, 1, completed.stdout + completed.stderr
            )
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
            self.assertEqual(
                completed.returncode, 1, completed.stdout + completed.stderr
            )
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

    def test_combined_trace_accepts_contract_rows_and_ignores_unrelated_tasks(
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
            wrong_hash_row[3] = f"task-hash:ff/ffffff;marker:{relative.as_posix()}"
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

    def test_current_invocation_rejects_startup_and_early_failures_then_accepts_recovery(
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

        image = "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4"
        contract = verify_module.Contract(
            label="quickstart1-ont",
            root_arg="ont_root",
            expected_rows=1,
            processes=frozenset({verify_module.ICHORCNA_RUN_PROCESS}),
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
            temporary_root = Path(directory)
            root = temporary_root / "live"
            trace_dir = root / "results" / "pipeline_info"
            evidence = root / "evidence"
            trace_dir.mkdir(parents=True)
            evidence.mkdir()
            base_mtime = 1_700_000_000_000_000_000

            def write_attempt(
                name: str,
                process: str,
                task_hash: str,
                status: str,
                exit_code: str,
                mtime_ns: int,
            ) -> Path:
                path = trace_dir / f"execution_trace_{name}.txt"
                with path.open("w", newline="", encoding="utf-8") as handle:
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
                            "name": f"DINCALCILAB_SAMURAI:{process} (DRR165691)",
                            "status": status,
                            "exit": exit_code,
                            "container": image,
                        }
                    )
                os.utime(path, ns=(mtime_ns, mtime_ns))
                return path

            def write_marker(task_hash: str, fill: str) -> Path:
                prefix, suffix = task_hash.split("/", 1)
                marker = (
                    root
                    / "work"
                    / prefix
                    / (suffix + fill * (30 - len(suffix)))
                    / ".oncotracer-ichorcna-plot-compat.tsv"
                )
                marker.parent.mkdir(parents=True)
                marker.write_text(marker_text, encoding="utf-8")
                return marker

            old_hash = "aa/000001"
            write_attempt(
                "old-complete",
                verify_module.ICHORCNA_RUN_PROCESS,
                old_hash,
                "COMPLETED",
                "0",
                base_mtime,
            )
            old_marker = write_marker(old_hash, "a")
            startup_pre = evidence / "startup-pre.tsv"
            verify_module.snapshot_trace_inventory(root, startup_pre)

            combined, sources, _ = verify_module.combine_root(root)
            ok, reason, selected_rows, _ = verify_module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            with self.assertRaisesRegex(SystemExit, "no new or content-changed"):
                verify_module.verify_current_trace_invocation(
                    root,
                    startup_pre,
                    sources,
                    selected_rows,
                    evidence / "startup-post.tsv",
                    evidence / "startup-delta.tsv",
                    require_ichorcna=True,
                )

            write_attempt(
                "early-failure",
                "SAMURAI:STARTUP_PROBE",
                "bb/000002",
                "FAILED",
                "1",
                base_mtime + 100,
            )
            combined, sources, _ = verify_module.combine_root(root)
            ok, reason, selected_rows, _ = verify_module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            marker, _metadata, task_hash, _relative = verify_module.find_compat_marker(
                root, selected_rows
            )
            self.assertEqual(marker, old_marker)
            self.assertEqual(task_hash, old_hash)
            with self.assertRaisesRegex(
                SystemExit, "newest current-invocation trace contributes no selected"
            ):
                verify_module.verify_current_trace_invocation(
                    root,
                    startup_pre,
                    sources,
                    selected_rows,
                    evidence / "failed-post.tsv",
                    evidence / "failed-delta.tsv",
                    require_ichorcna=True,
                )

            recovery_pre = evidence / "recovery-pre.tsv"
            verify_module.snapshot_trace_inventory(root, recovery_pre)
            recovered_hash = "cc/000003"
            recovered_trace = write_attempt(
                "current-complete",
                verify_module.ICHORCNA_RUN_PROCESS,
                recovered_hash,
                "COMPLETED",
                "0",
                base_mtime + 200,
            )
            write_marker(recovered_hash, "c")
            combined, sources, _ = verify_module.combine_root(root)
            ok, reason, selected_rows, _ = verify_module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            post_path = evidence / "recovery-post.tsv"
            delta_path = evidence / "recovery-delta.tsv"
            invocation = verify_module.verify_current_trace_invocation(
                root,
                recovery_pre,
                sources,
                selected_rows,
                post_path,
                delta_path,
                require_ichorcna=True,
            )
            recovered_relative = recovered_trace.relative_to(root).as_posix()
            self.assertEqual(invocation["newest_source_trace"], recovered_relative)
            self.assertEqual(
                invocation["selected_ichorcna_source_trace"], recovered_relative
            )
            self.assertEqual(
                [row["source_trace"] for row in invocation["delta"]["entries"]],
                [recovered_relative],
            )

            context = temporary_root / "audit-context"
            context.mkdir()
            file_copies = {
                recovery_pre: context / "nested-v1-ont-trace-pre.tsv",
                post_path: context / "nested-v1-ont-trace-post.tsv",
                delta_path: context / "nested-v1-ont-trace-delta.tsv",
                sources: context / "candidate-ont-trace-sources.tsv",
                combined: context / "nested-v1-ont-trace.tsv",
            }
            for source, destination in file_copies.items():
                shutil.copyfile(source, destination)
            shutil.copyfile(combined, context / "candidate-ont-combined-trace.tsv")
            verify_module.materialize_trace_sources(
                root,
                sources,
                context / "candidate-ont-trace-source-files",
            )
            invocation_name = "nested-v1-ont-trace-invocation.json"
            invocation_path = context / invocation_name
            invocation_path.write_text(
                json.dumps(invocation, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            selection = {
                contract.label
                + "-current-invocation": [
                    contract.label + "-current-invocation",
                    "",
                    "",
                    f"newest-trace:{recovered_relative}",
                    invocation_name,
                    audit_module.sha256(invocation_path),
                ]
            }
            audit_module.verify_trace_invocation(
                context,
                context / "nested-v1-ont-trace.tsv",
                contract,
                selection,
            )

            (context / "nested-v1-ont-trace-delta.tsv").write_text(
                "source_trace\tmtime_ns\tbytes\tsha256\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(audit_module.AuditError, "empty or forged"):
                audit_module.verify_trace_invocation(
                    context,
                    context / "nested-v1-ont-trace.tsv",
                    contract,
                    selection,
                )

    def test_trace_inventory_rejects_symlink_and_unsafe_source_paths(self) -> None:
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
            root = Path(directory)
            trace_dir = root / "results" / "pipeline_info"
            trace_dir.mkdir(parents=True)
            target = root / "outside-trace.txt"
            target.write_text(
                "task_id\thash\tname\tstatus\texit\tcontainer\n",
                encoding="utf-8",
            )
            (trace_dir / "execution_trace_symlink.txt").symlink_to(target)
            with self.assertRaisesRegex(SystemExit, "symbolic link"):
                module.snapshot_trace_inventory(root, root / "inventory.tsv")

        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "unsafe.tsv"
            inventory.write_text(
                "source_trace\tmtime_ns\tbytes\tsha256\n"
                f"../pipeline_info/execution_trace_escape.txt\t1\t1\t{'0' * 64}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "unsafe nested trace path"):
                module.read_trace_inventory(inventory)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real = root / "real" / "pipeline_info" / "execution_trace_real.txt"
            real.parent.mkdir(parents=True)
            real.write_text(
                "task_id\thash\tname\tstatus\texit\tcontainer\n",
                encoding="utf-8",
            )
            (root / "linked").symlink_to(root / "real", target_is_directory=True)
            linked_trace = root / "linked" / "pipeline_info" / real.name
            with self.assertRaisesRegex(SystemExit, "symbolic-link component"):
                module.trace_identity(root, linked_trace)

    def test_trace_delta_uses_path_and_content_not_mtime_only(self) -> None:
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
            root = Path(directory)
            trace = root / "results" / "pipeline_info" / "execution_trace_same.txt"
            trace.parent.mkdir(parents=True)
            original = (
                "task_id\thash\tname\tstatus\texit\tcontainer\n"
                "1\taa/000001\tSAMURAI:X\tCOMPLETED\t0\timage:1\n"
            )
            trace.write_text(original, encoding="utf-8")
            before = module.capture_trace_inventory(root)

            touched_ns = trace.stat().st_mtime_ns + 1_000_000_000
            os.utime(trace, ns=(touched_ns, touched_ns))
            touched = module.capture_trace_inventory(root)
            self.assertEqual(module.trace_inventory_delta(before, touched), [])

            trace.write_text(
                original.replace("aa/000001", "bb/000002"), encoding="utf-8"
            )
            changed = module.capture_trace_inventory(root)
            delta = module.trace_inventory_delta(before, changed)
            self.assertEqual(len(delta), 1)
            self.assertEqual(
                delta[0].source_trace,
                "results/pipeline_info/execution_trace_same.txt",
            )
            self.assertNotEqual(delta[0].sha256, before[0].sha256)

    def test_every_contract_binds_to_the_current_newest_trace_live_and_sealed(
        self,
    ) -> None:
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import parity_audit as audit_module
            import verify_nested_samurai as verify_module
        finally:
            sys.path.pop(0)

        image = "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1"
        process = "SAMURAI:SOLID_BIOPSY:QDNASEQ"
        contract = verify_module.Contract(
            label="quickstart1-illumina",
            root_arg="illumina_root",
            expected_rows=1,
            processes=frozenset({process}),
            images=frozenset({image}),
        )
        pins = {image: "sha256:" + "1" * 64}

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "live"
            trace_dir = root / "results" / "pipeline_info"
            trace_dir.mkdir(parents=True)

            def write_trace(
                name: str, task_process: str, task_hash: str, mtime_ns: int
            ) -> Path:
                path = trace_dir / f"execution_trace_{name}.txt"
                with path.open("w", newline="", encoding="utf-8") as handle:
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
                            "name": f"DINCALCILAB_SAMURAI:{task_process} (S1)",
                            "status": "COMPLETED",
                            "exit": "0",
                            "container": image,
                        }
                    )
                os.utime(path, ns=(mtime_ns, mtime_ns))
                return path

            def seal_context(
                context: Path,
                combined: Path,
                source_manifest: Path,
                pre_path: Path,
                post_path: Path,
                delta_path: Path,
                invocation: dict[str, object],
            ) -> dict[str, list[str]]:
                context.mkdir()
                copies = {
                    combined: context / "nested-v1-illumina-trace.tsv",
                    source_manifest: context / "candidate-illumina-trace-sources.tsv",
                    pre_path: context / "nested-v1-illumina-trace-pre.tsv",
                    post_path: context / "nested-v1-illumina-trace-post.tsv",
                    delta_path: context / "nested-v1-illumina-trace-delta.tsv",
                }
                for source, destination in copies.items():
                    shutil.copyfile(source, destination)
                shutil.copyfile(
                    combined, context / "candidate-illumina-combined-trace.tsv"
                )
                verify_module.materialize_trace_sources(
                    root,
                    source_manifest,
                    context / "candidate-illumina-trace-source-files",
                )
                invocation_name = "nested-v1-illumina-trace-invocation.json"
                invocation_path = context / invocation_name
                invocation_path.write_text(
                    json.dumps(invocation, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                row = [
                    contract.label + "-current-invocation",
                    "",
                    "",
                    f"newest-trace:{invocation['newest_source_trace']}",
                    invocation_name,
                    audit_module.sha256(invocation_path),
                ]
                return {row[0]: row}

            base_mtime = 1_700_000_000_000_000_000
            write_trace("old-contract", process, "aa/000001", base_mtime)
            stale_pre = workspace / "stale-pre.tsv"
            verify_module.snapshot_trace_inventory(root, stale_pre)
            write_trace(
                "new-unrelated",
                "SAMURAI:STARTUP_PROBE",
                "bb/000002",
                base_mtime + 100,
            )
            combined, manifest, _ = verify_module.combine_root(root)
            ok, reason, selected, _ = verify_module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            with self.assertRaisesRegex(
                SystemExit, "newest current-invocation trace contributes no selected"
            ):
                verify_module.verify_current_trace_invocation(
                    root,
                    stale_pre,
                    manifest,
                    selected,
                    workspace / "rejected-post.tsv",
                    workspace / "rejected-delta.tsv",
                    require_ichorcna=False,
                )

            stale_post_rows = verify_module.capture_trace_inventory(root)
            stale_delta_rows = verify_module.trace_inventory_delta(
                verify_module.read_trace_inventory(stale_pre), stale_post_rows
            )
            stale_post = workspace / "stale-post.tsv"
            stale_delta = workspace / "stale-delta.tsv"
            verify_module.write_trace_inventory(stale_post, stale_post_rows)
            verify_module.write_trace_inventory(stale_delta, stale_delta_rows)
            newest = max(
                stale_post_rows, key=lambda row: (row.mtime_ns, row.source_trace)
            )
            forged_invocation = {
                "schema": "oncotracer-nested-trace-invocation-v1",
                "newest_source_trace": newest.source_trace,
                "selected_contract_source_trace": newest.source_trace,
                "selected_contract_row_count": 1,
                "selected_ichorcna_source_trace": None,
                "pre": {
                    "sha256": verify_module.sha256(stale_pre),
                    "entries": [
                        verify_module.identity_record(row)
                        for row in verify_module.read_trace_inventory(stale_pre)
                    ],
                },
                "post": {
                    "sha256": verify_module.sha256(stale_post),
                    "entries": [
                        verify_module.identity_record(row) for row in stale_post_rows
                    ],
                },
                "delta": {
                    "sha256": verify_module.sha256(stale_delta),
                    "entries": [
                        verify_module.identity_record(row) for row in stale_delta_rows
                    ],
                },
            }
            stale_context = workspace / "sealed-stale"
            stale_selection = seal_context(
                stale_context,
                combined,
                manifest,
                stale_pre,
                stale_post,
                stale_delta,
                forged_invocation,
            )
            with self.assertRaisesRegex(
                audit_module.AuditError,
                "newest current trace contributes no selected contracted task",
            ):
                audit_module.verify_trace_invocation(
                    stale_context,
                    stale_context / "nested-v1-illumina-trace.tsv",
                    contract,
                    stale_selection,
                )

            current_pre = workspace / "current-pre.tsv"
            verify_module.snapshot_trace_inventory(root, current_pre)
            current_trace = write_trace(
                "new-contract", process, "cc/000003", base_mtime + 200
            )
            combined, manifest, _ = verify_module.combine_root(root)
            ok, reason, selected, _ = verify_module.evaluate_trace(
                combined, contract, pins
            )
            self.assertTrue(ok, reason)
            current_post = workspace / "current-post.tsv"
            current_delta = workspace / "current-delta.tsv"
            invocation = verify_module.verify_current_trace_invocation(
                root,
                current_pre,
                manifest,
                selected,
                current_post,
                current_delta,
                require_ichorcna=False,
            )
            current_relative = current_trace.relative_to(root).as_posix()
            self.assertEqual(
                invocation["selected_contract_source_trace"], current_relative
            )
            self.assertEqual(invocation["selected_contract_row_count"], 1)
            current_context = workspace / "sealed-current"
            current_selection = seal_context(
                current_context,
                combined,
                manifest,
                current_pre,
                current_post,
                current_delta,
                invocation,
            )
            verified = audit_module.verify_trace_invocation(
                current_context,
                current_context / "nested-v1-illumina-trace.tsv",
                contract,
                current_selection,
            )
            self.assertEqual(
                verified["selected_contract_source_trace"], current_relative
            )

    def test_preserved_raw_traces_recompute_with_collision_safe_determinism(
        self,
    ) -> None:
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import combine_nested_samurai_traces as combine_module
            import parity_audit as audit_module
        finally:
            sys.path.pop(0)

        header = ["task_id", "hash", "name", "status", "exit", "container"]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "live"
            first = root / "attempt-a" / "pipeline_info" / "execution_trace_same.txt"
            second = root / "attempt-b" / "pipeline_info" / "execution_trace_same.txt"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)

            def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, header, delimiter="\t")
                    writer.writeheader()
                    writer.writerows(rows)

            def row(task_id: str, task_hash: str, name: str) -> dict[str, str]:
                return {
                    "task_id": task_id,
                    "hash": task_hash,
                    "name": name,
                    "status": "COMPLETED",
                    "exit": "0",
                    "container": "image:1",
                }

            write_rows(
                first,
                [
                    row("1", "aa/000001", "SAMURAI:TASK_X (S1)"),
                    row("2", "dd/000004", "SAMURAI:TASK_Y (S1)"),
                ],
            )
            write_rows(
                second,
                [
                    row("1", "bb/000002", "SAMURAI:TASK_X (S1)"),
                    row("2", "cc/000003", "SAMURAI:TASK_X (S1)"),
                ],
            )
            tied_mtime = 1_700_000_000_000_000_000
            os.utime(first, ns=(tied_mtime, tied_mtime))
            os.utime(second, ns=(tied_mtime, tied_mtime))
            combined, manifest, _ = combine_module.combine_root(root)
            with combined.open(newline="", encoding="utf-8") as handle:
                combined_rows = list(csv.DictReader(handle, delimiter="\t"))
            task_x = next(row for row in combined_rows if "TASK_X" in row["name"])
            self.assertEqual(task_x["hash"], "cc/000003")
            self.assertEqual(
                task_x["source_trace"],
                "attempt-b/pipeline_info/execution_trace_same.txt",
            )
            self.assertEqual(task_x["source_row"], "3")

            raw_root = workspace / "artifact" / "raw"
            manifest_copy = workspace / "artifact" / "source-manifest.tsv"
            manifest_copy.parent.mkdir()
            shutil.copyfile(manifest, manifest_copy)
            combine_module.materialize_trace_sources(root, manifest, raw_root)
            copied_first = raw_root / first.relative_to(root)
            copied_second = raw_root / second.relative_to(root)
            self.assertTrue(copied_first.is_file())
            self.assertTrue(copied_second.is_file())
            self.assertEqual(copied_first.name, copied_second.name)
            self.assertNotEqual(copied_first, copied_second)
            self.assertEqual(copied_first.stat().st_mtime_ns, 0)
            self.assertEqual(copied_second.stat().st_mtime_ns, 0)

            recomputed, regenerated, _ = (
                combine_module.recompute_preserved_trace_artifact(
                    raw_root, manifest_copy, workspace / "shared-recompute"
                )
            )
            self.assertEqual(recomputed.read_bytes(), combined.read_bytes())
            self.assertEqual(regenerated.read_bytes(), manifest_copy.read_bytes())
            audit_module.verify_preserved_trace_render(
                raw_root, manifest_copy, combined
            )

            os.utime(copied_first, ns=(999, 999))
            os.utime(copied_second, ns=(1, 1))
            recomputed, _regenerated, _ = (
                combine_module.recompute_preserved_trace_artifact(
                    raw_root, manifest_copy, workspace / "mtime-recompute"
                )
            )
            self.assertEqual(recomputed.read_bytes(), combined.read_bytes())
            audit_module.verify_preserved_trace_render(
                raw_root, manifest_copy, combined
            )

            copied_first.unlink()
            with self.assertRaisesRegex(SystemExit, "inventory does not match"):
                combine_module.recompute_preserved_trace_artifact(
                    raw_root, manifest_copy, workspace / "missing-recompute"
                )
            with self.assertRaisesRegex(audit_module.AuditError, "inventory mismatch"):
                audit_module.verify_preserved_trace_render(
                    raw_root, manifest_copy, combined
                )
            combine_module.materialize_trace_sources(root, manifest, raw_root)

            with copied_first.open("ab") as handle:
                handle.write(b"tampered\n")
            with self.assertRaisesRegex(SystemExit, "recorded identity"):
                combine_module.recompute_preserved_trace_artifact(
                    raw_root, manifest_copy, workspace / "modified-recompute"
                )
            with self.assertRaisesRegex(audit_module.AuditError, "identity mismatch"):
                audit_module.verify_preserved_trace_render(
                    raw_root, manifest_copy, combined
                )
            combine_module.materialize_trace_sources(root, manifest, raw_root)

            tampered_manifest = workspace / "tampered-manifest.tsv"
            with manifest_copy.open(newline="", encoding="utf-8") as handle:
                manifest_rows = list(csv.reader(handle, delimiter="\t"))
            manifest_rows[1][3] = str(int(manifest_rows[1][3]) + 1)
            with tampered_manifest.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle, delimiter="\t", lineterminator="\n").writerows(
                    manifest_rows
                )
            with self.assertRaisesRegex(
                audit_module.AuditError, "row-count evidence mismatch"
            ):
                audit_module.verify_preserved_trace_render(
                    raw_root, tampered_manifest, combined
                )

            tampered_combined = workspace / "tampered-combined.tsv"
            with combined.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            rows[0]["source_row"] = str(int(rows[0]["source_row"]) + 10)
            with tampered_combined.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    combine_module.OUTPUT_COLUMNS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                audit_module.AuditError, "not the deterministic rendering"
            ):
                audit_module.verify_preserved_trace_render(
                    raw_root, manifest_copy, tampered_combined
                )

            copied_first.unlink()
            copied_first.symlink_to(first)
            with self.assertRaisesRegex(audit_module.AuditError, "symbolic link"):
                audit_module.verify_preserved_trace_render(
                    raw_root, manifest_copy, combined
                )
            copied_first.unlink()
            combine_module.materialize_trace_sources(root, manifest, raw_root)
            extra = raw_root / "unexpected.txt"
            extra.write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(audit_module.AuditError, "unexpected raw"):
                audit_module.verify_preserved_trace_render(
                    raw_root, manifest_copy, combined
                )

    def test_server_trace_audit_command_substitution_receives_one_path(self) -> None:
        driver = (ROOT / "scripts/validate_v2_release.sh").read_text(encoding="utf-8")
        function_start = driver.index("generate_samurai_trace_audit() {")
        program_start = driver.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        program_end = driver.index("\nPY\n", program_start)
        program = driver[program_start:program_end]

        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import verify_nested_samurai as verify_module
        finally:
            sys.path.pop(0)
        processes = sorted(verify_module.SERVER_ILLUMINA_PROCESSES)
        images = sorted(verify_module.SERVER_ILLUMINA_IMAGES)

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "nested"
            trace = root / "attempt-1/pipeline_info/execution_trace_current.txt"
            trace.parent.mkdir(parents=True)
            with trace.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerow(
                    ["task_id", "hash", "name", "status", "exit", "container"]
                )
                for task_id, process in enumerate(processes, start=1):
                    writer.writerow(
                        [
                            task_id,
                            f"{task_id:02x}/{task_id:06x}",
                            f"{process} (SAMPLE_A)",
                            "COMPLETED",
                            "0",
                            images[(task_id - 1) % len(images)],
                        ]
                    )

            pins = workspace / "samurai-container-pins.tsv"
            pins.write_text(
                "".join(f"{image}\tsha256:{'0' * 64}\n" for image in images),
                encoding="utf-8",
            )
            pre_inventory = workspace / "trace-pre.tsv"
            pre_inventory.write_text(
                "source_trace\tmtime_ns\tbytes\tsha256\n", encoding="utf-8"
            )
            destination = workspace / "trace-audit.json"
            post_inventory = workspace / "trace-post.tsv"
            delta_inventory = workspace / "trace-delta.tsv"
            source_manifest = workspace / "v1-illumina-samurai-trace-sources.tsv"
            raw_sources = workspace / "v1-illumina-samurai-trace-source-files"
            embedded_program = workspace / "generate-samurai-trace-audit.py"
            embedded_program.write_text(program, encoding="utf-8")

            arguments = [
                sys.executable,
                str(embedded_program),
                str(root),
                "illumina",
                "12",
                str(pins),
                str(destination),
                str(pre_inventory),
                str(post_inventory),
                str(delta_inventory),
                "v1-illumina-samurai",
                "",
                str(source_manifest),
                str(raw_sources),
                "",
                str(ROOT / "tests"),
            ]
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    """set -Eeuo pipefail
selected_output="$("$@")"
[[ "$selected_output" != *$'\\n'* ]]
[[ -f "$selected_output" ]]
printf '%s\\n' "$selected_output"
""",
                    "trace-audit-command-substitution",
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            selected = (
                root
                / ".oncotracer-parity/pipeline_info/execution_trace_oncotracer_combined.txt"
            ).resolve()
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            self.assertEqual(completed.stdout, f"{selected}\n")
            self.assertEqual(
                completed.stderr.count("Combined 1 nested trace file(s)"), 2
            )
            self.assertTrue(destination.is_file())
            self.assertTrue(source_manifest.is_file())
            self.assertTrue(raw_sources.is_dir())

    def test_server_sealed_trace_proof_recomputes_and_bundle_wiring_is_complete(
        self,
    ) -> None:
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import verify_nested_samurai as verify_module
        finally:
            sys.path.pop(0)

        prefix = "v1-ont-samurai"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "live"
            trace_dir = root / "results" / "pipeline_info"
            trace_dir.mkdir(parents=True)
            pre_live = workspace / "pre-live.tsv"
            verify_module.snapshot_trace_inventory(root, pre_live)
            trace = trace_dir / "execution_trace_current.txt"
            processes = sorted(verify_module.SERVER_ONT_PROCESSES)
            images = sorted(verify_module.SERVER_ONT_IMAGES)
            with trace.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    ["task_id", "hash", "name", "status", "exit", "container"],
                    delimiter="\t",
                )
                writer.writeheader()
                for index, process in enumerate(processes, start=1):
                    writer.writerow(
                        {
                            "task_id": str(index),
                            "hash": f"{index:02x}/{index:06x}",
                            "name": f"DINCALCILAB_SAMURAI:{process} (S1)",
                            "status": "COMPLETED",
                            "exit": "0",
                            "container": images[(index - 1) % len(images)],
                        }
                    )
            ichor_index = processes.index(verify_module.ICHORCNA_RUN_PROCESS) + 1
            ichor_hash = f"{ichor_index:02x}/{ichor_index:06x}"
            hash_prefix, hash_suffix = ichor_hash.split("/", 1)
            marker_relative = (
                Path("work")
                / hash_prefix
                / (hash_suffix + "a" * (30 - len(hash_suffix)))
                / ".oncotracer-ichorcna-plot-compat.tsv"
            )
            marker = root / marker_relative
            marker.parent.mkdir(parents=True)
            marker.write_text(
                "key\tvalue\n"
                "schema\toncotracer-ichorcna-plot-compat-v1\n"
                "status\tpatched\n"
                "target_quantile_calls\t2\n"
                "zero_median_plot_guard\tplaceholder\n",
                encoding="utf-8",
            )
            os.utime(trace, ns=(1_700_000_000_000_000_000,) * 2)
            combined, manifest, _ = verify_module.combine_root(root)
            contract = verify_module.Contract(
                label="server-ont",
                root_arg="root",
                expected_rows=10,
                processes=verify_module.SERVER_ONT_PROCESSES,
                images=verify_module.SERVER_ONT_IMAGES,
                require_ichorcna_compat=True,
            )
            ok, reason, selected, _ = verify_module.evaluate_trace(
                combined, contract, verify_module.SERVER_IMAGE_DIGESTS
            )
            self.assertTrue(ok, reason)
            post_live = workspace / "post-live.tsv"
            delta_live = workspace / "delta-live.tsv"
            invocation = verify_module.verify_current_trace_invocation(
                root,
                pre_live,
                manifest,
                selected,
                post_live,
                delta_live,
                require_ichorcna=True,
            )

            context = workspace / "context"
            context.mkdir()
            paths = {
                combined: context / f"{prefix}-execution-trace.txt",
                manifest: context / f"{prefix}-trace-sources.tsv",
                pre_live: context / f"{prefix}-trace-pre.tsv",
                post_live: context / f"{prefix}-trace-post.tsv",
                delta_live: context / f"{prefix}-trace-delta.tsv",
            }
            for source, destination in paths.items():
                shutil.copyfile(source, destination)
            raw_root = context / f"{prefix}-trace-source-files"
            verify_module.materialize_trace_sources(root, manifest, raw_root)
            marker_copy = context / f"{prefix}-ichorcna-plot-compat.tsv"
            shutil.copyfile(marker, marker_copy)
            compatibility = {
                "artifact": marker_copy.name,
                "relative_path": marker_relative.as_posix(),
                "task_hash": ichor_hash,
                "sha256": verify_module.sha256(marker_copy),
                "metadata": verify_module.parse_compat(marker_copy),
            }
            pins_path = context / "samurai-container-pins.tsv"
            with pins_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerows(verify_module.SERVER_IMAGE_DIGESTS.items())
            identities_path = context / "samurai-container-identities.tsv"
            with identities_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerow(["tag", "pinned_reference", "image_id", "repo_digests"])
                for tag, digest in verify_module.SERVER_IMAGE_DIGESTS.items():
                    writer.writerow(
                        [
                            tag,
                            f"{tag}@{digest}",
                            "sha256:" + hashlib.sha256(tag.encode()).hexdigest(),
                            f"{tag}@{digest}",
                        ]
                    )
            combined_rows = verify_module.parse_trace(
                context / f"{prefix}-execution-trace.txt"
            )
            proof_rows = []
            for row in combined_rows:
                process = verify_module.normalize_process(row["name"])
                if process not in verify_module.SERVER_ONT_PROCESSES:
                    continue
                canonical, digest = verify_module._resolve_server_container(
                    row["container"], verify_module.SERVER_IMAGE_DIGESTS
                )
                proof_rows.append(
                    {
                        "hash": row["hash"].lower(),
                        "name": row["name"].strip(),
                        "normalized_process": process,
                        "status": row["status"].upper(),
                        "exit": row["exit"],
                        "container": row["container"],
                        "canonical_container": canonical,
                        "repo_digest": digest,
                        "source_trace": row["source_trace"],
                        "source_row": row["source_row"],
                    }
                )
            records = verify_module.read_source_manifest(
                context / f"{prefix}-trace-sources.tsv"
            )
            evidence = {
                "schema": "oncotracer-samurai-trace-audit-v1",
                "mode": "ont",
                "evidence_mode": "complete-combined-trace",
                "source_trace": f"{prefix}-execution-trace.txt",
                "source_trace_sha256": verify_module.sha256(
                    context / f"{prefix}-execution-trace.txt"
                ),
                "source_manifest": f"{prefix}-trace-sources.tsv",
                "source_manifest_sha256": verify_module.sha256(
                    context / f"{prefix}-trace-sources.tsv"
                ),
                "source_files": raw_root.name,
                "available_traces": [
                    {
                        "relative_path": record.identity.source_trace,
                        "artifact_path": f"{raw_root.name}/{record.identity.source_trace}",
                        "mtime_ns": record.identity.mtime_ns,
                        "bytes": record.identity.bytes,
                        "rows": record.rows,
                        "successful_rows": record.successful_rows,
                        "sha256": record.identity.sha256,
                    }
                    for record in records
                ],
                "contract_row_count": 10,
                "row_count": 10,
                "processes": processes,
                "contract_processes": processes,
                "containers": images,
                "contract_containers": images,
                "ichorcna_plot_compat": compatibility,
                "rows": proof_rows,
                "trace_invocation": invocation,
            }
            evidence_path = context / f"{prefix}-trace-audit.json"
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            verify_module.verify_preserved_server_trace_proof(
                context, prefix, "ont", 10
            )
            evidence["ichorcna_plot_compat"]["task_hash"] = "ff/ffffff"
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "compatibility evidence"):
                verify_module.verify_preserved_server_trace_proof(
                    context, prefix, "ont", 10
                )
            evidence["ichorcna_plot_compat"] = compatibility
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            raw_trace = raw_root / trace.relative_to(root)
            raw_trace.unlink()
            with self.assertRaisesRegex(SystemExit, "inventory does not match"):
                verify_module.verify_preserved_server_trace_proof(
                    context, prefix, "ont", 10
                )
            verify_module.materialize_trace_sources(root, manifest, raw_root)
            with raw_trace.open("ab") as handle:
                handle.write(b"modified\n")
            with self.assertRaisesRegex(SystemExit, "recorded identity"):
                verify_module.verify_preserved_server_trace_proof(
                    context, prefix, "ont", 10
                )
            verify_module.materialize_trace_sources(root, manifest, raw_root)

            combined_copy = context / f"{prefix}-execution-trace.txt"
            original_combined = combined_copy.read_bytes()
            combined_copy.write_bytes(original_combined.replace(b"\t2\n", b"\t99\n", 1))
            with self.assertRaisesRegex(SystemExit, "do not reproduce"):
                verify_module.verify_preserved_server_trace_proof(
                    context, prefix, "ont", 10
                )
            combined_copy.write_bytes(original_combined)
            evidence["source_trace"] = "/private/server/execution_trace.txt"
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "absolute paths"):
                verify_module.verify_preserved_server_trace_proof(
                    context, prefix, "ont", 10
                )

        driver = (ROOT / "scripts/validate_v2_release.sh").read_text(encoding="utf-8")
        for expected in (
            "v1-illumina-samurai illumina 12",
            "v1-ont-samurai ont 10",
            "v1-hcc1143-samurai illumina 32",
        ):
            self.assertIn(expected, driver)
        for tag, digest in verify_module.SERVER_IMAGE_DIGESTS.items():
            self.assertIn(f"{tag}\t{digest}", driver)
        self.assertGreaterEqual(
            driver.count("verify_preserved_trace_bundle_context"), 4
        )
        self.assertIn('tests/parity_audit.py" verify-trace-proof', driver)
        self.assertIn("tar --sort=name --mtime='@0' --owner=0 --group=0", driver)
        self.assertIn("gzip -n >", driver)

    def test_minimal_native_environment_audit_is_suite_exact_and_sealed(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "parity_audit_minimal_env_test", ROOT / "tests" / "parity_audit.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        audit_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = audit_module
        try:
            spec.loader.exec_module(audit_module)
        finally:
            sys.modules.pop(spec.name, None)

        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            (context / "native-environments").mkdir()
            (context / "native-environment-probes").mkdir()
            required_probes = {
                "core": {"bwa", "samtools", "minimap2", "pigz", "picard"},
                "qdnaseq": {"rscript"},
            }
            inventory_rows = ["environment\tdefinition_sha256\texplicit_sha256"]
            probe_rows = ["environment\tprobe\tresult\tevidence_sha256"]
            for environment, probes in required_probes.items():
                definition = context / "native-environments" / f"{environment}.yml"
                explicit = context / f"native-{environment}.explicit.txt"
                definition.write_text(
                    f"name: oncotracer-v2-{environment}\n", encoding="utf-8"
                )
                explicit.write_text(
                    f"@EXPLICIT\nhttps://example.invalid/{environment}.conda\n",
                    encoding="utf-8",
                )
                inventory_rows.append(
                    "\t".join(
                        (
                            environment,
                            hashlib.sha256(definition.read_bytes()).hexdigest(),
                            hashlib.sha256(explicit.read_bytes()).hexdigest(),
                        )
                    )
                )
                for probe in sorted(probes):
                    evidence = (
                        context
                        / "native-environment-probes"
                        / f"{environment}-{probe}.txt"
                    )
                    evidence.write_text(f"{environment}/{probe} OK\n", encoding="utf-8")
                    probe_rows.append(
                        "\t".join(
                            (
                                environment,
                                probe,
                                "PASS",
                                hashlib.sha256(evidence.read_bytes()).hexdigest(),
                            )
                        )
                    )
            (context / "native-environment-inventory.tsv").write_text(
                "\n".join(inventory_rows) + "\n", encoding="utf-8"
            )
            (context / "native-environment-probes.tsv").write_text(
                "\n".join(probe_rows) + "\n", encoding="utf-8"
            )
            resource_sha = "a" * 40
            runner_temp = "/runner/temp"
            swap_file = f"{runner_temp}/oncotracer-swap-123-2"
            (context / "hosted-resource-preflight.txt").write_text(
                "\n".join(
                    (
                        "resource_preflight_schema=oncotracer-hosted-resource-preflight-v3",
                        "resource_preflight_status=PASS",
                        "resource_preflight_run_id=123",
                        "resource_preflight_run_attempt=2",
                        "resource_preflight_suite=quickstart2",
                        f"resource_preflight_candidate_sha={resource_sha}",
                        "resource_preflight_purpose=test parity",
                        f"resource_preflight_minimum_available_kib={80 * 1024 * 1024}",
                        "resource_preflight_checked_path_count=4",
                        "resource_preflight_unique_device_count=3",
                        "resource_preflight_checked_path_000_path=/workspace",
                        "resource_preflight_checked_path_000_device=/dev/test",
                        f"resource_preflight_checked_path_000_available_kib={80 * 1024 * 1024}",
                        f"resource_preflight_checked_path_001_path={runner_temp}",
                        "resource_preflight_checked_path_001_device=/dev/test",
                        f"resource_preflight_checked_path_001_available_kib={80 * 1024 * 1024}",
                        "resource_preflight_checked_path_002_path=/tmp",
                        "resource_preflight_checked_path_002_device=/dev/other",
                        f"resource_preflight_checked_path_002_available_kib={80 * 1024 * 1024}",
                        "resource_preflight_checked_path_003_path=/docker",
                        "resource_preflight_checked_path_003_device=/dev/docker",
                        f"resource_preflight_checked_path_003_available_kib={80 * 1024 * 1024}",
                        "resource_preflight_required_free_gib=72",
                        f"resource_preflight_mem_total_kib={16 * 1024 * 1024}",
                        "resource_preflight_required_physical_gib=15",
                        "resource_preflight_swap_total_kib=0",
                        "resource_preflight_planned_swap_gib=32",
                        f"resource_preflight_expected_swap_file={swap_file}",
                        "resource_preflight_required_addressable_gib=47",
                        "resource_preflight_standard_contract_free_gib=14",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            phase_names = (
                "preflight-passed",
                "swap-active",
                "public-inputs-ready",
                "frozen-images-ready",
                "frozen-traces-authenticated",
                "frozen-reference-released",
                "frozen-images-released",
                "native-environments-with-cache",
                "native-package-cache-released",
                "native-runs-complete",
                "final",
            )
            phase_root = context / "hosted-resource-phases"
            phase_root.mkdir()
            for phase_index, phase in enumerate(phase_names, start=1):
                swap_required = int(phase != "preflight-passed")
                swap_size = 32 * 1024**3 if swap_required else 0
                (phase_root / f"{phase}.txt").write_text(
                    "\n".join(
                        (
                            "schema\toncotracer-hosted-resource-phase-v2",
                            "run_id\t123",
                            "run_attempt\t2",
                            "suite\tquickstart2",
                            f"candidate_sha\t{resource_sha}",
                            f"phase\t{phase}",
                            f"phase_index\t{phase_index}",
                            "minimum_free_gib\t72",
                            "minimum_physical_gib\t15",
                            "minimum_addressable_gib\t47",
                            "planned_swap_gib\t32",
                            "filesystem_reserve_gib\t8",
                            f"runner_temp\t{runner_temp}",
                            f"expected_swap_file\t{swap_file}",
                            f"swap_required\t{swap_required}",
                            f"active_swap_size_bytes\t{swap_size}",
                            "active_swap_used_bytes\t0",
                            f"minimum_available_kib\t{70 * 1024 * 1024}",
                            f"mem_total_kib\t{16 * 1024 * 1024}",
                            f"swap_total_kib\t{0 if phase == 'preflight-passed' else 32 * 1024 * 1024}",
                            "recorded_at\t2026-08-12T00:00:00Z",
                            "",
                            "[df-kibibytes]",
                            "[memory-kibibytes]",
                            "[active-swap]",
                            "[docker]",
                            "[path-bytes]",
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (context / "hosted-resource-final.txt").write_bytes(
                (phase_root / "final.txt").read_bytes()
            )

            verified = audit_module.verify_minimal_native_environments(
                context, "quickstart2", resource_sha
            )
            self.assertEqual(verified["environments"], ["core", "qdnaseq"])
            self.assertEqual(verified["resources"]["phase_count"], 11)
            swap_phase = phase_root / "swap-active.txt"
            untampered_swap = swap_phase.read_text(encoding="utf-8")
            swap_phase.write_text(
                untampered_swap.replace(
                    "active_swap_size_bytes\t34359738368", "active_swap_size_bytes\t0"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(audit_module.AuditError, "planned swap"):
                audit_module.verify_minimal_native_environments(
                    context, "quickstart2", resource_sha
                )
            swap_phase.write_text(untampered_swap, encoding="utf-8")
            swap_phase.write_text(
                untampered_swap.replace("run_id\t123", "run_id\t999"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(audit_module.AuditError, "identity/order"):
                audit_module.verify_minimal_native_environments(
                    context, "quickstart2", resource_sha
                )
            swap_phase.write_text(untampered_swap, encoding="utf-8")
            swap_phase.write_text(
                untampered_swap.replace("minimum_free_gib\t72", "minimum_free_gib\t71"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(audit_module.AuditError, "threshold"):
                audit_module.verify_minimal_native_environments(
                    context, "quickstart2", resource_sha
                )
            swap_phase.write_text(untampered_swap, encoding="utf-8")
            final_evidence = context / "hosted-resource-final.txt"
            final_evidence.write_text(
                final_evidence.read_text(encoding="utf-8") + "tampered\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(audit_module.AuditError, "sealed final"):
                audit_module.verify_minimal_native_environments(
                    context, "quickstart2", resource_sha
                )
            final_evidence.write_bytes((phase_root / "final.txt").read_bytes())
            with self.assertRaisesRegex(audit_module.AuditError, "inventory mismatch"):
                audit_module.verify_minimal_native_environments(context, "quickstart1")

            preflight_path = context / "hosted-resource-preflight.txt"
            original_preflight = preflight_path.read_text(encoding="utf-8")
            original_phases = {
                phase: (phase_root / f"{phase}.txt").read_text(encoding="utf-8")
                for phase in phase_names
            }
            preflight_path.write_text(
                original_preflight.replace(
                    "resource_preflight_required_free_gib=72",
                    "resource_preflight_required_free_gib=40",
                )
                .replace(
                    f"resource_preflight_mem_total_kib={16 * 1024 * 1024}",
                    f"resource_preflight_mem_total_kib={64 * 1024 * 1024}",
                )
                .replace(
                    "resource_preflight_planned_swap_gib=32",
                    "resource_preflight_planned_swap_gib=0",
                )
                .replace(
                    f"resource_preflight_expected_swap_file={swap_file}",
                    "resource_preflight_expected_swap_file=none",
                ),
                encoding="utf-8",
            )
            for phase, phase_text in original_phases.items():
                zero_swap_text = (
                    phase_text.replace("minimum_free_gib\t72", "minimum_free_gib\t40")
                    .replace("planned_swap_gib\t32", "planned_swap_gib\t0")
                    .replace(
                        f"expected_swap_file\t{swap_file}", "expected_swap_file\tnone"
                    )
                    .replace("swap_required\t1", "swap_required\t0")
                    .replace(
                        "active_swap_size_bytes\t34359738368",
                        "active_swap_size_bytes\t0",
                    )
                    .replace(
                        f"mem_total_kib\t{16 * 1024 * 1024}",
                        f"mem_total_kib\t{64 * 1024 * 1024}",
                    )
                    .replace(
                        f"swap_total_kib\t{32 * 1024 * 1024}",
                        "swap_total_kib\t0",
                    )
                )
                (phase_root / f"{phase}.txt").write_text(
                    zero_swap_text, encoding="utf-8"
                )
            final_evidence.write_bytes((phase_root / "final.txt").read_bytes())
            zero_swap_verified = audit_module.verify_minimal_native_environments(
                context, "quickstart2", resource_sha
            )
            self.assertEqual(
                zero_swap_verified["resources"]["phase_count"], len(phase_names)
            )
            preflight_path.write_text(original_preflight, encoding="utf-8")
            for phase, phase_text in original_phases.items():
                (phase_root / f"{phase}.txt").write_text(phase_text, encoding="utf-8")
            final_evidence.write_bytes((phase_root / "final.txt").read_bytes())

            (context / "native-classifier.explicit.txt").write_text(
                "@EXPLICIT\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(audit_module.AuditError, "unused environment"):
                audit_module.verify_minimal_native_environments(context, "quickstart2")

    def test_job_image_action_audit_rejects_mismatched_ownership(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "parity_audit_image_action_test", ROOT / "tests" / "parity_audit.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        audit_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = audit_module
        try:
            spec.loader.exec_module(audit_module)
        finally:
            sys.modules.pop(spec.name, None)

        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            pins = {
                image: "sha256:" + f"{index:064x}"
                for index, image in enumerate(
                    sorted(
                        set().union(
                            *(
                                set(contract.images)
                                for contract in audit_module.CONTRACTS["quickstart2"]
                            )
                        )
                    ),
                    start=1,
                )
            }
            pin_lines = ["container\tmanifest_digest"] + [
                f"{container}\t{digest}" for container, digest in sorted(pins.items())
            ]
            (context / "nested-v1-container-pins.tsv").write_text(
                "\n".join(pin_lines) + "\n", encoding="utf-8"
            )
            references = {audit_module.V1_IMAGE}
            for container, digest in pins.items():
                references.add(container)
                references.add(f"{container.rsplit(':', 1)[0]}@{digest}")
            ownership_lines = ["reference\timage_id\tcreated_by_job"]
            action_lines = ["reference\timage_id\taction"]
            for index, reference in enumerate(sorted(references), start=1):
                image_id = "sha256:" + f"{index + 100:064x}"
                created = str(index % 2)
                action = (
                    "REMOVED_JOB_CREATED" if created == "1" else "PRESERVED_PREEXISTING"
                )
                ownership_lines.append(f"{reference}\t{image_id}\t{created}")
                action_lines.append(f"{reference}\t{image_id}\t{action}")
            (context / "job-image-reference-ownership.tsv").write_text(
                "\n".join(ownership_lines) + "\n", encoding="utf-8"
            )
            actions_path = context / "job-image-reference-actions.tsv"
            actions_path.write_text("\n".join(action_lines) + "\n", encoding="utf-8")

            verified = audit_module.verify_job_image_actions(context)
            self.assertEqual(verified["reference_count"], len(references))
            actions_path.write_text(
                actions_path.read_text(encoding="utf-8").replace(
                    "REMOVED_JOB_CREATED", "PRESERVED_PREEXISTING", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                audit_module.AuditError, "invalid job image action"
            ):
                audit_module.verify_job_image_actions(context)


if __name__ == "__main__":
    unittest.main()
