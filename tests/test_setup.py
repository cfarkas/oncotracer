"""First-use journeys: explicit commands must produce configurations run can read."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.cli import build_parser, main
from oncotracer_cli.engine import run_native
from oncotracer_cli.runtime import load_flat_yaml, render_flat_yaml
from tests.test_native_methylation import Fixture


class SetupTests(unittest.TestCase):
    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(list(args))
        return code, output.getvalue()

    def test_single_illumina_library_with_spaces_hash_and_quotes_in_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reads = base / "reads #1's.fastq"
            reads.write_text("@read\nACGT\n+\nIIII\n")
            project = base / "study #1's files"
            code, output = self.cli(
                "setup",
                "--non-interactive",
                "--project",
                str(project),
                "--mode",
                "illumina",
                "--sample-name",
                "tumor1",
                "--fastq-1",
                str(reads),
                "--threads",
                "3",
            )
            self.assertEqual(code, 0, output)
            config = project / "config/run.yml"
            self.assertIn("oncotracer check --config", output)
            self.assertIn("oncotracer run --backend conda --config", output)
            self.assertFalse((project / "results").exists())
            code, output = self.cli("check", "--config", str(config), "--json")
            self.assertEqual(code, 0, output)
            report = json.loads(output)
            self.assertEqual(report["plan"]["threads"], 3)
            self.assertEqual(report["plan"]["samples"], ["tumor1"])
            self.assertFalse((project / "reference").exists())
            self.assertEqual(load_flat_yaml(config)["outdir"], str(project / "results"))
            original = config.read_bytes()
            code, _ = self.cli(
                "setup",
                "--non-interactive",
                "--project",
                str(project),
                "--mode",
                "illumina",
            )
            self.assertEqual(code, 2)
            self.assertEqual(config.read_bytes(), original)

    def test_ont_setup_requires_explicit_barcodes_and_keeps_sample_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            project = fixture.root / "new-project"
            args = [
                "setup",
                "--non-interactive",
                "--project",
                str(project),
                "--mode",
                "ont",
                "--reads-folder",
                str(fixture.fastq.parent),
            ]
            code, output = self.cli(*args)
            self.assertEqual(code, 2, output)
            self.assertIn("Barcode folders", output)
            code, output = self.cli(
                *args, "--barcodes", "barcode01", "--sample-names", "sampleA"
            )
            self.assertEqual(code, 0, output)
            code, output = self.cli(
                "check", "--config", str(project / "config/run.yml"), "--json"
            )
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output)["plan"]["samples"], ["sampleA"])

    def test_methylation_setup_pins_local_assets_without_pod5_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), "marlin")
            bam = fixture.root / "completed.bam"
            bam.write_bytes(b"fixture-bam")
            resources = fixture.root / "local-resources.yml"
            resources.write_text(render_flat_yaml(fixture.config()))
            project = fixture.root / "new-project"
            code, output = self.cli(
                "setup",
                "--non-interactive",
                "--project",
                str(project),
                "--mode",
                "ont",
                "--analysis",
                "methylation",
                "--reads-folder",
                str(fixture.fastq.parent),
                "--barcodes",
                "barcode01",
                "--classifier",
                "marlin",
                "--modbam",
                str(bam),
                "--resources",
                str(resources),
                "--cpu",
            )
            self.assertEqual(code, 0, output)
            config = load_flat_yaml(project / "config/run.yml")
            self.assertTrue(config["methylation_only"])
            self.assertFalse(config["methylation_gpu"])
            self.assertNotIn("methylation_dorado_model", config)
            self.assertNotIn("methylation_pod5_dir", config)
            self.assertEqual(len(config["marlin_model_sha256"]), 64)
            code, output = self.cli(
                "check", "--config", str(project / "config/run.yml"), "--json"
            )
            self.assertEqual(code, 0, output)
            plan = json.loads(output)["plan"]
            self.assertIn("modbam-cpu-alignment", plan["stages"])
            self.assertNotIn("ont-alignment", plan["stages"])
            self.assertIsNone(plan["caller"])

    def test_check_reports_multiple_missing_resources_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "broken.yml"
            config.write_text(
                "mode: ont\nmethylation: true\nmethylation_classifier: marlin\n"
            )
            before = list(root.iterdir())
            code, output = self.cli("check", "--config", str(config), "--json")
            self.assertEqual(code, 2)
            report = json.loads(output)
            errors = "\n".join(report["errors"])
            for key in (
                "outdir",
                "marlin_model",
                "marlin_features",
                "marlin_probe_bed",
                "methylation_pod5_dir",
            ):
                self.assertIn(key, errors)
            self.assertEqual(before, list(root.iterdir()))

    def test_interactive_journey_uses_public_flags_and_same_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            answers = iter(
                [
                    "ont",
                    "cna",
                    str(fixture.root / "study"),
                    str(fixture.fastq.parent),
                    "barcode01",
                    "sample1",
                ]
            )
            with patch("builtins.input", side_effect=lambda prompt: next(answers)):
                code, output = self.cli("setup")
            self.assertEqual(code, 0, output)
            self.assertTrue((fixture.root / "study/config/run.yml").is_file())

    def test_cpu_flag_overrides_yaml_gpu_and_device_flags_are_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            config = fixture.config()
            config["methylation_gpu"] = True
            config_path = fixture.root / "run.yml"
            config_path.write_text(render_flat_yaml(config))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                run_native(config_path, root=ROOT, dry_run=True, methylation_gpu=False)
            self.assertEqual(
                json.loads(output.getvalue())["methylation"]["dorado_device"], "cpu"
            )
            args = build_parser().parse_args(
                ["run", "--config", str(config_path), "--cpu"]
            )
            self.assertFalse(args.gpu)
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                build_parser().parse_args(
                    ["run", "--config", str(config_path), "--cpu", "--gpu"]
                )

    def test_yaml_roundtrip_preserves_quoted_scalars_and_comments(self):
        values = {
            "a": "true",
            "b": "001",
            "c": "file # sample's data",
            "d": 'a "quoted" path',
            "e": "two\nlines",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paths.yml"
            path.write_text(
                render_flat_yaml(values) + "other: '/work/it''s #1' # comment\n"
            )
            self.assertEqual(load_flat_yaml(path), {**values, "other": "/work/it's #1"})

    def test_conflicting_setup_flags_are_not_silently_ignored(self):
        cases = [
            (["--mode", "ont", "--samplesheet", "/data/samples.csv"], "does not apply"),
            (
                [
                    "--mode",
                    "illumina",
                    "--samplesheet",
                    "/data/samples.csv",
                    "--fastq-1",
                    "/data/reads.fastq",
                ],
                "choose --samplesheet",
            ),
            (
                ["--mode", "ont", "--analysis", "cna", "--classifier", "marlin"],
                "methylation flags need",
            ),
        ]
        for flags, expected in cases:
            with self.subTest(flags=flags):
                code, output = self.cli("setup", "--non-interactive", *flags)
                self.assertEqual(code, 2, output)
                self.assertIn(expected, output)
                self.assertNotIn("Traceback", output)

    @unittest.skipUnless(
        os.environ.get("ONCOTRACER_TEST_EXECUTABLE"), "installed launcher not selected"
    )
    def test_installed_setup_and_check_from_outside_checkout(self):
        executable = str(Path(os.environ["ONCOTRACER_TEST_EXECUTABLE"]).resolve())
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fastq = base / "reads #1's.fastq"
            fastq.write_text("@read1\nACGT\n+\nIIII\n")
            project = base / "study with spaces"
            environment = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {"PYTHONPATH", "ONCOTRACER_ROOT", "ONCOTRACER_PAYLOAD_CACHE"}
            }
            setup = subprocess.run(
                [
                    executable,
                    "setup",
                    "--non-interactive",
                    "--project",
                    str(project),
                    "--reference-root",
                    str(base / "shared reference"),
                    "--mode",
                    "illumina",
                    "--sample-name",
                    "sample1",
                    "--fastq-1",
                    str(fastq),
                ],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            check = subprocess.run(
                [
                    executable,
                    "check",
                    "--config",
                    str(project / "config/run.yml"),
                    "--json",
                ],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(json.loads(check.stdout)["plan"]["samples"], ["sample1"])
            self.assertEqual(
                load_flat_yaml(project / "config/run.yml")["lpwgs_root"],
                str(base / "shared reference"),
            )
            self.assertFalse((base / "shared reference").exists())
            self.assertFalse((project / "results").exists())
            self.assertFalse((project / "reference").exists())


if __name__ == "__main__":
    unittest.main()
