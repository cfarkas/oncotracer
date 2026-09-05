"""Safe native ONT POD5 methylation execution.

The methylation branch deliberately has no discovery, download, installation,
or shared-cache behavior. Every POD5/model/resource location is explicit,
inputs are read-only, and all generated files remain under '07_methylation'.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
    atomic_write_json,
    atomic_write_text,
    require_directory,
    require_file,
    sha256_file,
    utc_now,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_CLASSIFIER_INTERFACE_COMMITS = {
    "sturgeon": "4c742ddea49b0077a8f8ff3d99daafb238d00706",
    "marlin": "37c9836cc325ff2edccbdff06736604163db2c15",
}


@dataclass(frozen=True)
class MethylationRequest:
    """Fully resolved, immutable inputs for one optional methylation branch."""

    classifier: str
    pod5_dir: Path | None
    pod5_files: tuple[Path, ...]
    gpu: bool
    dorado: Path
    modkit: Path
    samtools: Path
    dorado_model: Path | None
    dorado_modbase_model: Path | None
    classifier_executable: Path | None
    classifier_paths: Mapping[str, Path]
    executable_sha256: Mapping[str, str]
    asset_sha256: Mapping[str, str]
    pod5_inventory_sha256: str
    classifier_interface_contract_commit: str
    modbam_input: Path | None = None
    modbam_files: tuple[Path, ...] = ()
    modbam_inventory_sha256: str = ""

    @property
    def dorado_device(self) -> str:
        return "cuda:all" if self.gpu and self.modbam_input is None else "cpu"


def _strict_bool(value: object, *, key: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0"}:
        return False
    raise OncoTracerError(f"{key} must be true or false, found {value!r}")


def _configured_path(config: Mapping[str, object], key: str, label: str) -> Path:
    value = config.get(key)
    if not value:
        raise OncoTracerError(f"methylation requires {key}")
    return require_file(Path(str(value)), label)


def _configured_directory(config: Mapping[str, object], key: str, label: str) -> Path:
    value = config.get(key)
    if not value:
        raise OncoTracerError(f"methylation requires {key}")
    return require_directory(Path(str(value)), label)


def _configured_executable(
    config: Mapping[str, object], key: str, default: str, label: str
) -> Path:
    value = str(config.get(key) or default).strip()
    candidate: str | None
    if os.sep in value:
        path = Path(value).expanduser().absolute()
        candidate = str(path) if path.is_file() and os.access(path, os.X_OK) else None
    else:
        candidate = shutil.which(value)
    if not candidate:
        raise OncoTracerError(
            f"{label} executable is unavailable; set {key} to an existing executable"
        )
    return Path(candidate).expanduser().absolute()


def _directory_files(directory: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise OncoTracerError(
                f"symlinks are not accepted in a methylation model tree: {path}"
            )
        if path.is_file():
            files.append(path)
    return tuple(files)


def directory_sha256(directory: Path) -> str:
    """Hash relative names and content of a directory without writing a manifest."""
    digest = hashlib.sha256()
    files = _directory_files(directory)
    if not files:
        raise OncoTracerError(f"model directory contains no regular files: {directory}")
    for path in files:
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _validate_expected_file_hash(
    config: Mapping[str, object],
    key: str,
    path: Path,
    label: str,
) -> str:
    expected = str(config.get(key) or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise OncoTracerError(
            f"{key} must contain the exact 64-character SHA-256 for {label}"
        )
    observed = sha256_file(path)
    if observed != expected:
        raise OncoTracerError(
            f"{label} SHA-256 mismatch: expected {expected}, observed {observed}"
        )
    return observed


def _validate_optional_tree_hash(
    config: Mapping[str, object],
    key: str,
    directory: Path,
    label: str,
) -> str:
    observed = directory_sha256(directory)
    expected = str(config.get(key) or "").strip().lower()
    if expected:
        if not SHA256_PATTERN.fullmatch(expected):
            raise OncoTracerError(f"{key} must be a 64-character SHA-256")
        if observed != expected:
            raise OncoTracerError(
                f"{label} tree SHA-256 mismatch: expected {expected}, observed {observed}"
            )
    return observed


def _pod5_files(directory: Path) -> tuple[Path, ...]:
    root = require_directory(directory, "methylation POD5 directory")
    files: list[Path] = []
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise OncoTracerError(
                f"symlinks are not accepted in the explicit POD5 directory: {candidate}"
            )
        if candidate.suffix.lower() != ".pod5":
            continue
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if root != resolved and root not in resolved.parents:
            raise OncoTracerError(
                f"POD5 path escapes the explicit directory: {candidate}"
            )
        if resolved.stat().st_size <= 0:
            raise OncoTracerError(
                f"empty POD5 file is not accepted in the explicit directory: {candidate}"
            )
        files.append(resolved)
    if not files:
        raise OncoTracerError(
            f"explicit methylation POD5 directory contains no non-empty .pod5 files: {root}"
        )
    return tuple(files)


def _pod5_inventory_sha256(root: Path, files: Sequence[Path]) -> str:
    """Hash stable inventory metadata, not patient signal content."""
    digest = hashlib.sha256()
    for path in files:
        stat = path.stat()
        record = json.dumps(
            {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return digest.hexdigest()


def _modbam_files(path: Path) -> tuple[Path, ...]:
    """Accept one closed BAM or an explicitly selected directory of BAM batches."""
    if path.is_symlink():
        raise OncoTracerError(
            f"use a physical modified-base BAM path, not a symlink: {path}"
        )
    path = path.expanduser().resolve()
    if path.is_dir():
        candidates = sorted(path.rglob("*"))
        if any(p.is_symlink() for p in candidates):
            raise OncoTracerError(
                "symlinks are not accepted in the explicit modified-base BAM directory"
            )
        files = tuple(
            p for p in candidates if p.is_file() and p.suffix.lower() == ".bam"
        )
    else:
        files = (path,) if path.is_file() and path.suffix.lower() == ".bam" else ()
    if not files or any(p.stat().st_size == 0 for p in files):
        raise OncoTracerError(
            "--modbam must contain non-empty .bam files; use completed MinKNOW BAM batches with MM/ML tags"
        )
    return files


def resolve_methylation_request(
    config: Mapping[str, object],
    *,
    mode: str,
    enabled_override: bool | None = None,
    classifier_override: str | None = None,
    pod5_override: Path | None = None,
    gpu_override: bool | None = None,
    modbam_override: Path | None = None,
) -> MethylationRequest | None:
    """Resolve CLI/YAML methylation settings without creating any files."""
    configured_enabled = _strict_bool(
        config.get("methylation"), key="methylation", default=False
    )
    enabled = configured_enabled if enabled_override is None else enabled_override
    if classifier_override and not enabled:
        raise OncoTracerError("--sturgeon/--marlin requires --methylation")
    if pod5_override is not None and not enabled:
        raise OncoTracerError("--pod5-dir requires --methylation")
    if modbam_override is not None and not enabled:
        raise OncoTracerError("--modbam requires --methylation or --methylation-only")
    if gpu_override and not enabled:
        raise OncoTracerError("--gpu requires --methylation")
    if not enabled:
        return None
    if mode != "ont":
        raise OncoTracerError("--methylation is restricted to mode: ont")

    classifier = (
        classifier_override
        or str(config.get("methylation_classifier") or "").strip().lower()
    )
    if classifier not in {"sturgeon", "marlin"}:
        raise OncoTracerError(
            "methylation requires exactly one classifier: --sturgeon or --marlin"
        )
    pod5_value = pod5_override or (
        Path(str(config["methylation_pod5_dir"]))
        if config.get("methylation_pod5_dir")
        else None
    )
    modbam_value = modbam_override or (
        Path(str(config["methylation_modbam"]))
        if config.get("methylation_modbam")
        else None
    )
    if pod5_value is not None and modbam_value is not None:
        raise OncoTracerError(
            "choose one methylation input: --pod5-dir OR --modbam; remove the other input from the YAML"
        )
    if pod5_value is None and modbam_value is None:
        raise OncoTracerError(
            "methylation requires an explicit --pod5-dir (or methylation_pod5_dir), "
            "or --modbam (methylation_modbam) to reuse existing modified-base calls"
        )
    pod5_dir = (
        require_directory(pod5_value, "methylation POD5 directory")
        if pod5_value
        else None
    )
    pod5_files = _pod5_files(pod5_dir) if pod5_dir else ()
    modbam_files = _modbam_files(modbam_value) if modbam_value else ()
    modbam_input = modbam_value.expanduser().resolve() if modbam_value else None

    build = str(config.get("methylation_reference_build") or "hg38").strip().lower()
    if build != "hg38":
        raise OncoTracerError(
            "native v2 methylation currently requires methylation_reference_build: hg38"
        )
    gpu = (
        _strict_bool(config.get("methylation_gpu"), key="methylation_gpu")
        if gpu_override is None
        else gpu_override
    )
    dorado = _configured_executable(
        config, "methylation_dorado_executable", "dorado", "Dorado"
    )
    modkit = _configured_executable(
        config, "methylation_modkit_executable", "modkit", "Modkit"
    )
    samtools = _configured_executable(
        config, "methylation_samtools_executable", "samtools", "samtools"
    )
    dorado_model = (
        _configured_directory(
            config, "methylation_dorado_model", "Dorado basecalling model"
        )
        if pod5_dir
        else None
    )
    dorado_modbase_model = (
        _configured_directory(
            config,
            "methylation_dorado_modbase_model",
            "Dorado 5mCG/5hmCG modified-base model",
        )
        if pod5_dir
        else None
    )
    asset_sha256: dict[str, str] = {}
    if dorado_model is not None and dorado_modbase_model is not None:
        asset_sha256.update(
            {
                "dorado_model_tree": _validate_optional_tree_hash(
                    config,
                    "methylation_dorado_model_sha256",
                    dorado_model,
                    "Dorado basecalling model",
                ),
                "dorado_modbase_model_tree": _validate_optional_tree_hash(
                    config,
                    "methylation_dorado_modbase_model_sha256",
                    dorado_modbase_model,
                    "Dorado modified-base model",
                ),
            }
        )
    classifier_paths: dict[str, Path] = {}
    classifier_executable: Path | None
    supported_commit = SUPPORTED_CLASSIFIER_INTERFACE_COMMITS[classifier]
    legacy_commit_key = f"{classifier}_source_commit"
    interface_commit_key = f"{classifier}_interface_contract_commit"
    if legacy_commit_key in config:
        raise OncoTracerError(
            f"{legacy_commit_key} is misleading and unsupported; use "
            f"{interface_commit_key}, which identifies the tested interface "
            "contract rather than authenticating the external installation"
        )
    configured_commit = (
        str(config.get(interface_commit_key) or supported_commit).strip().lower()
    )
    if not COMMIT_PATTERN.fullmatch(configured_commit):
        raise OncoTracerError(
            f"{interface_commit_key} must be a full 40-character Git commit"
        )
    if configured_commit != supported_commit:
        raise OncoTracerError(
            f"native v2 {classifier} integration supports exact interface contract "
            f"{supported_commit}, found {configured_commit}"
        )
    if classifier == "sturgeon":
        if not _strict_bool(
            config.get("sturgeon_license_acknowledged"),
            key="sturgeon_license_acknowledged",
        ):
            raise OncoTracerError(
                "Sturgeon requires sturgeon_license_acknowledged: true; "
                "OncoTracer does not distribute or sublicense Sturgeon"
            )
        classifier_executable = _configured_executable(
            config, "sturgeon_executable", "sturgeon", "user-licensed Sturgeon"
        )
        classifier_paths["model"] = _configured_path(
            config, "sturgeon_model", "Sturgeon model"
        )
        classifier_paths["probes"] = _configured_path(
            config, "sturgeon_probes", "Sturgeon hg38 probes"
        )
        asset_sha256["sturgeon_model"] = _validate_expected_file_hash(
            config,
            "sturgeon_model_sha256",
            classifier_paths["model"],
            "Sturgeon model",
        )
        asset_sha256["sturgeon_probes"] = _validate_expected_file_hash(
            config,
            "sturgeon_probes_sha256",
            classifier_paths["probes"],
            "Sturgeon probes",
        )
    else:
        classifier_executable = _configured_executable(
            config, "marlin_rscript", "Rscript", "MARLIN Rscript"
        )
        classifier_paths["python"] = _configured_executable(
            config, "marlin_python", "python", "MARLIN Python"
        )
        asset_sha256["marlin_python"] = sha256_file(classifier_paths["python"])
        for name, key, label in (
            ("model", "marlin_model", "MARLIN model"),
            ("features", "marlin_features", "MARLIN features"),
            ("classes", "marlin_class_annotations", "MARLIN class annotations"),
            ("probes", "marlin_probe_bed", "MARLIN hg38 probe BED"),
        ):
            classifier_paths[name] = _configured_path(config, key, label)
            asset_sha256[f"marlin_{name}"] = _validate_expected_file_hash(
                config,
                f"{key}_sha256",
                classifier_paths[name],
                label,
            )

    executable_sha256 = {
        "dorado": sha256_file(dorado),
        "modkit": sha256_file(modkit),
        "samtools": sha256_file(samtools),
        classifier: (
            sha256_file(classifier_executable)
            if classifier_executable is not None
            else ""
        ),
    }
    return MethylationRequest(
        classifier=classifier,
        pod5_dir=pod5_dir,
        pod5_files=pod5_files,
        gpu=gpu,
        dorado=dorado,
        modkit=modkit,
        samtools=samtools,
        dorado_model=dorado_model,
        dorado_modbase_model=dorado_modbase_model,
        classifier_executable=classifier_executable,
        classifier_paths=classifier_paths,
        executable_sha256=executable_sha256,
        asset_sha256=asset_sha256,
        pod5_inventory_sha256=(
            _pod5_inventory_sha256(pod5_dir, pod5_files) if pod5_dir else ""
        ),
        classifier_interface_contract_commit=configured_commit,
        modbam_input=modbam_input,
        modbam_files=modbam_files,
        modbam_inventory_sha256=(
            _pod5_inventory_sha256(
                modbam_input if modbam_input.is_dir() else modbam_input.parent,
                modbam_files,
            )
            if modbam_input
            else ""
        ),
    )


def methylation_plan(request: MethylationRequest) -> dict[str, object]:
    return {
        "enabled": True,
        "classifier": request.classifier,
        "input_kind": "modbam" if request.modbam_input else "pod5",
        "pod5_dir": str(request.pod5_dir) if request.pod5_dir else None,
        "modbam_input": str(request.modbam_input) if request.modbam_input else None,
        "modbam_file_count": len(request.modbam_files),
        "modbam_inventory_sha256": request.modbam_inventory_sha256,
        "pod5_file_count": len(request.pod5_files),
        "pod5_inventory_sha256": request.pod5_inventory_sha256,
        "gpu_requested": request.gpu,
        "dorado_device": request.dorado_device,
        "modkit_acceleration": "cpu_threads",
        "classifier_gpu_visibility": (
            request.gpu if request.classifier == "marlin" else False
        ),
        "zero_cpg_action": "abort_methylation_continue_cna",
        "asset_sha256": dict(request.asset_sha256),
        "classifier_interface_contract_commit": (
            request.classifier_interface_contract_commit
        ),
        "classifier_runtime_source_authenticated": False,
        "classifier_runtime_external": True,
        "stages": [
            "fastq-read-id-selection",
            (
                "modbam-cpu-alignment"
                if request.modbam_input
                else "dorado-modified-base-basecalling"
            ),
            "modkit-cpg-pileup",
            f"{request.classifier}-classification-if-cpg-detected",
        ],
    }


def _accelerator_environment(gpu: bool) -> dict[str, str | None]:
    """Expose a requested GPU or make a CPU-only stage unambiguous."""
    if not gpu:
        return {
            "CUDA_VISIBLE_DEVICES": "",
            "NVIDIA_VISIBLE_DEVICES": "void",
        }
    cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    nvidia = os.environ.get("NVIDIA_VISIBLE_DEVICES")
    return {
        # An empty CUDA mask means no devices. Explicit --gpu removes that
        # mask, while preserving a non-empty administrator/user selection.
        "CUDA_VISIBLE_DEVICES": cuda if cuda else None,
        "NVIDIA_VISIBLE_DEVICES": (
            nvidia
            if nvidia and nvidia.strip().lower() not in {"none", "void"}
            else "all"
        ),
    }


def _safe_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    message = re.sub(r"(?<![A-Za-z0-9._-])/(?:[^\s,;]+)", "<path>", message)
    return message[:500]


def _status_payload(
    request: MethylationRequest,
    records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    completed = [
        str(record["sample"]) for record in records if record["status"] == "complete"
    ]
    failed = [
        str(record["sample"]) for record in records if record["status"] == "failed"
    ]
    no_cpg = [
        str(record["sample"])
        for record in records
        if record["status"] == "no_cpg_modifications"
    ]
    no_probes = [
        str(record["sample"])
        for record in records
        if record["status"] == "no_classifier_probes"
    ]
    pending = [
        str(record["sample"]) for record in records if record["status"] == "pending"
    ]
    if pending:
        overall = "in_progress"
    elif completed and (failed or no_cpg or no_probes):
        overall = "partial_failure"
    elif failed:
        overall = "failed"
    elif no_cpg:
        overall = "no_cpg_modifications"
    elif no_probes:
        overall = "no_classifier_probes"
    else:
        overall = "complete"
    return {
        "schema": "oncotracer-native-ont-methylation-status-v1",
        "overall_status": overall,
        "classifier": request.classifier,
        "gpu_requested": request.gpu,
        "dorado_device": request.dorado_device,
        "modkit_acceleration": "cpu_threads",
        "completed_samples": completed,
        "failed_samples": failed,
        "no_cpg_samples": no_cpg,
        "no_classifier_probe_samples": no_probes,
        "pending_samples": pending,
        "samples": list(records),
        "updated_at": utc_now(),
    }


def _write_status(
    path: Path,
    request: MethylationRequest,
    records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    payload = _status_payload(request, records)
    atomic_write_json(path, payload)
    return payload


def _provenance_payload(
    request: MethylationRequest,
    status: str,
    *,
    failure: BaseException | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "oncotracer-native-ont-methylation-provenance-v2",
        "classifier": request.classifier,
        "classifier_interface_contract_commit": (
            request.classifier_interface_contract_commit
        ),
        "classifier_runtime_external": True,
        "classifier_runtime_source_authenticated": False,
        "input_kind": "modbam" if request.modbam_input else "pod5",
        "pod5_dir": str(request.pod5_dir) if request.pod5_dir else None,
        "modbam_input": str(request.modbam_input) if request.modbam_input else None,
        "modbam_file_count": len(request.modbam_files),
        "modbam_inventory_sha256": request.modbam_inventory_sha256,
        "pod5_file_count": len(request.pod5_files),
        "pod5_inventory_sha256": request.pod5_inventory_sha256,
        "gpu_requested": request.gpu,
        "dorado_device": request.dorado_device,
        "modkit_acceleration": "cpu_threads",
        "executables": {
            "dorado": {
                "path": str(request.dorado),
                "sha256": request.executable_sha256["dorado"],
            },
            "modkit": {
                "path": str(request.modkit),
                "sha256": request.executable_sha256["modkit"],
            },
            "samtools": {
                "path": str(request.samtools),
                "sha256": request.executable_sha256["samtools"],
            },
            request.classifier: {
                "path": str(request.classifier_executable),
                "sha256": request.executable_sha256[request.classifier],
            },
        },
        "asset_sha256": dict(request.asset_sha256),
        "status": status,
        "created_at": utc_now(),
    }
    if failure is not None:
        payload["failure"] = _safe_error(failure)
    return payload


def write_global_methylation_failure(
    outdir: Path,
    request: MethylationRequest,
    error: BaseException,
) -> dict[str, object]:
    methylation_out = outdir / "07_methylation"
    record = {
        "sample": "all",
        "barcode": None,
        "status": "failed",
        "stage": "methylation_setup_or_basecalling",
        "error": _safe_error(error),
        "read_id_count": None,
        "cpg_rows": None,
        "modified_cpg_calls": None,
        "bedmethyl": None,
        "classification": None,
    }
    status = _write_status(
        methylation_out / "methylation_status.json", request, [record]
    )
    atomic_write_json(
        methylation_out / "methylation_provenance.json",
        _provenance_payload(request, "failed", failure=error),
    )
    return status


def _fastq_files(directory: Path) -> tuple[Path, ...]:
    files = tuple(
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and path.stat().st_size > 0
        and path.name.lower().endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))
    )
    if not files:
        raise OncoTracerError(
            f"no FASTQ files are available to map barcode read IDs under {directory}"
        )
    return files


def _read_ids_from_fastq(path: Path) -> set[str]:
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    read_ids: set[str] = set()
    with opener(path, "rt", encoding="utf-8", errors="strict") as handle:
        record_number = 0
        while True:
            header = handle.readline()
            if not header:
                break
            sequence = handle.readline()
            plus = handle.readline()
            quality = handle.readline()
            record_number += 1
            if not sequence or not plus or not quality:
                raise OncoTracerError(
                    f"truncated FASTQ record {record_number} in {path}"
                )
            if not header.startswith("@") or not plus.startswith("+"):
                raise OncoTracerError(
                    f"malformed FASTQ record {record_number} in {path}"
                )
            read_id = header[1:].split(maxsplit=1)[0].strip()
            if not read_id:
                raise OncoTracerError(f"empty FASTQ read ID in {path}")
            read_ids.add(read_id)
    return read_ids


def _atomic_write_if_changed(path: Path, contents: str) -> None:
    """Keep input mtimes stable so content-aware resume remains effective."""
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == contents:
                return
        except OSError:
            pass
    atomic_write_text(path, contents)


def _prepare_read_ids(samples: Sequence[object], output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    observed: dict[str, str] = {}
    paths: dict[str, Path] = {}
    union: set[str] = set()
    for sample in samples:
        name = str(getattr(sample, "sample"))
        barcode = str(getattr(sample, "barcode"))
        directory = Path(getattr(sample, "fastq_dir"))
        identifiers: set[str] = set()
        for fastq in _fastq_files(directory):
            identifiers.update(_read_ids_from_fastq(fastq))
        if not identifiers:
            raise OncoTracerError(f"sample {name} has no FASTQ read IDs")
        for read_id in identifiers:
            previous = observed.get(read_id)
            if previous is not None and previous != name:
                raise OncoTracerError(
                    f"read ID {read_id!r} occurs in both samples {previous!r} and {name!r}"
                )
            observed[read_id] = name
        destination = output / f"{name}.{barcode}.read_ids.txt"
        _atomic_write_if_changed(
            destination, "".join(f"{value}\n" for value in sorted(identifiers))
        )
        paths[name] = destination
        union.update(identifiers)
    _atomic_write_if_changed(
        output / "all_selected.read_ids.txt",
        "".join(f"{value}\n" for value in sorted(union)),
    )
    return paths


def _nonempty(path: Path, label: str) -> Path:
    return require_file(path, label)


def _existing_file(path: Path, label: str) -> Path:
    """Require a regular file while allowing a scientifically valid empty result."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise OncoTracerError(f"{label} is missing: {resolved}")
    return resolved


def _line_count(path: Path) -> int:
    with path.open("rt", encoding="utf-8") as handle:
        return sum(1 for _line in handle)


def _safe_result_path(outdir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = (outdir / value).resolve()
    root = outdir.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _load_reusable_sample_record(path: Path, outdir: Path, sample: str):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("sample") != sample:
        return None
    status = payload.get("status")
    if status not in {"complete", "no_cpg_modifications", "no_classifier_probes"}:
        return None
    bedmethyl = _safe_result_path(outdir, payload.get("bedmethyl"))
    if bedmethyl is None or not bedmethyl.is_file():
        return None
    if status == "complete":
        classification = _safe_result_path(outdir, payload.get("classification"))
        if (
            classification is None
            or not classification.is_file()
            or not classification.stat().st_size
        ):
            return None
    return payload


def _bedmethyl_counts(path: Path) -> tuple[int, int, int]:
    rows = 0
    covered_rows = 0
    modified_calls = 0
    with path.open("rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12:
                raise OncoTracerError(
                    f"Modkit bedMethyl has fewer than 12 columns at line {line_number}"
                )
            try:
                coverage = int(fields[9])
                modified = int(fields[11])
            except ValueError as error:
                raise OncoTracerError(
                    f"Modkit bedMethyl has non-integer counts at line {line_number}"
                ) from error
            if coverage < 0 or modified < 0 or modified > coverage:
                raise OncoTracerError(
                    f"Modkit bedMethyl has invalid coverage at line {line_number}"
                )
            rows += 1
            if coverage > 0:
                covered_rows += 1
            if modified > 0:
                modified_calls += modified
    return rows, covered_rows, modified_calls


def _marlin_probe_coverage(bedmethyl: Path, probes: Path) -> int:
    """Count covered probe IDs using the same coordinate join as MARLIN preparation."""
    covered: set[tuple[str, int]] = set()
    with bedmethyl.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            fields = line.split("\t")
            if int(fields[9]) > 0:
                covered.add((fields[0], int(fields[1])))
    matched: set[str] = set()
    opener = gzip.open if probes.suffix.lower() == ".gz" else open
    with opener(probes, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            fields = line.rstrip().split("\t")
            if len(fields) < 4:
                raise OncoTracerError(
                    f"MARLIN probe BED needs four columns at line {line_number}"
                )
            if (fields[0], int(fields[1])) in covered:
                matched.add(fields[3])
    return len(matched)


def _select_modbam_input(
    request: MethylationRequest,
    union_ids: Path,
    output: Path,
    runner: CommandRunner,
    threads: int,
) -> Path:
    """Select tagged primary records, then let Dorado align them on CPU to our hg38."""
    merged = output / ".input_batches.bam"
    selected = output / ".selected_input.bam"
    for path in (merged, selected):
        path.unlink(missing_ok=True)
    for index, path in enumerate(request.modbam_files):
        runner.run(
            f"methylation-input-quickcheck-{index}",
            [request.samtools, "quickcheck", "-u", path],
        )
    runner.run(
        "methylation-input-merge",
        [
            request.samtools,
            "merge",
            "-@",
            str(threads),
            "-u",
            "-o",
            merged,
            *request.modbam_files,
        ],
    )
    runner.run(
        "methylation-input-select",
        [
            request.samtools,
            "view",
            "-@",
            str(threads),
            "-N",
            union_ids,
            # MM is a string; ML is an array, unsupported by samtools expressions.
            # The separate tag-presence filter works for ML without interpreting it.
            "-F",
            "2304",
            "-e",
            "exists([MM])",
            "-d",
            "ML",
            "-b",
            "-o",
            selected,
            merged,
        ],
    )
    count_file = output / "input_tagged_read_count.txt"
    with count_file.open("w", encoding="utf-8") as handle:
        runner.run(
            "methylation-input-count",
            [request.samtools, "view", "-c", selected],
            stdout=handle,
        )
    if int(count_file.read_text().strip()) == 0:
        raise OncoTracerError(
            "No FASTQ-selected reads with MM/ML modification tags in --modbam. "
            "Use matching completed MinKNOW BAMs made with modified-base calling, "
            "or use --pod5-dir to call modifications from raw signal. FASTQ alone cannot supply methylation."
        )
    # Multiple basecalling exports can contain the same primary read. Do not
    # inflate methylation coverage by accepting duplicate batches.
    try:
        runner.pipeline(
            "methylation-input-unique-reads",
            [request.samtools, "view", selected],
            ["awk", "{ if (seen[$1]++) exit 1 }"],
        )
    except OncoTracerError as error:
        raise OncoTracerError(
            "Could not verify one primary BAM record per read: duplicate read IDs "
            "or a read-validation tool failed. Use one copy of each completed batch, "
            "not overlapping basecalling exports; see the execution trace."
        ) from error
    merged.unlink(missing_ok=True)
    return selected


def _run_sturgeon(
    request: MethylationRequest,
    sample: str,
    bedmethyl: Path,
    output: Path,
    runner: CommandRunner,
) -> Path:
    executable = request.classifier_executable
    assert executable is not None
    converted = output / f"{sample}.sturgeon.bed"
    predictions = output / "predictions"
    output.mkdir(parents=True, exist_ok=True)
    predictions.mkdir(parents=True, exist_ok=True)
    expected_prediction = (
        predictions / f"{converted.stem}_{request.classifier_paths['model'].stem}.csv"
    )
    expected_plot = expected_prediction.with_suffix(".pdf")
    for owned in (
        converted,
        Path(str(converted) + ".tmp"),
        expected_prediction,
        expected_plot,
    ):
        owned.unlink(missing_ok=True)
    runner.run(
        f"methylation-sturgeon-inputtobed-{sample}",
        [
            executable,
            "--no-logfile",
            "inputtobed",
            "-i",
            bedmethyl,
            "-o",
            converted,
            "-s",
            "modkit_pileup",
            "--probes-file",
            request.classifier_paths["probes"],
        ],
        env=_accelerator_environment(False),
    )
    converted = _nonempty(converted, f"Sturgeon converted BED for {sample}")
    runner.run(
        f"methylation-sturgeon-predict-{sample}",
        [
            executable,
            "--no-logfile",
            "predict",
            "-i",
            converted,
            "-o",
            predictions,
            "--model-files",
            request.classifier_paths["model"],
            "--plot-results",
        ],
        env=_accelerator_environment(False),
    )
    return _nonempty(expected_prediction, f"Sturgeon prediction CSV for {sample}")


def _run_marlin(
    root: Path,
    request: MethylationRequest,
    sample: str,
    bedmethyl: Path,
    output: Path,
    runner: CommandRunner,
) -> Path:
    executable = request.classifier_executable
    assert executable is not None
    prepare = require_file(
        root / "bin" / "scripts" / "native_marlin_prepare.R",
        "native MARLIN preparation script",
    )
    predict = require_file(
        root / "bin" / "scripts" / "native_marlin_predict.R",
        "native MARLIN prediction script",
    )
    output.mkdir(parents=True, exist_ok=True)
    prepared = output / f"{sample}.marlin_input.bed"
    prediction = output / f"{sample}.marlin_predictions.tsv"
    environment = _marlin_environment(request, output)
    runner.run(
        f"methylation-marlin-prepare-{sample}",
        [
            executable,
            "--vanilla",
            prepare,
            "--bedmethyl",
            bedmethyl,
            "--probes",
            request.classifier_paths["probes"],
            "--output",
            prepared,
        ],
        cwd=output,
        env=environment,
    )
    _nonempty(prepared, f"MARLIN prepared input for {sample}")
    runner.run(
        f"methylation-marlin-predict-{sample}",
        [
            executable,
            "--vanilla",
            predict,
            "--input",
            prepared,
            "--features",
            request.classifier_paths["features"],
            "--model",
            request.classifier_paths["model"],
            "--classes",
            request.classifier_paths["classes"],
            "--sample",
            sample,
            "--output",
            prediction,
        ],
        cwd=output,
        env=environment,
    )
    return _nonempty(prediction, f"MARLIN prediction for {sample}")


def _marlin_environment(
    request: MethylationRequest, output: Path
) -> dict[str, str | None]:
    cache = output / ".runtime-cache"
    environment = _accelerator_environment(request.gpu)
    environment.update(
        {
            "TF_FORCE_GPU_ALLOW_GROWTH": "true",
            "RETICULATE_PYTHON": str(request.classifier_paths["python"]),
            "RETICULATE_USE_MANAGED_VENV": "no",
            "UV_OFFLINE": "1",
            "UV_CACHE_DIR": str(cache / "uv"),
            "XDG_CACHE_HOME": str(cache / "xdg"),
            "KERAS_HOME": str(cache / "keras"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def run_methylation(
    root: Path,
    request: MethylationRequest,
    samples: Sequence[object],
    reference: Mapping[str, Path],
    outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    *,
    threads: int,
    force: bool,
) -> dict[str, object]:
    """Run modified-base basecalling/classification before CNA execution."""
    methylation_out = outdir / "07_methylation"
    ids_out = methylation_out / "read_ids"
    modbam_out = methylation_out / "modbam"
    bed_out = methylation_out / "bedmethyl"
    classifier_out = methylation_out / request.classifier
    logs_out = methylation_out / "logs"
    sample_status_out = methylation_out / "sample_status"
    for directory in (
        ids_out,
        modbam_out,
        bed_out,
        classifier_out,
        logs_out,
        sample_status_out,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    status_path = methylation_out / "methylation_status.json"
    records: list[dict[str, object]] = [
        {
            "sample": str(getattr(sample, "sample")),
            "barcode": str(getattr(sample, "barcode")),
            "status": "pending",
            "stage": "pending",
            "error": None,
            "read_id_count": None,
            "modbam_records": None,
            "cpg_rows": None,
            "covered_cpg_rows": None,
            "modified_cpg_calls": None,
            "covered_classifier_probes": None,
            "bedmethyl": None,
            "classification": None,
        }
        for sample in samples
    ]
    _write_status(status_path, request, records)
    read_id_paths = _prepare_read_ids(samples, ids_out)
    union_ids = _nonempty(ids_out / "all_selected.read_ids.txt", "selected read IDs")
    read_id_counts = {name: _line_count(path) for name, path in read_id_paths.items()}
    fasta = require_file(reference["fasta"], "hg38 FASTA for methylation")
    require_file(reference["fai"], "hg38 FASTA index for methylation")

    if request.classifier == "marlin":
        preflight = require_file(
            root / "bin" / "scripts" / "native_marlin_preflight.R",
            "native MARLIN runtime preflight",
        )
        assert request.classifier_executable is not None
        runner.run(
            "methylation-marlin-preflight",
            [
                request.classifier_executable,
                "--vanilla",
                preflight,
                "--python",
                request.classifier_paths["python"],
            ],
            cwd=methylation_out,
            env=_marlin_environment(request, methylation_out),
        )

    all_bam = modbam_out / "all_selected.mod.sorted.bam"
    all_bai = Path(str(all_bam) + ".bai")
    unsorted = modbam_out / f".all_selected.mod.unsorted.{os.getpid()}.bam"
    dorado_command: list[str | Path] = [
        request.dorado,
        "basecaller",
        request.dorado_model,
        request.pod5_dir,
        "--recursive",
        "--read-ids",
        union_ids,
        "--reference",
        fasta,
        "--mm2-opts",
        "-x map-ont",
        "--modified-bases-models",
        request.dorado_modbase_model,
        "--device",
        request.dorado_device,
    ]
    basecall_inputs = [
        *request.pod5_files,
        union_ids,
        fasta,
        request.dorado,
        request.samtools,
        *(_directory_files(request.dorado_model) if request.dorado_model else ()),
        *(
            _directory_files(request.dorado_modbase_model)
            if request.dorado_modbase_model
            else ()
        ),
    ]
    basecall_stage = "methylation-dorado-basecall"
    if request.modbam_input:
        basecall_stage = "methylation-modbam-align"
        dorado_command = [
            request.dorado,
            "aligner",
            fasta,
            modbam_out / ".selected_input.bam",
            "--threads",
            str(max(1, threads)),
            "--mm2-opts",
            "-x map-ont",
        ]
        basecall_inputs = [
            *request.modbam_files,
            union_ids,
            fasta,
            request.dorado,
            request.samtools,
        ]
    signature = ledger.signature(
        basecall_stage,
        ["primary-mm-ml-input-v1", *[str(value) for value in dorado_command]],
        basecall_inputs,
    )
    if force or not ledger.reusable(basecall_stage, signature, [all_bam, all_bai]):
        for owned in (all_bam, all_bai, unsorted):
            owned.unlink(missing_ok=True)
        if request.modbam_input:
            _select_modbam_input(
                request, union_ids, modbam_out, runner, max(1, threads)
            )
        dorado_log = logs_out / "dorado.stderr.log"
        with (
            unsorted.open("wb") as output,
            dorado_log.open("w", encoding="utf-8") as error_log,
        ):
            runner.run(
                basecall_stage,
                dorado_command,
                env=_accelerator_environment(
                    request.gpu if request.pod5_dir else False
                ),
                stdout=output,  # type: ignore[arg-type]
                stderr=error_log,
            )
        _nonempty(unsorted, "Dorado modified-base BAM stream")
        runner.run(
            "methylation-dorado-sort",
            [
                request.samtools,
                "sort",
                "-@",
                str(max(1, threads)),
                "-o",
                all_bam,
                unsorted,
            ],
        )
        runner.run(
            "methylation-dorado-index",
            [request.samtools, "index", "-@", str(max(1, threads)), all_bam],
        )
        runner.run(
            "methylation-dorado-quickcheck", [request.samtools, "quickcheck", all_bam]
        )
        current_pod5_files = _pod5_files(request.pod5_dir) if request.pod5_dir else ()
        current_pod5_inventory = (
            _pod5_inventory_sha256(request.pod5_dir, current_pod5_files)
            if request.pod5_dir
            else ""
        )
        if (
            current_pod5_files != request.pod5_files
            or current_pod5_inventory != request.pod5_inventory_sha256
        ):
            raise OncoTracerError(
                "the explicit POD5 inventory changed during modified-base basecalling"
            )
        if request.modbam_input:
            source = request.modbam_input
            current_files = _modbam_files(source)
            if (
                current_files != request.modbam_files
                or _pod5_inventory_sha256(
                    source if source.is_dir() else source.parent, current_files
                )
                != request.modbam_inventory_sha256
            ):
                raise OncoTracerError(
                    "The modified-base BAM inputs changed during analysis. Use a snapshot of completed batches."
                )
            (modbam_out / ".selected_input.bam").unlink(missing_ok=True)
        unsorted.unlink(missing_ok=True)
        ledger.complete(basecall_stage, signature, [all_bam, all_bai])

    for index, sample in enumerate(samples):
        name = str(getattr(sample, "sample"))
        record = records[index]
        record["read_id_count"] = read_id_counts[name]
        raw_bam = modbam_out / f"{name}.mod.sorted.bam"
        combined_bam = modbam_out / f"{name}.5mC_5hmC_combined.sorted.bam"
        combined_bai = Path(str(combined_bam) + ".bai")
        bedmethyl = bed_out / f"{name}.CpG.combined_5mC_5hmC.bed"
        record_count = logs_out / f"{name}.modbam_record_count.txt"
        record_path = sample_status_out / f"{name}.json"
        sample_stage = f"methylation-sample-{name}"
        sample_inputs = [
            all_bam,
            all_bai,
            read_id_paths[name],
            fasta,
            request.modkit,
            request.samtools,
            *request.classifier_paths.values(),
        ]
        if request.classifier_executable is not None:
            sample_inputs.append(request.classifier_executable)
        if request.classifier == "marlin":
            sample_inputs.extend(
                [
                    root / "bin" / "scripts" / "native_marlin_prepare.R",
                    root / "bin" / "scripts" / "native_marlin_predict.R",
                ]
            )
        sample_signature = ledger.signature(
            sample_stage,
            [
                "native-ont-methylation-v2-primary-probe-qc",
                request.classifier,
                request.classifier_interface_contract_commit,
                f"threads={max(1, threads)}",
                f"gpu={str(request.gpu).lower()}",
            ],
            sample_inputs,
        )
        if not force and ledger.reusable(sample_stage, sample_signature, [record_path]):
            cached = _load_reusable_sample_record(record_path, outdir, name)
            if cached is not None:
                records[index] = cached
                _write_status(status_path, request, records)
                continue
        try:
            # A recomputation that ends in a no-call must not leave an older
            # prediction at the usual result path.
            sample_classifier_out = classifier_out / name
            previous_predictions = (
                [
                    sample_classifier_out / f"{name}.marlin_predictions.tsv",
                    sample_classifier_out / f"{name}.marlin_input.bed",
                ]
                if request.classifier == "marlin"
                else [
                    sample_classifier_out
                    / "predictions"
                    / f"{name}.sturgeon_{request.classifier_paths['model'].stem}{suffix}"
                    for suffix in (".csv", ".pdf")
                ]
            )
            for owned in (
                raw_bam,
                combined_bam,
                combined_bai,
                bedmethyl,
                record_count,
                record_path,
                *previous_predictions,
            ):
                owned.unlink(missing_ok=True)
            split_command = [
                request.samtools,
                "view",
                "-@",
                str(max(1, threads)),
                "-N",
                read_id_paths[name],
                "-F",
                "2304",
                "-b",
                "-o",
                raw_bam,
                all_bam,
            ]
            runner.run(f"methylation-split-{name}", split_command)
            _nonempty(raw_bam, f"sample modified-base BAM for {name}")
            runner.run(
                f"methylation-quickcheck-{name}",
                [request.samtools, "quickcheck", raw_bam],
            )
            with record_count.open("w", encoding="utf-8") as count_output:
                runner.run(
                    f"methylation-count-{name}",
                    [request.samtools, "view", "-c", raw_bam],
                    stdout=count_output,
                )
            count_text = record_count.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[0-9]+", count_text):
                raise OncoTracerError(
                    f"samtools returned an invalid modified-base record count for {name}"
                )
            modbam_records = int(count_text)
            record["modbam_records"] = modbam_records
            if modbam_records == 0:
                raise OncoTracerError(
                    f"sample {name} has no FASTQ-selected reads in the methylation input; check the FASTQ and POD5/BAM sample mapping"
                )
            runner.run(
                f"methylation-adjust-{name}",
                [
                    request.modkit,
                    "adjust-mods",
                    raw_bam,
                    combined_bam,
                    "--convert",
                    "h",
                    "m",
                    "--cpg",
                    "--threads",
                    str(max(1, threads)),
                    "--log-filepath",
                    logs_out / f"{name}.adjust_mods.log",
                    "--ff",
                ],
                env=_accelerator_environment(False),
            )
            runner.run(
                f"methylation-index-{name}",
                [request.samtools, "index", "-@", str(max(1, threads)), combined_bam],
            )
            _nonempty(combined_bai, f"sample modified-base BAM index for {name}")
            runner.run(
                f"methylation-pileup-{name}",
                [
                    request.modkit,
                    "pileup",
                    combined_bam,
                    bedmethyl,
                    "--cpg",
                    "--combine-strands",
                    "--modified-bases",
                    "5mC",
                    "--reference",
                    fasta,
                    "--threads",
                    str(max(1, threads)),
                    "--sampling-threads",
                    str(max(1, min(threads, 4))),
                    "--sampling-frac",
                    "1.0",
                    "--seed",
                    "1",
                    "--log-filepath",
                    logs_out / f"{name}.modkit_pileup.log",
                    "--suppress-progress",
                ],
                env=_accelerator_environment(False),
            )
            # An empty pileup is a meaningful negative result: no callable CpG
            # modifications. Preserve and classify it instead of converting it
            # into an opaque command failure.
            _existing_file(bedmethyl, f"Modkit CpG bedMethyl for {name}")
            rows, covered_rows, modified_calls = _bedmethyl_counts(bedmethyl)
            record.update(
                {
                    "cpg_rows": rows,
                    "covered_cpg_rows": covered_rows,
                    "modified_cpg_calls": modified_calls,
                    "bedmethyl": str(bedmethyl.relative_to(outdir)),
                }
            )
            if modified_calls == 0:
                record.update(
                    {
                        "status": "no_cpg_modifications",
                        "stage": "cpg_modification_preflight",
                        "error": "no_usable_cpg_cytosine_modification_calls",
                    }
                )
                atomic_write_json(record_path, record)
                ledger.complete(sample_stage, sample_signature, [record_path])
                _write_status(status_path, request, records)
                continue
            if request.classifier == "marlin":
                record["covered_classifier_probes"] = _marlin_probe_coverage(
                    bedmethyl, request.classifier_paths["probes"]
                )
                if record["covered_classifier_probes"] == 0:
                    record.update(
                        {
                            "status": "no_classifier_probes",
                            "stage": "classifier_probe_coverage",
                            "error": "CpG calls were found, but none cover the supplied MARLIN probes. No leukemia prediction was made. Check human alignment, hg38 probe coordinates and usable read yield.",
                        }
                    )
                    atomic_write_json(record_path, record)
                    ledger.complete(sample_stage, sample_signature, [record_path])
                    _write_status(status_path, request, records)
                    continue
            if request.classifier == "sturgeon":
                prediction = _run_sturgeon(
                    request,
                    name,
                    bedmethyl,
                    classifier_out / name,
                    runner,
                )
            else:
                prediction = _run_marlin(
                    root,
                    request,
                    name,
                    bedmethyl,
                    classifier_out / name,
                    runner,
                )
            record.update(
                {
                    "status": "complete",
                    "stage": "complete",
                    "error": None,
                    "classification": str(prediction.relative_to(outdir)),
                }
            )
            atomic_write_json(record_path, record)
            ledger.complete(sample_stage, sample_signature, [record_path])
        except (OSError, OncoTracerError, ValueError) as error:
            record.update(
                {
                    "status": "failed",
                    "stage": "methylation_or_classification",
                    "error": _safe_error(error),
                }
            )
        _write_status(status_path, request, records)

    status = _write_status(status_path, request, records)
    atomic_write_json(
        methylation_out / "methylation_provenance.json",
        _provenance_payload(request, str(status["overall_status"])),
    )
    return status
