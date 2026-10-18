#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import __version__  # noqa: E402
from oncotracer_cli.engine import Toolchain  # noqa: E402
from oncotracer_cli.cli import (  # noqa: E402
    _conda_prefixes,
    _check_process,
    _configured_native_prefixes,
    _probe_native_prefixes,
    _run,
    _run_docker,
    command_doctor,
    execute_run,
    _legacy_to_modern,
    build_parser,
    prepare_quickstart1,
)
from oncotracer_cli.runtime import (  # noqa: E402
    OncoTracerError,
    atomic_write_workflow_summary,
    load_flat_yaml,
    render_flat_yaml,
    render_key_value_summary,
)


class NativeCliTests(unittest.TestCase):
    def test_version_is_v2(self) -> None:
        self.assertEqual(__version__, "2.1.0")

    def test_parser_exposes_native_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--config", "x.yml", "--backend", "conda"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.backend, "conda")

    def test_docker_install_local_image_keeps_health_check(self) -> None:
        from oncotracer_cli import cli
        args = build_parser().parse_args([
            "install", "--docker", "--image", "oncotracer:local", "--no-pull"
        ])
        with patch.object(cli.shutil, "which", return_value="/bin/docker"), \
             patch.object(cli, "_run") as run, \
             patch.object(cli, "_save_install_config") as save:
            cli._install_docker(args)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [
            ["/bin/docker", "info"],
            ["/bin/docker", "image", "inspect", "oncotracer:local"],
            ["/bin/docker", "run", "--rm", "oncotracer:local", "doctor", "--backend", "host"],
        ])
        save.assert_called_once_with({"backend": "docker", "image": "oncotracer:local"})

    def test_docker_install_default_still_pulls(self) -> None:
        from oncotracer_cli import cli
        for flags, image in [
            ([], "carlosfarkas/oncotracer:run-audit-20261018"),
            (["--image", "registry/image:tag"], "registry/image:tag"),
        ]:
            with self.subTest(image=image):
                args = build_parser().parse_args(["install", "--docker", *flags])
                with patch.object(cli.shutil, "which", return_value="/bin/docker"), \
                     patch.object(cli, "_run") as run, \
                     patch.object(cli, "_save_install_config") as save:
                    cli._install_docker(args)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn(["/bin/docker", "pull", image], commands)
                self.assertIn(["/bin/docker", "run", "--rm", image, "doctor", "--backend", "host"], commands)
                save.assert_called_once_with({"backend": "docker", "image": image})

    def test_docker_install_missing_or_unhealthy_local_image_does_not_save(self) -> None:
        from oncotracer_cli import cli
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                args = build_parser().parse_args(["install", "--docker", "--image", "oncotracer:local", "--no-pull"])
                effects = [None] * fail_at + [OncoTracerError("image unavailable or unhealthy")]
                with patch.object(cli.shutil, "which", return_value="/bin/docker"), \
                     patch.object(cli, "_run", side_effect=effects), \
                     patch.object(cli, "_save_install_config") as save:
                    with self.assertRaises(OncoTracerError):
                        cli._install_docker(args)
                save.assert_not_called()

    def test_no_pull_is_rejected_for_other_install_backends(self) -> None:
        from oncotracer_cli import cli
        for backend in ("conda", "poetry", "singularity"):
            with self.subTest(backend=backend):
                args = build_parser().parse_args(["install", "--" + backend, "--no-pull", "--dry-run"])
                with self.assertRaisesRegex(OncoTracerError, "backend-irrelevant.*--no-pull"):
                    cli.command_install(args)

    def test_native_docker_uses_invoking_uid_gid(self) -> None:
        config = Path("/data/study/config/run.yml")
        args = build_parser().parse_args(["run", "--config", str(config), "--backend", "docker", "--dry-run"])
        with (
            patch("oncotracer_cli.cli.os.getuid", return_value=4321),
            patch("oncotracer_cli.cli.os.getgid", return_value=8765),
            patch("oncotracer_cli.cli._load_install_config", return_value={}),
            patch("oncotracer_cli.cli._project_mounts", return_value=[config.parent]),
            patch("oncotracer_cli.cli.load_flat_yaml", return_value={}),
            patch("oncotracer_cli.cli._run") as execute,
        ):
            _run_docker(config, args)
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index("--user") + 1], "4321:8765")
        self.assertEqual(command[command.index("--volume") + 1], f"{config.parent}:{config.parent}")
        self.assertIn("internal-run", command)
        self.assertEqual(execute.call_args.kwargs, {"dry_run": True})

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
            self.assertEqual(
                set(prefixes), {"core", "qdnaseq", "ichorcna", "classifier", "gistic", "variants"}
            )
            self.assertEqual(len(set(prefixes.values())), 6)
            base = Path(directory).resolve()
            self.assertEqual(prefixes["variants"], base.parent / (base.name + "-variants") / "variants")
            self.assertTrue(all(prefix.parent == base for name, prefix in prefixes.items() if name != "variants"))

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
            script.write_text(
                "#!/bin/sh\nprintf 'loader failure\\n'\nexit 255\n", encoding="utf-8"
            )
            unexpected = _check_process(
                [script],
                accepted_returncodes=frozenset({255}),
                required_output=r"Please specify a BAM file\.\s*Usage:",
            )
            self.assertFalse(unexpected["success"])

    def test_run_redirects_child_output_away_from_json_stdout(self) -> None:
        stream = io.StringIO()
        completed = subprocess.CompletedProcess([], 0)
        with (
            patch("sys.stderr", stream),
            patch("oncotracer_cli.cli.subprocess.run", return_value=completed) as run,
        ):
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
            prefixes["variants"] = root / "managed-variants" / "variants"
            prefixes["variants"].mkdir(parents=True)
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
            runtime_cache = root / "runtime-cache"
            runtime_cache.mkdir(mode=0o700)
            toolchain = Toolchain(
                core_prefix=prefixes["core"],
                qdnaseq_prefix=prefixes["qdnaseq"],
                ichorcna_prefix=prefixes["ichorcna"],
                classifier_prefix=prefixes["classifier"],
                gistic_prefix=prefixes["gistic"],
                runtime_cache=runtime_cache,
            )
            with (
                patch.dict(os.environ, contaminated),
                patch(
                    "oncotracer_cli.cli._check_process", side_effect=successful_probe
                ) as process,
                patch(
                    "oncotracer_cli.variant_install_checks.verify_variant_environment",
                    return_value={"success": True, "probes": {}},
                ) as variants_probe,
            ):
                results = _probe_native_prefixes(prefixes, toolchain)

            self.assertTrue(all(result["success"] for result in results.values()))
            variants_probe.assert_called_once_with(prefixes["variants"])
            calls = process.call_args_list
            invoked = [Path(call.args[0][0]) for call in calls]
            self.assertNotIn(foreign / "Rscript", invoked)
            for group, executables in names.items():
                for name in executables:
                    self.assertIn(prefixes[group] / "bin" / name, invoked)
            qdna_call = next(
                call
                for call in calls
                if Path(call.args[0][0]) == prefixes["qdnaseq"] / "bin" / "Rscript"
            )
            for variable in ("R_HOME", "R_LIBS", "R_LIBS_USER", "R_LIBS_SITE"):
                self.assertNotIn(variable, qdna_call.kwargs["env"])
            classifier_call = next(
                call
                for call in calls
                if Path(call.args[0][0]) == prefixes["classifier"] / "bin" / "python"
            )
            self.assertEqual(classifier_call.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(
                classifier_call.kwargs["env"]["NVIDIA_VISIBLE_DEVICES"], "void"
            )
            bwa_call = next(
                call
                for call in calls
                if Path(call.args[0][0]) == prefixes["core"] / "bin" / "bwa"
            )
            picard_call = next(
                call
                for call in calls
                if Path(call.args[0][0]) == prefixes["core"] / "bin" / "picard"
            )
            self.assertEqual(bwa_call.kwargs["accepted_returncodes"], frozenset({1}))
            self.assertEqual(picard_call.kwargs["accepted_returncodes"], frozenset({1}))
            gistic_call = next(
                call
                for call in calls
                if Path(call.args[0][0]) == prefixes["gistic"] / "bin" / "gistic2"
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
        args = build_parser().parse_args(["doctor", "--json", "--backend", "host"])
        output = io.StringIO()
        core = {"success": True, "probes": {}}
        with (
            patch("oncotracer_cli.cli._load_install_config", return_value={}),
            patch("oncotracer_cli.cli._probe_core", return_value=core),
            patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
            patch("sys.stdout", output),
        ):
            returncode = command_doctor(args)
        result = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(result["source"]["success"])
        self.assertFalse(result["success"])

    def test_doctor_folds_missing_prefixes_into_nonzero_exit(self) -> None:
        prefixes = {
            name: None
            for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic", "variants")
        }
        provenance = {
            "source_commit": "a" * 40,
            "source_sha256": "b" * 64,
            "source_sha256_definition": "git archive tar",
            "source_metadata_origin": "embedded",
            "source_tree_dirty": False,
        }
        args = build_parser().parse_args(["doctor", "--json", "--backend", "conda"])
        output = io.StringIO()
        with (
            patch("oncotracer_cli.cli._load_install_config", return_value={}),
            patch(
                "oncotracer_cli.cli._configured_native_prefixes", return_value=prefixes
            ),
            patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
            patch("sys.stdout", output),
        ):
            returncode = command_doctor(args)
        result = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(result["success"])
        self.assertTrue(result["source"]["success"])
        self.assertEqual(result["source"]["source_commit"], "a" * 40)
        self.assertTrue(
            all(not item["configured"] for item in result["prefixes"].values())
        )

    def test_doctor_folds_any_failed_environment_into_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes = {
                name: root / name
                for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic", "variants")
            }
            for prefix in prefixes.values():
                prefix.mkdir()
            install = {f"{name}_prefix": str(prefix) for name, prefix in prefixes.items()}
            provenance = {
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
                "source_sha256_definition": "git archive tar",
                "source_metadata_origin": "embedded",
                "source_tree_dirty": False,
            }
            args = build_parser().parse_args(["doctor", "--json", "--backend", "host"])
            for failed_group in ("gistic", "variants"):
                with self.subTest(failed_group=failed_group):
                    environments = {
                        name: {"success": name != failed_group, "probes": {}} for name in prefixes
                    }
                    output = io.StringIO()
                    with (
                        patch("oncotracer_cli.cli._load_install_config", return_value=install),
                        patch(
                            "oncotracer_cli.cli._probe_native_prefixes",
                            return_value=environments,
                        ) as probes,
                        patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
                        patch("sys.stdout", output),
                    ):
                        returncode = command_doctor(args)
                    result = json.loads(output.getvalue())
                    probes.assert_called_once()
                    self.assertEqual(returncode, 1)
                    self.assertFalse(result["success"])
                    self.assertTrue(result["managed_install"]["success"])
                    self.assertFalse(result["environments"][failed_group]["success"])
                    self.assertTrue(all(item["success"] for name, item in result["environments"].items() if name != failed_group))

    def test_poetry_doctor_requires_strict_managed_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "managed"
            core_prefixes = {
                name: base / name
                for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
            }
            variants_prefix = base.parent / "managed-variants" / "variants"
            poetry_prefix = base.parent / "managed-poetry" / "poetry-runtime"
            prefixes = {**core_prefixes, "variants": variants_prefix}
            for prefix in (*prefixes.values(), poetry_prefix):
                prefix.mkdir(parents=True, exist_ok=True)
            install = {
                **{f"{name}_prefix": str(prefix) for name, prefix in prefixes.items()},
                "poetry_prefix": str(poetry_prefix),
            }
            provenance = {
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
                "source_sha256_definition": "git archive tar",
                "source_metadata_origin": "embedded",
                "source_tree_dirty": False,
            }
            environments = {name: {"success": True, "probes": {}} for name in prefixes}
            args = build_parser().parse_args(["doctor", "--json", "--backend", "poetry"])
            output = io.StringIO()
            active = {"core": False, "variants": False}
            core_lock, variants_lock = MagicMock(), MagicMock()

            def enter_core(*_args):
                active["core"] = True
                return {**core_prefixes, "poetry-runtime": poetry_prefix}

            def enter_variants(*_args):
                active["variants"] = True
                return variants_prefix

            def exit_core(*_args):
                active["core"] = False
                return False

            def exit_variants(*_args):
                active["variants"] = False
                return False

            def contained_probes(*_args):
                self.assertTrue(all(active.values()))
                return environments

            core_lock.__enter__.side_effect = enter_core
            core_lock.__exit__.side_effect = exit_core
            variants_lock.__enter__.side_effect = enter_variants
            variants_lock.__exit__.side_effect = exit_variants
            with (
                patch("oncotracer_cli.cli._load_install_config", return_value=install),
                patch(
                    "oncotracer_cli.cli._probe_native_prefixes",
                    side_effect=contained_probes,
                ) as probes,
                patch(
                    "oncotracer_cli.cli.managed_conda_runtime_lock",
                    return_value=core_lock,
                ) as strict,
                patch(
                    "oncotracer_cli.cli.managed_variants_runtime_lock",
                    return_value=variants_lock,
                ) as strict_variants,
                patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
                patch("sys.stdout", output),
            ):
                returncode = command_doctor(args)
            record = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertTrue(record["managed_install"]["success"])
            self.assertTrue(record["success"])
            probes.assert_called_once()
            strict.assert_called_once_with(base, require_poetry=True, semantic=False)
            strict_variants.assert_called_once_with(variants_prefix, semantic=False)
            for lock in (core_lock, variants_lock):
                lock.__enter__.assert_called_once()
                lock.__exit__.assert_called_once()
            self.assertFalse(any(active.values()))

    def test_host_doctor_reports_containment_error_as_json_failure(self) -> None:
        provenance = {
            "source_commit": "a" * 40,
            "source_sha256": "b" * 64,
            "source_sha256_definition": "git archive tar",
            "source_metadata_origin": "embedded",
            "source_tree_dirty": False,
        }
        args = build_parser().parse_args(["doctor", "--json", "--backend", "host"])
        output = io.StringIO()
        with (
            patch("oncotracer_cli.cli._load_install_config", return_value={}),
            patch(
                "oncotracer_cli.cli._probe_core",
                side_effect=OncoTracerError("unsafe Fontconfig containment"),
            ),
            patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
            patch("sys.stdout", output),
        ):
            returncode = command_doctor(args)
        record = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(record["success"])
        self.assertFalse(record["environments"]["core"]["success"])
        self.assertIn(
            "unsafe Fontconfig containment",
            record["environments"]["core"]["error"],
        )

    def test_singularity_doctor_authenticates_pair_and_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sif = Path(directory) / "oncotracer.sif"
            sif.write_bytes(b"test SIF")
            install = {
                "sif": str(sif),
                "singularity_command": "/usr/bin/apptainer",
            }
            provenance = {
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
                "source_sha256_definition": "git archive tar",
                "source_metadata_origin": "embedded",
                "source_tree_dirty": False,
            }
            args = build_parser().parse_args(["doctor", "--json", "--backend", "singularity"])
            output = io.StringIO()
            marker = {"sif_sha256": "c" * 64}
            with (
                patch("oncotracer_cli.cli._load_install_config", return_value=install),
                patch(
                    "oncotracer_cli.cli.verify_managed_sif_runtime",
                    return_value=marker,
                ) as strict,
                patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
                patch("sys.stdout", output),
            ):
                returncode = command_doctor(args)
            record = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertTrue(record["managed_sif"]["success"])
            self.assertTrue(record["success"])
            strict.assert_called_once_with(
                sif.resolve(), executable="/usr/bin/apptainer"
            )

    @patch("oncotracer_cli.cli._probe_core", return_value={"success": True})
    def test_conda_analysis_holds_managed_consumer_lock(self, _core) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "analysis.yml"
            config_path.write_text("mode: illumina\n", encoding="utf-8")
            base = root / "managed"
            install = {
                **{
                    f"{name}_prefix": str(base / name)
                    for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
                },
                "variants_prefix": str(base.parent / "managed-variants" / "variants"),
                "backend": "conda",
            }
            arguments = SimpleNamespace(
                backend="conda",
                root=None,
                threads=None,
                force=False,
                dry_run=False,
                methylation=None,
                methylation_classifier=None,
                pod5_dir=None,
                gpu=False,
            )
            lock = MagicMock()
            lock.__enter__.return_value = {}
            lock.__exit__.return_value = False
            with (
                patch("oncotracer_cli.cli._load_install_config", return_value=install),
                patch(
                    "oncotracer_cli.cli.managed_conda_runtime_lock",
                    return_value=lock,
                ) as managed_lock,
                patch(
                    "oncotracer_cli.cli.run_native", return_value=root / "results"
                ) as native,
            ):
                result = execute_run(config_path, arguments)
            self.assertEqual(result, root / "results")
            self.assertEqual(managed_lock.call_count, 2)
            self.assertEqual([call.kwargs["semantic"] for call in managed_lock.call_args_list], [True, False])
            managed_lock.assert_called_with(base, require_poetry=False, semantic=False)
            self.assertEqual(lock.__enter__.call_count, 2)
            self.assertEqual(lock.__exit__.call_count, 2)
            native.assert_called_once()

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

    def test_host_doctor_accepts_semantic_external_container_prefix_matrix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes = {
                "core": root / "opt" / "conda",
                **{
                    name: root / "opt" / "oncotracer-envs" / name
                    for name in ("qdnaseq", "ichorcna", "classifier", "gistic", "variants")
                },
            }
            for prefix in prefixes.values():
                prefix.mkdir(parents=True)
            variables = {
                f"ONCOTRACER_{name.upper()}_PREFIX": str(prefix)
                for name, prefix in prefixes.items()
            }
            provenance = {
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
                "source_sha256_definition": "git archive tar",
                "source_metadata_origin": "embedded",
                "source_tree_dirty": False,
            }
            environments = {name: {"success": True, "probes": {}} for name in prefixes}
            args = build_parser().parse_args(["doctor", "--json", "--backend", "host"])
            output = io.StringIO()
            with (
                patch.dict(os.environ, variables, clear=False),
                patch("oncotracer_cli.cli._load_install_config", return_value={}),
                patch(
                    "oncotracer_cli.cli._probe_native_prefixes",
                    return_value=environments,
                ),
                patch(
                    "oncotracer_cli.cli._managed_conda_base",
                    side_effect=AssertionError(
                        "external prefixes are not installer-owned"
                    ),
                ),
                patch("oncotracer_cli.cli.get_provenance", return_value=provenance),
                patch("sys.stdout", output),
            ):
                returncode = command_doctor(args)
            record = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertTrue(record["success"])
            self.assertEqual(record["managed_install"]["provisioning"], "external")
            self.assertFalse(record["managed_install"]["required"])

    @patch("oncotracer_cli.cli._probe_core", return_value={"success": True})
    def test_host_external_prefixes_do_not_claim_installer_consumer_lock(self, _core) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "analysis.yml"
            config_path.write_text("mode: illumina\n", encoding="utf-8")
            arguments = SimpleNamespace(
                backend="host",
                root=None,
                threads=None,
                force=False,
                dry_run=False,
                methylation=None,
                methylation_classifier=None,
                pod5_dir=None,
                gpu=False,
            )
            with (
                patch("oncotracer_cli.cli._load_install_config", return_value={}),
                patch(
                    "oncotracer_cli.cli.managed_conda_runtime_lock",
                    side_effect=AssertionError(
                        "external layout must not use managed lock"
                    ),
                ),
                patch(
                    "oncotracer_cli.cli.run_native", return_value=root / "results"
                ) as native,
            ):
                result = execute_run(config_path, arguments)
            self.assertEqual(result, root / "results")
            native.assert_called_once()


class SingularityIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "host home"
        self.project = self.home / "project"
        self.external = self.home / "outside project's reads"
        self.project.mkdir(parents=True)
        self.external.mkdir()
        self.fastq = self.external / "R1.fastq"
        self.fastq.write_text("@read\nACGT\n+\nIIII\n")
        self.lane = self.external / "R1 lane2.fastq"
        self.lane.write_text(self.fastq.read_text())
        self.link = self.project / "R1.fastq"
        self.link.symlink_to(self.fastq)
        self.sheet = self.project / "samples.csv"
        self.config = self.project / "run.yml"
        self.outdir = self.project / "results"
        self.host_settings = self.home / ".config/oncotracer/config.json"
        self.host_settings.parent.mkdir(parents=True)
        self.host_settings.write_text(json.dumps({"backend": "conda", "core_prefix": "/host/poison"}))
        self.values = dict(mode="illumina", lpwgs_root=str(self.project),
                           outdir=str(self.outdir), illumina_samplesheet=str(self.sheet))
        self.sheet.write_text(f"sample,fastq_1,fastq_2,status\nMOCK,{self.link},,tumor\n")
        self.config.write_text(render_flat_yaml(self.values))

    @staticmethod
    def command_environment(command):
        values = dict(str(command[index + 1]).split("=", 1)
                      for index, value in enumerate(command) if value == "--env")
        values["HOME"] = str(command[command.index("--home") + 1]).split(":")[1]
        return values

    def test_private_settings_survive_home_data_bind_and_cleanup(self):
        from oncotracer_cli.sif_runtime import isolated_sif_command
        original = self.host_settings.read_bytes()
        with patch.dict(os.environ, {"HOME": str(self.home), "DISPLAY": ":999",
                                     "XDG_CONFIG_HOME": str(self.home / ".config")}):
            with isolated_sif_command("apptainer", mounts=[(self.home, "rw")], cwd=self.home) as command:
                self.assertIn("--cleanenv", command)
                self.assertIn("--no-home", command)
                settings = self.command_environment(command)
                self.assertNotEqual(settings["HOME"], str(self.home))
                self.assertNotEqual(settings["XDG_CONFIG_HOME"], str(self.home / ".config"))
                self.assertEqual(command[command.index("--pwd") + 1], self.home)
                binds = [str(command[i + 1]) for i, part in enumerate(command) if part == "--bind"]
                private = Path(binds[-1].split(":")[0])
                self.assertEqual(binds[0], f"{self.home}:{self.home}:rw")
                self.assertTrue((private / "config").is_dir())
                self.assertEqual(list((private / "config").iterdir()), [])
                self.assertEqual(os.environ["DISPLAY"], ":999")
            self.assertFalse(private.exists())
        self.assertEqual(self.host_settings.read_bytes(), original)

    def test_installer_doctor_and_provenance_share_isolation(self):
        from oncotracer_cli import install_safety
        source = dict(oncotracer_version="2.1.0", source_commit="a" * 40,
                      source_sha256="b" * 64, source_tree_dirty=False)
        seen = []
        def checked(command):
            self.assertIn("--cleanenv", command)
            self.assertIn("--no-home", command)
            seen.append(self.command_environment(command))
            record = {"success": True} if "doctor" in command else source
            return subprocess.CompletedProcess(command, 0, json.dumps(record), "")
        with patch.object(install_safety, "_run_checked", side_effect=checked):
            install_safety._verify_sif_runtime("apptainer", self.root / "image.sif", source)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])

    def test_runtime_binds_relative_external_lanes_and_keeps_config_bytes(self):
        import csv
        from oncotracer_cli import cli
        with self.sheet.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
            writer.writerow(["MOCK", json.dumps(["project/R1.fastq", str(self.lane)]), "", "tumor"])
        self.values["lpwgs_root"] = "project"
        self.values["illumina_samplesheet"] = "project/samples.csv"
        self.values["outdir"] = "project/results"
        self.config.write_text(render_flat_yaml(self.values))
        before = self.config.read_bytes()
        args = build_parser().parse_args(["run", "--config", str(self.config),
                                         "--backend", "singularity", "--dry-run"])
        previous = Path.cwd()
        try:
            os.chdir(self.home)
            with patch.dict(os.environ, {"HOME": str(self.home), "DISPLAY": ":999",
                                         "XDG_CONFIG_HOME": str(self.home / ".config")}), \
                 patch.object(cli, "_run") as run:
                cli._run_singularity(self.config, args)
        finally:
            os.chdir(previous)
        command = run.call_args.args[0]
        binds = [str(command[i + 1]) for i, part in enumerate(command) if part == "--bind"]
        for path in (self.link, self.fastq, self.lane, self.sheet):
            self.assertIn(f"{path}:{path}:ro", binds)
        self.assertEqual(command[command.index("--pwd") + 1], self.home)
        self.assertNotEqual(self.command_environment(command)["HOME"], str(self.home))
        self.assertEqual(run.call_args.kwargs, {"dry_run": True})
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.outdir.exists())
        self.assertFalse((self.project / ".oncotracer").exists())

    def test_ont_directory_symlink_targets_are_visible_and_overlap_fails(self):
        from oncotracer_cli.sif_runtime import sif_read_mounts
        reads = self.project / "ont"
        reads.mkdir()
        (reads / "selected library").symlink_to(self.external, target_is_directory=True)
        self.values.update(mode="ont", ont_folder=str(reads), ont_single_sample=True,
                           ont_barcodes=".", ont_sample_names="MOCK")
        self.values.pop("illumina_samplesheet")
        self.config.write_text(render_flat_yaml(self.values))
        mounts = dict(sif_read_mounts(self.config))
        self.assertEqual(mounts[self.external], "ro")
        self.assertEqual(mounts[reads / "selected library"], "ro")
        self.values["outdir"] = str(reads / "results")
        self.config.write_text(render_flat_yaml(self.values))
        with self.assertRaisesRegex(OncoTracerError, "choose a separate output directory"):
            sif_read_mounts(self.config)
        alias = self.project / "output parent alias"
        alias.symlink_to(reads, target_is_directory=True)
        self.values["outdir"] = str(alias / "results")
        self.config.write_text(render_flat_yaml(self.values))
        with self.assertRaisesRegex(OncoTracerError, "choose a separate output directory"):
            sif_read_mounts(self.config)

    def test_reference_cache_under_ont_reads_is_rejected(self):
        from oncotracer_cli.sif_runtime import sif_read_mounts
        self.values.update(mode="ont", ont_folder=str(self.project), ont_single_sample=True,
                           ont_barcodes=".", ont_sample_names="MOCK",
                           outdir=str(self.root / "separate results"))
        self.values.pop("illumina_samplesheet")
        alias = self.home / "project alias"
        alias.symlink_to(self.project, target_is_directory=True)
        for project in (self.project, alias):
            with self.subTest(project=project):
                self.values["lpwgs_root"] = str(project)
                self.config.write_text(render_flat_yaml(self.values))
                with self.assertRaisesRegex(OncoTracerError, "reference cache overlaps.*lpwgs_root"):
                    sif_read_mounts(self.config)

    def test_project_directory_alias_keeps_lexical_parent(self):
        from oncotracer_cli.sif_runtime import sif_project_alias_mounts
        alias_parent = self.home / "aliases"
        alias_parent.mkdir()
        alias = alias_parent / "reference project"
        alias.symlink_to(self.project, target_is_directory=True)
        self.values["lpwgs_root"] = str(alias)
        self.config.write_text(render_flat_yaml(self.values))
        self.assertIn((alias_parent, "rw"), sif_project_alias_mounts(self.config))

    def test_unsupported_bind_paths_and_home_syntax_fail_before_execution(self):
        from oncotracer_cli.sif_runtime import isolated_sif_command, sif_read_mounts
        for path in (Path("/"), Path("/opt"), self.root / "reads,extra"):
            with self.subTest(path=path), self.assertRaises(OncoTracerError):
                with isolated_sif_command("apptainer", mounts=[(path, "ro")]):
                    self.fail("unsafe bind was accepted")
        self.sheet.write_text("sample,fastq_1,fastq_2,status\nMOCK,~/R1.fastq,,tumor\n")
        with self.assertRaisesRegex(OncoTracerError, "fastq_1 in samplesheet row 2"):
            sif_read_mounts(self.config)

    def test_private_directory_is_removed_on_interruption(self):
        from oncotracer_cli.sif_runtime import isolated_sif_command
        private = None
        with self.assertRaises(KeyboardInterrupt):
            with isolated_sif_command("apptainer") as command:
                bind = command[command.index("--bind") + 1]
                private = Path(str(bind).split(":")[0])
                raise KeyboardInterrupt
        self.assertIsNotNone(private)
        self.assertFalse(private.exists())



class InteractiveEntryTests(unittest.TestCase):
    def test_ffpe_alias_opens_only_browser_with_selected_defaults_and_resources(self):
        from oncotracer_cli import cli
        for backend in ("conda", "docker"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / "project $(touch SHOULD_NOT_EXIST)"
                values = ["--interactive", "--ffpe", "--backend", backend,
                          "--project", str(project), "--input-folder", str(root / "reads"),
                          "--variant-tool-prefix", str(root / "variants"),
                          "--variant-ffperase-root", str(root / "ffperase"),
                          "--variant-ffperase-models", str(root / "models"),
                          "--variant-ffperase-prefix", str(root / "ffpe-env"),
                          "--variant-ffperase-sif", str(root / "ffpe.sif"), "--no-browser"]
                if backend == "docker":
                    values += ["--image", "oncotracer:local"]
                with patch("oncotracer_cli.web.command_web", return_value=0) as browser, \
                     patch("oncotracer_cli.setup._command_setup") as save, \
                     patch.object(cli, "execute_run") as run:
                    self.assertEqual(cli.main(values), 0)
                browser.assert_called_once()
                args = browser.call_args.args[0]
                self.assertEqual(args.command, "setup")
                self.assertEqual(args.backend, backend)
                self.assertEqual(args.mode, "illumina")
                self.assertEqual(args.analysis, "cna")
                self.assertTrue(args.variants)
                self.assertEqual(args.variant_engine, "glimpse2")
                self.assertEqual(args.variant_specimen_type, "ffpe")
                self.assertEqual(args.variant_ffperase, "required")
                self.assertEqual(args.variant_tool_prefix, str(root / "variants"))
                self.assertEqual(args.variant_ffperase_models, str(root / "models"))
                self.assertEqual(args.variant_ffperase_prefix, str(root / "ffpe-env"))
                self.assertEqual(args.variant_ffperase_sif, str(root / "ffpe.sif"))
                self.assertTrue(args.no_browser)
                self.assertFalse(args.run)
                self.assertFalse(args.non_interactive)
                self.assertIsNone(args.variant_download_resources)
                self.assertIsNone(args.variant_accept_ffperase_license)
                save.assert_not_called()
                run.assert_not_called()
                self.assertFalse(project.exists())
                self.assertEqual(list(root.iterdir()), [])

    def test_ffpe_defaults_reach_the_browser_form_without_creating_a_job(self):
        from oncotracer_cli.cli import parse_arguments
        from oncotracer_cli.web import WebState
        with tempfile.TemporaryDirectory() as directory:
            args = parse_arguments(["--interactive", "--ffpe", "--backend", "conda"])
            state = WebState(Path(directory), args)
            with patch("oncotracer_cli.web.inspect_hardware", return_value={"cpu_workers_available": 4}), \
                 patch("oncotracer_cli.web.resource_report", return_value={"suggested_threads": 2}):
                defaults = state.system()["defaults"]
            self.assertEqual(defaults["variant_specimen_type"], "ffpe")
            self.assertEqual(defaults["variant_ffperase"], "required")
            self.assertTrue(defaults["variants"])
            self.assertIsNone(defaults["variant_accept_ffperase_license"])
            self.assertIsNone(defaults["variant_download_resources"])
            self.assertIsNone(state.job)
            self.assertEqual(state.projects, {})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_python_native_entry_help_and_unselected_preset_are_inert(self):
        with tempfile.TemporaryDirectory() as directory:
            for flags, expected in (([], 0), (["--help"], 0), (["--ffpe"], 2),
                                    (["--interactive", "--ffpe", "--help"], 0)):
                with self.subTest(flags=flags):
                    result = subprocess.run([sys.executable, str(ROOT / "oncotracer"), *flags],
                                            cwd=directory, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_plain_interactive_alias_preserves_general_setup(self):
        from oncotracer_cli import cli
        with patch("oncotracer_cli.web.command_web", return_value=0) as browser:
            self.assertEqual(cli.main(["--backend", "conda", "--interactive"]), 0)
        args = browser.call_args.args[0]
        self.assertFalse(args.variants)
        self.assertIsNone(args.variant_specimen_type)
        self.assertFalse(args.run)

    def test_interactive_rejects_every_nonbrowser_route_before_dispatch(self):
        from oncotracer_cli import cli
        cases = [["--run"], ["--terminal"], ["--manual"], ["--non-interactive"],
                 ["--samplesheet", "samples.csv"], ["--fastq-1", "R1.fastq"],
                 ["--fastq-2", "R2.fastq"], ["--sample-name", "MOCK"],
                 ["--barcodes", "barcode01"], ["--sample-names", "MOCK"],
                 ["--status", "tumor"], ["--threads", "0"]]
        for flags in cases:
            with self.subTest(flags=flags), patch("oncotracer_cli.web.command_web") as browser, \
                 patch("oncotracer_cli.setup._command_setup") as save, \
                 patch.object(cli, "execute_run") as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--interactive", *flags]), 2)
                browser.assert_not_called()
                save.assert_not_called()
                run.assert_not_called()

    def test_ffpe_rejects_conflicting_presets_and_existing_bam_config(self):
        from oncotracer_cli import cli
        cases = [["--mode", "ont"], ["--analysis", "methylation"],
                 ["--variant-specimen-type", "fresh"], ["--variant-ffperase", "off"],
                 ["--variant-config", "saved.yml"], ["--backend", "singularity"],
                 ["--reads-folder", "ont"], ["--modbam", "calls.bam"], ["--pod5-dir", "pod5"]]
        for flags in cases:
            with self.subTest(flags=flags), patch("oncotracer_cli.web.command_web") as browser, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--interactive", "--ffpe", *flags]), 2)
                browser.assert_not_called()

    def test_no_arguments_or_ffpe_alone_start_nothing(self):
        from oncotracer_cli import cli
        with patch("oncotracer_cli.web.command_web") as browser, \
             patch.object(cli, "execute_run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(cli.main(["--ffpe"]), 2)
        self.assertIn("--interactive", output.getvalue())
        self.assertIn("--ffpe", output.getvalue())
        self.assertIn("--interactive --ffpe", error.getvalue())
        browser.assert_not_called()
        run.assert_not_called()

    def test_existing_subcommands_remain_explicit(self):
        from oncotracer_cli.cli import parse_arguments
        self.assertTrue(parse_arguments(["setup", "--run", "--project", "saved"]).run)
        self.assertEqual(parse_arguments(["run", "--config", "saved.yml"]).command, "run")
        self.assertEqual(parse_arguments(["--conda", "-params-file", "saved.yml"]).command, "run")


if __name__ == "__main__":
    unittest.main()
