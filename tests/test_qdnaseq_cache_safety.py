#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import engine
from oncotracer_cli.engine import Toolchain, prepare_qdnaseq_annotation
from oncotracer_cli.runtime import CommandRunner, OncoTracerError


class QdnaSeqCacheSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        helper = self.repository / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        helper.chmod(0o755)
        self.project = self.root / "project"
        self.project.mkdir()
        self.prefix = self.root / "qdnaseq"
        for name in ("bash", "Rscript"):
            executable = self.prefix / "bin" / name
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        self.source_bytes = b"fixture-pinned-qdnaseq-source"
        self.rds_bytes = b"fixture-qdnaseq-rds"
        self.source_sha256 = hashlib.sha256(self.source_bytes).hexdigest()
        self.rds_sha256 = hashlib.sha256(self.rds_bytes).hexdigest()
        self.pins = {
            **engine.QDNASEQ_HG38_SOURCE_SHA256,
            100: self.source_sha256,
        }
        self.run_count = 0
        self.run_count_lock = threading.Lock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build_bundle(self, command, **_kwargs):
        with self.run_count_lock:
            self.run_count += 1
        staging = Path(command[command.index("--cache-dir") + 1])
        source = staging / "QDNAseq.hg38.100kbp.SR50.source.rda"
        annotation = staging / "QDNAseq.hg38.100kbp.SR50.rds"
        provenance = Path(f"{annotation}.provenance.tsv")
        source.write_bytes(self.source_bytes)
        annotation.write_bytes(self.rds_bytes)
        provenance.write_text(
            "field\tvalue\n"
            "source_url\thttps://raw.githubusercontent.com/asntech/"
            f"QDNAseq.hg38/{engine.QDNASEQ_HG38_COMMIT}/"
            "data/hg38.100kbp.SR50.rda\n"
            f"source_commit\t{engine.QDNASEQ_HG38_COMMIT}\n"
            f"source_rda_sha256\t{self.source_sha256}\n"
            "object\thg38.100kbp.SR50\n"
            f"rds_sha256\t{self.rds_sha256}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{annotation}\n", stderr=""
        )

    def prepare(self) -> Path:
        return prepare_qdnaseq_annotation(
            self.repository,
            self.project,
            100,
            CommandRunner(self.root / "trace.tsv", echo=False),
            Toolchain(qdnaseq_prefix=self.prefix),
        )

    def cache(self) -> Path:
        caches = list(
            (self.project / ".oncotracer" / "reference-cache").glob(
                "qdnaseq-hg38-100kb-*"
            )
        )
        self.assertEqual(len(caches), 1)
        return caches[0]

    def test_initial_claim_and_build_are_serialized_across_threads(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        results: list[Path] = []
        errors: list[BaseException] = []

        def delayed_build(command, **kwargs):
            result = self.build_bundle(command, **kwargs)
            entered.set()
            if not release.wait(5):
                raise AssertionError("timed out holding qDNAseq staging build")
            return result

        def worker() -> None:
            try:
                results.append(self.prepare())
            except BaseException as error:  # pragma: no cover - surfaced below
                errors.append(error)

        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=delayed_build),
        ):
            first = threading.Thread(target=worker)
            second = threading.Thread(target=worker)
            first.start()
            self.assertTrue(entered.wait(2), "first qDNAseq builder did not enter")
            second.start()
            time.sleep(0.2)
            self.assertEqual(self.run_count, 1)
            release.set()
            first.join(3)
            second.join(3)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.run_count, 1)
        pointer = self.cache() / "current-100kb.json"
        self.assertTrue(pointer.is_file())
        self.assertEqual(
            json.loads(pointer.read_text())["files"].keys(),
            {
                "QDNAseq.hg38.100kbp.SR50.source.rda",
                "QDNAseq.hg38.100kbp.SR50.rds",
                "QDNAseq.hg38.100kbp.SR50.rds.provenance.tsv",
            },
        )

    def test_failed_builder_leaves_no_pointer_generation_or_staging(self) -> None:
        legacy = self.project / ".oncotracer" / "qdnaseq-bin-data"
        legacy.mkdir(parents=True)
        sentinel = legacy / "PATIENT_SENTINEL"
        sentinel.write_bytes(b"must-survive")
        failed = subprocess.CompletedProcess([], 9, stdout="", stderr="failed\n")
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", return_value=failed),
            self.assertRaisesRegex(OncoTracerError, "preparation failed"),
        ):
            self.prepare()

        cache = self.cache()
        self.assertFalse((cache / "current-100kb.json").exists())
        self.assertEqual(list((cache / "generations").iterdir()), [])
        self.assertEqual(list(cache.glob(".qdnaseq-100kb-build-*")), [])
        self.assertEqual(sentinel.read_bytes(), b"must-survive")

    def test_symlink_pointer_is_rejected_without_touching_target(self) -> None:
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
        ):
            self.prepare()
        pointer = self.cache() / "current-100kb.json"
        protected = self.root / "protected-pointer-target"
        protected.write_bytes(b"must-survive")
        pointer = self.root / pointer.absolute().relative_to(self.root.absolute())
        pointer.unlink()
        pointer.symlink_to(protected)

        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run") as run,
            self.assertRaisesRegex(OncoTracerError, "not a physical file"),
        ):
            self.prepare()
        run.assert_not_called()
        self.assertEqual(protected.read_bytes(), b"must-survive")

    def test_tampered_published_generation_is_never_overwritten(self) -> None:
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
        ):
            annotation = self.prepare()
        source = annotation.with_name("QDNAseq.hg38.100kbp.SR50.source.rda")
        pointer = self.cache() / "current-100kb.json"
        pointer_before = pointer.read_bytes()
        source.chmod(0o644)
        source.write_bytes(b"tampered")

        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
            self.assertRaisesRegex(OncoTracerError, "provenance does not match"),
        ):
            self.prepare()
        self.assertEqual(source.read_bytes(), b"tampered")
        self.assertEqual(pointer.read_bytes(), pointer_before)
        self.assertEqual(list(self.cache().glob(".qdnaseq-100kb-build-*")), [])

    def test_reader_revalidates_bundle_after_scientific_use(self) -> None:
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
        ):
            annotation = self.prepare()
        source = annotation.with_name("QDNAseq.hg38.100kbp.SR50.source.rda")
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            self.assertRaisesRegex(OncoTracerError, "changed or became invalid"),
        ):
            with engine._validated_qdnaseq_reader(
                annotation,
                self.project,
                100,
                CommandRunner(self.root / "reader-trace.tsv", echo=False),
            ):
                source.chmod(0o644)
                source.write_bytes(b"tampered-during-qdnaseq")
                raise RuntimeError("simulated scientific failure")

    def test_hardlinked_staged_file_is_rejected_without_chmod_or_publication(
        self,
    ) -> None:
        protected = self.root / "protected-source"
        protected.write_bytes(self.source_bytes)
        protected.chmod(0o640)
        protected_mode = protected.stat().st_mode

        def malicious_build(command, **kwargs):
            result = self.build_bundle(command, **kwargs)
            staging = Path(command[command.index("--cache-dir") + 1])
            source = staging / "QDNAseq.hg38.100kbp.SR50.source.rda"
            source = self.root / source.absolute().relative_to(self.root.absolute())
            source.unlink()
            source.hardlink_to(protected)
            return result

        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=malicious_build),
            self.assertRaisesRegex(OncoTracerError, "must not be hardlinked"),
        ):
            self.prepare()
        cache = self.cache()
        self.assertEqual(protected.read_bytes(), self.source_bytes)
        self.assertEqual(protected.stat().st_mode, protected_mode)
        self.assertFalse((cache / "current-100kb.json").exists())
        self.assertEqual(list((cache / "generations").iterdir()), [])
        self.assertEqual(list(cache.glob(".qdnaseq-100kb-build-*")), [])

    def test_interrupted_initial_claim_leaves_target_empty_and_retryable(self) -> None:
        parent = self.root / "claims"
        parent.mkdir()
        target = parent / "cache"
        target.mkdir()
        marker = {"schema": "fixture", "canonical_path": str(target)}
        with (
            patch("os.replace", side_effect=OSError("simulated interruption")),
            self.assertRaisesRegex(OSError, "simulated interruption"),
        ):
            engine._claim_marker(target, marker, "fixture cache")
        self.assertEqual(list(target.iterdir()), [])
        orphan = parent / ".oncotracer-reference-owner.json.tmp-interrupted"
        orphan.write_bytes(b"simulated crash residue")
        engine._claim_marker(target, marker, "fixture cache")
        self.assertTrue((target / ".oncotracer-reference-owner.json").is_file())
        self.assertEqual(orphan.read_bytes(), b"simulated crash residue")

    def test_claim_rejects_hardlinked_marker_without_mutation(self) -> None:
        parent = self.root / "hardlink-claims"
        parent.mkdir()
        target = parent / "cache"
        target.mkdir()
        marker_path = target / ".oncotracer-reference-owner.json"
        marker = {"schema": "fixture", "canonical_path": str(target)}
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        protected = self.root / "protected-marker-link"
        protected.hardlink_to(marker_path)
        before = marker_path.read_bytes()
        with self.assertRaisesRegex(OncoTracerError, "mismatched ownership marker"):
            engine._claim_marker(target, marker, "fixture cache")
        self.assertEqual(marker_path.read_bytes(), before)
        self.assertEqual(protected.read_bytes(), before)

    def test_claim_detects_directory_replacement_while_waiting(self) -> None:
        parent = self.root / "swap-claims"
        parent.mkdir()
        target = parent / "cache"
        target.mkdir()
        original = parent / "original-cache"

        @contextlib.contextmanager
        def swap_before_lock(_path, _label):
            target.rename(original)
            target.mkdir()
            yield

        with (
            patch.object(engine, "_physical_directory_lock", swap_before_lock),
            self.assertRaisesRegex(
                OncoTracerError, "changed while acquiring ownership"
            ),
        ):
            engine._claim_marker(
                target,
                {"schema": "fixture", "canonical_path": str(target)},
                "fixture cache",
            )
        self.assertEqual(list(original.iterdir()), [])
        self.assertEqual(list(target.iterdir()), [])

    def test_interrupted_pointer_publish_recovers_without_overwrite(self) -> None:
        original = engine._atomic_write_owned_json

        def interrupt_pointer(path, value, label, **kwargs):
            if path.name == "current-100kb.json":
                raise OncoTracerError("simulated pointer publication interruption")
            return original(path, value, label, **kwargs)

        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
            patch.object(
                engine, "_atomic_write_owned_json", side_effect=interrupt_pointer
            ),
            self.assertRaisesRegex(OncoTracerError, "publication interruption"),
        ):
            self.prepare()

        cache = self.cache()
        generations = list((cache / "generations").iterdir())
        self.assertEqual(len(generations), 1)
        self.assertFalse((cache / "current-100kb.json").exists())
        before = {path.name: path.read_bytes() for path in generations[0].iterdir()}
        with (
            patch.object(engine, "QDNASEQ_HG38_SOURCE_SHA256", self.pins),
            patch("subprocess.run", side_effect=self.build_bundle),
        ):
            recovered = self.prepare()
        self.assertEqual(recovered.parent, generations[0])
        self.assertEqual(
            {path.name: path.read_bytes() for path in generations[0].iterdir()},
            before,
        )
        self.assertTrue((cache / "current-100kb.json").is_file())


if __name__ == "__main__":
    unittest.main()
