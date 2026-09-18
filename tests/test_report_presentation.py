"""Bibliography compaction must preserve publication and feature provenance."""
import importlib.util
import sys
import unittest
from pathlib import Path
import pandas as pd

scripts = Path(__file__).resolve().parents[1] / "bin/cna_classifier_nf/bin"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("presentation_pdf", scripts / "06_pdf_knowledge_reports.py")
pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdf)
sys.path.remove(str(scripts))


class ReportPresentationTests(unittest.TestCase):
    def test_publication_aliases_merge_features_without_losing_citations(self):
        source = pd.DataFrame([
            {"pmid": "123", "feature_id": "MYC", "title": "One paper"},
            {"doi": "https://doi.org/10.1/ABC", "feature_id": "BCL2", "title": "One paper"},
            {"pmid": "123", "doi": "10.1/abc", "feature_id": "TP53", "title": "One paper"},
            {"pmid": "456", "feature_id": "MYC", "title": "Second paper"},
        ])
        original = source.copy(deep=True)
        result = pdf.unique_report_references(source)
        self.assertEqual(len(result), 2)
        self.assertEqual(set(result.iloc[0]["feature_id"].split("; ")), {"MYC", "BCL2", "TP53"})
        self.assertEqual(set(result["pmid"]), {"123", "456"})
        pd.testing.assert_frame_equal(source, original)

    def test_missing_identifiers_do_not_collapse_placeholder_records(self):
        rows = pd.DataFrame([
            {"title": "PMID seed from built-in CNA knowledge dictionary", "feature_id": "A"},
            {"title": "PMID seed from built-in CNA knowledge dictionary", "feature_id": "B"},
            {"title": "Exact paper title", "feature_id": "C"},
            {"title": "  Exact paper title ", "feature_id": "D"},
        ])
        result = pdf.unique_report_references(rows)
        self.assertEqual(len(result), 3)
        self.assertEqual(result.iloc[-1]["feature_id"], "C; D")

    def test_wide_evidence_tables_are_contained_for_browser_scrolling(self):
        rendered = pdf.html_table(pd.DataFrame([{"source": "x" * 500, "value": "<unsafe>"}]))
        self.assertTrue(rendered.startswith("<div class='table-wrap'>"))
        self.assertTrue(rendered.endswith("</div>"))
        self.assertIn("&lt;unsafe&gt;", rendered)

    def test_methods_do_not_present_unvalidated_sigmoid_as_probability(self):
        text = pdf.probable_cna_score_method_text() + pdf.pathology_score_method_text()
        self.assertIn("Diagnostic probability is not estimated", text)
        self.assertNotIn("sigmoid-derived", text)
        self.assertIn("do not increase", text)


if __name__ == "__main__":
    unittest.main()
