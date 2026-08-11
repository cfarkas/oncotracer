#!/usr/bin/env python3
"""Build one auditable nested-SAMURAI trace from resumed trace fragments.

Nextflow writes a new execution trace for each nested invocation. A completed
resumed run can therefore be split across several individually incomplete trace
files. The release verifier needs a single task inventory, not an assumption
that one invocation necessarily contains every completed or cached task. The
latest occurrence of each task remains authoritative even when it failed, so a
new failure cannot be hidden behind an older successful cache entry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REQUIRED_COLUMNS = {"hash", "name", "status", "exit", "container"}
OUTPUT_COLUMNS = (
    "task_id",
    "hash",
    "name",
    "status",
    "exit",
    "container",
    "source_trace",
    "source_row",
)
COMBINED_NAME = "execution_trace_oncotracer_combined.txt"
MANIFEST_NAME = "execution_trace_oncotracer_sources.tsv"
INVENTORY_COLUMNS = ("source_trace", "mtime_ns", "bytes", "sha256")
SOURCE_MANIFEST_COLUMNS = (
    "source_trace",
    "mtime_ns",
    "bytes",
    "rows",
    "successful_rows",
    "sha256",
)


@dataclass(frozen=True)
class SourceRow:
    trace: Path
    trace_mtime_ns: int
    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class TraceIdentity:
    source_trace: str
    mtime_ns: int
    bytes: int
    sha256: str


@dataclass(frozen=True)
class SourceManifestRecord:
    identity: TraceIdentity
    rows: int
    successful_rows: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_task(name: str) -> str:
    """Normalize workflow prefixes while retaining the per-task sample label."""
    value = re.sub(r"\s+", " ", name.strip())
    if ":SAMURAI:" in value:
        value = "SAMURAI:" + value.rsplit(":SAMURAI:", 1)[1]
    return value


def parse_trace(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = list(reader.fieldnames or [])
        if not REQUIRED_COLUMNS <= set(header):
            missing = sorted(REQUIRED_COLUMNS - set(header))
            raise SystemExit(f"nested trace is missing columns {missing}: {path}")
        return header, list(reader)


def validate_trace_relative(value: str) -> str:
    relative = PurePosixPath(value)
    if (
        not value
        or value != relative.as_posix()
        or relative.is_absolute()
        or ".." in relative.parts
        or "\t" in value
        or "\n" in value
        or "\r" in value
        or relative.parent.name != "pipeline_info"
        or not relative.name.startswith("execution_trace_")
        or "\\" in value
        or not relative.name.endswith(".txt")
        or relative.name == COMBINED_NAME
    ):
        raise ValueError(f"unsafe nested trace path: {value!r}")
    return value


def safe_trace_path(root: Path, path: Path) -> tuple[Path, str]:
    try:
        if path.is_symlink():
            raise ValueError("trace is a symbolic link")
        resolved = path.resolve(strict=True)
        if resolved != path:
            raise ValueError("trace path contains a symbolic-link component")
        relative = resolved.relative_to(root).as_posix()
        validate_trace_relative(relative)
        metadata = resolved.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("trace is not a regular file")
    except (OSError, ValueError) as error:
        raise SystemExit(f"unsafe nested SAMURAI trace {path}: {error}") from error
    return resolved, relative


def trace_identity(root: Path, path: Path) -> TraceIdentity:
    resolved, relative = safe_trace_path(root, path)
    before = resolved.stat(follow_symlinks=False)
    digest = sha256(resolved)
    after = resolved.stat(follow_symlinks=False)
    before_key = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_key = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_key != after_key:
        raise SystemExit(f"nested SAMURAI trace changed while hashing: {resolved}")
    return TraceIdentity(relative, after.st_mtime_ns, after.st_size, digest)


def discover_traces(root: Path, *, require_nonempty: bool = True) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"nested SAMURAI root is missing: {root}")
    traces: list[Path] = []
    for candidate in sorted(root.rglob("pipeline_info/execution_trace_*.txt")):
        if candidate.name == COMBINED_NAME:
            continue
        resolved, _ = safe_trace_path(root, candidate)
        traces.append(resolved)
    if require_nonempty and not traces:
        raise SystemExit(f"no nested SAMURAI execution traces found under {root}")
    return traces


def capture_trace_inventory(root: Path) -> list[TraceIdentity]:
    root = root.expanduser().resolve()
    identities = [
        trace_identity(root, trace)
        for trace in discover_traces(root, require_nonempty=False)
    ]
    return sorted(identities, key=lambda item: (item.mtime_ns, item.source_trace))


def write_trace_inventory(path: Path, rows: list[TraceIdentity]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(INVENTORY_COLUMNS)
            writer.writerows(
                (row.source_trace, row.mtime_ns, row.bytes, row.sha256) for row in rows
            )
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def snapshot_trace_inventory(root: Path, destination: Path) -> list[TraceIdentity]:
    rows = capture_trace_inventory(root)
    write_trace_inventory(destination, rows)
    return rows


def read_trace_inventory(path: Path) -> list[TraceIdentity]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            table = list(csv.reader(handle, delimiter="\t"))
    except (OSError, csv.Error) as error:
        raise SystemExit(
            f"cannot read nested trace inventory {path}: {error}"
        ) from error
    if not table or table[0] != list(INVENTORY_COLUMNS):
        raise SystemExit(f"invalid nested trace inventory header: {path}")
    rows: list[TraceIdentity] = []
    seen: set[str] = set()
    for record in table[1:]:
        if len(record) != len(INVENTORY_COLUMNS):
            raise SystemExit(f"invalid nested trace inventory row: {record!r}")
        source_trace, mtime_text, bytes_text, digest = record
        try:
            validate_trace_relative(source_trace)
            mtime_ns = int(mtime_text)
            size = int(bytes_text)
        except (ValueError, TypeError) as error:
            raise SystemExit(
                f"invalid nested trace inventory row: {record!r}: {error}"
            ) from error
        if (
            source_trace in seen
            or mtime_ns < 0
            or size < 0
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise SystemExit(f"invalid nested trace inventory row: {record!r}")
        seen.add(source_trace)
        rows.append(TraceIdentity(source_trace, mtime_ns, size, digest))
    if rows != sorted(rows, key=lambda item: (item.mtime_ns, item.source_trace)):
        raise SystemExit(
            f"nested trace inventory is not deterministically sorted: {path}"
        )
    return rows


def trace_inventory_delta(
    before: list[TraceIdentity], after: list[TraceIdentity]
) -> list[TraceIdentity]:
    before_by_path = {row.source_trace: row for row in before}
    if len(before_by_path) != len(before):
        raise SystemExit("pre-run nested trace inventory contains duplicate paths")
    changed = [
        row
        for row in after
        if row.source_trace not in before_by_path
        or (row.bytes, row.sha256)
        != (
            before_by_path[row.source_trace].bytes,
            before_by_path[row.source_trace].sha256,
        )
    ]
    return sorted(changed, key=lambda item: (item.mtime_ns, item.source_trace))


def read_source_manifest(path: Path) -> list[SourceManifestRecord]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            table = list(csv.reader(handle, delimiter="\t"))
    except (OSError, csv.Error) as error:
        raise SystemExit(
            f"cannot read nested trace source manifest {path}: {error}"
        ) from error
    if len(table) < 2 or table[0] != list(SOURCE_MANIFEST_COLUMNS):
        raise SystemExit(f"invalid nested trace source manifest header: {path}")
    records: list[SourceManifestRecord] = []
    seen: set[str] = set()
    for row in table[1:]:
        if len(row) != len(SOURCE_MANIFEST_COLUMNS):
            raise SystemExit(f"invalid nested trace source manifest row: {row!r}")
        source_trace, mtime_text, bytes_text, rows_text, successful_text, digest = row
        try:
            validate_trace_relative(source_trace)
            mtime_ns = int(mtime_text)
            size = int(bytes_text)
            row_count = int(rows_text)
            successful_rows = int(successful_text)
        except (TypeError, ValueError) as error:
            raise SystemExit(
                f"invalid nested trace source manifest row: {row!r}: {error}"
            ) from error
        if (
            source_trace in seen
            or mtime_ns < 0
            or size < 0
            or row_count < 0
            or not 0 <= successful_rows <= row_count
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or str(mtime_ns) != mtime_text
            or str(size) != bytes_text
            or str(row_count) != rows_text
            or str(successful_rows) != successful_text
        ):
            raise SystemExit(f"invalid nested trace source manifest row: {row!r}")
        seen.add(source_trace)
        records.append(
            SourceManifestRecord(
                TraceIdentity(source_trace, mtime_ns, size, digest),
                row_count,
                successful_rows,
            )
        )
    if records != sorted(
        records,
        key=lambda record: (
            record.identity.mtime_ns,
            record.identity.source_trace,
        ),
    ):
        raise SystemExit(
            f"nested trace source manifest is not deterministically sorted: {path}"
        )
    return records


def _safe_artifact_root(path: Path, *, live_root: Path | None = None) -> Path:
    absolute = path.expanduser().absolute()
    if absolute.is_symlink():
        raise SystemExit(f"trace artifact root is a symbolic link: {absolute}")
    resolved = absolute.resolve(strict=False)
    if resolved != absolute:
        raise SystemExit(
            f"trace artifact root contains a symbolic-link component: {absolute}"
        )
    if resolved == Path(resolved.anchor) or resolved == resolved.parent:
        raise SystemExit(f"unsafe trace artifact root: {resolved}")
    if live_root is not None:
        live_root = live_root.resolve()
        if resolved == live_root or live_root in resolved.parents:
            raise SystemExit("trace artifact root must be outside the live trace root")
    return resolved


def materialize_trace_sources(
    root: Path, source_manifest: Path, destination: Path
) -> list[SourceManifestRecord]:
    """Copy every authenticated raw trace into one deterministic safe subtree."""
    root = root.expanduser().resolve()
    destination = _safe_artifact_root(destination, live_root=root)
    records = read_source_manifest(source_manifest)
    expected = {record.identity.source_trace for record in records}
    expected_directories = {
        parent.as_posix()
        for relative in expected
        for parent in PurePosixPath(relative).parents
        if parent != PurePosixPath(".")
    }
    observed_live = capture_trace_inventory(root)
    if [record.identity for record in records] != observed_live:
        raise SystemExit(
            "source manifest no longer matches the complete live trace inventory"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(destination.rglob("*")):
        if item.is_symlink():
            raise SystemExit(f"trace artifact subtree contains a symbolic link: {item}")
        relative = item.relative_to(destination).as_posix()
        if item.is_file() and relative not in expected:
            raise SystemExit(f"unexpected file in trace artifact subtree: {relative}")
        if item.is_dir() and relative not in expected_directories:
            raise SystemExit(
                f"unexpected directory in trace artifact subtree: {relative}"
            )
        if not item.is_file() and not item.is_dir():
            raise SystemExit(f"unsupported object in trace artifact subtree: {item}")
    for record in records:
        identity = record.identity
        source_path = root / identity.source_trace
        if trace_identity(root, source_path) != identity:
            raise SystemExit(
                f"live nested trace identity changed before copy: {source_path}"
            )
        target = destination.joinpath(*PurePosixPath(identity.source_trace).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.parent.resolve() != target.parent:
            raise SystemExit(f"unsafe trace artifact destination: {target}")
        temporary_name: str | None = None
        try:
            with (
                tempfile.NamedTemporaryFile(
                    "wb", dir=target.parent, prefix=f".{target.name}.", delete=False
                ) as output,
                source_path.open("rb") as input_handle,
            ):
                temporary_name = output.name
                shutil.copyfileobj(input_handle, output, length=8 * 1024 * 1024)
            temporary = Path(temporary_name)
            if (
                temporary.stat().st_size != identity.bytes
                or sha256(temporary) != identity.sha256
            ):
                raise SystemExit(
                    f"nested trace changed while preserving it: {source_path}"
                )
            temporary.chmod(0o644)
            os.utime(temporary, ns=(0, 0), follow_symlinks=False)
            os.replace(temporary, target)
            temporary_name = None
        finally:
            if temporary_name is not None and Path(temporary_name).exists():
                Path(temporary_name).unlink()
    actual = {
        item.relative_to(destination).as_posix()
        for item in destination.rglob("*")
        if item.is_file()
    }
    if actual != expected:
        raise SystemExit(
            "preserved trace artifact inventory mismatch: "
            f"missing={sorted(expected - actual)!r} "
            f"extra={sorted(actual - expected)!r}"
        )
    return records


def _combine_identities(
    root: Path,
    identities: list[TraceIdentity],
    destination_dir: Path,
    *,
    require_recorded_mtime: bool,
) -> tuple[Path, Path, int]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"nested SAMURAI root is missing: {root}")
    selected: dict[str, SourceRow] = {}
    manifest_rows: list[list[str]] = []

    for identity in identities:
        trace = root / identity.source_trace
        observed = trace_identity(root, trace)
        if (
            observed.source_trace != identity.source_trace
            or observed.bytes != identity.bytes
            or observed.sha256 != identity.sha256
            or (require_recorded_mtime and observed.mtime_ns != identity.mtime_ns)
        ):
            raise SystemExit(f"nested trace disagrees with recorded identity: {trace}")
        _, rows = parse_trace(trace)
        successful = 0
        for row_number, row in enumerate(rows, start=2):
            if (
                row["status"].strip() in {"COMPLETED", "CACHED"}
                and row["exit"].strip() == "0"
                and row["container"].strip()
            ):
                successful += 1
            key = canonical_task(row["name"])
            candidate = SourceRow(
                trace=trace,
                trace_mtime_ns=identity.mtime_ns,
                row_number=row_number,
                values={column: (row.get(column) or "").strip() for column in row},
            )
            previous = selected.get(key)
            if previous is None or (
                candidate.trace_mtime_ns,
                candidate.trace.as_posix(),
                candidate.row_number,
            ) >= (
                previous.trace_mtime_ns,
                previous.trace.as_posix(),
                previous.row_number,
            ):
                selected[key] = candidate
        manifest_rows.append(
            [
                identity.source_trace,
                str(identity.mtime_ns),
                str(identity.bytes),
                str(len(rows)),
                str(successful),
                identity.sha256,
            ]
        )

    if not selected:
        raise SystemExit(f"no nested task occurrences found under {root}")

    destination_dir = destination_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    combined = destination_dir / COMBINED_NAME
    manifest = destination_dir / MANIFEST_NAME

    ordered = sorted(selected.items(), key=lambda item: item[0])
    with combined.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=OUTPUT_COLUMNS, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for task_id, (_, source_row) in enumerate(ordered, start=1):
            writer.writerow(
                {
                    "task_id": str(task_id),
                    "hash": source_row.values.get("hash", ""),
                    "name": source_row.values["name"],
                    "status": source_row.values["status"],
                    "exit": source_row.values["exit"],
                    "container": source_row.values["container"],
                    "source_trace": source_row.trace.relative_to(root).as_posix(),
                    "source_row": str(source_row.row_number),
                }
            )

    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(SOURCE_MANIFEST_COLUMNS)
        writer.writerows(manifest_rows)

    print(
        f"Combined {len(identities)} nested trace file(s) into {len(ordered)} "
        f"latest task occurrence(s): {combined}"
    )
    return combined, manifest, len(ordered)


def combine_root(root: Path) -> tuple[Path, Path, int]:
    root = root.expanduser().resolve()
    traces = discover_traces(root)
    identities = [trace_identity(root, trace) for trace in traces]
    identities.sort(key=lambda item: (item.mtime_ns, item.source_trace))
    return _combine_identities(
        root,
        identities,
        root / ".oncotracer-parity" / "pipeline_info",
        require_recorded_mtime=True,
    )


def recompute_preserved_trace_artifact(
    source_root: Path, source_manifest: Path, destination_dir: Path
) -> tuple[Path, Path, int]:
    """Rebuild latest-task evidence using copied bytes and recorded source ordering."""
    source_root = _safe_artifact_root(source_root)
    records = read_source_manifest(source_manifest)
    expected = {record.identity.source_trace for record in records}
    observed_paths = {
        trace.relative_to(source_root).as_posix()
        for trace in discover_traces(source_root, require_nonempty=False)
    }
    if observed_paths != expected:
        raise SystemExit(
            "preserved raw trace inventory does not match source manifest: "
            f"missing={sorted(expected - observed_paths)!r} "
            f"extra={sorted(observed_paths - expected)!r}"
        )
    combined, regenerated, count = _combine_identities(
        source_root,
        [record.identity for record in records],
        destination_dir,
        require_recorded_mtime=False,
    )
    if regenerated.read_bytes() != source_manifest.read_bytes():
        raise SystemExit("preserved traces do not regenerate the exact source manifest")
    return combined, regenerated, count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        required=True,
        help="Nested SAMURAI result root; may be supplied more than once",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    for root in args.root:
        combine_root(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
