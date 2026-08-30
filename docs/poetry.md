# Poetry Launcher

Poetry provides a managed Python launcher for OncoTracer's native v2 engine. It does not replace the scientific software runtime: installation also creates the same five isolated Conda environments used by the global executable.

Poetry is intended for source development. Beginners who only want to run analyses should normally install the verified global executable and choose Conda, Docker, or Singularity/Apptainer.

## Install Poetry and the launcher

Use Poetry 2.0 or newer. OncoTracer rejects Poetry 1.x before it creates a lock,
journal, environment, or target directory.

```bash
cd /path/to/my/oncotracer_source/

./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

ONCOTRACER_DEV=/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer
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

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV=/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

"$ONCOTRACER_DEV" doctor --backend poetry
```

## Run QuickStart 1 through Poetry

Keep the source checkout separate from the analysis output. The output path below is explicit so it does not depend on whichever source directory contains `pyproject.toml`.

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV=/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer
"$ONCOTRACER_DEV" quickstart 1 --test-root /path/to/my/analyses_dir/oncotracer-quickstart1-poetry --download-only
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1-poetry/configs/illumina.quickstart.yml
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1-poetry/configs/ont.quickstart.yml
```

## Run QuickStart 2 through Poetry

```bash
cd /path/to/my/oncotracer_source/

ONCOTRACER_DEV=/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer
"$ONCOTRACER_DEV" quickstart 2 --test-root /path/to/my/analyses_dir/oncotracer-quickstart2-poetry --download-only
"$ONCOTRACER_DEV" run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart2-poetry/configs/hcc1143_lpwgs/illumina.auto.yml
```

For a different managed runtime, install it explicitly with `oncotracer install --docker`, `--singularity`, or `--conda`, then select the matching `--backend`. Every v2 route executes the native stage graph and records `nextflow_used=false`.
