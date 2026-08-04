#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import __version__  # noqa: E402
from oncotracer_cli.cli import (  # noqa: E402
    _conda_prefixes,
    _legacy_to_modern,
    build_parser,
    prepare_quickstart1,
)
from oncotracer_cli.runtime import load_flat_yaml, render_flat_yaml  # noqa: E402


class NativeCliTests(unittest.TestCase):
    def test_version_is_v2(self) -> None:
        self.assertEqual(__version__, "2.0.0")

    def test_parser_exposes_native_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--config", "x.yml", "--backend", "conda"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.backend, "conda")

    def test_legacy_params_file_is_native_run(self) -> None:
        self.assertEqual(
            _legacy_to_modern(["--conda", "-params-file", "run.yml", "-resume"]),
            ["run", "--config", "run.yml", "--backend", "conda"],
        )

    def test_flat_yaml_round_trip(self) -> None:
        values = {
            "mode": "illumina",
            "outdir": "/tmp/results",
            "force": True,
            "bins": 100,
            "threshold": 0.25,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yml"
            path.write_text(render_flat_yaml(values), encoding="utf-8")
            self.assertEqual(load_flat_yaml(path), values)

    def test_conda_prefixes_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefixes = _conda_prefixes(Path(directory))
            self.assertEqual(set(prefixes), {"core", "qdnaseq", "ichorcna", "classifier", "gistic"})
            self.assertEqual(len(set(prefixes.values())), 5)

    def test_quickstart_configuration_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_download(_url, destination, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"test")
                return destination

            with patch("oncotracer_cli.cli.download", side_effect=fake_download):
                illumina, ont = prepare_quickstart1(root)
            illumina_values = load_flat_yaml(illumina)
            ont_values = load_flat_yaml(ont)
            self.assertEqual(illumina_values["mode"], "illumina")
            self.assertEqual(ont_values["mode"], "ont")
            self.assertEqual(illumina_values["outdir"], str(root / "runs" / "illumina"))
            self.assertEqual(ont_values["outdir"], str(root / "runs" / "ont"))


if __name__ == "__main__":
    unittest.main()
