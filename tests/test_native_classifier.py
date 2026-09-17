from __future__ import annotations

import importlib.util
import json
import subprocess
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from oncotracer_cli.classifier import _stage, _run_gistic, _update_summary, run_native_classifier, sample_set_key
from oncotracer_cli.engine import Toolchain
from oncotracer_cli.runtime import CommandRunner, OncoTracerError, StageLedger


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
    def test_model_cache_changes_invalidate_llm_stage_but_font_cache_does_not(self):
        ledger = Mock()
        ledger.signature.side_effect = StageLedger.signature
        ledger.reusable.return_value = True
        runner = Mock()
        def signature(env):
            _stage("classifier-knowledge", ["python", "knowledge.py"], [], [],
                   cwd=Path("."), runner=runner, ledger=ledger, force=False, containment=env)
            return ledger.reusable.call_args.args[1]
        first = signature({"HF_HOME":"/models/a", "HOME":"/font/run1"})
        self.assertEqual(first, signature({"HF_HOME":"/models/a", "HOME":"/font/run2"}))
        self.assertNotEqual(first, signature({"HF_HOME":"/models/b", "HOME":"/font/run2"}))
        runner.run.assert_not_called()

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
                "ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1\t100\t2\t0\nS2\t1\t1\t100\t2\t0\n",
                encoding="utf-8",
            )
            (prepared / "gistic_events.seg").write_text(
                "ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1\t100\t2\t0\nS2\t1\t1\t100\t2\t0\n",
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
                'if [ "$1" != "-h" ]; then\n'
                '  printf "Unique Name\\tDescriptor\\tWide Peak Limits\\tPeak Limits\\tRegion Limits\\tq values\\tResidual q values\\tBroad or Focal\\tAmplitude Threshold\\tS1\\tS2\\n" > "$2/all_lesions.conf_90.txt"\n'
                'fi\n'
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

    def test_gistic_zero_exit_requires_final_report_and_resume_tracks_it(self) -> None:
        for mode in ("missing", "malformed", "wrong_samples", "header_only"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                prepared = base / "prepared"
                prepared.mkdir()
                for name in ("gistic_full.seg", "gistic_events.seg"):
                    (prepared / name).write_text("ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1\t100\t2\t0\nS2\t1\t1\t100\t2\t0\n")
                (prepared / "gistic_markers.tsv").write_text("m1\t1\t1\nm2\t1\t100\n")
                (prepared / "prepare_metrics.json").write_text('{"samples_total":2}')
                refgene = base / "refgene.mat"
                refgene.write_text("fixture")
                prefix = base / "gistic"
                (prefix / "bin").mkdir(parents=True)
                (prefix / "bin/gistic2").write_text("fixture")
                (prefix / "bin/gistic2").chmod(0o755)
                for component in ("runtime/glnxa64", "bin/glnxa64", "sys/os/glnxa64"):
                    (prefix / "share/mcr-8.3-0/v83" / component).mkdir(parents=True)
                runner = CommandRunner(base / "trace.tsv", echo=False)
                ledger = StageLedger(base / "state.json")
                output = base / "output"
                lesions = output / "gistic2_out/all_lesions.conf_90.txt"
                config = {"run_gistic": True, "gistic_required": True, "gistic_refgene": str(refgene)}
                toolchain = Toolchain(gistic_prefix=prefix, runtime_cache=base / "cache")
                calls = []
                def run(stage, argv, **kwargs):
                    calls.append(stage)
                    if stage == "classifier-gistic" and mode != "missing":
                        if mode == "malformed":
                            lesions.write_text("GISTIC error\n")
                        else:
                            samples = "S1\tS2" if mode == "header_only" else "S1\tOTHER"
                            lesions.write_text("Unique Name\tDescriptor\tWide Peak Limits\tPeak Limits\tRegion Limits\tq values\tResidual q values\tBroad or Focal\tAmplitude Threshold\t" + samples + "\t\n")
                    return subprocess.CompletedProcess(argv, 0)
                def invoke(force=False):
                    return _run_gistic(ROOT, config, base / "lpwgs", prepared, output,
                                       runner, ledger, toolchain, force=force)
                with patch.object(runner, "run", side_effect=run):
                    if mode != "header_only":
                        with self.assertRaisesRegex(OncoTracerError, "GISTIC2 did not complete"):
                            invoke()
                        self.assertIn("failed\t", (output / "gistic2_status.tsv").read_text())
                        self.assertFalse((output / "gistic2_out/.oncotracer-complete").exists())
                        self.assertNotIn("classifier-gistic", ledger.data["stages"])
                        continue
                    invoke()
                    self.assertEqual(calls.count("classifier-gistic"), 1)
                    invoke()
                    self.assertEqual(calls.count("classifier-gistic"), 1)
                    tracked = ledger.data["stages"]["classifier-gistic"]["outputs"]
                    self.assertIn(str(lesions.resolve()), {item["path"] for item in tracked})
                    lesions.rename(lesions.with_suffix(".held"))
                    invoke()
                    self.assertEqual(calls.count("classifier-gistic"), 2)
                    # A subsequent zero-exit run must not reuse an untouched prior report.
                    with patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0)):
                        with self.assertRaisesRegex(OncoTracerError, "result_not_refreshed"):
                            invoke(force=True)
                    failure = output / "gistic2_out/GISTIC_FAILED.txt"
                    previous_failure = failure.read_text()
                    invoke(force=True)
                    self.assertFalse(failure.exists())
                    self.assertEqual((output / "gistic2_out/GISTIC_PREVIOUS_FAILURE.txt").read_text(), previous_failure)

    def test_gistic_upstream_trailing_tabs_do_not_create_an_empty_sample(self) -> None:
        script = ROOT / "bin/cna_classifier_nf/bin/04_parse_gistic_results.py"
        spec = importlib.util.spec_from_file_location("gistic_parser_fixture", script)
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all_lesions.conf_90.txt"
            path.write_text(
                "Unique Name\tDescriptor\tWide Peak Limits\tPeak Limits\tRegion Limits\tq values\tResidual q values\tBroad or Focal\tAmplitude Threshold\tS1\tS2\t\n"
                "+1\tAmplification\tchr1:1-100\tchr1:1-100\tchr1:1-100\t0.01\t0.01\tfocal\t0.1\t2\t0\t\n"
            )
            matrix, long, summary = parser.parse_all_lesions(path)
            self.assertEqual(list(matrix.index), ["S1", "S2"])
            self.assertEqual(matrix.shape, (2, 1))
            self.assertEqual(list(long["sample"]), ["S1"])
            self.assertEqual(int(summary.iloc[0]["n_samples"]), 1)

    def test_optional_failed_gistic_is_visible_in_workflow_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            summary = base / "06_workflow_summary"
            summary.mkdir()
            (summary / "workflow_summary.json").write_text('{"workflow_status":"complete","completed_samples":["S1","S2"]}')
            classifier = base / "05_cna_classifier"
            (classifier / "04_gistic2").mkdir(parents=True)
            (classifier / "04_gistic2/gistic2_status.tsv").write_text("status\treason\nfailed\tmissing_result\n")
            _update_summary(base, classifier, gistic_requested=True)
            result = json.loads((summary / "workflow_summary.json").read_text())
            self.assertEqual(result["workflow_status"], "partial_failure")
            self.assertEqual(result["cna_status"], "complete")
            self.assertEqual(result["gistic_status"], "failed")
            self.assertFalse(result["cna_classifier_completed"])
            # Legacy optional single-sample cohorts may skip recurrence analysis.
            (summary / "workflow_summary.json").write_text('{"workflow_status":"complete"}')
            (classifier / "04_gistic2/gistic2_status.tsv").write_text("status\treason\nskipped\tnot_enough_samples\n")
            _update_summary(base, classifier, gistic_requested=True)
            result = json.loads((summary / "workflow_summary.json").read_text())
            self.assertEqual(result["workflow_status"], "complete")
            self.assertEqual(result["gistic_status"], "skipped")
            self.assertTrue(result["cna_classifier_completed"])

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
            knowledge_reports = analysis / "05_cna_classifier/03_report/llm_reports"
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
            self.assertIn("../cna_classifier_report.html", index)
            self.assertIn("../../06_knowledge/knowledge_llm_trials.tsv", index)
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
                    held = path.with_name(path.name + ".held")
                    path.rename(held)
                    try:
                        self.assertFalse(ledger.reusable("classifier-pdf-reports", record["signature"], report_outputs))
                    finally:
                        held.rename(path)
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
