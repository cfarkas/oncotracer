#!/usr/bin/env python3
"""Build and verify auditable OncoTracer v2 QuickStart parity artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_nested_samurai import (  # noqa: E402
    CONTRACTS,
    ICHORCNA_RUN_PROCESS,
    Contract,
    evaluate_trace,
    marker_path_matches_task_hash,
    parse_compat,
    normalize_process,
    parse_trace,
    require_ichorcna_task_hash,
)

SCHEMA = "oncotracer-native-v2-parity-audit-v2"
V1_COMMIT = "032c1268fa7fdcadc48087055066d7a9fc59bd89"
V1_IMAGE = "carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed"
SAMURAI_COMMIT = "6a901940288b008237703c6b181d447e7dee4fcf"
NEXTFLOW_VERSION = "26.04.6"
NEXTFLOW_URL = "https://github.com/nextflow-io/nextflow/releases/download/v26.04.6/nextflow-26.04.6-dist"
NEXTFLOW_SHA256 = "182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"
QDNASEQ_COMMIT = "cf7c07e39de0ac64a9c38cb030cba4626e2aae83"


class AuditError(RuntimeError):
    pass


def require_file(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise AuditError(f"missing or empty file: {path}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if (
        relative.startswith("/")
        or ".." in Path(relative).parts
        or "\n" in relative
        or "\t" in relative
    ):
        raise AuditError(f"unsafe relative path: {relative!r}")
    return relative


def write_manifest(root: Path, destination: Path) -> None:
    if not root.is_dir():
        raise AuditError(f"manifest root is missing: {root}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination.resolve(strict=False)
    rows: list[tuple[str, int, str]] = []
    paths = sorted(
        (safe_relative(item, root), item) for item in root.rglob("*") if item.is_file()
    )
    for relative, path in paths:
        if path.resolve() == destination_resolved:
            continue
        rows.append((sha256(path), path.stat().st_size, relative))
    if not rows:
        raise AuditError(f"manifest root contains no files: {root}")
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["sha256", "bytes", "path"])
        writer.writerows(rows)


def read_manifest(path: Path) -> dict[str, tuple[str, int]]:
    with require_file(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or rows[0] != ["sha256", "bytes", "path"] or len(rows) < 2:
        raise AuditError(f"invalid manifest: {path}")
    records: dict[str, tuple[str, int]] = {}
    for row in rows[1:]:
        if len(row) != 3:
            raise AuditError(f"invalid manifest row: {row!r}")
        digest, size_text, relative = row
        if (
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in records
        ):
            raise AuditError(f"unsafe manifest record: {row!r}")
        records[relative] = (digest, int(size_text))
    if list(records) != sorted(records):
        raise AuditError(f"manifest paths are not sorted: {path}")
    return records


def verify_manifest_tree(root: Path, path: Path) -> dict[str, tuple[str, int]]:
    records = read_manifest(path)
    actual = sorted(
        safe_relative(item, root) for item in root.rglob("*") if item.is_file()
    )
    if actual != sorted(records):
        raise AuditError(f"manifest is not a complete inventory of {root}")
    for relative, (digest, size) in records.items():
        file_path = require_file(root / relative)
        if file_path.stat().st_size != size or sha256(file_path) != digest:
            raise AuditError(f"manifest mismatch: {file_path}")
    return records


def write_checksums(audit: Path) -> None:
    audit.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for path in sorted(item for item in audit.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS":
            continue
        rows.append(f"{sha256(path)}  {safe_relative(path, audit)}")
    if not rows:
        raise AuditError(f"audit contains no files: {audit}")
    (audit / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def verify_checksums(audit: Path) -> None:
    lines = require_file(audit / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    expected: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise AuditError(f"invalid SHA256SUMS row: {line!r}")
        digest, relative = match.groups()
        if (
            relative in expected
            or relative.startswith("/")
            or ".." in Path(relative).parts
        ):
            raise AuditError(f"unsafe or duplicate SHA256SUMS path: {relative!r}")
        expected[relative] = digest
    actual = sorted(
        safe_relative(path, audit)
        for path in audit.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    if actual != sorted(expected):
        raise AuditError("SHA256SUMS is not a complete audit inventory")
    for relative, digest in expected.items():
        if sha256(require_file(audit / relative)) != digest:
            raise AuditError(f"audit checksum mismatch: {relative}")


def read_single_line(path: Path) -> str:
    value = require_file(path).read_text(encoding="utf-8").strip()
    if not value or "\n" in value:
        raise AuditError(f"expected one nonempty line: {path}")
    return value


def read_key_values(path: Path, delimiter: str = "=") -> dict[str, str]:
    result: dict[str, str] = {}
    for line in require_file(path).read_text(encoding="utf-8").splitlines():
        if delimiter not in line:
            continue
        key, value = line.split(delimiter, 1)
        result[key] = value
    return result


def read_tsv(path: Path) -> list[list[str]]:
    with require_file(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def verify_trace(path: Path, contract: Contract, pins: dict[str, str]) -> str:
    """Independently re-evaluate the selected combined trace and evidence mode."""
    ok, reason, _rows, _images = evaluate_trace(path, contract, pins)
    if ok:
        return "complete-combined-trace"

    raise AuditError(
        f"selected nested trace does not satisfy {contract.label}: {reason}: {path}"
    )


TRACE_INVENTORY_HEADER = ["source_trace", "mtime_ns", "bytes", "sha256"]
TRACE_SOURCE_HEADER = [
    "source_trace",
    "mtime_ns",
    "bytes",
    "rows",
    "successful_rows",
    "sha256",
]


def validate_audit_trace_relative(value: str) -> None:
    relative = PurePosixPath(value)
    if (
        not value
        or value != relative.as_posix()
        or relative.is_absolute()
        or ".." in relative.parts
        or any(character in value for character in ("\t", "\n", "\r"))
        or "\\" in value
        or relative.parent.name != "pipeline_info"
        or not relative.name.startswith("execution_trace_")
        or not relative.name.endswith(".txt")
        or relative.name == "execution_trace_oncotracer_combined.txt"
    ):
        raise AuditError(f"unsafe nested source-trace path: {value!r}")


def parse_trace_identity_fields(record: list[str]) -> tuple[str, int, int, str]:
    source_trace, mtime_text, bytes_text, digest = record
    validate_audit_trace_relative(source_trace)
    try:
        mtime_ns = int(mtime_text)
        size = int(bytes_text)
    except ValueError as error:
        raise AuditError(f"invalid nested trace identity: {record!r}") from error
    if (
        mtime_ns < 0
        or size < 0
        or str(mtime_ns) != mtime_text
        or str(size) != bytes_text
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise AuditError(f"invalid nested trace identity: {record!r}")
    return source_trace, mtime_ns, size, digest


def read_trace_inventory_audit(path: Path) -> list[tuple[str, int, int, str]]:
    table = read_tsv(path)
    if not table or table[0] != TRACE_INVENTORY_HEADER:
        raise AuditError(f"invalid nested trace inventory: {path}")
    identities: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for record in table[1:]:
        if len(record) != 4:
            raise AuditError(f"invalid nested trace inventory row: {record!r}")
        identity = parse_trace_identity_fields(record)
        if identity[0] in seen:
            raise AuditError(f"duplicate nested trace identity: {identity[0]}")
        seen.add(identity[0])
        identities.append(identity)
    if identities != sorted(identities, key=lambda row: (row[1], row[0])):
        raise AuditError(
            f"nested trace inventory is not deterministically sorted: {path}"
        )
    return identities


def read_trace_source_audit(path: Path) -> list[tuple[str, int, int, str]]:
    table = read_tsv(path)
    if not table or table[0] != TRACE_SOURCE_HEADER:
        raise AuditError(f"invalid nested trace source manifest: {path}")
    identities: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for record in table[1:]:
        if len(record) != 6:
            raise AuditError(f"invalid nested trace source row: {record!r}")
        identity = parse_trace_identity_fields(record[:3] + record[5:])
        try:
            row_count = int(record[3])
            successful_rows = int(record[4])
        except ValueError as error:
            raise AuditError(f"invalid nested trace source row: {record!r}") from error
        if (
            identity[0] in seen
            or row_count < 0
            or not 0 <= successful_rows <= row_count
            or str(row_count) != record[3]
            or str(successful_rows) != record[4]
        ):
            raise AuditError(f"invalid nested trace source row: {record!r}")
        seen.add(identity[0])
        identities.append(identity)
    if identities != sorted(identities, key=lambda row: (row[1], row[0])):
        raise AuditError(f"nested trace source manifest is not sorted: {path}")
    return identities


def audit_trace_delta(
    pre: list[tuple[str, int, int, str]],
    post: list[tuple[str, int, int, str]],
) -> list[tuple[str, int, int, str]]:
    pre_by_path = {row[0]: row for row in pre}
    return [
        row
        for row in post
        if row[0] not in pre_by_path
        or (row[2], row[3]) != (pre_by_path[row[0]][2], pre_by_path[row[0]][3])
    ]


def inventory_json_rows(
    rows: list[tuple[str, int, int, str]],
) -> list[dict[str, object]]:
    return [
        {"source_trace": row[0], "mtime_ns": row[1], "bytes": row[2], "sha256": row[3]}
        for row in rows
    ]


def verify_trace_invocation(
    context: Path,
    trace_path: Path,
    contract: Contract,
    selection_by_run: dict[str, list[str]],
) -> dict[str, object]:
    suffix = contract.label.removeprefix("quickstart1-").removeprefix("quickstart2-")
    pre_path = context / f"nested-v1-{suffix}-trace-pre.tsv"
    post_path = context / f"nested-v1-{suffix}-trace-post.tsv"
    delta_path = context / f"nested-v1-{suffix}-trace-delta.tsv"
    source_path = context / f"candidate-{suffix}-trace-sources.tsv"
    invocation_name = f"nested-v1-{suffix}-trace-invocation.json"
    invocation_path = context / invocation_name

    pre = read_trace_inventory_audit(pre_path)
    post = read_trace_inventory_audit(post_path)
    delta = read_trace_inventory_audit(delta_path)
    sources = read_trace_source_audit(source_path)
    if not post or sources != post:
        raise AuditError("source manifest is not the complete post-run trace inventory")
    expected_delta = audit_trace_delta(pre, post)
    if not expected_delta or delta != expected_delta:
        raise AuditError("nested current-invocation trace delta is empty or forged")
    newest = max(post, key=lambda row: (row[1], row[0]))
    if newest not in delta:
        raise AuditError(
            "deterministic newest nested trace is not current-invocation evidence"
        )

    payload = json.loads(require_file(invocation_path).read_text(encoding="utf-8"))
    if (
        set(payload)
        != {
            "schema",
            "newest_source_trace",
            "selected_ichorcna_source_trace",
            "pre",
            "post",
            "delta",
        }
        or payload.get("schema") != "oncotracer-nested-trace-invocation-v1"
    ):
        raise AuditError("invalid nested current-invocation evidence schema")
    for label, path, rows in (
        ("pre", pre_path, pre),
        ("post", post_path, post),
        ("delta", delta_path, delta),
    ):
        section = payload.get(label)
        if not isinstance(section, dict) or set(section) != {"sha256", "entries"}:
            raise AuditError(f"invalid nested {label} inventory evidence")
        if section["sha256"] != sha256(path) or section[
            "entries"
        ] != inventory_json_rows(rows):
            raise AuditError(f"nested {label} inventory evidence mismatch")
    if payload["newest_source_trace"] != newest[0]:
        raise AuditError("nested invocation JSON does not identify the newest trace")

    ichorcna_source: str | None = None
    if contract.require_ichorcna_compat:
        rows = parse_trace(trace_path)
        try:
            require_ichorcna_task_hash(rows)
        except SystemExit as error:
            raise AuditError(str(error)) from error
        matches = [
            row
            for row in rows
            if normalize_process(row.get("name", "")) == ICHORCNA_RUN_PROCESS
        ]
        if len(matches) != 1:
            raise AuditError("ambiguous selected ICHORCNA_RUN source trace")
        ichorcna_source = matches[0].get("source_trace", "").strip()
        validate_audit_trace_relative(ichorcna_source)
        if ichorcna_source != newest[0] or newest not in delta:
            raise AuditError("ICHORCNA_RUN is not bound to the newest current trace")
    if payload["selected_ichorcna_source_trace"] != ichorcna_source:
        raise AuditError("nested invocation JSON has the wrong ICHORCNA_RUN source")

    selected = selection_by_run.get(contract.label + "-current-invocation")
    if (
        selected is None
        or len(selected) != 6
        or selected[3] != f"newest-trace:{newest[0]}"
        or selected[4] != invocation_name
        or selected[5] != sha256(invocation_path)
    ):
        raise AuditError("missing or invalid current-invocation selection evidence")
    return payload


def verify_compat_selection(
    context: Path,
    trace_path: Path,
    contract: Contract,
    selection_by_run: dict[str, list[str]],
) -> dict[str, str]:
    try:
        task_hash = require_ichorcna_task_hash(parse_trace(trace_path))
    except SystemExit as error:
        raise AuditError(str(error)) from error
    marker_selection = selection_by_run.get(contract.label + "-ichorcna-compat")
    marker_name = "nested-v1-ont-ichorcna-plot-compat.tsv"
    if (
        marker_selection is None
        or len(marker_selection) != 6
        or marker_selection[4] != marker_name
    ):
        raise AuditError("missing nested ichorCNA compatibility selection evidence")
    prefix = f"task-hash:{task_hash};marker:"
    if not marker_selection[3].startswith(prefix):
        raise AuditError("nested ichorCNA marker is not bound to selected task hash")
    marker_relative = Path(marker_selection[3][len(prefix) :])
    if not marker_path_matches_task_hash(marker_relative, task_hash):
        raise AuditError("nested ichorCNA marker source path does not match task hash")
    marker_path = context / marker_name
    if marker_selection[5] != sha256(marker_path):
        raise AuditError("nested ichorCNA compatibility marker checksum mismatch")
    metadata = parse_compat(marker_path)
    if metadata["target_quantile_calls"] != "2":
        raise AuditError("invalid v1 ichorCNA compatibility marker")
    return metadata


def verify_nested(audit: Path, suite: str) -> dict[str, object]:
    context = audit / "context"
    pin_rows = read_tsv(context / "nested-v1-container-pins.tsv")
    if not pin_rows or pin_rows[0] != ["container", "manifest_digest"]:
        raise AuditError("invalid nested pin manifest")
    pins = dict(pin_rows[1:])
    expected_union = set().union(
        *(set(contract.images) for contract in CONTRACTS[suite])
    )
    if set(pins) != expected_union or len(pin_rows) != len(expected_union) + 1:
        raise AuditError("nested pin inventory mismatch")
    if any(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in pins.values()
    ):
        raise AuditError("invalid nested image digest")

    preflight = read_tsv(context / "nested-v1-container-preflight.tsv")
    if not preflight or preflight[0] != ["container", "manifest_digest", "image_id"]:
        raise AuditError("invalid nested preflight manifest")
    if len(preflight) != len(pins) + 1:
        raise AuditError("nested preflight row count mismatch")
    for container, digest, image_id in preflight[1:]:
        if (
            pins.get(container) != digest
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        ):
            raise AuditError(f"invalid nested preflight identity: {container}")

    runtime = read_tsv(context / "nested-v1-container-runtime.tsv")
    if not runtime or runtime[0] != ["run", "container", "manifest_digest", "image_id"]:
        raise AuditError("invalid nested runtime manifest")
    observed: dict[str, set[str]] = {}
    for run, container, digest, image_id in runtime[1:]:
        observed.setdefault(run, set()).add(container)
        if (
            pins.get(container) != digest
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        ):
            raise AuditError(f"invalid nested runtime identity: {container}")
    expected_counts = {
        contract.label: len(contract.images) for contract in CONTRACTS[suite]
    }
    if {run: len(images) for run, images in observed.items()} != expected_counts:
        raise AuditError(f"nested runtime image counts mismatch: {observed!r}")
    if set().union(*observed.values()) != set(pins):
        raise AuditError("nested runtime did not exercise every pinned image")

    selection = read_tsv(context / "nested-v1-trace-selection.tsv")
    if not selection or selection[0] != [
        "run",
        "candidate_traces",
        "qualified_traces",
        "selected_source",
        "audit_copy",
        "sha256",
    ]:
        raise AuditError("invalid nested trace selection manifest")
    selection_by_run = {row[0]: row for row in selection[1:]}
    for contract in CONTRACTS[suite]:
        suffix = contract.label.removeprefix("quickstart1-").removeprefix(
            "quickstart2-"
        )
        filename = f"nested-v1-{suffix}-trace.tsv"
        path = context / filename
        evidence_mode = verify_trace(path, contract, pins)
        selected = selection_by_run.get(contract.label)
        if selected is None or selected[4] != filename or selected[5] != sha256(path):
            raise AuditError(f"selected trace manifest mismatch: {contract.label}")
        if int(selected[1]) < 1 or int(selected[2]) < 1:
            raise AuditError(f"selected trace counts invalid: {contract.label}")
        if not selected[3].startswith(evidence_mode + ":"):
            raise AuditError(
                f"selected trace evidence mode mismatch for {contract.label}: "
                f"expected {evidence_mode!r}, observed {selected[3]!r}"
            )
        verify_trace_invocation(context, path, contract, selection_by_run)
        if contract.require_ichorcna_compat:
            verify_compat_selection(context, path, contract, selection_by_run)
    return {
        "pin_count": len(pins),
        "runtime_counts": expected_counts,
        "pin_sha256": sha256(context / "nested-v1-container-pins.tsv"),
        "runtime_sha256": sha256(context / "nested-v1-container-runtime.tsv"),
    }


def verify_parity_reports(audit: Path, suite: str) -> dict[str, object]:
    if suite == "quickstart1":
        specs = (
            (
                "illumina",
                "QuickStart 1 / Illumina",
                "illumina",
                "illumina_qdnaseq_100kb",
                ["ERR12341627"],
            ),
            ("ont", "QuickStart 1 / ONT", "ont", "ONT_ichorcna_500kb", ["DRR165691"]),
        )
    else:
        specs = (
            (
                ".",
                "QuickStart 2 / HCC1143",
                "illumina",
                "illumina_qdnaseq_100kb",
                sorted(["HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"]),
            ),
        )
    metrics: dict[str, object] = {}
    for directory, label, mode, dataset, samples in specs:
        root = audit if directory == "." else audit / directory
        report_path = require_file(root / "parity_report.json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("label") != label or report.get("passed") is not True:
            raise AuditError(f"failed or mislabeled parity report: {report_path}")
        checks = report.get("checks", {})
        if not checks or not all(value is True for value in checks.values()):
            raise AuditError(f"failed semantic checks: {report_path}")
        if report.get("v1_samples") != samples or report.get("v2_samples") != samples:
            raise AuditError(f"sample set mismatch: {report_path}")
        for summary_name in ("v1_summary", "v2_summary"):
            summary = report.get(summary_name, {})
            if summary.get("mode") != mode or summary.get("dataset") != dataset:
                raise AuditError(f"summary identity mismatch: {report_path}")
        if report.get("v2_summary", {}).get("engine") != "native":
            raise AuditError(f"v2 report does not use native engine: {report_path}")
        if (
            str(report.get("v2_summary", {}).get("nextflow_used", "")).lower()
            != "false"
        ):
            raise AuditError(f"v2 report records Nextflow use: {report_path}")
        metrics[label] = {
            "report_sha256": sha256(report_path),
            "checks": checks,
            "v1_samples": report["v1_samples"],
            "v2_samples": report["v2_samples"],
        }
    if suite == "quickstart1":
        aggregate = json.loads(
            require_file(audit / "quickstart1_parity.json").read_text(encoding="utf-8")
        )
        if (
            aggregate.get("schema") != "oncotracer-quickstart1-parity-v1"
            or aggregate.get("passed") is not True
        ):
            raise AuditError("QuickStart 1 aggregate report failed")
    return metrics


def verify_context(
    audit: Path,
    suite: str,
    candidate_sha: str,
    source_sha256: str,
    binary_sha256: str,
) -> dict[str, object]:
    context = audit / "context"
    for name in (
        "workflow-event-sha.txt",
        "validated-candidate-sha.txt",
        "v2-source-commit.txt",
    ):
        if read_single_line(context / name) != candidate_sha:
            raise AuditError(f"candidate identity mismatch: {name}")
    if read_single_line(context / "v2-source-sha256.txt") != source_sha256:
        raise AuditError("source SHA-256 mismatch")
    if read_single_line(context / "v1-baseline-commit.txt") != V1_COMMIT:
        raise AuditError("v1 baseline commit mismatch")
    if read_single_line(context / "v1-tag-commit.txt") != V1_COMMIT:
        raise AuditError("v1 tag commit mismatch")
    if read_single_line(context / "v1-docker-digest.txt") != V1_IMAGE:
        raise AuditError("v1 image mismatch")

    nextflow = read_key_values(context / "nextflow-identity.txt")
    if nextflow != {
        "version": NEXTFLOW_VERSION,
        "url": NEXTFLOW_URL,
        "sha256": NEXTFLOW_SHA256,
    }:
        raise AuditError(f"Nextflow identity mismatch: {nextflow!r}")
    if f"version {NEXTFLOW_VERSION}" not in require_file(
        context / "nextflow-version.txt"
    ).read_text(encoding="utf-8"):
        raise AuditError("Nextflow version output mismatch")
    samurai = read_key_values(context / "samurai.oncotracer-source")
    if samurai.get("revision") != "v1.4.0" or samurai.get("commit") != SAMURAI_COMMIT:
        raise AuditError("SAMURAI source identity mismatch")

    observed_binary = (
        require_file(context / "native-binary.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    if (
        observed_binary != binary_sha256
        or re.fullmatch(r"[0-9a-f]{64}", observed_binary) is None
    ):
        raise AuditError("native binary checksum mismatch")
    provenance = json.loads(
        require_file(context / "native-binary-provenance.json").read_text(
            encoding="utf-8"
        )
    )
    if not (
        provenance.get("source_commit") == candidate_sha
        and provenance.get("source_sha256") == source_sha256
        and provenance.get("source_tree_dirty") is False
        and provenance.get("binary_sha256") == binary_sha256
    ):
        raise AuditError("native binary provenance mismatch")
    doctor = json.loads(
        require_file(context / "native-doctor.json").read_text(encoding="utf-8")
    )
    if doctor.get("success") is not True:
        raise AuditError("native doctor did not pass")
    for environment in ("core", "qdnaseq", "ichorcna", "classifier", "gistic"):
        require_file(context / f"native-{environment}.explicit.txt")

    input_manifest = read_manifest(context / "manifests/public-input-manifest.tsv")
    reference_manifest = read_manifest(
        context / "manifests/shared-reference-manifest.tsv"
    )
    required_reference = {
        "genome.fa",
        "genome.fa.fai",
        "genome.dict",
        "bwa/genome.amb",
        "bwa/genome.ann",
        "bwa/genome.bwt",
        "bwa/genome.pac",
        "bwa/genome.sa",
    }
    if suite == "quickstart1":
        required_reference.add("genome.fa.map-ont.mmi")
        expected_fastqs = {
            "illumina_ERR12341627/ERR12341627_1.fastq.gz",
            "illumina_ERR12341627/ERR12341627_2.fastq.gz",
            "ont_DRR165691/fastq_pass/barcode01/DRR165691_1.fastq.gz",
        }
    else:
        expected_fastqs = {
            f"HCC1143_{condition}_{mate}.fastq.gz"
            for condition in ("DMSO", "BEZ235", "TRAMETINIB")
            for mate in ("R1", "R2")
        }
    observed_fastqs = {name for name in input_manifest if name.endswith(".fastq.gz")}
    if observed_fastqs != expected_fastqs:
        raise AuditError(f"public FASTQ manifest mismatch: {observed_fastqs!r}")
    if not required_reference <= set(reference_manifest):
        raise AuditError(
            f"reference manifest lacks {required_reference - set(reference_manifest)!r}"
        )

    qdna_root = context / "qdnaseq-annotation"
    qdna_manifest = verify_manifest_tree(
        qdna_root, context / "manifests/qdnaseq-annotation-manifest.tsv"
    )
    annotation = "QDNAseq.hg38.100kbp.SR50.rds"
    source = "QDNAseq.hg38.100kbp.SR50.source.rda"
    qdna_provenance = annotation + ".provenance.tsv"
    if set(qdna_manifest) != {annotation, source, qdna_provenance}:
        raise AuditError("unexpected qDNAseq audit inventory")
    rows = read_tsv(qdna_root / qdna_provenance)
    if not rows or rows[0] != ["field", "value"]:
        raise AuditError("invalid qDNAseq provenance header")
    qdna = dict(rows[1:])
    if qdna.get("source_commit") != QDNASEQ_COMMIT:
        raise AuditError("qDNAseq source commit mismatch")
    if qdna.get("source_rda_sha256") != sha256(qdna_root / source):
        raise AuditError("qDNAseq source checksum mismatch")
    if qdna.get("rds_sha256") != sha256(qdna_root / annotation):
        raise AuditError("qDNAseq RDS checksum mismatch")

    manifest_names = (
        (
            "v1-illumina-output-manifest.tsv",
            "v2-illumina-output-manifest.tsv",
            "v1-ont-output-manifest.tsv",
            "v2-ont-output-manifest.tsv",
        )
        if suite == "quickstart1"
        else ("v1-hcc1143-output-manifest.tsv", "v2-hcc1143-output-manifest.tsv")
    )
    for name in manifest_names:
        records = read_manifest(context / "manifests" / name)
        if not any(
            path.endswith("/01_tables/refined_bins.tsv.gz") and size > 0
            for path, (_, size) in records.items()
        ):
            raise AuditError(f"output manifest lacks refined bins: {name}")

    native_trace_names = (
        ("v2-illumina-trace.tsv", "v2-ont-trace.tsv")
        if suite == "quickstart1"
        else ("v2-trace.tsv",)
    )
    for name in native_trace_names:
        if (
            "nextflow"
            in require_file(context / name)
            .read_text(encoding="utf-8", errors="replace")
            .lower()
        ):
            raise AuditError(f"native trace invokes Nextflow: {name}")
    if suite == "quickstart1":
        parse_compat(context / "v2-ont-ichorcna-plot-compat.tsv")
    return {
        "input_manifest_sha256": sha256(
            context / "manifests/public-input-manifest.tsv"
        ),
        "reference_manifest_sha256": sha256(
            context / "manifests/shared-reference-manifest.tsv"
        ),
        "qdnaseq_manifest_sha256": sha256(
            context / "manifests/qdnaseq-annotation-manifest.tsv"
        ),
    }


def verify_audit(
    audit: Path,
    suite: str,
    candidate_sha: str,
    source_sha256: str,
    binary_sha256: str,
    check_checksums: bool,
    write_summary: bool,
) -> dict[str, object]:
    if check_checksums:
        verify_checksums(audit)
    nested = verify_nested(audit, suite)
    parity = verify_parity_reports(audit, suite)
    context = verify_context(audit, suite, candidate_sha, source_sha256, binary_sha256)
    summary = {
        "schema": SCHEMA,
        "suite": suite,
        "passed": True,
        "candidate_sha": candidate_sha,
        "source_sha256": source_sha256,
        "binary_sha256": binary_sha256,
        "frozen_v1_commit": V1_COMMIT,
        "frozen_v1_image": V1_IMAGE,
        "samurai_commit": SAMURAI_COMMIT,
        "nextflow": {
            "version": NEXTFLOW_VERSION,
            "url": NEXTFLOW_URL,
            "sha256": NEXTFLOW_SHA256,
        },
        "qdnaseq_source_commit": QDNASEQ_COMMIT,
        "nested": nested,
        "context": context,
        "parity": parity,
    }
    if write_summary:
        (audit / "audit_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        recorded = json.loads(
            require_file(audit / "audit_summary.json").read_text(encoding="utf-8")
        )
        if recorded != summary:
            raise AuditError("audit_summary.json does not match recomputed evidence")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("root", type=Path)
    manifest.add_argument("destination", type=Path)

    checksums = subparsers.add_parser("checksums")
    checksums.add_argument("audit", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--suite", choices=sorted(CONTRACTS), required=True)
    verify.add_argument("--audit", type=Path, required=True)
    verify.add_argument("--candidate-sha", required=True)
    verify.add_argument("--source-sha256", required=True)
    verify.add_argument("--binary-sha256", required=True)
    verify.add_argument("--skip-checksums", action="store_true")
    verify.add_argument("--write-summary", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "manifest":
            write_manifest(args.root, args.destination)
        elif args.command == "checksums":
            write_checksums(args.audit)
        else:
            if re.fullmatch(r"[0-9a-f]{40}", args.candidate_sha) is None:
                raise AuditError(f"invalid candidate SHA: {args.candidate_sha}")
            for label, value in (
                ("source SHA-256", args.source_sha256),
                ("binary SHA-256", args.binary_sha256),
            ):
                if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise AuditError(f"invalid {label}: {value}")
            summary = verify_audit(
                args.audit,
                args.suite,
                args.candidate_sha,
                args.source_sha256,
                args.binary_sha256,
                not args.skip_checksums,
                args.write_summary,
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
    except (AuditError, OSError, ValueError, csv.Error, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
