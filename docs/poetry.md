# Poetry launcher

The Poetry route is intended for development of the Python launcher while retaining the same native scientific stage graph used by the copied release executable.

For normal users, install the verified release asset and choose Conda, Docker, or Singularity/Apptainer. See [Installation](installation.md).

## Prepare a source-development checkout

From an existing source checkout:

```bash
./oncotracer install --poetry
poetry run oncotracer --version
poetry run oncotracer provenance --json
poetry run oncotracer doctor --backend poetry
```

The installer performs two jobs:

1. `poetry install --no-interaction` creates the locked Python launcher environment.
2. The five scientific Conda prefixes are created for core tools, qDNAseq, ichorCNA/HMMcopy, classifier/reporting, and GISTIC2.

The saved backend is `poetry`, but scientific stages execute through those exact Conda prefixes rather than through the Poetry virtual environment.

## Run a YAML

```bash
poetry run oncotracer run \
  --backend poetry \
  --config "$PWD/project/config/illumina.auto.yml"
```

## Run the public examples

```bash
poetry run oncotracer quickstart 1 \
  --backend poetry \
  --test-root "$PWD/oncotracer-quickstart1-poetry"

poetry run oncotracer quickstart 2 \
  --backend poetry \
  --test-root "$PWD/oncotracer-quickstart2-poetry"
```

## Rebuild after source changes

```bash
poetry install --no-interaction
./oncotracer install --poetry --force
poetry run oncotracer doctor --backend poetry
```

Use `--force` only when the environment definitions changed or a prefix is known to be damaged.

## Boundary between Poetry and the scientific runtime

Poetry manages the launcher and Python development dependencies. It does not replace the versioned R stacks, alignment programs, Picard, HMMcopy, ichorCNA, or GISTIC2. The run trace and result contract are identical to the other v2 backends.
