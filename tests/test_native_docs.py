#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = [
    ROOT / "README.md",
    ROOT / "docs/index.md",
    ROOT / "docs/installation.md",
    ROOT / "docs/quick_start.md",
    ROOT / "docs/public_cohort.md",
    ROOT / "docs/auto_params.md",
    ROOT / "docs/running.md",
    ROOT / "docs/containers.md",
    ROOT / "docs/poetry.md",
    ROOT / "docs/configuration_v2.md",
    ROOT / "docs/gallery.md",
    ROOT / "docs/citation_research_use.md",
    ROOT / "docs/troubleshooting.md",
    ROOT / "docs/parity_release.md",
]


class NativeDocumentationTests(unittest.TestCase):
    def assert_recorded_source_matches(self, relative: str, expected: str) -> None:
        from oncotracer_cli.cli import DEFAULT_IMAGE

        content = (ROOT / relative).read_bytes()
        compatibility = json.loads(
            (ROOT / "docs/assets/tutorial/image-default-compatibility.json").read_text()
        )
        self.assertEqual(compatibility["schema"], 1)
        self.assertEqual(set(compatibility["files"]), {
            "oncotracer_cli/cli.py", "docs/assets/setup-demo/mock-api.js",
        })
        self.assertEqual(compatibility["current_image"], DEFAULT_IMAGE)
        transition = compatibility["files"].get(relative)
        if transition:
            self.assertEqual(transition["recorded_sha256"], expected)
            self.assertEqual(transition["current_sha256"], hashlib.sha256(content).hexdigest())
            current = compatibility["current_image"].encode()
            recorded = compatibility["recorded_image"].encode()
            self.assertNotEqual(current, recorded)
            self.assertEqual(content.count(current), 1)
            content = content.replace(current, recorded)
        self.assertEqual(hashlib.sha256(content).hexdigest(), expected, relative)

    def test_pip_installation_needs_no_checkout_and_source_route_uses_main(self) -> None:
        for relative in ("README.md", "docs/installation.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("pip install oncotracer", text)
            self.assertNotIn("git clone", text)
            self.assertNotIn("improve/beginner-setup-methylation", text)
            self.assertNotIn("not in the v2.0.0 release executable", text)
        advanced = (ROOT / "docs/installation_details.md").read_text(encoding="utf-8")
        self.assertIn("git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src", advanced)
        self.assertIn("pip install -e ./oncotracer-src", advanced)

    def test_pages_validates_prs_and_deploys_only_main_artifacts(self) -> None:
        text = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertIn("pull_request", workflow["on"])
        self.assertEqual(workflow["on"]["push"]["branches"], ["main"])
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertIn("github.event.pull_request.number", workflow["concurrency"]["group"])

        build = workflow["jobs"]["build"]
        deploy = workflow["jobs"]["deploy"]
        trusted_main = "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
        self.assertEqual(deploy["if"], trusted_main)
        self.assertEqual(deploy["needs"], "build")
        self.assertEqual(deploy["permissions"], {"pages": "write", "id-token": "write"})
        self.assertEqual(deploy["environment"]["name"], "github-pages")
        self.assertNotIn("permissions", build)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("gh-deploy", text)
        self.assertNotIn("pull_request_target", text)
        self.assertIn("python3 tests/test_docs_style.py", text)
        self.assertIn("python3 -m unittest tests.test_native_docs -q", text)
        self.assertIn("mkdocs build --strict --site-dir site", text)
        self.assertIn('Path("site/build-info.json")', text)
        self.assertIn('"source_commit": os.environ["GITHUB_SHA"]', text)
        self.assertEqual(build["steps"][0]["with"]["persist-credentials"], "false")
        for action in ("actions/configure-pages@v5", "actions/upload-pages-artifact@v4"):
            step = next(step for step in build["steps"] if step.get("uses") == action)
            self.assertEqual(step["if"], trusted_main)
        self.assertEqual(deploy["steps"][0]["uses"], "actions/deploy-pages@v4")

    def test_beginner_guides_stay_short_and_use_public_commands(self) -> None:
        budgets = {
            "README.md": 800,
            "docs/setup.md": 950,
            "docs/installation.md": 650,
            "docs/quick_start.md": 850,
            "docs/public_cohort.md": 850,
            "docs/quickstart_terminal.md": 1200,
            "docs/configuration/methylation.md": 1100,
            "docs/auto_params.md": 900,
            "docs/full_tutorial.md": 1200,
            "docs/six_tumor_four_normal.md": 650,
            "docs/configuration/pathology.md": 750,
        }
        for relative, words in budgets.items():
            with self.subTest(page=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertLess(len(text.split()), words)
                self.assertNotRegex(text, r"run_QS\d+\.sh")
                if relative not in {"README.md", "docs/installation.md", "docs/quick_start.md", "docs/public_cohort.md"}:
                    self.assertIn("--config", text)
                self.assertIn("--backend", text)

    def test_primary_docs_use_global_binary(self) -> None:
        for path in ACTIVE:
            text = path.read_text(encoding="utf-8")
            self.assertIn("oncotracer", text.lower(), path)
            self.assertNotIn("carlosfarkas/oncotracer:latest", text)

    def test_current_gateway_is_supported_and_retired_scripts_are_rejected(self) -> None:
        from tests.test_examples_start_from_clone import retired_public_entrypoints
        supported = (
            "oncotracer --interactive --backend conda",
            "nextflow run /absolute/path/to/oncotracer-src/main.nf --interactive --backend conda",
            "nextflow run cfarkas/oncotracer -r main --interactive",
        )
        for relative in ("README.md", "docs/index.md", "docs/setup.md", "docs/nextflow_entry.md"):
            for command in supported:
                with self.subTest(page=relative, command=command):
                    self.assertEqual(retired_public_entrypoints(relative, command), [])
                    for launcher in ("run_QS1.sh", "run_QS2.sh", "run_example.sh"):
                        self.assertEqual(retired_public_entrypoints(relative, command + " " + launcher), [launcher])

    def test_quickstart_recordings_match_current_ui_and_linked_media(self) -> None:
        from oncotracer_cli.web_ui import PAGE

        assets = ROOT / "docs/assets/tutorial"
        record = json.loads((assets / "quick-start-recording.json").read_text(encoding="utf-8"))
        self.assertIs(record["passed"], True)
        self.assertIs(record["analysis_started"], False)
        self.assertIs(record["real_reads_downloaded"], False)
        active_job_review = json.loads(
            (assets / "active-job-ui-compatibility.json").read_text(encoding="utf-8")
        )
        self.assertEqual(active_job_review["schema"], 1)
        self.assertEqual(active_job_review["current_page_sha256"],
                         hashlib.sha256(PAGE.encode("utf-8")).hexdigest())
        self.assertTrue(active_job_review["replacements"])
        recorded_page = PAGE
        for replacement in active_job_review["replacements"]:
            self.assertNotEqual(replacement["recorded"], replacement["current"])
            self.assertTrue(replacement["current"])
            self.assertEqual(recorded_page.count(replacement["current"]), 1)
            recorded_page = recorded_page.replace(
                replacement["current"], replacement["recorded"], 1
            )
        self.assertEqual(active_job_review["recorded_page_sha256"],
                         hashlib.sha256(recorded_page.encode("utf-8")).hexdigest())
        compatibility = record.get("ui_compatibility")
        if compatibility:
            import html
            from oncotracer_cli.variant_model_assets import CLAIR3_PROFILES
            self.assertEqual(compatibility["reviewed_ui_page_sha256"],
                             hashlib.sha256(recorded_page.encode("utf-8")).hexdigest())
            profiles = compatibility["added_unrecorded_clair3_profiles"]
            self.assertTrue(profiles)
            self.assertEqual(len(profiles), len(set(profiles)))
            for profile in profiles:
                option = ('<option value="' + html.escape(profile, quote=True) + '">' +
                          html.escape(CLAIR3_PROFILES[profile]["label"]) + '</option>')
                self.assertEqual(recorded_page.count(option), 1)
                recorded_page = recorded_page.replace(option, "")
        self.assertEqual(record["ui_page_sha256"],
                         hashlib.sha256(recorded_page.encode("utf-8")).hexdigest())
        self.assert_recorded_source_matches(
            "docs/assets/setup-demo/mock-api.js", record["mock_api_sha256"]
        )
        pages = {"quick-start-illumina-ont": None,
                 "quick-start-illumina": "docs/quick_start.md",
                 "quick-start-ont": "docs/quick_start.md",
                 "quick-start-hcc1143": "docs/public_cohort.md"}
        self.assertEqual({video["name"] for video in record["videos"]}, set(pages))
        self.assertEqual(len(record["videos"]), len(pages))
        for video in record["videos"]:
            with self.subTest(video=video["name"]):
                self.assert_recording_media(video, pages[video["name"]])
                self.assertNotIn("/api/run", json.dumps(video.get("scenes", [])))

    def assert_recording_media(self, video, relative, *, extra_extensions=()) -> None:
        assets = ROOT / "docs/assets/tutorial"
        page = (ROOT / relative).read_text(encoding="utf-8") if relative else None
        self.assertGreater(video["duration_seconds"], 0)
        self.assertEqual(set(video["files"]), {".mp4", ".jpg", ".vtt", ".txt", *extra_extensions})
        for extension, item in video["files"].items():
            self.assertEqual(item["name"], video["name"] + extension)
            content = (assets / item["name"]).read_bytes()
            self.assertGreater(len(content), 0)
            self.assertEqual(len(content), item["bytes"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
            if page is not None and extension in {".mp4", ".jpg", ".vtt", ".txt"}:
                self.assertIn(item["name"], page)
        captions = (assets / (video["name"] + ".vtt")).read_text(encoding="utf-8")
        self.assertTrue(captions.startswith("WEBVTT"))
        self.assertIn("-->", captions)
        self.assertTrue((assets / (video["name"] + ".txt")).read_text(encoding="utf-8").strip())

    def test_terminal_recordings_match_source_and_saved_only_sessions(self) -> None:
        assets = ROOT / "docs/assets/tutorial"
        record = json.loads((assets / "quick-start-terminal-recording.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], 1)
        self.assertIs(record["passed"], True)
        self.assertIs(record["analysis_started"], False)
        self.assertIs(record["synthetic_inputs"], True)
        self.assertIs(record["real_reads_downloaded"], False)
        self.assertTrue({"oncotracer_cli/wizard.py", "oncotracer_cli/setup.py",
                         "oncotracer_cli/cli.py", "oncotracer_cli/discovery.py",
                         "scripts/record_terminal_quickstarts.py"}.issubset(record["source_files"]))
        for relative, expected in record["source_files"].items():
            self.assertFalse(Path(relative).is_absolute())
            self.assertNotIn("..", Path(relative).parts)
            self.assert_recorded_source_matches(relative, expected)
        pages = {"quick-start-terminal-illumina": "docs/quick_start.md",
                 "quick-start-terminal-ont": "docs/quick_start.md",
                 "quick-start-terminal-hcc1143": "docs/public_cohort.md"}
        self.assertEqual({video["name"] for video in record["videos"]}, set(pages))
        self.assertEqual(len(record["videos"]), len(pages))
        for video in record["videos"]:
            with self.subTest(video=video["name"]):
                self.assert_recording_media(video, pages[video["name"]], extra_extensions=(".cast",))
                self.assertEqual(video["exit_code"], 0)
                command = shlex.split(video["command"]) if isinstance(video["command"], str) else video["command"]
                self.assertIn("setup", command)
                self.assertIn("--terminal", command)
                self.assertNotIn("--run", command)
                self.assertTrue(video["answers"][-1]["prompt"].startswith("Final action"))
                self.assertEqual(video["answers"][-1]["answer"], "save")
                checks = video["config_checks"]
                self.assertIs(checks["saved"], True)
                for key in ("analysis_started", "reference_created", "results_created"):
                    self.assertIs(checks[key], False, key)
                expected_mode, expected_caller, expected_bins = (
                    ("ont", "ichorcna", 500) if video["name"].endswith("-ont")
                    else ("illumina", "qdnaseq", 100)
                )
                self.assertEqual((checks["mode"], checks["caller"], checks["bin_size_kb"]),
                                 (expected_mode, expected_caller, expected_bins))
                self.assertEqual(checks["threads"], 4)
                self.assertIs(checks["run_variants"], False)
                self.assertIs(checks["hg38_auto_download"], True)
                expected_samples = {
                    "quick-start-terminal-illumina": ["ERR12341627"],
                    "quick-start-terminal-ont": ["DRR165691"],
                    "quick-start-terminal-hcc1143": ["HCC1143_BEZ235", "HCC1143_DMSO", "HCC1143_TRAMETINIB"],
                }[video["name"]]
                self.assertEqual(checks["samples"], expected_samples)
                self.assertEqual([item["sample"] for item in checks["metadata"]], expected_samples)
                for item in checks["metadata"]:
                    self.assertEqual((item["sample_type"], item["analysis_role"]), ("cancer", "tumor"))
                    sample = item["sample"]
                    expected_files = (["DRR165691_1.fastq.gz"] if expected_mode == "ont" else
                                      [f"{sample}_{mate}.fastq.gz" for mate in
                                       (("R1", "R2") if sample.startswith("HCC1143_") else ("1", "2"))])
                    self.assertEqual(sorted(Path(file).name for file in json.loads(item["fastq_files"])), expected_files)
                cast = [json.loads(line) for line in (assets / (video["name"] + ".cast")).read_text(encoding="utf-8").splitlines()]
                self.assertEqual(cast[0]["version"], 2)
                output = "".join(event[2] for event in cast[1:] if event[1] == "o")
                self.assertIn("Final action", output)
                self.assertIn("Saved without starting analysis", output)

    def test_architecture_overview_keeps_implementation_details_separate(self) -> None:
        text = (ROOT / "docs/native_architecture.md").read_text(encoding="utf-8")
        self.assertLess(len(text.split()), 400)
        for required in (
            "# How OncoTracer works",
            "## What happens to your data?",
            "## What is optional?",
            "## Can I restart a run?",
            "## How do I check what ran?",
            "architecture_details.md",
            "installation.md",
            "outputs.md",
        ):
            self.assertIn(required, text)
        for technical_detail in (
            "authenticated rollback journal",
            "canonical prefix",
            "SHA-256",
            "zipapp",
            "SIGKILL",
        ):
            self.assertNotIn(technical_detail, text)

        # Keep existing bookmarks usable while headings become reader-friendly.
        for anchor in (
            "native-architecture",
            "stage-graph",
            "single-file-executable",
            "installer-ownership-boundary",
            "native-invariant",
        ):
            self.assertIn(f'<a id="{anchor}"></a>', text)

        nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
        getting_started = next(
            item["Getting Started"] for item in nav if "Getting Started" in item
        )
        self.assertIn({"How OncoTracer Works": "native_architecture.md"}, getting_started)
        advanced = (ROOT / "docs/architecture_details.md").read_text(encoding="utf-8")
        self.assertIn("native_architecture.md", advanced)

    def test_readme_is_a_landing_page(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        # Separate fenced terminal examples need more lines without more prose.
        # The independent 800-word landing-page limit remains unchanged.
        self.assertLess(len(text.splitlines()), 120)
        self.assertIn("docs/installation.md", text)
        headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
        self.assertTrue(headings)
        self.assertEqual(headings[0].casefold(), "quick start")
        self.assertIn("oncotracer --interactive --backend conda", text)
        self.assertNotIn("not in the v2.0.0 release executable", text)
        self.assertIn("complete documentation", text.lower())

    def test_all_markdown_fences_are_balanced(self) -> None:
        for path in [ROOT / "README.md", *ROOT.glob("docs/*.md")]:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("```") % 2, 0, path)

    def test_optional_classifier_is_documented_as_native(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        configuration = (
            (ROOT / "docs/configuration_v2.md").read_text(encoding="utf-8").lower()
        )
        architecture = (
            (ROOT / "docs/native_architecture.md").read_text(encoding="utf-8").lower()
        )
        self.assertIn("run_cna_classifier: true", readme)
        self.assertIn("run_cna_classifier: true", configuration)
        self.assertIn("optional cna classifier", architecture)
        self.assertNotIn("nextflow run", configuration)

    def test_parity_documentation_matches_the_executable_gate(self) -> None:
        text = (ROOT / "docs/parity_release.md").read_text(encoding="utf-8")
        self.assertIn("state-specific CNA genomic-coverage recall and precision", text)
        self.assertIn("corrected input log₂-signal Pearson correlation", text)
        self.assertIn("complete ten-process set", text)
        self.assertIn("exact Nextflow work directory", text)
        self.assertIn("An incomplete resume fragment", text)
        self.assertNotIn("four-process final-resume", text)
        self.assertNotIn("event recall and precision of at least", text)
        self.assertNotIn("refined-bin Pearson correlation", text)

    def test_mkdocs_contains_assurance_pages(self) -> None:
        text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        for page in (
            "native_architecture.md",
            "architecture_details.md",
            "parity_release.md",
            "installation_details.md",
        ):
            self.assertIn(page, text)
        self.assertNotIn("legacy_v1.md", text)
        self.assertNotIn("migration_v1_to_v2.md", text)

    def test_standalone_payload_cache_contract_is_documented(self) -> None:
        architecture = (ROOT / "docs/architecture_details.md").read_text(
            encoding="utf-8"
        )
        installation = (ROOT / "docs/installation_details.md").read_text(encoding="utf-8")
        troubleshooting = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")

        cache_layout = "$XDG_CACHE_HOME/oncotracer/2.1.0/<executable-sha256>/payload"
        self.assertIn(cache_layout, architecture)
        self.assertIn(cache_layout, installation)
        for required in (
            "canonical path",
            "normalized mode",
            "size",
            "SHA-256",
            "Symlinks",
            "special files",
        ):
            self.assertIn(required, architecture)
        self.assertIn("process-scoped temporary payload", architecture)
        self.assertIn("does not populate the persistent cache", installation)
        self.assertIn("unset ONCOTRACER_PAYLOAD_CACHE", troubleshooting)
        self.assertIn(
            "preserves them rather than recursively deleting", troubleshooting
        )
        self.assertIn("success, error, or interruption", troubleshooting)

    def test_installer_ownership_and_atomic_replacement_are_documented(self) -> None:
        installation = (ROOT / "docs/installation_details.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/architecture_details.md").read_text(
            encoding="utf-8"
        )
        parameters = (ROOT / "docs/configuration/parameter_reference.md").read_text(
            encoding="utf-8"
        )
        troubleshooting = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
        poetry = (ROOT / "docs/poetry.md").read_text(encoding="utf-8")

        for required in (
            "never adopts a populated Conda directory",
            "ownership-checked lock",
            "Unrelated siblings",
            "same-directory transaction",
            "container provenance",
            "Existing unowned files",
        ):
            self.assertIn(required, installation)
        for required in (
            "authenticated rollback journal",
            "fixed isolated `poetry-runtime` child",
            "created directly at its final canonical prefix",
            "exact file inventory",
            "checkout-local virtual",
            "Neither `--force` path adopts or pre-deletes",
        ):
            self.assertIn(required, architecture)
        self.assertIn("Backend-irrelevant options are errors", parameters)
        self.assertIn("cannot adopt or erase an unowned", troubleshooting)
        self.assertIn("does not alter Poetry's global environment", poetry)
        self.assertIn("poetry-runtime/bin/oncotracer", poetry)
        self.assertNotIn("poetry run oncotracer", poetry)

    def test_native_ci_uses_exact_semantic_tool_probes(self) -> None:
        workflow = (ROOT / ".github/workflows/native-v2-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("bash -lc", workflow)
        self.assertNotRegex(
            workflow,
            r"command -v\s+(?:bwa|picard|readCounter|gistic2|Rscript)",
        )
        self.assertNotRegex(
            workflow,
            r"(?m)(?:bwa|picard|readCounter|gistic2).*2>&1\s*\|\|\s*true\s*$",
        )

        for status_check in (
            'test "$BWA_STATUS" -eq 1',
            'test "$PICARD_STATUS" -eq 1',
            'test "$READCOUNTER_STATUS" -eq 255',
            'test "$GISTIC_STATUS" -eq 0',
        ):
            self.assertGreaterEqual(workflow.count(status_check), 2, status_check)
        for semantic_output in (
            "Program: bwa",
            "USAGE: PicardCommandLine",
            "Please specify a BAM file.",
            "Usage: gp_gistic2_from_seg",
        ):
            self.assertGreaterEqual(workflow.count(semantic_output), 2, semantic_output)

        self.assertGreaterEqual(workflow.count('test "${#MCR_ROOTS[@]}" -eq 1'), 2)
        self.assertGreaterEqual(
            workflow.count("env -u LD_LIBRARY_PATH -u LD_LIBRARY_PATH_MCR"), 2
        )
        self.assertGreaterEqual(workflow.count("CUDA_VISIBLE_DEVICES="), 2)
        self.assertGreaterEqual(workflow.count("NVIDIA_VISIBLE_DEVICES=void"), 2)

    def test_server_validation_driver_is_safe_and_auditable(self) -> None:
        driver = ROOT / "scripts/validate_v2_release.sh"
        text = driver.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        for option in (
            "--validation-root",
            "--threads",
            "--resume",
            "--shared-reference",
        ):
            self.assertIn(option, text)
        self.assertIn('[[ -n "$VALIDATION_ROOT_ARG" ]] || die', text)
        self.assertIn('[[ "$VALIDATION_ROOT" != "/" ]] || die', text)
        self.assertIn('case "$VALIDATION_ROOT/" in', text)
        self.assertIn('"$REPOSITORY_ROOT/"*)', text)
        self.assertIn(".oncotracer-v2-release-validation-root", text)

        self.assertNotIn("rm -rf", text)
        self.assertNotIn("nvidia-smi", text.lower())
        self.assertIn('export CUDA_VISIBLE_DEVICES=""', text)
        self.assertIn('export NVIDIA_VISIBLE_DEVICES="void"', text)
        self.assertNotIn("nextflow -version", text)
        self.assertNotIn("nextflow run", text)
        self.assertEqual(text.count('"$NEXTFLOW" -log'), 3)
        self.assertEqual(text.count('[[ "$(command -v nextflow)" == "$NEXTFLOW" ]]'), 2)
        self.assertEqual(text.count('PATH="$TOOL_BIN:$PATH" command -v nextflow'), 2)
        self.assertIn(
            'readonly NEXTFLOW_VERSION="26.04.6"',
            text,
        )
        self.assertIn(
            'readonly NEXTFLOW_SHA256="182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"',
            text,
        )
        self.assertIn(
            'readonly V1_COMMIT_EXPECTED="032c1268fa7fdcadc48087055066d7a9fc59bd89"',
            text,
        )
        self.assertIn(
            'readonly SAMURAI_COMMIT_EXPECTED="6a901940288b008237703c6b181d447e7dee4fcf"',
            text,
        )
        self.assertIn("refs/tags/${V1_TAG}^{commit}", text)
        self.assertIn("samurai-container-identities.tsv", text)
        self.assertIn("samurai-nextflow-audit.config", text)
        self.assertIn("oncotracer-samurai-trace-audit-v1", text)
        self.assertIn("from combine_nested_samurai_traces import combine_root", text)
        self.assertIn("from contextlib import redirect_stdout", text)
        self.assertIn("with redirect_stdout(sys.stderr):", text)
        self.assertIn("from verify_nested_samurai import find_compat_marker", text)
        self.assertIn("v1-ichorcna-plot-compat-SHA256SUMS", text)
        self.assertIn("withName: ICHORCNA_RUN", text)
        self.assertIn('"evidence_mode": "complete-combined-trace"', text)
        self.assertNotIn("exact-ont-final-resume-trace", text)
        self.assertIn('"contract_containers": sorted(expected_images[mode])', text)
        self.assertIn('"task_hash": task_hash', text)
        self.assertIn('"relative_path": marker_relative.as_posix()', text)
        self.assertIn("executor.queueSize = 4", text)
        self.assertIn("oncotracer_nested_audit_policy_sha256", text)
        self.assertNotIn("env.ONCOTRACER_NESTED_AUDIT_POLICY_SHA256", text)
        self.assertIn("cache = false", text)
        self.assertIn("$REPORT_DIR/frozen-v1.1-quickstart1-$SESSION_ID", text)
        self.assertIn("$REPORT_DIR/frozen-v1.1-quickstart2-$SESSION_ID", text)
        self.assertNotRegex(text, r"(?m)^\s+-resume\s*$")
        self.assertNotIn("selected = max(traces", text)
        self.assertIn('"container"', text)
        self.assertIn("unresolved or forbidden SAMURAI container", text)
        for expected_rows in (
            "illumina 12",
            "ont 10",
            "illumina 32",
        ):
            self.assertGreaterEqual(text.count(expected_rows), 2, expected_rows)
        for digest in (
            "e194048df39c3145d9b4e0a14f4da20b59d59250465b6f2a9cb698445fd45900",
            "fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75",
            "c6240b1bcc57de07d9a92373f6fad080870bba0075be6cd25c6d37179d928c72",
            "39fae3f3a2edb8cb174b3ffade1741b6b1ec850a323b4f7a0dca6908f2e49cf8",
        ):
            self.assertIn(digest, text)
        self.assertGreaterEqual(text.count("-c tar.umask=0002 archive"), 4)
        self.assertGreaterEqual(
            text.count("key=lambda candidate: candidate.relative_to(root).as_posix()"),
            2,
        )
        self.assertIn(
            'umask 0002\n      git -C "$REPOSITORY_ROOT" -c tar.umask=0002 archive',
            text,
        )
        self.assertIn(
            "carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed",
            text,
        )
        for environment in ("core", "qdnaseq", "ichorcna", "classifier", "gistic"):
            self.assertIn(f"ENV_ROOT/{environment}", text)
        for threshold in ("0.80", "0.90", "0.95", "0.98", "0.08"):
            self.assertIn(threshold, text)

        hosted_driver = (ROOT / "scripts/ci_native_parity.sh").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            hosted_driver.count('"$NEXTFLOW" -log "$NEXTFLOW_REPORT_ROOT/'), 3
        )
        self.assertIn("executor.queueSize = 4", hosted_driver)
        self.assertIn("seal_nested_config", hosted_driver)
        self.assertIn("oncotracer_nested_audit_policy_sha256", hosted_driver)
        self.assertNotIn("env.ONCOTRACER_NESTED_AUDIT_POLICY_SHA256", hosted_driver)
        self.assertIn("cache = false", hosted_driver)
        self.assertNotIn("-resume 2>&1", hosted_driver)
        self.assertIn(
            'readonly NEXTFLOW_REPORT_ROOT="$REPORT_ROOT/frozen-v1.1-$PARITY_SESSION_ID"',
            hosted_driver,
        )

        self.assertIn('cd "$TMP_DIR"', text)
        self.assertIn('env -u PYTHONHOME -u PYTHONPATH "$BINARY" "$@"', text)
        self.assertIn(
            "'^Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file$'", text
        )
        self.assertIn("'^Usage: .*/readCounter \\[options\\] <BAM file>$'", text)
        self.assertGreaterEqual(text.count('[[ "$rc" -ne 1 ]]'), 2)
        self.assertIn('[[ "$rc" -ne 255 ]]', text)
        self.assertIn('[[ "${#mcr_roots[@]}" -ne 1 ]]', text)
        self.assertNotIn(
            "|| true",
            text[
                text.index("probe_picard() {") : text.index(
                    "action_install_environments() {"
                )
            ],
        )
        for evidence in (
            'cp -a "$LOG_DIR/."',
            'cp -a "$REPORT_DIR/."',
            'cp "$CONTEXT_DIR"/*-output-SHA256SUMS',
            "QDNAseq.hg38.100kbp.SR50.rds.provenance.tsv",
        ):
            self.assertIn(evidence, text)

    def test_server_validation_tree_manifests_are_complete_and_self_safe(self) -> None:
        driver = (ROOT / "scripts/validate_v2_release.sh").read_text(encoding="utf-8")
        start = driver.index("write_tree_manifest() {")
        end = driver.index("\nverify_reference_directory() {", start)
        helpers = driver[start:end]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / "tree"
            scratch = root / "scratch"
            tree.mkdir()
            scratch.mkdir()
            manifest = tree / "SHA256SUMS"
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"""set -Eeuo pipefail
TMP_DIR="$1"
tree="$2"
manifest="$3"
{helpers}

printf 'original\n' > "$tree/payload.txt"
write_tree_manifest "$tree" "$manifest" SHA256SUMS
verify_tree_manifest "$tree" "$manifest"
if grep -Fq './SHA256SUMS' "$manifest"; then
  printf 'manifest included itself or its temporary file\n' >&2
  exit 91
fi

cp "$manifest" "$TMP_DIR/first-manifest"
write_tree_manifest "$tree" "$manifest" SHA256SUMS
cmp -s "$manifest" "$TMP_DIR/first-manifest"

printf 'unrecorded\n' > "$tree/extra.txt"
if verify_tree_manifest "$tree" "$manifest"; then
  printf 'verification accepted an unrecorded file\n' >&2
  exit 92
fi
rm -f "$tree/extra.txt"

printf 'changed\n' > "$tree/payload.txt"
if verify_tree_manifest "$tree" "$manifest"; then
  printf 'verification accepted changed file content\n' >&2
  exit 93
fi
write_tree_manifest "$tree" "$manifest" SHA256SUMS
verify_tree_manifest "$tree" "$manifest"

rm -f "$tree/payload.txt"
if verify_tree_manifest "$tree" "$manifest"; then
  printf 'verification accepted a missing file\n' >&2
  exit 94
fi
""",
                    "manifest-regression",
                    str(scratch),
                    str(tree),
                    str(manifest),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )


if __name__ == "__main__":
    unittest.main()
