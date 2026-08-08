from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oncotracer_cli import cli
from oncotracer_cli.engine import run_native
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml

ROOT = Path(__file__).resolve().parents[1]


class BeginnerRuntimeTests(unittest.TestCase):
    def test_quickstart_dry_runs_are_side_effect_free(self) -> None:
        for number in ("1", "2"):
            with self.subTest(number=number), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / f"quickstart-{number}"
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = cli.main(
                        [
                            "quickstart",
                            number,
                            "--test-root",
                            str(destination),
                            "--backend",
                            "conda",
                            "--dry-run",
                            "--root",
                            str(ROOT),
                        ]
                    )
                self.assertEqual(status, 0)
                self.assertFalse(destination.exists())
                self.assertIn("dry-run completed without writing files", stdout.getvalue())

    def test_install_dry_runs_need_no_backend_and_write_nothing(self) -> None:
        for backend in ("conda", "docker", "singularity", "poetry"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                environment = {
                    "XDG_CONFIG_HOME": str(base / "config"),
                    "XDG_DATA_HOME": str(base / "data"),
                }
                arguments = [
                    "install",
                    f"--{backend}",
                    "--dry-run",
                    "--root",
                    str(ROOT),
                ]
                if backend == "conda":
                    arguments.extend(["--prefix", str(base / "envs")])
                with mock.patch.dict(os.environ, environment, clear=False):
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        status = cli.main(arguments)
                self.assertEqual(status, 0)
                self.assertFalse((base / "config").exists())
                self.assertFalse((base / "data").exists())
                self.assertFalse((base / "envs").exists())

    def test_quickstart1_generated_configs_resume_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "qs1"

            def fake_download(_url: str, destination: Path, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"reads")
                return destination

            with mock.patch.object(cli, "download", side_effect=fake_download):
                illumina, ont = cli.prepare_quickstart1(root)
            self.assertIn("force: false", illumina.read_text(encoding="utf-8"))
            self.assertIn("force: false", ont.read_text(encoding="utf-8"))

    def test_native_run_dry_run_validates_without_outputs_or_reference_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fastq_1 = base / "TUMOR_R1.fastq.gz"
            fastq_2 = base / "TUMOR_R2.fastq.gz"
            fastq_1.write_bytes(b"reads-1")
            fastq_2.write_bytes(b"reads-2")
            sheet = base / "samples.csv"
            sheet.write_text(
                "sample,fastq_1,fastq_2,status\n"
                f"TUMOR,{fastq_1},{fastq_2},tumor\n",
                encoding="utf-8",
            )
            outdir = base / "results"
            lpwgs_root = base / "project"
            config = base / "run.yml"
            config.write_text(
                "mode: illumina\n"
                f"lpwgs_root: {lpwgs_root}\n"
                f"outdir: {outdir}\n"
                f"illumina_samplesheet: {sheet}\n"
                "illumina_binsize_kb: 100\n"
                "force: false\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                observed = run_native(config, root=ROOT, dry_run=True)
            self.assertEqual(observed, outdir.resolve())
            self.assertFalse(outdir.exists())
            self.assertFalse(lpwgs_root.exists())
            self.assertIn('"schema": "oncotracer-native-dry-run-v1"', output.getvalue())
            self.assertIn('"nextflow_used": false', output.getvalue())

    def test_duplicate_yaml_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "duplicate.yml"
            config.write_text("mode: illumina\nmode: ont\n", encoding="utf-8")
            with self.assertRaisesRegex(OncoTracerError, "duplicate YAML key"):
                load_flat_yaml(config)


if __name__ == "__main__":
    unittest.main()
