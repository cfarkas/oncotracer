#!/usr/bin/env python3
"""Behavioral checks for native variant calls using synthetic, nonpatient data."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli import annovar, variants  # noqa: E402
from oncotracer_cli.runtime import CommandRunner, OncoTracerError, StageLedger  # noqa: E402


class SyntheticInput:
    """Small coordinate-sorted BAM with a known heterozygous SNP, or no SNP."""

    def __init__(self, root: Path, *, alternate: bool = True):
        self.root = root
        self.inputs = root / "read only source"
        self.inputs.mkdir()
        self.outdir = root / "results"
        self.outdir.mkdir()
        self.reference = self.inputs / "synthetic.fa"
        rng = random.Random(2047)
        sequence = "".join(rng.choice("ACGT") for _ in range(500))
        self.position = 120
        sequence = sequence[:119] + "C" + sequence[120:]
        sequence = sequence[:249] + "C" + "A" * 10 + sequence[260:]
        self.ref_base = sequence[self.position - 1]
        self.alt_base = "T"
        self.reference.write_text(f">chr1\n{sequence}\n", encoding="utf-8")
        sam = self.inputs / "synthetic.sam"
        lines = [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:500",
            "@RG\tID:synthetic\tSM:FIXTURE\tPL:ILLUMINA\tLB:synthetic\tPU:synthetic",
        ]
        for i in range(40):
            start0 = 45 + i
            read = list(sequence[start0:start0 + 150])
            if alternate and i % 2 == 0:
                read[self.position - 1 - start0] = self.alt_base
            lines.append("\t".join([
                f"synthetic_{i:03d}", "0", "chr1", str(start0 + 1), "60", "150M",
                "*", "0", "0", "".join(read), "I" * 150, "RG:Z:synthetic",
            ]))
        sam.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.bam = self.inputs / "synthetic.bam"
        subprocess.run([shutil.which("samtools"), "view", "-b", "-o", str(self.bam), str(sam)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Neither user BAM nor reference has an index: the implementation must stage them.
        self.input_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in self.inputs.iterdir() if p.is_file()}
        self.runner = CommandRunner(self.outdir / "trace.tsv", echo=False)
        self.ledger = StageLedger(self.outdir / "ledger.json")

    def assert_unchanged(self, testcase: unittest.TestCase) -> None:
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.inputs.iterdir() if p.is_file()}
        testcase.assertEqual(self.input_before, after)


def vcf_rows(path: Path) -> list[dict[str, object]]:
    opener = gzip.open if path.name.endswith(".gz") else open
    rows = []
    with opener(path, "rt") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            genotype = dict(zip(fields[8].split(":"), fields[9].split(":"))) if len(fields) >= 10 else {}
            rows.append({"key": (fields[0], int(fields[1]), fields[3], fields[4]),
                         "filter": fields[6], "genotype": genotype})
    return rows


def request_config(**updates: object) -> dict[str, object]:
    config = {"run_variants": True, "variant_specimen_type": "fresh",
              "variant_callers": "bcftools", "variant_annovar": "off"}
    config.update(updates)
    return config


class VariantRequestTests(unittest.TestCase):
    def test_disabled_request_does_not_require_tools_or_resources(self):
        with patch.object(shutil, "which", return_value=None):
            self.assertIsNone(variants.resolve_variant_request({}, mode="illumina"))
            self.assertIsNone(variants.resolve_variant_request({"run_variants": False}, mode="ont"))

    def test_preset_plan_does_not_require_installed_callers(self):
        with patch.object(shutil, "which", return_value=None):
            request = variants.resolve_variant_request(request_config(), mode="illumina")
            self.assertEqual(request.specimen_type, "fresh")
            self.assertEqual(request.callers, ("bcftools",))
            self.assertIsInstance(variants.variant_plan(request), dict)

    def test_missing_or_invalid_preservation_is_rejected(self):
        for value in ("", "unknown", "frozen", "FFPE,fresh"):
            with self.subTest(value=value), self.assertRaises(OncoTracerError):
                variants.resolve_variant_request(request_config(variant_specimen_type=value), mode="illumina")

    def test_platform_caller_mismatch_is_rejected(self):
        for mode, caller in (("illumina", "clair3"), ("illumina", "clairs_to"),
                             ("ont", "mutect2"), ("ont", "freebayes"), ("ont", "bcftools")):
            with self.subTest(mode=mode, caller=caller), self.assertRaises(OncoTracerError):
                variants.resolve_variant_request(request_config(variant_callers=caller), mode=mode)

    def test_unknown_caller_is_rejected(self):
        with self.assertRaises(OncoTracerError):
            variants.resolve_variant_request(request_config(variant_callers="not_a_caller"), mode="illumina")

    def test_ont_callers_require_their_model_or_platform(self):
        for caller in ("clair3", "clairs_to"):
            with self.subTest(caller=caller), self.assertRaises(OncoTracerError):
                variants.resolve_variant_request(request_config(variant_callers=caller), mode="ont")

    def test_missing_tools_fail_preflight(self):
        request = variants.resolve_variant_request(request_config(), mode="illumina")
        with patch.object(shutil, "which", return_value=None), self.assertRaises(OncoTracerError):
            variants.preflight_variant_tools(request)


class NativeCallerCommandTests(unittest.TestCase):
    def exercise(self, caller: str, *, specimen="fresh"):
        root_context = tempfile.TemporaryDirectory(prefix="oncotracer-caller-contract-")
        self.addCleanup(root_context.cleanup)
        root = Path(root_context.name)
        directory = root / "caller output"
        directory.mkdir()
        model = root / "model with spaces"
        model.mkdir()
        (model / "checkpoint").write_text("synthetic-model-not-executed")
        request = variants.resolve_variant_request(request_config(
            variant_specimen_type=specimen, variant_callers=caller,
            variant_clair3_model=str(model), variant_clairsto_platform="ont_r10_dorado_sup_5khz"),
            mode="ont" if caller in {"clair3", "clairs_to"} else "illumina")
        class RecordRunner:
            def __init__(self):
                self.calls = []
            def run(self, stage, command, **kwargs):
                self.calls.append((stage, [str(x) for x in command]))
        runner = RecordRunner()
        tools = {tool: f"/synthetic/bin/{tool}" for tool in
                 ("samtools", "bcftools", "gatk", "freebayes", "clair3", "clairs_to")}
        bam, ref, bed = root/"sample input.bam", root/"reference.fa", root/"targets.bed"
        with patch.object(variants, "validate_vcf", return_value=1), patch.object(variants, "_evidence", return_value=1):
            variants._call(request, caller, "FIXTURE", bam, ref, directory,
                           tools, runner, 3, {}, bed)
        return request, runner.calls, bam, ref, bed

    def test_mutect2_keeps_orientation_model_and_filter_in_both_modes(self):
        for specimen in ("fresh", "ffpe"):
            with self.subTest(specimen=specimen):
                _, calls, bam, ref, bed = self.exercise("mutect2", specimen=specimen)
                commands = [argv for _, argv in calls]
                mutect = next(c for c in commands if "Mutect2" in c)
                self.assertEqual(mutect[mutect.index("-I")+1], str(bam))
                self.assertEqual(mutect[mutect.index("-R")+1], str(ref))
                self.assertEqual(mutect[mutect.index("-L")+1], str(bed))
                self.assertEqual(mutect[mutect.index("--native-pair-hmm-threads")+1], "3")
                self.assertIn("--f1r2-tar-gz", mutect)
                self.assertTrue(any("LearnReadOrientationModel" in c for c in commands))
                filtered = next(c for c in commands if "FilterMutectCalls" in c)
                self.assertIn("--stats", filtered)
                self.assertTrue("--ob-priors" in filtered or "--orientation-bias-artifact-priors" in filtered)
                self.assertFalse(any("nextflow" in str(c) or "ffperase" in str(c).lower() for c in commands))

    def test_freebayes_uses_explicit_evidence_thresholds_and_target_bed(self):
        request, calls, bam, _, bed = self.exercise("freebayes")
        command = next(argv for _, argv in calls if argv[0].endswith("/freebayes"))
        self.assertEqual(command[command.index("--min-alternate-count")+1], str(request.min_alt_count))
        self.assertEqual(command[command.index("--min-alternate-fraction")+1], str(request.min_alt_fraction))
        self.assertEqual(command[command.index("-t")+1], str(bed))
        self.assertEqual(command[-1], str(bam))

    def test_clair3_receives_explicit_model_and_ont_platform(self):
        request, calls, bam, ref, bed = self.exercise("clair3")
        command = next(argv for _, argv in calls if argv[0].endswith("/clair3"))
        for value in [f"--bam_fn={bam}", f"--ref_fn={ref}", "--platform=ont",
                      f"--model_path={request.clair3_model}", f"--bed_fn={bed}", "--sample_name=FIXTURE"]:
            self.assertIn(value, command)

    def test_clairsto_keeps_tumor_only_command_and_merges_both_variant_types(self):
        request, calls, bam, ref, bed = self.exercise("clairs_to")
        command = next(argv for _, argv in calls if argv[0].endswith("/clairs_to"))
        self.assertEqual(command[command.index("--tumor_bam_fn")+1], str(bam))
        self.assertEqual(command[command.index("--ref_fn")+1], str(ref))
        self.assertEqual(command[command.index("--platform")+1], request.clairsto_platform)
        self.assertEqual(command[command.index("--bed_fn")+1], str(bed))
        self.assertNotIn("--normal_bam_fn", command)
        concat = next(argv for _, argv in calls if "concat" in argv)
        self.assertTrue(any(v.endswith("snv.vcf.gz") for v in concat))
        self.assertTrue(any(v.endswith("indel.vcf.gz") for v in concat))


@unittest.skipUnless(shutil.which("samtools") and shutil.which("bcftools"),
                     "samtools and bcftools are required for synthetic integration")
class NativeVariantIntegrationTests(unittest.TestCase):
    def run_fixture(self, fixture: SyntheticInput, *, specimen_type="fresh", runner=None, annotation="off", force=False):
        request = variants.resolve_variant_request(
            request_config(variant_specimen_type=specimen_type, variant_annovar=annotation, variant_ffperase="off"), mode="illumina")
        result = variants.run_variants(
            request, {"FIXTURE": fixture.bam}, fixture.reference,
            fixture.outdir, runner or fixture.runner, fixture.ledger,
            threads=1, force=force, sample_statuses={"FIXTURE": "normal"})
        return result

    def test_real_bcftools_call_preserves_native_genotype_and_inputs(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-") as directory:
            fixture = SyntheticInput(Path(directory))
            result = self.run_fixture(fixture)
            self.assertEqual(result["overall_status"], "complete")
            fixture.assert_unchanged(self)
            vcfs = list((fixture.outdir / "08_variants").rglob("*.vcf.gz"))
            self.assertTrue(vcfs, "successful branch must publish a VCF")
            key = ("chr1", fixture.position, fixture.ref_base, fixture.alt_base)
            matching = [(p, r) for p in vcfs for r in vcf_rows(p) if r["key"] == key]
            self.assertTrue(matching, "synthetic SNP must survive calling and normalization")
            for path, row in matching:
                with self.subTest(path=path):
                    self.assertEqual(row["genotype"].get("GT"), "0/1")
                    self.assertIn("PL", row["genotype"], "caller likelihoods must survive normalization")
            trace = (fixture.outdir / "trace.tsv").read_text()
            with (fixture.outdir / "trace.tsv").open() as handle:
                trace_rows = list(csv.DictReader(handle, delimiter="\t"))
            index_rows = [r for r in trace_rows if r.get("stage", "").endswith("-index")]
            self.assertTrue(index_rows, "private BAM index command must be recorded")
            self.assertTrue(any("08_variants" in str(r) for r in index_rows))
            self.assertIn("mpileup", trace)
            self.assertIn("norm", trace)
            self.assertNotIn("nextflow", trace.lower())
            self.assertNotIn("FFPErase", trace)

    def test_successful_zero_variant_vcf_is_complete(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-empty-") as directory:
            fixture = SyntheticInput(Path(directory), alternate=False)
            result = self.run_fixture(fixture)
            self.assertEqual(result["overall_status"], "complete")
            vcfs = list((fixture.outdir / "08_variants").rglob("*.vcf.gz"))
            self.assertTrue(vcfs)
            for path in vcfs:
                self.assertEqual(vcf_rows(path), [])
            fixture.assert_unchanged(self)

    def test_ffpe_deamination_is_annotation_and_does_not_change_genotype(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-ffpe-") as directory:
            fixture = SyntheticInput(Path(directory))
            result = self.run_fixture(fixture, specimen_type="ffpe")
            self.assertEqual(result["overall_status"], "complete")
            tagged = []
            for path in (fixture.outdir / "08_variants").rglob("*.vcf.gz"):
                with gzip.open(path, "rt") as handle:
                    records = [line for line in handle if not line.startswith("#")]
                tagged.extend(line for line in records if "OC_FFPE_DEAMINATION" in line)
            self.assertTrue(tagged, "C>T in FFPE should be explicitly annotated")
            for line in tagged:
                fields = line.rstrip().split("\t")
                self.assertNotIn("OC_FFPE_DEAMINATION", fields[6], "review annotation must not become a FILTER")
                gt = dict(zip(fields[8].split(":"), fields[9].split(":")))["GT"]
                self.assertEqual(gt, "0/1")
            fixture.assert_unchanged(self)

    def test_indel_is_left_aligned_without_replacing_gt_from_allele_fraction(self):
        class RightShiftedCaller(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                if len(argv) < 2 or argv[1] != "call":
                    return super().run(stage, command, **kwargs)
                output = Path(argv[argv.index("-o") + 1])
                text = output.with_suffix(".fixture.vcf")
                text.write_text(
                    "##fileformat=VCFv4.2\n"
                    "##contig=<ID=chr1,length=500>\n"
                    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
                    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
                    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depth">\n'
                    '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele fraction">\n'
                    '##FORMAT=<ID=PL,Number=G,Type=Integer,Description="Genotype likelihoods">\n'
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFIXTURE\n"
                    "chr1\t255\t.\tAA\tA\t60\tPASS\t.\tGT:DP:AD:AF:PL\t1|0:40:2,38:0.95:120,0,130\n")
                return super().run(stage + "-synthetic-record", [
                    shutil.which("bcftools"), "view", "-Oz", "-o", output, text])

        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-norm-") as directory:
            fixture = SyntheticInput(Path(directory))
            runner = RightShiftedCaller(fixture.outdir / "normalization_trace.tsv", echo=False)
            result = self.run_fixture(fixture, runner=runner)
            self.assertEqual(result["overall_status"], "complete")
            left_aligned = [row for path in (fixture.outdir / "08_variants").rglob("*.vcf.gz")
                            for row in vcf_rows(path) if row["key"] == ("chr1", 250, "CA", "C")]
            self.assertTrue(left_aligned, "repeat-associated deletion must be left-aligned")
            for row in left_aligned:
                self.assertEqual(row["genotype"]["GT"], "1|0")
                self.assertEqual(row["genotype"]["PL"], "120,0,130")
                self.assertEqual(float(row["genotype"]["AF"]), 0.95)
            fixture.assert_unchanged(self)

    def test_resume_skips_calling_after_validated_success(self):
        class NoSecondCall(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                if len(argv) > 1 and argv[1] in {"mpileup", "call"}:
                    raise AssertionError("unchanged successful caller should resume")
                return super().run(stage, command, **kwargs)
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-resume-") as directory:
            fixture = SyntheticInput(Path(directory))
            self.assertEqual(self.run_fixture(fixture)["overall_status"], "complete")
            runner = NoSecondCall(fixture.outdir / "resume_trace.tsv", echo=False)
            result = self.run_fixture(fixture, runner=runner)
            self.assertEqual(result["overall_status"], "complete")
            self.assertTrue(result["samples"][0]["callers"][0]["resumed"])
            fixture.assert_unchanged(self)

    def test_modified_published_output_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-edited-") as directory:
            fixture = SyntheticInput(Path(directory))
            first = self.run_fixture(fixture)
            evidence = Path(first["samples"][0]["callers"][0]["evidence"])
            edited = evidence.read_bytes() + b"# user review kept here\n"
            evidence.write_bytes(edited)
            second = self.run_fixture(fixture)
            self.assertEqual(second["overall_status"], "failed")
            self.assertEqual(evidence.read_bytes(), edited)

    def test_caller_vcf_with_unrelated_sample_identity_is_rejected(self):
        class WrongSampleCaller(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                result = super().run(stage, command, **kwargs)
                if len(argv) > 1 and argv[1] == "call":
                    raw = Path(argv[argv.index("-o") + 1])
                    names = raw.parent / "wrong_sample_names.txt"
                    names.write_text("UNRELATED_SAMPLE\n")
                    changed = raw.parent / "wrong_sample.vcf.gz"
                    super().run(stage + "-wrong-sample", [shutil.which("bcftools"), "reheader",
                                 "-s", names, "-o", changed, raw])
                    changed.replace(raw)
                return result
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-sample-") as directory:
            fixture = SyntheticInput(Path(directory))
            runner = WrongSampleCaller(fixture.outdir / "wrong_sample_trace.tsv", echo=False)
            result = self.run_fixture(fixture, runner=runner)
            self.assertEqual(result["overall_status"], "failed")
            self.assertIn("sample", json.dumps(result).lower())

    def test_newly_available_annotation_invalidates_previous_unannotated_resume(self):
        class SyntheticAnnotation(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                if argv[0] != "synthetic_annovar":
                    return super().run(stage, command, **kwargs)
                source, prefix = Path(argv[1]), Path(argv[2])
                Path(str(prefix) + ".hg38_multianno.txt").write_text("Chr\tStart\tGene.refGene\nchr1\t120\tSYNTHETIC\n")
                with gzip.open(source, "rt") as handle:
                    Path(str(prefix) + ".hg38_multianno.vcf").write_text(handle.read())

        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-annotation-") as directory:
            fixture = SyntheticInput(Path(directory))
            unavailable = annovar.AnnovarDetection(False, "Synthetic installation absent", "hg38")
            with patch.object(annovar, "discover_annovar", return_value=unavailable):
                first = self.run_fixture(fixture, annotation="auto")
            self.assertEqual(first["overall_status"], "complete")
            self.assertEqual(first["samples"][0]["callers"][0]["annotation"]["status"], "skipped")
            asset = Path(directory) / "synthetic_database.txt"
            asset.write_text("synthetic annotation resource")
            available = annovar.AnnovarDetection(True, "Synthetic installation available", "hg38", files=(asset,))
            runner = SyntheticAnnotation(fixture.outdir / "annotation_trace.tsv", echo=False)
            with patch.object(annovar, "discover_annovar", return_value=available), patch.object(
                    annovar, "build_annovar_command", side_effect=lambda detection, vcf, prefix: ["synthetic_annovar", vcf, prefix]):
                second = self.run_fixture(fixture, annotation="auto", runner=runner)
            self.assertEqual(second["overall_status"], "complete")
            self.assertEqual(second["samples"][0]["callers"][0]["annotation"]["status"], "complete")
            self.assertTrue(list((fixture.outdir / "08_variants").rglob("*.hg38_multianno.vcf")))
            fixture.assert_unchanged(self)

    def test_annotation_cannot_change_genotype_or_replace_primary_evidence(self):
        class ChangedGenotypeAnnotation(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                if argv[0] != "synthetic_bad_annotation":
                    return super().run(stage, command, **kwargs)
                source, prefix = Path(argv[1]), Path(argv[2])
                Path(str(prefix) + ".hg38_multianno.txt").write_text("Chr\tStart\tGene.refGene\nchr1\t120\tSYNTHETIC\n")
                with gzip.open(source, "rt") as handle:
                    lines = []
                    for line in handle:
                        if not line.startswith("#"):
                            fields = line.rstrip("\n").split("\t")
                            fmt = fields[8].split(":")
                            values = fields[9].split(":")
                            values[fmt.index("GT")] = "1/1"
                            fields[9] = ":".join(values)
                            line = "\t".join(fields) + "\n"
                        lines.append(line)
                Path(str(prefix) + ".hg38_multianno.vcf").write_text("".join(lines))

        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-bad-annotation-") as directory:
            fixture = SyntheticInput(Path(directory))
            available = annovar.AnnovarDetection(True, "Synthetic installation", "hg38")
            runner = ChangedGenotypeAnnotation(fixture.outdir / "bad_annotation_trace.tsv", echo=False)
            with patch.object(annovar, "discover_annovar", return_value=available), patch.object(
                    annovar, "build_annovar_command", side_effect=lambda detection, vcf, prefix: ["synthetic_bad_annotation", vcf, prefix]):
                result = self.run_fixture(fixture, annotation="auto", runner=runner)
            self.assertEqual(result["overall_status"], "partial_failure")
            caller = result["samples"][0]["callers"][0]
            self.assertEqual(caller["annotation"]["status"], "failed")
            primary = vcf_rows(Path(caller["vcf"]))
            self.assertEqual(primary[0]["genotype"]["GT"], "0/1")
            self.assertFalse(list((fixture.outdir / "08_variants").rglob("*.hg38_multianno.vcf")))
            fixture.assert_unchanged(self)

    def test_failed_publication_restores_previous_artifacts_and_completion(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-transaction-") as directory:
            fixture = SyntheticInput(Path(directory))
            first = self.run_fixture(fixture)
            caller = first["samples"][0]["callers"][0]
            destination = Path(caller["vcf"]).parent
            before = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
            original_replace = variants.os.replace
            injected = False
            def fail_one_publication(source, target):
                nonlocal injected
                target = Path(target)
                if not injected and target.parent == destination and target.name == "evidence.tsv":
                    injected = True
                    raise OSError("synthetic publication interruption")
                return original_replace(source, target)
            with patch.object(variants.os, "replace", side_effect=fail_one_publication):
                second = self.run_fixture(fixture, force=True)
            self.assertTrue(injected, "test must interrupt artifact publication")
            self.assertEqual(second["overall_status"], "failed")
            after = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
            self.assertEqual(after, before, "prior generation, including completion manifest, must be intact")
            self.assertIn("complete.json", after)
            self.assertEqual(vcf_rows(Path(caller["vcf"]))[0]["genotype"]["GT"], "0/1")
            fixture.assert_unchanged(self)

    def test_empty_success_does_not_invoke_available_annovar(self):
        with tempfile.TemporaryDirectory(prefix="oncotracer-empty-annotation-") as directory:
            fixture = SyntheticInput(Path(directory), alternate=False)
            available = annovar.AnnovarDetection(True, "Synthetic installation available", "hg38")
            with patch.object(annovar, "discover_annovar", return_value=available), patch.object(
                    annovar, "build_annovar_command", side_effect=AssertionError("No annotation needed for an empty callset")):
                result = self.run_fixture(fixture, annotation="auto")
            self.assertEqual(result["overall_status"], "complete")
            self.assertEqual(result["samples"][0]["callers"][0]["annotation"]["status"], "not_applicable")
            fixture.assert_unchanged(self)

    def test_caller_failure_is_recorded_instead_of_empty_success(self):
        class FailingCaller(CommandRunner):
            def run(self, stage, command, **kwargs):
                argv = [str(value) for value in command]
                if len(argv) > 1 and argv[1] == "call":
                    raise OncoTracerError("synthetic caller failure")
                return super().run(stage, command, **kwargs)

        with tempfile.TemporaryDirectory(prefix="oncotracer-variants-fail-") as directory:
            fixture = SyntheticInput(Path(directory))
            runner = FailingCaller(fixture.outdir / "failed_trace.tsv", echo=False)
            result = self.run_fixture(fixture, runner=runner)
            self.assertEqual(result["overall_status"], "failed")
            self.assertIn("synthetic caller failure", json.dumps(result))
            fixture.assert_unchanged(self)


if __name__ == "__main__":
    unittest.main(verbosity=2)
