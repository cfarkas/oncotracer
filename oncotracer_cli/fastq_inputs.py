"""Samplesheet fields for one FASTQ or an ordered list of lane FASTQs."""
from __future__ import annotations

import json
from pathlib import Path

from .runtime import OncoTracerError, require_file

FastqInput = Path | tuple[Path, ...]


def fastq_paths(value: FastqInput | None) -> tuple[Path, ...]:
    return () if value is None else (value,) if isinstance(value, Path) else value


def encode_fastq_field(value: FastqInput | None) -> str:
    paths = fastq_paths(value)
    return json.dumps([str(path) for path in paths]) if len(paths) > 1 else str(paths[0]) if paths else ""


def fastq_field_values(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not text:
        return ()
    if not text.startswith("["):
        return (text,)
    try:
        paths = json.loads(text)
    except ValueError as error:
        raise OncoTracerError("A FASTQ lane list must be a JSON array of file paths.") from error
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) or not path.strip() for path in paths):
        raise OncoTracerError("A FASTQ lane list must contain nonempty file paths.")
    return tuple(paths)


def parse_fastq_field(value: str, label: str) -> FastqInput:
    paths = tuple(require_file(Path(path), label) for path in fastq_field_values(value))
    if not paths:
        raise OncoTracerError(f"{label} is required.")
    if len(set(paths)) != len(paths):
        raise OncoTracerError(f"The same FASTQ appears more than once in {label}.")
    return paths[0] if len(paths) == 1 else paths
