#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oncotracer_cli import engine
from oncotracer_cli.engine import (
    Toolchain,
    _validated_bwa_reader,
    prepare_ichor_assets,
    prepare_reference,
)
from oncotracer_cli.runtime import OncoTracerError, StageLedger


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FixtureRunner:
    dry_run = False

    def __init__(self) -> None:
        self.stages: list[str] = []

    def run(self, stage, command, *, stdout=None, **_kwargs):
        self.stages.append(stage)
        arguments = [str(value) for value in command]
        if stage == "reference-bwa-build":
            prefix = Path(arguments[arguments.index("-p") + 1])
            Path(f"{prefix}.amb").write_text("4 1 0\n", encoding="utf-8")
            Path(f"{prefix}.ann").write_text(
                "4 1 11\n0 chr1\n0 4 0\n", encoding="utf-8"
            )
            Path(f"{prefix}.bwt").write_bytes(b"bwt")
            Path(f"{prefix}.pac").write_bytes(b"p0")
            Path(f"{prefix}.sa").write_bytes(b"sa")
        elif stage == "reference-minimap2-build":
            destination = Path(arguments[arguments.index("-d") + 1])
            destination.write_bytes(b"mmi")
        elif stage.startswith("reference-minimap2-validate"):
            if stdout is not None:
                stdout.write(b"@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:4\n")
                stdout.flush()
        return SimpleNamespace(returncode=0)


class ReferenceStorageSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.fasta = b">chr1\nACGT\n"
        self.fai = b"chr1\t4\t6\t4\t5\n"
        self.sequence_dict = b"@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:4\n"
        self.hg38 = {
            "genome.fa": digest(self.fasta),
            "genome.fa.fai": digest(self.fai),
            "genome.dict": digest(self.sequence_dict),
        }
        self.core = self.root / "core"
        for name in ("bwa", "minimap2"):
            executable = self.core / "bin" / name
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        self.toolchain = Toolchain(core_prefix=self.core)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fake_download(self, url: str, destination: Path, **_kwargs):
        payloads = {
            "genome.fa": self.fasta,
            "genome.fa.fai": self.fai,
            "genome.dict": self.sequence_dict,
        }
        destination.write_bytes(payloads[url.rsplit("/", 1)[-1]])
        return destination

    @staticmethod
    def snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
        result: dict[str, tuple[str, int, int]] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            observed = path.lstat()
            if path.is_symlink():
                result[relative] = (
                    f"link:{os.readlink(path)}",
                    observed.st_mode,
                    observed.st_mtime_ns,
                )
            elif path.is_file():
                result[relative] = (
                    digest(path.read_bytes()),
                    observed.st_mode,
                    observed.st_mtime_ns,
                )
            else:
                result[relative] = ("directory", observed.st_mode, observed.st_mtime_ns)
        return result

    def create_owned_reference(self, *, bwa: bool = True, minimap2: bool = True):
        runner = FixtureRunner()
        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download", side_effect=self.fake_download),
        ):
            result = prepare_reference(
                self.project,
                runner,  # type: ignore[arg-type]
                StageLedger(self.root / "first-state.json"),
                self.toolchain,
                need_bwa=bwa,
                need_minimap2=minimap2,
                threads=1,
            )
        return result, runner

    def test_external_missing_reference_fails_without_any_mutation(self) -> None:
        reference = self.project / "references" / "samurai_hg38"
        reference.mkdir(parents=True)
        sentinel = reference / "PATIENT_SENTINEL"
        sentinel.write_bytes(b"must-survive")
        before = self.snapshot(reference)
        runner = FixtureRunner()
        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download") as download,
        ):
            with self.assertRaisesRegex(OncoTracerError, "required external hg38"):
                prepare_reference(
                    self.project,
                    runner,  # type: ignore[arg-type]
                    StageLedger(self.root / "state.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=False,
                    threads=1,
                )
        download.assert_not_called()
        self.assertEqual(self.snapshot(reference), before)
        self.assertEqual(runner.stages, [])

    def test_owned_cache_is_content_addressed_and_reused_by_fresh_ledger(self) -> None:
        result, first = self.create_owned_reference()
        reference = Path(str(result["reference_root"]))
        self.assertTrue(result["reference_owned"])
        self.assertIn(
            self.project / ".oncotracer" / "reference-cache", reference.parents
        )
        self.assertFalse((self.project / "references").exists())
        self.assertEqual(first.stages.count("reference-bwa-build"), 1)
        self.assertEqual(first.stages.count("reference-minimap2-build"), 1)
        before = self.snapshot(reference)

        second = FixtureRunner()
        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download") as download,
        ):
            observed = prepare_reference(
                self.project,
                second,  # type: ignore[arg-type]
                StageLedger(self.root / "fresh-state.json"),
                self.toolchain,
                need_bwa=True,
                need_minimap2=True,
                threads=1,
            )
        download.assert_not_called()
        self.assertEqual(observed["reference_root"], reference)
        self.assertNotIn("reference-bwa-build", second.stages)
        self.assertNotIn("reference-minimap2-build", second.stages)
        self.assertEqual(self.snapshot(reference), before)

    def test_populated_unowned_cache_root_is_never_adopted(self) -> None:
        cache = self.project / ".oncotracer" / "reference-cache"
        cache.mkdir(parents=True)
        sentinel = cache / "PATIENT_SENTINEL"
        sentinel.write_bytes(b"must-survive")
        before = self.snapshot(cache)
        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download") as download,
        ):
            with self.assertRaisesRegex(OncoTracerError, "nonempty unowned"):
                prepare_reference(
                    self.project,
                    FixtureRunner(),  # type: ignore[arg-type]
                    StageLedger(self.root / "unowned-state.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=False,
                    threads=1,
                )
        download.assert_not_called()
        self.assertEqual(self.snapshot(cache), before)

    def test_mismatched_owned_cache_marker_preserves_every_entry(self) -> None:
        reference, _runner = self.create_owned_reference(bwa=False, minimap2=False)
        cache = Path(str(reference["reference_root"]))
        marker = cache / ".oncotracer-reference-owner.json"
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        marker_data["identity"] = "0" * 64
        marker.write_text(json.dumps(marker_data), encoding="utf-8")
        (cache / "PATIENT_SENTINEL").write_bytes(b"must-survive")
        before = self.snapshot(cache)

        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download") as download,
        ):
            with self.assertRaisesRegex(OncoTracerError, "mismatched ownership marker"):
                prepare_reference(
                    self.project,
                    FixtureRunner(),  # type: ignore[arg-type]
                    StageLedger(self.root / "mismatch-state.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=False,
                    threads=1,
                )
        download.assert_not_called()
        self.assertEqual(self.snapshot(cache), before)

    def test_cache_parent_symlink_is_rejected_without_touching_target(self) -> None:
        protected = self.root / "protected-state"
        protected.mkdir()
        sentinel = protected / "PATIENT_SENTINEL"
        sentinel.write_bytes(b"must-survive")
        (self.project / ".oncotracer").symlink_to(protected, target_is_directory=True)
        before = self.snapshot(protected)
        with (
            patch.object(engine, "HG38_ASSETS", self.hg38),
            patch.object(engine, "download") as download,
        ):
            with self.assertRaisesRegex(OncoTracerError, "physical directory"):
                prepare_reference(
                    self.project,
                    FixtureRunner(),  # type: ignore[arg-type]
                    StageLedger(self.root / "symlink-parent-state.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=False,
                    threads=1,
                )
        download.assert_not_called()
        self.assertEqual(self.snapshot(protected), before)

    def test_external_complete_reference_is_read_only_across_fresh_ledgers(
        self,
    ) -> None:
        owned, _ = self.create_owned_reference()
        owned_root = Path(str(owned["reference_root"]))
        external = self.project / "references" / "samurai_hg38"
        external.parent.mkdir()
        shutil.copytree(owned_root, external)
        before = self.snapshot(external)

        for number in (1, 2):
            runner = FixtureRunner()
            with (
                patch.object(engine, "HG38_ASSETS", self.hg38),
                patch.object(engine, "download") as download,
            ):
                observed = prepare_reference(
                    self.project,
                    runner,  # type: ignore[arg-type]
                    StageLedger(self.root / f"external-state-{number}.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=True,
                    threads=1,
                )
            download.assert_not_called()
            self.assertFalse(observed["reference_owned"])
            self.assertNotIn("reference-bwa-build", runner.stages)
            self.assertNotIn("reference-minimap2-build", runner.stages)
            self.assertEqual(self.snapshot(external), before)

    def test_external_index_symlink_is_rejected_without_touching_target(self) -> None:
        owned, _ = self.create_owned_reference(bwa=True, minimap2=False)
        external = self.project / "references" / "samurai_hg38"
        external.parent.mkdir()
        shutil.copytree(Path(str(owned["reference_root"])), external)
        target = self.root / "protected-patient-file"
        target.write_bytes(b"patient-data")
        amb = external / "bwa" / "genome.amb"
        amb.unlink()
        amb.symlink_to(target)
        before = self.snapshot(external)
        with patch.object(engine, "HG38_ASSETS", self.hg38):
            with self.assertRaisesRegex(OncoTracerError, "refusing to modify"):
                prepare_reference(
                    self.project,
                    FixtureRunner(),  # type: ignore[arg-type]
                    StageLedger(self.root / "symlink-state.json"),
                    self.toolchain,
                    need_bwa=True,
                    need_minimap2=False,
                    threads=1,
                )
        self.assertEqual(target.read_bytes(), b"patient-data")
        self.assertEqual(self.snapshot(external), before)

    def test_reader_rejects_generation_change_before_alignment(self) -> None:
        reference, runner = self.create_owned_reference(bwa=True, minimap2=False)
        manifest = Path(str(reference["bwa_manifest"]))
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["indexes"][".amb"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with patch.object(engine, "HG38_ASSETS", self.hg38):
            with self.assertRaisesRegex(OncoTracerError, "changed or became invalid"):
                with _validated_bwa_reader(reference, runner, self.toolchain):
                    self.fail("invalid reference reached alignment body")

    def test_concurrent_alignment_readers_share_the_reference_lock(self) -> None:
        reference, runner = self.create_owned_reference(bwa=True, minimap2=False)
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def hold_reader() -> None:
            try:
                with _validated_bwa_reader(reference, runner, self.toolchain):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError(
                            "timed out waiting to release first reader"
                        )
            except BaseException as error:  # pragma: no cover - surfaced below
                errors.append(error)

        thread = threading.Thread(target=hold_reader)
        thread.start()
        self.assertTrue(entered.wait(2), "first reference reader did not enter")
        started = time.monotonic()
        with _validated_bwa_reader(reference, runner, self.toolchain):
            self.assertTrue(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1.0)
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_exclusive_reference_writer_waits_for_alignment_reader(self) -> None:
        reference, runner = self.create_owned_reference(bwa=True, minimap2=False)
        entered = threading.Event()
        release = threading.Event()
        writer_acquired = threading.Event()
        errors: list[BaseException] = []

        def hold_reader() -> None:
            try:
                with _validated_bwa_reader(reference, runner, self.toolchain):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("timed out waiting to release reader")
            except BaseException as error:  # pragma: no cover - surfaced below
                errors.append(error)

        def acquire_writer() -> None:
            try:
                with engine._reference_lock(
                    Path(str(reference["bwa_lock"])),
                    exclusive=True,
                    create=False,
                ):
                    writer_acquired.set()
            except BaseException as error:  # pragma: no cover - surfaced below
                errors.append(error)

        reader = threading.Thread(target=hold_reader)
        writer = threading.Thread(target=acquire_writer)
        reader.start()
        self.assertTrue(entered.wait(2), "reference reader did not enter")
        writer.start()
        self.assertFalse(
            writer_acquired.wait(0.2),
            "writer entered while the alignment reader held a shared lock",
        )
        release.set()
        self.assertTrue(
            writer_acquired.wait(2), "writer did not enter after reader exit"
        )
        reader.join(2)
        writer.join(2)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])

    def test_external_ichor_assets_are_pinned_and_never_repaired(self) -> None:
        reference = self.project / "references" / "samurai_ichorcna_hg38_500kb"
        reference.mkdir(parents=True)
        fixture_assets = {
            "gc": ("gc.wig", digest(b"gc")),
            "map": ("map.wig", digest(b"map")),
            "centromere": ("centromere.txt", digest(b"centromere")),
            "reptime": ("reptime.wig", digest(b"reptime")),
            "pon": ("pon.rds", digest(b"pon")),
        }
        for key, (name, _expected) in fixture_assets.items():
            (reference / name).write_bytes(key.encode("utf-8"))
        before = self.snapshot(reference)
        with (
            patch.object(engine, "ICHOR_ASSETS", fixture_assets),
            patch.object(engine, "download") as download,
        ):
            observed = prepare_ichor_assets(self.project, 500)
        download.assert_not_called()
        self.assertEqual(set(observed), set(fixture_assets))
        self.assertEqual(self.snapshot(reference), before)

        (reference / "pon.rds").write_bytes(b"corrupt")
        corrupt = self.snapshot(reference)
        with (
            patch.object(engine, "ICHOR_ASSETS", fixture_assets),
            patch.object(engine, "download") as download,
        ):
            with self.assertRaisesRegex(OncoTracerError, "SHA-256 mismatch"):
                prepare_ichor_assets(self.project, 500)
        download.assert_not_called()
        self.assertEqual(self.snapshot(reference), corrupt)

    def test_owned_ichor_assets_download_to_marker_owned_cache(self) -> None:
        fixture_assets = {
            "gc": ("gc.wig", digest(b"gc")),
            "map": ("map.wig", digest(b"map")),
            "centromere": ("centromere.txt", digest(b"centromere")),
            "reptime": ("reptime.wig", digest(b"reptime")),
            "pon": ("pon.rds", digest(b"pon")),
        }

        def download_asset(url: str, destination: Path, **_kwargs):
            name = url.rsplit("/", 1)[-1]
            key = next(key for key, value in fixture_assets.items() if value[0] == name)
            destination.write_bytes(key.encode("utf-8"))
            return destination

        with (
            patch.object(engine, "ICHOR_ASSETS", fixture_assets),
            patch.object(engine, "download", side_effect=download_asset),
        ):
            observed = prepare_ichor_assets(self.project, 500)
        roots = {path.parent for path in observed.values()}
        self.assertEqual(len(roots), 1)
        root = roots.pop()
        self.assertIn(self.project / ".oncotracer" / "reference-cache", root.parents)
        self.assertTrue((root / ".oncotracer-reference-owner.json").is_file())
        self.assertFalse((self.project / "references").exists())


if __name__ == "__main__":
    unittest.main()
