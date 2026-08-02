#!/usr/bin/env python3
"""Unit tests for the Poetry-managed OncoTracer launcher."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.cli import UsageError, build_command  # noqa: E402


class PoetryLauncherTests(unittest.TestCase):
    def build(self, arguments: list[str]) -> tuple[list[str], bool]:
        with patch("oncotracer_cli.cli.shutil.which", return_value="/usr/bin/nextflow"):
            return build_command(arguments, cwd=ROOT)

    def test_docker_is_default_backend(self) -> None:
        command, print_only = self.build(
            ["--repo-dir", str(ROOT), "--make_test", "--test_root", "/tmp/test"]
        )
        self.assertEqual(command[:3], ["/usr/bin/nextflow", "run", str(ROOT / "main.nf")])
        self.assertEqual(command[3], "--docker")
        self.assertIn("--make_test", command)
        self.assertFalse(print_only)

    def test_conda_backend_is_forwarded(self) -> None:
        command, _ = self.build(
            ["--repo-dir", str(ROOT), "--backend", "conda", "-params-file", "run.yml"]
        )
        self.assertEqual(command[3], "--conda")
        self.assertIn("-params-file", command)

    def test_singularity_backend_is_forwarded(self) -> None:
        command, _ = self.build(
            ["--repo-dir", str(ROOT), "--backend=singularity", "-resume"]
        )
        self.assertEqual(command[3], "--singularity")

    def test_print_command_is_not_forwarded(self) -> None:
        command, print_only = self.build(
            ["--repo-dir", str(ROOT), "--print-command", "--make_test"]
        )
        self.assertTrue(print_only)
        self.assertNotIn("--print-command", command)

    def test_conflicting_runtime_is_rejected(self) -> None:
        with self.assertRaises(UsageError):
            self.build(
                [
                    "--repo-dir",
                    str(ROOT),
                    "--backend",
                    "conda",
                    "--docker",
                    "-resume",
                ]
            )

    def test_missing_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UsageError):
                self.build(["--repo-dir", directory, "--make_test"])


if __name__ == "__main__":
    unittest.main()
