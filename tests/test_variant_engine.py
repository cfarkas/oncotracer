#!/usr/bin/env python3
"""Native orchestration preserves independent successful CNA/variant branches."""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import engine, variants  # noqa: E402
from oncotracer_cli.runtime import OncoTracerError, OncoTracerPartialFailure  # noqa: E402
from tests.test_output_safety import make_illumina_config, make_runtime_root  # noqa: E402


def variant_config(base: Path, output: Path) -> Path:
    config = make_illumina_config(base, output, "SYNTHETIC")
    with config.open("a") as handle:
        handle.write("run_variants: true\nvariant_specimen_type: fresh\nvariant_callers: bcftools\nvariant_annovar: off\n")
    return config


class VariantEngineTests(unittest.TestCase):
    def test_missing_variant_tool_does_not_prevent_cna_or_browsable_failure_report(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variant-engine-") as directory:
            base = Path(directory)
            runtime = make_runtime_root(base)
            output = base / "results"
            config = variant_config(base, output)
            def publish_cna(*args, **kwargs):
                summary = output / "06_workflow_summary"
                summary.mkdir(parents=True)
                (summary / "workflow_summary.json").write_text(json.dumps({
                    "workflow_status": "complete", "completed_samples": ["SYNTHETIC"], "failed_samples": []}))
                (summary / "workflow_summary.txt").write_text("workflow_status=complete\n")
            with (
                patch.object(engine, "prepare_reference", return_value={}),
                patch.object(engine, "align_illumina", return_value={"SYNTHETIC": base / "input.bam"}),
                patch.object(engine, "_validated_fasta_reader", side_effect=lambda *a, **k: contextlib.nullcontext()),
                patch.object(variants, "preflight_variant_tools", side_effect=OncoTracerError("Synthetic variant tool unavailable")) as preflight,
                patch.object(engine, "run_qdnaseq", return_value=(base / "qdnaseq", base / "bams")) as qdnaseq,
                patch.object(engine, "run_refinement_and_outputs", side_effect=publish_cna) as refinement,
                self.assertRaisesRegex(OncoTracerPartialFailure, "variants=failed"),
            ):
                engine.run_native(config, root=runtime)
            preflight.assert_called_once()
            qdnaseq.assert_called_once()
            refinement.assert_called_once()
            summary = json.loads((output / "06_workflow_summary/workflow_summary.json").read_text())
            self.assertEqual(summary["workflow_status"], "partial_failure")
            self.assertEqual(summary["cna_status"], "complete")
            self.assertEqual(summary["variant_status"], "failed")
            self.assertEqual(summary["variant_failed_samples"], ["SYNTHETIC"])
            failure = Path(summary["variant_status_file"])
            self.assertTrue(failure.is_file())
            self.assertIn("tool unavailable", failure.read_text())
            self.assertFalse((output / "08_variants").exists(), "preflight failure must not create unowned caller output")
            self.assertTrue((output / "index.html").is_file())
            manifest = json.loads((output / "06_workflow_summary/native_run_manifest.json").read_text())
            self.assertEqual(manifest["workflow_status"], "partial_failure")

    def test_cna_failure_preserves_completed_variant_and_partial_result_index(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-cna-failure-") as directory:
            base = Path(directory)
            runtime = make_runtime_root(base)
            output = base / "results"
            config = variant_config(base, output)
            primary = output / "08_variants/samples/SYNTHETIC/bcftools/SYNTHETIC.bcftools.vcf"
            contents = ("##fileformat=VCFv4.2\n"
                        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC\n"
                        "chr1\t120\t.\tC\tT\t60\tPASS\t.\tGT\t0/1\n")
            def publish_variant(*args, **kwargs):
                primary.parent.mkdir(parents=True)
                primary.write_text(contents)
                status = {"overall_status": "complete", "completed_samples": ["SYNTHETIC"], "failed_samples": []}
                (output / "08_variants/variant_status.json").write_text(json.dumps(status))
                return status
            with (
                patch.object(engine, "prepare_reference", return_value={}),
                patch.object(engine, "align_illumina", return_value={"SYNTHETIC": base / "input.bam"}),
                patch.object(engine, "_validated_fasta_reader", side_effect=lambda *a, **k: contextlib.nullcontext()),
                patch.object(engine, "run_variants", side_effect=publish_variant) as call_variants,
                patch.object(engine, "run_qdnaseq", side_effect=OncoTracerError("Synthetic CNA failure")),
                patch.object(engine, "run_refinement_and_outputs") as refinement,
                self.assertRaisesRegex(OncoTracerPartialFailure, "CNA=failed"),
            ):
                engine.run_native(config, root=runtime)
            call_variants.assert_called_once()
            refinement.assert_not_called()
            self.assertEqual(primary.read_text(), contents)
            summary = json.loads((output / "06_workflow_summary/workflow_summary.json").read_text())
            self.assertEqual(summary["workflow_status"], "partial_failure")
            self.assertEqual(summary["cna_status"], "failed")
            self.assertEqual(summary["variant_status"], "complete")
            self.assertEqual(summary["variant_completed_samples"], ["SYNTHETIC"])
            self.assertTrue((output / "index.html").is_file())
            manifest = json.loads((output / "06_workflow_summary/native_run_manifest.json").read_text())
            self.assertEqual(manifest["workflow_status"], "partial_failure")

    def test_report_publication_failure_is_a_real_error_even_with_partial_summary(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-publication-failure-") as directory:
            base = Path(directory)
            runtime = make_runtime_root(base)
            output = base / "results"
            config = variant_config(base, output)
            def publish_cna(*args, **kwargs):
                summary = output / "06_workflow_summary"
                summary.mkdir(parents=True)
                (summary / "workflow_summary.json").write_text(json.dumps({
                    "workflow_status": "complete", "completed_samples": ["SYNTHETIC"], "failed_samples": []}))
                (summary / "workflow_summary.txt").write_text("workflow_status=complete\n")
            with (
                patch.object(engine, "prepare_reference", return_value={}),
                patch.object(engine, "align_illumina", return_value={"SYNTHETIC": base / "input.bam"}),
                patch.object(engine, "_validated_fasta_reader", side_effect=lambda *a, **k: contextlib.nullcontext()),
                patch.object(variants, "preflight_variant_tools", side_effect=OncoTracerError("Missing variant tool")),
                patch.object(engine, "run_qdnaseq", return_value=(base / "qdnaseq", base / "bams")),
                patch.object(engine, "run_refinement_and_outputs", side_effect=publish_cna),
                patch("oncotracer_cli.results.write_results_index", side_effect=OncoTracerError("Publication failed")),
                self.assertRaisesRegex(OncoTracerError, "Publication failed") as raised,
            ):
                engine.run_native(config, root=runtime)
            self.assertNotIsInstance(raised.exception, OncoTracerPartialFailure)
            summary = json.loads((output / "06_workflow_summary/workflow_summary.json").read_text())
            self.assertEqual(summary["workflow_status"], "partial_failure")
            self.assertFalse((output / "index.html").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
