# Troubleshooting

## Check the selected backend

```bash
oncotracer doctor
```

The JSON result records the executable version and source identity, backend, all five configured prefixes or image, semantic command/package checks, and whether Nextflow is required (`false`). A failed check makes the command nonzero.

## Find the failing native command

Open:

```text
<outdir>/.oncotracer-native/trace.tsv
```

Every row contains the stage, start/end times, exit code, working directory, and shell-escaped rendering of the original argument array.

## Resume safely

Repeat the same run command. Do not delete `.oncotracer-native/state.json`, reference indexes, or work outputs while an analysis is active. Use `--force` only to invalidate reusable analysis stages deliberately.

## Conda solver problems

The five environment definitions are independent. Rerun the fail-closed installer when a prefix is incomplete:

```bash
oncotracer install --conda --force
```

Do not set `R_HOME`, `R_LIBS`, `R_LIBS_USER`, or `R_LIBS_SITE` to another R installation. Native qDNAseq and ichorCNA stages invoke the exact `Rscript` in their own prefix with those ambient variables removed. Diagnose an installation with `oncotracer doctor --backend conda`; do not substitute a login-shell `command -v` check.

## Confirm release identity

```bash
oncotracer provenance --json
```

For a stable asset, `source_commit` and `source_sha256` must match `release-provenance.json` from the same release.

## Docker permissions

`docker info` must succeed for the invoking user. Docker daemon access is privileged; follow local policy rather than using undocumented `sudo` workarounds.
