#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.engine import (  # noqa: E402
    Toolchain,
    _correct_ichor_segments,
    parse_illumina_samplesheet,
    prepare_qdnaseq_annotation,
    parse_ont_samples,
    run_refinement_and_outputs,
)
from oncotracer_cli.runtime import CommandRunner, StageLedger  # noqa: E402


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

    def test_ont_barcode_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fastq_pass"
            (root / "barcode01").mkdir(parents=True)
            values = {
                "ont_folder": str(root),
                "ont_barcodes": "1",
                "ont_sample_names": "S1",
            }
            samples = parse_ont_samples(values)
            self.assertEqual(samples[0].barcode, "barcode01")
            self.assertEqual(samples[0].sample, "S1")

    def test_toolchain_wraps_stage_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "qdna" / "bin" / "Rscript"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            toolchain = Toolchain(
                root / "core", root / "qdna", root / "ichor",
                root / "classifier", root / "gistic",
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
                self.assertEqual(toolchain.wrap("qdnaseq", ["Rscript", "x.R"])[0], str(exact))

    def test_rscript_command_cleans_all_r_routing_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "qdna"
            rscript = prefix / "bin" / "Rscript"
            rscript.parent.mkdir(parents=True)
            rscript.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rscript.chmod(0o755)
            toolchain = Toolchain(qdnaseq_prefix=prefix)
            with patch("oncotracer_cli.engine.require_command", return_value="/usr/bin/env"):
                command = toolchain.rscript("qdnaseq", ["analysis.R", "--input", "x"])
            self.assertEqual(
                command,
                [
                    "/usr/bin/env",
                    "-u", "R_HOME",
                    "-u", "R_LIBS",
                    "-u", "R_LIBS_USER",
                    "-u", "R_LIBS_SITE",
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
            self.assertNotIn("conda", command)
            self.assertNotIn("-lc", command)

    def test_native_refinement_never_invokes_conda_or_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_bin = temporary / "fake-bin"
            fake_bin.mkdir()
            invocation_log = temporary / "python-invocations.txt"
            mutation_log = temporary / "forbidden-mutations.txt"

            python = fake_bin / "python"
            python.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$INVOCATION_LOG\"\nexit 0\n",
                encoding="utf-8",
            )
            samtools = fake_bin / "samtools"
            samtools.write_text("#!/bin/sh\nprintf 'samtools 1.22.1\\n'\n", encoding="utf-8")
            forbidden = (
                "#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$MUTATION_LOG\"\nexit 99\n"
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
                "--python-executable", str(python),
                "--samtools-executable", str(samtools),
                "--mode", "illumina",
                "--lpwgs-root", str(temporary / "project"),
                "--outdir", str(temporary / "output"),
                "--illumina-qdnaseq-dir", str(qdnaseq),
                "--illumina-bam-dir", str(bam),
                "--illumina-prior-seg", str(prior),
                "--zipcnv-mode", "adapted",
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
                str(ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine" / "bam_cnv_boundary_refine.py"),
                invocations,
            )
            self.assertIn(
                str(ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine" / "zipcnv_compare.py"),
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
            ROOT / "bin" / "scripts" / "bam_cnv_boundary_refine" / "bam_cnv_boundary_refine.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(committed, embedded)

    def test_qdnaseq_helper_receives_exact_rscript(self) -> None:
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
            rds = lpwgs / ".oncotracer" / "qdnaseq-bin-data" / "QDNAseq.hg38.100kbp.SR50.rds"
            rds.parent.mkdir(parents=True)
            rds.write_bytes(b"annotation")
            runner = CommandRunner(root / "trace.tsv", echo=False)
            completed = subprocess.CompletedProcess([], 0, stdout=f"{rds}\n", stderr="")
            with patch("subprocess.run", return_value=completed) as run:
                result = prepare_qdnaseq_annotation(
                    root, lpwgs, 100, runner, Toolchain(qdnaseq_prefix=prefix)
                )
            self.assertEqual(result, rds)
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


if __name__ == "__main__":
    unittest.main()
