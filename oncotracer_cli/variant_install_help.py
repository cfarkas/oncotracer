"""Display-only installation recipes for missing optional variant resources.

Nothing in this module downloads, installs, invokes a shell, or accepts licenses.
The browser renders these strings as copyable Bash snippets and source links.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Iterable

IMAGE = "carlosfarkas/oncotracer:fastq-variants-20260922"
SPEC_REVISION = "f378d5e28f629ab88be803460a44453df690cf69"
FFPERASE_REVISION = "b0dd56cbd0a939896a966b9ce30c4d719b158170"
FFPERASE_MODEL_REVISION = "dc4a9ab71bde34d084c4cc91d0ec291dc1f04258"
DOCS = "https://cfarkas.github.io/oncotracer/"
CLAIR3 = "https://github.com/HKU-BAL/Clair3/tree/v1.2.0"
CLAIRSTO = "https://github.com/HKU-BAL/ClairS-TO/tree/v0.4.4"
STRELKA2 = "https://github.com/Illumina/strelka/blob/v2.9.10/docs/userGuide/README.md"
FFPERASE = "https://github.com/papaemmelab/nf-ffperase"
MODELS = "https://huggingface.co/papaemmelab/ffperase"
ANNOVAR = "https://annovar.openbioinformatics.org/en/latest/user-guide/"
TOOLS = '"$HOME/.local/share/oncotracer/optional-tools"'


def _guide(identifier: str, title: str, reason: str, commands: str, *links: tuple[str, str]) -> dict:
    return {"id": identifier, "title": title, "reason": reason,
            "commands": commands.strip(),
            "links": [{"label": label, "url": url} for label, url in links]}


def _environment(name: str, root: str | Path | None) -> str:
    """Use a materialized specification, otherwise download a pinned public copy."""
    relative = f"environments/native-{name}.yml"
    local = Path(root) / relative if root is not None else None
    lines = [f"ONCOTRACER_OPTIONAL_TOOLS={TOOLS}",
             f'ONCOTRACER_ENV="$ONCOTRACER_OPTIONAL_TOOLS/{name}"',
             'if [ -e "$ONCOTRACER_ENV" ]; then',
             '  printf \'Existing folder left unchanged: %s\\n\' "$ONCOTRACER_ENV"',
             "else"]
    if local is not None and local.is_file():
        lines.append(f"  conda env create --prefix \"$ONCOTRACER_ENV\" --file {shlex.quote(str(local.resolve()))}")
    elif name == "strelka2":
        # This optional specification is newer than SPEC_REVISION. Use the
        # explicit tested packages until a published specification is pinned.
        lines.append('  conda create --yes --prefix "$ONCOTRACER_ENV" --channel conda-forge --channel bioconda "python=2.7.15=h5a48372_1011_cpython" "strelka=2.9.10=h9ee0642_1"')
    else:
        url = f"https://raw.githubusercontent.com/cfarkas/oncotracer/{SPEC_REVISION}/{relative}"
        lines += ['  ONCOTRACER_SPEC_DIR="$(mktemp -d)"',
                  f'  curl --fail --location --output "$ONCOTRACER_SPEC_DIR/native-{name}.yml" \\',
                  f"    {shlex.quote(url)} && \\",
                  f'  conda env create --prefix "$ONCOTRACER_ENV" --file "$ONCOTRACER_SPEC_DIR/native-{name}.yml"']
    variable = {"variants": "ONCOTRACER_VARIANTS_PREFIX", "ffperase": "ONCOTRACER_FFPERASE_PREFIX",
                "strelka2": "ONCOTRACER_STRELKA_PREFIX"}[name]
    lines += ["fi", f'export {variable}="$ONCOTRACER_ENV"',
              '# Paste this environment path into the corresponding browser field:',
              'printf \'%s\\n\' "$ONCOTRACER_ENV"',
              '# If relying on the exported variable, restart setup from this terminal.']
    return "\n".join(lines)


def installation_guides(missing_ids: Iterable[str], *, backend: str, mode: str,
                        root: str | Path | None = None) -> list[dict]:
    """Return ordered, deduplicated help for known missing-resource identifiers.

    ``root`` is an already materialized OncoTracer checkout/payload, never a
    request to extract one. Unknown identifiers are ignored. Chemistry, models,
    existing paths and a genome build are never selected on the user's behalf.
    """
    if isinstance(missing_ids, str):
        missing_ids = [missing_ids]
    missing = list(dict.fromkeys(str(item) for item in missing_ids))
    docker = backend == "docker"
    native_clair3 = not docker and mode == "ont" and "clair3_caller" in missing
    docker_tools = {"variant_tools", "strelka2_runtime", "ffperase_runtime", "clair3_caller", "clairsto_caller"}
    output: list[dict] = []
    emitted: set[str] = set()
    for requested in missing:
        identifier = "docker_image" if docker and requested in docker_tools else requested
        if native_clair3 and identifier == "variant_tools":
            identifier = "clair3_caller"
        if identifier in emitted:
            continue
        emitted.add(identifier)
        if identifier == "variant_tools":
            guide = _guide(identifier, "Install the optional variant tools",
                "Provides samtools, bcftools, Mutect2, FreeBayes and Varlociraptor in a separate Conda environment. ONT callers require the additional guidance below. Existing folders are left unchanged; choose a new prefix if the old environment is incomplete.",
                _environment("variants", root),
                ("OncoTracer variant environment", DOCS + "variants_reference/#conda-and-docker"))
        elif identifier == "strelka2_runtime":
            guide = _guide(identifier, "Install the Strelka2 Python 2 environment",
                "Provides the tested Linux x86_64 Strelka2 2.9.10 build h9ee0642_1 and Python 2.7.15 in a separate environment for paired-end Illumina reads. Somatic calling requires an explicitly matched normal BAM. The later noarch build hdfd78af_2 crashes before somatic calling starts. Existing folders are left unchanged; choose a new prefix for an incomplete or incompatible environment.",
                _environment("strelka2", root),
                ("Strelka2 v2.9.10 user guide", STRELKA2),
                ("Strelka2 runtime requirements", STRELKA2.replace("README.md", "installation.md")))
        elif identifier == "ffperase_runtime":
            guide = _guide(identifier, "Install the FFPERASE Python environment",
                "This isolated legacy Python environment supplies model dependencies. It does not include FFPERASE source or models. Existing folders are left unchanged.",
                _environment("ffperase", root),
                ("FFPERASE setup", DOCS + "variants_reference/#ffperase-for-illumina-ffpe"))
        elif identifier == "docker_image":
            guide = _guide(identifier, "Prepare the variant-enabled Docker image",
                "The tested image includes native callers and variant/FFPERASE runtimes; host Conda is not needed. Docker must already be installed and accessible. Run can prepare the selected Clair3 model and, after license acknowledgment, missing FFPERASE resources. Licensed ANNOVAR resources must be supplied separately. Change the image field explicitly if needed.",
                f"docker pull {IMAGE}\noncotracer doctor --backend docker --image {IMAGE}",
                ("Docker installation", "https://docs.docker.com/engine/install/"),
                ("OncoTracer Docker setup", DOCS + "installation/#docker"))
        elif identifier == "ffperase_source":
            guide = _guide(identifier, "Obtain the tested FFPERASE source",
                "Review the upstream personal/academic/noncommercial license before obtaining source or models. This clones the tested source revision into a new folder; existing source is never changed. OncoTracer runs its native interfaces.",
                f'''# Review the upstream license before running these commands.
ONCOTRACER_OPTIONAL_TOOLS={TOOLS}
export ONCOTRACER_FFPERASE_ROOT="$ONCOTRACER_OPTIONAL_TOOLS/nf-ffperase"
mkdir -p "$ONCOTRACER_OPTIONAL_TOOLS"
if [ -e "$ONCOTRACER_FFPERASE_ROOT" ]; then
  printf 'Existing source left unchanged: %s\\n' "$ONCOTRACER_FFPERASE_ROOT"
else
  git clone --no-checkout {FFPERASE}.git "$ONCOTRACER_FFPERASE_ROOT" &&
  git -C "$ONCOTRACER_FFPERASE_ROOT" checkout {FFPERASE_REVISION}
fi
printf '%s\\n' "$ONCOTRACER_FFPERASE_ROOT"
# Paste the printed path into FFPERASE source folder.''',
                ("FFPERASE license", FFPERASE + "/blob/" + FFPERASE_REVISION + "/LICENSE"),
                ("Tested source revision", FFPERASE + "/tree/" + FFPERASE_REVISION))
        elif identifier == "ffperase_models":
            guide = _guide(identifier, "Obtain the FFPERASE SNV and indel models",
                "Download both official joblib models only after reviewing their upstream terms. The recipe uses a pinned model revision, retains existing files and downloads to a temporary file before moving a complete result into place. Set the printed folder in the browser.",
                f'''# Review the FFPERASE upstream terms before downloading.
export ONCOTRACER_FFPERASE_MODELS="$HOME/.local/share/oncotracer/optional-tools/ffperase-models"
mkdir -p "$ONCOTRACER_FFPERASE_MODELS"
for ONCOTRACER_KIND in snvs indels; do
  ONCOTRACER_MODEL="$ONCOTRACER_FFPERASE_MODELS/model.$ONCOTRACER_KIND.joblib"
  if [ -e "$ONCOTRACER_MODEL" ]; then
    printf 'Existing model left unchanged: %s\\n' "$ONCOTRACER_MODEL"
  else
    ONCOTRACER_DOWNLOAD="$(mktemp "$ONCOTRACER_FFPERASE_MODELS/.download.XXXXXX")"
    curl --fail --location --output "$ONCOTRACER_DOWNLOAD" \\
      "{MODELS}/resolve/{FFPERASE_MODEL_REVISION}/model.$ONCOTRACER_KIND.joblib" &&
    mv -n "$ONCOTRACER_DOWNLOAD" "$ONCOTRACER_MODEL"
  fi
done
printf '%s\\n' "$ONCOTRACER_FFPERASE_MODELS"''',
                ("Official model files", MODELS + "/tree/" + FFPERASE_MODEL_REVISION),
                ("FFPERASE terms", FFPERASE + "/blob/" + FFPERASE_REVISION + "/LICENSE"))
        elif identifier == "clair3_caller":
            guide = _guide(identifier, "Install Clair3 with shared variant utilities",
                "Creates a separate ONT tool environment including Clair3 1.2.0 with CPU TensorFlow, samtools, bcftools and Varlociraptor. Set this as the variant environment when using Clair3. Choose a compatible TensorFlow model separately; newer Clair3 v2 PyTorch models do not fit this pinned runtime.",
                f'''ONCOTRACER_OPTIONAL_TOOLS={TOOLS}
export ONCOTRACER_VARIANTS_PREFIX="$ONCOTRACER_OPTIONAL_TOOLS/clair3"
if [ -e "$ONCOTRACER_VARIANTS_PREFIX" ]; then
  printf 'Existing environment left unchanged: %s\\n' "$ONCOTRACER_VARIANTS_PREFIX"
else
  conda create --prefix "$ONCOTRACER_VARIANTS_PREFIX" \\
    -c conda-forge -c bioconda --strict-channel-priority \\
    clair3=1.2.0 "tensorflow=2.15.0=cpu*" samtools=1.23.1 bcftools=1.23.1 varlociraptor=8.9.5
fi
printf '%s\\n' "$ONCOTRACER_VARIANTS_PREFIX"
# Paste this path into the variant environment field.
# Restart setup from this terminal if relying on the exported variable.''',
                ("Official Clair3 v1.2 installation", CLAIR3 + "#installation"),
                ("Model compatibility", CLAIR3 + "#pre-trained-models"))
        elif identifier == "clair3_model":
            guide = _guide(identifier, "Select a chemistry-matched Clair3 model",
                "Choose the model using the actual pore, chemistry, basecaller and caller version. The published OncoTracer image uses Clair3 1.2.0 (TensorFlow), not Clair3 v2 PyTorch models. The Automatic option downloads and verifies your selected supported profile when Run starts. This manual route is for an existing compatible model; a Conda installation may include models under PREFIX/bin/models.",
                '''# Obtain the matching model using the official links below.
# Enter its extracted directory, not the archive or the parent of all models.
read -r -p "Absolute path to the matching Clair3 model: " ONCOTRACER_CLAIR3_MODEL
if [ -d "$ONCOTRACER_CLAIR3_MODEL" ]; then
  printf 'Clair3 model folder: %s\\n' "$ONCOTRACER_CLAIR3_MODEL"
fi
# Paste this path into Clair3 model folder, then run Autodetect again.''',
                ("Clair3 v1.2 model choices", CLAIR3 + "#pre-trained-models"),
                ("ONT Rerio models", "https://github.com/nanoporetech/rerio"))
        elif identifier == "clairsto_caller":
            guide = _guide(identifier, "Supply ClairS-TO and its runtime",
                "For Conda/host execution, an existing Apptainer or Singularity installation can obtain the tested ClairS-TO v0.4.4 SIF with its preset models. Shared samtools/bcftools still come from your variant environment. Select the printed SIF in the browser and choose the chemistry-matched preset; alternatively follow upstream native installation.",
                f'''ONCOTRACER_OPTIONAL_TOOLS={TOOLS}
mkdir -p "$ONCOTRACER_OPTIONAL_TOOLS/containers"
ONCOTRACER_CLAIRSTO_SIF="$ONCOTRACER_OPTIONAL_TOOLS/containers/clairs-to_v0.4.4.sif"
ONCOTRACER_GUIDE_RUNTIME="$(command -v apptainer || command -v singularity)"
if [ -z "$ONCOTRACER_GUIDE_RUNTIME" ]; then
  printf 'Install Apptainer/Singularity using the official guide below.\\n'
elif [ -e "$ONCOTRACER_CLAIRSTO_SIF" ]; then
  printf 'Existing image left unchanged: %s\\n' "$ONCOTRACER_CLAIRSTO_SIF"
else
  "$ONCOTRACER_GUIDE_RUNTIME" pull "$ONCOTRACER_CLAIRSTO_SIF" docker://hkubal/clairs-to:v0.4.4
fi
printf '%s\\n' "$ONCOTRACER_CLAIRSTO_SIF"''',
                ("Official ClairS-TO installation", CLAIRSTO + "#installation"),
                ("ClairS-TO presets", CLAIRSTO + "#pre-trained-models"))
        elif identifier == "clairsto_model":
            guide = _guide(identifier, "Choose the matching ClairS-TO preset",
                "A preset must match the sequencing chemistry, basecaller and installed ClairS-TO release. Use the v0.4.4 model table for the published OncoTracer image or the supplied v0.4.4 SIF. Newer upstream presets are not necessarily present. Autodetect never guesses a preset from a filename.",
                '''# Consult the official model table and the sequencing/basecaller records.
# Enter the matching ont_* preset into the ClairS-TO platform field.
# No download is needed for presets already bundled in the tested image.''',
                ("ClairS-TO v0.4.4 model table", CLAIRSTO + "#pre-trained-models"))
        elif identifier == "annovar":
            guide = _guide(identifier, "Set up optional licensed ANNOVAR resources",
                "Obtain ANNOVAR through its registration page and obtain/unpack the matching hg38 or hg19 RefSeq TXT plus Mrna FASTA databases. Optional ClinVar must use that same build. No software, license or database is bundled or downloaded by Autodetect. Follow the official resource instructions, then enter the existing folders below or choose Skip ANNOVAR annotation.",
                '''# Register/download using the official ANNOVAR links below first.
# Paths must not contain spaces or shell metacharacters.
read -r -p "Absolute path to your ANNOVAR installation: " ANNOVAR_HOME
read -r -p "Absolute path to its matching database folder: " ANNOVAR_DB
export ANNOVAR_HOME ANNOVAR_DB
printf 'ANNOVAR installation: %s\\nDatabase folder: %s\\n' "$ANNOVAR_HOME" "$ANNOVAR_DB"
# Paste both paths into the browser fields and rerun Autodetect.
# If using these exports instead, restart setup from this terminal.''',
                ("ANNOVAR registration and downloads", ANNOVAR + "download/"),
                ("Official database setup", ANNOVAR + "startup/"),
                ("OncoTracer ANNOVAR requirements", DOCS + "annovar/"))
        elif identifier == "container_runtime":
            guide = _guide(identifier, "Install the runtime for an existing SIF",
                "The selected SIF needs Apptainer or Singularity on the OncoTracer host. Follow your operating system's official installation steps, then restart the setup server if PATH changed. The Docker FASTQ route uses its own native callers and does not need a nested SIF runtime.",
                'command -v apptainer || command -v singularity\n# If neither is found, follow the official installation guide below.',
                ("Apptainer installation", "https://apptainer.org/docs/admin/main/installation.html"))
        else:
            continue
        output.append(guide)
    return output
