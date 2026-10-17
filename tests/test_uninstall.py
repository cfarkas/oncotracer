from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import __version__, install_safety as safety
from oncotracer_cli.cli import main
from oncotracer_cli.runtime import OncoTracerError, sha256_file
from oncotracer_cli.uninstall import uninstall_target

SOURCE = {
    "oncotracer_version": __version__,
    "source_commit": "a" * 40,
    "source_sha256": "b" * 64,
}


class UninstallTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix="oncotracer-uninstall-home-")
        self.addCleanup(home.cleanup)
        environment = patch.dict(os.environ, {"HOME": home.name})
        environment.start()
        self.addCleanup(environment.stop)

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

    def sibling_fixture(self, base, family="variants", *, owned=True):
        suffix = "poetry" if family == "poetry-runtime" else "variants"
        domain = base.with_name(f"{base.name}-{suffix}")
        domain.mkdir()
        install_id = "e" * 32
        source = {**SOURCE, "source_commit": "f" * 40}
        marker = safety._base_marker(domain, install_id, source)
        marker["managed_children"] = [family]
        if owned:
            (domain / safety.BASE_MARKER).write_text(json.dumps(marker))
        child = domain / family
        child.mkdir()
        if family == "poetry-runtime":
            (child / "bin").mkdir()
            for name in ("python", "oncotracer"):
                executable = child / "bin" / name
                executable.write_text("#!/bin/sh\nexit 0\n")
                executable.chmod(0o700)
            digest = safety._write_child_inventory(child)
            child_marker = safety._poetry_marker(
                child, install_id, "d" * 64, "e" * 64,
                sha256_file(child / "bin/oncotracer"), source, digest,
            )
            marker_name = safety.POETRY_MARKER
        else:
            (child / "installed-file").write_text("synthetic variants")
            digest = safety._write_child_inventory(child)
            child_marker = safety._environment_marker(
                child, install_id, family, "d" * 64, source, digest,
            )
            marker_name = safety.ENV_MARKER
        (child / marker_name).write_text(json.dumps(child_marker))
        return domain

    def test_preview_includes_owned_siblings_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            variants = self.sibling_fixture(base)
            poetry = self.sibling_fixture(base, "poetry-runtime")
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result = uninstall_target(base, "conda", dry_run=True)
            self.assertEqual(set(result["managed_roots"]), {str(base), str(variants), str(poetry)})
            self.assertIn(str(variants / "variants"), result["paths"])
            self.assertIn(str(poetry / "poetry-runtime"), result["paths"])
            self.assertEqual(before, sorted(str(p.relative_to(root)) for p in root.rglob("*")))

    def test_all_owned_domains_share_recovery_and_preserve_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            variants = self.sibling_fixture(base)
            poetry = self.sibling_fixture(base, "poetry-runtime")
            keep = variants / "reference.fa"
            keep.write_text("user reference")
            with patch.object(safety, "_active_processes", return_value=[]):
                result = uninstall_target(base, "conda", dry_run=False)
            recovery = Path(result["recovery_directory"])
            record = json.loads((recovery / "uninstall.json").read_text())
            for original in (base / "core", variants / "variants", poetry / "poetry-runtime"):
                self.assertFalse(original.exists())
                self.assertTrue((recovery / record["recovery_entries"][str(original)]).is_dir())
            self.assertEqual((recovery / "core/installed-file").read_text(), "synthetic tool")
            self.assertEqual(keep.read_text(), "user reference")
            self.assertFalse((variants / safety.BASE_MARKER).exists())
            self.assertFalse(poetry.exists())

    def test_untrusted_sibling_refuses_entire_removal(self):
        for defect in ("unowned", "wrong-family", "tampered", "symlink", "pending"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = self.fixture(root)
                sibling = self.sibling_fixture(base, owned=defect != "unowned")
                if defect == "wrong-family":
                    marker_path = sibling / safety.BASE_MARKER
                    marker = json.loads(marker_path.read_text())
                    marker["managed_children"] = list(safety.MANAGED_CHILDREN)
                    marker_path.write_text(json.dumps(marker))
                elif defect == "tampered":
                    (sibling / "variants/foreign.txt").write_text("keep")
                elif defect == "symlink":
                    real = sibling.with_name("unrelated-environment")
                    sibling.rename(real)
                    sibling.symlink_to(real, target_is_directory=True)
                elif defect == "pending":
                    safety._journal_path(sibling, "conda").write_text("{}")
                with patch.object(safety, "_active_processes", return_value=[]):
                    with self.assertRaises(OncoTracerError):
                        uninstall_target(base, "conda", dry_run=False, purge=True)
                self.assertTrue((base / "core/installed-file").is_file())
                self.assertTrue((base / safety.BASE_MARKER).is_file())
                self.assertFalse(list(root.glob("*.oncotracer-uninstalled-*")))

    def test_sibling_move_or_verification_failure_rolls_back_all_domains(self):
        for failure in ("move", "verification"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = self.fixture(root)
                variants = self.sibling_fixture(base)
                poetry = self.sibling_fixture(base, "poetry-runtime")
                rename = safety._rename_noreplace
                verify = safety._child_marker_at
                failed = False

                def move(source, destination, label):
                    nonlocal failed
                    if failure == "move" and source == poetry / "poetry-runtime" and not failed:
                        failed = True
                        raise OncoTracerError("synthetic sibling failure")
                    return rename(source, destination, label)

                def check(storage, destination, marker, name):
                    nonlocal failed
                    if failure == "verification" and name == "variants" and storage != destination and not failed:
                        failed = True
                        raise OncoTracerError("synthetic sibling failure")
                    return verify(storage, destination, marker, name)

                with (
                    patch.object(safety, "_active_processes", return_value=[]),
                    patch.object(safety, "_rename_noreplace", side_effect=move),
                    patch.object(safety, "_child_marker_at", side_effect=check),
                ):
                    with self.assertRaisesRegex(OncoTracerError, "synthetic sibling"):
                        uninstall_target(base, "conda", dry_run=False)
                for domain in (base, variants, poetry):
                    self.assertEqual(safety._classify_base(domain)[0], "owned")

    def test_active_sibling_blocks_removal_before_any_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            variants = self.sibling_fixture(base)
            with patch.object(safety, "_active_processes", side_effect=lambda path, **kwargs: [123] if path == variants else []):
                with self.assertRaisesRegex(OncoTracerError, "active process"):
                    uninstall_target(base, "conda", dry_run=False)
            self.assertTrue((base / "core").is_dir())
            self.assertTrue((variants / "variants").is_dir())
            self.assertFalse(list(root.glob("*.oncotracer-uninstalled-*")))

    def test_direct_singleton_removal_does_not_infer_further_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            variants = self.sibling_fixture(base)
            unrelated = variants.with_name(variants.name + "-variants")
            unrelated.mkdir()
            (unrelated / "data").write_text("keep")
            with patch.object(safety, "_active_processes", return_value=[]):
                result = uninstall_target(variants, "conda", dry_run=False, purge=True)
            self.assertFalse(result["recoverable"])
            self.assertFalse(variants.exists())
            self.assertTrue((base / "core").is_dir())
            self.assertEqual((unrelated / "data").read_text(), "keep")

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

    def launcher_fixture(self, root):
        launcher = root / "oncotracer"
        source = Path(__file__).resolve().parents[1]
        with zipfile.ZipFile(launcher, "w") as archive:
            archive.writestr(
                "__main__.py",
                "from oncotracer_cli.cli import main\nraise SystemExit(main())\n",
            )
            for path in (source / "oncotracer_cli").rglob("*.py"):
                archive.write(path, str(path.relative_to(source)))
            archive.writestr("payload/provenance/native-v2-sources.json", "{}")
        return launcher

    def test_flag_alias_preserves_backend_and_preview_confirmation(self):
        for arguments in (
            ["--uninstall", "--conda"],
            ["--conda", "--uninstall"],
        ):
            with (
                self.subTest(arguments=arguments),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                base = self.fixture(root)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(
                        [*arguments, "--prefix", str(base), "--purge", "--json"]
                    )
                self.assertEqual(code, 0)
                self.assertTrue(json.loads(output.getvalue())["dry_run"])
                self.assertTrue((base / "core").is_dir())

    def test_explicit_targets_work_with_corrupted_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            launcher = self.launcher_fixture(root)
            image = root / "oncotracer.sif"
            image.write_bytes(b"synthetic image")
            safety._sif_sidecar(image).write_text(
                json.dumps(safety._sif_marker(
                    image, "a" * 32, "image@sha256:test", sha256_file(image), SOURCE
                ))
            )
            settings = root / "settings/oncotracer/config.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("{damaged settings")
            for target in (
                ["--conda", "--prefix", str(base)],
                ["--singularity", "--sif", str(image)],
                ["--launcher", str(launcher)],
            ):
                output = io.StringIO()
                with (
                    self.subTest(target=target),
                    contextlib.redirect_stdout(output),
                    patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "settings")}),
                ):
                    code = main(["uninstall", *target, "--dry-run", "--json"])
                    self.assertEqual(code, 0)
                    self.assertTrue(json.loads(output.getvalue())["dry_run"])
            self.assertEqual(settings.read_text(), "{damaged settings")

    def test_saved_target_still_reports_corrupted_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "oncotracer/config.json"
            settings.parent.mkdir()
            settings.write_text("{damaged settings")
            errors = io.StringIO()
            with (
                contextlib.redirect_stderr(errors),
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}),
            ):
                self.assertEqual(main(["uninstall", "--conda"]), 2)
            self.assertIn("invalid installation config", errors.getvalue())

    @unittest.skipUnless(Path("/proc").is_dir(), "requires Linux process-use checks")
    def test_subprocess_removal_keeps_process_checks_and_confirmation(self):
        source = Path(__file__).resolve().parents[1] / "oncotracer"
        for purge in (False, True):
            with self.subTest(purge=purge), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = self.fixture(root)
                variants = self.sibling_fixture(base)
                poetry = self.sibling_fixture(base, "poetry-runtime")
                keep = base / "results.tsv"
                keep.write_text("user result")
                settings = root / "settings/oncotracer/config.json"
                settings.parent.mkdir(parents=True)
                saved = json.dumps(
                    {"backend": "conda", "core_prefix": str(base / "core")}
                )
                settings.write_text(saved)
                arguments = [
                    sys.executable, str(source), "--uninstall", "--yes", "--json",
                    *(["--purge"] if purge else []),
                ]
                environment = {**os.environ, "XDG_CONFIG_HOME": str(root / "settings")}
                preview = subprocess.run(
                    [*arguments, "--dry-run"], cwd=root, env=environment,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertTrue(json.loads(preview.stdout)["dry_run"])
                self.assertTrue((base / "core").is_dir())
                removed = subprocess.run(
                    arguments, cwd=root, env=environment,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(removed.returncode, 0, removed.stderr)
                result = json.loads(removed.stdout)
                self.assertFalse(result["dry_run"])
                self.assertFalse((base / "core").exists())
                self.assertFalse(variants.exists())
                self.assertFalse(poetry.exists())
                self.assertEqual(keep.read_text(), "user result")
                self.assertEqual(settings.read_text(), saved)
                if purge:
                    self.assertIsNone(result["recovery_directory"])
                    self.assertFalse(list(root.glob("*.oncotracer-uninstalled-*")))
                else:
                    recovery = Path(result["recovery_directory"])
                    self.assertEqual(
                        (recovery / "core/installed-file").read_text(), "synthetic tool"
                    )

    @unittest.skipUnless(Path("/proc").is_dir(), "requires Linux process-use checks")
    def test_standalone_launcher_can_remove_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = self.launcher_fixture(root)
            keep = root / "run.yml"
            keep.write_text("project configuration")
            result = subprocess.run(
                [
                    sys.executable, str(launcher), "--uninstall", "--launcher",
                    str(launcher), "--yes", "--purge", "--json",
                ],
                cwd=root, env={**os.environ, "XDG_CONFIG_HOME": str(root / "settings")},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["recoverable"])
            self.assertFalse(launcher.exists())
            self.assertEqual(keep.read_text(), "project configuration")


if __name__ == "__main__":
    unittest.main()
