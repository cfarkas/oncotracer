#!/usr/bin/env python3
"""Verify and select completed nested SAMURAI traces for v2 release parity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from combine_nested_samurai_traces import (
    SOURCE_MANIFEST_COLUMNS,
    TraceIdentity,
    capture_trace_inventory,
    combine_root,
    read_trace_inventory,
    snapshot_trace_inventory,
    trace_identity,
    trace_inventory_delta,
    validate_trace_relative,
    write_trace_inventory,
)


@dataclass(frozen=True)
class Contract:
    label: str
    root_arg: str
    expected_rows: int
    processes: frozenset[str]
    images: frozenset[str]
    require_ichorcna_compat: bool = False


ILLUMINA_PROCESSES = frozenset(
    {
        "SAMURAI:SAMTOOLS_INDEX",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:SOLID_BIOPSY:QDNASEQ",
        "SAMURAI:SOLID_BIOPSY:CONCATENATE_QDNASEQ_PLOTS",
        "SAMURAI:MULTIQC",
    }
)
ILLUMINA_IMAGES = frozenset(
    {
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    }
)
ONT_PROCESSES = frozenset(
    {
        "SAMURAI:SAMTOOLS_INDEX",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:ICHORCNA_RUN",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:AGGREGATE_ICHORCNA_TABLE",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CORRECT_LOGR_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:PLOT_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CONCATENATE_BIN_PLOTS",
        "SAMURAI:MULTIQC",
    }
)
ONT_IMAGES = frozenset(
    {
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
        "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4",
        "quay.io/einar_rainhart/pandas-pandera:1.5.3",
        "community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3",
        "community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    }
)

ICHORCNA_RUN_PROCESS = "SAMURAI:LIQUID_BIOPSY:ICHORCNA:ICHORCNA_RUN"

CONTRACTS: dict[str, tuple[Contract, ...]] = {
    "quickstart1": (
        Contract(
            label="quickstart1-illumina",
            root_arg="illumina_root",
            expected_rows=6,
            processes=ILLUMINA_PROCESSES,
            images=ILLUMINA_IMAGES,
        ),
        Contract(
            label="quickstart1-ont",
            root_arg="ont_root",
            expected_rows=10,
            processes=ONT_PROCESSES,
            images=ONT_IMAGES,
            require_ichorcna_compat=True,
        ),
    ),
    "quickstart2": (
        Contract(
            label="quickstart2-hcc1143",
            root_arg="hcc_root",
            expected_rows=14,
            processes=ILLUMINA_PROCESSES,
            images=ILLUMINA_IMAGES,
        ),
    ),
}

REQUIRED_TRACE_COLUMNS = {"hash", "name", "status", "exit", "container"}
NEXTFLOW_TASK_HASH = re.compile(r"([0-9a-f]{2})/([0-9a-f]{6,})")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_process(name: str) -> str:
    value = re.sub(r"\s+\([^()]*(?:\([^()]*\)[^()]*)*\)$", "", name.strip())
    if ":SAMURAI:" in value:
        value = "SAMURAI:" + value.rsplit(":SAMURAI:", 1)[1]
    return value


def normalize_container(value: str, pins: dict[str, str]) -> str:
    normalized = value.strip().removeprefix("docker://")
    supplied_digest: str | None = None
    if "@sha256:" in normalized:
        normalized, supplied_digest = normalized.rsplit("@", 1)
    if normalized.startswith("biocontainers/"):
        normalized = "quay.io/" + normalized
    elif normalized.startswith("t0shy/qpdf-docker:"):
        normalized = "docker.io/" + normalized
    if normalized not in pins:
        raise ValueError(f"unexpected nested SAMURAI container: {normalized}")
    if supplied_digest is not None and supplied_digest != pins[normalized]:
        raise ValueError(f"trace digest disagrees with pin for {normalized}")
    return normalized


def read_pins(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or rows[0] != ["container", "manifest_digest"]:
        raise SystemExit(f"invalid SAMURAI pin manifest: {path}")
    pins: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) != 2:
            raise SystemExit(f"invalid pin row: {row!r}")
        container, digest = row
        if container in pins or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit(f"duplicate or invalid pin: {row!r}")
        pins[container] = digest
    return pins


def parse_trace(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def evaluate_trace(
    path: Path, contract: Contract, pins: dict[str, str]
) -> tuple[bool, str, list[dict[str, str]], set[str]]:
    try:
        rows = parse_trace(path)
    except (OSError, csv.Error) as error:
        return False, f"cannot parse: {error}", [], set()
    if not rows:
        return False, "empty", rows, set()
    if not REQUIRED_TRACE_COLUMNS <= set(rows[0]):
        return (
            False,
            f"missing columns {sorted(REQUIRED_TRACE_COLUMNS - set(rows[0]))}",
            rows,
            set(),
        )

    selected: list[dict[str, str]] = []
    normalized_processes: list[str] = []
    try:
        for row in rows:
            process = normalize_process(row["name"])
            # Combined resumed traces can include reference-indexing, FASTQC, or
            # earlier-attempt tasks outside this scientific contract. Preserve
            # them in the audit file but evaluate only the expected stage set.
            if process not in contract.processes:
                continue
            if (
                row["status"] not in {"COMPLETED", "CACHED"}
                or row["exit"] != "0"
                or not row["container"].strip()
            ):
                return (
                    False,
                    "failed, nonzero, or container-free contracted task",
                    rows,
                    set(),
                )
            task_hash = row["hash"].strip().lower()
            if NEXTFLOW_TASK_HASH.fullmatch(task_hash) is None:
                return (
                    False,
                    f"invalid contracted task hash: {task_hash!r}",
                    rows,
                    set(),
                )
            normalized = dict(row)
            normalized["hash"] = task_hash
            normalized["container"] = normalize_container(row["container"], pins)
            selected.append(normalized)
            normalized_processes.append(process)
    except ValueError as error:
        return False, str(error), rows, set()

    if len(selected) != contract.expected_rows:
        return (
            False,
            f"contracted row count {len(selected)} != {contract.expected_rows}; "
            f"counts={dict(Counter(normalized_processes))}",
            selected,
            {row["container"] for row in selected},
        )
    processes = set(normalized_processes)
    images = {row["container"] for row in selected}
    if processes != set(contract.processes):
        return (
            False,
            f"process mismatch {sorted(processes ^ set(contract.processes))}",
            selected,
            images,
        )
    if images != set(contract.images):
        return (
            False,
            f"image mismatch {sorted(images ^ set(contract.images))}",
            selected,
            images,
        )
    return True, "qualified", selected, images


def docker_output(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def verify_image_identity(container: str, digest: str) -> str:
    repository = container.rsplit(":", 1)[0]
    immutable = f"{repository}@{digest}"
    image_id = docker_output(
        ["docker", "image", "inspect", container, "--format", "{{.Id}}"]
    )
    immutable_id = docker_output(
        ["docker", "image", "inspect", immutable, "--format", "{{.Id}}"]
    )
    if (
        image_id != immutable_id
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
    ):
        raise SystemExit(f"{container} is not bound to pinned digest {digest}")
    repo_digests = json.loads(
        docker_output(
            [
                "docker",
                "image",
                "inspect",
                container,
                "--format",
                "{{json .RepoDigests}}",
            ]
        )
    )
    if not any(item.endswith("@" + digest) for item in repo_digests or []):
        raise SystemExit(f"{container} lacks expected RepoDigest {digest}")
    return image_id


def parse_compat(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or rows[0] != ["key", "value"]:
        raise ValueError("invalid compatibility marker header")
    metadata = dict(rows[1:])
    if metadata.get("schema") != "oncotracer-ichorcna-plot-compat-v1":
        raise ValueError("invalid compatibility marker schema")
    if metadata.get("status") not in {"patched", "upstream-safe"}:
        raise ValueError("invalid compatibility status")
    if metadata.get("target_quantile_calls") != "2":
        raise ValueError("unexpected quantile target count")
    if metadata.get("zero_median_plot_guard") != "placeholder":
        raise ValueError("missing zero-median plotting guard")
    return metadata


def require_ichorcna_task_hash(rows: Iterable[dict[str, str]]) -> str:
    matches = [
        row
        for row in rows
        if normalize_process(row.get("name", "")) == ICHORCNA_RUN_PROCESS
    ]
    if len(matches) != 1:
        raise SystemExit(
            "complete nested ONT evidence must contain exactly one successful "
            f"ICHORCNA_RUN task; observed={len(matches)}"
        )
    row = matches[0]
    if (
        row.get("status", "").strip().upper() != "COMPLETED"
        or row.get("exit", "").strip() != "0"
    ):
        raise SystemExit(
            "nested ICHORCNA_RUN must be freshly COMPLETED with exit status zero; "
            "the release comparator disables its cache"
        )
    task_hash = row.get("hash", "").strip().lower()
    if NEXTFLOW_TASK_HASH.fullmatch(task_hash) is None:
        raise SystemExit(
            f"nested ICHORCNA_RUN has invalid Nextflow work hash: {task_hash!r}"
        )
    return task_hash


def identity_record(identity: TraceIdentity) -> dict[str, object]:
    return {
        "source_trace": identity.source_trace,
        "mtime_ns": identity.mtime_ns,
        "bytes": identity.bytes,
        "sha256": identity.sha256,
    }


def read_source_manifest_identities(path: Path) -> list[TraceIdentity]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            table = list(csv.reader(handle, delimiter="\t"))
    except (OSError, csv.Error) as error:
        raise SystemExit(
            f"cannot read nested trace source manifest {path}: {error}"
        ) from error
    if not table or table[0] != list(SOURCE_MANIFEST_COLUMNS):
        raise SystemExit(f"invalid nested trace source manifest header: {path}")
    identities: list[TraceIdentity] = []
    seen: set[str] = set()
    for record in table[1:]:
        if len(record) != len(SOURCE_MANIFEST_COLUMNS):
            raise SystemExit(f"invalid nested trace source manifest row: {record!r}")
        source_trace, mtime_text, bytes_text, rows_text, successful_text, digest = (
            record
        )
        try:
            validate_trace_relative(source_trace)
            mtime_ns = int(mtime_text)
            size = int(bytes_text)
            row_count = int(rows_text)
            successful_rows = int(successful_text)
        except (ValueError, TypeError) as error:
            raise SystemExit(
                f"invalid nested trace source manifest row: {record!r}: {error}"
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
            raise SystemExit(f"invalid nested trace source manifest row: {record!r}")
        seen.add(source_trace)
        identities.append(TraceIdentity(source_trace, mtime_ns, size, digest))
    expected = sorted(identities, key=lambda item: (item.mtime_ns, item.source_trace))
    if identities != expected:
        raise SystemExit(
            f"nested trace source manifest is not deterministically sorted: {path}"
        )
    return identities


def verify_current_trace_invocation(
    root: Path,
    pre_inventory_path: Path,
    source_manifest_path: Path,
    selected_rows: Iterable[dict[str, str]],
    post_inventory_path: Path,
    delta_inventory_path: Path,
    *,
    require_ichorcna: bool,
) -> dict[str, object]:
    """Bind selected evidence to trace content created by this outer invocation."""
    root = root.expanduser().resolve()
    pre = read_trace_inventory(pre_inventory_path)
    post = capture_trace_inventory(root)
    manifest_identities = read_source_manifest_identities(source_manifest_path)
    if manifest_identities != post:
        raise SystemExit(
            "nested trace source manifest is not the complete current real-file "
            f"inventory: {source_manifest_path}"
        )
    delta = trace_inventory_delta(pre, post)
    if not delta:
        raise SystemExit(
            "nested comparator produced no new or content-changed execution trace "
            "during the current invocation"
        )
    newest = max(post, key=lambda item: (item.mtime_ns, item.source_trace))
    if newest not in delta:
        raise SystemExit(
            "newest nested execution trace predates the current comparator invocation: "
            f"{newest.source_trace}"
        )

    selected_ichorcna_source: str | None = None
    if require_ichorcna:
        selected = list(selected_rows)
        require_ichorcna_task_hash(selected)
        ichorcna_rows = [
            row
            for row in selected
            if normalize_process(row.get("name", "")) == ICHORCNA_RUN_PROCESS
        ]
        if len(ichorcna_rows) != 1:
            raise SystemExit(
                "selected nested trace has ambiguous ICHORCNA_RUN evidence"
            )
        selected_ichorcna_source = ichorcna_rows[0].get("source_trace", "").strip()
        try:
            validate_trace_relative(selected_ichorcna_source)
        except ValueError as error:
            raise SystemExit(
                f"selected ICHORCNA_RUN has unsafe source trace: {error}"
            ) from error
        if selected_ichorcna_source != newest.source_trace:
            raise SystemExit(
                "selected freshly completed ICHORCNA_RUN does not come from the "
                "deterministic newest nested trace: "
                f"selected={selected_ichorcna_source!r} newest={newest.source_trace!r}"
            )
        if not any(
            row.source_trace == selected_ichorcna_source and row == newest
            for row in delta
        ):
            raise SystemExit(
                "selected ICHORCNA_RUN source trace was not newly created or "
                "content-changed by the current comparator invocation"
            )

    inventory_paths = {
        path.expanduser().resolve(strict=False)
        for path in (pre_inventory_path, post_inventory_path, delta_inventory_path)
    }
    if len(inventory_paths) != 3:
        raise SystemExit("pre/post/delta trace inventory paths must be distinct")
    write_trace_inventory(post_inventory_path, post)
    write_trace_inventory(delta_inventory_path, delta)
    return {
        "schema": "oncotracer-nested-trace-invocation-v1",
        "newest_source_trace": newest.source_trace,
        "selected_ichorcna_source_trace": selected_ichorcna_source,
        "pre": {
            "sha256": sha256(pre_inventory_path),
            "entries": [identity_record(row) for row in pre],
        },
        "post": {
            "sha256": sha256(post_inventory_path),
            "entries": [identity_record(row) for row in post],
        },
        "delta": {
            "sha256": sha256(delta_inventory_path),
            "entries": [identity_record(row) for row in delta],
        },
    }


def marker_path_matches_task_hash(relative: Path, task_hash: str) -> bool:
    match = NEXTFLOW_TASK_HASH.fullmatch(task_hash)
    if match is None or relative.is_absolute() or ".." in relative.parts:
        return False
    prefix, suffix = match.groups()
    if len(relative.parts) != 4:
        return False
    work, observed_prefix, full_suffix, marker_name = relative.parts
    return (
        work == "work"
        and observed_prefix == prefix
        and re.fullmatch(r"[0-9a-f]{30}", full_suffix) is not None
        and full_suffix.startswith(suffix)
        and marker_name == ".oncotracer-ichorcna-plot-compat.tsv"
    )


def find_compat_marker(
    root: Path, selected_rows: Iterable[dict[str, str]]
) -> tuple[Path, dict[str, str], str, Path]:
    root = root.resolve()
    task_hash = require_ichorcna_task_hash(selected_rows)
    valid: list[tuple[Path, dict[str, str], Path]] = []
    diagnostics: list[str] = []
    for marker in sorted(root.rglob(".oncotracer-ichorcna-plot-compat.tsv")):
        try:
            if marker.is_symlink():
                raise ValueError("marker is a symbolic link")
            resolved = marker.resolve(strict=True)
            if resolved != marker:
                raise ValueError("marker path contains a symbolic-link component")
            relative = resolved.relative_to(root)
        except (OSError, ValueError) as error:
            diagnostics.append(f"{marker}: unsafe marker path: {error}")
            continue
        if not marker_path_matches_task_hash(relative, task_hash):
            continue
        try:
            valid.append((resolved, parse_compat(resolved), relative))
        except (OSError, ValueError, csv.Error) as error:
            diagnostics.append(f"{marker}: {error}")
    if len(valid) != 1:
        details = (
            "\n".join(diagnostics) if diagnostics else "no matching marker files found"
        )
        raise SystemExit(
            "expected exactly one valid nested ichorCNA compatibility marker in "
            f"successful ICHORCNA_RUN work hash {task_hash}; observed={len(valid)}:\n"
            f"{details}"
        )
    selected, metadata, relative = valid[0]
    return selected, metadata, task_hash, relative


def select_compat_marker(
    root: Path, destination: Path, selected_rows: Iterable[dict[str, str]]
) -> tuple[dict[str, str], str, Path]:
    selected, metadata, task_hash, relative = find_compat_marker(root, selected_rows)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(selected, destination)
    return metadata, task_hash, relative


def write_tsv(path: Path, header: Iterable[str], rows: Iterable[Iterable[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(list(header))
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--snapshot-out", type=Path)
    parser.add_argument("--suite", choices=sorted(CONTRACTS))
    parser.add_argument("--pins", type=Path)
    parser.add_argument("--runtime-out", type=Path)
    parser.add_argument("--selected-dir", type=Path)
    parser.add_argument("--selection-out", type=Path)
    parser.add_argument("--illumina-root", type=Path)
    parser.add_argument("--ont-root", type=Path)
    parser.add_argument("--hcc-root", type=Path)
    parser.add_argument("--illumina-pre-inventory", type=Path)
    parser.add_argument("--ont-pre-inventory", type=Path)
    parser.add_argument("--hcc-pre-inventory", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.snapshot_root is not None or args.snapshot_out is not None:
        if args.snapshot_root is None or args.snapshot_out is None:
            raise SystemExit(
                "--snapshot-root and --snapshot-out must be supplied together"
            )
        snapshot_trace_inventory(args.snapshot_root, args.snapshot_out)
        print(f"captured pre-run nested trace inventory: {args.snapshot_out}")
        return 0

    required = {
        "--suite": args.suite,
        "--pins": args.pins,
        "--runtime-out": args.runtime_out,
        "--selected-dir": args.selected_dir,
        "--selection-out": args.selection_out,
    }
    missing_required = [name for name, value in required.items() if value is None]
    if missing_required:
        raise SystemExit(
            "missing verification argument(s): " + ", ".join(missing_required)
        )
    if args.snapshot_root is not None or args.snapshot_out is not None:
        raise SystemExit("snapshot arguments cannot be combined with verification")

    pins = read_pins(args.pins)
    contracts = CONTRACTS[args.suite]
    expected_union = set().union(*(set(contract.images) for contract in contracts))
    if set(pins) != expected_union:
        missing = sorted(expected_union - set(pins))
        extra = sorted(set(pins) - expected_union)
        raise SystemExit(f"pin inventory mismatch; missing={missing!r} extra={extra!r}")

    args.selected_dir.mkdir(parents=True, exist_ok=True)
    runtime_rows: list[list[str]] = []
    selection_rows: list[list[str]] = []

    for contract in contracts:
        root = getattr(args, contract.root_arg)
        if root is None or not root.is_dir():
            raise SystemExit(f"missing --{contract.root_arg.replace('_', '-')}: {root}")
        pre_arg = contract.root_arg.removesuffix("_root") + "_pre_inventory"
        pre_inventory = getattr(args, pre_arg)
        if pre_inventory is None:
            raise SystemExit(
                f"missing --{pre_arg.replace('_', '-')} for {contract.label}"
            )
        diagnostic_prefix = contract.label.removeprefix("quickstart1-").removeprefix(
            "quickstart2-"
        )
        pre_audit = args.selected_dir / f"nested-v1-{diagnostic_prefix}-trace-pre.tsv"
        write_trace_inventory(pre_audit, read_trace_inventory(pre_inventory))

        # A nested Nextflow resume can split one complete run across several
        # execution traces. Build one deterministic latest-occurrence task
        # bundle, then require every contracted occurrence to be successful,
        # instead of trusting an arbitrary individual trace file.
        combined_trace, source_manifest, _ = combine_root(root)
        combined_audit = (
            args.selected_dir / f"candidate-{diagnostic_prefix}-combined-trace.tsv"
        )
        source_audit = (
            args.selected_dir / f"candidate-{diagnostic_prefix}-trace-sources.tsv"
        )
        shutil.copyfile(combined_trace, combined_audit)
        shutil.copyfile(source_manifest, source_audit)

        ok, reason, selected_rows, observed_images = evaluate_trace(
            combined_trace, contract, pins
        )
        if not ok:
            raise SystemExit(
                f"combined nested trace did not satisfy {contract.label}: {reason}; "
                f"trace={combined_trace}; sources={source_manifest}"
            )

        post_audit = args.selected_dir / f"nested-v1-{diagnostic_prefix}-trace-post.tsv"
        delta_audit = (
            args.selected_dir / f"nested-v1-{diagnostic_prefix}-trace-delta.tsv"
        )
        invocation = verify_current_trace_invocation(
            root,
            pre_audit,
            source_manifest,
            selected_rows,
            post_audit,
            delta_audit,
            require_ichorcna=contract.require_ichorcna_compat,
        )
        invocation_name = f"nested-v1-{diagnostic_prefix}-trace-invocation.json"
        invocation_path = args.selected_dir / invocation_name
        invocation_path.write_text(
            json.dumps(invocation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trace_count = len(invocation["post"]["entries"])

        selected_name = f"nested-v1-{diagnostic_prefix}-trace.tsv"
        selected_destination = args.selected_dir / selected_name
        shutil.copyfile(combined_trace, selected_destination)
        selection_rows.append(
            [
                contract.label,
                str(trace_count),
                "1",
                f"complete-combined-trace:{combined_trace.as_posix()};current:{invocation['newest_source_trace']}",
                selected_destination.name,
                sha256(selected_destination),
            ]
        )

        # Authenticate the complete immutable image contract even when a resumed
        # trace fragment records only the final subset of executed tasks.
        for container in sorted(contract.images):
            runtime_rows.append(
                [
                    contract.label,
                    container,
                    pins[container],
                    verify_image_identity(container, pins[container]),
                ]
            )
        selection_rows.append(
            [
                contract.label + "-current-invocation",
                "",
                "",
                f"newest-trace:{invocation['newest_source_trace']}",
                invocation_name,
                sha256(invocation_path),
            ]
        )
        if contract.require_ichorcna_compat:
            metadata, task_hash, marker_relative = select_compat_marker(
                root,
                args.selected_dir / "nested-v1-ont-ichorcna-plot-compat.tsv",
                selected_rows,
            )
            selection_rows.append(
                [
                    contract.label + "-ichorcna-compat",
                    "",
                    "",
                    f"task-hash:{task_hash};marker:{marker_relative.as_posix()}",
                    "nested-v1-ont-ichorcna-plot-compat.tsv",
                    sha256(
                        args.selected_dir / "nested-v1-ont-ichorcna-plot-compat.tsv"
                    ),
                ]
            )
            print(f"{contract.label} ichorCNA compatibility: {metadata['status']}")

    write_tsv(
        args.runtime_out,
        ["run", "container", "manifest_digest", "image_id"],
        runtime_rows,
    )
    write_tsv(
        args.selection_out,
        [
            "run",
            "candidate_traces",
            "qualified_traces",
            "selected_source",
            "audit_copy",
            "sha256",
        ],
        selection_rows,
    )
    if {row[1] for row in runtime_rows} != set(pins):
        raise SystemExit("selected traces did not exercise every pinned image")
    print(f"verified {args.suite}: {len(runtime_rows)} run/image records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
