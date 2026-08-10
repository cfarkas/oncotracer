#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.cli import build_parser, execute_run  # noqa: E402
from oncotracer_cli.engine import (
    OntSample,
    _merge_methylation_summary,
    run_native,
)  # noqa: E402
from oncotracer_cli.methylation import (  # noqa: E402
    SUPPORTED_CLASSIFIER_COMMITS,
    _accelerator_environment,
    _bedmethyl_counts,
    resolve_methylation_request,
    run_methylation,
    write_global_methylation_failure,
)
from oncotracer_cli.runtime import (  # noqa: E402
    CommandRunner,
    OncoTracerError,
    StageLedger,
    render_flat_yaml,
    sha256_file,
)


class Fixture:
    def __init__(self, root: Path, classifier: str = "sturgeon"):
        self.root = root
        self.classifier = classifier
        self.pod5 = root / "pod5"
        self.pod5.mkdir()
        (self.pod5 / "batch.pod5").write_bytes(b"pod5-fixture")
        self.fastq = root / "fastq_pass" / "barcode01"
        self.fastq.mkdir(parents=True)
        (self.fastq / "reads.fastq").write_text(
            "@read-1 description\nACGT\n+\nIIII\n", encoding="utf-8"
        )
        self.base_model = root / "dorado-base-model"
        self.mod_model = root / "dorado-mod-model"
        self.base_model.mkdir()
        self.mod_model.mkdir()
        (self.base_model / "config.toml").write_text("base", encoding="utf-8")
        (self.mod_model / "config.toml").write_text("5mCG_5hmCG", encoding="utf-8")
        self.bin = root / "bin"
        self.bin.mkdir()
        self.executables = {
            name: self._executable(name)
            for name in (
                "dorado",
                "modkit",
                "samtools",
                "sturgeon",
                "Rscript",
                "python",
            )
        }
        self.assets = {}
        if classifier == "sturgeon":
            self.assets = {
                "model": self._asset("sturgeon-model.zip", "model"),
                "probes": self._asset("sturgeon-probes.bed", "chr1\t100\t101\tp1\n"),
            }
        else:
            self.assets = {
                "model": self._asset("marlin-model.h5", "model"),
                "features": self._asset("features.RData", "features"),
                "classes": self._asset("classes.xlsx", "classes"),
                "probes": self._asset("marlin-probes.bed", "chr1\t100\t101\tp1\n"),
            }

    def _executable(self, name: str) -> Path:
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def _asset(self, name: str, contents: str) -> Path:
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def config(self) -> dict[str, object]:
        values: dict[str, object] = {
            "mode": "ont",
            "ont_folder": str(self.fastq.parent),
            "ont_barcodes": "1",
            "ont_sample_names": "SNC_F",
            "outdir": str(self.root / "result"),
            "lpwgs_root": str(self.root / "project"),
            "methylation": True,
            "methylation_classifier": self.classifier,
            "methylation_pod5_dir": str(self.pod5),
            "methylation_dorado_executable": str(self.executables["dorado"]),
            "methylation_modkit_executable": str(self.executables["modkit"]),
            "methylation_samtools_executable": str(self.executables["samtools"]),
            "methylation_dorado_model": str(self.base_model),
            "methylation_dorado_modbase_model": str(self.mod_model),
            f"{self.classifier}_source_commit": SUPPORTED_CLASSIFIER_COMMITS[
                self.classifier
            ],
        }
        if self.classifier == "sturgeon":
            values.update(
                {
                    "sturgeon_license_acknowledged": True,
                    "sturgeon_executable": str(self.executables["sturgeon"]),
                    "sturgeon_model": str(self.assets["model"]),
                    "sturgeon_model_sha256": sha256_file(self.assets["model"]),
                    "sturgeon_probes": str(self.assets["probes"]),
                    "sturgeon_probes_sha256": sha256_file(self.assets["probes"]),
                }
            )
        else:
            values.update(
                {
                    "marlin_rscript": str(self.executables["Rscript"]),
                    "marlin_python": str(self.executables["python"]),
                    "marlin_model": str(self.assets["model"]),
                    "marlin_model_sha256": sha256_file(self.assets["model"]),
                    "marlin_features": str(self.assets["features"]),
                    "marlin_features_sha256": sha256_file(self.assets["features"]),
                    "marlin_class_annotations": str(self.assets["classes"]),
                    "marlin_class_annotations_sha256": sha256_file(
                        self.assets["classes"]
                    ),
                    "marlin_probe_bed": str(self.assets["probes"]),
                    "marlin_probe_bed_sha256": sha256_file(self.assets["probes"]),
                }
            )
        return values


class FakeRunner:
    def __init__(
        self,
        pileup: str,
        *,
        mutate_pod5: Path | None = None,
        matching_reads: int = 1,
    ):
        self.pileup = pileup
        self.mutate_pod5 = mutate_pod5
        self.matching_reads = matching_reads
        self.calls: list[tuple[str, list[str]]] = []
        self.environments: dict[str, dict[str, str | None]] = {}

    def run(self, stage, command, **kwargs):
        argv = [str(value) for value in command]
        self.calls.append((stage, argv))
        self.environments[stage] = dict(kwargs.get("env") or {})
        if stage == "methylation-dorado-basecall":
            kwargs["stdout"].write(b"unsorted-bam")
            if self.mutate_pod5 is not None:
                self.mutate_pod5.write_bytes(
                    self.mutate_pod5.read_bytes() + b"-changed"
                )
        elif stage == "methylation-dorado-sort":
            Path(argv[argv.index("-o") + 1]).write_bytes(b"sorted-bam")
        elif stage.startswith("methylation-dorado-index") or stage.startswith(
            "methylation-index-"
        ):
            Path(argv[-1] + ".bai").write_bytes(b"index")
        elif stage.startswith("methylation-split-"):
            Path(argv[argv.index("-o") + 1]).write_bytes(b"sample-bam")
        elif stage.startswith("methylation-count-"):
            kwargs["stdout"].write(f"{self.matching_reads}\n")
        elif stage.startswith("methylation-adjust-"):
            Path(argv[3]).write_bytes(b"combined-bam")
        elif stage.startswith("methylation-pileup-"):
            Path(argv[3]).write_text(self.pileup, encoding="utf-8")
        elif stage.startswith("methylation-sturgeon-inputtobed-"):
            destination = Path(argv[argv.index("-o") + 1])
            destination.write_text(
                "chrom\tchromStart\tchromEnd\tmethylation_call\tprobe_id\n"
                "1\t100\t101\t1\tp1\n",
                encoding="utf-8",
            )
        elif stage.startswith("methylation-sturgeon-predict-"):
            destination = Path(argv[argv.index("-o") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            converted = Path(argv[argv.index("-i") + 1])
            model = Path(argv[argv.index("--model-files") + 1])
            (destination / f"{converted.stem}_{model.stem}.csv").write_text(
                "class,score\nCNS,0.9\n", encoding="utf-8"
            )


class NativeMethylationTests(unittest.TestCase):
    def test_cli_exposes_ont_methylation_flags_and_rejects_two_classifiers(
        self,
    ) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--config",
                "run.yml",
                "--methylation",
                "--sturgeon",
                "--pod5-dir",
                "/data/pod5",
                "--gpu",
            ]
        )
        self.assertTrue(args.methylation)
        self.assertEqual(args.methylation_classifier, "sturgeon")
        self.assertEqual(args.pod5_dir, "/data/pod5")
        self.assertTrue(args.gpu)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "run",
                    "--config",
                    "run.yml",
                    "--methylation",
                    "--sturgeon",
                    "--marlin",
                ]
            )

    def test_methylation_is_ont_only_and_requires_explicit_nonempty_pod5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            config = fixture.config()
            with self.assertRaisesRegex(OncoTracerError, "restricted to mode: ont"):
                resolve_methylation_request(config, mode="illumina")
            config.pop("methylation_pod5_dir")
            with self.assertRaisesRegex(OncoTracerError, "explicit --pod5-dir"):
                resolve_methylation_request(config, mode="ont")
            empty = fixture.root / "empty-pod5"
            empty.mkdir()
            with self.assertRaisesRegex(OncoTracerError, "no non-empty .pod5"):
                resolve_methylation_request(config, mode="ont", pod5_override=empty)
            linked = fixture.root / "linked-pod5"
            linked.mkdir()
            (linked / "batch.pod5").symlink_to(fixture.pod5 / "batch.pod5")
            with self.assertRaisesRegex(OncoTracerError, "symlinks"):
                resolve_methylation_request(config, mode="ont", pod5_override=linked)
            mixed = fixture.root / "mixed-pod5"
            mixed.mkdir()
            (mixed / "valid.pod5").write_bytes(b"pod5")
            (mixed / "empty.pod5").touch()
            with self.assertRaisesRegex(OncoTracerError, "empty POD5 file"):
                resolve_methylation_request(config, mode="ont", pod5_override=mixed)

    def test_assets_and_supported_classifier_commit_are_exactly_pinned(self) -> None:
        for classifier in ("sturgeon", "marlin"):
            with (
                self.subTest(classifier=classifier),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = Fixture(Path(directory), classifier)
                config = fixture.config()
                request = resolve_methylation_request(config, mode="ont")
                assert request is not None
                self.assertEqual(
                    request.classifier_source_commit,
                    SUPPORTED_CLASSIFIER_COMMITS[classifier],
                )
                config[f"{classifier}_source_commit"] = "f" * 40
                with self.assertRaisesRegex(
                    OncoTracerError, "supports exact source commit"
                ):
                    resolve_methylation_request(config, mode="ont")
                config[f"{classifier}_source_commit"] = SUPPORTED_CLASSIFIER_COMMITS[
                    classifier
                ]
                hash_key = (
                    "sturgeon_model_sha256"
                    if classifier == "sturgeon"
                    else "marlin_model_sha256"
                )
                config[hash_key] = "0" * 64
                with self.assertRaisesRegex(OncoTracerError, "SHA-256 mismatch"):
                    resolve_methylation_request(config, mode="ont")
                model_link = fixture.base_model / "linked-config.toml"
                model_link.symlink_to(fixture.base_model / "config.toml")
                with self.assertRaisesRegex(OncoTracerError, "model tree"):
                    resolve_methylation_request(config, mode="ont")

    def test_empty_bedmethyl_is_a_zero_call_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bed = Path(directory) / "empty.bed"
            bed.write_text("", encoding="utf-8")
            self.assertEqual(_bedmethyl_counts(bed), (0, 0, 0))
            bed.write_text(
                "chr1\t100\t101\tm\t0\t+\t100\t101\t0,0,0\t10\t80.0\t8\t2\t0\t0\t0\t0\t0\n",
                encoding="utf-8",
            )
            self.assertEqual(_bedmethyl_counts(bed), (1, 1, 8))

    def test_global_failure_still_seals_sanitized_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            request = resolve_methylation_request(fixture.config(), mode="ont")
            assert request is not None
            outdir = fixture.root / "result"
            status = write_global_methylation_failure(
                outdir,
                request,
                OncoTracerError("failed at /patient/private/file"),
            )
            self.assertEqual(status["overall_status"], "failed")
            provenance = json.loads(
                (outdir / "07_methylation" / "methylation_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(provenance["status"], "failed")
            self.assertNotIn("/patient", provenance["failure"])
            request.dorado.unlink()
            second = write_global_methylation_failure(
                fixture.root / "second-result",
                request,
                OncoTracerError("Dorado disappeared"),
            )
            self.assertEqual(second["overall_status"], "failed")

    def _run_fixture(self, pileup: str, *, gpu: bool):
        temporary = tempfile.TemporaryDirectory()
        fixture = Fixture(Path(temporary.name))
        config = fixture.config()
        config["methylation_gpu"] = gpu
        request = resolve_methylation_request(config, mode="ont")
        assert request is not None
        fasta = fixture.root / "genome.fa"
        fai = fixture.root / "genome.fa.fai"
        fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
        fai.write_text("chr1\t4\t6\t4\t5\n", encoding="utf-8")
        outdir = fixture.root / "result"
        runner = FakeRunner(pileup)
        status = run_methylation(
            ROOT,
            request,
            [OntSample("SNC_F", "barcode01", fixture.fastq)],
            {"fasta": fasta, "fai": fai},
            outdir,
            runner,  # type: ignore[arg-type]
            StageLedger(outdir / ".oncotracer-native" / "state.json"),
            threads=4,
            force=False,
        )
        return temporary, fixture, outdir, runner, status

    def test_zero_cpg_aborts_classifier_but_preserves_status_for_cna(self) -> None:
        temporary, _fixture, outdir, runner, status = self._run_fixture("", gpu=False)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(status["overall_status"], "no_cpg_modifications")
        self.assertEqual(status["no_cpg_samples"], ["SNC_F"])
        self.assertFalse(
            any("sturgeon" in stage for stage, _command in runner.calls), runner.calls
        )
        sample = status["samples"][0]
        self.assertEqual(sample["modified_cpg_calls"], 0)
        self.assertTrue(
            (outdir / "07_methylation" / "methylation_status.json").is_file()
        )

    def test_pod5_without_selected_fastq_reads_is_not_a_zero_cpg_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            request = resolve_methylation_request(fixture.config(), mode="ont")
            assert request is not None
            fasta = fixture.root / "genome.fa"
            fai = fixture.root / "genome.fa.fai"
            fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
            fai.write_text("chr1\t4\t6\t4\t5\n", encoding="utf-8")
            outdir = fixture.root / "result"
            status = run_methylation(
                ROOT,
                request,
                [OntSample("SNC_F", "barcode01", fixture.fastq)],
                {"fasta": fasta, "fai": fai},
                outdir,
                FakeRunner("", matching_reads=0),  # type: ignore[arg-type]
                StageLedger(outdir / ".oncotracer-native" / "state.json"),
                threads=4,
                force=False,
            )
            self.assertEqual(status["overall_status"], "failed")
            self.assertEqual(status["failed_samples"], ["SNC_F"])
            self.assertEqual(status["no_cpg_samples"], [])
            self.assertEqual(status["samples"][0]["modbam_records"], 0)
            self.assertIn("no FASTQ-selected reads", status["samples"][0]["error"])

    def test_detected_cpg_runs_sturgeon_and_gpu_routes_only_dorado(self) -> None:
        pileup = (
            "chr1\t100\t101\tm\t0\t+\t100\t101\t0,0,0\t10\t80.0\t8\t2\t0\t0\t0\t0\t0\n"
        )
        temporary, _fixture, outdir, runner, status = self._run_fixture(
            pileup, gpu=True
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(status["overall_status"], "complete")
        self.assertEqual(status["completed_samples"], ["SNC_F"])
        dorado = next(
            command
            for stage, command in runner.calls
            if stage == "methylation-dorado-basecall"
        )
        self.assertEqual(dorado[dorado.index("--device") + 1], "cuda:all")
        for stage, command in runner.calls:
            if stage.startswith("methylation-sturgeon-"):
                self.assertEqual(command[1], "--no-logfile")
        self.assertTrue(
            any(
                stage.startswith("methylation-sturgeon-predict")
                for stage, _ in runner.calls
            )
        )
        provenance = json.loads(
            (outdir / "07_methylation" / "methylation_provenance.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(provenance["gpu_requested"])
        self.assertEqual(provenance["modkit_acceleration"], "cpu_threads")
        self.assertIsNone(
            runner.environments["methylation-dorado-basecall"]["CUDA_VISIBLE_DEVICES"]
        )
        self.assertEqual(
            runner.environments["methylation-adjust-SNC_F"]["CUDA_VISIBLE_DEVICES"],
            "",
        )
        self.assertEqual(
            runner.environments["methylation-sturgeon-predict-SNC_F"][
                "NVIDIA_VISIBLE_DEVICES"
            ],
            "void",
        )

    def test_explicit_gpu_removes_empty_masks_but_preserves_device_selection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.tsv"
            observed = root / "environment.json"
            with patch.dict(
                os.environ,
                {
                    "CUDA_VISIBLE_DEVICES": "",
                    "NVIDIA_VISIBLE_DEVICES": "void",
                },
            ):
                with observed.open("w", encoding="utf-8") as output:
                    CommandRunner(trace, echo=False).run(
                        "gpu-environment-probe",
                        [
                            sys.executable,
                            "-c",
                            (
                                "import json, os; "
                                "print(json.dumps({"
                                "'cuda': os.environ.get('CUDA_VISIBLE_DEVICES'), "
                                "'nvidia': os.environ.get('NVIDIA_VISIBLE_DEVICES')"
                                "}))"
                            ),
                        ],
                        env=_accelerator_environment(True),
                        stdout=output,
                    )
            self.assertEqual(
                json.loads(observed.read_text(encoding="utf-8")),
                {"cuda": None, "nvidia": "all"},
            )
            with patch.dict(
                os.environ,
                {
                    "CUDA_VISIBLE_DEVICES": "2",
                    "NVIDIA_VISIBLE_DEVICES": "GPU-selected",
                },
            ):
                self.assertEqual(
                    _accelerator_environment(True),
                    {
                        "CUDA_VISIBLE_DEVICES": "2",
                        "NVIDIA_VISIBLE_DEVICES": "GPU-selected",
                    },
                )

    def test_resume_reuses_dorado_modkit_and_classifier_outputs(self) -> None:
        pileup = (
            "chr1\t100\t101\tm\t0\t+\t100\t101\t0,0,0\t10\t80.0\t8\t2\t0\t0\t0\t0\t0\n"
        )
        temporary, fixture, outdir, _runner, first = self._run_fixture(
            pileup, gpu=False
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(first["overall_status"], "complete")
        request = resolve_methylation_request(fixture.config(), mode="ont")
        assert request is not None
        runner = FakeRunner(pileup)
        second = run_methylation(
            ROOT,
            request,
            [OntSample("SNC_F", "barcode01", fixture.fastq)],
            {
                "fasta": fixture.root / "genome.fa",
                "fai": fixture.root / "genome.fa.fai",
            },
            outdir,
            runner,  # type: ignore[arg-type]
            StageLedger(outdir / ".oncotracer-native" / "state.json"),
            threads=4,
            force=False,
        )
        self.assertEqual(second["overall_status"], "complete")
        self.assertEqual(runner.calls, [])

    def test_pod5_inventory_change_during_basecalling_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            request = resolve_methylation_request(fixture.config(), mode="ont")
            assert request is not None
            fasta = fixture.root / "genome.fa"
            fai = fixture.root / "genome.fa.fai"
            fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
            fai.write_text("chr1\t4\t6\t4\t5\n", encoding="utf-8")
            outdir = fixture.root / "result"
            runner = FakeRunner(
                "",
                mutate_pod5=fixture.pod5 / "batch.pod5",
            )
            with self.assertRaisesRegex(OncoTracerError, "POD5 inventory changed"):
                run_methylation(
                    ROOT,
                    request,
                    [OntSample("SNC_F", "barcode01", fixture.fastq)],
                    {"fasta": fasta, "fai": fai},
                    outdir,
                    runner,  # type: ignore[arg-type]
                    StageLedger(outdir / ".oncotracer-native" / "state.json"),
                    threads=4,
                    force=False,
                )

    def test_methylation_dry_run_has_no_output_and_precedes_cna(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            config = fixture.config()
            config_path = fixture.root / "run.yml"
            config_path.write_text(render_flat_yaml(config), encoding="utf-8")
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = run_native(config_path, root=ROOT, dry_run=True)
            self.assertEqual(result, Path(str(config["outdir"])).resolve())
            self.assertFalse(result.exists())
            plan = json.loads(output.getvalue())
            self.assertEqual(plan["methylation"]["pod5_file_count"], 1)
            stages = plan["stages"]
            self.assertLess(
                stages.index("dorado-modified-base-basecalling"),
                stages.index("hmmcopy-ichorcna"),
            )

    def test_container_backends_reject_unbundled_methylation_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "run.yml"
            config.write_text("mode: ont\noutdir: /tmp/out\n", encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "run",
                    "--config",
                    str(config),
                    "--backend",
                    "docker",
                    "--methylation",
                    "--sturgeon",
                    "--pod5-dir",
                    "/explicit/pod5",
                ]
            )
            with self.assertRaisesRegex(OncoTracerError, "requires backend host"):
                execute_run(config, args)

    def test_cna_and_methylation_failures_are_reported_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outdir = Path(directory) / "out"
            summary_dir = outdir / "06_workflow_summary"
            summary_dir.mkdir(parents=True)
            (summary_dir / "workflow_summary.json").write_text(
                json.dumps({"workflow_status": "complete"}), encoding="utf-8"
            )
            no_cpg = {
                "overall_status": "no_cpg_modifications",
                "classifier": "sturgeon",
                "completed_samples": [],
                "failed_samples": [],
                "no_cpg_samples": ["SNC_F"],
            }
            merged = _merge_methylation_summary(outdir, no_cpg, cna_error=None)
            self.assertEqual(merged["cna_status"], "complete")
            self.assertEqual(merged["workflow_status"], "partial_failure")

            completed = {
                "overall_status": "complete",
                "classifier": "sturgeon",
                "completed_samples": ["SNC_F"],
                "failed_samples": [],
                "no_cpg_samples": [],
            }
            merged = _merge_methylation_summary(
                outdir,
                completed,
                cna_error=OncoTracerError(
                    "failure in /protected/patient-secret/file"
                ),
            )
            self.assertEqual(merged["cna_status"], "failed")
            self.assertEqual(merged["methylation_status"], "complete")
            self.assertEqual(merged["workflow_status"], "partial_failure")
            self.assertNotIn("patient-secret", str(merged["cna_error"]))


if __name__ == "__main__":
    unittest.main()
