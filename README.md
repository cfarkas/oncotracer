# OncoTracer

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Native CI](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml/badge.svg)](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml)

OncoTracer v2 is a **native, auditable LP-WGS copy-number analysis** application for Illumina and Oxford Nanopore Technologies (ONT) FASTQs. Its normal execution path does not invoke Nextflow. The installed `oncotracer` executable schedules alignment, copy-number calling, BAM-supported boundary refinement, CNA codification, plots, reports, and optional cancer-context interpretation directly.

ONT runs can additionally request an explicit-POD5 methylation branch with `--methylation --sturgeon` for CNS-tumor research or `--methylation --marlin` for leukemia research. The branch always requires `--pod5-dir`, runs before but independently from CNA, and skips classification rather than inventing a result when no usable modified-CpG calls are detected. See the [complete methylation guide](https://cfarkas.github.io/oncotracer/configuration/methylation/).

```text
Illumina FASTQ -> BWA/Picard -> qDNAseq -----------+
                                                    +-> boundary refinement -> CNA tables -> plots/reports
ONT FASTQ ------> minimap2 ---> HMMcopy/ichorCNA --+
                                                    +-> optional native CNA classifier/GISTIC2
```

## Install the verified global executable

```bash
gh release download v2.0.0 \
  --repo cfarkas/oncotracer \
  --dir oncotracer-v2.0.0
cd oncotracer-v2.0.0
sha256sum -c SHA256SUMS
chmod +x oncotracer
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
oncotracer --version
oncotracer provenance --json
```

Prepare exactly one backend:

```bash
oncotracer install --conda
# or: oncotracer install --docker
# or: oncotracer install --singularity
# or, from a source-development checkout: ./oncotracer install --poetry
```

Verify it:

```bash
oncotracer doctor --backend conda
```

## Run the same complete public examples shown in the documentation

Choose an existing analyses directory first. Every `$PWD` path below is created inside that directory.

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 1 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart1"

oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

QuickStart 1 analyzes one public Illumina library and one public ONT library. QuickStart 2 analyzes all three HCC1143 Illumina libraries. Both commands download and validate the public FASTQs, create the YAML files, run the native stages, and verify the required outputs. The [complete documentation](https://cfarkas.github.io/oncotracer/) also presents the same examples step by step: download only, inspect the generated YAML, run each branch separately, resume, verify outputs, and choose Conda, Docker, Singularity/Apptainer, or Poetry.

## Analyze your own FASTQs

```bash
cd /path/to/my/analyses_dir/

oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results"

oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

Set `run_cna_classifier: true` in the YAML to run the complete native cancer-context classifier, optional GISTIC2 recurrence branch, knowledge/pathology concordance, HTML/PDF reports, and clinician summaries.

## Release assurance

Stable v2 releases require native source and copied-executable tests, five managed environment solves, container validation, complete QuickStart 1 parity for Illumina and ONT, complete HCC1143 QuickStart 2 parity, semantic CNA-event and refined-bin concordance, output hashes, and a trace containing no Nextflow command. Each release records the exact commit, deterministic source-tree SHA-256, executable SHA-256, container digest, workflow runs, and artifacts in `release-provenance.json`.

OncoTracer is for research use and is not a standalone diagnostic system.

## Documentation

- [Install requirements](https://cfarkas.github.io/oncotracer/installation/)
- [QuickStart 1 — Illumina + ONT](https://cfarkas.github.io/oncotracer/quick_start/)
- [QuickStart 2 — HCC1143](https://cfarkas.github.io/oncotracer/public_cohort/)
- [Full tutorial — PRJNA754199](https://cfarkas.github.io/oncotracer/full_tutorial/)
- [Run your own FASTQs](https://cfarkas.github.io/oncotracer/auto_params/)
- [Execution environments](https://cfarkas.github.io/oncotracer/containers/)
- [Optional ONT methylation](https://cfarkas.github.io/oncotracer/configuration/methylation/)
- [Output files](https://cfarkas.github.io/oncotracer/outputs/)
- [Troubleshooting](https://cfarkas.github.io/oncotracer/troubleshooting/)
