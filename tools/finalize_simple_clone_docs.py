#!/usr/bin/env python3
"""Make public documentation concise while preserving working absolute runtime paths."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE_COMMENT = "# Clone OncoTracer into a given directory."
CLONE_COMMAND = "git clone https://github.com/cfarkas/oncotracer.git"
CD_COMMAND = "cd oncotracer"
EXACT_CLONE = f"{CLONE_COMMENT}\n\n{CLONE_COMMAND}\n{CD_COMMAND}"

MARKDOWN_FILES = [ROOT / "README.md"]
MARKDOWN_FILES.extend(sorted((ROOT / "docs").rglob("*.md")))
MARKDOWN_FILES.extend(sorted((ROOT / "examples").rglob("README.md")))

BASH_BLOCK_RE = re.compile(r"```bash[ \t]*\n(.*?)```", re.DOTALL)
TEXT_BLOCK_RE = re.compile(r"```text[ \t]*\n(.*?)```", re.DOTALL)

GENERIC_COMMENTS = {
    "# Run this command from the oncotracer directory.",
    "# Run this step from the cloned oncotracer directory.",
    "# Set the standard repository path.",
    "# Set the repository path.",
}

CLONE_COMMENT_HINTS = (
    "clone oncotracer",
    "clone the repository",
    "enter the repository",
    "destination path",
)


def trim_blank(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def comment_for_block(text: str) -> str:
    lower = text.lower()
    if "--make_prjna754199" in text:
        return "# Download and validate the public PRJNA754199 reads."
    if "--make_test" in text:
        return "# Download and validate the QuickStart reads."
    if "--auto_params" in text:
        return "# Generate the OncoTracer configuration."
    if "poetry run oncotracer" in text or "poetry install" in text:
        return "# Install or run OncoTracer through Poetry."
    if "--singularity" in text:
        return "# Run OncoTracer through Singularity or Apptainer."
    if "--docker" in text:
        return "# Run OncoTracer through Docker."
    if "--conda" in text:
        return "# Run OncoTracer through Conda."
    if "verify_outputs.py" in text:
        return "# Verify the completed outputs."
    if any(command in lower for command in ("sed ", "cat ", "find ", "grep ", "head ", "ls ")):
        return "# Inspect the generated files."
    if any(command in lower for command in ("mkdir ", "cat >", "curl ", "wget ")):
        return "# Prepare the input files."
    return "# Run this command."


def normalize_nonclone_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in GENERIC_COMMENTS:
            continue
        cleaned.append(line.rstrip())
    cleaned = trim_blank(cleaned)
    if not cleaned:
        return []
    first_nonempty = next((line.strip() for line in cleaned if line.strip()), "")
    if not first_nonempty.startswith("#"):
        cleaned.insert(0, comment_for_block("\n".join(cleaned)))
    return cleaned


def normalize_bash_block(match: re.Match[str]) -> str:
    block = match.group(1)
    lines = block.splitlines()

    if CLONE_COMMAND not in block:
        cleaned = normalize_nonclone_lines(lines)
        if not cleaned:
            return ""
        return "```bash\n" + "\n".join(cleaned) + "\n```"

    clone_index = next(i for i, line in enumerate(lines) if CLONE_COMMAND in line)
    try:
        cd_index = next(
            i for i in range(clone_index + 1, len(lines)) if lines[i].strip() == CD_COMMAND
        )
    except StopIteration as exc:
        raise SystemExit("A clone command is not followed by 'cd oncotracer'") from exc

    extras: list[str] = []
    for line in lines[:clone_index] + lines[cd_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            extras.append("")
            continue
        if stripped in GENERIC_COMMENTS:
            continue
        if stripped.startswith("#") and any(
            hint in stripped.casefold() for hint in CLONE_COMMENT_HINTS
        ):
            continue
        extras.append(line.rstrip())

    clone_fence = f"```bash\n{EXACT_CLONE}\n```"
    extras = normalize_nonclone_lines(extras)
    if not extras:
        return clone_fence
    return clone_fence + "\n\n```bash\n" + "\n".join(extras) + "\n```"


def normalize_text_block(match: re.Match[str]) -> str:
    content = match.group(1)
    content = content.replace("/path/to/my/directory/oncotracer/test/", "test/")
    content = content.replace("/path/to/my/directory/oncotracer/project/", "project/")
    return "```text\n" + content.rstrip() + "\n```"


def replace_section(text: str, start: str, end: str, replacement: str) -> str:
    if start not in text or end not in text:
        raise SystemExit(f"Unable to locate documentation section: {start}")
    before, remainder = text.split(start, 1)
    _, after = remainder.split(end, 1)
    return before + replacement.rstrip() + "\n\n" + end + after


HCC_SAMPLE_TABLE = """sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR"""

HCC_GZIP = """gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \\
    HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \\
    HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz"""

PUBLIC_HCC_SECTION = f'''## 3. Verify the FASTQs and create `samples.csv`

Use the copy/paste-ready block below. The validation runs in a subshell, so the terminal remains inside the cloned `oncotracer` directory afterward.

```bash
# Validate the six FASTQs and create the sample table.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
CHECKSUMS="$(pwd)/examples/hcc1143_lpwgs/checksums.md5"

# Check the MD5 values and compressed files without changing the current shell directory.
(
  cd "$READS_DIR"
  md5sum -c "$CHECKSUMS"
  {HCC_GZIP}
)

# Create or replace the exact HCC1143 sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
{HCC_SAMPLE_TABLE}
CSV

# Display the saved table.
cat "$READS_DIR/samples.csv"
```

`md5sum` should print `OK` six times. `gzip -t` is silent when the FASTQs are valid.

To edit manually instead, run:

```bash
# Open the sample table in Nano.
nano test/public/hcc1143_lpwgs/samples.csv
```

Paste the same four lines, save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.'''

EXAMPLE_HCC_SECTION = f'''## 3. Validate the files and create the sample table

```bash
# Validate the six FASTQs and create the sample table.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
CHECKSUMS="$(pwd)/examples/hcc1143_lpwgs/checksums.md5"

# Check the MD5 values and compressed files without changing the current shell directory.
(
  cd "$READS_DIR"
  md5sum -c "$CHECKSUMS"
  {HCC_GZIP}
)

# Create or replace the exact HCC1143 sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
{HCC_SAMPLE_TABLE}
CSV

# Display the saved table.
cat "$READS_DIR/samples.csv"
```

`md5sum` should print `OK` six times. `gzip -t` is silent when every file is valid.'''


for path in MARKDOWN_FILES:
    text = path.read_text(encoding="utf-8")

    for sentence in (
        "Run the commands from the cloned `oncotracer` directory.\n\n",
        "Skip the clone command when the repository already exists.\n",
        "Use an empty destination path so this tutorial starts from a fresh clone.\n",
        "Use `/path/to/my/directory/oncotracer` throughout this tutorial.\n\n",
        "Use `/path/to/my/directory/oncotracer` as the repository path throughout this tutorial.\n\n",
    ):
        text = text.replace(sentence, "")

    text = text.replace(
        "The examples use `.` as the repository path.",
        "Run commands inside the cloned `oncotracer` directory.",
    )
    text = text.replace(
        "requires editing only `.`.",
        "uses paths relative to the cloned `oncotracer` directory.",
    )
    text = text.replace(
        "## 1. Clone the repository and create the data folder",
        "## 1. Clone OncoTracer",
    )
    text = text.replace("## 1. Clone the repository", "## 1. Clone OncoTracer")

    text = BASH_BLOCK_RE.sub(normalize_bash_block, text)
    text = TEXT_BLOCK_RE.sub(normalize_text_block, text)

    if path == ROOT / "README.md":
        marker = '<a id="four-equivalent-analysis-commands"></a>\n'
        clone_section = (
            "## Clone OncoTracer\n\n"
            f"```bash\n{EXACT_CLONE}\n```\n\n"
        )
        if marker in text and clone_section not in text.split(marker, 1)[0]:
            text = text.replace(marker, clone_section + marker, 1)

    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")

public_hcc = ROOT / "docs/public_cohort.md"
text = public_hcc.read_text(encoding="utf-8")
text = replace_section(
    text,
    "## 3. Verify the FASTQs and create `samples.csv`",
    "## 4. Generate the YAML automatically",
    PUBLIC_HCC_SECTION,
)
public_hcc.write_text(text, encoding="utf-8")

example_hcc = ROOT / "examples/hcc1143_lpwgs/README.md"
text = example_hcc.read_text(encoding="utf-8")
text = replace_section(
    text,
    "## 3. Validate the files and create the sample table",
    "## 4. Generate the configuration",
    EXAMPLE_HCC_SECTION,
)
example_hcc.write_text(text, encoding="utf-8")
