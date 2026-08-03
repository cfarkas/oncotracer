# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer. It does not replace the scientific software runtime: the launcher calls the versioned Nextflow workflow with Docker by default, or with Singularity/Apptainer or Conda when selected.

## Install Poetry and the launcher

```bash
# Run this command from the oncotracer directory.
poetry install --no-interaction
poetry run oncotracer --help
```

## Run through Poetry

```bash
# Prepare and run QuickStart Example 1 through Poetry with Docker.
poetry run oncotracer --repo-dir . --backend docker --make_test   --test_root "test"
poetry run oncotracer --repo-dir . --backend docker   -params-file "test/configs/illumina.quickstart.yml"   -work-dir "test/work/poetry-illumina" -resume
poetry run oncotracer --repo-dir . --backend docker   -params-file "test/configs/ont.quickstart.yml"   -work-dir "test/work/poetry-ont" -resume
```

Change `--backend docker` to `--backend singularity` on a configured HPC system or to `--backend conda` for native Conda environments. Remaining arguments are forwarded unchanged to `nextflow run main.nf`.
