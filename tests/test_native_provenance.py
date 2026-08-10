from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest import mock

from oncotracer_cli import cli
from oncotracer_cli import provenance
from scripts import build_native_binary as builder


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_native_binary.py"
HISTORICAL_NATIVE_SOURCE_SHA256 = (
    "f374c8f80dd320ab7de0be13bb6ad6ad9a82dfdb725f318893322efee6deb4a1"
)
CLASSIFIER_OVERLAY_SHA256 = (
    "75fc9bf97e0312e3aa550fa8290e0e1aa8c8a4127f842ba1718a0239aff956e6"
)


class NativeProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="oncotracer-provenance-test-"
        )
        cls.temp_root = Path(cls.temporary.name)
        cls.source_root = cls.temp_root / "source"
        for name in (
            "oncotracer_cli",
            "bin",
            "examples",
            "params",
            "environments",
            "provenance",
        ):
            shutil.copytree(
                ROOT / name,
                cls.source_root / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        (cls.source_root / ".gitignore").write_text(
            "*.ignored-sentinel\n__pycache__/\n*.py[cod]\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(cls.source_root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(cls.source_root),
                "config",
                "user.name",
                "OncoTracer test",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(cls.source_root),
                "config",
                "user.email",
                "oncotracer-test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(cls.source_root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(cls.source_root), "commit", "-q", "-m", "fixture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(cls.source_root), "config", "tar.umask", "0022"],
            check=True,
        )
        cls.ignored_sentinel = cls.source_root / "bin" / "payload.ignored-sentinel"
        cls.ignored_sentinel.write_text("must not be packaged\n", encoding="utf-8")
        status = subprocess.run(
            [
                "git",
                "-C",
                str(cls.source_root),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            raise AssertionError(
                f"provenance fixture is unexpectedly dirty: {status.stdout}"
            )
        cls.commit = builder.git_commit(cls.source_root)
        cls.source_sha256 = builder.git_archive_sha256(cls.source_root, cls.commit)
        cls.first_binary = cls.temp_root / "oncotracer-first"
        cls.second_binary = cls.temp_root / "oncotracer-second"
        for output in (cls.first_binary, cls.second_binary):
            subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--root",
                    str(cls.source_root),
                    "--output",
                    str(output),
                    "--source-commit",
                    cls.commit,
                    "--source-sha256",
                    cls.source_sha256,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_source_hash_has_one_canonical_definition(self) -> None:
        archived = subprocess.run(
            [
                "git",
                "-C",
                str(self.source_root),
                "-c",
                "tar.umask=0002",
                "archive",
                "--format=tar",
                self.commit,
            ],
            check=True,
            capture_output=True,
        ).stdout
        configured_archive = subprocess.run(
            [
                "git",
                "-C",
                str(self.source_root),
                "archive",
                "--format=tar",
                self.commit,
            ],
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(
            builder.SOURCE_SHA256_DEFINITION,
            "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)",
        )
        self.assertEqual(hashlib.sha256(archived).hexdigest(), self.source_sha256)
        self.assertNotEqual(
            hashlib.sha256(configured_archive).hexdigest(),
            self.source_sha256,
        )

    def test_two_consecutive_builds_are_byte_identical(self) -> None:
        self.assertEqual(
            self.first_binary.read_bytes(), self.second_binary.read_bytes()
        )
        self.assertEqual(
            provenance.sha256_file(self.first_binary),
            provenance.sha256_file(self.second_binary),
        )

    def test_zipapp_is_normalized_and_contains_no_forbidden_payload(self) -> None:
        with zipfile.ZipFile(self.first_binary) as archive:
            names = archive.namelist()
            forbidden = [
                name
                for name in names
                if "__pycache__" in PurePosixPath(name).parts
                or PurePosixPath(name).suffix in {".pyc", ".pyo", ".nf"}
            ]
            self.assertEqual(forbidden, [])
            self.assertTrue(names)
            self.assertEqual(names, sorted(names))
            self.assertTrue(
                all(
                    info.date_time == builder.ZIP_TIMESTAMP
                    for info in archive.infolist()
                )
            )
            self.assertIn("oncotracer_cli/_build_metadata.py", names)
            self.assertIn("payload/provenance/native-v2-sources.json", names)
            self.assertIn("payload/bin/scripts/MARLIN-MIT-LICENSE.txt", names)
            self.assertNotIn("payload/bin/payload.ignored-sentinel", names)
            obsolete_methylation_launchers = {
                "payload/bin/scripts/run_ont_methylation_pod5_barcodes.sh",
                "payload/bin/scripts/run_ont_sturgeon_samples.sh",
                "payload/bin/scripts/run_ont_marlin_leukemia.sh",
                "payload/bin/scripts/run_ont_alma_epigenetic_predictions.sh",
                "payload/bin/scripts/run_ont_clinical_methylation_and_variants.sh",
                "payload/bin/scripts/sturgeon_consolidate_report.sh",
            }
            self.assertTrue(obsolete_methylation_launchers.isdisjoint(names))

    def test_copied_executable_reports_embedded_source_and_own_hash(self) -> None:
        outside = self.temp_root / "outside-checkout"
        outside.mkdir()
        copied = outside / "oncotracer"
        shutil.copy2(self.first_binary, copied)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        version = subprocess.run(
            [str(copied), "--version"],
            cwd=outside,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("OncoTracer 2.0.0", version.stdout)
        result = subprocess.run(
            [str(copied), "provenance", "--json"],
            cwd=outside,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        record = json.loads(result.stdout)
        self.assertEqual(record["source_commit"], self.commit)
        self.assertEqual(record["source_sha256"], self.source_sha256)
        self.assertEqual(record["source_metadata_origin"], "embedded")
        self.assertIs(record["source_tree_dirty"], False)
        self.assertEqual(record["binary_sha256"], provenance.sha256_file(copied))

    def test_dirty_checkout_is_recorded_but_untracked_payload_is_excluded(self) -> None:
        sentinel = self.source_root / "bin" / "untracked-payload-sentinel.txt"
        output = self.temp_root / "oncotracer-dirty"
        sentinel.write_text("must not be packaged\n", encoding="utf-8")
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--root",
                    str(self.source_root),
                    "--output",
                    str(output),
                    "--source-commit",
                    self.commit,
                    "--source-sha256",
                    self.source_sha256,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            sentinel.unlink(missing_ok=True)
        with zipfile.ZipFile(output) as archive:
            self.assertNotIn(
                "payload/bin/untracked-payload-sentinel.txt",
                archive.namelist(),
            )
        record = json.loads(
            subprocess.run(
                [str(output), "provenance", "--json"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        self.assertIs(record["source_tree_dirty"], True)

    def test_unbound_non_git_build_is_explicit_and_non_releaseable(self) -> None:
        context = self.temp_root / "docker-context"
        for name in (
            "oncotracer_cli",
            "bin",
            "examples",
            "params",
            "environments",
            "provenance",
        ):
            shutil.copytree(ROOT / name, context / name)
        unbound = self.temp_root / "oncotracer-unbound"
        subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--root",
                str(context),
                "--output",
                str(unbound),
                "--allow-unbound-development",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [str(unbound), "provenance", "--json"],
            cwd=self.temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        record = json.loads(result.stdout)
        self.assertIsNone(record["source_commit"])
        self.assertIsNone(record["source_sha256"])
        self.assertIsNone(record["source_tree_dirty"])
        self.assertEqual(record["source_metadata_origin"], "unbound-development")
        with mock.patch(
            "oncotracer_cli.provenance.get_provenance", return_value=record
        ):
            with self.assertRaises(provenance.ProvenanceError):
                provenance.release_provenance()
        with self.assertRaises(SystemExit):
            builder.resolve_source_metadata(context, None, None)
        with self.assertRaises(SystemExit):
            builder.resolve_source_metadata(
                context,
                self.commit,
                None,
                allow_unbound_development=True,
            )

    def test_historical_hashes_are_stable_and_separate(self) -> None:
        record = provenance.historical_source_provenance()
        sources = record["historical_sources"]
        self.assertEqual(
            sources["native_source_input_archive"]["sha256"],
            HISTORICAL_NATIVE_SOURCE_SHA256,
        )
        self.assertEqual(
            sources["classifier_overlay"]["sha256"],
            CLASSIFIER_OVERLAY_SHA256,
        )
        self.assertNotEqual(HISTORICAL_NATIVE_SOURCE_SHA256, self.source_sha256)

    def test_cli_provenance_json_parser_and_output(self) -> None:
        expected = {
            "schema": "oncotracer-provenance-v1",
            "source_commit": self.commit,
            "source_sha256": self.source_sha256,
        }
        output = io.StringIO()
        with mock.patch("oncotracer_cli.cli.get_provenance", return_value=expected):
            with contextlib.redirect_stdout(output):
                returncode = cli.main(["provenance", "--json"])
        self.assertEqual(returncode, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
