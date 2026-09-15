"""Folder discovery must preserve real sample/file identities without input writes."""

import csv
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.discovery import detect_fastq_mode, discover_fastqs
from oncotracer_cli.engine import parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import OncoTracerError


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer-discovery-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reads = self.root / "reads"
        self.reads.mkdir()

    def fastq(self, relative):
        path = self.reads / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"@read\nACGT\n+\nIIII\n")
        return path

    def test_nested_illumina_pairs_export_to_native_samplesheet_without_input_writes(self):
        pairs = (
            ("patient_A/lane1/patient_A_S1_L001_R1_001.fastq.gz", "patient_A/lane1/patient_A_S1_L001_R2_001.fastq.gz"),
            ("patient_B/patient_B_1.fq", "patient_B/patient_B_2.fq"),
            ("patient_C/R1.fastq", "patient_C/R2.fastq"),
            ("patient_D/patient_D.R1.fastq", "patient_D/patient_D.R2.fastq"),
        )
        for pair in pairs:
            for name in pair:
                self.fastq(name)
        before = {path: path.read_bytes() for path in self.reads.rglob("*") if path.is_file()}
        self.assertEqual(detect_fastq_mode(self.reads), "illumina")
        found = discover_fastqs(self.reads, "illumina")
        self.assertEqual(found.root, self.reads)
        self.assertEqual([sample.sample for sample in found.samples], ["patient_A_S1", "patient_B", "patient_C", "patient_D"])
        sheet = self.root / "samples.csv"
        with sheet.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
            writer.writerows([sample.sample, sample.fastq_1, sample.fastq_2, "tumor"] for sample in found.samples)
        parsed = parse_illumina_samplesheet(sheet)
        self.assertEqual([sample.sample for sample in parsed], [sample.sample for sample in found.samples])
        self.assertEqual([sample.fastq_1 for sample in parsed], [self.reads / pair[0] for pair in pairs])
        self.assertEqual(before, {path: path.read_bytes() for path in self.reads.rglob("*") if path.is_file()})

    def test_plain_single_end_needs_platform_choice_and_suggests_valid_names(self):
        first = self.fastq("subfolder/sample one.fastq")
        second = self.fastq("other/patient-B.fq.gz")
        self.assertIsNone(detect_fastq_mode(self.reads))
        found = discover_fastqs(self.reads, "illumina")
        self.assertEqual([(sample.sample, sample.fastq_1, sample.fastq_2) for sample in found.samples], [("patient-B", second, None), ("sample_one", first, None)])
        self.assertIn("confirm or rename", found.warnings[0])

    def test_mixed_layout_candidates_are_available_for_wizard_selection(self):
        self.fastq("paired_R1.fastq")
        self.fastq("paired_R2.fastq")
        self.fastq("single.fastq")
        found = discover_fastqs(self.reads, "illumina")
        self.assertEqual([(sample.sample, sample.fastq_2 is not None) for sample in found.samples], [("paired", True), ("single", False)])

    def test_mate_matching_rejects_orphans_duplicate_versions_and_cross_lane_pairs(self):
        cases = (
            (("sample_R1.fastq",), "found 1 R1 and 0 R2"),
            (("sample_R2.fastq",), "found 0 R1 and 1 R2"),
            (("sample_R1.fastq", "sample_R1.fq.gz", "sample_R2.fastq"), "found 2 R1 and 1 R2"),
            (("sample_L001_R1_001.fastq", "sample_L002_R2_001.fastq"), "mate names do not match"),
            (("sample_R1_001.fastq", "sample_R2_002.fastq"), "mate names do not match"),
            (("sample_R1.fastq", "sample_2.fastq"), "mate names do not match"),
        )
        for index, (names, expected) in enumerate(cases):
            with self.subTest(names=names):
                directory = self.reads / str(index)
                for name in names:
                    self.fastq(f"{index}/{name}")
                with self.assertRaisesRegex(OncoTracerError, expected):
                    discover_fastqs(directory, "illumina")

    def test_multiple_lanes_cannot_become_separate_biological_samples(self):
        for lane in ("L001", "L002"):
            for mate in ("R1", "R2"):
                self.fastq(f"sample_S1_{lane}_{mate}_001.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "combine sequencing lanes"):
            discover_fastqs(self.reads, "illumina")

    def test_duplicate_sample_stems_across_directories_are_ambiguous(self):
        self.fastq("first/sample.fastq")
        self.fastq("second/sample.fastq")
        with self.assertRaisesRegex(OncoTracerError, "Ambiguous sample name"):
            discover_fastqs(self.reads, "illumina")

    def test_name_sanitizing_collision_cannot_overwrite_sample_identity(self):
        self.fastq("sample one.fastq")
        self.fastq("sample_one.fastq")
        with self.assertRaisesRegex(OncoTracerError, "Ambiguous sample name"):
            discover_fastqs(self.reads, "illumina")

    def test_single_and_paired_files_for_same_sample_are_ambiguous(self):
        for name in ("sample.fastq", "sample_R1.fastq", "sample_R2.fastq"):
            self.fastq(name)
        with self.assertRaisesRegex(OncoTracerError, "Ambiguous sample name"):
            discover_fastqs(self.reads, "illumina")

    def test_ont_nested_batches_ignore_fail_tree_and_map_independent_controls(self):
        self.fastq("run/fastq_pass/barcode01/batch1.fastq.gz")
        self.fastq("run/fastq_pass/barcode01/later/batch2.fastq.gz")
        self.fastq("run/fastq_pass/barcode02/batch1.fq")
        self.fastq("run/fastq_pass/unclassified/batch.fastq")
        self.fastq("run/fastq_fail/barcode01/failed.fastq")
        before = {path: path.read_bytes() for path in self.reads.rglob("*") if path.is_file()}
        self.assertEqual(detect_fastq_mode(self.reads), "ont")
        found = discover_fastqs(self.reads, "ont")
        self.assertEqual(found.root, self.reads / "run/fastq_pass")
        self.assertEqual([(sample.barcode, len(sample.files)) for sample in found.samples], [("barcode01", 2), ("barcode02", 1), ("unclassified", 1)])
        self.assertIn("Unclassified", found.warnings[0])
        config = dict(ont_folder=str(found.root), ont_barcodes="barcode01", ont_sample_names="Cancer_A", ont_normal_folder=str(found.root), ont_normal_barcodes="barcode02", ont_normal_sample_names="Control_A", ont_caller="qdnaseq", ont_analysis_type="solid_biopsy")
        parsed = parse_ont_samples(config)
        self.assertEqual([(sample.sample, sample.status, sample.fastq_dir) for sample in parsed], [("Cancer_A", "tumor", found.samples[0].fastq_dir), ("Control_A", "normal", found.samples[1].fastq_dir)])
        self.assertEqual(before, {path: path.read_bytes() for path in self.reads.rglob("*") if path.is_file()})

    def test_platform_detection_ignores_empty_failed_ont_batches(self):
        self.fastq("run/fastq_pass/barcode01/batch.fastq")
        self.fastq("run/fastq_fail/barcode01/failed.fastq").write_bytes(b"")
        self.assertEqual(detect_fastq_mode(self.reads), "ont")
        found = discover_fastqs(self.reads, "ont")
        self.assertEqual(len(found.samples), 1)
        self.assertEqual(len(found.samples[0].files), 1)

    def test_selecting_one_ont_barcode_does_not_select_its_siblings(self):
        first = self.fastq("fastq_pass/barcode01/batch.fastq")
        self.fastq("fastq_pass/barcode02/batch.fastq")
        found = discover_fastqs(first.parent, "ont")
        self.assertEqual(found.root, first.parent.parent)
        self.assertEqual([sample.barcode for sample in found.samples], ["barcode01"])

    def test_multiple_ont_runs_require_narrower_folder(self):
        for run in ("run1", "run2"):
            self.fastq(f"{run}/fastq_pass/barcode01/batch.fastq")
        with self.assertRaisesRegex(OncoTracerError, "exactly one fastq_pass"):
            discover_fastqs(self.reads, "ont")

    def test_ont_does_not_silently_drop_unmapped_fastqs(self):
        self.fastq("fastq_pass/barcode01/batch.fastq")
        self.fastq("fastq_pass/reads.fastq")
        with self.assertRaisesRegex(OncoTracerError, "outside a barcode"):
            discover_fastqs(self.reads / "fastq_pass", "ont")

    def test_empty_ont_barcode_is_omitted_without_inventing_a_sample(self):
        self.fastq("fastq_pass/barcode01/batch.fastq")
        (self.reads / "fastq_pass/barcode02").mkdir()
        found = discover_fastqs(self.reads / "fastq_pass", "ont")
        self.assertEqual([sample.barcode for sample in found.samples], ["barcode01"])

    def test_empty_fastq_fails_instead_of_producing_a_partial_mapping(self):
        self.fastq("first.fastq")
        self.fastq("second.fastq").write_bytes(b"")
        with self.assertRaisesRegex(OncoTracerError, "missing or empty"):
            discover_fastqs(self.reads, "illumina")

    def test_duplicate_symlink_to_same_fastq_is_not_an_independent_sample(self):
        read = self.fastq("first.fastq")
        (self.reads / "second.fastq").symlink_to(read)
        with self.assertRaisesRegex(OncoTracerError, "same FASTQ appears more than once"):
            discover_fastqs(self.reads, "illumina")

    def test_directory_symlink_loop_is_not_followed(self):
        self.fastq("sample.fastq")
        (self.reads / "loop").symlink_to(self.reads, target_is_directory=True)
        found = discover_fastqs(self.reads, "illumina")
        self.assertEqual(len(found.samples), 1)

    def test_empty_folder_and_invalid_platform_have_actionable_errors(self):
        with self.assertRaisesRegex(OncoTracerError, "No FASTQ"):
            detect_fastq_mode(self.reads)
        with self.assertRaisesRegex(OncoTracerError, "No FASTQ"):
            discover_fastqs(self.reads, "illumina")
        with self.assertRaisesRegex(OncoTracerError, "must be illumina or ont"):
            discover_fastqs(self.reads, "unknown")


if __name__ == "__main__":
    unittest.main()
