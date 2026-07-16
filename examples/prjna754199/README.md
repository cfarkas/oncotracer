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
16 CPU cores and 64 GiB RAM are sensible starting resources for the complete cohort.
Actual time and storage depend on the executor, filesystem, cache state, and runtime.

## Follow the complete tutorial

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) is the
primary route. Its main path is deliberately short: prepare the software, run one
validated download command, use **Automatic Setup from a Reads Folder** to generate the
12-sample samplesheet and YAML, run the stub and real workflows, invoke one exact-output
verifier, and review the CNA and clinician-facing research reports.

## Optional automated replay

From a fresh clone:

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
bash examples/prjna754199/run_example.sh --docker
```

After following the manual lesson, the runner can replay the same operations:

1. validates the pinned 12-row manifest;
2. downloads each FASTQ over HTTPS with restart support;
3. verifies exact compressed bytes, ENA MD5, and `gzip -t`;
4. runs OncoTracer `--auto_params` on the reads folder and explicit `samples.csv`;
5. verifies that Automatic Setup writes a blank `fastq_2` field for every single-end
   library and a qDNAseq 100 kb YAML with the classifier enabled in `sarcoma` context;
6. performs a Nextflow stub wiring check;
7. runs the real workflow with `-resume`;
8. calls [`verify_outputs.py`](verify_outputs.py), which checks the exact 12 manifest
   aliases in BAM, SAMURAI, refinement, classifier, fitted-plot, and clinician-report
   outputs, plus the remaining CNA, plot, and summary files.

Use another supported runtime by replacing `--docker` with `--singularity` or
`--conda`.

## Download or prepare without analysis

Download and validate all 12 files, then stop:

```bash
bash examples/prjna754199/run_example.sh --download-only
```

Also run Automatic Setup, print its samplesheet and YAML, write a provenance receipt,
and then stop:

```bash
bash examples/prjna754199/run_example.sh --prepare-only
```

Both operations are resumable. A complete file is reused only after its byte count,
MD5, and gzip stream all validate. A partial file remains available for the next run.
The download step also places `samples.csv` and a frozen manifest copy beside the reads,
so the folder is self-describing.

After a completed analysis, rerun the exact checks without repeating any workflow task:

```bash
python3 examples/prjna754199/verify_outputs.py --outdir test/runs/prjna754199
```

Set a separate analysis root when the repository filesystem is too small:

```bash
COHORT_ROOT=/absolute/path/to/oncotracer-prjna754199 \
  bash examples/prjna754199/run_example.sh --docker
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
│   ├── illumina.samplesheet.csv
│   ├── illumina.auto.yml
│   └── run_provenance.tsv
├── references/samurai_hg38/
├── work/
│   ├── prjna754199/
│   ├── prjna754199_auto_params/
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
