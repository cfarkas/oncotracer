# Full tutorial: complete public PRJNA754199 archive

This tutorial processes the **12 Illumina plasma cfDNA libraries currently available in the public PRJNA754199 read archive**. It downloads and validates the FASTQs, generates the configuration automatically, runs OncoTracer, verifies the required files, and reviews the research outputs.

[![Roadmap for the complete PRJNA754199 tutorial.](assets/tutorial/full_tutorial_flow.svg)](assets/tutorial/full_tutorial_flow.svg)

The commands below use Docker. On a configured HPC system, replace `--docker` with `--singularity`. See [Installation](installation.md) and the maintained [Docker image](https://hub.docker.com/r/carlosfarkas/oncotracer).

## What this tutorial contains

The associated PLOS ONE article describes 41 plasma specimens from 15 patients. On **15 July 2026**, the ENA read-run report returned 12 public runs for the BioProject, so this tutorial processes those 12 runs rather than every specimen described in the article.

| Public archive snapshot | Value |
| --- | ---: |
| Public runs processed here | 12 |
| Layout | single-end |
| Instrument and read length | Illumina HiSeq 2500, 36 bp |
| Deposited reads | 266,097,582 |
| Deposited bases | 9,579,512,952 |
| Compressed download | 6,171,900,300 bytes, about 5.75 GiB |
| Reference and caller | hg38, SAMURAI/qDNAseq, 100 kb |

`DDLPS_*` and `WDLPS_*` are submitter-provided archive aliases. They are retained for provenance and are not independently verified diagnoses. This is an independent hg38/qDNAseq reanalysis, not an exact reproduction of the publication's GRCh37 Plasma-Seq method.

## Estimated time and resources

Use Linux with at least 150 GiB of free working space, 16 CPU cores, and at least 80 GiB of addressable RAM. The first uncached run downloads hg38 and creates a BWA index. Indexing commonly takes **30–60 minutes** before alignment begins, and the complete 12-library analysis can take several hours.

## 1. Clone the repository

This tutorial uses `/home/student/oncotracer` as an example path.

```bash
# Clone OncoTracer into the example directory.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository and confirm the path.
cd /home/student/oncotracer
pwd
```

Skip the `git clone` command when the repository already exists.

<a id="2-prepare-software-only"></a>

## 2. Prepare the software

Docker:

```bash
# Pull or reuse the Docker image and test the required software.
nextflow run /home/student/oncotracer/main.nf --install --docker \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install
```

Singularity or Apptainer:

```bash
# Prepare the same workflow image through the HPC container option.
nextflow run /home/student/oncotracer/main.nf --install --singularity \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install
```

The installation route checks the software and stops. It does not download patient reads or hg38 and does not start the analysis.

## 3. Download and validate the 12 public FASTQs

```bash
# Download or reuse all 12 FASTQs and verify their size, MD5, and gzip integrity.
nextflow run /home/student/oncotracer/main.nf --make_prjna754199 \
  --test_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/prjna754199_download
```

The command creates `/home/student/oncotracer/test/public/prjna754199` and places `samples.csv` beside the FASTQs. A completed file is reused when the command is repeated.

[![Successful validation checkpoint for the 12 PRJNA754199 FASTQs.](assets/tutorial/full_tutorial_download_checkpoint.svg)](assets/tutorial/full_tutorial_download_checkpoint.svg)

The exact generated sample table is:

```csv
sample_name,status
DDLPS_1a,TUMOR
DDLPS_1b,TUMOR
DDLPS_1c,TUMOR
DDLPS_2,TUMOR
DDLPS_3a,TUMOR
DDLPS_3b,TUMOR
WDLPS_1a,TUMOR
WDLPS_1b,TUMOR
WDLPS_1c,TUMOR
WDLPS_1d,TUMOR
WDLPS_2,TUMOR
WDLPS_3,TUMOR
```

See [`examples/prjna754199/manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/prjna754199/manifest.tsv) for the public files and checksums and [`PROVENANCE.md`](https://github.com/cfarkas/oncotracer/blob/main/examples/prjna754199/PROVENANCE.md) for the archive notes.

<a id="4-generate-and-inspect-the-single-end-configuration"></a>

## 4. Generate the samplesheet and YAML automatically

`--auto_params` matches the 12 sample names to the single-end FASTQs, validates the files, and writes the YAML and samplesheet. It does not start the analysis.

```bash
# Generate the 12-sample Illumina configuration and enable CNA-only reports.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/prjna754199 \
  --sample_table /home/student/oncotracer/test/public/prjna754199/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/prjna754199 \
  --auto_outdir /home/student/oncotracer/test/runs/prjna754199 \
  --run_cna_classifier true \
  --cna_classifier_sample_set sarcoma \
  --pathology_use_biomed_models false \
  -work-dir /home/student/oncotracer/test/work/prjna754199_auto_params
```

Inspect the generated files:

```bash
# List the generated YAML, samplesheet, and manifest.
ls -1 /home/student/oncotracer/test/configs/prjna754199

# Inspect the generated analysis settings.
sed -n '1,160p' /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml

# Inspect the generated 12-row single-end samplesheet.
sed -n '1,20p' /home/student/oncotracer/test/configs/prjna754199/illumina.samplesheet.csv

# Inspect the sample counts and file hashes.
cat /home/student/oncotracer/test/configs/prjna754199/auto_params_manifest.tsv
```

The configuration directory contains:

```text
/home/student/oncotracer/test/configs/prjna754199/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

<a id="5-check-wiring-then-run-the-real-workflow"></a>

## 5. Check the wiring and run the analysis

Optional stub check:

```bash
# Check the generated workflow connections without running the scientific tools.
nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199_stub
```

Real analysis:

```bash
# Run the complete 12-library workflow with Docker and resume support.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199 \
  -resume
```

Keep the terminal open until Nextflow returns to the prompt. To resume after an interruption, repeat the same command with the same YAML and work directory.

## 6. Verify the completed run

```bash
# Verify the exact 12 samples and all required output groups.
python3 /home/student/oncotracer/examples/prjna754199/verify_outputs.py \
  --outdir /home/student/oncotracer/test/runs/prjna754199
```

A successful check ends with:

```text
SUCCESS: complete PRJNA754199 tutorial outputs are verified.
```

[![Successful output-verification checkpoint for the complete tutorial.](assets/tutorial/full_tutorial_verify_checkpoint.svg)](assets/tutorial/full_tutorial_verify_checkpoint.svg)

Start reviewing the run from:

| Output | Location |
| --- | --- |
| Workflow summary | `06_workflow_summary/workflow_summary.txt` |
| SAMURAI qDNAseq profiles | `01_samurai_illumina/qdnaseq/plots/` |
| Boundary-refinement summary | `02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv` |
| Final CNA events | `03_cna_codification/cna_events.tsv` |
| Cohort and per-sample plots | `04_cna_custom_plots/` |
| Classifier HTML | `05_cna_classifier/03_report/cna_classifier_report.html` |
| Per-sample research PDFs | `05_cna_classifier/03_report/clinician_reports/` |

<a id="7-interpret-without-overclaiming"></a>

## 7. Review the results carefully

Black qDNAseq points show normalized bin-level signal; fitted horizontal lines show the caller's CNA segments. Boundary refinement evaluates whether local BAM coverage supports moving each coarse boundary.

The classifier may flag regions such as 12q13–q15 or overlaps with genes including `MDM2` and `CDK4`. These are research findings, not confirmed diagnoses or treatment recommendations. Review coverage, segment size, focality, longitudinal consistency, and the original CNA table. Confirm important findings with a validated orthogonal assay.

No pathology table is supplied in this public archive example, so the generated reports are CNA-only research summaries.

<a id="8-preserve-provenance"></a>

## 8. Keep the files that describe the run

Preserve:

- `examples/prjna754199/manifest.tsv` and `samples.csv`;
- `test/configs/prjna754199/illumina.samplesheet.csv`, `illumina.auto.yml`, and `auto_params_manifest.tsv`;
- `test/runs/prjna754199/06_workflow_summary/workflow_summary.txt`;
- the CNA tables, plots, and reports used in any interpretation;
- the OncoTracer commit and installation manifest.

## Verified result gallery

These static exports come from the complete 12-run workflow. They demonstrate software output and do not validate a diagnosis.

### SAMURAI fitted copy-number profile

[Open the source qDNAseq segment PDF](assets/full_tutorial/prjna754199_samurai_ddlps1b_segment_plot.pdf).

![SAMURAI qDNAseq profile for the public DDLPS_1b archive alias](assets/full_tutorial/prjna754199_samurai_ddlps1b_segment_plot.png)

### Boundary-refinement statistics

[Open the source 12-sample refinement summary](assets/full_tutorial/prjna754199_refinement_summary.csv).

![Counts of refined, retained, and poor-resolution boundaries](assets/full_tutorial/prjna754199_refinement_summary.png)

### CNA-only research interpretation

[Open the source research-use classifier report](assets/full_tutorial/prjna754199_cna_interpretation.pdf).

![CNA-only research interpretation for DDLPS_1b](assets/full_tutorial/prjna754199_cna_interpretation.png)

## Primary sources

- [NCBI BioProject PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/PRJNA754199)
- [ENA PRJNA754199 archive record](https://www.ebi.ac.uk/ena/browser/view/PRJNA754199)
- [Przybyl et al., PLOS ONE (2022)](https://doi.org/10.1371/journal.pone.0262272)

## Research use

OncoTracer is not a standalone diagnostic system or medical device. This tutorial must not be used by itself to diagnose disease, choose treatment, establish prognosis, or report a clinical result.
