# Poetry Launcher

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
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker --make_test   --test_root "$REPO_DIR/test"
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker   -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml"   -work-dir "$REPO_DIR/test/work/poetry-illumina" -resume
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker   -params-file "$REPO_DIR/test/configs/ont.quickstart.yml"   -work-dir "$REPO_DIR/test/work/poetry-ont" -resume
```

Change `--backend docker` to `--backend singularity` on a configured HPC system or to `--backend conda` for native Conda environments. Remaining arguments are forwarded unchanged to `nextflow run main.nf`.
