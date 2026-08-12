#!/usr/bin/env python3
"""Storage-safety regressions for native analysis output ownership."""

from __future__ import annotations

import io
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

from oncotracer_cli import engine, output_safety  # noqa: E402
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


def make_runtime_root(base: Path, name: str = "runtime") -> Path:
    root = base / name
    script = root / "bin" / "scripts" / "runtime-sentinel.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return root


def make_illumina_config(base: Path, output: Path, name: str) -> Path:
    first = base / f"{name}_R1.fastq.gz"
    second = base / f"{name}_R2.fastq.gz"
    first.write_bytes(b"reads-1")
    second.write_bytes(b"reads-2")
    samples = base / f"{name}.samples.csv"
    samples.write_text(
        "sample,fastq_1,fastq_2,status\n"
        f"{name},{first},{second},tumor\n",
        encoding="utf-8",
    )
    config = base / f"{name}.yml"
    config.write_text(
        "mode: illumina\n"
        f"outdir: {output}\n"
        f"illumina_samplesheet: {samples}\n"
        "illumina_binsize_kb: 100\n"
        "force: false\n",
        encoding="utf-8",
    )
    return config


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

    def test_configuration_change_is_detected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(
                output, config_path=config, identity=IDENTITY
            ) as lease:
                config.write_text("mode: ont\n", encoding="utf-8")
                with self.assertRaisesRegex(OncoTracerError, "configuration changed"):
                    lease.validate()

    def test_runtime_change_is_detected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "runtime"
            script = runtime_root / "bin" / "tool.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            config = root / "config.yml"
            config.write_text("mode: illumina\n", encoding="utf-8")
            output = root / "run"
            with claim_output_run(
                output, config_path=config, runtime_root_path=runtime_root
            ) as lease:
                script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                with self.assertRaisesRegex(OncoTracerError, "runtime changed"):
                    lease.validate()

    def test_dry_run_binds_the_effective_environment_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment_root = make_runtime_root(base)
            invalid_explicit = base / "missing-explicit-root"
            output = base / "dry-run-output"
            config = make_illumina_config(base, output, "DRY")

            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"ONCOTRACER_ROOT": str(environment_root)},
                    clear=False,
                ),
                patch.object(
                    engine, "runtime_root", wraps=engine.runtime_root
                ) as resolver,
                patch.object(
                    output_safety,
                    "current_runtime_identity",
                    wraps=output_safety.current_runtime_identity,
                ) as identity,
                patch("sys.stdout", stdout),
            ):
                self.assertEqual(
                    run_native(config, root=invalid_explicit, dry_run=True), output
                )

            resolver.assert_called_once_with(invalid_explicit)
            identity.assert_called_once_with(environment_root)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(plan["lpwgs_root"], str(environment_root / "project"))
            self.assertFalse(os.path.lexists(output))

    def test_real_run_resume_and_final_validation_share_one_runtime_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment_root = make_runtime_root(base)
            invalid_explicit = base / "missing-explicit-root"
            output = base / "run"
            config = make_illumina_config(base, output, "REAL")
            observed_execution_roots: list[Path] = []

            def fake_qdnaseq(execution_root: Path, *_args, **_kwargs):
                observed_execution_roots.append(execution_root)
                return output / "fake-qdnaseq", output / "fake-bams"

            def fake_outputs(*args, **_kwargs) -> None:
                selected_output = args[6]
                summary = selected_output / "06_workflow_summary"
                summary.mkdir(parents=True, exist_ok=True)
                (summary / "workflow_summary.json").write_text(
                    '{"workflow_status":"complete"}\n', encoding="utf-8"
                )
                (summary / "workflow_summary.txt").write_text(
                    "workflow_status=complete\n", encoding="utf-8"
                )

            with (
                patch.dict(
                    os.environ,
                    {"ONCOTRACER_ROOT": str(environment_root)},
                    clear=False,
                ),
                patch.object(
                    engine, "runtime_root", wraps=engine.runtime_root
                ) as resolver,
                patch.object(
                    output_safety,
                    "current_runtime_identity",
                    wraps=output_safety.current_runtime_identity,
                ) as identity,
                patch.object(engine, "prepare_reference", return_value={}),
                patch.object(
                    engine,
                    "align_illumina",
                    return_value={"REAL": base / "REAL.bam"},
                ),
                patch.object(engine, "run_qdnaseq", side_effect=fake_qdnaseq),
                patch.object(
                    engine,
                    "run_refinement_and_outputs",
                    side_effect=fake_outputs,
                ),
            ):
                self.assertEqual(run_native(config), output)
                first_owner = (output / OUTPUT_OWNER_RELATIVE).read_bytes()
                self.assertEqual(run_native(config, root=invalid_explicit), output)
                second_owner = (output / OUTPUT_OWNER_RELATIVE).read_bytes()

            self.assertEqual(first_owner, second_owner)
            self.assertEqual(
                [args[0] for args, _kwargs in resolver.call_args_list],
                [None, invalid_explicit],
            )
            self.assertEqual(observed_execution_roots, [environment_root] * 2)
            self.assertEqual(
                [args[0] for args, _kwargs in identity.call_args_list],
                [environment_root] * 6,
            )
            self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))

    def test_engine_final_validation_rehashes_selected_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment_root = make_runtime_root(base)
            runtime_script = (
                environment_root / "bin" / "scripts" / "runtime-sentinel.sh"
            )
            output = base / "run"
            config = make_illumina_config(base, output, "FINAL")

            def fake_outputs(*args, **_kwargs) -> None:
                selected_output = args[6]
                summary = selected_output / "06_workflow_summary"
                summary.mkdir(parents=True, exist_ok=True)
                (summary / "workflow_summary.json").write_text(
                    '{"workflow_status":"complete"}\n', encoding="utf-8"
                )
                (summary / "workflow_summary.txt").write_text(
                    "workflow_status=complete\n", encoding="utf-8"
                )
                runtime_script.write_text(
                    "#!/bin/sh\nexit 9\n", encoding="utf-8"
                )

            with (
                patch.dict(
                    os.environ,
                    {"ONCOTRACER_ROOT": str(environment_root)},
                    clear=False,
                ),
                patch.object(engine, "prepare_reference", return_value={}),
                patch.object(
                    engine,
                    "align_illumina",
                    return_value={"FINAL": base / "FINAL.bam"},
                ),
                patch.object(
                    engine,
                    "run_qdnaseq",
                    return_value=(output / "fake-qdnaseq", output / "fake-bams"),
                ),
                patch.object(
                    engine,
                    "run_refinement_and_outputs",
                    side_effect=fake_outputs,
                ),
            ):
                with self.assertRaisesRegex(OncoTracerError, "runtime changed"):
                    run_native(config)

            self.assertTrue((output / OUTPUT_OWNER_RELATIVE).is_file())
            self.assertFalse(os.path.lexists(output / OUTPUT_ACTIVE_RELATIVE))

    def test_config_parse_race_fails_before_output_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "run"
            config = root / "config.yml"
            original = f"mode: illumina\noutdir: {output}\n"
            config.write_text(original, encoding="utf-8")
            real_loader = engine.load_flat_yaml

            def changing_loader(path: Path) -> dict[str, object]:
                parsed = real_loader(path)
                path.write_text(original + "force: true\n", encoding="utf-8")
                return parsed

            with patch.object(engine, "load_flat_yaml", side_effect=changing_loader):
                with self.assertRaisesRegex(
                    OncoTracerError, "changed while it was being parsed"
                ):
                    run_native(config, root=ROOT)
            self.assertFalse(os.path.lexists(output))

    def test_verified_config_is_not_parsed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "run"
            config = root / "config.yml"
            config.write_text(f"mode: illumina\noutdir: {output}\n", encoding="utf-8")
            with patch.object(
                engine, "load_flat_yaml", wraps=engine.load_flat_yaml
            ) as loader:
                with self.assertRaisesRegex(OncoTracerError, "illumina_samplesheet"):
                    run_native(config, root=ROOT)
            self.assertEqual(loader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
