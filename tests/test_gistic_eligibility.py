"""GISTIC eligibility is independent of whether tool failures are required."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oncotracer_cli.classifier import _run_gistic, _update_summary
from oncotracer_cli.runtime import OncoTracerError

ROOT = Path(__file__).resolve().parents[1]
HEADER = "Sample\tChromosome\tStart\tEnd\tNum_Probes\tSegment_Mean\n"


class GisticEligibilityTests(unittest.TestCase):
    def prepare(self, base, names, *, event_names=None):
        prepared = base / "prepared"
        prepared.mkdir()
        for filename, samples in (("gistic_full.seg", names),
                                  ("gistic_events.seg", names if event_names is None else event_names)):
            (prepared / filename).write_text(HEADER + "".join(f"{name}\t1\t1\t100\t2\t0\n" for name in samples))
        (prepared / "gistic_markers.tsv").write_text("Marker Name\tChromosome\tMarker Position\nm1\t1\t1\n")
        # Eligibility must come from the selected SEG, not an upstream total.
        (prepared / "prepare_metrics.json").write_text('{"samples_total": 22}')
        return prepared

    def invoke(self, base, prepared, config, assessment=None):
        runner, toolchain = Mock(), Mock()
        with patch("oncotracer_cli.classifier.shutil.which", side_effect=AssertionError("tool lookup")):
            result = _run_gistic(ROOT, config, base / "reference", prepared, base / "gistic",
                                 runner, Mock(), toolchain, force=False, assessment=assessment)
        runner.run.assert_not_called()
        toolchain.environment.assert_not_called()
        toolchain.wrap.assert_not_called()
        self.assertFalse((base / "reference/.oncotracer/classifier/gistic2_refgene").exists())
        return result

    def test_required_zero_one_and_custom_minimum_skip_without_launch(self):
        for names, minimum in (([], 2), (["S1"], 2), (["S1", "S1"], 2),
                               (["S1", "S2"], 3), (["S1"], 1)):
            with self.subTest(names=names, minimum=minimum), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                prepared = self.prepare(base, names)
                _, status, _ = self.invoke(base, prepared, {"run_gistic": True, "gistic_required": True,
                                                          "gistic_min_samples": minimum})
                self.assertIn("skipped\tnot_enough_samples", status.read_text())
                record = json.loads((base / "gistic/gistic_eligibility.json").read_text())
                self.assertEqual(record["eligible_count"], len(set(names)))
                self.assertEqual(record["minimum_samples"], max(2, minimum))
                self.assertEqual(record["eligible_samples"], sorted(set(names)))

    def test_selected_seg_not_total_assigned_samples_controls_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prepared = self.prepare(base, ["S1", "S2"], event_names=["S1"])
            self.invoke(base, prepared, {"run_gistic": True, "gistic_required": True, "gistic_seg_type": "events"})
            record = json.loads((base / "gistic/gistic_eligibility.json").read_text())
            self.assertEqual(record["eligible_samples"], ["S1"])
            self.assertEqual(record["segmentation"], "events")

    def test_qc_exclusions_and_explicit_selection_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prepared = self.prepare(base, ["S1"])
            assessment = {"samples": [
                {"sample": "S1", "assessment": "assessable"},
                {"sample": "S2", "assessment": "not_assessable", "reason": "insufficient coverage"},
                {"sample": "S3", "assessment": "assessable"}]}
            self.invoke(base, prepared, {"run_gistic": True, "gistic_required": True,
                                        "cna_classifier_samples": "S1; S2"}, assessment)
            record = json.loads((base / "gistic/gistic_eligibility.json").read_text())
            self.assertEqual(record["eligible_samples"], ["S1"])
            self.assertEqual(record["excluded_samples"], [
                {"sample": "S2", "reason": "insufficient coverage"},
                {"sample": "S3", "reason": "not_selected_for_reports"}])

    def test_stale_inputs_cannot_reintroduce_excluded_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prepared = self.prepare(base, ["S1", "S2"])
            with self.assertRaisesRegex(OncoTracerError, "excluded by CNA coverage"):
                self.invoke(base, prepared, {"run_gistic": True, "gistic_required": True},
                            {"samples": [{"sample": "S1", "assessment": "assessable"},
                                         {"sample": "S2", "assessment": "not_assessable"}]})

    def test_skip_resume_is_stable_and_archives_previous_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prepared = self.prepare(base, ["S1"])
            old = base / "gistic/gistic2_out"
            old.mkdir(parents=True)
            (old / ".oncotracer-complete").write_text("old completion")
            (old / "GISTIC_FAILED.txt").write_text("old failure")
            config = {"run_gistic": True, "gistic_required": True}
            self.invoke(base, prepared, config)
            paths = [base / "gistic/gistic_eligibility.json", base / "gistic/gistic2_status.tsv",
                     old / "GISTIC_NOT_RUN.txt"]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
            self.invoke(base, prepared, config)
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})
            self.assertFalse((old / ".oncotracer-complete").exists())
            self.assertFalse((old / "GISTIC_FAILED.txt").exists())
            self.assertEqual((old / "GISTIC_PREVIOUS_FAILURE.txt").read_text(), "old failure")

    def test_eligible_cohort_still_requires_the_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prepared = self.prepare(base, ["S1", "S2"])
            tools = Mock()
            tools.gistic_prefix = base / "missing-gistic-runtime"
            with self.assertRaisesRegex(OncoTracerError, "executable not found"):
                _run_gistic(ROOT, {"run_gistic": True, "gistic_required": True}, base / "reference",
                            prepared, base / "gistic", Mock(), Mock(), tools, force=False)
            record = json.loads((base / "gistic/gistic_eligibility.json").read_text())
            self.assertEqual(record["status"], "eligible")
            self.assertEqual(record["eligible_count"], 2)

    def test_successful_report_resume_clears_only_resolved_classifier_failure(self):
        for cna_status in ("complete", "partial_failure"):
            with self.subTest(cna_status=cna_status), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                summary = base / "06_workflow_summary"
                summary.mkdir()
                path = summary / "workflow_summary.json"
                path.write_text(json.dumps({"workflow_status": "partial_failure", "cna_status": cna_status,
                                            "cna_classifier_error": "previous undersized GISTIC failure"}))
                gistic = base / "05_cna_classifier/diagnostics/gistic"
                gistic.mkdir(parents=True)
                (gistic / "gistic2_status.tsv").write_text("status\treason\nskipped\tnot_enough_samples\n")
                _update_summary(base, base / "05_cna_classifier", gistic_requested=True)
                value = json.loads(path.read_text())
                self.assertNotIn("cna_classifier_error", value)
                self.assertEqual(value["workflow_status"], cna_status)
                self.assertEqual(value["gistic_status"], "skipped")
                self.assertTrue(value["cna_reports_completed"])


if __name__ == "__main__":
    unittest.main()
