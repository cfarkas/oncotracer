#!/usr/bin/env python3
"""Release blockers for tracked-tree and native-payload hygiene."""

from __future__ import annotations

import ast
import re
import shlex
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
INTENTIONAL_PYTHON_NEXTFLOW_AUDIT_LINES = {
    "oncotracer_cli/engine.py": {
        'if "nextflow" in trace_text.lower():',
    },
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


def static_python_text(node: ast.AST) -> str | bytes | None:
    """Return static str/bytes composition without lossy bytes decoding."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_python_text(node.left)
        right = static_python_text(node.right)
        if type(left) is not type(right) or left is None or right is None:
            return None
        return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                part = static_python_text(value.value)
                if isinstance(part, bytes):
                    part = str(part)
            else:
                part = static_python_text(value)
            if part is None or isinstance(part, bytes):
                return None
            parts.append(part)
        return "".join(parts)
    return None


def contains_nextflow_run_tokens(value: str) -> bool:
    """Recognize a static Nextflow run command despite shell whitespace/paths."""
    try:
        tokens = shlex.split(value, comments=False, posix=True)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        executable = PurePosixPath(token.replace("\\", "/")).name.casefold()
        if executable not in {"nextflow", "nextflow.exe"}:
            continue
        return any(item.casefold() == "run" for item in tokens[index + 1 :])
    return False


def python_nextflow_command_violations(relative: str, text: str) -> list[str]:
    """Reject static Python command tokens that could execute Nextflow."""
    try:
        syntax = ast.parse(text)
    except SyntaxError as error:
        return [f"{relative}:{error.lineno or 0}: Python payload does not parse"]
    lines = text.splitlines()
    allowed = INTENTIONAL_PYTHON_NEXTFLOW_AUDIT_LINES.get(relative, set())
    violations: set[str] = set()
    for node in ast.walk(syntax):
        static_value = static_python_text(node)
        if static_value is None or not hasattr(node, "lineno"):
            continue
        if isinstance(static_value, bytes):
            # subprocess accepts bytes argv. Undecodable literals cannot be
            # classified safely, so never normalize them with lossy decoding.
            try:
                value = static_value.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                line = lines[node.lineno - 1].strip()
                violations.add(
                    f"{relative}:{node.lineno}: non-UTF-8 static bytes literal "
                    f"cannot be safely audited: {line}"
                )
                continue
        else:
            value = static_value
        stripped = value.strip()
        executable = PurePosixPath(stripped.replace("\\", "/")).name.casefold()
        standalone = executable in {"nextflow", "nextflow.exe"}
        if not standalone and not contains_nextflow_run_tokens(value):
            continue
        line = lines[node.lineno - 1].strip()
        if standalone and line in allowed:
            continue
        violations.add(
            f"{relative}:{node.lineno}: forbidden static Nextflow command token: {line}"
        )
    return sorted(violations)


def materialized_tree_violations(staging: Path) -> list[str]:
    """Audit every readable file that will enter the native zipapp."""
    violations: list[str] = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        name = PurePosixPath(relative)
        if name.suffix == ".nf" or name.name == "nextflow.config":
            violations.append(f"legacy workflow file leaked by {relative}")
        text = readable_text(path)
        if text is None:
            continue
        for marker in FORBIDDEN_LOCAL_TEXT:
            if marker in text:
                violations.append(f"local path leaked by {relative}: {marker}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(
                    f"credential-like literal leaked by {relative}: {pattern.pattern}"
                )
        folded = text.casefold()
        for command in NEXTFLOW_COMMAND_PATTERNS:
            if command in folded:
                violations.append(
                    f"legacy workflow command leaked by {relative}: {command}"
                )
        if path.suffix == ".py":
            violations.extend(python_nextflow_command_violations(relative, text))
    return violations


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
            payload_names = {
                path.relative_to(payload).as_posix()
                for path in payload.rglob("*")
                if path.is_file()
            }
            staging_names = {
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*")
                if path.is_file()
            }
            self.assertTrue(REQUIRED_NATIVE_EXCLUSIONS.isdisjoint(payload_names))
            self.assertIn("__main__.py", staging_names)
            self.assertIn("oncotracer_cli/cli.py", staging_names)
            violations = materialized_tree_violations(staging)
            self.assertEqual(
                violations,
                [],
                "native staging hygiene violations:\n" + "\n".join(violations),
            )

    def test_materialized_tree_rejects_python_nextflow_list_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            builder.copy_payload_from_tree(ROOT, staging)
            adversarial = staging / "oncotracer_cli" / "adversarial.py"
            adversarial.write_text(
                "import subprocess\n"
                'subprocess.run(["nextflow", "run", "main.nf"], check=True)\n',
                encoding="utf-8",
            )
            violations = materialized_tree_violations(staging)
            self.assertTrue(
                any(
                    "oncotracer_cli/adversarial.py:2: forbidden static Nextflow "
                    "command token" in violation
                    for violation in violations
                ),
                violations,
            )

    def test_materialized_tree_rejects_python_nextflow_bytes_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            builder.copy_payload_from_tree(ROOT, staging)
            adversarial = staging / "oncotracer_cli" / "adversarial_bytes.py"
            adversarial.write_text(
                "import subprocess\n"
                'subprocess.run([b"/usr/bin/nextflow", b"run", b"main.nf"])\n'
                'subprocess.Popen([b"nextflow", b"run", b"main.nf"])\n'
                'subprocess.run([b"./nextflow", b"run", b"main.nf"])\n'
                'subprocess.Popen([br"C:\\tools\\nextflow.exe", b"run", '
                'b"main.nf"])\n',
                encoding="utf-8",
            )
            violations = materialized_tree_violations(staging)
            for line_number in range(2, 6):
                with self.subTest(line_number=line_number):
                    self.assertTrue(
                        any(
                            f"oncotracer_cli/adversarial_bytes.py:{line_number}: "
                            "forbidden static Nextflow command token" in violation
                            for violation in violations
                        ),
                        violations,
                    )

    def test_malformed_and_non_utf8_python_bytes_fail_closed(self) -> None:
        malformed = (
            "import subprocess\n"
            'subprocess.run([b"\\xff\\xfe", b"run"], check=True)\n'
        )
        violations = python_nextflow_command_violations(
            "oncotracer_cli/malformed_bytes.py",
            malformed,
        )
        self.assertTrue(
            any(
                "oncotracer_cli/malformed_bytes.py:2: non-UTF-8 static bytes "
                "literal cannot be safely audited" in violation
                for violation in violations
            ),
            violations,
        )
        invalid_escape = (
            "import subprocess\n"
            'subprocess.Popen([b"\\xZZ", b"run", b"main.nf"])\n'
        )
        syntax_violations = python_nextflow_command_violations(
            "oncotracer_cli/malformed_bytes_syntax.py",
            invalid_escape,
        )
        self.assertTrue(
            any("Python payload does not parse" in item for item in syntax_violations),
            syntax_violations,
        )

    def test_python_nextflow_audit_allowlist_is_exact(self) -> None:
        audit = (
            "def verify_trace(trace_text):\n"
            '    if "nextflow" in trace_text.lower():\n'
            '        raise ValueError("forbidden workflow command")\n'
        )
        self.assertEqual(
            python_nextflow_command_violations("oncotracer_cli/engine.py", audit),
            [],
        )
        self.assertNotEqual(
            python_nextflow_command_violations("oncotracer_cli/not_engine.py", audit),
            [],
        )
        changed_audit = audit.replace(
            "trace_text.lower()",
            "str(trace_text).lower()",
        )
        self.assertNotEqual(
            python_nextflow_command_violations(
                "oncotracer_cli/engine.py", changed_audit
            ),
            [],
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
