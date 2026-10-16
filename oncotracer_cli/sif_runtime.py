"""Isolated Apptainer/Singularity commands and explicitly selected reads."""
from __future__ import annotations

import csv
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .fastq_inputs import fastq_field_values, fastq_paths
from .runtime import OncoTracerError, load_flat_yaml


def _bind_path(path: Path) -> Path:
    path = Path(os.path.abspath(os.fspath(path)))
    if any(character in str(path) for character in (",", ":", "\n", "\r")):
        raise OncoTracerError(f"Singularity bind path contains a separator: {path}")
    if path in {Path("/"), Path("/opt"), Path("/opt/conda"), Path("/opt/oncotracer")}:
        raise OncoTracerError(f"Singularity mount would obscure the container runtime: {path}")
    return path


@contextmanager
def isolated_sif_command(
    executable: str,
    *,
    mounts: Sequence[tuple[Path, str]] = (),
    cwd: Path | None = None,
) -> Iterator[list[str | Path]]:
    """Keep host GUI variables and personal installation settings out of SIF.

    --no-home alone is insufficient when HOME is also the working directory or
    a selected data parent. The final private bind and explicit HOME/XDG values
    isolate configuration even in those cases.
    """
    selected = [(_bind_path(path), access) for path, access in mounts]
    with tempfile.TemporaryDirectory(prefix="oncotracer-sif-") as temporary:
        private = _bind_path(Path(temporary))
        destination = Path("/tmp") / private.name
        environment = {
            "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
            "XDG_DATA_HOME": "data", "MPLCONFIGDIR": "matplotlib",
        }
        for name in environment.values():
            (private / name).mkdir()
        command: list[str | Path] = [executable, "exec", "--cleanenv", "--no-home"]
        for path, access in sorted(selected, key=lambda item: (len(item[0].parts), str(item[0]))):
            command.extend(["--bind", f"{path}:{path}:{access}"])
        # Always last: a legitimate data parent such as /tmp or HOME must not
        # shadow the private settings directory.
        command.extend(["--bind", f"{private}:{destination}:rw"])
        # Apptainer ignores --env HOME=...; --home is its supported override.
        command.extend(["--home", f"{private / 'home'}:{destination / 'home'}"])
        for key, name in environment.items():
            if key != "HOME":
                command.extend(["--env", f"{key}={destination / name}"])
        command.extend(["--pwd", cwd if cwd is not None else destination / "home"])
        yield command


def sif_read_mounts(config_path: Path) -> list[tuple[Path, str]]:
    """Preserve lexical read paths and resolved targets outside project binds."""
    from .engine import parse_illumina_samplesheet, parse_ont_samples

    config = load_flat_yaml(config_path)
    paths: set[Path] = set()

    def explicit(value: object, label: str) -> None:
        if str(value or "").startswith("~"):
            raise OncoTracerError(
                f"Singularity path {label} starts with '~'; use its absolute host path "
                "or a path relative to the current working directory. The saved YAML is unchanged."
            )

    for key in ("lpwgs_root", "outdir", "illumina_samplesheet", "ont_folder",
                "ont_normal_folder", "pathology_csv"):
        explicit(config.get(key), key)

    def add(value: object) -> None:
        path = _bind_path(Path(str(value)).absolute())
        paths.add(path)
        paths.add(_bind_path(path.resolve()))

    # Keep lexical config-resource paths too: resolving a symlinked samplesheet
    # in the parent mount planner must not hide the spelling used in the YAML.
    for key in ("illumina_samplesheet", "ont_folder", "ont_normal_folder", "pathology_csv"):
        if config.get(key):
            add(config[key])
    mode = str(config.get("mode", "")).lower()
    if mode == "illumina":
        sheet = Path(str(config.get("illumina_samplesheet", "")))
        with sheet.open(newline="", encoding="utf-8") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=2):
                for key in ("fastq_1", "fastq_2"):
                    for value in fastq_field_values(row.get(key) or ""):
                        explicit(value, f"{key} in samplesheet row {number}")
                        add(value)
        for sample in parse_illumina_samplesheet(sheet):
            for path in fastq_paths(sample.fastq_1) + fastq_paths(sample.fastq_2):
                add(path)
    elif mode == "ont":
        pending = []
        for sample in parse_ont_samples(config):
            add(sample.fastq_dir)
            pending.append(sample.fastq_dir.resolve())
        scanned: set[Path] = set()
        while pending:
            directory = pending.pop()
            if directory in scanned:
                continue
            if len(scanned) >= 4096:
                raise OncoTracerError("Singularity reads exceed 4096 symlink target directories")
            scanned.add(directory)
            for child in directory.rglob("*"):
                if child.is_symlink() and child.exists():
                    add(child)
                    if child.is_dir():
                        pending.append(child.resolve())
    if config.get("outdir"):
        output = Path(str(config["outdir"])).absolute()
        for source in paths:
            if any(candidate == source or candidate in source.parents
                   or (source.is_dir() and source in candidate.parents)
                   for candidate in (output, output.resolve())):
                raise OncoTracerError(
                    f"Singularity output overlaps a read-only input: {source}; "
                    "choose a separate output directory"
                )
    return [(path, "ro") for path in sorted(paths, key=lambda item: (len(item.parts), str(item)))]


def sif_project_alias_mounts(config_path: Path) -> list[tuple[Path, str]]:
    """Expose directory symlink entries without changing output ownership."""
    config = load_flat_yaml(config_path)
    parents: set[Path] = set()
    for key in ("lpwgs_root", "outdir"):
        if not config.get(key):
            continue
        path = Path(str(config[key])).absolute()
        for component in (path, *path.parents):
            if component.is_symlink():
                # Bind its lexical parent, keeping the symlink itself visible.
                # The existing project planner also binds the physical target.
                parents.add(_bind_path(component.parent))
    return [(path, "rw") for path in sorted(parents)]
