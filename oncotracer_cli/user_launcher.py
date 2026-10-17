"""Publish one authenticated per-user command without changing shell profiles."""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shlex
import stat
import sys
import uuid
from pathlib import Path

from .install_safety import _rename_noreplace_at
from .runtime import OncoTracerError

COMMAND = "oncotracer"
MARKER = ".oncotracer-launcher.json"
LOCK = ".oncotracer-launcher.lock"
PENDING = ".oncotracer-launcher.pending.json"
SCHEMA = "oncotracer-user-launcher-v1"


def _absolute(path) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def default_path() -> Path:
    return Path(_absolute(Path.home() / ".local" / "bin" / COMMAND))


def current_binding() -> dict:
    # Preserve the venv path: resolving its python symlink selects the base env.
    archive = getattr(globals().get("__loader__"), "archive", None)
    return {
        "python": _absolute(sys.executable),
        "kind": "archive" if archive else "module",
        "source": _absolute(archive if archive else Path(__file__).parent.parent),
    }


def installation_target(config: dict) -> dict:
    backend = config.get("backend")
    if backend in {"conda", "poetry"}:
        core = config.get("core_prefix")
        if not core:
            raise OncoTracerError("cannot publish a command without the managed core prefix")
        return {"kind": "conda", "target": _absolute(Path(str(core)).parent)}
    if backend == "singularity":
        if not config.get("sif"):
            raise OncoTracerError("cannot publish a command without the managed SIF path")
        return {"kind": "sif", "target": _absolute(str(config["sif"]))}
    if backend == "docker":
        if not config.get("image"):
            raise OncoTracerError("cannot publish a command without the verified Docker image")
        return {"kind": "docker", "target": str(config["image"])}
    raise OncoTracerError("unsupported backend for the user command")


def _wrapper(binding: dict, path: Path) -> bytes:
    if set(binding) != {"python", "kind", "source"} or binding["kind"] not in {"module", "archive"}:
        raise OncoTracerError("invalid user command binding")
    for key in ("python", "source"):
        if not isinstance(binding[key], str) or not os.path.isabs(binding[key]) or "\x00" in binding[key]:
            raise OncoTracerError("user command binding must use absolute paths")
    if binding["kind"] == "archive":
        command = [binding["python"], "-I", "-B", binding["source"]]
    else:
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {binding['source']!r}); "
            f"sys.argv[0] = {str(path)!r}; "
            "from oncotracer_cli.cli import main; raise SystemExit(main())"
        )
        command = [binding["python"], "-I", "-B", "-c", bootstrap]
    return (
        "#!/bin/sh\n"
        "# Managed by oncotracer install; use oncotracer uninstall --global-launcher.\n"
        "unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE "
        "CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER "
        "LD_LIBRARY_PATH LD_PRELOAD LD_LIBRARY_PATH_MCR\n"
        "exec " + shlex.join(command) + ' "$@"\n'
    ).encode()


def _json(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


@contextlib.contextmanager
def _directory(path: Path, *, create: bool, secure: bool = True):
    """Walk every parent without following links; operate through the pinned fd."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as error:
                raise OncoTracerError(f"user command parent must be a real directory, not a symlink: {path}") from error
            os.close(fd)
            fd = child
        if secure and os.fstat(fd).st_uid != os.getuid():
            raise OncoTracerError(f"user command directory is not owned by this user: {path}")
        if secure and stat.S_IMODE(os.fstat(fd).st_mode) & 0o022:
            raise OncoTracerError(f"user command directory is writable by another user: {path}; remove group/world write permission before installing (chmod go-w {shlex.quote(str(path))})")
        yield fd
    finally:
        os.close(fd)


def _same_directory(fd: int, path: Path, *, secure=True):
    with _directory(path, create=False, secure=secure) as current:
        if current is None or (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (
            os.fstat(current).st_dev, os.fstat(current).st_ino
        ):
            raise OncoTracerError("user command parent directory changed; refusing publication/removal")


def _read(fd: int, name: str, mode: int | None = None):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise OncoTracerError(f"user command file must not be a symlink: {name}") from error
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise OncoTracerError(f"user command file is not a user-owned single-link regular file: {name}")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise OncoTracerError(f"user command file has unsafe or changed permissions: {name}")
        if info.st_size > 1024 * 1024:
            raise OncoTracerError(f"user command metadata is unexpectedly large: {name}")
        data = b""
        while chunk := os.read(handle, 65536):
            data += chunk
        after = os.fstat(handle)
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
            raise OncoTracerError(f"user command file changed during validation: {name}")
        return data, (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, hashlib.sha256(data).hexdigest())
    finally:
        os.close(handle)


def _pip_recorded_command(path: Path, data: bytes) -> bool:
    """Retain only this distribution's unchanged pip-created console entry."""
    try:
        distribution = importlib.metadata.distribution("oncotracer")
        imported = Path(__file__).resolve()
        if Path(distribution.locate_file("oncotracer_cli/user_launcher.py")).resolve() != imported:
            return False
        if not any(entry.group == "console_scripts" and entry.name == COMMAND
                   and entry.value == "oncotracer_cli.cli:main"
                   for entry in distribution.entry_points):
            return False
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        return any(
            os.path.abspath(distribution.locate_file(entry)) == str(path)
            and entry.hash is not None and entry.hash.mode == "sha256"
            and entry.hash.value == digest and entry.size == len(data)
            for entry in distribution.files or ()
        )
    except (OSError, ValueError, TypeError, importlib.metadata.PackageNotFoundError):
        return False


def _inspect(fd: int | None, path: Path, *, allow_foreign=False):
    if fd is None:
        return None
    if _read(fd, PENDING, 0o600) is not None:
        raise OncoTracerError(
            f"user command has an interrupted transaction; preserve {path.parent / PENDING} "
            "and its recorded recovery directory, then recover the recorded files before retrying"
        )
    marker = _read(fd, MARKER, 0o600)
    if marker is None and allow_foreign:
        try:
            os.stat(COMMAND, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return {"foreign": True}
    command = _read(fd, COMMAND, 0o755 if marker is not None else None)
    if command is None and marker is None:
        return None
    if (command is not None and marker is None
            and stat.S_IMODE(command[1][2]) in {0o755, 0o775}
            and _pip_recorded_command(path, command[0])):
        return {"pip": True, "files": {COMMAND: command}}
    if command is None or marker is None:
        raise OncoTracerError(f"refusing an unowned or incomplete user command: {path}; use install --no-launcher to leave it unchanged")
    try:
        value = json.loads(marker[0])
        valid = (
            value["schema"] == SCHEMA
            and value["path"] == str(path)
            and value["uid"] == os.getuid()
            and value["sha256"] == hashlib.sha256(command[0]).hexdigest()
            and command[0] == _wrapper(value["binding"], path)
            and set(value["installation"]) == {"kind", "target"}
            and value["installation"]["kind"] in {"conda", "sif", "docker"}
        )
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise OncoTracerError(f"invalid ownership metadata for user command: {path}") from error
    if not valid:
        raise OncoTracerError(f"user command or ownership metadata was edited: {path}")
    return {"marker": value, "files": {COMMAND: command, MARKER: marker}}


def _write(fd: int, name: str, content: bytes, mode: int):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    try:
        os.fchmod(handle, mode)
        with os.fdopen(os.dup(handle), "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(handle)
    os.fsync(fd)


def _move(source: int, name: str, destination: int, target: str):
    _rename_noreplace_at(source, name, destination, target, "owned user command transaction")


class _Transaction:
    def __init__(self, fd, path, observed, *, dry_run, action, purge=False):
        self.fd, self.path, self.observed = fd, path, observed
        self.dry_run, self.action, self.purge = dry_run, action, purge
        self.recovery_fd = None
        self.recovery_name = None
        self.moved = []
        self.published = []
        self.pending = None
        self.result = {
            "path": str(path), "manifest": str(path.parent / MARKER),
            "action": action, "dry_run": dry_run,
            "paths": [str(path), str(path.parent / MARKER)],
            "path_ready": str(path.parent) in [os.path.abspath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p],
        }

    def _begin(self):
        _same_directory(self.fd, self.path.parent)
        if _inspect(self.fd, self.path) != self.observed:
            raise OncoTracerError("user command changed while the backend was being prepared")
        self.recovery_name = ".oncotracer-launcher-recovery-" + uuid.uuid4().hex
        os.mkdir(self.recovery_name, 0o700, dir_fd=self.fd)
        self.recovery_fd = os.open(self.recovery_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        record = {"schema": SCHEMA, "action": self.action, "path": str(self.path),
                  "recovery_directory": str(self.path.parent / self.recovery_name),
                  "previous": self.observed["marker"] if self.observed else None}
        _write(self.recovery_fd, "transaction.json", _json(record), 0o600)
        _write(self.fd, PENDING, _json(record), 0o600)
        self.pending = _read(self.fd, PENDING, 0o600)
        if self.observed:
            for name, expected in self.observed["files"].items():
                if _read(self.fd, name) != expected:
                    raise OncoTracerError("user command changed before its recovery move")
                _move(self.fd, name, self.recovery_fd, "old-" + name)
                self.moved.append(name)
                if _read(self.recovery_fd, "old-" + name) != expected:
                    raise OncoTracerError("user command changed during its recovery move")
        self.result["recovery_directory"] = str(self.path.parent / self.recovery_name)
        self.result["recoverable"] = bool(self.observed)

    def publish(self, config: dict):
        binding = current_binding()
        body = _wrapper(binding, self.path)
        marker = {"schema": SCHEMA, "path": str(self.path), "uid": os.getuid(),
                  "binding": binding, "installation": installation_target(config),
                  "sha256": hashlib.sha256(body).hexdigest()}
        self.result["binding"] = binding
        self.result["installation"] = marker["installation"]
        if self.dry_run:
            return dict(self.result)
        self._begin()
        for name, content, mode in ((COMMAND, body, 0o755), (MARKER, _json(marker), 0o600)):
            _write(self.recovery_fd, "new-" + name, content, mode)
            expected = _read(self.recovery_fd, "new-" + name, mode)
            _move(self.recovery_fd, "new-" + name, self.fd, name)
            self.published.append((name, expected))
            if _read(self.fd, name, mode) != expected:
                raise OncoTracerError("published user command changed during promotion; recovery files retained")
        self.validate_published()
        return dict(self.result)

    def validate_published(self):
        if self.fd is not None:
            _same_directory(self.fd, self.path.parent)
        for name, expected in self.published:
            if _read(self.fd, name) != expected:
                raise OncoTracerError("published user command changed before completion; recovery files retained")

    def detach(self):
        if not self.dry_run and self.observed:
            self._begin()
        return self.result

    def _clear_pending(self):
        if self.pending is not None:
            if _read(self.fd, PENDING, 0o600) != self.pending:
                raise OncoTracerError("user command transaction record changed; recovery files retained")
            # Claim before unlinking so a swapped pathname can never be destroyed.
            _move(self.fd, PENDING, self.recovery_fd, "completed-transaction.json")
            if _read(self.recovery_fd, "completed-transaction.json", 0o600) != self.pending:
                raise OncoTracerError("user command transaction changed during completion")
            self.pending = None
            os.fsync(self.fd)

    def rollback(self):
        if self.recovery_fd is None:
            return
        for name, expected in reversed(self.published):
            if _read(self.fd, name) != expected:
                raise OncoTracerError("user command changed during rollback; recovery files retained")
            _move(self.fd, name, self.recovery_fd, "rollback-" + name)
            if _read(self.recovery_fd, "rollback-" + name) != expected:
                raise OncoTracerError("user command changed during rollback move; recovery files retained")
        for name in reversed(self.moved):
            expected = self.observed["files"][name]
            if _read(self.recovery_fd, "old-" + name) != expected:
                raise OncoTracerError("previous user command recovery file changed")
            _move(self.recovery_fd, "old-" + name, self.fd, name)
        self._clear_pending()

    def finish(self):
        if self.recovery_fd is None:
            return
        self.validate_published()
        self._clear_pending()
        # Tool uninstallation retains backups by default. --purge removes only
        # these two previously authenticated files, never the shared bin folder.
        if self.purge and self.observed:
            for name in self.moved:
                expected = self.observed["files"][name]
                backup = "old-" + name
                if _read(self.recovery_fd, backup) != expected:
                    raise OncoTracerError("user command recovery file changed; refusing purge")
                os.unlink(backup, dir_fd=self.recovery_fd)
            self.result["recoverable"] = False


class _PipCommand:
    """Read-only participation: pip retains ownership and uninstall responsibility."""
    def __init__(self, fd, path, observed, dry_run):
        self.fd, self.path, self.observed = fd, path, observed
        self.result = {
            "path": str(path), "manifest": None, "action": "retain-pip",
            "dry_run": dry_run, "paths": [], "recoverable": False,
            "reason": "unchanged pip-owned console entry; remove with pip uninstall oncotracer",
            "path_ready": str(path.parent) in [os.path.abspath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p],
        }

    def validate_published(self):
        _same_directory(self.fd, self.path.parent, secure=False)
        if _inspect(self.fd, self.path) != self.observed:
            raise OncoTracerError("pip-owned console entry changed during backend preparation; no launcher files were modified")

    def publish(self, config):
        self.validate_published()
        return dict(self.result)


@contextlib.contextmanager
def _locked(action, *, dry_run, target=None, archive=None, required=False, purge=False):
    path = default_path()
    optional_removal = action == "remove" and not required
    with _directory(path.parent, create=False, secure=False) as fd:
        observed = _inspect(fd, path, allow_foreign=optional_removal)
    if observed is not None and observed.get("pip"):
        if action == "remove":
            raise OncoTracerError("this console entry is owned by pip; use pip uninstall oncotracer to remove it")
        with _directory(path.parent, create=False, secure=False) as fd:
            transaction = _PipCommand(fd, path, observed, dry_run)
            transaction.validate_published()
            yield transaction
            transaction.validate_published()
        return
    if not optional_removal:
        with _directory(path.parent, create=False) as fd:
            if _inspect(fd, path) != observed:
                raise OncoTracerError("user command changed during preflight")
    if action == "remove":
        reason = None
        if observed is None:
            reason = "no owned user command is installed"
        elif observed.get("foreign"):
            reason = "unowned user command is preserved"
        elif observed["marker"]["binding"] != current_binding():
            reason = "user command belongs to a different Python/source installation"
        elif target is not None and observed["marker"]["installation"] != target:
            reason = "user command belongs to a different tool installation"
        elif archive is not None and (
            observed["marker"]["binding"]["kind"] != "archive"
            or observed["marker"]["binding"]["source"] != _absolute(archive)
        ):
            reason = "user command belongs to a different copied executable"
        if reason:
            if required and observed is not None:
                raise OncoTracerError(reason + "; run the owning command to remove it")
            tx = _Transaction(None, path, None, dry_run=dry_run, action="preserve")
            tx.result.update({"reason": reason, "paths": [], "recoverable": False})
            yield tx
            return
    if dry_run:
        yield _Transaction(None, path, observed, dry_run=True, action=action, purge=purge)
        return
    with _directory(path.parent, create=True) as fd:
        try:
            lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        except FileExistsError:
            lock = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise OncoTracerError("unsafe user command lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OncoTracerError("another user command installation/removal is active") from error
            if _inspect(fd, path) != observed:
                raise OncoTracerError("user command changed before its lock was acquired")
            tx = _Transaction(fd, path, observed, dry_run=False, action=action, purge=purge)
            try:
                yield tx
            except BaseException:
                tx.rollback()
                raise
            else:
                tx.finish()
            finally:
                if tx.recovery_fd is not None:
                    os.close(tx.recovery_fd)
        finally:
            os.close(lock)


def install_transaction(*, dry_run=False):
    return _locked("publish", dry_run=dry_run)


def remove_transaction(target=None, *, archive=None, dry_run=False, purge=False, required=False):
    return _locked("remove", dry_run=dry_run, target=target, archive=archive, required=required, purge=purge)
