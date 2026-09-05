from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import install_safety as safety
from oncotracer_cli.cli import main
from oncotracer_cli.runtime import OncoTracerError, sha256_file
from oncotracer_cli.uninstall import uninstall_target

SOURCE = {
    "oncotracer_version": "2.0.0",
    "source_commit": "a" * 40,
    "source_sha256": "b" * 64,
}


class UninstallTests(unittest.TestCase):
    def fixture(self, root):
        base = root / "tools"
        base.mkdir()
        install_id = "c" * 32
        (base / safety.BASE_MARKER).write_text(
            json.dumps(safety._base_marker(base, install_id, SOURCE))
        )
        for name in ("core", "classifier"):
            child = base / name
            child.mkdir()
            (child / "installed-file").write_text("synthetic tool")
            digest = safety._write_child_inventory(child)
            (child / safety.ENV_MARKER).write_text(
                json.dumps(
                    safety._environment_marker(
                        child, install_id, name, "d" * 64, SOURCE, digest
                    )
                )
            )
        return base

    def test_preview_never_creates_locks_or_removes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result = uninstall_target(base, "conda", dry_run=True, purge=True)
            self.assertTrue(result["dry_run"])
            self.assertEqual(
                before, sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            )

    def test_uninstall_keeps_recoverable_tools_and_unrelated_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            unrelated = base / "my-project"
            unrelated.mkdir()
            (unrelated / "reads.fastq").write_text("user reads")
            with patch.object(safety, "_active_processes", return_value=[]):
                result = uninstall_target(base, "conda", dry_run=False)
            self.assertTrue(result["recoverable"])
            self.assertFalse((base / "core").exists())
            recovery = Path(result["recovery_directory"])
            self.assertTrue((recovery / "core/installed-file").is_file())
            self.assertTrue((recovery / "uninstall.json").is_file())
            self.assertEqual((unrelated / "reads.fastq").read_text(), "user reads")

    def test_purge_only_removes_owned_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            keep = root / "results.tsv"
            keep.write_text("user result")
            with patch.object(safety, "_active_processes", return_value=[]):
                result = uninstall_target(base, "conda", dry_run=False, purge=True)
            self.assertFalse(result["recoverable"])
            self.assertFalse(base.exists())
            self.assertFalse(list(root.glob("*.oncotracer-uninstalled-*")))
            self.assertEqual(keep.read_text(), "user result")

    def test_changed_inventory_active_process_and_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            with patch.object(safety, "_active_processes", return_value=[123]):
                with self.assertRaisesRegex(OncoTracerError, "active process"):
                    uninstall_target(base, "conda", dry_run=False)
            linked = root / "linked"
            linked.symlink_to(base, target_is_directory=True)
            with self.assertRaises(OncoTracerError):
                uninstall_target(linked, "conda", dry_run=True)
            (base / "core/foreign.txt").write_text("keep")
            with self.assertRaisesRegex(OncoTracerError, "foreign entries"):
                uninstall_target(base, "conda", dry_run=False, purge=True)
            self.assertTrue((base / "core/foreign.txt").is_file())

    def test_unowned_and_broad_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "user-env"
            base.mkdir()
            (base / "data").write_text("keep")
            for target in (base, Path("/tmp"), Path.home()):
                with self.subTest(target=target), self.assertRaises(OncoTracerError):
                    uninstall_target(target, "conda", dry_run=True)

    def test_partial_move_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            rename = safety._rename_noreplace
            failed = False

            def fail_once(source, destination, label):
                nonlocal failed
                if source.name == "classifier" and not failed:
                    failed = True
                    raise OncoTracerError("synthetic move failure")
                return rename(source, destination, label)

            with (
                patch.object(safety, "_active_processes", return_value=[]),
                patch.object(safety, "_rename_noreplace", side_effect=fail_once),
            ):
                with self.assertRaisesRegex(OncoTracerError, "synthetic"):
                    uninstall_target(base, "conda", dry_run=False)
            self.assertEqual(safety._classify_base(base)[0], "owned")

    def test_owned_sif_pair_can_be_removed_without_project_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "oncotracer.sif"
            image.write_bytes(b"synthetic image")
            sidecar = safety._sif_sidecar(image)
            sidecar.write_text(
                json.dumps(
                    safety._sif_marker(
                        image, "a" * 32, "image@sha256:test", sha256_file(image), SOURCE
                    )
                )
            )
            with patch.object(safety, "_active_processes", return_value=[]):
                result = uninstall_target(image, "sif", dry_run=False)
            self.assertFalse(image.exists())
            self.assertFalse(sidecar.exists())
            self.assertTrue((Path(result["recovery_directory"]) / image.name).exists())

    def test_public_command_defaults_to_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            output = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "settings")}),
            ):
                code = main(["uninstall", "--conda", "--prefix", str(base)])
            self.assertEqual(code, 0)
            self.assertIn("Preview only", output.getvalue())
            self.assertTrue((base / "core").exists())


if __name__ == "__main__":
    unittest.main()
