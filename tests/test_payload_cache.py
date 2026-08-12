#!/usr/bin/env python3
"""Concurrency, integrity, and storage-safety tests for zipapp payload caches."""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from oncotracer_cli import runtime
from oncotracer_cli.runtime import OncoTracerError

ROOT = Path(__file__).resolve().parents[1]


def _write_regular(
    bundle: zipfile.ZipFile,
    name: str,
    content: bytes,
    mode: int = 0o644,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    bundle.writestr(info, content)


def _write_directory(bundle: zipfile.ZipFile, name: str, mode: int = 0o755) -> None:
    info = zipfile.ZipInfo(name if name.endswith("/") else f"{name}/")
    info.create_system = 3
    info.external_attr = (stat.S_IFDIR | mode) << 16
    bundle.writestr(info, b"")


def _write_archive(path: Path, label: str) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _write_regular(
            bundle,
            "payload/bin/scripts/probe.py",
            f"print({label!r})\n".encode(),
            0o777,
        )
        _write_regular(
            bundle,
            "payload/environments/native-core.yml",
            f"name: {label}\n".encode(),
        )
        _write_directory(bundle, "payload/empty", 0o777)
    return path


def _expected_marker(archive: Path) -> dict[str, object]:
    manifest = runtime._archive_payload_manifest(archive)
    return {
        "schema": "oncotracer-payload-cache-v2",
        "version": runtime.__version__,
        "archive_sha256": runtime.sha256_file(archive),
        "payload_entries": len(manifest),
        "payload_manifest_sha256": runtime._manifest_sha256(manifest),
    }


def _snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str | None]]:
    snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            snapshot[relative] = ("symlink", mode, os.readlink(path))
        elif stat.S_ISDIR(metadata.st_mode):
            snapshot[relative] = ("directory", mode, None)
        elif stat.S_ISREG(metadata.st_mode):
            snapshot[relative] = ("file", mode, path.read_bytes())
        else:
            snapshot[relative] = ("special", mode, None)
    return snapshot


class PayloadCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache_home = self.base / "cache-home"

    @contextlib.contextmanager
    def default_cache(self):
        environment = {
            "HOME": str(self.base / "home"),
            "XDG_CACHE_HOME": str(self.cache_home),
            "ONCOTRACER_PAYLOAD_CACHE": "",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            yield

    def test_distinct_same_version_archives_are_content_addressed_concurrently(
        self,
    ) -> None:
        first = _write_archive(self.base / "first.zip", "first")
        second = _write_archive(self.base / "second.zip", "second")
        self.cache_home.mkdir()
        (self.cache_home / "preserve.txt").write_text("keep\n", encoding="utf-8")
        with self.default_cache():
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                roots = list(
                    executor.map(runtime._extract_zipapp_payload, (first, second))
                )

        self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(
            roots[0].parent.name, hashlib.sha256(first.read_bytes()).hexdigest()
        )
        self.assertEqual(
            roots[1].parent.name, hashlib.sha256(second.read_bytes()).hexdigest()
        )
        self.assertIn("first", (roots[0] / "bin" / "scripts" / "probe.py").read_text())
        self.assertIn("second", (roots[1] / "bin" / "scripts" / "probe.py").read_text())
        self.assertEqual(
            (self.cache_home / "preserve.txt").read_text(encoding="utf-8"),
            "keep\n",
        )

    def test_same_archive_concurrency_publishes_one_complete_root(self) -> None:
        archive = _write_archive(self.base / "same.zip", "same")
        with self.default_cache():
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                roots = list(
                    executor.map(runtime._extract_zipapp_payload, (archive,) * 8)
                )
            manifest = runtime._archive_payload_manifest(archive)
            marker = _expected_marker(archive)

        self.assertEqual(len(set(roots)), 1)
        self.assertTrue(runtime._complete_payload(roots[0], marker, manifest))
        self.assertEqual(list(roots[0].parent.glob(".payload.tmp.*")), [])

    def test_archive_sha_is_computed_once_and_valid_cache_is_not_republished(
        self,
    ) -> None:
        archive = _write_archive(self.base / "stable.zip", "stable")
        real_sha256 = runtime.sha256_file
        with self.default_cache():
            with mock.patch.object(runtime, "sha256_file", wraps=real_sha256) as digest:
                root = runtime._extract_zipapp_payload(archive)
            self.assertEqual(digest.call_count, 1)
            inode = root.stat().st_ino
            marker = (root / ".complete.json").read_bytes()
            with mock.patch.object(
                runtime,
                "_extract_payload_to_staging",
                side_effect=AssertionError("valid cache was unexpectedly rebuilt"),
            ):
                reused = runtime._extract_zipapp_payload(archive)

        self.assertEqual(reused, root)
        self.assertEqual(reused.stat().st_ino, inode)
        self.assertEqual((reused / ".complete.json").read_bytes(), marker)

    def test_interrupted_staging_leaves_no_destination_and_retry_succeeds(self) -> None:
        for number, error in enumerate((OSError("injected"), KeyboardInterrupt())):
            with self.subTest(error=type(error).__name__):
                archive = _write_archive(
                    self.base / f"failure-{number}.zip", f"failure-{number}"
                )
                digest = runtime.sha256_file(archive)
                with self.default_cache():
                    destination = runtime._payload_cache(digest)
                    with mock.patch.object(
                        runtime.shutil, "copyfileobj", side_effect=error
                    ):
                        with self.assertRaises(type(error)):
                            runtime._extract_zipapp_payload(archive)
                    self.assertFalse(destination.exists())
                    self.assertEqual(
                        list(destination.parent.glob(".payload.tmp.*")), []
                    )
                    recovered = runtime._extract_zipapp_payload(archive)
                self.assertTrue((recovered / ".complete.json").is_file())

    def test_interrupted_atomic_publish_cleans_staging_and_retry_succeeds(self) -> None:
        for number, error in enumerate((OSError("publish"), KeyboardInterrupt())):
            with self.subTest(error=type(error).__name__):
                archive = _write_archive(
                    self.base / f"publish-{number}.zip", f"publish-{number}"
                )
                digest = runtime.sha256_file(archive)
                with self.default_cache():
                    destination = runtime._payload_cache(digest)
                    real_replace = runtime.os.replace

                    def fail_publication(source, target):
                        if Path(target) == destination:
                            raise error
                        return real_replace(source, target)

                    with mock.patch.object(
                        runtime.os, "replace", side_effect=fail_publication
                    ):
                        with self.assertRaises(type(error)):
                            runtime._extract_zipapp_payload(archive)
                    self.assertFalse(destination.exists())
                    self.assertEqual(
                        list(destination.parent.glob(".payload.tmp.*")), []
                    )
                    recovered = runtime._extract_zipapp_payload(archive)
                self.assertTrue((recovered / ".complete.json").is_file())

    def test_cached_file_corruption_and_mode_change_are_rebuilt(self) -> None:
        archive = _write_archive(self.base / "repair.zip", "repair")
        with self.default_cache():
            root = runtime._extract_zipapp_payload(archive)
            probe = root / "bin" / "scripts" / "probe.py"
            expected = probe.read_bytes()
            probe.write_bytes(b"truncated")
            repaired = runtime._extract_zipapp_payload(archive)
            self.assertEqual(
                (repaired / "bin" / "scripts" / "probe.py").read_bytes(), expected
            )
            config = repaired / "environments" / "native-core.yml"
            config.chmod(0o600)
            repaired = runtime._extract_zipapp_payload(archive)

        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(probe.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((repaired / "empty").stat().st_mode), 0o755)

    def test_incomplete_expected_inventory_is_preserved_without_removal(self) -> None:
        archive = _write_archive(self.base / "incomplete.zip", "incomplete")
        destination = self.base / "explicit-incomplete"
        with mock.patch.dict(
            os.environ,
            {"ONCOTRACER_PAYLOAD_CACHE": str(destination)},
            clear=False,
        ):
            runtime._extract_zipapp_payload(archive)
            (destination / "environments" / "native-core.yml").unlink()
            before = _snapshot(destination)
            with mock.patch.object(
                runtime.shutil,
                "rmtree",
                side_effect=AssertionError("incomplete cache reached rmtree"),
            ):
                with self.assertRaisesRegex(OncoTracerError, "not owned"):
                    runtime._extract_zipapp_payload(archive)
        self.assertEqual(_snapshot(destination), before)

    def test_raced_post_publish_replacement_is_preserved(self) -> None:
        archive = _write_archive(self.base / "publish-race.zip", "publish-race")
        digest = runtime.sha256_file(archive)
        with self.default_cache():
            destination = runtime._payload_cache(digest)
            displaced = destination.parent / "published-before-race"
            real_replace = runtime.os.replace

            def replace_then_race(source, target):
                real_replace(source, target)
                if Path(target) == destination:
                    real_replace(target, displaced)
                    destination.mkdir()
                    (destination / "protected-sentinel.txt").write_text(
                        "preserve\n", encoding="utf-8"
                    )

            with mock.patch.object(
                runtime.os, "replace", side_effect=replace_then_race
            ):
                with mock.patch.object(
                    runtime.shutil,
                    "rmtree",
                    side_effect=AssertionError("raced destination reached rmtree"),
                ):
                    with self.assertRaisesRegex(
                        OncoTracerError, "preserving the cache"
                    ):
                        runtime._extract_zipapp_payload(archive)

        self.assertEqual(
            (destination / "protected-sentinel.txt").read_text(encoding="utf-8"),
            "preserve\n",
        )
        self.assertTrue((displaced / ".complete.json").is_file())

    def test_extra_directory_and_symlink_corruption_fail_without_removal(self) -> None:
        archive = _write_archive(self.base / "unsafe-cache.zip", "unsafe-cache")
        with self.default_cache():
            root = runtime._extract_zipapp_payload(archive)
            extra = root / "unexpected-empty-directory"
            extra.mkdir()
            with self.assertRaisesRegex(OncoTracerError, "refusing to replace"):
                runtime._extract_zipapp_payload(archive)
            self.assertTrue(extra.is_dir())
            extra.rmdir()

            protected = self.base / "protected.txt"
            protected.write_text("preserve\n", encoding="utf-8")
            probe = root / "bin" / "scripts" / "probe.py"
            probe.unlink()
            probe.symlink_to(protected)
            before = _snapshot(self.base)
            with self.assertRaisesRegex(OncoTracerError, "unsafe"):
                runtime._extract_zipapp_payload(archive)
            after = _snapshot(self.base)

        self.assertEqual(after, before)
        self.assertEqual(protected.read_text(encoding="utf-8"), "preserve\n")

    def test_explicit_absent_and_empty_dedicated_paths_are_allowed(self) -> None:
        archive = _write_archive(self.base / "explicit.zip", "explicit")
        for existing in (False, True):
            with self.subTest(existing=existing):
                destination = self.base / f"explicit-{existing}"
                if existing:
                    destination.mkdir()
                with mock.patch.dict(
                    os.environ,
                    {"ONCOTRACER_PAYLOAD_CACHE": str(destination)},
                    clear=False,
                ):
                    root = runtime._extract_zipapp_payload(archive)
                self.assertEqual(root, destination)
                self.assertTrue((root / ".complete.json").is_file())

    def test_explicit_unowned_broad_and_symlink_paths_are_preserved(self) -> None:
        archive = _write_archive(self.base / "preserve.zip", "preserve")

        populated = self.base / "populated"
        populated.mkdir()
        (populated / "sentinel.txt").write_text("keep\n", encoding="utf-8")
        before = _snapshot(populated)
        with mock.patch.dict(
            os.environ, {"ONCOTRACER_PAYLOAD_CACHE": str(populated)}, clear=False
        ):
            with mock.patch.object(
                runtime.shutil,
                "rmtree",
                side_effect=AssertionError("unowned explicit cache reached rmtree"),
            ):
                with self.assertRaisesRegex(OncoTracerError, "not owned"):
                    runtime._extract_zipapp_payload(archive)
        self.assertEqual(_snapshot(populated), before)

        broad = self.base / "broad-fixture"
        broad.mkdir()
        (broad / "protected.dat").write_bytes(b"patient-like fixture")
        before = _snapshot(broad)
        with mock.patch.dict(
            os.environ,
            {
                "XDG_CACHE_HOME": str(broad),
                "ONCOTRACER_PAYLOAD_CACHE": str(broad),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(OncoTracerError, "dedicated child path"):
                runtime._extract_zipapp_payload(archive)
        self.assertEqual(_snapshot(broad), before)

        target = self.base / "symlink-target"
        target.mkdir()
        (target / "protected.dat").write_bytes(b"preserve")
        link = self.base / "cache-link"
        link.symlink_to(target, target_is_directory=True)
        before = _snapshot(target)
        with mock.patch.dict(
            os.environ,
            {"ONCOTRACER_PAYLOAD_CACHE": str(link / "payload")},
            clear=False,
        ):
            with self.assertRaisesRegex(OncoTracerError, "must not contain symlinks"):
                runtime._extract_zipapp_payload(archive)
        self.assertTrue(link.is_symlink())
        self.assertEqual(_snapshot(target), before)

    def test_explicit_mismatched_and_forged_markers_are_preserved_byte_for_byte(
        self,
    ) -> None:
        first = _write_archive(self.base / "marker-first.zip", "marker-first")
        second = _write_archive(self.base / "marker-second.zip", "marker-second")
        destination = self.base / "explicit-mismatch"
        environment = {"ONCOTRACER_PAYLOAD_CACHE": str(destination)}
        with mock.patch.dict(os.environ, environment, clear=False):
            runtime._extract_zipapp_payload(first)
            before = _snapshot(destination)
            with mock.patch.object(
                runtime.shutil,
                "rmtree",
                side_effect=AssertionError("mismatched explicit cache reached rmtree"),
            ):
                with self.assertRaisesRegex(OncoTracerError, "not owned"):
                    runtime._extract_zipapp_payload(second)
        self.assertEqual(_snapshot(destination), before)

        markers = {
            "forged": _expected_marker(first),
            "legacy": {
                "version": runtime.__version__,
                "archive_sha256": runtime.sha256_file(first),
            },
        }
        for label, marker in markers.items():
            with self.subTest(marker=label):
                forged = self.base / label
                forged.mkdir()
                (forged / ".complete.json").write_text(
                    json.dumps(marker), encoding="utf-8"
                )
                (forged / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
                before = _snapshot(forged)
                with mock.patch.dict(
                    os.environ,
                    {"ONCOTRACER_PAYLOAD_CACHE": str(forged)},
                    clear=False,
                ):
                    with mock.patch.object(
                        runtime.shutil,
                        "rmtree",
                        side_effect=AssertionError(
                            "forged explicit cache reached rmtree"
                        ),
                    ):
                        with self.assertRaisesRegex(OncoTracerError, "not owned"):
                            runtime._extract_zipapp_payload(first)
                self.assertEqual(_snapshot(forged), before)

    def test_malicious_archive_paths_duplicates_and_special_files_are_rejected(
        self,
    ) -> None:
        malicious = (
            ("traversal", "payload/bin/scripts/../../../escape", stat.S_IFREG),
            ("absolute", "payload//absolute", stat.S_IFREG),
            ("backslash", "payload/bin\\scripts\\escape", stat.S_IFREG),
            ("control", "payload/bin/scripts/bad\nname", stat.S_IFREG),
            ("symlink", "payload/bin/scripts/link", stat.S_IFLNK),
            ("fifo", "payload/bin/scripts/fifo", stat.S_IFIFO),
        )
        for label, name, file_type in malicious:
            with self.subTest(label=label):
                archive = self.base / f"malicious-{label}.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    _write_regular(
                        bundle,
                        "payload/bin/scripts/probe.py",
                        b"print('safe')\n",
                        0o755,
                    )
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (file_type | 0o644) << 16
                    bundle.writestr(info, b"malicious")
                with self.default_cache():
                    with self.assertRaises(OncoTracerError):
                        runtime._extract_zipapp_payload(archive)

        duplicate = self.base / "malicious-duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as bundle:
                _write_regular(
                    bundle, "payload/bin/scripts/probe.py", b"first\n", 0o755
                )
                _write_regular(
                    bundle, "payload/bin/scripts/probe.py", b"second\n", 0o755
                )
        with self.default_cache():
            with self.assertRaisesRegex(OncoTracerError, "duplicate payload path"):
                runtime._extract_zipapp_payload(duplicate)
        self.assertFalse((self.base / "escape").exists())

    def test_validator_uses_digest_cache_without_touching_legacy_override(self) -> None:
        driver = (ROOT / "scripts" / "validate_v2_release.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("export ONCOTRACER_PAYLOAD_CACHE=", driver)
        self.assertIn('export XDG_CACHE_HOME="$VALIDATION_ROOT/cache"', driver)

    def test_child_commands_disable_python_bytecode_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "0"}, clear=False):
            environment = runtime._command_environment(None)
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")


if __name__ == "__main__":
    unittest.main()
