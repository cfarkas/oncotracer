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
- event-count recall and precision retained in the report as split/merge diagnostics;
- sample-, chromosome-, and state-specific CNA genomic-coverage recall and precision of at least 0.90;
- at least 0.95 of the original corrected-bin coordinate grid shared exactly;
- corrected input log₂-signal Pearson correlation of at least 0.98;
- median absolute corrected input log₂ difference no greater than 0.08.

Each artifact includes `parity_report.json`, `parity_report.md`, `event_matches.tsv`, the native trace, run summaries, and `SHA256SUMS`.

## Release automation

The release workflow verifies that Native v2 CI and both named parity workflows succeeded as push runs for the same exact current `main` SHA. It then builds the copied standalone executable, builds and pushes the native container, records checksums and image identity, downloads both parity artifacts, and creates `v2.0.0`. A release cannot be created from a stale or partially validated commit.

## Hosted-runner capacity contract

The full parity gates and five-environment container build are intentionally
fail-closed before they download public reads, install scientific environments,
pull pinned images, or publish anything. GitHub's documented standard public
`ubuntu-24.04` runner contract is 4 CPUs, 16 GB RAM, and 14 GB SSD storage.
Observed free space above that contract is incidental runner-image capacity and
must not be made available by deleting runner tools, unrelated images, or global
caches.

The parity preflight requires every checked filesystem independently to meet
the storage floor; capacities are never summed across devices. Its sealed
evidence enumerates every checked path, device, and free-space observation.
It also requires at least 15 GiB physical RAM and at least 47 GiB addressable memory. A runner
below the addressable floor receives an exact 32 GiB run-owned swap file and
must have 72 GiB free. A runner whose physical RAM already satisfies the
47 GiB floor uses no job swap and must have 40 GiB free. This is a phase peak,
not a sum of mutually exclusive Docker and Conda material. Frozen v1.1 runs first;
only after its nested traces are authenticated does the job release exact image
references proven absent before this run. It then creates only `core`, `qdnaseq`,
and (for QuickStart 1) `ichorcna` from the committed definitions, records
explicit exports and executable probes, and deletes only its run-ID-owned Conda
package cache. Native execution uses those exact prefixes with the host backend.

The measured shared reference is 15,852,699,648 bytes (rounded to 16 GiB).
QuickStart 1 pinned image virtual sizes total 14,850,685,496 bytes (14 GiB),
and its frozen outputs and inputs each round to 1 GiB. QuickStart 2 images total
8,188,638,552 bytes (8 GiB), frozen output rounds to 6 GiB, and inputs round to
2 GiB. The measured minimal native prefixes round to 3 GiB for QuickStart 1 and
1 GiB for QuickStart 2. The 8 GiB and 7 GiB native transient allowances bound
the larger mutually exclusive footprint: package solve/cache before its exact
removal, or native-output growth afterwards. With 32 GiB job-owned swap and an
8 GiB reserve, both low-memory frozen and native
phase models peak at 72 GiB; removing the unnecessary swap allocation on an
ample-memory runner makes the maximum 40 GiB. Each boundary immediately
enforces the 8 GiB filesystem reserve and the physical/addressable memory
floors before it records `df`, `du`, memory, swap, and
Docker evidence in the sealed audit. Earlier hosted logs recorded 15.61 GiB
physical RAM and 34 GiB total swap after adding the 32 GiB file, but no peak
swap use, so reducing that allocation is not evidence-supported.

The driver first uses preinstalled comparator commands, including a configured
Conda prefix when present. It runs `apt-get` only when a command is missing and
passwordless noninteractive sudo is available; otherwise it stops with the
exact missing-command list. Thus an ample-memory preconfigured runner requires
neither sudo package mutation nor sudo swap operations.

The Native v2 CI Docker job and permanent release publisher each require 40 GiB
free and 15 GiB physical RAM: 14 GiB for the final five scientific environments,
18 GiB for transient solves, package downloads, and image export, plus an 8 GiB
reserve. Their preflight runs before the Docker build, and the publisher runs it
before any registry or release mutation.

These requirements exceed the standard runner's guaranteed storage. A
repository administrator may explicitly set `ONCOTRACER_HEAVY_RUNNER` to the
label of a preconfigured runner satisfying the preflight. The variable only
selects an existing runner; it does not provision, purchase, resize, or clean
one. Leave it unset to use `ubuntu-24.04` and receive an actionable early
failure when the observed machine is insufficient. Fork pull requests always
remain on GitHub's isolated standard runner, even when the variable is set. If
a self-hosted label is used, it must identify a dedicated ephemeral runner for
trusted in-repository refs; never expose a persistent scientific server or a
runner containing protected data to pull-request code.

Until such a runner is configured, the exact-head hosted parity and release
gates have a genuine infrastructure blocker. Broad Docker pruning, global Conda
or Nextflow cleanup, and deletion of preinstalled runner software are not
accepted remedies. The current public-runner specifications are maintained in
the [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

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
audit also pins all five ichorCNA assets to the exact SAMURAI commit by byte
size and SHA-256, requires byte-identical frozen/native asset manifests, and
seals those manifests into the artifact checksums. The upstream HD_ULP PoN is
one of those static SAMURAI assets; it is not constructed from example NORMAL
controls. The QuickStart 1
ONT fixture can contain NA read-count bins. ichorCNA 0.5.1 performs two
`quantile(copy, ...)` calls without `na.rm = TRUE` while creating a correction
plot, after its analytical segment outputs have been written.

For the frozen comparator, CI mounts an R startup profile only into the pinned
`ICHORCNA_RUN` container. The profile rewrites exactly those two calls, refuses
an unexpected function shape, and writes an audit marker. Native v2 applies the
same checked compatibility shim and emits `<sample>.ichorcna_plot_compat.tsv`.
The shim does not modify read counting, normalization, HMM fitting, segmentation,
or copy-number calls.

The Illumina GitHub parity contracts validate the BAM-stage SAMURAI execution
actually invoked by OncoTracer. QuickStart 1 therefore expects six contracted
nested tasks for one sample, and QuickStart 2 expects fourteen contracted tasks
for three samples. Only the five runtime images used by those BAM-stage tasks
are required by those contracts; FASTQ QC and alignment occur in OncoTracer
before SAMURAI is called.

Nested Nextflow resumes can distribute successful tasks across several trace
files. Both the hosted release gate and the standalone validation-server driver
therefore build a deterministic combined trace from the latest occurrence of
each canonical task and require every contracted latest occurrence to be
successful or cached with exit status zero. The audit preserves every regular,
non-symlink source trace at its complete root-relative path, so equal basenames
cannot collide, together with a manifest containing each trace's recorded
nanosecond mtime, byte size, row counts, and full SHA-256. Hosted verification
and the extracted server-bundle verifier independently render the combined
trace again from those copied bytes and the manifest's ordering metadata; they
do not trust the precomputed combined trace or archive-reset filesystem mtimes.

Immediately before every outer comparator, the gate records that root's trace
inventory. The sealed audit preserves the pre-run inventory, the post-run
inventory, and their content delta. A successful run must create a new trace
path or change trace content; touching an old file is not sufficient. The
deterministic newest post-run trace is selected by `(mtime_ns, path)`, must be
in the current invocation's content delta, and must contribute at least one
selected contracted scientific task for every Illumina, ONT, and HCC1143
contract. A stale contracted selection accompanied only by a newer unrelated
startup or failure trace therefore fails closed.

The ONT audit additionally requires the complete ten-process contract, including
a freshly `COMPLETED`, exit-zero `ICHORCNA_RUN`; `CACHED` is rejected for this
one deliberately non-cacheable task. Its compatibility marker must be inside
the exact Nextflow work directory identified by that selected task's trace
hash. That fresh task itself must come from the deterministic newest trace.
An early failure with a new trace or a startup failure with no trace fails closed.
An incomplete resume fragment, an unbound marker, a missing or modified raw
trace, or a smaller process subset fails closed. Outer comparator sessions do
not use `-resume`. A content-derived audit-policy digest seals the nested config
and mounted compatibility sources, and ICHORCNA_RUN caching is disabled so a
changed shim cannot reuse stale work. Release audit archives use sorted paths,
fixed ownership and mtimes, and timestamp-free gzip output.
