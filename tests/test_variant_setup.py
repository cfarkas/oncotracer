"""Variant selections must round-trip through setup without running caller tools."""
import contextlib
import gzip
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import build_parser, main
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml
from oncotracer_cli.web import WebState
from tests import test_wizard as wizard_tests

HARDWARE = wizard_tests.HARDWARE


class VariantSetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.reads = self.root / "reads"
        self.reads.mkdir()
        self.fastq = self.reads / "case.fastq.gz"
        with gzip.open(self.fastq, "wt") as handle:
            handle.write("@read\nACGT\n+\nIIII\n")
        self.project = self.root / "project"
        self.addCleanup(patch.stopall)
        patch("oncotracer_cli.cli._load_install_config", return_value={}).start()

    def cli(self, *flags):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["setup", "--non-interactive", "--mode", "illumina", "--project", str(self.project),
                         "--sample-name", "case", "--fastq-1", str(self.fastq), *flags])
        return code, output.getvalue()

    def test_ffpe_multiple_illumina_callers_roundtrip_and_dry_run(self):
        bed = self.root / "targets.bed"
        bed.write_text("chr1\t100\t200\n")
        code, output = self.cli("--variants", "--variant-specimen-type", "ffpe",
                                "--variant-callers", "mutect2,freebayes,bcftools",
                                "--variant-annovar", "off", "--variant-targets-bed", str(bed))
        self.assertEqual(code, 0, output)
        config = load_flat_yaml(self.project / "config/run.yml")
        self.assertIs(config["run_variants"], True)
        self.assertEqual(config["variant_specimen_type"], "ffpe")
        self.assertEqual(config["variant_callers"], "mutect2,freebayes,bcftools")
        self.assertEqual(config["variant_targets_bed"], str(bed))
        self.assertEqual(config["variant_annovar"], "off")
        self.assertFalse((self.project / "results").exists())
        self.assertFalse((self.project / "reference").exists())

    def test_missing_preservation_bad_platform_and_disabled_options_write_nothing(self):
        for flags, message in [
            (("--variants",), "variant-specimen-type"),
            (("--variants", "--variant-specimen-type", "fresh", "--variant-callers", "clair3"), "Variant callers for illumina"),
            (("--variant-specimen-type", "ffpe"), "require --variants"),
            (("--variants", "--variant-specimen-type", "fresh", "--variant-callers", "bcftools,bcftools"), "only once"),
            (("--variants", "--variant-specimen-type", "fresh", "--backend", "singularity"), "backend"),
        ]:
            with self.subTest(flags=flags):
                code, output = self.cli(*flags)
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)
                self.assertFalse(self.project.exists())

    def state(self):
        state = WebState(self.root)
        state.hardware = HARDWARE
        return state

    def prepare(self, state, *, mode="illumina", **options):
        scan = state.scan({"mode": mode, "folder": str(self.reads)})
        data = {"scan_id": scan["scan_id"], "project": str(self.project), "threads": 2,
                "samples": [{"id": sample["id"], "name": sample["name"], "type": "cancer"}
                            for sample in scan["samples"]]}
        data.update(options)
        with contextlib.redirect_stdout(io.StringIO()):
            return state.prepare(data)

    def test_web_fresh_bcftools_real_check_keeps_reads_and_does_not_start_analysis(self):
        before = self.fastq.read_bytes()
        result = self.prepare(self.state(), variants=True, variant_specimen_type="fresh",
                              variant_callers="bcftools", variant_annovar="auto")
        self.assertTrue(result["valid"], result["check"])
        config = load_flat_yaml(Path(result["config_path"]))
        self.assertEqual(config["variant_specimen_type"], "fresh")
        self.assertEqual(config["variant_callers"], "bcftools")
        self.assertEqual(config["variant_annovar"], "auto")
        self.assertEqual(self.fastq.read_bytes(), before)
        self.assertFalse((self.project / "results").exists())
        self.assertFalse((self.project / "reference").exists())

    def test_docker_browser_config_checks_without_host_tool_prefix_and_retains_image(self):
        result = self.prepare(self.state(), backend="docker", docker_image="oncotracer:local-test",
                              variants=True, variant_specimen_type="fresh", variant_callers="bcftools",
                              variant_tool_prefix="/opt/oncotracer-envs/variants", variant_annovar="off")
        self.assertTrue(result["valid"], result["check"])
        config = load_flat_yaml(Path(result["config_path"]))
        self.assertEqual(config["execution_backend"], "docker")
        self.assertEqual(config["docker_image"], "oncotracer:local-test")
        self.assertEqual(config["variant_tool_prefix"], "/opt/oncotracer-envs/variants")
        self.assertFalse((self.project / "results").exists())
        self.assertFalse((self.project / ".oncotracer").exists())
        self.assertEqual(result["backend"], "docker")

    def test_docker_rejects_nested_sif_and_still_checks_external_resources(self):
        for options, message in [
            ({"variant_ffperase_sif": str(self.root / "ffpe.sif")}, "clear variant_ffperase_sif"),
            ({"variant_targets_bed": str(self.root / "missing.bed")}, "Variant target BED"),
            ({"analysis": "both"}, "choose cna"),
            ({"variant_ffperase_models": str(self.root / "missing-models")}, "variant_ffperase_models"),
        ]:
            with self.subTest(options=options), self.assertRaisesRegex(OncoTracerError, message):
                self.prepare(self.state(), backend="docker", variants=True,
                             variant_specimen_type="fresh", variant_callers="bcftools", **options)
            self.assertFalse(self.project.exists())

    def test_docker_image_prefill_and_non_docker_rejection(self):
        args = build_parser().parse_args(["setup", "--backend", "docker", "--image", "oncotracer:local-test"])
        self.assertEqual(WebState(self.root, args).system()["defaults"]["image"], "oncotracer:local-test")
        with self.assertRaisesRegex(OncoTracerError, "requires the Docker backend"):
            self.prepare(self.state(), backend="host", docker_image="oncotracer:local-test")
        code, output = self.cli("--backend", "host", "--image", "oncotracer:local-test")
        self.assertEqual(code, 2, output)
        self.assertIn("requires --backend docker", output)
        self.assertFalse(self.project.exists())

    def test_docker_resume_reuses_saved_backend_image_and_installed_docker(self):
        code, output = self.cli("--backend", "docker", "--image", "oncotracer:local-test", "--variants",
                                "--variant-specimen-type", "fresh", "--variant-callers", "bcftools",
                                "--variant-tool-prefix", "/opt/oncotracer-envs/variants", "--variant-annovar", "off")
        self.assertEqual(code, 0, output)
        with patch("oncotracer_cli.setup.shutil.which", return_value="/usr/bin/docker"), \
             patch("oncotracer_cli.cli.command_install") as installer, \
             patch("oncotracer_cli.cli.command_run", return_value=0) as runner, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup", "--project", str(self.project), "--run"]), 0)
        installer.assert_not_called()
        self.assertEqual(runner.call_args.args[0].backend, "docker")
        self.assertEqual(runner.call_args.args[0].image, "oncotracer:local-test")

    def test_web_rejects_untyped_or_platform_incompatible_variant_requests(self):
        for options, message in [
            ({"variants": "yes"}, "true or false"),
            ({"variants": True, "variant_specimen_type": False}, "Provide variant specimen type"),
            ({"variants": True, "variant_specimen_type": "fresh", "variant_callers": ["bcftools"]}, "Provide variant callers"),
            ({"variants": True, "variant_specimen_type": "ffpe", "variant_callers": "clairs_to"}, "Variant callers for illumina"),
            ({"variants": False, "variant_callers": "bcftools"}, "require --variants"),
        ]:
            with self.subTest(options=options), self.assertRaisesRegex(OncoTracerError, message):
                self.prepare(self.state(), **options)
            self.assertFalse(self.project.exists())

    def test_web_ont_clair3_requires_explicit_model(self):
        with self.assertRaisesRegex(OncoTracerError, "variant-clair3-model"):
            self.prepare(self.state(), mode="ont", variants=True, variant_specimen_type="fresh", variant_callers="clair3")
        self.assertFalse(self.project.exists())

    def test_methylation_only_plus_variants_rejected_before_resource_prompts(self):
        with self.assertRaisesRegex(OncoTracerError, "CNA or CNA and methylation"):
            self.prepare(self.state(), mode="ont", analysis="methylation", classifier="marlin",
                         methylation_source="modbam", methylation_path=str(self.root / "calls.bam"),
                         variants=True, variant_specimen_type="fresh", variant_callers="clairs_to",
                         variant_clairsto_platform="ont_r10_fixture")
        self.assertFalse(self.project.exists())

    def test_existing_bam_config_routes_to_browser_without_running_analysis(self):
        config = self.root / "variants.yml"
        with patch("oncotracer_cli.web.command_web", return_value=0) as browser:
            self.assertEqual(main(["setup", "--variant-config", str(config), "--no-browser"]), 0)
        args = browser.call_args.args[0]
        self.assertEqual(args.variant_config, str(config))
        self.assertTrue(args.no_browser)
        self.assertFalse(self.project.exists())

    def test_existing_bam_browser_rejects_conflicting_setup_modes(self):
        for flags in (("--terminal",), ("--run",), ("--non-interactive",),
                      ("--manual",), ("--input-folder", str(self.reads))):
            with self.subTest(flags=flags), patch("oncotracer_cli.web.command_web") as browser:
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    code = main(["setup", "--variant-config", "variants.yml", *flags])
                self.assertEqual(code, 2)
                browser.assert_not_called()
                self.assertIn("existing-BAM browser form", output.getvalue())

    def test_setup_flags_prefill_web_variant_fields(self):
        args = build_parser().parse_args(["setup", "--mode", "illumina", "--variants",
            "--variant-specimen-type", "ffpe", "--variant-callers", "mutect2,bcftools",
            "--variant-annovar", "off", "--variant-targets-bed", str(self.root / "targets.bed")])
        state = WebState(self.root, args)
        state.hardware = HARDWARE
        defaults = state.system()["defaults"]
        self.assertIs(defaults["variants"], True)
        for field in ("variant_specimen_type", "variant_callers", "variant_annovar", "variant_targets_bed"):
            self.assertEqual(defaults[field], getattr(args, field))

    def test_web_ont_clairsto_existing_sif_roundtrip_and_prefill(self):
        image = self.root / "caller.sif"
        image.write_bytes(b"fixture-container-not-executed")
        args = build_parser().parse_args(["setup", "--mode", "ont", "--variants",
            "--variant-specimen-type", "fresh", "--variant-callers", "clairs_to",
            "--variant-clairsto-platform", "ont_r10_fixture", "--variant-clairsto-sif", str(image)])
        state = WebState(self.root, args)
        state.hardware = HARDWARE
        self.assertEqual(state.system()["defaults"]["variant_clairsto_sif"], str(image))
        self.assertIn(str(image), [row["path"] for row in state.browse(self.root, "asset")["files"]])
        result = self.prepare(state, mode="ont", variants=True, variant_specimen_type="fresh",
            variant_callers="clairs_to", variant_clairsto_platform="ont_r10_fixture",
            variant_clairsto_sif=str(image), variant_annovar="off")
        self.assertTrue(result["valid"], result["check"])
        config = load_flat_yaml(Path(result["config_path"]))
        self.assertEqual(config["variant_clairsto_sif"], str(image))
        self.assertEqual(config["variant_callers"], "clairs_to")
        self.assertFalse((self.project / "results").exists())

    def test_clairsto_sif_rejects_missing_file_or_wrong_caller(self):
        image = self.root / "missing.sif"
        with self.assertRaisesRegex(OncoTracerError, "ClairS-TO SIF image"):
            self.prepare(self.state(), mode="ont", variants=True, variant_specimen_type="fresh",
                variant_callers="clairs_to", variant_clairsto_platform="ont_r10_fixture",
                variant_clairsto_sif=str(image))
        self.assertFalse(self.project.exists())
        image.write_bytes(b"fixture-container-not-executed")
        code, output = self.cli("--variants", "--variant-specimen-type", "fresh",
            "--variant-callers", "bcftools", "--variant-clairsto-sif", str(image))
        self.assertEqual(code, 2, output)
        self.assertIn("requires selecting the clairs_to", output)
        self.assertFalse(self.project.exists())
        code, output = self.cli("--variant-clairsto-sif", str(image))
        self.assertEqual(code, 2, output)
        self.assertIn("require --variants", output)
        self.assertFalse(self.project.exists())

    def test_terminal_wizard_accepts_fresh_and_caller_options(self):
        # Use the established prompt harness without inheriting/rerunning its suite.
        harness = wizard_tests.WizardTests()
        code, output, prompts, run = harness.invoke("setup", "--terminal", "--project", str(self.project),
            "--mode", "illumina", "--input-folder", str(self.reads), answers={
                "Type for": "cancer", "Add small-variant": "yes", "Sample preservation": "fresh",
                "Variant callers": "bcftools", "ANNOVAR annotation": "off"})
        self.assertEqual(code, 0, output)
        run.assert_not_called()
        config = load_flat_yaml(self.project / "config/run.yml")
        self.assertIs(config["run_variants"], True)
        self.assertEqual(config["variant_specimen_type"], "fresh")
        self.assertEqual(config["variant_callers"], "bcftools")
        self.assertIn("Small variants: bcftools; preservation: fresh", output)
        self.assertTrue(any(prompt.startswith("Final action") for prompt in prompts))


if __name__ == "__main__":
    unittest.main()
