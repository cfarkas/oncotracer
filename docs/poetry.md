# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer's native v2 engine. It does not replace the scientific software runtime: installation also creates the same five isolated Conda environments used by the global executable.

Poetry is intended for source development. Beginners who only want to run analyses should normally install the verified global executable and choose Conda, Docker, or Singularity/Apptainer.

## Install Poetry and the launcher

```bash
cd /path/to/my/oncotracer_source/

poetry install --no-interaction
poetry run oncotracer --help
```

## Prepare the Poetry backend

```bash
cd /path/to/my/oncotracer_source/

./oncotracer install --poetry
poetry run oncotracer doctor --backend poetry
```

## Run QuickStart 1 through Poetry

Keep the source checkout separate from the analysis output. The output path below is explicit so it does not depend on whichever source directory contains `pyproject.toml`.

```bash
cd /path/to/my/oncotracer_source/

poetry run oncotracer quickstart 1 \
  --backend poetry \
  --test-root /path/to/my/analyses_dir/oncotracer-quickstart1-poetry
```

## Run QuickStart 2 through Poetry

```bash
cd /path/to/my/oncotracer_source/

poetry run oncotracer quickstart 2 \
  --backend poetry \
  --test-root /path/to/my/analyses_dir/oncotracer-quickstart2-poetry
```

For a different managed runtime, install it explicitly with `oncotracer install --docker`, `--singularity`, or `--conda`, then select the matching `--backend`. Every v2 route executes the native stage graph and records `nextflow_used=false`.
