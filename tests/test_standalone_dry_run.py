#!/usr/bin/env python3
"""Side-effect regressions for release-shaped standalone dry-runs."""

from __future__ import annotations

import contextlib
import concurrent.futures
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from oncotracer_cli import cli, runtime

ROOT = Path(__file__).resolve().parents[1]
CaseSetup = Callable[[Path], tuple[list[str], tuple[Path, ...]]]


class DryRunPayloadCacheScopeTests(unittest.TestCase):
    def test_cli_restores_existing_payload_cache_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "persistent-payload"
            environment = {"ONCOTRACER_PAYLOAD_CACHE": str(existing)}
            with mock.patch.dict(os.environ, environment, clear=False):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    status = cli.main(
                        [
                            "install",
                            "--docker",
                            "--dry-run",
                            "--root",
                            str(ROOT),
                        ]
                    )
                self.assertEqual(status, 0)
                self.assertEqual(os.environ["ONCOTRACER_PAYLOAD_CACHE"], str(existing))
            self.assertFalse(existing.exists())

    def test_payload_cache_scope_is_isolated_between_threads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistent = (Path(directory) / "persistent-payload").resolve()
            barrier = threading.Barrier(2)

            def worker(label: str) -> Path:
                with runtime.isolated_payload_cache():
                    scoped = runtime._payload_cache()
                    scoped.mkdir(parents=True)
                    (scoped / label).write_text(label, encoding="utf-8")
                    barrier.wait()
                    if runtime._payload_cache() != scoped:
                        raise AssertionError(
                            "payload-cache context leaked between threads"
                        )
                    barrier.wait()
                    return scoped

            with mock.patch.dict(
                os.environ, {"ONCOTRACER_PAYLOAD_CACHE": str(persistent)}, clear=False
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    paths = list(executor.map(worker, ("first", "second")))
                self.assertEqual(runtime._payload_cache(), persistent)

            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(not path.parent.exists() for path in paths))
            self.assertFalse(persistent.exists())

    def test_cli_cleans_payload_scope_after_keyboard_interrupt(self) -> None:
        observed: list[Path] = []

        def interrupt(_args) -> int:
            scoped = runtime._payload_cache()
            scoped.mkdir(parents=True)
            observed.append(scoped)
            raise KeyboardInterrupt

        args = mock.Mock(func=interrupt, dry_run=True)
        parser = mock.Mock()
        parser.parse_args.return_value = args
        with mock.patch.object(cli, "build_parser", return_value=parser):
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = cli.main(["install", "--docker", "--dry-run"])

        self.assertEqual(status, 130)
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].parent.exists())

    def test_cli_restores_payload_cache_override_after_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "persistent-payload"
            missing = Path(directory) / "missing.yml"
            environment = {"ONCOTRACER_PAYLOAD_CACHE": str(existing)}
            with mock.patch.dict(os.environ, environment, clear=False):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    status = cli.main(["run", "--config", str(missing), "--dry-run"])
                self.assertEqual(status, 2)
                self.assertEqual(os.environ["ONCOTRACER_PAYLOAD_CACHE"], str(existing))
            self.assertFalse(existing.exists())


class StandaloneDryRunTests(unittest.TestCase):
    """Run dry-runs through the same single-file shape shipped in a release."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._build_directory = tempfile.TemporaryDirectory(
            prefix="oncotracer-standalone-dry-run-test-"
        )
        cls.addClassCleanup(cls._build_directory.cleanup)
        cls.binary = Path(cls._build_directory.name) / "oncotracer"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_native_binary.py"),
                "--root",
                str(ROOT),
                "--output",
                str(cls.binary),
                "--allow-unbound-development",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            raise AssertionError(
                "standalone test build failed:\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

    @staticmethod
    def _tree_snapshot(root: Path) -> dict[Path, tuple[str, bytes | None]]:
        snapshot: dict[Path, tuple[str, bytes | None]] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.fsencode(os.readlink(path)))
            elif path.is_dir():
                snapshot[relative] = ("directory", None)
            else:
                snapshot[relative] = ("file", path.read_bytes())
        return snapshot

    def _run_case(self, prepare: CaseSetup) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(
            prefix="oncotracer-dry-run-case-"
        ) as directory:
            base = Path(directory)
            fake_bin = base / "fake-bin"
            temp_space = base / "temp-space"
            fake_bin.mkdir()
            temp_space.mkdir()
            fake_program = (
                "#!/bin/sh\n" ': > "$ONCOTRACER_DRY_RUN_SENTINEL"\n' "exit 97\n"
            )
            for name in (
                "apptainer",
                "bash",
                "conda",
                "docker",
                "poetry",
                "singularity",
            ):
                executable = fake_bin / name
                executable.write_text(fake_program, encoding="utf-8")
                executable.chmod(0o755)

            arguments, forbidden = prepare(base)
            sentinel = base / "external-command-ran"
            home = base / "home"
            config_home = base / "config-home"
            data_home = base / "data-home"
            cache_home = base / "cache-home"
            persistent_payload = base / "persistent-payload-cache"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CACHE_HOME": str(cache_home),
                    "ONCOTRACER_PAYLOAD_CACHE": str(persistent_payload),
                    "ONCOTRACER_DRY_RUN_SENTINEL": str(sentinel),
                    "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(temp_space),
                }
            )
            before = self._tree_snapshot(base)
            completed = subprocess.run(
                [str(self.binary), *arguments],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"arguments={arguments!r}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            self.assertEqual(self._tree_snapshot(base), before)
            for path in (
                home,
                config_home,
                data_home,
                cache_home,
                persistent_payload,
                sentinel,
                *forbidden,
            ):
                self.assertFalse(path.exists(), f"dry-run created {path}")
            return completed

    def test_standalone_install_dry_runs_leave_no_state(self) -> None:
        for backend in ("conda", "docker", "singularity", "poetry"):
            with self.subTest(backend=backend):

                def prepare(base: Path, selected: str = backend):
                    envs = base / "generated-envs"
                    image = base / "generated-image.sif"
                    return (
                        [
                            "install",
                            f"--{selected}",
                            "--dry-run",
                            "--prefix",
                            str(envs),
                            "--sif",
                            str(image),
                        ],
                        (envs, image),
                    )

                self._run_case(prepare)

    def test_standalone_explicit_root_wins_without_cache_extraction(self) -> None:
        def prepare(base: Path):
            explicit = base / "explicit-root"
            (explicit / "bin" / "scripts").mkdir(parents=True)
            environments = explicit / "environments"
            environments.mkdir()
            for name in (
                "native-core.yml",
                "native-qdnaseq.yml",
                "native-ichorcna.yml",
                "native-classifier.yml",
                "native-gistic2.yml",
            ):
                (environments / name).write_text("name: test\n", encoding="utf-8")
            target = base / "generated-envs"
            return (
                [
                    "install",
                    "--conda",
                    "--root",
                    str(explicit),
                    "--prefix",
                    str(target),
                    "--dry-run",
                ],
                (target,),
            )

        completed = self._run_case(prepare)
        self.assertIn("explicit-root/environments/native-core.yml", completed.stderr)

    def test_standalone_ignores_an_adjacent_checkout_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            checkout = base / "adjacent-checkout"
            (checkout / "bin" / "scripts").mkdir(parents=True)
            (checkout / "bin" / "scripts" / "adjacent-sentinel").write_text(
                "must not be used\n", encoding="utf-8"
            )
            adjacent_environments = checkout / "environments"
            adjacent_environments.mkdir()
            for name in (
                "native-core.yml",
                "native-qdnaseq.yml",
                "native-ichorcna.yml",
                "native-classifier.yml",
                "native-gistic2.yml",
            ):
                (adjacent_environments / name).write_text(
                    "name: adjacent-sentinel\n", encoding="utf-8"
                )

            executable = checkout / "oncotracer"
            executable.write_bytes(self.binary.read_bytes())
            executable.chmod(0o755)
            fake_bin = base / "fake-bin"
            fake_bin.mkdir()
            conda_ran = base / "conda-ran"
            conda = fake_bin / "conda"
            conda.write_text(
                "#!/bin/sh\n" ': > "$ONCOTRACER_CONDA_SENTINEL"\n' "exit 97\n",
                encoding="utf-8",
            )
            conda.chmod(0o755)
            payload = base / "verified-embedded-payload"
            environment = os.environ.copy()
            environment.pop("ONCOTRACER_ROOT", None)
            environment.update(
                {
                    "HOME": str(base / "home"),
                    "XDG_CONFIG_HOME": str(base / "config-home"),
                    "XDG_DATA_HOME": str(base / "data-home"),
                    "XDG_CACHE_HOME": str(base / "cache-home"),
                    "ONCOTRACER_PAYLOAD_CACHE": str(payload),
                    "ONCOTRACER_CONDA_SENTINEL": str(conda_ran),
                    "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            completed = subprocess.run(
                [
                    str(executable),
                    "install",
                    "--conda",
                    "--prefix",
                    str(base / "envs"),
                ],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertTrue(conda_ran.is_file())
            self.assertTrue((payload / ".complete.json").is_file())
            embedded_core = payload / "environments" / "native-core.yml"
            self.assertTrue(embedded_core.is_file())
            self.assertNotIn("adjacent-sentinel", embedded_core.read_text())
            self.assertIn(str(embedded_core), completed.stderr)
            self.assertNotIn(str(adjacent_environments), completed.stderr)

    def test_normal_standalone_command_keeps_reusable_payload_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fake_bin = base / "fake-bin"
            fake_bin.mkdir()
            sentinel = base / "conda-ran"
            conda = fake_bin / "conda"
            conda.write_text(
                "#!/bin/sh\n" ': > "$ONCOTRACER_NORMAL_RUN_SENTINEL"\n' "exit 97\n",
                encoding="utf-8",
            )
            conda.chmod(0o755)
            payload = base / "persistent-payload-cache"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(base / "home"),
                    "XDG_CONFIG_HOME": str(base / "config-home"),
                    "XDG_DATA_HOME": str(base / "data-home"),
                    "XDG_CACHE_HOME": str(base / "cache-home"),
                    "ONCOTRACER_PAYLOAD_CACHE": str(payload),
                    "ONCOTRACER_NORMAL_RUN_SENTINEL": str(sentinel),
                    "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            completed = subprocess.run(
                [
                    str(self.binary),
                    "install",
                    "--conda",
                    "--prefix",
                    str(base / "envs"),
                ],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertTrue(sentinel.is_file())
            self.assertTrue((payload / ".complete.json").is_file())
            self.assertTrue((payload / "bin" / "scripts").is_dir())

    def test_standalone_analysis_dry_runs_leave_no_state(self) -> None:
        for backend in ("host", "conda", "docker", "singularity", "poetry"):
            with self.subTest(backend=backend):

                def prepare(base: Path, selected: str = backend):
                    first = base / "TUMOR_R1.fastq.gz"
                    second = base / "TUMOR_R2.fastq.gz"
                    first.write_bytes(b"reads-1")
                    second.write_bytes(b"reads-2")
                    samples = base / "samples.csv"
                    samples.write_text(
                        "sample,fastq_1,fastq_2,status\n"
                        f"TUMOR,{first},{second},tumor\n",
                        encoding="utf-8",
                    )
                    project = base / "generated-project"
                    results = base / "generated-results"
                    config = base / "run.yml"
                    config.write_text(
                        "mode: illumina\n"
                        f"lpwgs_root: {project}\n"
                        f"outdir: {results}\n"
                        f"illumina_samplesheet: {samples}\n"
                        "illumina_binsize_kb: 100\n"
                        "force: false\n",
                        encoding="utf-8",
                    )
                    image = base / "generated-image.sif"
                    return (
                        [
                            "run",
                            "--config",
                            str(config),
                            "--backend",
                            selected,
                            "--sif",
                            str(image),
                            "--dry-run",
                        ],
                        (project, results, image),
                    )

                completed = self._run_case(prepare)
                self.assertIn(
                    '"schema": "oncotracer-native-dry-run-v1"', completed.stdout
                )

    def test_standalone_auto_and_quickstart_dry_runs_leave_no_state(self) -> None:
        def prepare_auto(base: Path):
            reads = base / "reads"
            reads.mkdir()
            (reads / "TUMOR_R1.fastq.gz").write_bytes(b"reads-1")
            samples = base / "sample-table.csv"
            samples.write_text("sample,status\nTUMOR,tumor\n", encoding="utf-8")
            configs = base / "generated-configs"
            results = base / "generated-results"
            return (
                [
                    "auto",
                    "--mode",
                    "illumina",
                    "--reads-folder",
                    str(reads),
                    "--sample-table",
                    str(samples),
                    "--config-dir",
                    str(configs),
                    "--outdir",
                    str(results),
                    "--dry-run",
                ],
                (configs, results),
            )

        self._run_case(prepare_auto)
        for number in ("1", "2"):
            with self.subTest(quickstart=number):

                def prepare_quickstart(base: Path, selected: str = number):
                    destination = base / f"quickstart-{selected}"
                    return (
                        [
                            "quickstart",
                            selected,
                            "--test-root",
                            str(destination),
                            "--backend",
                            "conda",
                            "--dry-run",
                        ],
                        (destination,),
                    )

                completed = self._run_case(prepare_quickstart)
                self.assertIn(
                    f"QuickStart {number} dry-run completed without writing files",
                    completed.stdout,
                )


if __name__ == "__main__":
    unittest.main()
