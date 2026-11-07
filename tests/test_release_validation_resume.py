"""Exercise rebuilding an invalidated release-validation artifact stage."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    "oncotracer", "oncotracer.provenance.json", "oncotracer.version.txt",
    "oncotracer.help.txt", "SHA256SUMS",
)


class ReleaseValidationResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        archive = subprocess.check_output(
            ["git", "-c", "tar.umask=0002", "archive", "--format=tar", cls.commit], cwd=ROOT)
        cls.digest = hashlib.sha256(archive).hexdigest()
        source = (ROOT / "scripts/validate_v2_release.sh").read_text()
        cls.action = "action_build_binary() {" + source.split(
            "action_build_binary() {", 1)[1].split("\nverify_build_binary() {", 1)[0]

    def run_stage(self, folder, session):
        env = os.environ.copy()
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            env[key] = str(folder / key.lower())
        script = "\n".join([
            "set -euo pipefail",
            "REPOSITORY_ROOT=" + shlex.quote(str(ROOT)),
            "RELEASE_CANDIDATE_DIR=" + shlex.quote(str(folder)),
            'BINARY="$RELEASE_CANDIDATE_DIR/oncotracer"',
            "SOURCE_COMMIT=" + shlex.quote(self.commit),
            "SOURCE_SHA256=" + shlex.quote(self.digest),
            "SESSION_ID=" + shlex.quote(session),
            'die() { printf "%s\\n" "$*" >&2; exit 1; }',
            'run_copied_binary() { "$BINARY" "$@"; }',
            self.action,
            "action_build_binary",
        ])
        return subprocess.run(["bash", "-c", script], cwd=folder, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_invalidated_stage_preserves_all_previous_artifacts_and_rebuilds(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self.run_stage(folder, "initial")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertFalse(list(folder.glob("previous.*")))
            original = {name: (folder / name).read_bytes() for name in ARTIFACTS}
            second = self.run_stage(folder, "retry")
            self.assertEqual(second.returncode, 0, second.stderr)
            for name, contents in original.items():
                self.assertEqual((folder / "previous.retry" / name).read_bytes(), contents)
                self.assertEqual((folder / name).read_bytes(), contents)
            check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=folder,
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_unexpected_artifact_type_is_rejected_before_moving_anything(self):
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "oncotracer").write_bytes(b"previous binary")
                target = folder / "unrelated"; target.write_bytes(b"preserve me")
                bad = folder / "oncotracer.provenance.json"
                if kind == "symlink":
                    bad.symlink_to(target)
                else:
                    bad.mkdir()
                result = self.run_stage(folder, "retry")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unexpected candidate artifact type", result.stderr)
                self.assertEqual((folder / "oncotracer").read_bytes(), b"previous binary")
                self.assertEqual(target.read_bytes(), b"preserve me")
                self.assertFalse(list(folder.glob("previous.*")))

    def test_archive_collision_does_not_replace_previous_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "oncotracer").write_bytes(b"previous binary")
            previous = folder / "previous.retry"; previous.mkdir()
            (previous / "oncotracer").write_bytes(b"older binary")
            result = self.run_stage(folder, "retry")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((folder / "oncotracer").read_bytes(), b"previous binary")
            self.assertEqual((previous / "oncotracer").read_bytes(), b"older binary")


if __name__ == "__main__":
    unittest.main()
