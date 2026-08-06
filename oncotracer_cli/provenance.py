"""Read and report immutable OncoTracer source and binary provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Sequence

from . import __version__
from ._build_metadata import (
    BUILD_METADATA_SCHEMA,
    PROVENANCE_PAYLOAD_PATH,
    PROVENANCE_PAYLOAD_SHA256,
    SOURCE_COMMIT,
    SOURCE_METADATA_ORIGIN,
    SOURCE_SHA256,
    SOURCE_SHA256_DEFINITION,
    SOURCE_TREE_DIRTY,
)


PROVENANCE_SCHEMA = "oncotracer-provenance-v1"


class ProvenanceError(RuntimeError):
    """Raised when embedded provenance is absent, malformed, or inconsistent."""


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 of *path* without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _zipapp_path() -> Path | None:
    """Return the archive providing this module when imported from a zipapp."""
    archive = getattr(globals().get("__loader__"), "archive", None)
    if archive:
        candidate = Path(str(archive)).expanduser()
        if candidate.is_file() and zipfile.is_zipfile(candidate):
            return candidate.resolve()
    return None


def _payload_bytes(explicit: Path | None = None) -> bytes:
    if explicit is not None:
        return explicit.expanduser().resolve().read_bytes()

    checkout_payload = Path(__file__).resolve().parents[1] / "provenance" / "native-v2-sources.json"
    if checkout_payload.is_file():
        return checkout_payload.read_bytes()

    archive = _zipapp_path()
    if archive is not None:
        with zipfile.ZipFile(archive) as bundle:
            try:
                return bundle.read(PROVENANCE_PAYLOAD_PATH)
            except KeyError as error:
                raise ProvenanceError(
                    f"standalone executable lacks {PROVENANCE_PAYLOAD_PATH}"
                ) from error
    raise ProvenanceError("native v2 source provenance payload was not found")


def historical_source_provenance(explicit: Path | None = None) -> dict[str, object]:
    """Load the stable historical input hashes and verify the embedded payload."""
    payload = _payload_bytes(explicit)
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if PROVENANCE_PAYLOAD_SHA256 and observed_sha256 != PROVENANCE_PAYLOAD_SHA256:
        raise ProvenanceError(
            "native v2 source provenance payload SHA-256 mismatch: "
            f"expected {PROVENANCE_PAYLOAD_SHA256}, observed {observed_sha256}"
        )
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ProvenanceError("native v2 source provenance payload is invalid JSON") from error
    if not isinstance(value, dict) or value.get("schema") != "oncotracer-native-v2-sources-v1":
        raise ProvenanceError("native v2 source provenance payload has an unknown schema")
    if not isinstance(value.get("historical_sources"), dict):
        raise ProvenanceError("native v2 source provenance payload lacks historical sources")
    return value


def _checkout_source_metadata() -> tuple[str, str, bool] | None:
    """Return canonical HEAD metadata when running from a Git checkout."""
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    try:
        top_level = Path(probe.stdout.strip()).resolve()
    except OSError:
        return None
    if top_level != root:
        return None
    commit_result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if commit_result.returncode != 0:
        return None
    commit = commit_result.stdout.strip().lower()
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
    if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract
        return None
    digest = hashlib.sha256()
    while chunk := process.stdout.read(1024 * 1024):
        digest.update(chunk)
    process.stderr.read()
    process.stdout.close()
    process.stderr.close()
    if process.wait() != 0:
        return None
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return None
    return commit, digest.hexdigest(), bool(status.stdout.strip())


def _source_metadata() -> tuple[str | None, str | None, bool | None, str]:
    if bool(SOURCE_COMMIT) != bool(SOURCE_SHA256):
        raise ProvenanceError("embedded source commit and SHA-256 are incomplete")
    if SOURCE_COMMIT and SOURCE_SHA256:
        return SOURCE_COMMIT, SOURCE_SHA256, SOURCE_TREE_DIRTY, SOURCE_METADATA_ORIGIN or "embedded"
    if SOURCE_METADATA_ORIGIN == "unbound-development":
        return None, None, SOURCE_TREE_DIRTY, SOURCE_METADATA_ORIGIN
    checkout = _checkout_source_metadata()
    if checkout is not None:
        commit, source_sha256, dirty = checkout
        return commit, source_sha256, dirty, "git-checkout"
    return None, None, SOURCE_TREE_DIRTY, SOURCE_METADATA_ORIGIN or "unavailable"


def get_provenance(binary_path: Path | None = None) -> dict[str, object]:
    """Return machine-readable source, payload, and running-binary provenance."""
    payload = historical_source_provenance()
    source_commit, source_sha256, source_tree_dirty, source_origin = _source_metadata()
    archive = binary_path.expanduser().resolve() if binary_path is not None else _zipapp_path()
    binary_sha256 = sha256_file(archive) if archive is not None and archive.is_file() else None
    return {
        "schema": PROVENANCE_SCHEMA,
        "build_metadata_schema": BUILD_METADATA_SCHEMA,
        "oncotracer_version": __version__,
        "source_commit": source_commit,
        "source_sha256": source_sha256,
        "source_sha256_definition": SOURCE_SHA256_DEFINITION,
        "source_metadata_origin": source_origin,
        "source_tree_dirty": source_tree_dirty,
        "binary_path": str(archive) if archive is not None else None,
        "binary_sha256": binary_sha256,
        "provenance_payload_path": PROVENANCE_PAYLOAD_PATH,
        "provenance_payload_sha256": hashlib.sha256(_payload_bytes()).hexdigest(),
        "historical_sources_schema": payload["schema"],
        "historical_sources_note": payload.get("note"),
        "historical_sources": payload["historical_sources"],
    }


def release_provenance(binary_path: Path | None = None) -> dict[str, object]:
    """Return provenance only when it is complete and release-safe."""
    record = get_provenance(binary_path)
    if not record["source_commit"] or not record["source_sha256"]:
        raise ProvenanceError("exact source provenance is unavailable")
    if record["source_tree_dirty"] is not False:
        raise ProvenanceError("source provenance is not explicitly marked as a clean tree")
    return record


def provenance_record(binary_path: Path | None = None) -> dict[str, object]:
    """Compatibility alias for :func:`get_provenance`."""
    return get_provenance(binary_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the complete JSON record")
    args = parser.parse_args(argv)
    record = get_provenance()
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        for key in (
            "oncotracer_version",
            "source_commit",
            "source_sha256",
            "source_sha256_definition",
            "source_tree_dirty",
            "binary_sha256",
        ):
            value = record[key]
            print(f"{key}={value if value is not None else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
