# OncoTracer v2.0.0

OncoTracer is a native, auditable workflow for low-pass whole-genome sequencing (LP-WGS) copy-number analysis from Illumina and Oxford Nanopore Technologies (ONT) FASTQs.

For ONT only, an optional explicit-POD5 branch can perform Dorado/Modkit modified-base processing followed by user-selected Sturgeon (CNS research) or MARLIN (leukemia research) classification. It runs before CNA but records and preserves the two outcomes independently.

```text
Illumina FASTQ -> BWA/Picard -> qDNAseq -----------+
                                                    +-> BAM-supported refinement
ONT FASTQ ------> minimap2 ---> HMMcopy/ichorCNA --+              |
                                                                   v
                                                       CNA tables and notation
                                                                   |
                                                                   +-> plots and summaries
                                                                   +-> optional classifier/GISTIC2
```

**Nextflow is not installed or invoked by the v2 analysis path.** The frozen v1.1 Nextflow release remains available only for reproducing historical analyses and for the independent semantic parity comparator used by the v2 release gate.

## Start with a complete public example

QuickStart 1 runs one checksum-validated Illumina library and one checksum-validated ONT library. Replace the example analyses directory with a real directory on your Linux workstation or server:

```bash
cd /path/to/my/analyses_dir/

oncotracer install --conda
oncotracer doctor --backend conda

oncotracer quickstart 1 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart1"
```

QuickStart 2 runs three public HCC1143 Illumina libraries:

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

The QuickStart pages reproduce the detailed style of the original documentation: prepare the backend, download and validate inputs, inspect generated YAML, run each analysis, verify output tables and PDFs, and resume safely.

- [QuickStart 1 — Illumina and ONT](quick_start.md)
- [QuickStart 2 — HCC1143](public_cohort.md)
- [Complete 12-library PRJNA754199 tutorial](full_tutorial.md)
- [Mock six-tumor/four-normal cohort](six_tumor_four_control.md)

## Run your own FASTQs

Automatic Setup is the recommended route:

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results"

oncotracer run --config "$PWD/project/config/illumina.auto.yml" \
  --backend conda
```

Use `--mode ont` for a `fastq_pass` directory organized by barcode. Automatic Setup creates the analysis YAML and, for Illumina, an exact four-column samplesheet. It does not start alignment or CNA calling.

## What v2 records

Every native run writes:

- `.oncotracer-native/trace.tsv`, with the exact argument arrays for executed stages;
- `.oncotracer-native/state.json`, used for content-aware reuse of completed stages;
- `06_workflow_summary/workflow_summary.txt` and JSON;
- `native_run_manifest.json` and output checksums;
- CNA events, cytogenomic notation, refined bins, plots, and optional classifier reports.

The stable release also ships `release-provenance.json`, containing the exact Git commit, deterministic source-tree SHA-256, copied-executable SHA-256, container digest, and successful QuickStart workflow/artifact identities.

## Choose a backend

| Backend | Installation | Typical use |
| --- | --- | --- |
| Conda | `oncotracer install --conda` | Native workstation or server execution |
| Docker | `oncotracer install --docker` | Reproducible container execution |
| Singularity/Apptainer | `oncotracer install --singularity` | HPC systems |
| Poetry | `./oncotracer install --poetry` from a source checkout | Launcher development with the same Conda scientific environments |

All backends use the same native stage graph and output contract.

## Continue

1. [Install the verified executable and one backend](installation.md).
2. [Run QuickStart 1](quick_start.md).
3. [Generate a YAML for your FASTQs](auto_params.md).
4. [Understand inputs, settings, and outputs](inputs.md).
5. [Review the native architecture and release parity evidence](native_architecture.md).
