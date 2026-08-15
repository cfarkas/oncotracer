#!/usr/bin/env python3
"""Build and verify auditable OncoTracer v2 QuickStart parity artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import stat
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
ICHOR_ASSET_IDENTITIES = {
    "GRCh38.GCA_000001405.2_centromere_acen.txt": (
        853,
        "5ca2fed871adaa395773d932b94d40866690f69797694a21b057e8e1b3681e22",
    ),
    "HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds": (
        675953,
        "2f2e94d529d0ef3ca74b93e0814c89fae0f6b918b38a7efec3bf4207c25452c0",
    ),
    "Koren_repTiming_hg38_500kb.wig": (
        59884,
        "d7d20a549fb2a54a91dd73562ca820524a33b8bf33ab45bee881b1e031c96c8c",
    ),
    "gc_hg38_500kb.wig": (
        52184,
        "4ae9c5d7f3e8260b3d192e88b21e717ac7f761946ba16e896b7d375557e85b57",
    ),
    "map_hg38_500kb.wig": (
        54494,
        "18efe127d1fde052b5537d4bf0494f73710fe38eb3ec0e7e49fa483b4c647d89",
    ),
}


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


def read_ichor_asset_manifest(path: Path) -> dict[str, tuple[int, str]]:
    rows = read_tsv(path)
    if not rows or rows[0] != ["filename", "bytes", "sha256"]:
        raise AuditError(f"invalid ichorCNA asset manifest: {path}")
    observed: dict[str, tuple[int, str]] = {}
    for row in rows[1:]:
        if len(row) != 3 or row[0] in observed:
            raise AuditError(f"invalid ichorCNA asset manifest row: {row!r}")
        filename, bytes_text, digest = row
        if not bytes_text.isdigit() or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise AuditError(f"invalid ichorCNA asset identity: {row!r}")
        observed[filename] = (int(bytes_text), digest)
    if list(observed) != sorted(observed):
        raise AuditError("ichorCNA asset manifest is not sorted")
    return observed


def verify_ichor_asset_manifests(context: Path, suite: str) -> str | None:
    frozen = context / "manifests/frozen-ichorcna-assets-manifest.tsv"
    native = context / "manifests/native-ichorcna-assets-manifest.tsv"
    if suite == "quickstart1":
        frozen_assets = read_ichor_asset_manifest(frozen)
        native_assets = read_ichor_asset_manifest(native)
        if frozen_assets != ICHOR_ASSET_IDENTITIES or native_assets != frozen_assets:
            raise AuditError("frozen/native immutable ichorCNA asset identity mismatch")
        if frozen.read_bytes() != native.read_bytes():
            raise AuditError("frozen/native ichorCNA asset manifests differ")
        return sha256(frozen)
    if frozen.exists() or native.exists():
        raise AuditError("unexpected ichorCNA asset manifests for QuickStart 2")
    return None


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


def read_trace_source_audit(
    path: Path,
) -> list[tuple[str, int, int, int, int, str]]:
    table = read_tsv(path)
    if not table or table[0] != TRACE_SOURCE_HEADER:
        raise AuditError(f"invalid nested trace source manifest: {path}")
    records: list[tuple[str, int, int, int, int, str]] = []
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
        records.append(
            (
                identity[0],
                identity[1],
                identity[2],
                row_count,
                successful_rows,
                identity[3],
            )
        )
    if records != sorted(records, key=lambda row: (row[1], row[0])):
        raise AuditError(f"nested trace source manifest is not sorted: {path}")
    if not records:
        raise AuditError(f"nested trace source manifest is empty: {path}")
    return records


def canonical_audit_task(name: str) -> str:
    value = re.sub(r"\s+", " ", name.strip())
    if ":SAMURAI:" in value:
        value = "SAMURAI:" + value.rsplit(":SAMURAI:", 1)[1]
    return value


def render_preserved_combined_trace(
    raw_root: Path,
    source_records: list[tuple[str, int, int, int, int, str]],
) -> bytes:
    """Independently render latest-task evidence from sealed raw trace bytes."""
    absolute_root = raw_root.expanduser().absolute()
    if absolute_root.is_symlink():
        raise AuditError(f"raw trace artifact root is a symbolic link: {raw_root}")
    try:
        root = absolute_root.resolve(strict=True)
    except OSError as error:
        raise AuditError(f"raw trace artifact root is missing: {raw_root}") from error
    if root != absolute_root or not root.is_dir():
        raise AuditError(f"unsafe raw trace artifact root: {raw_root}")

    expected = {record[0] for record in source_records}
    expected_directories = {
        parent.as_posix()
        for relative in expected
        for parent in PurePosixPath(relative).parents
        if parent != PurePosixPath(".")
    }
    actual: set[str] = set()
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise AuditError(f"raw trace artifact contains a symbolic link: {item}")
        resolved = item.resolve(strict=True)
        if resolved != item:
            raise AuditError(f"raw trace artifact has a linked path component: {item}")
        relative = resolved.relative_to(root).as_posix()
        metadata = resolved.stat(follow_symlinks=False)
        if stat.S_ISREG(metadata.st_mode):
            if relative not in expected:
                raise AuditError(f"unexpected raw trace artifact: {relative}")
            actual.add(relative)
        elif stat.S_ISDIR(metadata.st_mode):
            if relative not in expected_directories:
                raise AuditError(f"unexpected raw trace directory: {relative}")
        else:
            raise AuditError(f"unsupported raw trace artifact object: {relative}")
    if actual != expected:
        raise AuditError(
            "raw trace artifact inventory mismatch: "
            f"missing={sorted(expected - actual)!r} extra={sorted(actual - expected)!r}"
        )

    selected: dict[str, tuple[tuple[int, str, int], dict[str, str]]] = {}
    for (
        relative,
        mtime_ns,
        expected_bytes,
        expected_rows,
        expected_success,
        digest,
    ) in source_records:
        path = root.joinpath(*PurePosixPath(relative).parts)
        before = path.stat(follow_symlinks=False)
        content = path.read_bytes()
        after = path.stat(follow_symlinks=False)
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
            raise AuditError(
                f"raw trace changed during sealed verification: {relative}"
            )
        if (
            len(content) != expected_bytes
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise AuditError(f"raw trace identity mismatch: {relative}")
        try:
            text = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
            header = list(reader.fieldnames or [])
            rows = list(reader)
        except (UnicodeDecodeError, csv.Error) as error:
            raise AuditError(f"cannot parse raw trace {relative}: {error}") from error
        required = {"hash", "name", "status", "exit", "container"}
        if not required <= set(header):
            raise AuditError(
                f"raw trace lacks required columns {sorted(required - set(header))}: {relative}"
            )
        successful = 0
        for row_number, row in enumerate(rows, start=2):
            values = {column: (row.get(column) or "").strip() for column in header}
            if (
                values["status"] in {"COMPLETED", "CACHED"}
                and values["exit"] == "0"
                and values["container"]
            ):
                successful += 1
            key = canonical_audit_task(values["name"])
            order = (mtime_ns, relative, row_number)
            previous = selected.get(key)
            if previous is None or order >= previous[0]:
                selected[key] = (order, values)
        if len(rows) != expected_rows or successful != expected_success:
            raise AuditError(
                f"raw trace row-count evidence mismatch: {relative}: "
                f"rows={len(rows)}/{expected_rows} successful={successful}/{expected_success}"
            )
    if not selected:
        raise AuditError("preserved raw traces contain no task occurrences")

    output = io.StringIO(newline="")
    fields = (
        "task_id",
        "hash",
        "name",
        "status",
        "exit",
        "container",
        "source_trace",
        "source_row",
    )
    writer = csv.DictWriter(
        output, fieldnames=fields, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    for task_id, key in enumerate(sorted(selected), start=1):
        order, values = selected[key]
        writer.writerow(
            {
                "task_id": str(task_id),
                "hash": values.get("hash", ""),
                "name": values["name"],
                "status": values["status"],
                "exit": values["exit"],
                "container": values["container"],
                "source_trace": order[1],
                "source_row": str(order[2]),
            }
        )
    return output.getvalue().encode("utf-8")


def verify_preserved_trace_render(
    raw_root: Path, source_manifest: Path, combined_trace: Path
) -> list[tuple[str, int, int, int, int, str]]:
    records = read_trace_source_audit(source_manifest)
    rendered = render_preserved_combined_trace(raw_root, records)
    if rendered != require_file(combined_trace).read_bytes():
        raise AuditError(
            "sealed combined trace is not the deterministic rendering of raw sources"
        )
    return records


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
    raw_source_root = context / f"candidate-{suffix}-trace-source-files"
    candidate_trace_path = context / f"candidate-{suffix}-combined-trace.tsv"
    invocation_name = f"nested-v1-{suffix}-trace-invocation.json"
    invocation_path = context / invocation_name

    pre = read_trace_inventory_audit(pre_path)
    post = read_trace_inventory_audit(post_path)
    delta = read_trace_inventory_audit(delta_path)
    source_records = verify_preserved_trace_render(
        raw_source_root, source_path, trace_path
    )
    sources = [
        (record[0], record[1], record[2], record[5]) for record in source_records
    ]
    if not post or sources != post:
        raise AuditError("source manifest is not the complete post-run trace inventory")
    expected_trace = require_file(trace_path).read_bytes()
    if require_file(candidate_trace_path).read_bytes() != expected_trace:
        raise AuditError("candidate and selected combined trace copies differ")
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
            "selected_contract_source_trace",
            "selected_contract_row_count",
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

    combined_rows = parse_trace(trace_path)
    contracted_rows = [
        row
        for row in combined_rows
        if normalize_process(row.get("name", "")) in contract.processes
    ]
    post_paths = {row[0] for row in post}
    contracted_sources: list[str] = []
    for row in contracted_rows:
        source_trace = row.get("source_trace", "").strip()
        validate_audit_trace_relative(source_trace)
        if source_trace not in post_paths:
            raise AuditError(
                "selected contracted row is absent from post-run trace inventory"
            )
        contracted_sources.append(source_trace)
    current_contract_rows = sum(
        source_trace == newest[0] for source_trace in contracted_sources
    )
    if current_contract_rows < 1:
        raise AuditError("newest current trace contributes no selected contracted task")
    if (
        payload["selected_contract_source_trace"] != newest[0]
        or payload["selected_contract_row_count"] != current_contract_rows
    ):
        raise AuditError("nested invocation JSON has the wrong contract binding")

    ichorcna_source: str | None = None
    if contract.require_ichorcna_compat:
        try:
            require_ichorcna_task_hash(contracted_rows)
        except SystemExit as error:
            raise AuditError(str(error)) from error
        matches = [
            row
            for row in contracted_rows
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


def _unique_mapping(lines: list[str], separator: str, label: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        if not line:
            break
        if separator not in line:
            raise AuditError(f"invalid {label} evidence row: {line!r}")
        key, value = line.split(separator, 1)
        if not key or key in values:
            raise AuditError(f"duplicate or empty {label} evidence key: {key!r}")
        values[key] = value
    return values


def _evidence_integer(values: dict[str, str], key: str) -> int:
    value = values.get(key, "")
    if re.fullmatch(r"[0-9]+", value) is None:
        raise AuditError(f"resource evidence {key!r} is not a nonnegative integer")
    return int(value)


def verify_hosted_resource_evidence(
    context: Path, suite: str, candidate_sha: str | None
) -> dict[str, object]:
    preflight_path = require_file(context / "hosted-resource-preflight.txt")
    preflight = _unique_mapping(
        preflight_path.read_text(encoding="utf-8").splitlines(),
        "=",
        "resource preflight",
    )
    required_preflight = {
        "resource_preflight_schema",
        "resource_preflight_status",
        "resource_preflight_run_id",
        "resource_preflight_run_attempt",
        "resource_preflight_suite",
        "resource_preflight_candidate_sha",
        "resource_preflight_purpose",
        "resource_preflight_minimum_available_kib",
        "resource_preflight_checked_path_count",
        "resource_preflight_unique_device_count",
        "resource_preflight_required_free_gib",
        "resource_preflight_mem_total_kib",
        "resource_preflight_required_physical_gib",
        "resource_preflight_swap_total_kib",
        "resource_preflight_planned_swap_gib",
        "resource_preflight_expected_swap_file",
        "resource_preflight_required_addressable_gib",
        "resource_preflight_standard_contract_free_gib",
    }
    if not required_preflight <= set(preflight):
        raise AuditError(
            f"resource preflight static key inventory mismatch: {set(preflight)!r}"
        )
    if (
        preflight["resource_preflight_schema"]
        != "oncotracer-hosted-resource-preflight-v3"
        or preflight["resource_preflight_status"] != "PASS"
        or preflight["resource_preflight_suite"] != suite
    ):
        raise AuditError("resource preflight identity or status mismatch")
    run_id = preflight["resource_preflight_run_id"]
    run_attempt = preflight["resource_preflight_run_attempt"]
    evidence_sha = preflight["resource_preflight_candidate_sha"]
    if (
        re.fullmatch(r"[1-9][0-9]*", run_id) is None
        or re.fullmatch(r"[1-9][0-9]*", run_attempt) is None
        or re.fullmatch(r"[0-9a-f]{40}", evidence_sha) is None
        or (candidate_sha is not None and evidence_sha != candidate_sha)
    ):
        raise AuditError("resource preflight run or source identity mismatch")
    thresholds = {
        "minimum_free_gib": _evidence_integer(
            preflight, "resource_preflight_required_free_gib"
        ),
        "minimum_physical_gib": _evidence_integer(
            preflight, "resource_preflight_required_physical_gib"
        ),
        "minimum_addressable_gib": _evidence_integer(
            preflight, "resource_preflight_required_addressable_gib"
        ),
        "planned_swap_gib": _evidence_integer(
            preflight, "resource_preflight_planned_swap_gib"
        ),
    }
    valid_threshold_models = (
        {
            "minimum_free_gib": 72,
            "minimum_physical_gib": 15,
            "minimum_addressable_gib": 47,
            "planned_swap_gib": 32,
        },
        {
            "minimum_free_gib": 40,
            "minimum_physical_gib": 15,
            "minimum_addressable_gib": 47,
            "planned_swap_gib": 0,
        },
    )
    if (
        thresholds not in valid_threshold_models
        or _evidence_integer(preflight, "resource_preflight_standard_contract_free_gib")
        != 14
    ):
        raise AuditError(f"resource preflight threshold model mismatch: {thresholds!r}")
    kib_per_gib = 1024 * 1024
    checked_path_count = _evidence_integer(
        preflight, "resource_preflight_checked_path_count"
    )
    if checked_path_count != 4:
        raise AuditError("parity resource preflight must bind four checked paths")
    dynamic_keys: set[str] = set()
    checked_paths: list[tuple[str, str, int]] = []
    for index in range(checked_path_count):
        prefix = f"resource_preflight_checked_path_{index:03d}_"
        path_key = prefix + "path"
        device_key = prefix + "device"
        available_key = prefix + "available_kib"
        dynamic_keys.update((path_key, device_key, available_key))
        path = preflight.get(path_key, "")
        device = preflight.get(device_key, "")
        available = _evidence_integer(preflight, available_key)
        if (
            not path.startswith("/")
            or ".." in PurePosixPath(path).parts
            or not device
            or available < thresholds["minimum_free_gib"] * kib_per_gib
        ):
            raise AuditError(f"resource preflight checked path {index} is invalid")
        checked_paths.append((path, device, available))
    if set(preflight) != required_preflight | dynamic_keys:
        raise AuditError(
            f"resource preflight key inventory mismatch: {set(preflight)!r}"
        )
    if len({path for path, _, _ in checked_paths}) != checked_path_count:
        raise AuditError("resource preflight checked paths are not unique")
    if _evidence_integer(preflight, "resource_preflight_minimum_available_kib") != min(
        available for _, _, available in checked_paths
    ):
        raise AuditError("resource preflight minimum storage observation mismatch")
    if _evidence_integer(preflight, "resource_preflight_unique_device_count") != len(
        {device for _, device, _ in checked_paths}
    ):
        raise AuditError("resource preflight unique-device count mismatch")
    if _evidence_integer(preflight, "resource_preflight_mem_total_kib") < thresholds[
        "minimum_physical_gib"
    ] * kib_per_gib or (
        _evidence_integer(preflight, "resource_preflight_mem_total_kib")
        + _evidence_integer(preflight, "resource_preflight_swap_total_kib")
        + thresholds["planned_swap_gib"] * kib_per_gib
        < thresholds["minimum_addressable_gib"] * kib_per_gib
    ):
        raise AuditError("resource preflight observations do not satisfy thresholds")
    expected_swap = preflight["resource_preflight_expected_swap_file"]
    runner_temp_candidates = [
        path
        for path, _, _ in checked_paths
        if path == PurePosixPath(expected_swap).parent.as_posix()
    ]
    if thresholds["planned_swap_gib"] == 0:
        if expected_swap != "none":
            raise AuditError("zero-swap resource preflight must bind literal none")
    elif (
        not expected_swap.startswith("/")
        or PurePosixPath(expected_swap).name
        != f"oncotracer-swap-{run_id}-{run_attempt}"
        or ".." in PurePosixPath(expected_swap).parts
    ):
        raise AuditError("resource preflight swap is not exact and job-owned")
    if thresholds["planned_swap_gib"] > 0 and len(runner_temp_candidates) != 1:
        raise AuditError(
            "resource preflight swap parent is not a checked filesystem path"
        )

    phase_names = (
        "preflight-passed",
        "swap-active",
        "public-inputs-ready",
        "frozen-images-ready",
        "frozen-traces-authenticated",
        "frozen-reference-released",
        "frozen-images-released",
        "native-environments-with-cache",
        "native-package-cache-released",
        "native-runs-complete",
        "final",
    )
    phase_root = context / "hosted-resource-phases"
    observed_phases = {path.stem for path in phase_root.glob("*.txt") if path.is_file()}
    if observed_phases != set(phase_names):
        raise AuditError(f"resource phase evidence mismatch: {observed_phases!r}")
    for index, phase in enumerate(phase_names, start=1):
        phase_path = require_file(phase_root / f"{phase}.txt")
        text = phase_path.read_text(encoding="utf-8", errors="strict")
        headers = _unique_mapping(text.splitlines(), "\t", f"resource phase {phase}")
        for marker in (
            "[df-kibibytes]",
            "[memory-kibibytes]",
            "[active-swap]",
            "[docker]",
            "[path-bytes]",
        ):
            if text.count(marker) != 1:
                raise AuditError(
                    f"resource phase {phase!r} lacks one required marker {marker!r}"
                )
        if (
            headers.get("schema") != "oncotracer-hosted-resource-phase-v2"
            or headers.get("run_id") != run_id
            or headers.get("run_attempt") != run_attempt
            or headers.get("suite") != suite
            or headers.get("candidate_sha") != evidence_sha
            or headers.get("phase") != phase
            or _evidence_integer(headers, "phase_index") != index
        ):
            raise AuditError(f"resource phase {phase!r} identity/order mismatch")
        for key, expected in thresholds.items():
            if _evidence_integer(headers, key) != expected:
                raise AuditError(
                    f"resource phase {phase!r} threshold mismatch for {key}"
                )
        if _evidence_integer(headers, "filesystem_reserve_gib") != 8:
            raise AuditError(f"resource phase {phase!r} reserve threshold mismatch")
        runner_temp = headers.get("runner_temp", "")
        if (
            not runner_temp.startswith("/")
            or headers.get("expected_swap_file") != expected_swap
        ):
            raise AuditError(f"resource phase {phase!r} swap ownership mismatch")
        if thresholds["planned_swap_gib"] > 0 and (
            PurePosixPath(expected_swap).parent != PurePosixPath(runner_temp)
        ):
            raise AuditError(f"resource phase {phase!r} swap ownership mismatch")
        swap_required = _evidence_integer(headers, "swap_required")
        swap_size = _evidence_integer(headers, "active_swap_size_bytes")
        swap_used = _evidence_integer(headers, "active_swap_used_bytes")
        if phase == "preflight-passed" or thresholds["planned_swap_gib"] == 0:
            if swap_required != 0 or swap_size != 0 or swap_used != 0:
                raise AuditError(f"resource phase {phase!r} falsely records job swap")
        elif (
            swap_required != 1
            or swap_size < thresholds["planned_swap_gib"] * 1024**3
            or swap_used > swap_size
        ):
            raise AuditError(f"resource phase {phase!r} lacks exact planned swap")
        available = _evidence_integer(headers, "minimum_available_kib")
        memory = _evidence_integer(headers, "mem_total_kib")
        total_swap = _evidence_integer(headers, "swap_total_kib")
        if available < 8 * kib_per_gib:
            raise AuditError(f"resource phase {phase!r} exhausted its storage reserve")
        if memory < thresholds["minimum_physical_gib"] * kib_per_gib:
            raise AuditError(f"resource phase {phase!r} lacks physical memory")
        phase_addressable = memory + total_swap
        if phase == "preflight-passed":
            phase_addressable += thresholds["planned_swap_gib"] * kib_per_gib
        if phase_addressable < thresholds["minimum_addressable_gib"] * kib_per_gib:
            raise AuditError(f"resource phase {phase!r} lacks addressable memory")
    final_path = require_file(context / "hosted-resource-final.txt")
    if final_path.read_bytes() != (phase_root / "final.txt").read_bytes():
        raise AuditError("final resource evidence is not the sealed final phase")
    return {
        "run_id": int(run_id),
        "run_attempt": int(run_attempt),
        "candidate_sha": evidence_sha,
        "phase_count": len(phase_names),
        "preflight_sha256": sha256(preflight_path),
        "checked_filesystems": [
            {"path": path, "device": device, "available_kib": available}
            for path, device, available in checked_paths
        ],
        "final_sha256": sha256(final_path),
    }


def verify_minimal_native_environments(
    context: Path, suite: str, candidate_sha: str | None = None
) -> dict[str, object]:
    required_probes = {
        "core": {"bwa", "samtools", "minimap2", "pigz", "picard"},
        "qdnaseq": {"rscript"},
    }
    if suite == "quickstart1":
        required_probes["ichorcna"] = {"rscript", "readcounter"}
    required_environments = set(required_probes)

    inventory = read_tsv(context / "native-environment-inventory.tsv")
    if not inventory or inventory[0] != [
        "environment",
        "definition_sha256",
        "explicit_sha256",
    ]:
        raise AuditError("invalid native environment inventory header")
    if any(len(row) != 3 for row in inventory[1:]):
        raise AuditError("invalid native environment inventory row")
    inventory_by_environment = {row[0]: row[1:] for row in inventory[1:]}
    if (
        set(inventory_by_environment) != required_environments
        or len(inventory) != len(required_environments) + 1
    ):
        raise AuditError(
            f"native environment inventory mismatch: {set(inventory_by_environment)!r}"
        )
    for environment, (definition_digest, explicit_digest) in sorted(
        inventory_by_environment.items()
    ):
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (definition_digest, explicit_digest)
        ):
            raise AuditError(f"invalid native environment digest: {environment}")
        definition = require_file(
            context / "native-environments" / f"{environment}.yml"
        )
        explicit = require_file(context / f"native-{environment}.explicit.txt")
        if (
            sha256(definition) != definition_digest
            or sha256(explicit) != explicit_digest
        ):
            raise AuditError(f"native environment evidence mismatch: {environment}")

    forbidden_environments = {"classifier", "gistic"} | (
        {"ichorcna"} if suite == "quickstart2" else set()
    )
    for environment in forbidden_environments:
        for path in (
            context / "native-environments" / f"{environment}.yml",
            context / f"native-{environment}.explicit.txt",
        ):
            if path.exists() or path.is_symlink():
                raise AuditError(
                    f"unused environment leaked into {suite} parity evidence: {path.name}"
                )

    probes = read_tsv(context / "native-environment-probes.tsv")
    if not probes or probes[0] != [
        "environment",
        "probe",
        "result",
        "evidence_sha256",
    ]:
        raise AuditError("invalid native environment probe header")
    if any(len(row) != 4 for row in probes[1:]):
        raise AuditError("invalid native environment probe row")
    expected = {
        (environment, probe)
        for environment, names in required_probes.items()
        for probe in names
    }
    observed: dict[tuple[str, str], tuple[str, str]] = {}
    for environment, probe, result, evidence_digest in probes[1:]:
        key = (environment, probe)
        if key in observed:
            raise AuditError(f"duplicate native environment probe: {key!r}")
        if result != "PASS" or re.fullmatch(r"[0-9a-f]{64}", evidence_digest) is None:
            raise AuditError(f"failed native environment probe: {key!r}")
        evidence = require_file(
            context / "native-environment-probes" / f"{environment}-{probe}.txt"
        )
        if sha256(evidence) != evidence_digest:
            raise AuditError(f"native environment probe checksum mismatch: {key!r}")
        observed[key] = (result, evidence_digest)
    if set(observed) != expected:
        raise AuditError(
            f"native environment probe inventory mismatch: {set(observed)!r}"
        )

    resources = verify_hosted_resource_evidence(context, suite, candidate_sha)
    return {
        "environments": sorted(required_environments),
        "probe_count": len(expected),
        "inventory_sha256": sha256(context / "native-environment-inventory.tsv"),
        "probes_sha256": sha256(context / "native-environment-probes.tsv"),
        "resources": resources,
    }


def verify_job_image_actions(context: Path) -> dict[str, object]:
    pin_rows = read_tsv(context / "nested-v1-container-pins.tsv")
    if not pin_rows or pin_rows[0] != ["container", "manifest_digest"]:
        raise AuditError("invalid nested pin manifest for image action audit")
    pins = dict(pin_rows[1:])
    expected_references = {V1_IMAGE}
    for container, digest in pins.items():
        expected_references.add(container)
        expected_references.add(f"{container.rsplit(':', 1)[0]}@{digest}")

    ownership = read_tsv(context / "job-image-reference-ownership.tsv")
    if not ownership or ownership[0] != [
        "reference",
        "image_id",
        "created_by_job",
    ]:
        raise AuditError("invalid job image ownership header")
    if any(len(row) != 3 for row in ownership[1:]):
        raise AuditError("invalid job image ownership row")
    owned: dict[str, tuple[str, str]] = {}
    for reference, image_id, created_by_job in ownership[1:]:
        if (
            reference in owned
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or created_by_job not in {"0", "1"}
        ):
            raise AuditError(f"invalid job image ownership: {reference!r}")
        owned[reference] = (image_id, created_by_job)
    if set(owned) != expected_references:
        raise AuditError(f"job image ownership inventory mismatch: {set(owned)!r}")

    actions = read_tsv(context / "job-image-reference-actions.tsv")
    if not actions or actions[0] != ["reference", "image_id", "action"]:
        raise AuditError("invalid job image action header")
    if any(len(row) != 3 for row in actions[1:]):
        raise AuditError("invalid job image action row")
    observed_actions: dict[str, tuple[str, str]] = {}
    preexisting_image_ids = {
        image_id for image_id, created_by_job in owned.values() if created_by_job == "0"
    }
    for reference, image_id, action in actions[1:]:
        expected = owned.get(reference)
        if reference in observed_actions or expected is None or image_id != expected[0]:
            raise AuditError(f"invalid job image action identity: {reference!r}")
        expected_actions = {"PRESERVED_PREEXISTING"}
        if expected[1] == "1":
            if image_id in preexisting_image_ids:
                expected_actions = {"PRESERVED_JOB_CREATED_SHARED"}
            else:
                expected_actions = {"REMOVED_JOB_CREATED"}
        if action not in expected_actions:
            raise AuditError(f"invalid job image action: {reference!r}")
        observed_actions[reference] = (image_id, action)
    if set(observed_actions) != expected_references:
        raise AuditError("job image action inventory is incomplete")
    return {
        "reference_count": len(expected_references),
        "removed_count": sum(
            action == "REMOVED_JOB_CREATED" for _, action in observed_actions.values()
        ),
        "shared_preserved_count": sum(
            action == "PRESERVED_JOB_CREATED_SHARED"
            for _, action in observed_actions.values()
        ),
        "ownership_sha256": sha256(context / "job-image-reference-ownership.tsv"),
        "actions_sha256": sha256(context / "job-image-reference-actions.tsv"),
    }


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
    native_environments = verify_minimal_native_environments(
        context, suite, candidate_sha
    )
    image_actions = verify_job_image_actions(context)

    input_manifest = read_manifest(context / "manifests/public-input-manifest.tsv")
    reference_manifest = read_manifest(
        context / "manifests/shared-reference-manifest.tsv"
    )
    native_reference_manifest = read_manifest(
        context / "manifests/native-reference-manifest.tsv"
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
    if not required_reference <= set(native_reference_manifest):
        raise AuditError(
            "native reference manifest lacks "
            f"{required_reference - set(native_reference_manifest)!r}"
        )
    for name in ("genome.fa", "genome.fa.fai", "genome.dict"):
        if native_reference_manifest[name] != reference_manifest[name]:
            raise AuditError(
                f"native/frozen canonical reference identity mismatch: {name}"
            )

    ichor_asset_manifest_sha256 = verify_ichor_asset_manifests(context, suite)

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
        "native_environments": native_environments,
        "job_image_actions": image_actions,
        "input_manifest_sha256": sha256(
            context / "manifests/public-input-manifest.tsv"
        ),
        "reference_manifest_sha256": sha256(
            context / "manifests/shared-reference-manifest.tsv"
        ),
        "native_reference_manifest_sha256": sha256(
            context / "manifests/native-reference-manifest.tsv"
        ),
        "ichorcna_asset_manifest_sha256": ichor_asset_manifest_sha256,
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

    trace_proof = subparsers.add_parser("verify-trace-proof")
    trace_proof.add_argument("--raw-root", type=Path, required=True)
    trace_proof.add_argument("--source-manifest", type=Path, required=True)
    trace_proof.add_argument("--combined-trace", type=Path, required=True)

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
        elif args.command == "verify-trace-proof":
            records = verify_preserved_trace_render(
                args.raw_root, args.source_manifest, args.combined_trace
            )
            print(
                json.dumps(
                    {
                        "schema": "oncotracer-preserved-nested-trace-proof-v1",
                        "passed": True,
                        "source_trace_count": len(records),
                        "source_manifest_sha256": sha256(args.source_manifest),
                        "combined_trace_sha256": sha256(args.combined_trace),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
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
