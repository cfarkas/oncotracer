#!/usr/bin/env python3
"""Release blockers for tracked-tree and native-payload hygiene."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_native_binary as builder  # noqa: E402


FORBIDDEN_LOCAL_TEXT = (
    "/".join(("", "media", "server", "")),
    "/".join(("", "home", "server", "")),
    "LPWGS" + "_" + "2025",
    "oncotracer-v2-" + "clean",
    "oncotracer_fresh_" + "validation",
)
FORBIDDEN_TRACKED_NAMES = {"commands" + ".txt"}
FORBIDDEN_ARTIFACT_SUFFIXES = (
    ".log",
    ".trace",
    ".bam",
    ".cram",
    ".pod5",
    ".fastq.gz",
    ".fq.gz",
    ".fastq",
    ".fq",
    ".vcf",
    ".bcf",
    ".sif",
    ".vcf.gz",
    ".simg",
    ".img",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
    ".7z",
    ".rar",
    ".iso",
)
SECRET_PATTERNS = (
    re.compile("gh" + r"p_[A-Za-z0-9]{20,}"),
    re.compile("github_" + r"pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile("BEGIN " + r"(?:RSA |OPENSSH |EC )?PRIVATE KEY"),
)
NEXTFLOW_COMMAND_PATTERNS = (
    "next" + "flow run",
    "next" + "flow -version",
    "command -v next" + "flow",
)
REQUIRED_NATIVE_EXCLUSIONS = {
    "bin/cna_classifier_nf/README.md",
    "bin/scripts/install_oncotracer.sh",
    "bin/scripts/prepare_samurai_source.sh",
    "bin/scripts/qdnaseq_local_pon.R",
    "bin/scripts/run_ifcnv_ont_lpwgs.py",
    "bin/scripts/run_illumina_samurai_fastq.sh",
    "bin/scripts/run_ont_samurai_barcodes.sh",
    "bin/scripts/run_qdnaseq_local_pon.sh",
    "examples/hcc1143_lpwgs/README.md",
    "examples/hcc1143_lpwgs/run_example.sh",
    "examples/prjna754199/PROVENANCE.md",
    "examples/prjna754199/README.md",
    "examples/prjna754199/run_example.sh",
}


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        ROOT / raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def readable_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


class ProductHygieneTests(unittest.TestCase):
    def test_tracked_tree_has_no_local_paths_secrets_or_runtime_artifacts(self) -> None:
        paths = tracked_paths()
        relative_names = {
            path.relative_to(ROOT).as_posix()
            for path in paths
            if path.exists()
        }
        self.assertTrue(FORBIDDEN_TRACKED_NAMES.isdisjoint(relative_names))
        for relative in sorted(relative_names):
            folded = relative.casefold()
            self.assertFalse(
                folded.endswith(FORBIDDEN_ARTIFACT_SUFFIXES),
                f"runtime/data/archive artifact is tracked: {relative}",
            )
            path = ROOT / relative
            text = readable_text(path)
            if text is None:
                continue
            for marker in FORBIDDEN_LOCAL_TEXT:
                self.assertNotIn(marker, text, f"local path leaked by {relative}")
            for pattern in SECRET_PATTERNS:
                self.assertIsNone(
                    pattern.search(text),
                    f"credential-like literal leaked by {relative}",
                )

    def test_materialized_native_payload_excludes_legacy_and_local_state(self) -> None:
        self.assertEqual(
            set(builder.NATIVE_PAYLOAD_EXCLUDED_PATHS),
            REQUIRED_NATIVE_EXCLUSIONS,
        )
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            builder.copy_payload_from_tree(ROOT, staging)
            payload = staging / "payload"
            names = {
                path.relative_to(payload).as_posix()
                for path in payload.rglob("*")
                if path.is_file()
            }
            self.assertTrue(REQUIRED_NATIVE_EXCLUSIONS.isdisjoint(names))
            self.assertFalse(
                any(
                    PurePosixPath(name).suffix == ".nf"
                    or PurePosixPath(name).name == "nextflow.config"
                    for name in names
                )
            )
            for name in sorted(names):
                text = readable_text(payload / name)
                if text is None:
                    continue
                for marker in FORBIDDEN_LOCAL_TEXT:
                    self.assertNotIn(marker, text, f"local path leaked by payload/{name}")
                folded = text.casefold()
                for command in NEXTFLOW_COMMAND_PATTERNS:
                    self.assertNotIn(
                        command,
                        folded,
                        f"legacy workflow command leaked by payload/{name}",
                    )

    def test_every_nonlegacy_svg_is_native(self) -> None:
        for path in sorted((ROOT / "docs" / "assets").rglob("*.svg")):
            if any(part in {"legacy", "migration"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("nextflow", text, path)
            self.assertNotIn("main.nf", text, path)


if __name__ == "__main__":
    unittest.main()
