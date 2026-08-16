#!/usr/bin/env python3
"""Prevent host-wide cleanup from entering executable CI command surfaces."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
ACTIVE_WORKFLOWS = (
    *sorted(WORKFLOW_ROOT.glob("*.yml")),
    *sorted(WORKFLOW_ROOT.glob("*.yaml")),
)
WHOLE_RUNNER_ACTIONS = (
    "jlumbroso/free-disk-space",
    "easimon/maximize-build-space",
    "adityagarg8/remove-unwanted-software",
)
RUN_RE = re.compile(r"^(?P<indent>[ \t]*)(?:-[ \t]+)?run:[ \t]*(?P<value>.*)$")
USES_RE = re.compile(r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*(?P<value>[^#]+?)\s*$")
HEREDOC_RE = re.compile(
    r"<<(?P<strip>-?)[ \t]*(?P<quote>['\"]?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=quote)"
)
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_PUNCTUATION = frozenset(";&|()")
SHELL_BOUNDARIES = frozenset({"then", "do", "else", "elif"})
ALLOWED_JOB_EXPANSIONS = frozenset(
    {
        "$RUNNER_TEMP",
        "${RUNNER_TEMP}",
        "${{ runner.temp }}",
        "$GITHUB_WORKSPACE",
        "${GITHUB_WORKSPACE}",
        "${{ github.workspace }}",
        "$GITHUB_RUN_ID",
        "${GITHUB_RUN_ID}",
        "${{ github.run_id }}",
        "$GITHUB_RUN_ATTEMPT",
        "${GITHUB_RUN_ATTEMPT}",
        "${{ github.run_attempt }}",
    }
)
REPOSITORY_ENTRYPOINT_ROOTS = frozenset({"scripts", "tests", "bin"})
VERIFIED_IMAGE_HELPER_SHA256 = (
    "23f64178e1cfcd0ea1b3c1e1c04b33bef36c424905f9a65477a9c156d683790f"
)


@dataclass(frozen=True)
class EmbeddedBody:
    line: int
    language: str
    source: str


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def workflow_command_surfaces(
    text: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Extract only executable ``run`` blocks and active ``uses`` values."""
    lines = text.splitlines()
    runs: list[tuple[int, str]] = []
    uses: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        uses_match = USES_RE.match(line)
        if uses_match:
            uses.append((index + 1, _yaml_scalar(uses_match.group("value"))))

        run_match = RUN_RE.match(line)
        if not run_match:
            index += 1
            continue
        value = run_match.group("value").strip()
        start_line = index + 1
        if not re.fullmatch(r"[|>][+-]?", value):
            runs.append((start_line, _yaml_scalar(value)))
            index += 1
            continue

        base_indent = len(run_match.group("indent").expandtabs(8))
        block: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip():
                block.append("")
                index += 1
                continue
            indentation = len(candidate) - len(candidate.lstrip(" \t"))
            if indentation <= base_indent:
                break
            block.append(candidate)
            index += 1
        nonempty_indents = [
            len(item) - len(item.lstrip(" \t")) for item in block if item.strip()
        ]
        dedent = min(nonempty_indents, default=base_indent + 2)
        body_lines = [item[dedent:] if item else "" for item in block]
        separator = "\n" if value.startswith("|") else " "
        runs.append((start_line, separator.join(body_lines)))
    return runs, uses


def _shell_tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _heredoc_command_language(command: str) -> str | None:
    try:
        tokens = _shell_tokens(command)
    except ValueError:
        return False
    for segment in command_segments(tokens):
        invocation = unwrap_command(segment)
        if invocation is None:
            continue
        executable, _ = invocation
        if re.fullmatch(r"python(?:[23](?:\.\d+)*)?", executable):
            return "python"
        if executable in {"bash", "sh"}:
            return "shell"
    return None


def extract_heredocs(shell: str) -> tuple[str, list[EmbeddedBody], list[str]]:
    """Remove data heredocs and return bodies executed by an interpreter."""
    lines = shell.splitlines()
    retained: list[str] = []
    executable_bodies: list[EmbeddedBody] = []
    errors: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        retained.append(line)
        match = HEREDOC_RE.search(line)
        if not match:
            index += 1
            continue
        delimiter = match.group("name")
        strip_tabs = match.group("strip") == "-"
        body_start = index + 2
        body: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            comparable = candidate.lstrip("\t") if strip_tabs else candidate.strip()
            if comparable == delimiter:
                break
            body.append(candidate.lstrip("\t") if strip_tabs else candidate)
            retained.append("")
            index += 1
        if index >= len(lines):
            errors.append(f"line {body_start}: unterminated heredoc {delimiter}")
            break
        retained.append("")
        language = _heredoc_command_language(line[: match.start()])
        if language is not None:
            executable_bodies.append(
                EmbeddedBody(body_start, language, "\n".join(body))
            )
        index += 1
    return "\n".join(retained), executable_bodies, errors


def shell_chunks(shell: str) -> tuple[list[tuple[int, list[str]]], list[str]]:
    """Tokenize logical shell lines while excluding comments and quoted prose."""
    chunks: list[tuple[int, list[str]]] = []
    errors: list[str] = []
    pending: list[str] = []
    start_line = 1
    for line_number, line in enumerate(shell.splitlines(), start=1):
        if not pending:
            start_line = line_number
        pending.append(line)
        candidate = "\n".join(pending)
        try:
            tokens = _shell_tokens(candidate)
        except ValueError:
            continue
        if tokens and tokens[-1] in {"|", "||", "&&"}:
            continue
        chunks.append((start_line, tokens))
        pending = []
    if pending:
        errors.append(f"line {start_line}: shell tokenization did not terminate")
    return chunks, errors


def command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        punctuation = token and set(token).issubset(SHELL_PUNCTUATION)
        if punctuation or token in SHELL_BOUNDARIES:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _skip_options(tokens: list[str], index: int, value_options: frozenset[str]) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        option = token.split("=", 1)[0]
        if not token.startswith("-"):
            return index
        index += 1
        if "=" not in token and option in value_options and index < len(tokens):
            index += 1
    return index


def unwrap_command(segment: list[str]) -> tuple[str, list[str]] | None:
    """Return the executable and argv after common shell wrappers."""
    index = 0
    while index < len(segment) and segment[index] in {"!", "if", "while", "until"}:
        index += 1
    while index < len(segment) and ASSIGNMENT_RE.match(segment[index]):
        index += 1
    while index < len(segment):
        executable = PurePosixPath(segment[index]).name
        if executable == "sudo":
            index = _skip_options(
                segment,
                index + 1,
                frozenset({"-u", "--user", "-g", "--group", "-h", "--host"}),
            )
        elif executable == "env":
            index = _skip_options(
                segment,
                index + 1,
                frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
            )
            while index < len(segment) and ASSIGNMENT_RE.match(segment[index]):
                index += 1
        elif executable in {"command", "builtin", "exec"}:
            index = _skip_options(segment, index + 1, frozenset())
        elif executable == "nice":
            index = _skip_options(segment, index + 1, frozenset({"-n", "--adjustment"}))
        elif executable == "ionice":
            index = _skip_options(
                segment,
                index + 1,
                frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid"}),
            )
        elif executable == "xargs":
            index = _skip_options(
                segment,
                index + 1,
                frozenset(
                    {
                        "-a",
                        "--arg-file",
                        "-E",
                        "--eof",
                        "-I",
                        "--replace",
                        "-L",
                        "--max-lines",
                        "-n",
                        "--max-args",
                        "-P",
                        "--max-procs",
                        "-s",
                        "--max-chars",
                    }
                ),
            )
        elif executable == "busybox":
            index += 1
        else:
            return executable, segment[index + 1 :]
        while index < len(segment) and ASSIGNMENT_RE.match(segment[index]):
            index += 1
    return None


def _has_run_identity(value: str) -> bool:
    return any(
        marker in value
        for marker in (
            "$GITHUB_RUN_ID",
            "${GITHUB_RUN_ID}",
            "${{ github.run_id }}",
        )
    )


def _only_proven_job_expansions(value: str) -> bool:
    remainder = value
    for marker in sorted(ALLOWED_JOB_EXPANSIONS, key=len, reverse=True):
        remainder = remainder.replace(marker, "JOB_VALUE")
    return "$" not in remainder and "`" not in remainder and "\n" not in remainder


def exact_job_path(value: str) -> bool:
    roots = (
        "$RUNNER_TEMP/",
        "${RUNNER_TEMP}/",
        "${{ runner.temp }}/",
        "$GITHUB_WORKSPACE/",
        "${GITHUB_WORKSPACE}/",
        "${{ github.workspace }}/",
    )
    if (
        not value.startswith(roots)
        or "oncotracer" not in value.casefold()
        or not _has_run_identity(value)
        or not _only_proven_job_expansions(value)
        or value.endswith("/")
    ):
        return False
    if any(marker in value for marker in ("*", "?", "[", "]", "$(", "`")):
        return False
    return ".." not in PurePosixPath(value.replace("${{ github.run_id }}", "run")).parts


def exact_job_docker_object(value: str) -> bool:
    return (
        "oncotracer" in value.casefold()
        and _has_run_identity(value)
        and _only_proven_job_expansions(value)
        and not any(marker in value for marker in ("*", "?", "[", "]", "$(", "`"))
    )


def _docker_command(args: list[str]) -> tuple[str, list[str]]:
    index = _skip_options(
        args,
        0,
        frozenset({"--config", "-c", "--context", "-H", "--host", "--log-level"}),
    )
    if index >= len(args):
        return "", []
    return args[index], args[index + 1 :]


def _conda_command(args: list[str]) -> str:
    index = _skip_options(
        args,
        0,
        frozenset(
            {
                "-n",
                "--name",
                "-p",
                "--prefix",
                "-r",
                "--root-prefix",
                "--rc-file",
            }
        ),
    )
    return args[index] if index < len(args) else ""


def _nextflow_command(args: list[str]) -> str:
    index = _skip_options(
        args,
        0,
        frozenset({"-C", "-log", "-syslog", "-trace"}),
    )
    return args[index] if index < len(args) else ""


def docker_invocation(
    args: list[str], *, allow_verified_manifest_reference: bool = False
) -> tuple[list[str], bool, bool]:
    """Return violations plus global-list and object-deletion indicators."""
    violations: list[str] = []
    command, remainder = _docker_command(args)
    if command == "buildx":
        index = _skip_options(
            remainder,
            0,
            frozenset({"--builder", "-b", "--config"}),
        )
        subcommand = remainder[index] if index < len(remainder) else ""
        if subcommand == "prune":
            violations.append("global Docker buildx prune")
            return violations, False, False
        if subcommand in {"rm", "remove"}:
            targets = [
                item
                for item in remainder[index + 1 :]
                if not item.startswith("-") and item != "--"
            ]
            if not targets or not all(
                exact_job_docker_object(item) for item in targets
            ):
                violations.append(
                    "Docker buildx deletion is not bound to exact "
                    "run-ID-qualified OncoTracer builders"
                )
            return violations, False, True
        return violations, subcommand in {"ls", "list"}, False
    if command == "compose":
        index = 0
        project = ""
        while index < len(remainder) and remainder[index].startswith("-"):
            option = remainder[index].split("=", 1)[0]
            if option in {"-p", "--project-name"}:
                if "=" in remainder[index]:
                    project = remainder[index].split("=", 1)[1]
                    index += 1
                elif index + 1 < len(remainder):
                    project = remainder[index + 1]
                    index += 2
                else:
                    index += 1
            elif option in {"-f", "--file", "--project-directory", "--env-file"}:
                index += 1 if "=" in remainder[index] else 2
            else:
                index += 1
        if index < len(remainder) and remainder[index] in {"down", "rm"}:
            if not exact_job_docker_object(project):
                violations.append(
                    "Docker Compose deletion is not bound to an exact run-ID-qualified project"
                )
            return violations, False, True
        return violations, False, False
    if command in {"system", "image", "container", "volume", "network", "builder"}:
        index = _skip_options(remainder, 0, frozenset({"--filter", "-f"}))
        if index < len(remainder) and remainder[index] == "prune":
            violations.append("global Docker prune")
            return violations, False, False

    global_listing = False
    deletion = False
    if command in {"ps", "images"}:
        global_listing = True
    elif (
        command in {"image", "container", "volume", "network", "builder"} and remainder
    ):
        global_listing = remainder[0] in {"ls", "list"}

    delete_args: list[str] = []
    if command in {"rm", "rmi"}:
        deletion = True
        delete_args = remainder
    elif (
        command in {"image", "container", "volume", "network", "builder"} and remainder
    ):
        if remainder[0] in {"rm", "remove"}:
            deletion = True
            delete_args = remainder[1:]
    if deletion:
        targets = [
            item for item in delete_args if not item.startswith("-") and item != "--"
        ]
        exact_targets = all(exact_job_docker_object(item) for item in targets)
        verified_manifest_target = allow_verified_manifest_reference and targets == [
            "$reference"
        ]
        if not targets or not (exact_targets or verified_manifest_target):
            violations.append(
                "Docker deletion is not bound to exact run-ID-qualified OncoTracer objects"
            )
    return violations, global_listing, deletion


def _contains_global_docker_listing(command: str) -> bool:
    try:
        tokens = _shell_tokens(command)
    except ValueError:
        return False
    for segment in command_segments(tokens):
        invocation = unwrap_command(segment)
        if invocation is None or invocation[0] != "docker":
            continue
        _, listing, _ = docker_invocation(invocation[1])
        if listing:
            return True
    return False


def docker_list_tainted_variables(shell: str) -> frozenset[str]:
    """Find variables fed by global Docker object inventories across lines."""
    tainted: set[str] = set()
    for match in re.finditer(
        r"(?m)^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"(?:\"|')?\$\((?P<body>[^)\n]+)\)",
        shell,
    ):
        if _contains_global_docker_listing(match.group("body")):
            tainted.add(match.group("name"))
    for match in re.finditer(
        r"(?m)^\s*(?:mapfile|readarray)(?:\s+-[A-Za-z]+)*\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*<\s*<\((?P<body>[^)]+)\)",
        shell,
    ):
        if _contains_global_docker_listing(match.group("body")):
            tainted.add(match.group("name"))
    changed = True
    while changed:
        changed = False
        for match in re.finditer(
            r"\bfor\s+(?P<loop>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+"
            r"(?P<source>\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)",
            shell,
        ):
            source = match.group("source").lstrip("${").rstrip("}")
            if source in tainted and match.group("loop") not in tainted:
                tainted.add(match.group("loop"))
                changed = True
    for match in re.finditer(
        r"\bwhile\s+read(?:\s+-[A-Za-z]+)*\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[^\n]*;\s*do[\s\S]*?done\s*"
        r"<\s*<\((?P<body>[^)]+)\)",
        shell,
    ):
        if _contains_global_docker_listing(match.group("body")):
            tainted.add(match.group("name"))
    return frozenset(tainted)


def rm_invocation(
    args: list[str], *, temporary_variables: frozenset[str] = frozenset()
) -> list[str]:
    recursive = False
    targets: list[str] = []
    options_done = False
    for item in args:
        if not options_done and item == "--":
            options_done = True
            continue
        if not options_done and item.startswith("-"):
            recursive = (
                recursive
                or item == "--recursive"
                or (not item.startswith("--") and "r" in item[1:])
            )
            continue
        targets.append(item)
    wildcard = any(
        any(marker in item for marker in ("*", "?", "[", "]")) for item in targets
    )
    host_absolute = any(
        item.startswith(("/", "~/", "$HOME/", "${HOME}/")) for item in targets
    )
    runner_scoped = any(
        item.startswith(
            (
                "$RUNNER_TEMP/",
                "${RUNNER_TEMP}/",
                "${{ runner.temp }}/",
                "$GITHUB_WORKSPACE/",
                "${GITHUB_WORKSPACE}/",
                "${{ github.workspace }}/",
            )
        )
        for item in targets
    )
    exact_temporary = bool(targets) and all(
        any(item in {f"${name}", f"${{{name}}}"} for name in temporary_variables)
        for item in targets
    )
    if recursive or wildcard or host_absolute or runner_scoped:
        if (
            not targets
            or wildcard
            or not (exact_temporary or all(exact_job_path(item) for item in targets))
        ):
            return [
                "filesystem deletion is not bound to exact run-ID-qualified job paths"
            ]
    return []


def rsync_invocation(args: list[str]) -> list[str]:
    destructive = any(
        item == "--delete" or item.startswith("--delete-") for item in args
    )
    if not destructive:
        return []
    operands = [item for item in args if item != "--" and not item.startswith("-")]
    destination = operands[-1] if operands else ""
    if not exact_job_path(destination):
        return ["rsync --delete destination is not an exact run-ID-qualified job path"]
    return []


def git_clean_invocation(args: list[str]) -> list[str]:
    index = 0
    directory = ""
    while index < len(args):
        item = args[index]
        if item in {"-C", "--git-dir", "--work-tree", "-c"}:
            if index + 1 >= len(args):
                return ["git clean invocation has an incomplete global option"]
            if item == "-C":
                directory = args[index + 1]
            index += 2
            continue
        if item.startswith("-C") and item != "-C":
            directory = item[2:]
            index += 1
            continue
        if item.startswith("-"):
            index += 1
            continue
        break
    if index >= len(args) or args[index] != "clean":
        return []
    if not exact_job_path(directory):
        return ["git clean is not bound to an exact run-ID-qualified job path"]
    return []


PYTHON_UNKNOWN_PATH = "\0oncotracer-unproved-path"
PYTHON_OWNED_TEMP_PREFIX = "\0oncotracer-secure-temp-object/"


class PythonCleanupVisitor(ast.NodeVisitor):
    _ALIAS_ATTRIBUTES = (
        "shutil_aliases",
        "rmtree_aliases",
        "os_aliases",
        "remove_aliases",
        "unlink_aliases",
        "path_aliases",
        "tempfile_aliases",
        "temporary_directory_aliases",
        "named_temporary_file_aliases",
        "mkdtemp_aliases",
    )

    def __init__(self) -> None:
        self.shutil_aliases: set[str] = set()
        self.rmtree_aliases: set[str] = set()
        self.os_aliases: set[str] = set()
        self.remove_aliases: set[str] = set()
        self.unlink_aliases: set[str] = set()
        self.path_aliases: set[str] = set()
        self.tempfile_aliases: set[str] = set()
        self.temporary_directory_aliases: set[str] = set()
        self.named_temporary_file_aliases: set[str] = set()
        self.mkdtemp_aliases: set[str] = set()
        self.calls: list[tuple[int, str, str | None]] = []
        self.symbols: dict[str, str] = {}

    def _temporary_placeholder(self, name: str) -> str:
        # The stdlib tempfile APIs create a new exact object with O_EXCL-style
        # ownership; only paths derived from that returned object get this token.
        return f"{PYTHON_OWNED_TEMP_PREFIX}{name}"

    def _temporary_call_kind(self, node: ast.Call) -> str | None:
        function = node.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            if function.value.id not in self.tempfile_aliases:
                return None
            return {
                "TemporaryDirectory": "temporary-directory",
                "NamedTemporaryFile": "named-temporary-file",
                "mkdtemp": "temporary-directory",
            }.get(function.attr)
        if not isinstance(function, ast.Name):
            return None
        if function.id in self.temporary_directory_aliases | self.mkdtemp_aliases:
            return "temporary-directory"
        if function.id in self.named_temporary_file_aliases:
            return "named-temporary-file"
        return None

    @staticmethod
    def _target_key(node: ast.AST) -> str | None:
        if isinstance(node, (ast.Name, ast.Attribute)):
            return ast.unparse(node)
        return None

    def _assign_symbol(self, target: ast.AST, rendered: str | None) -> None:
        key = self._target_key(target)
        if key is None:
            return
        if rendered is None:
            # A later unproved assignment must revoke an earlier proof.
            self.symbols[key] = PYTHON_UNKNOWN_PATH
        else:
            self.symbols[key] = rendered

    def _invalidate_import_alias(self, target: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        for aliases in (
            self.shutil_aliases,
            self.rmtree_aliases,
            self.os_aliases,
            self.remove_aliases,
            self.unlink_aliases,
            self.path_aliases,
            self.tempfile_aliases,
            self.temporary_directory_aliases,
            self.named_temporary_file_aliases,
            self.mkdtemp_aliases,
        ):
            aliases.discard(target.id)

    def _callable_alias_attributes(self, node: ast.AST) -> tuple[str, ...]:
        if isinstance(node, ast.Name):
            return tuple(
                attribute
                for attribute in (
                    "rmtree_aliases",
                    "remove_aliases",
                    "unlink_aliases",
                )
                if node.id in getattr(self, attribute)
            )
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            return ()
        if node.value.id in self.shutil_aliases and node.attr == "rmtree":
            return ("rmtree_aliases",)
        if node.value.id in self.os_aliases and node.attr == "remove":
            return ("remove_aliases",)
        if node.value.id in self.os_aliases and node.attr == "unlink":
            return ("unlink_aliases",)
        return ()

    def _assign_callable_aliases(
        self, target: ast.AST, attributes: tuple[str, ...]
    ) -> None:
        if not isinstance(target, ast.Name):
            return
        for attribute in attributes:
            getattr(self, attribute).add(target.id)

    @classmethod
    def _target_nodes(cls, target: ast.AST) -> tuple[ast.AST, ...]:
        if isinstance(target, ast.Starred):
            return cls._target_nodes(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(
                child for element in target.elts for child in cls._target_nodes(element)
            )
        return (target,)

    def _bind_assignment(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            values: tuple[ast.AST | None, ...]
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                and not any(isinstance(item, ast.Starred) for item in target.elts)
            ):
                values = tuple(value.elts)
            else:
                values = (None,) * len(target.elts)
            for child, child_value in zip(target.elts, values, strict=True):
                self._bind_assignment(child, child_value)
            return
        if isinstance(target, ast.Starred):
            self._bind_assignment(target.value, None)
            return

        callable_aliases = (
            self._callable_alias_attributes(value) if value is not None else ()
        )
        rendered = _python_path_symbol(value, self.symbols, self.path_aliases)
        if rendered is None and isinstance(value, ast.Call):
            temporary_kind = self._temporary_call_kind(value)
            if temporary_kind == "temporary-directory":
                rendered = self._temporary_placeholder(temporary_kind)
        self._invalidate_import_alias(target)
        self._assign_symbol(target, rendered)
        self._assign_callable_aliases(target, callable_aliases)

    def _alias_snapshot(self) -> dict[str, set[str]]:
        return {
            attribute: getattr(self, attribute).copy()
            for attribute in self._ALIAS_ATTRIBUTES
        }

    def _restore_aliases(self, snapshot: dict[str, set[str]]) -> None:
        for attribute, aliases in snapshot.items():
            setattr(self, attribute, aliases)

    def _visit_branch(
        self,
        statements: list[ast.stmt],
        symbols: dict[str, str],
        aliases: dict[str, set[str]],
    ) -> tuple[dict[str, str], dict[str, set[str]]]:
        outer_symbols = self.symbols
        outer_aliases = self._alias_snapshot()
        self.symbols = symbols.copy()
        self._restore_aliases(
            {attribute: values.copy() for attribute, values in aliases.items()}
        )
        for statement in statements:
            self.visit(statement)
        result = self.symbols.copy(), self._alias_snapshot()
        self.symbols = outer_symbols
        self._restore_aliases(outer_aliases)
        return result

    def _merge_branches(
        self,
        branches: list[tuple[dict[str, str], dict[str, set[str]]]],
    ) -> None:
        symbol_keys = set().union(*(symbols for symbols, _ in branches))
        merged_symbols: dict[str, str] = {}
        for key in symbol_keys:
            values = {symbols.get(key, PYTHON_UNKNOWN_PATH) for symbols, _ in branches}
            merged_symbols[key] = (
                values.pop() if len(values) == 1 else PYTHON_UNKNOWN_PATH
            )
        self.symbols = merged_symbols
        for attribute in self._ALIAS_ATTRIBUTES:
            common = set.intersection(*(aliases[attribute] for _, aliases in branches))
            setattr(self, attribute, common)

    def _visit_isolated_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> dict[str, str]:
        inherited = self.symbols
        inherited_aliases = self._alias_snapshot()
        self.symbols = inherited.copy()
        parameters: list[ast.arg] = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if node.args.vararg is not None:
            parameters.append(node.args.vararg)
        if node.args.kwarg is not None:
            parameters.append(node.args.kwarg)
        for parameter in parameters:
            self._bind_assignment(ast.Name(id=parameter.arg), None)
        for statement in node.body:
            self.visit(statement)
        result = self.symbols
        self.symbols = inherited
        self._restore_aliases(inherited_aliases)
        return result

    def _visit_function_definition_surface(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
        ):
            self.visit(expression)
        annotations = [
            argument.annotation
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            if argument.annotation is not None
        ]
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            annotations.append(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            annotations.append(node.args.kwarg.annotation)
        if node.returns is not None:
            annotations.append(node.returns)
        for annotation in annotations:
            self.visit(annotation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition_surface(node)
        self._visit_isolated_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition_surface(node)
        self._visit_isolated_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        outer = self.symbols
        outer_aliases = self._alias_snapshot()
        for expression in (*node.decorator_list, *node.bases):
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        class_symbols = outer.copy()
        for statement in node.body:
            self.symbols = class_symbols.copy()
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function_definition_surface(statement)
                result = self._visit_isolated_function(statement)
                if statement.name in {"setUp", "setUpClass"}:
                    for key, rendered in result.items():
                        if key.startswith("self."):
                            class_symbols[key] = rendered
                        elif key.startswith("cls."):
                            suffix = key.removeprefix("cls.")
                            class_symbols[key] = rendered
                            class_symbols[f"self.{suffix}"] = rendered
            else:
                self.visit(statement)
                class_symbols = self.symbols.copy()
        self.symbols = outer
        self._restore_aliases(outer_aliases)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        base_symbols = self.symbols.copy()
        base_aliases = self._alias_snapshot()
        branches = [self._visit_branch(node.body, base_symbols, base_aliases)]
        branches.append(
            self._visit_branch(node.orelse, base_symbols, base_aliases)
            if node.orelse
            else (base_symbols, base_aliases)
        )
        self._merge_branches(branches)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "shutil":
                self.shutil_aliases.add(alias.asname or alias.name)
            elif alias.name == "os":
                self.os_aliases.add(alias.asname or alias.name)
            elif alias.name == "tempfile":
                self.tempfile_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "shutil":
            for alias in node.names:
                if alias.name == "rmtree":
                    self.rmtree_aliases.add(alias.asname or alias.name)
        elif node.module == "os":
            for alias in node.names:
                if alias.name == "remove":
                    self.remove_aliases.add(alias.asname or alias.name)
                elif alias.name == "unlink":
                    self.unlink_aliases.add(alias.asname or alias.name)
        elif node.module == "pathlib":
            for alias in node.names:
                if alias.name == "Path":
                    self.path_aliases.add(alias.asname or alias.name)
        elif node.module == "tempfile":
            for alias in node.names:
                imported = alias.asname or alias.name
                if alias.name == "TemporaryDirectory":
                    self.temporary_directory_aliases.add(imported)
                elif alias.name == "NamedTemporaryFile":
                    self.named_temporary_file_aliases.add(imported)
                elif alias.name == "mkdtemp":
                    self.mkdtemp_aliases.add(imported)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        direct_rmtree = (
            isinstance(function, ast.Name) and function.id in self.rmtree_aliases
        )
        qualified_rmtree = (
            isinstance(function, ast.Attribute)
            and function.attr == "rmtree"
            and isinstance(function.value, ast.Name)
            and function.value.id in self.shutil_aliases
        )
        direct_remove = isinstance(function, ast.Name) and function.id in (
            self.remove_aliases | self.unlink_aliases
        )
        qualified_remove = (
            isinstance(function, ast.Attribute)
            and function.attr in {"remove", "unlink"}
            and isinstance(function.value, ast.Name)
            and function.value.id in self.os_aliases
        )
        path_unlink = isinstance(function, ast.Attribute) and function.attr == "unlink"
        if direct_rmtree or qualified_rmtree:
            self.calls.append(
                (
                    node.lineno,
                    "shutil.rmtree",
                    _python_path_symbol(
                        node.args[0] if node.args else None,
                        self.symbols,
                        self.path_aliases,
                    ),
                )
            )
        elif direct_remove or qualified_remove:
            self.calls.append(
                (
                    node.lineno,
                    "os.remove/unlink",
                    _python_path_symbol(
                        node.args[0] if node.args else None,
                        self.symbols,
                        self.path_aliases,
                    ),
                )
            )
        elif path_unlink:
            self.calls.append(
                (
                    node.lineno,
                    "Path.unlink",
                    _python_path_symbol(
                        function.value, self.symbols, self.path_aliases
                    ),
                )
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # Even `safe += dynamic` can redirect a string path.  Treat every
        # augmented result as unproved; the active scanner is intentionally
        # fail-closed rather than trying to model arbitrary __iadd__ methods.
        self._bind_assignment(node.target, None)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        temporary_names: list[str] = []
        for item in node.items:
            if not isinstance(item.context_expr, ast.Call):
                continue
            temporary_kind = self._temporary_call_kind(item.context_expr)
            if temporary_kind is None:
                continue
            optional = item.optional_vars
            key = self._target_key(optional) if optional is not None else None
            if key is not None:
                self.symbols[key] = self._temporary_placeholder(
                    f"{temporary_kind}-{key}"
                )
                temporary_names.append(key)
        self.generic_visit(node)
        for name in temporary_names:
            self.symbols.pop(name, None)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        base_symbols = self.symbols.copy()
        base_aliases = self._alias_snapshot()
        body_symbols = base_symbols.copy()
        iterator = node.iter
        if (
            isinstance(node.target, ast.Name)
            and isinstance(iterator, ast.Call)
            and isinstance(iterator.func, ast.Attribute)
            and iterator.func.attr in {"glob", "rglob"}
        ):
            base = _python_path_symbol(
                iterator.func.value, self.symbols, self.path_aliases
            )
            pattern = _python_path_symbol(
                iterator.args[0] if iterator.args else None,
                self.symbols,
                self.path_aliases,
            )
            if base is not None and pattern is not None:
                body_symbols[node.target.id] = f"{base}/{pattern}"
            else:
                body_symbols[node.target.id] = PYTHON_UNKNOWN_PATH
        else:
            for target in self._target_nodes(node.target):
                key = self._target_key(target)
                if key is not None:
                    body_symbols[key] = PYTHON_UNKNOWN_PATH
        body_result = self._visit_branch(node.body, body_symbols, base_aliases)
        self._merge_branches([(base_symbols, base_aliases), body_result])
        if node.orelse:
            merged_symbols = self.symbols.copy()
            merged_aliases = self._alias_snapshot()
            else_result = self._visit_branch(
                node.orelse, merged_symbols, merged_aliases
            )
            self._merge_branches([(merged_symbols, merged_aliases), else_result])

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        base_symbols = self.symbols.copy()
        base_aliases = self._alias_snapshot()
        body_result = self._visit_branch(node.body, base_symbols, base_aliases)
        self._merge_branches([(base_symbols, base_aliases), body_result])
        if node.orelse:
            merged_symbols = self.symbols.copy()
            merged_aliases = self._alias_snapshot()
            else_result = self._visit_branch(
                node.orelse, merged_symbols, merged_aliases
            )
            self._merge_branches([(merged_symbols, merged_aliases), else_result])


def _python_environment_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        function = ast.unparse(node.func)
        if function in {"os.getenv", "os.environ.get"} and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return key.value
    if not isinstance(node, ast.Subscript):
        return None
    value = node.value
    if not (
        isinstance(value, ast.Attribute)
        and value.attr == "environ"
        and isinstance(value.value, ast.Name)
        and value.value.id == "os"
    ):
        return None
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _python_path_symbol(
    node: ast.AST | None,
    symbols: dict[str, str] | None = None,
    path_aliases: set[str] | None = None,
) -> str | None:
    """Render only statically provable environment-bound Python paths."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Name, ast.Attribute)) and symbols is not None:
        rendered = symbols.get(ast.unparse(node))
        if rendered == PYTHON_UNKNOWN_PATH:
            return None
        if rendered is not None:
            return rendered
    if isinstance(node, ast.Attribute) and symbols is not None:
        base = _python_path_symbol(node.value, symbols, path_aliases)
        if base is not None:
            return f"{base}/{node.attr}"
    environment = _python_environment_name(node)
    if environment is not None:
        return "${" + environment + "}"
    aliases = (
        path_aliases
        if path_aliases is not None
        else {"Path", "PurePath", "PurePosixPath"}
    )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in aliases and len(node.args) == 1:
            return _python_path_symbol(node.args[0], symbols, path_aliases)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        function = ast.unparse(node.func)
        if function == "Path.home" and not node.args:
            return "${HOME}"
        if function in {"os.path.join", "posixpath.join"}:
            pieces = [
                _python_path_symbol(item, symbols, path_aliases) for item in node.args
            ]
            if (
                pieces
                and all(item is not None for item in pieces)
                and all(not str(item).startswith("/") for item in pieces[1:])
            ):
                return str(pieces[0]).rstrip("/") + "".join(
                    "/" + str(item).strip("/") for item in pieces[1:]
                )
        if node.func.attr in {"absolute", "expanduser", "resolve"} and not node.args:
            return _python_path_symbol(node.func.value, symbols, path_aliases)
        if node.func.attr == "relative_to" and node.args:
            root = _python_path_symbol(node.args[0], symbols, path_aliases)
            if root is not None and (
                exact_job_path(root) or root.startswith(PYTHON_OWNED_TEMP_PREFIX)
            ):
                # pathlib rejects paths outside root and never returns '..'.
                return "verified-relative-child"
        if node.func.attr in {"with_name", "with_suffix"} and node.args:
            base = _python_path_symbol(node.func.value, symbols, path_aliases)
            replacement = _python_path_symbol(node.args[0], symbols, path_aliases)
            if base is not None and replacement is not None:
                return f"{base}/{replacement.strip('/')}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left = _python_path_symbol(node.left, symbols, path_aliases)
        right = _python_path_symbol(node.right, symbols, path_aliases)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Div) and right.startswith("/"):
            return None
        separator = "/" if isinstance(node.op, ast.Div) else ""
        return f"{left}{separator}{right}"
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                rendered = _python_path_symbol(value.value, symbols, path_aliases)
                if rendered is None:
                    return None
                pieces.append(rendered)
            else:
                return None
        return "".join(pieces)
    return None


def python_cleanup_violations(source: str, start_line: int) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        line = start_line + (error.lineno or 1) - 1
        return [f"line {line}: embedded Python cannot be audited: {error.msg}"]
    visitor = PythonCleanupVisitor()
    visitor.visit(tree)
    findings: list[str] = []
    for line, operation, rendered in visitor.calls:
        if rendered is None:
            findings.append(
                f"line {start_line + line - 1}: Python {operation} target cannot "
                "be statically proven as an exact run-ID-qualified job path"
            )
        elif not (
            exact_job_path(rendered) or rendered.startswith(PYTHON_OWNED_TEMP_PREFIX)
        ):
            findings.append(
                f"line {start_line + line - 1}: Python {operation} is not bound "
                "to an exact run-ID-qualified job path"
            )
    return findings


def shell_cleanup_violations(
    shell: str,
    *,
    depth: int = 0,
    allow_verified_manifest_reference: bool = False,
) -> list[str]:
    if depth > 2:
        return ["line 1: nested shell audit depth exceeded"]
    shell, executable_bodies, errors = extract_heredocs(shell)
    temporary_variables = frozenset(
        match.group("name")
        for match in re.finditer(
            r"(?m)^(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
            r"(?:\"|')?\$\(mktemp\s+-[^\n)]*d[^\n)]*\)",
            shell,
        )
    )
    docker_tainted = docker_list_tainted_variables(shell)
    violations = list(errors)
    for body in executable_bodies:
        if body.language == "python":
            violations.extend(python_cleanup_violations(body.source, body.line))
        else:
            nested = shell_cleanup_violations(body.source, depth=depth + 1)
            violations.extend(
                f"line {body.line}: shell heredoc {item}" for item in nested
            )

    chunks, parse_errors = shell_chunks(shell)
    violations.extend(parse_errors)
    for line, tokens in chunks:
        chunk_has_global_docker_list = False
        chunk_has_docker_deletion = False
        for segment in command_segments(tokens):
            invocation = unwrap_command(segment)
            if invocation is None:
                continue
            executable, args = invocation
            if executable.startswith("$"):
                _, _, indirect_docker_deletion = docker_invocation(
                    args,
                    allow_verified_manifest_reference=allow_verified_manifest_reference,
                )
                if indirect_docker_deletion:
                    violations.append(
                        f"line {line}: Docker deletion uses a dynamic executable"
                    )
                    chunk_has_docker_deletion = True
            if executable == "docker":
                findings, global_listing, deletion = docker_invocation(
                    args,
                    allow_verified_manifest_reference=allow_verified_manifest_reference,
                )
                violations.extend(f"line {line}: {finding}" for finding in findings)
                if global_listing:
                    chunk_has_global_docker_list = True
                if deletion:
                    chunk_has_docker_deletion = True
                    if any(
                        item.lstrip("${").rstrip("}") in docker_tainted
                        for item in args
                        if item.startswith("$")
                    ):
                        violations.append(
                            f"line {line}: Docker deletion target is tainted by a global object listing"
                        )
            elif executable in {
                "conda",
                "mamba",
                "micromamba",
                "$CONDA_EXE",
                "${CONDA_EXE}",
            }:
                if _conda_command(args) == "clean":
                    violations.append(
                        f"line {line}: global Conda or Mamba cache cleanup"
                    )
            elif executable in {"nextflow", "$NEXTFLOW", "${NEXTFLOW}"}:
                if _nextflow_command(args) == "clean":
                    violations.append(f"line {line}: global Nextflow cleanup")
            elif executable == "rm":
                violations.extend(
                    f"line {line}: {finding}"
                    for finding in rm_invocation(
                        args, temporary_variables=temporary_variables
                    )
                )
            elif executable == "unlink":
                targets = [
                    item for item in args if item != "--" and not item.startswith("-")
                ]
                if not targets or not all(exact_job_path(item) for item in targets):
                    violations.append(
                        f"line {line}: unlink is not bound to exact run-ID-qualified job paths"
                    )
            elif executable == "rsync":
                violations.extend(
                    f"line {line}: {finding}" for finding in rsync_invocation(args)
                )
            elif executable == "git":
                violations.extend(
                    f"line {line}: {finding}" for finding in git_clean_invocation(args)
                )
            elif executable == "find":
                exec_deletion = any(
                    item in {"-exec", "-execdir", "-ok", "-okdir"} for item in args
                ) and any(re.search(r"(?:^|[/\s])rm(?:\s|$)", item) for item in args)
                destructive = "-delete" in args or exec_deletion
                root = next((item for item in args if not item.startswith("-")), "")
                if destructive and (
                    exec_deletion or "-L" in args or not exact_job_path(root)
                ):
                    violations.append(
                        f"line {line}: find deletion is not bound to an exact run-ID-qualified job path"
                    )
            elif re.fullmatch(r"python(?:[23](?:\.\d+)*)?", executable):
                if "-c" in args:
                    index = args.index("-c")
                    if index + 1 < len(args):
                        violations.extend(
                            python_cleanup_violations(args[index + 1], line)
                        )
            elif executable in {"bash", "sh"} and "-c" in args:
                index = args.index("-c")
                if index + 1 < len(args):
                    nested = shell_cleanup_violations(args[index + 1], depth=depth + 1)
                    violations.extend(f"line {line}: nested {item}" for item in nested)
            elif executable == "eval" and args:
                nested = shell_cleanup_violations(" ".join(args), depth=depth + 1)
                violations.extend(f"line {line}: nested {item}" for item in nested)

        # xargs appends piped stdin to its command even when an explicit exact
        # target also appears. Reject that data flow without conflating a
        # harmless inventory on another line with an exact deletion.
        if "|" in tokens and chunk_has_global_docker_list and chunk_has_docker_deletion:
            violations.append(
                f"line {line}: Docker deletion is sourced from a global object listing"
            )

    return sorted(set(violations))


def _repo_script_prefix_allowed(prefix: str) -> bool:
    if not prefix:
        return True
    if not prefix.endswith("/"):
        return prefix[-1] in "($=`"
    fragment = re.split(r"[\s()=;|`]+", prefix[:-1])[-1]
    parent = fragment.rsplit("/", 1)[-1]
    return parent in {".", "v2", "$REPO", "${REPO}"} or parent.startswith("$")


def _repository_entrypoints(token: str, *, root: Path = ROOT) -> set[Path]:
    """Resolve literal repository shell/Python entrypoints from one argv token."""
    references: set[Path] = set()
    pattern = re.compile(
        r"(?P<base>(?:\.github/actions|scripts|tests|bin)/)"
        r"(?P<relative>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:sh|py))"
    )
    for match in pattern.finditer(token):
        prefix = token[: match.start()]
        if not _repo_script_prefix_allowed(prefix):
            continue
        relative = PurePosixPath(match.group("base") + match.group("relative"))
        if ".." in relative.parts:
            continue
        references.add(root / Path(*relative.parts))
    return references


def referenced_entrypoints_from_shell(shell: str, *, root: Path = ROOT) -> set[Path]:
    """Find repository shell/Python files executed by a shell command surface."""
    shell, executable_bodies, _ = extract_heredocs(shell)
    chunks, _ = shell_chunks(shell)
    references: set[Path] = set(_repository_entrypoints(shell, root=root))
    for body in executable_bodies:
        if body.language == "shell":
            references.update(referenced_entrypoints_from_shell(body.source, root=root))
        else:
            references.update(
                referenced_entrypoints_from_python(body.source, root=root)
            )
    for _, tokens in chunks:
        for segment in command_segments(tokens):
            invocation = unwrap_command(segment)
            if invocation is None:
                continue
            executable, args = invocation
            references.update(_repository_entrypoints(executable, root=root))
            interpreter = (
                executable in {"bash", "sh", "source", "."}
                or re.fullmatch(r"python(?:[23](?:\.\d+)*)?", executable) is not None
            )
            if interpreter and "-c" not in args:
                for candidate in args:
                    references.update(_repository_entrypoints(candidate, root=root))
            for token in segment:
                if "$(" in token or "`" in token:
                    references.update(_repository_entrypoints(token, root=root))
    return references


class PythonEntrypointVisitor(ast.NodeVisitor):
    """Collect repository entrypoints and literal process commands from Python."""

    PROCESS_APIS = {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.references: set[Path] = set()
        self.paths: dict[str, Path] = {"ROOT": self.root}
        self.sequences: dict[str, tuple[str | None, ...]] = {}
        self.process_surfaces: list[tuple[int, str]] = []

    def _path_value(self, node: ast.AST) -> Path | None:
        if isinstance(node, ast.Name):
            return self.paths.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            candidate = Path(value)
            if candidate.is_absolute():
                return candidate
            if candidate.parts and candidate.parts[0] in (
                REPOSITORY_ENTRYPOINT_ROOTS | {".github"}
            ):
                return self.root / candidate
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._path_value(node.left)
            if (
                left is not None
                and isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)
            ):
                return left / node.right.value
        if isinstance(node, ast.Call) and len(node.args) == 1:
            function = ast.unparse(node.func)
            if function in {"str", "Path"}:
                return self._path_value(node.args[0])
        return None

    def _string_value(self, node: ast.AST) -> str | None:
        path = self._path_value(node)
        if path is not None:
            return str(path)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Attribute) and ast.unparse(node) == "sys.executable":
            return "python3"
        return None

    def _sequence_value(self, node: ast.AST) -> tuple[str | None, ...] | None:
        if isinstance(node, ast.Name):
            return self.sequences.get(node.id)
        if isinstance(node, (ast.List, ast.Tuple)):
            return tuple(self._string_value(item) for item in node.elts)
        return None

    def _record_repository_path(self, value: str | None) -> None:
        if value is None:
            return
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            return
        if (
            candidate.suffix in {".sh", ".py"}
            and relative.parts
            and relative.parts[0] in (REPOSITORY_ENTRYPOINT_ROOTS | {".github"})
        ):
            self.references.add(candidate)

    def _record_process(self, line: int, argv: tuple[str | None, ...]) -> None:
        if not argv or argv[0] is None:
            return
        executable = PurePosixPath(argv[0]).name
        self._record_repository_path(argv[0])
        if executable in {"bash", "sh", "source", "."}:
            for index, item in enumerate(argv[1:], 1):
                if item == "-c" and index + 1 < len(argv):
                    command = argv[index + 1]
                    if command is not None:
                        self.process_surfaces.append((line, command))
                    break
                if item is not None and not item.startswith("-"):
                    self._record_repository_path(item)
                    break
        elif re.fullmatch(r"python(?:[23](?:\.\d+)*)?", executable):
            for item in argv[1:]:
                if item == "-c":
                    break
                if item is not None and not item.startswith("-"):
                    self._record_repository_path(item)
                    break

        rendered = " ".join(
            shlex.quote(item if item is not None else "__ONCOTRACER_UNKNOWN__")
            for item in argv
        )
        if rendered:
            self.process_surfaces.append((line, rendered))

    def visit_Assign(self, node: ast.Assign) -> None:
        path = self._path_value(node.value)
        sequence = self._sequence_value(node.value)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if path is not None:
                self.paths[target.id] = path
            if sequence is not None:
                self.sequences[target.id] = sequence
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = ast.unparse(node.func)
        if function in self.PROCESS_APIS and node.args:
            sequence = self._sequence_value(node.args[0])
            if sequence is not None:
                self._record_process(node.lineno, sequence)
        elif function == "os.system" and node.args:
            command = self._string_value(node.args[0])
            if command is not None:
                self.references.update(
                    referenced_entrypoints_from_shell(command, root=self.root)
                )
                self.process_surfaces.append((node.lineno, command))
        self.generic_visit(node)


def _python_process_inventory(
    source: str, *, root: Path = ROOT
) -> PythonEntrypointVisitor | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    visitor = PythonEntrypointVisitor(root)
    visitor.visit(tree)
    return visitor


def referenced_entrypoints_from_python(source: str, *, root: Path = ROOT) -> set[Path]:
    visitor = _python_process_inventory(source, root=root)
    return set() if visitor is None else visitor.references


def python_process_cleanup_violations(source: str) -> list[str]:
    visitor = _python_process_inventory(source)
    if visitor is None:
        return ["line 1: Python process surface cannot be audited"]
    findings: list[str] = []
    for line, shell in visitor.process_surfaces:
        findings.extend(
            f"line {line}: subprocess {finding}"
            for finding in shell_cleanup_violations(shell)
        )
    return sorted(set(findings))


def _local_action_metadata(action: str, *, root: Path = ROOT) -> Path | None:
    if not action.startswith("./") or any(
        marker in action for marker in ("$", "*", "?")
    ):
        return None
    directory = (root / action[2:]).resolve()
    if not directory.is_relative_to(root.resolve()):
        return None
    for name in ("action.yml", "action.yaml"):
        candidate = directory / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return directory / "action.yml"


def active_hosted_entrypoints(
    workflow_surfaces: dict[Path, tuple[list[tuple[int, str]], list[tuple[int, str]]]],
    *,
    root: Path = ROOT,
) -> tuple[tuple[Path, ...], list[tuple[Path, int, str]], list[str]]:
    """Resolve active local actions and shell/Python dependencies transitively."""
    action_runs: list[tuple[Path, int, str]] = []
    action_entrypoints: set[Path] = set()
    action_pending = {
        metadata
        for _, uses in workflow_surfaces.values()
        for _, action in uses
        if (metadata := _local_action_metadata(action, root=root)) is not None
    }
    actions_seen: set[Path] = set()
    errors: list[str] = []
    while action_pending:
        metadata = action_pending.pop()
        if metadata in actions_seen:
            continue
        actions_seen.add(metadata)
        if not metadata.is_file() or metadata.is_symlink():
            errors.append(
                f"{metadata.relative_to(root)}: local action metadata is missing or a symlink"
            )
            continue
        metadata_text = metadata.read_text(encoding="utf-8")
        runs, uses = workflow_command_surfaces(metadata_text)
        action_runs.extend((metadata, line, shell) for line, shell in runs)
        for match in re.finditer(
            r"(?m)^\s*(?:entrypoint|main|pre|post):\s*(?P<value>[^#]+?)\s*$",
            metadata_text,
        ):
            value = _yaml_scalar(match.group("value"))
            if value.endswith((".sh", ".py")) and not any(
                marker in value for marker in ("$", "*", "?", "..")
            ):
                action_entrypoints.add(metadata.parent / value.lstrip("./"))
        image_match = re.search(
            r"(?m)^\s*image:\s*(?P<value>[^#]+?Dockerfile)\s*$", metadata_text
        )
        if image_match:
            dockerfile = metadata.parent / _yaml_scalar(
                image_match.group("value")
            ).lstrip("./")
            if dockerfile.is_file() and not dockerfile.is_symlink():
                docker_text = dockerfile.read_text(encoding="utf-8")
                for match in re.finditer(
                    r"(?im)^\s*(?:ENTRYPOINT|CMD)\s+"
                    r"(?:\[\s*)?[\"']?(?P<value>[^\"',\]\s]+\.(?:sh|py))",
                    docker_text,
                ):
                    action_entrypoints.add(
                        metadata.parent / match.group("value").lstrip("./")
                    )
        for _, action in uses:
            nested = _local_action_metadata(action, root=root)
            if nested is not None:
                action_pending.add(nested)

    pending = {
        reference
        for runs, _ in workflow_surfaces.values()
        for _, shell in runs
        for reference in referenced_entrypoints_from_shell(shell, root=root)
    }
    pending.update(
        reference
        for _, _, shell in action_runs
        for reference in referenced_entrypoints_from_shell(shell, root=root)
    )
    pending.update(action_entrypoints)
    discovered: set[Path] = set()
    allowed_roots = tuple(
        (root / item).resolve() for item in REPOSITORY_ENTRYPOINT_ROOTS
    )
    allowed_roots += ((root / ".github" / "actions").resolve(),)
    while pending:
        path = pending.pop()
        if path in discovered:
            continue
        discovered.add(path)
        if not path.is_file() or path.is_symlink():
            errors.append(
                f"{path.relative_to(ROOT)}: active hosted script is missing, non-regular, or a symlink"
            )
            continue
        resolved = path.resolve()
        if not any(resolved.is_relative_to(item) for item in allowed_roots):
            errors.append(
                f"{path.relative_to(root)}: active hosted entrypoint escapes audited roots"
            )
            continue
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".sh":
            references = referenced_entrypoints_from_shell(source, root=root)
        else:
            references = referenced_entrypoints_from_python(source, root=root)
        pending.update(references - discovered)
    return tuple(sorted(discovered)), action_runs, errors


def active_hosted_scripts(
    workflow_runs: dict[Path, list[tuple[int, str]]],
) -> tuple[tuple[Path, ...], list[str]]:
    """Backward-compatible test helper returning active shell entrypoints."""
    surfaces = {path: (runs, []) for path, runs in workflow_runs.items()}
    entrypoints, _, errors = active_hosted_entrypoints(surfaces)
    return tuple(path for path in entrypoints if path.suffix == ".sh"), errors


def verified_image_helper_violations(source: str) -> list[str]:
    """Validate the sole dynamic Docker target before granting scanner trust."""
    findings: list[str] = []
    start_marker = "remove_owned_image_references() {"
    end_marker = "run_native_environment_probe() {"
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        return ["verified image cleanup helper boundary is missing or ambiguous"]
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    helper = source[start:end]
    helper_sha256 = hashlib.sha256(helper.encode("utf-8")).hexdigest()
    if helper_sha256 != VERIFIED_IMAGE_HELPER_SHA256:
        findings.append(
            "verified image cleanup helper differs from the fully reviewed implementation: "
            f"sha256={helper_sha256}"
        )
    deletion = 'docker image rm -- "$reference"'
    helper_lines = tuple(
        line.strip()
        for line in helper.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    alias_helper_name = "verify_job_owned_image_aliases"
    alias_definitions = re.findall(
        rf"(?m)^\s*(?:function\s+)?{alias_helper_name}(?:\s*\(\s*\))?\s*\{{",
        helper,
    )
    if len(alias_definitions) != 1 or helper.count(alias_helper_name) != 3:
        findings.append(
            "verified image cleanup alias helper definition or call set is ambiguous"
        )
    required = (
        'expected_manifest="$RUNNER_TEMP/oncotracer-image-ownership-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}.tsv"',
        '[[ "$IMAGE_OWNERSHIP" == "$expected_manifest" ]] || {',
        '[[ -f "$IMAGE_OWNERSHIP" && ! -L "$IMAGE_OWNERSHIP" ]] || {',
        '[[ "${GITHUB_ACTIONS:-}" == true && -n "${RUNNER_NAME:-}" ]] || {',
        'allowed["$V1_DOCKER_IMAGE"]=1',
        'done < "$PINS"',
        '[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]',
        '[[ "$created_by_job" == 0 || "$created_by_job" == 1 ]]',
        '[[ -z "${seen[$reference]+present}" ]] || {',
        'observed_id="$(docker image inspect "$reference" --format',
        '[[ "$observed_id" == "$image_id" ]] || {',
        'containers="$(docker ps --all --quiet --filter "ancestor=$reference")"',
        '[[ -z "$containers" ]] || {',
        deletion,
        'if docker image inspect "$reference" >/dev/null 2>&1; then',
        "action=REMOVED_JOB_CREATED",
        "action=PRESERVED_PREEXISTING",
        '[[ "$line_number" -gt 1 && "$observed_count" -eq "$expected_count" ]]',
        'for reference in "${!allowed[@]}"',
        "verify_job_owned_image_aliases() {",
        '[[ -n "$expected_alias" &&',
        '-z "${expected_alias_keys[$expected_alias]+present}" ]] || {',
        'expected_alias_keys["$expected_alias"]="$expected_reference"',
        'docker image inspect "$target_image_id"',
        '[[ -n "$canonical_alias" &&',
        '-z "${actual_alias_keys[$canonical_alias]+present}" ]] || {',
        'actual_alias_keys["$canonical_alias"]="$actual_alias"',
        '[[ -n "${expected_alias_keys[$canonical_alias]+present}" ]] || {',
        '[[ "$actual_alias_count" -eq "$expected_alias_count" ]] || {',
        '[[ -n "${actual_alias_keys[$expected_alias]+present}" ]] || {',
        'rebound_id="$(docker image inspect "$expected_reference" --format',
        '[[ "$rebound_id" == "$target_image_id" ]] || {',
        "action=PRESERVED_JOB_CREATED_SHARED",
        'removed_aliases["${references[$alias_index]}"]=1',
    )
    for marker in required:
        if not any(line.startswith(marker) for line in helper_lines):
            findings.append(
                f"verified image cleanup helper lacks required guard: {marker}"
            )
    if source.count(deletion) != 1 or helper.count(deletion) != 1:
        findings.append(
            "verified image cleanup must contain exactly one exact-reference deletion"
        )
    alias_verification = 'verify_job_owned_image_aliases "$image_id"'
    if helper_lines.count(alias_verification) != 2:
        findings.append(
            "verified image cleanup must validate the complete alias group twice"
        )
    if source.count(deletion) == 1 and helper.count(deletion) == 1:
        lines = helper.splitlines()
        stripped = [line.strip() for line in lines]
        deletion_index = stripped.index(deletion)
        ordered_guards = (
            "while IFS=$'\\t' read -r reference image_id created_by_job extra; do",
            '[[ -n "$reference" && -z "$extra" ]]',
            '[[ -n "${allowed[$reference]+present}" ]] || {',
            '[[ -z "${seen[$reference]+present}" ]] || {',
            '[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]',
            '[[ "$created_by_job" == 0 || "$created_by_job" == 1 ]]',
            'observed_id="$(docker image inspect "$reference" --format',
            '[[ "$observed_id" == "$image_id" ]] || {',
            'if [[ "$created_by_job" == 1 ]]; then',
            'containers="$(docker ps --all --quiet --filter "ancestor=$reference")"',
            '[[ -z "$containers" ]] || {',
            deletion,
        )
        positions: list[int] = []
        for marker in ordered_guards:
            matches = [
                index for index, line in enumerate(stripped) if line.startswith(marker)
            ]
            if len(matches) != 1:
                findings.append(
                    f"verified image cleanup guard is missing or ambiguous: {marker}"
                )
                break
            positions.append(matches[0])
        if positions and positions != sorted(positions):
            findings.append(
                "verified image deletion precedes an ownership, identity, or active-use guard"
            )

        created_if = next(
            (
                index
                for index, line in enumerate(stripped)
                if line == 'if [[ "$created_by_job" == 1 ]]; then'
            ),
            -1,
        )
        matching_fi = -1
        created_else = -1
        if created_if >= 0:
            depth = 0
            for index in range(created_if, len(stripped)):
                line = stripped[index]
                if re.match(r"^if\b.*\bthen$", line):
                    depth += 1
                elif line == "else" and depth == 1:
                    created_else = index
                elif line == "fi":
                    depth -= 1
                    if depth == 0:
                        matching_fi = index
                        break
        alias_call_indices = [
            index for index, line in enumerate(stripped) if line == alias_verification
        ]
        allowed_completeness_loop = next(
            (
                index
                for index, line in enumerate(stripped)
                if line == 'for reference in "${!allowed[@]}"; do'
            ),
            -1,
        )
        if len(alias_call_indices) == 2 and not (
            allowed_completeness_loop < alias_call_indices[0] < created_if
        ):
            findings.append(
                "verified image cleanup does not prevalidate aliases after the complete manifest"
            )
        if not (created_if < deletion_index < matching_fi):
            findings.append(
                "verified image deletion is outside the created-by-this-job branch"
            )
        if created_else >= 0 and deletion_index > created_else:
            findings.append(
                "verified image deletion occurs in the pre-existing-image else branch"
            )

        active_use_guard = next(
            (
                index
                for index, line in enumerate(stripped)
                if line == '[[ -z "$containers" ]] || {'
            ),
            -1,
        )
        active_use_guard_close = -1
        if active_use_guard >= 0:
            depth = 0
            for index in range(active_use_guard, len(stripped)):
                line = stripped[index]
                depth += line.count("{")
                depth -= line.count("}")
                if depth == 0:
                    active_use_guard_close = index
                    break
        if deletion_index <= active_use_guard_close:
            findings.append(
                "verified image deletion occurs inside the active-use rejection branch"
            )

        current_if_matches = [
            index
            for index, line in enumerate(stripped)
            if line.startswith(
                'if current_id="$(docker image inspect "$reference" --format'
            )
        ]
        if len(current_if_matches) != 1:
            findings.append(
                "verified image cleanup lacks one unambiguous second identity check"
            )
        else:
            current_if = current_if_matches[0]
            current_branch_end = -1
            current_matching_fi = -1
            depth = 0
            for index in range(current_if, len(stripped)):
                line = stripped[index]
                if re.match(r"^if\b.*\bthen$", line):
                    depth += 1
                elif (line == "else" or line.startswith("elif ")) and depth == 1:
                    if current_branch_end < 0:
                        current_branch_end = index
                elif line == "fi":
                    depth -= 1
                    if depth == 0:
                        current_matching_fi = index
                        break
            if current_branch_end < 0:
                current_branch_end = current_matching_fi
            branch_markers = (
                '[[ "$current_id" == "$image_id" ]] || {',
                'containers="$(docker ps --all --quiet --filter "ancestor=$reference")"',
                '[[ -z "$containers" ]] || {',
                'daemon_containers="$(docker ps --all --quiet)"',
                '[[ -z "$daemon_containers" ]] || {',
                alias_verification,
            )
            branch_positions: list[int] = []
            for marker in branch_markers:
                matches = [
                    index
                    for index in range(
                        current_if + 1,
                        max(min(current_branch_end, deletion_index), 0),
                    )
                    if stripped[index].startswith(marker)
                ]
                if len(matches) != 1:
                    findings.append(
                        "verified image cleanup second-pass guard is missing or ambiguous: "
                        f"{marker}"
                    )
                    break
                branch_positions.append(matches[0])
            ordered_positions = [
                current_if,
                *branch_positions,
                deletion_index,
                current_branch_end,
                current_matching_fi,
            ]
            if (
                len(branch_positions) != len(branch_markers)
                or ordered_positions != sorted(ordered_positions)
                or len(set(ordered_positions)) != len(ordered_positions)
            ):
                findings.append(
                    "verified image deletion is not inside the second identity-check success branch"
                )

        helper_without_delete = helper.replace(deletion, "true", 1)
        findings.extend(
            f"verified image cleanup contains another unsafe command: {finding}"
            for finding in shell_cleanup_violations(helper_without_delete)
        )
    if re.search(r"docker\s+image\s+rm\b[^\n]*(?:--force|(?:^|\s)-f(?:\s|$))", helper):
        findings.append("verified image cleanup must never force image deletion")
    return findings


def active_surface_violations() -> list[str]:
    violations: list[str] = []
    workflow_surfaces: dict[
        Path, tuple[list[tuple[int, str]], list[tuple[int, str]]]
    ] = {}
    for path in ACTIVE_WORKFLOWS:
        runs, uses = workflow_command_surfaces(path.read_text(encoding="utf-8"))
        workflow_surfaces[path] = (runs, uses)
        for line, action in uses:
            if action.casefold().startswith(WHOLE_RUNNER_ACTIONS):
                violations.append(
                    f"{path.relative_to(ROOT)}:{line}: whole-runner cleanup action {action}"
                )
        for line, shell in runs:
            violations.extend(
                f"{path.relative_to(ROOT)}:{line}: {finding}"
                for finding in shell_cleanup_violations(shell)
            )
    hosted_entrypoints, action_runs, discovery_errors = active_hosted_entrypoints(
        workflow_surfaces
    )
    violations.extend(discovery_errors)
    for path, line, shell in action_runs:
        violations.extend(
            f"{path.relative_to(ROOT)}:{line}: {finding}"
            for finding in shell_cleanup_violations(shell)
        )
    for path in hosted_entrypoints:
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            for findings in (
                python_cleanup_violations(source, 1),
                python_process_cleanup_violations(source),
            ):
                violations.extend(
                    f"{path.relative_to(ROOT)}: {finding}" for finding in findings
                )
            continue
        if path == ROOT / "scripts" / "ci_native_parity.sh":
            violations.extend(
                f"{path.relative_to(ROOT)}: {finding}"
                for finding in verified_image_helper_violations(source)
            )
            start = source.index("remove_owned_image_references() {")
            end = source.index("run_native_environment_probe() {", start)
            helper = source[start:end]
            outside_helper = source[:start] + "\n" + source[end:]
            scans = (
                (outside_helper, False),
                (helper, True),
            )
        else:
            scans = ((source, False),)
        for surface, allow_verified in scans:
            violations.extend(
                f"{path.relative_to(ROOT)}: {finding}"
                for finding in shell_cleanup_violations(
                    surface,
                    allow_verified_manifest_reference=allow_verified,
                )
            )
    return violations


class HostedCiStorageSafetyTests(unittest.TestCase):
    def test_active_executable_surfaces_have_no_host_wide_cleanup(self) -> None:
        self.assertTrue(ACTIVE_WORKFLOWS)
        workflow_runs = {
            path: workflow_command_surfaces(path.read_text(encoding="utf-8"))[0]
            for path in ACTIVE_WORKFLOWS
        }
        hosted_scripts, errors = active_hosted_scripts(workflow_runs)
        self.assertEqual(errors, [])
        required_scripts = {
            ROOT / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh",
            ROOT / "scripts" / "ci_native_parity.sh",
            ROOT / "scripts" / "ci_resource_preflight.sh",
            ROOT / "scripts" / "release_registry_digest.sh",
            ROOT / "scripts" / "release_registry_pair.sh",
        }
        self.assertTrue(required_scripts.issubset(set(hosted_scripts)))
        for path in (*ACTIVE_WORKFLOWS, *hosted_scripts):
            self.assertTrue(path.is_file(), path)
        self.assertEqual(active_surface_violations(), [])

    def test_rejects_global_cleanup_variants(self) -> None:
        fixtures = (
            "docker system prune --all --force",
            "docker --context default system prune --all --force",
            "docker buildx prune --all --force",
            "docker --host unix:///run/docker.sock buildx --builder default prune -af",
            "docker --debug buildx --builder default prune -af",
            "docker buildx rm --all-inactive",
            "docker network ls -q | xargs -r docker network rm",
            "docker builder ls -q | xargs -r docker builder rm",
            "docker rmi $(docker images -aq)",
            "docker ps -aq | xargs -r docker rm -f",
            "docker ps -aq | xargs -r docker rm -f oncotracer-${GITHUB_RUN_ID}",
            "docker ps -aq |\n  xargs -r docker rm -f oncotracer-${GITHUB_RUN_ID}",
            'docker ps -aq | while read -r id; do docker rm -f "$id"; done',
            'for id in $(docker images -aq); do docker rmi "$id"; done',
            "docker compose down --volumes --rmi all",
            "sudo rm -rf /opt/hostedtoolcache/CodeQL",
            "sudo rm -f /opt/hostedtoolcache/*",
            "busybox rm -rf /opt/hostedtoolcache/CodeQL",
            'rm --recursive --force "$HOME/.nextflow"',
            "find /opt/hostedtoolcache -mindepth 1 -delete",
            "find /opt/hostedtoolcache -mindepth 1 -print0 | xargs -0 rm -rf",
            "find /opt/hostedtoolcache -execdir sh -c 'rm -rf \"$1\"' sh {} \\;",
            "conda --no-plugins clean --all -y",
            'find "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}" -exec rm -rf / {} \\;',
            "micromamba clean --all --yes",
            "nextflow -log trace.log clean -f",
            '"$NEXTFLOW" clean -f',
            "python3 -c \"import shutil; shutil.rmtree('/opt/hostedtoolcache')\"",
            "bash -c 'docker system prune --all --force'",
            "eval 'docker system prune --all --force'",
            "unlink /opt/hostedtoolcache/sentinel",
            "rsync -a --delete source/ /opt/hostedtoolcache/",
            "git clean -fdx",
            "git -C /opt/hostedtoolcache clean -ffdx",
            'ids="$(docker --context default images -aq)"\nfor id in $ids; do\n  docker image rm "$id"\ndone',
            'while read -r id; do docker container rm "$id"; done < <(docker ps -aq)',
            'mapfile -t ids < <(docker image ls -q)\nfor id in ${ids[@]}; do docker rmi "$id"; done',
        )
        for fixture in fixtures:
            with self.subTest(command=fixture):
                self.assertTrue(shell_cleanup_violations(fixture), fixture)

    def test_embedded_python_rmtree_is_rejected(self) -> None:
        shell = """python3 - <<'PY'
import shutil as storage
storage.rmtree('/opt/hostedtoolcache')
PY
"""
        self.assertTrue(shell_cleanup_violations(shell))

        imported_alias = """python3 - <<'PY'
from shutil import rmtree as wipe
wipe('/opt/hostedtoolcache')
PY
"""
        self.assertTrue(shell_cleanup_violations(imported_alias))

        variants = (
            "import os\nos.unlink('/opt/hostedtoolcache/file')\n",
            "import os, shutil\nshutil.rmtree(os.getenv('UNTRUSTED_CACHE'))\n",
            "from os import remove as erase\nerase('/opt/hostedtoolcache/file')\n",
            "from pathlib import Path\nPath('/opt/hostedtoolcache/file').unlink()\n",
            "from pathlib import Path\ntarget = Path('/opt/hostedtoolcache/file')\ntarget.unlink()\n",
            "from pathlib import Path as P\ntarget = P('/opt/hostedtoolcache/file')\ntarget.unlink()\n",
            "from pathlib import Path\nfor target in Path('/opt/hostedtoolcache').glob('*'):\n    target.unlink()\n",
            "from pathlib import Path\n(Path.home() / '.nextflow').unlink()\n",
            "import os\nos.remove(os.path.join('/opt/hostedtoolcache', 'file'))\n",
            "import shutil, sys\nshutil.rmtree(sys.argv[1])\n",
            "import shutil, sys\nwipe = shutil.rmtree\nwipe(sys.argv[1])\n",
            "from pathlib import Path\nPath(input()).unlink()\n",
        )
        for source in variants:
            with self.subTest(source=source):
                self.assertTrue(
                    shell_cleanup_violations("python3 - <<'PY'\n" + source + "PY\n")
                )
        dynamic_inline = (
            "python3 -c 'import shutil, sys; shutil.rmtree(sys.argv[1])' "
            "/opt/hostedtoolcache"
        )
        self.assertTrue(shell_cleanup_violations(dynamic_inline))
        self.assertTrue(
            python_process_cleanup_violations(
                "import subprocess\n"
                "subprocess.run(['rm', '-rf', '/opt/hostedtoolcache'])\n"
            )
        )

    def test_python_cleanup_proof_is_scoped_and_revoked_on_reassignment(self) -> None:
        secure_temporary_objects = """import shutil
import tempfile
from pathlib import Path
with tempfile.NamedTemporaryFile(delete=False) as handle:
    target = Path(handle.name)
target.unlink()
with tempfile.TemporaryDirectory() as directory:
    nested = Path(directory) / 'oncotracer-owned-child'
    shutil.rmtree(nested)
"""
        self.assertEqual(python_cleanup_violations(secure_temporary_objects, 1), [])

        adversarial = (
            "import shutil, sys, tempfile\n"
            "from pathlib import Path\n"
            "with tempfile.NamedTemporaryFile(delete=False) as handle:\n"
            "    handle.name = sys.argv[1]\n"
            "    Path(handle.name).unlink()\n",
            "import shutil, sys, tempfile\n"
            "from pathlib import Path\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    target = Path(directory) / 'owned'\n"
            "    target = Path(sys.argv[1])\n"
            "    shutil.rmtree(target)\n",
            "import shutil, sys, tempfile\n"
            "from pathlib import Path\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    target = Path(directory) / 'owned'\n"
            "    if input():\n"
            "        target = Path(sys.argv[1])\n"
            "    shutil.rmtree(target)\n",
            "import sys, tempfile\n"
            "from pathlib import Path\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    target = Path(sys.argv[1])\n"
            "    for _item in []:\n"
            "        target = Path(directory) / 'owned'\n"
            "    target.unlink()\n",
            "import tempfile\n"
            "from pathlib import Path\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    target = Path(directory) / '/opt/hostedtoolcache'\n"
            "    target.unlink()\n",
            "import shutil\n"
            "def TemporaryDirectory():\n"
            "    return '/opt/hostedtoolcache'\n"
            "target = TemporaryDirectory()\n"
            "shutil.rmtree(target)\n",
            "import shutil, tempfile\n"
            "class FakeTempfile:\n"
            "    def TemporaryDirectory(self):\n"
            "        return '/opt/hostedtoolcache'\n"
            "tempfile = FakeTempfile()\n"
            "target = tempfile.TemporaryDirectory()\n"
            "shutil.rmtree(target)\n",
            "import tempfile\n"
            "from pathlib import Path\n"
            "class Safe:\n"
            "    def setUp(self):\n"
            "        self.tmp = tempfile.TemporaryDirectory()\n"
            "        self.root = Path(self.tmp.name)\n"
            "class Unsafe:\n"
            "    def test_delete(self):\n"
            "        self.root.unlink()\n",
            "from pathlib import Path\n"
            "def active_default(value=Path('/opt/hostedtoolcache').unlink()):\n"
            "    return value\n",
            "from pathlib import Path\n"
            "target = '$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}/owned'\n"
            "def cleanup(target):\n"
            "    Path(target).unlink()\n"
            "cleanup(input())\n",
            "from pathlib import Path\n"
            "target = '$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}/owned'\n"
            "target += input()\n"
            "Path(target).unlink()\n",
            "from pathlib import Path\n"
            "target = '$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}/owned'\n"
            "target, other = input(), 'value'\n"
            "Path(target).unlink()\n",
        )
        for source in adversarial:
            with self.subTest(source=source):
                self.assertTrue(python_cleanup_violations(source, 1))

    def test_shell_heredoc_is_executable_but_data_heredoc_is_not(self) -> None:
        executable = """bash <<'SHELL'
docker --context default buildx --builder shared prune --all --force
SHELL
"""
        self.assertTrue(shell_cleanup_violations(executable))
        data = executable.replace("bash <<", "cat <<")
        self.assertEqual(shell_cleanup_violations(data), [])

    def test_recursive_workflow_local_action_and_entrypoint_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/actions/audit").mkdir(parents=True)
            (root / ".github/actions/docker-audit").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                "jobs:\n  test:\n    steps:\n      - uses: ./.github/actions/audit\n",
                encoding="utf-8",
            )
            action = root / ".github/actions/audit/action.yml"
            action.write_text(
                "runs:\n  using: composite\n  steps:\n"
                "    - run: bash scripts/first.sh\n      shell: bash\n"
                "    - uses: ./.github/actions/docker-audit\n",
                encoding="utf-8",
            )
            (root / ".github/actions/docker-audit/action.yml").write_text(
                "runs:\n  using: docker\n  image: Dockerfile\n",
                encoding="utf-8",
            )
            (root / ".github/actions/docker-audit/Dockerfile").write_text(
                'FROM scratch\nENTRYPOINT ["entrypoint.sh"]\n', encoding="utf-8"
            )
            (root / ".github/actions/docker-audit/entrypoint.sh").write_text(
                "bash scripts/fourth.sh\n", encoding="utf-8"
            )
            (root / "scripts/first.sh").write_text(
                "python3 tests/second.py\n", encoding="utf-8"
            )
            (root / "tests/second.py").write_text(
                "import subprocess\n"
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                "HELPER = ROOT / 'scripts' / 'third.sh'\n"
                "command = ['bash', str(HELPER)]\n"
                "subprocess.run(command)\n",
                encoding="utf-8",
            )
            (root / "scripts/third.sh").write_text(
                "docker buildx --builder shared prune --all --force\n",
                encoding="utf-8",
            )
            (root / "scripts/fourth.sh").write_text("true\n", encoding="utf-8")
            surfaces = {workflow: workflow_command_surfaces(workflow.read_text())}
            entrypoints, action_runs, errors = active_hosted_entrypoints(
                surfaces, root=root
            )
            self.assertEqual(errors, [])
            self.assertEqual(len(action_runs), 1)
            self.assertEqual(
                {path.relative_to(root).as_posix() for path in entrypoints},
                {
                    ".github/actions/docker-audit/entrypoint.sh",
                    "scripts/first.sh",
                    "tests/second.py",
                    "scripts/third.sh",
                    "scripts/fourth.sh",
                },
            )
            third = root / "scripts/third.sh"
            self.assertTrue(shell_cleanup_violations(third.read_text()))

    def test_exact_run_owned_cleanup_is_allowed(self) -> None:
        fixtures = (
            'rm -rf -- "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}"',
            'rm --recursive --force "${GITHUB_WORKSPACE}/oncotracer-parity-${GITHUB_RUN_ID}"',
            'docker image rm "oncotracer:v2-ci-${GITHUB_RUN_ID}"',
            'docker container rm "oncotracer-parity-${GITHUB_RUN_ID}"',
            'docker compose --project-name "oncotracer-${GITHUB_RUN_ID}" down',
            'rm -f -- "$RUNNER_TEMP/oncotracer-marker-${GITHUB_RUN_ID}"',
            'find "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}" -mindepth 1 -delete',
            'unlink "$RUNNER_TEMP/oncotracer-marker-${GITHUB_RUN_ID}"',
            'rsync -a --delete source/ "$RUNNER_TEMP/oncotracer-sync-${GITHUB_RUN_ID}"',
            'git -C "$RUNNER_TEMP/oncotracer-tree-${GITHUB_RUN_ID}" clean -fdx',
        )
        for fixture in fixtures:
            with self.subTest(command=fixture):
                self.assertEqual(shell_cleanup_violations(fixture), [], fixture)

    def test_unqualified_or_ambiguous_cleanup_is_rejected(self) -> None:
        fixtures = (
            'rm -rf -- "$RUNNER_TEMP/oncotracer"',
            'rm -f -- "$RUNNER_TEMP/oncotracer-shared-marker"',
            'rm -rf -- "$TMP_DIR"',
            'rm -rf -- "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}"/*',
            'rm -rf -- "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}/"',
            'rm -rf -- "/opt/oncotracer-${GITHUB_RUN_ID}"',
            "docker image rm oncotracer:v2-ci",
            'docker image rm "$IMAGE_ID"',
            'docker image rm -- "$reference"',
            'docker image rm "oncotracer-${GITHUB_RUN_ID}:$IMAGE_ID"',
            'docker container rm "oncotracer-${GITHUB_RUN_ID}-$CONTAINER_ID"',
            'rm -rf -- "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}/$UNTRUSTED"',
            "docker compose down",
            'find -L "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}" -delete',
        )
        for fixture in fixtures:
            with self.subTest(command=fixture):
                self.assertTrue(shell_cleanup_violations(fixture), fixture)

    def test_verified_image_helper_is_narrowly_structural(self) -> None:
        source = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        self.assertEqual(verified_image_helper_violations(source), [])
        dynamic_delete = 'docker image rm -- "$reference"'
        alias_start = source.index("  verify_job_owned_image_aliases() {")
        alias_end = source.index("\n  }\n\n  expected_manifest=", alias_start) + 4
        alias_helper = source[alias_start:alias_end]
        fail_open_alias_helper = alias_helper.replace("exit 1", "true")
        self.assertNotEqual(alias_helper, fail_open_alias_helper)
        self.assertTrue(shell_cleanup_violations(dynamic_delete))
        self.assertEqual(
            shell_cleanup_violations(
                dynamic_delete, allow_verified_manifest_reference=True
            ),
            [],
        )

        mutations = (
            source.replace(
                '[[ "$observed_id" == "$image_id" ]]',
                '[[ -n "$observed_id" ]]',
            ),
            source.replace(
                '[[ "$observed_id" == "$image_id" ]]',
                '[[ -n "$observed_id" ]] # [[ "$observed_id" == "$image_id" ]]',
            ),
            source.replace(
                '[[ -z "$containers" ]]',
                "true",
            ),
            source + "\\n" + dynamic_delete + "\\n",
            source.replace(
                "run_native_environment_probe() {",
                'docker rmi "$reference"\n}\n\nrun_native_environment_probe() {',
            ),
            source.replace(
                dynamic_delete,
                'docker image rm --force -- "$reference"',
            ),
            source.replace(
                dynamic_delete,
                "true",
            ).replace(
                '    [[ -n "$reference" && -z "$extra" ]]',
                '    docker image rm -- "$reference"\n'
                '    [[ -n "$reference" && -z "$extra" ]]',
                1,
            ),
            source.replace(dynamic_delete, "true", 1).replace(
                '      [[ -z "$containers" ]] || {',
                '      docker image rm -- "$reference"\n'
                '      [[ -z "$containers" ]] || {',
                1,
            ),
            source.replace(
                dynamic_delete,
                'else\n      docker image rm -- "$reference"',
                1,
            ),
            source.replace(
                '          [[ "$current_id" == "$image_id" ]] || {',
                "          true",
                1,
            ),
            source.replace(dynamic_delete, "true", 1).replace(
                '        elif [[ -z "${removed_aliases[$reference]+present}" ]]; then',
                '        elif [[ -z "${removed_aliases[$reference]+present}" ]]; then\n'
                '          docker image rm -- "$reference"',
                1,
            ),
            source.replace(
                '      [[ -n "$expected_alias" &&\n'
                '        -z "${expected_alias_keys[$expected_alias]+present}" ]] || {',
                "      true || {",
                1,
            ),
            source.replace(
                '      [[ -n "$canonical_alias" &&\n'
                '        -z "${actual_alias_keys[$canonical_alias]+present}" ]] || {',
                "      true || {",
                1,
            ),
            source.replace(
                '      [[ -n "${expected_alias_keys[$canonical_alias]+present}" ]] || {',
                "      true",
                1,
            ),
            source.replace(
                '    [[ "$actual_alias_count" -eq "$expected_alias_count" ]] || {',
                "    true",
                1,
            ),
            source.replace(
                '      [[ -n "${actual_alias_keys[$expected_alias]+present}" ]] || {',
                "      true",
                1,
            ),
            source.replace(
                '      [[ "$rebound_id" == "$target_image_id" ]] || {',
                "      true",
                1,
            ),
            source.replace(
                '    verify_job_owned_image_aliases "$image_id"',
                "    true",
                1,
            ),
            source.replace(
                '          verify_job_owned_image_aliases "$image_id"',
                "          true",
                1,
            ),
            source.replace(
                '          [[ -z "$daemon_containers" ]] || {',
                "          true",
                1,
            ),
            source.replace(
                '  expected_manifest="$RUNNER_TEMP/oncotracer-image-ownership-',
                "  verify_job_owned_image_aliases() { :; }\n\n"
                '  expected_manifest="$RUNNER_TEMP/oncotracer-image-ownership-',
                1,
            ),
            source.replace(
                dynamic_delete,
                dynamic_delete
                + "\n          docker_cmd=docker\n"
                + '          "$docker_cmd" image rm -- "$reference"',
                1,
            ),
            source.replace(
                "  verify_job_owned_image_aliases() {",
                "  verify_job_owned_image_aliases() {\n    return 0",
                1,
            ),
            source.replace(alias_helper, fail_open_alias_helper, 1),
        )
        for mutation in mutations:
            with self.subTest():
                self.assertTrue(verified_image_helper_violations(mutation))

    def test_verified_image_helper_checks_identity_and_use_before_exact_rm(
        self,
    ) -> None:
        source = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        start = source.index("remove_owned_image_references() {")
        end = source.index("\n}\n\nrun_native_environment_probe() {", start) + 2
        helper = source[start:end]
        digest = "sha256:" + "a" * 64
        v1_image_id = "sha256:" + "c" * 64
        v1 = "docker.io/carlosfarkas/oncotracer@sha256:" + "b" * 64
        mutable = "docker.io/example/oncotracer:v1"
        immutable = f"docker.io/example/oncotracer@{digest}"
        index_mutable = "index.docker.io/example/oncotracer:v1"
        index_immutable = f"index.docker.io/example/oncotracer@{digest}"

        def invoke(
            *,
            alias_cascade: bool = True,
            containers: bool = False,
            mismatch: bool = False,
            missing_before_cleanup: bool = False,
            shared: bool = False,
            untracked_alias: bool = False,
            index_aliases: bool = False,
            duplicate_canonical_alias: bool = False,
            duplicate_tracked_canonical: bool = False,
            disappear_before_alias_output_call: int = 0,
            disappear_after_alias_output_call: int = 0,
        ) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                runner_temp = root / "runner"
                context = root / "context"
                runner_temp.mkdir()
                context.mkdir()
                manifest = (
                    runner_temp / "oncotracer-image-ownership-77-3-quickstart1.tsv"
                )
                pins = root / "pins.tsv"
                pin_lines = ["container\tmanifest_digest", f"{mutable}\t{digest}"]
                ownership_lines = [
                    "reference\timage_id\tcreated_by_job",
                    f"{v1}\t{v1_image_id}\t0",
                    f"{immutable}\t{digest}\t{int(not shared)}",
                    f"{mutable}\t{digest}\t1",
                ]
                if duplicate_tracked_canonical:
                    pin_lines.append(f"{index_mutable}\t{digest}")
                    ownership_lines.extend(
                        (
                            f"{index_immutable}\t{digest}\t1",
                            f"{index_mutable}\t{digest}\t1",
                        )
                    )
                pins.write_text(
                    "\n".join(pin_lines) + "\n",
                    encoding="utf-8",
                )
                manifest.write_text(
                    "\n".join(ownership_lines) + "\n",
                    encoding="utf-8",
                )
                fake_bin = root / "bin"
                fake_bin.mkdir()
                fake = fake_bin / "docker"
                fake.write_text(
                    """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [[ "$1 $2" == 'image inspect' ]]; then
  reference="$3"
  if [[ "${4:-}" == --format ]]; then
    if [[ "$reference" == "$EXPECTED_ID" && "${5:-}" == *'.RepoTags'* ]]; then
      alias_call=0
      if [[ -e "$ALIAS_COUNT_FILE" ]]; then
        read -r alias_call < "$ALIAS_COUNT_FILE"
      fi
      alias_call=$((alias_call + 1))
      printf '%s\n' "$alias_call" > "$ALIAS_COUNT_FILE"
      if [[ "$FAKE_DISAPPEAR_BEFORE_ALIAS_OUTPUT_CALL" == "$alias_call" ]]; then
        grep -Fxv -- "$MUTABLE_REFERENCE" "$STATE_FILE" > "$STATE_FILE.next"
        mv "$STATE_FILE.next" "$STATE_FILE"
      fi
      if grep -Fxq -- "$MUTABLE_REFERENCE" "$STATE_FILE"; then
        if [[ "$FAKE_INDEX_ALIASES" == 1 ]]; then
          printf 'index.docker.io/%s\n' "${MUTABLE_REFERENCE#docker.io/}"
        else
          printf '%s\n' "${MUTABLE_REFERENCE#docker.io/}"
        fi
        if [[ "$FAKE_DUPLICATE_CANONICAL_ALIAS" == 1 ]]; then
          printf '%s\n' "$MUTABLE_REFERENCE"
        fi
      fi
      if grep -Fxq -- "$IMMUTABLE_REFERENCE" "$STATE_FILE"; then
        if [[ "$FAKE_INDEX_ALIASES" == 1 ]]; then
          printf 'index.docker.io/%s\n' "${IMMUTABLE_REFERENCE#docker.io/}"
        else
          printf '%s\n' "${IMMUTABLE_REFERENCE#docker.io/}"
        fi
      fi
      if [[ "$FAKE_UNTRACKED_ALIAS" == 1 ]]; then
        printf '%s\n' 'example/oncotracer:preexisting'
      fi
      if [[ "$FAKE_DISAPPEAR_AFTER_ALIAS_OUTPUT_CALL" == "$alias_call" ]]; then
        grep -Fxv -- "$MUTABLE_REFERENCE" "$STATE_FILE" > "$STATE_FILE.next"
        mv "$STATE_FILE.next" "$STATE_FILE"
      fi
      exit 0
    fi
    grep -Fxq -- "$reference" "$STATE_FILE" || exit 1
    if [[ "$FAKE_MISMATCH" == 1 && "$reference" == *':v1' ]]; then
      printf 'sha256:%064d\n' 0
    elif [[ "$reference" == "$V1_REFERENCE" ]]; then
      printf '%s\n' "$V1_IMAGE_ID"
    else
      printf '%s\n' "$EXPECTED_ID"
    fi
  else
    grep -Fxq -- "$reference" "$STATE_FILE"
  fi
elif [[ "$1" == ps ]]; then
  [[ "$FAKE_CONTAINERS" == 0 ]] || printf 'container-id\n'
elif [[ "$1 $2" == 'image rm' ]]; then
  reference="$4"
  if [[ "$FAKE_ALIAS_CASCADE" == 1 &&
    ( "$reference" == "$MUTABLE_REFERENCE" || "$reference" == "$IMMUTABLE_REFERENCE" ) ]]; then
    grep -Fxv -e "$MUTABLE_REFERENCE" -e "$IMMUTABLE_REFERENCE" \
      "$STATE_FILE" > "$STATE_FILE.next"
  else
    grep -Fxv -- "$reference" "$STATE_FILE" > "$STATE_FILE.next"
  fi
  mv "$STATE_FILE.next" "$STATE_FILE"
else
  exit 91
fi
""",
                    encoding="utf-8",
                )
                fake.chmod(0o755)
                state = root / "state"
                initial_references = [v1, immutable, mutable]
                if duplicate_tracked_canonical:
                    initial_references.extend((index_immutable, index_mutable))
                if missing_before_cleanup:
                    initial_references.remove(mutable)
                state.write_text("\n".join(initial_references) + "\n", encoding="utf-8")
                docker_log = root / "docker.log"
                program = "\n".join(
                    (
                        "set -Eeuo pipefail",
                        helper,
                        "remove_owned_image_references",
                    )
                )
                environment = {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "GITHUB_ACTIONS": "true",
                    "RUNNER_NAME": "isolated-test-runner",
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_RUN_ID": "77",
                    "GITHUB_RUN_ATTEMPT": "3",
                    "SUITE": "quickstart1",
                    "IMAGE_OWNERSHIP": str(manifest),
                    "PINS": str(pins),
                    "CONTEXT": str(context),
                    "V1_DOCKER_IMAGE": v1,
                    "DOCKER_LOG": str(docker_log),
                    "STATE_FILE": str(state),
                    "EXPECTED_ID": digest,
                    "V1_IMAGE_ID": v1_image_id,
                    "V1_REFERENCE": v1,
                    "MUTABLE_REFERENCE": mutable,
                    "IMMUTABLE_REFERENCE": immutable,
                    "FAKE_ALIAS_CASCADE": str(int(alias_cascade)),
                    "FAKE_UNTRACKED_ALIAS": str(int(untracked_alias)),
                    "FAKE_INDEX_ALIASES": str(int(index_aliases)),
                    "FAKE_DUPLICATE_CANONICAL_ALIAS": str(
                        int(duplicate_canonical_alias)
                    ),
                    "FAKE_DISAPPEAR_BEFORE_ALIAS_OUTPUT_CALL": str(
                        disappear_before_alias_output_call
                    ),
                    "FAKE_DISAPPEAR_AFTER_ALIAS_OUTPUT_CALL": str(
                        disappear_after_alias_output_call
                    ),
                    "FAKE_MISMATCH": str(int(mismatch)),
                    "FAKE_CONTAINERS": str(int(containers)),
                    "ALIAS_COUNT_FILE": str(root / "alias-count"),
                }
                completed = subprocess.run(
                    ["bash", "-c", program],
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                completed.docker_log = docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
                action_path = context / "job-image-reference-actions.tsv"
                completed.action_log = (  # type: ignore[attr-defined]
                    action_path.read_text(encoding="utf-8")
                    if action_path.exists()
                    else ""
                )
                completed.final_state = state.read_text(encoding="utf-8")  # type: ignore[attr-defined]
                return completed

        success = invoke()
        self.assertEqual(success.returncode, 0, success.stderr)
        commands = success.docker_log.splitlines()  # type: ignore[attr-defined]
        rm_index = commands.index(f"image rm -- {immutable}")
        self.assertLess(
            commands.index(f"image inspect {immutable} --format {{{{.Id}}}}"),
            rm_index,
        )
        self.assertLess(
            commands.index(f"ps --all --quiet --filter ancestor={immutable}"),
            rm_index,
        )
        self.assertEqual(sum(item.startswith("image rm ") for item in commands), 1)
        self.assertEqual(success.final_state, f"{v1}\n")  # type: ignore[attr-defined]
        self.assertIn(
            f"{immutable}\t{digest}\tREMOVED_JOB_CREATED\n",
            success.action_log,  # type: ignore[attr-defined]
        )
        self.assertIn(
            f"{mutable}\t{digest}\tREMOVED_JOB_CREATED\n",
            success.action_log,  # type: ignore[attr-defined]
        )

        index_names = invoke(index_aliases=True)
        self.assertEqual(index_names.returncode, 0, index_names.stderr)

        duplicate = invoke(duplicate_canonical_alias=True)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("canonically duplicated", duplicate.stderr)
        self.assertNotIn("image rm ", duplicate.docker_log)  # type: ignore[attr-defined]

        duplicate_tracked = invoke(duplicate_tracked_canonical=True)
        self.assertNotEqual(duplicate_tracked.returncode, 0)
        self.assertIn("Tracked Docker aliases", duplicate_tracked.stderr)
        self.assertIn("canonically duplicated", duplicate_tracked.stderr)
        self.assertNotIn(  # type: ignore[attr-defined]
            "image rm ", duplicate_tracked.docker_log
        )

        disappeared_before_snapshot = invoke(disappear_before_alias_output_call=1)
        self.assertNotEqual(disappeared_before_snapshot.returncode, 0)
        self.assertIn(
            "missing a tracked job-owned alias", disappeared_before_snapshot.stderr
        )
        self.assertNotIn(  # type: ignore[attr-defined]
            "image rm ", disappeared_before_snapshot.docker_log
        )

        disappeared_after_snapshot = invoke(disappear_after_alias_output_call=1)
        self.assertNotEqual(disappeared_after_snapshot.returncode, 0)
        self.assertIn("disappeared after inventory", disappeared_after_snapshot.stderr)
        self.assertNotIn(  # type: ignore[attr-defined]
            "image rm ", disappeared_after_snapshot.docker_log
        )

        disappeared_during_immediate_recheck = invoke(
            disappear_after_alias_output_call=2
        )
        self.assertNotEqual(disappeared_during_immediate_recheck.returncode, 0)
        self.assertIn(
            "disappeared after inventory", disappeared_during_immediate_recheck.stderr
        )
        self.assertNotIn(  # type: ignore[attr-defined]
            "image rm ", disappeared_during_immediate_recheck.docker_log
        )

        no_cascade = invoke(alias_cascade=False)
        self.assertEqual(no_cascade.returncode, 0, no_cascade.stderr)
        self.assertEqual(
            sum(
                item.startswith("image rm ")
                for item in no_cascade.docker_log.splitlines()  # type: ignore[attr-defined]
            ),
            2,
        )

        shared = invoke(shared=True)
        self.assertEqual(shared.returncode, 0, shared.stderr)
        self.assertNotIn("image rm ", shared.docker_log)  # type: ignore[attr-defined]
        self.assertIn(
            f"{mutable}\t{digest}\tPRESERVED_JOB_CREATED_SHARED\n",
            shared.action_log,  # type: ignore[attr-defined]
        )
        mismatch = invoke(mismatch=True)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertNotIn("image rm ", mismatch.docker_log)  # type: ignore[attr-defined]
        active = invoke(containers=True)
        self.assertNotEqual(active.returncode, 0)
        self.assertNotIn("image rm ", active.docker_log)  # type: ignore[attr-defined]
        missing = invoke(missing_before_cleanup=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotIn("image rm ", missing.docker_log)  # type: ignore[attr-defined]
        untracked = invoke(untracked_alias=True)
        self.assertNotEqual(untracked.returncode, 0)
        self.assertIn("untracked alias", untracked.stderr)
        self.assertNotIn("image rm ", untracked.docker_log)  # type: ignore[attr-defined]

    def test_comments_names_heredoc_data_and_echo_are_not_commands(self) -> None:
        workflow = """name: Do not run docker system prune
on: workflow_dispatch
jobs:
  safe:
    runs-on: ubuntu-24.04
    steps:
      # uses: jlumbroso/free-disk-space@v1
      - name: Explain why rm -rf is forbidden
        run: |
          echo 'docker system prune --all --force'
          cat <<'TEXT'
          nextflow clean -f
          shutil.rmtree('/opt/hostedtoolcache')
          TEXT
"""
        runs, uses = workflow_command_surfaces(workflow)
        self.assertEqual(uses, [])
        self.assertEqual(
            [
                finding
                for _, shell in runs
                for finding in shell_cleanup_violations(shell)
            ],
            [],
        )

    def test_inventory_prose_and_subcommand_arguments_do_not_false_positive(
        self,
    ) -> None:
        safe_shell = """docker images --digests
docker image rm "oncotracer:v2-ci-${GITHUB_RUN_ID}"
conda run echo clean
nextflow run main.nf --name clean
python3 -c "print('shutil.rmtree is forbidden')"
cat <<'PY'
import shutil
shutil.rmtree('/opt/hostedtoolcache')
PY
"""
        self.assertEqual(shell_cleanup_violations(safe_shell), [])

    def test_active_cleanup_action_is_rejected_but_comment_is_ignored(self) -> None:
        workflow = """jobs:
  unsafe:
    steps:
      # uses: easimon/maximize-build-space@v10
      - uses: jlumbroso/free-disk-space@v1
"""
        _, uses = workflow_command_surfaces(workflow)
        active = [
            action
            for _, action in uses
            if action.casefold().startswith(WHOLE_RUNNER_ACTIONS)
        ]
        self.assertEqual(active, ["jlumbroso/free-disk-space@v1"])

    def test_resource_preflight_passes_and_fails_without_mutation(self) -> None:
        script = ROOT / "scripts" / "ci_resource_preflight.sh"
        base = [
            "bash",
            str(script),
            "--purpose",
            "regression fixture",
            "--min-addressable-gib",
            "1",
            "--min-physical-gib",
            "1",
            "--planned-swap-gib",
            "0",
            "--standard-contract-free-gib",
            "14",
            "--run-id",
            "123",
            "--run-attempt",
            "2",
            "--suite",
            "regression-fixture",
            "--candidate-sha",
            "a" * 40,
            "--expected-swap-file",
            "none",
            "--path",
            str(ROOT),
        ]
        passing = subprocess.run(
            [*base, "--min-free-gib", "1"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(passing.returncode, 0, passing.stderr)
        self.assertIn("resource_preflight_required_free_gib=1", passing.stdout)
        self.assertIn("resource_preflight_status=PASS", passing.stdout)
        with tempfile.TemporaryDirectory() as raw:
            evidence = Path(raw) / "preflight.txt"
            evidence.write_text(passing.stdout, encoding="utf-8")
            verified = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/verify_ci_resource_preflight.py"),
                    "--evidence",
                    str(evidence),
                    "--run-id",
                    "123",
                    "--run-attempt",
                    "2",
                    "--suite",
                    "regression-fixture",
                    "--candidate-sha",
                    "a" * 40,
                    "--min-free-gib",
                    "1",
                    "--min-physical-gib",
                    "1",
                    "--min-addressable-gib",
                    "1",
                    "--planned-swap-gib",
                    "0",
                    "--standard-contract-free-gib",
                    "14",
                    "--expected-swap-file",
                    "none",
                    "--path",
                    str(ROOT),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            tampered = passing.stdout.replace(
                "resource_preflight_run_id=123", "resource_preflight_run_id=999"
            )
            evidence.write_text(tampered, encoding="utf-8")
            rejected = subprocess.run(
                verified.args,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("does not match", rejected.stderr)

        failing = subprocess.run(
            [*base, "--min-free-gib", "1048576"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(failing.returncode, 1)
        self.assertIn("requires at least 1048576 GiB free", failing.stderr)
        self.assertIn("Broad host cleanup is not an accepted remedy", failing.stderr)

        memory_failing = subprocess.run(
            [
                *base,
                "--min-free-gib",
                "1",
                "--min-physical-gib",
                "1048576",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(memory_failing.returncode, 1)
        self.assertIn(
            "requires at least 1048576 GiB physical memory", memory_failing.stderr
        )
        self.assertIn("swap is not a substitute", memory_failing.stderr)

        split_filesystem = subprocess.run(
            [*base, "--min-free-gib", "1", "--path", "/tmp"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(split_filesystem.returncode, 0, split_filesystem.stderr)
        self.assertIn(
            "resource_preflight_checked_path_count=2", split_filesystem.stdout
        )
        self.assertIn(
            "resource_preflight_checked_path_001_path=/tmp", split_filesystem.stdout
        )

        one_filesystem_low = subprocess.run(
            [*base, "--min-free-gib", "1", "--path", "/proc"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(one_filesystem_low.returncode, 1)
        self.assertIn("every checked filesystem", one_filesystem_low.stderr)
        self.assertIn(
            "capacities are never summed across filesystems", one_filesystem_low.stderr
        )

        with tempfile.TemporaryDirectory() as raw:
            fixture = Path(raw)
            first = fixture / "first"
            second = fixture / "second"
            first.mkdir()
            second.mkdir()
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            fake_df = fake_bin / "df"
            fake_df.write_text(
                "#!/bin/sh\n"
                "for target do :; done\n"
                f'if [ "$target" = "{first}" ]; then device=/dev/alpha; free=2097152; '
                f'elif [ "$target" = "{second}" ]; then device=/dev/beta; free=${{SECOND_FREE_KIB:-2097152}}; '
                "else exit 91; fi\n"
                "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'\n"
                'printf \'%s 4194304 0 %s 0%% /fixture\\n\' "$device" "$free"\n',
                encoding="utf-8",
            )
            fake_df.chmod(0o755)
            fake_base = [
                *base[: base.index("--path")],
                "--path",
                str(first),
                "--path",
                str(second),
                "--min-free-gib",
                "1",
            ]
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            two_devices = subprocess.run(
                fake_base,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(two_devices.returncode, 0, two_devices.stderr)
            self.assertIn(
                "resource_preflight_unique_device_count=2", two_devices.stdout
            )
            self.assertIn(
                f"resource_preflight_checked_path_001_path={second}",
                two_devices.stdout,
            )
            multi_evidence = fixture / "multi-device-preflight.txt"
            multi_evidence.write_text(two_devices.stdout, encoding="utf-8")
            multi_verifier = [
                "python3",
                str(ROOT / "scripts/verify_ci_resource_preflight.py"),
                "--evidence",
                str(multi_evidence),
                "--run-id",
                "123",
                "--run-attempt",
                "2",
                "--suite",
                "regression-fixture",
                "--candidate-sha",
                "a" * 40,
                "--min-free-gib",
                "1",
                "--min-physical-gib",
                "1",
                "--min-addressable-gib",
                "1",
                "--planned-swap-gib",
                "0",
                "--standard-contract-free-gib",
                "14",
                "--expected-swap-file",
                "none",
                "--path",
                str(first),
                "--path",
                str(second),
            ]
            verified_multi = subprocess.run(
                multi_verifier,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verified_multi.returncode, 0, verified_multi.stderr)
            multi_evidence.write_text(
                two_devices.stdout.replace(
                    f"resource_preflight_checked_path_001_path={second}",
                    "resource_preflight_checked_path_001_path=/wrong-device-path",
                ),
                encoding="utf-8",
            )
            rejected_path_binding = subprocess.run(
                multi_verifier,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected_path_binding.returncode, 0)
            self.assertIn("exact invocation path", rejected_path_binding.stderr)
            environment["SECOND_FREE_KIB"] = "524288"
            one_device_low = subprocess.run(
                fake_base,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(one_device_low.returncode, 1)
            self.assertIn(str(second), one_device_low.stderr)
            self.assertIn("/dev/beta", one_device_low.stderr)

        missing_path = subprocess.run(
            [
                *base,
                "--min-free-gib",
                "1",
                "--path",
                "/path-that-must-not-exist/oncotracer-preflight",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing_path.returncode, 1)
        self.assertIn("resource-preflight path does not exist", missing_path.stderr)

    def test_native_probe_requires_each_readcounter_semantic_line(self) -> None:
        source = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        start = source.index("run_native_environment_probe() {")
        end = source.index("\n}\n\ncreate_native_environment() {", start) + 2
        helper = source[start:end]

        def invoke(output: str, status: int) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                context = root / "context"
                probes = context / "native-environment-probes"
                probes.mkdir(parents=True)
                executable = root / "readCounter"
                rendered = output.replace("{executable}", str(executable))
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s' {shlex.quote(rendered)}\n"
                    f"exit {status}\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
                harness = f"""set -Eeuo pipefail
{helper}
CONTEXT={shlex.quote(str(context))}
printf 'environment\\tprobe\\tresult\\tevidence_sha256\\n' \\
  > "$CONTEXT/native-environment-probes.tsv"
run_native_environment_probe ichorcna readcounter 255 \\
  $'^Please specify a BAM file[.]$\\n^Usage: .*/readCounter \\\\[options\\\\] <BAM file>$' \\
  {shlex.quote(str(executable))}
"""
                completed = subprocess.run(
                    ["bash", "-c", harness],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                rows = (
                    (context / "native-environment-probes.tsv")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                if completed.returncode == 0:
                    self.assertEqual(len(rows), 2)
                    self.assertRegex(
                        rows[1], r"^ichorcna\treadcounter\tPASS\t[0-9a-f]{64}$"
                    )
                else:
                    self.assertEqual(
                        rows,
                        ["environment\tprobe\tresult\tevidence_sha256"],
                    )
                return completed

        valid = invoke(
            "Please specify a BAM file.\n"
            "Usage: {executable} [options] <BAM file>\n"
            "\nOptions:\n    -s, --seg\n",
            255,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        missing_usage = invoke("Please specify a BAM file.\n", 255)
        self.assertEqual(missing_usage.returncode, 1)
        self.assertIn("Native environment probe failed", missing_usage.stderr)

        missing_sentinel = invoke(
            "Usage: {executable} [options] <BAM file>\n",
            255,
        )
        self.assertEqual(missing_sentinel.returncode, 1)

        joined_lines = invoke(
            "Please specify a BAM file. Usage: " "{executable} [options] <BAM file>\n",
            255,
        )
        self.assertEqual(joined_lines.returncode, 1)

        wrong_status = invoke(
            "Please specify a BAM file.\n" "Usage: {executable} [options] <BAM file>\n",
            0,
        )
        self.assertEqual(wrong_status.returncode, 1)

    def test_capacity_models_and_job_swap_are_explicit_and_fail_closed(self) -> None:
        parity = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        for required in (
            "SHARED_REFERENCE_GIB=16",
            "FROZEN_PHASE_GIB=",
            "NATIVE_PHASE_GIB=",
            '[[ "$MIN_FREE_GIB" -eq 72 ]]',
            '[[ "$PARITY_SWAP_GIB" -eq 0 && "$MIN_FREE_GIB" -eq 40 ]]',
            "scripts/ci_select_parity_swap.sh",
            "scripts/ci_resource_phase_guard.sh",
            "scripts/ci_parity_prerequisites.sh",
            "FILESYSTEM_RESERVE_GIB=8",
            "MIN_PHYSICAL_GIB=15",
            'CONDA_PKGS_DIRS="$CONDA_PACKAGE_CACHE"',
            'create_native_environment core "$REPO/environments/native-core.yml"',
            'create_native_environment qdnaseq "$REPO/environments/native-qdnaseq.yml"',
            'create_native_environment ichorcna "$REPO/environments/native-ichorcna.yml"',
            'rm -rf -- "$RUNNER_TEMP/oncotracer-conda-pkgs-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            "record_phase_resources frozen-traces-authenticated",
            "remove_owned_image_references",
            "record_phase_resources frozen-images-released",
            "record_phase_resources native-package-cache-released",
            "STANDARD_RUNNER_CONTRACT_FREE_GIB=14",
            "scripts/ci_resource_preflight.sh",
            "scripts/verify_ci_resource_preflight.py",
            '--min-free-gib "$MIN_FREE_GIB"',
            '--min-addressable-gib "$MIN_ADDRESSABLE_GIB"',
            "trap cleanup_job_swap EXIT",
            'sudo -n swapon "$SWAP_FILE"',
            'if ! sudo -n swapoff -- "$SWAP_FILE"',
            "active swap could not be established",
            "Refusing to remove active swap after swapoff failed",
            '[[ ! -e "$TEST_ROOT" && ! -L "$TEST_ROOT" ]]',
            '[[ -d "$TEST_ROOT" && ! -L "$TEST_ROOT" ]]',
            'readonly V1_PROJECT_ROOT="$TEST_ROOT/frozen-v1-project"',
            'readonly V2_PROJECT_ROOT="$TEST_ROOT/native-v2-project"',
            '--lpwgs-root "$V1_PROJECT_ROOT"',
            '--lpwgs-root "$V2_PROJECT_ROOT"',
            '[[ -d "$V1_PROJECT_ROOT" && ! -L "$V1_PROJECT_ROOT" ]]',
            '[[ ! -e "$V2_PROJECT_ROOT/references"',
            "record_phase_resources frozen-reference-released",
            "record_phase_resources final",
        ):
            self.assertIn(required, parity)
        selector = (ROOT / "scripts" / "ci_select_parity_swap.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("32", selector)
        self.assertIn("0\\tnone", selector)
        self.assertIn("oncotracer-swap-%s-%s", selector)
        self.assertNotIn("/swapfile.oncotracer", parity)
        self.assertNotIn('sudo swapon "$SWAP_FILE" || true', parity)
        self.assertNotIn('install --conda --prefix "$V2_ENV_PREFIX"', parity)
        self.assertNotIn("create_native_environment classifier", parity)
        self.assertNotIn("create_native_environment gistic", parity)
        self.assertEqual(parity.count("run --backend host"), 3)
        self.assertEqual(parity.count("run --backend conda"), 0)
        self.assertLess(
            parity.index("Select and authenticate completed nested SAMURAI traces"),
            parity.index('log "Release only image references'),
        )
        self.assertLess(
            parity.index('log "Release only image references'),
            parity.index('log "Create and probe only the native environments'),
        )
        self.assertLess(
            parity.index('"$V1_PROJECT_ROOT/references/samurai_hg38"'),
            parity.index("record_phase_resources frozen-reference-released"),
        )
        self.assertLess(
            parity.index('[[ ! -e "$TEST_ROOT" && ! -L "$TEST_ROOT" ]]'),
            parity.index('mkdir -p "$TEST_ROOT/configs"'),
        )
        self.assertLess(
            parity.index('[[ -d "$V1_PROJECT_ROOT" && ! -L "$V1_PROJECT_ROOT" ]]'),
            parity.index(
                'rm -rf -- "$GITHUB_WORKSPACE/oncotracer-parity-${GITHUB_RUN_ID}'
            ),
        )
        self.assertLess(
            parity.index("record_phase_resources frozen-reference-released"),
            parity.index('log "Create and probe only the native environments'),
        )
        self.assertNotIn('--lpwgs-root "$TEST_ROOT"', parity)

        workflow = (WORKFLOW_ROOT / "native-v2-ci.yml").read_text(encoding="utf-8")
        for required in (
            "FINAL_SCIENTIFIC_ENVIRONMENTS_GIB=14",
            "TRANSIENT_SOLVE_AND_EXPORT_GIB=18",
            "RUNNER_RESERVE_GIB=8",
            "scripts/ci_resource_preflight.sh",
            "MIN_PHYSICAL_GIB=15",
            '--min-physical-gib "$MIN_PHYSICAL_GIB"',
            "--standard-contract-free-gib 14",
            "docker-resource-preflight.txt",
        ):
            self.assertIn(required, workflow)

        release = (WORKFLOW_ROOT / "release-v2.yml").read_text(encoding="utf-8")
        for required in (
            "Require safe release-container build capacity",
            "FINAL_SCIENTIFIC_ENVIRONMENTS_GIB=14",
            "TRANSIENT_SOLVE_AND_EXPORT_GIB=18",
            "RUNNER_RESERVE_GIB=8",
            "MIN_PHYSICAL_GIB=15",
            "scripts/ci_resource_preflight.sh",
            "before any publication step",
        ):
            self.assertIn(required, release)
        capacity_start = release.index(
            "- name: Require safe release-container build capacity"
        )
        capacity_end = release.index(
            "- name: Verify immutable source identity", capacity_start
        )
        capacity_step = release[capacity_start:capacity_end]
        self.assertIn("MAIN_SHA: ${{ steps.gate.outputs.main_sha }}", capacity_step)
        self.assertIn('--candidate-sha "$MAIN_SHA"', capacity_step)
        self.assertIn("verify_ci_resource_preflight.py", capacity_step)

    def test_parity_artifact_upload_is_exact_and_fail_closed(self) -> None:
        driver = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        self.assertIn(
            'readonly TEST_ROOT="$GITHUB_WORKSPACE/oncotracer-parity-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}"',
            driver,
        )
        for number in (1, 2):
            suite = f"quickstart{number}"
            workflow = (WORKFLOW_ROOT / f"native-v2-{suite}-parity.yml").read_text(
                encoding="utf-8"
            )
            artifact_root = (
                "oncotracer-parity-${{ github.run_id }}-"
                f"${{{{ github.run_attempt }}}}-{suite}"
            )
            artifact_name = (
                f"native-v2-{suite}-parity-${{{{ github.run_id }}}}-"
                "${{ github.run_attempt }}"
            )
            self.assertIn(f"name: {artifact_name}", workflow)
            self.assertIn("if-no-files-found: error", workflow)
            self.assertIn(f"{artifact_root}/audit/**", workflow)
            self.assertIn(f"{artifact_root}/reports/**", workflow)
            self.assertNotIn(f"parity-{suite}/", workflow)

        release = (WORKFLOW_ROOT / "release-v2.yml").read_text(encoding="utf-8")
        self.assertIn(
            'Q1_NAME="native-v2-quickstart1-parity-' '$Q1_RUN_ID-$Q1_RUN_ATTEMPT"',
            release,
        )
        self.assertIn(
            'Q2_NAME="native-v2-quickstart2-parity-' '$Q2_RUN_ID-$Q2_RUN_ATTEMPT"',
            release,
        )

    def test_heavy_runner_selection_is_explicit_and_fork_safe(self) -> None:
        trusted_expression = (
            "runs-on: ${{ github.event_name == 'pull_request' && "
            "github.event.pull_request.head.repo.full_name != github.repository && "
            "'ubuntu-24.04' || vars.ONCOTRACER_HEAVY_RUNNER || 'ubuntu-24.04' }}"
        )
        for name in (
            "native-v2-quickstart1-parity.yml",
            "native-v2-quickstart2-parity.yml",
        ):
            workflow = (WORKFLOW_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(trusted_expression, workflow)

        ci = (WORKFLOW_ROOT / "native-v2-ci.yml").read_text(encoding="utf-8")
        self.assertIn(trusted_expression, ci)
        self.assertEqual(ci.count("vars.ONCOTRACER_HEAVY_RUNNER"), 1)

        release = (WORKFLOW_ROOT / "release-v2.yml").read_text(encoding="utf-8")
        self.assertIn(
            "runs-on: ${{ vars.ONCOTRACER_HEAVY_RUNNER || 'ubuntu-24.04' }}",
            release,
        )
        all_workflows = "\n".join(
            path.read_text(encoding="utf-8") for path in ACTIVE_WORKFLOWS
        )
        self.assertNotRegex(all_workflows, r"runs-on:\s*\[?\s*self-hosted")
        self.assertNotRegex(
            all_workflows, r"runs-on:\s*ubuntu-[^\s]*-(?:8|16|32|64)core"
        )

        documentation = (ROOT / "docs" / "parity_release.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "Hosted-runner capacity contract",
            "72 GiB free",
            "15 GiB physical RAM",
            "47 GiB",
            "40 GiB",
            "ONCOTRACER_HEAVY_RUNNER",
            "does not provision, purchase, resize, or clean",
            "Fork pull requests always",
            "genuine infrastructure blocker",
        ):
            self.assertIn(required, documentation)


if __name__ == "__main__":
    unittest.main()
