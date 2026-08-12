#!/usr/bin/env python3
"""Prevent host-wide cleanup from entering executable CI command surfaces."""

from __future__ import annotations

import ast
import re
import shlex
import subprocess
import unittest
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


def _python_heredoc_command(command: str) -> bool:
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
            return True
    return False


def extract_heredocs(shell: str) -> tuple[str, list[tuple[int, str]], list[str]]:
    """Remove heredoc data from shell scanning and return embedded Python bodies."""
    lines = shell.splitlines()
    retained: list[str] = []
    python_bodies: list[tuple[int, str]] = []
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
        if _python_heredoc_command(line[: match.start()]):
            python_bodies.append((body_start, "\n".join(body)))
        index += 1
    return "\n".join(retained), python_bodies, errors


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


def rm_invocation(args: list[str]) -> list[str]:
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
    if recursive or wildcard or host_absolute or runner_scoped:
        if not targets or wildcard or not all(exact_job_path(item) for item in targets):
            return [
                "filesystem deletion is not bound to exact run-ID-qualified job paths"
            ]
    return []


class PythonCleanupVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.shutil_aliases = {"shutil"}
        self.rmtree_aliases: set[str] = set()
        self.lines: list[int] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "shutil":
                self.shutil_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "shutil":
            for alias in node.names:
                if alias.name == "rmtree":
                    self.rmtree_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        direct = isinstance(function, ast.Name) and function.id in self.rmtree_aliases
        qualified = (
            isinstance(function, ast.Attribute)
            and function.attr == "rmtree"
            and isinstance(function.value, ast.Name)
            and function.value.id in self.shutil_aliases
        )
        if direct or qualified:
            self.lines.append(node.lineno)
        self.generic_visit(node)


def python_cleanup_violations(source: str, start_line: int) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        line = start_line + (error.lineno or 1) - 1
        return [f"line {line}: embedded Python cannot be audited: {error.msg}"]
    visitor = PythonCleanupVisitor()
    visitor.visit(tree)
    return [
        f"line {start_line + line - 1}: embedded Python shutil.rmtree is forbidden"
        for line in visitor.lines
    ]


def shell_cleanup_violations(
    shell: str,
    *,
    depth: int = 0,
    allow_verified_manifest_reference: bool = False,
) -> list[str]:
    if depth > 2:
        return ["line 1: nested shell audit depth exceeded"]
    shell, python_bodies, errors = extract_heredocs(shell)
    violations = list(errors)
    for line, source in python_bodies:
        violations.extend(python_cleanup_violations(source, line))

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
                    f"line {line}: {finding}" for finding in rm_invocation(args)
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


def _repository_shell_script(token: str) -> Path | None:
    marker = "scripts/"
    position = token.rfind(marker)
    if position < 0:
        return None
    prefix = token[:position]
    if not _repo_script_prefix_allowed(prefix):
        return None
    relative = token[position + len(marker) :]
    if not relative.endswith(".sh") or any(
        item in relative for item in ("$", "*", "?", "[", "]", "`")
    ):
        return None
    parts = PurePosixPath(relative).parts
    if not parts or ".." in parts:
        return None
    return ROOT / "scripts" / Path(*parts)


def referenced_shell_scripts(shell: str) -> set[Path]:
    """Find repository shell scripts that an executable shell surface invokes."""
    shell, _, _ = extract_heredocs(shell)
    chunks, _ = shell_chunks(shell)
    references: set[Path] = set()
    for _, tokens in chunks:
        for segment in command_segments(tokens):
            for token in segment:
                if "$(" not in token and "`" not in token:
                    continue
                for match in re.finditer(
                    r"scripts/(?P<relative>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.sh)",
                    token,
                ):
                    prefix = token[: match.start()]
                    if not _repo_script_prefix_allowed(prefix):
                        continue
                    references.add(ROOT / "scripts" / match.group("relative"))
            invocation = unwrap_command(segment)
            if invocation is None:
                continue
            executable, args = invocation
            candidates: list[str] = []
            executable_path = _repository_shell_script(executable)
            if executable_path is not None:
                references.add(executable_path)
            if executable in {"bash", "sh", "source", "."} and "-c" not in args:
                candidates.extend(item for item in args if not item.startswith("-"))
            for candidate in candidates[:1]:
                script = _repository_shell_script(candidate)
                if script is not None:
                    references.add(script)
    return references


def active_hosted_scripts(
    workflow_runs: dict[Path, list[tuple[int, str]]],
) -> tuple[tuple[Path, ...], list[str]]:
    """Resolve active workflow shell-script dependencies transitively."""
    pending = {
        reference
        for runs in workflow_runs.values()
        for _, shell in runs
        for reference in referenced_shell_scripts(shell)
    }
    discovered: set[Path] = set()
    errors: list[str] = []
    scripts_root = (ROOT / "scripts").resolve()
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
        if not resolved.is_relative_to(scripts_root):
            errors.append(
                f"{path.relative_to(ROOT)}: active hosted script escapes scripts/"
            )
            continue
        source = path.read_text(encoding="utf-8")
        pending.update(referenced_shell_scripts(source) - discovered)
    return tuple(sorted(discovered)), errors


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
    deletion = 'docker image rm -- "$reference"'
    helper_lines = tuple(
        line.strip()
        for line in helper.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    required = (
        'expected_manifest="$RUNNER_TEMP/oncotracer-image-ownership-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}.tsv"',
        '[[ "$IMAGE_OWNERSHIP" == "$expected_manifest" ]] || {',
        '[[ -f "$IMAGE_OWNERSHIP" && ! -L "$IMAGE_OWNERSHIP" ]] || {',
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
    else:
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
    workflow_runs: dict[Path, list[tuple[int, str]]] = {}
    for path in ACTIVE_WORKFLOWS:
        runs, uses = workflow_command_surfaces(path.read_text(encoding="utf-8"))
        workflow_runs[path] = runs
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
    hosted_scripts, discovery_errors = active_hosted_scripts(workflow_runs)
    violations.extend(discovery_errors)
    for path in hosted_scripts:
        source = path.read_text(encoding="utf-8")
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

    def test_exact_run_owned_cleanup_is_allowed(self) -> None:
        fixtures = (
            'rm -rf -- "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}"',
            'rm --recursive --force "${GITHUB_WORKSPACE}/oncotracer-parity-${GITHUB_RUN_ID}"',
            'docker image rm "oncotracer:v2-ci-${GITHUB_RUN_ID}"',
            'docker container rm "oncotracer-parity-${GITHUB_RUN_ID}"',
            'docker compose --project-name "oncotracer-${GITHUB_RUN_ID}" down',
            'rm -f -- "$RUNNER_TEMP/oncotracer-marker-${GITHUB_RUN_ID}"',
            'find "$RUNNER_TEMP/oncotracer-${GITHUB_RUN_ID}" -mindepth 1 -delete',
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
        )
        for mutation in mutations:
            with self.subTest():
                self.assertTrue(verified_image_helper_violations(mutation))

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

        failing = subprocess.run(
            [*base, "--min-free-gib", "1048576"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(failing.returncode, 1)
        self.assertIn("requires at least 1048576 GiB free", failing.stderr)
        self.assertIn("Broad host cleanup is not an accepted remedy.", failing.stderr)

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
            [*base, "--min-free-gib", "1", "--path", "/proc"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(split_filesystem.returncode, 1)
        self.assertIn("spans filesystems", split_filesystem.stderr)
        self.assertIn(
            "broad host cleanup is not an accepted remedy", split_filesystem.stderr
        )

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

    def test_capacity_models_and_job_swap_are_explicit_and_fail_closed(self) -> None:
        parity = (ROOT / "scripts" / "ci_native_parity.sh").read_text(encoding="utf-8")
        for required in (
            "SHARED_REFERENCE_GIB=16",
            "FROZEN_PHASE_GIB=",
            "NATIVE_PHASE_GIB=",
            '[[ "$MIN_FREE_GIB" -eq 72 ]]',
            "PARITY_SWAP_GIB=32",
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
            '--min-free-gib "$MIN_FREE_GIB"',
            '--min-addressable-gib "$MIN_ADDRESSABLE_GIB"',
            "trap cleanup_job_swap EXIT",
            'sudo swapon "$SWAP_FILE"',
            'if ! sudo swapoff -- "$SWAP_FILE"',
            "active swap could not be established",
            "Refusing to remove active swap after swapoff failed",
        ):
            self.assertIn(required, parity)
        self.assertIn(
            'SWAP_FILE="$RUNNER_TEMP/oncotracer-swap-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            parity,
        )
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
