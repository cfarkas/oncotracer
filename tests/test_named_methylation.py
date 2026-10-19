"""Named ONT libraries scope methylation signals to their own passed reads."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from oncotracer_cli.engine import parse_ont_samples
from oncotracer_cli.methylation import (methylation_plan, resolve_methylation_request, run_methylation,
                                       _prepare_read_ids, _select_named_modbam_input)
from oncotracer_cli.runtime import CommandRunner, OncoTracerError, StageLedger
from tests.test_native_methylation import FakeRunner, Fixture, ROOT


class NamedRunner(FakeRunner):
    def run(self, stage, command, **kwargs):
        if stage == "methylation-dorado-merge":
            argv = [str(value) for value in command]
            self.calls.append((stage, argv))
            Path(argv[argv.index("-o") + 1]).write_bytes(b"merged named streams")
        else:
            super().run(stage, command, **kwargs)


class NamedMethylationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)
        self.cohort = self.root / "named cohort"
        self.cohort.mkdir()
        for name in ("ONCO-001", "ONCO-002", "UNSELECTED"):
            library = self.cohort / name
            fastq = library / "fastq_pass/barcode01"
            fastq.mkdir(parents=True)
            (fastq / "reads.fastq").write_text(f"@read-{name}\nACGT\n+\nIIII\n")
            for folder, filename in (("modified_bam", "matching.bam"), ("pod5_pass/barcode01", "signal.pod5"),
                                     ("pod5_pass/barcode02", "other.pod5"), ("pod5_fail", "failed.pod5"),
                                     ("bam_fail", "failed.bam"), ("fastq_fail", "failed.fastq")):
                path = library / folder / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic fixture")
            (library / "original.unaligned.bam").write_bytes(b"not an active signal source")
        self.config = self.fixture.config()
        self.config.update(ont_named_samples=True, ont_folder=str(self.cohort),
                           ont_barcodes="ONCO-001,ONCO-002", ont_sample_names="reviewed_A,reviewed_B",
                           methylation_pod5_dir=str(self.cohort))

    def request(self, kind="pod5"):
        if kind == "modbam":
            self.config.pop("methylation_pod5_dir", None)
            self.config["methylation_modbam"] = str(self.cohort)
        return resolve_methylation_request(self.config, mode="ont")

    def reference(self):
        fasta, fai = self.root / "hg38.fa", self.root / "hg38.fa.fai"
        fasta.write_text(">chr1\nACGT\n")
        fai.write_text("chr1\t4\t6\t4\t5\n")
        return {"fasta": fasta, "fai": fai}

    def test_modified_bams_only_come_from_selected_named_libraries(self):
        request = self.request("modbam")
        self.assertEqual(request.modbam_files, tuple(self.cohort / name / "modified_bam/matching.bam"
                                                    for name in ("ONCO-001", "ONCO-002")))
        self.assertEqual([group.sample for group in request.sample_signals], ["reviewed_A", "reviewed_B"])
        self.assertEqual([group.barcode for group in request.sample_signals], ["barcode01", "barcode01"])
        self.assertEqual([row["file_count"] for row in methylation_plan(request)["sample_signals"]], [1, 1])
        self.assertFalse(any("fail" in str(path) or "UNSELECTED" in str(path) for path in request.modbam_files))

    def test_wrong_or_failed_named_signal_source_never_falls_back(self):
        self.config.pop("methylation_pod5_dir")
        for source in (self.cohort / "UNSELECTED/modified_bam", self.cohort / "ONCO-001/bam_fail"):
            with self.subTest(source=source):
                self.config["methylation_modbam"] = str(source)
                with self.assertRaisesRegex(OncoTracerError, "does not cover|failed-read"):
                    resolve_methylation_request(self.config, mode="ont")
        (self.cohort / "ONCO-001/modified_bam/matching.bam").unlink()
        self.config["methylation_modbam"] = str(self.cohort)
        with self.assertRaisesRegex(OncoTracerError, "non-empty .bam"):
            resolve_methylation_request(self.config, mode="ont")

    def test_signal_barcodes_are_scoped_and_mismatches_rejected(self):
        request = self.request()
        self.assertEqual(request.pod5_files, tuple(self.cohort / name / "pod5_pass/barcode01/signal.pod5"
                                                  for name in ("ONCO-001", "ONCO-002")))
        (self.cohort / "ONCO-001/pod5_pass/barcode01/signal.pod5").unlink()
        with self.assertRaisesRegex(OncoTracerError, "barcodes do not unambiguously match"):
            self.request()

    def test_nested_library_uses_its_selected_run_and_all_passed_signal_batches(self):
        self.config.update(ont_barcodes="ONCO-001", ont_sample_names="reviewed_A")
        library = self.cohort / "ONCO-001"
        run = library / "runA"
        run.mkdir()
        (library / "fastq_pass").rename(run / "fastq_pass")
        (library / "modified_bam").rename(run / "modified_bam")
        unrelated = library / "runB/modified_bam/unrelated.bam"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(b"not selected")
        request = self.request("modbam")
        self.assertEqual(request.modbam_files, (run / "modified_bam/matching.bam",))
        self.assertEqual(parse_ont_samples(self.config)[0].fastq_dir, run / "fastq_pass/barcode01")

    def test_named_pod5_calls_use_sample_specific_ids_and_never_cohort_recursion(self):
        request = self.request()
        samples = parse_ont_samples(self.config)
        runner = NamedRunner("")
        output = self.root / "output"
        reference = self.reference()
        before = {path: path.read_bytes() for path in self.cohort.rglob("*") if path.is_file()}
        run_methylation(ROOT, request, samples, reference, output, runner, StageLedger(output / ".oncotracer-native/state.json"), threads=2, force=False)
        calls = [argv for stage, argv in runner.calls if stage == "methylation-dorado-basecall"]
        self.assertEqual(len(calls), 2)
        for argv, sample, folder in zip(calls, ("reviewed_A", "reviewed_B"), ("ONCO-001", "ONCO-002")):
            self.assertEqual(Path(argv[3]), self.cohort / folder / "pod5_pass/barcode01")
            identifiers = Path(argv[argv.index("--read-ids") + 1])
            self.assertEqual(identifiers.read_text(), f"read-{folder}\n")
            self.assertTrue(identifiers.name.startswith(sample + "."))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(list((output / "07_methylation/modbam").glob(".selected_signal.*")))
        self.assertEqual([stage for stage, _ in runner.calls].count("methylation-dorado-merge"), 1)
        provenance = json.loads((output / "07_methylation/methylation_provenance.json").read_text())
        self.assertEqual([row["sample"] for row in provenance["sample_signals"]], ["reviewed_A", "reviewed_B"])

    def test_named_pod5_inventory_mutation_fails_closed(self):
        request = self.request()
        runner = NamedRunner("", mutate_pod5=request.pod5_files[0])
        output = self.root / "output"
        with self.assertRaisesRegex(OncoTracerError, "POD5 inventory changed"):
            run_methylation(ROOT, request, parse_ont_samples(self.config), self.reference(), output,
                            runner, StageLedger(output / ".oncotracer-native/state.json"), threads=2, force=False)

    def test_pass_fastq_ids_never_include_failed_nested_batches(self):
        failed = self.cohort / "ONCO-001/fastq_pass/fastq_fail/failed.fastq"
        failed.parent.mkdir()
        failed.write_text("@wrong-read\nACGT\n+\nIIII\n")
        request = self.request()
        output = self.root / "output"
        run_methylation(ROOT, request, parse_ont_samples(self.config), self.reference(), output,
                        NamedRunner(""), StageLedger(output / ".oncotracer-native/state.json"), threads=2, force=False)
        self.assertNotIn("wrong-read", (output / "07_methylation/read_ids/all_selected.read_ids.txt").read_text())

    @unittest.skipUnless(shutil.which("samtools") and shutil.which("awk"), "optional real samtools is not installed")
    def test_real_modified_bam_intersection_cannot_borrow_reads_from_other_library(self):
        self.config.pop("methylation_pod5_dir")
        self.config.update(methylation_modbam=str(self.cohort),
                           methylation_samtools_executable=shutil.which("samtools"))
        bams = []
        for name in ("ONCO-001", "ONCO-002"):
            sam = self.root / (name + ".sam")
            sam.write_text("@HD\tVN:1.6\tSO:unknown\n" +
                           f"read-{name}\t4\t*\t0\t0\t*\t*\t0\t0\tACGT\tIIII\tMM:Z:C+m,0;\tML:B:C,240\n")
            bam = self.cohort / name / "modified_bam/matching.bam"
            subprocess.run(["samtools", "view", "-b", "-o", str(bam), str(sam)], check=True, capture_output=True)
            bams.append(bam)
        ids = _prepare_read_ids(parse_ont_samples(self.config), self.root / "read_ids")
        output = self.root / "selected"
        output.mkdir()
        runner = CommandRunner(output / "trace.tsv", echo=False)
        request = resolve_methylation_request(self.config, mode="ont")
        selected = _select_named_modbam_input(request, ids, output, runner, 2)
        names = subprocess.check_output(["samtools", "view", str(selected)], text=True)
        self.assertEqual({row.split("\t")[0] for row in names.splitlines()}, {"read-ONCO-001", "read-ONCO-002"})
        first, second = (path.read_bytes() for path in bams)
        bams[0].write_bytes(second)
        bams[1].write_bytes(first)
        request = resolve_methylation_request(self.config, mode="ont")
        with self.assertRaisesRegex(OncoTracerError, "reviewed_A.*No FASTQ-selected reads"):
            _select_named_modbam_input(request, ids, output, runner, 2)

    def test_named_signal_mutation_before_run_fails_without_outputs(self):
        request = self.request()
        request.pod5_files[0].write_bytes(b"changed signal fixture")
        runner = NamedRunner("")
        output = self.root / "output"
        with self.assertRaisesRegex(OncoTracerError, "changed before analysis"):
            run_methylation(ROOT, request, parse_ont_samples(self.config), self.reference(), output,
                            runner, StageLedger(output / ".oncotracer-native/state.json"), threads=2, force=False)
        self.assertFalse(output.exists())
        self.assertFalse(runner.calls)

    def test_overlapping_selected_fastq_ids_still_fail_before_basecalling(self):
        path = self.cohort / "ONCO-002/fastq_pass/barcode01/reads.fastq"
        path.write_bytes((self.cohort / "ONCO-001/fastq_pass/barcode01/reads.fastq").read_bytes())
        request = self.request()
        runner = NamedRunner("")
        output = self.root / "output"
        with self.assertRaisesRegex(OncoTracerError, "occurs in both samples"):
            run_methylation(ROOT, request, parse_ont_samples(self.config), self.reference(), output,
                            runner, StageLedger(output / ".oncotracer-native/state.json"), threads=2, force=False)
        self.assertFalse(runner.calls)


if __name__ == "__main__":
    unittest.main()
