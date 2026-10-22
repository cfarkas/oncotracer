"""Loopback-only browser setup, using the public configuration and run paths."""
from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import signal
import time
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .discovery import discover_fastqs, discover_ont_inputs
from .fastq_inputs import encode_fastq_field
from .fontconfig_safety import prepare_project_runtime_removal
from .native_picker import choose_path, has_sequencing_files, KINDS, NO_SEQUENCING
from .engine import QDNASEQ_HG38_SOURCE_SHA256, _safe_sample
from .runtime import OncoTracerError, load_flat_yaml
from .system_check import inspect_hardware, resource_report
from .variant_resources import discover_variant_resources
from .variant_installer import VariantInstaller, tools_roots
from .web_ui import PAGE
from .web_progress import progress_for_job
from .source_runtime import python_module


def _launcher() -> list[str]:
    # Preserve the exact release archive when launched from a standalone build.
    import oncotracer_cli
    archive = getattr(oncotracer_cli.__loader__, "archive", None)
    if archive:
        return [sys.executable, str(archive)]
    entry = Path(__file__).resolve().parents[1] / "oncotracer"
    if entry.is_file():
        return [sys.executable, str(entry)]
    return python_module('oncotracer_cli.cli')


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


def _existing_setup_project(project: Path) -> bool:
    """Recognize an empty destination or a dedicated saved OncoTracer project."""
    try:
        if project.is_symlink() or not project.is_dir() or project.is_mount() or project == Path.home():
            return False
        contents = {path.name for path in project.iterdir()}
        if not contents:
            return True
        if contents - {"config", "logs", "reference", "results"}:
            return False
        config_path = project / "config/run.yml"
        if config_path.parent.is_symlink() or config_path.is_symlink():
            return False
        config = load_flat_yaml(config_path)
        outdir = Path(str(config.get("outdir", ""))).expanduser()
        if not outdir.is_absolute():
            outdir = config_path.parent / outdir
        return config.get("mode") in {"illumina", "ont"} and outdir.resolve() == project / "results"
    except (OSError, ValueError, OncoTracerError):
        return False


def _project_removal_error(prepared):
    if not prepared.get("project_removable", prepared.get("project_created", False)):
        return "This existing folder is not a dedicated OncoTracer project; automatic removal is unavailable."
    path = Path(prepared["project"])
    try:
        if path.is_symlink() or path.resolve() != path:
            return "The project folder was replaced or redirected; removal is blocked."
        current = path.stat()
        if (current.st_dev, current.st_ino) != prepared["project_identity"]:
            return "The project folder was replaced; removal is blocked."
        if path.is_mount() or path == Path.home():
            return "A home folder or filesystem root cannot be removed here."
        for source in (*prepared["input_snapshot"], *prepared.get("protected_paths", ())):
            if Path(source).expanduser().resolve().is_relative_to(path):
                return "This project contains an input or shared resource; keep the folder to preserve it."
    except OSError:
        return "The project folder or its inputs could not be checked; removal is blocked."
    return None


class WebState:
    """Server-owned discoveries, prepared configs and one explicitly started job."""

    def __init__(self, start_dir: Path, setup_args=None):
        self.start_dir = start_dir.expanduser().resolve()
        self.setup_args = setup_args
        self.token = secrets.token_urlsafe(32)
        self.suggested_project_name = "oncotracer-analysis-" + secrets.token_hex(4)
        self.lock = threading.RLock()
        self.picker_lock = threading.Lock()
        self.scans = {}
        self.variant_loads = {}
        self.projects = {}
        self.result_roots = {}
        self.job = None
        self.hardware = None
        self.cpg_context = None
        self.cpg_scan = None
        if setup_args is not None and getattr(setup_args, "cpg_from", None):
            from .cpg_setup import prepare_cpg_setup
            self.cpg_context = prepare_cpg_setup(setup_args)
            discovered = self.cpg_context.discovery
            scan_id = secrets.token_urlsafe(18)
            self.scans[scan_id] = discovered
            self.cpg_scan = {"scan_id": scan_id, "mode": "ont", "root": str(discovered.root),
                             "warnings": list(self.cpg_context.warnings), "samples": [
                                 {"id": index, "name": sample.sample, "barcode": sample.barcode,
                                  "file_count": len(sample.files), "files": [str(path) for path in sample.files],
                                  "layout": "ONT batches"} for index, sample in enumerate(discovered.samples)]}
        self.variant_installer = VariantInstaller(self.start_dir)

    def system(self):
        if self.hardware is None:
            self.hardware = inspect_hardware(include_gpus=True)
        defaults = {}
        if self.setup_args:
            args = self.setup_args
            from .setup import VARIANT_FIELDS
            defaults = {key: getattr(args, key, None) for key in
                        ("mode", "analysis", "threads", "backend", "classifier", "gpu", "accept_sturgeon_license", "variants", "variant_config", "image", *VARIANT_FIELDS)}
            for key, value in {"input_folder": getattr(args, 'input_folder', None) or getattr(args, 'reads_folder', None),
                               "project": getattr(args, "project", None), "resources": getattr(args, "resources", None),
                               "methylation_path": getattr(args, "modbam", None) or getattr(args, "pod5_dir", None)}.items():
                if value:
                    defaults[key] = str(Path(value).expanduser().resolve())
            defaults["methylation_source"] = "pod5" if getattr(args, "pod5_dir", None) else "modbam"
            reference = getattr(args, "hg38_build", None) or getattr(args, "reference_root", None)
            defaults["reference"] = "build" if getattr(args, "build_reference", False) else "reuse" if reference else "download"
            defaults["reference_path"] = str(Path(reference).expanduser().resolve()) if reference else ""
        from .variant_runtime import apply_variant_runtime
        try:
            defaults, selection = apply_variant_runtime(defaults, mode=str(defaults.get("mode") or ""),
                                                        backend=defaults.get("backend"))
            if selection.managed:
                defaults["managed_variant_prefix"] = str(selection.prefix)
                defaults["variant_runtime_note"] = "Using the validated OncoTracer variant environment: " + str(selection.prefix)
                if selection.diagnostics:
                    defaults["variant_runtime_note"] = " ".join(selection.diagnostics)
        except OncoTracerError as error:
            defaults.pop("variant_tool_prefix", None)
            defaults["variant_runtime_note"] = str(error)
        places = [("Home", Path.home()), ("This computer", Path("/")),
                  ("Starting folder", self.start_dir), ("Mounted drives", Path("/media")),
                  ("Other mounts", Path("/mnt"))]
        locations = [{"name": name, "path": str(path)} for name, path in places if path.is_dir()]
        from .cli import DEFAULT_IMAGE
        return {"hardware": self.hardware, "defaults": defaults, "locations": locations,
                "default_docker_image": DEFAULT_IMAGE,
                "resume_project": defaults.get("project") if defaults.get("project") and
                    (Path(defaults["project"]) / "config/run.yml").is_file() else None,
                "cpg": copy.deepcopy(self.cpg_context.public(self.cpg_scan)) if self.cpg_context else None,
                "suggested_project_name": self.suggested_project_name,
                "suggested_threads": resource_report(hardware=self.hardware, path=self.start_dir)["suggested_threads"],
                "start_dir": str(self.start_dir), "qdnaseq_binsizes": sorted(QDNASEQ_HG38_SOURCE_SHA256)}

    def pick_path(self, data):
        kind = _choice(data, "kind", KINDS, "folder")
        start = _text(data, "path", default=str(self.start_dir))
        sequencing = data.get("sequencing", False)
        if type(sequencing) is not bool:
            raise OncoTracerError("sequencing must be true or false.")
        if not self.picker_lock.acquire(blocking=False):
            raise OncoTracerError("A system file chooser is already open. Finish or cancel that selection first.")
        try:
            selected = choose_path(Path(start), kind)
            if selected is None:
                return {"cancelled": True, "path": None}
            warning = NO_SEQUENCING if sequencing and not has_sequencing_files(selected) else ""
            return {"cancelled": False, "path": str(selected), "warning": warning}
        finally:
            self.picker_lock.release()

    def browse(self, value, kind="folder"):
        path = Path(value or self.start_dir).expanduser().resolve()
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            raise OncoTracerError(f"Folder does not exist or is not accessible: {path}")
        directories, files, fastq_entries, fastqs, pod5s, bams, truncated = [], [], [], 0, 0, 0, False
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir():
                    if len(directories) < 1000:
                        directories.append({"name": entry.name, "path": str(path / entry.name)})
                    else:
                        truncated = True
                elif entry.name.lower().endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")):
                    fastqs += 1
                    if len(fastq_entries) < 1000:
                        fastq_entries.append({"name": entry.name, "path": str(path / entry.name)})
                    else:
                        truncated = True
                elif entry.is_file():
                    pod5s += entry.name.lower().endswith(".pod5")
                    bams += entry.name.lower().endswith(".bam")
                    if kind == "asset" or entry.name.lower().endswith((".yaml", ".yml", ".bam", ".pod5")):
                        if len(files) < 1000:
                            files.append({"name": entry.name, "path": str(path / entry.name)})
                        else:
                            truncated = True
        directories.sort(key=lambda item: item["name"].casefold())
        return {"path": str(path), "parent": str(path.parent), "directories": directories,
                "files": sorted(files, key=lambda item: item["name"].casefold()),
                "fastq_entries": sorted(fastq_entries, key=lambda item: item["name"].casefold()),
                "fastq_files": fastqs, "pod5_files": pod5s, "bam_files": bams, "truncated": truncated}

    def ont_inputs(self, data):
        """Suggest bounded matching folders without reading or modifying data."""
        return discover_ont_inputs(_text(data, "folder"))

    def scan(self, data):
        mode = _choice(data, "mode", ("illumina", "ont"))
        discovered = discover_fastqs(_text(data, "folder"), mode, named_ont=True if getattr(self.setup_args, "ont_named_samples", False) else None)
        scan_id = secrets.token_urlsafe(18)
        with self.lock:
            if len(self.scans) >= 32:
                self.scans.pop(next(iter(self.scans)))
            self.scans[scan_id] = discovered
        result = {"scan_id": scan_id, "mode": mode, "root": str(discovered.root),
                "warnings": list(discovered.warnings), "samples": [
                    {"id": index, "name": sample.sample, "barcode": sample.barcode,
                     "file_count": len(sample.files), "files": [str(p) for p in sample.files],
                     "layout": ("named ONT library" if discovered.named_samples else "ONT batches" if mode == "ont" else
                                "paired-end" if sample.fastq_2 else "single-end")}
                    for index, sample in enumerate(discovered.samples)]}
        if mode == "ont":
            result["ont_inputs"] = discover_ont_inputs(_text(data, "folder"), fastq_root=discovered.root, named_ont=discovered.named_samples)
        return result

    def prepare(self, data):
        self.variant_installer.require_idle()
        from .cli import build_parser
        from .setup import _command_setup

        # Serialize saves so concurrent requests cannot mix configuration files.
        with self.lock:
            if self.job and self.job["status"] in {"running", "stopping"}:
                raise OncoTracerError("An analysis is running. Wait for it to finish before preparing another project.")
            discovered = self.scans.get(_text(data, "scan_id"))
            if discovered is None:
                raise OncoTracerError("This folder scan expired. Scan the FASTQ folder again.")
            if self.cpg_context:
                self.cpg_context.validate_unchanged()
                if (data.get("scan_id") != self.cpg_scan["scan_id"] or data.get("analysis") != "methylation"
                        or data.get("variants", False) is not False or "methylation_assignments" not in data):
                    raise OncoTracerError("CpG continuation uses the saved ONT samples, methylation-only analysis and explicit classifier groups; variants stay off.")
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
                kind = _text(selected, "type").casefold()
                if kind == "control":
                    kind = "normal"
                if kind not in ("cancer", "normal", "custom"):
                    raise OncoTracerError("type: choose cancer, normal, custom.")
                label = _text(selected, "label") if kind == "custom" else kind
                # Standard labels always use the matching role. Other tags stay intact.
                if label.casefold() in ("normal", "control", "cancer"):
                    kind = "normal" if label.casefold() in ("normal", "control") else "cancer"
                    label = kind
                if len(label) > 160 or any(ord(char) < 32 for char in label):
                    raise OncoTracerError("Sample labels must be at most 160 characters without control characters.")
                role = (_choice(selected, "role", ("tumor", "normal")) if kind == "custom"
                        else "normal" if kind == "normal" else "tumor")
                entries.append({"source": discovered.samples[index], "sample": name,
                                "sample_type": label, "analysis_role": role, "scan_index": index})
            if self.cpg_context:
                self.cpg_context.validate_selections(entries)
            methylation_group = None
            if "methylation_assignments" in data:
                if discovered.mode != "ont" or data.get("analysis") != "methylation":
                    raise OncoTracerError("Classifier sample groups require ONT methylation-only analysis.")
                classifier = _choice(data, "classifier", ("marlin", "sturgeon"))
                assignments = data["methylation_assignments"]
                if not isinstance(assignments, list):
                    raise OncoTracerError("Provide explicit methylation sample assignments.")
                assigned = {}
                for assignment in assignments:
                    if (not isinstance(assignment, dict) or set(assignment) != {"id", "classifier"}
                            or type(assignment["id"]) is not int or assignment["id"] not in indices
                            or assignment["id"] in assigned
                            or assignment["classifier"] not in ("marlin", "sturgeon", "")):
                        raise OncoTracerError("Invalid or repeated methylation sample assignment; review the classifier groups.")
                    assigned[assignment["id"]] = assignment["classifier"]
                if set(assigned) != indices:
                    raise OncoTracerError("Assign or explicitly exclude every included sample from methylation classification.")
                included = [row for row in entries if assigned[row["scan_index"]] == classifier]
                if not included:
                    raise OncoTracerError("The selected classifier group is empty. Assign at least one sample to it.")
                methylation_group = {"classifier": classifier, "included": len(included),
                                     "excluded": len(entries) - len(included)}
                entries = included
            if discovered.mode == "illumina" and len({row["source"].fastq_2 is not None for row in entries}) > 1:
                raise OncoTracerError("Select either paired-end or single-end samples for this project; use separate projects for different layouts.")
            # Recheck the discovery before saving, so changed folders need a fresh review.
            refreshed = self.cpg_context.discovery if self.cpg_context else discover_fastqs(discovered.root, discovered.mode, named_ont=discovered.named_samples)
            current = {sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1: sample for sample in refreshed.samples}
            for row in entries:
                sample = row["source"]
                key = sample.fastq_dir if discovered.mode == "ont" else sample.fastq_1
                if current.get(key) != sample:
                    raise OncoTracerError("FASTQ files changed since discovery. Scan the folder and review samples again.")
            project = Path(_text(data, "project")).expanduser().resolve()
            if self.cpg_context:
                self.cpg_context.validate_destination(project)
            if _choice(data, "project_mode", ("new", "existing"), "new") == "existing" and not project.is_dir():
                raise OncoTracerError("Choose an existing project folder, or select Create a project.")
            if project == discovered.root or project in discovered.root.parents or discovered.root in project.parents:
                raise OncoTracerError("Choose a project directory outside the input FASTQ folder.")
            analysis = _choice(data, "analysis", ("cna",) if discovered.mode == "illumina" else ("cna", "methylation", "both"), "cna")
            if methylation_group and (project / "config/run.yml").exists():
                prior = load_flat_yaml(project / "config/run.yml")
                if (prior.get("methylation_only") is not True
                        or prior.get("methylation_classifier") != methylation_group["classifier"]):
                    raise OncoTracerError("Choose a separate methylation project for this classifier group; keep the completed ONT project and other classifier projects unchanged.")
            backend = _choice(data, "backend", ("conda", "docker", "singularity", "poetry", "host"), "conda")
            from .project_resume import has_recorded_outputs
            if (project / "config/run.yml").is_file() and has_recorded_outputs(project):
                # A typed Create destination may actually be a moved, used project.
                # Resolve it before writing any settings or sample assignments.
                return self.resume({"project": str(project), "backend": backend,
                                    "image": data.get("docker_image")})
            threads = data.get("threads")
            maximum = self.system()["hardware"]["cpu_workers_available"]
            if type(threads) is not int or not 1 <= threads <= maximum:
                raise OncoTracerError(f"CPU threads must be a whole number from 1 to {maximum}.")
            args = build_parser().parse_args(["setup", "--non-interactive", "--mode", discovered.mode,
                                             "--project", str(project), "--analysis", analysis,
                                             "--backend", backend, "--threads", str(threads)])
            if self.setup_args:
                # Keep advanced local resource and cache flags supplied to setup.
                from .setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
                for key in ("reference_cache", *EXECUTABLES, *RESOURCE_FLAGS,
                            *RESOURCE_FILES["marlin"], *RESOURCE_FILES["sturgeon"]):
                    setattr(args, key, copy.deepcopy(getattr(self.setup_args, key, None)))
            from .setup import VARIANT_FIELDS
            variants = data.get("variants", False)
            if type(variants) is not bool:
                raise OncoTracerError("Small-variant calling selection must be true or false.")
            args.variants = variants
            if data.get('docker_image') not in (None, ''):
                if backend != 'docker':
                    raise OncoTracerError('Docker image selection requires the Docker backend.')
                args.image = _text(data, 'docker_image')
            for key in VARIANT_FIELDS:
                # Empty optional fields are omitted. Explicit false, arrays and
                # dictionaries are invalid rather than silently truthy defaults.
                if key in data and data[key] not in (None, ""):
                    from .setup import VARIANT_BOOLEAN_FIELDS
                    if key == "variant_matched_normals":
                        from .strelka2 import parse_matched_normals
                        setattr(args, key, parse_matched_normals(data[key]))
                    elif key == "variant_parallel_samples":
                        if data[key] != 'auto' and type(data[key]) is not int:
                            raise OncoTracerError('Parallel samples must be auto or a positive integer.')
                        from .sample_resources import parallel_value
                        setattr(args, key, parallel_value(data[key]))
                    elif key in VARIANT_BOOLEAN_FIELDS:
                        if type(data[key]) is not bool:
                            raise OncoTracerError(f"{key} must be true or false.")
                        setattr(args, key, data[key])
                    else:
                        setattr(args, key, _text(data, key))
            values = {}
            if discovered.mode == "illumina":
                args._wizard_rows = [[row["sample"], encode_fastq_field(row["source"].fastq_1),
                                      encode_fastq_field(row["source"].fastq_2), row["analysis_role"]] for row in entries]
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
                if discovered.named_samples:
                    values["ont_named_samples"] = True
                if controls:
                    values.update(ont_normal_folder=str(self.cpg_context.control_root if self.cpg_context else discovered.root),
                                  ont_normal_barcodes=",".join(row["source"].barcode for row in controls),
                                  ont_normal_sample_names=",".join(row["sample"] for row in controls))
                caller = _choice(data, "caller", ("ichorcna", "qdnaseq"), "ichorcna")
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
                detail = _choice(data, "report_detail", ("catalog", "models", "literature"), "catalog")
                gistic = data.get("gistic", False)
                if type(gistic) is not bool:
                    raise OncoTracerError("GISTIC selection must be true or false.")
                if gistic and len(entries) < 2:
                    raise OncoTracerError("GISTIC needs at least two selected samples. Disable it for a single-sample project.")
                online = detail == "literature"
                values.update(cna_classifier_sample_set=_text(data, "report_context", default="broad_cancer"),
                              cna_classifier_samples=",".join(row["sample"] for row in entries),
                              knowledge_web=online, knowledge_literature_llm=online,
                              knowledge_deep_literature=online, knowledge_deep_enable_llm_ranker=online,
                              pathology_use_biomed_models=False, knowledge_catalog_llm=detail == "models",
                              run_gistic=gistic, gistic_required=gistic,
                              knowledge_llm_threads=min(threads, 4))
            reference = _choice(data, "reference", ("download", "reuse", "build"), "download")
            if self.cpg_context and (reference != "reuse" or
                    Path(_text(data, "reference_path")).expanduser().resolve() != Path(self.setup_args.hg38_build)):
                raise OncoTracerError("CpG continuation must reuse the saved ONT reference.")
            if reference == "reuse":
                args.hg38_build = _text(data, "reference_path")
            args.build_reference = reference == "build"
            if analysis != "cna":
                args.classifier = _choice(data, "classifier", ("marlin", "sturgeon"))
                source = _choice(data, "methylation_source", ("modbam", "pod5"))
                setattr(args, "modbam" if source == "modbam" else "pod5_dir", _text(data, "methylation_path"))
                args.resources = _text(data, "resources") if data.get("resources") else None
                from .setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
                resources = data.get("resource_paths", {})
                allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[args.classifier])
                if not isinstance(resources, dict) or any(key not in allowed for key in resources):
                    raise OncoTracerError("Choose valid methylation tool and model resource paths.")
                for key in resources:
                    if resources[key]:
                        setattr(args, key, _text(resources, key))
                args.gpu = _choice(data, "device", ("cpu", "gpu"), "cpu") == "gpu"
                args.accept_sturgeon_license = data.get("accept_sturgeon_license") is True
            if data.get('resources_selection') is not None and analysis != 'methylation':
                from .sample_resources import selected_config
                selection = selected_config({'mode': discovered.mode, 'run_variants': args.variants,
                    'threads': threads}, len(entries), data['resources_selection'])
                args.threads = selection['threads']
                values.update({key: selection[key] for key in ('parallel_samples', 'analysis_memory')})
                if args.variants:
                    args.variant_parallel_samples = selection['variant_parallel_samples']
                    args.variant_memory = selection['variant_memory']
            args._wizard_values = values
            args._wizard_metadata = [
                {key: row[key] for key in ("sample", "sample_type", "analysis_role")}
                | {"fastq_files": json.dumps([str(p) for p in row["source"].files])} for row in entries]
            selected_sources = tuple(row["source"] for row in entries)
            inputs = _input_snapshot(selected_sources)
            project_created = not project.exists()
            project_removable = project_created or _existing_setup_project(project)
            _command_setup(args)
            from .setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
            from .variant_resources import PATH_FIELDS
            protected_paths = [str(value) for key in ("pod5_dir", "modbam", "reference_cache", "resources",
                                *EXECUTABLES, *RESOURCE_FLAGS, *RESOURCE_FILES["marlin"], *RESOURCE_FILES["sturgeon"], *PATH_FIELDS)
                               if isinstance(value := getattr(args, key, None), str) and value
                               and Path(value).expanduser().exists()]
            project_stat = project.stat()
            config_path = project / "config/run.yml"
            command = _launcher() + ["check", "--variant-tools", "--json", "--config", str(config_path)]
            try:
                checked = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False,
                                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                try:
                    report = json.loads(checked.stdout)
                except ValueError:
                    report = {"errors": [checked.stderr.strip() or checked.stdout.strip() or "Configuration check did not return a report."]}
                valid = checked.returncode == 0 and not report.get("errors")
            except subprocess.TimeoutExpired:
                valid, report = False, {"errors": ["Configuration check timed out. Settings were saved; use oncotracer check before running."]}
            for message in getattr(args, "_variant_runtime_diagnostics", []):
                if message not in report.setdefault("warnings", []):
                    report["warnings"].append(message)
            project_id = secrets.token_urlsafe(18)
            paths = [config_path, project / "config/sample_metadata.csv"]
            if discovered.mode == "illumina":
                paths.append(project / "config/samplesheet.csv")
            # Keep only reviewed display fields, never arbitrary YAML or patient metadata.
            saved = load_flat_yaml(config_path)
            reviewed_summary = {
                "project_id": project_id, "project": str(project), "config_path": str(config_path),
                "config": {"mode": discovered.mode, "backend": backend, "analysis": analysis,
                           "threads": saved["threads"], "caller": caller, "bin_size_kb": binsize,
                           "run_variants": bool(saved.get("run_variants", False)),
                           "specimen_type": saved.get("variant_specimen_type"),
                           "variant_callers": saved.get("variant_callers") if saved.get("run_variants") else None},
                "methylation_group": copy.deepcopy(methylation_group),
                "samples": [{"name": row["sample"], "sample_type": row["sample_type"],
                             "analysis_role": row["analysis_role"],
                             "detected_name": row["source"].sample,
                             "barcode": row["source"].barcode,
                             "file_count": len(row["source"].files)} for row in entries],
            }
            prepared = {"id": project_id, "project": str(project), "config_path": str(config_path),
                        "reviewed_summary": reviewed_summary,
                        "config_backup": str(args._setup_backup) if args._setup_backup else None,
                        "config": config_path.read_text(), "backend": backend, "valid": valid,
                        "check": report, "fingerprint": _fingerprint(paths),
                        "discovered": discovered, "selected_sources": selected_sources,
                        "input_snapshot": inputs, "outdir": str(project / "results"),
                        "project_created": project_created,
                        "project_removable": project_removable, "protected_paths": protected_paths,
                        "project_identity": (project_stat.st_dev, project_stat.st_ino)}
            if analysis != "cna":
                reviewed_summary["config"]["methylation_classifier"] = args.classifier
            if methylation_group:
                report.setdefault("warnings", []).append(
                    f"Only the {methylation_group['classifier'].upper()} group ({methylation_group['included']} samples) is saved. "
                    f"{methylation_group['excluded']} other/unassigned samples are excluded and are not queued. "
                    "Use a separate project for the other classifier group.")
            self.projects[project_id] = prepared
            return {key: value for key, value in prepared.items()
                    if key not in {"fingerprint", "discovered", "selected_sources", "input_snapshot", "project_created", "project_removable", "protected_paths", "project_identity"}}

    def sturgeon_resources(self, data):
        from .sturgeon_resources import prepare_sturgeon
        return prepare_sturgeon(acknowledged=data.get("accept_sturgeon_license"))

    def resume(self, data):
        from .project_resume import resume_project, sample_snapshot
        self.variant_installer.require_idle()
        with self.lock:
            if self.job and self.job["status"] in {"running", "stopping"}:
                raise OncoTracerError("Stop the active analysis before loading another project.")
            # An explicit blank means use the saved selection, not a launch-time
            # override that the user has just cleared in the browser.
            def selection(key):
                value = data[key] if key in data else getattr(self.setup_args, key, None)
                if value is None:
                    return None
                if not isinstance(value, str) or "\x00" in value:
                    raise OncoTracerError(f"Invalid resume {key}.")
                return value.strip() or None
            backend, image = selection("backend"), selection("image")
            if backend and backend not in {"conda", "docker", "host", "poetry"}:
                raise OncoTracerError("Choose a supported backend for continuing this project.")
            if backend and backend != "docker":
                image = None
            result = resume_project(_text(data, "project"), backend=backend, image=image,
                                    resources=data.get('resources'))
            project, config_path = Path(result["project"]), Path(result["config_path"])
            config = load_flat_yaml(config_path)
            snapshot, samples = sample_snapshot(config)
            from .sample_resources import offers
            try:
                resource_choices = offers(config, len(samples)) if not config.get('methylation_only') else None
            except OncoTracerError as error:
                resource_choices = {'error': str(error)}
            checked = subprocess.run(_launcher() + ["check", "--config", str(config_path), "--json"],
                                     capture_output=True, text=True, timeout=120,
                                     env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                report = json.loads(checked.stdout)
            except ValueError:
                report = {"errors": [checked.stderr.strip() or "Configuration check did not return a report."]}
            report.setdefault("warnings", []).append("Saved sample assignments and settings retained. Run validates cached stages and recomputes missing or incompatible work.")
            if result["moved"]:
                report["warnings"].append("Moved project paths were updated; original configuration is in config/backups/.")
            if result["recovered_configuration"]:
                report["warnings"].append("Recovered the saved analysis settings from the backup matching these outputs; the newer overwritten configuration is also backed up. Review the recovered settings below.")
            paths = [config_path, *(path for path in (project / "config/samplesheet.csv", project / "config/sample_metadata.csv") if path.exists())]
            project_id = secrets.token_urlsafe(18)
            analysis = "methylation" if config.get("methylation_only") else "both" if config.get("methylation") else "cna"
            prepared = {**result, "id": project_id, "kind": "existing_project", "config": config_path.read_text(),
                        "docker_image": config.get("docker_image", ""),
                        "resource_choices": resource_choices, "resources_confirmed": data.get('resources') is not None,
                        "valid": checked.returncode == 0 and not report.get("errors"), "check": report,
                        "fingerprint": _fingerprint(paths), "input_snapshot": snapshot,
                        "project_created": False, "project_removable": False, "protected_paths": [],
                        "project_identity": (project.stat().st_dev, project.stat().st_ino),
                        "reviewed_summary": {"project_id": project_id, "project": str(project),
                            "config_path": str(config_path), "samples": samples,
                            "config": {"mode": config["mode"], "backend": result["backend"], "analysis": analysis,
                                "threads": config.get("threads"), "caller": config.get("ont_caller", "qdnaseq"),
                                "run_variants": config.get("run_variants", False),
                                "methylation_classifier": config.get("methylation_classifier")}}}
            self.projects[project_id] = prepared
            return {key: value for key, value in prepared.items() if key not in
                    {"fingerprint", "input_snapshot", "project_identity", "protected_paths"}}

    def variant_resources(self, data):
        return discover_variant_resources(data, roots=tools_roots(self.start_dir, data))

    def resource_options(self, data):
        from .sample_resources import offers
        if data.get('project'):
            from .project_resume import project_path, sample_snapshot
            config = load_flat_yaml(project_path(_text(data, 'project')) / 'config/run.yml')
            _, samples = sample_snapshot(config)
            count = len(samples)
        else:
            discovery = self.scans.get(_text(data, 'scan_id'))
            if discovery is None:
                raise OncoTracerError('Discover samples before choosing resources')
            count = len(discovery.samples)
            config = {'mode': discovery.mode, 'run_variants': data.get('variants') is True}
        return offers(config, count, threads=data.get('threads'), memory=data.get('memory'))

    def variant_load(self, data):
        from .variant_web import load_for_browser
        return load_for_browser(self, data)

    def variant_prepare(self, data):
        self.variant_installer.require_idle()
        from .variant_web import prepare_for_browser
        return prepare_for_browser(self, data)

    def run(self, data):
        self.variant_installer.require_idle()
        with self.lock:
            prepared = self.projects.get(_text(data, "project_id"))
            if not prepared or not prepared["valid"]:
                raise OncoTracerError("Save and validate a project before running it.")
            if self.job and self.job["project_id"] == prepared["id"]:
                return self.status()
            if self.job and self.job["status"] in {"running", "stopping"}:
                raise OncoTracerError("An analysis is already running in this browser session.")
            if _fingerprint([Path(path) for path in prepared["fingerprint"]]) != prepared["fingerprint"]:
                raise OncoTracerError("Saved configuration changed after review. Save and check settings again before running.")
            if prepared.get('kind') == 'existing_bam_variants':
                from .variant_web import assert_snapshot
                assert_snapshot(prepared['input_snapshot'], prepared['input_digests'])
            elif prepared.get('kind') == 'existing_project':
                from .project_resume import sample_snapshot
                current, _ = sample_snapshot(load_flat_yaml(Path(prepared['config_path'])))
                if current != prepared['input_snapshot']:
                    raise OncoTracerError("FASTQ inputs changed after resume review; load and check the project again.")
            else:
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
                    raise OncoTracerError("FASTQ inputs changed after review. Rescan and review the samples, then save and check before running.") from error
            project = Path(prepared["project"])
            if project.is_symlink() or project.resolve() != project:
                raise OncoTracerError('The reviewed project folder was redirected. Prepare a new project.')
            project_stat = project.stat()
            if (project_stat.st_dev, project_stat.st_ino) != prepared['project_identity']:
                raise OncoTracerError('The reviewed project folder was replaced. Prepare a new project.')
            logs = project / "logs"
            logs.mkdir(exist_ok=True)
            log_path = logs / "web-analysis.log"
            attempt = 1
            while log_path.exists() or log_path.is_symlink():
                attempt += 1
                log_path = logs / f"web-analysis-{attempt}.log"
            if prepared.get('kind') == 'existing_bam_variants':
                command = _launcher() + ['variants', '--config', prepared['config_path']]
            else:
                command = _launcher() + ["setup", "--project", str(project), "--run", "--non-interactive",
                                         "--backend", prepared["backend"]]
            status_path = Path(prepared['outdir']) / '08_variants/variant_status.json'
            prior_status = status_path.stat().st_mtime_ns if status_path.exists() else None
            manifest_path = Path(prepared['outdir']) / '06_workflow_summary/native_run_manifest.json'
            prior_manifest = manifest_path.stat().st_mtime_ns if manifest_path.exists() else None
            terminal_started = time.time_ns()
            with log_path.open("xb") as handle:
                try:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle,
                                               stderr=subprocess.STDOUT, start_new_session=True,
                                               env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"})
                except OSError as error:
                    handle.write(f"Analysis could not start: {error}\n".encode())
                    raise
            self.job = {"project_id": prepared["id"], "status": "running", "exit_code": None,
                        "log_path": str(log_path), "pid": process.pid, "process": process,
                        "_reviewed_summary": copy.deepcopy(prepared.get("reviewed_summary")),
                        "_started_at": time.monotonic(), '_prior_variant_status_mtime': prior_status,
                        '_terminal_started_ns': terminal_started,
                        '_prior_manifest_mtime': prior_manifest}
            threading.Thread(target=self._wait, args=(self.job,), daemon=True).start()
            return self.status()

    def _wait(self, job):
        # The child owns the full log. TTY users see one compact sample dashboard.
        from .terminal_progress import TerminalDisplay, is_terminal
        display = None
        if is_terminal(sys.stderr) and job.get('log_path'):
            display = TerminalDisplay(self.projects[job['project_id']]['outdir'], job['log_path'],
                                      started_ns=job.get('_terminal_started_ns'))
            display.start()
        def mirror():
            try:
                with Path(job['log_path']).open(errors='replace') as reader:
                    while True:
                        chunk=reader.read()
                        if chunk:print(chunk,end='',flush=True)
                        if job['process'].poll() is not None:
                            print(reader.read(),end='',flush=True)
                            return
                        time.sleep(.2)
            except (OSError,KeyError):
                return
        thread=threading.Thread(target=mirror if display is None else lambda: None,daemon=True)
        thread.start()
        try:
            result = job["process"].wait()
        except BaseException as error:
            if display:
                display.finish(error=error)
            raise
        finally:
            thread.join(timeout=2)
        with self.lock:
            job["exit_code"] = result
            if job.get("stop_requested"):
                # The cancellation worker waits for the whole process group.
                if display:
                    display.finish(error='Cancellation requested; remaining workers are being stopped.')
                return
            job["_finished_at"] = time.monotonic()
            job["status"] = "complete" if result == 0 else "failed"
            prepared = self.projects[job["project_id"]]
            outdir = Path(prepared["outdir"]).resolve()
            if result == 2 and prepared.get('kind') == 'existing_bam_variants':
                from .variant_web import published_partial_result
                if published_partial_result(prepared, job.get('_prior_variant_status_mtime')):
                    job['status'] = 'partial_failure'
            elif result == 2:
                from .run_completion import published_native_partial
                if published_native_partial(outdir, prepared['fingerprint'][prepared['config_path']],
                                            job.get('_prior_manifest_mtime')):
                    job['status'] = 'partial_failure'
            from .run_completion import published_completion
            job["completion"] = published_completion(
                outdir, prepared.get('fingerprint', {}).get(prepared.get('config_path'), ''),
                job.get('_prior_manifest_mtime'))
            if display:
                display.finish(job['completion'], status=job['status'])
            if (outdir / "index.html").is_file():
                key = secrets.token_urlsafe(32)
                self.result_roots[key] = outdir
                job["results_url"] = f"/results/{key}/index.html"

    @staticmethod
    def _group_running(pid):
        try:
            os.killpg(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self, data):
        """Cancel only this server's active job; never accept a caller-supplied PID."""
        with self.lock:
            if not self.job or self.job["project_id"] != _text(data, "project_id"):
                raise OncoTracerError("This analysis is not the current browser job.")
            job = self.job
            if job["status"] not in {"running", "stopping"}:
                return self.status()
            if job["status"] == "stopping" and not job.get("stop_error"):
                return self.status()
            if job["process"].poll() is not None and not job.get("stop_requested"):
                raise OncoTracerError("The analysis has already finished. Refresh its status.")
            # Every analysis is launched in its own session; children inherit its
            # process group, including command pipelines and reference downloads.
            if not job.get("stop_requested") and os.getpgid(job["pid"]) != job["pid"]:
                raise OncoTracerError("Cannot identify the analysis process group safely.")
            job.update(status="stopping", stop_requested=True)
            job.pop("stop_error", None)
            threading.Thread(target=self._stop_group, args=(job,), daemon=True).start()
            return self.status()

    def _stop_group(self, job):
        try:
            # Interrupt first to allow download and tool cleanup, then bound the
            # wait if a tool ignores interrupts. Do not block the HTTP request.
            for sig, seconds in ((signal.SIGINT, 5), (signal.SIGTERM, 3), (signal.SIGKILL, 2)):
                try:
                    os.killpg(job["pid"], sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline and self._group_running(job["pid"]):
                    time.sleep(.1)
                if not self._group_running(job["pid"]):
                    break
            with self.lock:
                if self._group_running(job["pid"]):
                    job["stop_error"] = "Some analysis processes have not exited. Retry Stop; project removal is blocked."
                else:
                    job["exit_code"] = job["process"].poll()
                    job["_finished_at"] = time.monotonic()
                    job["status"] = "stopped"
        except OSError as error:
            with self.lock:
                job["stop_error"] = f"Could not stop the analysis: {error}"

    def remove_project(self, data):
        """Remove a dedicated project only after stop and exact confirmation."""
        with self.lock:
            project_id = _text(data, "project_id")
            if not self.job or self.job["project_id"] != project_id or self.job["status"] != "stopped":
                raise OncoTracerError("Stop this analysis completely before removing its project folder.")
            prepared = self.projects[project_id]
            path = Path(prepared["project"])
            if data.get("confirm_remove") is not True or data.get("confirm_path") != str(path):
                raise OncoTracerError("Confirm deletion of this exact incomplete project folder.")
            if self._group_running(self.job["pid"]):
                raise OncoTracerError("Analysis processes are still active; removal is blocked.")
            reason = _project_removal_error(prepared)
            if reason:
                raise OncoTracerError(reason)
            # Never follow directory symlinks into inputs, shared references or tools.
            if not shutil.rmtree.avoids_symlink_attacks:
                raise OncoTracerError("Safe folder removal is unavailable on this platform.")
            prepare_project_runtime_removal(path)
            shutil.rmtree(path)
            self.job["status"] = "removed"
            self.job.pop("results_url", None)
            for key, root in list(self.result_roots.items()):
                if root.is_relative_to(path):
                    del self.result_roots[key]
            prepared["valid"] = False
            return self.status()

    def result_file(self, key, relative):
        """Resolve a report capability without granting access outside its results."""
        with self.lock:
            root = self.result_roots.get(key)
        if root is None:
            raise FileNotFoundError("Results session expired or missing.")
        relative = unquote(relative) or "index.html"
        parts = PurePosixPath(relative).parts
        if (not parts or relative.startswith("/") or "\\" in relative or "\x00" in relative
                or any(part in {".", ".."} or part.startswith(".") for part in parts)):
            raise FileNotFoundError("Result file not found.")
        path = (root / relative).resolve()
        if path.is_dir():
            path = (path / "index.html").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError("Result file not found.")
        return path

    def status(self):
        with self.lock:
            if not self.job:
                return {"status": "idle", "log": ""}
            result = {key: value for key, value in self.job.items() if key != "process" and not key.startswith("_")}
            prepared = self.projects[result["project_id"]]
            result["project_path"] = prepared["project"]
            reviewed = self.job.get("_reviewed_summary")
            if reviewed is not None:
                result["reviewed_summary"] = copy.deepcopy(reviewed)
            result["remove_block_reason"] = _project_removal_error(prepared) if result["status"] == "stopped" else None
            result["can_remove"] = result["status"] == "stopped" and not result["remove_block_reason"]
            if result["status"] == "removed":
                result.update(log="Project folder removed by confirmation. Input files and shared download caches were kept.", log_truncated=False)
                result["progress"] = progress_for_job(self.job, result["log"])
                return result
            with Path(result["log_path"]).open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 262144))
                result["log"] = handle.read().decode("utf-8", errors="replace")
                result["log_truncated"] = size > 262144
            result["progress"] = progress_for_job(self.job, result["log"])
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
        if self.command != "HEAD":
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

    def _result(self, url, *, head=False):
        # Browser links cannot set the API header. Their random path is scoped to
        # one prepared project's results; it never exposes arbitrary host files.
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self._reply(403, {"error": "Use the printed loopback address."})
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self._reply(403, {"error": "Open results from the local OncoTracer page."})
            return
        try:
            key, separator, relative = url.path[len("/results/"):].partition("/")
            if not separator:
                raise FileNotFoundError("Result file not found.")
            path = self.server.state.result_file(key, relative)
            with path.open("rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                start, end, partial = 0, size - 1, False
                value = self.headers.get("Range")
                if value:
                    try:
                        unit, interval = value.split("=", 1)
                        left, right = interval.split("-", 1)
                        if unit != "bytes" or "," in interval or not size:
                            raise ValueError
                        if left:
                            start = int(left)
                            end = min(int(right), size - 1) if right else size - 1
                        else:
                            suffix = int(right)
                            if suffix <= 0:
                                raise ValueError
                            start = max(0, size - suffix)
                        if start < 0 or start >= size or end < start:
                            raise ValueError
                        partial = True
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                inline = path.suffix.lower() in {".html", ".htm", ".pdf", ".png", ".svg", ".jpg", ".jpeg", ".txt", ".json"}
                if path.suffix.lower() in {".txt", ".json", ".html", ".htm"}:
                    content_type += "; charset=utf-8"
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.send_header("Content-Disposition", ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(path.name, safe=""))
                self.end_headers()
                if not head:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except (FileNotFoundError, PermissionError, ValueError):
            self._reply(404, {"error": "Result file not found or inaccessible."})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        url = urlsplit(self.path)
        if url.path.startswith("/results/"):
            self._result(url, head=True)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/results/"):
            self._result(url)
            return
        if url.path in ("/", "/variants", "/variants/"):
            if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
                self._reply(403, {"error": "Use the printed loopback address."})
            else:
                if url.path != '/':
                    from .variant_web_ui import PAGE as variant_page
                    self._reply(200, variant_page, html=True)
                else:
                    self._reply(200, PAGE, html=True)
            return
        if not self._authorized():
            return
        try:
            if url.path == "/api/system":
                value = self.server.state.system()
            elif url.path == "/api/browse":
                query = parse_qs(url.query)
                value = self.server.state.browse(query.get("path", [None])[0], query.get("kind", ["folder"])[0])
            elif url.path == "/api/status":
                value = self.server.state.status()
            elif url.path == "/api/variant-install/status":
                value = self.server.state.variant_installer.status()
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
            methods = {"/api/pick-path": self.server.state.pick_path,
                       "/api/resource-options": self.server.state.resource_options,
                       "/api/resume": self.server.state.resume,
                       "/api/sturgeon-resources": self.server.state.sturgeon_resources,
                       "/api/ont-inputs": self.server.state.ont_inputs, "/api/scan": self.server.state.scan, "/api/prepare": self.server.state.prepare,
                       '/api/variant-resources': self.server.state.variant_resources,
                       '/api/variant-install/plan': self.server.state.variant_installer.plan,
                       '/api/variant-install/start': self.server.state.variant_installer.start,
                       '/api/variant-install/stop': self.server.state.variant_installer.stop,
                       '/api/variant-load': self.server.state.variant_load, '/api/variant-prepare': self.server.state.variant_prepare,
                       "/api/run": self.server.state.run, "/api/stop": self.server.state.stop,
                       "/api/remove-project": self.server.state.remove_project}
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


DEFAULT_WEB_PORT = 8888


def _open_web_server(state: WebState, port: int | None = None) -> WebServer:
    """Bind directly: the kernel allocates a free port without a probe race."""
    if port is not None and not 1 <= port <= 65535:
        raise OncoTracerError("--port must be from 1 to 65535.")
    selected = DEFAULT_WEB_PORT if port is None else port
    try:
        return WebServer(selected, state)
    except OSError as error:
        if port is None and error.errno == errno.EADDRINUSE:
            try:
                return WebServer(0, state)
            except OSError as fallback:
                raise OncoTracerError(f"Cannot open a local browser port: {fallback}.") from fallback
        hint = " Omit --port to select an available port automatically." if port is not None else ""
        raise OncoTracerError(f"Cannot open 127.0.0.1:{selected}: {error}.{hint}") from error


def command_web(args):
    port = getattr(args, "port", None)
    if port is not None and not 1 <= port <= 65535:
        raise OncoTracerError("--port must be from 1 to 65535.")
    state = WebState(Path(args.start_dir), args if hasattr(args, "input_folder") or getattr(args, "variant_config", None) else None)
    server = None
    try:
        server = _open_web_server(state, port)
        route = '/variants/' if getattr(args, 'variant_config', None) else '/'
        url = f"{server.origin}{route}#{state.token}"
        print(f"OncoTracer browser URL (copy the entire link, including #):\n{url}", flush=True)
        print("Folders are on this computer. Keep this terminal open; Ctrl+C closes the setup page.", flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass  # The printed URL works on computers without a browser.
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        if state.job and state.job["status"] == "running":
            print(f"Analysis continues (PID {state.job['pid']}). Log: {state.job['log_path']}", flush=True)
    finally:
        state.variant_installer.close()
        if server is not None:
            server.server_close()
    return 0


def add_web_command(subparsers):
    parser = subparsers.add_parser("web", help="Open the local browser setup and analysis dashboard")
    parser.add_argument("--port", type=int, help="fixed loopback HTTP port; default: 8888 or an available port")
    parser.add_argument("--start-dir", default=str(Path.cwd()), help="starting folder in the local file navigator")
    parser.add_argument("--no-browser", action="store_true", help="print the local URL without opening a browser automatically")
    parser.add_argument('--variant-config', help='Review and rerun an existing-BAM variant YAML in the browser')
    parser.set_defaults(func=command_web)
