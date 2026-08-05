# Migrating from v1.1

The scientific inputs and principal output folders remain compatible. The launch command changes.

## Analysis

```bash
# v1.1
nextflow run main.nf --conda -params-file run.yml -resume

# v2
oncotracer run --backend conda --config run.yml
```

For transition scripts, v2 also recognizes the old `--conda -params-file run.yml -resume` argument shape and translates it to the native runner. It never forwards that command to Nextflow.

## Automatic Setup

```bash
oncotracer auto --mode illumina \
  --reads-folder reads \
  --sample-table samples.csv \
  --config-dir config \
  --outdir results
```

## Legacy reproducibility

The immutable `v1.1` tag remains available for reproducing historical analyses. Its Nextflow implementation is not bundled in the v2 global executable or container. Stable-v2 parity workflows check out `v1.1` independently and preserve the resulting comparator records.

## Preserve exact identity

Archive `oncotracer provenance --json`, the run's `native_run_manifest.json`, input/reference checksums, and `release-provenance.json` with migrated analyses. The source SHA-256 is the deterministic SHA-256 of `git -c tar.umask=0002 archive --format=tar <exact-commit>`; it is distinct from the copied-executable checksum.
