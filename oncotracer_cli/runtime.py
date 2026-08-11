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
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence, TextIO

from . import __version__


class OncoTracerError(RuntimeError):
    """Base exception for clear user-facing failures."""


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


def _payload_cache() -> Path:
    override = os.environ.get("ONCOTRACER_PAYLOAD_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return (base / "oncotracer" / __version__ / "payload").resolve()


def _extract_zipapp_payload(archive: Path) -> Path:
    destination = _payload_cache()
    marker = destination / ".complete.json"
    lock = destination.parent / ".payload.lock"
    with exclusive_lock(lock):
        expected = {"version": __version__, "archive_sha256": sha256_file(archive)}
        if marker.is_file():
            try:
                observed = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                observed = None
            if observed == expected and (destination / "bin" / "scripts").is_dir():
                return destination
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            members = [
                name for name in bundle.namelist() if name.startswith("payload/")
            ]
            if not members:
                raise OncoTracerError(
                    f"standalone executable has no payload: {archive}"
                )
            for member in members:
                relative = Path(member).relative_to("payload")
                if not relative.parts:
                    continue
                target = destination / relative
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                info = bundle.getinfo(member)
                mode = info.external_attr >> 16
                if mode:
                    with contextlib.suppress(OSError):
                        target.chmod(mode)
        atomic_write_json(marker, expected)
    return destination


def runtime_root(explicit: str | Path | None = None) -> Path:
    """Return a repository/payload root containing ``bin/scripts``."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if value := os.environ.get("ONCOTRACER_ROOT"):
        candidates.append(Path(value).expanduser())
    package = Path(__file__).resolve()
    candidates.extend(
        [
            package.parents[1],
            package.parents[2] if len(package.parents) > 2 else package.parent,
        ]
    )
    executable = Path(sys.argv[0]).expanduser()
    if executable.exists() and zipfile.is_zipfile(executable):
        return _extract_zipapp_payload(executable.resolve())
    for candidate in candidates:
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
