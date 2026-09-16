"""Exercise folder discovery through generated config and the final run choice."""

import contextlib
import csv
import gzip
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import main
from oncotracer_cli.engine import parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import load_flat_yaml
from oncotracer_cli.system_check import GIB

HARDWARE = {
    "os": "Linux", "architecture": "x86_64", "python_supported": True,
    "cpu_workers_available": 6, "ram_total_bytes": 64 * GIB,
    "ram_available_bytes": 48 * GIB,
    "gpus": [{"index": 0, "name": "Test GPU", "memory_total_bytes": 8 * GIB,
              "memory_free_bytes": 6 * GIB}], "gpu_detection_status": "detected",
}


class WizardTests(unittest.TestCase):
    def fastq(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write("@read\nACGT\n+\nIIII\n")
        return path

    def invoke(self, *args, answers=None, final="save"):
        answers = answers or {}
        prompts = []
        output = io.StringIO()

        def answer(prompt):
            prompts.append(prompt)
            for key, value in answers.items():
                if prompt.startswith(key):
                    return value.pop(0) if isinstance(value, list) else value
            if prompt.startswith("Final action"):
                return final
            if prompt.startswith("Type for"):
                raise AssertionError("A sample type must be supplied explicitly: " + prompt)
            # Accept only displayed defaults. An unexpected required question
            # fails instead of inventing a response and concealing omissions.
            self.assertIn("[", prompt, "Unexpected required prompt: " + prompt)
            return ""

        with (
            patch("builtins.input", side_effect=answer),
            patch("oncotracer_cli.wizard.inspect_hardware", return_value=HARDWARE),
            patch("oncotracer_cli.cli._load_install_config", return_value={}),
            patch("oncotracer_cli.setup._run_setup", return_value=0) as run,
            patch("oncotracer_cli.cli.command_install") as install,
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
        ):
            code = main(list(args))
        install.assert_not_called()
        return code, output.getvalue(), prompts, run

    def test_illumina_roles_counts_and_custom_type_are_saved_without_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads, project = root / "reads with spaces", root / "project"
            for sample in ("case", "control", "other"):
                for mate in (1, 2):
                    self.fastq(reads / f"{sample}_R{mate}.fastq.gz")
            before = {p: p.read_bytes() for p in reads.iterdir()}
            code, output, prompts, run = self.invoke(
                "setup", "--terminal", "--project", str(project), "--input-folder", str(reads),
                answers={"Sequencing platform": "illumina", "Type for case": "cancer", "Type for control": "control",
                         "Type for other": "other", "Other sample type": "benign",
                         "Analysis group for other": "study", "CPU threads": "2"},
            )
            self.assertEqual(code, 0, output)
            run.assert_not_called()
            self.assertIn("Detected 6 FASTQ files in 3 samples", output)
            self.assertIn("Available CPU workers: 6", output)
            self.assertIn("Test GPU", output)
            config = load_flat_yaml(project / "config/run.yml")
            self.assertEqual(config["threads"], 2)
            samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
            self.assertEqual([(s.sample, s.status) for s in samples],
                             [("case", "tumor"), ("control", "normal"), ("other", "tumor")])
            with Path(config["sample_metadata"]).open() as handle:
                metadata = list(csv.DictReader(handle))
            self.assertEqual([row["sample_type"] for row in metadata], ["cancer", "control", "benign"])
            self.assertEqual(json.loads(metadata[2]["fastq_files"]), [str(reads / "other_R1.fastq.gz"), str(reads / "other_R2.fastq.gz")])
            self.assertTrue(config["hg38_auto_download"])
            self.assertFalse((project / "reference").exists())
            self.assertFalse((project / "results").exists())
            self.assertEqual(before, {p: p.read_bytes() for p in reads.iterdir()})
            self.assertTrue(prompts[-1].startswith("Final action"))

    def test_ont_discovers_batches_excludes_unclassified_and_maps_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads, project = root / "run/fastq_pass", root / "project"
            for barcode in ("barcode01", "barcode02", "unclassified"):
                for batch in (1, 2):
                    self.fastq(reads / barcode / f"batch{batch}.fastq.gz")
            code, output, prompts, run = self.invoke(
                "setup", "--terminal", "--project", str(project), "--input-folder", str(reads.parent),
                answers={"Sequencing platform": "ont", "Sample name": ["patient", "healthy"], "Type for patient": "cancer",
                         "Type for healthy": "control"},
            )
            self.assertEqual(code, 0, output)
            run.assert_not_called()
            config = load_flat_yaml(project / "config/run.yml")
            self.assertEqual(config["ont_caller"], "qdnaseq")
            self.assertEqual(config["ont_analysis_type"], "solid_biopsy")
            self.assertEqual(config["ont_binsize_kb"], 100)
            self.assertEqual([(s.sample, s.barcode, s.status) for s in parse_ont_samples(config)],
                             [("patient", "barcode01", "tumor"), ("healthy", "barcode02", "normal")])
            self.assertIn("Detected 6 FASTQ files in 3 samples", output)
            self.assertIn("ONT controls require", output)
            self.assertFalse(any("Type for unclassified" in prompt for prompt in prompts))

    def test_platform_is_asked_before_the_fastq_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fastq(root / "reads/library.fastq.gz")
            code, output, prompts, _ = self.invoke(
                "setup", "--terminal", "--project", str(root / "project"),
                answers={"Sequencing platform": "ont", "FASTQ folder": str(root / "reads"),
                         "Type for reads": "cancer"},
            )
            self.assertEqual(code, 0, output)
            self.assertTrue(prompts[0].startswith("Sequencing platform"))
            self.assertTrue(prompts[1].startswith("FASTQ folder"))

    def test_real_input_stream_with_69_barcode_fastqs_reaches_name_and_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads = root / "fastq_pass/barcode01"
            for batch in range(69):
                self.fastq(reads / f"batch{batch:03}.fastq.gz")
            for project_name, answers, expected_code in (
                ("complete", "all\n\ncancer\ncna\nichorcna\nno\nsave\n", 0),
                ("input-ended", "all\n", 2),
            ):
                project = root / project_name
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "oncotracer_cli.cli", "setup", "--terminal", "--project", str(project),
                     "--mode", "ont", "--input-folder", str(reads.parent), "--threads", "1",
                     "--backend", "conda", "--hg38_build"],
                    cwd=Path(__file__).resolve().parents[1], input=answers, text=True,
                    capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                self.assertIn("Selected: barcode01 (69 FASTQs)\nSample name [barcode01]: ", result.stdout)
                if expected_code == 0:
                    config = load_flat_yaml(project / "config/run.yml")
                    with Path(config["sample_metadata"]).open() as handle:
                        metadata, = csv.DictReader(handle)
                    self.assertEqual(len(json.loads(metadata["fastq_files"])), 69)
                    sample, = parse_ont_samples(config)
                    self.assertEqual((sample.sample, sample.fastq_dir), ("barcode01", reads))
                    self.assertFalse((project / "results").exists())
                else:
                    self.assertIn("setup input ended at Sample name", result.stderr)
                    self.assertFalse(project.exists())

    def test_ligation_folder_saves_one_sample_and_preserves_custom_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads = root / "ligation library"
            paths = [self.fastq(reads / f"batch{batch}.fastq.gz") for batch in range(3)]
            code, output, _, run = self.invoke(
                "setup", "--terminal", "--mode", "ont", "--project", str(root / "project"),
                "--input-folder", str(reads),
                answers={"Sample name": "sample_A", "Type for sample_A": "other",
                         "Other sample type": "research", "Analysis group": "study"},
            )
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(root / "project/config/run.yml")
            self.assertTrue(config["ont_single_sample"])
            self.assertEqual(config["ont_barcodes"], ".")
            sample, = parse_ont_samples(config)
            self.assertEqual((sample.sample, sample.fastq_dir), ("sample_A", reads))
            with Path(config["sample_metadata"]).open() as handle:
                metadata, = csv.DictReader(handle)
            self.assertEqual(metadata["sample_type"], "research")
            self.assertEqual(metadata["analysis_role"], "tumor")
            self.assertEqual(json.loads(metadata["fastq_files"]), [str(path) for path in paths])
            self.assertFalse((root / "project/results").exists())
            run.assert_not_called()

    def test_run_choice_or_run_flag_uses_saved_config_only_after_review(self):
        for flag in (False, True):
            with self.subTest(run_flag=flag), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fastq(root / "reads/sample.fastq.gz")
                project = root / "project"
                code, output, prompts, run = self.invoke(
                    "setup", "--terminal", "--mode", "illumina", "--project", str(project),
                    "--input-folder", str(root / "reads"), *( ["--run"] if flag else [] ),
                    answers={"Type for sample": "cancer"}, final="run",
                )
                self.assertEqual(code, 0, output)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0], project / "config/run.yml")
                self.assertTrue((project / "config/run.yml").is_file())
                self.assertIn("Configuration OK", output)
                self.assertEqual(any(p.startswith("Final action") for p in prompts), not flag)

    def test_invalid_threads_and_mixed_layout_selection_can_be_corrected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("paired_R1.fastq.gz", "paired_R2.fastq.gz", "single.fastq.gz"):
                self.fastq(root / "reads" / filename)
            code, output, _, run = self.invoke(
                "setup", "--terminal", "--mode", "illumina", "--project", str(root / "project"),
                "--input-folder", str(root / "reads"),
                answers={"Samples to include": ["0", "all", "2"], "Type for single": "control",
                         "CPU threads": ["0", "99", "two", "3"]},
            )
            self.assertEqual(code, 0, output)
            self.assertIn("Select either paired-end or single-end", output)
            config = load_flat_yaml(root / "project/config/run.yml")
            self.assertEqual(config["threads"], 3)
            samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
            self.assertEqual([(s.sample, s.status) for s in samples], [("single", "normal")])
            run.assert_not_called()

    def test_all_control_ont_fails_before_config_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fastq(root / "reads/fastq_pass/barcode01/batch.fastq.gz")
            code, output, _, run = self.invoke(
                "setup", "--terminal", "--project", str(root / "project"), "--input-folder", str(root / "reads"),
                answers={"Sequencing platform": "ont", "Type for barcode01": "control"},
            )
            self.assertEqual(code, 2, output)
            self.assertIn("at least one study sample", output)
            self.assertFalse((root / "project").exists())
            run.assert_not_called()

    def test_report_features_preserve_current_sample_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fastq(root / "reads/current.fastq.gz")
            code, output, _, _ = self.invoke(
                "setup", "--terminal", "--project", str(root / "project"), "--mode", "illumina",
                "--input-folder", str(root / "reads"),
                answers={"Type for current": "cancer", "Add CNA interpretation": "yes",
                         "Study context": "lymphoma"},
            )
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(root / "project/config/run.yml")
            self.assertTrue(config["run_cna_classifier"])
            self.assertEqual(config["cna_classifier_samples"], "current")
            self.assertEqual(config["cna_classifier_sample_set"], "lymphoma")
            for key in ("knowledge_web", "knowledge_literature_llm", "knowledge_deep_enable_llm_ranker", "pathology_use_biomed_models", "knowledge_catalog_llm", "run_gistic"):
                self.assertFalse(config[key], key)

    def test_local_catalog_models_do_not_enable_web_or_pathology_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fastq(root / "reads/current.fastq.gz")
            code, output, _, run = self.invoke(
                "setup", "--terminal", "--project", str(root / "project"), "--mode", "illumina",
                "--input-folder", str(root / "reads"),
                answers={"Type for current": "cancer", "Add CNA interpretation": "yes",
                         "Use local language models": "yes"},
            )
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(root / "project/config/run.yml")
            self.assertTrue(config["knowledge_catalog_llm"])
            for key in ("knowledge_web", "knowledge_literature_llm", "pathology_use_biomed_models"):
                self.assertFalse(config[key], key)
            run.assert_not_called()

    def test_explicit_gistic_is_required_and_needs_a_cohort(self):
        for count in (1, 2):
            with self.subTest(samples=count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in ("one", "two")[:count]:
                    self.fastq(root / f"reads/{name}.fastq.gz")
                code, output, prompts, run = self.invoke(
                    "setup", "--terminal", "--project", str(root / "project"), "--mode", "illumina",
                    "--input-folder", str(root / "reads"),
                    answers={"Type for one": "cancer", "Type for two": "cancer",
                             "Add CNA interpretation": "yes", "Add GISTIC": "yes"},
                )
                if count == 1:
                    self.assertEqual(code, 0, output)
                    self.assertIn("at least two", output)
                    config = load_flat_yaml(root / "project/config/run.yml")
                    self.assertFalse(config["run_gistic"])
                    self.assertFalse(config["gistic_required"])
                    self.assertTrue(config["run_cna_classifier"])
                    self.assertFalse(any(p.startswith("Add GISTIC") for p in prompts))
                else:
                    self.assertEqual(code, 0, output)
                    config = load_flat_yaml(root / "project/config/run.yml")
                    self.assertTrue(config["run_gistic"])
                    self.assertTrue(config["gistic_required"])
                run.assert_not_called()

    def test_existing_config_and_conflicting_flags_do_not_prompt_or_change_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "project/config/run.yml"
            config.parent.mkdir(parents=True)
            config.write_text("existing configuration\n")
            for extra in ([], ["--input-folder", str(root), "--non-interactive"],
                          ["--input-folder", str(root), "--manual"],
                          ["--input-folder", str(root), "--fastq-2", "mate.fastq.gz"],
                          ["--input-folder", str(root), "--status", "normal"],
                          ["--input-folder", str(root), "--reads-folder", "another-folder"]):
                code, output, prompts, run = self.invoke("setup", "--terminal", "--project", str(root / "project"), *extra)
                self.assertEqual(code, 2, output)
                self.assertEqual(prompts, [])
                self.assertEqual(config.read_text(), "existing configuration\n")
                run.assert_not_called()

    def test_reference_choices_remain_deferred_and_preserve_prepared_files(self):
        from tests.test_hg38_setup import Hg38SetupTests
        for mode in ("illumina", "ont"):
            for choice in ("reuse", "build"):
                with self.subTest(mode=mode, choice=choice), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    reads = root / "reads"
                    if mode == "illumina":
                        self.fastq(reads / "sample.fastq.gz")
                        name = "sample"
                    else:
                        self.fastq(reads / "fastq_pass/barcode01/reads.fastq.gz")
                        name = "barcode01"
                    reference = root / "shared reference"
                    build = Hg38SetupTests().fake_build(reference)
                    before = {p: p.read_bytes() for p in build.rglob("*") if p.is_file()}
                    code, output, _, run = self.invoke(
                        "setup", "--terminal", "--mode", mode, "--project", str(root / "project"),
                        "--input-folder", str(reads),
                        answers={f"Type for {name}": "cancer", "hg38 reference": choice,
                                 "Prepared OncoTracer reference": str(build)},
                    )
                    self.assertEqual(code, 0, output)
                    config = load_flat_yaml(root / "project/config/run.yml")
                    self.assertFalse(config["hg38_auto_download"])
                    self.assertEqual(config["lpwgs_root"], str(reference if choice == "reuse" else root / "project/reference"))
                    self.assertEqual(before, {p: p.read_bytes() for p in build.rglob("*") if p.is_file()})
                    self.assertFalse((root / "project/reference").exists())
                    run.assert_not_called()

    def test_eof_at_selection_or_reference_prompt_exits_without_writing(self):
        for stop in ("Samples to include", "Prepared OncoTracer reference"):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fastq(root / "reads/sample.fastq.gz")
                output = io.StringIO()
                def answer(prompt):
                    if prompt.startswith(stop):
                        raise EOFError
                    if prompt.startswith("Type for"):
                        return "cancer"
                    if prompt.startswith("hg38 reference"):
                        return "reuse"
                    self.assertIn("[", prompt)
                    return ""
                with (
                    patch("builtins.input", side_effect=answer),
                    patch("oncotracer_cli.wizard.inspect_hardware", return_value=HARDWARE),
                    patch("oncotracer_cli.cli._load_install_config", return_value={}),
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
                ):
                    code = main(["setup", "--terminal", "--mode", "illumina", "--project", str(root / "project"), "--input-folder", str(root / "reads")])
                self.assertEqual(code, 2, output.getvalue())
                self.assertIn("setup input ended", output.getvalue())
                self.assertFalse((root / "project").exists())


if __name__ == "__main__":
    unittest.main()
