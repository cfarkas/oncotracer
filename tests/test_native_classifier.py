from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import sys
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.classifier import _run_gistic, run_native_classifier, sample_set_key
from oncotracer_cli.engine import Toolchain
from oncotracer_cli.runtime import CommandRunner, StageLedger


ROOT = Path(__file__).resolve().parents[1]


class _RecordingRunner(CommandRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.containment_calls: list[tuple[str, dict[str, str | None] | None, bool]] = (
            []
        )

    def run(self, stage, command, **kwargs):
        containment = kwargs.get("containment")
        self.containment_calls.append((stage, containment, "env" in kwargs))
        return super().run(stage, command, **kwargs)


class NativeClassifierTests(unittest.TestCase):
    def test_sample_set_aliases(self) -> None:
        self.assertEqual(
            sample_set_key({"cna_classifier_sample_set": "DLBCL"}), "lymphoma"
        )
        self.assertEqual(
            sample_set_key({"cna_classifier_sample_set": "AML"}), "leukemia"
        )
        self.assertEqual(
            sample_set_key({"cna_classifier_sample_set": "breast:S1,S2"}), "breast"
        )

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
                f'test "$LD_LIBRARY_PATH" = {expected!r} || exit 88\n'
                "exit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            native = workspace / ".native"
            runner = _RecordingRunner(native / "trace.tsv", echo=False)
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
                Toolchain(
                    gistic_prefix=prefix,
                    runtime_cache=workspace / "runtime-cache",
                ),
                force=True,
            )
            self.assertTrue((output / ".oncotracer-complete").is_file())
            self.assertIn("completed", status.read_text(encoding="utf-8"))
            routed = {
                stage: (containment, used_env)
                for stage, containment, used_env in runner.containment_calls
                if stage.startswith("classifier-gistic")
            }
            self.assertEqual(
                set(routed), {"classifier-gistic-version", "classifier-gistic"}
            )
            for containment, used_env in routed.values():
                self.assertFalse(used_env)
                self.assertIn("gistic.conf", str(containment["FONTCONFIG_FILE"]))

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
                (
                    ROOT
                    / "bin/cna_classifier_nf/test/mini_cna_cytogenomic_notation.tsv"
                ).read_bytes()
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
            runner = _RecordingRunner(native / "trace.tsv", echo=False)
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
                Toolchain(
                    classifier_prefix=Path(sys.prefix),
                    runtime_cache=workspace / "runtime-cache",
                ),
                force=True,
            )
            self.assertEqual(result, analysis / "05_cna_classifier")
            knowledge_reports = analysis / "04_cna_custom_plots/llm_reports"
            required = [
                result / "01_prepared/clean_events.tsv",
                result / "02_classification/cna_patient_classification.tsv",
                result / "03_report/cna_classifier_report.html",
                knowledge_reports / "index.html",
                knowledge_reports / "pdf_report_index.tsv",
                knowledge_reports / "pdf_html_report_index.tsv",
                knowledge_reports / "all_sample_CNA_knowledge_reports.pdf",
                result / "03_report/clinician_reports/clinician_report_index.tsv",
                result / "06_knowledge/sample_knowledge_summary.tsv",
                result / "07_pathology/pathology_concordance.tsv",
                result / "native_classifier_summary.json",
            ]
            for path in required:
                self.assertTrue(path.is_file() and path.stat().st_size > 0, path)
            self.assertFalse((result / "03_report/pdf_reports").exists())
            self.assertTrue(list(knowledge_reports.glob("*_CNA_knowledge_report.html")))
            self.assertTrue(list(knowledge_reports.glob("*_CNA_knowledge_report.pdf")))

            class Links(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.hrefs = []

                def handle_starttag(self, tag, attrs):
                    if tag == "a":
                        self.hrefs.extend(value for key, value in attrs if key == "href")

            pages = [*knowledge_reports.glob("*.html"), result / "03_report/cna_classifier_report.html"]
            for page in pages:
                parser = Links()
                parser.feed(page.read_text())
                for href in parser.hrefs:
                    url = urlsplit(href)
                    if not url.scheme and url.path:
                        with self.subTest(page=page.name, href=href):
                            self.assertTrue((page.parent / unquote(url.path)).exists())
            index = (knowledge_reports / "index.html").read_text()
            self.assertIn("../../05_cna_classifier/03_report/cna_classifier_report.html", index)
            self.assertIn("../../05_cna_classifier/06_knowledge/knowledge_llm_trials.tsv", index)
            classifier_summary = json.loads(
                (result / "native_classifier_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(classifier_summary["engine"], "native")
            self.assertFalse(classifier_summary["nextflow_used"])
            self.assertEqual(classifier_summary["gistic_status"], "skipped")
            self.assertEqual(classifier_summary["knowledge_report_index"], str(knowledge_reports / "index.html"))
            trace = (native / "trace.tsv").read_text(encoding="utf-8").lower()
            self.assertNotIn("nextflow", trace)
            self.assertIn(str(Path(sys.prefix) / "bin" / "python").lower(), trace)
            workflow_summary = (summary / "workflow_summary.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("nextflow_used=false", workflow_summary)
            self.assertIn("cna_classifier_completed=true", workflow_summary)
            workflow_summary_json = json.loads(
                (summary / "workflow_summary.json").read_text(encoding="utf-8")
            )
            self.assertIs(workflow_summary_json["nextflow_used"], False)
            self.assertIs(workflow_summary_json["cna_classifier_completed"], True)
            self.assertEqual(workflow_summary_json["cna_knowledge_report_index"], str(knowledge_reports / "index.html"))
            self.assertEqual(workflow_summary_json["cna_knowledge_reports"], str(knowledge_reports))
            self.assertEqual(workflow_summary_json["cna_knowledge_evidence"], str(result / "06_knowledge"))
            self.assertIn("cna_knowledge_report_index=", workflow_summary)
            # Resume tracks the new report paths through the shared analysis ledger.
            record = ledger.data["stages"]["classifier-pdf-reports"]
            report_outputs = [knowledge_reports / name for name in
                              ("index.html", "pdf_report_index.tsv", "pdf_html_report_index.tsv",
                               "all_sample_CNA_knowledge_reports.pdf")]
            self.assertEqual({row["path"] for row in record["outputs"]},
                             {str(path.resolve()) for path in report_outputs})
            self.assertTrue(ledger.reusable("classifier-pdf-reports", record["signature"], report_outputs))
            for missing in ("index.html", "all_sample_CNA_knowledge_reports.pdf"):
                with self.subTest(missing=missing):
                    path = knowledge_reports / missing
                    contents = path.read_bytes()
                    path.unlink()
                    self.assertFalse(ledger.reusable("classifier-pdf-reports", record["signature"], report_outputs))
                    path.write_bytes(contents)
            classifier_calls = [
                (stage, containment)
                for stage, containment, _used_env in runner.containment_calls
                if stage.startswith("classifier-")
            ]
            self.assertTrue(classifier_calls)
            for stage, containment in classifier_calls:
                with self.subTest(stage=stage):
                    self.assertIn(
                        "classifier.conf", str(containment["FONTCONFIG_FILE"])
                    )


if __name__ == "__main__":
    unittest.main()
