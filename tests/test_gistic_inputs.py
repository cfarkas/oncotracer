"""GISTIC SEG boundaries must resolve to the generated marker coordinates."""
from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

SOURCE = Path(__file__).resolve().parents[1] / "bin/cna_classifier_nf/bin/01_prepare_cna_inputs.py"
spec = importlib.util.spec_from_file_location("prepare_gistic_inputs", SOURCE)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class GisticInputTests(unittest.TestCase):
    def read_rows(self, path):
        with path.open(newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))

    def test_refined_events_and_neutral_gaps_have_exact_marker_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = pd.DataFrame([
                {"sample": "treated", "chrom": "1", "start": 64700, "end": 128000, "n_bins": 1, "mean_log2": -0.74},
                {"sample": "treated", "chrom": "1", "start": 150001, "end": 180001, "n_bins": 1, "mean_log2": 0.64},
                {"sample": "baseline", "chrom": "1", "start": 50000, "end": 110005, "n_bins": 1, "mean_log2": -0.52},
            ])
            sizes = {"1": 250000, "2": 40000}
            full = root / "full.seg"
            prepare.make_full_genome_gistic_seg(events, ["treated", "baseline", "no_events"], sizes, 100000, str(full))
            altered = root / "events.seg"
            events.rename(columns={"sample": "Sample", "chrom": "Chromosome", "start": "Start", "end": "End", "n_bins": "Num_Probes", "mean_log2": "Segment_Mean"}).to_csv(altered, sep="\t", index=False)
            before = {path: path.read_bytes() for path in (full, altered)}
            markers = root / "markers.tsv"
            count = prepare.make_gistic_marker_file(sizes, 100000, str(markers), segment_paths=(altered, full))
            rows = self.read_rows(markers)
            coords = {(row["Chromosome"], int(row["Marker Position"])) for row in rows}
            self.assertEqual(count, len(coords))
            self.assertEqual(len(rows), len(coords))
            self.assertEqual([row["Marker Name"] for row in rows], list(dict.fromkeys(row["Marker Name"] for row in rows)))
            for path in (full, altered):
                self.assertEqual(path.read_bytes(), before[path])
                for row in self.read_rows(path):
                    for key in ("Start", "End"):
                        self.assertIn((row["Chromosome"], int(row[key])), coords)
            for position in (50000, 150000, 250000):
                self.assertIn(("1", position), coords)
            self.assertIn(("1", 64700), coords)
            self.assertIn(("1", 64699), coords)
            self.assertIn(("1", 128001), coords)
            self.assertIn(("2", 1), coords)
            self.assertIn(("2", 40000), coords)
            no_events = [row for row in self.read_rows(full) if row["Sample"] == "no_events"]
            self.assertEqual(len(no_events), 2)
            self.assertTrue(all(float(row["Segment_Mean"]) == 0 for row in no_events))
            ordered = [(int(row["Chromosome"]), int(row["Marker Position"])) for row in rows]
            self.assertEqual(ordered, sorted(ordered))

    def test_shared_boundaries_gaps_and_single_base_segments_keep_coordinates_and_consistent_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seg = root / "segments.seg"
            seg.write_text(
                "Sample\tChromosome\tStart\tEnd\tNum_Probes\tSegment_Mean\n"
                "a\t1\t1\t30\t99\t0.0\n"
                "a\t1\t30\t40\t99\t0.7\n"
                "a\t1\t81\t100\t99\t-0.4\n"
                "b\t1\t31\t31\t99\t1.2\n"
            )
            before = self.read_rows(seg)
            markers = root / "markers.tsv"
            prepare.make_gistic_marker_file({"1": 100}, 100, str(markers), segment_paths=(seg,))
            prepare.update_gistic_probe_counts((seg,), markers)
            after = self.read_rows(seg)
            for old, new in zip(before, after):
                self.assertEqual({k: v for k, v in old.items() if k != "Num_Probes"}, {k: v for k, v in new.items() if k != "Num_Probes"})
            self.assertEqual([int(row["Num_Probes"]) for row in after], [2, 3, 2, 1])
            self.assertEqual([int(row["Marker Position"]) for row in self.read_rows(markers)], [1, 30, 31, 40, 50, 81, 100])
            # No synthetic neutral segment is added to the altered-only SEG gap.
            self.assertEqual(len(after), 4)

    def test_probe_counts_refuse_endpoints_absent_from_marker_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seg = root / "segments.seg"
            seg.write_text("Sample\tChromosome\tStart\tEnd\tNum_Probes\tSegment_Mean\na\t1\t1\t100\t1\t0.2\n")
            before = seg.read_bytes()
            markers = root / "markers.tsv"
            prepare.make_gistic_marker_file({"1": 100}, 100, str(markers))
            with self.assertRaisesRegex(ValueError, "endpoints are missing"):
                prepare.update_gistic_probe_counts((seg,), markers)
            self.assertEqual(seg.read_bytes(), before)

    def test_complete_preparation_retains_narrow_events_and_original_bin_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "cna_events.tsv"
            events.write_text(
                "sample\tstate\tchrom\tstart\tend\tn_bins\tmean_log2\n"
                "a\tgain\t1\t21\t40\t1\t0.4\n"
                "b\tamplification\t1\t61\t61\t1\t1.2\n"
            )
            original = events.read_bytes()
            notation = root / "notation.tsv"
            notation.write_text("sample\tn_cna_events\na\t1\nb\t1\nc\t0\n")
            sizes = root / "sizes.tsv"
            sizes.write_text("chrom\tsize\n1\t100\n")
            result = subprocess.run(
                [sys.executable, str(SOURCE), "--cna-events", str(events),
                 "--cna-notation", str(notation), "--region-catalog",
                 str(SOURCE.parent.parent / "assets/pancancer_cna_regions.tsv"),
                 "--chrom-sizes", str(sizes), "--min-bins", "1", "--min-size-mb", "0",
                 "--gistic-window-bp", "100", "--samples", "a,b,c"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(events.read_bytes(), original)
            self.assertEqual([row["Num_Probes"] for row in self.read_rows(root / "samurai_events.seg")], ["1", "1"])
            self.assertEqual([row["n_bins"] for row in self.read_rows(root / "clean_events.tsv")], ["1", "1"])
            points = {int(row["Marker Position"]) for row in self.read_rows(root / "gistic_markers.tsv")}
            for name in ("gistic_events.seg", "gistic_full.seg"):
                for row in self.read_rows(root / name):
                    start, end = int(row["Start"]), int(row["End"])
                    self.assertIn(start, points)
                    self.assertIn(end, points)
                    self.assertEqual(int(row["Num_Probes"]), sum(start <= point <= end for point in points))
            narrow = next(row for row in self.read_rows(root / "gistic_events.seg") if row["Sample"] == "b")
            self.assertEqual((narrow["Start"], narrow["End"], narrow["Num_Probes"]), ("61", "61", "1"))
            metrics = json.loads((root / "prepare_metrics.json").read_text())
            self.assertEqual(metrics["samples_total"], 3)
            self.assertEqual(metrics["gistic_marker_model"], "uniform_pseudo_markers_plus_all_seg_endpoints")

    def test_grid_and_terminal_boundary_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            markers = Path(directory) / "markers.tsv"
            count = prepare.make_gistic_marker_file({"1": 150000}, 100000, str(markers))
            self.assertEqual(count, 2)
            self.assertEqual([int(row["Marker Position"]) for row in self.read_rows(markers)], [50000, 150000])

    def test_invalid_segment_is_rejected_before_marker_file_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment = root / "invalid.seg"
            markers = root / "markers.tsv"
            for chrom, start, end in (("1", 0, 10), ("1", 20, 10), ("1", 1, 200001), ("2", 1, 10)):
                with self.subTest(chrom=chrom, start=start, end=end):
                    segment.write_text(f"Chromosome\tStart\tEnd\n{chrom}\t{start}\t{end}\n")
                    with self.assertRaisesRegex(ValueError, "outside chromosome bounds"):
                        prepare.make_gistic_marker_file({"1": 200000}, 100000, str(markers), segment_paths=(segment,))
                    self.assertFalse(markers.exists())


if __name__ == "__main__":
    unittest.main()
