# Execution backends

All backends use the same native stage graph and output contract.

| Backend | Installation | Scientific execution |
| --- | --- | --- |
| Conda | `oncotracer install --conda` | Five isolated versioned prefixes |
| Docker | `oncotracer install --docker` | Native v2 image from GHCR |
| Singularity/Apptainer | `oncotracer install --singularity` | The same native image as a SIF |
| Poetry | `oncotracer install --poetry` | Development launcher; scientific tools remain explicit |

## Container mounts

The CLI reads the flat YAML, identifies the config, input, `lpwgs_root`, and output parents, and mounts the minimal distinct roots at identical absolute paths. Patient-data policies and institutional container restrictions still apply.

## Image identity

Stable images use the immutable release tag:

```text
ghcr.io/cfarkas/oncotracer:2.0.0
```

Release records contain the source commit, binary checksum, container digest, and the exact successful native-CI and two parity workflow run identifiers.

The five Conda groups are `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`. `oncotracer doctor --backend conda` checks their exact prefixes and performs semantic tool/package probes rather than relying on a login shell's `PATH`.

For direct Compose use, mount a project directory at `/project`:

```bash
ONCOTRACER_PROJECT_DIR="$PWD/project" docker compose run --rm oncotracer --version
```

The ordinary `oncotracer run --backend docker` route mounts the absolute paths referenced by the YAML automatically and is preferred for analyses.
