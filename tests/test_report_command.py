"""Reports-only invocation must preserve scientific results and fail closed."""
from __future__ import annotations

import contextlib
import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import report_command as reports
from oncotracer_cli.cli import build_parser, main
from oncotracer_cli.engine import write_run_manifest
from oncotracer_cli.output_safety import OUTPUT_OWNER_RELATIVE, claim_output_run
from oncotracer_cli.runtime import OncoTracerError, atomic_write_json

IDENTITY = {"oncotracer_version": "2.0.0", "source_commit": "a" * 40,
            "source_sha256": "b" * 64, "source_tree_dirty": False,
            "binary_sha256": "c" * 64, "runtime_payload_sha256": "c" * 64}
ROOT = Path(__file__).resolve().parents[1]


class ReportCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.output = self.base / "results"
        self.config = self.base / "run.yml"
        self.config.write_text(f"mode: illumina\noutdir: {self.output}\nrun_cna_classifier: false\n")
        with claim_output_run(self.output, config_path=self.config, identity=IDENTITY):
            pass
        for relative in reports.CNA_INPUTS:
            path = self.output / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text("sample\tchrom\nS1\t1\n")
        summary = self.output / "06_workflow_summary/workflow_summary.json"
        atomic_write_json(summary, {"workflow_status": "complete", "cna_status": "complete",
                                    "completed_samples": ["S1"], "failed_samples": []})
        self.native = self.output / ".oncotracer-native"
        (self.native / "trace.tsv").write_text("original trace\n")
        (self.native / "state.json").write_text('{"original":true}\n')
        write_run_manifest(self.output, self.config, self.native / "trace.tsv")
        self.args = build_parser().parse_args(["reports", "--config", str(self.config),
                                               "--backend", "conda", "--literature",
                                               "--model", "cached/test-model"])
        prefixes = {name: self.base / name for name in ("classifier", "gistic")}
        for target, value in (("oncotracer_cli.cli._load_install_config", {}),
                              ("oncotracer_cli.cli._managed_conda_base", self.base / "envs"),
                              ("oncotracer_cli.report_command.managed_conda_runtime_lock", contextlib.nullcontext(prefixes)),
                              ("oncotracer_cli.report_command.runtime_root", ROOT),
                              ("oncotracer_cli.report_command.current_runtime_identity", dict(IDENTITY, source_commit="d" * 40))):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(reports, "run_native_classifier", side_effect=self.fake_classifier)
        self.classifier = patcher.start()
        self.addCleanup(patcher.stop)

    def fake_classifier(self, root, config, outdir, lpwgs_root, runner, ledger, toolchain, *, force):
        report = outdir / "05_cna_classifier/03_report/llm_reports/index.html"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("generated report")
        summary_path = outdir / "06_workflow_summary/workflow_summary.json"
        summary = json.loads(summary_path.read_text())
        summary.update(cna_classifier_status="complete", cna_classifier_completed=True)
        atomic_write_json(summary_path, summary)
        return report.parent

    def invoke(self):
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            return reports.command_reports(self.args)

    def test_reports_from_older_runtime_preserve_inputs_config_and_scientific_ledger(self):
        paths = [self.config, self.output / reports.MANIFEST, self.output / OUTPUT_OWNER_RELATIVE,
                 self.native / "state.json", self.native / "trace.tsv",
                 *(self.output / relative for relative in reports.CNA_INPUTS)]
        original = {path: path.read_bytes() for path in paths}
        self.assertEqual(self.invoke(), 0)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)
        config = self.classifier.call_args.args[1]
        self.assertTrue(config["knowledge_literature_llm"])
        self.assertTrue(config["knowledge_web"])
        self.assertTrue(config["knowledge_literature_llm_local_files_only"])
        self.assertFalse(config["knowledge_deep_enable_llm_ranker"])
        self.assertFalse(config["knowledge_literature_reference_llm_selection"])
        self.assertFalse(config["pathology_use_biomed_models"])
        self.assertFalse(config["run_gistic"])
        self.assertEqual(config["knowledge_llm_threads"], 4)
        self.assertEqual(config["knowledge_literature_llm_max_features"], 8)
        self.assertEqual(config["knowledge_max_papers"], 8)
        self.assertEqual(config["knowledge_literature_llm_max_new_tokens"], 192)
        self.assertFalse(config["knowledge_deep_literature"])
        self.assertEqual(config["knowledge_literature_llm_models"], "cached/test-model")
        self.assertTrue((self.output / "index.html").is_file())
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["status"], "complete")
        self.assertTrue(marker["files"])
        generation = self.output / marker["generation"]
        self.assertTrue((generation / "effective_config.yml").is_file())
        self.assertTrue((generation / "trace.tsv").is_file())
        self.assertEqual((generation.parent / "native_run_manifest.original.json").read_bytes(),
                         original[self.output / reports.MANIFEST])

    def test_public_entry_point_dispatches_reports(self):
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            self.assertEqual(main(["reports", "--config", str(self.config), "--literature"]), 0)
        self.classifier.assert_called_once()

    def test_reports_without_accepted_llm_drafts_record_distinct_status(self):
        def no_drafts(*args, **kwargs):
            result = self.fake_classifier(*args, **kwargs)
            atomic_write_json(self.output / "05_cna_classifier/06_knowledge/knowledge_metrics.json",
                              {"literature_llm_completed_features": 0, "literature_llm_attempted_features": 8})
            return result
        self.classifier.side_effect = no_drafts
        self.invoke()
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["status"], "complete")
        self.assertEqual(marker["literature_llm"]["status"], "no_accepted_drafts")
        self.assertEqual(marker["literature_llm"]["accepted_drafts"], 0)

    def test_explicit_report_limits_override_bounded_defaults(self):
        self.args.max_features = 3
        self.args.max_papers = 4
        self.args.max_new_tokens = 256
        self.args.deep_literature = True
        self.invoke()
        config = self.classifier.call_args.args[1]
        self.assertEqual(config["knowledge_literature_llm_max_features"], 3)
        self.assertEqual(config["knowledge_max_papers"], 4)
        self.assertEqual(config["knowledge_literature_llm_max_new_tokens"], 256)
        self.assertTrue(config["knowledge_deep_literature"])

    def test_organize_only_preserves_science_and_skips_runtime_and_models(self):
        self.invoke()
        self.classifier.reset_mock()
        original = {p: p.read_bytes() for p in (self.config, self.output / reports.MANIFEST,
                    self.output / OUTPUT_OWNER_RELATIVE, self.native / "state.json", self.native / "trace.tsv")}
        self.args.organize_only = True
        self.args.literature = False
        self.args.model = None
        def organize(stage):
            source = stage / "03_report/llm_reports/index.html"
            source.replace(stage / "final_report.html")
            atomic_write_json(stage / "layout_manifest.json", {"schema": "oncotracer-classifier-layout-v1"})
            return {}
        with patch("oncotracer_cli.classifier_layout.organize_classifier", side_effect=organize) as organizer, \
             patch("oncotracer_cli.cli._load_install_config") as install, \
             patch.object(reports, "managed_conda_runtime_lock") as runtime:
            self.assertEqual(self.invoke(), 0)
        organizer.assert_called_once()
        install.assert_not_called()
        runtime.assert_not_called()
        self.classifier.assert_not_called()
        for path, contents in original.items():
            self.assertEqual(path.read_bytes(), contents)
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["operation"], "organize_only")
        self.assertEqual(marker["status"], "complete")
        self.assertIn("layout_manifest_sha256", marker)
        summary = reports._json(self.output / "06_workflow_summary/workflow_summary.json")
        self.assertEqual(summary["cna_knowledge_report_index"], str(self.output / "05_cna_classifier/final_report.html"))

    def test_organize_only_rejects_generation_flags_and_missing_reports(self):
        self.args.organize_only = True
        with self.assertRaisesRegex(OncoTracerError, "cannot be combined"):
            self.invoke()
        self.args.literature = False
        self.args.model = None
        with self.assertRaisesRegex(OncoTracerError, "No existing classifier"):
            self.invoke()
        self.classifier.assert_not_called()
        self.assertFalse((self.output / "05_cna_classifier").exists())

    def test_canonical_evidence_metrics_are_used(self):
        def canonical(*args, **kwargs):
            result = self.fake_classifier(*args, **kwargs)
            atomic_write_json(self.output / "05_cna_classifier/evidence/knowledge_metrics.json",
                              {"literature_llm_completed_features": 2, "literature_llm_attempted_features": 3})
            return result
        self.classifier.side_effect = canonical
        self.invoke()
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["literature_llm"]["accepted_drafts"], 2)

    def test_existing_report_generation_is_allowed(self):
        self.invoke()
        first = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.invoke()
        second = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertNotEqual(first["generation"], second["generation"])
        self.assertTrue((self.output / first["generation"] / "provenance.json").exists())

    def test_manifest_owned_original_classifier_is_allowed(self):
        path = self.output / "05_cna_classifier/native_classifier_summary.json"
        atomic_write_json(path, {"schema": "oncotracer-native-classifier-v1",
                                 "classifier_outdir": str(path.parent)})
        write_run_manifest(self.output, self.config, self.native / "trace.tsv")
        self.assertEqual(self.invoke(), 0)

    def test_failed_report_can_retry_without_stale_failure_status(self):
        self.classifier.side_effect = OncoTracerError("failed once")
        with self.assertRaises(OncoTracerError):
            self.invoke()
        self.classifier.side_effect = self.fake_classifier
        self.invoke()
        summary = reports._json(self.output / "06_workflow_summary/workflow_summary.json")
        self.assertEqual(summary["reports_status"], "complete")
        self.assertEqual(summary["workflow_status"], "complete")
        self.assertNotIn("report_error", summary)

    def test_catalog_default_does_not_enable_network_or_llms(self):
        self.args.literature = False
        self.invoke()
        config = self.classifier.call_args.args[1]
        self.assertFalse(config["knowledge_web"])
        self.assertFalse(config["knowledge_literature_llm"])
        self.assertFalse(config["knowledge_catalog_llm"])

    def test_active_run_lock_refused(self):
        with (self.native / "run.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(OncoTracerError, "already running"):
                self.invoke()
        self.classifier.assert_not_called()
        self.assertFalse((self.output / "05_cna_classifier").exists())

    def test_changed_input_refused(self):
        (self.output / reports.CNA_INPUTS[0]).write_text("changed")
        with self.assertRaisesRegex(OncoTracerError, "checksum changed"):
            self.invoke()
        self.classifier.assert_not_called()

    def test_changed_config_refused(self):
        with self.config.open("a") as handle:
            handle.write("knowledge_web: true\n")
        with self.assertRaisesRegex(OncoTracerError, "config changed"):
            self.invoke()
        self.classifier.assert_not_called()

    def test_foreign_report_directory_refused_without_changes(self):
        foreign = self.output / "05_cna_classifier"
        foreign.mkdir()
        sentinel = foreign / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(OncoTracerError, "foreign 05"):
            self.invoke()
        self.assertEqual(list(foreign.iterdir()), [sentinel])
        self.classifier.assert_not_called()

    def test_symlink_in_reserved_report_directory_refused(self):
        target = self.base / "unrelated"
        target.mkdir()
        (self.output / "05_cna_classifier").symlink_to(target)
        with self.assertRaisesRegex(OncoTracerError, "symlink"):
            self.invoke()
        self.assertEqual(list(target.iterdir()), [])
        self.classifier.assert_not_called()

    def test_mismatched_canonical_owner_refused(self):
        owner_path = self.output / OUTPUT_OWNER_RELATIVE
        value = reports._json(owner_path)
        value["canonical_path_sha256"] = "e" * 64
        atomic_write_json(owner_path, value)
        with self.assertRaisesRegex(OncoTracerError, "owner path mismatch"):
            self.invoke()
        self.classifier.assert_not_called()

    def test_runtime_lock_exit_failure_marks_reports_failed(self):
        @contextlib.contextmanager
        def invalidated_runtime(*args, **kwargs):
            yield {name: self.base / name for name in ("classifier", "gistic")}
            raise OncoTracerError("runtime changed during use")
        with patch.object(reports, "managed_conda_runtime_lock", side_effect=invalidated_runtime):
            with self.assertRaisesRegex(OncoTracerError, "runtime changed"):
                self.invoke()
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["status"], "failed")
        summary = reports._json(self.output / "06_workflow_summary/workflow_summary.json")
        self.assertEqual(summary["reports_status"], "failed")

    def test_failure_records_partial_status_and_preserves_manifest(self):
        manifest = (self.output / reports.MANIFEST).read_bytes()
        self.classifier.side_effect = OncoTracerError("report failed")
        with self.assertRaisesRegex(OncoTracerError, "report failed"):
            self.invoke()
        marker = reports._json(self.output / "05_cna_classifier/report_provenance.json")
        self.assertEqual(marker["status"], "failed")
        summary = reports._json(self.output / "06_workflow_summary/workflow_summary.json")
        self.assertEqual(summary["reports_status"], "failed")
        self.assertEqual(summary["workflow_status"], "partial_failure")
        self.assertEqual((self.output / reports.MANIFEST).read_bytes(), manifest)


if __name__ == "__main__":
    unittest.main()
