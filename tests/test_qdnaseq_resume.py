"""Regression for generated sample sheets invalidating native stage reuse."""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.engine import IlluminaSample, run_qdnaseq
from oncotracer_cli.runtime import StageLedger


class QdnaseqResumeTests(unittest.TestCase):
    def test_reuses_unchanged_calling_but_reacts_to_changed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bam = base / "sample.bam"
            bam.write_bytes(b"aligned reads")
            annotation = base / "bins.rds"
            annotation.write_bytes(b"annotation")
            sample = IlluminaSample("sample", base / "reads.fastq", None, "tumor")
            result_dir = base / "run"
            outputs = result_dir / "qdnaseq"
            toolchain = Mock()
            toolchain.rscript.side_effect = lambda group, command: [str(item) for item in command]
            toolchain.environment.return_value = {}
            runner = Mock(dry_run=False)

            def create_outputs(*args, **kwargs):
                outputs.mkdir(parents=True, exist_ok=True)
                (outputs / "all_segments.seg").write_text("sample segments\n")
                (outputs / "qdnaseq_sample_status.json").write_text('{"overall_status":"complete"}\n')
                (outputs / "qdnaseq_sample_roles.tsv").write_text("sample\tstatus\n")

            runner.run.side_effect = create_outputs

            def run(selected_sample=sample):
                run_qdnaseq(
                    ROOT, base, [selected_sample], {"sample": bam}, result_dir,
                    100, runner, StageLedger(base / "state.json"), toolchain,
                    force=False,
                )

            with (
                patch("oncotracer_cli.engine.prepare_qdnaseq_annotation", return_value=annotation),
                patch("oncotracer_cli.engine._validated_qdnaseq_reader",
                      side_effect=lambda *args: contextlib.nullcontext()),
            ):
                run()
                self.assertEqual(runner.run.call_count, 1)
                run()
                self.assertEqual(runner.run.call_count, 1, "unchanged sample sheet must reuse QDNAseq")
                bam.write_bytes(b"new aligned reads")
                run()
                self.assertEqual(runner.run.call_count, 2)
                run(IlluminaSample("sample", sample.fastq_1, None, "normal"))
                self.assertEqual(runner.run.call_count, 3)
                (outputs / "all_segments.seg").write_text("")
                run(IlluminaSample("sample", sample.fastq_1, None, "normal"))
                self.assertEqual(runner.run.call_count, 4, "empty results must be recomputed")


if __name__ == "__main__":
    unittest.main()
