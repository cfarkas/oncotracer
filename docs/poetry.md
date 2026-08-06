# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer's native v2 engine. It does not replace the scientific software runtime: installation also creates the same five isolated Conda environments used by the global executable.

## Install Poetry and the launcher

```bash
# Run this command from the oncotracer directory.
poetry install --no-interaction
poetry run oncotracer --help
```

## Run through Poetry

```bash
# Install the launcher and its five scientific environments.
poetry run oncotracer install --poetry
poetry run oncotracer doctor --backend poetry

# Run the complete native QuickStart 1 through the Poetry launcher.
poetry run oncotracer quickstart 1 \
  --backend poetry \
  --test-root "$PWD/oncotracer-quickstart1"
```

For a different managed runtime, install it explicitly with `oncotracer install --docker`, `--singularity`, or `--conda`, then select the matching `--backend`. Every v2 route executes the native stage graph and records `nextflow_used=false`.
