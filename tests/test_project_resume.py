import fcntl
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.output_safety import (_create_owner, current_runtime_identity,
    OUTPUT_LOCK_RELATIVE, OUTPUT_OWNER_RELATIVE, _canonical_path_sha256)
from oncotracer_cli.project_resume import resume_project, sample_snapshot
from oncotracer_cli.runtime import OncoTracerError, StageLedger, load_flat_yaml
from oncotracer_cli.setup import _render_config


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "original"
        (self.project / "config").mkdir(parents=True)
        self.output = self.project / "results"
        self.output.mkdir()
        self.identity = current_runtime_identity()
        _create_owner(self.output, self.identity)
        self.fastq = self.root / "sample.fastq"
        self.fastq.write_text("@read\nACGT\n+\nIIII\n")
        sheet = self.project / "config/samplesheet.csv"
        sheet.write_text(f"sample,fastq_1,fastq_2,status\ncase,{self.fastq},,tumor\n")
        self.config = {"mode": "illumina", "outdir": str(self.output),
                       "illumina_samplesheet": str(sheet), "threads": 20, "execution_backend": "host"}
        (self.project / "config/run.yml").write_text(_render_config(self.config))
        self.patch = patch("oncotracer_cli.project_resume._runtime", return_value=self.identity)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def move(self):
        target = self.root / "Paper" / "outputs" / "study"
        target.parent.mkdir(parents=True)
        self.project.rename(target)
        return target

    def test_move_keeps_signature_and_checks_output_content(self):
        product = self.output / "counts.txt"
        product.write_text("original counts\n")
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        command = ["count", str(product)]
        signature = ledger.signature("count", command, [self.fastq])
        ledger.complete("count", signature, [product])
        target = self.move()
        result = resume_project(target)
        self.assertTrue(result["moved"])
        config = load_flat_yaml(target / "config/run.yml")
        self.assertEqual(config["outdir"], str(target / "results"))
        self.assertEqual(config["illumina_samplesheet"], str(target / "config/samplesheet.csv"))
        self.assertTrue(Path(result["backup"]).joinpath("run.yml").is_file())
        owner = json.loads((target / "results" / OUTPUT_OWNER_RELATIVE).read_text())
        self.assertEqual(owner["canonical_path_sha256"], _canonical_path_sha256(target / "results"))
        product = target / "results/counts.txt"
        ledger = StageLedger(target / "results/.oncotracer-native/state.json")
        resumed = ledger.signature("count", ["count", str(product)], [self.fastq])
        self.assertEqual(resumed, signature)
        self.assertTrue(ledger.reusable("count", resumed, [product]))
        product.write_text("modified counts\n")
        self.assertFalse(ledger.reusable("count", resumed, [product]))
        self.assertEqual(sample_snapshot(config)[1][0]["name"], "case")

    def test_changed_input_invalidates_reuse(self):
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        signature = ledger.signature("test", ["test"], [self.fastq])
        target = self.move()
        resume_project(target)
        self.fastq.write_text("different reads")
        ledger = StageLedger(target / "results/.oncotracer-native/state.json")
        self.assertNotEqual(signature, ledger.signature("test", ["test"], [self.fastq]))

    def test_second_move_reuses_stages_completed_after_first_move(self):
        first = self.move()
        resume_project(first)
        product = first / "results/new-stage.txt"
        product.write_text("completed after first move")
        ledger = StageLedger(first / "results/.oncotracer-native/state.json")
        signature = ledger.signature("new", ["output", str(product)], [self.fastq])
        ledger.complete("new", signature, [product])
        second = first.with_name("second-location")
        first.rename(second)
        resume_project(second)
        product = second / "results/new-stage.txt"
        ledger = StageLedger(second / "results/.oncotracer-native/state.json")
        self.assertEqual(ledger.signature("new", ["output", str(product)], [self.fastq]), signature)
        self.assertTrue(ledger.reusable("new", signature, [product]))

    def test_failed_relocation_rolls_back_symlinks_and_config(self):
        (self.output / "data.txt").write_text("data")
        (self.output / "alias.txt").symlink_to(self.output / "data.txt")
        target = self.move()
        before = (target / "config/run.yml").read_bytes()
        old_target = str(self.output / "data.txt")
        with patch("oncotracer_cli.project_resume.atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                resume_project(target)
        self.assertEqual((target / "config/run.yml").read_bytes(), before)
        self.assertEqual(str((target / "results/alias.txt").readlink()), old_target)
        self.assertFalse((target / "config/resume.json").exists())

    def test_running_project_is_not_modified(self):
        config = self.project / "config/run.yml"
        original = config.read_bytes()
        with (self.output / OUTPUT_LOCK_RELATIVE).open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(OncoTracerError, "still running"):
                resume_project(self.project)
        self.assertEqual(original, config.read_bytes())
        self.assertFalse((self.project / "config/resume.json").exists())

    def test_copy_cannot_silently_adopt_same_project(self):
        copy = self.root / "copy"
        shutil.copytree(self.project, copy)
        with self.assertRaisesRegex(OncoTracerError, "original project still exists"):
            resume_project(copy)

    def test_unowned_results_are_rejected(self):
        (self.output / OUTPUT_OWNER_RELATIVE).unlink()
        with self.assertRaises(OncoTracerError):
            resume_project(self.project)

    def test_runtime_change_is_recorded_without_blessing_stages(self):
        identity = dict(self.identity, source_commit="a" * 40)
        with patch("oncotracer_cli.project_resume._runtime", return_value=identity):
            resume_project(self.project)
        owner = json.loads((self.output / OUTPUT_OWNER_RELATIVE).read_text())
        receipt = json.loads((self.project / "config/resume.json").read_text())
        self.assertEqual(owner["runtime_identity"], identity)
        self.assertEqual(receipt["previous_owner"]["runtime_identity"], self.identity)
        self.assertFalse((self.output / ".oncotracer-native/state.json").exists())

    def test_internal_symlink_rebased_external_file_unchanged(self):
        (self.output / "data.txt").write_text("data")
        (self.output / "alias.txt").symlink_to(self.output / "data.txt")
        target = self.move()
        resume_project(target)
        self.assertEqual((target / "results/alias.txt").read_text(), "data")
        self.assertTrue(self.fastq.is_file())

    def test_browser_resume_retains_sample_roles_and_waits_for_run(self):
        from oncotracer_cli.web import WebState
        state = WebState(self.root)
        with patch("oncotracer_cli.web.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"errors":[]}', '')):
            prepared = state.resume({"project": str(self.project)})
        self.assertTrue(prepared["valid"])
        self.assertEqual(prepared["reviewed_summary"]["samples"][0]["analysis_role"], "tumor")
        self.assertEqual(prepared["reviewed_summary"]["config"]["threads"], 20)
        self.assertIsNone(state.job)
        self.fastq.write_text("changed")
        with self.assertRaisesRegex(OncoTracerError, "FASTQ inputs changed"):
            state.run({"project_id": prepared["id"]})


if __name__ == "__main__":
    unittest.main()
