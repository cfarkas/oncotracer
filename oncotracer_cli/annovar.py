"""Optional, offline discovery and command planning for an existing ANNOVAR.

No installation, license acceptance, database download, decompression or execution
happens here. Annotation adds database descriptions, not clinical interpretation.
Commands follow https://annovar.openbioinformatics.org/en/latest/user-guide/startup/.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .runtime import OncoTracerError

_SCRIPTS = ("table_annovar.pl", "annotate_variation.pl", "convert2annovar.pl", "coding_change.pl")
_GENES = ("refGeneWithVer", "refGene")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./+\-]+$")
_CLINVAR = re.compile(r"^clinvar_[0-9]{8}$")


@dataclass(frozen=True)
class AnnovarDetection:
    available: bool
    reason: str
    build: str
    table_annovar: Path | None = None
    perl: Path | None = None
    database_dir: Path | None = None
    protocols: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    files: tuple[Path, ...] = ()
    version: str = ""
    file_provenance: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available, "reason": self.reason, "build": self.build,
            "table_annovar": str(self.table_annovar) if self.table_annovar else None,
            "perl": str(self.perl) if self.perl else None,
            "database_dir": str(self.database_dir) if self.database_dir else None,
            "protocols": list(self.protocols), "operations": list(self.operations),
            "files": [str(p) for p in self.files], "version": self.version,
            "file_provenance": [dict(item) for item in self.file_provenance],
        }


def _path(value: str | Path, home: str | None = None) -> Path:
    text = str(value)
    if text == "~" or text.startswith("~/"):
        text = str(Path(home or str(Path.home())) / text[2:])
    return Path(text).absolute().resolve()


def _usable(path: Path, *, executable: bool = False) -> bool:
    try:
        return (path.is_file() and path.stat().st_size > 0
                and os.access(path, os.R_OK)
                and (not executable or os.access(path, os.X_OK)))
    except OSError:
        return False


def _operation(protocol: str) -> str | None:
    if protocol in _GENES:
        return "g"
    if _CLINVAR.fullmatch(protocol):
        return "f"
    return None


def _database_files(directory: Path, build: str, protocol: str) -> tuple[Path, ...]:
    main = directory / f"{build}_{protocol}.txt"
    required = (main, directory / f"{build}_{protocol}Mrna.fa") if protocol in _GENES else (main,)
    if not all(_usable(p) for p in required):
        return ()
    index = Path(str(main) + ".idx")
    return required + ((index,) if _usable(index) else ())


def _provenance(path: Path, *, script: bool = False) -> dict[str, object]:
    stat = path.stat()
    item: dict[str, object] = {"path": str(path), "size_bytes": stat.st_size,
                              "mtime_ns": stat.st_mtime_ns}
    # Discovery is bounded: fingerprint scripts and small assets, not multi-GB DBs.
    if script or stat.st_size <= 1024 * 1024:
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        item["identity_method"] = "sha256_and_stat"
    else:
        item["identity_method"] = "stat_only_not_content_verified"
    return item


def discover_annovar(
    build: str = "hg38", *, annovar_dir: str | Path | None = None,
    database_dir: str | Path | None = None,
    environment: Mapping[str, str | None] | None = None,
    search_roots: Sequence[str | Path] = (),
    protocols: Sequence[str] | None = None,
) -> AnnovarDetection:
    """Inspect bounded local candidates; return an explicit optional-skip reason.

    Only exact hg19/hg38 builds are supported; no build aliasing or liftover.
    Environment entries override the current process environment (None removes).
    Explicit paths and ANNOVAR_HOME/ANNOVAR_DIR/ANNOVAR_DB overrides never fall
    back silently. Search roots may be an installation or contain tools/annovar.
    Automatic protocols are one complete RefSeq database and latest unpacked
    ClinVar. Explicit protocols currently support those same database families.
    """
    if build not in {"hg19", "hg38"}:
        return AnnovarDetection(False, "Unsupported build; choose exactly hg19 or hg38", build)
    requested = tuple(protocols) if protocols is not None else None
    if requested is not None and (not requested or isinstance(protocols, str)
            or len(set(requested)) != len(requested)
            or any(_operation(p) is None for p in requested)):
        return AnnovarDetection(False, "Protocols must be unique refGene/refGeneWithVer or dated clinvar_YYYYMMDD names", build)
    env = dict(os.environ)
    for key, value in (environment or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    home = env.get("HOME")
    perl_name = shutil.which("perl", path=env.get("PATH", ""))
    if not perl_name:
        return AnnovarDetection(False, "Perl executable is unavailable on PATH", build)
    perl = _path(perl_name)
    override = annovar_dir or env.get("ANNOVAR_HOME") or env.get("ANNOVAR_DIR")
    db_override = database_dir or env.get("ANNOVAR_DB") or env.get("ANNOVAR_DATABASE_DIR")
    candidates: list[Path] = []
    if override:
        candidates.append(_path(override, home))
    else:
        on_path = shutil.which("table_annovar.pl", path=env.get("PATH", ""))
        if on_path:
            candidates.append(_path(on_path).parent)
        if home:
            candidates.append(_path(Path(home) / "annovar"))
        for root in search_roots:
            resolved = _path(root, home)
            candidates.extend((resolved, resolved / "annovar", resolved / "tools" / "annovar"))
    failures: list[str] = []
    for install in dict.fromkeys(candidates):
        scripts = tuple(install / name for name in _SCRIPTS)
        if not all(_usable(p, executable=True) for p in scripts):
            failures.append(f"Incomplete executable ANNOVAR installation: {install}")
            continue
        database = _path(db_override, home) if db_override else install / "humandb"
        if not database.is_dir():
            failures.append(f"Database directory unavailable: {database}")
            continue
        selected = requested
        if selected is None:
            gene = next((p for p in _GENES if _database_files(database, build, p)), None)
            if gene is None:
                failures.append(f"No complete {build} RefSeq TXT and Mrna FASTA in {database}")
                continue
            clinvar = sorted((p.stem[len(build) + 1:] for p in database.glob(f"{build}_clinvar_*.txt")
                              if _CLINVAR.fullmatch(p.stem[len(build) + 1:]) and _usable(p)), reverse=True)
            selected = (gene,) + tuple(clinvar[:1])
        db_files: list[Path] = []
        missing = []
        for protocol in selected:
            present = _database_files(database, build, protocol)
            if not present:
                missing.append(protocol)
            db_files.extend(present)
        if missing:
            failures.append(f"Missing unpacked {build} database files for: {', '.join(missing)}")
            continue
        files = scripts + tuple(dict.fromkeys(db_files))
        if any(not _SAFE_PATH.fullmatch(str(p)) for p in (perl, *files, database)):
            failures.append("ANNOVAR paths contain spaces or shell metacharacters unsupported by its internal shell commands")
            continue
        try:
            with scripts[0].open(encoding="utf-8", errors="replace") as handle:
                header = handle.read(8192)
            revision = re.search(r"\$Revision: ([^$]+)\$", header)
            date = re.search(r"\$Date: ([^$]+)\$", header)
            version = "; ".join(m.group(1).strip() for m in (revision, date) if m) or "not_reported"
            provenance = tuple(_provenance(p, script=p in scripts) for p in files)
        except OSError as exc:
            failures.append(f"Unable to inspect ANNOVAR resources: {exc}")
            continue
        return AnnovarDetection(True, "Matching local annotation resources available", build,
                                scripts[0], perl, database, selected,
                                tuple(_operation(p) or "" for p in selected), files, version, provenance)
    reason = "; ".join(failures) if failures else "No local ANNOVAR installation found"
    return AnnovarDetection(False, reason, build, perl=perl)


def expected_outputs(detection: AnnovarDetection, prefix: str | Path) -> list[Path]:
    if not detection.available:
        raise OncoTracerError(f"Optional ANNOVAR annotation unavailable: {detection.reason}")
    return [Path(f"{prefix}.{detection.build}_multianno.{suffix}") for suffix in ("txt", "vcf")]


def build_annovar_command(
    detection: AnnovarDetection, vcf: str | Path, prefix: str | Path,
) -> list[str]:
    """Plan a serial annotation command, refusing paths ANNOVAR cannot quote.

    This function creates nothing. The caller owns isolated output directories,
    input validity/build checks, execution, logs and output validation. A planned
    input may not exist yet (dry-run). Existing prefix artifacts are never reused.
    """
    if not detection.available:
        raise OncoTracerError(f"Optional ANNOVAR annotation unavailable: {detection.reason}")
    assert detection.table_annovar and detection.perl and detection.database_dir
    input_path, output_prefix = _path(vcf), _path(prefix)
    for path in (input_path, output_prefix, detection.table_annovar, detection.perl, detection.database_dir):
        if not _SAFE_PATH.fullmatch(str(path)):
            raise OncoTracerError("ANNOVAR requires paths without spaces or shell metacharacters")
    for protected in (detection.table_annovar.parent, detection.database_dir):
        if output_prefix == protected or protected in output_prefix.parents:
            raise OncoTracerError("ANNOVAR outputs must be outside installation and database directories")
    if output_prefix == input_path or output_prefix.exists():
        raise OncoTracerError("ANNOVAR output prefix would overwrite an existing input or file")
    if output_prefix.parent.exists() and any(output_prefix.parent.glob(output_prefix.name + ".*")):
        raise OncoTracerError("ANNOVAR output prefix already has files; use a fresh output prefix")
    return [str(detection.perl), str(detection.table_annovar), str(input_path),
            str(detection.database_dir), "-buildver", detection.build, "-out", str(output_prefix),
            "-protocol", ",".join(detection.protocols), "-operation", ",".join(detection.operations),
            "-vcfinput", "-nastring", ".", "-polish", "-thread", "1"]
