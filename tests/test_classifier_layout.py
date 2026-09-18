"""Physical report publication preserves artifacts and refuses unsafe migrations."""
from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.classifier_layout import SCHEMA, classifier_path, organize_classifier


class ClassifierLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / "05_cna_classifier"
        self.stage.mkdir()

    def put(self, name, data):
        path = self.stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def snapshot(self):
        return {p.relative_to(self.stage).as_posix(): p.read_bytes() for p in self.stage.rglob("*") if p.is_file() and not p.is_symlink()}

    def reports(self, samples=("sample1",), clinician=False):
        folder = "clinician_reports" if clinician else "llm_reports"
        suffix = "_clinical_driver_summary" if clinician else "_CNA_knowledge_report"
        prefix = "03_report/" + folder
        rows = "sample\thtml\tpdf\n"
        for sample in samples:
            self.put(f"{prefix}/{sample}{suffix}.html", f'<h1>{sample}</h1><a href="../cna_classifier_report.html">Cohort</a>')
            self.put(f"{prefix}/{sample}{suffix}.pdf", ("%PDF " + sample).encode())
            rows += f"{sample}\t{sample}{suffix}.html\t{sample}{suffix}.pdf\n"
        index = "clinician_report_index.tsv" if clinician else "pdf_html_report_index.tsv"
        self.put(f"{prefix}/{index}", rows)
        if not clinician:
            self.put(f"{prefix}/pdf_report_index.tsv", rows)
        combined = "all_sample_clinician_driver_summaries.pdf" if clinician else "all_sample_CNA_knowledge_reports.pdf"
        self.put(f"{prefix}/{combined}", b"%PDF combined")
        self.put(f"{prefix}/index.html", "".join(f'<a href="{sample}{suffix}.html">{sample}</a>' for sample in samples))

    def test_legacy_single_sample_moves_evidence_and_removes_verified_duplicates(self):
        self.put("01_prepared/clean_events.tsv", "sample\tgene\nsample1\tTP53\n")
        self.put("02_classification/results.tsv", "classification\n")
        self.put("04_gistic2/status.txt", "disabled\n")
        self.put("05_gistic2_parsed/gistic.tsv", "status\n")
        self.put("06_knowledge/knowledge_metrics.json", "{}")
        self.put("06_knowledge/knowledge_http_cache/response.json", "{}")
        self.put("06_knowledge/knowledge_cache.json", "{}")
        self.put("07_pathology/results.tsv", "pathology\n")
        self.put("03_report/report_tables/clean_events.tsv", "sample\tgene\nsample1\tTP53\n")
        self.put("03_report/report_tables/user_snapshot.tsv", "different\n")
        self.put("03_report/figures/cna_event_burden.pdf", b"figure")
        self.put("03_report/figures/driver_region_oncoprint.png", b"driver")
        self.put("03_report/figures/cna_feature_heatmap.png", b"cohort")
        self.put("03_report/cna_classifier_report.html", '<a href="report_tables/clean_events.tsv">Data</a><img src="figures/cna_event_burden.pdf"><a href="llm_reports/index.html">Full</a>')
        self.put("03_report/sample_reports/sample1_CNA_report.html", "basic report")
        self.put("03_report/sample_reports/index.html", "basic index")
        provenance = self.put(".reports/generation/provenance.json", '{"historical":"untouched"}')
        self.reports()
        self.reports(clinician=True)
        manifest = organize_classifier(self.stage)
        self.assertEqual(manifest["schema"], SCHEMA)
        self.assertEqual((self.stage / "final_report.pdf").read_bytes(), b"%PDF sample1")
        self.assertEqual((self.stage / "clinician_report.pdf").read_bytes(), b"%PDF sample1")
        self.assertFalse((self.stage / "03_report").exists())
        self.assertFalse((self.stage / "01_prepared").exists())
        self.assertFalse((self.stage / "06_knowledge").exists())
        self.assertTrue((self.stage / "diagnostics/cache/http/response.json").is_file())
        self.assertTrue((self.stage / "evidence/knowledge_metrics.json").is_file())
        self.assertTrue((self.stage / "tables/report_copies/user_snapshot.tsv").is_file())
        self.assertTrue((self.stage / "figures/drivers/driver_region_oncoprint.png").is_file())
        self.assertTrue((self.stage / "figures/cohort/cna_feature_heatmap.png").is_file())
        cohort = (self.stage / "cohort_report.html").read_text()
        self.assertIn('href="diagnostics/prepared/clean_events.tsv"', cohort)
        self.assertIn('href="final_report.html"', cohort)
        self.assertEqual(provenance.read_text(), '{"historical":"untouched"}')
        self.assertTrue(any(r["path"].endswith("report_tables/clean_events.tsv") for r in manifest["removed"]))
        self.assertTrue(all(len(r["sha256"]) == 64 for r in manifest["removed"]))
        index = list(csv.DictReader(io.StringIO((self.stage / "tables/report_index.tsv").read_text()), delimiter="\t"))
        self.assertEqual(index[0]["html"], "../final_report.html")
        self.assertEqual(index[0]["pdf"], "../final_report.pdf")
        self.assertEqual(classifier_path(self.stage, "03_report/llm_reports/index.html"), self.stage / "final_report.html")
        before = self.snapshot()
        organize_classifier(self.stage)
        self.assertEqual(before, self.snapshot())

    def test_multisample_has_per_sample_reports_and_combined_root_reports(self):
        self.put("03_report/cna_classifier_report.html", "cohort")
        self.reports(("A", "B"))
        self.reports(("A", "B"), clinician=True)
        organize_classifier(self.stage)
        self.assertEqual((self.stage / "final_report.pdf").read_bytes(), b"%PDF combined")
        for sample in ("A", "B"):
            self.assertTrue((self.stage / f"samples/{sample}/final_report.html").is_file())
            self.assertTrue((self.stage / f"samples/{sample}/clinician_report.pdf").is_file())
        root = (self.stage / "final_report.html").read_text()
        self.assertIn('href="samples/A/final_report.html"', root)
        child = (self.stage / "samples/A/final_report.html").read_text()
        self.assertIn('href="../../cohort_report.html"', child)

    def test_partial_publication_then_full_reports_rewrites_previously_published_links(self):
        self.put("tables/classification/result.tsv", "direct canonical result")
        self.put("03_report/cna_classifier_report.html", '<a href="sample_reports/index.html">Basic</a><a href="llm_reports/index.html">Full</a>')
        self.put("03_report/sample_reports/sample1_CNA_report.html", '<a href="../cna_classifier_report.html">Cohort</a>')
        self.put("03_report/sample_reports/index.html", '<a href="sample1_CNA_report.html">Sample</a>')
        organize_classifier(self.stage)
        self.assertTrue((self.stage / "samples/sample1/cna_report.html").is_file())
        self.reports()
        organize_classifier(self.stage)
        self.assertFalse((self.stage / "samples/sample1/cna_report.html").exists())
        self.assertFalse((self.stage / "samples/index.html").exists())
        self.assertEqual((self.stage / "cohort_report.html").read_text(), '<a href="final_report.html">Basic</a><a href="final_report.html">Full</a>')
        self.put("03_report/cna_classifier_report.html", '<h1>New generation</h1><a href="llm_reports/index.html">Full</a>')
        organize_classifier(self.stage)
        self.assertIn("New generation", (self.stage / "cohort_report.html").read_text())
        self.assertIn('href="final_report.html"', (self.stage / "cohort_report.html").read_text())

    def test_symlink_refusal_makes_no_changes(self):
        self.put("01_prepared/data.tsv", "data")
        (self.stage / "03_report").symlink_to(self.stage / "01_prepared", target_is_directory=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlink"):
            organize_classifier(self.stage)
        self.assertEqual(before, self.snapshot())

    def test_unknown_collision_refuses_all_moves_before_writing(self):
        self.put("01_prepared/data.tsv", "data")
        self.put("03_report/cna_classifier_report.html", "new report")
        self.put("cohort_report.html", "user report")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "foreign|changed"):
            organize_classifier(self.stage)
        self.assertEqual(before, self.snapshot())

    def test_changed_owned_destination_refuses_overwrite(self):
        self.put("03_report/cna_classifier_report.html", "old report")
        organize_classifier(self.stage)
        self.put("cohort_report.html", "user modified report")
        self.put("03_report/cna_classifier_report.html", "new report")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "foreign|changed"):
            organize_classifier(self.stage)
        self.assertEqual(before, self.snapshot())

    def test_unknown_legacy_files_and_mutable_audit_are_preserved(self):
        self.put("03_report/my_notes.txt", "user notes")
        self.put("native_classifier_summary.json", "{}")
        self.put("report_provenance.json", "{}")
        self.put("index.html", "browser index")
        self.put("diagnostics.html", "browser diagnostics")
        self.put("evidence/index.html", "browser evidence")
        before = self.snapshot()
        manifest = organize_classifier(self.stage)
        for name, data in before.items():
            self.assertEqual((self.stage / name).read_bytes(), data)
            self.assertNotIn(name, manifest["files"])

    def test_external_reference_links_and_fragments_remain_valid(self):
        self.put("03_report/cna_classifier_report.html", '<a href="https://example.org/?a=1&amp;b=2">Reference</a><a href="#part">Part</a>')
        organize_classifier(self.stage)
        output = (self.stage / "cohort_report.html").read_text()
        self.assertIn('href="https://example.org/?a=1&amp;b=2"', output)
        self.assertIn('href="#part"', output)
        before = self.snapshot()
        organize_classifier(self.stage)
        self.assertEqual(before, self.snapshot())

    def test_single_report_missing_individual_pdf_preserves_available_combined_pdf(self):
        self.reports()
        (self.stage / "03_report/llm_reports/sample1_CNA_knowledge_report.pdf").unlink()
        organize_classifier(self.stage)
        self.assertEqual((self.stage / "final_report.pdf").read_bytes(), b"%PDF combined")

    def test_cache_generated_directly_in_evidence_is_published_as_diagnostics(self):
        self.put("evidence/knowledge_cache.json", "{}")
        self.put("evidence/knowledge_http_cache/r.json", "{}")
        organize_classifier(self.stage)
        self.assertFalse((self.stage / "evidence/knowledge_cache.json").exists())
        self.assertTrue((self.stage / "diagnostics/cache/knowledge_cache.json").is_file())
        self.assertTrue((self.stage / "diagnostics/cache/http/r.json").is_file())


if __name__ == "__main__":
    unittest.main()
