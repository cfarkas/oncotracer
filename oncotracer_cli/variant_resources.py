"""Bounded, read-only hints for the variant forms; never execute discovered tools.

A found file is not a tested environment. Models/SIFs are candidates requiring
review, and chemistry/preservation are always supplied by the user. This module
uses only the standard library and the existing bounded ANNOVAR reader.
"""
from __future__ import annotations

import json
import os
import re
from itertools import islice
from pathlib import Path
from typing import Mapping

from .runtime import OncoTracerError

MAX_ROOTS = 12
MAX_CHILDREN = 64
MAX_PREFIXES = 96
MAX_CANDIDATES = 2048
MAX_SEARCHED = 384
MAX_TEXT_BYTES = 256 * 1024
SYSTEM_PREFIXES = (Path("/opt/conda"), Path("/opt/miniforge3"))
FFPERASE_REVISION = "b0dd56cbd0a939896a966b9ce30c4d719b158170"
CALLERS = {"illumina": ("mutect2", "freebayes", "bcftools"), "ont": ("clair3", "clairs_to")}
TOOLS = {"mutect2": ("gatk",), "freebayes": ("freebayes",), "bcftools": ("bcftools",),
         "clair3": ("run_clair3.sh", "run_clair3.py"), "clairs_to": ("run_clairs_to",)}
PATH_FIELDS = {
    "variant_tool_prefix", "variant_targets_bed", "variant_clair3_model", "variant_clairsto_sif",
    "variant_ffperase_root", "variant_ffperase_models", "variant_ffperase_prefix", "variant_ffperase_sif",
    "variant_annovar_dir", "variant_annovar_db", "variant_varlociraptor_scenario",
}
SETTING_FIELDS = {"variant_ffperase", "variant_varlociraptor", "variant_annovar", "variant_reference_build",
                  "variant_clairsto_platform", "variant_specimen_type", "variant_callers", "variant_varlociraptor_fdr",
                  "variant_varlociraptor_events", "variant_varlociraptor_sample"}
ENV_FIELDS = {
    "variant_tool_prefix": ("ONCOTRACER_VARIANTS_PREFIX",),
    "variant_ffperase_root": ("ONCOTRACER_FFPERASE_ROOT",),
    "variant_ffperase_models": ("ONCOTRACER_FFPERASE_MODELS",),
    "variant_ffperase_prefix": ("ONCOTRACER_FFPERASE_PREFIX",),
    "variant_ffperase_sif": ("ONCOTRACER_FFPERASE_SIF",),
    "variant_annovar_dir": ("ANNOVAR_HOME", "ANNOVAR_DIR"),
    "variant_annovar_db": ("ANNOVAR_DB", "ANNOVAR_DATABASE_DIR"),
}
DOCKER_IGNORED = {"variant_tool_prefix", "variant_ffperase_prefix", "variant_ffperase_sif", "variant_clairsto_sif"}


def _string(value, label, *, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise OncoTracerError(f"{label} must be a short text value without control characters")
    return value.strip()


def _payload(data):
    if not isinstance(data, Mapping):
        raise OncoTracerError("Resource discovery expects a JSON object")
    mode = _string(data.get("mode", ""), "mode")
    backend = _string(data.get("backend", "host"), "backend")
    specimen = _string(data.get("specimen_type", ""), "specimen_type")
    if mode not in CALLERS:
        raise OncoTracerError("mode must be illumina or ont")
    if backend not in {"conda", "host", "poetry", "docker", "singularity"}:
        raise OncoTracerError("backend must be conda, host, poetry, docker or singularity")
    if specimen not in {"", "fresh", "ffpe"}:
        raise OncoTracerError("specimen_type must be fresh, ffpe or empty")
    raw = data.get("callers", [])
    if isinstance(raw, str):
        raw = _string(raw, "callers").split(",") if raw.strip() else []
    if not isinstance(raw, (list, tuple)) or len(raw) > 3:
        raise OncoTracerError("callers must be a list or comma-separated caller names")
    callers = [_string(c, "caller").lower() for c in raw]
    if len(set(callers)) != len(callers) or any(c not in CALLERS[mode] for c in callers):
        raise OncoTracerError(f"Choose unique {mode} callers from: {', '.join(CALLERS[mode])}")
    values = data.get("values", {})
    if not isinstance(values, Mapping) or any(k not in PATH_FIELDS | SETTING_FIELDS for k in values):
        raise OncoTracerError("values must contain supported variant resource/settings fields")
    values = {k: _string(v, k) for k, v in values.items() if v is not None}
    for key, allowed in (("variant_ffperase", {"", "required", "off"}),
                         ("variant_varlociraptor", {"", "required", "off"}),
                         ("variant_annovar", {"", "auto", "off"}),
                         ("variant_reference_build", {"", "hg19", "hg38"})):
        if values.get(key, "") not in allowed:
            raise OncoTracerError(f"Invalid {key}")
    image = _string(data.get("docker_image", ""), "docker_image")
    return mode, backend, specimen, callers, values, image


class _Search:
    def __init__(self, environment, roots):
        self.env = dict(os.environ)
        if environment is not None:
            if not isinstance(environment, Mapping):
                raise OncoTracerError("environment must be a mapping")
            for key, value in environment.items():
                if not isinstance(key, str) or (value is not None and not isinstance(value, str)):
                    raise OncoTracerError("environment values must be text or null")
                if value is None:
                    self.env.pop(key, None)
                else:
                    self.env[key] = value
        self.home = Path(self.env.get("HOME") or str(Path.home())).absolute()
        self.searched, self.notes, self._seen, self._cache = [], [], set(), {}
        if isinstance(roots, (str, bytes, Path)):
            raise OncoTracerError("roots must be a sequence of directory paths")
        try:
            supplied = list(islice(iter(roots), MAX_ROOTS + 1))
        except TypeError as error:
            raise OncoTracerError("roots must be a sequence of directory paths") from error
        if len(supplied) > MAX_ROOTS:
            self.note(f"Search roots limited to the first {MAX_ROOTS} entries.")
        self.roots = self.unique([self.path(p) for p in supplied[:MAX_ROOTS]] + [self.home])
        self.path_dirs = self.unique([self.path(p) for p in self.env.get("PATH", "").split(os.pathsep)[:MAX_CHILDREN] if p])
        self.config = self.read_json(self.path(self.env.get("XDG_CONFIG_HOME") or str(self.home / ".config")) / "oncotracer/config.json")

    def note(self, value):
        if value not in self.notes:
            self.notes.append(value)

    def path(self, value):
        if not isinstance(value, (str, Path)):
            raise OncoTracerError("resource paths must be text or Path values")
        text = _string(str(value), "resource path")
        if text == "~" or text.startswith("~/"):
            text = str(self.home / text[2:])
        elif text.startswith("~"):
            raise OncoTracerError("Use an absolute resource path or ~/; ~user paths are unsupported")
        # Preserve environment symlinks. No recursive symlink resolution or I/O.
        return Path(os.path.abspath(text))

    def record(self, path):
        value = str(path)
        if value in self._seen:
            return
        self._seen.add(value)
        if len(self.searched) < MAX_SEARCHED:
            self.searched.append(value)
        else:
            self.note(f"Search audit list limited to {MAX_SEARCHED} paths.")

    @staticmethod
    def unique(paths):
        return list(dict.fromkeys(paths))

    def file(self, path, *, executable=False):
        try:
            return path.is_file() and path.stat().st_size > 0 and os.access(path, os.R_OK | (os.X_OK if executable else 0))
        except OSError:
            return False

    def children(self, path, *, limit=MAX_CHILDREN):
        key = (str(path), limit)
        if key in self._cache:
            return self._cache[key]
        self.record(path)
        entries = []
        try:
            with os.scandir(path) as handle:
                for entry in islice(handle, limit + 1):
                    entries.append(Path(entry.path))
        except OSError:
            pass
        if len(entries) > limit:
            self.note(f"Only the first {limit} entries were inspected in {path}; use an explicit path if needed.")
        result = sorted(entries[:limit], key=lambda p: p.name.casefold())
        self._cache[key] = result
        return result

    def read_text(self, path):
        self.record(path)
        try:
            if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
                return ""
            with path.open(encoding="utf-8", errors="replace") as handle:
                return handle.read(MAX_TEXT_BYTES)
        except OSError:
            return ""

    def read_json(self, path):
        text = self.read_text(path)
        if not text:
            return {}
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except ValueError:
            self.note(f"No usable saved installation settings in {path}.")
            return {}

    def which(self, names, prefix=None):
        directories = [prefix / "bin"] if prefix else self.path_dirs
        for directory in directories:
            self.record(directory)
            for name in names:
                candidate = directory / name
                if self.file(candidate, executable=True):
                    return candidate
        return None

    def override(self, field, values):
        value = values.get(field) or next((self.env.get(key) for key in ENV_FIELDS.get(field, ()) if self.env.get(key)), None)
        return self.path(value) if value else None

    def prefixes(self):
        choices = []
        for key in ("variants_prefix", "variant_tool_prefix", "ffperase_prefix", "core_prefix"):
            if isinstance(self.config.get(key), str) and self.config[key]:
                choices.append(self.path(self.config[key]))
        for key in ("CONDA_PREFIX", "ONCOTRACER_VARIANTS_PREFIX", "ONCOTRACER_FFPERASE_PREFIX", "ONCOTRACER_CORE_PREFIX"):
            if self.env.get(key):
                choices.append(self.path(self.env[key]))
        for directory in self.path_dirs:
            if directory.name == "bin":
                choices.append(directory.parent)
        for line in self.read_text(self.home / ".conda/environments.txt").splitlines()[:MAX_CHILDREN]:
            if line.strip() and not line.startswith("#"):
                try:
                    choices.append(self.path(line.strip()))
                except OncoTracerError:
                    continue
        bases = [self.home / name for name in ("anaconda3", "miniconda3", "miniforge3", "mambaforge")]
        bases += list(SYSTEM_PREFIXES)
        env_dirs = [base / "envs" for base in bases] + [self.home / ".conda/envs"]
        for root in self.roots:
            env_dirs += [root / "environments", root / "envs", root / "tools/environments", root / "tools/envs"]
            # User-created prefixes commonly live directly under project/tools.
            choices += [p for p in self.children(root / "tools") if (p / "bin").is_dir()]
        # Named managed versions, when present, are shallow and finite.
        data_home = self.path(self.env.get("XDG_DATA_HOME") or str(self.home / ".local/share"))
        for version in self.children(data_home / "oncotracer", limit=16):
            env_dirs.append(version / "envs")
        optional = self.home / ".local/share/oncotracer/optional-tools"
        choices += [optional / name for name in ("variants", "ffperase", "clair3")]
        env_dirs.append(data_home / "oncotracer/optional-tools")
        choices += bases
        for folder in self.unique(env_dirs):
            choices += [p for p in self.children(folder) if p.is_dir()]
        choices = self.unique(choices)
        if len(choices) > MAX_PREFIXES:
            self.note(f"Only the first {MAX_PREFIXES} environment candidates were inspected.")
        return choices[:MAX_PREFIXES]

    def resource_locations(self):
        locations = list(self.roots)
        for root in self.roots:
            for suffix in ("tools", "resources", "models", "containers"):
                folder = root / suffix
                locations.append(folder)
                locations.extend(self.children(folder))
                # One extra named directory, not a recursive traversal.
                if suffix == "tools":
                    locations.extend(self.children(folder / "containers"))
        optional = self.home / ".local/share/oncotracer/optional-tools"
        locations = [optional, *self.children(optional), *locations]
        return self.unique(locations)[:256]

    def existing(self, paths, *, directories=True):
        """Bound stat probes separately from deeper resource inspection."""
        found = []
        for path in self.unique(paths)[:MAX_CANDIDATES]:
            try:
                exists = path.is_dir() if directories else path.is_file()
                if exists:
                    found.append(path)
            except OSError:
                continue
        return found[:MAX_PREFIXES]


def discover_variant_resources(data, *, roots=(), environment=None):
    """Return UI suggestions without running commands, loading models or writing.

    ``environment`` overlays the process environment; null deletes a key. Explicit
    paths (including invalid ones) never fall back to another candidate. ``found``
    means the documented files/executables exist, not that a scientific run passed.
    """
    mode, backend, specimen, callers, values, image = _payload(data)
    search = _Search(environment, roots)
    fields = {k: v for k, v in values.items() if k in PATH_FIELDS and v and not (backend == "docker" and k in DOCKER_IGNORED)}
    resources, missing = [], []
    prefixes = search.prefixes()
    locations = search.resource_locations()
    if backend == "singularity":
        search.note("Variant discovery uses host tools for this backend. OncoTracer has no all-callers Singularity workflow; only supported ClairS-TO and FFPERASE SIF resources are used.")

    def row(identity, label, status, path=None, detail=""):
        resources.append({"id": identity, "label": label, "status": status, "path": str(path) if path else "", "detail": detail})

    def need(identity):
        if identity not in missing:
            missing.append(identity)

    def find_resource(field, label, candidates, predicate, *, candidate=False):
        override = search.override(field, values)
        choices = [override] if override else search.existing(candidates, directories=not field.endswith("_sif"))
        found = []
        for path in choices:
            search.record(path)
            try:
                if predicate(path):
                    found.append(path)
            except OSError:
                continue
        identity = field.removeprefix("variant_")
        if override:
            fields.setdefault(field, str(override))
        if not found:
            row(identity, label, "missing", override, "The supplied path is unavailable or incomplete; it was preserved." if override else "No matching resource in the bounded search locations.")
            return None
        selected = found[0]
        if len(found) == 1 or override:
            fields.setdefault(field, str(selected))
        else:
            search.note(f"Multiple {label} candidates found; choose a path explicitly: " + ", ".join(str(p) for p in found[:4]))
        status = "candidate" if candidate or len(found) > 1 else "found"
        detail = "Matching files exist; versions and compatibility have not been tested."
        if candidate:
            detail = "Candidate only. Model chemistry or container contents have not been verified; review before running."
        if len(found) > 1 and not override:
            detail += " Multiple candidates exist; the form was not filled automatically."
        row(identity, label, status, selected, detail)
        return selected

    ffpe_needed = mode == "illumina" and specimen == "ffpe" and values.get("variant_ffperase", "required") != "off"
    varlociraptor_needed = values.get("variant_varlociraptor") == "required"
    if not specimen:
        search.note("Choose Fresh or FFPE yourself; preservation is never inferred from filenames.")
    if not callers:
        search.note("Select at least one caller to check its required executables.")
    def sif_candidates(fragment):
        files = []
        for base in locations:
            if base.suffix.lower() == ".sif":
                files.append(base)
            if base.name.lower() in {"containers", "singularity", "images"}:
                files.extend(search.children(base))
        return [p for p in files if p.suffix.lower() == ".sif" and fragment in re.sub(r"[^a-z0-9]", "", p.name.lower())]

    # Prefer a complete native environment unless a SIF was selected explicitly.
    sif = None
    if "clairs_to" in callers and backend != "docker":
        sif_override = search.override("variant_clairsto_sif", values)
        tool_override = search.override("variant_tool_prefix", values)
        native_candidates = [tool_override] if tool_override else [None, *prefixes]
        has_native = any(search.which(("run_clairs_to",), prefix) and
                         search.which(("samtools",), prefix) and search.which(("bcftools",), prefix)
                         for prefix in native_candidates)
        if sif_override or not has_native:
            found_sif = find_resource("variant_clairsto_sif", "ClairS-TO SIF", sif_candidates("clairsto"), lambda p: search.file(p), candidate=True)
            sif = found_sif if fields.get("variant_clairsto_sif") else None
            if not found_sif and sif_override:
                need("clairsto_caller")
    required = {"samtools": ("samtools",), "bcftools": ("bcftools",)}
    for caller in callers:
        if caller != "clairs_to" or not sif:
            required[caller] = TOOLS[caller]
    if ffpe_needed:
        required["gatk"] = ("gatk",)
    if varlociraptor_needed:
        required["varlociraptor"] = ("varlociraptor",)
    selected_prefix = search.override("variant_tool_prefix", values)
    if backend == "docker":
        selected_image = image or (search.config.get("image") if isinstance(search.config.get("image"), str) else "")
        docker = search.which(("docker",))
        row("docker_runtime", "Docker executable", "found" if docker else "missing", docker,
            "Executable exists; daemon access has not been tested." if docker else "Docker is not on PATH. No daemon was contacted.")
        row("docker_image", "Docker caller image", "unverified" if selected_image else "missing", selected_image,
            "Local image presence and executables will be checked by Run preflight. Autodetect never pulls or launches containers.")
        if not selected_image or not docker:
            need("docker_image")
        for identity in required:
            row(identity, identity.replace("_", " "), "unverified", detail="Provided by the selected Docker image; host executables are not substituted.")
        if any(values.get(field) for field in DOCKER_IGNORED):
            search.note("Docker ignores host caller/FFPERASE prefixes and SIFs. External source, models and annotation paths remain host resources.")
    else:
        if not selected_prefix and not all(search.which(names) for names in required.values()):
            selected_prefix = next((p for p in prefixes if all(search.which(names, p) for names in required.values())), None)
            if selected_prefix:
                fields["variant_tool_prefix"] = str(selected_prefix)
        elif selected_prefix:
            fields.setdefault("variant_tool_prefix", str(selected_prefix))
        absent = []
        for identity, names in required.items():
            found = search.which(names, selected_prefix)
            row(identity, "/".join(names), "found" if found else "missing", found,
                "Executable file found; it was not run." if found else "Missing in the selected prefix." if selected_prefix else "No executable found on PATH or in a complete candidate environment.")
            if not found:
                absent.append(identity)
                if identity == "clair3": need("clair3_caller")
                elif identity == "clairs_to": need("clairsto_caller")
        if absent:
            need("variant_tools")
        row("variant_tools", "Caller environment", "missing" if absent else "found", selected_prefix,
            ("Missing: " + ", ".join(absent)) if absent else "Requested executable files are present. Dependencies and versions need run preflight.")

    if "clair3" in callers:
        models = []
        for base in search.existing([*locations, *prefixes]):
            for folder in (base / "models", base / "models/clair3", base / "clair3-model", base / "clair3_models"):
                if "clair3" in str(folder).lower():
                    models += [folder, *search.children(folder)]
            if "clair3" in base.name.lower():
                models += [base, *search.children(base)]
        # A model candidate needs checkpoint marker names, not merely any folder.
        def clair_model(path):
            names = [p.name.lower() for p in search.children(path)]
            return any(n.startswith("pileup") for n in names) and any(n.startswith("full_alignment") for n in names)
        model = find_resource("variant_clair3_model", "Clair3 model", models,
                              (lambda p: p.is_dir() and bool(search.children(p))) if values.get("variant_clair3_model") else clair_model,
                              candidate=True)
        if not model: need("clair3_model")
    else:
        row("clair3_model", "Clair3 model", "not_needed", detail="Clair3 is not selected.")
    if "clairs_to" in callers:
        row("clairsto_model", "ClairS-TO chemistry preset", "unverified" if values.get("variant_clairsto_platform") else "missing",
            detail=("User-supplied preset: " + values["variant_clairsto_platform"] + ". Compatibility has not been checked.") if values.get("variant_clairsto_platform") else "Select the preset matching the sequencing chemistry and basecaller; autodetection never guesses it.")
        if not values.get("variant_clairsto_platform"): need("clairsto_model")

    if ffpe_needed:
        cached = search.home / ".nextflow/assets/.repos/papaemmelab/nf-ffperase/clones" / FFPERASE_REVISION
        source_candidates = [cached]
        for base in locations:
            source_candidates += [base / "nf-ffperase", base / "FFPErase", base / "ffperase"]
            if "ffperase" in base.name.lower(): source_candidates.append(base)
        source = find_resource("variant_ffperase_root", "FFPERASE source", source_candidates,
                               lambda p: all(search.file(p / "bin" / name) for name in ("annotate_w_pileup", "annotate_variants.py", "classify_w_random_forest.py", "microrep_python3.py")))
        if not source: need("ffperase_source")
        model_candidates = [p / name for p in locations for name in ("ffperase-models", "ffperase_models", "ffperase/models")]
        model_candidates += [p for p in locations if "ffperase" in p.name.lower()]
        if source: model_candidates = [source / "models", source / "model", *model_candidates]
        model = find_resource("variant_ffperase_models", "FFPERASE models", model_candidates,
                              lambda p: all(search.file(p / f"model.{kind}.joblib") for kind in ("snvs", "indels")))
        if not model: need("ffperase_models")
        if backend == "docker":
            row("ffperase_runtime", "FFPERASE runtime", "unverified", detail="The Docker image supplies this runtime; external source/models are still required.")
        else:
            ffpe_sif_override = search.override("variant_ffperase_sif", values)
            if ffpe_sif_override:
                ffpe_sif = find_resource("variant_ffperase_sif", "FFPERASE SIF", [], lambda p: search.file(p), candidate=True)
                if ffpe_sif: sif = ffpe_sif
                else: need("ffperase_runtime")
            else:
                named = [p for p in prefixes if "ffperase" in p.name.lower() and search.file(p / "bin/python", executable=True)]
                specialized = named or [p for p in prefixes if "ffpe" in p.name.lower()]
                prefix_override = search.override("variant_ffperase_prefix", values)
                native = [p for p in specialized if search.file(p / "bin/python", executable=True)]
                possible_sifs = sif_candidates("ffperase")
                if prefix_override or native or not possible_sifs:
                    prefix = find_resource("variant_ffperase_prefix", "FFPERASE runtime", specialized,
                                           lambda p: search.file(p / "bin/python", executable=True), candidate=True)
                    if not prefix: need("ffperase_runtime")
                else:
                    found_sif = find_resource("variant_ffperase_sif", "FFPERASE SIF", possible_sifs, lambda p: search.file(p), candidate=True)
                    if found_sif and fields.get("variant_ffperase_sif"): sif = found_sif
                    if not found_sif: need("ffperase_runtime")
    else:
        row("ffperase", "FFPERASE", "not_needed", detail="Only requested for Illumina FFPE; choose preservation explicitly and enable the assessment to search its resources.")
    if sif:
        runtime = search.which(("apptainer", "singularity"))
        row("container_runtime", "Apptainer / Singularity", "found" if runtime else "missing", runtime, "Required to execute the selected SIF. No container was opened.")
        if not runtime: need("container_runtime")

    for field, label in (("variant_targets_bed", "Target BED"), ("variant_varlociraptor_scenario", "Varlociraptor scenario")):
        if values.get(field):
            find_resource(field, label, [], lambda p: search.file(p))

    _annovar(search, values, locations, fields, row, need, backend=backend)
    recipe_root = next((p for p in [Path(__file__).absolute().parent.parent, *search.roots]
                        if (p / "environments/native-variants.yml").is_file()), None)
    from .variant_install_help import installation_guides
    guides = installation_guides(missing, backend=backend, mode=mode, root=recipe_root)
    return {"backend": backend, "fields": fields, "resources": resources, "install_guides": guides,
            "searched": search.searched, "notes": ["Read-only discovery: no executable, model, installer, download or analysis was run. Review suggestions before saving.",
            "Only common installation folders and a bounded set of their children were checked. For resources elsewhere, enter the exact path and select Autodetect resources again.", *search.notes]}


def _annovar(search, values, locations, fields, row, need, *, backend):
    if values.get("variant_annovar") == "off":
        row("annovar", "ANNOVAR", "not_needed", detail="Annotation is disabled.")
        return
    from .annovar import discover_annovar
    override = search.override("variant_annovar_dir", values)
    db_override = search.override("variant_annovar_db", values)
    for field, path in (("variant_annovar_dir", override), ("variant_annovar_db", db_override)):
        if path: fields.setdefault(field, str(path))
    on_path = search.which(("table_annovar.pl",))
    choices = [override] if override else search.unique(([on_path.parent] if on_path else []) + [search.home / "annovar"] + [p / "annovar" for p in locations] + [p for p in locations if p.name.lower() == "annovar"])
    build = values.get("variant_reference_build") or "hg38"
    detail = "No complete local ANNOVAR installation and matching unpacked RefSeq pair found."
    unverified = None
    for install in ([override] if override else search.existing(choices)):
        search.record(install)
        scripts = [install / name for name in ("table_annovar.pl", "annotate_variation.pl", "convert2annovar.pl", "coding_change.pl")]
        if not all(search.file(p, executable=True) for p in scripts):
            continue
        try:
            oversized = any(p.stat().st_size > 1024 * 1024 for p in scripts)
        except OSError:
            continue
        if oversized:
            unverified = install
            detail = "ANNOVAR helper exceeds the bounded inspection size; supply and validate resources manually."
            continue
        database = db_override or install / "humandb"
        gene = next((name for name in ("refGeneWithVer", "refGene") if all(search.file(database / filename) for filename in (f"{build}_{name}.txt", f"{build}_{name}Mrna.fa"))), None)
        if not gene:
            detail = f"Missing complete unpacked {build} RefSeq TXT and Mrna FASTA pair in {database}."
            continue
        clinvar = sorted((p.stem[len(build) + 1:] for p in search.children(database) if re.fullmatch(re.escape(build) + r"_clinvar_\d{8}\.txt", p.name) and search.file(p)), reverse=True)
        protocols = [gene, *clinvar[:1]]
        # Explicit protocols prevent a second unbounded database glob. Large DBs
        # receive stat-only provenance in discover_annovar; no models are loaded.
        # Preserve removed environment keys and the bounded PATH when calling
        # the existing helper, whose environment argument is itself an overlay.
        env = {key: None for key in os.environ if key not in search.env}
        env.update(search.env)
        env["PATH"] = os.pathsep.join(str(p) for p in search.path_dirs)
        try:
            detection = discover_annovar(build, annovar_dir=install, database_dir=database,
                                         environment=env, protocols=protocols)
        except (OSError, RuntimeError) as error:
            detail = f"Local resources could not be inspected: {error}"
            continue
        if backend == "docker" and not detection.available and not search.which(("perl",)):
            fields.setdefault("variant_annovar_dir", str(install))
            fields.setdefault("variant_annovar_db", str(database))
            row("annovar", "ANNOVAR", "candidate", install,
                "Executable helpers and the matching RefSeq pair exist. Container Perl and full annotation compatibility require run preflight.")
            return
        detail = detection.reason
        if detection.available:
            fields.setdefault("variant_annovar_dir", str(install))
            fields.setdefault("variant_annovar_db", str(database))
            row("annovar", "ANNOVAR", "found", install, "Matching local resources: " + ", ".join(detection.protocols) + ". No annotation or download was run.")
            return
    row("annovar", "ANNOVAR", "unverified" if unverified else "missing", override or unverified,
        detail + (" Explicit installation/database overrides were preserved." if override or db_override else ""))
    need("annovar")
