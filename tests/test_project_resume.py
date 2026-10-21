import fcntl
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.output_safety import (_create_owner, current_runtime_identity,
    OUTPUT_LOCK_RELATIVE, OUTPUT_OWNER_RELATIVE, _canonical_path_sha256)
from oncotracer_cli.project_resume import _runtime, resume_project, sample_snapshot, audit_intermediates
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

    def test_selected_resource_budget_keeps_samples_and_stage_records(self):
        stage = self.output / 'existing.bam'
        stage.write_bytes(b'completed fixture')
        ledger = StageLedger(self.output / '.oncotracer-native/state.json')
        ledger.complete('fixture', 'original-signature', [stage])
        before = ledger.path.read_bytes()
        sheet_before = (self.project / 'config/samplesheet.csv').read_bytes()
        with patch('oncotracer_cli.sample_resources.inspect_hardware', return_value={
                'cpu_workers_available': 32, 'ram_available_bytes': 128 * 1024**3}):
            result = resume_project(self.project, resources={
                'threads': 16, 'parallel_samples': 2, 'memory': '64GB'})
        config = load_flat_yaml(Path(result['config_path']))
        self.assertEqual(config['parallel_samples'], 2)
        self.assertEqual(config['variant_parallel_samples'], 2)
        self.assertEqual(config['analysis_memory'], '64GB')
        self.assertEqual(config['threads'], 16)
        self.assertEqual(ledger.path.read_bytes(), before)
        self.assertEqual((self.project / 'config/samplesheet.csv').read_bytes(), sheet_before)
        self.assertTrue(Path(result['backup']).is_dir())

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

    def test_overwritten_moved_config_recovers_matching_backup(self):
        from oncotracer_cli.setup import _save_setup_files
        target = self.move()
        backup = target / "config/backups/20261019T000000Z-fixture"
        shutil.copytree(target / "config", backup, ignore=shutil.ignore_patterns("backups"))
        current = dict(self.config, outdir=str(target / "results"), threads=1,
                       lpwgs_root=str(target / "reference"))
        (target / "config/run.yml").write_text(_render_config(current))
        result = resume_project(target)
        saved = load_flat_yaml(target / "config/run.yml")
        self.assertEqual(saved["threads"], 20)
        self.assertEqual(saved["outdir"], str(target / "results"))
        self.assertEqual(result["recovered_configuration"], str(backup / "run.yml"))
        self.assertEqual(load_flat_yaml(Path(result["backup"]) / "run.yml")["threads"], 1)
        with self.assertRaisesRegex(OncoTracerError, "recorded analysis outputs"):
            _save_setup_files(target, {"run.yml": "threads: 2\n"})
        self.assertEqual(load_flat_yaml(target / "config/run.yml"), saved)

    def test_native_run_relocates_before_executing_pipeline(self):
        from oncotracer_cli.cli import main
        target = self.move()
        def validate_before_execution(path, args):
            saved = load_flat_yaml(path)
            self.assertEqual(saved["outdir"], str(target / "results"))
            self.assertEqual(args.backend, "host")
            self.assertEqual(json.loads((target / "results" / OUTPUT_OWNER_RELATIVE).read_text())["canonical_path_sha256"], _canonical_path_sha256(target / "results"))
            return target / "results"
        with patch("oncotracer_cli.cli._preflight_host_runtime"), patch("oncotracer_cli.cli._prepare_configured_hg38"), patch("oncotracer_cli.cli._run_host", side_effect=validate_before_execution) as run, contextlib.redirect_stdout(io.StringIO()) as text:
            self.assertEqual(main(["run", "--config", str(target / "config/run.yml"), "--backend", "host"]), 0)
        run.assert_called_once()
        self.assertIn("Intermediate output checks", text.getvalue())

    def test_unrelated_backup_does_not_authorize_adoption(self):
        target = self.move()
        backup = target / "config/backups/fixture"
        backup.mkdir(parents=True)
        current = dict(self.config, outdir=str(target / "results"))
        text = _render_config(current)
        (target / "config/run.yml").write_text(text)
        (backup / "run.yml").write_text(text)
        with self.assertRaisesRegex(OncoTracerError, "saved backups"):
            resume_project(target)
        self.assertEqual((target / "config/run.yml").read_text(), text)

    def test_audit_marks_missing_and_changed_outputs_for_recomputation(self):
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        for name in ("valid", "changed", "missing"):
            product = self.output / (name + ".txt")
            product.write_text(name)
            ledger.complete(name, ledger.signature(name, [name], [self.fastq]), [product])
        target = self.move()
        resume_project(target)
        (target / "results/changed.txt").write_text("changed after completion")
        (target / "results/missing.txt").rename(self.root / "missing-stage-output.txt")
        audit = audit_intermediates(target)
        self.assertEqual(audit["recorded_stages"], 3)
        self.assertEqual(audit["integrity_passed"], 1)
        self.assertEqual(audit["needs_recompute"], 2)

    def test_unrecorded_alignment_and_sort_fragments_are_not_complete(self):
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        bam = self.output / "interrupted.bam"
        index = self.output / "interrupted.bam.bai"
        fragment = self.output / "interrupted.bam.tmp.0000.bam"
        for path in (bam, index, fragment):
            path.write_bytes(b"partial output must not imply successful completion")
        self.assertFalse(ledger.reusable("illumina-align-interrupted", "signature", [bam, index]))
        self.assertFalse(ledger.reusable("illumina-align-interrupted", "signature", [fragment]))
        self.assertEqual(audit_intermediates(self.project)["integrity_passed"], 0)
        ledger.complete("previous", "previous-signature", [bam, index])
        index.unlink()
        self.assertFalse(ledger.reusable("previous", "previous-signature", [bam, index]))

    def test_reads_folder_is_rejected_before_docker_or_mutation(self):
        reads = self.root / "raw_data/ONT"
        reads.mkdir(parents=True)
        with patch("oncotracer_cli.project_resume._runtime") as runtime:
            with self.assertRaisesRegex(OncoTracerError, "analysis output project"):
                resume_project(reads, backend="docker", image="oncotracer:missing")
            runtime.assert_not_called()
        self.assertFalse((reads / "config").exists())
        self.assertFalse((reads / "results").exists())

    def test_audit_validates_configured_shared_reference_but_not_arbitrary_paths(self):
        reference = self.root / "shared/reference"
        product = reference / "references/samurai_hg38/bwa/genome.bwt"
        product.parent.mkdir(parents=True)
        product.write_text("synthetic reference index")
        config = dict(self.config, lpwgs_root=str(reference))
        (self.project / "config/run.yml").write_text(_render_config(config))
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        ledger.complete("reference-bwa", "signature", [product])
        unrelated = self.root / "unrelated.txt"
        unrelated.write_text("not a reference or analysis output")
        ledger.complete("unrelated", "signature", [unrelated])
        audit = audit_intermediates(self.project)
        self.assertEqual(audit["integrity_passed"], 1)
        self.assertTrue(next(row for row in audit["stages"] if row["stage"] == "reference-bwa")["integrity_passed"])
        self.assertEqual(audit["needs_recompute"], 1)

    def test_changed_input_invalidates_reuse(self):
        ledger = StageLedger(self.output / ".oncotracer-native/state.json")
        signature = ledger.signature("test", ["test"], [self.fastq])
        target = self.move()
        resume_project(target)
        self.fastq.write_text("different reads")
        ledger = StageLedger(target / "results/.oncotracer-native/state.json")
        self.assertNotEqual(signature, ledger.signature("test", ["test"], [self.fastq]))

    def test_docker_check_validates_selected_image_identity_and_never_adopts_mismatch(self):
        import contextlib
        import io
        from oncotracer_cli.cli import main
        from oncotracer_cli.docker_runtime import host_preview_runtime_identity
        identity = dict(self.identity, binary_sha256="a" * 64)
        marker = self.output / OUTPUT_OWNER_RELATIVE
        owner = json.loads(marker.read_text())
        owner["runtime_identity"] = identity
        marker.write_text(json.dumps(owner))
        original_owner = marker.read_bytes()
        config = dict(self.config, execution_backend="docker", docker_image="test:fixture")
        (self.project / "config/run.yml").write_text(_render_config(config))
        for actual, expected in ((identity, 0), (dict(identity, binary_sha256="b" * 64), 2)):
            with self.subTest(matching_image=expected == 0):
                output = io.StringIO()
                with patch("oncotracer_cli.project_resume._runtime", return_value=actual), contextlib.redirect_stdout(output):
                    code = main(["check", "--config", str(self.project / "config/run.yml"), "--json"])
                self.assertEqual(code, expected, output.getvalue())
                self.assertEqual(json.loads(output.getvalue())["valid"], expected == 0)
                self.assertEqual(marker.read_bytes(), original_owner)
                self.assertIsNone(host_preview_runtime_identity())

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
        (self.output / OUTPUT_OWNER_RELATIVE).rename(self.root / "removed-output-owner.json")
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

    def test_explicit_docker_image_is_saved_and_verified(self):
        resume_project(self.project, backend="docker", image="oncotracer:tested")
        config = load_flat_yaml(self.project / "config/run.yml")
        self.assertEqual(config["docker_image"], "oncotracer:tested")
        self.assertEqual(config["execution_backend"], "docker")
        with self.assertRaisesRegex(OncoTracerError, "Docker"):
            resume_project(self.project, backend="conda", image="oncotracer:tested")

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

    def test_browser_can_correct_or_clear_launch_image_override(self):
        from oncotracer_cli.web import WebState
        config = dict(self.config, execution_backend="docker", docker_image="oncotracer:saved")
        config_path = self.project / "config/run.yml"
        for image, expected in (("oncotracer:corrected", "oncotracer:corrected"), ("", "oncotracer:saved")):
            with self.subTest(image=image):
                config_path.write_text(_render_config(config))
                state = WebState(self.root, SimpleNamespace(backend="docker", image="oncotracer:typo"))
                with patch("oncotracer_cli.project_resume._runtime", return_value=self.identity) as runtime, \
                     patch("oncotracer_cli.web.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"errors":[]}', '')):
                    result = state.resume({"project": str(self.project), "backend": "docker", "image": image})
                self.assertTrue(result["valid"])
                self.assertEqual(runtime.call_args.args[1]["docker_image"], expected)
                self.assertEqual(load_flat_yaml(config_path)["docker_image"], expected)
                self.assertIsNone(state.job)

    def test_browser_saved_backend_ignores_cleared_cli_overrides(self):
        from oncotracer_cli.web import WebState
        state = WebState(self.root, SimpleNamespace(backend="docker", image="oncotracer:typo"))
        with patch("oncotracer_cli.project_resume._runtime", return_value=self.identity) as runtime, \
             patch("oncotracer_cli.web.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"errors":[]}', '')):
            result = state.resume({"project": str(self.project), "backend": "", "image": ""})
        self.assertEqual(runtime.call_args.args[0], "host")
        self.assertEqual(result["backend"], "host")
        self.assertIsNone(state.job)

    def test_missing_image_does_not_modify_saved_project_or_outputs(self):
        state = StageLedger(self.output / ".oncotracer-native/state.json")
        product = self.output / "complete.txt"
        product.write_text("complete")
        state.complete("complete", "signature", [product])
        paths = [self.project / "config/run.yml", self.output / OUTPUT_OWNER_RELATIVE, state.path, product]
        before = {p: p.read_bytes() for p in paths}
        with patch("oncotracer_cli.project_resume._runtime", side_effect=OncoTracerError("Docker image was not found")):
            with self.assertRaisesRegex(OncoTracerError, "not found"):
                resume_project(self.project, backend="docker", image="oncotracer:typo")
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertFalse((self.project / "config/resume.json").exists())


class RuntimeIdentityTests(unittest.TestCase):
    def test_missing_docker_tag_explains_browser_recovery_without_fallback(self):
        from oncotracer_cli.cli import DEFAULT_IMAGE
        failure = subprocess.CompletedProcess([], 125, "", "manifest for oncotracer:typo not found: manifest unknown")
        with patch("oncotracer_cli.project_resume.subprocess.run", return_value=failure) as command:
            with self.assertRaises(OncoTracerError) as caught:
                _runtime("docker", {"docker_image": "oncotracer:typo"})
        self.assertIn("oncotracer:typo", str(caught.exception))
        self.assertIn("Use current image", str(caught.exception))
        self.assertIn(DEFAULT_IMAGE, str(caught.exception))
        self.assertNotIn("Run 'docker run --help'", str(caught.exception))
        self.assertEqual(command.call_count, 1)

    def test_docker_queries_actual_launcher_not_unstamped_source(self):
        identity = current_runtime_identity()
        with patch("oncotracer_cli.project_resume.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(identity), "")) as command:
            self.assertEqual(_runtime("docker", {"docker_image": "oncotracer:tested"}), identity)
        args = command.call_args.args[0]
        self.assertEqual(args[-3:], ["oncotracer:tested", "provenance", "--runtime-identity"])
        self.assertNotIn("--entrypoint", args)

    def test_unverifiable_docker_does_not_authorize_resume(self):
        for status, output in ((2, ""), (0, "not json"), (0, "{}")):
            with self.subTest(status=status, output=output), patch(
                "oncotracer_cli.project_resume.subprocess.run", return_value=subprocess.CompletedProcess([], status, output, "unsupported")):
                with self.assertRaises(OncoTracerError):
                    _runtime("docker", {"docker_image": "oncotracer:old"})

    def test_runtime_identity_command_uses_analysis_payload_root(self):
        from oncotracer_cli.cli import main
        from oncotracer_cli.runtime import runtime_root
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["provenance", "--runtime-identity"]), 0)
        self.assertEqual(json.loads(output.getvalue()), current_runtime_identity(runtime_root()))


if __name__ == "__main__":
    unittest.main()
