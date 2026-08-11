#!/usr/bin/env python3
"""Build one auditable nested-SAMURAI trace from resumed trace fragments.

Nextflow writes a new execution trace for each nested invocation. A completed
resumed run can therefore be split across several individually incomplete trace
files. The release verifier needs a single task inventory, not an assumption
that one invocation necessarily contains every completed or cached task.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class SourceRow:
    trace: Path
    trace_mtime_ns: int
    row_number: int
    values: dict[str, str]


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


def discover_traces(root: Path) -> list[Path]:
    traces = sorted(
        path
        for path in root.rglob("pipeline_info/execution_trace_*.txt")
        if path.name != COMBINED_NAME and path.is_file() and path.stat().st_size > 0
    )
    if not traces:
        raise SystemExit(f"no nested SAMURAI execution traces found under {root}")
    return traces


def combine_root(root: Path) -> tuple[Path, Path, int]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"nested SAMURAI root is missing: {root}")

    traces = discover_traces(root)
    selected: dict[str, SourceRow] = {}
    manifest_rows: list[list[str]] = []

    for trace in sorted(traces, key=lambda path: (path.stat().st_mtime_ns, path.as_posix())):
        _, rows = parse_trace(trace)
        successful = 0
        for row_number, row in enumerate(rows, start=2):
            if (
                row["status"].strip() not in {"COMPLETED", "CACHED"}
                or row["exit"].strip() != "0"
                or not row["container"].strip()
            ):
                continue
            successful += 1
            key = canonical_task(row["name"])
            candidate = SourceRow(
                trace=trace,
                trace_mtime_ns=trace.stat().st_mtime_ns,
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
                trace.relative_to(root).as_posix(),
                str(len(rows)),
                str(successful),
                sha256(trace),
            ]
        )

    if not selected:
        raise SystemExit(f"no successful containerized tasks found under {root}")

    destination_dir = root / ".oncotracer-parity" / "pipeline_info"
    destination_dir.mkdir(parents=True, exist_ok=True)
    combined = destination_dir / COMBINED_NAME
    manifest = destination_dir / MANIFEST_NAME

    ordered = sorted(selected.items(), key=lambda item: item[0])
    with combined.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for task_id, (_, source) in enumerate(ordered, start=1):
            writer.writerow(
                {
                    "task_id": str(task_id),
                    "hash": source.values.get("hash", ""),
                    "name": source.values["name"],
                    "status": source.values["status"],
                    "exit": source.values["exit"],
                    "container": source.values["container"],
                    "source_trace": source.trace.relative_to(root).as_posix(),
                    "source_row": str(source.row_number),
                }
            )

    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["source_trace", "rows", "successful_rows", "sha256"])
        writer.writerows(manifest_rows)

    print(
        f"Combined {len(traces)} nested trace file(s) into {len(ordered)} unique successful tasks: "
        f"{combined}"
    )
    return combined, manifest, len(ordered)


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
