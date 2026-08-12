# Troubleshooting

Start with the exact command, backend, YAML, `outdir`, and source identity. Avoid deleting reference indexes, `.oncotracer-native/state.json`, or partial outputs before identifying the failing stage.

## 1. Verify the installed executable and backend

```bash
oncotracer --version
oncotracer provenance --json
oncotracer doctor --backend conda
```

The JSON records the executable version and source identity, backend, all five configured prefixes or image, semantic command/package checks, and whether Nextflow is required (`false`). A failed check returns a nonzero status.

Use the backend that actually runs the analysis:

```bash
oncotracer doctor --backend docker
oncotracer doctor --backend singularity
oncotracer doctor --backend poetry
```

## 2. Validate the configuration without computation

```bash
CONFIG="$PWD/project/config/illumina.auto.yml"

test -s "$CONFIG"
sed -n '1,220p' "$CONFIG"
oncotracer run \
  --backend conda \
  --config "$CONFIG" \
  --dry-run
```

The YAML must be flat. Check absolute paths, mode-specific required fields, and a dedicated `outdir`.

## 3. Find the failing native command

Open:

```text
<outdir>/.oncotracer-native/trace.tsv
<outdir>/.oncotracer-native/state.json
```

The trace records stage, start/end time, exit code, working directory, and a shell-escaped rendering of the original argument array.

```bash
OUT="$PWD/project/results"

column -t -s $'\t' "$OUT/.oncotracer-native/trace.tsv" | tail -30
cat "$OUT/06_workflow_summary/workflow_summary.txt" 2>/dev/null || true
```

Search stage logs and non-empty error files:

```bash
OUT="$PWD/project/results"

find "$OUT" -type f \
  \( -name '*.stderr.log' -o -name '*.command.err' -o -name '*error*log' \) \
  -size +0c -print | sort
```

Many programs write progress to standard error; inspect content and exit status rather than treating any non-empty file as automatic failure.

## 4. Resume safely

Repeat the same `oncotracer run` or QuickStart command. The native ledger reuses a stage only when its signature and expected outputs remain valid.

```bash
oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

Use `--force` only to deliberately invalidate reusable stages:

```bash
oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml" \
  --force
```

OncoTracer now rejects a second writer while the first process holds the exact `outdir` run lock. A new run may claim only an absent or empty output directory. Resume and `--force` require the existing `.oncotracer-native/output-owner.json` to match the same exact OncoTracer runtime and canonical location; they never adopt or delete a nonempty unowned directory. Preserve an unowned or mismatched tree and choose a new `outdir`.

## 5. Public QuickStart download problems

Download and validate without analysis. Replace the example analyses directory with a directory where you have write permission:

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 1 \
  --test-root "$PWD/oncotracer-quickstart1" \
  --download-only
```

Repeat the command after a transient interruption. Completed files are accepted only when expected byte count and MD5 match. A partial temporary download is not treated as valid.

For HCC1143:

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 2 \
  --test-root "$PWD/oncotracer-quickstart2" \
  --download-only
```

## 6. Conda solver or prefix problems

The five environment definitions are independent. Recreate incomplete prefixes:

```bash
oncotracer install --conda --force
oncotracer doctor --backend conda
```

Do not set `R_HOME`, `R_LIBS`, `R_LIBS_USER`, or `R_LIBS_SITE` to another R installation. Native qDNAseq and ichorCNA stages invoke the exact `Rscript` in their own prefix with those ambient variables removed. Diagnose the exact prefixes; do not substitute a login-shell `command -v` result.

Check storage and inode availability when environment creation fails:

```bash
df -h "$HOME" "$PWD"
df -i "$HOME" "$PWD"
```

## 7. Docker errors

```bash
docker info
oncotracer install --docker
oncotracer doctor --backend docker
```

Common causes:

- the invoking user cannot access the Docker daemon;
- a configured path is outside the roots derived from the YAML;
- a symlink points outside a mounted root;
- the filesystem does not permit the invoking UID/GID to write outputs;
- the requested image tag/digest is unavailable.

Use absolute paths and test project permissions:

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/results"
touch "$PROJECT_DIR/results/.write-test"
rm "$PROJECT_DIR/results/.write-test"
```

## 8. Singularity or Apptainer errors

```bash
command -v apptainer || command -v singularity
oncotracer install --singularity
oncotracer doctor --backend singularity
```

Check that the recorded SIF exists and all YAML paths are bind-visible. On managed HPC systems, ask the administrator about allowed bind roots and cache locations.

## 9. Illumina input errors

Automatic Setup supports one consistent layout per run:

- one single-end FASTQ per sample; or
- exactly one R1/R2 pair per sample.

Check names and gzip integrity:

```bash
READS="$PWD/project/input/fastq"

find "$READS" -maxdepth 1 -type f -print | sort
gzip -t "$READS/Patient_A_R1.fastq.gz"
gzip -t "$READS/Patient_A_R2.fastq.gz"
```

Common failures include duplicate sample IDs, missing mates, mixed single/paired layouts.

## 10. Confirm independent Illumina normal outputs

The generated/manual samplesheet preserves each submitted `normal` status, but
normal rows are not reference inputs. Confirm that each expected normal has its
own qDNAseq status and result files:

```bash
SHEET="$PWD/project/config/illumina.samplesheet.csv"
QDNA="$PWD/project/results/01_samurai_illumina/qdnaseq"

sed -n '1,40p' "$SHEET"
cat "$QDNA/qdnaseq_sample_status.json"
find "$QDNA/bins" "$QDNA/segments" "$QDNA/plots" -maxdepth 1 -type f -print | sort
```

If a normal row is absent from `completed_samples`, inspect its entry in the
same status JSON. A mathematically invalid sample may fail post-normalization
QC without stopping later viable samples; OncoTracer must not silently turn the
remaining normals into a panel.

## 11. ONT barcode errors

```bash
FASTQ_PASS="$PWD/project/input/fastq_pass"

find "$FASTQ_PASS" -maxdepth 2 -type d -print | sort
find "$FASTQ_PASS" -maxdepth 2 -type f -print | sort | head -40
```

Confirm that:

- `ont_folder` is the barcode parent or contains exactly one `fastq_pass`;
- every configured barcode directory exists;
- barcode and sample-name lists have identical lengths/order;
- selected FASTQs are directly inside each barcode directory;
- `ont_min_age_minutes` is not excluding completed data unexpectedly.

Review used/skipped/warning logs beneath `01_samurai_ont/logs/`.

## 12. Reference or indexing failures

The first Illumina run creates a BWA hg38 index in the OncoTracer-owned cache and can require at least 80 GiB of addressable memory. Check free storage, memory, and the two possible reference locations:

```bash
free -h
df -h "$PWD/project"
find "$PWD/project/references" -maxdepth 3 -type f -ls 2>/dev/null | head -50
find "$PWD/project/.oncotracer/reference-cache" -maxdepth 3 -type f -ls 2>/dev/null | head -50
```

An existing `project/references/samurai_hg38` or `project/references/samurai_ichorcna_hg38_500kb` is external and read-only. A checksum, manifest, physical-lock, tool-identity, layout, or symlink error there stops the run without repair. This includes an otherwise usable FASTA/index directory created by another tool but lacking OncoTracer's exact `.oncotracer` manifests and locks. Do not manufacture or copy those records. Coordinate shared-reference maintenance outside OncoTracer, or use a new project root so OncoTracer can create its own content-addressed cache. Never delete a valid shared reference during another active run.

qDNAseq annotations are published under `project/.oncotracer/reference-cache/qdnaseq-hg38-<binsize>kb-*/generations/`. A failed build leaves no current-generation pointer and is safe to rerun. A changed published generation fails closed rather than being repaired in place. OncoTracer deliberately ignores and preserves the older `project/.oncotracer/qdnaseq-bin-data` location.

## 13. Classifier or GISTIC2 failures

First confirm the CNA-only stages completed:

```bash
OUT="$PWD/project/results"

test -s "$OUT/03_cna_codification/cna_events.tsv"
test -s "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"
```

Then inspect:

```bash
find "$OUT/05_cna_classifier" -maxdepth 3 -type f -print | sort | head -100
cat "$OUT/05_cna_classifier/native_classifier_summary.json"
```

Keep `gistic_required: false` unless the study requires a fatal GISTIC2 branch. Start with deterministic offline settings before enabling model/network enrichment.

## 14. Confirm stable release identity

```bash
oncotracer provenance --json
sha256sum oncotracer 2>/dev/null || true
```

For a stable asset, `source_commit`, `source_sha256`, and binary checksum must agree with `release-provenance.json` and `SHA256SUMS` from the same release.

## 15. Standalone payload-cache safety

The copied executable normally manages its own content-addressed directory below the XDG cache. If `ONCOTRACER_PAYLOAD_CACHE` is set accidentally, return to the safe default before retrying:

```bash
unset ONCOTRACER_PAYLOAD_CACHE
oncotracer --version
```

The override is an advanced integration option. It must name an absent or empty dedicated path, or a complete cache already owned by that exact executable archive. Never point it at a home directory, XDG root, shared reference, analysis, or scientific-data directory. OncoTracer rejects symlinked, unowned, mismatched, or unexpectedly populated paths and preserves them rather than recursively deleting them. Inspect an ownership or integrity error; do not respond with a broad cache deletion.

The default cache layout is:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/oncotracer/2.0.0/<executable-sha256>/payload
```

Every `--dry-run` uses a separate temporary payload and removes it on success, error, or interruption. A dry-run must not create persistent XDG state, outputs, environments, images, or SIF files.

## 16. Report a reproducible problem

Include only de-identified information:

- exact OncoTracer version and provenance JSON;
- backend and `doctor` JSON;
- YAML with sensitive paths/sample names redacted consistently;
- failing trace row and relevant stage log;
- operating system, available memory/storage, and container/runtime version;
- whether the problem reproduces in QuickStart 1 or 2.

Never attach identifiable patient data or private pathology text to a public issue.
