# Native architecture

OncoTracer v2 is an orchestration application, not a reimplementation of BWA, samtools, Picard, qDNAseq, HMMcopy, ichorCNA, or GISTIC2.

## Stage graph

Each native stage has explicit inputs, outputs, argument-array command, tool environment, validation, trace record, and resume signature. The Python engine coordinates:

- BWA and Picard for Illumina;
- minimap2 for ONT;
- qDNAseq in its pinned R 4.1 environment;
- HMMcopy and ichorCNA 0.5.1 in their pinned R 4.4 environment;
- the existing BAM boundary-refinement, CNA notation, and plotting implementations;
- the optional CNA classifier, GISTIC2, knowledge enrichment, pathology concordance, HTML/PDF reports, and clinician summaries.

The optional classifier is a direct native stage graph over its versioned Python scripts and assets. No `.nf` file is present in the release executable or container.

## Single-file executable

`scripts/build_native_binary.py` creates a compressed Python zipapp containing the CLI and the complete versioned payload (`bin`, examples, parameter templates, and environment definitions). At first use, payload files are extracted atomically into a versioned user cache and verified against the executable SHA-256.

The release file can therefore be installed directly:

```bash
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
```

## Native invariant

Normal v2 execution does not invoke Nextflow. The managed Conda and container backends include the Java runtime used internally by Picard for Illumina duplicate marking; users do not need a separate host Java installation. CI parses the source, inspects the built image, executes an offline classifier fixture, and rejects any native command trace containing `nextflow`. The frozen v1.1 workflow is retained only in a separate checkout used to establish parity.
