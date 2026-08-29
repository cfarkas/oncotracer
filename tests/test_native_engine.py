#!/usr/bin/env python3
from __future__ import annotations

import ast
import contextlib
import hashlib
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import engine  # noqa: E402
from oncotracer_cli.engine import (  # noqa: E402
    ICHOR_ASSETS,
    IlluminaSample,
    OntSample,
    Toolchain,
    _correct_ichor_segments,
    _ont_caller,
    _reject_local_sample_panel,
    parse_illumina_samplesheet,
    prepare_qdnaseq_annotation,
    parse_ont_samples,
    run_ichorcna,
    run_qdnaseq,
    run_refinement_and_outputs,
)
from oncotracer_cli.runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
)  # noqa: E402


class NativeEngineTests(unittest.TestCase):
    def test_illumina_samplesheet_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            r1 = root / "A_R1.fastq.gz"
            r2 = root / "A_R2.fastq.gz"
            r1.write_bytes(b"x")
            r2.write_bytes(b"x")
            sheet = root / "samples.csv"
            with sheet.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
                writer.writerow(["A", r1, r2, "tumor"])
            samples = parse_illumina_samplesheet(sheet)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0].sample, "A")
            self.assertEqual(samples[0].status, "tumor")

    def test_normal_rows_are_valid_independent_illumina_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fastq = root / "CTRL001.fastq.gz"
            fastq.write_bytes(b"x")
            sheet = root / "samples.csv"
            sheet.write_text(
                "sample,fastq_1,fastq_2,status\n" f"CTRL001,{fastq},,normal\n",
                encoding="utf-8",
            )
            samples = parse_illumina_samplesheet(sheet)
            self.assertEqual(
                [(sample.sample, sample.status) for sample in samples],
                [("CTRL001", "normal")],
            )

    def test_local_sample_panel_settings_are_rejected_even_when_false(self) -> None:
        for key in (
            "illumina_build_pon",
            "illumina_pon_normal_samples",
            "illumina_pon_min_normals",
            "illumina_pon_name",
            "illumina_pon_min_mapq",
            "illumina_pon_r_container",
            "ont_build_pon",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    OncoTracerError,
                    "NORMAL samples are analyzed independently",
                ):
                    _reject_local_sample_panel({key: False})

    def test_native_qdnaseq_preserves_roles_without_panel_logic(self) -> None:
        source = (ROOT / "bin/scripts/native_qdnaseq.R").read_text(encoding="utf-8")
        self.assertIn("qdnaseq_sample_roles.tsv", source)
        self.assertIn("sample_role = samples$status[[i]]", source)
        self.assertNotIn("normal_panel_manifest", source)
        self.assertNotIn("pon_median", source)

    def test_legacy_ont_launcher_cannot_construct_a_sample_panel(self) -> None:
        launcher = ROOT / "bin/scripts/run_ont_samurai_barcodes.sh"
        source = launcher.read_text(encoding="utf-8")
        for forbidden in (
            "QDNASEQ_BUILD_LOCAL_PON",
            "write_qdnaseq_local_pon_rscript",
            "run_qdnaseq_local_pon",
            "NF_CMD+=( --build_pon",
            "normal_panel_manifest.tsv",
            "median PBMC/normal log2 profile",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("SAMPLE_DERIVED_PANEL_USED=false", source)
        self.assertNotIn(
            "ont_build_pon", (ROOT / "nextflow.config").read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "ont_build_pon", (ROOT / "main.nf").read_text(encoding="utf-8")
        )
        result = subprocess.run(
            ["bash", str(launcher), "--build-pon"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("NORMAL samples are analyzed independently", result.stderr)

        normal_result = subprocess.run(
            [
                "bash",
                str(launcher),
                "--folder",
                "/not-used",
                "--barcodes",
                "barcode01",
                "--outdir",
                "/not-used",
                "--status",
                "normal",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(normal_result.returncode, 2)
        self.assertIn(
            "frozen Nextflow comparator cannot CNA-call NORMAL rows independently",
            normal_result.stderr,
        )
        self.assertNotIn("nextflow is required", normal_result.stderr)

    def test_ont_barcode_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fastq_pass"
            (root / "barcode01").mkdir(parents=True)
            (root / "barcode02").mkdir()
            values = {
                "ont_folder": str(root),
                "ont_barcodes": "1",
                "ont_sample_names": "S1",
                "ont_normal_folder": str(root),
                "ont_normal_barcodes": "2",
                "ont_normal_sample_names": "CTRL1",
                "ont_analysis_type": "solid_biopsy",
                "ont_caller": "qdnaseq",
            }
            samples = parse_ont_samples(values)
            self.assertEqual(
                [(sample.sample, sample.barcode, sample.status) for sample in samples],
                [
                    ("S1", "barcode01", "tumor"),
                    ("CTRL1", "barcode02", "normal"),
                ],
            )

    def test_ont_normal_samples_require_role_preserving_qdnaseq(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fastq_pass"
            (root / "barcode01").mkdir(parents=True)
            (root / "barcode02").mkdir()
            values = {
                "ont_folder": str(root),
                "ont_barcodes": "barcode01",
                "ont_sample_names": "Tumor_A",
                "ont_normal_folder": str(root),
                "ont_normal_barcodes": "barcode02",
                "ont_normal_sample_names": "Control_A",
            }
            with self.assertRaisesRegex(
                OncoTracerError, "independent ONT NORMAL samples require"
            ):
                parse_ont_samples(values)

            values["ont_analysis_type"] = "solid_biopsy"
            values["ont_caller"] = "qdnaseq"
            self.assertEqual(
                [sample.status for sample in parse_ont_samples(values)],
                ["tumor", "normal"],
            )

    def test_ont_barcode_directory_cannot_have_conflicting_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fastq_pass"
            (root / "barcode01").mkdir(parents=True)
            with self.assertRaisesRegex(OncoTracerError, "may appear only once"):
                parse_ont_samples(
                    {
                        "ont_folder": str(root),
                        "ont_barcodes": "barcode01",
                        "ont_sample_names": "Tumor_A",
                        "ont_normal_folder": str(root),
                        "ont_normal_barcodes": "barcode01",
                        "ont_normal_sample_names": "Control_A",
                    }
                )

    def test_toolchain_wraps_stage_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "qdna" / "bin" / "Rscript"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            toolchain = Toolchain(
                root / "core",
                root / "qdna",
                root / "ichor",
                root / "classifier",
                root / "gistic",
            )
            command = toolchain.wrap("qdnaseq", ["Rscript", "x.R"])
            self.assertEqual(command, [str(executable), "x.R"])
            self.assertNotIn("conda", command)

    def test_toolchain_ignores_foreign_path_for_configured_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact = root / "qdna" / "bin" / "Rscript"
            foreign = root / "foreign" / "bin" / "Rscript"
            for executable in (exact, foreign):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            toolchain = Toolchain(qdnaseq_prefix=root / "qdna")
            with patch.dict(os.environ, {"PATH": str(foreign.parent)}):
                self.assertEqual(toolchain.executable("qdnaseq", "Rscript"), str(exact))
                self.assertEqual(
                    toolchain.wrap("qdnaseq", ["Rscript", "x.R"])[0], str(exact)
                )

    def test_rscript_command_cleans_all_r_routing_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "qdna"
            rscript = prefix / "bin" / "Rscript"
            rscript.parent.mkdir(parents=True)
            rscript.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rscript.chmod(0o755)
            toolchain = Toolchain(qdnaseq_prefix=prefix)
            with patch(
                "oncotracer_cli.engine.require_command", return_value="/usr/bin/env"
            ):
                command = toolchain.rscript("qdnaseq", ["analysis.R", "--input", "x"])
            self.assertEqual(
                command,
                [
                    "/usr/bin/env",
                    "-u",
                    "R_HOME",
                    "-u",
                    "R_LIBS",
                    "-u",
                    "R_LIBS_USER",
                    "-u",
                    "R_LIBS_SITE",
                    str(rscript),
                    "--vanilla",
                    "analysis.R",
                    "--input",
                    "x",
                ],
            )

    def test_gistic_environment_is_derived_from_exact_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "gistic"
            mcr = prefix / "share" / "mcr-8.3-0" / "v83"
            libraries = [
                mcr / "runtime" / "glnxa64",
                mcr / "bin" / "glnxa64",
                mcr / "sys" / "os" / "glnxa64",
            ]
            for path in libraries:
                path.mkdir(parents=True)
            environment = Toolchain(gistic_prefix=prefix).environment("gistic")
            self.assertEqual(
                environment["LD_LIBRARY_PATH"],
                os.pathsep.join(str(path) for path in libraries),
            )
            self.assertEqual(environment["LD_LIBRARY_PATH_MCR"], "")

    def test_refinement_command_uses_exact_core_python_and_samtools(self) -> None:
        class StopAfterCapture(RuntimeError):
            pass

        class CaptureRunner:
            command: list[str] | None = None

            def run(self, stage, command, **_kwargs):
                self.command = [str(item) for item in command]
                raise StopAfterCapture(stage)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            core = temporary / "core"
            python = core / "bin" / "python"
            samtools = core / "bin" / "samtools"
            for executable in (python, samtools):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            runner = CaptureRunner()
            with self.assertRaisesRegex(StopAfterCapture, "bam-refinement"):
                run_refinement_and_outputs(
                    ROOT,
                    {},
                    "illumina",
                    temporary / "samurai",
                    temporary / "qdnaseq",
                    temporary / "bam",
                    temporary / "out",
                    temporary / "lpwgs",
                    runner,  # type: ignore[arg-type]
                    Toolchain(core_prefix=core),
                    force=False,
                )
            self.assertIsNotNone(runner.command)
            command = runner.command or []
            self.assertIn("--native-current-environment", command)
            python_index = command.index("--python-executable")
            samtools_index = command.index("--samtools-executable")
            self.assertEqual(command[python_index + 1], str(python))
            self.assertEqual(command[samtools_index + 1], str(samtools))
            codification_index = command.index("--codification-script")
            cytoband_index = command.index("--cytoband")
            self.assertEqual(
                command[codification_index + 1],
                str(
                    ROOT / "bin/cna_codification/scripts/cna_to_cytogenomic_notation.py"
                ),
            )
            self.assertEqual(
                command[cytoband_index + 1],
                str(ROOT / "bin/cna_codification/resources/hg38.cytoBand.txt.gz"),
            )
            self.assertNotIn("conda", command)
            self.assertNotIn("-lc", command)
            self.assertNotIn("--normal-samples", command)
            self.assertNotIn("--normal-bam-dirs", command)
            self.assertNotIn("--pon-mode", command)

    def test_native_refinement_never_invokes_conda_or_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_bin = temporary / "fake-bin"
            fake_bin.mkdir()
            invocation_log = temporary / "python-invocations.txt"
            mutation_log = temporary / "forbidden-mutations.txt"

            python = fake_bin / "python"
            python.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$INVOCATION_LOG"\nexit 0\n',
                encoding="utf-8",
            )
            samtools = fake_bin / "samtools"
            samtools.write_text(
                "#!/bin/sh\nprintf 'samtools 1.22.1\\n'\n", encoding="utf-8"
            )
            forbidden = (
                '#!/bin/sh\nprintf \'%s\\n\' "$0 $*" >> "$MUTATION_LOG"\nexit 99\n'
            )
            for name in ("conda", "git"):
                path = fake_bin / name
                path.write_text(forbidden, encoding="utf-8")
                path.chmod(0o755)
            python.chmod(0o755)
            samtools.chmod(0o755)

            qdnaseq = temporary / "qdnaseq"
            bam = temporary / "bam"
            qdnaseq.mkdir()
            bam.mkdir()
            prior = qdnaseq / "all_segments.seg"
            prior.write_text("sample\tchrom\tstart\tend\n", encoding="utf-8")
            command = [
                "/bin/bash",
                str(ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine.sh"),
                "--native-current-environment",
                "--python-executable",
                str(python),
                "--samtools-executable",
                str(samtools),
                "--mode",
                "illumina",
                "--lpwgs-root",
                str(temporary / "project"),
                "--outdir",
                str(temporary / "output"),
                "--illumina-qdnaseq-dir",
                str(qdnaseq),
                "--illumina-bam-dir",
                str(bam),
                "--illumina-prior-seg",
                str(prior),
                "--zipcnv-mode",
                "adapted",
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "INVOCATION_LOG": str(invocation_log),
                    "MUTATION_LOG": str(mutation_log),
                }
            )
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(mutation_log.exists(), completed.stderr)
            invocations = invocation_log.read_text(encoding="utf-8")
            self.assertIn(
                str(
                    ROOT
                    / "bin"
                    / "scripts"
                    / "bam_cnv_boundary_refine"
                    / "bam_cnv_boundary_refine.py"
                ),
                invocations,
            )
            self.assertIn(
                str(
                    ROOT
                    / "bin"
                    / "scripts"
                    / "bam_cnv_boundary_refine"
                    / "zipcnv_compare.py"
                ),
                invocations,
            )
            self.assertIn(f"--samtools-executable {samtools}", invocations)
            self.assertNotIn(str(temporary / "project" / "scripts"), invocations)

    def test_native_refinement_rejects_purge_environment(self) -> None:
        script = ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine.sh"
        completed = subprocess.run(
            ["/bin/bash", script, "--native-current-environment", "--purge-env"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("cannot be combined", completed.stderr)

    def test_committed_refinement_helper_matches_embedded_payload(self) -> None:
        wrapper = (ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine.sh").read_text(
            encoding="utf-8"
        )
        start = wrapper.index("cat > \"$PY_HELPER\" <<'PY'\n") + len(
            "cat > \"$PY_HELPER\" <<'PY'\n"
        )
        end = wrapper.index("\nPY\n", start)
        embedded = wrapper[start:end] + "\n"
        committed = (
            ROOT
            / "bin"
            / "scripts"
            / "bam_cnv_boundary_refine"
            / "bam_cnv_boundary_refine.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(committed, embedded)

    def test_boundary_refinement_has_no_sample_derived_panel_path(self) -> None:
        helper = (
            ROOT
            / "bin"
            / "scripts"
            / "bam_cnv_boundary_refine"
            / "bam_cnv_boundary_refine.py"
        ).read_text(encoding="utf-8")
        wrapper = (ROOT / "bin/scripts/bam_cnv_boundary_refine.sh").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "normal_bams",
            "normal_counts",
            "used_pon",
            "--normal-bam-dirs",
            "--pon-mode auto",
            "--pon-mode on",
        ):
            self.assertNotIn(forbidden, helper)
        self.assertIn("sample_derived_panel_used", helper)
        self.assertIn("sample-derived panel refinement has been removed", wrapper)

    def test_generated_codification_runner_is_self_contained_and_relocatable(
        self,
    ) -> None:
        helper_path = (
            ROOT
            / "bin"
            / "scripts"
            / "bam_cnv_boundary_refine"
            / "bam_cnv_boundary_refine.py"
        )
        syntax = ast.parse(helper_path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in syntax.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "write_cna_codification_runner"
        )
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        namespace: dict[str, object] = {"Path": Path, "shutil": shutil}
        exec(compile(module, str(helper_path), "exec"), namespace)
        writer = namespace["write_cna_codification_runner"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-assets"
            source.mkdir()
            converter = source / "converter.py"
            converter.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "input_dir = pathlib.Path(sys.argv[sys.argv.index('--input_dir') + 1])\n"
                "cytoband = pathlib.Path(sys.argv[sys.argv.index('--cytoband') + 1])\n"
                "outdir = pathlib.Path(sys.argv[sys.argv.index('--outdir') + 1])\n"
                "assert input_dir.is_dir()\n"
                "assert cytoband.read_bytes() == b'cytoband-fixture'\n"
                "outdir.mkdir(parents=True, exist_ok=True)\n"
                "(outdir / 'fixture.ok').write_text('ok\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            cytoband = source / "cytoband.gz"
            cytoband.write_bytes(b"cytoband-fixture")
            output = root / "original" / "cna_cytogenomic_input"
            (output / "qdnaseq_bins").mkdir(parents=True)

            runner = writer(output, converter, cytoband)
            self.assertIsInstance(runner, Path)
            self.assertTrue(os.access(runner, os.X_OK))
            self.assertEqual(
                (output / "resources/cna_to_cytogenomic_notation.py").read_bytes(),
                converter.read_bytes(),
            )
            self.assertEqual(
                (output / "resources/hg38.cytoBand.txt.gz").read_bytes(),
                cytoband.read_bytes(),
            )
            script_text = runner.read_text(encoding="utf-8")
            for invariant in (
                "--loss -0.30",
                "--gain 0.25",
                "--deep-loss -1.00",
                "--amp 0.70",
                "--min-bins 3",
                "--min-mb 1.0",
                "--max-gap-bp 500000",
            ):
                self.assertIn(invariant, script_text)

            shutil.rmtree(source)
            relocated = root / "relocated"
            shutil.move(output, relocated)
            environment = os.environ.copy()
            environment["PYTHON_EXECUTABLE"] = sys.executable
            completed = subprocess.run(
                [relocated / "run_cna_codification.sh"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                (relocated / "cytogenomic_notation/fixture.ok").read_text(),
                "ok\n",
            )

    def test_qdnaseq_helper_publishes_owned_generation_and_preserves_legacy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh"
            helper.parent.mkdir(parents=True)
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)
            prefix = root / "qdna"
            bash = prefix / "bin" / "bash"
            rscript = prefix / "bin" / "Rscript"
            for executable in (bash, rscript):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            lpwgs = root / "project"
            legacy = lpwgs / ".oncotracer" / "qdnaseq-bin-data"
            legacy.mkdir(parents=True)
            sentinel = legacy / "PATIENT_SENTINEL"
            sentinel.write_bytes(b"must-survive")
            source_bytes = b"pinned-source"
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            rds_bytes = b"annotation"
            runner = CommandRunner(root / "trace.tsv", echo=False)

            def build(command, **_kwargs):
                staging = Path(command[command.index("--cache-dir") + 1])
                source = staging / "QDNAseq.hg38.100kbp.SR50.source.rda"
                rds = staging / "QDNAseq.hg38.100kbp.SR50.rds"
                provenance = Path(f"{rds}.provenance.tsv")
                source.write_bytes(source_bytes)
                rds.write_bytes(rds_bytes)
                provenance.write_text(
                    "field\tvalue\n"
                    "source_url\thttps://raw.githubusercontent.com/asntech/"
                    "QDNAseq.hg38/cf7c07e39de0ac64a9c38cb030cba4626e2aae83/"
                    "data/hg38.100kbp.SR50.rda\n"
                    "source_commit\tcf7c07e39de0ac64a9c38cb030cba4626e2aae83\n"
                    f"source_rda_sha256\t{source_sha256}\n"
                    "object\thg38.100kbp.SR50\n"
                    f"rds_sha256\t{hashlib.sha256(rds_bytes).hexdigest()}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"{rds}\n", stderr=""
                )

            pinned = {**engine.QDNASEQ_HG38_SOURCE_SHA256, 100: source_sha256}
            with (
                patch("oncotracer_cli.engine.QDNASEQ_HG38_SOURCE_SHA256", pinned),
                patch("subprocess.run", side_effect=build) as run,
            ):
                result = prepare_qdnaseq_annotation(
                    root, lpwgs, 100, runner, Toolchain(qdnaseq_prefix=prefix)
                )
                reused = prepare_qdnaseq_annotation(
                    root, lpwgs, 100, runner, Toolchain(qdnaseq_prefix=prefix)
                )
            self.assertEqual(result, reused)
            self.assertEqual(result.read_bytes(), rds_bytes)
            self.assertIn(lpwgs / ".oncotracer" / "reference-cache", result.parents)
            self.assertEqual(sentinel.read_bytes(), b"must-survive")
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(bash))
            self.assertEqual(command[1], str(helper))
            index = command.index("--rscript")
            self.assertEqual(command[index + 1], str(rscript))
            self.assertNotIn("conda", command)

    def test_trace_uses_argument_arrays_without_nextflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.tsv"
            runner = CommandRunner(trace, dry_run=True, echo=False)
            runner.run("example", ["python", "-c", "print('ok')"])
            text = trace.read_text(encoding="utf-8")
            self.assertIn("example", text)
            self.assertNotIn("nextflow", text.lower())

    def test_stage_ledger_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root / "input"
            output_file = root / "output"
            input_file.write_text("a", encoding="utf-8")
            output_file.write_text("b", encoding="utf-8")
            ledger = StageLedger(root / "state.json")
            signature = ledger.signature("stage", ["command"], [input_file])
            ledger.complete("stage", signature, [output_file])
            self.assertTrue(ledger.reusable("stage", signature, [output_file]))

    def test_ichor_logr_correction_matches_samurai_formula(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seg = root / "segments.seg"
            summary = root / "summary.tsv"
            output = root / "corrected.seg"
            seg.write_text(
                "ID\tchrom\tstart\tend\tnum.mark\tlogR_Copy_Number\n"
                "S1\tchr1\t0\t100\t1\t4\n",
                encoding="utf-8",
            )
            summary.write_text(
                "samplename\tTumor Fraction\tPloidy\tGC-Map correction MAD\n"
                "S1\t0.5\t2\t0.1\n",
                encoding="utf-8",
            )
            _correct_ichor_segments(seg, summary, output)
            with output.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertAlmostEqual(float(rows[0]["adj.seg"]), 1.0)

    def test_ichorcna_sample_failure_does_not_block_later_sample(self) -> None:
        class FakeToolchain:
            @staticmethod
            def wrap(_group, command):
                return [str(item) for item in command]

            @staticmethod
            def rscript(_group, command):
                return [str(item) for item in command]

            @staticmethod
            def environment(_group):
                return {}

        class SampleRunner:
            dry_run = False

            def __init__(self) -> None:
                self.stages: list[str] = []
                self.commands: list[list[str]] = []

            def run(self, stage, command, *, cwd=None, stdout=None, **_kwargs):
                self.stages.append(stage)
                self.commands.append([str(item) for item in command])
                if stage.startswith("ichor-readcounter-"):
                    assert stdout is not None
                    stdout.write(
                        "fixedStep chrom=chr1 start=1 step=500000 span=500000\n1\n"
                    )
                    return None
                sample = str(command[command.index("--sample") + 1])
                assert cwd is not None
                if sample == "SNC-E":
                    (cwd / f"{sample}.correctedDepth.txt").write_text(
                        "chr\tstart\tend\tcopy.number\nchr1\t1\t500000\t0\n",
                        encoding="utf-8",
                    )
                    raise OncoTracerError(
                        "replication timing normalization at "
                        "/protected/analysis/SNC-E.wig: invalid 'x'"
                    )
                (cwd / f"{sample}.params.txt").write_text(
                    "Tumor Fraction: 0.25\nPloidy: 2\nGC-Map correction MAD: 0.1\n",
                    encoding="utf-8",
                )
                (cwd / f"{sample}.seg.txt").write_text(
                    "ID\tchrom\tstart\tend\tnum.mark\tlogR_Copy_Number\n"
                    f"{sample}\tchr1\t1\t500000\t1\t4\n",
                    encoding="utf-8",
                )
                (cwd / f"{sample}.correctedDepth.txt").write_text(
                    "chr\tstart\tend\tcopy.number\nchr1\t1\t500000\t2\n",
                    encoding="utf-8",
                )
                return None

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            assets = {}
            fixture_assets = {}
            fixture_sizes = {}
            for name in ("gc", "map", "centromere", "reptime", "pon"):
                filename = f"{name}.asset"
                payload = name.encode("utf-8")
                asset = temporary / filename
                asset.write_bytes(payload)
                assets[name] = asset
                fixture_assets[name] = (filename, hashlib.sha256(payload).hexdigest())
                fixture_sizes[name] = len(payload)
            bams = {}
            samples = []
            fastq_dir = temporary / "fastq"
            fastq_dir.mkdir()
            for name in ("SNC-E", "SNC-F"):
                bam = temporary / f"{name}.bam"
                bam.write_bytes(b"bam")
                bams[name] = bam
                samples.append(OntSample(name, name, fastq_dir))
            samurai = temporary / "01_samurai_ont"
            ichor_out = samurai / "results" / "ichorcna"
            ichor_out.mkdir(parents=True)
            stale = ichor_out / "SNC-E.correctedDepth.txt"
            stale.write_text("stale\n", encoding="utf-8")
            runner = SampleRunner()
            ledger = StageLedger(temporary / "state.json")

            with (
                patch(
                    "oncotracer_cli.engine.prepare_ichor_assets", return_value=assets
                ),
                patch.object(engine, "ICHOR_ASSETS", fixture_assets),
                patch.object(engine, "ICHOR_ASSET_SIZES", fixture_sizes),
            ):
                observed = run_ichorcna(
                    ROOT,
                    temporary / "project",
                    samples,
                    bams,
                    samurai,
                    500,
                    runner,  # type: ignore[arg-type]
                    ledger,
                    FakeToolchain(),  # type: ignore[arg-type]
                    threads=2,
                    force=True,
                )

            self.assertEqual(observed, ichor_out)
            self.assertLess(
                runner.stages.index("ichorcna-SNC-E"),
                runner.stages.index("ichorcna-SNC-F"),
            )
            self.assertEqual(
                ICHOR_ASSETS["pon"][0],
                "HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds",
            )
            ichor_commands = [
                command for command in runner.commands if "--normal-panel" in command
            ]
            self.assertEqual(len(ichor_commands), 2)
            for command in ichor_commands:
                self.assertEqual(
                    command[command.index("--normal-panel") + 1], str(assets["pon"])
                )
            status = json.loads(
                (ichor_out / "ichorcna_sample_status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["overall_status"], "partial_failure")
            self.assertEqual(status["failed_samples"], ["SNC-E"])
            self.assertEqual(status["completed_samples"], ["SNC-F"])
            self.assertEqual(status["samples"][0]["stage"], "ichorcna")
            self.assertIn("invalid 'x'", status["samples"][0]["error"])
            self.assertIn("<path>", status["samples"][0]["error"])
            self.assertNotIn("/media/", status["samples"][0]["error"])
            self.assertFalse(stale.exists())
            self.assertTrue(
                (ichor_out / "SNC-E" / "SNC-E.correctedDepth.txt").is_file()
            )
            self.assertTrue((ichor_out / "SNC-F.correctedDepth.txt").is_file())
            aggregate = (ichor_out / "all_segments_ichorcna_gistic.seg").read_text(
                encoding="utf-8"
            )
            summary = (ichor_out / "ichorcna_summary_mqc.txt").read_text(
                encoding="utf-8"
            )
            corrected = (ichor_out / "segments_logR_corrected_gistic.seg").read_text(
                encoding="utf-8"
            )
            for output in (aggregate, summary, corrected):
                self.assertIn("SNC-F", output)
                self.assertNotIn("SNC-E", output)

    def test_qdnaseq_accepts_explicit_ont_bams_as_unpaired(self) -> None:
        class FakeToolchain:
            @staticmethod
            def rscript(_group, command):
                return [str(item) for item in command]

            @staticmethod
            def environment(_group):
                return {}

        class QdnaRunner:
            dry_run = False

            def __init__(self, output: Path) -> None:
                self.output = output
                self.command: list[str] | None = None

            def run(self, _stage, command, **_kwargs):
                self.command = [str(item) for item in command]
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_text(
                    "ID\tchrom\tloc.start\tloc.end\tseg.mean\n", encoding="utf-8"
                )
                (self.output.parent / "qdnaseq_sample_status.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "complete",
                            "completed_samples": ["SNC-E", "SNC-F"],
                            "failed_samples": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (self.output.parent / "qdnaseq_sample_roles.tsv").write_text(
                    "sample\tstatus\nSNC-E\ttumor\nSNC-F\tnormal\n",
                    encoding="utf-8",
                )
                return None

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fastq_dir = temporary / "fastq"
            fastq_dir.mkdir()
            samples = [
                OntSample("SNC-E", "barcode01", fastq_dir),
                OntSample("SNC-F", "barcode02", fastq_dir, status="normal"),
            ]
            bams = {}
            for sample in samples:
                bam = temporary / f"{sample.sample}.bam"
                bam.write_bytes(b"bam")
                bams[sample.sample] = bam
            annotation = temporary / "QDNAseq.hg38.100kbp.SR50.rds"
            annotation.write_bytes(b"annotation")
            samurai = temporary / "01_samurai_ont"
            output = samurai / "qdnaseq" / "all_segments.seg"
            runner = QdnaRunner(output)

            with (
                patch(
                    "oncotracer_cli.engine.prepare_qdnaseq_annotation",
                    return_value=annotation,
                ),
                patch(
                    "oncotracer_cli.engine._validated_qdnaseq_reader",
                    return_value=contextlib.nullcontext(),
                ),
            ):
                observed, _ = run_qdnaseq(
                    ROOT,
                    temporary / "project",
                    samples,
                    bams,
                    samurai,
                    100,
                    runner,  # type: ignore[arg-type]
                    StageLedger(temporary / "state.json"),
                    FakeToolchain(),  # type: ignore[arg-type]
                    force=True,
                    paired_ends=False,
                )

            self.assertEqual(observed, samurai / "qdnaseq")
            command = runner.command or []
            paired_index = command.index("--paired-ends")
            self.assertEqual(command[paired_index + 1], "false")
            mapq_index = command.index("--min-mapq")
            self.assertEqual(command[mapq_index + 1], "37")
            self.assertEqual(Path(command[0]).name, "native_qdnaseq.R")
            self.assertNotIn("native_qdnaseq_pon.R", command)
            sheet = (samurai / "input" / "native.bam.samplesheet.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("SNC-E", sheet)
            self.assertIn("SNC-F", sheet)
            self.assertIn(",normal", sheet)
            roles = (samurai / "qdnaseq" / "qdnaseq_sample_roles.tsv").read_text(
                encoding="utf-8"
            )
            self.assertIn("SNC-E\ttumor", roles)
            self.assertIn("SNC-F\tnormal", roles)

    def test_illumina_qdnaseq_uses_unmarked_bam_and_frozen_mapq(self) -> None:
        class FakeToolchain:
            @staticmethod
            def rscript(_group, command):
                return [str(item) for item in command]

            @staticmethod
            def environment(_group):
                return {}

        class QdnaRunner:
            dry_run = False

            def __init__(self, output: Path) -> None:
                self.output = output
                self.command: list[str] | None = None

            def run(self, _stage, command, **_kwargs):
                self.command = [str(item) for item in command]
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_text(
                    "ID\tchrom\tloc.start\tloc.end\tseg.mean\n", encoding="utf-8"
                )
                (self.output.parent / "qdnaseq_sample_status.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "complete",
                            "completed_samples": ["CASE"],
                            "failed_samples": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (self.output.parent / "qdnaseq_sample_roles.tsv").write_text(
                    "sample\tstatus\nCASE\ttumor\n", encoding="utf-8"
                )

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            read_1 = temporary / "CASE_R1.fastq.gz"
            read_2 = temporary / "CASE_R2.fastq.gz"
            read_1.write_bytes(b"r1")
            read_2.write_bytes(b"r2")
            sample = IlluminaSample("CASE", read_1, read_2, "tumor")
            alignment_bam = temporary / "alignment" / "CASE.bam"
            alignment_bam.parent.mkdir()
            alignment_bam.write_bytes(b"unmarked-alignment")
            annotation = temporary / "QDNAseq.hg38.100kbp.SR50.rds"
            annotation.write_bytes(b"annotation")
            samurai = temporary / "01_samurai_illumina"
            output = samurai / "qdnaseq" / "all_segments.seg"
            runner = QdnaRunner(output)

            with (
                patch(
                    "oncotracer_cli.engine.prepare_qdnaseq_annotation",
                    return_value=annotation,
                ),
                patch(
                    "oncotracer_cli.engine._validated_qdnaseq_reader",
                    return_value=contextlib.nullcontext(),
                ),
            ):
                run_qdnaseq(
                    ROOT,
                    temporary / "project",
                    [sample],
                    {sample.sample: alignment_bam},
                    samurai,
                    100,
                    runner,  # type: ignore[arg-type]
                    StageLedger(temporary / "state.json"),
                    FakeToolchain(),  # type: ignore[arg-type]
                    force=True,
                )

            command = runner.command or []
            paired_index = command.index("--paired-ends")
            self.assertEqual(command[paired_index + 1], "true")
            mapq_index = command.index("--min-mapq")
            self.assertEqual(command[mapq_index + 1], "1")
            sheet = (samurai / "input" / "native.bam.samplesheet.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn(str(alignment_bam), sheet)

    def test_illumina_alignment_keeps_markdup_qc_but_returns_unmarked_bam(
        self,
    ) -> None:
        class FakeToolchain:
            @staticmethod
            def executable(_group, name):
                return name

        class AlignmentRunner:
            def __init__(self) -> None:
                self.markdup_runs = 0

            def pipeline(self, _stage, _left, right, **_kwargs):
                command = [str(item) for item in right]
                output = Path(command[command.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"unmarked-alignment")

            def run(self, stage, command, **_kwargs):
                command = [str(item) for item in command]
                if stage.startswith("illumina-index-"):
                    Path(command[-1] + ".bai").write_bytes(b"alignment-index")
                elif "MarkDuplicates" in command:
                    self.markdup_runs += 1
                    output = Path(
                        next(item[2:] for item in command if item.startswith("O="))
                    )
                    metrics = Path(
                        next(item[2:] for item in command if item.startswith("M="))
                    )
                    output.write_bytes(b"duplicate-marked-qc")
                    Path(str(output) + ".bai").write_bytes(b"markdup-index")
                    metrics.write_text("metrics\n", encoding="utf-8")
                else:
                    self.fail(f"unexpected stage: {stage}")

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            read_1 = temporary / "CASE_R1.fastq.gz"
            read_2 = temporary / "CASE_R2.fastq.gz"
            read_1.write_bytes(b"r1")
            read_2.write_bytes(b"r2")
            sample = IlluminaSample("CASE", read_1, read_2, "tumor")
            samurai = temporary / "01_samurai_illumina"
            runner = AlignmentRunner()
            ledger = StageLedger(temporary / "state.json")
            observed = engine._align_illumina_locked(
                [sample],
                {"bwa_prefix": temporary / "reference" / "genome"},
                samurai,
                runner,  # type: ignore[arg-type]
                ledger,
                FakeToolchain(),  # type: ignore[arg-type]
                threads=4,
                force=True,
            )

            unmarked = samurai / "alignment" / "CASE.bam"
            marked = samurai / "markduplicates" / "CASE_markdup.bam"
            self.assertEqual(observed, {"CASE": unmarked})
            self.assertTrue(unmarked.is_file())
            self.assertTrue(Path(str(unmarked) + ".bai").is_file())
            self.assertTrue(marked.is_file())
            self.assertTrue(Path(str(marked) + ".bai").is_file())
            self.assertTrue(
                (samurai / "markduplicates" / "CASE_markdup.metrics.txt").is_file()
            )
            self.assertEqual(runner.markdup_runs, 1)

            metrics = samurai / "markduplicates" / "CASE_markdup.metrics.txt"
            metrics.unlink()
            engine._align_illumina_locked(
                [sample],
                {"bwa_prefix": temporary / "reference" / "genome"},
                samurai,
                runner,  # type: ignore[arg-type]
                ledger,
                FakeToolchain(),  # type: ignore[arg-type]
                threads=4,
                force=False,
            )
            self.assertTrue(metrics.is_file())
            self.assertEqual(runner.markdup_runs, 2)

    def test_ont_caller_validation_is_explicit(self) -> None:
        self.assertEqual(_ont_caller({}), "ichorcna")
        self.assertEqual(
            _ont_caller({"ont_caller": "QDNAseq", "ont_analysis_type": "solid_biopsy"}),
            "qdnaseq",
        )
        with self.assertRaisesRegex(OncoTracerError, "requires ont_analysis_type"):
            _ont_caller({"ont_caller": "qdnaseq"})
        with self.assertRaisesRegex(OncoTracerError, "ont_caller must be"):
            _ont_caller({"ont_caller": "unsupported"})

    def test_ont_qdnaseq_refinement_routes_caller_and_prior(self) -> None:
        class StopAfterCapture(RuntimeError):
            pass

        class CaptureRunner:
            command: list[str] | None = None

            def run(self, stage, command, **_kwargs):
                self.command = [str(item) for item in command]
                raise StopAfterCapture(stage)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            core = temporary / "core"
            for name in ("python", "samtools"):
                executable = core / "bin" / name
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            caller_dir = temporary / "qdnaseq"
            caller_dir.mkdir()
            runner = CaptureRunner()
            with self.assertRaisesRegex(StopAfterCapture, "bam-refinement"):
                run_refinement_and_outputs(
                    ROOT,
                    {"ont_binsize_kb": 100},
                    "ont",
                    temporary / "samurai",
                    caller_dir,
                    temporary / "bam",
                    temporary / "out",
                    temporary / "project",
                    runner,  # type: ignore[arg-type]
                    Toolchain(core_prefix=core),
                    force=False,
                    caller="qdnaseq",
                )
            command = runner.command or []
            caller_index = command.index("--ont-caller")
            input_index = command.index("--ont-cna-dir")
            prior_index = command.index("--ont-prior-seg")
            self.assertEqual(command[caller_index + 1], "qdnaseq")
            self.assertEqual(command[input_index + 1], str(caller_dir))
            self.assertEqual(
                command[prior_index + 1], str(caller_dir / "all_segments.seg")
            )
            self.assertNotIn("--normal-samples", command)
            self.assertNotIn("--normal-bam-dirs", command)
            self.assertNotIn("--pon-mode", command)


if __name__ == "__main__":
    unittest.main()
