#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

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
    def test_primary_docs_use_global_binary(self) -> None:
        for path in ACTIVE:
            text = path.read_text(encoding="utf-8")
            self.assertIn("oncotracer", text.lower(), path)
            self.assertNotIn("nextflow run", text.lower(), path)

    def test_readme_is_a_landing_page(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 100)
        self.assertIn("sudo install -m 0755 oncotracer", text)
        self.assertIn("complete documentation", text.lower())

    def test_all_markdown_fences_are_balanced(self) -> None:
        for path in [ROOT / "README.md", *ROOT.glob("docs/*.md")]:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("```" ) % 2, 0, path)

    def test_optional_classifier_is_documented_as_native(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        configuration = (ROOT / "docs/configuration_v2.md").read_text(encoding="utf-8").lower()
        architecture = (ROOT / "docs/native_architecture.md").read_text(encoding="utf-8").lower()
        self.assertIn("run_cna_classifier: true", readme)
        self.assertIn("run_cna_classifier: true", configuration)
        self.assertIn("optional cna classifier", architecture)
        self.assertNotIn("nextflow run", configuration)

    def test_mkdocs_contains_assurance_pages(self) -> None:
        text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        for page in ("native_architecture.md", "parity_release.md", "migration_v1_to_v2.md"):
            self.assertIn(page, text)

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
        for option in ("--validation-root", "--threads", "--resume", "--shared-reference"):
            self.assertIn(option, text)
        self.assertIn('[[ -n "$VALIDATION_ROOT_ARG" ]] || die', text)
        self.assertIn('[[ "$VALIDATION_ROOT" != "/" ]] || die', text)
        self.assertIn('case "$VALIDATION_ROOT/" in', text)
        self.assertIn('"$REPOSITORY_ROOT/"*)', text)
        self.assertIn('.oncotracer-v2-release-validation-root', text)

        self.assertNotIn("rm -rf", text)
        self.assertNotIn("nvidia-smi", text.lower())
        self.assertIn('export CUDA_VISIBLE_DEVICES=""', text)
        self.assertIn('export NVIDIA_VISIBLE_DEVICES="void"', text)
        self.assertNotIn("nextflow -version", text)
        self.assertNotIn("nextflow run", text)
        self.assertEqual(text.count('"$NEXTFLOW" -log'), 3)
        self.assertEqual(
            text.count('[[ "$(command -v nextflow)" == "$NEXTFLOW" ]]'), 2
        )
        self.assertEqual(
            text.count('PATH="$TOOL_BIN:$PATH" command -v nextflow'), 2
        )
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
        self.assertIn(
            "carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed",
            text,
        )
        for environment in ("core", "qdnaseq", "ichorcna", "classifier", "gistic"):
            self.assertIn(f'ENV_ROOT/{environment}', text)
        for threshold in ("0.80", "0.90", "0.95", "0.98", "0.08"):
            self.assertIn(threshold, text)

        self.assertIn('cd "$TMP_DIR"', text)
        self.assertIn('env -u PYTHONHOME -u PYTHONPATH "$BINARY" "$@"', text)
        self.assertIn("'^Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file$'", text)
        self.assertIn("'^Usage: .*/readCounter \\[options\\] <BAM file>$'", text)
        self.assertGreaterEqual(text.count('[[ "$rc" -ne 1 ]]'), 2)
        self.assertIn('[[ "$rc" -ne 255 ]]', text)
        self.assertIn('[[ "${#mcr_roots[@]}" -ne 1 ]]', text)
        self.assertNotIn("|| true", text[text.index("probe_picard() {"):text.index("action_install_environments() {")])
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
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
