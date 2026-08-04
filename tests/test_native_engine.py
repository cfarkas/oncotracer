#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.engine import (  # noqa: E402
    Toolchain,
    _correct_ichor_segments,
    parse_illumina_samplesheet,
    parse_ont_samples,
)
from oncotracer_cli.runtime import CommandRunner, StageLedger  # noqa: E402


class NativeEngineTests(unittest.TestCase):
    def test_illumina_samplesheet_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            r1 = root / "A_R1.fastq.gz"
            r2 = root / "A_R2.fastq.gz"
            r1.write_bytes(b"x")
            r2.write_bytes(b"x")
            sheet = root / "samples.csv"
            with sheet.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
                writer.writerow(["A", r1, r2, "tumor"])
            samples = parse_illumina_samplesheet(sheet)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0].sample, "A")
            self.assertEqual(samples[0].status, "tumor")

    def test_ont_barcode_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fastq_pass"
            (root / "barcode01").mkdir(parents=True)
            values = {
                "ont_folder": str(root),
                "ont_barcodes": "1",
                "ont_sample_names": "S1",
            }
            samples = parse_ont_samples(values)
            self.assertEqual(samples[0].barcode, "barcode01")
            self.assertEqual(samples[0].sample, "S1")

    def test_toolchain_wraps_stage_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("core", "qdna", "ichor"):
                (root / name).mkdir()
            toolchain = Toolchain(root / "core", root / "qdna", root / "ichor")
            with patch("oncotracer_cli.engine.require_command", return_value="conda"):
                command = toolchain.wrap("qdnaseq", ["Rscript", "x.R"])
            self.assertEqual(command[:4], ["conda", "run", "--no-capture-output", "--prefix"])
            self.assertEqual(command[4], str(root / "qdna"))

    def test_trace_uses_argument_arrays_without_nextflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.tsv"
            runner = CommandRunner(trace, dry_run=True, echo=False)
            runner.run("example", ["python", "-c", "print('ok')"])
            text = trace.read_text(encoding="utf-8")
            self.assertIn("example", text)
            self.assertNotIn("nextflow", text.lower())

    def test_stage_ledger_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root / "input"
            output_file = root / "output"
            input_file.write_text("a", encoding="utf-8")
            output_file.write_text("b", encoding="utf-8")
            ledger = StageLedger(root / "state.json")
            signature = ledger.signature("stage", ["command"], [input_file])
            ledger.complete("stage", signature, [output_file])
            self.assertTrue(ledger.reusable("stage", signature, [output_file]))

    def test_ichor_logr_correction_matches_samurai_formula(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seg = root / "segments.seg"
            summary = root / "summary.tsv"
            output = root / "corrected.seg"
            seg.write_text(
                "ID\tchrom\tstart\tend\tnum.mark\tlogR_Copy_Number\n"
                "S1\tchr1\t0\t100\t1\t4\n",
                encoding="utf-8",
            )
            summary.write_text(
                "samplename\tTumor Fraction\tPloidy\tGC-Map correction MAD\n"
                "S1\t0.5\t2\t0.1\n",
                encoding="utf-8",
            )
            _correct_ichor_segments(seg, summary, output)
            with output.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertAlmostEqual(float(rows[0]["adj.seg"]), 1.0)


if __name__ == "__main__":
    unittest.main()
