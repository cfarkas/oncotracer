"""Storage-safe, ownership-bound installation transactions."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import venv
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from . import __version__
from .provenance import ProvenanceError, get_provenance
from .runtime import OncoTracerError, sha256_file


CONDA_BASE_SCHEMA = "oncotracer-conda-install-root-v1"
CONDA_ENV_SCHEMA = "oncotracer-conda-environment-v1"
POETRY_ENV_SCHEMA = "oncotracer-poetry-runtime-v1"
SIF_SCHEMA = "oncotracer-sif-install-v1"
TRANSACTION_SCHEMA = "oncotracer-install-transaction-v1"
TRANSACTION_OWNER_SCHEMA = "oncotracer-install-transaction-owner-v1"
LOCK_SCHEMA = "oncotracer-install-lock-v1"
INVENTORY_SCHEMA = "oncotracer-install-inventory-v1"

CONDA_NAMES = ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
MANAGED_CHILDREN = (*CONDA_NAMES, "poetry-runtime")
BASE_MARKER = ".oncotracer-conda-root.json"
ENV_MARKER = ".oncotracer-environment.json"
POETRY_MARKER = ".oncotracer-poetry-runtime.json"
CHILD_INVENTORY = ".oncotracer-install-inventory.json"
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _source_identity() -> dict[str, object]:
    try:
        provenance = get_provenance()
    except ProvenanceError as error:
        raise OncoTracerError(
            f"installer cannot establish OncoTracer source identity: {error}"
        ) from error
    commit = provenance.get("source_commit")
    source_sha256 = provenance.get("source_sha256")
    if provenance.get("oncotracer_version") != __version__:
        raise OncoTracerError(
            "installer provenance version does not match this OncoTracer executable"
        )
    if provenance.get("source_tree_dirty") is not False:
        raise OncoTracerError(
            "installer requires a clean, exactly identified OncoTracer source tree"
        )
    if not isinstance(commit, str) or not _HEX_40.fullmatch(commit):
        raise OncoTracerError(
            "installer requires an exact 40-character OncoTracer source commit"
        )
    if not isinstance(source_sha256, str) or not _HEX_64.fullmatch(source_sha256):
        raise OncoTracerError(
            "installer requires an exact OncoTracer source archive SHA-256"
        )
    return {
        "oncotracer_version": __version__,
        "source_commit": commit,
        "source_sha256": source_sha256,
    }


def _valid_source(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "oncotracer_version",
            "source_commit",
            "source_sha256",
        }
        and value.get("oncotracer_version") == __version__
        and isinstance(value.get("source_commit"), str)
        and bool(_HEX_40.fullmatch(str(value["source_commit"])))
        and isinstance(value.get("source_sha256"), str)
        and bool(_HEX_64.fullmatch(str(value["source_sha256"])))
    )


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise OncoTracerError(
                f"could not inspect installer target component {current}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise OncoTracerError(
                f"installer targets must not contain symlinks: {current}"
            )


def _broad_targets() -> set[Path]:
    broad = {
        Path("/"),
        _absolute(Path.home()),
        Path("/home"),
        Path("/media"),
        Path("/mnt"),
        Path("/opt"),
        Path("/srv"),
        Path("/tmp"),
        Path("/usr"),
        Path("/usr/local"),
        Path("/var"),
        Path("/var/tmp"),
    }
    for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        if value := os.environ.get(name):
            broad.add(_absolute(Path(value)))
    return broad


def _guard_dedicated(path: Path, label: str) -> Path:
    path = _absolute(path)
    if path in _broad_targets() or (os.path.lexists(path) and os.path.ismount(path)):
        raise OncoTracerError(
            f"{label} must be a dedicated child path, not a broad system, home, "
            f"temporary, storage, or XDG root: {path}"
        )
    _reject_symlink_components(path)
    return path


def _safe_read_json(path: Path, label: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OncoTracerError(
                    f"{label} must be one non-hardlinked regular file: {path}"
                )
            if metadata.st_size > 1024 * 1024:
                raise OncoTracerError(f"{label} is unexpectedly large: {path}")
            value = json.load(handle)
    except OncoTracerError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OncoTracerError(f"could not verify {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise OncoTracerError(f"{label} is not a JSON object: {path}")
    return value


def _safe_unlink(path: Path, label: str) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OncoTracerError(f"refusing to remove unsafe {label}: {path}")
    path.unlink()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: object) -> None:
    """Write JSON without following or reusing a predictable temporary path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.oncotracer-write-{os.getpid()}-{uuid.uuid4().hex}"
    )
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    created_identity: tuple[int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OncoTracerError(
                    f"installer metadata staging file is unsafe: {temporary}"
                )
            created_identity = (metadata.st_dev, metadata.st_ino)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        observed = temporary.lstat()
        if (observed.st_dev, observed.st_ino) != created_identity:
            raise OncoTracerError(
                f"installer metadata staging path changed unexpectedly: {temporary}"
            )
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if created_identity is not None and os.path.lexists(temporary):
            with contextlib.suppress(OSError):
                observed = temporary.lstat()
                if (observed.st_dev, observed.st_ino) == created_identity:
                    temporary.unlink()


def _lock_record(path: Path, target: Path, kind: str) -> dict[str, object]:
    return {
        "schema": LOCK_SCHEMA,
        "kind": kind,
        "canonical_lock": str(path),
        "canonical_target": str(target),
    }


@contextlib.contextmanager
def _install_lock(
    path: Path, target: Path, kind: str, *, exclusive: bool
) -> Iterator[None]:
    """Hold the ownership-bound installer/consumer lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = _lock_record(path, target, kind)
    created = False
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OncoTracerError(
                f"installer lock must be one non-hardlinked regular file: {path}"
            )
        # Initialize a new record while exclusively locked. Existing readers
        # can therefore never observe a half-written ownership record.
        fcntl.flock(
            handle.fileno(), fcntl.LOCK_EX if created or exclusive else fcntl.LOCK_SH
        )
        if created:
            json.dump(expected, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            _fsync_directory(path.parent)
            if not exclusive:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            try:
                current = path.lstat()
            except OSError as error:
                raise OncoTracerError(
                    f"installer lock path changed while acquiring it: {path}: {error}"
                ) from error
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OncoTracerError(
                    f"installer lock path was replaced while acquiring it: {path}"
                )
            handle.seek(0)
            try:
                observed = json.load(handle)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise OncoTracerError(
                    f"installer lock is malformed or unowned: {path}: {error}"
                ) from error
            if observed != expected:
                raise OncoTracerError(
                    f"installer lock is foreign or target-mismatched: {path}"
                )
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _exclusive_install_lock(path: Path, target: Path, kind: str) -> Iterator[None]:
    with _install_lock(path, target, kind, exclusive=True):
        yield


@contextlib.contextmanager
def _shared_install_lock(path: Path, target: Path, kind: str) -> Iterator[None]:
    with _install_lock(path, target, kind, exclusive=False):
        yield


def _path_contains(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _active_processes(
    path: Path, *, proc: Path = Path("/proc"), timeout: float = 5.0
) -> list[int]:
    """Return processes whose cwd/executable/fd/argv uses *path*."""
    deadline = time.monotonic() + timeout
    target = _absolute(path)
    observed: set[int] = set()
    if not proc.is_dir():
        raise OncoTracerError(
            "active-use verification requires a readable /proc filesystem"
        )
    try:
        processes = list(itertools.islice(proc.iterdir(), 131073))
    except OSError as error:
        raise OncoTracerError(
            f"could not enumerate /proc for active-use safety: {error}"
        ) from error
    if len(processes) > 131072:
        raise OncoTracerError("active-use verification exceeded its process bound")
    for process in processes:
        if time.monotonic() > deadline:
            raise OncoTracerError(
                f"active-use verification exceeded its safety deadline for {target}"
            )
        if not process.name.isdigit():
            continue
        pid = int(process.name)
        links = [process / "cwd", process / "exe"]
        try:
            fd_entries = list(itertools.islice((process / "fd").iterdir(), 4097))
        except OSError:
            fd_entries = []
        if len(fd_entries) > 4096:
            raise OncoTracerError(
                f"active-use verification exceeded its fd bound for PID {pid}"
            )
        links.extend(fd_entries)
        for link in links:
            if time.monotonic() > deadline:
                raise OncoTracerError(
                    f"active-use verification exceeded its safety deadline for {target}"
                )
            try:
                rendered = os.readlink(link).removesuffix(" (deleted)")
            except OSError:
                continue
            # pipe:[N], socket:[N], anon_inode:..., and other pseudo-targets
            # are not paths and must never be resolved against our cwd.
            if not rendered.startswith("/"):
                continue
            used = _absolute(Path(rendered))
            if _path_contains(target, used):
                observed.add(pid)
                break
        if pid in observed:
            continue
        try:
            with (process / "cmdline").open("rb") as handle:
                raw_arguments = handle.read(2 * 1024 * 1024 + 1)
        except OSError:
            continue
        if len(raw_arguments) > 2 * 1024 * 1024:
            raise OncoTracerError(
                f"active-use verification exceeded its argv bound for PID {pid}"
            )
        arguments = raw_arguments.split(b"\0")
        for argument in arguments:
            if not argument or not argument.startswith(b"/"):
                continue
            with contextlib.suppress(OSError, UnicodeDecodeError):
                used = _absolute(Path(os.fsdecode(argument)))
                if _path_contains(target, used):
                    observed.add(pid)
                    break
        if pid in observed:
            continue
        try:
            with (process / "maps").open(
                "r", encoding="utf-8", errors="replace"
            ) as handle:
                raw_mappings = handle.read(16 * 1024 * 1024 + 1)
        except OSError:
            continue
        if len(raw_mappings) > 16 * 1024 * 1024:
            raise OncoTracerError(
                f"active-use verification exceeded its map bound for PID {pid}"
            )
        mappings = raw_mappings.splitlines()
        for mapping in mappings:
            if str(target) not in mapping:
                continue
            fields = mapping.split(maxsplit=5)
            if len(fields) != 6 or not fields[5].startswith("/"):
                continue
            rendered = fields[5].removesuffix(" (deleted)")
            used = _absolute(Path(rendered))
            if _path_contains(target, used):
                observed.add(pid)
                break
    return sorted(observed)


def _assert_inactive(path: Path) -> None:
    if processes := _active_processes(path):
        rendered = ", ".join(map(str, processes[:20]))
        raise OncoTracerError(
            f"refusing to replace installer asset used by active process(es) {rendered}: {path}"
        )


def _run_checked(
    command: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str] | None:
    argv = [str(value) for value in command]
    import shlex

    print(f"OncoTracer command: {shlex.join(argv)}", file=sys.stderr, flush=True)
    if dry_run:
        return None
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="", file=sys.stderr)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode not in accepted_returncodes:
        raise OncoTracerError(
            f"command failed with exit code {completed.returncode}: {shlex.join(argv)}"
        )
    return completed


def _tree_inventory(
    root: Path, *, include_installer_metadata: bool = False
) -> dict[str, object]:
    """Return an exact, deterministic inventory without following symlinks."""
    entries: list[dict[str, object]] = []
    pending = [root]
    root_device = root.lstat().st_dev
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(root).as_posix()
            if not include_installer_metadata and relative in {
                ENV_MARKER,
                POETRY_MARKER,
                CHILD_INVENTORY,
            }:
                continue
            metadata = path.lstat()
            if metadata.st_dev != root_device:
                raise OncoTracerError(
                    f"managed environment crosses filesystems: {path}"
                )
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISDIR(metadata.st_mode):
                entries.append({"path": relative, "type": "directory", "mode": mode})
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                entries.append(
                    {
                        "path": relative,
                        "type": "file",
                        "mode": mode,
                        "size": metadata.st_size,
                        "sha256": sha256_file(path),
                    }
                )
            elif stat.S_ISLNK(metadata.st_mode):
                entries.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "mode": mode,
                        "target": os.readlink(path),
                    }
                )
            else:
                raise OncoTracerError(
                    f"managed environment contains a special file: {path}"
                )
    entries.sort(key=lambda item: str(item["path"]))
    return {"schema": INVENTORY_SCHEMA, "entries": entries}


def _valid_inventory(value: object, *, allow_installer_metadata: bool = False) -> bool:
    if not isinstance(value, dict) or set(value) != {"schema", "entries"}:
        return False
    if value.get("schema") != INVENTORY_SCHEMA or not isinstance(
        value.get("entries"), list
    ):
        return False
    previous = ""
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            return False
        relative = entry.get("path")
        kind = entry.get("type")
        mode = entry.get("mode")
        if (
            not isinstance(relative, str)
            or not relative
            or relative <= previous
            or relative.startswith("/")
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or (
                not allow_installer_metadata
                and relative in {ENV_MARKER, POETRY_MARKER, CHILD_INVENTORY}
            )
            or not isinstance(mode, int)
            or mode < 0
            or mode > 0o7777
        ):
            return False
        previous = relative
        if kind == "directory":
            if set(entry) != {"path", "type", "mode"}:
                return False
        elif kind == "file":
            if (
                set(entry) != {"path", "type", "mode", "size", "sha256"}
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not _HEX_64.fullmatch(str(entry["sha256"]))
            ):
                return False
        elif kind == "symlink":
            if set(entry) != {"path", "type", "mode", "target"} or not isinstance(
                entry.get("target"), str
            ):
                return False
        else:
            return False
    return True


def _write_child_inventory(destination: Path) -> str:
    inventory = _tree_inventory(destination)
    path = destination / CHILD_INVENTORY
    _atomic_write_json(path, inventory)
    return sha256_file(path)


def _verify_child_inventory(storage: Path, marker: Mapping[str, object]) -> None:
    inventory_path = storage / CHILD_INVENTORY
    inventory = _safe_read_json(inventory_path, "managed environment inventory")
    if not _valid_inventory(inventory) or sha256_file(inventory_path) != marker.get(
        "inventory_sha256"
    ):
        raise OncoTracerError(
            f"managed environment inventory is malformed or marker-mismatched: {storage}"
        )
    observed = _tree_inventory(storage)
    if observed != inventory:
        raise OncoTracerError(
            "managed environment contains changed or foreign entries and will not "
            f"be reused or replaced: {storage}"
        )


def _run_semantic_probe(
    command: Sequence[str | Path],
    label: str,
    required: str,
    *,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> None:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = _run_checked(
        command, env=environment, accepted_returncodes=accepted_returncodes
    )
    assert result is not None
    combined = f"{result.stdout}\n{result.stderr}"
    if required.casefold() not in combined.casefold():
        raise OncoTracerError(f"{label} did not report {required}")


def _verify_conda_runtime(destination: Path, name: str) -> None:
    probes: dict[str, tuple[list[str | Path], str]] = {
        "core": (
            [
                destination / "bin" / "python",
                "-c",
                "import numpy,pandas,pysam,reportlab; print('CORE_OK')",
            ],
            "CORE_OK",
        ),
        "qdnaseq": (
            [
                destination / "bin" / "Rscript",
                "-e",
                "suppressPackageStartupMessages(library(QDNAseq));cat('QDNASEQ_OK\\n')",
            ],
            "QDNASEQ_OK",
        ),
        "ichorcna": (
            [
                destination / "bin" / "Rscript",
                "-e",
                "suppressPackageStartupMessages(library(ichorCNA));cat('ICHORCNA_OK\\n')",
            ],
            "ICHORCNA_OK",
        ),
        "classifier": (
            [
                destination / "bin" / "python",
                "-c",
                "import numpy,pandas,reportlab,sklearn,torch,transformers; print('CLASSIFIER_OK')",
            ],
            "CLASSIFIER_OK",
        ),
        "gistic": ([destination / "bin" / "gistic2", "-h"], "gistic"),
    }
    command, required = probes[name]
    executable = Path(command[0])
    try:
        metadata = executable.lstat()
    except OSError as error:
        raise OncoTracerError(
            f"managed {name} environment lacks its semantic probe: {executable}"
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK):
        raise OncoTracerError(f"managed {name} probe is not executable: {executable}")
    _run_semantic_probe(
        command,
        f"managed {name} environment",
        required,
        accepted_returncodes=(
            frozenset({0, 1, 255}) if name == "gistic" else frozenset({0})
        ),
    )


def _base_marker(
    base: Path, install_id: str, source: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema": CONDA_BASE_SCHEMA,
        "object_type": "conda-install-root",
        "install_id": install_id,
        "canonical_path": str(base),
        "managed_children": list(MANAGED_CHILDREN),
        "source": dict(source),
    }


def _valid_base_marker(value: object, base: Path) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "schema",
            "object_type",
            "install_id",
            "canonical_path",
            "managed_children",
            "source",
        }
        and value.get("schema") == CONDA_BASE_SCHEMA
        and value.get("object_type") == "conda-install-root"
        and isinstance(value.get("install_id"), str)
        and bool(_HEX_32.fullmatch(str(value["install_id"])))
        and value.get("canonical_path") == str(base)
        and value.get("managed_children") == list(MANAGED_CHILDREN)
        and _valid_source(value.get("source"))
    )


def _environment_marker(
    destination: Path,
    install_id: str,
    name: str,
    definition_sha256: str,
    source: Mapping[str, object],
    inventory_sha256: str,
) -> dict[str, object]:
    return {
        "schema": CONDA_ENV_SCHEMA,
        "object_type": "conda-environment",
        "install_id": install_id,
        "canonical_path": str(destination),
        "environment": name,
        "definition_sha256": definition_sha256,
        "inventory_sha256": inventory_sha256,
        "source": dict(source),
    }


def _poetry_marker(
    destination: Path,
    install_id: str,
    project_sha256: str,
    lock_sha256: str,
    launcher_sha256: str,
    source: Mapping[str, object],
    inventory_sha256: str,
) -> dict[str, object]:
    return {
        "schema": POETRY_ENV_SCHEMA,
        "object_type": "poetry-runtime",
        "install_id": install_id,
        "canonical_path": str(destination),
        "project_sha256": project_sha256,
        "lock_sha256": lock_sha256,
        "launcher_sha256": launcher_sha256,
        "inventory_sha256": inventory_sha256,
        "source": dict(source),
    }


def _valid_child_marker(
    value: object,
    destination: Path,
    install_id: str,
    name: str,
) -> bool:
    if not isinstance(value, dict):
        return False
    common = (
        value.get("install_id") == install_id
        and value.get("canonical_path") == str(destination)
        and _valid_source(value.get("source"))
    )
    if name == "poetry-runtime":
        return (
            common
            and set(value)
            == {
                "schema",
                "object_type",
                "install_id",
                "canonical_path",
                "project_sha256",
                "lock_sha256",
                "launcher_sha256",
                "inventory_sha256",
                "source",
            }
            and value.get("schema") == POETRY_ENV_SCHEMA
            and value.get("object_type") == "poetry-runtime"
            and isinstance(value.get("project_sha256"), str)
            and bool(_HEX_64.fullmatch(str(value["project_sha256"])))
            and isinstance(value.get("lock_sha256"), str)
            and bool(_HEX_64.fullmatch(str(value["lock_sha256"])))
            and isinstance(value.get("launcher_sha256"), str)
            and bool(_HEX_64.fullmatch(str(value["launcher_sha256"])))
            and isinstance(value.get("inventory_sha256"), str)
            and bool(_HEX_64.fullmatch(str(value["inventory_sha256"])))
        )
    return (
        common
        and set(value)
        == {
            "schema",
            "object_type",
            "install_id",
            "canonical_path",
            "environment",
            "definition_sha256",
            "inventory_sha256",
            "source",
        }
        and value.get("schema") == CONDA_ENV_SCHEMA
        and value.get("object_type") == "conda-environment"
        and value.get("environment") == name
        and isinstance(value.get("definition_sha256"), str)
        and bool(_HEX_64.fullmatch(str(value["definition_sha256"])))
        and isinstance(value.get("inventory_sha256"), str)
        and bool(_HEX_64.fullmatch(str(value["inventory_sha256"])))
    )


def _classify_base(base: Path) -> tuple[str, dict[str, object] | None]:
    if not os.path.lexists(base):
        return "absent", None
    metadata = base.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise OncoTracerError(
            f"Conda install root must be a real directory, not a symlink or file: {base}"
        )
    entries = list(base.iterdir())
    if not entries:
        return "empty", None
    marker_path = base / BASE_MARKER
    if not marker_path.exists():
        raise OncoTracerError(
            "refusing to adopt or modify a non-empty unowned Conda install root; "
            f"choose a new empty dedicated --prefix: {base}"
        )
    marker = _safe_read_json(marker_path, "Conda install-root ownership marker")
    if not _valid_base_marker(marker, base):
        raise OncoTracerError(
            f"Conda install-root ownership marker is malformed or path-mismatched: {marker_path}"
        )
    install_id = str(marker["install_id"])
    for name in MANAGED_CHILDREN:
        child = base / name
        if not os.path.lexists(child):
            continue
        metadata = child.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise OncoTracerError(
                f"managed installer child is not a real directory: {child}"
            )
        child_marker = child / (
            POETRY_MARKER if name == "poetry-runtime" else ENV_MARKER
        )
        if not child_marker.exists():
            raise OncoTracerError(
                f"refusing to adopt unowned managed child path: {child}"
            )
        value = _safe_read_json(child_marker, f"{name} ownership marker")
        if not _valid_child_marker(value, child, install_id, name):
            raise OncoTracerError(
                f"managed child ownership marker is malformed or mismatched: {child_marker}"
            )
        _verify_child_inventory(child, value)
        if name == "poetry-runtime" and (
            not _poetry_complete(child)
            or sha256_file(child / "bin" / "oncotracer") != value["launcher_sha256"]
        ):
            raise OncoTracerError(
                f"managed Poetry launcher does not match its ownership marker: {child}"
            )
    return "owned", marker


def _child_marker_at(
    storage: Path,
    destination: Path,
    base_marker: Mapping[str, object],
    name: str,
) -> dict[str, object] | None:
    if not os.path.lexists(storage):
        return None
    marker_name = POETRY_MARKER if name == "poetry-runtime" else ENV_MARKER
    value = _safe_read_json(storage / marker_name, f"{name} ownership marker")
    if not _valid_child_marker(
        value, destination, str(base_marker["install_id"]), name
    ) or value.get("source") != base_marker.get("source"):
        raise OncoTracerError(f"managed child ownership is invalid: {storage}")
    _verify_child_inventory(storage, value)
    if name == "poetry-runtime" and (
        not _poetry_complete(storage)
        or sha256_file(storage / "bin" / "oncotracer") != value["launcher_sha256"]
    ):
        raise OncoTracerError(
            f"managed Poetry launcher integrity is invalid: {storage}"
        )
    return value


def _child_marker_value(
    base: Path, base_marker: Mapping[str, object], name: str
) -> dict[str, object] | None:
    destination = base / name
    return _child_marker_at(destination, destination, base_marker, name)


def _conda_complete(path: Path) -> bool:
    history = path / "conda-meta" / "history"
    try:
        metadata = history.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def _poetry_complete(path: Path) -> bool:
    def safe_executable(candidate: Path) -> bool:
        try:
            metadata = candidate.lstat()
        except OSError:
            return False
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and os.access(candidate, os.X_OK)
        )

    return safe_executable(path / "bin" / "python") and safe_executable(
        path / "bin" / "oncotracer"
    )


_POETRY_PAYLOAD_ROOTS = ("bin", "examples", "params", "environments", "provenance")
_POETRY_EXCLUDED_PATHS = frozenset(
    {
        "bin/cna_classifier_nf/README.md",
        "bin/scripts/install_oncotracer.sh",
        "examples/hcc1143_lpwgs/README.md",
        "examples/hcc1143_lpwgs/run_example.sh",
        "examples/prjna754199/PROVENANCE.md",
        "examples/prjna754199/README.md",
        "examples/prjna754199/run_example.sh",
        "bin/scripts/prepare_samurai_source.sh",
        "bin/scripts/qdnaseq_local_pon.R",
        "bin/scripts/native_qdnaseq_pon.R",
        "bin/scripts/run_ifcnv_ont_lpwgs.py",
        "bin/scripts/run_illumina_samurai_fastq.sh",
        "bin/scripts/run_ont_samurai_barcodes.sh",
        "bin/scripts/run_qdnaseq_local_pon.sh",
    }
)


def _require_poetry_v2(poetry: str) -> None:
    result = _run_checked([poetry, "--version"])
    assert result is not None
    match = re.fullmatch(
        r"Poetry \(version ([0-9]+)(?:\.[0-9]+){1,2}\)", result.stdout.strip()
    )
    if match is None or int(match.group(1)) < 2:
        rendered = result.stdout.strip() or result.stderr.strip() or "unknown"
        raise OncoTracerError(
            "Poetry installation requires Poetry >=2 before any target is changed; "
            f"observed {rendered!r}"
        )


def _verify_poetry_source_checkout(root: Path, source: Mapping[str, object]) -> None:
    """Bind the tree copied into the wheel to the executable source identity."""

    def git(*arguments: str, binary: bool = False) -> bytes | str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=not binary,
        )
        if result.returncode:
            detail = (
                result.stderr
                if not binary
                else result.stderr.decode("utf-8", errors="replace")
            )
            raise OncoTracerError(
                f"Poetry requires an exact clean Git checkout: {detail.strip()}"
            )
        return result.stdout

    top = _absolute(Path(str(git("rev-parse", "--show-toplevel")).strip()))
    if top != root:
        raise OncoTracerError(
            f"Poetry build root is not the exact Git checkout root: {root}"
        )
    commit = str(git("rev-parse", "--verify", "HEAD^{commit}")).strip().lower()
    if commit != source.get("source_commit"):
        raise OncoTracerError(
            "Poetry checkout HEAD does not match this executable source identity"
        )
    status = str(git("status", "--porcelain", "--untracked-files=all"))
    if status.strip():
        raise OncoTracerError("Poetry requires an exact clean Git checkout")
    archive = git(
        "-c", "tar.umask=0002", "archive", "--format=tar", commit, binary=True
    )
    assert isinstance(archive, bytes)
    if hashlib.sha256(archive).hexdigest() != source.get("source_sha256"):
        raise OncoTracerError(
            "Poetry checkout archive does not match this executable source identity"
        )


def _poetry_payload_allowed(relative: Path) -> bool:
    rendered = relative.as_posix()
    return not (
        rendered in _POETRY_EXCLUDED_PATHS
        or any(
            part == "__pycache__"
            or part == "work"
            or part == "nextflow.config"
            or part.startswith(".nextflow")
            or part.endswith((".pyc", ".pyo", ".nf"))
            for part in relative.parts
        )
    )


def _copy_poetry_project(root: Path, staging: Path) -> None:
    """Copy only fixed product roots into an isolated Poetry build tree."""
    roots = (
        "pyproject.toml",
        "poetry.lock",
        "README.md",
        "oncotracer_cli",
        *_POETRY_PAYLOAD_ROOTS,
    )
    for name in roots:
        source = root / name
        if not os.path.lexists(source):
            raise OncoTracerError(f"Poetry build input is missing: {source}")
        metadata = source.lstat()
        destination = staging / name
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink < 1:
                raise OncoTracerError(f"Poetry build input is unsafe: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
            continue
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OncoTracerError(
                f"Poetry build input is not a physical tree: {source}"
            )
        destination.mkdir(parents=True)
        for member in sorted(source.rglob("*")):
            relative = member.relative_to(root)
            if not _poetry_payload_allowed(relative):
                continue
            observed = member.lstat()
            target = staging / relative
            if stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(observed.st_mode):
                target.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISREG(observed.st_mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(member, target, follow_symlinks=False)
            else:
                raise OncoTracerError(
                    f"Poetry build input contains a symlink or special file: {member}"
                )


def _write_poetry_build_metadata(staging: Path, source: Mapping[str, object]) -> None:
    payload = staging / "provenance" / "native-v2-sources.json"
    if not payload.is_file():
        raise OncoTracerError(f"Poetry build lacks provenance payload: {payload}")
    contents = (
        '"""Generated immutable source metadata for this OncoTracer build."""\n\n'
        "from __future__ import annotations\n\n"
        'BUILD_METADATA_SCHEMA = "oncotracer-build-metadata-v1"\n'
        'SOURCE_SHA256_DEFINITION = "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)"\n'
        f'SOURCE_COMMIT = {source["source_commit"]!r}\n'
        f'SOURCE_SHA256 = {source["source_sha256"]!r}\n'
        "SOURCE_TREE_DIRTY = False\n"
        'SOURCE_METADATA_ORIGIN = "embedded"\n'
        "ONCOTRACER_SOURCE_COMMIT = SOURCE_COMMIT\n"
        "ONCOTRACER_SOURCE_SHA256 = SOURCE_SHA256\n"
        'PROVENANCE_PAYLOAD_PATH = "payload/provenance/native-v2-sources.json"\n'
        f"PROVENANCE_PAYLOAD_SHA256 = {hashlib.sha256(payload.read_bytes()).hexdigest()!r}\n"
    )
    (staging / "oncotracer_cli" / "_build_metadata.py").write_text(
        contents, encoding="utf-8"
    )


def _verify_poetry_wheel(wheel: Path) -> None:
    try:
        metadata = wheel.lstat()
    except OSError as error:
        raise OncoTracerError(f"Poetry did not produce its wheel: {wheel}") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OncoTracerError(f"Poetry wheel is not one regular file: {wheel}")
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as error:
        raise OncoTracerError(f"Poetry produced an invalid wheel: {wheel}") from error
    if len(names) != len(set(names)) or any(
        name.startswith("/") or ".." in Path(name).parts for name in names
    ):
        raise OncoTracerError(f"Poetry wheel has unsafe member names: {wheel}")
    required = (
        "oncotracer_cli/cli.py",
        "bin/scripts/native_qdnaseq.R",
        "environments/native-core.yml",
        "provenance/native-v2-sources.json",
    )
    for expected in required:
        if expected not in names:
            raise OncoTracerError(
                f"Poetry wheel lacks required native payload {expected}: {wheel}"
            )
    forbidden = [
        name
        for name in names
        if not _poetry_payload_allowed(Path(name)) or name in _POETRY_EXCLUDED_PATHS
    ]
    if forbidden:
        raise OncoTracerError(
            f"Poetry wheel contains forbidden legacy/Nextflow payload: {forbidden[0]}"
        )


def _build_poetry_wheel(
    root: Path,
    transaction: Path,
    poetry: str,
    source: Mapping[str, object],
) -> Path:
    state = _require_transaction_subdir(transaction, "poetry-state", create=True)
    staging = state / "source"
    wheels = state / "wheels"
    staging.mkdir(mode=0o700)
    wheels.mkdir(mode=0o700)
    _copy_poetry_project(root, staging)
    _write_poetry_build_metadata(staging, source)
    environment = os.environ.copy()
    environment.update(
        {
            "POETRY_VIRTUALENVS_CREATE": "false",
            "POETRY_VIRTUALENVS_IN_PROJECT": "false",
            "POETRY_CACHE_DIR": str(state / "cache"),
            "POETRY_CONFIG_DIR": str(state / "config"),
            "POETRY_DATA_DIR": str(state / "data"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    environment.pop("VIRTUAL_ENV", None)
    _run_checked(
        [poetry, "build", "--format", "wheel", "--output", wheels, "--no-interaction"],
        cwd=staging,
        env=environment,
    )
    candidates = sorted(wheels.glob("*.whl"))
    if len(candidates) != 1:
        raise OncoTracerError(
            f"isolated Poetry build produced {len(candidates)} wheels, expected one"
        )
    _verify_poetry_wheel(candidates[0])
    return candidates[0]


def _pip_install_poetry_wheel(target: Path, wheel: Path, transaction: Path) -> None:
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PIP_PREFIX", "PIP_TARGET", "PIP_USER"):
        environment.pop(name, None)
    environment.update(
        {
            "VIRTUAL_ENV": str(target),
            "PIP_CACHE_DIR": str(transaction / "poetry-state" / "pip-cache"),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_REQUIRE_VIRTUALENV": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    _run_checked(
        [
            target / "bin" / "python",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            wheel,
        ],
        env=environment,
    )


def _verify_poetry_runtime(
    destination: Path, expected_source: Mapping[str, object]
) -> None:
    launcher = destination / "bin" / "oncotracer"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    version = _run_checked([launcher, "--version"], env=environment)
    provenance = _run_checked([launcher, "provenance", "--json"], env=environment)
    assert version is not None and provenance is not None
    if version.stdout.strip() != f"OncoTracer {__version__}":
        raise OncoTracerError(
            f"managed Poetry launcher reported an unexpected version: {launcher}"
        )
    try:
        record = json.loads(provenance.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise OncoTracerError(
            f"managed Poetry launcher emitted invalid provenance JSON: {launcher}"
        ) from error
    if not isinstance(record, dict):
        raise OncoTracerError(
            f"managed Poetry launcher provenance is not an object: {launcher}"
        )
    for key in ("oncotracer_version", "source_commit", "source_sha256"):
        if record.get(key) != expected_source.get(key):
            raise OncoTracerError(
                f"managed Poetry launcher provenance {key} is inconsistent"
            )
    if record.get("source_tree_dirty") is not False:
        raise OncoTracerError("managed Poetry launcher provenance is not clean")


def _transaction_owner(
    transaction: Path, base: Path, transaction_id: str, kind: str
) -> dict[str, object]:
    return {
        "schema": TRANSACTION_OWNER_SCHEMA,
        "kind": kind,
        "transaction_id": transaction_id,
        "canonical_transaction": str(transaction),
        "canonical_target": str(base),
    }


def _valid_transaction_owner(
    value: object, transaction: Path, target: Path, transaction_id: str, kind: str
) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "schema",
            "kind",
            "transaction_id",
            "canonical_transaction",
            "canonical_target",
        }
        and value.get("schema") == TRANSACTION_OWNER_SCHEMA
        and value.get("kind") == kind
        and value.get("transaction_id") == transaction_id
        and value.get("canonical_transaction") == str(transaction)
        and value.get("canonical_target") == str(target)
    )


def _assert_single_filesystem_tree(root: Path, parent: Path) -> None:
    """Refuse cleanup if an owned transaction contains another filesystem."""
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise OncoTracerError(
            f"could not inspect installer transaction parent {parent}: {error}"
        ) from error
    expected_device = parent_metadata.st_dev
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
        except OSError as error:
            raise OncoTracerError(
                f"could not inspect installer transaction member {current}: {error}"
            ) from error
        if metadata.st_dev != expected_device:
            raise OncoTracerError(
                "refusing to remove an installer transaction that crosses "
                f"filesystems: {current}"
            )
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise OncoTracerError(
                f"refusing special file in installer transaction: {current}"
            )
        try:
            pending.extend(current.iterdir())
        except OSError as error:
            raise OncoTracerError(
                f"could not inventory installer transaction {current}: {error}"
            ) from error


def _require_transaction(
    transaction: Path, target: Path, transaction_id: str, kind: str
) -> None:
    expected = target.parent / (
        f".{target.name}.oncotracer-{kind}-txn-{transaction_id}"
    )
    if transaction != expected:
        raise OncoTracerError(
            f"installer transaction path is not the exact expected child: {transaction}"
        )
    try:
        parent_metadata = target.parent.lstat()
        metadata = transaction.lstat()
    except OSError as error:
        raise OncoTracerError(
            f"could not inspect installer transaction {transaction}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_dev != parent_metadata.st_dev
    ):
        raise OncoTracerError(
            f"installer transaction is not a same-filesystem physical directory: {transaction}"
        )
    owner = _safe_read_json(
        transaction / ".oncotracer-transaction-owner.json",
        "installer transaction ownership marker",
    )
    if not _valid_transaction_owner(owner, transaction, target, transaction_id, kind):
        raise OncoTracerError(
            f"refusing an unowned installer transaction: {transaction}"
        )


def _require_transaction_subdir(
    transaction: Path, name: str, *, create: bool = False
) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise OncoTracerError(f"invalid installer transaction member: {name!r}")
    path = transaction / name
    if create and not os.path.lexists(path):
        path.mkdir(mode=0o700)
    try:
        transaction_metadata = transaction.lstat()
        metadata = path.lstat()
    except OSError as error:
        raise OncoTracerError(
            f"could not inspect installer transaction directory {path}: {error}"
        ) from error
    if (
        path.parent != transaction
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_dev != transaction_metadata.st_dev
    ):
        raise OncoTracerError(
            f"installer transaction member is not a physical directory: {path}"
        )
    return path


def _target_claim_path(transaction: Path, name: str) -> Path:
    return transaction / "claims" / f"{name}.json"


def _write_target_claim(
    transaction: Path,
    transaction_id: str,
    name: str,
    target: Path,
    *,
    marker: Mapping[str, object] | None = None,
    partial_inventory: Mapping[str, object] | None = None,
    storage: Path | None = None,
) -> None:
    claimed_storage = storage or target
    metadata = claimed_storage.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OncoTracerError(
            f"installer-created prefix is not physical: {claimed_storage}"
        )
    value = {
        "schema": "oncotracer-install-target-claim-v1",
        "transaction_id": transaction_id,
        "name": name,
        "canonical_target": str(target),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "marker": dict(marker) if marker is not None else None,
        "partial_inventory": (
            dict(partial_inventory) if partial_inventory is not None else None
        ),
    }
    _atomic_write_json(_target_claim_path(transaction, name), value)


def _read_target_claim(
    transaction: Path, transaction_id: str, name: str, target: Path
) -> dict[str, object]:
    value = _safe_read_json(
        _target_claim_path(transaction, name), "installer target claim"
    )
    valid_shape = (
        set(value)
        == {
            "schema",
            "transaction_id",
            "name",
            "canonical_target",
            "device",
            "inode",
            "marker",
            "partial_inventory",
        }
        and value.get("schema") == "oncotracer-install-target-claim-v1"
        and value.get("transaction_id") == transaction_id
        and value.get("name") == name
        and value.get("canonical_target") == str(target)
        and isinstance(value.get("device"), int)
        and isinstance(value.get("inode"), int)
        and (value.get("marker") is None or isinstance(value.get("marker"), dict))
        and (
            value.get("partial_inventory") is None
            or _valid_inventory(
                value.get("partial_inventory"), allow_installer_metadata=True
            )
        )
    )
    if not valid_shape:
        raise OncoTracerError(f"installer target claim is malformed: {target}")
    return value


def _discard_claimed_target(
    transaction: Path,
    transaction_id: str,
    name: str,
    target: Path,
    discarded: Path,
    base_marker: Mapping[str, object],
) -> None:
    claim = _read_target_claim(transaction, transaction_id, name, target)
    metadata = target.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (claim["device"], claim["inode"])
    ):
        raise OncoTracerError(
            f"installer-created target identity changed; refusing removal: {target}"
        )
    marker = claim.get("marker")
    _assert_inactive(target)
    if marker is None:
        # A killed package manager can leave arbitrary bytes, including data
        # written by another process before we regain the lock. Never delete
        # such an unsealed tree. Move its exact claimed inode aside so the
        # prior managed prefix can be restored, and leave it for inspection.
        preserved = target.parent.parent / (
            f".{target.parent.name}-{target.name}.oncotracer-preserved-"
            f"{transaction_id}-{name}"
        )
        if os.path.lexists(preserved):
            raise OncoTracerError(
                f"installer preservation destination already exists: {preserved}"
            )
        os.replace(target, preserved)
        print(
            f"OncoTracer preserved interrupted installer target at {preserved}",
            file=sys.stderr,
            flush=True,
        )
        return
    observed = _child_marker_at(target, target, base_marker, name)
    if observed != marker:
        raise OncoTracerError(
            f"installer-created target marker changed; refusing removal: {target}"
        )
    os.replace(target, discarded / name)


def _remove_transaction(
    transaction: Path, target: Path, transaction_id: str, kind: str
) -> None:
    if not os.path.lexists(transaction):
        return
    _require_transaction(transaction, target, transaction_id, kind)
    _assert_inactive(transaction)
    _assert_single_filesystem_tree(transaction, target.parent)
    shutil.rmtree(transaction)


def _transaction_path(target: Path, kind: str, transaction_id: str) -> Path:
    return target.parent / f".{target.name}.oncotracer-{kind}-txn-{transaction_id}"


def _create_transaction(target: Path, kind: str, transaction_id: str) -> Path:
    transaction = _transaction_path(target, kind, transaction_id)
    transaction.mkdir(mode=0o700)
    _atomic_write_json(
        transaction / ".oncotracer-transaction-owner.json",
        _transaction_owner(transaction, target, transaction_id, kind),
    )
    return transaction


def _new_transaction(target: Path, kind: str) -> tuple[str, Path]:
    transaction_id = uuid.uuid4().hex
    return transaction_id, _create_transaction(target, kind, transaction_id)


def _preserve_path(path: Path, transaction_id: str, label: str) -> Path:
    preserved = path.parent / (
        f".{path.name}.oncotracer-preserved-{transaction_id}-{label}"
    )
    if os.path.lexists(preserved):
        raise OncoTracerError(
            f"installer preservation destination already exists: {preserved}"
        )
    return preserved


def _preserve_staging_transaction(
    transaction: Path, target: Path, transaction_id: str, kind: str
) -> Path | None:
    if not os.path.lexists(transaction):
        return None
    metadata = transaction.lstat()
    parent_metadata = target.parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_dev != parent_metadata.st_dev
    ):
        raise OncoTracerError(
            f"staging transaction is not a same-filesystem physical directory: {transaction}"
        )
    _assert_inactive(transaction)
    preserved = _preserve_path(transaction, transaction_id, f"{kind}-staging")
    os.replace(transaction, preserved)
    print(
        f"OncoTracer preserved interrupted installer staging at {preserved}",
        file=sys.stderr,
        flush=True,
    )
    return preserved


def _journal_path(target: Path, kind: str) -> Path:
    return target.parent / f".{target.name}.oncotracer-{kind}-transaction.json"


def _lock_path(target: Path, kind: str) -> Path:
    return target.parent / f".{target.name}.oncotracer-{kind}.lock"


def _write_journal(path: Path, value: Mapping[str, object]) -> None:
    _atomic_write_json(path, dict(value))


def _valid_conda_journal(value: object, base: Path) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "kind",
        "phase",
        "transaction_id",
        "install_id",
        "canonical_target",
        "canonical_transaction",
        "prestate",
        "assets",
        "previous_markers",
        "new_markers",
        "base_marker_before",
        "base_marker_after",
    }:
        return False
    transaction_id = value.get("transaction_id")
    transaction = base.parent / f".{base.name}.oncotracer-conda-txn-{transaction_id}"
    shallow_valid = (
        value.get("schema") == TRANSACTION_SCHEMA
        and value.get("kind") == "conda"
        and value.get("phase") in {"staging", "publishing", "committed"}
        and isinstance(transaction_id, str)
        and bool(_HEX_32.fullmatch(transaction_id))
        and isinstance(value.get("install_id"), str)
        and bool(_HEX_32.fullmatch(str(value["install_id"])))
        and value.get("canonical_target") == str(base)
        and value.get("canonical_transaction") == str(transaction)
        and value.get("prestate") in {"absent", "empty", "owned"}
        and isinstance(value.get("assets"), list)
        and bool(value["assets"])
        and all(name in MANAGED_CHILDREN for name in value["assets"])
        and len(set(value["assets"])) == len(value["assets"])
        and isinstance(value.get("previous_markers"), dict)
        and isinstance(value.get("new_markers"), dict)
        and set(value["previous_markers"]) == set(value["assets"])
        and set(value["new_markers"]) == set(value["assets"])
    )
    if not shallow_valid:
        return False
    install_id = str(value["install_id"])
    assets = [str(name) for name in value["assets"]]
    base_after = value.get("base_marker_after")
    if (
        not _valid_base_marker(base_after, base)
        or base_after.get("install_id") != install_id
    ):
        return False
    for name in assets:
        new = value["new_markers"].get(name)
        if not _valid_child_marker(new, base / name, install_id, name) or new.get(
            "source"
        ) != base_after.get("source"):
            return False
        previous = value["previous_markers"].get(name)
        if previous is not None and not _valid_child_marker(
            previous, base / name, install_id, name
        ):
            return False
    if value["prestate"] in {"absent", "empty"}:
        return (
            value.get("base_marker_before") is None
            and all(value["previous_markers"].get(name) is None for name in assets)
            and (
                set(assets) == set(CONDA_NAMES) or set(assets) == set(MANAGED_CHILDREN)
            )
        )
    base_before = value.get("base_marker_before")
    return (
        _valid_base_marker(base_before, base)
        and base_before.get("install_id") == install_id
        and all(
            value["previous_markers"].get(name) is None
            or value["previous_markers"][name].get("source")
            == base_before.get("source")
            for name in assets
        )
    )


def _restore_conda_transaction(
    base: Path, journal_path: Path, journal: Mapping[str, object]
) -> None:
    """Recover the exact-final-prefix installer transaction idempotently."""
    transaction_id = str(journal["transaction_id"])
    transaction = Path(str(journal["canonical_transaction"]))
    if journal["phase"] == "staging":
        prestate = str(journal["prestate"])
        if prestate == "absent":
            unchanged = not os.path.lexists(base)
        elif prestate == "empty":
            unchanged = (
                base.is_dir() and not base.is_symlink() and not any(base.iterdir())
            )
        else:
            state, marker = _classify_base(base)
            unchanged = state == "owned" and marker == journal["base_marker_before"]
            if unchanged and marker is not None:
                unchanged = all(
                    _child_marker_value(base, marker, str(name))
                    == journal["previous_markers"][str(name)]
                    for name in journal["assets"]
                )
        if not unchanged:
            raise OncoTracerError(
                f"Conda target changed during staging recovery: {base}"
            )
        _preserve_staging_transaction(transaction, base, transaction_id, "conda")
        _safe_unlink(journal_path, "Conda transaction journal")
        return
    if journal["phase"] == "committed":
        state, marker = _classify_base(base)
        if state != "owned" or marker != journal["base_marker_after"]:
            raise OncoTracerError(
                f"committed Conda transaction does not match its target: {base}"
            )
        for raw_name in journal["assets"]:
            name = str(raw_name)
            observed = _child_marker_value(base, marker, name)
            if observed != journal["new_markers"][name]:
                raise OncoTracerError(
                    f"committed Conda child is inconsistent: {base / name}"
                )
            if name == "poetry-runtime":
                _verify_poetry_runtime(base / name, observed["source"])
            else:
                _verify_conda_runtime(base / name, name)
            _verify_child_inventory(base / name, observed)
        if os.path.lexists(transaction):
            _require_transaction(transaction, base, transaction_id, "conda")
            backups = _require_transaction_subdir(transaction, "backups")
            claims = _require_transaction_subdir(transaction, "claims")
            empty = _require_transaction_subdir(transaction, "empty")
            discarded = _require_transaction_subdir(transaction, "discarded")
            expected_top_level = {
                ".oncotracer-transaction-owner.json",
                "backups",
                "claims",
                "empty",
                "discarded",
            }
            observed_top_level = {path.name for path in transaction.iterdir()}
            if (
                "poetry-runtime" in journal["assets"]
                and "poetry-state" in observed_top_level
            ):
                _require_transaction_subdir(transaction, "poetry-state")
                expected_top_level.add("poetry-state")
            if observed_top_level != expected_top_level:
                raise OncoTracerError(
                    "Conda transaction contains unexpected entries and will be "
                    f"preserved: {transaction}"
                )
            if any(empty.iterdir()) or any(discarded.iterdir()):
                raise OncoTracerError(
                    "committed Conda transaction has unexpected pending entries; "
                    f"preserving it: {transaction}"
                )
            base_before = journal.get("base_marker_before")
            expected_backups = {
                str(name)
                for name in journal["assets"]
                if journal["previous_markers"][str(name)] is not None
            }
            if {path.name for path in backups.iterdir()} != expected_backups:
                raise OncoTracerError(
                    f"Conda backup set changed before cleanup: {backups}"
                )
            expected_claims = {f"{name}.json" for name in journal["assets"]}
            if {path.name for path in claims.iterdir()} != expected_claims:
                raise OncoTracerError(
                    f"Conda target-claim set changed before cleanup: {claims}"
                )
            for raw_name in journal["assets"]:
                name = str(raw_name)
                backup = backups / name
                previous = journal["previous_markers"][name]
                claim = _read_target_claim(
                    transaction, transaction_id, name, base / name
                )
                if claim.get("marker") != journal["new_markers"][name]:
                    raise OncoTracerError(
                        f"Conda final-prefix claim changed before cleanup: {base / name}"
                    )
                _assert_claimed_identity(transaction, transaction_id, name, base / name)
                if previous is None:
                    continue
                if not isinstance(base_before, Mapping):
                    raise OncoTracerError(
                        "committed Conda transaction lacks its prior base ownership"
                    )
                observed_backup = _child_marker_at(
                    backup, base / name, base_before, name
                )
                if observed_backup != previous:
                    raise OncoTracerError(
                        f"Conda backup changed before cleanup; preserving it: {backup}"
                    )
            _remove_transaction(transaction, base, transaction_id, "conda")
        _safe_unlink(journal_path, "Conda transaction journal")
        return

    _require_transaction(transaction, base, transaction_id, "conda")
    backups = _require_transaction_subdir(transaction, "backups")
    _require_transaction_subdir(transaction, "claims")
    discarded = _require_transaction_subdir(transaction, "discarded", create=True)
    prestate = str(journal["prestate"])
    base_before = journal.get("base_marker_before")
    base_after = journal["base_marker_after"]
    marker_for_validation = base_before if base_before is not None else base_after
    assert isinstance(marker_for_validation, Mapping)

    for raw_name in reversed(journal["assets"]):
        name = str(raw_name)
        target = base / name
        backup = backups / name
        previous = journal["previous_markers"][name]
        if os.path.lexists(backup):
            metadata = backup.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_dev != transaction.lstat().st_dev
            ):
                raise OncoTracerError(
                    f"Conda rollback backup is not a physical directory: {backup}"
                )
            observed_backup = _child_marker_at(
                backup, target, marker_for_validation, name
            )
            if previous is None or observed_backup != previous:
                raise OncoTracerError(
                    f"Conda rollback backup is inconsistent: {backup}"
                )
            if os.path.lexists(target):
                _discard_claimed_target(
                    transaction,
                    transaction_id,
                    name,
                    target,
                    discarded,
                    base_after,
                )
            os.replace(backup, target)
            continue

        if previous is not None:
            # A crash immediately after backup -> target is already restored.
            observed_target = _child_marker_at(
                target, target, marker_for_validation, name
            )
            if observed_target == previous:
                continue
            raise OncoTracerError(
                f"Conda rollback lost or changed the prior target: {target}"
            )
        if os.path.lexists(target):
            _discard_claimed_target(
                transaction,
                transaction_id,
                name,
                target,
                discarded,
                base_after,
            )

    marker_path = base / BASE_MARKER
    if prestate == "owned":
        if not _valid_base_marker(base_before, base):
            raise OncoTracerError("Conda rollback has an invalid prior base marker")
        _atomic_write_json(marker_path, base_before)
    else:
        if os.path.lexists(marker_path):
            observed = _safe_read_json(marker_path, "Conda root ownership marker")
            if observed != base_after:
                raise OncoTracerError(
                    f"Conda rollback refuses a changed root marker: {marker_path}"
                )
            _safe_unlink(marker_path, "Conda root ownership marker")
        if prestate == "absent" and base.is_dir() and not any(base.iterdir()):
            base.rmdir()
    _remove_transaction(transaction, base, transaction_id, "conda")
    _safe_unlink(journal_path, "Conda transaction journal")


def _recover_conda_journal(base: Path) -> None:
    journal_path = _journal_path(base, "conda")
    if not os.path.lexists(journal_path):
        return
    journal = _safe_read_json(journal_path, "Conda transaction journal")
    if not _valid_conda_journal(journal, base):
        raise OncoTracerError(
            f"refusing malformed or foreign Conda transaction journal: {journal_path}"
        )
    _restore_conda_transaction(base, journal_path, journal)


def _dry_run_conda_plan(
    base: Path,
    root: Path,
    conda: str,
    *,
    include_poetry: bool,
    poetry: str | None,
) -> None:
    state, marker = _classify_base(base)
    definitions = {
        "core": root / "environments" / "native-core.yml",
        "qdnaseq": root / "environments" / "native-qdnaseq.yml",
        "ichorcna": root / "environments" / "native-ichorcna.yml",
        "classifier": root / "environments" / "native-classifier.yml",
        "gistic": root / "environments" / "native-gistic2.yml",
    }
    for name, definition in definitions.items():
        if not definition.is_file():
            raise OncoTracerError(
                f"native {name} environment definition is missing: {definition}"
            )
        if state == "owned" and marker is not None:
            _child_marker_value(base, marker, name)
        _run_checked(
            [conda, "env", "create", "--prefix", base / name, "--file", definition],
            dry_run=True,
        )
    if include_poetry:
        assert poetry is not None
        _run_checked([poetry, "--version"], dry_run=True)
        _run_checked(
            [
                poetry,
                "build",
                "--format",
                "wheel",
                "--output",
                "<isolated-wheel-dir>",
                "--no-interaction",
            ],
            cwd=Path("<isolated-source-tree>"),
            dry_run=True,
        )
        _run_checked(
            [sys.executable, "-m", "venv", base / "poetry-runtime"], dry_run=True
        )
        _run_checked(
            [
                base / "poetry-runtime" / "bin" / "python",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "<isolated-built-wheel>",
            ],
            dry_run=True,
        )


def _same_child_spec(
    observed: Mapping[str, object], expected: Mapping[str, object], name: str
) -> bool:
    ignored = {"inventory_sha256"}
    if name == "poetry-runtime":
        ignored.add("launcher_sha256")
    return {key: value for key, value in observed.items() if key not in ignored} == {
        key: value for key, value in expected.items() if key not in ignored
    }


def _assert_claimed_identity(
    transaction: Path, transaction_id: str, name: str, target: Path
) -> None:
    claim = _read_target_claim(transaction, transaction_id, name, target)
    metadata = target.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (claim["device"], claim["inode"])
    ):
        raise OncoTracerError(
            f"installer-created final prefix changed identity: {target}"
        )


def install_conda_managed(
    root: Path,
    base: Path,
    *,
    conda: str,
    force: bool,
    dry_run: bool,
    poetry: str | None = None,
) -> dict[str, Path]:
    """Install directly at final prefixes with owned transactional backups."""
    base = _guard_dedicated(base, "Conda install root")
    root = _absolute(root)
    include_poetry = poetry is not None
    pending_journal = _journal_path(base, "conda")
    if os.path.lexists(pending_journal):
        pending = _safe_read_json(pending_journal, "Conda transaction journal")
        if not _valid_conda_journal(pending, base):
            raise OncoTracerError(
                f"refusing malformed or foreign Conda transaction journal: {pending_journal}"
            )
        if dry_run:
            raise OncoTracerError(
                "Conda dry-run cannot recover a pending owned transaction; rerun "
                f"without --dry-run to recover it first: {pending_journal}"
            )
        initial_state = "pending"
    else:
        initial_state, _ = _classify_base(base)
    if not dry_run and initial_state == "empty":
        _assert_inactive(base)
    if dry_run:
        _dry_run_conda_plan(
            base, root, conda, include_poetry=include_poetry, poetry=poetry
        )
        return {name: base / name for name in CONDA_NAMES}

    source = _source_identity()
    project = root / "pyproject.toml"
    lock = root / "poetry.lock"
    if include_poetry:
        if not project.is_file() or not lock.is_file():
            raise OncoTracerError(
                "Poetry installation requires pyproject.toml and poetry.lock"
            )
        assert poetry is not None
        _require_poetry_v2(poetry)
        _verify_poetry_source_checkout(root, source)
    base.parent.mkdir(parents=True, exist_ok=True)
    base = _guard_dedicated(base, "Conda install root")
    with _exclusive_install_lock(_lock_path(base, "conda"), base, "conda"):
        base = _guard_dedicated(base, "Conda install root")
        _recover_conda_journal(base)
        state, observed_base_marker = _classify_base(base)
        install_id = (
            str(observed_base_marker["install_id"])
            if observed_base_marker is not None
            else uuid.uuid4().hex
        )
        expected_base_marker = _base_marker(base, install_id, source)
        if (
            state == "owned"
            and not include_poetry
            and os.path.lexists(base / "poetry-runtime")
            and observed_base_marker is not None
            and observed_base_marker.get("source") != source
        ):
            raise OncoTracerError(
                "this owned prefix includes a Poetry runtime from another "
                "OncoTracer source; update it with install --poetry or select a "
                "new dedicated Conda --prefix"
            )
        definitions = {
            "core": root / "environments" / "native-core.yml",
            "qdnaseq": root / "environments" / "native-qdnaseq.yml",
            "ichorcna": root / "environments" / "native-ichorcna.yml",
            "classifier": root / "environments" / "native-classifier.yml",
            "gistic": root / "environments" / "native-gistic2.yml",
        }
        for name, definition in definitions.items():
            if not definition.is_file() or definition.stat().st_size == 0:
                raise OncoTracerError(
                    f"native {name} environment definition is missing or empty: {definition}"
                )
        names = [*CONDA_NAMES, *(["poetry-runtime"] if include_poetry else [])]
        previous: dict[str, dict[str, object] | None] = {}
        expected: dict[str, dict[str, object]] = {}
        changed: list[str] = []
        for name in names:
            prior = (
                _child_marker_value(base, observed_base_marker, name)
                if state == "owned" and observed_base_marker is not None
                else None
            )
            previous[name] = prior
            prior_inventory = (
                str(prior["inventory_sha256"]) if prior is not None else "0" * 64
            )
            if name == "poetry-runtime":
                planned = _poetry_marker(
                    base / name,
                    install_id,
                    sha256_file(project),
                    sha256_file(lock),
                    str(prior["launcher_sha256"]) if prior else "0" * 64,
                    source,
                    prior_inventory,
                )
            else:
                planned = _environment_marker(
                    base / name,
                    install_id,
                    name,
                    sha256_file(definitions[name]),
                    source,
                    prior_inventory,
                )
            expected[name] = planned
            complete = (
                _poetry_complete(base / name)
                if name == "poetry-runtime"
                else _conda_complete(base / name)
            )
            needs_change = (
                force
                or prior is None
                or not complete
                or not _same_child_spec(prior, planned, name)
            )
            if needs_change:
                changed.append(name)
            else:
                if name == "poetry-runtime":
                    _verify_poetry_runtime(base / name, source)
                else:
                    _verify_conda_runtime(base / name, name)
                _verify_child_inventory(base / name, prior)
                expected[name] = prior

        if not changed:
            if observed_base_marker != expected_base_marker:
                _atomic_write_json(base / BASE_MARKER, expected_base_marker)
            return {name: base / name for name in CONDA_NAMES}
        if state == "owned":
            _assert_inactive(base)

        transaction_id = uuid.uuid4().hex
        transaction = _transaction_path(base, "conda", transaction_id)
        journal_path = _journal_path(base, "conda")
        journal: dict[str, object] = {
            "schema": TRANSACTION_SCHEMA,
            "kind": "conda",
            "phase": "staging",
            "transaction_id": transaction_id,
            "install_id": install_id,
            "canonical_target": str(base),
            "canonical_transaction": str(transaction),
            "prestate": state,
            "assets": changed,
            "previous_markers": {name: previous[name] for name in changed},
            "new_markers": {name: expected[name] for name in changed},
            "base_marker_before": observed_base_marker,
            "base_marker_after": expected_base_marker,
        }
        # The durable journal precedes transaction creation, package-manager
        # execution, and every target mutation.
        _write_journal(journal_path, journal)
        try:
            transaction = _create_transaction(base, "conda", transaction_id)
            backups = _require_transaction_subdir(transaction, "backups", create=True)
            _require_transaction_subdir(transaction, "claims", create=True)
            empty = _require_transaction_subdir(transaction, "empty", create=True)
            _require_transaction_subdir(transaction, "discarded", create=True)
            for name in changed:
                placeholder = empty / name
                placeholder.mkdir(mode=0o700)
                _write_target_claim(
                    transaction,
                    transaction_id,
                    name,
                    base / name,
                    storage=placeholder,
                )
            journal = {**journal, "phase": "publishing"}
            _write_journal(journal_path, journal)
            if state == "absent":
                base.mkdir(mode=0o700)
            elif not base.is_dir() or base.is_symlink():
                raise OncoTracerError(f"Conda root changed before installation: {base}")
            _atomic_write_json(base / BASE_MARKER, expected_base_marker)

            for name in changed:
                target = base / name
                backup = backups / name
                if previous[name] is not None:
                    _assert_inactive(target)
                    observed_before_backup = _child_marker_value(
                        base, observed_base_marker, name
                    )
                    if observed_before_backup != previous[name]:
                        raise OncoTracerError(
                            f"managed prefix changed before backup: {target}"
                        )
                    target_identity = target.lstat()
                    os.replace(target, backup)
                    backup_identity = backup.lstat()
                    if (backup_identity.st_dev, backup_identity.st_ino) != (
                        target_identity.st_dev,
                        target_identity.st_ino,
                    ):
                        raise OncoTracerError(
                            f"managed prefix identity changed during backup: {target}"
                        )
                    observed_backup = _child_marker_at(
                        backup, target, observed_base_marker, name
                    )
                    if observed_backup != previous[name]:
                        raise OncoTracerError(
                            f"managed prefix changed during backup: {target}"
                        )
                elif os.path.lexists(target):
                    raise OncoTracerError(
                        f"new managed prefix appeared before creation: {target}"
                    )
                os.replace(empty / name, target)
                _assert_claimed_identity(transaction, transaction_id, name, target)
                try:
                    if name == "poetry-runtime":
                        assert poetry is not None
                        wheel = _build_poetry_wheel(root, transaction, poetry, source)
                        venv.EnvBuilder(with_pip=True, symlinks=False).create(target)
                        _assert_claimed_identity(
                            transaction, transaction_id, name, target
                        )
                        _pip_install_poetry_wheel(target, wheel, transaction)
                        _assert_claimed_identity(
                            transaction, transaction_id, name, target
                        )
                        if not _poetry_complete(target):
                            raise OncoTracerError(
                                f"Poetry runtime is incomplete: {target}"
                            )
                        _verify_poetry_runtime(target, source)
                        inventory_sha256 = _write_child_inventory(target)
                        final_marker = _poetry_marker(
                            target,
                            install_id,
                            sha256_file(project),
                            sha256_file(lock),
                            sha256_file(target / "bin" / "oncotracer"),
                            source,
                            inventory_sha256,
                        )
                        marker_name = POETRY_MARKER
                    else:
                        _run_checked(
                            [
                                conda,
                                "env",
                                "create",
                                "--prefix",
                                target,
                                "--file",
                                definitions[name],
                            ]
                        )
                        _assert_claimed_identity(
                            transaction, transaction_id, name, target
                        )
                        if not _conda_complete(target):
                            raise OncoTracerError(
                                f"Conda prefix lacks conda-meta/history: {target}"
                            )
                        _verify_conda_runtime(target, name)
                        inventory_sha256 = _write_child_inventory(target)
                        final_marker = _environment_marker(
                            target,
                            install_id,
                            name,
                            sha256_file(definitions[name]),
                            source,
                            inventory_sha256,
                        )
                        marker_name = ENV_MARKER
                    _atomic_write_json(target / marker_name, final_marker)
                    _assert_claimed_identity(transaction, transaction_id, name, target)
                    _write_target_claim(
                        transaction,
                        transaction_id,
                        name,
                        target,
                        marker=final_marker,
                    )
                    expected[name] = final_marker
                except BaseException:
                    _assert_claimed_identity(transaction, transaction_id, name, target)
                    claim = _read_target_claim(
                        transaction, transaction_id, name, target
                    )
                    if claim.get("marker") is None:
                        _write_target_claim(
                            transaction,
                            transaction_id,
                            name,
                            target,
                            partial_inventory=_tree_inventory(
                                target, include_installer_metadata=True
                            ),
                        )
                    raise

            journal = {**journal, "new_markers": {n: expected[n] for n in changed}}
            _write_journal(journal_path, journal)
            state_after, marker_after = _classify_base(base)
            if state_after != "owned" or marker_after != expected_base_marker:
                raise OncoTracerError(
                    f"Conda root verification failed after installation: {base}"
                )
            for name in names:
                observed = _child_marker_value(base, marker_after, name)
                if observed != expected[name]:
                    raise OncoTracerError(
                        f"managed installation verification failed: {base / name}"
                    )
                if name == "poetry-runtime":
                    _verify_poetry_runtime(base / name, source)
                else:
                    _verify_conda_runtime(base / name, name)
                _verify_child_inventory(base / name, observed)
            committed = {**journal, "phase": "committed"}
            _write_journal(journal_path, committed)
            journal = committed
            _restore_conda_transaction(base, journal_path, journal)
        except BaseException:
            try:
                _restore_conda_transaction(base, journal_path, journal)
            except Exception as rollback_error:
                raise OncoTracerError(
                    "Conda installation failed and automatic rollback could not "
                    f"complete; preserve the journal for recovery: {journal_path}: {rollback_error}"
                ) from rollback_error
            raise
    return {name: base / name for name in CONDA_NAMES}


def _sif_sidecar(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.oncotracer.json")


def _sif_marker(
    destination: Path,
    install_id: str,
    image: str,
    digest: str,
    source: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema": SIF_SCHEMA,
        "object_type": "singularity-image",
        "install_id": install_id,
        "canonical_path": str(destination),
        "image": image,
        "sif_sha256": digest,
        "source": dict(source),
    }


def _valid_sif_marker(value: object, destination: Path) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "schema",
            "object_type",
            "install_id",
            "canonical_path",
            "image",
            "sif_sha256",
            "source",
        }
        and value.get("schema") == SIF_SCHEMA
        and value.get("object_type") == "singularity-image"
        and isinstance(value.get("install_id"), str)
        and bool(_HEX_32.fullmatch(str(value["install_id"])))
        and value.get("canonical_path") == str(destination)
        and isinstance(value.get("image"), str)
        and bool(value["image"])
        and isinstance(value.get("sif_sha256"), str)
        and bool(_HEX_64.fullmatch(str(value["sif_sha256"])))
        and _valid_source(value.get("source"))
    )


def _classify_sif(destination: Path) -> tuple[str, dict[str, object] | None]:
    sidecar = _sif_sidecar(destination)
    destination_exists = os.path.lexists(destination)
    sidecar_exists = os.path.lexists(sidecar)
    if not destination_exists and not sidecar_exists:
        return "absent", None
    if destination_exists != sidecar_exists:
        raise OncoTracerError(
            f"refusing incomplete or unowned SIF installation pair: {destination}, {sidecar}"
        )
    metadata = destination.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size == 0
    ):
        raise OncoTracerError(
            f"SIF target must be one nonempty, non-hardlinked regular file: {destination}"
        )
    marker = _safe_read_json(sidecar, "SIF ownership sidecar")
    if not _valid_sif_marker(marker, destination):
        raise OncoTracerError(
            f"SIF sidecar is malformed, foreign, or path-mismatched: {sidecar}"
        )
    if sha256_file(destination) != marker["sif_sha256"]:
        raise OncoTracerError(
            f"SIF bytes do not match the strict ownership sidecar: {destination}"
        )
    return "owned", marker


def _parse_json_output(
    completed: subprocess.CompletedProcess[str], label: str
) -> dict[str, object]:
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise OncoTracerError(f"{label} did not emit valid JSON") from error
    if not isinstance(value, dict):
        raise OncoTracerError(f"{label} JSON is not an object")
    return value


def _verify_sif_runtime(
    executable: str,
    sif: Path,
    expected_source: Mapping[str, object],
) -> None:
    doctor = _run_checked(
        [executable, "exec", sif, "oncotracer", "doctor", "--backend", "host"]
    )
    provenance = _run_checked(
        [executable, "exec", sif, "oncotracer", "provenance", "--json"]
    )
    assert doctor is not None and provenance is not None
    doctor_record = _parse_json_output(doctor, "container doctor")
    provenance_record = _parse_json_output(provenance, "container provenance")
    if doctor_record.get("success") is not True:
        raise OncoTracerError("staged SIF failed the native host doctor")
    for key in ("oncotracer_version", "source_commit", "source_sha256"):
        if provenance_record.get(key) != expected_source.get(key):
            raise OncoTracerError(
                f"staged SIF provenance {key} does not match this executable"
            )
    if provenance_record.get("source_tree_dirty") is not False:
        raise OncoTracerError("staged SIF provenance is not a clean source tree")


def _valid_sif_journal(value: object, destination: Path) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "kind",
        "phase",
        "transaction_id",
        "install_id",
        "canonical_target",
        "canonical_transaction",
        "prestate",
        "old_marker",
        "new_marker",
    }:
        return False
    transaction_id = value.get("transaction_id")
    transaction = (
        destination.parent / f".{destination.name}.oncotracer-sif-txn-{transaction_id}"
    )
    shallow_valid = (
        value.get("schema") == TRANSACTION_SCHEMA
        and value.get("kind") == "sif"
        and value.get("phase") in {"staging", "publishing", "committed"}
        and isinstance(transaction_id, str)
        and bool(_HEX_32.fullmatch(transaction_id))
        and isinstance(value.get("install_id"), str)
        and bool(_HEX_32.fullmatch(str(value["install_id"])))
        and value.get("canonical_target") == str(destination)
        and value.get("canonical_transaction") == str(transaction)
        and value.get("prestate") in {"absent", "owned"}
        and (
            value.get("old_marker") is None
            or _valid_sif_marker(value["old_marker"], destination)
        )
        and (
            (value.get("phase") == "staging" and value.get("new_marker") is None)
            or (
                value.get("phase") in {"publishing", "committed"}
                and _valid_sif_marker(value.get("new_marker"), destination)
            )
        )
    )
    if not shallow_valid:
        return False
    install_id = str(value["install_id"])
    new_marker = value.get("new_marker")
    old_marker = value.get("old_marker")
    return (new_marker is None or new_marker.get("install_id") == install_id) and (
        (value["prestate"] == "absent" and old_marker is None)
        or (
            value["prestate"] == "owned"
            and old_marker is not None
            and old_marker.get("install_id") == install_id
        )
    )


def _require_sif_bytes(path: Path, digest: object, label: str) -> None:
    if not isinstance(digest, str) or not _HEX_64.fullmatch(digest):
        raise OncoTracerError(f"{label} has no valid expected SHA-256")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise OncoTracerError(f"could not inspect {label} {path}: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size == 0
        or sha256_file(path) != digest
    ):
        raise OncoTracerError(f"{label} bytes are unsafe or inconsistent: {path}")


def _restore_sif_transaction(
    destination: Path, journal_path: Path, journal: Mapping[str, object]
) -> None:
    transaction_id = str(journal["transaction_id"])
    transaction = Path(str(journal["canonical_transaction"]))
    sidecar = _sif_sidecar(destination)
    if journal["phase"] == "staging":
        if journal["prestate"] == "absent":
            unchanged = not os.path.lexists(destination) and not os.path.lexists(
                sidecar
            )
        else:
            state, marker = _classify_sif(destination)
            unchanged = state == "owned" and marker == journal["old_marker"]
        if not unchanged:
            raise OncoTracerError(
                f"SIF target changed during staging recovery: {destination}"
            )
        _preserve_staging_transaction(transaction, destination, transaction_id, "sif")
        _safe_unlink(journal_path, "SIF transaction journal")
        return
    if journal["phase"] == "committed":
        state, marker = _classify_sif(destination)
        if state != "owned" or marker != journal["new_marker"]:
            raise OncoTracerError(
                f"committed SIF transaction does not match its target: {destination}"
            )
        if os.path.lexists(transaction):
            _require_transaction(transaction, destination, transaction_id, "sif")
            owner_name = ".oncotracer-transaction-owner.json"
            expected_entries = {owner_name}
            old_marker = journal.get("old_marker")
            if old_marker is not None:
                expected_entries.update({"backup.sif", "backup.sidecar.json"})
                _require_sif_bytes(
                    transaction / "backup.sif",
                    old_marker["sif_sha256"],
                    "committed SIF rollback backup",
                )
                observed_backup_sidecar = _safe_read_json(
                    transaction / "backup.sidecar.json",
                    "committed SIF rollback backup sidecar",
                )
                if observed_backup_sidecar != old_marker:
                    raise OncoTracerError(
                        "SIF backup changed before cleanup; preserving its transaction"
                    )
            observed_entries = {path.name for path in transaction.iterdir()}
            if observed_entries != expected_entries:
                raise OncoTracerError(
                    "SIF transaction contains unexpected entries and will be preserved: "
                    f"{transaction}"
                )
            _remove_transaction(transaction, destination, transaction_id, "sif")
        _safe_unlink(journal_path, "SIF transaction journal")
        return

    _require_transaction(transaction, destination, transaction_id, "sif")

    backup_sif = transaction / "backup.sif"
    backup_sidecar = transaction / "backup.sidecar.json"
    candidate_sif = transaction / "candidate.sif"
    candidate_sidecar = transaction / "candidate.sidecar.json"
    discarded_sif = transaction / "discarded.sif"
    discarded_sidecar = transaction / "discarded.sidecar.json"
    old_marker = journal["old_marker"]
    new_marker = journal["new_marker"]
    if old_marker is not None:
        # Restore each component independently. Publication intentionally moves
        # the old SIF and sidecar in separate atomic operations; a crash between
        # those operations must not make the still-present component look lost.
        if os.path.lexists(backup_sif):
            _require_sif_bytes(
                backup_sif, old_marker["sif_sha256"], "SIF rollback backup"
            )
            if os.path.lexists(destination):
                _require_sif_bytes(
                    destination, new_marker["sif_sha256"], "new SIF rollback target"
                )
                _assert_inactive(destination)
                os.replace(destination, discarded_sif)
            os.replace(backup_sif, destination)
        else:
            _require_sif_bytes(destination, old_marker["sif_sha256"], "prior owned SIF")

        if os.path.lexists(backup_sidecar):
            observed_backup = _safe_read_json(
                backup_sidecar, "SIF rollback backup sidecar"
            )
            if observed_backup != old_marker:
                raise OncoTracerError(
                    "SIF rollback backup sidecar does not match its journal"
                )
            if os.path.lexists(sidecar):
                observed_target = _safe_read_json(
                    sidecar, "new SIF rollback target sidecar"
                )
                if observed_target != new_marker:
                    raise OncoTracerError(
                        f"SIF rollback refuses an unexpectedly changed sidecar: {sidecar}"
                    )
                os.replace(sidecar, discarded_sidecar)
            os.replace(backup_sidecar, sidecar)
        else:
            observed_sidecar = _safe_read_json(sidecar, "prior owned SIF sidecar")
            if observed_sidecar != old_marker:
                raise OncoTracerError("SIF rollback lost the prior owned sidecar")
    else:
        # A fresh install rolls back to absence. A component can only have been
        # published if its same-directory candidate has disappeared.
        if os.path.lexists(destination):
            if os.path.lexists(candidate_sif):
                raise OncoTracerError(
                    f"SIF rollback refuses a foreign target that appeared: {destination}"
                )
            _require_sif_bytes(
                destination, new_marker["sif_sha256"], "new SIF rollback target"
            )
            _assert_inactive(destination)
            os.replace(destination, discarded_sif)
        if os.path.lexists(sidecar):
            if os.path.lexists(candidate_sidecar):
                raise OncoTracerError(
                    f"SIF rollback refuses a foreign sidecar that appeared: {sidecar}"
                )
            observed_target = _safe_read_json(
                sidecar, "new SIF rollback target sidecar"
            )
            if observed_target != new_marker:
                raise OncoTracerError(
                    f"SIF rollback refuses an unexpectedly changed sidecar: {sidecar}"
                )
            os.replace(sidecar, discarded_sidecar)
    _remove_transaction(transaction, destination, transaction_id, "sif")
    _safe_unlink(journal_path, "SIF transaction journal")


def _recover_sif_journal(destination: Path) -> None:
    journal_path = _journal_path(destination, "sif")
    if not os.path.lexists(journal_path):
        return
    journal = _safe_read_json(journal_path, "SIF transaction journal")
    if not _valid_sif_journal(journal, destination):
        raise OncoTracerError(
            f"refusing malformed or foreign SIF transaction journal: {journal_path}"
        )
    _restore_sif_transaction(destination, journal_path, journal)


def install_sif_managed(
    destination: Path,
    *,
    executable: str,
    image: str,
    force: bool,
    dry_run: bool,
) -> dict[str, object]:
    destination = _guard_dedicated(destination, "SIF destination")
    journal_path = _journal_path(destination, "sif")
    if os.path.lexists(journal_path):
        pending = _safe_read_json(journal_path, "SIF transaction journal")
        if not _valid_sif_journal(pending, destination):
            raise OncoTracerError(
                f"refusing malformed or foreign SIF transaction journal: {journal_path}"
            )
        if dry_run:
            raise OncoTracerError(
                "SIF dry-run cannot recover a pending owned transaction; rerun "
                f"without --dry-run to recover it first: {journal_path}"
            )
    else:
        # An incomplete pair is normally unowned and must fail immediately. A
        # strict pending journal is the sole exception because a process crash
        # can occur between the two same-directory publication renames.
        _classify_sif(destination)
    if dry_run:
        _run_checked(
            [executable, "pull", "<same-directory-staged-sif>", f"docker://{image}"],
            dry_run=True,
        )
        _run_checked(
            [
                executable,
                "exec",
                "<same-directory-staged-sif>",
                "oncotracer",
                "doctor",
                "--backend",
                "host",
            ],
            dry_run=True,
        )
        _run_checked(
            [
                executable,
                "exec",
                "<same-directory-staged-sif>",
                "oncotracer",
                "provenance",
                "--json",
            ],
            dry_run=True,
        )
        return {"sif": str(destination), "image": image}

    source = _source_identity()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _guard_dedicated(destination, "SIF destination")
    with _exclusive_install_lock(_lock_path(destination, "sif"), destination, "sif"):
        destination = _guard_dedicated(destination, "SIF destination")
        _recover_sif_journal(destination)
        state, old_marker = _classify_sif(destination)
        if state == "owned" and old_marker is not None and not force:
            if old_marker.get("image") != image or old_marker.get("source") != source:
                raise OncoTracerError(
                    "owned SIF belongs to a different image or OncoTracer source; "
                    "use --force for an ownership-verified staged replacement"
                )
            _verify_sif_runtime(executable, destination, source)
            return {"sif": str(destination), "image": image}
        if destination.exists():
            _assert_inactive(destination)
        if _sif_sidecar(destination).exists():
            _assert_inactive(_sif_sidecar(destination))

        install_id = (
            str(old_marker["install_id"])
            if old_marker is not None
            else uuid.uuid4().hex
        )
        transaction_id = uuid.uuid4().hex
        transaction = _transaction_path(destination, "sif", transaction_id)
        journal: dict[str, object] = {
            "schema": TRANSACTION_SCHEMA,
            "kind": "sif",
            "phase": "staging",
            "transaction_id": transaction_id,
            "install_id": install_id,
            "canonical_target": str(destination),
            "canonical_transaction": str(transaction),
            "prestate": state,
            "old_marker": old_marker,
            "new_marker": None,
        }
        _write_journal(journal_path, journal)
        try:
            transaction = _create_transaction(destination, "sif", transaction_id)
            candidate = transaction / "candidate.sif"
            candidate_sidecar = transaction / "candidate.sidecar.json"
            _run_checked([executable, "pull", candidate, f"docker://{image}"])
            metadata = candidate.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size == 0
            ):
                raise OncoTracerError(
                    f"staged SIF is not one nonempty regular file: {candidate}"
                )
            _verify_sif_runtime(executable, candidate, source)
            new_marker = _sif_marker(
                destination, install_id, image, sha256_file(candidate), source
            )
            _atomic_write_json(candidate_sidecar, new_marker)
            journal = {**journal, "phase": "publishing", "new_marker": new_marker}
            _write_journal(journal_path, journal)
            sidecar = _sif_sidecar(destination)
            if state == "owned":
                _assert_inactive(destination)
                _assert_inactive(sidecar)
                current_state, current_marker = _classify_sif(destination)
                if current_state != "owned" or current_marker != old_marker:
                    raise OncoTracerError(
                        f"owned SIF changed before backup: {destination}"
                    )
                destination_identity = destination.lstat()
                sidecar_identity = sidecar.lstat()
                os.replace(destination, transaction / "backup.sif")
                backup_identity = (transaction / "backup.sif").lstat()
                if (backup_identity.st_dev, backup_identity.st_ino) != (
                    destination_identity.st_dev,
                    destination_identity.st_ino,
                ):
                    raise OncoTracerError(
                        f"owned SIF identity changed during backup: {destination}"
                    )
                _require_sif_bytes(
                    transaction / "backup.sif",
                    old_marker["sif_sha256"],
                    "SIF rollback backup",
                )
                observed_sidecar = _safe_read_json(
                    sidecar, "owned SIF sidecar before backup"
                )
                if observed_sidecar != old_marker:
                    raise OncoTracerError(
                        f"owned SIF sidecar changed before backup: {sidecar}"
                    )
                os.replace(sidecar, transaction / "backup.sidecar.json")
                backup_sidecar_identity = (transaction / "backup.sidecar.json").lstat()
                if (
                    backup_sidecar_identity.st_dev,
                    backup_sidecar_identity.st_ino,
                ) != (sidecar_identity.st_dev, sidecar_identity.st_ino):
                    raise OncoTracerError(
                        f"owned SIF sidecar identity changed during backup: {sidecar}"
                    )
                if (
                    _safe_read_json(
                        transaction / "backup.sidecar.json",
                        "SIF rollback backup sidecar",
                    )
                    != old_marker
                ):
                    raise OncoTracerError(
                        f"owned SIF sidecar changed during backup: {sidecar}"
                    )
            elif destination.exists() or sidecar.exists():
                raise OncoTracerError(
                    f"absent SIF target changed before publication: {destination}"
                )
            os.replace(candidate, destination)
            os.replace(candidate_sidecar, sidecar)
            state_after, marker_after = _classify_sif(destination)
            if state_after != "owned" or marker_after != new_marker:
                raise OncoTracerError(
                    f"SIF verification failed after atomic publication: {destination}"
                )
            committed_journal = dict(journal)
            committed_journal["phase"] = "committed"
            _write_journal(journal_path, committed_journal)
            journal = committed_journal
            _restore_sif_transaction(destination, journal_path, journal)
        except BaseException:
            try:
                _restore_sif_transaction(destination, journal_path, journal)
            except Exception as rollback_error:
                raise OncoTracerError(
                    "SIF installation failed and automatic rollback could not "
                    f"complete; preserve the journal for recovery: {journal_path}: {rollback_error}"
                ) from rollback_error
            raise
    return {"sif": str(destination), "image": image}


@contextlib.contextmanager
def managed_conda_runtime_lock(
    base: Path, *, require_poetry: bool, semantic: bool = False
) -> Iterator[dict[str, Path]]:
    """Authenticate a managed runtime and hold a shared consumer lock."""
    base = _guard_dedicated(base, "managed Conda runtime")
    with _shared_install_lock(_lock_path(base, "conda"), base, "conda"):
        if os.path.lexists(_journal_path(base, "conda")):
            raise OncoTracerError(
                f"managed runtime has an interrupted installer transaction: {base}"
            )
        state, marker = _classify_base(base)
        if state != "owned" or marker is None:
            raise OncoTracerError(
                f"Conda runtime is not strictly installer-owned: {base}"
            )
        source = _source_identity()
        if marker.get("source") != source:
            raise OncoTracerError(
                f"managed runtime source identity differs from this executable: {base}"
            )
        names = [*CONDA_NAMES, *(["poetry-runtime"] if require_poetry else [])]
        paths: dict[str, Path] = {}
        for name in names:
            child = base / name
            observed = _child_marker_value(base, marker, name)
            if observed is None:
                raise OncoTracerError(f"managed runtime child is missing: {child}")
            _verify_child_inventory(child, observed)
            if semantic:
                if name == "poetry-runtime":
                    _verify_poetry_runtime(child, source)
                else:
                    _verify_conda_runtime(child, name)
            paths[name] = child
        yield paths


def verify_managed_conda_runtime(
    base: Path, *, require_poetry: bool
) -> dict[str, Path]:
    with managed_conda_runtime_lock(
        base, require_poetry=require_poetry, semantic=True
    ) as paths:
        return dict(paths)


@contextlib.contextmanager
def managed_sif_runtime_lock(
    destination: Path, *, executable: str, semantic: bool = False
) -> Iterator[dict[str, object]]:
    """Authenticate a managed SIF pair and hold its shared consumer lock."""
    destination = _guard_dedicated(destination, "managed SIF runtime")
    with _shared_install_lock(_lock_path(destination, "sif"), destination, "sif"):
        if os.path.lexists(_journal_path(destination, "sif")):
            raise OncoTracerError(
                f"managed SIF has an interrupted installer transaction: {destination}"
            )
        state, marker = _classify_sif(destination)
        if state != "owned" or marker is None:
            raise OncoTracerError(f"SIF is not strictly installer-owned: {destination}")
        source = _source_identity()
        if marker.get("source") != source:
            raise OncoTracerError(
                f"managed SIF source identity differs from this executable: {destination}"
            )
        if semantic:
            _verify_sif_runtime(executable, destination, source)
        yield marker


def verify_managed_sif_runtime(
    destination: Path, *, executable: str
) -> dict[str, object]:
    with managed_sif_runtime_lock(
        destination, executable=executable, semantic=True
    ) as marker:
        return dict(marker)
