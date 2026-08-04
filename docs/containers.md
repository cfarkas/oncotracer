# Execution backends

All backends use the same native stage graph and output contract.

| Backend | Installation | Scientific execution |
| --- | --- | --- |
| Conda | `oncotracer install --conda` | Three isolated versioned prefixes |
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

Release records contain the source commit, binary checksum, container digest, and the two successful parity workflow run identifiers.
