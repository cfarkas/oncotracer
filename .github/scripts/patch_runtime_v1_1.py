#!/usr/bin/env python3
"""Apply the v1.1 Conda compatibility and Poetry documentation changes."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path.relative_to(ROOT)}: expected one occurrence, found {count}: {old[:100]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_illumina() -> None:
    path = ROOT / "bin/scripts/run_illumina_samurai_fastq.sh"
    replace_once(
        path,
        "set -Eeuo pipefail\n\nusage() {",
        "set -Eeuo pipefail\n\nSCRIPT_DIR=\"$(cd -- \"$(dirname -- \"${BASH_SOURCE[0]}\")\" && pwd -P)\"\n\nusage() {",
    )
    replace_once(
        path,
        'command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools is required to prepare the hg38 reference index" >&2; exit 1; }\n\nLPWGS_ROOT=',
        '''command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools is required to prepare the hg38 reference index" >&2; exit 1; }

if [[ "$SAMURAI_PROFILE" == "conda" ]]; then
  command -v Rscript >/dev/null 2>&1 || { echo "ERROR: the OncoTracer Conda environment is missing Rscript" >&2; exit 1; }
  Rscript --vanilla - <<'RS_CONDA_CHECK'
required <- c("Biobase", "QDNAseq", "argparser", "future", "R.cache")
missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) stop("Missing Conda R package(s): ", paste(missing, collapse = ", "))
RS_CONDA_CHECK
  command -v qpdf >/dev/null 2>&1 || { echo "ERROR: the OncoTracer Conda environment is missing qpdf" >&2; exit 1; }
fi

LPWGS_ROOT=''',
    )
    replace_once(
        path,
        'echo "Detected Illumina read layout: $READ_LAYOUT-end"\n\nif [[ ! -s "$REF_FA" ]]; then',
        '''echo "Detected Illumina read layout: $READ_LAYOUT-end"

QDNASEQ_BIN_ARGS=()
if [[ "$SAMURAI_PROFILE" == "conda" && "$CALLER" == "qdnaseq" ]]; then
  QDNASEQ_BIN_HELPER="$SCRIPT_DIR/prepare_qdnaseq_bin_data.sh"
  [[ -s "$QDNASEQ_BIN_HELPER" ]] || { echo "ERROR: qDNAseq annotation helper not found: $QDNASEQ_BIN_HELPER" >&2; exit 1; }
  QDNASEQ_BIN_RDS="$(bash "$QDNASEQ_BIN_HELPER" \
    --binsize "$BINSIZE" \
    --cache-dir "$LPWGS_ROOT/.oncotracer/qdnaseq-bin-data")"
  [[ -s "$QDNASEQ_BIN_RDS" ]] || { echo "ERROR: qDNAseq annotation was not prepared: $QDNASEQ_BIN_RDS" >&2; exit 1; }
  QDNASEQ_BIN_ARGS=(--qdnaseq_bin_data "$QDNASEQ_BIN_RDS")
  echo "Using qDNAseq hg38 annotation: $QDNASEQ_BIN_RDS"
fi

if [[ ! -s "$REF_FA" ]]; then''',
    )
    text = path.read_text(encoding="utf-8")
    first = '  "${QDNASEQ_LAYOUT_ARGS[@]}" \\\n  --run_fastp false \\\n'
    first_new = '  "${QDNASEQ_LAYOUT_ARGS[@]}" \\\n  "${QDNASEQ_BIN_ARGS[@]}" \\\n  --run_fastp false \\\n'
    if text.count(first) != 1:
        raise SystemExit("Illumina initial qDNAseq insertion point not found")
    text = text.replace(first, first_new, 1)
    second = '    "${QDNASEQ_LAYOUT_ARGS[@]}" \\\n    --index_genome false \\\n'
    second_new = '    "${QDNASEQ_LAYOUT_ARGS[@]}" \\\n    "${QDNASEQ_BIN_ARGS[@]}" \\\n    --index_genome false \\\n'
    if text.count(second) != 1:
        raise SystemExit("Illumina fallback qDNAseq insertion point not found")
    path.write_text(text.replace(second, second_new, 1), encoding="utf-8")


def patch_ont() -> None:
    path = ROOT / "bin/scripts/run_ont_samurai_barcodes.sh"
    replace_once(
        path,
        "set -Eeuo pipefail\ntrap 'echo \"ERROR at line ${LINENO}: ${BASH_COMMAND}\" >&2' ERR\n",
        "set -Eeuo pipefail\ntrap 'echo \"ERROR at line ${LINENO}: ${BASH_COMMAND}\" >&2' ERR\n\nSCRIPT_DIR=\"$(cd -- \"$(dirname -- \"${BASH_SOURCE[0]}\")\" && pwd -P)\"\n",
    )
    replace_once(
        path,
        '[[ "$SAMURAI_PROFILE" == "docker" || "$SAMURAI_PROFILE" == "singularity" || "$SAMURAI_PROFILE" == "conda" ]] || { echo "ERROR: --profile must be docker, singularity, or conda" >&2; exit 1; }\n\nif (( ${#NORMAL_FOLDERS[@]} > 0 )); then',
        '''[[ "$SAMURAI_PROFILE" == "docker" || "$SAMURAI_PROFILE" == "singularity" || "$SAMURAI_PROFILE" == "conda" ]] || { echo "ERROR: --profile must be docker, singularity, or conda" >&2; exit 1; }

if [[ "$SAMURAI_PROFILE" == "conda" ]]; then
  command -v Rscript >/dev/null 2>&1 || { echo "ERROR: the OncoTracer Conda environment is missing Rscript" >&2; exit 1; }
  Rscript --vanilla - <<'RS_CONDA_CHECK'
required <- c("argparser", "readr", "dplyr", "ggplot2", "scales", "yaml")
missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) stop("Missing Conda R package(s): ", paste(missing, collapse = ", "))
RS_CONDA_CHECK
  python3 - <<'PY_CONDA_CHECK'
import janitor
import natsort
import openpyxl
import pandas
import pandera
import polars
import typer
PY_CONDA_CHECK
  command -v qpdf >/dev/null 2>&1 || { echo "ERROR: the OncoTracer Conda environment is missing qpdf" >&2; exit 1; }
fi

if (( ${#NORMAL_FOLDERS[@]} > 0 )); then''',
    )
    replace_once(
        path,
        'resolve_ichorcna_refs\n\nif [[ ! -s "$REF_MMI" ]]; then',
        '''resolve_ichorcna_refs

if [[ "$SAMURAI_PROFILE" == "conda" && "$CALLER" == "qdnaseq" && -z "$QDNASEQ_BIN_DATA" ]]; then
  QDNASEQ_BIN_HELPER="$SCRIPT_DIR/prepare_qdnaseq_bin_data.sh"
  [[ -s "$QDNASEQ_BIN_HELPER" ]] || { echo "ERROR: qDNAseq annotation helper not found: $QDNASEQ_BIN_HELPER" >&2; exit 1; }
  QDNASEQ_BIN_DATA="$(bash "$QDNASEQ_BIN_HELPER" \
    --binsize "$BINSIZE" \
    --cache-dir "$LPWGS_ROOT/.oncotracer/qdnaseq-bin-data")"
  [[ -s "$QDNASEQ_BIN_DATA" ]] || { echo "ERROR: qDNAseq annotation was not prepared: $QDNASEQ_BIN_DATA" >&2; exit 1; }
  echo "Using qDNAseq hg38 annotation: $QDNASEQ_BIN_DATA"
fi

if [[ ! -s "$REF_MMI" ]]; then''',
    )
    replace_once(
        path,
        '[[ "$CALLER" == "qdnaseq" ]] && NF_CMD+=( --qdnaseq_paired_ends false )',
        '''if [[ "$CALLER" == "qdnaseq" ]]; then
  NF_CMD+=( --qdnaseq_paired_ends false )
  [[ -n "$QDNASEQ_BIN_DATA" ]] && NF_CMD+=( --qdnaseq_bin_data "$QDNASEQ_BIN_DATA" )
fi''',
    )


def write_verifiers() -> None:
    quickstart = ROOT / "examples/quickstart/verify_outputs.py"
    quickstart.write_text(
        '''#!/usr/bin/env python3
"""Verify the public Illumina and ONT QuickStart result sets."""
from __future__ import annotations
import argparse
from pathlib import Path

SUMMARY_MARKERS = {
    "Illumina": ("mode=illumina", "dataset=illumina_qdnaseq_100kb"),
    "ONT": ("mode=ont", "dataset=ONT_ichorcna_500kb"),
}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check required outputs from QuickStart Example 1.")
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--illumina-outdir", type=Path)
    parser.add_argument("--ont-outdir", type=Path)
    return parser.parse_args()

def verify(label: str, outdir: Path) -> list[str]:
    required = (
        outdir / "06_workflow_summary/workflow_summary.txt",
        outdir / "03_cna_codification/cna_events.tsv",
        outdir / "03_cna_codification/cna_cytogenomic_notation.tsv",
        outdir / "04_cna_custom_plots/cna_per_sample_pages.pdf",
        outdir / "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
    )
    problems: list[str] = []
    for output in required:
        if not output.is_file() or output.stat().st_size == 0:
            problems.append(f"missing or empty: {output}")
    summary = required[0]
    if summary.is_file() and summary.stat().st_size > 0:
        try:
            lines = set(summary.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError) as error:
            problems.append(f"could not read {summary}: {error}")
        else:
            for marker in SUMMARY_MARKERS[label]:
                if marker not in lines:
                    problems.append(f"{summary} does not contain {marker!r}")
    return problems

def main() -> int:
    args = parse_args()
    root = args.test_root.expanduser().resolve()
    illumina = (args.illumina_outdir or root / "runs/illumina").expanduser().resolve()
    ont = (args.ont_outdir or root / "runs/ont").expanduser().resolve()
    problems = verify("Illumina", illumina) + verify("ONT", ont)
    if problems:
        print("ERROR: QuickStart output verification failed.")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("SUCCESS: both QuickStart workflows completed and required outputs were found.")
    print(f"Illumina summary: {illumina / '06_workflow_summary/workflow_summary.txt'}")
    print(f"ONT summary:      {ont / '06_workflow_summary/workflow_summary.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )

    hcc = ROOT / "examples/hcc1143_lpwgs/verify_outputs.py"
    hcc.write_text(
        '''#!/usr/bin/env python3
"""Verify the complete HCC1143 QuickStart Example 2 output set."""
from __future__ import annotations
import argparse
from pathlib import Path

EXPECTED = {"HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True, type=Path)
    outdir = parser.parse_args().outdir.expanduser().resolve()
    required = (
        outdir / "01_samurai_illumina/qdnaseq/all_segments.seg",
        outdir / "03_cna_codification/cna_events.tsv",
        outdir / "03_cna_codification/cna_cytogenomic_notation.tsv",
        outdir / "04_cna_custom_plots/cna_per_sample_pages.pdf",
        outdir / "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
        outdir / "06_workflow_summary/workflow_summary.txt",
    )
    problems: list[str] = []
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"missing or empty: {path}")
    bam_dir = outdir / "01_samurai_illumina/alignment"
    bams = {path.stem for path in bam_dir.glob("*.bam") if path.stat().st_size > 0}
    if bams != EXPECTED:
        problems.append(f"expected BAMs {sorted(EXPECTED)}, found {sorted(bams)}")
    if required[0].is_file() and required[0].stat().st_size > 0:
        text = required[0].read_text(encoding="utf-8", errors="replace")
        for sample in EXPECTED:
            if sample not in text:
                problems.append(f"sample absent from qDNAseq segments: {sample}")
    if required[-1].is_file() and required[-1].stat().st_size > 0:
        lines = set(required[-1].read_text(encoding="utf-8").splitlines())
        for marker in ("mode=illumina", "dataset=illumina_qdnaseq_100kb"):
            if marker not in lines:
                problems.append(f"workflow summary is missing {marker!r}")
    if problems:
        print("ERROR: HCC1143 verification failed.")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("SUCCESS: all three HCC1143 libraries produced the required outputs.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )


def patch_docs() -> None:
    poetry = ROOT / "docs/poetry.md"
    poetry.write_text(
        '''# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer. It does not replace the scientific software runtime: the launcher calls the versioned Nextflow workflow with Docker by default, or with Singularity/Apptainer or Conda when selected.

## Install Poetry and the launcher

```bash
# Set the standard repository path and install the locked launcher environment.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"
poetry install --no-interaction
poetry run oncotracer --help
```

## Run through Poetry

```bash
# Prepare and run QuickStart Example 1 through Poetry with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker --make_test \
  --test_root "$REPO_DIR/test"
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/poetry-illumina" -resume
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/poetry-ont" -resume
```

Change `--backend docker` to `--backend singularity` on a configured HPC system or to `--backend conda` for native Conda environments. Remaining arguments are forwarded unchanged to `nextflow run main.nf`.
''',
        encoding="utf-8",
    )

    readme = ROOT / "README.md"
    old = '''Run OncoTracer through Nextflow with one execution option:

- `--conda` makes Nextflow create and reuse the required Conda environments automatically from the versioned environment definitions. Install [Miniforge or Conda](https://github.com/conda-forge/miniforge) first; no container runtime is required.
- `--docker` uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).
- `--singularity` uses the same image as `docker://carlosfarkas/oncotracer:latest` on an HPC system configured with Singularity or Apptainer.
'''
    new = '''Run OncoTracer by one of three routes:

1. **Docker or Singularity/Apptainer:** call `nextflow run` with `--docker` or `--singularity`. Docker uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer); Singularity/Apptainer uses the same image as `docker://carlosfarkas/oncotracer:latest`.
2. **Poetry launcher:** run `poetry install`, then use `poetry run oncotracer --backend docker ...`. Poetry manages the Python launcher and forwards the analysis to Nextflow and the selected scientific backend.
3. **Conda:** call `nextflow run` with `--conda`. Nextflow creates and reuses the required native Conda environments automatically from the versioned definitions.
'''
    replace_once(readme, old, new)
    text = readme.read_text(encoding="utf-8")
    req = "Choose one execution environment: [Miniforge/Conda](https://github.com/conda-forge/miniforge), [Docker Engine](https://docs.docker.com/engine/install/), or [SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html)/[Apptainer](https://apptainer.org/docs/admin/main/installation.html)."
    req_new = "Choose a direct execution environment: [Miniforge/Conda](https://github.com/conda-forge/miniforge), [Docker Engine](https://docs.docker.com/engine/install/), or [SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html)/[Apptainer](https://apptainer.org/docs/admin/main/installation.html). Install [Poetry](https://python-poetry.org/docs/#installation) for the Poetry launcher route."
    if req not in text:
        raise SystemExit("README requirements sentence not found")
    text = text.replace(req, req_new, 1)
    section = '''## Poetry launcher

```bash
# Install the locked Poetry launcher in the repository clone.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"
poetry install --no-interaction

# Launch OncoTracer through Poetry with Docker as the scientific backend.
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file /path/to/my/directory/my_oncotracer_project/config/illumina.auto.yml \
  -work-dir /path/to/my/directory/my_oncotracer_project/work \
  -resume
```

The Poetry launcher also accepts `--backend singularity` and `--backend conda`. See the [Poetry Launcher guide](https://cfarkas.github.io/oncotracer/poetry/).

'''
    marker = "## Run your own FASTQs\n"
    if marker not in text:
        raise SystemExit("README run marker not found")
    readme.write_text(text.replace(marker, section + marker, 1), encoding="utf-8")

    index = ROOT / "docs/index.md"
    old_index = '''Run OncoTracer through Nextflow with one execution option:

- `--conda` makes Nextflow create and reuse the required Conda environments automatically from the versioned environment definitions. Install [Miniforge or Conda](https://github.com/conda-forge/miniforge) first.
- `--docker` uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).
- `--singularity` uses the same image as `docker://carlosfarkas/oncotracer:latest` on a configured HPC system.
'''
    new_index = '''Choose one of three execution routes:

1. Run Nextflow directly with `--docker` or `--singularity`.
2. Use the [Poetry launcher](poetry.md), which forwards commands to Nextflow and uses Docker by default.
3. Run Nextflow directly with `--conda`; Nextflow creates and reuses the native environments automatically.
'''
    replace_once(index, old_index, new_index)

    additions = {
        "docs/installation.md": '''\n## Poetry launcher installation\n\n```bash\n# Install Poetry, enter the standard repository clone, and create its locked launcher environment.\nREPO_DIR=/path/to/my/directory/oncotracer\ncd "$REPO_DIR"\npoetry install --no-interaction\npoetry run oncotracer --help\n```\n\nPoetry manages the Python launcher. Select `--backend docker`, `--backend singularity`, or `--backend conda` for the scientific runtime.\n''',
        "docs/containers.md": '''\n## Poetry as a launcher\n\n```bash\n# Run a configuration through the Poetry launcher and Docker backend.\nREPO_DIR=/path/to/my/directory/oncotracer\npoetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \\\n  -params-file /path/to/my/directory/my_oncotracer_project/config/illumina.auto.yml \\\n  -work-dir /path/to/my/directory/my_oncotracer_project/work -resume\n```\n\nPoetry isolates the Python launcher; Docker, Singularity/Apptainer, or Conda supplies the scientific programs selected by `--backend`.\n''',
        "docs/running.md": '''\n## Launch through Poetry\n\n```bash\n# Forward an existing run configuration through Poetry to Nextflow.\nREPO_DIR=/path/to/my/directory/oncotracer\npoetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \\\n  -params-file /path/to/my/directory/my_oncotracer_project/config/illumina.auto.yml \\\n  -work-dir /path/to/my/directory/my_oncotracer_project/work -resume\n```\n''',
        "docs/quick_start.md": '''\n## Poetry alternative\n\n```bash\n# Install the launcher and run the same QuickStart configurations with Docker.\nREPO_DIR=/path/to/my/directory/oncotracer\ncd "$REPO_DIR"\npoetry install --no-interaction\npoetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \\\n  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \\\n  -work-dir "$REPO_DIR/test/work/poetry-illumina" -resume\npoetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \\\n  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \\\n  -work-dir "$REPO_DIR/test/work/poetry-ont" -resume\n```\n''',
        "docs/public_cohort.md": '''\n## Poetry alternative\n\n```bash\n# Run the generated HCC1143 configuration through Poetry and Docker.\nREPO_DIR=/path/to/my/directory/oncotracer\npoetry install --no-interaction\npoetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \\\n  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \\\n  -work-dir "$REPO_DIR/test/work/poetry-hcc1143" -resume\n```\n''',
    }
    for relative, addition in additions.items():
        target = ROOT / relative
        text = target.read_text(encoding="utf-8")
        heading = addition.splitlines()[1]
        if heading not in text:
            target.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")

    mkdocs = ROOT / "mkdocs.yml"
    text = mkdocs.read_text(encoding="utf-8")
    nav = "      - Poetry Launcher: poetry.md\n"
    marker = "      - 1. Install Requirements: installation.md\n"
    if nav not in text:
        if marker not in text:
            raise SystemExit("mkdocs installation nav marker not found")
        text = text.replace(marker, marker + nav, 1)
    mkdocs.write_text(text, encoding="utf-8")

    tests = ROOT / "tests/test_docs_style.py"
    text = tests.read_text(encoding="utf-8")
    text = text.replace(
        '        "--conda",\n        "create and reuse the required Conda environments automatically",',
        '        "--conda",\n        "Poetry launcher",\n        "poetry run oncotracer",\n        "create and reuse the required Conda environments automatically",',
        1,
    )
    text = text.replace(
        '    "docs/index.md": (\n        "--conda",',
        '    "docs/index.md": (\n        "Poetry launcher",\n        "--conda",',
        1,
    )
    text = text.replace(
        '    "mkdocs.yml": (\n        "Other Example Runs:",',
        '    "mkdocs.yml": (\n        "Poetry Launcher: poetry.md",\n        "Other Example Runs:",',
        1,
    )
    text = text.replace(
        'print("PASS: Conda-first documentation, generic paths, mock normal example, downloads, and Bash syntax")',
        'print("PASS: Docker/Singularity, Poetry, and Conda documentation, generic paths, examples, downloads, and Bash syntax")',
        1,
    )
    tests.write_text(text, encoding="utf-8")


def patch_ci() -> None:
    path = ROOT / ".github/workflows/ci.yml"
    text = path.read_text(encoding="utf-8")
    marker = '''          bash tests/test_generate_auto_params.sh
          bash tests/test_illumina_pon_preflight.sh
          bash tests/test_qdnaseq_local_pon.sh
'''
    replacement = marker + '''          bash tests/test_qdnaseq_conda_compat.sh
          python3 tests/test_poetry_cli.py
'''
    if marker not in text:
        raise SystemExit("CI focused source test block not found")
    path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")


def main() -> None:
    patch_illumina()
    patch_ont()
    write_verifiers()
    patch_docs()
    patch_ci()
    print("Applied Conda compatibility, Poetry launcher, verifiers, and documentation patches")


if __name__ == "__main__":
    main()
