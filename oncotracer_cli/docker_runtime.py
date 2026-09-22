"""Docker input binding and early validation for integrated FASTQ workflows.

Input YAML and host environments are never rewritten. Only explicitly selected
data/resources are bound; tool environments are supplied by the image itself.
"""
from __future__ import annotations

import os
import csv
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Mapping

from .runtime import OncoTracerError, load_flat_yaml
from .variant_model_assets import is_auto_resource


_HOST_PREVIEW = ContextVar("oncotracer_docker_host_preview", default=False)


@contextmanager
def docker_host_preview():
    """Plan Docker inputs without resolving image executables on the host."""
    token = _HOST_PREVIEW.set(True)
    try:
        yield
    finally:
        _HOST_PREVIEW.reset(token)


def variants_enabled(config: Mapping[str, object]) -> bool:
    return str(config.get("run_variants", False)).strip().lower() in {"true", "1", "yes", "on"}


def validate_docker_variants(config: Mapping[str, object]) -> None:
    """Validate user resources without requiring host-installed caller tools."""
    if not variants_enabled(config):
        return
    # HOME is intentionally private inside the image. Reject host-home syntax
    # before downloads or output creation instead of resolving it to /tmp there.
    def reject_home(value, label):
        if str(value or "").startswith("~"):
            raise OncoTracerError(
                f"Docker path {label} starts with '~'; use its absolute host path "
                "or a path relative to the current working directory. The saved YAML is unchanged."
            )
    for key in ("lpwgs_root", "outdir", "illumina_samplesheet", "ont_folder", "ont_normal_folder",
                "pathology_csv", "variant_targets_bed", "variant_clair3_model",
                "variant_annovar_dir", "variant_annovar_db", "variant_ffperase_root",
                "variant_ffperase_models", "variant_varlociraptor_scenario"):
        reject_home(config.get(key), key)
    sheet = config.get("illumina_samplesheet")
    if str(config.get("mode", "")).lower() == "illumina" and sheet and Path(str(sheet)).is_file():
        with Path(str(sheet)).open(newline="") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=2):
                for key in ("fastq_1", "fastq_2"):
                    reject_home(row.get(key), f"{key} in samplesheet row {number}")
    for key in ("variant_clairsto_sif", "variant_ffperase_sif"):
        if config.get(key):
            raise OncoTracerError(
                f"Docker uses image-native callers; clear {key} to use Docker, "
                "or select the host/Conda backend for this SIF image"
            )
    for key in ("variant_ffperase_root", "variant_ffperase_models", "variant_annovar_dir", "variant_annovar_db"):
        if config.get(key) and not is_auto_resource(key, config[key]) and not Path(str(config[key])).expanduser().is_dir():
            raise OncoTracerError(f"{key} must be an existing host directory for Docker")
    from .variants import resolve_variant_request
    with docker_host_preview():
        resolve_variant_request(config, mode=str(config.get("mode", "")).lower())


def container_variant_config(config: Mapping[str, object]) -> dict[str, object]:
    """Resolve native image prefixes in memory, retaining the saved YAML hash."""
    result = dict(config)
    if _HOST_PREVIEW.get():
        result["_docker_skip_host_tools"] = True
    elif os.environ.get("ONCOTRACER_CONTAINER_RUNTIME") == "docker":
        result["variant_tool_prefix"] = os.environ.get("ONCOTRACER_VARIANTS_PREFIX", "/opt/oncotracer-envs/variants")
        result["variant_ffperase_prefix"] = os.environ.get("ONCOTRACER_FFPERASE_PREFIX", "/opt/oncotracer-envs/ffperase")
    return result


def docker_resource_environment(config: Mapping[str, object]) -> dict[str, str]:
    """Carry autodetected optional host assets into the image, never host tools."""
    result = {"ONCOTRACER_CONTAINER_RUNTIME": "docker"}
    if not variants_enabled(config):
        return result
    for key in ("ONCOTRACER_FFPERASE_ROOT", "ONCOTRACER_FFPERASE_MODELS"):
        if os.environ.get(key):
            result[key] = str(Path(os.environ[key]).expanduser().resolve())
    if str(config.get("variant_annovar", "auto")).lower() == "auto":
        from .annovar import discover_annovar
        found = discover_annovar(
            str(config.get("variant_reference_build") or "hg38"),
            annovar_dir=config.get("variant_annovar_dir"),
            database_dir=config.get("variant_annovar_db"),
        )
        if found.available:
            result["ANNOVAR_HOME"] = str(found.table_annovar.parent)
            result["ANNOVAR_DB"] = str(found.database_dir)
    return result


def docker_mounts(config_path: Path, *, environment: Mapping[str, str], create: bool = False) -> list[tuple[Path, str]]:
    """Return read-only inputs plus writable output and reference cache.

    The output directory is precreated empty; ownership remains with the native
    runner. Input files remain read-only even when outputs share their parent.
    Both lexical and resolved paths are preserved for symlinked datasets.
    """
    from .engine import parse_illumina_samplesheet, parse_ont_samples, _fastq_files
    config = load_flat_yaml(config_path)
    mounts: dict[Path, str] = {}
    inputs: set[Path] = set()
    resource_directories: list[Path] = []
    scanned_resources: set[Path] = set()

    def absolute(value):
        return Path(os.path.abspath(os.fspath(Path(str(value)).expanduser())))

    def add(value, access="ro", *, resource=False):
        path = absolute(value)
        for item in (path, path.resolve()):
            if any(c in str(item) for c in (":", "\n", "\r")):
                raise OncoTracerError(f"Docker bind path contains a separator: {item}")
            if item == Path("/") or item == Path("/opt") or item == Path("/opt/conda") or item == Path("/opt/oncotracer"):
                raise OncoTracerError(f"Docker mount would obscure the container runtime: {item}")
            mounts[item] = access
            if resource:
                inputs.add(item)
        if resource and path.is_dir():
            resource_directories.append(path.resolve())

    add(Path.cwd())
    add(config_path.parent)
    add(config_path, resource=True)
    project_value = config.get("lpwgs_root")
    if not project_value:
        raise OncoTracerError("Docker runs require an explicit lpwgs_root for persistent references")
    project = absolute(project_value)
    out_value = config.get("outdir")
    if not out_value:
        raise OncoTracerError("config requires outdir")
    outdir = absolute(out_value)
    add(project)
    add(outdir, "rw")
    cache = project / ".oncotracer"
    add(cache, "rw")
    for key in ("illumina_samplesheet", "ont_folder", "ont_normal_folder", "pathology_csv",
                "variant_targets_bed", "variant_clair3_model", "variant_annovar_dir", "variant_annovar_db",
                "variant_ffperase_root", "variant_ffperase_models", "variant_varlociraptor_scenario"):
        if config.get(key) and not is_auto_resource(key, config[key]):
            add(config[key], resource=True)
    for key in ("ANNOVAR_HOME", "ANNOVAR_DB", "ONCOTRACER_FFPERASE_ROOT", "ONCOTRACER_FFPERASE_MODELS"):
        if environment.get(key):
            add(environment[key], resource=True)
    reference = project / "references" / "samurai_hg38"
    if reference.exists():
        add(reference, resource=True)
    mode = str(config.get("mode", "")).lower()
    if mode == "illumina":
        # Keep the spelling used in the CSV as well as the validated real path.
        # This preserves relative paths and symlinks outside the project tree.
        with Path(str(config.get("illumina_samplesheet", ""))).open(newline="") as handle:
            for row in csv.DictReader(handle):
                for key in ("fastq_1", "fastq_2"):
                    if row.get(key):
                        add(row[key], resource=True)
        for sample in parse_illumina_samplesheet(Path(str(config.get("illumina_samplesheet", "")))):
            add(sample.fastq_1, resource=True)
            if sample.fastq_2:
                add(sample.fastq_2, resource=True)
    elif mode == "ont":
        for sample in parse_ont_samples(config):
            add(sample.fastq_dir, resource=True)
            for path in _fastq_files(sample.fastq_dir, 0):
                if path.is_symlink():
                    add(path, resource=True)
    # Path.rglob deliberately does not descend into directory symlinks. Queue
    # their physical targets explicitly so nested model/checkpoint symlinks also
    # remain visible. Scan only selected resource trees, each physical tree once.
    while resource_directories:
        directory = resource_directories.pop()
        if any(directory == prior or prior in directory.parents for prior in scanned_resources):
            continue
        if len(scanned_resources) >= 4096:
            raise OncoTracerError("Docker resources exceed 4096 symlink target directories; use a smaller resource directory")
        scanned_resources.add(directory)
        for child in directory.rglob("*"):
            if child.is_symlink():
                if not child.exists():
                    continue
                add(child, resource=True)
    for source in inputs:
        if outdir == source or outdir in source.parents or (source.is_dir() and source in outdir.parents):
            raise OncoTracerError(f"Docker output directory overlaps a read-only input/resource: {source}")
    if create:
        from .output_safety import inspect_output_target
        inspect_output_target(outdir)
        for directory in (project, outdir, cache):
            if directory.is_symlink():
                raise OncoTracerError(f"Docker writable directory must not be a symlink: {directory}")
            directory.mkdir(parents=True, exist_ok=True)
    return sorted(mounts.items(), key=lambda item: (len(item[0].parts), str(item[0])))


def preflight(config_path: str) -> None:
    """Executed inside Docker before starting alignment or CNA analysis."""
    from .variants import resolve_variant_request, preflight_variant_tools
    config = load_flat_yaml(Path(config_path))
    request = resolve_variant_request(config, mode=str(config.get("mode", "")).lower())
    if request:
        preflight_variant_tools(request)
        if request.ffperase != "off":
            from .variant_model_assets import ffperase_pending, preflight_ffperase_runtime
            if ffperase_pending(request):
                # Alignment preflight remains read-only. Fetch resources later
                # under the authenticated variant output directory at RUN.
                preflight_ffperase_runtime(request)
            else:
                from .ffperase import discover
                discover(request.ffperase_root, request.ffperase_models, request.ffperase_sif, request.ffperase_prefix)
    print("Docker variant caller and resource preflight passed.")
