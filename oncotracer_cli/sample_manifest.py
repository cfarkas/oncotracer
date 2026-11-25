"""One explicit sample table for launch, native execution and Docker binding."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .runtime import OncoTracerError, require_directory, require_file


@dataclass(frozen=True)
class ManifestSample:
    sample: str
    status: str
    mode: str
    fastq_1: tuple[Path, ...] = ()
    fastq_2: tuple[Path, ...] = ()
    fastq_dir: Path | None = None
    library: Path | None = None
    tissue: str = ""
    pod5: Path | None = None
    modbam: Path | None = None
    annotations: dict[str, str] = field(default_factory=dict)


def _path(value, base):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def read_manifest(path, *, mode=None, methylation=False, caller="rockfish"):
    """Resolve paths against the table; never infer biological roles from names."""
    from .engine import _safe_sample
    from .fastq_inputs import fastq_field_values
    from .ont_layout import pass_fastq_files, resolve_named_ont_sample
    from .methylation_routing import classifier_for_tissue

    path = require_file(Path(path), "Sample table")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise OncoTracerError("Sample table has duplicate column names")
        if not {"sample", "status"}.issubset(fields):
            raise OncoTracerError("Sample table requires sample and status (tumor/normal) columns")
        inferred = "illumina" if "fastq_1" in fields else "ont" if {"library", "fastq_dir"}.intersection(fields) else None
        if inferred is None or (mode and mode != inferred):
            raise OncoTracerError("Use fastq_1,fastq_2 columns for Illumina or library/fastq_dir for ONT; do not mix platforms")
        if "fastq_1" in fields and {"library", "fastq_dir"}.intersection(fields):
            raise OncoTracerError("Sample table cannot mix Illumina and ONT input columns")
        rows = list(reader)
    if not rows:
        raise OncoTracerError("Sample table has no samples")
    samples, names, read_owners, signal_owners = [], set(), {}, {}
    for number, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise OncoTracerError(f"Wrong column count in sample table row {number}")
        sample = _safe_sample(row["sample"])
        if sample in names:
            raise OncoTracerError(f"Duplicate sample ID: {sample}")
        names.add(sample)
        status = row["status"].strip().lower()
        if status not in {"tumor", "normal"}:
            raise OncoTracerError(f"Sample {sample}: status must be tumor or normal")
        values = dict(sample=sample, status=status, mode=inferred, tissue=row.get("tissue", "").strip())
        if row.get("analysis_role", "").strip() not in {"", status}:
            raise OncoTracerError(f"Sample {sample}: analysis_role conflicts with status")
        input_fields = {"sample", "status", "analysis_role", "library", "fastq_dir", "fastq_1", "fastq_2", "tissue", "pod5", "modbam"}
        values["annotations"] = {key: value for key, value in row.items() if key not in input_fields}
        if inferred == "illumina":
            if methylation:
                raise OncoTracerError("Methylation requires ONT signal; this is an Illumina table")
            for key in ("fastq_1", "fastq_2"):
                values[key] = tuple(require_file(_path(value, path.parent), f"{key} for {sample}")
                                    for value in fastq_field_values(row.get(key, ""))) if row.get(key, "").strip() else ()
            if not values["fastq_1"]:
                raise OncoTracerError(f"Sample {sample}: fastq_1 is required")
            if values["fastq_2"] and len(values["fastq_1"]) != len(values["fastq_2"]):
                raise OncoTracerError(f"Sample {sample}: paired FASTQ lane counts differ")
            files = values["fastq_1"] + values["fastq_2"]
        else:
            library_value, directory_value = row.get("library", "").strip(), row.get("fastq_dir", "").strip()
            if bool(library_value) == bool(directory_value):
                raise OncoTracerError(f"Sample {sample}: supply exactly one library or fastq_dir")
            layout = None
            if library_value:
                library = _path(library_value, path.parent)
                layout = resolve_named_ont_sample(library)
                values.update(library=library, fastq_dir=layout.fastq_dir)
                files = layout.files
            else:
                directory = require_directory(_path(directory_value, path.parent), f"FASTQ folder for {sample}")
                if any(part.endswith("_fail") for part in directory.parts):
                    raise OncoTracerError(f"Sample {sample}: select passed reads, not a failed-read folder")
                values["fastq_dir"] = directory
                files = pass_fastq_files(directory)
                barcodes = {next((part.casefold() for part in source.relative_to(directory).parts[:-1]
                                  if re.fullmatch(r"barcode\d+|unclassified", part, re.I)), ".")
                            for source in files}
                if len(barcodes) > 1:
                    raise OncoTracerError(f"Sample {sample}: fastq_dir contains multiple barcodes; give each library its own row")
            if not files:
                raise OncoTracerError(f"Sample {sample}: no passed FASTQs found")
            if methylation:
                if classifier_for_tissue(values["tissue"]) is None:
                    raise OncoTracerError(f"Sample {sample}: supply a supported tissue (blood, marrow or CNS); no diagnosis is inferred")
                key = "pod5" if caller == "rockfish" else "modbam"
                supplied = row.get(key, "").strip()
                if supplied:
                    signal = _path(supplied, path.parent)
                elif layout and caller == "rockfish":
                    signal = layout.run_root / "pod5_pass"
                    if layout.barcode != ".":
                        signal = signal / layout.barcode
                else:
                    raise OncoTracerError(f"Sample {sample}: provide {key} in the sample table")
                if any(part.endswith("_fail") for part in signal.parts):
                    raise OncoTracerError(f"Sample {sample}: failed-read signal is not accepted")
                if key == "pod5":
                    require_directory(signal, f"POD5 for {sample}")
                    from .methylation import _pod5_files
                    signal_files = _pod5_files(signal)
                    if not signal_files:
                        raise OncoTracerError(f"Sample {sample}: no POD5 files in {signal}")
                else:
                    require_file(signal, f"Modified BAM for {sample}")
                    signal_files = (signal,)
                for source in signal_files:
                    stat = source.stat(); identity = (stat.st_dev, stat.st_ino)
                    if identity in signal_owners:
                        raise OncoTracerError(f"Signal is shared by {signal_owners[identity]} and {sample}; assign each source once")
                    signal_owners[identity] = sample
                values[key] = signal
        for source in files:
            stat = source.stat(); identity = (stat.st_dev, stat.st_ino)
            if stat.st_size == 0:
                raise OncoTracerError(f"Empty FASTQ: {source}")
            if identity in read_owners:
                raise OncoTracerError(f"FASTQ is reused by {read_owners[identity]} and {sample}: {source}")
            read_owners[identity] = sample
        samples.append(ManifestSample(**values))
    if inferred == "illumina" and len({bool(s.fastq_2) for s in samples}) != 1:
        raise OncoTracerError("Separate single-end and paired-end Illumina samples into different projects")
    return tuple(samples)


def parse_ont_manifest(path):
    from .engine import OntSample
    return [OntSample(s.sample, s.sample, s.fastq_dir, status=s.status, specimen_root=s.library)
            for s in read_manifest(path, mode="ont")]
