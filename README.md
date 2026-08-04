# OncoTracer

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Native CI](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml/badge.svg)](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml)

OncoTracer v2 is a **native, auditable LP-WGS copy-number analysis application** for Illumina and Oxford Nanopore FASTQs. Its normal execution path does not invoke Nextflow. Conda and container backends manage all scientific dependencies, including the Java runtime used internally by Picard for Illumina duplicate marking, so no separate host Java installation is required.

```text
FASTQ -> alignment -> qDNAseq/ichorCNA -> BAM refinement -> CNA tables -> plots
```

## Install the global executable

Download the `oncotracer` asset from the latest stable release, then install it like a Kent utility:

```bash
chmod +x oncotracer
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
oncotracer --version
```

Prepare exactly one backend:

```bash
oncotracer install --conda
# or: oncotracer install --docker
# or: oncotracer install --singularity
# or: oncotracer install --poetry
```

Run a generated YAML:

```bash
oncotracer run --backend conda --config project/config/illumina.auto.yml
```

Run the complete public examples:

```bash
oncotracer quickstart 1 --backend conda --test-root "$PWD/quickstart1"
oncotracer quickstart 2 --backend conda --test-root "$PWD/quickstart2"
```

The [complete documentation](https://cfarkas.github.io/oncotracer/) contains installation, Automatic Setup, complete QuickStarts, configuration, output interpretation, provenance, v1.1 migration, and the v2 release parity reports.

## Release assurance

Stable v2 releases are generated only after current `main` passes:

- complete QuickStart 1 parity for both Illumina and ONT against the pinned v1.1 workflow;
- complete three-library HCC1143 QuickStart 2 parity;
- exact sample-set checks, CNA event precision/recall, refined-bin profile concordance, output hashes, and a native trace containing no Nextflow command;
- copied single-file executable testing outside the repository checkout.

OncoTracer is for research use and is not a standalone diagnostic system.
