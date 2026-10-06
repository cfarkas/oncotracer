"""Explicit, background installation of optional tools from server-owned plans.

Discovery and planning are read-only. Only Start launches commands; neither
request bodies nor displayed recipe snippets are ever executed as shell code.
Each installation gets new prefixes, separate from any running analysis.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import threading
import time

from .runtime import OncoTracerError, runtime_root
from .variant_resources import _payload, discover_variant_resources

ACTIVE = {"running", "stopping"}
LOG_LIMIT = 65536
CLAIRSTO_IMAGE = "docker://hkubal/clairs-to:v0.4.4@sha256:4587ee6307575f68eec686f8cce23919a59b20b8f97c6d84d0084b1ef46a8c01"
CLAIR3_PACKAGES = ["clair3=1.2.0", "tensorflow=2.15.0=cpu*", "samtools=1.23.1",
                   "bcftools=1.23.1", "varlociraptor=8.9.5"]
DOCKER_TOOL_CHECK = """import json, os, shutil, sys
from pathlib import Path
request = json.loads(sys.argv[1])
prefix = Path(os.environ.get('ONCOTRACER_VARIANTS_PREFIX', '/opt/oncotracer-envs/variants'))
names = ['samtools', 'bcftools']
mapping = {'mutect2': 'gatk', 'freebayes': 'freebayes', 'clair3': 'run_clair3.sh', 'clairs_to': 'run_clairs_to'}
names += [mapping[c] for c in request['callers'] if c in mapping]
if request['varlociraptor']: names.append('varlociraptor')
if request['ffperase']: names.append('gatk')
for name in set(names):
    path = prefix / 'bin' / name
    if not (path.is_file() and os.access(path, os.X_OK)) and not shutil.which(name):
        raise SystemExit('Selected Docker image is missing ' + name)
if any(c.startswith('strelka2_') for c in request['callers']):
    prefix = Path(os.environ.get('ONCOTRACER_STRELKA_PREFIX', '/opt/oncotracer-envs/strelka2'))
    for name in ['python2.7', 'configureStrelkaGermlineWorkflow.py', 'configureStrelkaSomaticWorkflow.py']:
        if not (prefix / 'bin' / name).is_file(): raise SystemExit('Selected Docker image is missing ' + name)
if request['ffperase']:
    prefix = Path(os.environ.get('ONCOTRACER_FFPERASE_PREFIX', '/opt/oncotracer-envs/ffperase'))
    if not (prefix / 'bin/python').is_file(): raise SystemExit('Selected Docker image is missing the FFPERASE runtime')
print('Selected caller executables are present in the Docker image')
"""


def tools_roots(start_dir: Path, data: dict) -> tuple[Path, ...]:
    value = data.get("tools_folder", "")
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise OncoTracerError("Tools folder must be a folder path.")
    if not value.strip():
        return (start_dir,)
    folder = Path(value.strip()).expanduser().resolve()
    if not folder.is_dir():
        raise OncoTracerError("Choose an existing tools folder to search.")
    return (folder, start_dir)


def _executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return str(Path(found).absolute())
    if name == "conda":
        choices = [Path(os.environ["CONDA_EXE"])] if os.environ.get("CONDA_EXE") else []
        choices += [Path.home() / base / "bin/conda" for base in
                    ("miniforge3", "anaconda3", "miniconda3", "mambaforge")]
        choices += [Path("/opt/conda/bin/conda"), Path("/opt/miniforge3/bin/conda")]
        for path in choices:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.absolute())
    return None


class VariantInstaller:
    def __init__(self, start_dir: Path, *, install_root: Path | None = None):
        self.start_dir = start_dir
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        self.install_root = (install_root or data_home / "oncotracer/optional-tools/installs").absolute()
        self.lock = threading.RLock()
        self.plans: dict[str, dict] = {}
        self.job: dict | None = None
        self.process: subprocess.Popen | None = None
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None

    def active(self) -> bool:
        with self.lock:
            return bool(self.job and self.job["status"] in ACTIVE)

    def require_idle(self):
        if self.active():
            raise OncoTracerError("Tool installation is still running. Wait for it to finish before saving or starting analysis.")

    def plan(self, data: dict) -> dict:
        mode, backend, specimen, callers, values, image = _payload(data)
        if not callers or not specimen:
            raise OncoTracerError("Choose Fresh or FFPE and at least one caller before installing tools.")
        flow = data.get("flow", "fastq")
        if flow not in {"fastq", "bam"}:
            raise OncoTracerError("Unknown analysis form.")
        roots = tools_roots(self.start_dir, data)
        manager = next((path for name in ("conda", "mamba", "micromamba") if (path := _executable(name))), None)
        docker = _executable("docker")
        native_reason = "" if manager else "Install Conda, Mamba or Micromamba first, then try again."
        if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
            native_reason = "These pinned caller environments require Linux x86_64. Use a supported Docker host or the manual guides."
        sif_runtime = _executable("apptainer") or _executable("singularity")
        if "clairs_to" in callers and not values.get("variant_clairsto_sif") and not sif_runtime:
            native_reason = "ClairS-TO also needs Apptainer/Singularity for its tested SIF. Use Docker or the manual native installation guide."
        docker_reason = "" if docker else "Install Docker and enable access to its daemon first."
        if flow == "bam":
            docker_reason = "The existing-BAM form uses native tools. Docker installation is available in FASTQ setup."
        elif data.get("analysis", "cna") != "cna":
            docker_reason = "Docker supports CNA with optional variants; choose Conda for a methylation workflow."
        options = [{"method": "conda", "label": "Conda / Mamba", "available": not native_reason, "reason": native_reason},
                   {"method": "docker", "label": "Docker", "available": not docker_reason, "reason": docker_reason}]
        requested = data.get("method", "auto")
        if requested not in {"auto", "conda", "docker"}:
            raise OncoTracerError("Choose Conda or Docker installation.")
        method = requested if requested != "auto" else (
            "docker" if backend == "docker" and not docker_reason else
            "conda" if not native_reason else "docker" if not docker_reason else "conda")
        reason = native_reason if method == "conda" else docker_reason
        identity = secrets.token_hex(12)
        directory = self.install_root / identity
        plan = {"id": identity, "method": method, "directory": str(directory), "options": options,
                "available": not reason, "reason": reason, "steps": [], "fields": {},
                "selection": copy.deepcopy(data), "note": "Install tools, then save and check your settings. Models and registered ANNOVAR resources are configured separately."}

        def step(label, command=None, **extra):
            plan["steps"].append({"label": label, "command": command or [], **extra})

        if not reason and method == "docker":
            from .cli import DEFAULT_IMAGE
            image = image or DEFAULT_IMAGE
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}", image):
                raise OncoTracerError("Provide a Docker image name or digest, without options or shell characters.")
            plan["fields"] = {"backend": "docker", "docker_image": image}
            step("Check Docker access", [docker, "info"])
            step("Download the selected Docker image", [docker, "pull", image])
            step("Check the image's analysis tools", [docker, "run", "--rm", "--network", "none", image, "doctor", "--backend", "host"])
            selection = {"callers": callers, "varlociraptor": values.get("variant_varlociraptor") == "required",
                         "ffperase": mode == "illumina" and specimen == "ffpe" and values.get("variant_ffperase", "required") != "off"}
            step("Check selected callers in Docker", [docker, "run", "--rm", "--network", "none", "--entrypoint", "python3", image,
                                                      "-B", "-c", DOCKER_TOOL_CHECK, json.dumps(selection)])
        elif not reason:
            # Model compatibility and licenses are handled by setup/run, not by
            # an executable installer. Inspect only the selected tool resources.
            inspected = {k: v for k, v in values.items() if k in {
                "variant_tool_prefix", "variant_strelka_prefix", "variant_ffperase_prefix",
                "variant_ffperase_sif", "variant_clairsto_sif", "variant_ffperase",
                "variant_varlociraptor", "variant_targets_bed"}}
            detected = discover_variant_resources({"mode": mode, "backend": "conda", "specimen_type": specimen,
                "callers": callers, "values": {**inspected, "variant_annovar": "off"}}, roots=roots)
            states = {row["id"]: row["status"] for row in detected["resources"]}
            if flow == "fastq":
                plan["fields"]["backend"] = backend if backend in {"conda", "host", "poetry"} else "conda"
            def environment(name, field, packages=None):
                prefix = directory / name
                plan["fields"][field] = str(prefix)
                command = [manager, "create" if packages else "env", *([] if packages else ["create"]),
                           "--yes", "--prefix", str(prefix)]
                if packages:
                    command += ["--override-channels", "--channel", "conda-forge", "--channel", "bioconda", "--strict-channel-priority", *packages]
                else:
                    # Resolve the bundled specification only after Start. A
                    # copied executable must use its own verified payload.
                    command += ["--file", str(directory / f"native-{name}.yml")]
                step("Install " + name, command, specification=None if packages else f"native-{name}.yml")
                return prefix

            if states.get("variant_tools") == "missing":
                prefix = environment("clair3" if "clair3" in callers else "variants", "variant_tool_prefix",
                                     CLAIR3_PACKAGES if "clair3" in callers else None)
                checks = ["samtools", "bcftools"]
                checks += [tool for caller, tool in (("mutect2", "gatk"), ("freebayes", "freebayes")) if caller in callers]
                if values.get("variant_varlociraptor") == "required": checks.append("varlociraptor")
                if mode == "illumina" and specimen == "ffpe" and values.get("variant_ffperase", "required") != "off": checks.append("gatk")
                for name in dict.fromkeys(checks):
                    step("Check " + name, [str(prefix / "bin" / name), "--version"])
                if "clair3" in callers:
                    step("Check Clair3", [str(prefix / "bin/run_clair3.sh"), "--help"])
            if any(c.startswith("strelka2_") for c in callers) and states.get("strelka_prefix") == "missing":
                prefix = environment("strelka2", "variant_strelka_prefix")
                for caller, script in (("strelka2_germline", "configureStrelkaGermlineWorkflow.py"), ("strelka2_somatic", "configureStrelkaSomaticWorkflow.py")):
                    if caller in callers:
                        step("Check " + caller, [str(prefix / "bin/python2.7"), str(prefix / "bin" / script), "--help"])
            if mode == "illumina" and specimen == "ffpe" and values.get("variant_ffperase", "required") != "off" and not values.get("variant_ffperase_sif") and states.get("ffperase_prefix") == "missing":
                prefix = environment("ffperase", "variant_ffperase_prefix")
                step("Check FFPERASE dependencies", [str(prefix / "bin/python"), "-B", "-c",
                    "import numpy, pandas, scipy, sklearn, joblib, pysam, imblearn; print('FFPERASE runtime imports passed')"])
            if "clairs_to" in callers and not values.get("variant_clairsto_sif") and states.get("clairs_to") == "missing":
                target = directory / "clairs-to_v0.4.4.sif"
                plan["fields"]["variant_clairsto_sif"] = str(target)
                step("Download ClairS-TO with its tested presets", [sif_runtime, "pull", str(target), CLAIRSTO_IMAGE])
            if not plan["steps"]:
                plan.update(available=False, reason="No missing caller environment was found. Select an existing candidate or use the guides below for models and annotation.")
        with self.lock:
            if len(self.plans) >= 16:
                self.plans.pop(next(iter(self.plans)))
            self.plans[identity] = plan
        return copy.deepcopy(plan)

    def start(self, data: dict) -> dict:
        with self.lock:
            self.require_idle()
            if not isinstance(data.get("plan_id"), str):
                raise OncoTracerError("Review an available installation plan first.")
            plan = self.plans.get(data.get("plan_id"))
            if not plan or not plan["available"]:
                raise OncoTracerError("Review an available installation plan first.")
            directory = Path(plan["directory"])
            if any(path.is_symlink() for path in (directory, *directory.parents)):
                raise OncoTracerError("The installation directory must not contain symlinks.")
            directory.mkdir(parents=True, exist_ok=False)
            self.plans.pop(plan["id"])
            self.cancel.clear()
            self.job = {"installer": "oncotracer-optional-tools-v1", "id": plan["id"], "status": "running", "started_at": time.time(),
                        "stage": "Preparing installation", "completed_steps": 0, "total_steps": len(plan["steps"]),
                        "plan": copy.deepcopy(plan), "fields": {}, "log_path": str(directory / "install.log")}
            Path(self.job["log_path"]).touch(exist_ok=False)
            self._save()
            self.worker = threading.Thread(target=self._install, daemon=True, name="oncotracer-tool-install")
            self.worker.start()
            return self.status()

    def _save(self):
        path = Path(self.job["plan"]["directory"]) / "status.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.job, indent=2) + "\n")
        temporary.replace(path)

    def _install(self):
        outcome = {}
        try:
            plan = self.job["plan"]
            with Path(self.job["log_path"]).open("a", buffering=1) as log:
                for number, step in enumerate(plan["steps"]):
                    if self.cancel.is_set():
                        raise OncoTracerError("Installation stopped.")
                    if step.get("specification"):
                        source = runtime_root() / "environments" / step["specification"]
                        shutil.copyfile(source, Path(plan["directory"]) / step["specification"])
                    command = step["command"]
                    with self.lock:
                        self.job["stage"] = step["label"]
                        self.job["completed_steps"] = number
                        self._save()
                        log.write("\n[" + step["label"] + "] " + shlex.join(command) + "\n")
                        self.process = subprocess.Popen(command, cwd=plan["directory"], stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CONDA_CHANNEL_PRIORITY": "strict"})
                        process = self.process
                    cancelled_at = None
                    while process.poll() is None:
                        if self.cancel.wait(0.2):
                            if cancelled_at is None:
                                cancelled_at = time.monotonic()
                            with self.lock:
                                try:
                                    os.killpg(process.pid, signal.SIGKILL if time.monotonic() - cancelled_at > 3 else signal.SIGTERM)
                                except ProcessLookupError:
                                    pass
                            time.sleep(0.05)
                    if self.cancel.is_set():
                        raise OncoTracerError("Installation stopped. Partial files and the log were retained; retry creates a new environment.")
                    if process.returncode:
                        raise OncoTracerError(f"{step['label']} failed (exit {process.returncode}). Review the installation log and retry.")
                outcome = dict(status="complete", fields=plan["fields"], stage="Installation and tool checks completed", completed_steps=len(plan["steps"]))
        except Exception as error:
            outcome = dict(status="failed", error=str(error), stage=str(error))
        finally:
            with self.lock:
                # Publish the terminal state only after cleanup, so a retry
                # cannot replace self.job while this worker still updates it.
                if self.cancel.is_set():
                    message = "Installation stopped. Partial files and the log were retained; retry creates a new environment."
                    outcome = dict(status="cancelled", fields={}, error=message, stage=message)
                self.process = None
                self.job.update(outcome, finished_at=time.time())
                self._save()

    def status(self) -> dict:
        with self.lock:
            if self.job is None:
                return {"status": "idle"}
            result = copy.deepcopy(self.job)
            path = Path(result["log_path"])
            with path.open("rb") as handle:
                size = path.stat().st_size
                handle.seek(max(0, size - LOG_LIMIT))
                result["log"] = handle.read(LOG_LIMIT).decode("utf-8", errors="replace")
            result["log_truncated"] = size > LOG_LIMIT
            result["elapsed_seconds"] = round(result.get("finished_at", time.time()) - result["started_at"])
            return result

    def stop(self, data: dict) -> dict:
        with self.lock:
            if not self.job or data.get("job_id") != self.job["id"]:
                raise OncoTracerError("This is not the current tool installation.")
            if self.active():
                self.job["status"] = "stopping"
                self.cancel.set()
            return self.status()

    def close(self):
        if self.active():
            self.stop({"job_id": self.job["id"]})
            if self.worker:
                self.worker.join(timeout=5)
