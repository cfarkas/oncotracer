"""Exercise local HTTP authorization and real config mapping without analysis."""
import contextlib
import csv
import gzip
import http.client
import io
import json
import os
import subprocess
import signal
import sys
import time
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import build_parser, _legacy_to_modern
from oncotracer_cli.engine import parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml
from oncotracer_cli.web import WebServer, WebState

HARDWARE = {"cpu_workers_available": 8, "ram_total_bytes": 64 * 1024**3,
            "ram_available_bytes": 48 * 1024**3, "gpus": [],
            "os": "Linux", "architecture": "x86_64", "python_supported": True}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = WebState(self.root)
        self.state.hardware = HARDWARE
        self.addCleanup(patch.stopall)
        patch("oncotracer_cli.cli._load_install_config", return_value={}).start()

    def fastq(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write("@read\nACGT\n+\nIIII\n")
        return path

    def prepare(self, mode="illumina", folder="reads", **options):
        scan = self.state.scan({"mode": mode, "folder": str(self.root / folder)})
        data = {"scan_id": scan["scan_id"], "project": str(self.root / "project"), "threads": 2,
                "samples": [{"id": sample["id"], "name": sample["name"], "type": "cancer"}
                            for sample in scan["samples"]]}
        data.update(options)
        with contextlib.redirect_stdout(io.StringIO()):
            return self.state.prepare(data)

    def test_real_illumina_check_and_metadata_preserve_inputs_without_running(self):
        paths = [self.fastq(f"reads/{name}_R{mate}.fastq.gz")
                 for name in ("case", "control", "other") for mate in (1, 2)]
        before = {path: path.read_bytes() for path in paths}
        with patch("oncotracer_cli.web.subprocess.Popen", wraps=subprocess.Popen) as popen:
            prepared = self.prepare(samples=[{"id": 0, "name": "patient", "type": "cancer"},
                                            {"id": 1, "name": "healthy", "type": "normal"},
                                            {"id": 2, "name": "custom", "type": "custom",
                                             "label": "benign", "role": "tumor"}])
        self.assertTrue(prepared["valid"], prepared["check"])
        # Only a check process, never setup --run or an installation.
        self.assertEqual(popen.call_count, 1)
        self.assertIn("check", popen.call_args.args[0])
        self.assertNotIn("--run", popen.call_args.args[0])
        config = load_flat_yaml(Path(prepared["config_path"]))
        rows = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
        self.assertEqual([(r.sample, r.status) for r in rows],
                         [("patient", "tumor"), ("healthy", "normal"), ("custom", "tumor")])
        with Path(config["sample_metadata"]).open() as handle:
            metadata = list(csv.DictReader(handle))
        self.assertEqual([r["sample_type"] for r in metadata], ["cancer", "normal", "benign"])
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        self.assertFalse((self.root / "project/results").exists())
        self.assertFalse((self.root / "project/reference").exists())

    def test_ont_batches_with_control_use_correct_workflow(self):
        for barcode in ("barcode01", "barcode02", "unclassified"):
            for number in range(3):
                self.fastq(f"reads/fastq_pass/{barcode}/batch{number}.fastq.gz")
        prepared = self.prepare(mode="ont", samples=[
            {"id": 0, "name": "patient", "type": "cancer"},
            {"id": 1, "name": "control", "type": "normal"}], caller="qdnaseq")
        self.assertTrue(prepared["valid"], prepared["check"])
        config = load_flat_yaml(Path(prepared["config_path"]))
        self.assertEqual(config["ont_analysis_type"], "solid_biopsy")
        self.assertEqual(config["ont_binsize_kb"], 100)
        self.assertEqual([(r.sample, r.status) for r in parse_ont_samples(config)],
                         [("patient", "tumor"), ("control", "normal")])

    def test_nonbarcoded_ont_many_files_are_one_sample(self):
        for number in range(69):
            self.fastq(f"ligation/batch{number}.fastq.gz")
        prepared = self.prepare(mode="ont", folder="ligation")
        self.assertTrue(prepared["valid"], prepared["check"])
        config = load_flat_yaml(Path(prepared["config_path"]))
        self.assertTrue(config["ont_single_sample"])
        samples = parse_ont_samples(config)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].fastq_dir, self.root / "ligation")
        with Path(config["sample_metadata"]).open() as handle:
            metadata = list(csv.DictReader(handle))
        self.assertEqual(len(json.loads(metadata[0]["fastq_files"])), 69)

    def test_duplicate_names_or_missing_custom_role_fail_before_writes(self):
        self.fastq("reads/one.fastq.gz")
        self.fastq("reads/two.fastq.gz")
        for samples, message in [
            ([{"id": i, "name": "same", "type": "cancer"} for i in (0, 1)], "unique"),
            ([{"id": 0, "name": "one", "type": "custom", "label": "other"}], "role"),
            ([{"id": 0, "name": "one", "type": "custom", "label": "other", "role": "guess"}], "choose"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(OncoTracerError, message):
                self.prepare(samples=samples)
            self.assertFalse((self.root / "project").exists())

    def test_mixed_layout_and_ont_all_normal_rejected(self):
        self.fastq("reads/one_R1.fastq.gz")
        self.fastq("reads/one_R2.fastq.gz")
        self.fastq("reads/two.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "paired-end or single-end"):
            self.prepare()
        self.fastq("ont/barcode01/batch.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "study sample"):
            self.prepare(mode="ont", folder="ont", samples=[{"id": 0, "name": "control", "type": "normal"}])
        self.assertFalse((self.root / "project").exists())

    def test_changed_discovery_requires_another_review(self):
        self.fastq("reads/barcode01/batch1.fastq.gz")
        scan = self.state.scan({"mode": "ont", "folder": str(self.root / "reads")})
        self.fastq("reads/barcode01/batch2.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "changed since discovery"):
            self.state.prepare({"scan_id": scan["scan_id"], "project": str(self.root / "project"),
                                "threads": 2, "samples": [{"id": 0, "name": "patient", "type": "cancer"}]})

    def test_invalid_threads_existing_project_and_input_overlap_are_safe(self):
        self.fastq("reads/library.fastq.gz")
        for count in (0, 9, 1.5, True):
            with self.subTest(count=count), self.assertRaisesRegex(OncoTracerError, "threads"):
                self.prepare(threads=count)
        with self.assertRaisesRegex(OncoTracerError, "outside"):
            self.prepare(project=str(self.root / "reads/project"))
        self.prepare()
        before = (self.root / "project/config/run.yml").read_bytes()
        with self.assertRaisesRegex(OncoTracerError, "will not overwrite"):
            self.prepare()
        self.assertEqual(before, (self.root / "project/config/run.yml").read_bytes())

    def test_run_explicit_argv_idempotency_status_and_logs(self):
        self.fastq("reads/library.fastq.gz")
        prepared = self.prepare(project=str(self.root / "project with spaces ; $literal"))
        with patch("oncotracer_cli.web.subprocess.Popen") as popen, patch("oncotracer_cli.web.threading.Thread"):
            popen.return_value.pid = 123
            job = self.state.run({"project_id": prepared["id"]})
            self.assertEqual(job["status"], "running")
            self.assertEqual(self.state.run({"project_id": prepared["id"]}), job)
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertIn(str(self.root / "project with spaces ; $literal"), command)
            self.assertIn("--non-interactive", command)
            self.assertIn("--run", command)
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", popen.call_args.kwargs)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            popen.return_value.wait.return_value = 0
            self.state._wait(self.state.job)
            self.assertEqual(self.state.status()["status"], "complete")
            self.assertEqual(self.state.status()["exit_code"], 0)
            # A retry after completion must not start the same analysis twice.
            self.state.run({"project_id": prepared["id"]})
            popen.assert_called_once()

    def test_changed_config_cannot_run_from_old_review(self):
        self.fastq("reads/library.fastq.gz")
        prepared = self.prepare()
        Path(prepared["config_path"]).write_text("mode: ont\n")
        with patch("oncotracer_cli.web.subprocess.Popen") as popen:
            with self.assertRaisesRegex(OncoTracerError, "changed after review"):
                self.state.run({"project_id": prepared["id"]})
            popen.assert_not_called()

    def test_only_generated_files_are_fingerprinted(self):
        self.fastq("reads/library.fastq.gz")
        existing = self.root / "project/config/unrelated/subdirectory"
        existing.mkdir(parents=True)
        prepared = self.prepare()
        self.assertTrue(prepared["valid"], prepared["check"])
        self.assertTrue(existing.is_dir())
        internal = self.state.projects[prepared["id"]]
        self.assertEqual({Path(path).name for path in internal["fingerprint"]},
                         {"run.yml", "samplesheet.csv", "sample_metadata.csv"})
        for key in ("fingerprint", "discovered", "selected_sources", "input_snapshot"):
            self.assertNotIn(key, prepared)

    def test_fastqs_added_or_modified_after_save_cannot_run(self):
        for change in ("added", "modified", "removed"):
            with self.subTest(change=change):
                folder = f"reads-{change}/barcode01"
                path = self.fastq(folder + "/batch1.fastq.gz")
                prepared = self.prepare(mode="ont", folder=f"reads-{change}",
                                        project=str(self.root / f"project-{change}"))
                if change == "added":
                    self.fastq(folder + "/batch2.fastq.gz")
                elif change == "modified":
                    # The same path and size must still be rejected on changed mtime.
                    stat = path.stat()
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
                else:
                    path.rename(path.with_name(path.name + ".held"))
                with patch("oncotracer_cli.web.subprocess.Popen") as popen:
                    with self.assertRaisesRegex(OncoTracerError, "FASTQ inputs changed after review"):
                        self.state.run({"project_id": prepared["id"]})
                    popen.assert_not_called()
                self.assertFalse((self.root / f"project-{change}/logs").exists())

    def test_failed_launch_preserves_error_log_and_can_retry(self):
        self.fastq("reads/library.fastq.gz")
        prepared = self.prepare()
        with patch("oncotracer_cli.web.subprocess.Popen", side_effect=OSError("Cannot start process")):
            with self.assertRaisesRegex(OSError, "Cannot start process"):
                self.state.run({"project_id": prepared["id"]})
        first = self.root / "project/logs/web-analysis.log"
        self.assertIn("Cannot start process", first.read_text())
        self.assertIsNone(self.state.job)
        with patch("oncotracer_cli.web.subprocess.Popen") as popen, patch("oncotracer_cli.web.threading.Thread"):
            popen.return_value.pid = 123
            job = self.state.run({"project_id": prepared["id"]})
        self.assertEqual(job["status"], "running")
        self.assertEqual(Path(job["log_path"]).name, "web-analysis-2.log")
        self.assertIn("Cannot start process", first.read_text())

    def test_methylation_classifier_must_be_explicit(self):
        self.fastq("reads/barcode01/batch.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "classifier"):
            self.prepare(mode="ont", analysis="methylation", classifier="")
        self.assertFalse((self.root / "project").exists())

    def test_methylation_requires_resources_and_does_not_silently_run_cna(self):
        self.fastq("reads/barcode01/batch.fastq.gz")
        with self.assertRaisesRegex(OncoTracerError, "resources"):
            self.prepare(mode="ont", analysis="both", classifier="marlin", methylation_source="modbam",
                         methylation_path=str(self.root / "calls.bam"))
        self.assertFalse((self.root / "project").exists())

    def start_server(self):
        server = WebServer(0, self.state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, method, path, data=None, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        defaults = {"X-OncoTracer-Token": self.state.token, "Origin": server.origin}
        if data is not None:
            defaults["Content-Type"] = "application/json"
        defaults.update(headers)
        connection.request(method, path, json.dumps(data) if data is not None else None, headers=defaults)
        response = connection.getresponse()
        code, content, reply_headers = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return code, content, reply_headers

    def test_http_auth_origin_host_and_page_security(self):
        server = self.start_server()
        self.assertEqual(server.server_address[0], "127.0.0.1")
        code, content, headers = self.request(server, "GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Choose your sequencing platform", content)
        self.assertNotIn(self.state.token.encode(), content)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path, changes in [("/api/system", {"X-OncoTracer-Token": ""}),
                              ("/api/system", {"Origin": "https://example.com"}),
                              ("/api/system", {"Origin": "null"}),
                              ("/api/system", {"Host": "example.com"})]:
            with self.subTest(changes=changes):
                self.assertEqual(self.request(server, "GET", path, **changes)[0], 403)
        self.assertEqual(self.request(server, "POST", "/api/run", {"project_id": "guess"}, Origin="")[0], 403)
        self.assertEqual(self.request(server, "POST", "/api/run", {"project_id": "guess"})[0], 400)
        self.assertEqual(self.request(server, "GET", "/api/system")[0], 200)

    def test_browse_escaped_names_and_permission_errors(self):
        folder = self.root / '<img src=x onerror=alert(1)>'
        folder.mkdir()
        server = self.start_server()
        code, content, _headers = self.request(server, "GET", "/api/browse")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(content)["directories"][0]["name"], folder.name)
        with patch.object(self.state, "browse", side_effect=PermissionError(13, "Permission denied", "restricted folder")):
            code, content, _headers = self.request(server, "GET", "/api/browse")
        self.assertEqual(code, 400)
        self.assertIn("Choose a folder your account can access", json.loads(content)["error"])

    def test_report_detail_controls_local_and_online_stages_explicitly(self):
        self.fastq("reads/one_R1.fastq.gz")
        self.fastq("reads/one_R2.fastq.gz")
        self.fastq("reads/two_R1.fastq.gz")
        self.fastq("reads/two_R2.fastq.gz")
        for detail in ("catalog", "models", "literature"):
            with self.subTest(detail=detail):
                result = self.prepare(project=str(self.root / detail), reports=True,
                                      report_detail=detail, gistic=detail == "literature")
                config = load_flat_yaml(Path(result["config_path"]))
                self.assertTrue(config["run_cna_classifier"])
                self.assertEqual(config["knowledge_web"], detail == "literature")
                self.assertEqual(config["knowledge_literature_llm"], detail == "literature")
                self.assertFalse(config["pathology_use_biomed_models"])
                self.assertEqual(config["knowledge_catalog_llm"], detail == "models")
                self.assertEqual(config["run_gistic"], detail == "literature")
                self.assertEqual(config["gistic_required"], detail == "literature")
        with self.assertRaisesRegex(OncoTracerError, "report_detail"):
            self.prepare(reports=True, report_detail="invented")
        with self.assertRaisesRegex(OncoTracerError, "true or false"):
            self.prepare(reports=True, gistic="false")
        with self.assertRaisesRegex(OncoTracerError, "at least two"):
            self.prepare(reports=True, gistic=True, samples=[{"id": 0, "name": "one", "type": "cancer"}])

    def test_finished_run_exposes_only_its_results_index(self):
        self.fastq("reads/library.fastq.gz")
        prepared = self.prepare()
        outdir = self.root / "project/results"
        outdir.mkdir()
        (outdir / "index.html").write_text("<h1>Finished results</h1>")
        with patch("oncotracer_cli.web.subprocess.Popen") as popen, patch("oncotracer_cli.web.threading.Thread"):
            popen.return_value.pid = 123
            popen.return_value.wait.return_value = 0
            self.state.run({"project_id": prepared["id"]})
            self.assertNotIn("results_url", self.state.status())
            self.state._wait(self.state.job)
        job = self.state.status()
        self.assertEqual(job["status"], "complete")
        self.assertTrue(job["results_url"].startswith("/results/"))
        server = self.start_server()
        code, content, headers = self.request(server, "GET", job["results_url"], **{"X-OncoTracer-Token": ""})
        self.assertEqual(code, 200)
        self.assertIn(b"Finished results", content)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.state.status()["results_url"], job["results_url"])

    def test_results_capability_rejects_traversal_hidden_files_and_external_symlinks(self):
        outdir = self.root / "results"
        outdir.mkdir()
        (outdir / "index.html").write_text("results")
        (outdir / ".private").write_text("hidden")
        (outdir / "nested").mkdir()
        (outdir / "nested/index.html").write_text("nested results")
        (outdir / "no_index").mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("private")
        (outdir / "external.txt").symlink_to(outside)
        (outdir / "external_dir").symlink_to(self.root, target_is_directory=True)
        self.state.result_roots["fixture-key"] = outdir
        server = self.start_server()
        for path in ("../outside.txt", "%2e%2e/outside.txt", ".private", "external.txt",
                     "external_dir/outside.txt", "index.html%00", "/index.html", "missing.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request(server, "GET", "/results/fixture-key/" + path)[0], 404)
        self.assertEqual(self.request(server, "GET", "/results/wrong-key/index.html")[0], 404)
        self.assertEqual(self.request(server, "GET", "/results/fixture-key/")[0], 200)
        self.assertEqual(self.request(server, "GET", "/results/fixture-key/nested/")[1], b"nested results")
        self.assertEqual(self.request(server, "GET", "/results/fixture-key/no_index/")[0], 404)
        for headers in ({"Host": "example.com"}, {"Origin": "https://example.com"}):
            self.assertEqual(self.request(server, "GET", "/results/fixture-key/index.html", **headers)[0], 403)

    def test_results_pdf_ranges_head_and_downloads(self):
        outdir = self.root / "results"
        outdir.mkdir()
        payload = b"%PDF-1.4\nreport content\n%%EOF"
        (outdir / "report.pdf").write_bytes(payload)
        (outdir / "table.tsv").write_text("sample\tvalue\na\t1\n")
        self.state.result_roots["fixture-key"] = outdir
        server = self.start_server()
        path = "/results/fixture-key/report.pdf"
        code, content, headers = self.request(server, "GET", path, Range="bytes=0-7")
        self.assertEqual((code, content), (206, payload[:8]))
        self.assertEqual(headers["Content-Range"], f"bytes 0-7/{len(payload)}")
        self.assertEqual(headers["Content-Type"], "application/pdf")
        code, content, _ = self.request(server, "GET", path, Range="bytes=-5")
        self.assertEqual((code, content), (206, payload[-5:]))
        code, content, headers = self.request(server, "HEAD", path)
        self.assertEqual((code, content), (200, b""))
        self.assertEqual(int(headers["Content-Length"]), len(payload))
        for value in ("bytes=999-", "bytes=5-2", "bytes=-0", "bytes=0-1,3-5", "invalid"):
            with self.subTest(value=value):
                code, content, headers = self.request(server, "GET", path, Range=value)
                self.assertEqual((code, content), (416, b""))
                self.assertEqual(headers["Content-Range"], f"bytes */{len(payload)}")
        code, content, headers = self.request(server, "GET", "/results/fixture-key/table.tsv")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertIn(b"sample", content)

    def test_setup_defaults_to_browser_and_preserves_prefilled_settings(self):
        from oncotracer_cli.cli import main
        with patch("oncotracer_cli.web.command_web", return_value=0) as launch:
            code = main(["setup", "--project", str(self.root / "project"),
                         "--mode", "illumina", "--input-folder", str(self.root / "reads"),
                         "--threads", "3", "--hg38_build", "/prepared", "--port", "8899"])
        self.assertEqual(code, 0)
        args = launch.call_args.args[0]
        self.assertEqual(args.port, 8899)
        state = WebState(self.root, args)
        state.hardware = HARDWARE
        defaults = state.system()["defaults"]
        self.assertEqual(defaults["mode"], "illumina")
        self.assertEqual(defaults["threads"], 3)
        self.assertEqual(defaults["project"], str(self.root / "project"))
        self.assertEqual(defaults["input_folder"], str(self.root / "reads"))
        self.assertEqual((defaults["reference"], defaults["reference_path"]), ("reuse", "/prepared"))
        self.assertFalse((self.root / "project").exists())

    def test_standard_sample_types_are_case_insensitive_and_keep_matching_roles(self):
        self.fastq("reads/one.fastq.gz")
        for i, label in enumerate(("Normal", "NORMAL", "nORMAl", "Cancer", "CANCER", "cANCER")):
            for custom in (False, True):
                with self.subTest(label=label, custom=custom):
                    selection = {"id": 0, "name": "renamed", "type": "custom" if custom else label,
                                 "label": label, "role": "tumor" if label.lower() == "normal" else "normal"}
                    prepared = self.prepare(samples=[selection], project=str(self.root / f"p{i}-{custom}"))
                    self.assertTrue(prepared["valid"], prepared["check"])
                    config = load_flat_yaml(Path(prepared["config_path"]))
                    with Path(config["sample_metadata"]).open() as handle:
                        row = next(csv.DictReader(handle))
                    self.assertEqual(row["sample_type"], label.lower())
                    self.assertEqual(row["analysis_role"], "normal" if label.lower() == "normal" else "tumor")

    def start_dummy_analysis(self, *, stubborn=False):
        self.fastq("reads/library.fastq.gz")
        prepared = self.prepare()
        if stubborn:
            code = "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('READY',flush=True); time.sleep(60)"
        else:
            code = """import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
print('READY '+str(child.pid),flush=True)
try:
    child.wait()
except KeyboardInterrupt:
    child.wait()
    raise SystemExit(130)
"""
        with patch("oncotracer_cli.web._launcher", return_value=[sys.executable, "-u", "-c", code]):
            self.state.run({"project_id": prepared["id"]})
        process = self.state.job["process"]
        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while "READY" not in self.state.status()["log"] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIn("READY", self.state.status()["log"])
        return prepared

    def wait_stopped(self):
        deadline = time.monotonic() + 12
        while self.state.status()["status"] == "stopping" and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(self.state.status()["status"], "stopped", self.state.status())

    def test_stop_real_process_group_then_remove_requires_exact_confirmation(self):
        prepared = self.start_dummy_analysis()
        original = (self.root / "reads/library.fastq.gz").read_bytes()
        cache = self.root / "shared-download.part"
        cache.write_text("keep verified cache")
        (self.root / "project/input-link").symlink_to(self.root / "reads", target_is_directory=True)
        server = self.start_server()
        for endpoint in ("/api/stop", "/api/remove-project"):
            self.assertEqual(self.request(server, "POST", endpoint, {"project_id": prepared["id"]}, **{"X-OncoTracer-Token": ""})[0], 403)
        self.assertEqual(self.request(server, "POST", "/api/remove-project", {"project_id": prepared["id"], "confirm_remove": True, "confirm_path": prepared["project"]})[0], 400)
        self.assertEqual(self.request(server, "POST", "/api/stop", {"project_id": prepared["id"]})[0], 200)
        self.wait_stopped()
        self.assertFalse(self.state._group_running(self.state.job["pid"]))
        self.assertTrue((self.root / "project/config/run.yml").exists())
        self.assertTrue(self.state.status()["can_remove"])
        for changes in ({}, {"confirm_remove": True}, {"confirm_remove": True, "confirm_path": str(self.root)}):
            self.assertEqual(self.request(server, "POST", "/api/remove-project", {"project_id": prepared["id"], **changes})[0], 400)
        self.assertEqual(self.state.stop({"project_id": prepared["id"]})["status"], "stopped")
        code, body, _ = self.request(server, "POST", "/api/remove-project", {"project_id": prepared["id"], "confirm_remove": True, "confirm_path": prepared["project"]})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["status"], "removed")
        self.assertFalse((self.root / "project").exists())
        self.assertEqual((self.root / "reads/library.fastq.gz").read_bytes(), original)
        self.assertEqual(cache.read_text(), "keep verified cache")

    def test_stop_escalates_for_an_unresponsive_process(self):
        prepared = self.start_dummy_analysis(stubborn=True)
        self.state.stop({"project_id": prepared["id"]})
        self.wait_stopped()
        self.assertEqual(self.state.job["exit_code"], -signal.SIGKILL)
        self.assertTrue((self.root / "project").is_dir())

    def test_remove_rejects_preexisting_or_replaced_project_folders(self):
        for existing in (True, False):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                previous_root, previous_state = self.root, self.state
                self.root = Path(directory); self.state = WebState(self.root); self.state.hardware = HARDWARE
                try:
                    if existing:
                        (self.root / "project").mkdir()
                        (self.root / "project/unrelated.txt").write_text("keep")
                    self.fastq("reads/library.fastq.gz")
                    prepared = self.prepare()
                    self.state.job = {"project_id": prepared["id"], "status": "stopped", "pid": 99999999}
                    if not existing:
                        (self.root / "project").rename(self.root / "original")
                        (self.root / "project").symlink_to(self.root / "reads", target_is_directory=True)
                    with self.assertRaisesRegex(OncoTracerError, "existed|redirected"):
                        self.state.remove_project({"project_id": prepared["id"], "confirm_remove": True, "confirm_path": prepared["project"]})
                    self.assertTrue((self.root / "reads/library.fastq.gz").is_file())
                finally:
                    self.root, self.state = previous_root, previous_state

    def test_terminal_prints_complete_local_session_url(self):
        from oncotracer_cli.web import command_web
        args = build_parser().parse_args(["web", "--no-browser"])
        with patch("oncotracer_cli.web.WebServer") as server, contextlib.redirect_stdout(io.StringIO()) as output:
            server.return_value.origin = "http://127.0.0.1:8888"
            command_web(args)
        url = next(line for line in output.getvalue().splitlines() if line.startswith("http://127.0.0.1:"))
        self.assertEqual(url, server.return_value.origin + "/#" + server.call_args.args[1].token)
        self.assertGreater(len(url.split("#")[1]), 30)

    def test_public_command_registration(self):
        args = build_parser().parse_args(["web"])
        self.assertEqual(args.port, 8888)
        self.assertEqual(args.func.__name__, "command_web")
        self.assertEqual(_legacy_to_modern(["web", "--port", "8889"]), ["web", "--port", "8889"])


if __name__ == "__main__":
    unittest.main()
