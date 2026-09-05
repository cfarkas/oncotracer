# Prebuilt hg38 indexes

A prebuilt index avoids the one-time genome-index construction step. It does
**not** remove RAM needed for alignment, sorting, CNA analysis or optional models.
Start with `oncotracer system --path /absolute/path/project` to see planning limits.

These commands require the updated source, not the existing v2.0.0 executable.

## Install a bundle from another computer

Copy the complete bundle directory — `hg38-reference.json` and its `.part` files —
to your computer. Then run:

```bash
oncotracer reference install \
  --manifest /absolute/path/hg38-bundle/hg38-reference.json \
  --lpwgs-root /absolute/path/shared-reference \
  --mode ont --dry-run
```

Remove `--dry-run` to install. The command uses a 1-MiB transfer buffer, verifies
each chunk and complete file, and publishes the directory only after all checks
pass. No index is rebuilt or loaded into RAM during import.

| Flag | Meaning |
| --- | --- |
| `--manifest` | Bundle's small JSON inventory, not the FASTA or reads |
| `--lpwgs-root` | Parent where reusable references will live |
| `--mode ont` | Install hg38 and the minimap2 index |
| `--mode illumina` | Install hg38 and BWA indexes |
| `--mode both` | Install both index sets |
| `--dry-run` | Show required space and destination without transferring genome files |

Set the same parent in every project YAML that should reuse this reference:

```yaml
lpwgs_root: /absolute/path/shared-reference
```

The installed files are under `shared-reference/references/samurai_hg38/`.
Existing reference directories are never overwritten or silently adopted. If you
need both platforms, select `--mode both` at the first install; otherwise use a new
parent for a different bundle. Keep the downloaded parts until installation succeeds.

## Published bundles

The installer also accepts an HTTPS manifest. Supply its SHA-256 from the trusted
publisher using `--sha256`; the installer will not trust a remote manifest without
it. Only the selected platform's chunks are downloaded. An existing third-party
folder labeled “hg38” is not enough: genome sequence, contig names, index settings,
and recorded tool identities must agree with OncoTracer.

The source currently provides export/import machinery; a public reference-release
URL is documented here only after its assets are published and verified.

## Prepare a bundle on a larger computer

Use an **already validated** reference with both BWA and minimap2 indexes:

```bash
oncotracer reference export \
  --reference /absolute/path/reference/references/samurai_hg38 \
  --core-prefix /absolute/path/envs/core \
  --output /absolute/path/new-hg38-bundle
```

`--core-prefix` contains the exact BWA/minimap2 tools that built the indexes.
Omit it only when those tools are on `PATH`. Export checks the pinned genome,
file hashes, tool identities and index readability. It writes chunks smaller than
2 GiB plus the manifest; the output directory must not exist.

On the receiving machine, normal analysis verifies the installed indexing-tool
identities before using the reference. If they differ, it stops instead of
rebuilding the external index or assuming compatibility. Use the matching tool
builds or ask the bundle provider for a compatible bundle.
