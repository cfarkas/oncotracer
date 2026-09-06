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
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.cli import QS1_FILES, main
from oncotracer_cli.engine import _fastq_files, merge_fastqs, parse_illumina_samplesheet, parse_ont_samples
from oncotracer_cli.runtime import load_flat_yaml

ROOT = Path(__file__).resolve().parents[1]
BASH = re.compile(r"```bash\n(.*?)```", re.DOTALL)
TABLE = re.compile(r"^cat > [^\n]+ <<(?:'CSV'|CSV)\n.*?^CSV$", re.MULTILINE | re.DOTALL)


class DocumentedWorkflowTests(unittest.TestCase):
    def cli(self, *args):
        # Real auto invokes the shared gzip/table generator, which needs a
        # file descriptor rather than StringIO for its captured output.
        with tempfile.TemporaryFile(mode="w+") as output:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(list(args))
            output.seek(0)
            return code, output.read()

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

    def create_tables(self, text, base):
        base.mkdir(parents=True, exist_ok=True)
        count = 0
        for block in BASH.findall(text):
            tables = list(TABLE.finditer(block))
            if not tables and not re.search(r'^mkdir ', block, re.MULTILINE):
                continue
            # Run the documented cat commands, not a reconstructed CSV body.
            # Only directory/variable setup is included, never downloads or runs.
            prefix = block[:tables[0].start()] if tables else block
            prefix = prefix.replace("\\\n", " ")
            setup = [line for line in prefix.splitlines() if re.match(r'^(?:[A-Z_]+=|mkdir |cd )', line)]
            script = "\n".join([*setup, *(match.group() for match in tables)])
            script = script.replace("cd /path/to/my/analyses_dir/", 'cd -- "$TEST_ANALYSES"')
            script = self.remap(script.replace("$PWD", "${PWD}"), base)
            result = subprocess.run(["bash", "-eu"], input=script, text=True, capture_output=True, cwd=base, env={**os.environ, "TEST_ANALYSES": str(base)})
            self.assertEqual(result.returncode, 0, result.stderr + "\n" + script)
            count += len(tables)
        return count

    def write_sheet(self, text, base, path):
        self.create_tables(text, base)
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
            self.assertIn(action, ("setup", "auto", "check", "run"))
            if action == "setup":
                project = Path(args[args.index("--project") + 1])
                configured.append(project / "config/run.yml")
            elif action == "auto":
                config_dir = Path(args[args.index("--config-dir") + 1])
                mode = args[args.index("--mode") + 1]
                configured.append(config_dir / f"{mode}.auto.yml")
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
        with (ROOT / "examples/prjna754199/manifest.tsv").open() as handle:
            full_cohort = list(csv.DictReader(handle, delimiter="\t"))
        cases = (
            ("docs/quick_start.md", {Path(path).name: (url, md5) for url, path, size, md5 in QS1_FILES}, [["ERR12341627"], ["DRR165691"]]),
            ("docs/public_cohort.md", {row["filename"]: (row["url"], row["md5"]) for row in cohort}, [["HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"]]),
            ("docs/full_tutorial.md", {row["sample_alias"] + ".fastq.gz": (row["https_url"], row["fastq_md5"]) for row in full_cohort}, [[row["sample_alias"] for row in full_cohort]]),
        )
        for relative, manifest, samples in cases:
            with self.subTest(page=relative), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary) / "analysis with spaces"
                text = (ROOT / relative).read_text()
                downloads = [command for command in self.commands(text, "curl") if command[-1].endswith(".fastq.gz")]
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
                self.create_tables(text, base)
                # Hardware/backend commands are useful prerequisites, not analysis steps.
                workflow_text = text if relative != "docs/full_tutorial.md" else text.split("## 4. Save the settings", 1)[1]
                configs = self.steps(workflow_text, base, samples)
                self.assertTrue(all(config["hg38_auto_download"] for config in configs))
                for config in configs:
                    expected_parent = Path(config["outdir"]).parent
                    if relative == "docs/full_tutorial.md":
                        expected_parent /= "config"
                        self.assertEqual(config["cna_classifier_sample_set"], "sarcoma")
                        self.assertFalse(config["pathology_use_biomed_models"])
                        self.assertFalse(config["knowledge_web"])
                        self.assertTrue(all(sample.fastq_2 is None for sample in parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))))
                    self.assertEqual(Path(config["lpwgs_root"]), expected_parent / "reference")

    def test_all_input_table_examples_create_files_with_cat(self):
        count = 0
        for path in [ROOT / "README.md", *(ROOT / "docs").rglob("*.md"), *(ROOT / "examples").rglob("*.md")]:
            text = path.read_text()
            self.assertNotRegex(text, r"```(?:csv|tsv)\b", str(path))
            self.assertNotIn("csv.writer", text, str(path))
            if not TABLE.search(text):
                continue
            with self.subTest(page=path.relative_to(ROOT)), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary) / "analysis #1, with spaces"
                count += self.create_tables(text, base)
                for table in base.rglob("*.csv"):
                    with table.open() as handle:
                        rows = list(csv.reader(handle))
                    self.assertGreater(len(rows), 1, str(table))
                    self.assertTrue(all(len(row) == len(rows[0]) for row in rows), str(table))
                    self.assertNotIn("$PWD", table.read_text(), str(table))
        self.assertGreaterEqual(count, 15)

    def test_batch_and_mock_cohort_examples_keep_all_samples_separate(self):
        cases = (
            ("docs/auto_params.md", "## Illumina: multiple libraries", "project", ["TUMOR_01", "TUMOR_02", "CONTROL_01", "CONTROL_02"]),
            ("docs/auto_params.md", "## ONT: multiple barcodes and FASTQ batches", "ont-project", ["PATIENT_A", "PATIENT_B"]),
            ("docs/six_tumor_four_normal.md", "## 2. Create the sample table", "oncotracer-onco6-ctrl4", [*(f"ONCO{i:03}" for i in range(1, 7)), *(f"CTRL{i:03}" for i in range(1, 5))]),
        )
        for relative, heading, project, names in cases:
            with self.subTest(page=relative, section=heading), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary) / "analysis with spaces"
                text = (ROOT / relative).read_text().split(heading, 1)[1]
                if relative.endswith("auto_params.md"):
                    text = text.split("\n## ", 1)[0]
                self.create_tables(text, base)
                ont = project == "ont-project"
                if ont:
                    for barcode in ("barcode01", "barcode02", "unclassified"):
                        for batch in (1, 2):
                            self.fastq(base / project / "input/fastq_pass" / barcode / f"reads_{batch:03}.fastq.gz", f"{barcode}_{batch}")
                else:
                    for name in names:
                        for end in ("R1", "R2"):
                            self.fastq(base / project / "input/fastq" / f"{name}_{end}.fastq.gz", name + end)
                config, = self.steps(text, base, [names])
                if ont:
                    samples = parse_ont_samples(config)
                    self.assertEqual([sample.barcode for sample in samples], ["barcode01", "barcode02"])
                    self.assertTrue(all(len(_fastq_files(sample.fastq_dir, 0)) == 2 for sample in samples))
                else:
                    samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
                    self.assertEqual([sample.sample for sample in samples], names)
                    self.assertEqual(sum(sample.status == "normal" for sample in samples), 4 if project.startswith("oncotracer-onco") else 2)

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
