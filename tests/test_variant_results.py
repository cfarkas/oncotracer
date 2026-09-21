"""Saved variant statuses and artifacts remain distinct in the results browser."""
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from oncotracer_cli.results import write_results_index


class VariantResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oncotracer-variant-browser-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "results"
        self.put("06_workflow_summary/workflow_summary.json", json.dumps({
            "workflow_status": "partial_failure", "cna_status": "not_requested", "analysis": "variants",
            "variant_status": "partial_failure", "variant_completed_samples": ["ZERO"],
            "variant_failed_samples": ["FAIL"]}))

    def put(self, relative, text="fixture\n"):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def catalog(self):
        data = json.loads((self.root / "06_workflow_summary/results_catalog.json").read_text())
        return next(stage for stage in data["stages"] if stage["stage"] == "08_variants")

    def test_completed_zero_records_and_failed_annotation_have_separate_status(self):
        self.put("08_variants/variant_status.json", json.dumps({
            "overall_status": "partial_failure", "annotation": "available",
            "samples": [
                {"sample": "ZERO", "status": "complete", "callers": [
                    {"caller": "bcftools", "status": "complete", "variant_records": 0,
                     "annotation": {"status": "skipped", "reason": "No matching database"}}]},
                {"sample": "FAIL", "status": "partial_failure", "callers": [
                    {"caller": "mutect2", "status": "partial_failure", "variant_records": 2,
                     "annotation": {"status": "failed", "reason": "<unsafe> & unavailable"}},
                    {"caller": "freebayes", "status": "failed"}]}]}))
        write_results_index(self.root)
        page = (self.root / "08_variants/index.html").read_text()
        self.assertIn('<td>ZERO</td><td>bcftools</td><td>complete</td><td>0</td>', page)
        self.assertIn('<td>freebayes</td><td>failed</td><td>not reported</td>', page)
        self.assertIn('failed: &lt;unsafe&gt; &amp; unavailable', page)
        self.assertNotIn('<unsafe>', page)
        self.assertIn('Database availability does not mean annotation completed', page)
        self.assertEqual(self.catalog()["status"], "partial_failure")
        dashboard = (self.root / "index.html").read_text()
        self.assertIn('Small-variant status: Partial failure', dashboard)
        self.assertIn('CNA: not requested', dashboard)
        self.assertNotIn("Add them from the completed CNA outputs", dashboard)
        self.assertIn("Open small-variant results", dashboard)

    def test_partial_assessment_is_presented_separately_from_failed_samples(self):
        status = self.put("08_variants/variant_status.json", json.dumps({
            "overall_status": "partial_failure", "samples": [
                {"sample": "FAIL", "status": "partial_failure", "callers": [
                    {"caller": "bcftools", "status": "partial_failure", "variant_records": 84,
                     "annotation": {"status": "complete"}, "assessments": {
                         "ffperase": {"status": "not_assessed", "reason": "Insufficient depth"}}}]}]}))
        before = status.read_bytes()
        write_results_index(self.root)
        page = (self.root / "08_variants/index.html").read_text()
        dashboard = (self.root / "index.html").read_text()
        self.assertIn('class="status partial">Status: Partial failure', page)
        self.assertIn('Available variant results are preserved', page)
        self.assertIn('not_assessed', page)
        self.assertIn('Insufficient depth', page)
        self.assertIn('Partial samples: 1 · Failed samples: 0', dashboard)
        self.assertEqual(status.read_bytes(), before)
        self.assertEqual(self.catalog()["status"], "partial_failure")

    def test_artifacts_are_categorized_links_resolve_and_inputs_are_unchanged(self):
        names = ["variant_provenance.json", "owner.json",
                 "samples/TEST/bcftools/TEST.bcftools.vcf.gz",
                 "samples/TEST/bcftools/TEST.bcftools.vcf.gz.tbi",
                 "samples/TEST/bcftools/evidence.tsv",
                 "samples/TEST/bcftools/annovar.hg38_multianno.txt",
                 "samples/TEST/bcftools/annovar.hg38_multianno.vcf",
                 "samples/TEST/bcftools/caller_raw.vcf.gz",
                 "samples/TEST/bcftools/complete.json",
                 "samples/TEST/bcftools/call.stderr.log",
                 "failed_logs/OTHER/bcftools/call.stderr.log"]
        for name in names:
            self.put("08_variants/" + name)
        self.put("08_variants/variant_status.json", '{"overall_status":"complete","samples":[]}')
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        write_results_index(self.root)
        write_results_index(self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        stage = self.catalog()
        self.assertIn("08_variants/samples/TEST/bcftools/evidence.tsv", stage["primary_files"])
        self.assertIn("08_variants/samples/TEST/bcftools/annovar.hg38_multianno.vcf", stage["primary_files"])
        self.assertIn("08_variants/variant_status.json", stage["quality_control_files"])
        self.assertIn("08_variants/variant_provenance.json", stage["supporting_files"])
        self.assertNotIn("08_variants/samples/TEST/bcftools/caller_raw.vcf.gz", stage["primary_files"])
        self.assertEqual(stage["diagnostic_file_count"], 5)
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.links.extend(value for key, value in attrs if key == 'href')
        for path in self.root.rglob("*.html"):
            parser = Links()
            parser.feed(path.read_text())
            for href in parser.links:
                url = urlsplit(href)
                if not url.scheme and url.path:
                    self.assertTrue((path.parent / unquote(url.path)).is_file(), (path, href))

    def test_absent_requested_stage_is_explicit_without_broken_index_link(self):
        write_results_index(self.root)
        stage = self.catalog()
        self.assertIsNone(stage["index"])
        page = (self.root / "index.html").read_text()
        self.assertIn("Variant output files are absent", page)
        self.assertNotIn('href="08_variants/index.html"', page)

    def test_current_failure_pointer_hides_stale_or_unowned_stage_outputs(self):
        summary_path = self.root / "06_workflow_summary/workflow_summary.json"
        summary = json.loads(summary_path.read_text())
        summary["variant_status_file"] = ".oncotracer-native/variant_failure.json"
        summary_path.write_text(json.dumps(summary))
        self.put(".oncotracer-native/variant_failure.json", '{"overall_status":"failed","error":"ownership conflict"}')
        self.put("08_variants/variant_status.json", '{"overall_status":"complete"}')
        old_page = self.put("08_variants/index.html", "unowned prior report")
        old_vcf = self.put("08_variants/samples/OLD/old.vcf.gz", "old callset")
        before = {p: p.read_bytes() for p in (old_page, old_vcf)}
        write_results_index(self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        stage = self.catalog()
        self.assertIsNone(stage["index"])
        self.assertEqual(stage["status"], "failed")
        self.assertFalse(stage["primary_files"])
        self.assertEqual(stage["quality_control_files"], [".oncotracer-native/variant_failure.json"])
        page = (self.root / "index.html").read_text()
        self.assertIn("Current attempt status: failed", page)
        self.assertNotIn("old.vcf.gz", page)
        self.assertNotIn('href="08_variants/index.html"', page)

    def test_external_current_status_pointer_is_never_read_or_linked(self):
        secret = Path(self.temp.name) / "external-status.json"
        secret.write_text('{"overall_status":"SECRET_EXTERNAL"}')
        summary_path = self.root / "06_workflow_summary/workflow_summary.json"
        summary = json.loads(summary_path.read_text())
        summary["variant_status_file"] = str(secret)
        summary_path.write_text(json.dumps(summary))
        self.put("08_variants/variant_status.json", '{"overall_status":"complete"}')
        write_results_index(self.root)
        self.assertEqual(self.catalog()["status"], "unavailable")
        self.assertFalse(self.catalog()["quality_control_files"])
        page = (self.root / "index.html").read_text()
        self.assertNotIn("SECRET_EXTERNAL", page)
        self.assertNotIn("external-status.json", page)

    def test_corrupt_or_external_status_is_not_mistaken_for_success(self):
        self.put("08_variants/variant_status.json", "invalid json")
        path = self.root / "08_variants/variant_status.json"
        self.put("08_variants/samples/TEST/bcftools/TEST.bcftools.vcf.gz")
        write_results_index(self.root)
        self.assertEqual(self.catalog()["status"], "unavailable")
        external = Path(self.temp.name) / "external.json"
        external.write_text('{"overall_status":"SECRET_EXTERNAL"}')
        path.unlink()
        path.symlink_to(external)
        write_results_index(self.root)
        page = (self.root / "08_variants/index.html").read_text()
        self.assertNotIn("SECRET_EXTERNAL", page)
        self.assertIn("missing or unreadable", page)


if __name__ == "__main__":
    unittest.main()
