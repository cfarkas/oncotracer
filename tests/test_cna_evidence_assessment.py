"""Conservative molecular CNA assessment and truthful uncertainty presentation."""
from __future__ import annotations
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "bin/cna_classifier_nf/bin"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classifier = load("evidence_classifier", "02_classify_cna.py")
pathology = load("evidence_pathology", "07_pathology_concordance.py")
clinician = load("evidence_clinician", "08_clinician_driver_reports.py")
sys.path.insert(0, str(SCRIPTS))
knowledge = load("evidence_knowledge", "05_scrape_cna_knowledge.py")
sys.path.remove(str(SCRIPTS))


class CNAEvidenceTests(unittest.TestCase):
    def test_single_egfr_gain_does_not_assign_glioma(self):
        label = classifier.final_class("CNA-intermediate", "gain-dominant", ["7p11_EGFR_gain_amp"])
        self.assertEqual(label, "EGFR_region_gain_amp_CNA_pattern")
        self.assertNotIn("glioma", label.lower())

    def test_multiple_patterns_are_retained_without_rule_order_winner(self):
        flags = ["7p11_EGFR_gain_amp", "17q12_ERBB2_HER2_gain_amp"]
        self.assertEqual(classifier.final_class("CNA-intermediate", "mixed", flags), "multiple_molecular_CNA_patterns")
        self.assertEqual(set(classifier.matching_cna_patterns("CNA-intermediate", flags)),
                         {"EGFR_region_gain_amp", "ERBB2_HER2_region_gain_amp"})
        self.assertEqual(classifier.matching_cna_patterns("CNA-flat", flags), [])

    def test_shared_segments_are_counted_once_and_partial_overlaps_flagged(self):
        hits = pd.DataFrame([
            {"feature_id": "7p11_EGFR_gain_amp", "event_chrom": "chr7", "event_start": 0,
             "event_end": 159000000, "event_state": "gain", "overlap_fraction_region": 1},
            {"feature_id": "7q31_MET_gain_amp", "event_chrom": "7", "event_start": 0,
             "event_end": 159000000, "event_state": "gain", "overlap_fraction_region": 0.5},
        ])
        result = classifier.assess_cna_evidence(pd.Series({"n_cna_events": 1}),
                    ["7p11_EGFR_gain_amp", "7q31_MET_gain_amp"], hits, ["EGFR", "MET"])
        self.assertEqual(result["n_catalog_regions_detected"], 2)
        self.assertEqual(result["n_distinct_driver_supporting_segments"], 1)
        self.assertEqual(result["n_shared_driver_supporting_segments"], 1)
        self.assertEqual(result["n_partial_catalog_region_overlaps"], 1)
        self.assertEqual(result["n_driver_supporting_amplification_segments"], 0)
        self.assertIn("catalog_hits_share_segments", result["cna_uncertainty_flags"])
        self.assertIn("does not establish focal EGFR amplification", result["cna_assessment_summary"])

    def test_missing_coordinates_and_flat_profile_are_explicit(self):
        hits = pd.DataFrame([{"feature_id": "MYC", "event_state": "gain"}])
        result = classifier.assess_cna_evidence(pd.Series({"n_cna_events": 2}), ["MYC"], hits, [])
        self.assertEqual(result["driver_segment_support_status"], "partial")
        flat = classifier.assess_cna_evidence(pd.Series({"n_cna_events": 0}), [], pd.DataFrame(), [])
        self.assertEqual(flat["cna_diagnostic_resolution"], "not_assessable")
        self.assertIn("does not exclude", flat["cna_assessment_summary"])
        bad = classifier.assess_cna_evidence(pd.Series({"n_cna_events": 0}), ["MYC"], hits, [])
        self.assertEqual(bad["cna_evidence_status"], "inconsistent_input")

    def test_broad_context_and_literature_do_not_create_a_tissue_assignment(self):
        row = pd.Series({"n_cna_events": 20, "altered_mb": 100, "cna_burden_class": "CNA-intermediate",
                         "driver_region_flags": "7p11_EGFR_gain_amp", "matched_cna_patterns": "EGFR_region_gain_amp"})
        profile = pathology.infer_cna_profile(row, pd.DataFrame(), pd.DataFrame())
        result = pathology.cna_probable_classification(profile, row, sample_set="broad_cancer")
        enriched = pathology.cna_probable_classification(profile, row,
                    pd.Series({"knowledge_literature_strength": 8, "knowledge_refined_class": "Glioma-like"}), sample_set="broad_cancer")
        self.assertNotIn("glioma", result["probable_cna_classification"].lower())
        self.assertEqual(result["context_assignment_status"], "not_inferred_from_cna")
        self.assertEqual(result["probable_cna_score"], enriched["probable_cna_score"])
        self.assertEqual(result["probable_cna_probability_estimate"], "")
        restricted = pathology.cna_probable_classification(profile, row, sample_set="breast")
        self.assertEqual(restricted["context_assignment_status"], "supplied_study_context_not_independently_inferred")

    def test_knowledge_summary_preserves_neutral_patterns_from_saved_evidence(self):
        row = pd.Series({"sample": "synthetic", "n_cna_events": 20,
                         "cna_burden_class": "CNA-intermediate",
                         "rule_based_cna_class": "EGFR_CNS_glioma_like_legacy_pattern",
                         "driver_region_flags": "7p11_EGFR_gain_amp;17q12_ERBB2_gain_amp",
                         "matched_cna_patterns": "EGFR_region_gain_amp;ERBB2_HER2_region_gain_amp"})
        hits = pd.DataFrame([{"sample": "synthetic", "feature_id": feature} for feature in
                             ("7p11_EGFR_gain_amp", "17q12_ERBB2_gain_amp")])
        _, summary = knowledge.build_sample_knowledge(pd.DataFrame([row]), hits, pd.DataFrame(), "broad_cancer")
        label = summary.iloc[0]["knowledge_refined_class"]
        self.assertIn("EGFR", label)
        self.assertIn("ERBB2", label)
        self.assertNotIn("glioma", label.lower())
        legacy = row.drop("matched_cna_patterns")
        self.assertEqual(knowledge.infer_refined_class(legacy, ["7p11_EGFR_gain_amp"], "broad_cancer")[0],
                         "CNA pattern, tumor type unresolved")
        self.assertIn("breast", knowledge.infer_refined_class(row, [], "breast")[0])

    def test_unlabelled_scores_never_become_probabilities(self):
        for score in (0, 50, 100):
            self.assertEqual(pathology.score_to_probability_fields(score)["probability_estimate"], "")
        result = pathology.probability_fields_for_score(70, lambda value: float("nan"), "fit", "test")
        self.assertEqual(result["probability_estimate"], "")
        self.assertEqual(result["probability_calibration_status"], "not_estimated_calibration_prediction_failed")

    def test_user_table_fit_preserves_target_and_is_not_external_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "calibration.tsv"
            pd.DataFrame({"agreement_score": [0, 20, 40, 60, 80, 100], "label": [0, 0, 0, 1, 1, 1]}).to_csv(table, sep="\t", index=False)
            fit, status, method = pathology.build_probability_calibrator(str(table))
            self.assertIsNotNone(fit)
            self.assertIn("external_validation_not_established", status)
            mapped = pathology.probability_fields_for_score(80, fit, status, method, "agreement_score")
            self.assertGreater(mapped["probability_estimate"], 0)
            mismatch = pathology.probability_fields_for_score(80, fit, status, method, "probable_cna_score")
            self.assertEqual(mismatch["probability_estimate"], "")
            self.assertEqual(mismatch["probability_calibration_status"], "not_estimated_calibration_target_mismatch")
            pd.DataFrame({"score": [0, 20, 40, 60, 80, 100], "label": [0, 0, 0, 2, 2, 2]}).to_csv(table, sep="\t", index=False)
            self.assertIsNone(pathology.build_probability_calibrator(str(table))[0])

    def test_clinician_html_exposes_evidence_and_withholds_legacy_pseudo_probability(self):
        row = pd.Series({"sample": "synthetic", "n_cna_events": 1, "altered_mb": 100,
                         "cna_assessment_summary": "Two regions share one supporting segment.",
                         "n_distinct_driver_supporting_segments": 1, "driver_segment_support_status": "complete"})
        pr = pd.Series({"probable_cna_classification": "Molecular CNA pattern",
                        "probable_cna_score": 70, "probable_cna_probability_estimate": 0.987,
                        "probable_cna_probability_calibration_status": "heuristic_sigmoid_uncalibrated"})
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.html"
            clinician.build_html(report, "synthetic", row, pd.Series(dtype=object), pr,
                                 pd.Series(dtype=object), pd.DataFrame(), pd.DataFrame(), "report.pdf")
            text = report.read_text()
            self.assertIn("CNA Pattern and Evidence Assessment", text)
            self.assertIn("Two regions share one supporting segment.", text)
            self.assertIn("Diagnostic probability", text)
            self.assertNotIn("0.987", text)
            self.assertNotIn("Probability estimate", text)


if __name__ == "__main__":
    unittest.main()
