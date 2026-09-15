"""Loopback-only browser setup, using the public configuration and run paths."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .discovery import discover_fastqs
from .engine import QDNASEQ_HG38_SOURCE_SHA256, _safe_sample
from .runtime import OncoTracerError, load_flat_yaml
from .system_check import inspect_hardware, resource_report
from .web_ui import PAGE


def _launcher() -> list[str]:
    # Preserve the exact release archive when launched from a standalone build.
    import oncotracer_cli
    archive = getattr(oncotracer_cli.__loader__, "archive", None)
    if archive:
        return [sys.executable, str(archive)]
    entry = Path(__file__).resolve().parents[1] / "oncotracer"
    if entry.is_file():
        return [sys.executable, str(entry)]
    return [sys.executable, "-c", "from oncotracer_cli.cli import main; raise SystemExit(main())"]


def _text(data, key, *, default=None):
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise OncoTracerError(f"Provide {key.replace('_', ' ')}.")
    return value.strip()


def _choice(data, key, choices, default=None):
    value = _text(data, key, default=default)
    if value not in choices:
        raise OncoTracerError(f"{key}: choose {', '.join(map(str, choices))}.")
    return value


def _fingerprint(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _input_snapshot(samples):
    result = {}
    for sample in samples:
        for path in sample.files:
            stat = path.stat()
            result[str(path)] = (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)
    return result


class WebState:
    """Server-owned discoveries, prepared configs and one explicitly started job."""

    def __init__(self, start_dir: Path):
        self.start_dir = start_dir.expanduser().resolve()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.scans = {}
        self.projects = {}
        self.job = None
        self.hardware = None

    def system(self):
        if self.hardware is None:
            self.hardware = inspect_hardware(include_gpus=True)
        return {"hardware": self.hardware,
                "suggested_threads": resource_report(hardware=self.hardware, path=self.start_dir)["suggested_threads"],
                "start_dir": str(self.start_dir), "qdnaseq_binsizes": sorted(QDNASEQ_HG38_SOURCE_SHA256)}

    def browse(self, value):
        path = Path(value or self.start_dir).expanduser().resolve()
        if not path.is_dir():
            raise OncoTracerError(f"Folder does not exist or is not accessible: {path}")
        directories, files, fastqs, truncated = [], [], 0, False
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir():
                    if len(directories) < 1000:
                        directories.append({"name": entry.name, "path": str(path / entry.name)})
                    else:
                        truncated = True
                elif entry.name.lower().endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")):
                    fastqs += 1
                elif entry.name.lower().endswith((".yaml", ".yml", ".bam")) and len(files) < 1000:
                    files.append({"name": entry.name, "path": str(path / entry.name)})
        directories.sort(key=lambda item: item["name"].casefold())
        return {"path": str(path), "parent": str(path.parent), "directories": directories,
                "files": sorted(files, key=lambda item: item["name"].casefold()),
                "fastq_files": fastqs, "truncated": truncated}

    def scan(self, data):
        mode = _choice(data, "mode", ("illumina", "ont"))
        discovered = discover_fastqs(_text(data, "folder"), mode)
        scan_id = secrets.token_urlsafe(18)
        with self.lock:
            if len(self.scans) >= 32:
                self.scans.pop(next(iter(self.scans)))
            self.scans[scan_id] = discovered
        return {"scan_id": scan_id, "mode": mode, "root": str(discovered.root),
                "warnings": list(discovered.warnings), "samples": [
                    {"id": index, "name": sample.sample, "barcode": sample.barcode,
                     "file_count": len(sample.files), "files": [str(p) for p in sample.files],
                     "layout": ("ONT batches" if mode == "ont" else
                                "paired-end" if sample.fastq_2 else "single-end")}
                    for index, sample in enumerate(discovered.samples)]}

    def prepare(self, data):
        from .cli import build_parser
        from .setup import _command_setup

        # Serialize creation and prevent a second request racing exclusive writes.
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise OncoTracerError("An analysis is running. Wait for it to finish before preparing another project.")
            discovered = self.scans.get(_text(data, "scan_id"))
            if discovered is None:
                raise OncoTracerError("This folder scan expired. Scan the FASTQ folder again.")
            selections = data.get("samples")
            if not isinstance(selections, list) or not selections:
                raise OncoTracerError("Select at least one sample.")
            entries, names, indices = [], set(), set()
            for selected in selections:
                if not isinstance(selected, dict):
                    raise OncoTracerError("Invalid sample selection.")
                index = selected.get("id")
                if type(index) is not int or not 0 <= index < len(discovered.samples) or index in indices:
                    raise OncoTracerError("Invalid or repeated sample selection. Scan the folder again.")
                indices.add(index)
                name = _safe_sample(_text(selected, "name"))
                if name in names:
                    raise OncoTracerError(f"Sample names must be unique: {name}")
                names.add(name)
                kind = _choice(selected, "type", ("cancer", "normal", "custom"))
                label = _text(selected, "label") if kind == "custom" else kind
                if len(label) > 160 or any(ord(char) < 32 for char in label):
                    raise OncoTracerError("Sample labels must be at most 160 characters without control characters.")
                role = (_choice(selected, "role", ("tumor", "normal")) if kind == "custom"
                        else "normal" if kind == "normal" else "tumor")
                entries.append({"source": discovered.samples[index], "sample": name,
                                "sample_type": label, "analysis_role": role})
            if discovered.mode == "illumina" and len({row["source"].fastq_2 is not None for row in entries}) > 1:
                raise OncoTracerError("Select either paired-end or single-end samples for this project; use separate projects for different layouts.")
            # Recheck the discovery before saving, so changed folders need a fresh review.
            refreshed = discover_fastqs(discovered.root, discovered.mode)
            current = {sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1: sample for sample in refreshed.samples}
            for row in entries:
                sample = row["source"]
                key = sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1
                if current.get(key) != sample:
                    raise OncoTracerError("FASTQ files changed since discovery. Scan the folder and review samples again.")
            project = Path(_text(data, "project")).expanduser().resolve()
            if project == discovered.root or project in discovered.root.parents or discovered.root in project.parents:
                raise OncoTracerError("Choose a project directory outside the input FASTQ folder.")
            analysis = _choice(data, "analysis", ("cna",) if discovered.mode == "illumina" else ("cna", "methylation", "both"), "cna")
            backend = _choice(data, "backend", ("conda", "docker", "singularity", "poetry", "host"), "conda")
            threads = data.get("threads")
            maximum = self.system()["hardware"]["cpu_workers_available"]
            if type(threads) is not int or not 1 <= threads <= maximum:
                raise OncoTracerError(f"CPU threads must be a whole number from 1 to {maximum}.")
            args = build_parser().parse_args(["setup", "--non-interactive", "--mode", discovered.mode,
                                             "--project", str(project), "--analysis", analysis,
                                             "--backend", backend, "--threads", str(threads)])
            values = {}
            if discovered.mode == "illumina":
                args._wizard_rows = [[row["sample"], str(row["source"].fastq_1),
                                      str(row["source"].fastq_2 or ""), row["analysis_role"]] for row in entries]
                caller = "qdnaseq"
            else:
                study = [row for row in entries if row["analysis_role"] == "tumor"]
                controls = [row for row in entries if row["analysis_role"] == "normal"]
                if not study:
                    raise OncoTracerError("An ONT project needs at least one study sample; all-normal ONT projects are not supported.")
                args.reads_folder = str(discovered.root)
                args.barcodes = ",".join(row["source"].barcode or "." for row in study)
                args.sample_names = ",".join(row["sample"] for row in study)
                if discovered.single_sample:
                    values["ont_single_sample"] = True
                if controls:
                    values.update(ont_normal_folder=str(discovered.root),
                                  ont_normal_barcodes=",".join(row["source"].barcode for row in controls),
                                  ont_normal_sample_names=",".join(row["sample"] for row in controls))
                caller = _choice(data, "caller", ("qdnaseq",) if controls else ("ichorcna", "qdnaseq"),
                                 "qdnaseq" if controls else "ichorcna")
                values["ont_caller"] = caller
                if caller == "qdnaseq":
                    values["ont_analysis_type"] = "solid_biopsy"
            binsize = data.get("binsize", 500 if caller == "ichorcna" else 100)
            allowed = (500,) if caller == "ichorcna" else QDNASEQ_HG38_SOURCE_SHA256
            if type(binsize) is not int or binsize not in allowed:
                raise OncoTracerError(f"CNA bin size must be one of: {', '.join(map(str, allowed))} kb.")
            values[discovered.mode + "_binsize_kb"] = binsize
            reports = data.get("reports", False)
            if type(reports) is not bool:
                raise OncoTracerError("Interpretation report selection must be true or false.")
            values["run_cna_classifier"] = reports and analysis != "methylation"
            if values["run_cna_classifier"]:
                values.update(cna_classifier_sample_set=_text(data, "report_context", default="broad_cancer"),
                              cna_classifier_samples=",".join(row["sample"] for row in entries),
                              knowledge_web=False, knowledge_literature_llm=False,
                              knowledge_deep_literature=False, knowledge_deep_enable_llm_ranker=False,
                              pathology_use_biomed_models=False, run_gistic=False)
            reference = _choice(data, "reference", ("download", "reuse", "build"), "download")
            if reference == "reuse":
                args.hg38_build = _text(data, "reference_path")
            args.build_reference = reference == "build"
            if analysis != "cna":
                args.classifier = _choice(data, "classifier", ("marlin", "sturgeon"))
                source = _choice(data, "methylation_source", ("modbam", "pod5"))
                setattr(args, "modbam" if source == "modbam" else "pod5_dir", _text(data, "methylation_path"))
                args.resources = _text(data, "resources")
                args.gpu = _choice(data, "device", ("cpu", "gpu"), "cpu") == "gpu"
                args.accept_sturgeon_license = data.get("accept_sturgeon_license") is True
            args._wizard_values = values
            args._wizard_metadata = [
                {key: row[key] for key in ("sample", "sample_type", "analysis_role")}
                | {"fastq_files": json.dumps([str(p) for p in row["source"].files])} for row in entries]
            selected_sources = tuple(row["source"] for row in entries)
            inputs = _input_snapshot(selected_sources)
            _command_setup(args)
            config_path = project / "config/run.yml"
            command = _launcher() + ["check", "--json", "--config", str(config_path)]
            try:
                checked = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
                try:
                    report = json.loads(checked.stdout)
                except ValueError:
                    report = {"errors": [checked.stderr.strip() or checked.stdout.strip() or "Configuration check did not return a report."]}
                valid = checked.returncode == 0 and not report.get("errors")
            except subprocess.TimeoutExpired:
                valid, report = False, {"errors": ["Configuration check timed out. Settings were saved; use oncotracer check before running."]}
            project_id = secrets.token_urlsafe(18)
            paths = [config_path, project / "config/sample_metadata.csv"]
            if discovered.mode == "illumina":
                paths.append(project / "config/samplesheet.csv")
            prepared = {"id": project_id, "project": str(project), "config_path": str(config_path),
                        "config": config_path.read_text(), "backend": backend, "valid": valid,
                        "check": report, "fingerprint": _fingerprint(paths),
                        "discovered": discovered, "selected_sources": selected_sources,
                        "input_snapshot": inputs}
            self.projects[project_id] = prepared
            return {key: value for key, value in prepared.items()
                    if key not in {"fingerprint", "discovered", "selected_sources", "input_snapshot"}}

    def run(self, data):
        with self.lock:
            prepared = self.projects.get(_text(data, "project_id"))
            if not prepared or not prepared["valid"]:
                raise OncoTracerError("Save and validate a project before running it.")
            if self.job and self.job["project_id"] == prepared["id"]:
                return self.status()
            if self.job and self.job["status"] == "running":
                raise OncoTracerError("An analysis is already running in this browser session.")
            if _fingerprint([Path(path) for path in prepared["fingerprint"]]) != prepared["fingerprint"]:
                raise OncoTracerError("Saved configuration changed after review. Check and run it with the CLI, or prepare a new project.")
            discovered = prepared["discovered"]
            try:
                refreshed = discover_fastqs(discovered.root, discovered.mode)
                current = {sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1: sample
                           for sample in refreshed.samples}
                for sample in prepared["selected_sources"]:
                    key = sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1
                    if current.get(key) != sample:
                        raise OncoTracerError("The selected FASTQ listing changed.")
                if _input_snapshot(prepared["selected_sources"]) != prepared["input_snapshot"]:
                    raise OncoTracerError("A selected FASTQ file changed.")
            except (OncoTracerError, OSError) as error:
                raise OncoTracerError("FASTQ inputs changed after review. Rescan and review the samples in a new project before running.") from error
            project = Path(prepared["project"])
            logs = project / "logs"
            logs.mkdir(exist_ok=True)
            log_path = logs / "web-analysis.log"
            attempt = 1
            while log_path.exists() or log_path.is_symlink():
                attempt += 1
                log_path = logs / f"web-analysis-{attempt}.log"
            command = _launcher() + ["setup", "--project", str(project), "--run", "--non-interactive",
                                     "--backend", prepared["backend"]]
            with log_path.open("xb") as handle:
                try:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle,
                                               stderr=subprocess.STDOUT, start_new_session=True,
                                               env={**os.environ, "PYTHONUNBUFFERED": "1"})
                except OSError as error:
                    handle.write(f"Analysis could not start: {error}\n".encode())
                    raise
            self.job = {"project_id": prepared["id"], "status": "running", "exit_code": None,
                        "log_path": str(log_path), "pid": process.pid, "process": process}
            threading.Thread(target=self._wait, args=(self.job,), daemon=True).start()
            return self.status()

    def _wait(self, job):
        result = job["process"].wait()
        with self.lock:
            job["exit_code"] = result
            job["status"] = "complete" if result == 0 else "failed"

    def status(self):
        with self.lock:
            if not self.job:
                return {"status": "idle", "log": ""}
            result = {key: value for key, value in self.job.items() if key != "process"}
            with Path(result["log_path"]).open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 262144))
                result["log"] = handle.read().decode("utf-8", errors="replace")
                result["log_truncated"] = size > 262144
            return result


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, state: WebState):
        self.state = state
        super().__init__(("127.0.0.1", port), WebHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer

    def log_message(self, *_args):
        # URLs may contain local paths. Do not copy them to console logs.
        pass

    def _reply(self, code, value, *, html=False):
        raw = value.encode() if html else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'none'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self._reply(403, {"error": "Use the printed 127.0.0.1 address."})
            return False
        origin = self.headers.get("Origin")
        if (origin is not None and origin != self.server.origin) or (self.command == "POST" and origin != self.server.origin):
            self._reply(403, {"error": "This request must come from the local OncoTracer page."})
            return False
        if not hmac.compare_digest(self.headers.get("X-OncoTracer-Token", "").encode(), self.server.state.token.encode()):
            self._reply(403, {"error": "Session expired or missing. Open the complete URL printed in your terminal."})
            return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
                self._reply(403, {"error": "Use the printed loopback address."})
            else:
                self._reply(200, PAGE, html=True)
            return
        if not self._authorized():
            return
        try:
            if url.path == "/api/system":
                value = self.server.state.system()
            elif url.path == "/api/browse":
                value = self.server.state.browse(parse_qs(url.query).get("path", [None])[0])
            elif url.path == "/api/status":
                value = self.server.state.status()
            else:
                self._reply(404, {"error": "Unknown endpoint."})
                return
            self._reply(200, value)
        except (OncoTracerError, OSError, ValueError) as error:
            self._error(error)

    def do_POST(self):
        if not self._authorized():
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise OncoTracerError("Send a JSON request.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1048576:
                raise OncoTracerError("Request is empty or exceeds 1 MiB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise OncoTracerError("Expected a JSON object.")
            methods = {"/api/scan": self.server.state.scan, "/api/prepare": self.server.state.prepare,
                       "/api/run": self.server.state.run}
            method = methods.get(urlsplit(self.path).path)
            if method is None:
                self._reply(404, {"error": "Unknown endpoint."})
                return
            self._reply(200, method(data))
        except (OncoTracerError, OSError, ValueError) as error:
            self._error(error)

    def _error(self, error):
        if isinstance(error, PermissionError):
            message = f"Permission denied: {error.filename or 'this folder'}. Choose a folder your account can access."
        else:
            message = str(error)
        self._reply(400, {"error": message})


def command_web(args):
    if not 1 <= args.port <= 65535:
        raise OncoTracerError("--port must be from 1 to 65535.")
    state = WebState(Path(args.start_dir))
    server = WebServer(args.port, state)
    print(f"Open OncoTracer in your browser:\n{server.origin}/#{state.token}", flush=True)
    print("Folders are on this computer. Keep this terminal open; Ctrl+C closes the setup page.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        if state.job and state.job["status"] == "running":
            print(f"Analysis continues (PID {state.job['pid']}). Log: {state.job['log_path']}", flush=True)
    finally:
        server.server_close()
    return 0


def add_web_command(subparsers):
    parser = subparsers.add_parser("web", help="Open the local browser setup and analysis dashboard")
    parser.add_argument("--port", type=int, default=8888, help="loopback HTTP port (default: 8888)")
    parser.add_argument("--start-dir", default=str(Path.cwd()), help="starting folder in the local file navigator")
    parser.set_defaults(func=command_web)
