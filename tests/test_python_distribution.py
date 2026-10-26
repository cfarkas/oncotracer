"""Distribution contracts without optional build tools or scientific execution."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import oncotracer_build_backend as backend
from oncotracer_cli import provenance
from oncotracer_cli import install_safety as safety
from scripts import verify_python_dist as verify

IDENTITY = {"source_commit": "a" * 40, "source_sha256": "b" * 64, "source_tree_dirty": False}
DIST = "oncotracer-2.1.1.dist-info/"


class PythonDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oncotracer-dist-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {name: b"runtime asset\n" for name in verify.REQUIRED}
        self.files[backend.PROVENANCE] = b'{"schema":"historical-test"}\n'
        self.files["oncotracer_cli/__init__.py"] = b'__version__ = "2.1.1"\n'
        self.metadata = b"Metadata-Version: 2.3\nName: oncotracer\nVersion: 2.1.1\n\nDescription\n"

    def wheel(self):
        path = self.root / "oncotracer-2.1.1-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
            archive.writestr(DIST + "METADATA", self.metadata)
            archive.writestr(DIST + "entry_points.txt", "[console_scripts]\noncotracer=oncotracer_cli.cli:main\n")
            archive.writestr(DIST + "WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
            archive.writestr(DIST + "RECORD", "")
        backend.stamp_wheel(path, IDENTITY)
        return path

    def sdist(self):
        path = self.root / "oncotracer-2.1.1.tar.gz"
        files = {**self.files, "oncotracer_build_backend.py": b"# backend\n", "PKG-INFO": self.metadata}
        with tarfile.open(path, "w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo("oncotracer-2.1.1/" + name)
                info.size = len(data); info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
        backend.stamp_sdist(path, IDENTITY)
        return path

    def stage_source(self):
        _, files, _ = backend._tar_files(self.sdist())
        root = self.root / "source"; root.mkdir()
        for name, data in files.items():
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return root

    def test_wheel_has_exact_identity_complete_payload_manifest_and_valid_record(self):
        result = verify.verify_archive(self.wheel(), IDENTITY, "2.1.1")
        self.assertEqual(result["kind"], "wheel")
        self.assertGreater(result["payload_files"], len(verify.REQUIRED))

    def test_sdist_has_exact_identity_and_all_build_inputs(self):
        result = verify.verify_archive(self.sdist(), IDENTITY, "2.1.1")
        self.assertEqual(result["kind"], "sdist")

    def test_build_stamping_is_deterministic(self):
        wheel = self.wheel(); first = wheel.read_bytes(); backend.stamp_wheel(wheel, IDENTITY)
        self.assertEqual(first, wheel.read_bytes())
        source = self.sdist(); first = source.read_bytes(); backend.stamp_sdist(source, IDENTITY)
        self.assertEqual(first, source.read_bytes())

    def test_sdist_staging_preserves_bound_identity_without_git(self):
        source = self.stage_source(); destination = self.root / "staged"; destination.mkdir()
        self.assertEqual(backend._stage_sdist(source, destination), IDENTITY)
        self.assertEqual((destination / "environments/native-variants.yml").read_bytes(), self.files["environments/native-variants.yml"])

    def test_modified_source_input_is_rejected(self):
        source = self.stage_source(); (source / "environments/native-variants.yml").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "input changed"):
            backend._stage_sdist(source, self.root / "staged")

    def test_unrecorded_source_input_is_rejected(self):
        source = self.stage_source(); (source / "unexpected.py").write_text("raise RuntimeError()")
        with self.assertRaisesRegex(ValueError, "Unrecorded"):
            backend._stage_sdist(source, self.root / "staged")

    def test_symlinked_source_input_is_rejected(self):
        source = self.stage_source()
        target = source / "environments/native-variants.yml"
        saved = target.with_suffix(".original"); target.rename(saved); target.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "missing or unsafe"):
            backend._stage_sdist(source, self.root / "staged")

    def test_unsafe_wheel_path_is_rejected(self):
        wheel = self.wheel()
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("../escaped", "unsafe")
        with self.assertRaisesRegex(ValueError, "Unsafe distribution path"):
            backend.stamp_wheel(wheel, IDENTITY)

    def test_dirty_or_incomplete_identity_is_rejected(self):
        for identity in ({}, {**IDENTITY, "source_tree_dirty": True}, {**IDENTITY, "source_commit": "HEAD"}):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "exact clean"):
                backend.stamp_wheel(self.wheel(), identity)

    def test_artifact_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "name/version"):
            verify.verify_archive(self.wheel(), IDENTITY, "9.9.9")

    def installed_payload(self):
        site = self.root / "site"; site.mkdir()
        with zipfile.ZipFile(self.wheel()) as archive:
            for name in archive.namelist():
                path = site / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(archive.read(name))
        manifest = site / backend.WHEEL_MANIFEST
        distribution = importlib.metadata.PathDistribution(site / DIST)
        for patch in (
            mock.patch.object(provenance, "__file__", str(site / "oncotracer_cli/provenance.py")),
            mock.patch.object(provenance, "SOURCE_COMMIT", IDENTITY["source_commit"]),
            mock.patch.object(provenance, "SOURCE_SHA256", IDENTITY["source_sha256"]),
            mock.patch.object(provenance._build_metadata, "DISTRIBUTION_MANIFEST_SHA256", hashlib.sha256(manifest.read_bytes()).hexdigest(), create=True),
            mock.patch.object(provenance.importlib.metadata, "distribution", return_value=distribution),
        ):
            patch.start(); self.addCleanup(patch.stop)
        self.assertTrue(provenance.verify_distribution_files())
        return site

    def test_installed_payload_integrity_rejects_tampered_resource(self):
        site = self.installed_payload()
        (site / "environments/native-variants.yml").write_text("changed caller pin")
        with self.assertRaisesRegex(provenance.ProvenanceError, "integrity verification"):
            provenance.verify_distribution_files()

    def test_installed_payload_rejects_symlinked_parent_directory(self):
        site = self.installed_payload()
        (site / "environments").rename(site / "moved-environments")
        (site / "environments").symlink_to(site / "moved-environments", target_is_directory=True)
        with self.assertRaisesRegex(provenance.ProvenanceError, "integrity verification"):
            provenance.verify_distribution_files()

    def test_installed_metadata_cannot_downgrade_to_unchecked_source_identity(self):
        site = self.installed_payload()
        manifest = site / backend.WHEEL_MANIFEST
        manifest.rename(manifest.with_name("removed-manifest.json"))
        metadata = site / backend.BUILD_METADATA
        metadata.write_bytes(metadata.read_bytes() + b"DISTRIBUTION_MANIFEST_SHA256 = None\n")
        with mock.patch.object(provenance._build_metadata, "DISTRIBUTION_MANIFEST_SHA256", None):
            with self.assertRaisesRegex(provenance.ProvenanceError, "pip RECORD"):
                provenance.verify_distribution_files()

    def test_managed_poetry_staging_retains_authenticated_source_identity(self):
        stage = self.root / "managed"; (stage / "provenance").mkdir(parents=True)
        (stage / "oncotracer_cli").mkdir()
        (stage / backend.PROVENANCE).write_bytes(self.files[backend.PROVENANCE])
        (stage / "oncotracer_build_backend.py").write_text("# PEP 517 backend\n")
        source = {"oncotracer_version": "2.1.1", "source_commit": IDENTITY["source_commit"], "source_sha256": IDENTITY["source_sha256"]}
        safety._write_poetry_build_metadata(stage, source)
        self.assertEqual(backend._stage_sdist(stage, self.root / "restaged"), IDENTITY)


if __name__ == "__main__":
    unittest.main()
