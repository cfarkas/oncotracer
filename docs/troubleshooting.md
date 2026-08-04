# Troubleshooting

## Check the selected backend

```bash
oncotracer doctor
```

The JSON result records the executable version, backend, configured prefixes or image, command checks, and whether Nextflow is required (`false`).

## Find the failing native command

Open:

```text
<outdir>/.oncotracer-native/trace.tsv
```

Every row contains the stage, start/end times, exit code, working directory, and shell-escaped rendering of the original argument array.

## Resume safely

Repeat the same run command. Do not delete `.oncotracer-native/state.json`, reference indexes, or work outputs while an analysis is active. Use `--force` only to invalidate reusable analysis stages deliberately.

## Conda solver problems

The three environment definitions are independent. Remove the versioned installation directory or rerun:

```bash
oncotracer install --conda --force
```

## Docker permissions

`docker info` must succeed for the invoking user. Docker daemon access is privileged; follow local policy rather than using undocumented `sudo` workarounds.
