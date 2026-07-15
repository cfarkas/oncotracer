# Full tutorial: complete public PRJNA754199 archive

This tutorial processes **all 12 Illumina plasma cfDNA libraries currently exposed by the public PRJNA754199 archive**. It starts with an installation-only check, validates every downloaded byte against ENA, runs the real OncoTracer workflow, and ends with plots, refinement statistics, and research-use CNA interpretation.

## What this tutorial does—and does not—contain

The associated PLOS ONE article describes 41 plasma specimens from 15 patients: 10 longitudinal specimens from four DDLPS/WDLPS patients and 31 specimens from 11 patients with other soft-tissue tumors. On **15 July 2026**, the ENA read-run report returned only 12 read runs for the BioProject.

| Public archive snapshot | Value |
| --- | ---: |
| Public runs processed here | 12 |
| Layout | single-end |
| Instrument and read length | Illumina HiSeq 2500, 36 bp |
| Deposited reads | 266,097,582 |
| Deposited bases | 9,579,512,952 |
| Compressed download | 6,171,900,300 bytes (5.75 GiB) |
| Reference/caller | hg38, SAMURAI/qDNAseq, 100 kb |

Ten archive aliases correspond to the article's primary serial-sampling set. The additional aliases `WDLPS_2` and `WDLPS_3` are not reconciled to rows in the article supplement. None of the 31 other-tumor specimens is currently present in the read archive. One deposited run, `DDLPS_2`, contains 8,351,915 reads and is therefore below the article's general 10-million-read description.

!!! warning "Archive aliases are not diagnoses"
    `DDLPS_*` and `WDLPS_*` are submitter-provided sample aliases. OncoTracer preserves them for provenance; it does not independently verify a diagnosis. The generated `tumor` status is a SAMURAI condition label for this patient cohort, not proof of detectable ctDNA, active disease, or a CNA in any specimen.

This is also an **independent reanalysis**, not an exact reproduction of the publication. The publication used GRCh37/hg19, a Plasma-Seq Z-score method, variable-mappability windows, and healthy-donor references. This tutorial uses OncoTracer's hg38 SAMURAI/qDNAseq route and has no matched tumor or healthy-donor controls.

## 1. Plan the run

Use Linux with at least **150 GiB of free working space**. Sixteen CPU cores and 64 GiB RAM are a practical starting point; more cores and fast local storage reduce alignment time. The 5.75 GiB compressed FASTQs expand into BAMs, Nextflow work, reference indexes, and final reports.

The first run also prepares hg38 and its BWA index if they are not already cached. That one-time step adds several GiB of reference/index data and can take 30–60 minutes before sample alignment begins.

Complete the [host installation requirements](installation.md#1-install-the-host-prerequisites) first. This verified tutorial uses Docker and also needs the host-side stage-01 tools because the outer workflow launches SAMURAI and prepares references from the host:

```bash
set -Eeuo pipefail                                                       # stop immediately when a required check fails
command -v git java nextflow docker python3 curl samtools bwa minimap2 pigz
command -v gzip md5sum sha256sum awk stat find wc seq readlink
command -v nano >/dev/null || echo "Nano is absent; substitute your terminal editor in step 4."
docker info >/dev/null                                                   # prove this user can reach the daemon
```

`nano` is used only for the editing lesson; another terminal editor is fine. Choose a root that will contain reads, references, Nextflow work, and results. Keep the same terminal open so the variables defined here remain available in later steps:

```bash
git clone https://github.com/cfarkas/oncotracer.git                    # clone the workflow
cd oncotracer                                                        # main.nf is here
export COHORT_ROOT="$PWD/test"                                       # or a larger absolute filesystem path
export INSTALL_WORK_DIR="$COHORT_ROOT/work/install"                  # small installation-only task
export WORK_DIR="$COHORT_ROOT/work/prjna754199"                      # large real-run intermediates
export STUB_WORK_DIR="$COHORT_ROOT/work/prjna754199_stub"            # disposable wiring-check work
mkdir -p "$COHORT_ROOT" "$INSTALL_WORK_DIR" "$WORK_DIR" "$STUB_WORK_DIR"
```

The commands below use the ignored `test/` directory. To use another disk, set `COHORT_ROOT=/absolute/path` for every command below.

## 2. Prepare software only

The `--install` route prepares Docker and then stops:

```bash
nextflow run main.nf --install --docker --lpwgs_root "$COHORT_ROOT" \
  -work-dir "$INSTALL_WORK_DIR"                                      # pull, cache, and smoke-test software
cat .oncotracer/install/install_manifest.txt                         # inspect exact runtime and SAMURAI identity
```

The command does not require `mode`, reads, or an output directory. It does not download hg38 or PRJNA754199 and does not create analysis stages `01`–`06`. The [installation guide](installation.md#4-prepare-one-runtime-without-starting-an-analysis) gives the equivalent Singularity and Conda routes; use exactly one runtime flag.

A successful manifest records:

- the selected runtime and immutable image/environment identity;
- Java, Nextflow, Python, and samtools versions;
- pinned SAMURAI revision `v1.4.0` and its resolved commit;
- `hg38_prepared=false`, `reads_downloaded=false`, and `analysis_started=false`.

Repeating the command is safe: valid Docker layers, Conda packages, and the shared SAMURAI cache are reused.

## 3. Download and validate the complete public archive

First define the source manifest and destination. Inspect its header and confirm that it contains 12 data rows:

```bash
MANIFEST="$PWD/examples/prjna754199/manifest.tsv"
READS_DIR="$COHORT_ROOT/public/prjna754199"
mkdir -p "$READS_DIR"

sed -n '1,4p' "$MANIFEST"                                         # understand the columns
test "$(awk 'END {print NR-1}' "$MANIFEST")" -eq 12                # exactly 12 public runs
```

Before downloading, independently recalculate the manifest totals:

```bash
python3 - "$MANIFEST" <<'PY'
import csv, sys
with open(sys.argv[1], newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
print("runs:", len(rows))
print("reads:", sum(int(row["read_count"]) for row in rows))
print("bases:", sum(int(row["base_count"]) for row in rows))
print("compressed bytes:", sum(int(row["fastq_bytes"]) for row in rows))
assert len(rows) == 12
assert sum(int(row["read_count"]) for row in rows) == 266_097_582
assert sum(int(row["base_count"]) for row in rows) == 9_579_512_952
assert sum(int(row["fastq_bytes"]) for row in rows) == 6_171_900_300
PY
```

Now define the validation and download function directly in the terminal. It reuses a local file only when all three checks pass, keeps a smaller partial file for HTTP resume, and resets a complete-but-invalid file before retrying:

```bash
validate_fastq() {
  local fastq="$1" expected_bytes="$2" expected_md5="$3"
  [[ -s "$fastq" ]] || return 1
  [[ "$(stat -c %s "$fastq")" -eq "$expected_bytes" ]] || return 1
  [[ "$(md5sum "$fastq" | awk '{print $1}')" = "$expected_md5" ]] || return 1
  gzip -t "$fastq"
}

download_fastq() {
  local url="$1" fastq="$2" expected_bytes="$3" expected_md5="$4"
  local attempt current_bytes

  if validate_fastq "$fastq" "$expected_bytes" "$expected_md5"; then
    echo "REUSE: $fastq"
    return 0
  fi

  for attempt in $(seq 1 20); do
    current_bytes=0
    [[ -e "$fastq" ]] && current_bytes="$(stat -c %s "$fastq")"
    if [[ "$current_bytes" -ge "$expected_bytes" ]]; then
      echo "RESET: invalid complete file $fastq"
      rm -f -- "$fastq"
      current_bytes=0
    fi

    echo "DOWNLOAD $attempt/20: $fastq ($current_bytes/$expected_bytes bytes present)"
    curl --fail --location --continue-at - \
      --connect-timeout 30 --speed-time 60 --speed-limit 1024 \
      --retry 3 --retry-delay 3 --retry-all-errors \
      --output "$fastq" "$url" || true

    if validate_fastq "$fastq" "$expected_bytes" "$expected_md5"; then
      echo "VALIDATED: $fastq"
      return 0
    fi
    sleep $((attempt < 10 ? attempt * 2 : 20))
  done

  echo "ERROR: download did not validate after 20 resumable attempts: $url" >&2
  return 1
}
```

Apply that function to every manifest row. The field names on the `read` line follow the manifest header from left to right:

```bash

while IFS=$'\t' read -r sample biosample experiment run instrument layout \
  read_length reads bases filename bytes md5 url; do
  [[ "$sample" == "sample_alias" ]] && continue
  echo "Preparing $sample from $run"
  download_fastq \
    "$url" \
    "$READS_DIR/$sample.fastq.gz" \
    "$bytes" \
    "$md5"
done < "$MANIFEST"
```

Check that all 12 files exist, then print the checksum of each downloaded file beside its expected checksum:

```bash
test "$(find "$READS_DIR" -maxdepth 1 -name '*.fastq.gz' | wc -l)" -eq 12

while IFS=$'\t' read -r sample _ _ run _ _ _ _ _ _ bytes md5 _; do
  [[ "$sample" == "sample_alias" ]] && continue
  fastq="$READS_DIR/$sample.fastq.gz"
  test "$(stat -c %s "$fastq")" -eq "$bytes"
  test "$(md5sum "$fastq" | awk '{print $1}')" = "$md5"
  gzip -t "$fastq"
  printf '%-10s %s  OK\n' "$sample" "$run"
done < "$MANIFEST"
```

These checks cover:

1. the pinned HTTPS ENA path;
2. exact compressed byte count;
3. ENA MD5 checksum;
4. a complete gzip stream.

An interrupted transfer is resumed. A local file is reused only after all validation checks pass.

## 4. Generate and inspect the single-end configuration

Create configuration paths:

```bash
CONFIG_DIR="$COHORT_ROOT/configs/prjna754199"
OUT="$COHORT_ROOT/runs/prjna754199"
SAMPLESHEET="$CONFIG_DIR/illumina.single_end.samplesheet.csv"
YAML="$CONFIG_DIR/illumina.full_tutorial.yml"
mkdir -p "$CONFIG_DIR" "$OUT"
```

Write one samplesheet row per manifest entry. The code deliberately leaves `fastq_2` empty and stops if a downloaded file is missing:

```bash
python3 - "$MANIFEST" "$READS_DIR" "$SAMPLESHEET" <<'PY'
import csv, sys
from pathlib import Path

manifest, reads_dir, output = map(Path, sys.argv[1:])
with manifest.open(newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
with output.open("w", newline="") as handle:
    writer = csv.DictWriter(
        handle, fieldnames=["sample", "fastq_1", "fastq_2", "status"]
    )
    writer.writeheader()
    for row in rows:
        fastq = (reads_dir / f"{row['sample_alias']}.fastq.gz").resolve()
        if not fastq.is_file():
            raise SystemExit(f"Missing FASTQ: {fastq}")
        writer.writerow({
            "sample": row["sample_alias"],
            "fastq_1": str(fastq),
            "fastq_2": "",
            "status": "tumor",
        })
PY

sed -n '1,20p' "$SAMPLESHEET"
```

The samplesheet retains the standard four-column contract. `fastq_2` is intentionally blank:

```csv
sample,fastq_1,fastq_2,status
DDLPS_1a,/absolute/path/DDLPS_1a.fastq.gz,,tumor
DDLPS_1b,/absolute/path/DDLPS_1b.fastq.gz,,tumor
```

The workflow detects that every row is single-end and passes `qdnaseq_paired_ends=false` to SAMURAI. A single invocation cannot mix single-end and paired-end rows.

Create the YAML in a terminal editor:

```bash
readlink -m "$COHORT_ROOT"                                            # value for lpwgs_root
readlink -m "$OUT"                                                    # value for outdir
readlink -m "$SAMPLESHEET"                                            # value for illumina_samplesheet
nano "$YAML"
```

Paste the block below, replacing the three `/absolute/path/...` values with the three paths printed above:

```yaml
mode: illumina
lpwgs_root: /absolute/path/to/cohort-root
outdir: /absolute/path/to/cohort-root/runs/prjna754199
illumina_samplesheet: /absolute/path/to/cohort-root/configs/prjna754199/illumina.single_end.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: true
cna_classifier_sample_set: sarcoma
cna_classifier_profile: conda
pathology_csv: null
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
force: false
```

In Nano, save with `Ctrl+O`, press `Enter`, and exit with `Ctrl+X`. Then inspect the saved file and make sure no placeholder remains:

```bash
sed -n '1,120p' "$YAML"
if grep -q '/absolute/path' "$YAML"; then
  echo "ERROR: replace every placeholder before running" >&2
  exit 1
fi
```

The fixed `sarcoma` context is chosen from the study design before results are examined. No pathology table is supplied, so the downstream report is a CNA-only research interpretation, not a pathology-concordance assessment.

## 5. Check wiring, then run the real workflow

Run the wiring check first, then the real analysis. The explicit work directories keep large intermediates below `COHORT_ROOT`, even when the repository itself is on a smaller filesystem:

```bash
nextflow run main.nf -stub-run --docker -params-file "$YAML" \
  -work-dir "$STUB_WORK_DIR"                                         # wiring only; no real analysis

nextflow run main.nf --docker -params-file "$YAML" \
  -work-dir "$WORK_DIR" -resume                                     # real 12-library analysis
```

`-stub-run` creates placeholders and cannot validate scientific output. Only the command without `-stub-run` performs alignment, CNA analysis, boundary refinement, and interpretation. Keep `-resume`: it reuses unchanged successful tasks after interruption.

The real command runs in the foreground. While it is active, open a **second terminal**, return to the same clone, set the same cohort root, and inspect the nested log. Stop only this live log view with `Ctrl+C`; that does not stop the workflow in the first terminal:

```bash
cd /absolute/path/to/oncotracer
export COHORT_ROOT="/the/same/cohort/root/used/in/terminal/one"
tail -n 30 -f "$COHORT_ROOT/runs/prjna754199/01_samurai_illumina/nextflow_launch/.nextflow.log"
```

During stage 01, the outer OncoTracer task may display `0 of 1` while this nested SAMURAI workflow is active. The changing nested log is the reliable progress signal.

## 6. Verify the completed run

First require non-empty outputs from every configured stage:

```bash
OUT="$COHORT_ROOT/runs/prjna754199"
test "$(find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -type f -name '*.bam' | wc -l)" -eq 12
test -s "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"
test -s "$OUT/02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv"
test -s "$OUT/03_cna_codification/cna_events.tsv"
test -s "$OUT/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"
test -s "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"
test -s "$OUT/05_cna_classifier/03_report/cna_classifier_report.html"
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

Then compare the manifest IDs to all four sample-bearing stages. This is stronger than accepting 12 arbitrary files or rows. SAMURAI adds a processing suffix such as `_markdup` to its segment IDs, so the check maps those IDs back to the longest matching manifest alias:

```bash
python3 - "$MANIFEST" "$OUT" <<'PY'
import csv, sys
from pathlib import Path

manifest, out = Path(sys.argv[1]), Path(sys.argv[2])
with manifest.open(newline="") as handle:
    expected = {row["sample_alias"] for row in csv.DictReader(handle, delimiter="\t")}

def require_exact(label, observed):
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing or extra:
        raise SystemExit(f"{label}: missing={missing}; unexpected={extra}")
    print(f"{label}: {len(observed)} expected samples")

bam_dir = out / "01_samurai_illumina" / "alignment"
require_exact("BAMs", {path.stem for path in bam_dir.glob("*.bam")})

segments = out / "01_samurai_illumina" / "qdnaseq" / "all_segments.seg"
with segments.open(newline="") as handle:
    segment_ids = {row["ID"] for row in csv.DictReader(handle, delimiter="\t")}
segment_samples = set()
for value in segment_ids:
    matches = [sample for sample in expected if value == sample or value.startswith(sample + "_")]
    if not matches:
        raise SystemExit(f"SAMURAI segments: unrecognized ID {value!r}")
    segment_samples.add(max(matches, key=len))
require_exact("SAMURAI segments", segment_samples)

refinement = out / "02_bam_refinement" / "illumina_qdnaseq_100kb" / "01_tables" / "sample_refinement_summary.csv"
with refinement.open(newline="") as handle:
    require_exact("Refinement summary", {row["sample"] for row in csv.DictReader(handle)})

classification = out / "05_cna_classifier" / "02_classification" / "cna_patient_classification.tsv"
with classification.open(newline="") as handle:
    require_exact("Classifier table", {row["sample"] for row in csv.DictReader(handle, delimiter="\t")})
PY
```

Start interpretation from:

| Capability | Source output |
| --- | --- |
| SAMURAI fitted CNA profiles | `01_samurai_illumina/qdnaseq/plots/*_segment_plot.pdf` |
| Boundary-refinement evidence | `02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv` |
| Final CNA event table | `03_cna_codification/cna_events.tsv` |
| Cohort and per-sample plots | `04_cna_custom_plots/` |
| CNA-only interpretation | `05_cna_classifier/03_report/` |
| Run entry point | `06_workflow_summary/workflow_summary.txt` |

## 7. Interpret without overclaiming

Black qDNAseq points show normalized bin-level signal; fitted horizontal segment lines summarize the caller's piecewise CNA model. Boundary refinement asks whether local BAM coverage supports moving each coarse boundary and records refined, retained, and low-resolution outcomes.

The classifier may flag a region such as 12q13–q15 or an MDM2/CDK4 overlap for research review. A flag is not confirmation of gene amplification, disease subtype, prognosis, or treatment actionability. Review coverage, segment size, focality, longitudinal consistency, and the original event table; confirm important findings with a validated orthogonal assay.

The article reported MDM2-associated signals for selected specimens under its own method. Do not use that statement to relabel a discordant OncoTracer result or choose parameters after seeing the answer. This reanalysis has no matched tissue or healthy-donor controls and is not a sensitivity/specificity validation set.

## 8. Preserve provenance

For the manual route, write a small receipt before moving the output:

```bash
PROVENANCE="$CONFIG_DIR/run_provenance.tsv"
{
  printf 'field\tvalue\n'
  printf 'archive_project\tPRJNA754199\n'
  printf 'archive_inventory_date\t2026-07-15\n'
  printf 'public_run_count\t12\n'
  printf 'manifest_sha256\t%s\n' "$(sha256sum "$MANIFEST" | awk '{print $1}')"
  printf 'oncotracer_commit\t%s\n' "$(git rev-parse HEAD)"
  if [[ -n "$(git status --short)" ]]; then
    printf 'oncotracer_worktree\tdirty\n'
  else
    printf 'oncotracer_worktree\tclean\n'
  fi
  printf 'runtime_flag\t--docker\n'
  printf 'container_identity\t%s\n' "$(docker image inspect carlosfarkas/oncotracer:latest --format '{{index .RepoDigests 0}}')"
  printf 'reference_build\thg38\n'
  printf 'initial_caller\tqDNAseq\n'
  printf 'initial_bin_size_kb\t100\n'
  printf 'classifier_context\tsarcoma\n'
} > "$PROVENANCE"
sed -n '1,80p' "$PROVENANCE"
```

Replace the runtime line and identity command when using Singularity or Conda. The automated replay writes the equivalent receipt itself.

Keep these files with any shared result:

- frozen `examples/prjna754199/manifest.tsv` and its SHA-256;
- generated samplesheet, YAML, and `run_provenance.tsv`;
- OncoTracer commit and clean/dirty state;
- install manifest and container digest;
- hg38/reference identity and SAMURAI `pipeline_info`;
- outer and nested Nextflow logs, plus the SAMURAI report, timeline, and trace;
- unedited source tables behind every exported figure.

## Optional automated replay

After learning each step above, the [versioned runner](https://github.com/cfarkas/oncotracer/tree/main/examples/prjna754199) can replay the same operations and output checks. It is a convenience and reproducibility tool, not a substitute for understanding the manifest, samplesheet, YAML, and result tables:

```bash
COHORT_ROOT="$COHORT_ROOT" \
  bash examples/prjna754199/run_example.sh --docker
```

Use `--download-only` to stop after validation or `--prepare-only` to stop after writing the samplesheet, YAML, and provenance receipt.

## Verified result gallery

The following images are static exports from the complete 12-run workflow described above. They demonstrate software output; they do not validate a diagnosis. [Inspect the asset/source checksums and transformation record](assets/full_tutorial/gallery_provenance.tsv).

### SAMURAI fitted copy-number profile

[Open the source qDNAseq segment PDF](assets/full_tutorial/prjna754199_samurai_ddlps1b_segment_plot.pdf).

![SAMURAI qDNAseq profile for the public DDLPS_1b archive alias, with bin-level signal and fitted horizontal CNA segments](assets/full_tutorial/prjna754199_samurai_ddlps1b_segment_plot.png)

### BAM-supported boundary-refinement statistics

[Open the source 12-sample refinement summary](assets/full_tutorial/prjna754199_refinement_summary.csv).

![Counts of refined, retained, and poor-resolution boundaries for all 12 public PRJNA754199 libraries](assets/full_tutorial/prjna754199_refinement_summary.png)

### CNA-only research interpretation (no pathology supplied)

[Open the source research-use classifier report](assets/full_tutorial/prjna754199_cna_interpretation.pdf).

![CNA-only research interpretation for DDLPS_1b exported from the verified OncoTracer classifier output; no pathology was supplied](assets/full_tutorial/prjna754199_cna_interpretation.png)

!!! warning "Research use only"
    OncoTracer is not a standalone diagnostic system or medical device. The gallery is a reproducible software demonstration. It must not be used by itself to diagnose disease, choose treatment, establish prognosis, or report a clinical result.

## Primary sources

- [NCBI BioProject PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/PRJNA754199)
- [ENA PRJNA754199 archive record](https://www.ebi.ac.uk/ena/browser/view/PRJNA754199)
- [Przybyl et al., PLOS ONE (2022)](https://doi.org/10.1371/journal.pone.0262272)
- [Publication supplementary table S1](https://doi.org/10.1371/journal.pone.0262272.s001)
