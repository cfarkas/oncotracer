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
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import QS1_FILES, main, build_parser, parse_arguments
from oncotracer_cli.web import WebState
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
        for old, new in (("$PWD", base), ("/path/to/my/analyses_dir", base), ("/absolute/path", base), ("/data", base / "data"), ("/work", base / "work")):
            text = text.replace(old, str(new))
        return text

    def commands(self, text, executable="oncotracer"):
        for block in BASH.findall(text):
            for line in block.replace("\\\n", " ").splitlines():
                if line.strip().startswith(executable + " "):
                    yield shlex.split(line)

    def section(self, text, heading):
        marker = f"## {heading}\n"
        self.assertIn(marker, text)
        return text.split(marker, 1)[1].split("\n## ", 1)[0]

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

    def steps(self, text, base, expected, *, unattended=False, terminal_questions=False):
        configured, checked, planned = [], [], []
        # Follow the guide's choices through the real terminal wizard. Deeper
        # prompt validation and alternate routes live in test_wizard.py.
        names = iter(())
        prompts = []

        def answer_prompt(prompt):
            prompts.append(prompt)
            if prompt.startswith("Sample name ["):
                name = next(names, None)
                self.assertIsNotNone(name, "Wizard discovered more samples than documented")
                if terminal_questions and initial.mode == "illumina":
                    self.assertTrue(prompt.startswith(f"Sample name [{name}]"), prompt)
                    return ""  # Keep each library's discovered identity and pair.
                return name
            if prompt.startswith("Type for "):
                return "cancer"
            answers = {
                "Samples to include (numbers, ranges, or all)": "all" if terminal_questions else "",
                "Analysis (--analysis; cna=copy-number)": "cna",
                "CPU threads (--threads)": "4" if terminal_questions else "",
                "ONT CNA method": "ichorcna",
                "CNA bin size (kb)": "100",
                "Add CNA interpretation reports?": "no",
                "Add small-variant calling?": "no",
                "Analysis tools (--backend)": "conda",
                "hg38 reference": "download",
                "Final action": "save",
            }
            for label, answer in answers.items():
                if prompt.startswith(label + " [") or prompt.startswith(label + " ("):
                    return answer
            self.fail(f"Unexpected wizard question: {prompt!r}")

        for command in self.commands(text):
            args = [self.remap(arg, base) for arg in command[1:]]
            initial = parse_arguments(args)
            action = initial.command
            self.assertIn(action, ("setup", "auto", "check", "run"))
            if terminal_questions:
                self.assertEqual(action, "setup")
                self.assertTrue(initial.terminal)
                self.assertFalse(initial.non_interactive)
                self.assertFalse(initial.run)
            if action == "setup":
                if unattended:
                    self.assertIn("--non-interactive", args)
                    self.assertNotIn("--input-folder", args)
                project = Path(args[args.index("--project") + 1])
                configured.append(project / "config/run.yml")
                names = iter(expected[len(configured) - 1])
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
            if action == "setup" and "--input-folder" in args and "--terminal" not in args:
                # Exercise the actual browser discovery/configuration path for
                # browser-first examples; HTTP/DOM behavior has a Firefox smoke test.
                state = WebState(base, initial)
                scan = state.scan({"mode": initial.mode, "folder": initial.input_folder})
                selections = [{"id": sample["id"], "name": next(names), "type": "cancer"}
                              for sample in scan["samples"] if sample["barcode"] != "unclassified"]
                with contextlib.redirect_stdout(io.StringIO()):
                    saved = state.prepare({"scan_id": scan["scan_id"], "project": initial.project,
                                           "threads": 4, "samples": selections,
                                           "backend": initial.backend or "conda",
                                           "variant_specimen_type": "fresh"})
                code, output = (0 if saved["valid"] else 2), json.dumps(saved)
            else:
                prompt_handler = AssertionError("Scripted workflow requested terminal input") if unattended else answer_prompt
                prompts.clear()
                with contextlib.ExitStack() as guards:
                    guards.enter_context(patch("builtins.input", side_effect=prompt_handler))
                    if terminal_questions:
                        # Keep the documented four-thread choice portable to
                        # smaller CI runners without dispatching any tool.
                        guards.enter_context(patch("oncotracer_cli.wizard.inspect_hardware", return_value={
                            "os": "Linux", "architecture": "x86_64", "python_supported": True,
                            "cpu_workers_available": 8, "ram_total_bytes": 32 * 1024**3,
                            "ram_available_bytes": 24 * 1024**3, "gpus": [],
                            "gpu_detection_status": "synthetic test hardware",
                        }))
                        for target in ("oncotracer_cli.setup._run_setup", "oncotracer_cli.cli.command_install",
                                       "oncotracer_cli.web.command_web"):
                            guards.enter_context(patch(target, side_effect=AssertionError(
                                "Terminal tutorial dispatched analysis, installation, or a browser")))
                    code, output = self.cli(*args)
                if terminal_questions:
                    self.assertTrue(prompts[-1].startswith("Final action"), prompts)
                    self.assertIn("Saved without starting analysis", output)
            self.assertEqual(code, 0, f"{shlex.join(command)}\n{output}")
            if action == "setup" and "--input-folder" in args:
                self.assertIsNone(next(names, None), "Wizard omitted a documented sample")
                if terminal_questions:
                    # The wizard saves and checks. Independently inspect its
                    # config and plan without starting the scientific analysis.
                    config = configured[-1]
                    check_code, output = self.cli("check", "--config", str(config), "--json")
                    self.assertEqual(check_code, 0, output)
                    self.assertEqual(json.loads(output)["plan"]["samples"], expected[len(checked)])
                    checked.append(config)
                    run_code, output = self.cli("run", "--backend", initial.backend or "conda",
                                                "--config", str(config), "--dry-run")
                    self.assertEqual(run_code, 0, output)
                    self.assertIn("oncotracer-native-dry-run-v1", output)
                    planned.append(config)
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
        page = (ROOT / "docs/setup.md").read_text()
        setup = page.split("## 1. Configure interactively (recommended)\n", 1)[1].split("\n## ", 1)[0]
        run = page.split("## 2. Check and run your platform\n", 1)[1].split("\n## ", 1)[0]
        text = setup.split("### ONT\n", 1)[1] + run.split("### ONT\n", 1)[1]
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
            ("docs/public_cohort.md", {row["filename"]: (row["url"], row["md5"]) for row in cohort}, [["HCC1143_BEZ235", "HCC1143_DMSO", "HCC1143_TRAMETINIB"]]),
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
                original = {path: path.read_bytes() for path in base.rglob("*.fastq.gz")}
                terminal_questions = relative != "docs/full_tutorial.md"
                if terminal_questions:
                    # Parse the browser launch without starting a server, then
                    # exercise the separate headless terminal-question route.
                    walkthrough = "2. Terminal questions for headless servers"
                    launch_text = text.split(f"## {walkthrough}\n", 1)[0]
                    launches = list(self.commands(launch_text))
                    self.assertTrue(launches, "QuickStart omitted the simple interactive launch")
                    for command in launches:
                        with patch("subprocess.run", side_effect=AssertionError("Launch parser executed a subprocess")):
                            launch = parse_arguments(command[1:])
                        self.assertEqual(launch.command, "setup")
                        self.assertTrue(launch.interactive)
                        self.assertFalse(launch.run)
                        self.assertIsNone(launch.project)
                        self.assertIsNone(launch.input_folder)
                    workflow_text = self.section(text, walkthrough)
                else:
                    workflow_text = text.split("## 4. Save the settings", 1)[1]
                configs = self.steps(workflow_text, base, samples, terminal_questions=terminal_questions)
                self.assertEqual({path: path.read_bytes() for path in original}, original)
                if terminal_questions:
                    for config in configs:
                        self.assertFalse(config.get("methylation", False))
                        self.assertFalse(config.get("methylation_only", False))
                        self.assertFalse(config.get("run_variants", False))
                        self.assertFalse(config.get("run_cna_classifier", False))
                        self.assertFalse(config.get("run_gistic", False))
                        self.assertEqual(config["threads"], 4)
                        self.assertEqual(config["execution_backend"], "conda")
                        if config["mode"] == "illumina":
                            self.assertEqual(config["illumina_binsize_kb"], 100)
                            libraries = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
                            if relative == "docs/quick_start.md":
                                folder = base / "oncotracer-quickstart1/input/illumina"
                                expected_pairs = {"ERR12341627": (folder / "ERR12341627_1.fastq.gz",
                                                                 folder / "ERR12341627_2.fastq.gz", "tumor")}
                            else:
                                folder = base / "oncotracer-quickstart2/input"
                                expected_pairs = {name: (folder / f"{name}_R1.fastq.gz",
                                                         folder / f"{name}_R2.fastq.gz", "tumor")
                                                  for name in samples[0]}
                            self.assertEqual({item.sample: (item.fastq_1, item.fastq_2, item.status)
                                              for item in libraries}, expected_pairs)
                        else:
                            self.assertEqual(config["ont_caller"], "ichorcna")
                            self.assertEqual(config["ont_binsize_kb"], 500)
                            sample, = parse_ont_samples(config)
                            self.assertEqual((sample.sample, sample.barcode, sample.status, sample.fastq_dir),
                                             ("DRR165691", "barcode01", "tumor",
                                              base / "oncotracer-quickstart1/input/fastq_pass/barcode01"))
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

    def test_quickstart_scripted_alternative_uses_downloaded_inputs_without_prompts(self):
        page = (ROOT / "docs/quick_start.md").read_text()
        terminal = (ROOT / "docs/quickstart_terminal.md").read_text()
        text = self.section(terminal, "QuickStart 1")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "analysis with spaces"
            inputs = []
            for command in self.commands(page, "curl"):
                if command[-1].endswith(".fastq.gz"):
                    target = base / command[command.index("--output") + 1]
                    self.fastq(target, target.name)
                    inputs.append(target)
            self.assertEqual({path.name for path in inputs}, {Path(path).name for _, path, _, _ in QS1_FILES})
            original = {path: path.read_bytes() for path in inputs}
            illumina, ont = self.steps(text, base, [["ERR12341627"], ["DRR165691"]], unattended=True)
            sample, = parse_illumina_samplesheet(Path(illumina["illumina_samplesheet"]))
            folder = base / "oncotracer-quickstart1/input"
            self.assertEqual((sample.fastq_1, sample.fastq_2, sample.status), (
                folder / "illumina/ERR12341627_1.fastq.gz",
                folder / "illumina/ERR12341627_2.fastq.gz", "tumor",
            ))
            sample, = parse_ont_samples(ont)
            self.assertEqual((sample.sample, sample.barcode), ("DRR165691", "barcode01"))
            self.assertEqual(_fastq_files(sample.fastq_dir, 0), [folder / "fastq_pass/barcode01/DRR165691_1.fastq.gz"])
            for config in (illumina, ont):
                self.assertEqual(config["threads"], 4)
                self.assertTrue(config["hg38_auto_download"])
            self.assertEqual({path: path.read_bytes() for path in inputs}, original)

    def test_quickstart2_terminal_commands_keep_all_pairs_without_prompts(self):
        page = (ROOT / "docs/public_cohort.md").read_text()
        terminal = self.section((ROOT / "docs/quickstart_terminal.md").read_text(), "QuickStart 2")
        names = ["HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "analysis with spaces"
            for command in self.commands(page, "curl"):
                if command[-1].endswith(".fastq.gz"):
                    target = base / command[command.index("--output") + 1]
                    self.fastq(target, target.name)
            original = {path: path.read_bytes() for path in base.rglob("*.fastq.gz")}
            self.assertEqual(len(original), 6)
            self.create_tables(terminal, base)
            config, = self.steps(terminal, base, [names], unattended=True)
            samples = parse_illumina_samplesheet(Path(config["illumina_samplesheet"]))
            folder = base / "oncotracer-quickstart2/input"
            self.assertEqual([(sample.sample, sample.fastq_1, sample.fastq_2, sample.status) for sample in samples],
                             [(name, folder / (name + "_R1.fastq.gz"), folder / (name + "_R2.fastq.gz"), "tumor") for name in names])
            self.assertTrue(config["hg38_auto_download"])
            self.assertEqual(config["threads"], 4)
            self.assertEqual({path: path.read_bytes() for path in original}, original)

    def test_headless_scripted_workflows_keep_pairs_and_selected_barcode_batches(self):
        page = (ROOT / "docs/headless.md").read_text()
        text = "\n".join(self.section(page, heading) for heading in (
            "Scripted Illumina: setup, check and run", "Scripted ONT: setup, check and run",
        ))
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "analysis with spaces"
            for mate in ("R1", "R2"):
                self.fastq(base / "data/illumina" / f"sampleA_{mate}.fastq.gz", f"sampleA_{mate}")
            folder = base / "data/run/fastq_pass"
            for barcode in ("barcode01", "barcode02", "unclassified"):
                for batch in (1, 2):
                    self.fastq(folder / barcode / f"reads_{batch:03}.fastq.gz", f"{barcode}_{batch}")
            original = {path: path.read_bytes() for path in (base / "data").rglob("*.fastq.gz")}
            illumina, ont = self.steps(text, base, [["sampleA"], ["sampleA", "sampleB"]], unattended=True)
            sample, = parse_illumina_samplesheet(Path(illumina["illumina_samplesheet"]))
            self.assertEqual((sample.fastq_1, sample.fastq_2, sample.status), (
                base / "data/illumina/sampleA_R1.fastq.gz",
                base / "data/illumina/sampleA_R2.fastq.gz", "tumor",
            ))
            samples = parse_ont_samples(ont)
            self.assertEqual([(sample.sample, sample.barcode) for sample in samples], [
                ("sampleA", "barcode01"), ("sampleB", "barcode02"),
            ])
            for sample in samples:
                self.assertEqual(_fastq_files(sample.fastq_dir, 0), [
                    folder / sample.barcode / f"reads_{batch:03}.fastq.gz" for batch in (1, 2)
                ])
            for config in (illumina, ont):
                self.assertEqual(config["threads"], 8)
                self.assertTrue(config["hg38_auto_download"])
            self.assertEqual({path: path.read_bytes() for path in original}, original)

    def test_headless_docker_and_existing_bam_commands_parse_without_execution(self):
        page = (ROOT / "docs/headless.md").read_text()
        text = self.section(page, "Docker without a browser")
        commands = list(self.commands(text))
        self.assertEqual([command[1] for command in commands], ["setup", "check", "run", "variants", "variants"])
        # Parser validation only: this documentation test must never contact
        # Docker, pull an image, inspect data, or dispatch an analysis.
        with patch("subprocess.run", side_effect=AssertionError("Parser launched a subprocess")):
            setup, check, run, variant_plan, variant_run = [
                build_parser().parse_args(command[1:]) for command in commands
            ]
        self.assertTrue(setup.non_interactive)
        self.assertEqual((setup.mode, setup.analysis, setup.backend, run.backend), ("illumina", "cna", "docker", "docker"))
        self.assertEqual(setup.image, "carlosfarkas/oncotracer:multisample-20261020-r2")
        self.assertEqual(run.image, setup.image)
        self.assertEqual(Path(check.config), Path(setup.project) / "config/run.yml")
        self.assertEqual(run.config, check.config)
        self.assertEqual((setup.sample_name, setup.threads), ("sampleA", 8))
        self.assertTrue(variant_plan.dry_run)
        self.assertFalse(variant_run.dry_run)
        self.assertEqual(variant_plan.config, variant_run.config)
        self.assertEqual(variant_run.threads, 8)

    def test_post_ont_guide_saves_and_plans_each_classifier_separately(self):
        from oncotracer_cli.reference_bundle import _files, reference_paths
        from oncotracer_cli.runtime import render_flat_yaml
        from oncotracer_cli.setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
        from tests.test_native_methylation import Fixture
        from tests.test_web import HARDWARE

        text = (ROOT / "docs/post_ont_cpg.md").read_text()
        commands = list(self.commands(text))
        self.assertEqual(commands, [
            ["oncotracer", "--interactive", "--backend", "conda", "--mode", "ont",
             "--analysis", "methylation", "--input-folder", "/data/ONT"],
            ["oncotracer", "--interactive", "--cpg-from", "/path/to/ONT-project"],
        ])
        fresh = parse_arguments(commands[0][1:])
        self.assertEqual((fresh.command, fresh.mode, fresh.analysis, fresh.backend),
                         ("setup", "ont", "methylation", "conda"))
        self.assertEqual(str(fresh.input_folder), "/data/ONT")
        self.assertTrue(fresh.interactive)
        self.assertFalse(fresh.run)
        self.assertIn("https://github.com/cfarkas/oncotracer/tree/main", text)
        self.assertIn("installation_details.md#install-from-github", text)
        self.assertNotIn("releases/latest", text)
        from oncotracer_cli.nextflow_gateway import parser as gateway_parser
        gateway, = list(self.commands(text, "nextflow"))
        self.assertEqual(gateway[:3], ["nextflow", "run", "main.nf"])
        gateway_args = gateway_parser().parse_args(gateway[3:])
        self.assertTrue(gateway_args.interactive)
        self.assertEqual((gateway_args.cpg_from, gateway_args.backend), ("/path/to/ONT-project", "conda"))
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as guards:
            base = Path(temporary) / "analysis with spaces"
            base.mkdir()
            source = base / "ONT-project"
            config_dir = source / "config"
            config_dir.mkdir(parents=True)
            guards.enter_context(patch("oncotracer_cli.cli._load_install_config", return_value={}))
            for target in ("oncotracer_cli.web.WebState.run", "oncotracer_cli.web.command_web",
                           "oncotracer_cli.web.WebState.scan", "oncotracer_cli.cli.command_install",
                           "oncotracer_cli.engine.prepare_reference", "oncotracer_cli.methylation.run_methylation"):
                guards.enter_context(patch(target, side_effect=AssertionError(
                    "The restored-project guide must only restore, save, check and plan")))
            initial = parse_arguments([str(source) if value == "/path/to/ONT-project" else value
                                       for value in commands[1][1:]])
            self.assertEqual((initial.command, initial.cpg_from), ("setup", str(source)))
            self.assertTrue(initial.interactive)
            self.assertFalse(initial.run)
            folder = base / "original reads/fastq_pass"
            for barcode in ("barcode01", "barcode02", "barcode03"):
                self.fastq(folder / barcode / "reads.fastq.gz", barcode)
            bam = folder.parent / "bam_pass/original-modified-base.bam"
            bam.parent.mkdir()
            bam.write_bytes(b"synthetic BAM path fixture; never processed")
            reference = base / "prepared reference"
            for name in _files():
                asset = reference_paths(reference)[0] / name
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_bytes(b"synthetic reference path fixture; never processed")
            resources = {}
            for classifier in ("marlin", "sturgeon"):
                directory = base / "resources" / classifier
                directory.mkdir(parents=True)
                fixture = Fixture(directory, classifier)
                allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[classifier])
                resources.update({key: value for key, value in fixture.config().items()
                                  if key in allowed or key.endswith(("_sha256", "_interface_contract_commit"))})
            names = ["LEUKEMIA_EXAMPLE", "CNS_EXAMPLE", "UNKNOWN_EXAMPLE"]
            metadata = config_dir / "sample_metadata.csv"
            with metadata.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["sample", "sample_type", "analysis_role", "fastq_files"])
                writer.writeheader()
                for index, name in enumerate(names, 1):
                    writer.writerow({"sample": name, "sample_type": "cancer", "analysis_role": "tumor",
                                     "fastq_files": json.dumps([str(folder / f"barcode{index:02d}/reads.fastq.gz")])})
            values = {"mode": "ont", "ont_folder": str(folder), "ont_barcodes": "barcode01,barcode02,barcode03",
                      "ont_sample_names": ",".join(names), "ont_caller": "ichorcna", "ont_binsize_kb": 500,
                      "threads": 4, "lpwgs_root": str(reference), "hg38_auto_download": False,
                      "outdir": str(source / "results"), "sample_metadata": str(metadata),
                      "execution_backend": "conda", "variant_specimen_type": "fresh",
                      "run_variants": False, "methylation": False, "methylation_modbam": str(bam),
                      "sturgeon_license_acknowledged": True, **resources}
            (config_dir / "run.yml").write_text(render_flat_yaml(values))
            original = {path: path.read_bytes() for path in base.rglob("*") if path.is_file()}
            source_files = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
            state = WebState(base, initial)
            state.hardware = dict(HARDWARE)
            system = state.system()
            restored = system["cpg"]
            defaults = system["defaults"]
            self.assertFalse(defaults["accept_sturgeon_license"], "A prior project must not grant new license consent")
            self.assertEqual(Path(restored["source_project"]), source)
            self.assertEqual([row["name"] for row in restored["selections"]], names)
            self.assertTrue(all(row["type"] == "cancer" for row in restored["selections"]))
            assignments = [{"id": 0, "classifier": "marlin"}, {"id": 1, "classifier": "sturgeon"},
                           {"id": 2, "classifier": ""}]
            projects = []
            for offset, classifier in enumerate(("marlin", "sturgeon")):
                project = Path(restored["projects"][classifier])
                self.assertNotEqual(project, source)
                self.assertEqual(project.parent, source.parent)
                allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[classifier])
                with contextlib.redirect_stdout(io.StringIO()):
                    saved = state.prepare({
                        "scan_id": restored["scan"]["scan_id"], "project": str(project),
                        "samples": restored["selections"], "threads": defaults["threads"],
                        "backend": defaults["backend"], "analysis": "methylation", "classifier": classifier,
                        "methylation_assignments": assignments, "methylation_source": defaults["methylation_source"],
                        "methylation_path": defaults["methylation_path"], "reference": defaults["reference"],
                        "reference_path": defaults["reference_path"], "variants": False,
                        "resource_paths": {key: value for key, value in restored["resource_paths"].items() if key in allowed},
                        "variant_specimen_type": defaults["variant_specimen_type"],
                        "accept_sturgeon_license": classifier == "sturgeon",
                    })
                self.assertTrue(saved["valid"], saved["check"])
                config = load_flat_yaml(Path(saved["config_path"]))
                self.assertTrue(config["methylation_only"])
                self.assertFalse(config["run_variants"])
                self.assertEqual(config["methylation_classifier"], classifier)
                self.assertEqual(config["methylation_modbam"], str(bam))
                self.assertEqual(Path(config["lpwgs_root"]), reference)
                selected, = parse_ont_samples(config)
                self.assertEqual((selected.sample, selected.barcode), (names[offset], f"barcode{offset + 1:02d}"))
                self.assertEqual(saved["reviewed_summary"]["methylation_group"],
                                 {"classifier": classifier, "included": 1, "excluded": 2})
                code, output = self.cli("run", "--backend", "conda", "--config", saved["config_path"], "--dry-run")
                self.assertEqual(code, 0, output)
                self.assertIn("oncotracer-native-dry-run-v1", output)
                self.assertFalse(Path(config["outdir"]).exists())
                self.assertFalse((project / "reference").exists())
                self.assertIsNone(state.job)
                projects.append(project)
            self.assertNotEqual(*projects)
            self.assertEqual(original, {path: path.read_bytes() for path in original})
            self.assertEqual(source_files, {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()})
            for classifier in ("marlin", "sturgeon"):
                choice = parse_arguments(["--interactive", "--cpg-from", str(source), "--classifier", classifier])
                self.assertEqual(choice.classifier, classifier)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the static demo contract")
    def test_static_cpg_demo_filters_the_selected_group(self):
        assets = ROOT / "docs/assets/setup-demo"
        script = """
const assert=require('node:assert/strict');
const window={},document={addEventListener(){}},location={href:'https://example.test/'};
"""
        script += (assets / "computer-browser.js").read_text()
        script += (assets / "mock-api.js").read_text().replace(
            "__VARIANT_INSTALL_GUIDES__", "{}").replace("__ANNOVAR_RESOURCE_HELP__", "{}")
        script += r"""
const scan=demoApi('/api/scan',{mode:'ont',folder:demoPaths.ont});
const payload={scan_id:scan.scan_id,project:'/demo/projects/cpg-marlin',analysis:'methylation',
  classifier:'marlin',backend:'conda',threads:4,caller:'ichorcna',binsize:500,reports:false,
  reference:'download',methylation_source:'modbam',methylation_path:'/demo/nanopore/bam_pass',
  samples:scan.samples.map((sample,id)=>({id,name:['LEUKEMIA','CNS','UNKNOWN'][id],type:'cancer'})),
  methylation_assignments:[{id:0,classifier:'marlin'},{id:1,classifier:'sturgeon'},{id:2,classifier:''}]};
for(const [classifier,name,barcode] of [['marlin','LEUKEMIA','barcode01'],['sturgeon','CNS','barcode02']]){
  const before=JSON.stringify(payload);
  const prepared=demoApi('/api/prepare',{...payload,classifier,project:'/demo/projects/cpg-'+classifier});
  assert.equal(prepared.valid,true);
  assert.deepEqual(prepared.check.plan.samples,[name]);
  assert.ok(prepared.config.includes('ont_barcodes: '+JSON.stringify(barcode)));
  assert.ok(prepared.config.includes('methylation_classifier: '+JSON.stringify(classifier)));
  assert.ok(prepared.check.warnings.some(w=>w.includes('2 other/unassigned')&&w.includes('not queued')));
  assert.equal(demoState.job,null);
  assert.equal(JSON.stringify(payload),before);
}
for(const assignments of [[],[{id:0,classifier:'marlin'}],
  [{id:0,classifier:'marlin'},{id:0,classifier:'sturgeon'},{id:2,classifier:''}],
  [{id:0,classifier:''},{id:1,classifier:'sturgeon'},{id:2,classifier:''}]]){
  assert.throws(()=>demoApi('/api/prepare',{...payload,methylation_assignments:assignments}));
}
assert.throws(()=>demoApi('/api/prepare',{...payload,analysis:'both'}));
assert.equal(demoState.job,null);
"""
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

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
                if path.relative_to(ROOT).as_posix() == "docs/quickstart_terminal.md":
                    # This guide explicitly follows the linked public download
                    # page. Exercise its directory setup without downloading.
                    self.create_tables((ROOT / "docs/public_cohort.md").read_text(), base)
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
        self.assertIn("oncotracer_cli.nextflow_gateway", (ROOT / "main.nf").read_text())
        self.assertIn("mainScript", (ROOT / "nextflow.config").read_text())
        for relative in (
            "run_test.sh", "environment.yml",
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
