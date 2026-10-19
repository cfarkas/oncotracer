"""CpG continuation reuses saved ONT identity without running or changing it."""
from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.cli import parse_arguments
from oncotracer_cli.cpg_setup import prepare_cpg_setup
from oncotracer_cli.engine import parse_ont_samples
from oncotracer_cli.reference_bundle import _files, reference_paths
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml
from oncotracer_cli.setup import command_setup
from oncotracer_cli.web import WebState
from tests.test_native_methylation import Fixture
from tests.test_web import HARDWARE


class CpgSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch("oncotracer_cli.cli._load_install_config", return_value={}).start()
        self.project = self.root / "original ONT"
        (self.project / "config").mkdir(parents=True)
        self.reads = self.root / "original reads"
        for index in (1, 2, 3):
            folder = self.reads / f"barcode{index:02d}"
            folder.mkdir(parents=True)
            with gzip.open(folder / "reads.fastq.gz", "wt") as handle:
                handle.write(f"@read-{index}\nACGT\n+\nIIII\n")
        self.reference = self.project / "reference"
        for name in _files():
            asset = reference_paths(self.reference)[0] / name
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(b"synthetic path fixture; never processed")
        self.config = {"mode": "ont", "ont_folder": str(self.reads),
                       "ont_barcodes": "barcode01,barcode02", "ont_sample_names": "saved_A,saved_B",
                       "ont_normal_folder": str(self.reads), "ont_normal_barcodes": "barcode03",
                       "ont_normal_sample_names": "saved_control", "threads": 4,
                       "ont_caller": "qdnaseq", "ont_analysis_type": "solid_biopsy",
                       "lpwgs_root": str(self.reference), "outdir": str(self.project / "results"),
                       "execution_backend": "docker", "run_variants": True,
                       "variant_specimen_type": "fresh",
                       "sample_metadata": str(self.project / "config/sample_metadata.csv")}
        self.write_config()
        self.write_metadata()

    def write_config(self):
        (self.project / "config/run.yml").write_text(render_flat_yaml(self.config))

    def write_metadata(self):
        with (self.project / "config/sample_metadata.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample", "sample_type", "analysis_role", "fastq_files"])
            for name, kind, role, index in (("saved_A", "cancer", "tumor", 1),
                                             ("saved_B", "research context", "tumor", 2),
                                             ("saved_control", "normal", "normal", 3)):
                writer.writerow([name, kind, role, json.dumps([str(self.reads / f"barcode{index:02d}/reads.fastq.gz")])])

    def args(self, *extra):
        return parse_arguments(["--interactive", "--cpg-from", str(self.project), *extra])

    def state(self, *extra):
        state = WebState(self.root, self.args(*extra))
        state.hardware = dict(HARDWARE)
        return state

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def install_fixtures(self):
        for classifier in ("marlin", "sturgeon"):
            directory = self.root / classifier
            directory.mkdir()
            fixture = Fixture(directory, classifier)
            for key, value in fixture.config().items():
                if key.startswith(("methylation_", "marlin_", "sturgeon_")) and key not in (
                    "methylation_classifier", "methylation_pod5_dir"):
                    self.config[key] = value
        bam = self.root / "original.mod.bam"
        bam.write_bytes(b"synthetic path fixture; never processed")
        self.config["methylation_modbam"] = str(bam)
        self.write_config()

    def payload(self, state, classifier):
        system = state.system()
        return {"scan_id": system["cpg"]["scan"]["scan_id"],
                "samples": system["cpg"]["selections"],
                "project": system["cpg"]["projects"][classifier],
                "analysis": "methylation", "backend": "conda", "threads": 4,
                "variants": False, "classifier": classifier,
                "methylation_assignments": [{"id": 0, "classifier": "marlin"},
                                            {"id": 1, "classifier": "sturgeon"},
                                            {"id": 2, "classifier": ""}],
                "reference": "reuse", "reference_path": str(self.reference),
                "methylation_source": "modbam", "methylation_path": self.config.get("methylation_modbam", ""),
                "resources": str(self.project / "config/run.yml"),
                "accept_sturgeon_license": classifier == "sturgeon"}

    def use_named_libraries(self, *, signals=True):
        cohort = self.root / "named cohort"
        for index in (1, 2, 3):
            library = cohort / f"ONCO-{index:03d}"
            passed = library / "fastq_pass"
            passed.mkdir(parents=True)
            (self.reads / f"barcode{index:02d}").rename(passed / "barcode01")
            failed = library / "fastq_fail/ignored.fastq"
            failed.parent.mkdir()
            failed.write_text("@ignored\nACGT\n+\nIIII\n")
            if signals:
                bam = library / "modified_bam/matching.bam"
                bam.parent.mkdir()
                bam.write_bytes(b"synthetic original modified BAM; never processed")
                pod5 = library / "pod5_pass/barcode01/signal.pod5"
                pod5.parent.mkdir(parents=True)
                pod5.write_bytes(b"synthetic original raw signal; never processed")
        self.config.update(ont_named_samples=True, ont_folder=str(cohort),
                           ont_barcodes="ONCO-001,ONCO-002", ont_normal_folder=str(cohort),
                           ont_normal_barcodes="ONCO-003")
        self.config.pop("methylation_modbam", None)
        self.write_config()
        metadata = self.project / "config/sample_metadata.csv"
        with metadata.open() as handle:
            rows = list(csv.DictReader(handle))
        for index, row in enumerate(rows, 1):
            row["fastq_files"] = json.dumps([str(cohort / f"ONCO-{index:03d}/fastq_pass/barcode01/reads.fastq.gz")])
        with metadata.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        return cohort

    def test_named_cpg_restores_physical_libraries_and_saves_only_selected_group(self):
        self.install_fixtures()
        cohort = self.use_named_libraries()
        before = self.snapshot()
        state = self.state()
        system = state.system()
        self.assertTrue(state.cpg_context.discovery.named_samples)
        self.assertEqual(state.cpg_context.discovery.root, cohort)
        self.assertEqual(state.cpg_context.control_root, cohort)
        self.assertEqual([row.specimen_root for row in state.cpg_context.discovery.samples],
                         [cohort / f"ONCO-{index:03d}" for index in (1, 2, 3)])
        self.assertEqual(system["defaults"]["methylation_path"], str(cohort))
        self.assertEqual(system["defaults"]["methylation_source"], "pod5")
        self.assertFalse(system["cpg"]["missing_input"])
        payload = self.payload(state, "marlin")
        payload["methylation_path"] = str(cohort)
        payload["methylation_source"] = "pod5"
        with contextlib.redirect_stdout(io.StringIO()):
            saved = state.prepare(payload)
        self.assertTrue(saved["valid"], saved["check"])
        config = load_flat_yaml(Path(saved["config_path"]))
        self.assertIs(config["ont_named_samples"], True)
        self.assertEqual(config["ont_folder"], str(cohort))
        self.assertEqual(config["ont_barcodes"], "ONCO-001")
        self.assertEqual(config["methylation_pod5_dir"], str(cohort))
        self.assertFalse(config.get("methylation_modbam"))
        sample, = parse_ont_samples(config)
        self.assertEqual((sample.sample, sample.barcode, sample.specimen_root),
                         ("saved_A", "ONCO-001", cohort / "ONCO-001"))
        self.assertFalse(Path(config["outdir"]).exists())
        self.assertIsNone(state.job)
        self.assertTrue(all((self.root / path).read_bytes() == content for path, content in before.items()))

    def test_named_cpg_preserves_explicit_or_saved_modbam_despite_available_pod5(self):
        cohort = self.use_named_libraries()
        self.config["methylation_modbam"] = str(cohort)
        self.write_config()
        before = self.snapshot()
        saved = self.state().system()
        self.assertEqual(saved["defaults"]["methylation_source"], "modbam")
        self.assertEqual(saved["defaults"]["methylation_path"], str(cohort))
        self.assertEqual(self.snapshot(), before)
        self.config.pop("methylation_modbam")
        self.write_config()
        before = self.snapshot()
        explicit = self.state("--modbam", str(cohort)).system()
        self.assertEqual(explicit["defaults"]["methylation_source"], "modbam")
        self.assertEqual(explicit["defaults"]["methylation_path"], str(cohort))
        self.assertEqual(self.snapshot(), before)

    def test_named_cpg_fastq_only_does_not_invent_methylation_inputs(self):
        cohort = self.use_named_libraries(signals=False)
        for library in cohort.iterdir():
            failed = library / "bam_fail/failed.bam"
            failed.parent.mkdir()
            failed.write_bytes(b"failed source must not be reused")
        before = self.snapshot()
        system = self.state().system()
        self.assertTrue(system["cpg"]["missing_input"])
        self.assertFalse(system["defaults"].get("methylation_path"))
        self.assertEqual(self.snapshot(), before)

    def test_source_load_preserves_exact_identities_and_warns_without_resources(self):
        before = self.snapshot()
        with patch("subprocess.Popen", side_effect=AssertionError("loading dispatched a process")):
            state = self.state()
            system = state.system()
        restored = system["cpg"]
        self.assertEqual([row["name"] for row in restored["scan"]["samples"]],
                         ["saved_A", "saved_B", "saved_control"])
        self.assertEqual([(row["type"], row["label"], row["role"]) for row in restored["selections"]],
                         [("cancer", "", "tumor"), ("custom", "research context", "tumor"),
                          ("normal", "", "normal")])
        self.assertEqual(system["defaults"]["backend"], "conda")
        self.assertEqual(system["defaults"]["analysis"], "methylation")
        self.assertIs(system["defaults"]["variants"], False)
        self.assertEqual(system["defaults"]["reference_path"], str(self.reference))
        self.assertTrue(restored["missing_input"])
        self.assertTrue(all(restored["missing_resources"].values()))
        self.assertIsNone(system["defaults"]["classifier"])
        self.assertEqual(restored["projects"], {"marlin": str(self.root / "original ONT_MARLIN"),
                                              "sturgeon": str(self.root / "original ONT_Sturgeon")})
        self.assertEqual(before, self.snapshot())
        self.assertIsNone(state.job)

    def test_explicit_classifier_and_existing_resource_paths_are_prefilled_without_consent(self):
        self.install_fixtures()
        before = self.snapshot()
        with patch("subprocess.Popen", side_effect=AssertionError("loading ran a tool")):
            system = self.state("--classifier", "sturgeon", "--backend", "host").system()
        self.assertEqual(system["defaults"]["classifier"], "sturgeon")
        self.assertEqual(system["defaults"]["backend"], "host")
        self.assertFalse(system["defaults"]["accept_sturgeon_license"])
        self.assertFalse(system["cpg"]["missing_input"])
        self.assertEqual(system["cpg"]["missing_resources"], {"marlin": [], "sturgeon": []})
        self.assertEqual(before, self.snapshot())

    def test_executable_symlink_keeps_its_environment_in_defaults_and_saved_config(self):
        self.install_fixtures()
        environment = self.root / "methylation venv"
        (environment / "bin").mkdir(parents=True)
        interpreter = environment / "bin/python"
        interpreter.symlink_to(Path(sys.executable).resolve())
        (environment / "pyvenv.cfg").write_text("home = " + str(Path(sys.executable).resolve().parent) + "\n")
        self.config["marlin_python"] = str(interpreter)
        self.write_config()
        state = self.state()
        self.assertEqual(state.system()["cpg"]["resource_paths"]["marlin_python"], str(interpreter))
        self.assertEqual(state.setup_args.marlin_python, str(interpreter))
        self.assertNotEqual(str(interpreter.resolve()), str(interpreter))
        with contextlib.redirect_stdout(io.StringIO()):
            prepared = state.prepare(self.payload(state, "marlin"))
        self.assertTrue(prepared["valid"], prepared["check"])
        self.assertEqual(load_flat_yaml(Path(prepared["config_path"]))["marlin_python"], str(interpreter))

    def test_control_only_source_has_an_actionable_error(self):
        self.config["ont_barcodes"] = ","
        self.config["ont_sample_names"] = ","
        self.write_config()
        before = self.snapshot()
        with self.assertRaisesRegex(OncoTracerError, "at least one saved ONT study sample"):
            self.state()
        self.assertEqual(before, self.snapshot())

    def test_real_save_keeps_source_unchanged_and_selected_classifier_separate(self):
        self.install_fixtures()
        before = self.snapshot()
        state = self.state()
        for classifier, name, barcode in (("marlin", "saved_A", "barcode01"),
                                           ("sturgeon", "saved_B", "barcode02")):
            with self.subTest(classifier=classifier), contextlib.redirect_stdout(io.StringIO()):
                prepared = state.prepare(self.payload(state, classifier))
                self.assertTrue(prepared["valid"], prepared["check"])
                config = load_flat_yaml(Path(prepared["config_path"]))
                selected, = parse_ont_samples(config)
                self.assertEqual((selected.sample, selected.barcode), (name, barcode))
                self.assertEqual(config["methylation_classifier"], classifier)
                self.assertTrue(config["methylation_only"])
                self.assertFalse(config["run_variants"])
                self.assertEqual(Path(config["lpwgs_root"]), self.reference)
                self.assertFalse(Path(config["outdir"]).exists())
                self.assertIsNone(state.job)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})
        self.assertFalse((self.project / "results").exists())

    def test_invalid_project_mode_metadata_and_missing_reads_fail_before_writes(self):
        before = self.snapshot()
        args = self.args(); args.cpg_from = str(self.root / "missing")
        with self.assertRaisesRegex(OncoTracerError, "existing ONT"):
            prepare_cpg_setup(args)
        self.config["mode"] = "illumina"; self.write_config()
        with self.assertRaisesRegex(OncoTracerError, "ONT project"):
            self.state()
        self.config["mode"] = "ont"; self.write_config()
        self.config["ont_barcodes"] = "missing,barcode02"; self.write_config()
        with self.assertRaisesRegex(OncoTracerError, "directory not found"):
            self.state()
        self.config["ont_barcodes"] = "barcode01,barcode02"; self.write_config()
        (self.project / "config/sample_metadata.csv").write_text("sample,sample_type,analysis_role\nwrong,cancer,tumor\n")
        with self.assertRaisesRegex(OncoTracerError, "identities"):
            self.state()
        self.assertFalse((self.root / "original ONT_MARLIN").exists())
        self.assertEqual(before[str((self.reads / "barcode01/reads.fastq.gz").relative_to(self.root))],
                         (self.reads / "barcode01/reads.fastq.gz").read_bytes())

    def test_conflicting_modes_and_run_flags_never_open_browser(self):
        cases = [("--run",), ("--terminal",), ("--non-interactive",), ("--variants",),
                 ("--input-folder", str(self.reads)), ("--project", str(self.root / "other")),
                 ("--backend", "docker"), ("--mode", "illumina"), ("--analysis", "cna")]
        for extra in cases:
            with self.subTest(extra=extra), patch("oncotracer_cli.web.command_web") as browser:
                with self.assertRaises(OncoTracerError):
                    args = parse_arguments(["setup", "--cpg-from", str(self.project), *extra])
                    command_setup(args)
                browser.assert_not_called()

    def test_public_command_opens_only_browser_with_restored_context(self):
        before = self.snapshot()
        with patch("oncotracer_cli.web.command_web", return_value=0) as browser:
            self.assertEqual(command_setup(self.args("--classifier", "marlin")), 0)
        args = browser.call_args.args[0]
        self.assertIsNotNone(args._cpg_context)
        self.assertFalse(args.run)
        self.assertEqual(before, self.snapshot())

    def test_source_destination_aliases_and_changed_identity_are_rejected(self):
        self.install_fixtures()
        state = self.state()
        payload = self.payload(state, "marlin")
        alias = self.root / "source-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        for target in (self.project, self.project / "new", self.root, alias, self.reference,
                       self.reads / "barcode01"):
            with self.subTest(target=target), self.assertRaisesRegex(OncoTracerError, "separate CpG"):
                state.prepare(dict(payload, project=str(target)))
        renamed = [dict(row) for row in payload["samples"]]
        renamed[0]["name"] = "changed"
        with self.assertRaisesRegex(OncoTracerError, "names and roles"):
            state.prepare(dict(payload, samples=renamed))
        for change in ({"variants": True}, {"analysis": "both"}, {"scan_id": "different"}):
            with self.subTest(change=change), self.assertRaises(OncoTracerError):
                state.prepare(dict(payload, **change))
        with self.assertRaisesRegex(OncoTracerError, "reuse the saved"):
            state.prepare(dict(payload, reference="download"))
        self.assertFalse(Path(payload["project"]).exists())
        self.config["threads"] = 8; self.write_config()
        with self.assertRaisesRegex(OncoTracerError, "changed"):
            state.prepare(payload)
        self.assertFalse(Path(payload["project"]).exists())

    def test_changed_fastq_membership_is_not_silently_reused(self):
        self.install_fixtures()
        state = self.state()
        with gzip.open(self.reads / "barcode01/new.fastq.gz", "wt") as handle:
            handle.write("@new\nACGT\n+\nIIII\n")
        with self.assertRaisesRegex(OncoTracerError, "FASTQ files changed"):
            state.prepare(self.payload(state, "marlin"))

    def test_independent_control_folder_is_preserved(self):
        self.install_fixtures()
        control = self.root / "independent controls"
        control.mkdir()
        (self.reads / "barcode03").rename(control / "barcode03")
        self.config["ont_normal_folder"] = str(control); self.write_config()
        state = self.state()
        payload = self.payload(state, "marlin")
        payload["methylation_assignments"][2]["classifier"] = "marlin"
        with contextlib.redirect_stdout(io.StringIO()):
            result = state.prepare(payload)
        self.assertTrue(result["valid"], result["check"])
        config = load_flat_yaml(Path(result["config_path"]))
        selected = parse_ont_samples(config)
        self.assertEqual([(sample.sample, sample.status) for sample in selected],
                         [("saved_A", "tumor"), ("saved_control", "normal")])
        self.assertEqual(selected[1].fastq_dir, control / "barcode03")


if __name__ == "__main__":
    unittest.main()
