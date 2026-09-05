"""Execute documented setup/check/run plans using synthetic, CPU-only inputs.

Download commands are checked against pinned manifests, never executed here.
Run commands use --dry-run; separate scientific parity gates test real analyses.
"""

from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.cli import QS1_FILES, main
from oncotracer_cli.engine import _fastq_files, merge_fastqs, parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import load_flat_yaml

ROOT = Path(__file__).resolve().parents[1]
BASH = re.compile(r"```bash\n(.*?)```", re.DOTALL)
CSV = re.compile(r"```csv\n(.*?)```", re.DOTALL)


class DocumentedWorkflowTests(unittest.TestCase):
    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(list(args))
        return code, output.getvalue()

    def remap(self, text, base):
        for old, new in (("$PWD", base), ("/absolute/path", base), ("/data", base / "data"), ("/work", base / "work")):
            text = text.replace(old, str(new))
        return text

    def commands(self, text, executable="oncotracer"):
        for block in BASH.findall(text):
            for line in block.replace("\\\n", " ").splitlines():
                if line.strip().startswith(executable + " "):
                    yield shlex.split(line)

    def fastq(self, path, name):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write(f"@{name}\nACGT\n+\nIIII\n")

    def write_sheet(self, text, base, path):
        block, = CSV.findall(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.remap(block, base), encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(path.read_text())))
        for row in rows:
            for end in ("fastq_1", "fastq_2"):
                if row[end]:
                    self.fastq(Path(row[end]), row["sample"] + "_" + end)
        return rows

    def steps(self, text, base, expected):
        configured, checked, planned = [], [], []
        for command in self.commands(text):
            args = [self.remap(arg, base) for arg in command[1:]]
            action = args[0]
            self.assertIn(action, ("setup", "check", "run"))
            if action == "setup":
                project = Path(args[args.index("--project") + 1])
                configured.append(project / "config/run.yml")
            elif action == "check":
                args.append("--json")
            else:
                # Verify the real command's parser and engine plan, without
                # installing tools, downloading a reference, or running aligners.
                self.assertEqual(args[args.index("--backend") + 1], "conda")
                args.append("--dry-run")
            code, output = self.cli(*args)
            self.assertEqual(code, 0, f"{shlex.join(command)}\n{output}")
            if action == "check":
                checked.append(Path(args[args.index("--config") + 1]))
                self.assertEqual(json.loads(output)["plan"]["samples"], expected[len(checked) - 1])
            elif action == "run":
                planned.append(Path(args[args.index("--config") + 1]))
        self.assertEqual(len(configured), len(expected))
        self.assertEqual(configured, checked)
        self.assertEqual(configured, planned)
        configs = [load_flat_yaml(path) for path in configured]
        for config in configs:
            self.assertFalse(Path(config["outdir"]).exists())
            self.assertFalse(Path(config["lpwgs_root"]).exists())
        return configs

    def test_multisample_illumina_commands_keep_each_pair_separate(self):
        text = (ROOT / "docs/setup.md").read_text().split("## Illumina: multiple libraries\n", 1)[1].split("\n## ", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "analysis with spaces"
            rows = self.write_sheet(text, base, base / "data/illumina/samplesheet.csv")
            config, = self.steps(text, base, [["sampleA", "sampleB"]])
            samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
            self.assertEqual(
                [(s.sample, str(s.fastq_1), str(s.fastq_2), s.status) for s in samples],
                [(r["sample"], r["fastq_1"], r["fastq_2"], r["status"]) for r in rows],
            )
            self.assertEqual(config["threads"], 4)

    def test_multibarcode_ont_commands_include_batches_but_not_other_samples(self):
        text = (ROOT / "docs/setup.md").read_text().split("## ONT: multiple barcodes and FASTQ batches\n", 1)[1].split("\n## ", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "analysis with spaces"
            folder = base / "data/run/fastq_pass"
            for barcode in ("barcode01", "barcode02", "unclassified"):
                for batch in (1, 2):
                    self.fastq(folder / barcode / f"reads_{batch:03}.fastq.gz", f"{barcode}_{batch}")
            config, = self.steps(text, base, [["sampleA", "sampleB"]])
            samples = parse_ont_samples(config)
            self.assertEqual([(s.sample, s.barcode) for s in samples], [("sampleA", "barcode01"), ("sampleB", "barcode02")])
            for sample in samples:
                files = _fastq_files(sample.fastq_dir, 0)
                self.assertEqual(len(files), 2)
                merged = base / "merge-check" / f"{sample.sample}.fastq.gz"
                merge_fastqs(files, merged)
                with gzip.open(merged, "rt") as handle:
                    ids = [line[1:] for line in handle.read().splitlines()[::4]]
                self.assertEqual(ids, [f"{sample.barcode}_1", f"{sample.barcode}_2"])

    def test_quickstarts_use_standard_commands_and_pinned_downloads(self):
        with (ROOT / "examples/hcc1143_lpwgs/manifest.tsv").open() as handle:
            cohort = list(csv.DictReader(handle, delimiter="\t"))
        cases = (
            ("docs/quick_start.md", {Path(path).name: (url, md5) for url, path, size, md5 in QS1_FILES}, [["ERR12341627"], ["DRR165691"]]),
            ("docs/public_cohort.md", {row["filename"]: (row["url"], row["md5"]) for row in cohort}, [["HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"]]),
        )
        for relative, manifest, samples in cases:
            with self.subTest(page=relative), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary) / "analysis with spaces"
                text = (ROOT / relative).read_text()
                downloads = list(self.commands(text, "curl"))
                self.assertEqual(len(downloads), len(manifest))
                checksum_block, = re.findall(r"md5sum -c <<'MD5'\n(.*?)\nMD5", text, re.DOTALL)
                checksums = dict(line.split(None, 1)[::-1] for line in checksum_block.splitlines())
                observed = set()
                for command in downloads:
                    self.assertIn("--fail", command)
                    self.assertIn("--location", command)
                    self.assertEqual(command[command.index("--continue-at") + 1], "-")
                    target = command[command.index("--output") + 1]
                    filename = Path(target).name
                    self.assertNotIn(filename, observed)
                    observed.add(filename)
                    url, md5 = manifest[filename]
                    self.assertEqual(command[-1], url)
                    self.assertEqual(checksums[target], md5)
                    self.fastq(base / target, filename)
                self.assertEqual(observed, set(manifest))
                self.assertEqual(len(checksums), len(downloads))
                if CSV.search(text):
                    self.write_sheet(text, base, base / "oncotracer-quickstart2/input/samplesheet.csv")
                configs = self.steps(text, base, samples)
                self.assertEqual(len({config["lpwgs_root"] for config in configs}), 1)

    def test_reference_root_is_optional_reusable_and_read_only_during_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fastq = base / "reads.fastq.gz"
            self.fastq(fastq, "example")
            reference = base / "shared reference"
            reference.mkdir()
            marker = reference / "existing-data"
            marker.write_bytes(b"must remain unchanged")
            for index, flags in enumerate(([], ["--reference-root", str(reference / "../shared reference")], ["--reference-root", str(reference)])):
                project = base / f"project{index}"
                code, output = self.cli("setup", "--non-interactive", "--project", str(project), "--mode", "illumina", "--sample-name", "example", "--fastq-1", str(fastq), *flags)
                self.assertEqual(code, 0, output)
                config = load_flat_yaml(project / "config/run.yml")
                self.assertEqual(config["lpwgs_root"], str(reference if flags else project / "reference"))
                self.assertFalse((project / "reference").exists())
                self.assertFalse((project / "results").exists())
            self.assertEqual(list(reference.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"must remain unchanged")
            invalid_project = base / "invalid-project"
            code, output = self.cli("setup", "--non-interactive", "--project", str(invalid_project), "--mode", "illumina", "--reference-root", str(marker))
            self.assertEqual(code, 2, output)
            self.assertIn("directory, not a FASTA or index file", output)
            self.assertFalse(invalid_project.exists())

    def test_retired_entrypoints_are_absent_from_the_working_tree(self):
        for relative in (
            "main.nf", "nextflow.config", "run_test.sh", "environment.yml",
            "docs/legacy_v1.md", "docs/migration_v1_to_v2.md",
            ".github/templates/runtime-smoke-v1.1.yml",
            "bin/cna_classifier_nf/main.nf", "bin/cna_classifier_nf/nextflow.config",
            "bin/scripts/install_oncotracer.sh", "bin/scripts/prepare_samurai_source.sh",
            "bin/scripts/run_ifcnv_ont_lpwgs.py", "bin/scripts/run_illumina_samurai_fastq.sh",
            "bin/scripts/run_ont_samurai_barcodes.sh",
            "examples/hcc1143_lpwgs/run_example.sh", "examples/prjna754199/run_example.sh",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
