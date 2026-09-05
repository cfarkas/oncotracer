"""Streaming, checksum-verified transfer of the pinned hg38 reference/indexes.

Bundles contain only an explicit genome/index allowlist, never an arbitrary
archive. Import does not execute tools, build indexes, or deserialize models.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from . import engine
from .install_safety import _guard_dedicated, _rename_noreplace
from .runtime import CommandRunner, OncoTracerError, sha256_file

SCHEMA = "oncotracer-hg38-reference-bundle-v1"
BLOCK_BYTES = 1024 * 1024
MANIFEST_LIMIT = 2 * 1024 * 1024
STATE = ".oncotracer/reference-index-provenance/"
MANIFESTS = {
    "bwa": STATE + "samurai-hg38.bwa-index.json",
    "minimap2": STATE + "samurai-hg38-map-ont.minimap2-index.json",
}
INDEX_FILES = {
    "bwa": ["bwa/genome" + suffix for suffix in engine.BWA_INDEX_SUFFIXES],
    "minimap2": ["genome.fa.map-ont.mmi"],
}
HEX = re.compile(r"[0-9a-f]{64}")


def _files() -> dict[str, str]:
    return {
        **{name: "base" for name in engine.HG38_ASSETS},
        **{
            path: kind
            for kind in INDEX_FILES
            for path in [*INDEX_FILES[kind], MANIFESTS[kind]]
        },
    }


def _https(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def validate_manifest(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise OncoTracerError("not an OncoTracer hg38 reference-bundle manifest")
    if value.get("reference_sha256") != engine.HG38_ASSETS:
        raise OncoTracerError(
            "bundle genome identity differs from OncoTracer's pinned hg38"
        )
    if not isinstance(value.get("base_url"), str) or (
        value["base_url"] and not _https(value["base_url"])
    ):
        raise OncoTracerError("bundle base_url must be empty for local use, or HTTPS")
    records = value.get("files")
    if not isinstance(records, list) or not records or len(records) > len(_files()):
        raise OncoTracerError("invalid reference file inventory")
    seen, chunk_names = set(), set()
    for record in records:
        if not isinstance(record, dict) or record.get("path") not in _files():
            raise OncoTracerError("bundle contains an unexpected or unsafe path")
        name = record["path"]
        if name in seen or record.get("group") != _files()[name]:
            raise OncoTracerError("duplicate or incorrectly grouped reference file")
        seen.add(name)
        if (
            not isinstance(record.get("bytes"), int)
            or not 0 < record["bytes"] <= 20 * 1024**3
            or not HEX.fullmatch(str(record.get("sha256", "")))
        ):
            raise OncoTracerError("invalid reference file size or checksum")
        if name in engine.HG38_ASSETS and record["sha256"] != engine.HG38_ASSETS[name]:
            raise OncoTracerError(
                "bundle FASTA/FAI/dictionary hash differs from pinned hg38"
            )
        if name in MANIFESTS.values() and record["bytes"] > MANIFEST_LIMIT:
            raise OncoTracerError("index manifest exceeds the metadata size limit")
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 128:
            raise OncoTracerError("invalid chunk inventory")
        size = 0
        for chunk in chunks:
            if not isinstance(chunk, dict) or not re.fullmatch(
                r"hg38-[0-9]{2}-[0-9]{4}\.part", str(chunk.get("name", ""))
            ):
                raise OncoTracerError("unsafe chunk filename")
            if chunk["name"] in chunk_names:
                raise OncoTracerError("duplicate chunk name")
            chunk_names.add(chunk["name"])
            if (
                not isinstance(chunk.get("bytes"), int)
                or not 0 < chunk["bytes"] < 2 * 1024**3
                or not HEX.fullmatch(str(chunk.get("sha256", "")))
            ):
                raise OncoTracerError("invalid chunk size or checksum")
            size += chunk["bytes"]
        if size != record["bytes"]:
            raise OncoTracerError("chunk sizes do not equal the reference file size")
    if not set(engine.HG38_ASSETS).issubset(seen):
        raise OncoTracerError("bundle is missing the pinned base genome files")
    for kind in INDEX_FILES:
        expected = set([*INDEX_FILES[kind], MANIFESTS[kind]])
        if seen & expected and not expected.issubset(seen):
            raise OncoTracerError(f"incomplete {kind} index bundle")
    return value


def _verify_export_reference(
    root: Path, toolchain: engine.Toolchain, runner: CommandRunner
) -> None:
    engine._prepare_reference_state(root, owned=False)
    engine._prepare_hg38_base_locked(root, owned=False)
    for kind in INDEX_FILES:
        contract = engine._index_build_contract(
            kind,
            engine.HG38_ASSETS["genome.fa"],
            engine.HG38_ASSETS["genome.fa.fai"],
            toolchain.executable("core", kind),
        )
        manifest = root / MANIFESTS[kind]
        if kind == "bwa":
            valid = engine._bwa_manifest_matches(
                manifest, root / "bwa/genome", contract
            ) and engine._bwa_index_matches_fai(
                root / "genome.fa.fai", root / "bwa/genome"
            )
        else:
            index = root / "genome.fa.map-ont.mmi"
            valid = engine._minimap_manifest_matches(
                manifest, index, contract
            ) and engine._minimap_index_matches_fai(
                root / "genome.fa.fai",
                index,
                toolchain.executable("core", "minimap2"),
                runner,
                stage="reference-export-minimap2-read-check",
            )
        if not valid:
            raise OncoTracerError(
                f"cannot export: {kind} index or tool identity failed validation"
            )


def export_bundle(
    reference: Path,
    output: Path,
    *,
    base_url: str = "",
    core_prefix: Path | None = None,
    chunk_bytes: int = 1024**3,
) -> Path:
    reference = reference.expanduser().resolve(strict=True)
    output = _guard_dedicated(output, "reference bundle output")
    if os.path.lexists(output):
        raise OncoTracerError(f"will not overwrite a bundle output: {output}")
    if (
        base_url and not _https(base_url)
    ) or not BLOCK_BYTES <= chunk_bytes < 2 * 1024**3:
        raise OncoTracerError(
            "use an HTTPS base URL and chunks between 1 MiB and less than 2 GiB"
        )
    toolchain = engine.Toolchain(core_prefix=core_prefix)
    with (
        tempfile.TemporaryDirectory(
            prefix="oncotracer-reference-validation-"
        ) as temporary,
        contextlib.ExitStack() as locks,
    ):
        for kind in INDEX_FILES:
            lock, _ = engine._reference_state_paths(reference, kind)
            locks.enter_context(
                engine._reference_lock(lock, exclusive=False, create=False)
            )
        _verify_export_reference(
            reference,
            toolchain,
            CommandRunner(Path(temporary) / "trace.tsv", echo=False),
        )
        output.mkdir(parents=True)
        records = []
        for file_index, (name, kind) in enumerate(_files().items()):
            source = reference / name
            engine._require_physical_file(source, "bundle source")
            digest = hashlib.sha256()
            chunks = []
            total = 0
            with source.open("rb") as handle:
                while True:
                    first = handle.read(min(BLOCK_BYTES, chunk_bytes))
                    if not first:
                        break
                    part = f"hg38-{file_index:02d}-{len(chunks):04d}.part"
                    part_digest = hashlib.sha256()
                    count = 0
                    with (output / part).open("xb") as destination:
                        block = first
                        while block:
                            destination.write(block)
                            digest.update(block)
                            part_digest.update(block)
                            count += len(block)
                            if count == chunk_bytes:
                                break
                            block = handle.read(min(BLOCK_BYTES, chunk_bytes - count))
                    total += count
                    chunks.append(
                        {
                            "name": part,
                            "bytes": count,
                            "sha256": part_digest.hexdigest(),
                        }
                    )
                    print(
                        f"Prepared {part}: {count / 1024**2:.0f} MiB",
                        file=sys.stderr,
                        flush=True,
                    )
            records.append(
                {
                    "path": name,
                    "group": kind,
                    "bytes": total,
                    "sha256": digest.hexdigest(),
                    "chunks": chunks,
                }
            )
        # A second validation detects uncooperative writers ignoring reader locks.
        _verify_export_reference(
            reference,
            toolchain,
            CommandRunner(Path(temporary) / "final-trace.tsv", echo=False),
        )
    value = validate_manifest(
        {
            "schema": SCHEMA,
            "reference_sha256": engine.HG38_ASSETS,
            "base_url": base_url.rstrip("/"),
            "files": records,
        }
    )
    manifest = output / "hg38-reference.json"
    manifest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return manifest


def _read_manifest(
    source: str, expected_sha256: str | None
) -> tuple[dict, Path | None]:
    remote = _https(source)
    if "://" in source and not remote:
        raise OncoTracerError("remote manifests require HTTPS")
    if remote and not expected_sha256:
        raise OncoTracerError(
            "a remote reference manifest requires --sha256 from its trusted publisher"
        )
    if expected_sha256 and not HEX.fullmatch(expected_sha256):
        raise OncoTracerError("--sha256 must be 64 lowercase hexadecimal characters")
    with (
        urlopen(source, timeout=30) if remote else Path(source).expanduser().open("rb")
    ) as handle:
        raw = handle.read(MANIFEST_LIMIT + 1)
    if len(raw) > MANIFEST_LIMIT:
        raise OncoTracerError("reference manifest is too large")
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise OncoTracerError("reference manifest SHA-256 mismatch")
    try:
        value = validate_manifest(json.loads(raw))
    except (ValueError, UnicodeDecodeError) as error:
        raise OncoTracerError(f"invalid reference manifest: {error}") from error
    if remote and not value["base_url"]:
        raise OncoTracerError("remote manifest has no HTTPS chunk base URL")
    return value, None if remote else Path(source).expanduser().absolute().parent


def _verify_imported_indexes(root: Path, groups: set[str]) -> None:
    """Check index provenance without loading a whole-genome index into RAM.

    The normal analysis reader additionally verifies the exact installed tool
    identity and minimap2 readability before using this external reference.
    """
    for kind in groups - {"base"}:
        manifest = root / MANIFESTS[kind]
        try:
            data = json.loads(manifest.read_text())
            contract = data["build"]
            arguments = (
                ["index", "-p", "<PREFIX>", "<FASTA>"]
                if kind == "bwa"
                else ["-x", "map-ont", "-d", "<INDEX>", "<FASTA>"]
            )
            valid = (
                contract["schema"] == f"oncotracer-{kind}-build-contract-v1"
                and contract["fasta_sha256"] == engine.HG38_ASSETS["genome.fa"]
                and contract["fai_sha256"] == engine.HG38_ASSETS["genome.fa.fai"]
                and contract["logical_arguments"] == arguments
                and HEX.fullmatch(contract[f"{kind}_sha256"])
            )
            if kind == "bwa":
                valid = (
                    valid
                    and engine._bwa_manifest_matches(
                        manifest, root / "bwa/genome", contract
                    )
                    and engine._bwa_index_matches_fai(
                        root / "genome.fa.fai", root / "bwa/genome"
                    )
                )
            else:
                valid = valid and engine._minimap_manifest_matches(
                    manifest, root / "genome.fa.map-ont.mmi", contract
                )
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise OncoTracerError(
                f"downloaded {kind} index manifest does not match its files or pinned hg38"
            )


def install_bundle(
    source: str,
    lpwgs_root: Path,
    *,
    mode: str,
    expected_sha256: str | None = None,
    dry_run: bool = False,
) -> dict:
    value, local = _read_manifest(source, expected_sha256)
    wanted = {
        "base",
        *(
            {"illumina": ["bwa"], "ont": ["minimap2"], "both": ["bwa", "minimap2"]}[
                mode
            ]
        ),
    }
    records = [r for r in value["files"] if r["group"] in wanted]
    groups = {r["group"] for r in records}
    if groups != wanted:
        raise OncoTracerError(f"bundle does not contain all indexes needed for {mode}")
    root = lpwgs_root.expanduser().absolute()
    target = _guard_dedicated(root / "references/samurai_hg38", "reference destination")
    if os.path.lexists(target):
        raise OncoTracerError(
            f"reference destination already exists; choose a new --lpwgs-root: {target}"
        )
    size = sum(r["bytes"] for r in records)
    result = {
        "mode": mode,
        "destination": str(target),
        "bytes": size,
        "gib": round(size / 1024**3, 2),
        "dry_run": dry_run,
        "index_builds": 0,
        "note": "Uses a 1-MiB transfer buffer. Alignment still needs RAM and the exact indexing-tool identities recorded in the bundle manifests.",
    }
    if dry_run:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _guard_dedicated(target, "reference destination")
    if shutil.disk_usage(target.parent).free < size + 1024**3:
        raise OncoTracerError(
            f"reference import needs {size / 1024**3:.1f} GiB plus 1 GiB free headroom at {target.parent}"
        )
    with tempfile.TemporaryDirectory(
        prefix=".oncotracer-hg38-import-", dir=target.parent
    ) as temporary:
        stage = Path(temporary) / "reference"
        stage.mkdir()
        for record in records:
            destination = stage / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            full_digest = hashlib.sha256()
            with destination.open("xb") as output:
                for part in record["chunks"]:
                    location = (
                        local / part["name"]
                        if local
                        else value["base_url"] + "/" + part["name"]
                    )
                    with (
                        location.open("rb") if local else urlopen(location, timeout=30)
                    ) as incoming:
                        count, digest = 0, hashlib.sha256()
                        while block := incoming.read(BLOCK_BYTES):
                            count += len(block)
                            if count > part["bytes"]:
                                raise OncoTracerError(
                                    "reference chunk exceeds its declared size"
                                )
                            digest.update(block)
                            full_digest.update(block)
                            output.write(block)
                    if count != part["bytes"] or digest.hexdigest() != part["sha256"]:
                        raise OncoTracerError(
                            f"reference chunk size/hash mismatch: {part['name']}"
                        )
                    print(f"Verified {part['name']}", file=sys.stderr, flush=True)
            if (
                destination.stat().st_size != record["bytes"]
                or full_digest.hexdigest() != record["sha256"]
            ):
                raise OncoTracerError(
                    f"reference file size/hash mismatch: {record['path']}"
                )
        _verify_imported_indexes(stage, groups)
        # Reader locks are local synchronization objects, never downloaded.
        locks = stage / ".oncotracer/locks"
        locks.mkdir(parents=True)
        for kind in INDEX_FILES:
            lock, _ = engine._reference_state_paths(stage, kind)
            lock.touch(exist_ok=False)
        (stage / ".oncotracer/reference-bundle.json").write_text(
            json.dumps(value, indent=2) + "\n"
        )
        _rename_noreplace(stage, target, "validated reference import")
    return result


def command_reference(args) -> int:
    try:
        if args.reference_action == "export":
            manifest = export_bundle(
                Path(args.reference),
                Path(args.output),
                base_url=args.base_url,
                core_prefix=Path(args.core_prefix) if args.core_prefix else None,
            )
            print(
                json.dumps(
                    {"manifest": str(manifest), "sha256": sha256_file(manifest)},
                    indent=2,
                )
            )
        else:
            print(
                json.dumps(
                    install_bundle(
                        args.manifest,
                        Path(args.lpwgs_root),
                        mode=args.mode,
                        expected_sha256=args.sha256,
                        dry_run=args.dry_run,
                    ),
                    indent=2,
                )
            )
    except OSError as error:
        raise OncoTracerError(
            f"reference transfer failed without replacing an existing reference: {error}"
        ) from error
    return 0


def add_reference_command(subparsers) -> None:
    parser = subparsers.add_parser(
        "reference",
        help="Install prebuilt hg38 indexes, or export a validated reference for another computer",
    )
    actions = parser.add_subparsers(dest="reference_action", required=True)
    install = actions.add_parser(
        "install",
        help="Stream and verify a local or published bundle; never build an index",
    )
    install.add_argument(
        "--manifest",
        required=True,
        help="local hg38-reference.json or HTTPS publisher URL",
    )
    install.add_argument(
        "--sha256", help="manifest checksum, required for remote downloads"
    )
    install.add_argument(
        "--lpwgs-root",
        required=True,
        help="reference/cache parent; use this same lpwgs_root in your YAML",
    )
    install.add_argument(
        "--mode",
        choices=("illumina", "ont", "both"),
        required=True,
        help="download only indexes for the selected platform(s)",
    )
    install.add_argument(
        "--dry-run",
        action="store_true",
        help="read the manifest and show size/paths without downloading genome chunks",
    )
    install.set_defaults(func=command_reference)
    export = actions.add_parser(
        "export",
        help="Package existing validated BWA and minimap2 indexes; does not build them",
    )
    export.add_argument(
        "--reference", required=True, help="existing validated samurai_hg38 directory"
    )
    export.add_argument(
        "--output", required=True, help="new bundle directory; will not overwrite"
    )
    export.add_argument(
        "--base-url",
        default="",
        help="optional HTTPS directory where these chunks will be published",
    )
    export.add_argument(
        "--core-prefix",
        help="Conda environment containing the exact indexing tools; otherwise use PATH",
    )
    export.set_defaults(func=command_reference)
