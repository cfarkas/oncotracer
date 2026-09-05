#!/usr/bin/env python3
from __future__ import annotations

import re
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
    def test_source_installation_uses_main_without_stale_branch_links(self) -> None:
        for relative in ("README.md", "docs/installation.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(
                "git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src",
                text,
            )
            self.assertNotIn("improve/beginner-setup-methylation", text)
            self.assertIn("not in the v2.0.0 release executable", text)

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
            "docs/quick_start.md": 850,
            "docs/configuration/methylation.md": 1100,
        }
        for relative, words in budgets.items():
            with self.subTest(page=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertLess(len(text.split()), words)
                self.assertNotRegex(text, r"run_QS\d+\.sh")
                self.assertIn("--config", text)
                self.assertIn("--backend", text)

    def test_primary_docs_use_global_binary(self) -> None:
        for path in ACTIVE:
            text = path.read_text(encoding="utf-8")
            self.assertIn("oncotracer", text.lower(), path)
            self.assertNotIn("nextflow run", text.lower(), path)

    def test_readme_is_a_landing_page(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 100)
        self.assertIn(
            "docs/installation.md#1-install-the-stable-copied-executable", text
        )
        self.assertIn("oncotracer setup --project", text)
        self.assertIn("not in the v2.0.0 release executable", text)
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
            "parity_release.md",
            "migration_v1_to_v2.md",
        ):
            self.assertIn(page, text)

    def test_standalone_payload_cache_contract_is_documented(self) -> None:
        architecture = (ROOT / "docs/native_architecture.md").read_text(
            encoding="utf-8"
        )
        installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
        troubleshooting = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")

        cache_layout = "$XDG_CACHE_HOME/oncotracer/2.0.0/<executable-sha256>/payload"
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
        installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/native_architecture.md").read_text(
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
