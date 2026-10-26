#!/usr/bin/env python3
"""Exercise PyPI release gates without contacting GitHub or uploading packages."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"
MAIN = "a" * 40
OTHER = "b" * 40
WHEEL = "oncotracer-2.1.1-py3-none-any.whl"
SDIST = "oncotracer-2.1.1.tar.gz"


def workflow_step(name: str) -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    section = workflow.split(f"      - name: {name}\n", 1)[1]
    section = section.split("\n      - ", 1)[0]
    script = section.split("        run: |\n", 1)[1]
    return textwrap.dedent(script).strip() + "\n"


class PyPIPublishGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer-pypi-gates-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("python", "python3"):
            (self.bin / name).symlink_to(sys.executable)
        gh = self.bin / "gh"
        gh.write_text(
            '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
            'test "$*" = "api /repos/cfarkas/oncotracer/commits/main --jq .sha"\n'
            'test "${FAKE_GH_FAIL:-0}" = 0\n'
            'printf "%s\\n" "$FAKE_MAIN"\n',
            encoding="utf-8",
        )
        gh.chmod(0o755)
        git = self.bin / "git"
        git.write_text(
            '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
            'case "$*" in\n'
            '  "rev-parse HEAD") printf "%s\\n" "$FAKE_HEAD" ;;\n'
            '  "status --porcelain") printf "%s" "${FAKE_DIRTY:-}" ;;\n'
            '  "-c tar.umask=0002 archive --format=tar $REQUESTED_COMMIT")\n'
            '    printf "%s" "$FAKE_ARCHIVE" ;;\n'
            '  *) exit 98 ;;\n'
            'esac\n',
            encoding="utf-8",
        )
        git.chmod(0o755)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "oncotracer"\nversion = "2.1.1"\n',
            encoding="utf-8",
        )
        self.output = self.root / "github-output"
        self.output.write_text("", encoding="utf-8")
        self.environment = {
            "PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root), "RUNNER_TEMP": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "GITHUB_REPOSITORY": "cfarkas/oncotracer",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": MAIN, "GITHUB_OUTPUT": str(self.output),
            "REQUESTED_COMMIT": MAIN, "REQUESTED_VERSION": "2.1.1",
            "SOURCE_COMMIT": MAIN, "FAKE_HEAD": MAIN, "FAKE_MAIN": MAIN,
            "FAKE_ARCHIVE": "exact source archive fixture",
        }
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.artifacts = {WHEEL: b"validated wheel bytes", SDIST: b"validated source bytes"}
        for name, content in self.artifacts.items():
            (self.dist / name).write_bytes(content)
        self.hashes = {
            name: hashlib.sha256(content).hexdigest()
            for name, content in self.artifacts.items()
        }
        self.environment["EXPECTED_HASHES"] = json.dumps(self.hashes)

    def run_step(self, name: str, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", workflow_step(name)], cwd=self.root,
            env={**self.environment, **overrides}, text=True,
            capture_output=True, check=False, timeout=15,
        )

    def gate(self, **overrides: str) -> subprocess.CompletedProcess:
        return self.run_step(
            "Authenticate the requested version and exact current main", **overrides
        )

    def publisher(self, **overrides: str) -> subprocess.CompletedProcess:
        return self.run_step(
            "Recheck main and the exact validated distribution bytes", **overrides
        )

    def assert_refused(self, result: subprocess.CompletedProcess, message: str = "") -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        if message:
            self.assertIn(message, result.stderr)

    def test_exact_current_main_and_project_version_are_recorded(self) -> None:
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        digest = hashlib.sha256(self.environment["FAKE_ARCHIVE"].encode()).hexdigest()
        self.assertEqual(
            self.output.read_text(encoding="utf-8"),
            f"source_commit={MAIN}\nsource_sha256={digest}\nversion=2.1.1\n",
        )

    def test_dispatch_gate_rejects_wrong_context_before_emitting_outputs(self) -> None:
        cases = (
            {"GITHUB_REPOSITORY": "other/oncotracer"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_SHA": OTHER}, {"FAKE_HEAD": OTHER},
            {"FAKE_MAIN": OTHER}, {"FAKE_GH_FAIL": "1"},
            {"FAKE_DIRTY": " M pyproject.toml\n"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_refused(self.gate(**overrides))
                self.assertEqual(self.output.read_text(encoding="utf-8"), "")

    def test_gate_rejects_incomplete_or_malformed_commit(self) -> None:
        for value in (MAIN[:7], MAIN.upper(), MAIN + "\nversion=9", "refs/heads/main", ""):
            with self.subTest(commit=value):
                self.assert_refused(self.gate(REQUESTED_COMMIT=value))
                self.assertEqual(self.output.read_text(encoding="utf-8"), "")

    def test_gate_rejects_wrong_project_name_or_version(self) -> None:
        self.assert_refused(self.gate(REQUESTED_VERSION="2.2.0"), "pyproject.toml")
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "unrelated"\nversion = "2.1.1"\n', encoding="utf-8"
        )
        self.assert_refused(self.gate(), "Unexpected package name")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "")

    def test_user_inputs_cannot_execute_shell_or_inject_step_outputs(self) -> None:
        sentinel = self.root / "must-not-exist"
        for field, value in (
            ("REQUESTED_VERSION", f"$(touch {sentinel})"),
            ("REQUESTED_VERSION", f"2.1.1`; touch {sentinel}; `"),
            ("REQUESTED_VERSION", "2.1.1\nsource_commit=" + OTHER),
            ("REQUESTED_COMMIT", f"{MAIN}; touch {sentinel}"),
        ):
            with self.subTest(field=field, value=value):
                self.assert_refused(self.gate(**{field: value}))
                self.assertFalse(sentinel.exists())
                self.assertEqual(self.output.read_text(encoding="utf-8"), "")

    def test_hash_record_matches_exact_distribution_bytes(self) -> None:
        self.dist.rename(self.root / "distributions")
        result = self.run_step("Record the verified artifact hashes")
        self.assertEqual(result.returncode, 0, result.stderr)
        prefix, value = self.output.read_text(encoding="utf-8").strip().split("=", 1)
        self.assertEqual(prefix, "artifact_hashes")
        self.assertEqual(json.loads(value), self.hashes)
        self.assertEqual(
            json.loads((self.root / "python-distribution-sha256.json").read_text()),
            self.hashes,
        )

    def test_hash_record_refuses_extra_distribution(self) -> None:
        self.dist.rename(self.root / "distributions")
        (self.root / "distributions" / "unexpected.whl").write_bytes(b"extra")
        self.assert_refused(self.run_step("Record the verified artifact hashes"))
        self.assertEqual(self.output.read_text(), "")

    def test_hash_record_refuses_symlink_distribution(self) -> None:
        self.dist.rename(self.root / "distributions")
        wheel = self.root / "distributions" / WHEEL
        wheel.rename(self.root / WHEEL)
        wheel.symlink_to(self.root / WHEEL)
        self.assert_refused(self.run_step("Record the verified artifact hashes"))
        self.assertEqual(self.output.read_text(), "")

    def test_publisher_accepts_only_exact_validated_bytes_on_current_main(self) -> None:
        result = self.publisher()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_publisher_rechecks_main_and_dispatch_context(self) -> None:
        for overrides in (
            {"FAKE_MAIN": OTHER}, {"FAKE_GH_FAIL": "1"},
            {"GITHUB_REPOSITORY": "other/oncotracer"},
            {"GITHUB_EVENT_NAME": "push"}, {"GITHUB_REF": "refs/tags/v2.1.1"},
            {"GITHUB_SHA": OTHER}, {"SOURCE_COMMIT": MAIN[:7]},
        ):
            with self.subTest(overrides=overrides):
                self.assert_refused(self.publisher(**overrides))

    def test_publisher_refuses_tampered_distribution(self) -> None:
        (self.dist / WHEEL).write_bytes(b"replacement wheel")
        self.assert_refused(self.publisher(), "changed after validation")

    def test_publisher_refuses_extra_or_missing_files(self) -> None:
        extra = self.dist / "unvalidated.whl"
        extra.write_bytes(b"extra")
        self.assert_refused(self.publisher(), "validated file set")
        extra.rename(self.root / "extra")
        (self.dist / SDIST).rename(self.root / SDIST)
        self.assert_refused(self.publisher(), "validated file set")

    def test_publisher_refuses_symlinks_and_directories(self) -> None:
        wheel = self.dist / WHEEL
        wheel.rename(self.root / WHEEL)
        wheel.symlink_to(self.root / WHEEL)
        self.assert_refused(self.publisher(), "not a regular file")
        wheel.rename(self.root / "wheel-link")
        wheel.mkdir()
        self.assert_refused(self.publisher(), "not a regular file")

    def test_publisher_refuses_malformed_hash_manifest(self) -> None:
        manifests = (
            "not JSON", "[]", "null", json.dumps({WHEEL: self.hashes[WHEEL]}),
            json.dumps({**self.hashes, WHEEL: "0" * 63}),
            json.dumps({**self.hashes, WHEEL: 123}),
            json.dumps({"../" + WHEEL: self.hashes[WHEEL], SDIST: self.hashes[SDIST]}),
            json.dumps({"other-2.1.1.whl": self.hashes[WHEEL], SDIST: self.hashes[SDIST]}),
            json.dumps({"oncotracer-2.1.1.zip": self.hashes[WHEEL], SDIST: self.hashes[SDIST]}),
        )
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                self.assert_refused(self.publisher(EXPECTED_HASHES=manifest))


class PyPIPublishWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.build = self.workflow.split("  build:\n", 1)[1].split("\n  smoke:\n", 1)[0]
        self.smoke = self.workflow.split("  smoke:\n", 1)[1].split("\n  publish:\n", 1)[0]
        self.publish = self.workflow.split("  publish:\n", 1)[1]

    def test_publication_is_manual_and_permissions_are_job_scoped(self) -> None:
        triggers = self.workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("  workflow_dispatch:\n", triggers)
        self.assertNotRegex(triggers, r"(?m)^  (push|pull_request|workflow_run|release|schedule):")
        for field in ("version", "source_commit"):
            self.assertIn(f"      {field}:\n", triggers)
        self.assertEqual(triggers.count("required: true"), 2)
        self.assertIn("\npermissions: {}\n", self.workflow)
        self.assertNotIn("id-token:", self.build)
        self.assertNotIn("id-token:", self.smoke)
        self.assertEqual(self.publish.count("id-token: write"), 1)
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_oidc_publisher_receives_only_validated_artifacts(self) -> None:
        self.assertIn("needs: [build, smoke]", self.publish)
        self.assertIn("environment:\n      name: pypi", self.publish)
        self.assertIn("url: https://pypi.org/p/oncotracer", self.publish)
        self.assertIn("artifact-ids: ${{ needs.build.outputs.artifact_id }}", self.publish)
        self.assertNotIn("actions/checkout", self.publish)
        self.assertNotIn("setup-python", self.publish)
        self.assertNotIn("pip install", self.publish)
        self.assertNotIn("-m build", self.publish)
        self.assertNotRegex(self.publish, r"(?m)^\s*(password|repository-url):")
        self.assertNotIn("secrets.", self.workflow)
        for option in ("attestations: true", "verify-metadata: true", "skip-existing: false"):
            self.assertIn(option, self.publish)
        self.assertLess(
            self.publish.index("Recheck main and the exact validated distribution bytes"),
            self.publish.index("Publish to PyPI using Trusted Publishing"),
        )

    def test_every_action_uses_an_immutable_commit(self) -> None:
        actions = re.findall(r"(?m)^\s*(?:- )?uses: ([^\s#]+)", self.workflow)
        self.assertGreaterEqual(len(actions), 8)
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
        self.assertEqual(self.workflow.count("persist-credentials: false"), 2)

    def test_untrusted_dispatch_values_enter_as_environment_not_shell_code(self) -> None:
        for name in ("version", "source_commit"):
            self.assertEqual(self.workflow.count("${{ inputs." + name + " }}"), 1)
        for name in (
            "Authenticate the requested version and exact current main",
            "Record the verified artifact hashes",
            "Recheck main and the exact validated distribution bytes",
        ):
            self.assertNotIn("${{", workflow_step(name))

    def test_build_validates_both_formats_and_exact_source_identity(self) -> None:
        self.assertIn('python -m build --outdir "$RUNNER_TEMP/distributions"', self.build)
        self.assertIn('python -m twine check --strict "$RUNNER_TEMP"/distributions/*', self.build)
        self.assertIn("python scripts/verify_python_dist.py", self.build)
        self.assertIn('--expected-commit "$SOURCE_COMMIT"', self.build)
        self.assertIn('--expected-source-sha256 "$SOURCE_SHA256"', self.build)
        self.assertIn('--expected-version "$EXPECTED_VERSION"', self.build)
        self.assertIn('test -z "$(git status --porcelain)"', self.build)
        self.assertIn("if-no-files-found: error", self.build)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", self.build)
        self.assertNotIn("conda env create", self.workflow)
        self.assertNotIn("install --conda", self.workflow)

    def test_installed_wheel_smoke_covers_all_supported_python_versions(self) -> None:
        self.assertIn('python: ["3.10", "3.11", "3.12", "3.13"]', self.smoke)
        self.assertIn("fail-fast: false", self.smoke)
        self.assertIn("artifact-ids: ${{ needs.build.outputs.artifact_id }}", self.smoke)
        self.assertIn('--no-index --no-deps "$RUNNER_TEMP"/distributions/*.whl', self.smoke)
        self.assertIn('cd "$RUNNER_TEMP/smoke-cwd"', self.smoke)
        self.assertIn('"$GITHUB_WORKSPACE/scripts/smoke_python_install.py"', self.smoke)
        self.assertIn('--python "$RUNNER_TEMP/wheel-environment/bin/python"', self.smoke)
        self.assertIn('--expected-version "$EXPECTED_VERSION"', self.smoke)
        self.assertIn('export HOME="$RUNNER_TEMP/smoke-home"', self.smoke)
        self.assertIn("unset PYTHONPATH PYTHONHOME", self.smoke)

    def test_safety_tests_run_in_publishing_and_native_ci(self) -> None:
        self.assertIn("python -m unittest -v tests/test_pypi_publish.py", self.build)
        native_ci = (ROOT / ".github" / "workflows" / "native-v2-ci.yml").read_text()
        self.assertIn("tests/test_pypi_publish.py", native_ci)


if __name__ == "__main__":
    unittest.main()
