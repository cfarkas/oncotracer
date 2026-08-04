# Parity and stable-release gate

A v2 tag is not created from file-existence smoke tests. Two full public-data workflows run both the frozen v1.1 implementation and the candidate native v2 implementation from the same inputs and shared reference cache.

## QuickStart 1 gate

The gate completes both the Illumina ERR12341627 and ONT DRR165691 analyses.

## QuickStart 2 gate

The gate completes all three HCC1143 libraries from six checksum-validated FASTQs.

## Semantic criteria

For each branch of each quickstart, the audit program requires:

- all documented output files are non-empty and hashed;
- identical analysis mode, dataset name, and sample set;
- native workflow summary declares `engine=native` and `nextflow_used=false`;
- a non-empty native argument-array trace with no Nextflow command;
- CNA events matched by sample, state, chromosome, and at least 0.80 reciprocal interval overlap;
- event recall and precision of at least 0.90;
- at least 0.95 of each refined-bin grid shared exactly;
- refined-bin Pearson correlation of at least 0.98;
- median absolute refined-bin log₂ difference no greater than 0.08.

Each artifact includes `parity_report.json`, `parity_report.md`, `event_matches.tsv`, the native trace, run summaries, and `SHA256SUMS`.

## Release automation

The release workflow verifies that both named parity workflows succeeded for the exact current `main` SHA. It then builds the copied standalone executable, builds and pushes the native container, records checksums and image identity, downloads both parity artifacts, and creates `v2.0.0`. A release cannot be created from a stale or partially validated commit.
