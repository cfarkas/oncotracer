# Prebuilt hg38 indexes

A prebuilt index avoids the one-time genome-index construction step. It does
**not** remove RAM needed for alignment, sorting, CNA analysis or optional models.
Start with `oncotracer system --path /absolute/path/project` to see planning limits.

These commands require the [updated source](installation.md#current-source-for-the-new-setup-workflow), not the existing v2.0.0 executable.

## Download the ready-made indexes

The [hg38 reference release](https://github.com/cfarkas/oncotracer/releases/tag/hg38-reference-v1)
contains the public genome and indexes, not patient data or classifier models.
Choose a new reference parent and preview the installation:

```bash
oncotracer reference install \
  --manifest https://github.com/cfarkas/oncotracer/releases/download/hg38-reference-v1/hg38-reference.json \
  --sha256 1c704b1522fe37c13bf4272858792d03e827bc476fdb6712fa7f760f3c944632 \
  --lpwgs-root /absolute/path/shared-reference \
  --mode ont --dry-run
```

Replace `/absolute/path/shared-reference` with your chosen folder. Remove
`--dry-run` to install; there is no need to download individual parts manually.

| Flag | Meaning |
| --- | --- |
| `--manifest` | Small JSON inventory URL, not the FASTA or reads |
| `--sha256` | Trusted checksum that verifies the inventory |
| `--lpwgs-root` | Parent where reusable references will live |
| `--mode ont` | Genome and minimap2 index: 9.7 GiB |
| `--mode illumina` | Genome and BWA indexes: 8.0 GiB |
| `--mode both` | Genome and both index sets: 14.8 GiB |
| `--dry-run` | Show required space and destination without transferring genome files |

Allow 1 GiB of free disk headroom beyond the listed size. Set the same parent in
every project YAML that should reuse this reference:

```yaml
lpwgs_root: /absolute/path/shared-reference
```

The installed files are under `shared-reference/references/samurai_hg38/`.
Existing reference directories are never overwritten or silently adopted. If you
need both platforms, select `--mode both` at the first install; otherwise use a new
parent for a different bundle. Interrupted imports can be restarted, but partial
downloads are not resumed.

## RAM and compatibility

Import uses a 1-MiB transfer buffer and checks every chunk and complete file before
making the reference available. No index is built or loaded into RAM during import.
Alignment still needs more memory; this is not a way to run the whole analysis
with 1 GiB RAM.

This bundle requires Linux x86-64 Bioconda BWA `0.7.19=h577a1d6_1` and minimap2
`2.30=h577a1d6_0` for their respective platforms. Normal analysis verifies tool
identities before using the indexes. A different build stops with an error rather
than rebuilding the shared reference. Use a compatible bundle; do not manually
replace tools inside a managed OncoTracer installation.

## Copy a bundle between computers

Copy `hg38-reference.json` and all its `.part` files into one folder. Use that local
JSON path for `--manifest`, keeping the other flags above. Installation then uses
the copied files without an internet connection. Keep them until import succeeds.

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

An arbitrary folder labeled “hg38” is not a validated bundle: genome sequence,
contig names, index settings and tool identities must agree with OncoTracer.
