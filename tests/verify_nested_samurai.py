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
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from combine_nested_samurai_traces import (
    SOURCE_MANIFEST_COLUMNS,
    TraceIdentity,
    capture_trace_inventory,
    combine_root,
    materialize_trace_sources,
    read_source_manifest,
    read_trace_inventory,
    recompute_preserved_trace_artifact,
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

    selected = list(selected_rows)
    post_by_path = {row.source_trace: row for row in post}
    selected_sources: list[str] = []
    for row in selected:
        source_trace = row.get("source_trace", "").strip()
        try:
            validate_trace_relative(source_trace)
        except ValueError as error:
            raise SystemExit(
                f"selected contracted row has unsafe source trace: {error}"
            ) from error
        if source_trace not in post_by_path:
            raise SystemExit(
                "selected contracted row is absent from the complete post-run "
                f"inventory: {source_trace}"
            )
        selected_sources.append(source_trace)
    current_contract_rows = sum(
        source_trace == newest.source_trace for source_trace in selected_sources
    )
    if current_contract_rows < 1:
        raise SystemExit(
            "deterministic newest current-invocation trace contributes no selected "
            "contracted scientific task"
        )

    selected_ichorcna_source: str | None = None
    if require_ichorcna:
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
        "selected_contract_source_trace": newest.source_trace,
        "selected_contract_row_count": current_contract_rows,
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


SERVER_ILLUMINA_PROCESSES = frozenset(
    {
        "SAMURAI:FASTQ_TRIM_FASTP_FASTQC:FASTQC_RAW",
        "SAMURAI:FASTQ_ALIGN_DNA:BWAMEM1_MEM",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:PICARD_MARKDUPLICATES",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:SAMTOOLS_INDEX",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_STATS",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_FLAGSTAT",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_IDXSTATS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:SOLID_BIOPSY:QDNASEQ",
        "SAMURAI:SOLID_BIOPSY:CONCATENATE_QDNASEQ_PLOTS",
        "SAMURAI:MULTIQC",
    }
)
SERVER_ONT_PROCESSES = ONT_PROCESSES
SERVER_ILLUMINA_IMAGES = frozenset(
    {
        "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0",
        "community.wave.seqera.io/library/bwa_htslib_samtools:83b50ff84ead50d0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    }
)
SERVER_ONT_IMAGES = ONT_IMAGES
SERVER_IMAGE_DIGESTS = {
    "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0": "sha256:e194048df39c3145d9b4e0a14f4da20b59d59250465b6f2a9cb698445fd45900",
    "community.wave.seqera.io/library/bwa_htslib_samtools:83b50ff84ead50d0": "sha256:48812e48a9462145c065d1b8e15d996c4a2c4c69469f1249fb601f25939cd48e",
    "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6": "sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5",
    "quay.io/biocontainers/samtools:1.22.1--h96c455f_0": "sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2",
    "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1": "sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75",
    "docker.io/t0shy/qpdf-docker:11.3.0": "sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3",
    "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf": "sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d",
    "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2": "sha256:209b7aeca568155a099873da6e830427bf0a9d5418426b39f913db736d53e20b",
    "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4": "sha256:c6240b1bcc57de07d9a92373f6fad080870bba0075be6cd25c6d37179d928c72",
    "community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3": "sha256:3b7464a65a9b23f0969b19767303b8727ab9f7dce83b1885cd8f6334d75ed59e",
    "community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a": "sha256:28626b999449abe6ddc2167228023ec93e90d109a540be97d0a789d2093b4e8b",
    "quay.io/einar_rainhart/pandas-pandera:1.5.3": "sha256:39fae3f3a2edb8cb174b3ffade1741b6b1ec850a323b4f7a0dca6908f2e49cf8",
}
SERVER_TRACE_CONTRACTS = {
    ("v1-illumina-samurai", "illumina", 12): (
        SERVER_ILLUMINA_PROCESSES,
        SERVER_ILLUMINA_IMAGES,
    ),
    ("v1-ont-samurai", "ont", 10): (SERVER_ONT_PROCESSES, SERVER_ONT_IMAGES),
    ("v1-hcc1143-samurai", "illumina", 32): (
        SERVER_ILLUMINA_PROCESSES,
        SERVER_ILLUMINA_IMAGES,
    ),
}
SERVER_EVIDENCE_KEYS = {
    "schema",
    "mode",
    "evidence_mode",
    "source_trace",
    "source_trace_sha256",
    "source_manifest",
    "source_manifest_sha256",
    "source_files",
    "available_traces",
    "contract_row_count",
    "row_count",
    "processes",
    "contract_processes",
    "containers",
    "contract_containers",
    "ichorcna_plot_compat",
    "rows",
    "trace_invocation",
}
SERVER_ROW_KEYS = {
    "hash",
    "name",
    "normalized_process",
    "status",
    "exit",
    "container",
    "canonical_container",
    "repo_digest",
    "source_trace",
    "source_row",
}


def _absolute_json_strings(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
            found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_absolute_json_strings(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_absolute_json_strings(item))
    return found


def _read_server_container_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if (
                len(row) != 2
                or row[0] in pins
                or re.fullmatch(r"sha256:[0-9a-f]{64}", row[1]) is None
            ):
                raise SystemExit(f"invalid sealed SAMURAI container pin: {row!r}")
            pins[row[0]] = row[1]
    if pins != SERVER_IMAGE_DIGESTS:
        raise SystemExit("sealed SAMURAI container pins differ from release policy")
    return pins


def _verify_server_container_identities(context: Path) -> None:
    path = context / "samurai-container-identities.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or rows[0] != ["tag", "pinned_reference", "image_id", "repo_digests"]:
        raise SystemExit("invalid sealed SAMURAI container identity header")
    observed: dict[str, tuple[str, str, str]] = {}
    for row in rows[1:]:
        if len(row) != 4 or row[0] in observed:
            raise SystemExit(f"invalid sealed SAMURAI container identity: {row!r}")
        tag, pinned, image_id, repo_digests = row
        digest = SERVER_IMAGE_DIGESTS.get(tag)
        if (
            digest is None
            or pinned != f"{tag}@{digest}"
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or not any(
                value.endswith(f"@{digest}") for value in repo_digests.split(",")
            )
        ):
            raise SystemExit(f"unauthenticated sealed SAMURAI identity: {tag!r}")
        observed[tag] = (pinned, image_id, repo_digests)
    if set(observed) != set(SERVER_IMAGE_DIGESTS):
        raise SystemExit("sealed SAMURAI container identity inventory is incomplete")


def _resolve_server_container(value: str, pins: dict[str, str]) -> tuple[str, str]:
    normalized = value.strip().removeprefix("docker://")
    matches: list[tuple[str, str]] = []
    for tag, digest in pins.items():
        aliases = {tag, f"{tag}@{digest}"}
        if tag.startswith("quay.io/biocontainers/"):
            aliases.add(tag.removeprefix("quay.io/"))
        if tag.startswith("docker.io/"):
            aliases.add(tag.removeprefix("docker.io/"))
        if normalized in aliases:
            matches.append((tag, digest))
    if len(matches) != 1:
        raise SystemExit(f"unresolved sealed SAMURAI container: {value!r}")
    return matches[0]


def verify_preserved_server_trace_proof(
    context: Path, prefix: str, mode: str, expected_rows: int
) -> dict[str, object]:
    """Reverify one extracted server trace proof without its live analysis root."""
    contract = SERVER_TRACE_CONTRACTS.get((prefix, mode, expected_rows))
    if contract is None:
        raise SystemExit(
            f"unsupported server trace artifact contract: {(prefix, mode, expected_rows)!r}"
        )
    expected_processes, expected_images = contract
    context = context.expanduser().resolve()
    if not context.is_dir():
        raise SystemExit(f"server trace artifact context is missing: {context}")
    manifest = context / f"{prefix}-trace-sources.tsv"
    raw_root = context / f"{prefix}-trace-source-files"
    combined = context / f"{prefix}-execution-trace.txt"
    evidence_path = context / f"{prefix}-trace-audit.json"
    pre_path = context / f"{prefix}-trace-pre.tsv"
    post_path = context / f"{prefix}-trace-post.tsv"
    delta_path = context / f"{prefix}-trace-delta.tsv"

    pins = _read_server_container_pins(context / "samurai-container-pins.tsv")
    _verify_server_container_identities(context)
    with tempfile.TemporaryDirectory(
        prefix=f"oncotracer-{prefix}-sealed-"
    ) as directory:
        recomputed, regenerated, _ = recompute_preserved_trace_artifact(
            raw_root, manifest, Path(directory)
        )
        if recomputed.read_bytes() != combined.read_bytes():
            raise SystemExit(
                f"preserved raw traces do not reproduce server proof {prefix}"
            )
        if regenerated.read_bytes() != manifest.read_bytes():
            raise SystemExit(f"preserved source manifest changed for {prefix}")

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        not isinstance(evidence, dict)
        or set(evidence) != SERVER_EVIDENCE_KEYS
        or evidence.get("schema") != "oncotracer-samurai-trace-audit-v1"
        or evidence.get("evidence_mode") != "complete-combined-trace"
    ):
        raise SystemExit("invalid sealed server trace proof schema")
    absolute_values = _absolute_json_strings(evidence)
    if absolute_values:
        raise SystemExit(
            f"server trace proof leaks absolute paths: {absolute_values[:3]!r}"
        )
    records = read_source_manifest(manifest)
    post = read_trace_inventory(post_path)
    pre = read_trace_inventory(pre_path)
    delta = read_trace_inventory(delta_path)
    if [record.identity for record in records] != post:
        raise SystemExit("server source manifest is not the complete post inventory")
    expected_delta = trace_inventory_delta(pre, post)
    if not expected_delta or delta != expected_delta:
        raise SystemExit("server trace proof has an empty or forged current delta")
    newest = max(post, key=lambda row: (row.mtime_ns, row.source_trace))
    if newest not in delta:
        raise SystemExit("server trace proof newest trace is not current")

    combined_rows = parse_trace(combined)
    contracted = [
        row
        for row in combined_rows
        if normalize_process(row.get("name", "")) in expected_processes
    ]
    if (
        len(contracted) != expected_rows
        or {normalize_process(row.get("name", "")) for row in contracted}
        != expected_processes
    ):
        raise SystemExit("server combined trace process contract mismatch")
    recomputed_rows: list[dict[str, str]] = []
    for raw_row in contracted:
        process = normalize_process(raw_row.get("name", ""))
        status = (raw_row.get("status") or "").strip().upper()
        exit_code = (raw_row.get("exit") or "").strip()
        task_hash = (raw_row.get("hash") or "").strip().lower()
        container = (raw_row.get("container") or "").strip().removeprefix("docker://")
        canonical, digest = _resolve_server_container(container, pins)
        if (
            status not in {"COMPLETED", "CACHED"}
            or exit_code != "0"
            or re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{6,}", task_hash) is None
        ):
            raise SystemExit(
                "server combined trace contains a non-passing contracted task"
            )
        recomputed_rows.append(
            {
                "hash": task_hash,
                "name": (raw_row.get("name") or "").strip(),
                "normalized_process": process,
                "status": status,
                "exit": exit_code,
                "container": container,
                "canonical_container": canonical,
                "repo_digest": digest,
                "source_trace": (raw_row.get("source_trace") or "").strip(),
                "source_row": (raw_row.get("source_row") or "").strip(),
            }
        )
    selected = evidence.get("rows")
    if (
        not isinstance(selected, list)
        or any(
            not isinstance(row, dict) or set(row) != SERVER_ROW_KEYS for row in selected
        )
        or selected != recomputed_rows
    ):
        raise SystemExit("server trace proof rows do not independently recompute")

    post_by_path = {row.source_trace: row for row in post}
    selected_sources: list[str] = []
    for row in recomputed_rows:
        source_trace = row["source_trace"]
        try:
            validate_trace_relative(source_trace)
        except ValueError as error:
            raise SystemExit(f"unsafe server selected trace source: {error}") from error
        if source_trace not in post_by_path:
            raise SystemExit("server selected row is absent from post inventory")
        selected_sources.append(source_trace)
    current_count = sum(source == newest.source_trace for source in selected_sources)
    if current_count < 1:
        raise SystemExit("server newest trace contributes no contracted task")
    require_ichor = mode == "ont"
    selected_ichor: str | None = None
    selected_ichor_hash: str | None = None
    if require_ichor:
        selected_ichor_hash = require_ichorcna_task_hash(recomputed_rows)
        ichor_rows = [
            row
            for row in recomputed_rows
            if normalize_process(row["name"]) == ICHORCNA_RUN_PROCESS
        ]
        if len(ichor_rows) != 1:
            raise SystemExit("server proof has ambiguous ICHORCNA_RUN evidence")
        selected_ichor = ichor_rows[0]["source_trace"]
        if selected_ichor != newest.source_trace:
            raise SystemExit("server ICHORCNA_RUN is not from the newest trace")

    invocation = evidence.get("trace_invocation")
    expected_invocation = {
        "schema": "oncotracer-nested-trace-invocation-v1",
        "newest_source_trace": newest.source_trace,
        "selected_contract_source_trace": newest.source_trace,
        "selected_contract_row_count": current_count,
        "selected_ichorcna_source_trace": selected_ichor,
        "pre": {
            "sha256": sha256(pre_path),
            "entries": [identity_record(row) for row in pre],
        },
        "post": {
            "sha256": sha256(post_path),
            "entries": [identity_record(row) for row in post],
        },
        "delta": {
            "sha256": sha256(delta_path),
            "entries": [identity_record(row) for row in delta],
        },
    }
    if invocation != expected_invocation:
        raise SystemExit("server current-invocation evidence does not recompute")
    if evidence.get("source_trace") != combined.name:
        raise SystemExit("server proof has the wrong context-relative combined trace")
    if evidence.get("source_manifest") != manifest.name:
        raise SystemExit("server proof has the wrong context-relative source manifest")
    if evidence.get("source_files") != raw_root.name:
        raise SystemExit("server proof has the wrong context-relative raw source root")
    if evidence.get("source_trace_sha256") != sha256(combined):
        raise SystemExit("server combined trace checksum mismatch")
    if evidence.get("source_manifest_sha256") != sha256(manifest):
        raise SystemExit("server source manifest checksum mismatch")
    if (
        evidence.get("mode") != mode
        or evidence.get("contract_row_count") != expected_rows
        or evidence.get("row_count") != expected_rows
        or evidence.get("processes") != sorted(expected_processes)
        or evidence.get("contract_processes") != sorted(expected_processes)
        or evidence.get("containers") != sorted(expected_images)
        or evidence.get("contract_containers") != sorted(expected_images)
        or {row["canonical_container"] for row in recomputed_rows} != expected_images
    ):
        raise SystemExit("server trace proof scientific/runtime contract mismatch")
    available = evidence.get("available_traces")
    expected_available = [
        {
            "relative_path": record.identity.source_trace,
            "artifact_path": f"{raw_root.name}/{record.identity.source_trace}",
            "mtime_ns": record.identity.mtime_ns,
            "bytes": record.identity.bytes,
            "rows": record.rows,
            "successful_rows": record.successful_rows,
            "sha256": record.identity.sha256,
        }
        for record in records
    ]
    if available != expected_available:
        raise SystemExit("server trace proof raw-source inventory does not recompute")
    compatibility = evidence.get("ichorcna_plot_compat")
    if mode == "ont":
        marker = context / f"{prefix}-ichorcna-plot-compat.tsv"
        if not isinstance(compatibility, dict):
            raise SystemExit("server ONT proof lacks compatibility evidence")
        task_hash = compatibility.get("task_hash", "")
        relative_work = Path(compatibility.get("relative_path", ""))
        if (
            task_hash != selected_ichor_hash
            or compatibility.get("artifact") != marker.name
            or compatibility.get("sha256") != sha256(marker)
            or compatibility.get("metadata") != parse_compat(marker)
            or not marker_path_matches_task_hash(relative_work, task_hash)
        ):
            raise SystemExit("server ONT compatibility evidence does not recompute")
    elif compatibility is not None:
        raise SystemExit("non-ONT server proof contains compatibility evidence")
    print(f"verified preserved server trace proof: {prefix}")
    return evidence


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
    parser.add_argument("--verify-artifact-context", type=Path)
    parser.add_argument("--artifact-prefix")
    parser.add_argument("--artifact-mode", choices=("illumina", "ont"))
    parser.add_argument("--artifact-expected-rows", type=int)
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
    artifact_values = (
        args.verify_artifact_context,
        args.artifact_prefix,
        args.artifact_mode,
        args.artifact_expected_rows,
    )
    if any(value is not None for value in artifact_values):
        if any(value is None for value in artifact_values):
            raise SystemExit(
                "all preserved server trace artifact arguments are required"
            )
        verify_preserved_server_trace_proof(
            args.verify_artifact_context,
            args.artifact_prefix,
            args.artifact_mode,
            args.artifact_expected_rows,
        )
        return 0
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
        raw_source_audit = (
            args.selected_dir / f"candidate-{diagnostic_prefix}-trace-source-files"
        )
        materialize_trace_sources(root, source_manifest, raw_source_audit)
        with tempfile.TemporaryDirectory(
            prefix=f"oncotracer-{diagnostic_prefix}-trace-recompute-"
        ) as recompute_directory:
            recomputed_trace, recomputed_manifest, _ = (
                recompute_preserved_trace_artifact(
                    raw_source_audit, source_audit, Path(recompute_directory)
                )
            )
            if recomputed_trace.read_bytes() != combined_audit.read_bytes():
                raise SystemExit(
                    f"preserved raw traces do not reproduce {contract.label}"
                )
            if recomputed_manifest.read_bytes() != source_audit.read_bytes():
                raise SystemExit(
                    f"preserved source manifest changed for {contract.label}"
                )

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
                f"complete-combined-trace:{combined_audit.name};current:{invocation['newest_source_trace']}",
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
