#!/usr/bin/env python3
"""Storage-safety regressions for native analysis output ownership."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import output_safety  # noqa: E402
from oncotracer_cli.engine import run_native, write_run_manifest  # noqa: E402
from oncotracer_cli.output_safety import (  # noqa: E402
    OUTPUT_ACTIVE_RELATIVE,
    OUTPUT_OWNER_RELATIVE,
    claim_output_run,
    inspect_output_target,
)
from oncotracer_cli.runtime import OncoTracerError  # noqa: E402


IDENTITY = {
    "oncotracer_version": "2.0.0",
    "source_commit": "a" * 40,
    "source_sha256": "b" * 64,
    "source_tree_dirty": False,
    "binary_sha256": "c" * 64,
    "runtime_payload_sha256": "c" * 64,
}


def tree_snapshot(root: Path) -> list[tuple[str, str, int, str]]:
    if not os.path.lexists(root):
        return []
    records: list[tuple[str, str, int, str]] = []
    candidates = [root]
    if root.is_dir() and not root.is_symlink():
        candidates.extend(sorted(root.rglob("*"), key=lambda item: item.as_posix()))
    for path in candidates:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
            content = path.read_bytes().hex()
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            content = ""
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            content = os.readlink(path)
        elif stat.S_ISFIFO(metadata.st_mode):
            kind = "fifo"
            content = ""
        else:
            kind = "special"
            content = ""
        records.append((relative, kind, stat.S_IMODE(metadata.st_mode), content))
    return records


class OutputSafetyTests(unittest.TestCase):
    def test_absent_and_empty_output_are_claimed_then_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            for output in (root / "absent", root / "empty"):
                if output.name == "empty":
                    output.mkdir()
                with claim_output_run(
                    output, config_path=config, identity=IDENTITY
                ) as lease:
                    self.assertTrue((output / OUTPUT_OWNER_RELATIVE).is_file())
                    self.assertTrue((output / OUTPUT_ACTIVE_RELATIVE).is_file())
                    lease.validate()
                self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))
                first = json.loads((output / OUTPUT_OWNER_RELATIVE).read_text())
                with claim_output_run(output, config_path=config, identity=IDENTITY):
                    pass
                second = json.loads((output / OUTPUT_OWNER_RELATIVE).read_text())
                self.assertEqual(first, second)
                self.assertNotIn(str(output), json.dumps(first))

    def test_unowned_nonempty_output_is_preserved_with_or_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "foreign-results"
            (output / "03_cna_codification").mkdir(parents=True)
            sentinel = output / "03_cna_codification" / "cna_events.tsv"
            sentinel.write_bytes(b"protected scientific result\n")
            config = root / "config.yml"
            config.write_text(
                f"mode: illumina\noutdir: {output}\nforce: true\n", encoding="utf-8"
            )
            before = tree_snapshot(output)
            for force in (False, True):
                with self.assertRaisesRegex(OncoTracerError, "nonempty, unowned"):
                    run_native(config, root=ROOT, force=force)
                self.assertEqual(tree_snapshot(output), before)
            self.assertFalse(os.path.lexists(output / ".oncotracer-native"))

    def test_mismatched_owner_marker_preserves_every_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(output, config_path=config, identity=IDENTITY):
                pass
            sentinel = output / "scientific-result.tsv"
            sentinel.write_bytes(b"do not alter\n")
            before = tree_snapshot(output)
            different = dict(IDENTITY, source_commit="d" * 40)
            with self.assertRaisesRegex(
                OncoTracerError, "different OncoTracer runtime"
            ):
                claim_output_run(output, config_path=config, identity=different)
            self.assertEqual(tree_snapshot(output), before)

    def test_force_does_not_adopt_malformed_or_relocated_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            original = root / "original"
            with claim_output_run(original, config_path=config, identity=IDENTITY):
                pass
            relocated = root / "relocated"
            shutil.copytree(original, relocated)
            before = tree_snapshot(relocated)
            with self.assertRaisesRegex(OncoTracerError, "path mismatch"):
                claim_output_run(relocated, config_path=config, identity=IDENTITY)
            self.assertEqual(tree_snapshot(relocated), before)

            marker = original / OUTPUT_OWNER_RELATIVE
            marker.write_text('{"schema":"forged"}\n', encoding="utf-8")
            before = tree_snapshot(original)
            with self.assertRaisesRegex(OncoTracerError, "unknown schema"):
                claim_output_run(original, config_path=config, identity=IDENTITY)
            self.assertEqual(tree_snapshot(original), before)

    def test_symlinked_output_or_parent_never_claims_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            target = root / "protected"
            target.mkdir()
            (target / "SENTINEL").write_bytes(b"protected")
            before = tree_snapshot(target)

            link = root / "output-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(OncoTracerError, "symlinks"):
                claim_output_run(link, config_path=config, identity=IDENTITY)
            self.assertEqual(tree_snapshot(target), before)

            parent_link = root / "parent-link"
            parent_link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(OncoTracerError, "symlinks"):
                claim_output_run(
                    parent_link / "new-output", config_path=config, identity=IDENTITY
                )
            self.assertEqual(tree_snapshot(target), before)

    def test_broad_targets_and_dry_run_are_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            (home / "SENTINEL").write_bytes(b"home")
            before = tree_snapshot(home)
            with patch.dict(os.environ, {"HOME": str(home)}):
                with self.assertRaisesRegex(
                    OncoTracerError, "dedicated analysis child"
                ):
                    inspect_output_target(home, IDENTITY)
            self.assertEqual(tree_snapshot(home), before)
            with self.assertRaisesRegex(OncoTracerError, "dedicated analysis child"):
                inspect_output_target(Path("/"), IDENTITY)

            absent = Path(directory) / "absent" / "run"
            self.assertEqual(inspect_output_target(absent, IDENTITY), absent)
            self.assertFalse(os.path.lexists(absent.parent))

    def test_active_run_lock_is_nonblocking_and_released_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(output, config_path=config, identity=IDENTITY):
                with self.assertRaisesRegex(OncoTracerError, "already using"):
                    claim_output_run(output, config_path=config, identity=IDENTITY)
            self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))
            try:
                with claim_output_run(output, config_path=config, identity=IDENTITY):
                    raise RuntimeError("fixture failure")
            except RuntimeError:
                pass
            self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))
            with claim_output_run(output, config_path=config, identity=IDENTITY):
                pass

    def test_escaping_symlink_hardlink_and_fifo_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            outside = root / "outside"
            outside.write_bytes(b"protected")

            for name, create in (
                (
                    "symlink",
                    lambda product: product.symlink_to(outside),
                ),
                (
                    "hardlink",
                    lambda product: os.link(outside, product),
                ),
                (
                    "fifo",
                    lambda product: os.mkfifo(product),
                ),
            ):
                output = root / name
                with claim_output_run(output, config_path=config, identity=IDENTITY):
                    pass
                reserved = output / "03_cna_codification"
                reserved.mkdir()
                create(reserved / "product")
                before = tree_snapshot(output)
                with self.assertRaises(OncoTracerError):
                    claim_output_run(output, config_path=config, identity=IDENTITY)
                self.assertEqual(tree_snapshot(output), before)
                self.assertEqual(outside.read_bytes(), b"protected")

    def test_internal_symlink_used_by_native_pon_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(output, config_path=config, identity=IDENTITY):
                pass
            bam = output / "01_samurai_illumina" / "alignment" / "sample.bam"
            bam.parent.mkdir(parents=True)
            bam.write_bytes(b"bam")
            link = output / "01_samurai_illumina" / "pon_alignment" / "sample.bam"
            link.parent.mkdir()
            link.symlink_to(bam)
            with claim_output_run(output, config_path=config, identity=IDENTITY):
                pass

    def test_engine_failure_keeps_owner_and_removes_active_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "run"
            config = root / "config.yml"
            config.write_text(f"mode: illumina\noutdir: {output}\n", encoding="utf-8")
            with self.assertRaisesRegex(OncoTracerError, "illumina_samplesheet"):
                run_native(config, root=ROOT)
            self.assertTrue((output / OUTPUT_OWNER_RELATIVE).is_file())
            self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))
            with self.assertRaisesRegex(OncoTracerError, "illumina_samplesheet"):
                run_native(config, root=ROOT)

    def test_runtime_payload_identity_ignores_only_python_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "oncotracer_cli"
            package.mkdir()
            module = package / "output_safety.py"
            module.write_text("# fixture\n", encoding="utf-8")
            script = root / "bin" / "tool.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            with patch.object(output_safety, "__file__", str(module)):
                initial = output_safety._runtime_payload_sha256(None)
                cache = package / "__pycache__"
                cache.mkdir()
                (cache / "output_safety.cpython-312.pyc").write_bytes(b"cache")
                self.assertEqual(output_safety._runtime_payload_sha256(None), initial)
                script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                self.assertNotEqual(
                    output_safety._runtime_payload_sha256(None), initial
                )

    def test_explicit_runtime_root_is_bound_even_with_a_binary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first"
            second = base / "second"
            for root, body in ((first, "exit 0\n"), (second, "exit 1\n")):
                script = root / "bin" / "tool.sh"
                script.parent.mkdir(parents=True)
                script.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
            binary = "f" * 64
            self.assertNotEqual(
                output_safety._runtime_payload_sha256(binary, first),
                output_safety._runtime_payload_sha256(binary, second),
            )

    def test_runtime_payload_rejects_symlink_and_hardlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            outside = base / "outside.sh"
            outside.write_text("#!/bin/sh\n", encoding="utf-8")
            for name, create in (
                ("symlink", lambda path: path.symlink_to(outside)),
                ("hardlink", lambda path: os.link(outside, path)),
            ):
                root = base / name
                script = root / "bin" / "tool.sh"
                script.parent.mkdir(parents=True)
                create(script)
                with self.assertRaisesRegex(OncoTracerError, name):
                    output_safety._runtime_payload_sha256(None, root)

    def test_empty_crash_scaffold_is_recoverable_but_partial_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            recoverable = root / "recoverable"
            (recoverable / ".oncotracer-native").mkdir(parents=True)
            with claim_output_run(recoverable, config_path=config, identity=IDENTITY):
                pass
            self.assertTrue((recoverable / OUTPUT_OWNER_RELATIVE).is_file())

            partial = root / "partial"
            native = partial / ".oncotracer-native"
            native.mkdir(parents=True)
            sentinel = native / "SENTINEL"
            sentinel.write_bytes(b"preserve")
            before = tree_snapshot(partial)
            with self.assertRaisesRegex(OncoTracerError, "nonempty, unowned"):
                claim_output_run(partial, config_path=config, identity=IDENTITY)
            self.assertEqual(tree_snapshot(partial), before)

    def test_owner_tamper_is_detected_and_owner_is_sealed_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            trace = root / "trace.tsv"
            trace.write_text("timestamp\tcommand\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(
                output, config_path=config, identity=IDENTITY
            ) as lease:
                summary = output / "06_workflow_summary"
                summary.mkdir()
                (summary / "workflow_summary.json").write_text(
                    '{"workflow_status":"complete"}\n', encoding="utf-8"
                )
                write_run_manifest(output, config, trace)
                manifest = json.loads(
                    (summary / "native_run_manifest.json").read_text(encoding="utf-8")
                )
                owner_files = [
                    item
                    for item in manifest["files"]
                    if item["path"] == OUTPUT_OWNER_RELATIVE.as_posix()
                ]
                self.assertEqual(len(owner_files), 1)

                marker = output / OUTPUT_OWNER_RELATIVE
                tampered = json.loads(marker.read_text(encoding="utf-8"))
                tampered["created_at"] = "2099-01-01T00:00:00Z"
                marker.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(OncoTracerError, "owner changed"):
                    lease.validate()


if __name__ == "__main__":
    unittest.main()
