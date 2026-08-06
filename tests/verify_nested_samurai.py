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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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

REQUIRED_TRACE_COLUMNS = {"name", "status", "exit", "container"}


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
        return False, f"missing columns {sorted(REQUIRED_TRACE_COLUMNS - set(rows[0]))}", rows, set()
    if len(rows) != contract.expected_rows:
        return False, f"row count {len(rows)} != {contract.expected_rows}", rows, set()
    if any(
        row["status"] not in {"COMPLETED", "CACHED"}
        or row["exit"] != "0"
        or not row["container"].strip()
        for row in rows
    ):
        return False, "failed, nonzero, or container-free task", rows, set()
    try:
        processes = {normalize_process(row["name"]) for row in rows}
        images = {normalize_container(row["container"], pins) for row in rows}
    except ValueError as error:
        return False, str(error), rows, set()
    if processes != set(contract.processes):
        return False, f"process mismatch {sorted(processes ^ set(contract.processes))}", rows, images
    if images != set(contract.images):
        return False, f"image mismatch {sorted(images ^ set(contract.images))}", rows, images
    return True, "qualified", rows, images


def docker_output(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def verify_image_identity(container: str, digest: str) -> str:
    repository = container.rsplit(":", 1)[0]
    immutable = f"{repository}@{digest}"
    image_id = docker_output(["docker", "image", "inspect", container, "--format", "{{.Id}}"])
    immutable_id = docker_output(["docker", "image", "inspect", immutable, "--format", "{{.Id}}"])
    if image_id != immutable_id or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise SystemExit(f"{container} is not bound to pinned digest {digest}")
    repo_digests = json.loads(
        docker_output(["docker", "image", "inspect", container, "--format", "{{json .RepoDigests}}"])
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
    return metadata


def select_compat_marker(root: Path, destination: Path) -> dict[str, str]:
    markers = sorted(root.rglob(".oncotracer-ichorcna-plot-compat.tsv"))
    valid: list[tuple[Path, dict[str, str]]] = []
    diagnostics: list[str] = []
    for marker in markers:
        try:
            valid.append((marker, parse_compat(marker)))
        except (OSError, ValueError, csv.Error) as error:
            diagnostics.append(f"{marker}: {error}")
    if not valid:
        details = "\n".join(diagnostics) if diagnostics else "no marker files found"
        raise SystemExit(f"no valid nested ichorCNA compatibility marker under {root}:\n{details}")
    canonical = valid[0][1]
    if any(metadata != canonical for _, metadata in valid[1:]):
        raise SystemExit("nested ichorCNA compatibility markers disagree")
    selected = max(valid, key=lambda item: (item[0].stat().st_mtime_ns, item[0].as_posix()))[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(selected, destination)
    return canonical


def write_tsv(path: Path, header: Iterable[str], rows: Iterable[Iterable[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(list(header))
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=sorted(CONTRACTS), required=True)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--runtime-out", type=Path, required=True)
    parser.add_argument("--selected-dir", type=Path, required=True)
    parser.add_argument("--selection-out", type=Path, required=True)
    parser.add_argument("--illumina-root", type=Path)
    parser.add_argument("--ont-root", type=Path)
    parser.add_argument("--hcc-root", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
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
        traces = sorted(root.rglob("pipeline_info/execution_trace_*.txt"))
        if not traces:
            raise SystemExit(f"no nested SAMURAI traces found for {contract.label}: {root}")
        qualified: list[tuple[Path, list[dict[str, str]], set[str]]] = []
        diagnostics: list[tuple[str, str]] = []
        for trace in traces:
            ok, reason, rows, images = evaluate_trace(trace, contract, pins)
            diagnostics.append((trace.as_posix(), reason))
            if ok:
                qualified.append((trace, rows, images))
        if not qualified:
            report = "\n".join(f"  {path}: {reason}" for path, reason in diagnostics)
            raise SystemExit(f"no qualifying completed trace for {contract.label}:\n{report}")
        selected_path, selected_rows, selected_images = max(
            qualified, key=lambda item: (item[0].stat().st_mtime_ns, item[0].as_posix())
        )
        selected_name = f"nested-v1-{contract.label.removeprefix('quickstart1-').removeprefix('quickstart2-')}-trace.tsv"
        selected_destination = args.selected_dir / selected_name
        shutil.copyfile(selected_path, selected_destination)
        selection_rows.append(
            [
                contract.label,
                str(len(traces)),
                str(len(qualified)),
                selected_path.as_posix(),
                selected_destination.name,
                sha256(selected_destination),
            ]
        )
        for container in sorted(selected_images):
            runtime_rows.append(
                [contract.label, container, pins[container], verify_image_identity(container, pins[container])]
            )
        if contract.require_ichorcna_compat:
            metadata = select_compat_marker(
                root, args.selected_dir / "nested-v1-ont-ichorcna-plot-compat.tsv"
            )
            selection_rows.append(
                [
                    contract.label + "-ichorcna-compat",
                    "",
                    "",
                    "",
                    "nested-v1-ont-ichorcna-plot-compat.tsv",
                    sha256(args.selected_dir / "nested-v1-ont-ichorcna-plot-compat.tsv"),
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
        ["run", "candidate_traces", "qualified_traces", "selected_source", "audit_copy", "sha256"],
        selection_rows,
    )
    if {row[1] for row in runtime_rows} != set(pins):
        raise SystemExit("selected traces did not exercise every pinned image")
    print(f"verified {args.suite}: {len(runtime_rows)} run/image records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
