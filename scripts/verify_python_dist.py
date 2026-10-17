#!/usr/bin/env python3
"""Verify wheel/sdist identity, metadata, assets and every recorded payload byte."""
from __future__ import annotations

import argparse
import ast
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oncotracer_build_backend as backend

REQUIRED = {
    "oncotracer_cli/cli.py", "oncotracer_cli/provenance.py", backend.BUILD_METADATA,
    backend.PROVENANCE, "bin/scripts/native_qdnaseq.R", "main.nf", "nextflow.config",
    "oncotracer_cli/lpwgs/main.nf", "oncotracer_cli/lpwgs/nextflow.config",
    "oncotracer_cli/lpwgs/bin/bcftools_compat.py",
    *("environments/native-" + name + ".yml" for name in (
        "core", "qdnaseq", "ichorcna", "classifier", "gistic2", "variants", "ffperase", "strelka2")),
}


def metadata_values(data):
    values = {}
    for node in ast.parse(data.decode()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    return values


def verify_files(files, manifest_name, schema, identity):
    manifest = json.loads(files[manifest_name])
    if manifest.get("schema") != schema or backend._identity(manifest) != identity:
        raise ValueError("distribution manifest source identity mismatch")
    records = manifest.get("files")
    if not isinstance(records, dict) or not records:
        raise ValueError("missing distribution file records")
    for name, record in records.items():
        backend._safe_name(name)
        if name not in files or backend._file_record(files[name]) != record:
            raise ValueError("distribution payload mismatch: " + name)
    return set(records)


def verify_archive(path, identity, version):
    wheel = path.suffix == ".whl"
    if wheel:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            files = {backend._safe_name(info.filename): archive.read(info) for info in infos}
            if len(files) != len(infos) or any((info.external_attr >> 16) & 0o170000 == 0o120000 for info in infos):
                raise ValueError("unsafe or duplicate wheel member")
        record_names = [name for name in files if name.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise ValueError("wheel needs exactly one RECORD")
        record_name = record_names[0]
        rows = list(csv.reader(io.StringIO(files[record_name].decode())))
        if len(rows) != len(files) or {row[0] for row in rows} != set(files):
            raise ValueError("wheel RECORD is incomplete or duplicated")
        for name, digest, size in rows:
            if name == record_name:
                if digest or size:
                    raise ValueError("wheel RECORD must not hash itself")
            else:
                expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
                if digest != expected or size != str(len(files[name])):
                    raise ValueError("wheel RECORD mismatch: " + name)
        names = verify_files(files, backend.WHEEL_MANIFEST, "oncotracer-wheel-files-v1", identity)
        if names != set(files) - {record_name, backend.BUILD_METADATA, backend.WHEEL_MANIFEST}:
            raise ValueError("wheel payload manifest is incomplete")
        metadata_name = record_name.removesuffix("RECORD") + "METADATA"
        entrypoints = record_name.removesuffix("RECORD") + "entry_points.txt"
        if "oncotracer=oncotracer_cli.cli:main" not in files[entrypoints].decode().replace(" ", ""):
            raise ValueError("wheel lacks the native console entry")
    else:
        _, files, _ = backend._tar_files(path)
        names = verify_files(files, backend.SDIST_MANIFEST, "oncotracer-sdist-source-v1", identity)
        if names != set(files) - {backend.SDIST_MANIFEST}:
            raise ValueError("sdist source manifest is incomplete")
        if "oncotracer_build_backend.py" not in files:
            raise ValueError("sdist lacks its build backend")
        metadata_name = "PKG-INFO"
    missing = REQUIRED - set(files)
    if missing:
        raise ValueError("distribution lacks runtime assets: " + ", ".join(sorted(missing)))
    if any("__pycache__" in Path(name).parts or name.endswith((".pyc", ".pyo")) for name in files):
        raise ValueError("distribution includes interpreter caches")
    metadata = BytesParser().parsebytes(files[metadata_name])
    if metadata["Name"] != "oncotracer" or metadata["Version"] != version:
        raise ValueError("distribution package name/version mismatch")
    values = metadata_values(files[backend.BUILD_METADATA])
    for key, expected in (("SOURCE_COMMIT", identity["source_commit"]), ("SOURCE_SHA256", identity["source_sha256"]), ("SOURCE_TREE_DIRTY", False)):
        if values.get(key) != expected:
            raise ValueError("embedded source metadata mismatch: " + key)
    if values.get("PROVENANCE_PAYLOAD_SHA256") != hashlib.sha256(files[backend.PROVENANCE]).hexdigest():
        raise ValueError("historical provenance payload hash mismatch")
    if wheel and values.get("DISTRIBUTION_MANIFEST_SHA256") != hashlib.sha256(files[backend.WHEEL_MANIFEST]).hexdigest():
        raise ValueError("wheel manifest hash mismatch")
    return {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size, "payload_files": len(files), "version": version, "kind": "wheel" if wheel else "sdist"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    identity = backend._identity({"source_commit": args.expected_commit, "source_sha256": args.expected_source_sha256, "source_tree_dirty": False})
    wheels = sorted(args.dist_dir.glob("*.whl")); sources = sorted(args.dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("the distribution directory must contain exactly one wheel and one sdist")
    records = [verify_archive(path, identity, args.expected_version) for path in wheels + sources]
    result = {"passed": True, **identity, "version": args.expected_version, "analysis_started": False, "artifacts": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
