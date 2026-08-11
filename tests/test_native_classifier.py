from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.classifier import _run_gistic, run_native_classifier, sample_set_key
from oncotracer_cli.engine import Toolchain
from oncotracer_cli.runtime import CommandRunner, StageLedger


ROOT = Path(__file__).resolve().parents[1]


class NativeClassifierTests(unittest.TestCase):
    def test_sample_set_aliases(self) -> None:
        self.assertEqual(sample_set_key({"cna_classifier_sample_set": "DLBCL"}), "lymphoma")
        self.assertEqual(sample_set_key({"cna_classifier_sample_set": "AML"}), "leukemia")
        self.assertEqual(sample_set_key({"cna_classifier_sample_set": "breast:S1,S2"}), "breast")

    def test_gistic_runtime_receives_exact_prefix_mcr_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            prepared = workspace / "prepared"
            prepared.mkdir()
            (prepared / "gistic_full.seg").write_text(
                "ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\n",
                encoding="utf-8",
            )
            (prepared / "gistic_events.seg").write_text(
                "ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\n",
                encoding="utf-8",
            )
            (prepared / "gistic_markers.tsv").write_text(
                "marker\tchrom\tposition\n",
                encoding="utf-8",
            )
            (prepared / "prepare_metrics.json").write_text(
                json.dumps({"samples_total": 2}) + "\n",
                encoding="utf-8",
            )
            refgene = workspace / "refgene.mat"
            refgene.write_bytes(b"refgene")

            prefix = workspace / "gistic"
            executable = prefix / "bin" / "gistic2"
            executable.parent.mkdir(parents=True)
            mcr = prefix / "share" / "mcr-8.3-0" / "v83"
            libraries = [
                mcr / "runtime" / "glnxa64",
                mcr / "bin" / "glnxa64",
                mcr / "sys" / "os" / "glnxa64",
            ]
            for path in libraries:
                path.mkdir(parents=True)
            expected = ":".join(str(path) for path in libraries)
            executable.write_text(
                "#!/bin/sh\n"
                f"test \"$LD_LIBRARY_PATH\" = {expected!r} || exit 88\n"
                "exit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            native = workspace / ".native"
            runner = CommandRunner(native / "trace.tsv", echo=False)
            ledger = StageLedger(native / "state.json")
            output, status, _command = _run_gistic(
                ROOT,
                {
                    "run_gistic": True,
                    "gistic_required": True,
                    "gistic_refgene": str(refgene),
                    "gistic_min_samples": 2,
                },
                workspace / "lpwgs",
                prepared,
                workspace / "output",
                runner,
                ledger,
                Toolchain(gistic_prefix=prefix),
                force=True,
            )
            self.assertTrue((output / ".oncotracer-complete").is_file())
            self.assertIn("completed", status.read_text(encoding="utf-8"))

    def test_complete_offline_classifier_graph_without_nextflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            analysis = workspace / "analysis"
            codification = analysis / "03_cna_codification"
            summary = analysis / "06_workflow_summary"
            codification.mkdir(parents=True)
            summary.mkdir(parents=True)
            (codification / "cna_events.tsv").write_bytes(
                (ROOT / "bin/cna_classifier_nf/test/mini_cna_events.tsv").read_bytes()
            )
            (codification / "cna_cytogenomic_notation.tsv").write_bytes(
                (ROOT / "bin/cna_classifier_nf/test/mini_cna_cytogenomic_notation.tsv").read_bytes()
            )
            (summary / "workflow_summary.json").write_text(
                json.dumps(
                    {
                        "engine": "native",
                        "nextflow_used": False,
                        "mode": "illumina",
                        "dataset": "fixture",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (summary / "workflow_summary.txt").write_text(
                "engine=native\nnextflow_used=False\nmode=illumina\ndataset=fixture\n",
                encoding="utf-8",
            )
            native = analysis / ".oncotracer-native"
            runner = CommandRunner(native / "trace.tsv", echo=False)
            ledger = StageLedger(native / "state.json")
            config = {
                "run_cna_classifier": True,
                "cna_classifier_sample_set": "broad_cancer",
                "run_gistic": False,
                "knowledge_web": False,
                "knowledge_literature_llm": False,
                "knowledge_deep_literature": False,
                "knowledge_deep_enable_llm_ranker": False,
                "knowledge_literature_reference_llm_selection": False,
                "pathology_use_biomed_models": False,
                "run_pdf_reports": True,
                "run_clinician_reports": True,
            }
            result = run_native_classifier(
                ROOT,
                config,
                analysis,
                workspace / "lpwgs",
                runner,
                ledger,
                Toolchain(classifier_prefix=Path(sys.prefix)),
                force=True,
            )
            self.assertEqual(result, analysis / "05_cna_classifier")
            required = [
                result / "01_prepared/clean_events.tsv",
                result / "02_classification/cna_patient_classification.tsv",
                result / "03_report/cna_classifier_report.html",
                result / "03_report/pdf_reports/pdf_report_index.tsv",
                result / "03_report/clinician_reports/clinician_report_index.tsv",
                result / "06_knowledge/sample_knowledge_summary.tsv",
                result / "07_pathology/pathology_concordance.tsv",
                result / "native_classifier_summary.json",
            ]
            for path in required:
                self.assertTrue(path.is_file() and path.stat().st_size > 0, path)
            classifier_summary = json.loads(
                (result / "native_classifier_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(classifier_summary["engine"], "native")
            self.assertFalse(classifier_summary["nextflow_used"])
            self.assertEqual(classifier_summary["gistic_status"], "skipped")
            trace = (native / "trace.tsv").read_text(encoding="utf-8").lower()
            self.assertNotIn("nextflow", trace)
            self.assertIn(str(Path(sys.prefix) / "bin" / "python").lower(), trace)
            workflow_summary = (summary / "workflow_summary.txt").read_text(encoding="utf-8")
            self.assertIn("nextflow_used=false", workflow_summary)
            self.assertIn("cna_classifier_completed=true", workflow_summary)
            workflow_summary_json = json.loads(
                (summary / "workflow_summary.json").read_text(encoding="utf-8")
            )
            self.assertIs(workflow_summary_json["nextflow_used"], False)
            self.assertIs(workflow_summary_json["cna_classifier_completed"], True)


if __name__ == "__main__":
    unittest.main()
