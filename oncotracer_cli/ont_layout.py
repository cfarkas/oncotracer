"""Resolve explicit ONT library folders without pooling runs or failed reads."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .runtime import OncoTracerError, require_directory

FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_BARCODE = re.compile(r"barcode\d+", re.I)
_IGNORED = {"fastq_fail", "pod5_fail", "bam_fail", "fast5_fail", "sequencing_summary",
            "pod5_pass", "pod5", "bam_pass", "modified_bam", "modbam", "fast5_pass"}


def is_barcode(name: str) -> bool:
    return name == "unclassified" or _BARCODE.fullmatch(name) is not None


def pass_fastq_files(folder: Path, *, allow_empty: bool = False) -> tuple[Path, ...]:
    """List completed read files; never descend into a failed-read or signal tree."""
    paths, identities = [], {}

    def fail(error: OSError) -> None:
        raise OncoTracerError(f"Cannot inspect ONT FASTQ folder: {error}") from error

    for parent, directories, filenames in os.walk(folder, onerror=fail, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if name not in _IGNORED and not name.endswith("_fail"))
        for name in sorted(filenames):
            if not name.lower().endswith(FASTQ_SUFFIXES):
                continue
            path = Path(parent) / name
            if not path.is_file() or path.stat().st_size == 0:
                if allow_empty:
                    continue
                raise OncoTracerError(f"FASTQ is missing or empty: {path}")
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in identities:
                raise OncoTracerError(f"The same FASTQ appears more than once: {identities[identity]} and {path}")
            identities[identity] = path
            paths.append(path)
    return tuple(sorted(paths))


@dataclass(frozen=True)
class NamedOntSample:
    name: str
    specimen_root: Path
    run_root: Path
    fastq_root: Path
    fastq_dir: Path
    barcode: str
    files: tuple[Path, ...]


def _directories(folder: Path) -> list[Path]:
    try:
        paths = []
        with os.scandir(folder) as entries:
            for index, entry in enumerate(entries):
                if index >= 4096:
                    raise OncoTracerError(f"Too many entries in {folder}; select a narrower ONT cohort folder.")
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    paths.append(Path(entry.path))
        return sorted(paths)
    except OSError as error:
        raise OncoTracerError(f"Cannot inspect ONT library folder {folder}: {error}") from error


def _pass_roots(specimen: Path) -> list[Path]:
    roots = []
    for child in _directories(specimen):
        if child.name == "fastq_pass":
            roots.append(child)
        elif child.name not in _IGNORED and not child.name.endswith("_fail"):
            candidate = child / "fastq_pass"
            if candidate.is_dir() and not candidate.is_symlink():
                roots.append(candidate)
    return roots


def resolve_named_ont_sample(specimen_root: str | Path) -> NamedOntSample:
    """One specimen is one direct or single-run pass library, never an implicit pool."""
    specimen = require_directory(Path(specimen_root), "Named ONT library folder")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", specimen.name):
        raise OncoTracerError(f"Invalid named ONT folder {specimen.name!r}; use letters, digits, dot, underscore, or dash.")
    roots = _pass_roots(specimen)
    if len(roots) != 1:
        raise OncoTracerError(f"Named ONT library {specimen.name} requires exactly one fastq_pass "
                             f"directly or under one run folder; found {len(roots)}. "
                             "Keep distinct libraries in separately named folders; do not pool runs implicitly.")
    fastq = roots[0]
    files = pass_fastq_files(fastq)
    if not files:
        raise OncoTracerError(f"No FASTQ files found in {fastq}. Check your paths.")
    barcode_names = {path.relative_to(fastq).parts[0] for path in files
                     if len(path.relative_to(fastq).parts) > 1 and is_barcode(path.relative_to(fastq).parts[0])}
    if any("fastq_pass" in path.relative_to(fastq).parts[:-1] for path in files):
        raise OncoTracerError(f"Named ONT library {specimen.name} contains a nested fastq_pass; "
                             "keep one pass library per named folder.")
    nested = [path for path in files
              if any(is_barcode(part) for part in path.relative_to(fastq).parts[1:-1])]
    if nested or len(barcode_names) > 1:
        raise OncoTracerError(f"Named ONT library {specimen.name} contains multiple or nested barcodes; "
                             "choose one barcode per named library or select the legacy fastq_pass folder explicitly.")
    barcode = next(iter(barcode_names), ".")
    if barcode != "." and any(path.relative_to(fastq).parts[0] != barcode for path in files):
        raise OncoTracerError(f"Named ONT library {specimen.name} mixes barcode and unbarcoded FASTQs; review its pass reads.")
    directory = fastq / barcode if barcode != "." else fastq
    return NamedOntSample(specimen.name, specimen, fastq.parent, fastq, directory, barcode, files)


def discover_named_ont_samples(root: str | Path, *, explicit: bool = False) -> tuple[NamedOntSample, ...] | None:
    """Recognize immediate named libraries; retain automatic single-run legacy semantics."""
    root = require_directory(Path(root), "ONT cohort folder")
    if root.name == "fastq_pass" or is_barcode(root.name):
        if explicit:
            raise OncoTracerError("Named ONT mode requires the cohort parent containing named library folders.")
        return None
    candidates = [child for child in _directories(root)
                  if child.name != "fastq_pass" and not is_barcode(child.name)
                  and child.name not in _IGNORED and not child.name.endswith("_fail") and _pass_roots(child)]
    if (root / "fastq_pass").is_dir():
        if candidates:
            raise OncoTracerError("ONT input mixes a direct fastq_pass with named library folders; "
                                 "select the intended fastq_pass explicitly or separate the named cohort.")
        if explicit:
            raise OncoTracerError("Named ONT mode requires the cohort parent containing named library folders.")
        return None
    if not candidates or (len(candidates) == 1 and not explicit):
        if explicit and not candidates:
            raise OncoTracerError(f"No named ONT library folders with fastq_pass found in {root}.")
        return None
    if any(is_barcode(child.name) for child in _directories(root)) or any(
        path.is_file() and path.name.lower().endswith(FASTQ_SUFFIXES) for path in root.iterdir()
    ):
        raise OncoTracerError("ONT input mixes named libraries with loose FASTQs or legacy barcode folders; "
                             "choose one layout or select the intended library folder explicitly.")
    samples = tuple(resolve_named_ont_sample(child) for child in candidates)
    identities = {}
    for sample in samples:
        for path in sample.files:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in identities:
                raise OncoTracerError(f"The same FASTQ belongs to multiple named ONT libraries: {identities[identity]} and {path}")
            identities[identity] = path
    return samples
