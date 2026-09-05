# Troubleshooting

## Start with the command and configuration

Run `oncotracer check --config /absolute/path/to/run.yml`. It lists missing paths and settings without starting analysis. `oncotracer doctor --backend conda` checks the installed analysis tools.

| Message or symptom | What to do |
| --- | --- |
| `setup` or `check` is not recognized | Activate the environment created during [installation](installation.md); use `command -v oncotracer` to check which command your terminal is using |
| A path does not exist | Use the actual absolute path; `/data/...` and `/work/...` in examples must be replaced |
| Setup refuses to overwrite a YAML | Edit that existing file, or choose a new `--project` |
| Both POD5 and BAM inputs are configured | Keep only the input type you want to use |
| CPU methylation basecalling is very slow | Reuse completed modified-base BAMs with `--modbam` if available; raw signal can take days on CPU |
| No FASTQ-selected reads with MM/ML tags | Match the FASTQs to BAMs made with modified-base calling, or use raw POD5 |
| `no_classifier_probes` | Check usable human alignment and hg38 probe coordinates. CpG calls elsewhere cannot support a MARLIN prediction |

For methylation, `install --conda` does not install optional Dorado, Modkit, or classifier resources. See the [methylation guide](configuration/methylation.md).

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

Repeat the failed `curl` command from [QuickStart 1](quick_start.md) or
[QuickStart 2](public_cohort.md). `--continue-at -` resumes an interrupted
download. Use the same analysis directory and output filename.

Repeat the guide's `md5sum -c` block and continue only when every file says `OK`.
If a completed download fails its checksum, preserve that file under a different
name and download a fresh copy. Do not analyze a partial or mismatched file.
Downloading reads does not create a configuration: follow the separate `setup`,
`check`, and `run` steps afterwards.

## 6. Conda solver or prefix problems

The five environment definitions are independent. Recreate incomplete prefixes:

```bash
oncotracer install --conda --force
oncotracer doctor --backend conda
```

Do not set `R_HOME`, `R_LIBS`, `R_LIBS_USER`, or `R_LIBS_SITE` to another R installation. Native qDNAseq and ichorCNA stages invoke the exact `Rscript` in their own prefix with those ambient variables removed. Diagnose the exact prefixes; do not substitute a login-shell `command -v` result.

An ownership error means the selected `--prefix` is populated but was not
created by this installer, a fixed child has lost or mismatched its marker, a
symlink is present in the target path, or an interrupted journal cannot be
authenticated. Do not add a marker manually and do not delete the path broadly.
Preserve it, inspect the reported path, and choose a new absent or empty
dedicated prefix. `--force` intentionally cannot adopt or erase an unowned
directory. If a managed prefix is reported active, allow the named process to
finish or select another prefix instead of replacing files beneath it. After an
uncatchable interruption, OncoTracer can report an `oncotracer-preserved` sibling
containing an unsealed package-manager tree. It is not automatically deleted:
inspect it, confirm it contains no unique or foreign data, and remove only that
exact path when appropriate. Rerunning the installer recovers the journal and
restores or completes the managed installation.

Check storage and inode availability when environment creation fails:

```bash
df -h "$HOME" "$PWD"
df -i "$HOME" "$PWD"
```

Poetry installation additionally requires Poetry 2.0 or newer and an exact,
clean Git checkout matching the executable provenance. Version, dirty-tree, or
source-identity failures occur before managed targets are changed.

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

The installed SIF must have its adjacent strict `.oncotracer.json` sidecar. A
missing, malformed, path-mismatched, source-mismatched, or checksum-mismatched
pair is preserved and rejected; `--force` does not make an unowned file safe to
replace. Choose a new absent `--sif` destination. For an intact owned pair,
`--force` pulls and validates a same-directory candidate before an atomic swap,
so a pull, doctor, provenance, or publication failure leaves the prior image
available. Never pre-delete a shared or active SIF to work around an ownership
error.

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
