"""Public batch setup: safe generation, explicit identities, and reference choices."""

import csv
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli import cli
from oncotracer_cli.engine import parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import load_flat_yaml, sha256_file
from tests import test_documented_workflows as documented, test_hg38_setup


class BatchSetupTests(unittest.TestCase):
    cli = documented.DocumentedWorkflowTests.cli
    fastq = documented.DocumentedWorkflowTests.fastq

    def fixture(self, base, mode="illumina", table=None):
        reads = base / "reads"
        if mode == "illumina":
            for sample in ("Tumor_A", "Control_A"):
                for end in ("R1", "R2"):
                    self.fastq(reads / f"{sample}_{end}.fastq.gz", sample + end)
            contents = "sample_name,status\nTumor_A,TUMOR\nControl_A,NORMAL\n"
        else:
            for barcode in ("barcode01", "barcode02", "unclassified"):
                self.fastq(reads / barcode / "reads.fastq.gz", barcode)
            contents = "barcode,sample_name,status\nbarcode02,Tumor_A,TUMOR\nbarcode01,Control_A,NORMAL\n"
        sheet = base / "samples.csv"
        sheet.write_text(contents if table is None else table)
        return ["auto", "--mode", mode, "--reads-folder", str(reads), "--sample-table", str(sheet), "--config-dir", str(base / "config"), "--outdir", str(base / "results")]

    def test_reference_defaults_and_local_build_for_both_platforms(self):
        for mode in ("illumina", "ont"):
            for flags, download in (([], True), (["--hg38_build"], True), (["--build_reference"], False)):
                with self.subTest(mode=mode, flags=flags), tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    args = self.fixture(base, mode)
                    code, output = self.cli(*args, *flags, "--threads", "2")
                    self.assertEqual(code, 0, output)
                    config_path = base / f"config/{mode}.auto.yml"
                    config = load_flat_yaml(config_path)
                    self.assertEqual(config["hg38_auto_download"], download)
                    self.assertEqual(config["lpwgs_root"], str(base / "config/reference"))
                    self.assertEqual(config["threads"], 2)
                    self.assertFalse(config["knowledge_web"])
                    self.assertIn("Selected samples: 2 (1 TUMOR, 1 NORMAL)", output)
                    self.assertIn("oncotracer check --config", output)
                    self.assertIn("oncotracer run --backend conda --config", output)
                    self.assertFalse((base / "results").exists())
                    self.assertFalse((base / "config/reference").exists())
                    self.assertEqual(self.cli("check", "--config", str(config_path))[0], 0)
                    with (base / "config/auto_params_manifest.tsv").open() as handle:
                        record, = csv.DictReader(handle, delimiter="\t")
                    self.assertEqual(record["yaml_sha256"], sha256_file(config_path))
                    if mode == "illumina":
                        samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
                        self.assertEqual([s.status for s in samples], ["tumor", "normal"])
                        self.assertEqual(record["samplesheet_sha256"], sha256_file(Path(config["illumina_samplesheet"])))
                    else:
                        self.assertEqual(config["ont_caller"], "qdnaseq")
                        self.assertEqual(config["ont_binsize_kb"], 100)
                        self.assertEqual([(s.sample, s.barcode, s.status) for s in parse_ont_samples(config)], [("Tumor_A", "barcode02", "tumor"), ("Control_A", "barcode01", "normal")])

    def test_prepared_reference_parent_and_actual_folder_are_read_only(self):
        for mode in ("illumina", "ont"):
            for owned in (True, False):
                for direct in (True, False):
                    with self.subTest(mode=mode, owned=owned, direct=direct), tempfile.TemporaryDirectory() as temporary:
                        base = Path(temporary)
                        reference = base / "shared reference"
                        build = test_hg38_setup.Hg38SetupTests().fake_build(reference, owned=owned)
                        before = {p.relative_to(reference): p.read_bytes() for p in reference.rglob("*") if p.is_file()}
                        code, output = self.cli(*self.fixture(base, mode), "--hg38_build", str(build if direct else reference))
                        self.assertEqual(code, 0, output)
                        config = load_flat_yaml(base / f"config/{mode}.auto.yml")
                        self.assertEqual(config["lpwgs_root"], str(reference))
                        self.assertFalse(config["hg38_auto_download"])
                        self.assertEqual(before, {p.relative_to(reference): p.read_bytes() for p in reference.rglob("*") if p.is_file()})

    def test_quoted_spreadsheet_csv_and_special_character_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'study #1, donor\'s "quoted": project'
            args = self.fixture(base, table='\ufeffsample_name,status\r\n"Tumor_A","TUMOR"\r\nControl_A, NORMAL\r\n')
            code, output = self.cli(*args)
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(base / "config/illumina.auto.yml")
            self.assertEqual(config["outdir"], str(base / "results"))
            self.assertEqual(config["lpwgs_root"], str(base / "config/reference"))
            samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
            self.assertEqual(samples[0].fastq_1, base / "reads/Tumor_A_R1.fastq.gz")
            self.assertEqual(self.cli("check", "--config", str(base / "config/illumina.auto.yml"))[0], 0)

    def test_rerunning_auto_preserves_existing_configuration_and_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            args = self.fixture(base)
            self.assertEqual(self.cli(*args)[0], 0)
            before = {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            code, output = self.cli(*args, "--threads", "1")
            self.assertEqual(code, 2, output)
            self.assertIn("will not overwrite", output)
            self.assertIn("To resume", output)
            self.assertEqual(before, {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()})

    def test_bad_tables_fail_before_publishing_settings(self):
        cases = (
            ("ont", "sample_name,status\nTumor_A,TUMOR\n", "barcode,sample_name,status"),
            ("ont", "barcode,sample_name,status\n../reads,Tumor_A,TUMOR\n", "folder name, not a path"),
            ("ont", "barcode,sample_name,status\nbarcode01,Tumor_A,TUMOR\nbarcode01,Control_A,NORMAL\n", "duplicate ONT barcode"),
            ("illumina", "sample_name,status\nTumor_A,TUMOR,extra\n", "row 2"),
            ("illumina", "sample_name,status\nTumor_A,\n", "nonempty fields"),
            ("illumina", "sample,fastq_1,fastq_2,status\n", "setup --samplesheet"),
            ("illumina", "sample_name,status\nTumor_A,TUMOR\nTumor_A,NORMAL\n", "duplicate sample ID"),
        )
        for mode, table, message in cases:
            with self.subTest(mode=mode, table=table), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                code, output = self.cli(*self.fixture(base, mode, table))
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)
                self.assertFalse(list((base / "config").iterdir()))
                self.assertFalse((base / "results").exists())

    def test_corrupt_fastq_does_not_publish_or_create_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            args = self.fixture(base)
            (base / "reads/Tumor_A_R1.fastq.gz").write_bytes(b"incomplete gzip")
            code, output = self.cli(*args)
            self.assertEqual(code, 2, output)
            self.assertIn("corrupt or incomplete", output)
            self.assertFalse(list((base / "config").iterdir()))
            self.assertFalse((base / "results").exists())

    def test_dry_run_does_not_create_config_results_or_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            code, output = self.cli(*self.fixture(base), "--build_reference", "--dry-run")
            self.assertEqual(code, 0, output)
            self.assertFalse((base / "config").exists())
            self.assertFalse((base / "results").exists())

    def test_reference_flags_conflict_and_invalid_build_fails_before_writing(self):
        for flags in (("--hg38_build", "--build_reference"), ("--build_reference", "--hg38_build", "/reference")):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["auto", "--mode", "illumina", "--reads-folder", "/reads", "--sample-table", "/samples.csv", *flags])
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            code, output = self.cli(*self.fixture(base), "--hg38_build", str(base / "reads"))
            self.assertEqual(code, 2, output)
            self.assertIn("prepared illumina hg38 build", output)
            self.assertFalse((base / "config").exists())

    def test_report_options_are_explicit_and_do_not_duplicate_yaml_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            args = self.fixture(base)
            code, output = self.cli(*args, "--no-pathology-models")
            self.assertEqual(code, 2, output)
            self.assertFalse((base / "config").exists())
            code, output = self.cli(*args, "--run-cna-classifier", "--cna-classifier-sample-set", "sarcoma", "--no-pathology-models")
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(base / "config/illumina.auto.yml")
            self.assertEqual(config["cna_classifier_sample_set"], "sarcoma")
            self.assertTrue(config["run_cna_classifier"])
            self.assertFalse(config["pathology_use_biomed_models"])
            for key in ("knowledge_web", "knowledge_literature_llm", "knowledge_deep_literature"):
                self.assertFalse(config[key])


if __name__ == "__main__":
    unittest.main()
