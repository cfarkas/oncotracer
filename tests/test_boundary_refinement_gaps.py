"""Refinement must preserve unmeasured gaps and genomic coordinate contracts."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bin/scripts/bam_cnv_boundary_refine/bam_cnv_boundary_refine.py"
spec = importlib.util.spec_from_file_location("boundary_refinement_gap_tests", SOURCE)
refine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refine)


class BoundaryGapTests(unittest.TestCase):
    def setUp(self):
        self.old_args = refine.args_global
        refine.args_global = SimpleNamespace(state_gain_threshold=.25, state_loss_threshold=-.25)
        self.addCleanup(setattr, refine, "args_global", self.old_args)

    def prior(self, intervals):
        return pd.DataFrame([
            {"sample": "S1", "chrom": "chr1", "start": start, "end": end,
             "seg_log2": (-.6 if i % 2 == 0 else .5), "num_mark": 2}
            for i, (start, end) in enumerate(intervals)
        ])

    def stats(self, prior, decisions, final_positions=None):
        result = refine.build_boundaries(prior, .1)
        result["final_decision"] = decisions
        result["final_boundary"] = (final_positions if final_positions is not None
                                    else result["original_boundary"])
        result["refined_boundary"] = result["final_boundary"]
        result["boundary_shift_bp"] = result["final_boundary"] - result["original_boundary"]
        result["decision_reason"] = "test_fixture"
        result["coverage_resolution_status"] = "usable_bam_resolution"
        return result

    def bounds(self, rows):
        return list(rows[["start", "end"]].itertuples(index=False, name=None))

    def test_retained_gapped_segments_keep_exact_prior_endpoints(self):
        prior = self.prior([(850000, 121600000), (124950000, 140000000)])
        stats = self.stats(prior, ["kept_original_binning"], [np.nan])
        result = refine.apply_final_boundaries(prior, stats)
        self.assertEqual(self.bounds(result), self.bounds(prior))
        self.assertEqual(result.seg_log2.tolist(), prior.seg_log2.tolist())
        self.assertEqual(result.num_mark.tolist(), prior.num_mark.tolist())
        self.assertEqual(result.final_source.tolist(), ["prior_segmentation"] * 2)

    def test_missing_bam_or_failed_refinement_keeps_contiguous_endpoints(self):
        prior = self.prior([(0, 100), (100, 200)])
        # A rejected candidate's coordinate must never be applied.
        stats = self.stats(prior, ["kept_original_binning"], [130])
        self.assertEqual(self.bounds(refine.apply_final_boundaries(prior, stats)), self.bounds(prior))

    def test_only_accepted_contiguous_boundary_moves(self):
        prior = self.prior([(0, 100), (100, 200), (300, 400)])
        stats = self.stats(prior, ["refined_boundary", "kept_original_binning"], [120, np.nan])
        result = refine.apply_final_boundaries(prior, stats)
        self.assertEqual(self.bounds(result), [(0, 120), (120, 200), (300, 400)])
        self.assertEqual(result.final_source.tolist(), ["bam_refined_boundary", "bam_refined_boundary", "prior_segmentation"])
        self.assertEqual(result.seg_log2.tolist(), prior.seg_log2.tolist())

    def test_gap_is_ineligible_and_reports_actual_width(self):
        for gap in (1, 3350000):
            with self.subTest(gap=gap):
                row = refine.build_boundaries(self.prior([(0, 100), (100 + gap, 200 + gap)]), .1).iloc[0]
                self.assertFalse(row.eligible_for_refinement)
                self.assertEqual(row.prior_gap_bp, gap)
                self.assertEqual(row.original_boundary, 100)

    def test_gap_never_requests_bam_coverage(self):
        row = refine.build_boundaries(self.prior([(0, 100), (300, 500)]), .1).iloc[0]
        args = SimpleNamespace(coarse_binsize_kb=1, fine_bin_kb=.01,
                               search_radius_bp=0, search_radius_bins=2)
        with patch.object(refine, "count_bam_coverage", side_effect=AssertionError("gap reached BAM")):
            result = refine.refine_one_boundary(row, {"S1": Path("unused.bam")}, args, Path("unused"))
        self.assertEqual(result["final_decision"], "kept_original_binning")
        self.assertEqual(result["decision_reason"], "prior_segments_separated_by_gap")
        self.assertEqual(result["coverage_resolution_status"], "not_attempted_prior_gap")
        self.assertTrue(pd.isna(result["final_boundary"]))

    def test_forged_accepted_gap_cannot_fill_unmeasured_interval(self):
        prior = self.prior([(0, 100), (300, 400)])
        stats = self.stats(prior, ["refined_boundary"], [200])
        result = refine.apply_final_boundaries(prior, stats)
        self.assertEqual(self.bounds(result), self.bounds(prior))
        self.assertNotEqual(stats.iloc[0].final_decision, "refined_boundary")

    def test_invalid_accepted_positions_never_drop_a_segment(self):
        prior = self.prior([(100, 200), (200, 300)])
        for position in (50, 100, 300, 350):
            with self.subTest(position=position):
                stats = self.stats(prior, ["refined_boundary"], [position])
                result = refine.apply_final_boundaries(prior, stats)
                self.assertEqual(self.bounds(result), self.bounds(prior))
                self.assertEqual(len(result), 2)
                self.assertNotEqual(stats.iloc[0].final_decision, "refined_boundary")

    def test_crossing_neighbor_proposals_preserve_all_segments(self):
        prior = self.prior([(0, 100), (100, 200), (200, 300)])
        stats = self.stats(prior, ["refined_boundary", "refined_boundary"], [180, 120])
        result = refine.apply_final_boundaries(prior, stats)
        self.assertEqual(len(result), 3)
        self.assertTrue((result.end > result.start).all())
        self.assertEqual(result.end.iloc[:-1].tolist(), result.start.iloc[1:].tolist())
        self.assertLess((stats.final_decision == "refined_boundary").sum(), 2)
        self.assertEqual(result.seg_log2.tolist(), prior.seg_log2.tolist())

    def test_one_based_prior_and_bed_bins_describe_same_bases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.seg"
            path.write_text("ID\tchrom\tstart\tend\tnum.mark\tseg.mean\nS1\t1\t1\t100\t2\t-0.6\nS1\t1\t151\t250\t2\t0.5\n")
            prior = refine.read_prior_segments(path, ["S1"])
            self.assertEqual(self.bounds(prior), [(0, 100), (150, 250)])
            bed = pd.DataFrame({"chrom": ["1", "1"], "start": [0, 150], "end": [100, 250], "log2": [-.6, .5]})
            bins = refine.standardize_bin_df(bed, "S1", Path("input.bed"))
            result = refine.apply_final_boundaries(prior, pd.DataFrame())
            overlaid = refine.overlay_bins_with_segments(bins, result)
            self.assertEqual(self.bounds(overlaid), self.bounds(bins))
            self.assertEqual(sum(overlaid.end - overlaid.start), 200)

    def test_explicit_zero_based_prior_is_not_shifted_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.seg"
            path.write_text("ID\tchrom\tstart\tend\tseg.mean\nS1\t1\t0\t100\t-0.6\n")
            prior = refine.read_prior_segments(path, ["S1"], coordinate_system="zero-based-half-open")
            self.assertEqual(self.bounds(prior), [(0, 100)])

    def test_ichor_closed_bins_are_normalized_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "S1.correctedDepth.txt").write_text(
                "chr\tstart\tend\tlog2_TNratio_corrected\n1\t1\t100\t-0.6\n1\t101\t200\t0.5\n")
            bins = refine.read_ichorcna_bins(root)
            self.assertEqual(self.bounds(bins), [(0, 100), (100, 200)])

    def test_single_base_closed_segment_survives_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "single.seg"
            path.write_text("ID\tchrom\tstart\tend\tseg.mean\nS1\t1\t1\t1\t-0.6\n")
            self.assertEqual(self.bounds(refine.read_prior_segments(path, ["S1"])), [(0, 1)])

    def test_seg_bed_and_converter_exports_roundtrip_without_shift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior = self.prior([(0, 100), (150, 250)])
            bins = prior.rename(columns={"seg_log2": "input_log2"})
            final = refine.apply_final_boundaries(prior, pd.DataFrame())
            refined = refine.overlay_bins_with_segments(bins, final)
            converter = root / "converter.py"
            converter.write_text("# fixture\n")
            cyto = root / "cytoBand.txt.gz"
            cyto.write_bytes(b"fixture")
            args = SimpleNamespace(dataset_name="fixture", caller="qdnaseq",
                prior_coordinate_system="one-based-closed", codification_script=converter, cytoband=cyto)
            refine.write_outputs(root / "out", bins, prior, refine.empty_boundary_stats(), final, refined, args)
            out = root / "out"
            table = pd.read_csv(out / "01_tables/final_segments.tsv", sep="\t")
            self.assertEqual(self.bounds(table), [(0, 100), (150, 250)])
            bed = pd.read_csv(out / "01_tables/final_segments.bed", sep="\t", header=None)
            self.assertEqual(list(zip(bed[1], bed[2])), [(0, 100), (150, 250)])
            for name in ("all_segments.seg", "bam_boundary_refined_gistic.seg", "segments/S1.calls.seg"):
                seg = out / "02_samurai_compatible" / name
                self.assertEqual(self.bounds(refine.read_prior_segments(seg, ["S1"])), self.bounds(prior))
            ichor = pd.read_csv(out / "02_samurai_compatible/segments_logR_corrected_gistic.seg", sep="\t")
            self.assertEqual(self.bounds(ichor), [(1, 100), (151, 250)])
            cytobed = pd.read_csv(out / "04_final_results/cna_cytogenomic_input/qdnaseq_bins/S1_markdup_bins.bed", sep="\t", skiprows=1, header=None)
            self.assertEqual(list(zip(cytobed[1], cytobed[2])), [(0, 100), (150, 250)])
            self.assertEqual(cytobed[3].tolist(), ["chr1:1-100", "chr1:151-250"])
            metadata = list(out.rglob("coordinate_system.json"))
            self.assertTrue(metadata, "Outputs must declare their coordinate contracts")
            text = json.dumps(json.loads(metadata[0].read_text()))
            self.assertIn("zero-based-half-open", text)
            self.assertIn("one-based-closed", text)


if __name__ == "__main__":
    unittest.main()
