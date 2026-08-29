"""Fail-closed Fontconfig isolation for native OncoTracer child processes."""

from __future__ import annotations

import hashlib
import os
import stat
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .runtime import OncoTracerError

_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_MAX_FILES = 512
_MAX_DEPTH = 32
_ALLOWED_INCLUDE_PREFIXES = {None, "default", "xdg"}


def _safe_relative_include(text: str, label: str) -> Path:
    """Return one normalized relative include or reject ambiguous path syntax."""
    if (
        not text
        or text.startswith("/")
        or text.endswith("/")
        or "//" in text
        or "\\" in text
        or os.pathsep in text
        or any(ord(character) < 32 for character in text)
    ):
        raise OncoTracerError(f"{label} is not one normalized relative path: {text!r}")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise OncoTracerError(f"{label} is not one normalized relative path: {text!r}")
    return Path(*parts)


def _search_relative_include(node: ET.Element) -> Path | None:
    """Return the relative name searched through FONTCONFIG_PATH, if any."""
    text = (node.text or "").strip()
    prefix = node.get("prefix")
    if (
        prefix not in {None, "default"}
        or Path(text).is_absolute()
        or text.startswith("~")
    ):
        return None
    return _safe_relative_include(text, "Fontconfig search include")


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        raise OncoTracerError(f"cannot inspect {label} {path}: {error}") from error


def _private_directory(path: Path, label: str) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise OncoTracerError(f"cannot create {label} {path}: {error}") from error
    metadata = _lstat(path, label)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise OncoTracerError(
            f"{label} must be an owned physical mode-0700 directory: {path}"
        )
    return path


def _directory_identity(path: Path, label: str) -> tuple[int, int, int, int]:
    metadata = _lstat(path, label)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise OncoTracerError(
            f"{label} must remain an owned physical mode-0700 directory: {path}"
        )
    if not os.access(path, os.W_OK | os.X_OK):
        raise OncoTracerError(f"{label} is not writable and searchable: {path}")
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
    )


def _read_config(path: Path) -> tuple[bytes, tuple[object, ...], tuple[int, int]]:
    named_before = _lstat(path, "Fontconfig configuration")
    if stat.S_ISLNK(named_before.st_mode):
        if named_before.st_nlink != 1:
            raise OncoTracerError(f"Fontconfig symlink is hardlinked: {path}")
        try:
            link_target = os.readlink(path)
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise OncoTracerError(
                f"Fontconfig configuration symlink is broken: {path}: {error}"
            ) from error
    elif stat.S_ISREG(named_before.st_mode):
        link_target = ""
        resolved = path
    else:
        raise OncoTracerError(
            f"Fontconfig configuration is not a regular file or symlink: {path}"
        )

    descriptor: int | None = None
    try:
        descriptor = os.open(
            resolved,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or opened_before.st_size <= 0
            or opened_before.st_size > _MAX_FILE_BYTES
        ):
            raise OncoTracerError(
                f"Fontconfig configuration is not one bounded regular file: {path}"
            )
        chunks: list[bytes] = []
        remaining = _MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        opened_after = os.fstat(descriptor)
    except OncoTracerError:
        raise
    except OSError as error:
        raise OncoTracerError(
            f"cannot read Fontconfig configuration {path}: {error}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

    try:
        named_after = path.lstat()
        resolved_after = path.resolve(strict=True)
    except OSError as error:
        raise OncoTracerError(
            f"Fontconfig configuration changed while read: {path}: {error}"
        ) from error
    named = (
        named_before.st_dev,
        named_before.st_ino,
        named_before.st_mode,
        named_before.st_size,
        named_before.st_mtime_ns,
    )
    opened = (
        opened_before.st_dev,
        opened_before.st_ino,
        opened_before.st_mode,
        opened_before.st_size,
        opened_before.st_mtime_ns,
    )
    if (
        named
        != (
            named_after.st_dev,
            named_after.st_ino,
            named_after.st_mode,
            named_after.st_size,
            named_after.st_mtime_ns,
        )
        or resolved_after != resolved
        or opened
        != (
            opened_after.st_dev,
            opened_after.st_ino,
            opened_after.st_mode,
            opened_after.st_size,
            opened_after.st_mtime_ns,
        )
        or len(payload) != opened_before.st_size
        or len(payload) > _MAX_FILE_BYTES
    ):
        raise OncoTracerError(f"Fontconfig configuration changed while read: {path}")
    digest = hashlib.sha256(payload).hexdigest()
    return (
        payload,
        (
            "file",
            str(path),
            *named,
            link_target,
            str(resolved),
            *opened,
            digest,
        ),
        (opened_before.st_dev, opened_before.st_ino),
    )


def _include_path(
    node: ET.Element,
    *,
    config_path: Path,
    xdg_config: Path,
    home: Path,
) -> Path:
    text = (node.text or "").strip()
    if not text or "\x00" in text:
        raise OncoTracerError("Fontconfig include path is empty or malformed")
    prefix = node.get("prefix")
    if prefix not in _ALLOWED_INCLUDE_PREFIXES:
        raise OncoTracerError(
            f"Fontconfig include uses unsupported prefix {prefix!r}: {text}"
        )
    if prefix == "xdg":
        # Fontconfig concatenates XDG_CONFIG_HOME before interpreting text.
        relative = text.lstrip("/")
        return xdg_config / _safe_relative_include(relative, "Fontconfig XDG include")
    if text == "~":
        return home
    if text.startswith("~/"):
        return home / _safe_relative_include(text[2:], "Fontconfig home include")
    if text.startswith("~"):
        raise OncoTracerError(
            f"Fontconfig include uses unsupported home expansion: {text}"
        )
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    # Fontconfig resolves unprefixed/default includes through FONTCONFIG_PATH,
    # which OncoTracer binds to the selected root configuration directory.
    return config_path / _safe_relative_include(text, "Fontconfig search include")


def _missing_record(
    node: ET.Element, include: Path, *, searched_at_runtime: bool
) -> tuple[object, ...]:
    """Describe a missing include and whether Fontconfig searches config paths."""
    text = (node.text or "").strip()
    prefix = node.get("prefix")
    candidate = Path(text)
    searched = searched_at_runtime and (
        prefix in {None, "default"}
        and not candidate.is_absolute()
        and not text.startswith("~")
    )
    return (
        "missing",
        str(include),
        "search" if searched else "direct",
        text,
    )


def _scan_fragment(
    payload: bytes, path: Path
) -> tuple[tuple[str, Mapping[str, str]], ...]:
    """Parse a fragment with the same non-namespace Expat mode as Fontconfig."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    depth = 0
    current: tuple[int, dict[str, str], list[str]] | None = None
    includes: list[tuple[str, Mapping[str, str]]] = []

    def start(name: str, attributes: Mapping[str, str]) -> None:
        nonlocal depth, current
        depth += 1
        local_name = name.rsplit(":", 1)[-1]
        if local_name in {"cachedir", "cache"}:
            raise OncoTracerError(
                "included Fontconfig configuration declares a cache target: " f"{path}"
            )
        if local_name == "include":
            if current is not None:
                raise OncoTracerError(
                    f"Fontconfig fragment has a nested include element: {path}"
                )
            current = (depth, dict(attributes), [])

    def characters(value: str) -> None:
        if current is not None:
            current[2].append(value)

    def end(name: str) -> None:
        nonlocal depth, current
        local_name = name.rsplit(":", 1)[-1]
        if current is not None and local_name == "include" and current[0] == depth:
            includes.append(("".join(current[2]), current[1]))
            if len(includes) > _MAX_FILES:
                raise OncoTracerError(
                    f"Fontconfig fragment has too many includes: {path}"
                )
            current = None
        depth -= 1

    parser.StartElementHandler = start
    parser.CharacterDataHandler = characters
    parser.EndElementHandler = end
    try:
        parser.Parse(payload, True)
    except OncoTracerError:
        raise
    except expat.ExpatError as error:
        raise OncoTracerError(
            f"Fontconfig configuration is malformed: {path}: {error}"
        ) from error
    if current is not None or depth != 0:
        raise OncoTracerError(f"Fontconfig include parsing was incomplete: {path}")
    return tuple(includes)


def _audit_graph(
    source: Path,
    *,
    xdg_config: Path,
    home: Path,
) -> tuple[ET.Element, tuple[tuple[object, ...], ...]]:
    records: list[tuple[object, ...]] = []
    visited: set[tuple[int, int]] = set()
    active: set[tuple[int, int]] = set()
    total_bytes = 0
    files = 0

    def visit(path: Path, depth: int, *, root: bool = False) -> ET.Element | None:
        nonlocal total_bytes, files
        if depth > _MAX_DEPTH:
            raise OncoTracerError("Fontconfig include graph is too deep")
        if len(records) >= _MAX_FILES:
            raise OncoTracerError("Fontconfig include graph exceeds safety bounds")
        try:
            path_metadata = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise OncoTracerError(
                f"cannot inspect Fontconfig include {path}: {error}"
            ) from error

        if stat.S_ISDIR(path_metadata.st_mode):
            try:
                with os.scandir(path) as entries:
                    selected: list[str] = []
                    for entry in entries:
                        if (
                            entry.name
                            and "0" <= entry.name[0] <= "9"
                            and entry.name.endswith(".conf")
                        ):
                            selected.append(entry.name)
                            if len(records) + len(selected) > _MAX_FILES:
                                raise OncoTracerError(
                                    "Fontconfig include directory exceeds safety bounds"
                                )
                    names = sorted(selected)
                after = path.lstat()
            except OncoTracerError:
                raise
            except OSError as error:
                raise OncoTracerError(
                    f"cannot enumerate Fontconfig include directory {path}: {error}"
                ) from error
            identity = (
                path_metadata.st_dev,
                path_metadata.st_ino,
                path_metadata.st_mode,
                path_metadata.st_mtime_ns,
            )
            if identity != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_mtime_ns,
            ):
                raise OncoTracerError(
                    f"Fontconfig include directory changed while read: {path}"
                )
            records.append(("directory", str(path), *identity, *names))
            for name in names:
                visit(path / name, depth + 1)
            return None

        payload, record, identity = _read_config(path)
        if identity in active:
            raise OncoTracerError(f"Fontconfig include graph contains a cycle: {path}")
        if identity in visited:
            records.append(("repeat", str(path), *identity))
            return None
        files += 1
        total_bytes += len(payload)
        if files > _MAX_FILES or total_bytes > _MAX_TOTAL_BYTES:
            raise OncoTracerError("Fontconfig include graph exceeds safety bounds")
        visited.add(identity)
        active.add(identity)
        records.append(record)
        try:
            if not root:
                for text, attributes in _scan_fragment(payload, path):
                    unexpected = set(attributes) - {
                        "ignore_missing",
                        "prefix",
                        "deprecated",
                    }
                    if unexpected:
                        raise OncoTracerError(
                            "Fontconfig fragment include has unsupported "
                            f"attributes: {path}"
                        )
                    ignore_missing = attributes.get("ignore_missing", "no")
                    if ignore_missing not in {"yes", "no"}:
                        raise OncoTracerError(
                            f"Fontconfig include has invalid ignore_missing: {path}"
                        )
                    node = ET.Element("include", attributes)
                    node.text = text
                    include = Path(
                        os.path.abspath(
                            _include_path(
                                node,
                                config_path=source.parent,
                                xdg_config=xdg_config,
                                home=home,
                            )
                        )
                    )
                    if not os.path.lexists(include):
                        records.append(
                            _missing_record(node, include, searched_at_runtime=True)
                        )
                        if len(records) > _MAX_FILES:
                            raise OncoTracerError(
                                "Fontconfig include graph exceeds safety bounds"
                            )
                        if ignore_missing != "yes":
                            raise OncoTracerError(
                                "required Fontconfig include is missing: " f"{include}"
                            )
                        continue
                    searched = _search_relative_include(node)
                    if searched is not None:
                        records.append(("search-existing", str(searched), str(include)))
                        if len(records) > _MAX_FILES:
                            raise OncoTracerError(
                                "Fontconfig include graph exceeds safety bounds"
                            )
                    visit(include, depth + 1)
                return None
            try:
                parsed = ET.fromstring(payload)
            except ET.ParseError as error:
                raise OncoTracerError(
                    f"Fontconfig root configuration is malformed: {path}: {error}"
                ) from error
            if parsed.tag != "fontconfig":
                raise OncoTracerError(
                    f"Fontconfig root configuration has an unexpected root: {path}"
                )
            direct = list(parsed)
            direct_ids = {id(node) for node in direct}
            for node in parsed.iter():
                if (
                    node.tag in {"dir", "remap-dir"}
                    and node.get("prefix") == "relative"
                ):
                    raise OncoTracerError(
                        "Fontconfig root uses wrapper-relative path semantics: "
                        f"{path}"
                    )
                if node.tag not in {"cachedir", "cache"}:
                    continue
                if id(node) in direct_ids:
                    continue
                raise OncoTracerError(
                    f"Fontconfig root has a nested cache target: {path}"
                )
            includes = [node for node in direct if node.tag == "include"]
            if any(
                node.tag == "include" and id(node) not in direct_ids
                for node in parsed.iter()
            ):
                raise OncoTracerError(f"Fontconfig root has a nested include: {path}")
            for node in includes:
                unexpected = set(node.attrib) - {
                    "ignore_missing",
                    "prefix",
                    "deprecated",
                }
                if unexpected:
                    raise OncoTracerError(
                        f"Fontconfig include has unsupported attributes: {path}"
                    )
                ignore_missing = node.get("ignore_missing", "no")
                if ignore_missing not in {"yes", "no"}:
                    raise OncoTracerError(
                        f"Fontconfig include has invalid ignore_missing: {path}"
                    )
                include = Path(
                    os.path.abspath(
                        _include_path(
                            node,
                            config_path=source.parent,
                            xdg_config=xdg_config,
                            home=home,
                        )
                    )
                )
                if not os.path.lexists(include):
                    records.append(
                        _missing_record(node, include, searched_at_runtime=False)
                    )
                    if len(records) > _MAX_FILES:
                        raise OncoTracerError(
                            "Fontconfig include graph exceeds safety bounds"
                        )
                    if ignore_missing != "yes":
                        raise OncoTracerError(
                            f"required Fontconfig include is missing: {include}"
                        )
                    parsed.remove(node)
                    continue
                visit(include, depth + 1)
                node.text = str(include)
                node.attrib.pop("prefix", None)
                node.set("ignore_missing", "no")
            return parsed
        finally:
            active.remove(identity)

    parsed_root = visit(source, 0, root=True)
    if parsed_root is None:
        raise OncoTracerError(f"Fontconfig root configuration is missing: {source}")
    return parsed_root, tuple(records)


@dataclass(frozen=True)
class _Contract:
    source: Path
    snapshot: tuple[tuple[object, ...], ...]
    wrapper: Path
    wrapper_identity: tuple[int, int]
    wrapper_sha256: str
    include_guard: Path
    guard_identity: tuple[object, ...]


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _guard_tree_identity(path: Path) -> tuple[object, ...]:
    """Bind the complete flat, read-only include-guard directory by descriptor."""
    directory: int | None = None
    try:
        named_before = path.lstat()
        directory = os.open(
            path,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_before = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_before.st_mode)
            or stat.S_IMODE(directory_before.st_mode) != 0o500
            or directory_before.st_uid != os.getuid()
            or _stable_metadata(named_before) != _stable_metadata(directory_before)
        ):
            raise OncoTracerError(
                f"Fontconfig include guard is not an owned physical mode-0500 directory: {path}"
            )
        names = sorted(os.listdir(directory))
        if len(names) > _MAX_FILES:
            raise OncoTracerError("Fontconfig include guard exceeds safety bounds")
        entries: list[tuple[object, ...]] = []
        for name in names:
            if not name or "/" in name or name in {".", ".."}:
                raise OncoTracerError(
                    f"Fontconfig include guard has an unsafe entry: {path / name}"
                )
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o400
                or before.st_uid != os.getuid()
                or before.st_size > _MAX_FILE_BYTES
            ):
                raise OncoTracerError(
                    f"Fontconfig include guard has a non-private entry: {path / name}"
                )
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
            try:
                opened_before = os.fstat(descriptor)
                chunks: list[bytes] = []
                remaining = _MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                opened_after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            named_after = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                _stable_metadata(before) != _stable_metadata(opened_before)
                or _stable_metadata(before) != _stable_metadata(opened_after)
                or _stable_metadata(before) != _stable_metadata(named_after)
                or len(payload) != before.st_size
            ):
                raise OncoTracerError(
                    f"Fontconfig include guard changed while read: {path / name}"
                )
            entries.append(
                (name, *_stable_metadata(before), hashlib.sha256(payload).hexdigest())
            )
        directory_after = os.fstat(directory)
        named_after = path.lstat()
    except OncoTracerError:
        raise
    except OSError as error:
        raise OncoTracerError(
            f"cannot inventory Fontconfig include guard {path}: {error}"
        ) from error
    finally:
        if directory is not None:
            os.close(directory)
    stable = _stable_metadata(directory_before)
    if stable != _stable_metadata(directory_after) or stable != _stable_metadata(
        named_after
    ):
        raise OncoTracerError(f"Fontconfig include guard changed while read: {path}")
    return (*stable, tuple(entries))


class FontconfigRuntime:
    """Invocation-private Fontconfig configuration and cache containment."""

    def __init__(self, root: Path, prefixes: Mapping[str, Path | None]):
        root = _private_directory(root, "native runtime cache")
        resolved_root = root.resolve(strict=True)
        for name, prefix in prefixes.items():
            if prefix is None:
                continue
            resolved_prefix = prefix.expanduser().resolve()
            if (
                resolved_root == resolved_prefix
                or resolved_prefix in resolved_root.parents
            ):
                raise OncoTracerError(
                    "native runtime containment must be outside configured "
                    f"{name} prefix: {resolved_root}"
                )
        self.root = resolved_root
        self.prefixes = dict(prefixes)
        self.home = _private_directory(self.root / "home", "native runtime home")
        self.xdg_cache = _private_directory(self.root / "xdg-cache", "native XDG cache")
        self.xdg_config = _private_directory(
            self.root / "xdg-config", "native XDG configuration"
        )
        self.xdg_data = _private_directory(self.root / "xdg-data", "native XDG data")
        self.matplotlib = _private_directory(
            self.root / "matplotlib", "native Matplotlib cache"
        )
        self.cache = _private_directory(
            self.root / "fontconfig-cache", "native Fontconfig cache root"
        )
        self.configs = _private_directory(
            self.root / "fontconfig-configs", "native Fontconfig wrapper root"
        )
        self.guards = _private_directory(
            self.root / "fontconfig-include-guards",
            "native Fontconfig include-guard root",
        )
        self._directory_identities = {
            path: _directory_identity(path, label)
            for path, label in (
                (self.root, "native runtime cache"),
                (self.home, "native runtime home"),
                (self.xdg_cache, "native XDG cache"),
                (self.xdg_config, "native XDG configuration"),
                (self.xdg_data, "native XDG data"),
                (self.matplotlib, "native Matplotlib cache"),
                (self.cache, "native Fontconfig cache root"),
                (self.configs, "native Fontconfig wrapper root"),
                (self.guards, "native Fontconfig include-guard root"),
            )
        }
        self._contracts: dict[str, _Contract] = {}

    def _prepare_include_guard(
        self, group: str, snapshot: tuple[tuple[object, ...], ...]
    ) -> tuple[Path, tuple[object, ...]]:
        if not group or Path(group).name != group:
            raise OncoTracerError(f"invalid Fontconfig tool group: {group!r}")
        guard = _private_directory(
            self.guards / group, f"native {group} Fontconfig include guard"
        )
        relative_paths: set[Path] = set()
        existing_paths: set[Path] = set()
        for record in snapshot:
            if len(record) == 4 and record[0] == "missing" and record[2] == "search":
                relative = _safe_relative_include(
                    str(record[3]), "missing Fontconfig search include"
                )
                if len(relative.parts) != 1:
                    raise OncoTracerError(
                        "missing Fontconfig search include requires an unsafe "
                        f"guard parent: {relative}"
                    )
                relative_paths.add(relative)
            elif len(record) == 3 and record[0] == "search-existing":
                existing_paths.add(
                    _safe_relative_include(
                        str(record[1]), "existing Fontconfig search include"
                    )
                )
        ordered = sorted(relative_paths, key=str)
        for path in ordered:
            if any(existing.parts[0] == path.name for existing in existing_paths):
                raise OncoTracerError(
                    "missing Fontconfig include guard would shadow an audited "
                    f"search include: {path}"
                )
        payload = b"<fontconfig/>\n"
        for relative in ordered:
            target = guard / relative.name
            try:
                descriptor = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fchmod(handle.fileno(), 0o400)
                    os.fsync(handle.fileno())
            except FileExistsError as error:
                raise OncoTracerError(
                    f"Fontconfig include guard already exists: {target}"
                ) from error
            except OSError as error:
                raise OncoTracerError(
                    f"cannot publish Fontconfig include guard {target}: {error}"
                ) from error
        try:
            guard.chmod(0o500)
        except OSError as error:
            raise OncoTracerError(
                f"cannot seal Fontconfig include guard {guard}: {error}"
            ) from error
        return guard, _guard_tree_identity(guard)

    def _source(self, group: str) -> Path:
        candidates: list[Path] = []
        prefix = self.prefixes.get(group)
        core = self.prefixes.get("core")
        if prefix is not None:
            candidates.append(prefix / "etc" / "fonts" / "fonts.conf")
        if core is not None and core != prefix:
            candidates.append(core / "etc" / "fonts" / "fonts.conf")
        candidates.append(Path("/etc/fonts/fonts.conf"))
        for candidate in candidates:
            if os.path.lexists(candidate):
                return candidate
        raise OncoTracerError(
            f"no physical Fontconfig configuration is available for {group}"
        )

    def _prepare(self, group: str) -> _Contract:
        existing = self._contracts.get(group)
        if existing is not None:
            return existing
        source = self._source(group)
        if os.pathsep in str(source.parent) or os.pathsep in str(self.guards):
            raise OncoTracerError(
                "Fontconfig search path contains an ambiguous path-list separator"
            )
        parsed, snapshot = _audit_graph(
            source, xdg_config=self.xdg_config, home=self.home
        )
        include_guard, guard_identity = self._prepare_include_guard(group, snapshot)
        direct = list(parsed)
        for node in direct:
            if node.tag in {"cachedir", "cache"}:
                parsed.remove(node)
        if any(node.tag in {"cachedir", "cache"} for node in parsed.iter()):
            raise OncoTracerError(
                f"Fontconfig root has a nested cache target: {source}"
            )
        group_cache = _private_directory(
            self.cache / group, f"native {group} Fontconfig cache"
        )
        self._directory_identities[group_cache] = _directory_identity(
            group_cache, f"native {group} Fontconfig cache"
        )
        cachedir = ET.Element("cachedir")
        cachedir.text = str(group_cache)
        parsed.insert(0, cachedir)
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            + ET.tostring(parsed, encoding="utf-8")
            + b"\n"
        )
        wrapper = self.configs / f"{group}.conf"
        try:
            descriptor = os.open(
                wrapper,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fchmod(handle.fileno(), 0o400)
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise OncoTracerError(
                f"native Fontconfig wrapper already exists: {wrapper}"
            ) from error
        except OSError as error:
            raise OncoTracerError(
                f"cannot publish native Fontconfig wrapper {wrapper}: {error}"
            ) from error
        metadata = _lstat(wrapper, "native Fontconfig wrapper")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.getuid()
        ):
            raise OncoTracerError(
                f"native Fontconfig wrapper is not an owned physical file: {wrapper}"
            )
        contract = _Contract(
            source=source,
            snapshot=snapshot,
            wrapper=wrapper,
            wrapper_identity=(metadata.st_dev, metadata.st_ino),
            wrapper_sha256=hashlib.sha256(payload).hexdigest(),
            include_guard=include_guard,
            guard_identity=guard_identity,
        )
        self._contracts[group] = contract
        self.validate()
        return contract

    def environment(self, group: str) -> dict[str, str | None]:
        contract = self._prepare(group)
        return {
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.xdg_cache),
            "XDG_CONFIG_HOME": str(self.xdg_config),
            "XDG_DATA_HOME": str(self.xdg_data),
            "MPLCONFIGDIR": str(self.matplotlib),
            "FONTCONFIG_FILE": str(contract.wrapper),
            "FONTCONFIG_PATH": os.pathsep.join(
                (str(contract.include_guard), str(contract.source.parent))
            ),
            "FONTCONFIG_SYSROOT": None,
        }

    def validate(self) -> None:
        for path, expected in self._directory_identities.items():
            observed = _directory_identity(path, "native runtime containment")
            if observed != expected:
                raise OncoTracerError(
                    f"native runtime containment identity changed: {path}"
                )
        for group, contract in self._contracts.items():
            _, snapshot = _audit_graph(
                contract.source,
                xdg_config=self.xdg_config,
                home=self.home,
            )
            if snapshot != contract.snapshot:
                raise OncoTracerError(
                    f"{group} Fontconfig configuration changed during native execution"
                )
            if _guard_tree_identity(contract.include_guard) != contract.guard_identity:
                raise OncoTracerError(
                    f"{group} Fontconfig include guard changed during execution"
                )
            metadata = _lstat(contract.wrapper, "native Fontconfig wrapper")
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_uid != os.getuid()
                or (metadata.st_dev, metadata.st_ino) != contract.wrapper_identity
            ):
                raise OncoTracerError(
                    f"{group} Fontconfig wrapper identity changed during execution"
                )
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    contract.wrapper,
                    os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != contract.wrapper_identity
                    or opened.st_size > _MAX_FILE_BYTES
                ):
                    raise OncoTracerError(
                        f"{group} Fontconfig wrapper changed during execution"
                    )
                chunks: list[bytes] = []
                remaining = _MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                after = os.fstat(descriptor)
            except OSError as error:
                raise OncoTracerError(
                    f"cannot re-read {group} Fontconfig wrapper: {error}"
                ) from error
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            stable_opened = (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_uid,
                opened.st_nlink,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            stable_after = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if stable_opened != stable_after or len(payload) != opened.st_size:
                raise OncoTracerError(f"{group} Fontconfig wrapper changed while read")
            if hashlib.sha256(payload).hexdigest() != contract.wrapper_sha256:
                raise OncoTracerError(
                    f"{group} Fontconfig wrapper changed during execution"
                )
