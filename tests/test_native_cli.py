#!/usr/bin/env python3
from __future__ import annotations

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

from oncotracer_cli import __version__  # noqa: E402
from oncotracer_cli.cli import (  # noqa: E402
    _conda_prefixes,
    _check_process,
    _configured_native_prefixes,
    _probe_native_prefixes,
    _run,
    command_doctor,
    _legacy_to_modern,
    build_parser,
    prepare_quickstart1,
)
from oncotracer_cli.runtime import (  # noqa: E402
    atomic_write_workflow_summary,
    load_flat_yaml,
    render_flat_yaml,
    render_key_value_summary,
)


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

    def test_key_value_summary_uses_lowercase_booleans(self) -> None:
        rendered = render_key_value_summary(
            {"engine": "native", "nextflow_used": False, "complete": True}
        )
        self.assertEqual(
            rendered,
            "engine=native\nnextflow_used=false\ncomplete=true\n",
        )

    def test_workflow_summary_preserves_typed_json_and_lowercase_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary_dir = Path(directory) / "06_workflow_summary"
            atomic_write_workflow_summary(
                summary_dir,
                {"engine": "native", "nextflow_used": False, "complete": True},
            )
            parsed = json.loads(
                (summary_dir / "workflow_summary.json").read_text(encoding="utf-8")
            )
            self.assertIs(parsed["nextflow_used"], False)
            self.assertIs(parsed["complete"], True)
            self.assertEqual(
                (summary_dir / "workflow_summary.txt").read_text(encoding="utf-8"),
                "engine=native\nnextflow_used=false\ncomplete=true\n",
            )

    def test_conda_prefixes_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefixes = _conda_prefixes(Path(directory))
            self.assertEqual(set(prefixes), {"core", "qdnaseq", "ichorcna", "classifier", "gistic"})
            self.assertEqual(len(set(prefixes.values())), 5)

    def test_configured_gistic_prefix_uses_the_official_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory).resolve()
            with patch.dict(os.environ, {"ONCOTRACER_GISTIC_PREFIX": ""}):
                prefixes = _configured_native_prefixes({"gistic_prefix": str(expected)})
            self.assertEqual(prefixes["gistic"], expected)

    def test_check_process_requires_semantic_output_for_nonzero_help(self) -> None:
        result = _check_process(
            ["/bin/false"],
            accepted_returncodes=frozenset({1}),
            required_output=r"Picard|USAGE|CommandLineProgram",
        )
        self.assertFalse(result["success"])
        self.assertFalse(result["output_matched"])

        gistic = _check_process(
            ["/bin/false"],
            accepted_returncodes=frozenset({1}),
            required_output=r"GISTIC|usage|MATLAB",
        )
        self.assertFalse(gistic["success"])

    def test_readcounter_rc255_requires_exact_semantic_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "readCounter"
            script.write_text(
                "#!/bin/sh\nprintf 'Please specify a BAM file.\\nUsage: readCounter [options] <BAM file>\\n'\nexit 255\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            expected = _check_process(
                [script],
                accepted_returncodes=frozenset({255}),
                required_output=r"Please specify a BAM file\.\s*Usage:",
            )
            self.assertTrue(expected["success"])
            script.write_text("#!/bin/sh\nprintf 'loader failure\\n'\nexit 255\n", encoding="utf-8")
            unexpected = _check_process(
                [script],
                accepted_returncodes=frozenset({255}),
                required_output=r"Please specify a BAM file\.\s*Usage:",
            )
            self.assertFalse(unexpected["success"])

    def test_run_redirects_child_output_away_from_json_stdout(self) -> None:
        stream = io.StringIO()
        completed = subprocess.CompletedProcess([], 0)
        with patch("sys.stderr", stream), patch(
            "oncotracer_cli.cli.subprocess.run", return_value=completed
        ) as run:
            _run(["example", "--probe"])
        self.assertIs(run.call_args.kwargs["stdout"], stream)
        self.assertIs(run.call_args.kwargs["stderr"], stream)

    def test_prefix_probes_ignore_foreign_path_and_clean_r_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes = {
                name: root / name
                for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
            }
            names = {
                "core": ("bwa", "samtools", "minimap2", "pigz", "picard"),
                "qdnaseq": ("Rscript",),
                "ichorcna": ("Rscript", "readCounter"),
                "classifier": ("python",),
                "gistic": ("gistic2",),
            }
            for group, executables in names.items():
                for name in executables:
                    path = prefixes[group] / "bin" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("#!/bin/sh\n", encoding="utf-8")
                    path.chmod(0o755)
            mcr = prefixes["gistic"] / "share" / "mcr-8.3-0" / "v83"
            mcr_libraries = [
                mcr / "runtime" / "glnxa64",
                mcr / "bin" / "glnxa64",
                mcr / "sys" / "os" / "glnxa64",
            ]
            for path in mcr_libraries:
                path.mkdir(parents=True)

            def successful_probe(command, **_kwargs):
                return {"command": " ".join(map(str, command)), "success": True}

            foreign = root / "foreign" / "bin"
            foreign.mkdir(parents=True)
            contaminated = {
                "PATH": str(foreign),
                "R_HOME": "/foreign/R",
                "R_LIBS": "/foreign/libs",
                "R_LIBS_USER": "/foreign/user",
                "R_LIBS_SITE": "/foreign/site",
                "CUDA_VISIBLE_DEVICES": "7",
            }
            with patch.dict(os.environ, contaminated), patch(
                "oncotracer_cli.cli._check_process", side_effect=successful_probe
            ) as process:
                results = _probe_native_prefixes(prefixes)

            self.assertTrue(all(result["success"] for result in results.values()))
            calls = process.call_args_list
            invoked = [Path(call.args[0][0]) for call in calls]
            self.assertNotIn(foreign / "Rscript", invoked)
            for group, executables in names.items():
                for name in executables:
                    self.assertIn(prefixes[group] / "bin" / name, invoked)
            qdna_call = next(
                call for call in calls if Path(call.args[0][0]) == prefixes["qdnaseq"] / "bin" / "Rscript"
            )
            for variable in ("R_HOME", "R_LIBS", "R_LIBS_USER", "R_LIBS_SITE"):
                self.assertNotIn(variable, qdna_call.kwargs["env"])
            classifier_call = next(
                call for call in calls if Path(call.args[0][0]) == prefixes["classifier"] / "bin" / "python"
            )
            self.assertEqual(classifier_call.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(classifier_call.kwargs["env"]["NVIDIA_VISIBLE_DEVICES"], "void")
            bwa_call = next(
                call for call in calls if Path(call.args[0][0]) == prefixes["core"] / "bin" / "bwa"
            )
            picard_call = next(
                call for call in calls if Path(call.args[0][0]) == prefixes["core"] / "bin" / "picard"
            )
            self.assertEqual(bwa_call.kwargs["accepted_returncodes"], frozenset({1}))
            self.assertEqual(picard_call.kwargs["accepted_returncodes"], frozenset({1}))
            gistic_call = next(
                call for call in calls if Path(call.args[0][0]) == prefixes["gistic"] / "bin" / "gistic2"
            )
            self.assertEqual(
                gistic_call.kwargs["env"]["LD_LIBRARY_PATH"],
                os.pathsep.join(str(path) for path in mcr_libraries),
            )

    def test_doctor_rejects_dirty_source_provenance(self) -> None:
        provenance = {
            "source_commit": "a" * 40,
            "source_sha256": "b" * 64,
            "source_sha256_definition": "git archive tar",
            "source_metadata_origin": "checkout",
            "source_tree_dirty": True,
        }
        args = build_parser().parse_args(["doctor", "--backend", "host"])
        output = io.StringIO()
        core = {"success": True, "probes": {}}
        with patch("oncotracer_cli.cli._load_install_config", return_value={}), patch(
            "oncotracer_cli.cli._probe_core", return_value=core
        ), patch("oncotracer_cli.cli.get_provenance", return_value=provenance), patch(
            "sys.stdout", output
        ):
            returncode = command_doctor(args)
        result = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(result["source"]["success"])
        self.assertFalse(result["success"])

    def test_doctor_folds_missing_prefixes_into_nonzero_exit(self) -> None:
        prefixes = {
            name: None
            for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
        }
        provenance = {
            "source_commit": "a" * 40,
            "source_sha256": "b" * 64,
            "source_sha256_definition": "git archive tar",
            "source_metadata_origin": "embedded",
            "source_tree_dirty": False,
        }
        args = build_parser().parse_args(["doctor", "--backend", "conda"])
        output = io.StringIO()
        with patch("oncotracer_cli.cli._load_install_config", return_value={}), patch(
            "oncotracer_cli.cli._configured_native_prefixes", return_value=prefixes
        ), patch("oncotracer_cli.cli.get_provenance", return_value=provenance), patch(
            "sys.stdout", output
        ):
            returncode = command_doctor(args)
        result = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(result["success"])
        self.assertTrue(result["source"]["success"])
        self.assertEqual(result["source"]["source_commit"], "a" * 40)
        self.assertTrue(all(not item["configured"] for item in result["prefixes"].values()))

    def test_doctor_folds_any_failed_environment_into_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes = {
                name: root / name
                for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
            }
            for prefix in prefixes.values():
                prefix.mkdir()
            environments = {
                name: {"success": name != "gistic", "probes": {}}
                for name in prefixes
            }
            provenance = {
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
                "source_sha256_definition": "git archive tar",
                "source_metadata_origin": "embedded",
                "source_tree_dirty": False,
            }
            args = build_parser().parse_args(["doctor", "--backend", "conda"])
            output = io.StringIO()
            with patch("oncotracer_cli.cli._load_install_config", return_value={}), patch(
                "oncotracer_cli.cli._configured_native_prefixes", return_value=prefixes
            ), patch(
                "oncotracer_cli.cli._probe_native_prefixes", return_value=environments
            ), patch("oncotracer_cli.cli.get_provenance", return_value=provenance), patch(
                "sys.stdout", output
            ):
                returncode = command_doctor(args)
            result = json.loads(output.getvalue())
            self.assertEqual(returncode, 1)
            self.assertFalse(result["success"])
            self.assertFalse(result["environments"]["gistic"]["success"])

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
