# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer's native v2 engine. It does not replace the scientific software runtime: installation also creates the same five isolated Conda environments used by the global executable.

Poetry is intended for source development. For analyses, follow [installation](installation.md) and choose Conda, Docker, or Singularity/Apptainer.

## Install Poetry and the launcher

Use Poetry 2.0 or newer. OncoTracer rejects Poetry 1.x before it creates a lock,
journal, environment, or target directory.

```bash
cd /path/to/my/oncotracer_source/

./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
"$ONCOTRACER_DEV" --help
```

The explicit prefix is a dedicated OncoTracer installation root, not a Conda
base installation or a shared Poetry environment. It must be absent, empty, or
already carry the exact ownership markers written by this installer. OncoTracer
requires an exact clean Git checkout matching the launcher's source identity.
It builds a wheel in an isolated transaction tree, creates the final canonical
`poetry-runtime` path, and installs the wheel only through that target's Python
and pip with indexes and dependency resolution disabled. An ownership-checked
rollback transaction preserves the verified prior runtime until the replacement
passes provenance, executable, and exact-inventory checks. It does not alter Poetry's global environment, ambient Python site-packages, the checkout, or a
checkout-local `.venv`.

## Prepare the Poetry backend

The install command above already prepares the launcher and scientific tools.
If it succeeded, run only the `doctor` line below. The repeated install command
is for returning to an unfinished installation at the same prefix.

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

"$ONCOTRACER_DEV" doctor --backend poetry
```

## Run QuickStart 1 through Poetry

Download and checksum-verify the three read files in
[QuickStart 1, step 1](quick_start.md#1-download-and-verify-the-reads). Keep the
source checkout separate from analysis output. For new projects, these terminal
commands create the same Illumina and ONT configurations without browser setup.
Replace both directory placeholders with your actual paths:

```bash
ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
ONCOTRACER_ANALYSES="/path/to/my/analyses_dir"

"$ONCOTRACER_DEV" setup --non-interactive \
  --project "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/illumina" \
  --mode illumina --analysis cna --backend poetry --threads 4 \
  --sample-name ERR12341627 --status tumor \
  --fastq-1 "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz" \
  --fastq-2 "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz" \
  --hg38_build
"$ONCOTRACER_DEV" check \
  --config "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/illumina/config/run.yml"

"$ONCOTRACER_DEV" setup --non-interactive \
  --project "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/ont" \
  --mode ont --analysis cna --backend poetry --threads 4 \
  --reads-folder "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/input/fastq_pass" \
  --barcodes barcode01 --sample-names DRR165691 --hg38_build
"$ONCOTRACER_DEV" check \
  --config "$ONCOTRACER_ANALYSES/oncotracer-quickstart1/ont/config/run.yml"
```

If these projects are already configured, skip setup and check their existing
configuration files. Run them with the installed Poetry launcher:

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1/illumina/config/run.yml
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1/ont/config/run.yml
```

## Run QuickStart 2 through Poetry

Download and checksum-verify all six FASTQs in
[QuickStart 2, step 1](public_cohort.md#1-download-and-verify-the-reads). Then
create the sample table and configuration entirely in the terminal. All three
libraries are tumor samples; DMSO is a treatment control. Replace both directory
placeholders. The `cat` block writes the samplesheet and replaces that CSV if it
already exists; include the final `CSV` line:

```bash
ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
ONCOTRACER_ANALYSES="/path/to/my/analyses_dir"

mkdir -p "$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input"
cat > "$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
HCC1143_DMSO,"$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz","$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz",tumor
HCC1143_BEZ235,"$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz","$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz",tumor
HCC1143_TRAMETINIB,"$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz","$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz",tumor
CSV

"$ONCOTRACER_DEV" setup --non-interactive \
  --project "$ONCOTRACER_ANALYSES/oncotracer-quickstart2/analysis" \
  --mode illumina --analysis cna --backend poetry --threads 4 \
  --samplesheet "$ONCOTRACER_ANALYSES/oncotracer-quickstart2/input/samplesheet.csv" \
  --hg38_build
"$ONCOTRACER_DEV" check \
  --config "$ONCOTRACER_ANALYSES/oncotracer-quickstart2/analysis/config/run.yml"
```

Skip table generation and setup if this project is already configured. After
checking that all three libraries are selected, run:

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV="/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer"
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart2/analysis/config/run.yml
```

For a different managed runtime, install it explicitly with `oncotracer install --docker`, `--singularity`, or `--conda`, then select the matching `--backend`. Every v2 route executes the native stage graph and records `nextflow_used=false`.
