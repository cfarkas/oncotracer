#!/usr/bin/env python3
"""Compatibility tests for the Poetry-installed native v2 launcher."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import __version__
from oncotracer_cli.cli import _legacy_to_modern, build_parser


class PoetryNativeLauncherTests(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "2.0.0")

    def test_script_entrypoint_accepts_run(self) -> None:
        args = build_parser().parse_args(["run", "--config", "x.yml", "--backend", "poetry"])
        self.assertEqual(args.backend, "poetry")

    def test_v1_argument_shape_translates_without_nextflow(self) -> None:
        translated = _legacy_to_modern(["--docker", "-params-file", "x.yml", "-resume"])
        self.assertEqual(translated, ["run", "--config", "x.yml", "--backend", "docker"])


if __name__ == "__main__":
    unittest.main()
