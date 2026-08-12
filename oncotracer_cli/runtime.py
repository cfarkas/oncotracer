"""Runtime, payload, command, and provenance helpers for OncoTracer v2."""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping, Sequence, TextIO

from . import __version__


class OncoTracerError(RuntimeError):
    """Base exception for clear user-facing failures."""


_SCOPED_PAYLOAD_CACHE: ContextVar[Path | None] = ContextVar(
    "oncotracer_scoped_payload_cache", default=None
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class _PayloadCacheLocation:
    path: Path
    ownership: str
    base: Path | None = None


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


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
                f"could not inspect payload-cache path component {current}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise OncoTracerError(
                f"payload-cache path must not contain symlinks: {current}"
            )


def _payload_cache_location(archive_sha256: str | None = None) -> _PayloadCacheLocation:
    if scoped := _SCOPED_PAYLOAD_CACHE.get():
        return _PayloadCacheLocation(_absolute_lexical(scoped), "context")

    override = os.environ.get("ONCOTRACER_PAYLOAD_CACHE")
    if override:
        destination = _absolute_lexical(Path(override))
        broad = {
            Path(destination.anchor),
            _absolute_lexical(Path.home()),
        }
        for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            if value := os.environ.get(name):
                broad.add(_absolute_lexical(Path(value)))
        if destination in broad:
            raise OncoTracerError(
                "ONCOTRACER_PAYLOAD_CACHE must name a dedicated child path, not "
                f"a filesystem, home, or XDG root: {destination}"
            )
        _reject_symlink_components(destination)
        return _PayloadCacheLocation(destination, "explicit")

    if not archive_sha256:
        raise OncoTracerError(
            "the standalone archive SHA-256 is required for the default payload cache"
        )
    base = _absolute_lexical(
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    )
    _reject_symlink_components(base)
    destination = base / "oncotracer" / __version__ / archive_sha256 / "payload"
    if destination.parent.parent.parent.parent != base:
        raise OncoTracerError(f"invalid content-addressed payload-cache path: {destination}")
    return _PayloadCacheLocation(destination, "default", base)


def _payload_cache(archive_sha256: str | None = None) -> Path:
    return _payload_cache_location(archive_sha256).path


@contextlib.contextmanager
def isolated_payload_cache(enabled: bool = True) -> Iterator[None]:
    """Use an automatically removed, context-local payload cache when enabled."""
    if not enabled:
        yield
        return
    with tempfile.TemporaryDirectory(prefix="oncotracer-dry-run-payload-") as directory:
        token = _SCOPED_PAYLOAD_CACHE.set(Path(directory) / "payload")
        try:
            yield
        finally:
            _SCOPED_PAYLOAD_CACHE.reset(token)


def _payload_relative(member_name: str, *, directory: bool) -> PurePosixPath | None:
    if not member_name.startswith("payload/"):
        return None
    if "\\" in member_name or any(ord(character) < 32 or ord(character) == 127 for character in member_name):
        raise OncoTracerError(
            f"standalone executable has an unsafe payload path: {member_name!r}"
        )
    raw = member_name[len("payload/") :]
    if directory and raw.endswith("/"):
        raw = raw[:-1]
    parts = raw.split("/")
    if not raw:
        return None
    if any(part in {"", ".", ".."} for part in parts):
        raise OncoTracerError(
            f"standalone executable has an unsafe payload path: {member_name!r}"
        )
    return PurePosixPath(*parts)


def _zip_member_kind_mode(info: zipfile.ZipInfo) -> tuple[str, int]:
    raw_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(raw_mode)
    if info.is_dir():
        if file_type not in {0, stat.S_IFDIR}:
            raise OncoTracerError(
                f"standalone payload directory has a special file type: {info.filename}"
            )
        return "directory", 0o755
    if file_type != stat.S_IFREG:
        raise OncoTracerError(
            f"standalone payload member is not a regular file: {info.filename}"
        )
    return "file", 0o755 if stat.S_IMODE(raw_mode) & 0o111 else 0o644


def _archive_payload_manifest(archive: Path) -> dict[str, dict[str, object]]:
    manifest: dict[str, dict[str, object]] = {}
    explicit_members: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            relative = _payload_relative(info.filename, directory=info.is_dir())
            if relative is None:
                continue
            name = relative.as_posix()
            if name in explicit_members:
                raise OncoTracerError(
                    f"standalone executable has a duplicate payload path: {info.filename}"
                )
            explicit_members.add(name)
            kind, mode = _zip_member_kind_mode(info)
            for parent in reversed(relative.parents[:-1]):
                parent_name = parent.as_posix()
                previous = manifest.get(parent_name)
                if previous and previous["kind"] != "directory":
                    raise OncoTracerError(
                        f"standalone payload file conflicts with directory {parent_name!r}"
                    )
                manifest[parent_name] = {"kind": "directory", "mode": 0o755}
            previous = manifest.get(name)
            if previous and previous["kind"] != kind:
                raise OncoTracerError(
                    f"standalone payload path has conflicting file types: {name}"
                )
            if kind == "directory":
                manifest[name] = {"kind": kind, "mode": mode}
                continue
            digest = hashlib.sha256()
            with bundle.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            manifest[name] = {
                "bytes": info.file_size,
                "kind": kind,
                "mode": mode,
                "sha256": digest.hexdigest(),
            }
    if manifest.get("bin/scripts", {}).get("kind") != "directory":
        raise OncoTracerError(f"standalone executable has no payload: {archive}")
    return manifest


def _tree_payload_manifest(path: Path) -> dict[str, dict[str, object]] | None:
    manifest: dict[str, dict[str, object]] = {}
    try:
        root_metadata = path.lstat()
    except OSError:
        return None
    if not stat.S_ISDIR(root_metadata.st_mode):
        return None

    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            members = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            return None
        for member in members:
            relative = member.relative_to(path).as_posix()
            if relative == ".complete.json":
                continue
            try:
                metadata = member.lstat()
            except OSError:
                return None
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISDIR(metadata.st_mode):
                manifest[relative] = {"kind": "directory", "mode": mode}
                pending.append(member)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                return None
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(member, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        return None
                    digest = hashlib.sha256()
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError:
                return None
            manifest[relative] = {
                "bytes": opened.st_size,
                "kind": "file",
                "mode": stat.S_IMODE(opened.st_mode),
                "sha256": digest.hexdigest(),
            }
    return manifest


def _manifest_sha256(manifest: Mapping[str, Mapping[str, object]]) -> str:
    canonical = json.dumps(
        manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return sha256_text(canonical)


def _complete_payload(
    path: Path,
    expected: Mapping[str, object],
    payload_manifest: Mapping[str, Mapping[str, object]],
) -> bool:
    marker = path / ".complete.json"
    try:
        marker_metadata = marker.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(marker_metadata.st_mode):
        return False
    try:
        observed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        observed == expected
        and _tree_payload_manifest(path) == payload_manifest
    )


def _cache_state(
    path: Path,
    expected: Mapping[str, object],
    payload_manifest: Mapping[str, Mapping[str, object]],
) -> str:
    if not os.path.lexists(path):
        return "absent"
    try:
        metadata = path.lstat()
    except OSError:
        return "unsafe"
    if not stat.S_ISDIR(metadata.st_mode):
        return "unsafe"
    try:
        entries = list(path.iterdir())
    except OSError:
        return "unsafe"
    if not entries:
        return "empty"
    marker = path / ".complete.json"
    try:
        marker_metadata = marker.lstat()
        if not stat.S_ISREG(marker_metadata.st_mode):
            return "unsafe"
        observed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unowned"
    actual = _tree_payload_manifest(path)
    if observed == expected and actual == payload_manifest:
        return "valid"
    if observed != expected or not actual:
        return "unowned"
    if set(actual) != set(payload_manifest):
        return "unowned"
    if any(
        actual[name].get("kind") != payload_manifest[name].get("kind")
        for name in actual
    ):
        return "unowned"
    matching_file = any(
        actual[name].get("kind") == "file" and actual[name] == payload_manifest[name]
        for name in actual
    )
    return "owned-corrupt" if matching_file else "unowned"


def _guard_cache_location(location: _PayloadCacheLocation) -> None:
    destination = location.path
    if destination == Path(destination.anchor):
        raise OncoTracerError(f"refusing unsafe payload-cache path: {destination}")
    _reject_symlink_components(destination)
    if location.ownership == "default":
        assert location.base is not None
        expected = (
            location.base
            / "oncotracer"
            / __version__
            / destination.parent.name
            / "payload"
        )
        if destination != expected or destination.name != "payload":
            raise OncoTracerError(
                f"refusing malformed content-addressed payload-cache path: {destination}"
            )


def _remove_owned_payload(path: Path, state: str, ownership: str) -> None:
    if state == "absent":
        return
    if state == "empty":
        path.rmdir()
        return
    if state == "owned-corrupt" or ownership == "context":
        shutil.rmtree(path)
        return
    raise OncoTracerError(
        f"refusing to replace unowned or unsafe payload-cache contents: {path}"
    )


def _extract_payload_to_staging(
    archive: Path,
    staging: Path,
    payload_manifest: Mapping[str, Mapping[str, object]],
) -> None:
    for name, entry in sorted(
        payload_manifest.items(), key=lambda item: (item[0].count("/"), item[0])
    ):
        if entry["kind"] == "directory":
            directory = staging.joinpath(*PurePosixPath(name).parts)
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(int(entry["mode"]))
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            relative = _payload_relative(info.filename, directory=info.is_dir())
            if relative is None or info.is_dir():
                continue
            entry = payload_manifest[relative.as_posix()]
            target = staging.joinpath(*relative.parts)
            with bundle.open(info) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink)
            target.chmod(int(entry["mode"]))
    for name, entry in sorted(
        payload_manifest.items(), key=lambda item: item[0].count("/"), reverse=True
    ):
        if entry["kind"] == "directory":
            staging.joinpath(*PurePosixPath(name).parts).chmod(int(entry["mode"]))


def _extract_zipapp_payload(archive: Path) -> Path:
    archive_sha256 = sha256_file(archive)
    payload_manifest = _archive_payload_manifest(archive)
    expected = {
        "schema": "oncotracer-payload-cache-v2",
        "version": __version__,
        "archive_sha256": archive_sha256,
        "payload_entries": len(payload_manifest),
        "payload_manifest_sha256": _manifest_sha256(payload_manifest),
    }
    location = _payload_cache_location(archive_sha256)
    destination = location.path
    _guard_cache_location(location)
    initial_state = _cache_state(destination, expected, payload_manifest)
    if initial_state == "valid":
        return destination
    if location.ownership == "explicit" and initial_state not in {
        "absent",
        "empty",
        "owned-corrupt",
    }:
        raise OncoTracerError(
            "ONCOTRACER_PAYLOAD_CACHE contains data not owned by this exact "
            f"standalone executable; choose an absent or empty dedicated path: {destination}"
        )
    if initial_state == "unsafe":
        raise OncoTracerError(f"payload-cache path is unsafe: {destination}")

    lock = destination.parent / f".{destination.name}.oncotracer.lock"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _guard_cache_location(location)
    if os.path.lexists(lock) and not stat.S_ISREG(lock.lstat().st_mode):
        raise OncoTracerError(f"payload-cache lock is not a regular file: {lock}")
    with exclusive_lock(lock):
        state = _cache_state(destination, expected, payload_manifest)
        if state == "valid":
            return destination
        if state == "unsafe" or (
            location.ownership != "context"
            and state not in {"absent", "empty", "owned-corrupt"}
        ):
            raise OncoTracerError(
                f"refusing to replace unowned or unsafe payload-cache contents: {destination}"
            )

        staging = Path(
            tempfile.mkdtemp(prefix=".payload.tmp.", dir=str(destination.parent))
        )
        try:
            _extract_payload_to_staging(archive, staging, payload_manifest)
            atomic_write_json(staging / ".complete.json", expected)
            if not _complete_payload(staging, expected, payload_manifest):
                raise OncoTracerError(
                    f"standalone payload staging verification failed: {archive}"
                )
            state = _cache_state(destination, expected, payload_manifest)
            if state == "valid":
                return destination
            if state == "unsafe" or (
                location.ownership != "context"
                and state not in {"absent", "empty", "owned-corrupt"}
            ):
                raise OncoTracerError(
                    f"payload-cache contents changed during extraction: {destination}"
                )
            _guard_cache_location(location)
            _remove_owned_payload(destination, state, location.ownership)
            os.replace(staging, destination)
            if not _complete_payload(destination, expected, payload_manifest):
                raise OncoTracerError(
                    "standalone payload post-publication verification failed; "
                    f"preserving the cache for safe inspection: {destination}"
                )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return destination


def runtime_root(explicit: str | Path | None = None) -> Path:
    """Return a repository/payload root containing ``bin/scripts``."""
    configured: list[Path] = []
    if explicit:
        configured.append(Path(explicit).expanduser())
    if value := os.environ.get("ONCOTRACER_ROOT"):
        configured.append(Path(value).expanduser())
    for candidate in configured:
        with contextlib.suppress(OSError):
            resolved = candidate.resolve()
            if (resolved / "bin" / "scripts").is_dir():
                return resolved

    # A copied release executable must use its own embedded, verified payload
    # even when it happens to sit inside a source checkout. Inferred package
    # parents are meaningful only for a normal source import.
    executable = Path(sys.argv[0]).expanduser()
    if executable.exists() and zipfile.is_zipfile(executable):
        return _extract_zipapp_payload(executable.resolve())

    package = Path(__file__).resolve()
    inferred = [
        package.parents[1],
        package.parents[2] if len(package.parents) > 2 else package.parent,
    ]
    for candidate in inferred:
        with contextlib.suppress(OSError):
            resolved = candidate.resolve()
            if (resolved / "bin" / "scripts").is_dir():
                return resolved
    raise OncoTracerError(
        "OncoTracer payload was not found. Run from a repository clone, use the "
        "standalone release executable, or set ONCOTRACER_ROOT."
    )


def parse_scalar(value: str):
    value = value.strip()
    if not value:
        return ""
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [parse_scalar(part) for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_flat_yaml(path: Path) -> dict[str, object]:
    """Parse the flat YAML schema used by OncoTracer generated configs.

    v2 deliberately keeps user configuration flat. Nested YAML is rejected so
    a copied standalone executable does not need a third-party YAML parser.
    """
    values: dict[str, object] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            raise OncoTracerError(
                f"nested YAML is not supported ({path}:{line_number})"
            )
        if ":" not in raw:
            raise OncoTracerError(f"invalid YAML entry ({path}:{line_number}): {raw}")
        key, value = raw.split(":", 1)
        key = key.strip()
        if not key:
            raise OncoTracerError(f"empty YAML key ({path}:{line_number})")
        if key in values:
            raise OncoTracerError(f"duplicate YAML key {key!r} ({path}:{line_number})")
        # Generated OncoTracer values never contain unquoted inline comments.
        if " #" in value:
            value = value.split(" #", 1)[0]
        values[key] = parse_scalar(value)
    return values


def render_flat_yaml(values: Mapping[str, object]) -> str:
    lines: list[str] = []
    for key, value in values.items():
        if value is True:
            rendered = "true"
        elif value is False:
            rendered = "false"
        elif value is None:
            rendered = "null"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        elif isinstance(value, list):
            rendered = "[" + ", ".join(str(item) for item in value) + "]"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n"


def render_key_value_summary(values: Mapping[str, object]) -> str:
    """Render stable machine-readable ``key=value`` summary text.

    Python's default boolean spelling (``True``/``False``) is incompatible
    with the lowercase values documented and validated by OncoTracer. Keep
    the JSON representation typed while making the companion text format
    deterministic across every native writer.
    """
    lines: list[str] = []
    for key, value in values.items():
        if value is True:
            rendered = "true"
        elif value is False:
            rendered = "false"
        elif value is None:
            rendered = "null"
        else:
            rendered = str(value)
        lines.append(f"{key}={rendered}")
    return "\n".join(lines) + "\n"


def atomic_write_workflow_summary(
    summary_dir: Path, values: Mapping[str, object]
) -> None:
    """Atomically write the typed JSON and stable text summary pair."""
    summary_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(summary_dir / "workflow_summary.json", values)
    atomic_write_text(
        summary_dir / "workflow_summary.txt",
        render_key_value_summary(values),
    )


def require_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise OncoTracerError(f"{label} is missing or empty: {path}")
    return path


def require_directory(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise OncoTracerError(f"{label} is not a directory: {path}")
    return path


def require_command(command: str) -> str:
    found = shutil.which(command)
    if not found:
        raise OncoTracerError(f"required command is not available on PATH: {command}")
    return found


def download(
    url: str,
    destination: Path,
    *,
    expected_bytes: int | None = None,
    expected_md5: str | None = None,
    retries: int = 5,
) -> Path:
    """Download one file atomically and validate optional size/MD5."""
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    def valid(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        if expected_bytes is not None and path.stat().st_size != expected_bytes:
            return False
        if expected_md5 is not None:
            digest = hashlib.md5()  # noqa: S324 - archive integrity, not security
            with path.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest().lower() != expected_md5.lower():
                return False
        return True

    if valid(destination):
        return destination
    temporary = destination.with_name(f".{destination.name}.part")
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": f"OncoTracer/{__version__}"}
            )
            mode = "ab" if temporary.exists() and temporary.stat().st_size > 0 else "wb"
            offset = temporary.stat().st_size if mode == "ab" else 0
            if offset:
                request.add_header("Range", f"bytes={offset}-")
            with (
                urllib.request.urlopen(request, timeout=120) as source,
                temporary.open(mode) as sink,
            ):
                if offset and getattr(source, "status", None) == 200:
                    sink.close()
                    temporary.unlink(missing_ok=True)
                    return download(
                        url,
                        destination,
                        expected_bytes=expected_bytes,
                        expected_md5=expected_md5,
                        retries=retries,
                    )
                shutil.copyfileobj(source, sink, length=8 * 1024 * 1024)
            if valid(temporary):
                os.replace(temporary, destination)
                return destination
        except (OSError, urllib.error.URLError) as error:
            if attempt == retries:
                raise OncoTracerError(
                    f"download failed after {retries} attempts: {url}: {error}"
                ) from error
            time.sleep(min(2**attempt, 15))
    raise OncoTracerError(f"download validation failed: {url} -> {destination}")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    command: tuple[str, ...]
    started_at: str
    finished_at: str


def _command_environment(
    overrides: Mapping[str, str | None] | None,
) -> dict[str, str]:
    """Merge explicit overrides while allowing a caller to unset a variable."""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for key, value in (overrides or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    return environment


class CommandRunner:
    """Run commands without a shell and append an auditable TSV trace."""

    def __init__(self, trace_path: Path, *, dry_run: bool = False, echo: bool = True):
        self.trace_path = trace_path
        self.dry_run = dry_run
        self.echo = echo
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        if not trace_path.exists():
            with trace_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(
                    [
                        "stage",
                        "started_at",
                        "finished_at",
                        "returncode",
                        "cwd",
                        "command",
                    ]
                )

    def _record(
        self,
        stage: str,
        started: str,
        finished: str,
        returncode: int,
        cwd: Path | None,
        command: Sequence[str],
    ) -> None:
        with self.trace_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(
                [
                    stage,
                    started,
                    finished,
                    returncode,
                    str(cwd or Path.cwd()),
                    shlex.join(command),
                ]
            )

    def run(
        self,
        stage: str,
        command: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str | None] | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        stdin: TextIO | int | None = None,
        check: bool = True,
    ) -> CommandResult:
        argv = tuple(str(item) for item in command)
        started = utc_now()
        if self.echo:
            print(f"[{stage}] {shlex.join(argv)}", file=sys.stderr, flush=True)
        if self.dry_run:
            finished = utc_now()
            self._record(stage, started, finished, 0, cwd, argv)
            return CommandResult(0, argv, started, finished)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=_command_environment(env),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
        finished = utc_now()
        self._record(stage, started, finished, completed.returncode, cwd, argv)
        if check and completed.returncode != 0:
            raise OncoTracerError(
                f"stage {stage!r} failed with exit code {completed.returncode}: {shlex.join(argv)}"
            )
        return CommandResult(completed.returncode, argv, started, finished)

    def pipeline(
        self,
        stage: str,
        left: Sequence[str | Path],
        right: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str | None] | None = None,
    ) -> None:
        left_argv = tuple(str(item) for item in left)
        right_argv = tuple(str(item) for item in right)
        rendered = (*left_argv, "|", *right_argv)
        started = utc_now()
        if self.echo:
            print(
                f"[{stage}] {shlex.join(left_argv)} | {shlex.join(right_argv)}",
                file=sys.stderr,
            )
        if self.dry_run:
            self._record(stage, started, utc_now(), 0, cwd, rendered)
            return
        proc_env = _command_environment(env)
        left_process = subprocess.Popen(
            left_argv, cwd=cwd, env=proc_env, stdout=subprocess.PIPE
        )
        assert left_process.stdout is not None
        right_process = subprocess.Popen(
            right_argv, cwd=cwd, env=proc_env, stdin=left_process.stdout
        )
        left_process.stdout.close()
        right_rc = right_process.wait()
        left_rc = left_process.wait()
        returncode = right_rc or left_rc
        finished = utc_now()
        self._record(stage, started, finished, returncode, cwd, rendered)
        if returncode:
            raise OncoTracerError(
                f"pipeline stage {stage!r} failed (left={left_rc}, right={right_rc})"
            )


class StageLedger:
    """Small content-aware resume ledger for native stages."""

    def __init__(self, path: Path):
        self.path = path
        if path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}
        else:
            self.data = {}
        self.data.setdefault("version", __version__)
        self.data.setdefault("stages", {})

    @staticmethod
    def signature(name: str, command: Sequence[str], inputs: Iterable[Path]) -> str:
        records: list[dict[str, object]] = []
        for path in inputs:
            resolved = path.expanduser().resolve()
            if resolved.exists():
                stat = resolved.stat()
                records.append(
                    {
                        "path": str(resolved),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            else:
                records.append({"path": str(resolved), "missing": True})
        payload = json.dumps(
            {
                "name": name,
                "command": list(command),
                "inputs": records,
                "version": __version__,
            },
            sort_keys=True,
        )
        return sha256_text(payload)

    def reusable(self, name: str, signature: str, outputs: Iterable[Path]) -> bool:
        record = self.data["stages"].get(name)
        if not isinstance(record, dict) or record.get("signature") != signature:
            return False
        return all(path.is_file() and path.stat().st_size > 0 for path in outputs)

    def complete(self, name: str, signature: str, outputs: Iterable[Path]) -> None:
        self.data["stages"][name] = {
            "signature": signature,
            "completed_at": utc_now(),
            "outputs": [
                {
                    "path": str(path.resolve()),
                    "size": path.stat().st_size if path.exists() else None,
                    "sha256": (
                        sha256_file(path)
                        if path.is_file() and path.stat().st_size < 128 * 1024 * 1024
                        else None
                    ),
                }
                for path in outputs
            ],
        }
        atomic_write_json(self.path, self.data)
