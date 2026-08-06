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

The release workflow verifies that Native v2 CI and both named parity workflows succeeded as push runs for the same exact current `main` SHA. It then builds the copied standalone executable, builds and pushes the native container, records checksums and image identity, downloads both parity artifacts, and creates `v2.0.0`. A release cannot be created from a stale or partially validated commit.

## Repeat the complete gate on a validation server

Run the auditable driver from a clean checkout in a dedicated tmux session. The shared reference is copied or reflinked into the validation root, so the source cache is never modified:

```bash
tmux new-session -s oncotracer-v2-validation
scripts/validate_v2_release.sh \
  --validation-root /large/storage/oncotracer-v2-validation \
  --threads 16 \
  --shared-reference /large/storage/references/samurai_hg38 \
  --resume
```

The driver is CPU-only (`CUDA_VISIBLE_DEVICES` is empty and `NVIDIA_VISIBLE_DEVICES=void`) and never queries, resets, configures, or loads NVIDIA devices. This keeps release validation isolated from GPU-backed sequencing services.

The driver refuses an empty path, `/`, the repository checkout or any path inside it, a validation/reference path overlap, a dirty checkout, or a non-empty validation directory without its release-driver sentinel and `--resume`. Its content-derived stage signatures include the exact source, command, inputs, tool identity, and explicit Conda specifications; complete output manifests are regenerated and compared before a completed stage is reused. The five-environment probe uses exact prefix executables; in particular, GISTIC derives the one usable `share/mcr-*/v*` runtime exclusively from its exact prefix and must return the real `gp_gistic2_from_seg` usage signature with exit status zero. It downloads the official self-contained Nextflow 26.04.6 distribution and verifies SHA-256 `182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c`; that executable runs only the immutable v1.1 comparator at commit `032c1268fa7fdcadc48087055066d7a9fc59bd89`. Before any baseline starts, the nested SAMURAI v1.4.0 source must resolve to commit `6a901940288b008237703c6b181d447e7dee4fcf`. The copied v2 executable runs every native operation from outside the checkout with Python path injection disabled, and any scientific parity failure stops the script.

The final `bundles/` directory contains separate QuickStart audit archives, a deterministic combined `oncotracer-v2.0.0-parity-audit.tar.gz`, and `SHA256SUMS`. The audits retain input and output manifests, exact Conda specifications, qDNAseq annotation provenance, native traces, frozen-comparator traces/reports, stage logs, source identities, and the stage-ledger snapshot. The driver creates evidence only; it does not merge, tag, or publish a release.

## Frozen v1.1 ichorCNA plotting compatibility

The v2 release parity jobs keep the v1.1 OncoTracer source, SAMURAI source,
Nextflow version, and every runtime container digest pinned. The QuickStart 1
ONT fixture can contain NA read-count bins. ichorCNA 0.5.1 performs two
`quantile(copy, ...)` calls without `na.rm = TRUE` while creating a correction
plot, after its analytical segment outputs have been written.

For the frozen comparator, CI mounts an R startup profile only into the pinned
`ICHORCNA_RUN` container. The profile rewrites exactly those two calls, refuses
an unexpected function shape, and writes an audit marker. Native v2 applies the
same checked compatibility shim and emits `<sample>.ichorcna_plot_compat.tsv`.
The shim does not modify read counting, normalization, HMM fitting, segmentation,
or copy-number calls.

The Illumina parity contracts validate the BAM-stage SAMURAI execution actually
invoked by OncoTracer. QuickStart 1 therefore expects six nested tasks for one
sample, and QuickStart 2 expects fourteen nested tasks for three samples. Only
the five runtime images used by those BAM-stage tasks are pinned and required;
FASTQ QC and alignment occur in OncoTracer before SAMURAI is called.
