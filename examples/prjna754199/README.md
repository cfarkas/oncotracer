# PRJNA754199 full public-archive tutorial input

This opt-in example downloads and processes every FASTQ currently returned for
[NCBI BioProject PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/754199): 12
Illumina HiSeq 2500, single-end, 36 bp plasma cfDNA libraries. The files contain
266,097,582 reads and occupy 6,171,900,300 compressed bytes (about 5.75 GiB).

The associated publication is Przybyl et al., *Detection of MDM2 amplification by
shallow whole genome sequencing of cell-free DNA of patients with dedifferentiated
liposarcoma*, PLOS ONE (2022),
[doi:10.1371/journal.pone.0262272](https://doi.org/10.1371/journal.pone.0262272).

## Public archive scope is not the full publication cohort

The publication describes 41 plasma specimens from 15 patients: 10 serial specimens
from four patients with DDLPS/WDLPS and 31 specimens from 11 patients with other
soft-tissue tumors. On 2026-07-15, the ENA read-run report for `PRJNA754199` returned
12 public runs, not 41. This example therefore means **the entire currently public
BioProject archive (12 runs)**, not every specimen discussed in the article.

`DDLPS_*` and `WDLPS_*` are submitter-provided public sample aliases. They are retained
so the archive, generated outputs, and publication can be cross-referenced; an alias is
not an independently verified diagnosis. The generated SAMURAI `status` is `tumor` for
all rows because these are patient-cohort condition libraries. It does not assert that
active tumor or detectable circulating tumor DNA was present at a collection time.

See [`manifest.tsv`](manifest.tsv) for every BioSample, experiment, run, byte count,
MD5 checksum, and immutable HTTPS FASTQ path. [`samples.csv`](samples.csv) is the
explicit sample-to-condition table used by Automatic Setup. See
[`PROVENANCE.md`](PROVENANCE.md) for the archive query and interpretation boundaries.

## Requirements

For download-only preparation: Linux, Python 3, `curl`, `md5sum`, and `gzip`.
`aria2c` is optional and accelerates resumable downloads when available.

For analysis: Java 17+, Nextflow, Python 3, samtools, BWA, minimap2, pigz, curl or
wget, and one supported runtime: Docker, Singularity/Apptainer, or Conda. Plan for at least 150 GiB of free working space;
Use at least 16 CPU cores and 80 GiB of addressable RAM; the pinned BWA task
alone requests 72 GB.
Actual time and storage depend on the executor, filesystem, cache state, and runtime.

## Follow the complete tutorial

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) is the
primary route. Its main path is deliberately short: prepare the software, run one
validated download command, use **Automatic Setup from a Reads Folder** to generate the
12-sample samplesheet and YAML, run the stub and real workflows, invoke one exact-output
verifier, and review the CNA and clinician-facing research reports.

## Run the archive through Nextflow

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) explains
these commands and their outputs in detail. From a fresh clone, use the literal
commands below; no shell runner is required.

```bash
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer
cd /home/student/oncotracer

nextflow run /home/student/oncotracer/main.nf --make_prjna754199 \
  --test_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/prjna754199_download

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

nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199_stub

nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199 \
  -resume
```

The first Nextflow command validates the pinned 12-row manifest, downloads each
FASTQ with restart support, checks exact bytes and MD5, runs `gzip -t`, and
then stops. A complete file is reused when that same command is repeated.
Automatic Setup writes single-end sample rows with an empty `fastq_2` field.
The stub command checks wiring, and the final command performs the real
analysis. For an HPC configured with Apptainer/Singularity, change only
`--docker` to `--singularity` on the stub and real Nextflow commands.

After a completed analysis, rerun the exact checks without repeating any workflow task:

```bash
python3 examples/prjna754199/verify_outputs.py --outdir test/runs/prjna754199
```

To use a different project root, replace every
`/home/student/oncotracer/test` path above with the same absolute project
path. For example, the download begins with:

```bash
nextflow run /home/student/oncotracer/main.nf --make_prjna754199 \
  --test_root /absolute/path/to/oncotracer-prjna754199 \
  -work-dir /absolute/path/to/oncotracer-prjna754199/work/prjna754199_download
```

## Generated layout

The default paths are below the repository's ignored `test/` directory:

```text
test/
├── public/prjna754199/
│   ├── DDLPS_1a.fastq.gz
│   ├── ...
│   ├── WDLPS_3.fastq.gz
│   ├── manifest.tsv
│   └── samples.csv
├── configs/prjna754199/
│   ├── auto_params_manifest.tsv
│   ├── illumina.samplesheet.csv
│   └── illumina.auto.yml
├── references/samurai_hg38/
├── work/
│   ├── prjna754199/
│   ├── prjna754199_auto_params/
│   ├── prjna754199_download/
│   └── prjna754199_stub/
└── runs/prjna754199/
```

Downloaded reads, BAMs, references, Nextflow work, and full output directories are not
committed to Git.

## Reanalysis is not a reproduction of the publication method

The publication used a GRCh37/hg19 Plasma-Seq Z-score workflow with approximately
28 kb variable-mappability windows and a healthy-donor reference. This example uses
OncoTracer's current Illumina route: hg38, SAMURAI/qDNAseq at 100 kb, BAM-supported
boundary refinement, CNA codification, visualization, and an optional research-use CNA
interpretation layer. Results and thresholds are therefore not directly interchangeable
with the published Plasma-Seq calls.

The classifier context is fixed to `sarcoma` from the study design, not chosen after
examining the output. No pathology concordance table is supplied. Classifier labels,
driver-region summaries, literature links, and MDM2-region signals are hypotheses for
research review; none is a diagnosis, a clinical validation, or evidence of treatment
actionability without orthogonal confirmation.
