#!/usr/bin/env python3
"""Build the auditable single-file OncoTracer zipapp release executable."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


SOURCE_SHA256_DEFINITION = "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)"
PROVENANCE_PAYLOAD_PATH = "payload/provenance/native-v2-sources.json"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
PAYLOAD_ROOTS = ("bin", "examples", "params", "environments", "provenance")
NATIVE_PAYLOAD_EXCLUDED_PATHS = frozenset(
    {
        # Keep retired paths denied even if a future change reintroduces one.
        # Reference READMEs remain source-only; the executable ships runtime assets.
        "bin/cna_classifier_nf/README.md",
        "bin/scripts/install_oncotracer.sh",
        "examples/hcc1143_lpwgs/README.md",
        "examples/hcc1143_lpwgs/run_example.sh",
        "examples/prjna754199/PROVENANCE.md",
        "examples/prjna754199/README.md",
        "examples/prjna754199/run_example.sh",
        "bin/scripts/prepare_samurai_source.sh",
        "bin/scripts/run_ifcnv_ont_lpwgs.py",
        "bin/scripts/run_illumina_samurai_fastq.sh",
        "bin/scripts/run_ont_samurai_barcodes.sh",
    }
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise SystemExit(detail)
    return result.stdout.strip()


def _is_git_checkout(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == root


def git_commit(root: Path, reference: str = "HEAD") -> str:
    """Resolve *reference* to one exact Git commit."""
    commit = _git(root, "rev-parse", "--verify", f"{reference}^{{commit}}").lower()
    if not HEX_COMMIT.fullmatch(commit):
        raise SystemExit(f"Git returned an invalid commit ID: {commit!r}")
    return commit


def git_archive_sha256(root: Path, commit: str) -> str:
    """Compute the canonical, configuration-independent Git archive SHA-256."""
    process = subprocess.Popen(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "tar.umask=0002",
            "archive",
            "--format=tar",
            commit,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if (
        process.stdout is None or process.stderr is None
    ):  # pragma: no cover - Popen contract
        raise SystemExit("could not read git archive output")
    digest = hashlib.sha256()
    while chunk := process.stdout.read(1024 * 1024):
        digest.update(chunk)
    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
    process.stdout.close()
    process.stderr.close()
    returncode = process.wait()
    if returncode != 0:
        raise SystemExit(stderr or f"git archive failed with exit code {returncode}")
    return digest.hexdigest()


def git_tree_dirty(root: Path) -> bool:
    return bool(_git(root, "status", "--porcelain", "--untracked-files=all"))


def resolve_source_metadata(
    root: Path,
    source_commit: str | None,
    source_sha256: str | None,
    *,
    allow_unbound_development: bool = False,
) -> tuple[str | None, str | None, bool | None, str]:
    """Resolve exact metadata or an explicitly requested unbound development build."""
    if allow_unbound_development:
        if source_commit or source_sha256:
            raise SystemExit(
                "--allow-unbound-development cannot be combined with source metadata"
            )
        return None, None, None, "unbound-development"

    if bool(source_commit) != bool(source_sha256):
        raise SystemExit(
            "--source-commit and --source-sha256 must be supplied together"
        )

    checkout = _is_git_checkout(root)
    if source_commit is None or source_sha256 is None:
        if not checkout:
            raise SystemExit(
                "the build root is not a Git checkout; supply --source-commit and "
                "--source-sha256"
            )
        if git_tree_dirty(root):
            raise SystemExit(
                "refusing to derive release provenance from a dirty source tree; "
                "commit the source or supply the exact metadata explicitly"
            )
        source_commit = git_commit(root)
        source_sha256 = git_archive_sha256(root, source_commit)
        return source_commit, source_sha256, False, "embedded"

    source_commit = source_commit.lower()
    source_sha256 = source_sha256.lower()
    if not HEX_COMMIT.fullmatch(source_commit):
        raise SystemExit(
            "--source-commit must be a full 40-character hexadecimal commit ID"
        )
    if not HEX_SHA256.fullmatch(source_sha256):
        raise SystemExit("--source-sha256 must be a 64-character hexadecimal SHA-256")

    if not checkout:
        # In archive/container contexts the explicit pair is the caller's clean
        # source attestation. CI computes it immediately before creating context.
        return source_commit, source_sha256, False, "embedded"

    head_commit = git_commit(root)
    if source_commit != head_commit:
        raise SystemExit(
            f"--source-commit {source_commit} does not match checkout HEAD {head_commit}"
        )
    observed_sha256 = git_archive_sha256(root, source_commit)
    if source_sha256 != observed_sha256:
        raise SystemExit(
            "--source-sha256 does not match "
            f"{SOURCE_SHA256_DEFINITION}: expected {observed_sha256}"
        )
    return source_commit, source_sha256, git_tree_dirty(root), "embedded"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_main_module(staging: Path) -> None:
    main_module = staging / "__main__.py"
    main_module.write_text(
        "from oncotracer_cli.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    main_module.chmod(0o644)


def _payload_member_target(staging: Path, member_name: str) -> Path | None:
    relative = PurePosixPath(member_name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SystemExit(f"unsafe path in Git source archive: {member_name!r}")
    first = relative.parts[0]
    if first == "oncotracer_cli":
        forbidden = any(
            part == "__pycache__" or part.endswith((".pyc", ".pyo"))
            for part in relative.parts
        )
        return None if forbidden else staging.joinpath(*relative.parts)
    if first not in PAYLOAD_ROOTS:
        return None
    if relative.as_posix() in NATIVE_PAYLOAD_EXCLUDED_PATHS:
        return None
    forbidden = any(
        part == "__pycache__"
        or part == "work"
        or part == "nextflow.config"
        or part.startswith(".nextflow")
        or part.endswith((".pyc", ".pyo", ".nf"))
        for part in relative.parts
    )
    return None if forbidden else (staging / "payload").joinpath(*relative.parts)


def copy_payload_from_git_archive(
    root: Path,
    staging: Path,
    source_commit: str,
) -> None:
    """Materialize release payload files solely from *source_commit*."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "tar.umask=0002",
            "archive",
            "--format=tar",
            source_commit,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(
            detail or f"git archive failed with exit code {result.returncode}"
        )

    observed_roots: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.parts and (
                relative.parts[0] == "oncotracer_cli"
                or relative.parts[0] in PAYLOAD_ROOTS
            ):
                observed_roots.add(relative.parts[0])
            target = _payload_member_target(staging, member.name)
            if target is None:
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise SystemExit(
                    f"unsupported non-regular payload member in Git archive: {member.name}"
                )
            source = archive.extractfile(member)
            if source is None:  # pragma: no cover - tarfile contract for regular files
                raise SystemExit(f"could not read Git archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)

    required_roots = {"oncotracer_cli", *PAYLOAD_ROOTS}
    missing = sorted(required_roots - observed_roots)
    if missing:
        raise SystemExit(
            "required payload paths are missing from exact source commit "
            f"{source_commit}: {', '.join(missing)}"
        )
    _write_main_module(staging)


def copy_payload_from_tree(root: Path, staging: Path) -> None:
    """Copy an explicitly unbound or already-attested non-Git source tree."""
    for name in ("oncotracer_cli", *PAYLOAD_ROOTS):
        source_root = root / name
        if not source_root.is_dir() or source_root.is_symlink():
            raise SystemExit(
                f"required payload path is missing or unsafe: {source_root}"
            )
        for source in (source_root, *sorted(source_root.rglob("*"))):
            relative = source.relative_to(root).as_posix()
            target = _payload_member_target(staging, relative)
            if target is None:
                continue
            if source.is_symlink():
                raise SystemExit(f"payload source must not be a symlink: {source}")
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                raise SystemExit(f"unsupported payload source object: {source}")
    _write_main_module(staging)


def copy_payload(
    root: Path,
    staging: Path,
    source_commit: str | None = None,
) -> None:
    if source_commit is not None and _is_git_checkout(root):
        copy_payload_from_git_archive(root, staging, source_commit)
    else:
        copy_payload_from_tree(root, staging)


def write_build_metadata(
    staging: Path,
    source_commit: str | None,
    source_sha256: str | None,
    source_tree_dirty: bool | None,
    source_metadata_origin: str,
) -> None:
    provenance_payload = staging / PROVENANCE_PAYLOAD_PATH
    if not provenance_payload.is_file():
        raise SystemExit(
            f"required provenance payload is missing: {provenance_payload}"
        )
    payload_sha256 = sha256_file(provenance_payload)
    metadata = (
        '"""Generated immutable source metadata for this OncoTracer build."""\n\n'
        "from __future__ import annotations\n\n"
        'BUILD_METADATA_SCHEMA = "oncotracer-build-metadata-v1"\n'
        f"SOURCE_SHA256_DEFINITION = {SOURCE_SHA256_DEFINITION!r}\n"
        f"SOURCE_COMMIT = {source_commit!r}\n"
        f"SOURCE_SHA256 = {source_sha256!r}\n"
        f"SOURCE_TREE_DIRTY = {source_tree_dirty!r}\n"
        f"SOURCE_METADATA_ORIGIN = {source_metadata_origin!r}\n"
        "ONCOTRACER_SOURCE_COMMIT = SOURCE_COMMIT\n"
        "ONCOTRACER_SOURCE_SHA256 = SOURCE_SHA256\n"
        f"PROVENANCE_PAYLOAD_PATH = {PROVENANCE_PAYLOAD_PATH!r}\n"
        f"PROVENANCE_PAYLOAD_SHA256 = {payload_sha256!r}\n"
    )
    metadata_path = staging / "oncotracer_cli" / "_build_metadata.py"
    metadata_path.write_text(metadata, encoding="utf-8")
    metadata_path.chmod(0o644)


def validate_python_sources(staging: Path) -> None:
    """Compile native sources in memory without creating cleanup artifacts."""
    for source in sorted((staging / "oncotracer_cli").rglob("*.py")):
        try:
            compile(source.read_bytes(), str(source), "exec")
        except (SyntaxError, ValueError) as error:
            raise SystemExit(
                f"Python compilation failed for {source}: {error}"
            ) from error


def write_deterministic_zipapp(staging: Path, output: Path) -> None:
    """Write a byte-reproducible zipapp with normalized order, times, and modes."""
    try:
        with output.open("xb") as target:
            target.write(b"#!/usr/bin/env python3\n")
            with zipfile.ZipFile(
                target,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for source in sorted(
                    (path for path in staging.rglob("*") if path.is_file()),
                    key=lambda path: path.relative_to(staging).as_posix(),
                ):
                    relative = source.relative_to(staging).as_posix()
                    executable = bool(source.stat().st_mode & stat.S_IXUSR)
                    mode = 0o755 if executable else 0o644
                    info = zipfile.ZipInfo(relative, date_time=ZIP_TIMESTAMP)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    archive.writestr(info, source.read_bytes(), compresslevel=9)
    except FileExistsError as error:
        raise SystemExit(f"refusing to overwrite existing output: {output}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=Path("dist/oncotracer"))
    parser.add_argument(
        "--source-commit",
        help="exact full Git commit represented by the source payload",
    )
    parser.add_argument(
        "--source-sha256",
        help=f"source digest defined as {SOURCE_SHA256_DEFINITION}",
    )
    parser.add_argument(
        "--allow-unbound-development",
        action="store_true",
        help="embed explicitly unbound metadata for a non-release development build",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    (
        source_commit,
        source_sha256,
        source_tree_dirty,
        source_metadata_origin,
    ) = resolve_source_metadata(
        root,
        args.source_commit,
        args.source_sha256,
        allow_unbound_development=args.allow_unbound_development,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oncotracer-zipapp-") as directory:
        staging = Path(directory)
        copy_payload(root, staging, source_commit)
        write_build_metadata(
            staging,
            source_commit,
            source_sha256,
            source_tree_dirty,
            source_metadata_origin,
        )
        validate_python_sources(staging)
        write_deterministic_zipapp(staging, output)
    output.chmod(output.stat().st_mode | 0o755)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
