# Full tutorial: complete public PRJNA754199 archive

This tutorial processes **all 12 Illumina plasma cfDNA libraries currently exposed by the public PRJNA754199 archive**. The main path is intentionally short: download validated reads, let OncoTracer generate the samplesheet and YAML, run the workflow, verify the outputs, and review the reports.

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

This is an **independent reanalysis**, not an exact reproduction of the publication. The publication used GRCh37/hg19, a Plasma-Seq Z-score method, variable-mappability windows, and healthy-donor references. This tutorial uses OncoTracer's hg38 SAMURAI/qDNAseq route and has no matched tumor or healthy-donor controls.

## 1. Plan the run

Use Linux with at least **150 GiB of free working space**. Sixteen CPU cores and 64 GiB RAM are a practical starting point. The first run also prepares hg38 and its BWA index; that one-time step adds several GiB and can take 30–60 minutes before alignment begins.

Complete the [host installation requirements](installation.md#1-install-the-host-prerequisites), then clone OncoTracer and choose one absolute project root. Keep the same terminal open so these variables remain available:

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
export COHORT_ROOT="$PWD/test"  # use a larger absolute filesystem path if needed
export READS_DIR="$COHORT_ROOT/public/prjna754199"
export CONFIG_DIR="$COHORT_ROOT/configs/prjna754199"
export OUT="$COHORT_ROOT/runs/prjna754199"
export YAML="$CONFIG_DIR/illumina.auto.yml"
```

The commands below use the ignored `test/` directory. If you change `COHORT_ROOT`, keep all reads, configuration, work, and output paths below that root so the container can see them.

<a id="2-prepare-software-only"></a>

## 2. Prepare the software

Prepare and smoke-test Docker without starting an analysis:

```bash
nextflow run main.nf --install --docker --lpwgs_root "$COHORT_ROOT" \
  -work-dir "$COHORT_ROOT/work/install"
```

This command pulls and checks the software, records the runtime and pinned SAMURAI identity, and then stops. It does not download hg38 or patient reads and does not create analysis stages `01`–`06`. See [Installation](installation.md#4-prepare-one-runtime-without-starting-an-analysis) for Singularity and Conda alternatives.

## 3. Download and validate the complete public archive

Download all 12 FASTQs with one resumable command:

```bash
COHORT_ROOT="$COHORT_ROOT" bash examples/prjna754199/run_example.sh --download-only
```

The command uses the frozen 12-run manifest and accepts a file only after its ENA byte count, MD5 checksum, and gzip stream all pass. Valid files are reused and interrupted transfers resume. It stops after download validation; it does not generate the workflow configuration or run an analysis.

For the pinned URLs, checksums, archive query, and the implementation of the validation step, see the [example README](https://github.com/cfarkas/oncotracer/tree/main/examples/prjna754199) and [provenance notes](https://github.com/cfarkas/oncotracer/blob/main/examples/prjna754199/PROVENANCE.md).

<a id="4-generate-and-inspect-the-single-end-configuration"></a>

## 4. Generate the samplesheet and YAML automatically

The download command places an auditable `samples.csv` beside the FASTQs. It contains the 12 archive aliases and their patient-cohort `TUMOR` condition labels. Point **Automatic Setup from a Reads Folder** at that self-contained directory:

```bash
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "$CONFIG_DIR" \
  --auto_outdir "$OUT" \
  --run_cna_classifier true \
  --cna_classifier_sample_set sarcoma \
  --cna_classifier_profile conda \
  --pathology_use_biomed_models false \
  --pathology_biomed_local_files_only true
```

Automatic setup validates the supported single-end layout, writes a blank `fastq_2` field for every library, and creates:

```text
test/configs/prjna754199/
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

Inspect both generated files before running:

```bash
sed -n '1,20p' "$CONFIG_DIR/illumina.samplesheet.csv"
sed -n '1,120p' "$YAML"
```

Confirm that the samplesheet contains 12 data rows, each `fastq_1` path exists, every `fastq_2` cell is empty, and the YAML points to the intended output directory. It should also contain `run_cna_classifier: true` and `cna_classifier_sample_set: sarcoma`, which preserve the research-use interpretation reports shown below.

The workflow recognizes the uniform single-end layout and passes `qdnaseq_paired_ends=false` to SAMURAI. No pathology table is supplied, so stage 05 produces a **CNA-only research interpretation**, not a pathology-concordance assessment. Use [manual Illumina setup](configuration/illumina.md#second-option-manual-setup) only when automatic detection does not fit your data.

<a id="5-check-wiring-then-run-the-real-workflow"></a>

## 5. Check the wiring, then run the real workflow

Run the fast wiring check first, followed by the real 12-library analysis:

```bash
nextflow run main.nf -stub-run --docker -params-file "$YAML" \
  -work-dir "$COHORT_ROOT/work/prjna754199_stub"

nextflow run main.nf --docker -params-file "$YAML" \
  -work-dir "$COHORT_ROOT/work/prjna754199" -resume
```

`-stub-run` creates placeholders and cannot validate scientific output. Only the second command performs alignment, CNA analysis, boundary refinement, and interpretation. Keep `-resume`: it reuses unchanged successful tasks after an interruption.

The real command runs in the foreground. To follow SAMURAI in a **second terminal**, return to the clone, set the same output path, and follow the nested log:

```bash
cd /absolute/path/to/oncotracer
export OUT=/absolute/path/to/cohort-root/runs/prjna754199
tail -n 30 -f "$OUT/01_samurai_illumina/nextflow_launch/.nextflow.log"
```

Stop only the log view with `Ctrl+C`; that does not stop the workflow in the first terminal. During stage 01, the outer task may display `0 of 1` while the nested SAMURAI workflow is active. A changing nested log is the reliable progress signal.

## 6. Verify the completed run

Run the versioned verifier against the final output directory:

```bash
python3 examples/prjna754199/verify_outputs.py --outdir "$OUT"
```

The verifier requires the exact 12 manifest aliases—not merely 12 arbitrary rows—across the BAMs, SAMURAI segments, refinement summary, and classifier table. It also requires the CNA tables, plots, clinician-report index, and workflow summary used by this tutorial.

Start reviewing the verified run from these locations:

| Capability | Source output |
| --- | --- |
| Workflow inventory | `06_workflow_summary/workflow_summary.txt` |
| SAMURAI fitted CNA profiles | `01_samurai_illumina/qdnaseq/plots/*_segment_plot.pdf` |
| Boundary-refinement evidence | `02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv` |
| Final CNA event table | `03_cna_codification/cna_events.tsv` |
| Cohort and per-sample plots | `04_cna_custom_plots/` |
| Classifier report | `05_cna_classifier/03_report/cna_classifier_report.html` |
| Per-sample research reports | `05_cna_classifier/03_report/clinician_reports/` |

<a id="7-interpret-without-overclaiming"></a>

## 7. Review the CNA and pathology-facing reports carefully

Black qDNAseq points show normalized bin-level signal; fitted horizontal segment lines summarize the caller's piecewise CNA model. Boundary refinement asks whether local BAM coverage supports moving each coarse boundary and records refined, retained, and low-resolution outcomes.

The classifier may flag a region such as 12q13–q15 or an MDM2/CDK4 overlap for research review. A flag is not confirmation of gene amplification, disease subtype, prognosis, or treatment actionability. Review coverage, segment size, focality, longitudinal consistency, and the original event table; confirm important findings with a validated orthogonal assay.

The stage-05 HTML and per-sample PDFs are useful clinician-facing research summaries, but this archive supplies no pathology table. They must not be described as pathology-confirmed findings. To combine a future cohort with real pathology metadata, follow [Pathology and Classifier](configuration/pathology.md) and require exact sample-identifier matching.

The article reported MDM2-associated signals for selected specimens under its own method. Do not use that statement to relabel a discordant OncoTracer result or choose parameters after seeing the answer. This reanalysis has no matched tissue or healthy-donor controls and is not a sensitivity/specificity validation set.

## 8. Preserve provenance

Keep these files with any shared result:

- frozen `examples/prjna754199/manifest.tsv` and its SHA-256;
- `examples/prjna754199/samples.csv`;
- generated `illumina.samplesheet.csv` and `illumina.auto.yml`;
- OncoTracer commit and clean/dirty worktree state;
- install manifest and immutable container digest;
- hg38/reference identity and SAMURAI `pipeline_info`;
- outer and nested Nextflow logs, report, timeline, and trace;
- classifier report, clinician-report index, and per-sample PDFs;
- unedited source tables behind every exported figure.

<a id="optional-automated-replay"></a>

The [example provenance notes](https://github.com/cfarkas/oncotracer/blob/main/examples/prjna754199/PROVENANCE.md) document the archive snapshot and exact integrity checks. The [example README](https://github.com/cfarkas/oncotracer/tree/main/examples/prjna754199) retains the advanced audit and automated-replay details without interrupting this main path.

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
