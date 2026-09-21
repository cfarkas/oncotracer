"""Export, evidence-label and transaction regressions for paper reports."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import paper_report
from oncotracer_cli.runtime import OncoTracerError

HAS_PLOTTING = all(importlib.util.find_spec(name) is not None for name in ("numpy", "matplotlib"))


@unittest.skipUnless(HAS_PLOTTING, "optional paper plotting dependencies unavailable")
class PaperReportRenderingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "report"
        self.env = patch.dict(os.environ, {"MPLCONFIGDIR": str(self.root / "mpl")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def spec(self, panels=None):
        return {"schema": paper_report.SCHEMA, "language": "en", "dpi": 72,
                "title": "Synthetic <study>", "figures": [{
                    "stem": "Figure_1", "title": "Synthetic measurements",
                    "caption": "Synthetic renderer inputs; no biological validation.",
                    "evidence_status": "illustrative",
                    "layout": {"rows": 1, "cols": 1, "width": 3.2, "height": 2.6},
                    "panels": panels or [{"type": "bar", "letter": "A", "records": [
                        {"category": "Illumina", "value": 2}, {"category": "ONT", "value": 1}]}]}]}

    def render(self, spec):
        self.manifest.write_text(json.dumps(spec))
        return paper_report.generate_paper_report(self.manifest, self.output)

    def snapshot(self):
        return {str(p.relative_to(self.output)): p.read_bytes()
                for p in self.output.rglob("*") if p.is_file()}

    def test_real_exports_all_plot_types_and_verified_provenance(self):
        panels = [
            {"type": "bar", "letter": "A", "orientation": "horizontal", "stacked": True,
             "group": "platform", "records": [
                 {"category": "Cases", "platform": "Illumina", "value": 2},
                 {"category": "Cases", "platform": "ONT", "value": 1}]},
            {"type": "distribution", "letter": "B", "records": [
                {"category": "Measured", "value": 2}, {"category": "Measured", "value": 4},
                {"category": "Measured", "value": ""}]},
            {"type": "scatter", "letter": "C", "group": "group", "records": [
                {"group": "Pairs", "x": 2, "y": 3}, {"group": "Pairs", "x": 4, "y": 5}]},
            {"type": "line", "letter": "D", "lower": "low", "upper": "high", "records": [
                {"x": 2, "y": 4, "low": 3, "high": 5}, {"x": 1, "y": 2, "low": 1, "high": 3}]},
            {"type": "matrix", "letter": "E", "records": [
                {"row": "A", "column": "A", "value": 3},
                {"row": "B", "column": "A", "value": ""},
                {"row": "B", "column": "B", "value": 4}]},
        ]
        spec = self.spec(panels)
        spec["figures"][0]["layout"].update(rows=3, cols=2, width=6.4, height=7.5, top=0.875, bottom=0.10)
        panels[0].update(note="Synthetic data", note_y=-0.36)
        index = self.render(spec)
        self.assertEqual(index, self.output / "index.html")
        self.assertIn("Synthetic &lt;study&gt;", index.read_text())
        self.assertIn("illustrative", index.read_text())
        for prefix in ["Figure_1"] + [f"panels/Figure_1_{letter}" for letter in "ABCDE"]:
            self.assertTrue((self.output / (prefix + ".pdf")).read_bytes().startswith(b"%PDF-"))
            self.assertTrue((self.output / (prefix + ".png")).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn("<svg", (self.output / (prefix + ".svg")).read_text())
        report = json.loads((self.output / "provenance/render_manifest.json").read_text())
        self.assertEqual(len(report["inputs"]), 5)
        self.assertEqual(report["manifest_sha256"], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        for rel, digest in report["outputs"].items():
            self.assertEqual(digest, hashlib.sha256((self.output / rel).read_bytes()).hexdigest())
        with (self.output / "source_data/Figure_1_B.tsv").open() as f:
            self.assertEqual(len(list(csv.DictReader(f, delimiter="\t"))), 3)

    def test_inline_and_file_filters_export_only_selected_records(self):
        records = [{"category": "Keep", "value": 2}, {"category": "Exclude", "value": 9}]
        (self.root / "input.csv").write_text("category,value\nKeep,2\nExclude,9\n")
        panels = [{"type": "bar", "letter": "A", "records": records, "filter": {"category": ["Keep"]}},
                  {"type": "bar", "letter": "B", "data": "input.csv", "filter": {"category": "Keep"}}]
        spec = self.spec(panels)
        spec["figures"][0]["layout"].update(cols=2, width=6)
        self.render(spec)
        for letter in "AB":
            with (self.output / f"source_data/Figure_1_{letter}.tsv").open() as f:
                selected = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(selected, [{"category": "Keep", "value": "2"}])

    def test_independent_validation_requires_truth_and_preserves_it(self):
        for truth in (None, "", "  ", False, [], {}):
            with self.subTest(truth=truth):
                spec = self.spec()
                spec["figures"][0].update(evidence_status="independent_validation", truth_source=truth)
                with self.assertRaises(OncoTracerError):
                    self.render(spec)
                self.assertFalse(self.output.exists())
        spec = self.spec()
        spec["figures"][0].update(evidence_status="independent_validation", truth_source="  Synthetic truth <v1>  ")
        index = self.render(spec)
        report = json.loads((self.output / "provenance/render_manifest.json").read_text())
        self.assertEqual(report["figures"][0]["truth_source"], "Synthetic truth <v1>")
        self.assertIn("Synthetic truth &lt;v1&gt;", index.read_text())
        self.assertIn("Truth source: Synthetic truth <v1>", (self.output / "legends/Figure_1.md").read_text())

    def test_concordance_is_not_promoted_to_independent_validation(self):
        spec = self.spec()
        spec["figures"][0]["evidence_status"] = "concordance"
        index = self.render(spec)
        report = json.loads((self.output / "provenance/render_manifest.json").read_text())
        self.assertEqual(report["figures"][0]["evidence_status"], "concordance")
        self.assertEqual(report["figures"][0]["truth_source"], "")
        self.assertNotIn("independent validation", index.read_text())

    def test_malformed_manifest_and_layout_fail_without_publication(self):
        specs = [None, [], "not an object"]
        for key, val in [("figures", [None]), ("figures", [])]:
            spec = self.spec(); spec[key] = val; specs.append(spec)
        for field, value in [("layout", None), ("layout", {"cols": 0}), ("layout", {"rows": 0}),
                             ("layout", {"cols": "invalid"}), ("panels", [None]), ("panels", {}),
                             ("stem", "../escape"), ("evidence_status", "accuracy")]:
            spec = self.spec(); spec["figures"][0][field] = value; specs.append(spec)
        spec = self.spec(); spec["figures"][0]["panels"] *= 2; specs.append(spec)
        spec = self.spec(); spec["figures"][0]["panels"].append({"type": "bar", "letter": "B", "records": [{"category": "X", "value": 1}]}); specs.append(spec)
        for spec in specs:
            with self.subTest(spec=spec):
                with self.assertRaises(OncoTracerError):
                    self.render(spec)
                self.assertFalse(self.output.exists())

    def test_malformed_data_and_plot_domain_errors_are_rejected(self):
        invalid = [
            {"type": "distribution", "records": [{"category": "A", "value": ""}]},
            {"type": "scatter", "records": [{"x": 1, "y": ""}]},
            {"type": "matrix", "records": [{"row": "A", "column": "A", "value": ""}]},
            {"type": "bar", "records": [{"category": "A", "value": -1}]},
            {"type": "bar", "records": [{"category": "A", "value": "NaN"}]},
            {"type": "bar", "records": [{"category": "A", "value": 1}] * 2},
            {"type": "bar", "records": [{"value": 1}]},
            {"type": "bar", "records": [{"category": "A", "value": 1}], "filter": {"typo": ""}},
            {"type": "distribution", "yscale": "log", "records": [{"category": "A", "value": 0}]},
            {"type": "scatter", "xscale": "log", "records": [{"x": -1, "y": 2}]},
            {"type": "line", "yscale": "log", "records": [{"x": 1, "y": 0}]},
            {"type": "line", "lower": "lo", "upper": "hi", "records": [{"x": 1, "y": 2, "lo": 3, "hi": 4}]},
            {"type": "line", "yscale": "log", "lower": "lo", "upper": "hi", "records": [{"x": 1, "y": 2, "lo": 0, "hi": 4}]},
            {"type": "matrix", "records": [{"row": "A", "column": "A", "value": 1}] * 2},
        ]
        for panel in invalid:
            with self.subTest(panel=panel):
                with self.assertRaises(OncoTracerError):
                    self.render(self.spec([panel]))
                self.assertFalse(self.output.exists())
        for text in ("category,value,value\nA,1,2\n", "category,value\nA,1,2\n", "category,value\nA\n"):
            (self.root / "broken.csv").write_text(text)
            with self.assertRaises(OncoTracerError):
                self.render(self.spec([{"type": "bar", "data": "broken.csv"}]))
            self.assertFalse(self.output.exists())

    def test_owned_rerender_replaces_old_artifacts_preserving_unrelated_files(self):
        self.render(self.spec())
        (self.output / "notes.txt").write_text("Keep my manuscript notes")
        changed = self.spec()
        changed["figures"][0]["stem"] = "Figure_2"
        self.render(changed)
        self.assertFalse((self.output / "Figure_1.pdf").exists())
        self.assertTrue((self.output / "Figure_2.pdf").exists())
        self.assertEqual((self.output / "notes.txt").read_text(), "Keep my manuscript notes")

    def test_edited_owned_output_and_unowned_collision_are_preserved(self):
        self.output.mkdir()
        (self.output / "index.html").write_text("User-owned index")
        before = self.snapshot()
        with self.assertRaisesRegex(OncoTracerError, "unowned"):
            self.render(self.spec())
        self.assertEqual(self.snapshot(), before)
        (self.output / "index.html").unlink()
        self.render(self.spec())
        (self.output / "Figure_1.svg").write_text("Manually edited artwork")
        before = self.snapshot()
        with self.assertRaisesRegex(OncoTracerError, "edited"):
            self.render(self.spec())
        self.assertEqual(self.snapshot(), before)

    def test_rendering_failure_keeps_previous_report_and_recovery_succeeds(self):
        self.render(self.spec())
        before = self.snapshot()
        with patch("matplotlib.figure.Figure.savefig", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OncoTracerError, "disk failure"):
                self.render(self.spec())
        self.assertEqual(self.snapshot(), before)
        import matplotlib.pyplot as plt
        self.assertEqual(plt.get_fignums(), [])
        self.render(self.spec())
        self.assertTrue((self.output / "index.html").is_file())

    def test_mid_publication_failure_restores_previous_report(self):
        self.render(self.spec())
        before = self.snapshot()
        real_replace = os.replace
        failed = []
        def fail_once(source, target):
            if not failed and Path(source).name == "Figure_1.png" and "new" in Path(source).parts:
                failed.append(True)
                raise OSError("injected publication failure")
            return real_replace(source, target)
        with patch.object(paper_report.os, "replace", side_effect=fail_once):
            with self.assertRaisesRegex(OncoTracerError, "publication failure"):
                self.render(self.spec())
        self.assertTrue(failed)
        self.assertEqual(self.snapshot(), before)

    def test_symlinks_and_forged_provenance_paths_are_rejected(self):
        foreign = self.root / "foreign"; foreign.mkdir()
        (foreign / "keep.txt").write_text("Keep")
        self.output.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, "symlink"):
            self.render(self.spec())
        self.assertEqual((foreign / "keep.txt").read_text(), "Keep")
        self.output.unlink()
        self.render(self.spec())
        target = self.output / "provenance/render_manifest.json"
        data = json.loads(target.read_text())
        data["outputs"]["../foreign/keep.txt"] = hashlib.sha256(b"Keep").hexdigest()
        target.write_text(json.dumps(data))
        before = self.snapshot()
        with self.assertRaisesRegex(OncoTracerError, "artifact path"):
            self.render(self.spec())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((foreign / "keep.txt").read_text(), "Keep")

    def test_malformed_prior_ownership_record_is_not_overwritten(self):
        for previous in ({}, [], {"schema": "unrelated"}, {"schema": paper_report.SCHEMA, "outputs": []}):
            marker = self.output / "provenance/render_manifest.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(previous))
            before = self.snapshot()
            with self.assertRaises(OncoTracerError):
                self.render(self.spec())
            self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
