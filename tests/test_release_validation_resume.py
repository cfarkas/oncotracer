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


class SharedReferencePreflightTests(unittest.TestCase):
    """Reject incomplete legacy caches before running expensive baselines."""

    def make_reference(self, root):
        files = (
            "genome.fa.fai", "genome.dict", "genome.fa.map-ont.mmi",
            "bwa/genome.amb", "bwa/genome.ann", "bwa/genome.bwt",
            "bwa/genome.pac", "bwa/genome.sa",
            ".oncotracer/reference-index-provenance/samurai-hg38.bwa-index.json",
            ".oncotracer/reference-index-provenance/samurai-hg38-map-ont.minimap2-index.json",
        )
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (root / "genome.fa").write_text(">chr1\nACGT\n")
        locks = root / ".oncotracer/locks"
        locks.mkdir()
        for name in ("samurai-hg38.bwa-index.lock", "samurai-hg38-map-ont.minimap2-index.lock"):
            (locks / name).touch()

    def check_reference(self, root):
        source = (ROOT / "scripts/validate_v2_release.sh").read_text()
        function = "verify_reference_directory() {" + source.split(
            "verify_reference_directory() {", 1)[1].split(
            '\nverify_reference_directory "$SHARED_REFERENCE"', 1)[0]
        return subprocess.run(
            ["bash", "-c", function + '\nverify_reference_directory "$1"', "check", str(root)],
            capture_output=True, text=True, timeout=10)

    def test_complete_reference_accepts_empty_reader_locks_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_reference(root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = self.check_reference(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_legacy_indexes_without_provenance_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_reference(root)
            metadata = root / ".oncotracer/reference-index-provenance/samurai-hg38.bwa-index.json"
            metadata.unlink()
            result = self.check_reference(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("verified native reference bundle", result.stderr)
            self.assertFalse(metadata.exists())

    def test_missing_or_symlinked_reader_lock_is_rejected(self):
        for linked in (False, True):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.make_reference(root)
                lock = root / ".oncotracer/locks/samurai-hg38.bwa-index.lock"
                lock.unlink()
                if linked:
                    lock.symlink_to(root / "genome.fa")
                result = self.check_reference(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("reader lock", result.stderr)
                self.assertEqual((root / "genome.fa").read_text(), ">chr1\nACGT\n")


if __name__ == "__main__":
    unittest.main()
