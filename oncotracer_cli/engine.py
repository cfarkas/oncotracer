"""Native LP-WGS execution engine for OncoTracer v2."""

from __future__ import annotations

import contextlib
import csv
import fcntl
import gzip
import json
import math
import os
import re
import shutil
import stat
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from . import __version__
from .classifier import run_native_classifier
from .methylation import (
    MethylationRequest,
    methylation_plan,
    resolve_methylation_request,
    run_methylation,
    write_global_methylation_failure,
)
from .output_safety import OutputRunLease, claim_output_run, inspect_output_target
from .runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
    atomic_write_json,
    atomic_write_text,
    atomic_write_workflow_summary,
    download,
    load_flat_yaml,
    require_command,
    require_directory,
    require_file,
    runtime_root,
    sha256_file,
    sha256_text,
    utc_now,
)

HG38_BASE = "https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
SAMURAI_ICHOR_COMMIT = "6a901940288b008237703c6b181d447e7dee4fcf"
ICHOR_ASSET_BASE = (
    "https://raw.githubusercontent.com/DIncalciLab/samurai/"
    f"{SAMURAI_ICHOR_COMMIT}/assets/ichorcna"
)
ICHOR_ASSETS = {
    "gc": (
        "gc_hg38_500kb.wig",
        "4ae9c5d7f3e8260b3d192e88b21e717ac7f761946ba16e896b7d375557e85b57",
    ),
    "map": (
        "map_hg38_500kb.wig",
        "18efe127d1fde052b5537d4bf0494f73710fe38eb3ec0e7e49fa483b4c647d89",
    ),
    "centromere": (
        "GRCh38.GCA_000001405.2_centromere_acen.txt",
        "5ca2fed871adaa395773d932b94d40866690f69797694a21b057e8e1b3681e22",
    ),
    "reptime": (
        "Koren_repTiming_hg38_500kb.wig",
        "d7d20a549fb2a54a91dd73562ca820524a33b8bf33ab45bee881b1e031c96c8c",
    ),
    "pon": (
        "HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds",
        "2f2e94d529d0ef3ca74b93e0814c89fae0f6b918b38a7efec3bf4207c25452c0",
    ),
}
HG38_ASSETS = {
    "genome.fa": "d2b7be348fb20af46461855faec64dfbd21532620bd125783df050180446055e",
    "genome.fa.fai": "eb7e1fea3ac1c264d6f21a1358727ef533ad560634b0ef360818d970c5f09687",
    "genome.dict": "bae12a687634bdf379ee5eb61cb9c87f24a977523031844ef637c8943473b2f9",
}
BWA_INDEX_SUFFIXES = (".amb", ".ann", ".bwt", ".pac", ".sa")
REFERENCE_CACHE_SCHEMA = "oncotracer-reference-cache-owner-v1"
QDNASEQ_HG38_COMMIT = "cf7c07e39de0ac64a9c38cb030cba4626e2aae83"
QDNASEQ_HG38_SOURCE_SHA256 = {
    1: "b9ab0152649a913ad44ce38679bc8acd3073c636d9daa2b5926a1b410f666495",
    5: "fe897acdbe3555cf13f11e9c210b6cf236838990557777c74eed13b87475635a",
    10: "e26904321f93ea081559bcce7d59e3cede224db3eb7069581f0770a0ce138d1f",
    15: "f5e516f740c3e8acfbda782214358ea10f4e9b2689d9b76aad3d99c5bcf97849",
    30: "71a731557709991e62ea5c224919154cebdadb5f3d6e9f4a287f3999940f1e89",
    50: "44440e7d4b6d98fe7b422a5137ca121abe80a3ecafde374e20618d7ee480d054",
    100: "450b77a74dbba381e2f664334de90e41ec5e9eb6a5a8946d036c4b3534254d98",
    500: "1001b1cc723fb96d3eff54c04285049a13159dfe6100e593c628855dffd12089",
    1000: "3dc99f8080bc20b2fcfc737680b37894cf92b1416cd3550900b7811a45b76e42",
}
QDNASEQ_CACHE_POINTER_SCHEMA = "oncotracer-qdnaseq-cache-pointer-v1"


@dataclass(frozen=True)
class IlluminaSample:
    sample: str
    fastq_1: Path
    fastq_2: Path | None
    status: str


@dataclass(frozen=True)
class OntSample:
    sample: str
    barcode: str
    fastq_dir: Path
    status: str = "tumor"


@dataclass
class Toolchain:
    """Resolve stage-specific Conda prefixes without changing scientific code."""

    core_prefix: Path | None = None
    qdnaseq_prefix: Path | None = None
    ichorcna_prefix: Path | None = None
    classifier_prefix: Path | None = None
    gistic_prefix: Path | None = None

    @classmethod
    def from_environment(cls) -> "Toolchain":
        def resolved(name: str) -> Path | None:
            value = os.environ.get(name)
            return Path(value).expanduser().resolve() if value else None

        return cls(
            core_prefix=resolved("ONCOTRACER_CORE_PREFIX"),
            qdnaseq_prefix=resolved("ONCOTRACER_QDNASEQ_PREFIX"),
            ichorcna_prefix=resolved("ONCOTRACER_ICHORCNA_PREFIX"),
            classifier_prefix=resolved("ONCOTRACER_CLASSIFIER_PREFIX"),
            gistic_prefix=resolved("ONCOTRACER_GISTIC_PREFIX"),
        )

    def _prefix(self, group: str) -> Path | None:
        if group == "core":
            return self.core_prefix
        if group == "qdnaseq":
            return self.qdnaseq_prefix
        if group == "ichorcna":
            return self.ichorcna_prefix
        if group == "classifier":
            return self.classifier_prefix
        if group == "gistic":
            return self.gistic_prefix
        raise OncoTracerError(f"unknown toolchain group: {group}")

    def executable(self, group: str, name: str) -> str:
        """Return the exact executable for a native environment group.

        A configured prefix is authoritative. In particular, do not invoke a
        basename through conda run: an independently prepended environment can
        remain ahead of the requested prefix on PATH and select the wrong R or
        Python runtime.
        """
        if not name or Path(name).name != name:
            raise OncoTracerError(f"tool name must be a basename, found: {name!r}")
        prefix = self._prefix(group)
        if prefix is None:
            return require_command(name)
        prefix = prefix.expanduser().resolve()
        if not prefix.is_dir():
            raise OncoTracerError(
                f"configured {group} Conda prefix does not exist: {prefix}"
            )
        executable = prefix / "bin" / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise OncoTracerError(
                f"configured {group} Conda prefix is missing executable {name!r}: {executable}"
            )
        return str(executable)

    def wrap(self, group: str, command: Sequence[str | Path]) -> list[str]:
        argv = [str(item) for item in command]
        if not argv:
            raise OncoTracerError(f"empty command for toolchain group: {group}")
        return [self.executable(group, argv[0]), *argv[1:]]

    def environment(self, group: str) -> dict[str, str]:
        """Return the minimal exact-prefix environment required by a tool.

        Most native executables are relocatable and need no activation state.
        The pinned GISTIC package is an exception: its launcher depends on the
        MATLAB Compiler Runtime paths normally installed by a Conda activation
        hook. Resolve those paths from the configured GISTIC prefix instead of
        sourcing a shell or inheriting an unrelated environment.
        """
        prefix = self._prefix(group)
        if prefix is None:
            return {}
        prefix = prefix.expanduser().resolve()
        if not prefix.is_dir():
            raise OncoTracerError(
                f"configured {group} Conda prefix does not exist: {prefix}"
            )
        if group != "gistic":
            return {}

        roots = sorted(
            candidate
            for candidate in (prefix / "share").glob("mcr-*/v*")
            if candidate.is_dir()
        )
        valid: list[tuple[Path, list[Path]]] = []
        for root in roots:
            libraries = [
                root / "runtime" / "glnxa64",
                root / "bin" / "glnxa64",
                root / "sys" / "os" / "glnxa64",
            ]
            if all(path.is_dir() for path in libraries):
                valid.append((root, libraries))
        if len(valid) != 1:
            found = ", ".join(str(root) for root, _ in valid) or "none"
            raise OncoTracerError(
                "configured GISTIC prefix must contain exactly one usable MATLAB "
                f"Compiler Runtime under share/mcr-*/v*; found: {found}"
            )
        _, libraries = valid[0]
        return {
            "LD_LIBRARY_PATH": os.pathsep.join(str(path) for path in libraries),
            "LD_LIBRARY_PATH_MCR": "",
        }

    def rscript(self, group: str, command: Sequence[str | Path]) -> list[str]:
        """Run the group's exact Rscript with inherited R routing removed."""
        return [
            require_command("env"),
            "-u",
            "R_HOME",
            "-u",
            "R_LIBS",
            "-u",
            "R_LIBS_USER",
            "-u",
            "R_LIBS_SITE",
            self.executable(group, "Rscript"),
            "--vanilla",
            *[str(item) for item in command],
        ]


def _safe_sample(value: str) -> str:
    sample = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", sample):
        raise OncoTracerError(
            f"invalid sample ID {value!r}; use letters, digits, dot, underscore, or dash"
        )
    return sample


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


LOCAL_SAMPLE_PANEL_KEYS = frozenset(
    {
        "illumina_build_pon",
        "illumina_pon_normal_samples",
        "illumina_pon_min_normals",
        "illumina_pon_name",
        "illumina_pon_min_mapq",
        "illumina_pon_r_container",
        "ont_build_pon",
    }
)


def _reject_local_sample_panel(config: Mapping[str, object]) -> None:
    present = sorted(LOCAL_SAMPLE_PANEL_KEYS.intersection(config))
    if present:
        names = ", ".join(present)
        raise OncoTracerError(
            "local sample-derived panel construction was removed; delete deprecated "
            f"setting(s): {names}. NORMAL samples are analyzed independently and "
            "are never combined into a panel."
        )


def _as_int(value: object, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise OncoTracerError(f"expected integer, found {value!r}") from error


def _ont_caller(config: Mapping[str, object]) -> str:
    caller = str(config.get("ont_caller") or "ichorcna").strip().lower()
    if caller not in {"ichorcna", "qdnaseq"}:
        raise OncoTracerError("ont_caller must be ichorcna or qdnaseq")
    if caller == "qdnaseq":
        analysis_type = str(config.get("ont_analysis_type") or "").strip().lower()
        if analysis_type != "solid_biopsy":
            raise OncoTracerError(
                "ont_caller qdnaseq requires ont_analysis_type: solid_biopsy"
            )
    return caller


def parse_illumina_samplesheet(path: Path) -> list[IlluminaSample]:
    path = require_file(path, "Illumina samplesheet")
    rows: list[IlluminaSample] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"sample", "fastq_1", "fastq_2", "status"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise OncoTracerError(
                f"Illumina samplesheet is missing: {', '.join(sorted(missing))}"
            )
        for raw in reader:
            sample = _safe_sample(raw.get("sample", ""))
            if sample in seen:
                raise OncoTracerError(f"duplicate sample ID: {sample}")
            seen.add(sample)
            fq1 = require_file(Path(raw.get("fastq_1", "")), f"FASTQ 1 for {sample}")
            fq2_text = (raw.get("fastq_2") or "").strip()
            fq2 = (
                require_file(Path(fq2_text), f"FASTQ 2 for {sample}")
                if fq2_text
                else None
            )
            status = (raw.get("status") or "tumor").strip().lower()
            if status not in {"tumor", "normal"}:
                raise OncoTracerError(f"status for {sample} must be tumor or normal")
            rows.append(IlluminaSample(sample, fq1, fq2, status))
    if not rows:
        raise OncoTracerError("Illumina samplesheet has no data rows")
    layouts = {sample.fastq_2 is not None for sample in rows}
    if len(layouts) != 1:
        raise OncoTracerError(
            "a native Illumina run cannot mix single-end and paired-end libraries"
        )
    return rows


def _resolve_fastq_pass(folder: Path) -> Path:
    folder = require_directory(folder, "ONT folder")
    if folder.name == "fastq_pass":
        return folder
    if (folder / "fastq_pass").is_dir():
        return (folder / "fastq_pass").resolve()
    if any(path.is_dir() for path in folder.glob("barcode*")):
        return folder
    candidates = sorted(path for path in folder.rglob("fastq_pass") if path.is_dir())
    if len(candidates) != 1:
        raise OncoTracerError(
            f"expected exactly one fastq_pass under {folder}; found {len(candidates)}"
        )
    return candidates[0].resolve()


def parse_ont_samples(config: Mapping[str, object]) -> list[OntSample]:
    def parse_group(
        folder_value: object,
        barcodes_value: object,
        names_value: object,
        *,
        status: str,
        label: str,
    ) -> list[OntSample]:
        if not folder_value or not barcodes_value:
            raise OncoTracerError(
                f"ONT config requires {label}_folder and {label}_barcodes"
            )
        root = _resolve_fastq_pass(Path(str(folder_value)))
        barcodes = [
            token.strip()
            for token in str(barcodes_value).replace(";", ",").split(",")
            if token.strip()
        ]
        names = (
            [
                token.strip()
                for token in str(names_value).replace(";", ",").split(",")
                if token.strip()
            ]
            if names_value
            else barcodes
        )
        if len(names) != len(barcodes):
            raise OncoTracerError(
                f"{label}_sample_names must contain one name per barcode"
            )
        group: list[OntSample] = []
        for barcode, name in zip(barcodes, names, strict=True):
            candidate = root / barcode
            if not candidate.is_dir() and barcode.isdigit():
                candidate = root / f"barcode{int(barcode):02d}"
            if not candidate.is_dir():
                match = re.fullmatch(r"barcode0*(\d+)", barcode, flags=re.I)
                if match:
                    candidate = root / f"barcode{int(match.group(1)):02d}"
            if not candidate.is_dir():
                raise OncoTracerError(
                    f"ONT barcode directory not found: {root / barcode}"
                )
            group.append(
                OntSample(
                    _safe_sample(name),
                    candidate.name,
                    candidate.resolve(),
                    status=status,
                )
            )
        return group

    samples = parse_group(
        config.get("ont_folder"),
        config.get("ont_barcodes"),
        config.get("ont_sample_names"),
        status="tumor",
        label="ont",
    )
    normal_values = (
        config.get("ont_normal_folder"),
        config.get("ont_normal_barcodes"),
        config.get("ont_normal_sample_names"),
    )
    if any(value not in (None, "") for value in normal_values):
        if not normal_values[0] or not normal_values[1]:
            raise OncoTracerError(
                "independent ONT NORMAL samples require ont_normal_folder and "
                "ont_normal_barcodes"
            )
        samples.extend(
            parse_group(
                *normal_values,
                status="normal",
                label="ont_normal",
            )
        )
    if len({sample.sample for sample in samples}) != len(samples):
        raise OncoTracerError(
            "ONT sample names must be unique across TUMOR and NORMAL inputs"
        )
    directories = [sample.fastq_dir for sample in samples]
    if len(set(directories)) != len(directories):
        raise OncoTracerError(
            "each ONT barcode directory may appear only once across TUMOR and NORMAL inputs"
        )
    if any(sample.status == "normal" for sample in samples):
        caller = _ont_caller(config)
        if caller != "qdnaseq":
            raise OncoTracerError(
                "independent ONT NORMAL samples require ont_analysis_type: "
                "solid_biopsy and ont_caller: qdnaseq"
            )
    return samples


def _canonical_json_sha256(value: object) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _reference_identity(kind: str) -> str:
    if kind == "samurai-hg38":
        payload: object = {"kind": kind, "assets": HG38_ASSETS}
    elif kind == "ichorcna-hg38-500kb":
        payload = {
            "kind": kind,
            "samurai_commit": SAMURAI_ICHOR_COMMIT,
            "assets": ICHOR_ASSETS,
        }
    elif match := re.fullmatch(r"qdnaseq-hg38-(\d+)kb", kind):
        binsize = int(match.group(1))
        expected_sha256 = QDNASEQ_HG38_SOURCE_SHA256.get(binsize)
        if expected_sha256 is None:
            raise OncoTracerError(f"unsupported qDNAseq hg38 bin size: {binsize}")
        payload = {
            "kind": kind,
            "source_commit": QDNASEQ_HG38_COMMIT,
            "source_sha256": expected_sha256,
            "object": f"hg38.{binsize}kbp.SR50",
        }
    else:  # pragma: no cover - internal contract
        raise OncoTracerError(f"unknown reference cache kind: {kind}")
    return _canonical_json_sha256(payload)


def _lstat(path: Path, label: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise OncoTracerError(f"cannot inspect {label} {path}: {error}") from error


def _require_physical_directory(path: Path, label: str) -> Path:
    observed = _lstat(path, label)
    if observed is None:
        raise OncoTracerError(f"required {label} is missing: {path}")
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise OncoTracerError(
            f"{label} must be a physical directory, not a symlink or non-directory: {path}"
        )
    return path


def _require_physical_file(path: Path, label: str, *, nonempty: bool = True) -> Path:
    observed = _lstat(path, label)
    if observed is None:
        raise OncoTracerError(f"required {label} is missing: {path}")
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise OncoTracerError(
            f"{label} must be a physical regular file, not a symlink or non-file: {path}"
        )
    if nonempty and observed.st_size <= 0:
        raise OncoTracerError(f"required {label} is empty: {path}")
    return path


def _ensure_physical_directory(path: Path, label: str) -> Path:
    observed = _lstat(path, label)
    if observed is None:
        try:
            path.mkdir()
        except FileExistsError:
            pass
        except OSError as error:
            raise OncoTracerError(f"cannot create {label} {path}: {error}") from error
    return _require_physical_directory(path, label)


def _marker_matches(path: Path, expected: Mapping[str, object]) -> bool:
    try:
        _require_physical_file(path, "reference ownership marker")
        if path.lstat().st_nlink != 1:
            return False
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OncoTracerError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    return observed == dict(expected)


def _fsync_directory(path: Path, label: str) -> None:
    _require_physical_directory(path, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OncoTracerError(f"cannot open {label} {path}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OncoTracerError(f"{label} is not a stable physical directory: {path}")
        os.fsync(descriptor)
    except OSError as error:
        raise OncoTracerError(f"cannot sync {label} {path}: {error}") from error
    finally:
        os.close(descriptor)


def _fsync_physical_file(path: Path, label: str) -> None:
    _require_physical_file(path, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _require_stable_open_file(descriptor, path, label)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_owned_json(
    path: Path,
    value: object,
    label: str,
    *,
    temporary_directory: Path | None = None,
) -> None:
    _require_physical_directory(path.parent, f"{label} parent")
    observed = _lstat(path, label)
    if observed is not None and (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
    ):
        raise OncoTracerError(f"{label} is not a replaceable physical file: {path}")
    original_identity = (
        (observed.st_dev, observed.st_ino) if observed is not None else None
    )
    staging_parent = temporary_directory or path.parent
    _require_physical_directory(staging_parent, f"{label} staging parent")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=staging_parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        _require_physical_directory(path.parent, f"{label} parent")
        current = _lstat(path, label)
        current_identity = (
            (current.st_dev, current.st_ino) if current is not None else None
        )
        if current_identity != original_identity:
            raise OncoTracerError(f"{label} changed during atomic publication: {path}")
        if current is not None and (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
        ):
            raise OncoTracerError(f"{label} became unsafe during publication: {path}")
        os.replace(temporary, path)
        _require_physical_file(path, label)
        _fsync_directory(path.parent, f"{label} parent")
        if staging_parent != path.parent:
            _fsync_directory(staging_parent, f"{label} staging parent")
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _physical_directory_lock(
    path: Path, label: str, *, exclusive: bool = True
) -> Iterator[None]:
    """Serialize a claim without creating a lock object in an unowned parent."""
    _require_physical_directory(path, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OncoTracerError(f"cannot open {label} {path}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OncoTracerError(f"{label} is not a stable physical directory: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        named = _lstat(path, label)
        if (
            named is None
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OncoTracerError(f"{label} changed while acquiring its lock: {path}")
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _claim_marker(directory: Path, expected: Mapping[str, object], label: str) -> None:
    initial = _require_physical_directory(directory, label).lstat()
    with _physical_directory_lock(directory.parent, f"{label} parent"):
        current = _require_physical_directory(directory, label).lstat()
        if (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino):
            raise OncoTracerError(
                f"{label} changed while acquiring ownership: {directory}"
            )
        marker = directory / ".oncotracer-reference-owner.json"
        if _marker_matches(marker, expected):
            return
        if _lstat(marker, "reference ownership marker") is not None:
            raise OncoTracerError(
                f"{label} has a mismatched ownership marker: {marker}"
            )
        entries = [entry for entry in directory.iterdir() if entry != marker]
        if entries:
            raise OncoTracerError(
                f"refusing to claim nonempty unowned {label}: {directory}"
            )
        # Stage the marker outside the unowned directory. If the process is
        # killed before rename, the directory stays empty and a later run can
        # safely claim it instead of being blocked by a partial marker.
        _atomic_write_owned_json(
            marker,
            dict(expected),
            "reference ownership marker",
            temporary_directory=directory.parent,
        )
        if not _marker_matches(marker, expected):
            raise OncoTracerError(
                f"could not verify {label} ownership marker: {marker}"
            )


def _owned_reference_cache(lpwgs_root: Path, kind: str) -> Path:
    """Return one marker-owned, content-addressed cache below the project root."""
    lpwgs_root.mkdir(parents=True, exist_ok=True)
    project = lpwgs_root.expanduser().resolve(strict=True)
    _require_physical_directory(project, "LP-WGS project root")
    state = _ensure_physical_directory(
        project / ".oncotracer", "OncoTracer state directory"
    )
    cache_root = _ensure_physical_directory(
        state / "reference-cache", "reference cache root"
    )
    root_marker = {
        "schema": REFERENCE_CACHE_SCHEMA,
        "kind": "reference-cache-root",
        "canonical_path": str(cache_root),
    }
    _claim_marker(cache_root, root_marker, "reference cache root")

    identity = _reference_identity(kind)
    destination = _ensure_physical_directory(
        cache_root / f"{kind}-{identity[:16]}", f"{kind} reference cache"
    )
    marker = {
        "schema": REFERENCE_CACHE_SCHEMA,
        "kind": kind,
        "identity": identity,
        "canonical_path": str(destination),
    }
    _claim_marker(destination, marker, f"{kind} reference cache")
    return destination


def _require_stable_open_file(descriptor: int, path: Path, label: str) -> None:
    opened = os.fstat(descriptor)
    try:
        named = path.lstat()
    except OSError as error:
        raise OncoTracerError(f"cannot revalidate {label} {path}: {error}") from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OncoTracerError(f"{label} is not a stable physical file: {path}")


@contextlib.contextmanager
def _reference_lock(path: Path, *, exclusive: bool, create: bool) -> Iterator[None]:
    """Lock one physical file without following a final symbolic link."""
    _require_physical_directory(path.parent, "reference lock parent")
    flags = os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise OncoTracerError(f"cannot open reference lock {path}: {error}") from error
    try:
        _require_stable_open_file(descriptor, path, "reference lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        _require_stable_open_file(descriptor, path, "reference lock")
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_pinned_file(path: Path, expected_sha256: str, label: str) -> Path:
    _require_physical_file(path, label)
    observed = sha256_file(path)
    if observed != expected_sha256:
        raise OncoTracerError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, observed {observed}: {path}"
        )
    return path


def _ensure_owned_pinned_file(
    root: Path,
    path: Path,
    url: str,
    expected_sha256: str,
    label: str,
) -> Path:
    """Repair one owned cache file only after a complete verified download."""
    observed = _lstat(path, label)
    if observed is not None:
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
            raise OncoTracerError(f"owned {label} is not a physical file: {path}")
        if observed.st_size > 0 and sha256_file(path) == expected_sha256:
            return path

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.download-", dir=root
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    partial = temporary.with_name(f".{temporary.name}.part")
    try:
        download(url, temporary)
        _validate_pinned_file(temporary, expected_sha256, f"downloaded {label}")
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return _validate_pinned_file(path, expected_sha256, label)


def _prepare_hg38_base(
    reference_root: Path, lock: Path, *, owned: bool
) -> dict[str, Path]:
    if owned:
        # A repair can replace the FASTA used by either backend. Take both index
        # locks so BWA, minimap2, and methylation readers cannot observe a mixed
        # base-reference generation.
        locks = sorted(
            {
                _reference_state_paths(reference_root, "bwa")[0],
                _reference_state_paths(reference_root, "minimap2")[0],
            }
        )
    else:
        # External references are never repaired. Reuse the selected backend
        # lease, avoiding a new lock-file requirement for the unused backend.
        locks = [lock]
    with contextlib.ExitStack() as stack:
        for selected in locks:
            if not owned:
                _require_physical_file(
                    selected, "external hg38 reference lock", nonempty=False
                )
            stack.enter_context(
                _reference_lock(selected, exclusive=owned, create=owned)
            )
        return _prepare_hg38_base_locked(reference_root, owned=owned)


def _prepare_hg38_base_locked(reference_root: Path, *, owned: bool) -> dict[str, Path]:
    paths = {name: reference_root / name for name in HG38_ASSETS}
    for name, expected_sha256 in HG38_ASSETS.items():
        path = paths[name]
        if owned:
            _ensure_owned_pinned_file(
                reference_root,
                path,
                f"{HG38_BASE}/{name}",
                expected_sha256,
                f"hg38 {name}",
            )
        else:
            _validate_pinned_file(path, expected_sha256, f"external hg38 {name}")

    first = ""
    with paths["genome.fa"].open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(">"):
                first = line[1:].split()[0]
                break
    if not first.startswith("chr"):
        raise OncoTracerError(
            f"hg38 reference must use UCSC chr names; first contig is {first!r}"
        )
    if _fai_contigs(paths["genome.fa.fai"]) is None:
        raise OncoTracerError(f"invalid hg38 FASTA index: {paths['genome.fa.fai']}")
    return paths


def _fai_contigs(fai: Path) -> list[tuple[str, int]] | None:
    try:
        contigs: list[tuple[str, int]] = []
        for raw in fai.read_text(encoding="utf-8").splitlines():
            fields = raw.split("\t")
            if len(fields) < 5:
                return None
            name, length_text = fields[:2]
            length = int(length_text)
            if not name or length <= 0:
                return None
            contigs.append((name, length))
        if not contigs or len({name for name, _ in contigs}) != len(contigs):
            return None
        return contigs
    except (OSError, UnicodeError, ValueError):
        return None


def _bwa_index_matches_fai(fai: Path, prefix: Path) -> bool:
    paths = {suffix: Path(f"{prefix}{suffix}") for suffix in BWA_INDEX_SUFFIXES}
    contigs = _fai_contigs(fai)
    if contigs is None or any(
        not path.is_file() or path.stat().st_size <= 0 for path in paths.values()
    ):
        return False
    try:
        total_length = sum(length for _, length in contigs)
        ann_lines = paths[".ann"].read_text(encoding="utf-8").splitlines()
        ann_header = ann_lines[0].split()
        if len(ann_header) < 2 or [int(value) for value in ann_header[:2]] != [
            total_length,
            len(contigs),
        ]:
            return False
        if len(ann_lines) != 1 + 2 * len(contigs):
            return False
        expected_offset = 0
        for index, (expected_name, expected_length) in enumerate(contigs):
            annotation = ann_lines[1 + 2 * index].split(maxsplit=2)
            coordinates = ann_lines[2 + 2 * index].split()
            if len(annotation) < 2 or len(coordinates) < 3:
                return False
            offset, length = int(coordinates[0]), int(coordinates[1])
            if (
                annotation[1] != expected_name
                or offset != expected_offset
                or length != expected_length
            ):
                return False
            expected_offset += expected_length

        amb_lines = paths[".amb"].read_text(encoding="utf-8").splitlines()
        amb_header = amb_lines[0].split()
        if len(amb_header) != 3:
            return False
        amb_length, amb_contigs, amb_regions = (int(value) for value in amb_header)
        if (amb_length, amb_contigs) != (total_length, len(contigs)):
            return False
        if amb_regions < 0 or len(amb_lines) != amb_regions + 1:
            return False
        for raw in amb_lines[1:]:
            fields = raw.split()
            if len(fields) != 3:
                return False
            offset, length = int(fields[0]), int(fields[1])
            if offset < 0 or length <= 0 or offset + length > total_length:
                return False
        expected_pac_bytes = (total_length + 3) // 4 + 1
        return paths[".pac"].stat().st_size == expected_pac_bytes
    except (IndexError, OSError, UnicodeError, ValueError):
        return False


def _portable_conda_identities(executable: Path) -> dict[str, object] | None:
    prefix = executable.parent.parent if executable.parent.name == "bin" else None
    metadata = prefix / "conda-meta" if prefix is not None else None
    if metadata is None or not metadata.is_dir():
        return None
    expected_package = {"bwa": "bwa", "minimap2": "minimap2"}.get(executable.name)
    if expected_package is None:  # pragma: no cover - internal contract
        raise OncoTracerError(f"unsupported indexed-reference tool: {executable.name}")
    relative = executable.relative_to(prefix).as_posix()
    records: list[tuple[Path, dict[str, object]]] = []
    try:
        for record_path in sorted(metadata.glob(f"{expected_package}-*.json")):
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("name") == expected_package:
                records.append((record_path, record))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OncoTracerError(
            f"cannot establish Conda identity for {executable}: {error}"
        ) from error
    if len(records) != 1:
        raise OncoTracerError(
            f"cannot unambiguously identify Conda package {expected_package!r} for {executable}"
        )
    record_path, record = records[0]
    stable_fields = (
        "name",
        "version",
        "build",
        "build_number",
        "subdir",
        "sha256",
        "md5",
    )
    legacy_fields = (*stable_fields[:4], "channel", *stable_fields[4:])
    if any(
        not isinstance(record.get(field), str) or not record[field]
        for field in ("name", "version", "build")
    ):
        raise OncoTracerError(f"invalid Conda package record: {record_path}")
    owned: set[str] = set()
    if "files" in record:
        files = record["files"]
        if not isinstance(files, list) or any(
            not isinstance(item, str) for item in files
        ):
            raise OncoTracerError(f"malformed Conda file ownership: {record_path}")
        owned.update(files)
    paths_data = record.get("paths_data")
    if paths_data is not None:
        if not isinstance(paths_data, dict) or not isinstance(
            paths_data.get("paths"), list
        ):
            raise OncoTracerError(f"malformed Conda path ownership: {record_path}")
        for item in paths_data["paths"]:
            if not isinstance(item, dict) or not isinstance(item.get("_path"), str):
                raise OncoTracerError(f"malformed Conda path ownership: {record_path}")
            owned.add(item["_path"])
    if owned and relative not in owned:
        raise OncoTracerError(f"Conda record does not own {relative}: {record_path}")
    owner = {field: record.get(field) for field in stable_fields}
    legacy_owner = {field: record.get(field) for field in legacy_fields}
    return {
        "validity": {
            "package_count": 1,
            "sha256": _canonical_json_sha256([owner]),
        },
        "legacy": {
            "package_count": 1,
            "sha256": _canonical_json_sha256([legacy_owner]),
        },
        "channel": record.get("channel"),
    }


def _contract_with_conda_information(
    contract: dict[str, object], conda: Mapping[str, object] | None
) -> dict[str, object]:
    if conda is None:
        return contract
    legacy = dict(contract)
    legacy["conda_environment"] = conda["legacy"]
    contract["informational_provenance"] = {
        "conda_channel": conda["channel"],
        "legacy_contract": legacy,
    }
    return contract


def _index_build_contract(
    kind: str,
    fasta_sha256: str,
    fai_sha256: str,
    executable_value: str,
) -> dict[str, object]:
    configured = Path(executable_value).expanduser()
    executable = configured.resolve(strict=True)
    executable_sha256 = sha256_file(executable)
    conda = _portable_conda_identities(configured)
    if kind == "bwa":
        return _contract_with_conda_information(
            {
                "schema": "oncotracer-bwa-build-contract-v1",
                "fasta_sha256": fasta_sha256,
                "fai_sha256": fai_sha256,
                "bwa_sha256": executable_sha256,
                "conda_environment": conda["validity"] if conda is not None else None,
                "logical_arguments": ["index", "-p", "<PREFIX>", "<FASTA>"],
            },
            conda,
        )
    return _contract_with_conda_information(
        {
            "schema": "oncotracer-minimap2-build-contract-v1",
            "fasta_sha256": fasta_sha256,
            "fai_sha256": fai_sha256,
            "minimap2_sha256": executable_sha256,
            "conda_environment": conda["validity"] if conda is not None else None,
            "logical_arguments": ["-x", "map-ont", "-d", "<INDEX>", "<FASTA>"],
        },
        conda,
    )


def _index_validity_contract(contract: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in contract.items()
        if key != "informational_provenance"
    }


def _index_build_identity(contract: Mapping[str, object]) -> str:
    return _canonical_json_sha256(_index_validity_contract(contract))


def _legacy_index_build_contract_matches(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Accept v2 manifests whose Conda hash included a machine-specific channel."""
    if "informational_provenance" in observed:
        return False
    observed_stable = dict(observed)
    observed_conda = observed_stable.pop("conda_environment", None)
    expected_stable = _index_validity_contract(expected)
    expected_stable.pop("conda_environment", None)
    return bool(
        observed_stable == expected_stable
        and isinstance(observed_conda, dict)
        and observed_conda.get("package_count") == 1
        and isinstance(observed_conda.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(observed_conda["sha256"]))
    )


def _index_build_contract_matches(
    observed: object,
    observed_identity: object,
    expected: Mapping[str, object],
) -> bool:
    if not isinstance(observed, dict):
        return False
    if not isinstance(observed_identity, str) or observed_identity != (
        _index_build_identity(observed)
    ):
        return False
    if _index_validity_contract(observed) == _index_validity_contract(expected):
        return True
    information = expected.get("informational_provenance")
    if isinstance(information, dict):
        legacy = information.get("legacy_contract")
        if isinstance(legacy, dict) and observed == legacy:
            return True
    return _legacy_index_build_contract_matches(observed, expected)


def _bwa_manifest_payload(
    contract: Mapping[str, object], prefix: Path
) -> dict[str, object]:
    return {
        "schema": "oncotracer-bwa-index-manifest-v1",
        "build": dict(contract),
        "build_identity": _index_build_identity(contract),
        "indexes": {
            suffix: {
                "bytes": Path(f"{prefix}{suffix}").stat().st_size,
                "sha256": sha256_file(Path(f"{prefix}{suffix}")),
            }
            for suffix in BWA_INDEX_SUFFIXES
        },
    }


def _bwa_manifest_matches(
    manifest_path: Path, prefix: Path, contract: Mapping[str, object]
) -> bool:
    try:
        _require_physical_file(manifest_path, "BWA reference manifest")
        paths = {suffix: Path(f"{prefix}{suffix}") for suffix in BWA_INDEX_SUFFIXES}
        for suffix, path in paths.items():
            _require_physical_file(path, f"BWA index {suffix}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != "oncotracer-bwa-index-manifest-v1"
            or not _index_build_contract_matches(
                manifest.get("build"),
                manifest.get("build_identity"),
                contract,
            )
        ):
            return False
        records = manifest.get("indexes")
        if not isinstance(records, dict) or set(records) != set(BWA_INDEX_SUFFIXES):
            return False
        for suffix, path in paths.items():
            record = records[suffix]
            if not isinstance(record, dict):
                return False
            if record.get("bytes") != path.stat().st_size or record.get(
                "sha256"
            ) != sha256_file(path):
                return False
        return True
    except (OncoTracerError, OSError, UnicodeError, json.JSONDecodeError):
        return False


def _minimap_manifest_payload(
    contract: Mapping[str, object], index: Path
) -> dict[str, object]:
    return {
        "schema": "oncotracer-minimap2-index-manifest-v1",
        "build": dict(contract),
        "build_identity": _index_build_identity(contract),
        "index": {"bytes": index.stat().st_size, "sha256": sha256_file(index)},
    }


def _minimap_manifest_matches(
    manifest_path: Path, index: Path, contract: Mapping[str, object]
) -> bool:
    try:
        _require_physical_file(manifest_path, "minimap2 reference manifest")
        _require_physical_file(index, "minimap2 reference index")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest.get("index") if isinstance(manifest, dict) else None
        return bool(
            isinstance(manifest, dict)
            and manifest.get("schema") == "oncotracer-minimap2-index-manifest-v1"
            and _index_build_contract_matches(
                manifest.get("build"),
                manifest.get("build_identity"),
                contract,
            )
            and isinstance(record, dict)
            and record.get("bytes") == index.stat().st_size
            and record.get("sha256") == sha256_file(index)
        )
    except (OncoTracerError, OSError, UnicodeError, json.JSONDecodeError):
        return False


def _minimap_index_matches_fai(
    fai: Path,
    index: Path,
    minimap2: str,
    runner: CommandRunner,
    *,
    stage: str,
) -> bool:
    expected = _fai_contigs(fai)
    if expected is None:
        return False
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout,
        tempfile.TemporaryFile(mode="w+b") as stderr,
    ):
        result = runner.run(
            stage,
            [minimap2, "-a", index, os.devnull],
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
        if result.returncode != 0:
            return False
        stdout.seek(0)
        header = stdout.read().decode("utf-8", errors="replace")
    observed: list[tuple[str, int]] = []
    try:
        for raw in header.splitlines():
            if not raw.startswith("@SQ\t"):
                continue
            tags = {
                field.partition(":")[0]: field.partition(":")[2]
                for field in raw.split("\t")[1:]
                if ":" in field
            }
            observed.append((tags["SN"], int(tags["LN"])))
    except (KeyError, ValueError):
        return False
    return bool(observed) and observed == expected


def _reference_state_paths(root: Path, kind: str) -> tuple[Path, Path]:
    state = root / ".oncotracer"
    if kind == "bwa":
        return (
            state / "locks" / "samurai-hg38.bwa-index.lock",
            state / "reference-index-provenance" / "samurai-hg38.bwa-index.json",
        )
    return (
        state / "locks" / "samurai-hg38-map-ont.minimap2-index.lock",
        state
        / "reference-index-provenance"
        / "samurai-hg38-map-ont.minimap2-index.json",
    )


def _prepare_reference_state(root: Path, *, owned: bool) -> None:
    state = root / ".oncotracer"
    if owned:
        _ensure_physical_directory(state, "reference state directory")
        _ensure_physical_directory(state / "locks", "reference lock directory")
        _ensure_physical_directory(
            state / "reference-index-provenance", "reference provenance directory"
        )
    else:
        _require_physical_directory(state, "external reference state directory")
        _require_physical_directory(
            state / "locks", "external reference lock directory"
        )
        _require_physical_directory(
            state / "reference-index-provenance",
            "external reference provenance directory",
        )


def _prepare_bwa_index(
    root: Path,
    fasta: Path,
    fai: Path,
    toolchain: Toolchain,
    runner: CommandRunner,
    ledger: StageLedger,
    *,
    owned: bool,
) -> tuple[Path, Path, Path, str]:
    bwa = toolchain.executable("core", "bwa")
    contract = _index_build_contract(
        "bwa", HG38_ASSETS["genome.fa"], HG38_ASSETS["genome.fa.fai"], bwa
    )
    bwa_directory = root / "bwa"
    if owned:
        _ensure_physical_directory(bwa_directory, "owned BWA index directory")
    else:
        _require_physical_directory(bwa_directory, "external BWA index directory")
    prefix = bwa_directory / "genome"
    lock, manifest = _reference_state_paths(root, "bwa")
    if not owned:
        _require_physical_file(lock, "external BWA reference lock", nonempty=False)
    with _reference_lock(lock, exclusive=owned, create=owned):
        valid = _bwa_manifest_matches(
            manifest, prefix, contract
        ) and _bwa_index_matches_fai(fai, prefix)
        if not valid and not owned:
            raise OncoTracerError(
                "external/shared BWA reference is incomplete or does not match the "
                "pinned FASTA and installed BWA; refusing to modify it"
            )
        if not valid:
            temporary_directory = Path(
                tempfile.mkdtemp(prefix=".genome.bwa-build-", dir=bwa_directory)
            )
            temporary_prefix = temporary_directory / "genome"
            try:
                runner.run(
                    "reference-bwa-build",
                    [bwa, "index", "-p", temporary_prefix, fasta],
                )
                if not _bwa_index_matches_fai(fai, temporary_prefix):
                    raise OncoTracerError(
                        "new BWA index is incomplete or inconsistent with genome.fa.fai"
                    )
                payload = _bwa_manifest_payload(contract, temporary_prefix)
                for suffix in BWA_INDEX_SUFFIXES:
                    candidate = Path(f"{temporary_prefix}{suffix}")
                    candidate.chmod(0o644)
                    os.replace(candidate, Path(f"{prefix}{suffix}"))
                _atomic_write_owned_json(manifest, payload, "BWA reference manifest")
            finally:
                shutil.rmtree(temporary_directory)
        if not (
            _bwa_manifest_matches(manifest, prefix, contract)
            and _bwa_index_matches_fai(fai, prefix)
        ):
            raise OncoTracerError("published BWA reference failed validation")
        signature = ledger.signature(
            "reference-bwa", ["validate-read-only", bwa], [fasta, fai]
        )
        outputs = [
            *[Path(f"{prefix}{suffix}") for suffix in BWA_INDEX_SUFFIXES],
            manifest,
        ]
        if not ledger.reusable("reference-bwa", signature, outputs):
            ledger.complete("reference-bwa", signature, outputs)
        return prefix, lock, manifest, sha256_file(manifest)


def _prepare_minimap_index(
    root: Path,
    fasta: Path,
    fai: Path,
    toolchain: Toolchain,
    runner: CommandRunner,
    ledger: StageLedger,
    *,
    owned: bool,
) -> tuple[Path, Path, Path, str]:
    minimap2 = toolchain.executable("core", "minimap2")
    contract = _index_build_contract(
        "minimap2", HG38_ASSETS["genome.fa"], HG38_ASSETS["genome.fa.fai"], minimap2
    )
    index = root / "genome.fa.map-ont.mmi"
    lock, manifest = _reference_state_paths(root, "minimap2")
    if not owned:
        _require_physical_file(lock, "external minimap2 reference lock", nonempty=False)
    with _reference_lock(lock, exclusive=owned, create=owned):
        valid = _minimap_manifest_matches(
            manifest, index, contract
        ) and _minimap_index_matches_fai(
            fai,
            index,
            minimap2,
            runner,
            stage="reference-minimap2-validate",
        )
        if not valid and not owned:
            raise OncoTracerError(
                "external/shared minimap2 reference is incomplete or does not match the "
                "pinned FASTA and installed minimap2; refusing to modify it"
            )
        if not valid:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".genome.fa.map-ont.build-", suffix=".mmi", dir=root
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                runner.run(
                    "reference-minimap2-build",
                    [minimap2, "-x", "map-ont", "-d", temporary, fasta],
                )
                if not _minimap_index_matches_fai(
                    fai,
                    temporary,
                    minimap2,
                    runner,
                    stage="reference-minimap2-validate-candidate",
                ):
                    raise OncoTracerError(
                        "new minimap2 index is unreadable or does not match genome.fa.fai"
                    )
                payload = _minimap_manifest_payload(contract, temporary)
                temporary.chmod(0o644)
                os.replace(temporary, index)
                _atomic_write_owned_json(
                    manifest, payload, "minimap2 reference manifest"
                )
            finally:
                temporary.unlink(missing_ok=True)
        if not (
            _minimap_manifest_matches(manifest, index, contract)
            and _minimap_index_matches_fai(
                fai,
                index,
                minimap2,
                runner,
                stage="reference-minimap2-validate-published",
            )
        ):
            raise OncoTracerError("published minimap2 reference failed validation")
        signature = ledger.signature(
            "reference-minimap2", ["validate-read-only", minimap2], [fasta, fai]
        )
        outputs = [index, manifest]
        if not ledger.reusable("reference-minimap2", signature, outputs):
            ledger.complete("reference-minimap2", signature, outputs)
        return index, lock, manifest, sha256_file(manifest)


def prepare_reference(
    lpwgs_root: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    need_bwa: bool,
    need_minimap2: bool,
    threads: int,
) -> dict[str, object]:
    lpwgs_root.mkdir(parents=True, exist_ok=True)
    project = lpwgs_root.expanduser().resolve(strict=True)
    requested = project / "references" / "samurai_hg38"
    if requested.exists() or requested.is_symlink():
        try:
            ref_dir = requested.resolve(strict=True)
        except OSError as error:
            raise OncoTracerError(
                f"cannot resolve external hg38 reference: {requested}"
            ) from error
        _require_physical_directory(ref_dir, "external hg38 reference root")
        owned = False
    else:
        ref_dir = _owned_reference_cache(project, "samurai-hg38")
        owned = True

    _prepare_reference_state(ref_dir, owned=owned)
    base_kind = "minimap2" if need_minimap2 else "bwa"
    base_lock, _unused_base_manifest = _reference_state_paths(ref_dir, base_kind)
    base = _prepare_hg38_base(ref_dir, base_lock, owned=owned)
    base_generation = _canonical_json_sha256(HG38_ASSETS)
    bwa_prefix = ref_dir / "bwa" / "genome"
    bwa_lock, bwa_manifest = _reference_state_paths(ref_dir, "bwa")
    bwa_generation = ""
    if need_bwa:
        bwa_prefix, bwa_lock, bwa_manifest, bwa_generation = _prepare_bwa_index(
            ref_dir,
            base["genome.fa"],
            base["genome.fa.fai"],
            toolchain,
            runner,
            ledger,
            owned=owned,
        )

    minimap_index = ref_dir / "genome.fa.map-ont.mmi"
    minimap_lock, minimap_manifest = _reference_state_paths(ref_dir, "minimap2")
    minimap_generation = ""
    if need_minimap2:
        minimap_index, minimap_lock, minimap_manifest, minimap_generation = (
            _prepare_minimap_index(
                ref_dir,
                base["genome.fa"],
                base["genome.fa.fai"],
                toolchain,
                runner,
                ledger,
                owned=owned,
            )
        )

    return {
        "reference_root": ref_dir,
        "reference_owned": owned,
        "fasta": base["genome.fa"],
        "fai": base["genome.fa.fai"],
        "dict": base["genome.dict"],
        "base_lock": base_lock,
        "base_generation": base_generation,
        "fasta_sha256": HG38_ASSETS["genome.fa"],
        "fai_sha256": HG38_ASSETS["genome.fa.fai"],
        "bwa_prefix": bwa_prefix,
        "bwa_lock": bwa_lock,
        "bwa_manifest": bwa_manifest,
        "bwa_generation": bwa_generation,
        "minimap2_index": minimap_index,
        "minimap2_lock": minimap_lock,
        "minimap2_manifest": minimap_manifest,
        "minimap2_generation": minimap_generation,
    }


@contextlib.contextmanager
def _validated_fasta_reader(
    reference: Mapping[str, object],
    runner: CommandRunner,
) -> Iterator[None]:
    if runner.dry_run:
        yield
        return
    root = Path(str(reference.get("reference_root", "")))
    paths = {
        "genome.fa": Path(str(reference.get("fasta", ""))),
        "genome.fa.fai": Path(str(reference.get("fai", ""))),
        "genome.dict": Path(str(reference.get("dict", ""))),
    }
    lock = Path(str(reference.get("base_lock", "")))
    expected_locks = {
        _reference_state_paths(root, "bwa")[0],
        _reference_state_paths(root, "minimap2")[0],
    }
    if (
        paths["genome.fa"] != root / "genome.fa"
        or paths["genome.fa.fai"] != root / "genome.fa.fai"
        or paths["genome.dict"] != root / "genome.dict"
        or lock not in expected_locks
    ):
        raise OncoTracerError(
            "prepared FASTA reference paths escaped their physical root"
        )
    _require_physical_directory(root, "prepared reference root")
    _require_physical_file(lock, "FASTA reference lock", nonempty=False)
    expected_generation = reference.get("base_generation")
    if expected_generation != _canonical_json_sha256(HG38_ASSETS):
        raise OncoTracerError("prepared FASTA reference has no exact pinned generation")
    with _reference_lock(lock, exclusive=False, create=False):
        for name, expected_sha256 in HG38_ASSETS.items():
            _validate_pinned_file(paths[name], expected_sha256, f"hg38 {name}")
        try:
            yield
        finally:
            for name, expected_sha256 in HG38_ASSETS.items():
                _validate_pinned_file(paths[name], expected_sha256, f"hg38 {name}")


@contextlib.contextmanager
def _validated_bwa_reader(
    reference: Mapping[str, object],
    runner: CommandRunner,
    toolchain: Toolchain,
) -> Iterator[None]:
    if runner.dry_run:
        yield
        return
    root = Path(str(reference.get("reference_root", "")))
    fasta = Path(str(reference.get("fasta", "")))
    fai = Path(str(reference.get("fai", "")))
    prefix = Path(str(reference.get("bwa_prefix", "")))
    lock = Path(str(reference.get("bwa_lock", "")))
    manifest = Path(str(reference.get("bwa_manifest", "")))
    expected_lock, expected_manifest = _reference_state_paths(root, "bwa")
    if (
        fasta != root / "genome.fa"
        or fai != root / "genome.fa.fai"
        or prefix != root / "bwa" / "genome"
        or lock != expected_lock
        or manifest != expected_manifest
    ):
        raise OncoTracerError(
            "prepared BWA reference paths escaped their physical root"
        )
    _require_physical_directory(root, "prepared reference root")
    _require_physical_file(lock, "BWA reference lock", nonempty=False)
    expected_generation = reference.get("bwa_generation")
    if not isinstance(expected_generation, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_generation
    ):
        raise OncoTracerError("prepared BWA reference has no exact generation")
    with _reference_lock(lock, exclusive=False, create=False):
        bwa = toolchain.executable("core", "bwa")
        contract = _index_build_contract(
            "bwa",
            str(reference.get("fasta_sha256", "")),
            str(reference.get("fai_sha256", "")),
            bwa,
        )
        generation_valid = sha256_file(manifest) == expected_generation
        contents_valid = _bwa_manifest_matches(manifest, prefix, contract)
        structure_valid = _bwa_index_matches_fai(fai, prefix)
        if not (generation_valid and contents_valid and structure_valid):
            raise OncoTracerError(
                "BWA reference changed or became invalid after preparation"
            )
        try:
            yield
        finally:
            generation_valid = sha256_file(manifest) == expected_generation
            contents_valid = _bwa_manifest_matches(manifest, prefix, contract)
            structure_valid = _bwa_index_matches_fai(fai, prefix)
            if not (generation_valid and contents_valid and structure_valid):
                raise OncoTracerError(
                    "BWA reference bytes changed or became invalid while in use"
                )


@contextlib.contextmanager
def _validated_minimap_reader(
    reference: Mapping[str, object],
    runner: CommandRunner,
    toolchain: Toolchain,
) -> Iterator[None]:
    if runner.dry_run:
        yield
        return
    root = Path(str(reference.get("reference_root", "")))
    fasta = Path(str(reference.get("fasta", "")))
    fai = Path(str(reference.get("fai", "")))
    index = Path(str(reference.get("minimap2_index", "")))
    lock = Path(str(reference.get("minimap2_lock", "")))
    manifest = Path(str(reference.get("minimap2_manifest", "")))
    expected_lock, expected_manifest = _reference_state_paths(root, "minimap2")
    if (
        fasta != root / "genome.fa"
        or fai != root / "genome.fa.fai"
        or index != root / "genome.fa.map-ont.mmi"
        or lock != expected_lock
        or manifest != expected_manifest
    ):
        raise OncoTracerError(
            "prepared minimap2 reference paths escaped their physical root"
        )
    _require_physical_directory(root, "prepared reference root")
    _require_physical_file(lock, "minimap2 reference lock", nonempty=False)
    expected_generation = reference.get("minimap2_generation")
    if not isinstance(expected_generation, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_generation
    ):
        raise OncoTracerError("prepared minimap2 reference has no exact generation")
    with _reference_lock(lock, exclusive=False, create=False):
        minimap2 = toolchain.executable("core", "minimap2")
        contract = _index_build_contract(
            "minimap2",
            str(reference.get("fasta_sha256", "")),
            str(reference.get("fai_sha256", "")),
            minimap2,
        )
        generation_valid = sha256_file(manifest) == expected_generation
        contents_valid = _minimap_manifest_matches(manifest, index, contract)
        structure_valid = _minimap_index_matches_fai(
            fai,
            index,
            minimap2,
            runner,
            stage="reference-minimap2-validate-reader",
        )
        if not (generation_valid and contents_valid and structure_valid):
            raise OncoTracerError(
                "minimap2 reference changed or became invalid after preparation"
            )
        try:
            yield
        finally:
            generation_valid = sha256_file(manifest) == expected_generation
            contents_valid = _minimap_manifest_matches(manifest, index, contract)
            structure_valid = _minimap_index_matches_fai(
                fai,
                index,
                minimap2,
                runner,
                stage="reference-minimap2-validate-reader-exit",
            )
            if not (generation_valid and contents_valid and structure_valid):
                raise OncoTracerError(
                    "minimap2 reference bytes changed or became invalid while in use"
                )


def _qdnaseq_bundle_names(binsize: int) -> tuple[str, str, str]:
    stem = f"QDNAseq.hg38.{binsize}kbp.SR50"
    return f"{stem}.source.rda", f"{stem}.rds", f"{stem}.rds.provenance.tsv"


def _qdnaseq_bundle_records(
    directory: Path, binsize: int
) -> dict[str, dict[str, object]]:
    _require_physical_directory(directory, "qDNAseq generation")
    source_name, rds_name, provenance_name = _qdnaseq_bundle_names(binsize)
    expected_names = {source_name, rds_name, provenance_name}
    observed_names = {entry.name for entry in directory.iterdir()}
    if observed_names != expected_names:
        raise OncoTracerError(
            "qDNAseq generation inventory mismatch: "
            f"expected {sorted(expected_names)}, observed {sorted(observed_names)}"
        )
    source = _require_physical_file(
        directory / source_name, "qDNAseq pinned source RDA"
    )
    annotation = _require_physical_file(
        directory / rds_name, "qDNAseq converted annotation"
    )
    provenance = _require_physical_file(
        directory / provenance_name, "qDNAseq annotation provenance"
    )
    for path in (source, annotation, provenance):
        if path.lstat().st_nlink != 1:
            raise OncoTracerError(
                f"qDNAseq generation file must not be hardlinked: {path}"
            )
    try:
        with provenance.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle, delimiter="	"))
    except (OSError, UnicodeError, csv.Error) as error:
        raise OncoTracerError(
            f"cannot parse qDNAseq provenance: {provenance}"
        ) from error
    if not rows or rows[0] != ["field", "value"]:
        raise OncoTracerError(f"qDNAseq provenance has an invalid header: {provenance}")
    if any(len(row) != 2 for row in rows[1:]):
        raise OncoTracerError(f"qDNAseq provenance has a malformed row: {provenance}")
    fields = dict(rows[1:])
    if len(fields) != len(rows) - 1:
        raise OncoTracerError(f"qDNAseq provenance has duplicate fields: {provenance}")
    object_name = f"hg38.{binsize}kbp.SR50"
    source_url = (
        "https://raw.githubusercontent.com/asntech/QDNAseq.hg38/"
        f"{QDNASEQ_HG38_COMMIT}/data/{object_name}.rda"
    )
    source_sha256 = sha256_file(source)
    rds_sha256 = sha256_file(annotation)
    expected_fields = {
        "source_url": source_url,
        "source_commit": QDNASEQ_HG38_COMMIT,
        "source_rda_sha256": source_sha256,
        "object": object_name,
        "rds_sha256": rds_sha256,
    }
    if fields != expected_fields:
        raise OncoTracerError(
            f"qDNAseq provenance does not match its files: {provenance}"
        )
    pinned = QDNASEQ_HG38_SOURCE_SHA256.get(binsize)
    if pinned is None or source_sha256 != pinned:
        raise OncoTracerError(
            f"qDNAseq source SHA-256 mismatch for {binsize} kb: "
            f"expected {pinned}, observed {source_sha256}"
        )
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in (source, annotation, provenance)
    }


def _qdnaseq_generation_from_pointer(
    cache: Path, binsize: int, identity: str
) -> Path | None:
    pointer = cache / f"current-{binsize}kb.json"
    observed = _lstat(pointer, "qDNAseq current-generation pointer")
    if observed is None:
        return None
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
    ):
        raise OncoTracerError(
            f"qDNAseq current-generation pointer is not a physical file: {pointer}"
        )
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    generation_name = value.get("generation") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema") != QDNASEQ_CACHE_POINTER_SCHEMA
        or value.get("identity") != identity
        or value.get("canonical_cache") != str(cache)
        or not isinstance(generation_name, str)
        or not re.fullmatch(r"generation-[0-9a-f]{64}", generation_name)
    ):
        return None
    generation = cache / "generations" / generation_name
    try:
        records = _qdnaseq_bundle_records(generation, binsize)
    except OncoTracerError:
        return None
    source_name, rds_name, provenance_name = _qdnaseq_bundle_names(binsize)
    expected_value = {
        "schema": QDNASEQ_CACHE_POINTER_SCHEMA,
        "identity": identity,
        "canonical_cache": str(cache),
        "generation": generation_name,
        "files": records,
        "source_name": source_name,
        "annotation_name": rds_name,
        "provenance_name": provenance_name,
    }
    if value != expected_value:
        return None
    return generation


def prepare_qdnaseq_annotation(
    root: Path,
    lpwgs_root: Path,
    binsize: int,
    runner: CommandRunner,
    toolchain: Toolchain,
) -> Path:
    helper = require_file(
        root / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh",
        "qDNAseq annotation helper",
    )
    kind = f"qdnaseq-hg38-{binsize}kb"
    identity = _reference_identity(kind)
    source_name, rds_name, provenance_name = _qdnaseq_bundle_names(binsize)
    cache_hint = (
        lpwgs_root / ".oncotracer" / "reference-cache" / f"{kind}-{identity[:16]}"
    )
    if runner.dry_run:
        return cache_hint / "generations" / "planned" / rds_name

    cache = _owned_reference_cache(lpwgs_root, kind)
    lock = cache / f"qdnaseq-{binsize}kb.lock"
    with _reference_lock(lock, exclusive=True, create=True):
        generations = _ensure_physical_directory(
            cache / "generations", "qDNAseq generations directory"
        )
        current = _qdnaseq_generation_from_pointer(cache, binsize, identity)
        if current is not None:
            return current / rds_name

        staging = Path(
            tempfile.mkdtemp(prefix=f".qdnaseq-{binsize}kb-build-", dir=cache)
        )
        command = [
            toolchain.executable("qdnaseq", "bash"),
            str(helper),
            "--rscript",
            toolchain.executable("qdnaseq", "Rscript"),
            "--binsize",
            str(binsize),
            "--cache-dir",
            str(staging),
        ]
        try:
            import subprocess

            started = utc_now()
            print(
                f"[qdnaseq-annotation] {' '.join(map(str, command))}",
                file=sys.stderr,
            )
            completed = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            runner._record(
                "qdnaseq-annotation",
                started,
                utc_now(),
                completed.returncode,
                root,
                command,
            )
            if completed.returncode != 0:
                sys.stderr.write(completed.stdout)
                sys.stderr.write(completed.stderr)
                raise OncoTracerError("qDNAseq annotation preparation failed")
            output_lines = [
                line.strip() for line in completed.stdout.splitlines() if line.strip()
            ]
            expected_output = staging / rds_name
            if (
                not output_lines
                or Path(output_lines[-1]).resolve(strict=True) != expected_output
            ):
                raise OncoTracerError(
                    "qDNAseq annotation helper returned an unexpected path"
                )
            records = _qdnaseq_bundle_records(staging, binsize)
            for name in records:
                (staging / name).chmod(0o444)
                _fsync_physical_file(staging / name, f"staged qDNAseq file {name}")
            _fsync_directory(staging, "qDNAseq staging directory")
            rds_record = records[rds_name]
            rds_sha256 = rds_record.get("sha256")
            if not isinstance(rds_sha256, str):
                raise OncoTracerError("qDNAseq annotation has no exact SHA-256")
            generation = generations / f"generation-{rds_sha256}"
            observed_generation = _lstat(generation, "qDNAseq generation")
            if observed_generation is None:
                os.rename(staging, generation)
                staging = None
                _fsync_directory(generations, "qDNAseq generations directory")
            else:
                if stat.S_ISLNK(observed_generation.st_mode) or not stat.S_ISDIR(
                    observed_generation.st_mode
                ):
                    raise OncoTracerError(
                        f"qDNAseq generation is not a physical directory: {generation}"
                    )
                if _qdnaseq_bundle_records(generation, binsize) != records:
                    raise OncoTracerError(
                        f"qDNAseq generation digest collision: {generation}"
                    )

            pointer = cache / f"current-{binsize}kb.json"
            pointer_stat = _lstat(pointer, "qDNAseq current-generation pointer")
            if pointer_stat is not None and (
                stat.S_ISLNK(pointer_stat.st_mode)
                or not stat.S_ISREG(pointer_stat.st_mode)
            ):
                raise OncoTracerError(
                    f"qDNAseq current-generation pointer is not a physical file: {pointer}"
                )
            _atomic_write_owned_json(
                pointer,
                {
                    "schema": QDNASEQ_CACHE_POINTER_SCHEMA,
                    "identity": identity,
                    "canonical_cache": str(cache),
                    "generation": generation.name,
                    "files": records,
                    "source_name": source_name,
                    "annotation_name": rds_name,
                    "provenance_name": provenance_name,
                },
                "qDNAseq current-generation pointer",
            )
            verified = _qdnaseq_generation_from_pointer(cache, binsize, identity)
            if verified != generation:
                raise OncoTracerError("published qDNAseq generation failed validation")
            return generation / rds_name
        finally:
            if staging is not None and staging.exists():
                _require_physical_directory(staging, "qDNAseq staging directory")
                if staging.parent != cache or not staging.name.startswith(
                    f".qdnaseq-{binsize}kb-build-"
                ):
                    raise OncoTracerError(
                        f"refusing to clean unsafe qDNAseq staging path: {staging}"
                    )
                shutil.rmtree(staging)


@contextlib.contextmanager
def _validated_qdnaseq_reader(
    annotation: Path,
    lpwgs_root: Path,
    binsize: int,
    runner: CommandRunner,
) -> Iterator[None]:
    """Lease and revalidate the immutable qDNAseq bundle around R execution."""
    if runner.dry_run:
        yield
        return
    project = lpwgs_root.expanduser().resolve(strict=True)
    kind = f"qdnaseq-hg38-{binsize}kb"
    identity = _reference_identity(kind)
    cache = project / ".oncotracer" / "reference-cache" / f"{kind}-{identity[:16]}"
    generation = annotation.parent
    expected_annotation = generation / _qdnaseq_bundle_names(binsize)[1]
    if (
        annotation != expected_annotation
        or generation.parent != cache / "generations"
        or not re.fullmatch(r"generation-[0-9a-f]{64}", generation.name)
    ):
        raise OncoTracerError(
            "prepared qDNAseq annotation escaped its marker-owned cache"
        )
    _require_physical_directory(cache, "prepared qDNAseq cache")
    _require_physical_directory(
        cache / "generations", "prepared qDNAseq generations directory"
    )
    lock = cache / f"qdnaseq-{binsize}kb.lock"
    _require_physical_file(lock, "qDNAseq reference lock", nonempty=False)

    def require_current() -> None:
        current = _qdnaseq_generation_from_pointer(cache, binsize, identity)
        if current != generation:
            raise OncoTracerError(
                "qDNAseq annotation changed or became invalid while in use"
            )

    with _reference_lock(lock, exclusive=False, create=False):
        require_current()
        try:
            yield
        finally:
            require_current()


def _write_bam_sheet(
    samples: Iterable[IlluminaSample | OntSample],
    bams: Mapping[str, Path],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "bam", "status"])
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "sample": sample.sample,
                    "bam": str(bams[sample.sample]),
                    "status": sample.status,
                }
            )


def align_illumina(
    samples: list[IlluminaSample],
    reference: Mapping[str, object],
    outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    force: bool,
) -> dict[str, Path]:
    with _validated_bwa_reader(reference, runner, toolchain):
        return _align_illumina_locked(
            samples,
            reference,
            outdir,
            runner,
            ledger,
            toolchain,
            threads=threads,
            force=force,
        )


def _align_illumina_locked(
    samples: list[IlluminaSample],
    reference: Mapping[str, object],
    outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    force: bool,
) -> dict[str, Path]:
    alignment = outdir / "alignment"
    markduplicates = outdir / "markduplicates"
    alignment.mkdir(parents=True, exist_ok=True)
    markduplicates.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for sample in samples:
        bam = alignment / f"{sample.sample}.bam"
        bai = Path(str(bam) + ".bai")
        markdup = markduplicates / f"{sample.sample}_markdup.bam"
        markdup_bai = Path(str(markdup) + ".bai")
        metrics = markduplicates / f"{sample.sample}_markdup.metrics.txt"
        reads = [sample.fastq_1] + ([sample.fastq_2] if sample.fastq_2 else [])
        rg = (
            f"@RG\\tID:{sample.sample}\\tPU:1\\tSM:{sample.sample}"
            f"\\tLB:{sample.sample}\\tPL:Illumina"
        )
        bwa = [
            toolchain.executable("core", "bwa"),
            "mem",
            "-t",
            str(threads),
            "-R",
            rg,
            str(reference["bwa_prefix"]),
            *[str(path) for path in reads],
        ]
        sort = [
            toolchain.executable("core", "samtools"),
            "sort",
            "-@",
            str(max(1, threads // 2)),
            "-o",
            str(bam),
            "-",
        ]
        signature = ledger.signature(
            f"illumina-align-{sample.sample}", bwa + ["|"] + sort, reads
        )
        if force or not ledger.reusable(
            f"illumina-align-{sample.sample}", signature, [bam, bai]
        ):
            runner.pipeline(f"illumina-align-{sample.sample}", bwa, sort)
            runner.run(
                f"illumina-index-{sample.sample}",
                [
                    toolchain.executable("core", "samtools"),
                    "index",
                    "-@",
                    str(max(1, threads // 2)),
                    bam,
                ],
            )
            ledger.complete(f"illumina-align-{sample.sample}", signature, [bam, bai])

        picard = toolchain.executable("core", "picard")
        if picard:
            mark_command = [
                picard,
                "MarkDuplicates",
                f"I={bam}",
                f"O={markdup}",
                f"M={metrics}",
                "CREATE_INDEX=true",
                "VALIDATION_STRINGENCY=SILENT",
            ]
            mark_signature = ledger.signature(
                f"illumina-markdup-{sample.sample}", mark_command, [bam, bai]
            )
            if force or not ledger.reusable(
                f"illumina-markdup-{sample.sample}",
                mark_signature,
                [markdup, markdup_bai],
            ):
                runner.run(f"illumina-markdup-{sample.sample}", mark_command)
                if not markdup_bai.is_file():
                    runner.run(
                        f"illumina-markdup-index-{sample.sample}",
                        [toolchain.executable("core", "samtools"), "index", markdup],
                    )
                ledger.complete(
                    f"illumina-markdup-{sample.sample}",
                    mark_signature,
                    [markdup, markdup_bai],
                )
        else:
            raise OncoTracerError(
                "Picard MarkDuplicates is required for native Illumina analysis; "
                "run 'oncotracer install --conda' or use the maintained container backend"
            )
        results[sample.sample] = markdup
    return results


def run_qdnaseq(
    root: Path,
    lpwgs_root: Path,
    samples: list[IlluminaSample] | list[OntSample],
    markdup_bams: Mapping[str, Path],
    samurai_outdir: Path,
    binsize: int,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    force: bool,
    paired_ends: bool | None = None,
) -> tuple[Path, Path]:
    bam_sheet = samurai_outdir / "input" / "native.bam.samplesheet.csv"
    _write_bam_sheet(samples, markdup_bams, bam_sheet)
    annotation = prepare_qdnaseq_annotation(
        root, lpwgs_root, binsize, runner, toolchain
    )
    if paired_ends is None:
        if not all(isinstance(sample, IlluminaSample) for sample in samples):
            raise OncoTracerError(
                "paired_ends must be specified when qDNAseq consumes non-Illumina BAMs"
            )
        paired = all(sample.fastq_2 is not None for sample in samples)
    else:
        paired = paired_ends

    qdna_out = samurai_outdir / "qdnaseq"
    script = require_file(
        root / "bin" / "scripts" / "native_qdnaseq.R", "native qDNAseq R script"
    )
    qc_helper = require_file(
        root / "bin" / "scripts" / "qdnaseq_post_normalization_qc.R",
        "native qDNAseq post-normalization QC helper",
    )
    command = toolchain.rscript(
        "qdnaseq",
        [
            script,
            "--samplesheet",
            bam_sheet,
            "--outdir",
            qdna_out,
            "--binsize",
            str(binsize),
            "--min-mapq",
            "37",
            "--paired-ends",
            str(paired).lower(),
            "--bin-data",
            annotation,
        ],
    )
    output = qdna_out / "all_segments.seg"
    status = qdna_out / "qdnaseq_sample_status.json"
    roles = qdna_out / "qdnaseq_sample_roles.tsv"
    expected = [output, status, roles]
    with _validated_qdnaseq_reader(annotation, lpwgs_root, binsize, runner):
        signature = ledger.signature(
            "qdnaseq",
            command,
            [script, qc_helper, bam_sheet, annotation, *markdup_bams.values()],
        )
        if force or not ledger.reusable("qdnaseq", signature, expected):
            runner.run("qdnaseq", command, cwd=root)
            for path, label in (
                (output, "native qDNAseq segments"),
                (status, "native qDNAseq sample status"),
                (roles, "native qDNAseq sample roles"),
            ):
                require_file(path, label)
            ledger.complete("qdnaseq", signature, expected)
    return qdna_out, samurai_outdir / "alignment"


def _fastq_files(directory: Path, min_age_minutes: int) -> list[Path]:
    import time

    now = time.time()
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        lowered = path.name.lower()
        if not lowered.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")):
            continue
        if path.stat().st_size == 0:
            continue
        if now - path.stat().st_mtime < min_age_minutes * 60:
            continue
        files.append(path)
    if not files:
        raise OncoTracerError(f"no stable FASTQ files found under {directory}")
    return files


def merge_fastqs(files: list[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    with gzip.open(temporary, "wb", compresslevel=6) as output:
        for path in files:
            opener = gzip.open if path.name.lower().endswith(".gz") else open
            with opener(path, "rb") as source:  # type: ignore[arg-type]
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
    os.replace(temporary, destination)


def align_ont(
    samples: list[OntSample],
    reference: Mapping[str, object],
    samurai_outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    min_age_minutes: int,
    force: bool,
) -> dict[str, Path]:
    with _validated_minimap_reader(reference, runner, toolchain):
        return _align_ont_locked(
            samples,
            reference,
            samurai_outdir,
            runner,
            ledger,
            toolchain,
            threads=threads,
            min_age_minutes=min_age_minutes,
            force=force,
        )


def _align_ont_locked(
    samples: list[OntSample],
    reference: Mapping[str, object],
    samurai_outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    min_age_minutes: int,
    force: bool,
) -> dict[str, Path]:
    merged_dir = samurai_outdir / "merged_fastq"
    bam_dir = samurai_outdir / "bam"
    merged_dir.mkdir(parents=True, exist_ok=True)
    bam_dir.mkdir(parents=True, exist_ok=True)
    bams: dict[str, Path] = {}
    for sample in samples:
        files = _fastq_files(sample.fastq_dir, min_age_minutes)
        merged = merged_dir / f"{sample.sample}.fastq.gz"
        merge_signature = ledger.signature(
            f"ont-merge-{sample.sample}", ["native-gzip-merge", str(merged)], files
        )
        if force or not ledger.reusable(
            f"ont-merge-{sample.sample}", merge_signature, [merged]
        ):
            if not runner.dry_run:
                merge_fastqs(files, merged)
            ledger.complete(f"ont-merge-{sample.sample}", merge_signature, [merged])
        bam = bam_dir / f"{sample.sample}.sorted.bam"
        bai = Path(str(bam) + ".bai")
        left = [
            toolchain.executable("core", "minimap2"),
            "-ax",
            "map-ont",
            "-t",
            str(threads),
            "-R",
            f"@RG\\tID:{sample.sample}\\tSM:{sample.sample}\\tPL:ONT",
            reference["minimap2_index"],
            merged,
        ]
        right = [
            toolchain.executable("core", "samtools"),
            "sort",
            "-@",
            str(max(1, threads // 2)),
            "-o",
            bam,
            "-",
        ]
        signature = ledger.signature(
            f"ont-align-{sample.sample}",
            [*map(str, left), "|", *map(str, right)],
            [merged],
        )
        if force or not ledger.reusable(
            f"ont-align-{sample.sample}", signature, [bam, bai]
        ):
            runner.pipeline(f"ont-align-{sample.sample}", left, right)
            runner.run(
                f"ont-index-{sample.sample}",
                [
                    toolchain.executable("core", "samtools"),
                    "index",
                    "-@",
                    str(max(1, threads // 2)),
                    bam,
                ],
            )
            ledger.complete(f"ont-align-{sample.sample}", signature, [bam, bai])
        bams[sample.sample] = bam
    return bams


def prepare_ichor_assets(lpwgs_root: Path, binsize: int) -> dict[str, Path]:
    if binsize != 500:
        raise OncoTracerError(
            "native automatic ichorCNA assets are pinned to hg38/500 kb; use ont_binsize_kb: 500"
        )
    lpwgs_root.mkdir(parents=True, exist_ok=True)
    project = lpwgs_root.expanduser().resolve(strict=True)
    requested = project / "references" / "samurai_ichorcna_hg38_500kb"
    if requested.exists() or requested.is_symlink():
        try:
            directory = requested.resolve(strict=True)
        except OSError as error:
            raise OncoTracerError(
                f"cannot resolve external ichorCNA reference: {requested}"
            ) from error
        _require_physical_directory(directory, "external ichorCNA reference root")
        owned = False
    else:
        directory = _owned_reference_cache(project, "ichorcna-hg38-500kb")
        owned = True

    resolved: dict[str, Path] = {}
    with _physical_directory_lock(
        directory,
        "ichorCNA reference root",
        exclusive=owned,
    ):
        for key, (filename, expected_sha256) in ICHOR_ASSETS.items():
            path = directory / filename
            if owned:
                resolved[key] = _ensure_owned_pinned_file(
                    directory,
                    path,
                    f"{ICHOR_ASSET_BASE}/{filename}",
                    expected_sha256,
                    f"ichorCNA {key} asset",
                )
            else:
                resolved[key] = _validate_pinned_file(
                    path, expected_sha256, f"external ichorCNA {key} asset"
                )
    return resolved


@contextlib.contextmanager
def _validated_ichor_asset_reader(
    assets: Mapping[str, Path], runner: CommandRunner
) -> Iterator[None]:
    if runner.dry_run:
        yield
        return
    if set(assets) != set(ICHOR_ASSETS):
        raise OncoTracerError("prepared ichorCNA asset set is incomplete")
    directories = {Path(path).parent for path in assets.values()}
    if len(directories) != 1:
        raise OncoTracerError("prepared ichorCNA assets escaped their physical root")
    directory = directories.pop()
    _require_physical_directory(directory, "prepared ichorCNA reference root")

    def require_current() -> None:
        for key, (filename, expected_sha256) in ICHOR_ASSETS.items():
            path = Path(assets[key])
            if path != directory / filename:
                raise OncoTracerError(
                    f"prepared ichorCNA {key} asset escaped its physical root"
                )
            _validate_pinned_file(path, expected_sha256, f"ichorCNA {key} asset")

    with _physical_directory_lock(
        directory, "ichorCNA reference root", exclusive=False
    ):
        require_current()
        try:
            yield
        finally:
            require_current()


def _parse_ichor_params(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _combine_headered(paths: list[Path], destination: Path) -> None:
    if not paths:
        raise OncoTracerError(f"no source files for {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output:
        expected_header: str | None = None
        for index, path in enumerate(paths):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if not lines:
                raise OncoTracerError(f"empty ichorCNA segment file: {path}")
            if expected_header is None:
                expected_header = lines[0]
                output.write(expected_header + "\n")
            elif lines[0] != expected_header:
                raise OncoTracerError(f"ichorCNA segment headers differ: {path}")
            for line in lines[1:]:
                if line.strip():
                    output.write(line + "\n")


def _correct_ichor_segments(
    seg_path: Path, summary_path: Path, destination: Path
) -> None:
    with summary_path.open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle, delimiter="\t"))
    ploidy: dict[str, float] = {}
    for row in summary_rows:
        sample = row.get("samplename") or row.get("sample") or row.get("index")
        value = row.get("Ploidy") or row.get("ploidy")
        if sample and value:
            ploidy[sample] = float(value)
    with seg_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source, delimiter="\t")
        fieldnames = reader.fieldnames or []
        required = {"ID", "chrom", "start", "end", "num.mark", "logR_Copy_Number"}
        missing = required.difference(fieldnames)
        if missing:
            raise OncoTracerError(
                f"ichorCNA SEG is missing columns: {', '.join(sorted(missing))}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8") as sink:
            writer = csv.writer(sink, delimiter="\t")
            writer.writerow(
                ["sample", "chromosome", "start", "end", "num.mark", "adj.seg"]
            )
            for row in reader:
                sample = row["ID"]
                if sample not in ploidy:
                    raise OncoTracerError(
                        f"ploidy is missing for ichorCNA sample: {sample}"
                    )
                ratio = max(float(row["logR_Copy_Number"]) / ploidy[sample], 2e-8)
                adjusted = max(math.log2(ratio), -0.5)
                writer.writerow(
                    [
                        sample,
                        row["chrom"],
                        row["start"],
                        row["end"],
                        row["num.mark"],
                        adjusted,
                    ]
                )


def _write_ichorcna_sample_status(
    path: Path,
    records: list[dict[str, object]],
) -> dict[str, object]:
    completed = [
        str(record["sample"]) for record in records if record["status"] == "complete"
    ]
    failed = [
        str(record["sample"]) for record in records if record["status"] == "failed"
    ]
    pending = [
        str(record["sample"]) for record in records if record["status"] == "pending"
    ]
    if pending:
        overall_status = "in_progress"
    elif completed and failed:
        overall_status = "partial_failure"
    elif failed:
        overall_status = "failed"
    else:
        overall_status = "complete"
    payload: dict[str, object] = {
        "schema": "oncotracer-native-ichorcna-sample-status-v1",
        "overall_status": overall_status,
        "sample_count": len(records),
        "completed_samples": completed,
        "failed_samples": failed,
        "pending_samples": pending,
        "samples": records,
        "updated_at": utc_now(),
    }
    atomic_write_json(path, payload)
    return payload


def _sanitize_sample_error(error: BaseException) -> str:
    """Keep sample status useful without embedding local filesystem paths."""
    message = " ".join(str(error).split())
    message = re.sub(r"(?<![A-Za-z0-9._-])/(?:[^\s,;]+)", "<path>", message)
    return message[:500]


def _remove_published_ichorcna_sample(ichor_out: Path, sample: str) -> None:
    # Only exact, top-level files published by this stage are removed. Nested
    # partial caller outputs remain available for diagnosis and resume.
    for path in ichor_out.glob(f"{sample}.*"):
        if path.parent == ichor_out and (path.is_file() or path.is_symlink()):
            path.unlink()


def run_ichorcna(
    root: Path,
    lpwgs_root: Path,
    samples: list[OntSample],
    bams: Mapping[str, Path],
    samurai_outdir: Path,
    binsize: int,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    force: bool,
) -> Path:
    assets = prepare_ichor_assets(lpwgs_root, binsize)
    with _validated_ichor_asset_reader(assets, runner):
        return _run_ichorcna_with_assets(
            root,
            samples,
            bams,
            samurai_outdir,
            binsize,
            runner,
            ledger,
            toolchain,
            assets,
            threads=threads,
            force=force,
        )


def _run_ichorcna_with_assets(
    root: Path,
    samples: list[OntSample],
    bams: Mapping[str, Path],
    samurai_outdir: Path,
    binsize: int,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    assets: Mapping[str, Path],
    *,
    threads: int,
    force: bool,
) -> Path:
    ichor_out = samurai_outdir / "results" / "ichorcna"
    wig_dir = ichor_out / "wigfiles_samples"
    ichor_out.mkdir(parents=True, exist_ok=True)
    wig_dir.mkdir(parents=True, exist_ok=True)
    script = require_file(
        root / "bin" / "scripts" / "native_ichorcna.R", "native ichorCNA R script"
    )
    chromosomes = ",".join(f"chr{index}" for index in range(1, 23))
    status_path = ichor_out / "ichorcna_sample_status.json"
    records: list[dict[str, object]] = [
        {
            "sample": sample.sample,
            "status": "pending",
            "stage": "pending",
            "error": None,
            "parameters": None,
            "segments": None,
        }
        for sample in samples
    ]
    _write_ichorcna_sample_status(status_path, records)

    for aggregate in (
        ichor_out / "all_segments_ichorcna_gistic.seg",
        ichor_out / "ichorcna_summary_mqc.txt",
        ichor_out / "segments_logR_corrected_gistic.seg",
    ):
        aggregate.unlink(missing_ok=True)

    completed: list[tuple[OntSample, Path, Path]] = []
    for index, sample in enumerate(samples):
        failed_stage = "readcounter"
        _remove_published_ichorcna_sample(ichor_out, sample.sample)
        try:
            bam = bams[sample.sample]
            wig = wig_dir / f"{sample.sample}.wig"
            readcounter = toolchain.wrap(
                "ichorcna",
                [
                    "readCounter",
                    "--chromosome",
                    chromosomes,
                    "--quality",
                    "20",
                    "--window",
                    str(binsize * 1000),
                    bam,
                ],
            )
            signature = ledger.signature(
                f"ichor-readcounter-{sample.sample}", readcounter, [bam]
            )
            if force or not ledger.reusable(
                f"ichor-readcounter-{sample.sample}", signature, [wig]
            ):
                with wig.open("w", encoding="utf-8") as output:
                    runner.run(
                        f"ichor-readcounter-{sample.sample}", readcounter, stdout=output
                    )
                require_file(wig, f"ichorCNA readCounter WIG for {sample.sample}")
                ledger.complete(f"ichor-readcounter-{sample.sample}", signature, [wig])

            failed_stage = "ichorcna"
            sample_out = ichor_out / sample.sample
            sample_out.mkdir(parents=True, exist_ok=True)
            command = toolchain.rscript(
                "ichorcna",
                [
                    script,
                    "--wig",
                    wig,
                    "--sample",
                    sample.sample,
                    "--outdir",
                    sample_out,
                    "--gc-wig",
                    assets["gc"],
                    "--map-wig",
                    assets["map"],
                    "--centromere",
                    assets["centromere"],
                    "--reptime",
                    assets["reptime"],
                    "--normal-panel",
                    assets["pon"],
                    "--cores",
                    str(max(1, min(threads, 8))),
                ],
            )
            expected_params = sample_out / f"{sample.sample}.params.txt"
            expected_seg = sample_out / f"{sample.sample}.seg.txt"
            signature = ledger.signature(
                f"ichorcna-{sample.sample}", command, [wig, *assets.values()]
            )
            if force or not ledger.reusable(
                f"ichorcna-{sample.sample}", signature, [expected_params, expected_seg]
            ):
                runner.run(f"ichorcna-{sample.sample}", command, cwd=sample_out)
                if not expected_params.is_file():
                    matches = sorted(sample_out.rglob(f"{sample.sample}.params.txt"))
                    if matches:
                        expected_params = matches[0]
                if not expected_seg.is_file():
                    matches = sorted(sample_out.rglob(f"{sample.sample}.seg.txt"))
                    if matches:
                        expected_seg = matches[0]
                expected_params = require_file(
                    expected_params, f"ichorCNA params for {sample.sample}"
                )
                expected_seg = require_file(
                    expected_seg, f"ichorCNA SEG for {sample.sample}"
                )
                ledger.complete(
                    f"ichorcna-{sample.sample}",
                    signature,
                    [expected_params, expected_seg],
                )
            else:
                expected_params = require_file(
                    expected_params, f"ichorCNA params for {sample.sample}"
                )
                expected_seg = require_file(
                    expected_seg, f"ichorCNA SEG for {sample.sample}"
                )

            failed_stage = "publish"
            for path in sample_out.rglob("*"):
                if path.is_file() and path.name.startswith(f"{sample.sample}."):
                    target = ichor_out / path.name
                    if path.resolve() != target.resolve():
                        shutil.copy2(path, target)

            completed.append((sample, expected_params, expected_seg))
            records[index] = {
                "sample": sample.sample,
                "status": "complete",
                "stage": "complete",
                "error": None,
                "parameters": str(expected_params.relative_to(ichor_out)),
                "segments": str(expected_seg.relative_to(ichor_out)),
            }
        except (OncoTracerError, OSError) as error:
            _remove_published_ichorcna_sample(ichor_out, sample.sample)
            safe_error = _sanitize_sample_error(error)
            records[index] = {
                "sample": sample.sample,
                "status": "failed",
                "stage": failed_stage,
                "error": safe_error,
                "parameters": None,
                "segments": None,
            }
            print(
                f"WARNING: ichorCNA sample {sample.sample!r} failed during "
                f"{failed_stage}; continuing remaining samples: {safe_error}",
                file=sys.stderr,
                flush=True,
            )
        _write_ichorcna_sample_status(status_path, records)

    status = _write_ichorcna_sample_status(status_path, records)
    if not completed:
        failed = ", ".join(str(sample) for sample in status["failed_samples"])
        raise OncoTracerError(
            f"ichorCNA failed for every sample ({failed}); inspect {status_path}"
        )
    if status["overall_status"] == "partial_failure":
        print(
            "WARNING: native ichorCNA completed with sample failures; only complete "
            f"samples will be aggregated and reported. Inspect {status_path}",
            file=sys.stderr,
            flush=True,
        )

    combined_seg = ichor_out / "all_segments_ichorcna_gistic.seg"
    _combine_headered([segments for _, _, segments in completed], combined_seg)
    summary = ichor_out / "ichorcna_summary_mqc.txt"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            ["samplename", "Tumor Fraction", "Ploidy", "GC-Map correction MAD"]
        )
        for sample, params, _ in completed:
            values = _parse_ichor_params(params)
            writer.writerow(
                [
                    sample.sample,
                    values.get("Tumor Fraction", "NA"),
                    values.get("Ploidy", "NA"),
                    values.get("GC-Map correction MAD", "NA"),
                ]
            )
    corrected = ichor_out / "segments_logR_corrected_gistic.seg"
    _correct_ichor_segments(combined_seg, summary, corrected)
    return ichor_out


def run_refinement_and_outputs(
    root: Path,
    config: Mapping[str, object],
    mode: str,
    samurai_outdir: Path,
    qdna_or_ichor_dir: Path,
    bam_dir: Path,
    outdir: Path,
    lpwgs_root: Path,
    runner: CommandRunner,
    toolchain: Toolchain,
    *,
    force: bool,
    caller: str | None = None,
) -> None:
    selected_caller = caller or ("qdnaseq" if mode == "illumina" else "ichorcna")
    if mode == "illumina" and selected_caller != "qdnaseq":
        raise OncoTracerError("native Illumina refinement requires qdnaseq")
    if mode == "ont" and selected_caller not in {"ichorcna", "qdnaseq"}:
        raise OncoTracerError(
            "native ONT refinement caller must be ichorcna or qdnaseq"
        )
    refine_script = require_file(
        root / "bin" / "scripts" / "bam_cnv_boundary_refine.sh",
        "BAM boundary-refinement script",
    )
    codify = require_file(
        root
        / "bin"
        / "cna_codification"
        / "scripts"
        / "cna_to_cytogenomic_notation.py",
        "CNA codification script",
    )
    cytoband = require_file(
        root / "bin" / "cna_codification" / "resources" / "hg38.cytoBand.txt.gz",
        "hg38 cytoband resource",
    )
    refine_out = outdir / "02_bam_refinement"
    refine_out.mkdir(parents=True, exist_ok=True)
    if mode == "illumina":
        binsize = _as_int(config.get("illumina_binsize_kb"), 100)
        dataset = f"illumina_qdnaseq_{binsize}kb"
        prior = qdna_or_ichor_dir / "all_segments.seg"
        mode_args = [
            "--mode",
            "illumina",
            "--illumina-qdnaseq-dir",
            qdna_or_ichor_dir,
            "--illumina-bam-dir",
            bam_dir,
            "--illumina-prior-seg",
            prior,
            "--illumina-binsize-kb",
            str(binsize),
            "--fine-bin-kb-illumina",
            str(_as_int(config.get("fine_bin_kb_illumina"), 10)),
            "--coverage-mode-illumina",
            "starts",
            "--min-local-log2-diff",
            str(config.get("min_local_log2_diff_illumina", 0.10)),
        ]
    else:
        binsize = _as_int(config.get("ont_binsize_kb"), 500)
        dataset = f"ONT_{selected_caller}_{binsize}kb"
        prior = qdna_or_ichor_dir / (
            "segments_logR_corrected_gistic.seg"
            if selected_caller == "ichorcna"
            else "all_segments.seg"
        )
        mode_args = [
            "--mode",
            "ont",
            "--ont-cna-dir",
            qdna_or_ichor_dir,
            "--ont-caller",
            selected_caller,
            "--ont-bam-dir",
            bam_dir,
            "--ont-prior-seg",
            prior,
            "--ont-binsize-kb",
            str(binsize),
            "--fine-bin-kb-ont",
            str(_as_int(config.get("fine_bin_kb_ont"), 25)),
            "--coverage-mode-ont",
            "bases",
            "--min-local-log2-diff",
            str(config.get("min_local_log2_diff_ont", 0.12)),
        ]
    command: list[str | Path] = [
        require_command("bash"),
        refine_script,
        *mode_args,
        "--lpwgs-root",
        lpwgs_root,
        "--outdir",
        refine_out,
        "--codification-script",
        codify,
        "--cytoband",
        cytoband,
        "--search-radius-bins",
        str(_as_int(config.get("search_radius_bins"), 2)),
        "--min-mapq",
        str(_as_int(config.get("min_mapq"), 20)),
        "--min-adjacent-seg-delta",
        str(config.get("min_adjacent_seg_delta", 0.10)),
        "--min-bic-gain",
        str(config.get("min_bic_gain", 6)),
        "--permutations",
        str(_as_int(config.get("permutations"), 300)),
        "--permutation-p",
        str(config.get("permutation_p", 0.05)),
        "--accept-rule",
        str(config.get("accept_rule", "p_and_bic")),
        "--max-ci-fraction-of-coarse",
        str(config.get("max_ci_fraction_of_coarse", 1.0)),
        "--zipcnv-mode",
        str(config.get("zipcnv_mode", "adapted")),
        "--zipcnv-window-bins",
        str(_as_int(config.get("zipcnv_window_bins"), 5)),
        "--zipcnv-k",
        str(config.get("zipcnv_k", 0.05)),
        "--zipcnv-min-segment-bins",
        str(_as_int(config.get("zipcnv_min_segment_bins"), 3)),
        "--zipcnv-min-abs-log2",
        str(config.get("zipcnv_min_abs_log2", 0.25)),
        "--zipcnv-compare-min-overlap",
        str(config.get("zipcnv_compare_min_overlap", 0.50)),
        "--native-current-environment",
        "--python-executable",
        toolchain.executable("core", "python"),
        "--samtools-executable",
        toolchain.executable("core", "samtools"),
        "--skip-install",
    ]
    if force:
        command.append("--force")
    runner.run("bam-refinement", command, cwd=root)
    final_root = refine_out / dataset / "04_final_results"
    require_file(final_root / "final_segments.tsv", "refined final segments")
    bins_input = require_directory(
        final_root / "cna_cytogenomic_input" / "qdnaseq_bins",
        "CNA codification bins",
    )

    codify_out = outdir / "03_cna_codification"
    codify_out.mkdir(parents=True, exist_ok=True)
    runner.run(
        "cna-codification",
        toolchain.wrap(
            "core",
            [
                "python",
                codify,
                "--qdnaseq",
                "--input-dir",
                bins_input,
                "--cytoband",
                cytoband,
                "--outdir",
                codify_out,
                "--prefix",
                "cna",
            ],
        ),
        cwd=root,
    )
    events = require_file(codify_out / "cna_events.tsv", "CNA events")
    require_file(codify_out / "cna_cytogenomic_notation.tsv", "cytogenomic notation")

    plots_out = outdir / "04_cna_custom_plots"
    plots_out.mkdir(parents=True, exist_ok=True)
    plotter = require_file(
        root / "bin" / "cna_codification" / "scripts" / "plot_cna_events.py",
        "CNA plotting script",
    )
    bins_table = require_file(
        refine_out / dataset / "01_tables" / "refined_bins.tsv.gz", "refined bins"
    )
    runner.run(
        "cna-plots",
        toolchain.wrap(
            "core",
            [
                "python",
                plotter,
                "--events",
                events,
                "--cytoband",
                cytoband,
                "--outdir",
                plots_out,
                "--bins",
                bins_table,
                "--profile-sample",
                "all",
            ],
        ),
        cwd=root,
    )
    require_file(plots_out / "cna_per_sample_pages.pdf", "per-sample CNA PDF")
    require_file(
        plots_out / "cna_log2_ratio_profiles_all_samples.pdf", "cohort CNA profile PDF"
    )

    summary_dir = outdir / "06_workflow_summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "mode": mode,
        "caller": selected_caller,
        "dataset": dataset,
        "workflow_status": "complete",
        "outdir": str(outdir),
        "bam_refinement": str(refine_out / dataset),
        "cna_codification": str(codify_out),
        "cna_events": str(events),
        "cna_custom_plots": str(plots_out),
        "cna_notation": str(codify_out / "cna_cytogenomic_notation.tsv"),
        "completed_at": utc_now(),
    }
    sample_status_path: Path | None = None
    sample_status_key: str | None = None
    sample_status_label: str | None = None
    if mode == "ont" and selected_caller == "ichorcna":
        sample_status_path = qdna_or_ichor_dir / "ichorcna_sample_status.json"
        sample_status_key = "ichorcna_sample_status"
        sample_status_label = "ichorCNA"
    elif selected_caller == "qdnaseq":
        candidate = qdna_or_ichor_dir / "qdnaseq_sample_status.json"
        roles_path = require_file(
            qdna_or_ichor_dir / "qdnaseq_sample_roles.tsv",
            "qDNAseq sample-role manifest",
        )
        with roles_path.open(newline="", encoding="utf-8") as handle:
            roles = list(csv.DictReader(handle, delimiter="\t"))
        if not roles or set(roles[0]) != {"sample", "status"}:
            raise OncoTracerError(f"invalid qDNAseq sample-role manifest: {roles_path}")
        if len({row["sample"] for row in roles}) != len(roles):
            raise OncoTracerError(
                f"duplicate sample in qDNAseq role manifest: {roles_path}"
            )
        if any(row["status"] not in {"tumor", "normal"} for row in roles):
            raise OncoTracerError(
                f"invalid role in qDNAseq sample-role manifest: {roles_path}"
            )
        summary.update(
            {
                "qdnaseq_sample_roles": str(roles_path),
                "tumor_samples": [
                    row["sample"] for row in roles if row["status"] == "tumor"
                ],
                "normal_samples": [
                    row["sample"] for row in roles if row["status"] == "normal"
                ],
                "sample_derived_panel_used": False,
            }
        )
        if candidate.is_file():
            sample_status_path = candidate
            sample_status_key = "qdnaseq_sample_status"
            sample_status_label = "qDNAseq"

    if sample_status_path is not None:
        sample_status_path = require_file(
            sample_status_path,
            f"{sample_status_label} sample status",
        )
        try:
            sample_status = json.loads(sample_status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise OncoTracerError(
                f"invalid {sample_status_label} sample status: {sample_status_path}"
            ) from error
        if not isinstance(sample_status, dict):
            raise OncoTracerError(
                f"invalid {sample_status_label} sample status object: {sample_status_path}"
            )
        summary.update(
            {
                "workflow_status": sample_status.get("overall_status"),
                str(sample_status_key): str(sample_status_path),
                "completed_samples": sample_status.get("completed_samples", []),
                "failed_samples": sample_status.get("failed_samples", []),
            }
        )
    atomic_write_workflow_summary(summary_dir, summary)


def write_run_manifest(outdir: Path, config_path: Path, trace_path: Path) -> None:
    files: list[dict[str, object]] = []
    for relative in [
        ".oncotracer-native/output-owner.json",
        "06_workflow_summary/workflow_summary.txt",
        "06_workflow_summary/workflow_summary.json",
        "03_cna_codification/cna_events.tsv",
        "03_cna_codification/cna_cytogenomic_notation.tsv",
        "04_cna_custom_plots/cna_per_sample_pages.pdf",
        "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
        "05_cna_classifier/native_classifier_summary.json",
        "05_cna_classifier/02_classification/cna_patient_classification.tsv",
        "05_cna_classifier/03_report/cna_classifier_report.html",
        "01_samurai_ont/results/ichorcna/ichorcna_sample_status.json",
        "01_samurai_ont/qdnaseq/qdnaseq_sample_status.json",
        "01_samurai_ont/qdnaseq/qdnaseq_sample_roles.tsv",
        "01_samurai_illumina/qdnaseq/qdnaseq_sample_status.json",
        "01_samurai_illumina/qdnaseq/qdnaseq_sample_roles.tsv",
        "07_methylation/methylation_status.json",
        "07_methylation/methylation_provenance.json",
    ]:
        path = outdir / relative
        if path.is_file():
            files.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    summary_path = outdir / "06_workflow_summary" / "workflow_summary.json"
    summary: dict[str, object] = {}
    if summary_path.is_file():
        try:
            parsed = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise OncoTracerError(
                f"invalid workflow summary: {summary_path}"
            ) from error
        if isinstance(parsed, dict):
            summary = parsed
    manifest = {
        "schema": "oncotracer-native-run-manifest-v1",
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "trace": str(trace_path),
        "workflow_status": summary.get("workflow_status", "complete"),
        "completed_samples": summary.get("completed_samples", []),
        "failed_samples": summary.get("failed_samples", []),
        "cna_status": summary.get("cna_status"),
        "methylation_status": summary.get("methylation_status"),
        "methylation_completed_samples": summary.get(
            "methylation_completed_samples", []
        ),
        "methylation_no_cpg_samples": summary.get("methylation_no_cpg_samples", []),
        "created_at": utc_now(),
        "files": files,
    }
    atomic_write_json(
        outdir / "06_workflow_summary" / "native_run_manifest.json", manifest
    )


def _merge_methylation_summary(
    outdir: Path,
    status: Mapping[str, object],
    *,
    cna_error: BaseException | None,
) -> dict[str, object]:
    """Publish independent methylation/CNA outcomes even if either branch fails."""
    summary_dir = outdir / "06_workflow_summary"
    summary_path = summary_dir / "workflow_summary.json"
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise OncoTracerError(
                f"invalid workflow summary: {summary_path}"
            ) from error
        summary = loaded if isinstance(loaded, dict) else {}
    else:
        summary = {
            "oncotracer_version": __version__,
            "engine": "native",
            "nextflow_used": False,
            "mode": "ont",
            "outdir": str(outdir),
            "completed_at": utc_now(),
        }

    prior_cna_status = str(summary.get("workflow_status") or "complete")
    cna_status = "failed" if cna_error is not None else prior_cna_status
    methylation_status = str(status.get("overall_status") or "failed")
    cna_success = cna_status in {"complete", "partial_failure"}
    methylation_success = bool(status.get("completed_samples"))
    if cna_status == "complete" and methylation_status == "complete":
        workflow_status = "complete"
    elif cna_success or methylation_success:
        workflow_status = "partial_failure"
    else:
        workflow_status = "failed"

    summary.update(
        {
            "workflow_status": workflow_status,
            "cna_status": cna_status,
            "methylation_status": methylation_status,
            "methylation_classifier": status.get("classifier"),
            "methylation_status_file": str(
                outdir / "07_methylation" / "methylation_status.json"
            ),
            "methylation_completed_samples": status.get("completed_samples", []),
            "methylation_failed_samples": status.get("failed_samples", []),
            "methylation_no_cpg_samples": status.get("no_cpg_samples", []),
        }
    )
    if cna_error is not None:
        summary["cna_error"] = _sanitize_sample_error(cna_error)
    atomic_write_workflow_summary(summary_dir, summary)
    return summary


def _validate_native_dry_run(
    config: Mapping[str, object],
    config_path: Path,
    mode: str,
    lpwgs_root: Path,
    outdir: Path,
    force_run: bool,
    threads: int,
    methylation_request: MethylationRequest | None,
) -> None:
    plan: dict[str, object] = {
        "schema": "oncotracer-native-dry-run-v1",
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "config": str(config_path),
        "mode": mode,
        "lpwgs_root": str(lpwgs_root),
        "outdir": str(outdir),
        "threads": threads,
        "force": force_run,
        "run_cna_classifier": _as_bool(config.get("run_cna_classifier"), False),
    }
    if mode == "illumina":
        samplesheet_value = config.get("illumina_samplesheet")
        if not samplesheet_value:
            raise OncoTracerError("Illumina config requires illumina_samplesheet")
        samples = parse_illumina_samplesheet(Path(str(samplesheet_value)))
        binsize = _as_int(config.get("illumina_binsize_kb"), 100)
        if binsize < 1:
            raise OncoTracerError("illumina_binsize_kb must be positive")
        plan.update(
            {
                "samples": [sample.sample for sample in samples],
                "tumor_samples": [
                    sample.sample for sample in samples if sample.status == "tumor"
                ],
                "normal_samples": [
                    sample.sample for sample in samples if sample.status == "normal"
                ],
                "paired_end": all(sample.fastq_2 is not None for sample in samples),
                "caller": str(config.get("illumina_caller") or "qdnaseq"),
                "binsize_kb": binsize,
                "stages": [
                    "reference-validation",
                    "illumina-alignment",
                    "qdnaseq",
                    "bam-refinement",
                    "cna-codification",
                    "cna-plots",
                    "workflow-summary",
                ],
            }
        )
    else:
        samples = parse_ont_samples(config)
        caller = _ont_caller(config)
        binsize = _as_int(config.get("ont_binsize_kb"), 500)
        if binsize < 1:
            raise OncoTracerError("ont_binsize_kb must be positive")
        methylation = (
            methylation_plan(methylation_request) if methylation_request else None
        )
        plan.update(
            {
                "samples": [sample.sample for sample in samples],
                "barcodes": [sample.barcode for sample in samples],
                "tumor_samples": [
                    sample.sample for sample in samples if sample.status == "tumor"
                ],
                "normal_samples": [
                    sample.sample for sample in samples if sample.status == "normal"
                ],
                "caller": caller,
                "binsize_kb": binsize,
                "methylation": methylation,
                "stages": [
                    "reference-validation",
                    *(
                        list(methylation["stages"])
                        if isinstance(methylation, dict)
                        else []
                    ),
                    "ont-fastq-validation",
                    "ont-alignment",
                    "hmmcopy-ichorcna" if caller == "ichorcna" else "qdnaseq",
                    "bam-refinement",
                    "cna-codification",
                    "cna-plots",
                    "workflow-summary",
                ],
            }
        )
    pathology = config.get("pathology_csv")
    if pathology:
        require_file(Path(str(pathology)), "Pathology CSV")
        plan["pathology_csv"] = str(Path(str(pathology)).expanduser().resolve())
    print(json.dumps(plan, indent=2, sort_keys=True))


def _run_ont_cna_branch(
    root: Path,
    config: Mapping[str, object],
    samples: list[OntSample],
    samurai_out: Path,
    reference: Mapping[str, Path],
    outdir: Path,
    lpwgs_root: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    caller: str,
    threads: int,
    force: bool,
) -> None:
    """Execute the CNA branch so optional methylation can isolate its failure."""
    bams = align_ont(
        samples,
        reference,
        samurai_out,
        runner,
        ledger,
        toolchain,
        threads=threads,
        min_age_minutes=_as_int(config.get("ont_min_age_minutes"), 0),
        force=force,
    )
    binsize = _as_int(config.get("ont_binsize_kb"), 500)
    if caller == "ichorcna":
        caller_dir = run_ichorcna(
            root,
            lpwgs_root,
            samples,
            bams,
            samurai_out,
            binsize,
            runner,
            ledger,
            toolchain,
            threads=threads,
            force=force,
        )
    else:
        caller_dir, _ = run_qdnaseq(
            root,
            lpwgs_root,
            samples,
            bams,
            samurai_out,
            binsize,
            runner,
            ledger,
            toolchain,
            force=force,
            paired_ends=False,
        )
    run_refinement_and_outputs(
        root,
        config,
        "ont",
        samurai_out,
        caller_dir,
        samurai_out / "bam",
        outdir,
        lpwgs_root,
        runner,
        toolchain,
        force=force,
        caller=caller,
    )


def run_native(
    config_path: Path,
    *,
    root: Path | None = None,
    threads: int | None = None,
    force: bool | None = None,
    dry_run: bool = False,
    methylation: bool | None = None,
    methylation_classifier: str | None = None,
    methylation_pod5_dir: Path | None = None,
    methylation_gpu: bool | None = None,
) -> Path:
    """Run one owned native analysis or print its side-effect-free plan."""
    return _run_native_impl(
        config_path,
        root=root,
        threads=threads,
        force=force,
        dry_run=dry_run,
        methylation=methylation,
        methylation_classifier=methylation_classifier,
        methylation_pod5_dir=methylation_pod5_dir,
        methylation_gpu=methylation_gpu,
        _output_lease=None,
    )


def _run_native_impl(
    config_path: Path,
    *,
    root: Path | None,
    threads: int | None,
    force: bool | None,
    dry_run: bool,
    methylation: bool | None,
    methylation_classifier: str | None,
    methylation_pod5_dir: Path | None,
    methylation_gpu: bool | None,
    _output_lease: OutputRunLease | None,
) -> Path:
    explicit_root = root
    config_path = require_file(config_path, "OncoTracer YAML config")
    config = load_flat_yaml(config_path)
    _reject_local_sample_panel(config)
    mode = str(config.get("mode") or "").strip().lower()
    if mode not in {"illumina", "ont"}:
        raise OncoTracerError("config mode must be illumina or ont")
    ont_caller = _ont_caller(config) if mode == "ont" else None
    methylation_request = resolve_methylation_request(
        config,
        mode=mode,
        enabled_override=methylation,
        classifier_override=methylation_classifier,
        pod5_override=methylation_pod5_dir,
        gpu_override=methylation_gpu,
    )
    outdir_value = config.get("outdir")
    if not outdir_value:
        raise OncoTracerError("config requires outdir")
    outdir = Path(os.path.abspath(os.fspath(Path(str(outdir_value)).expanduser())))
    cpu = threads or max(1, min(os.cpu_count() or 1, 16))
    force_run = _as_bool(config.get("force"), False) if force is None else force
    if _output_lease is None and not dry_run:
        with claim_output_run(
            outdir, config_path=config_path, runtime_root_path=explicit_root
        ) as output_lease:
            return _run_native_impl(
                config_path,
                root=explicit_root,
                threads=threads,
                force=force,
                dry_run=False,
                methylation=methylation,
                methylation_classifier=methylation_classifier,
                methylation_pod5_dir=methylation_pod5_dir,
                methylation_gpu=methylation_gpu,
                _output_lease=output_lease,
            )

    payload_root: Path | None = None
    lpwgs_value = config.get("lpwgs_root")
    if lpwgs_value:
        lpwgs_root = Path(str(lpwgs_value)).expanduser().resolve()
    elif dry_run:
        root_hint = (
            Path(explicit_root).expanduser().resolve()
            if explicit_root
            else Path.cwd().resolve()
        )
        lpwgs_root = (root_hint / "project").resolve()
    else:
        payload_root = runtime_root(explicit_root)
        lpwgs_root = (payload_root / "project").resolve()
    if dry_run:
        inspect_output_target(outdir, runtime_root_path=explicit_root)
        _validate_native_dry_run(
            config,
            config_path,
            mode,
            lpwgs_root,
            outdir,
            force_run,
            cpu,
            methylation_request,
        )
        return outdir
    root = payload_root or runtime_root(explicit_root)
    native_dir = outdir / ".oncotracer-native"
    trace = native_dir / "trace.tsv"
    runner = CommandRunner(trace, dry_run=dry_run)
    ledger = StageLedger(native_dir / "state.json")
    toolchain = Toolchain.from_environment()

    # The native trace is an explicit release invariant.
    atomic_write_text(
        native_dir / "engine.txt",
        f"engine=native\nnextflow_used=false\nversion={__version__}\n",
    )
    cna_error: BaseException | None = None
    methylation_status: dict[str, object] | None = None

    if mode == "illumina":
        samplesheet_value = config.get("illumina_samplesheet")
        if not samplesheet_value:
            raise OncoTracerError("Illumina config requires illumina_samplesheet")
        samples = parse_illumina_samplesheet(Path(str(samplesheet_value)))
        samurai_out = outdir / "01_samurai_illumina"
        samurai_out.mkdir(parents=True, exist_ok=True)
        reference = prepare_reference(
            lpwgs_root,
            runner,
            ledger,
            toolchain,
            need_bwa=True,
            need_minimap2=False,
            threads=cpu,
        )
        bams = align_illumina(
            samples,
            reference,
            samurai_out,
            runner,
            ledger,
            toolchain,
            threads=cpu,
            force=force_run,
        )
        qdna_dir, refine_bam_dir = run_qdnaseq(
            root,
            lpwgs_root,
            samples,
            bams,
            samurai_out,
            _as_int(config.get("illumina_binsize_kb"), 100),
            runner,
            ledger,
            toolchain,
            force=force_run,
        )
        run_refinement_and_outputs(
            root,
            config,
            mode,
            samurai_out,
            qdna_dir,
            refine_bam_dir,
            outdir,
            lpwgs_root,
            runner,
            toolchain,
            force=force_run,
            caller="qdnaseq",
        )
    else:
        samples = parse_ont_samples(config)
        samurai_out = outdir / "01_samurai_ont"
        samurai_out.mkdir(parents=True, exist_ok=True)
        reference = prepare_reference(
            lpwgs_root,
            runner,
            ledger,
            toolchain,
            need_bwa=False,
            need_minimap2=True,
            threads=cpu,
        )
        assert ont_caller is not None
        if methylation_request is not None:
            try:
                with _validated_fasta_reader(reference, runner):
                    methylation_status = run_methylation(
                        root,
                        methylation_request,
                        samples,
                        reference,
                        outdir,
                        runner,
                        ledger,
                        threads=cpu,
                        force=force_run,
                    )
            except (OSError, OncoTracerError, ValueError) as error:
                methylation_status = write_global_methylation_failure(
                    outdir, methylation_request, error
                )
        try:
            _run_ont_cna_branch(
                root,
                config,
                samples,
                samurai_out,
                reference,
                outdir,
                lpwgs_root,
                runner,
                ledger,
                toolchain,
                caller=ont_caller,
                threads=cpu,
                force=force_run,
            )
        except (OSError, OncoTracerError, ValueError) as error:
            if methylation_request is None:
                raise
            cna_error = error

    if _as_bool(config.get("run_cna_classifier"), False) and cna_error is None:
        try:
            run_native_classifier(
                root,
                config,
                outdir,
                lpwgs_root,
                runner,
                ledger,
                toolchain,
                force=force_run,
            )
        except (OSError, OncoTracerError, ValueError) as error:
            if methylation_request is None:
                raise
            cna_error = error

    if methylation_request is not None:
        assert methylation_status is not None
        _merge_methylation_summary(
            outdir,
            methylation_status,
            cna_error=cna_error,
        )

    trace_text = trace.read_text(encoding="utf-8", errors="replace")
    if "nextflow" in trace_text.lower():
        raise OncoTracerError(
            "native execution trace contains a forbidden Nextflow command"
        )
    _output_lease.validate()
    write_run_manifest(outdir, config_path, trace)
    _output_lease.validate()
    summary_path = outdir / "06_workflow_summary" / "workflow_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("workflow_status") != "complete":
        failed = ", ".join(str(sample) for sample in summary.get("failed_samples", []))
        raise OncoTracerError(
            "native analysis completed with one or more incomplete branches "
            f"(CNA={summary.get('cna_status', summary.get('workflow_status'))}, "
            f"methylation={summary.get('methylation_status', 'not_requested')}, "
            f"failed_samples={failed or 'none'}); successful outputs and the "
            f"failure manifest were preserved under {outdir}"
        )
    return outdir
