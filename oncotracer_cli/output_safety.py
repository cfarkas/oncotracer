"""Fail-closed ownership and concurrency controls for native result trees."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import socket
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from . import __version__
from ._build_metadata import SOURCE_COMMIT, SOURCE_SHA256, SOURCE_TREE_DIRTY
from .runtime import OncoTracerError, atomic_write_json, utc_now


OUTPUT_OWNER_SCHEMA = "oncotracer-native-output-owner-v1"
OUTPUT_OWNER_RELATIVE = Path(".oncotracer-native") / "output-owner.json"
OUTPUT_LOCK_RELATIVE = Path(".oncotracer-native") / "run.lock"
OUTPUT_ACTIVE_RELATIVE = Path(".oncotracer-native") / "active-run.json"

_IDENTITY_KEYS = {
    "oncotracer_version",
    "source_commit",
    "source_sha256",
    "source_tree_dirty",
    "binary_sha256",
    "runtime_payload_sha256",
}
_OWNER_KEYS = {
    "schema",
    "canonical_path_sha256",
    "output_id",
    "created_at",
    "runtime_identity",
}
_RESERVED_TOP_LEVEL = {
    ".oncotracer-native",
    "01_samurai_illumina",
    "01_samurai_ont",
    "02_bam_refinement",
    "03_cna_codification",
    "04_cna_custom_plots",
    "05_cna_classifier",
    "06_workflow_summary",
    "07_methylation",
}
_RUNTIME_PAYLOAD_ROOTS = (
    "oncotracer_cli",
    "bin",
    "envs",
    "params",
    "provenance",
)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _canonical_path_sha256(path: Path) -> str:
    canonical = str(path.resolve(strict=True))
    return hashlib.sha256(os.fsencode(canonical)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
                f"could not inspect output path component {current}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise OncoTracerError(
                f"native output path must not contain symlinks: {current}"
            )


def _reject_broad_target(path: Path) -> None:
    broad = {Path(path.anchor), _absolute_lexical(Path.home())}
    broad.add(_absolute_lexical(Path.cwd()))
    package_root = Path(__file__).resolve().parents[1]
    if package_root.is_dir():
        broad.add(_absolute_lexical(package_root))
    for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        if value := os.environ.get(name):
            broad.add(_absolute_lexical(Path(value)))
    if path in broad:
        raise OncoTracerError(
            "native outdir must be a dedicated analysis child, not a filesystem, "
            f"home, or XDG root: {path}"
        )
    if path.exists() and path.is_mount():
        raise OncoTracerError(
            f"native outdir must not be the root of a mounted filesystem: {path}"
        )


def _runtime_payload_sha256(
    binary_sha256: object, explicit_root: Path | None = None
) -> str:
    if explicit_root is not None:
        root = explicit_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise OncoTracerError(
                f"explicit native runtime root is not a directory: {root}"
            )
    elif isinstance(binary_sha256, str) and len(binary_sha256) == 64:
        return binary_sha256
    else:
        root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    records = 0
    for top_name in _RUNTIME_PAYLOAD_ROOTS:
        top = root / top_name
        if not _path_exists(top):
            continue
        try:
            top_metadata = top.lstat()
        except OSError as error:
            raise OncoTracerError(
                f"could not inspect native runtime payload {top}: {error}"
            ) from error
        if stat.S_ISLNK(top_metadata.st_mode):
            raise OncoTracerError(
                f"native runtime payload must not contain symlinks: {top}"
            )
        candidates = [top]
        if stat.S_ISDIR(top_metadata.st_mode):
            candidates.extend(sorted(top.rglob("*"), key=lambda item: item.as_posix()))
        for candidate in candidates:
            try:
                relative_path = candidate.relative_to(root)
                if "__pycache__" in relative_path.parts or candidate.suffix in {
                    ".pyc",
                    ".pyo",
                }:
                    continue
                relative = relative_path.as_posix()
                metadata = candidate.lstat()
            except (OSError, ValueError) as error:
                raise OncoTracerError(
                    f"could not hash native runtime payload {candidate}: {error}"
                ) from error
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISDIR(metadata.st_mode):
                kind = "directory"
                content_digest = "-"
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise OncoTracerError(
                        f"native runtime payload contains a hard-linked file: {candidate}"
                    )
                kind = "file"
                file_digest = hashlib.sha256()
                try:
                    with candidate.open("rb") as handle:
                        while chunk := handle.read(1024 * 1024):
                            file_digest.update(chunk)
                except OSError as error:
                    raise OncoTracerError(
                        f"could not hash native runtime payload {candidate}: {error}"
                    ) from error
                content_digest = file_digest.hexdigest()
            elif stat.S_ISLNK(metadata.st_mode):
                raise OncoTracerError(
                    f"native runtime payload must not contain symlinks: {candidate}"
                )
            else:
                raise OncoTracerError(
                    f"native runtime payload contains a special file: {candidate}"
                )
            digest.update(
                f"{kind}\0{mode:o}\0{relative}\0{content_digest}\n".encode("utf-8")
            )
            records += 1
    if not records:
        raise OncoTracerError("native runtime payload could not be identified")
    return digest.hexdigest()


def current_runtime_identity(explicit_root: Path | None = None) -> dict[str, object]:
    """Return the exact runtime identity without executing any external process."""
    archive_value = getattr(globals().get("__loader__"), "archive", None)
    archive = Path(str(archive_value)) if archive_value else None
    binary_sha256: str | None = None
    if archive is not None and archive.is_file():
        binary_sha256 = _file_sha256(archive)
    return {
        "oncotracer_version": __version__,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_SHA256,
        "source_tree_dirty": SOURCE_TREE_DIRTY,
        "binary_sha256": binary_sha256,
        "runtime_payload_sha256": _runtime_payload_sha256(binary_sha256, explicit_root),
    }


def _parse_owner(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise OncoTracerError(
            f"could not inspect native output owner marker {path}: {error}"
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OncoTracerError(
            f"native output owner marker must be one regular, non-hard-linked file: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OncoTracerError(
            f"native output owner marker is invalid: {path}"
        ) from error
    if not isinstance(value, dict) or set(value) != _OWNER_KEYS:
        raise OncoTracerError(
            f"native output owner marker has an unknown schema: {path}"
        )
    if value.get("schema") != OUTPUT_OWNER_SCHEMA:
        raise OncoTracerError(
            f"native output owner marker has an unknown schema: {path}"
        )
    identity = value.get("runtime_identity")
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS:
        raise OncoTracerError(
            f"native output owner marker has invalid runtime identity: {path}"
        )
    try:
        parsed_id = uuid.UUID(str(value.get("output_id")))
    except (ValueError, AttributeError) as error:
        raise OncoTracerError(
            f"native output owner marker has an invalid output ID: {path}"
        ) from error
    if parsed_id.version != 4 or str(parsed_id) != value.get("output_id"):
        raise OncoTracerError(
            f"native output owner marker has an invalid output ID: {path}"
        )
    if not isinstance(value.get("created_at"), str) or not value["created_at"]:
        raise OncoTracerError(
            f"native output owner marker lacks a creation time: {path}"
        )
    return value


def _directory_entries(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError as error:
        raise OncoTracerError(
            f"could not inspect native outdir {path}: {error}"
        ) from error


def _inspect_existing_target(
    path: Path, identity: Mapping[str, object]
) -> tuple[str, dict[str, object] | None]:
    if not _path_exists(path):
        return "absent", None
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise OncoTracerError(f"native outdir must be a real directory: {path}")
    entries = _directory_entries(path)
    if not entries:
        return "empty", None
    marker = path / OUTPUT_OWNER_RELATIVE
    if not _path_exists(marker):
        native = path / ".oncotracer-native"
        if len(entries) == 1 and entries[0] == native:
            native_metadata = native.lstat()
            if stat.S_ISDIR(native_metadata.st_mode) and not _directory_entries(native):
                return "empty-scaffold", None
        raise OncoTracerError(
            "refusing to use a nonempty, unowned native outdir; preserve it and "
            f"choose a new empty path: {path}"
        )
    owner = _parse_owner(marker)
    canonical_sha256 = _canonical_path_sha256(path)
    if owner.get("canonical_path_sha256") != canonical_sha256:
        raise OncoTracerError(
            f"native output owner marker path mismatch for {path}; choose a new outdir"
        )
    if owner.get("runtime_identity") != dict(identity):
        raise OncoTracerError(
            "native output was created by a different OncoTracer runtime; preserve "
            f"it and choose a new outdir: {path}"
        )
    return "owned", owner


def _safe_make_parents(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not _path_exists(current):
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    _reject_symlink_components(current)
    if not current.is_dir():
        raise OncoTracerError(f"native outdir parent is not a directory: {current}")
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        _reject_symlink_components(directory)
        if not directory.is_dir():
            raise OncoTracerError(
                f"native outdir parent is not a directory: {directory}"
            )


@contextlib.contextmanager
def _parent_claim_lock(parent: Path) -> Iterator[None]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as error:
        raise OncoTracerError(
            f"could not lock native outdir parent {parent}: {error}"
        ) from error
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _create_owner(path: Path, identity: Mapping[str, object]) -> dict[str, object]:
    native = path / ".oncotracer-native"
    if _path_exists(native):
        metadata = native.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or _directory_entries(native):
            raise OncoTracerError(
                f"native output ownership scaffold is not empty and safe: {native}"
            )
    else:
        native.mkdir(mode=0o750)
    owner: dict[str, object] = {
        "schema": OUTPUT_OWNER_SCHEMA,
        "canonical_path_sha256": _canonical_path_sha256(path),
        "output_id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "runtime_identity": dict(identity),
    }
    atomic_write_json(path / OUTPUT_OWNER_RELATIVE, owner)
    return owner


def _validate_reserved_tree(path: Path) -> None:
    root = path.resolve(strict=True)
    for name in _RESERVED_TOP_LEVEL:
        candidate = path / name
        if not _path_exists(candidate):
            continue
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise OncoTracerError(
                f"reserved native output path must not be a symlink: {candidate}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise OncoTracerError(
                f"reserved native output path must be a directory: {candidate}"
            )

    stack = [path / name for name in _RESERVED_TOP_LEVEL if (path / name).is_dir()]
    while stack:
        directory = stack.pop()
        for candidate in _directory_entries(directory):
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                try:
                    target = candidate.resolve(strict=True)
                    target.relative_to(root)
                except (OSError, ValueError) as error:
                    raise OncoTracerError(
                        f"native output symlink escapes or is broken: {candidate}"
                    ) from error
            elif stat.S_ISDIR(metadata.st_mode):
                stack.append(candidate)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise OncoTracerError(
                        f"native output contains a hard-linked product: {candidate}"
                    )
            else:
                raise OncoTracerError(
                    f"native output contains a special filesystem object: {candidate}"
                )


def inspect_output_target(
    outdir: Path,
    identity: Mapping[str, object] | None = None,
    *,
    runtime_root_path: Path | None = None,
) -> Path:
    """Validate output ownership without changing the filesystem (dry-run path)."""
    path = _absolute_lexical(outdir)
    _reject_broad_target(path)
    _reject_symlink_components(path)
    runtime_identity = dict(identity or current_runtime_identity(runtime_root_path))
    state, _ = _inspect_existing_target(path, runtime_identity)
    if state == "owned":
        _validate_reserved_tree(path)
    return path


@dataclass
class OutputRunLease:
    """An acquired, exact-output run lease released on every exit path."""

    path: Path
    owner: dict[str, object]
    _lock_handle: object
    active_path: Path
    config_path: Path
    config_sha256: str
    runtime_root_path: Path | None
    revalidate_runtime: bool

    def validate(self) -> None:
        try:
            current_config_sha256 = _file_sha256(self.config_path)
        except OSError as error:
            raise OncoTracerError(
                f"native run configuration disappeared during execution: {self.config_path}"
            ) from error
        if current_config_sha256 != self.config_sha256:
            raise OncoTracerError(
                f"native run configuration changed during execution: {self.config_path}"
            )
        if self.revalidate_runtime:
            current_identity = current_runtime_identity(self.runtime_root_path)
            if current_identity != self.owner["runtime_identity"]:
                raise OncoTracerError(
                    "native OncoTracer runtime changed during execution; "
                    f"refusing final publication for {self.path}"
                )
        state, owner = _inspect_existing_target(
            self.path, self.owner["runtime_identity"]
        )
        if state != "owned" or owner != self.owner:
            raise OncoTracerError(
                f"native output owner changed during execution: {self.path}"
            )
        _validate_reserved_tree(self.path)

    def close(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        try:
            try:
                self.active_path.unlink(missing_ok=True)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
        finally:
            self._lock_handle = None

    def __enter__(self) -> "OutputRunLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def claim_output_run(
    outdir: Path,
    *,
    config_path: Path,
    identity: Mapping[str, object] | None = None,
    runtime_root_path: Path | None = None,
    expected_config_sha256: str | None = None,
) -> OutputRunLease:
    """Claim or authenticate *outdir* and acquire its nonblocking run lock."""
    path = _absolute_lexical(outdir)
    _reject_broad_target(path)
    _reject_symlink_components(path)
    if runtime_root_path is not None:
        runtime_root_path = runtime_root_path.expanduser().resolve(strict=True)
    revalidate_runtime = identity is None
    runtime_identity = dict(identity or current_runtime_identity(runtime_root_path))
    config_path = config_path.expanduser().resolve(strict=True)
    config_digest = _file_sha256(config_path)
    if expected_config_sha256 is not None and config_digest != expected_config_sha256:
        raise OncoTracerError(
            f"native run configuration changed before output acquisition: {config_path}"
        )

    # Read-only preflight ensures a typo naming protected nonempty data does not
    # even create an adjacent lock file or ownership marker.
    _inspect_existing_target(path, runtime_identity)
    _safe_make_parents(path.parent)
    with _parent_claim_lock(path.parent):
        _reject_symlink_components(path)
        state, owner = _inspect_existing_target(path, runtime_identity)
        if state == "absent":
            path.mkdir(mode=0o750)
            owner = _create_owner(path, runtime_identity)
        elif state in {"empty", "empty-scaffold"}:
            owner = _create_owner(path, runtime_identity)
        assert owner is not None
        _validate_reserved_tree(path)

    lock_path = path / OUTPUT_LOCK_RELATIVE
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    except OSError as error:
        raise OncoTracerError(
            f"could not open native output run lock {lock_path}: {error}"
        ) from error
    try:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OncoTracerError(
                f"native output run lock is not one regular file: {lock_path}"
            )
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OncoTracerError(
                f"another OncoTracer process is already using native outdir: {path}"
            ) from error
        active_path = path / OUTPUT_ACTIVE_RELATIVE
        if _path_exists(active_path):
            active_metadata = active_path.lstat()
            if (
                not stat.S_ISREG(active_metadata.st_mode)
                or active_metadata.st_nlink != 1
            ):
                raise OncoTracerError(
                    f"native output active-run record is unsafe: {active_path}"
                )
        atomic_write_json(
            active_path,
            {
                "schema": "oncotracer-native-active-run-v1",
                "output_id": owner["output_id"],
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "started_at": utc_now(),
                "config_name": config_path.name,
                "config_sha256": config_digest,
                "runtime_identity": runtime_identity,
            },
        )
        return OutputRunLease(
            path,
            owner,
            handle,
            active_path,
            config_path,
            config_digest,
            runtime_root_path,
            revalidate_runtime,
        )
    except BaseException:
        handle.close()
        raise
