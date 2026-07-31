<a id="three-sample-hcc1143-public-cohort"></a>

# QuickStart Example 2: three-sample HCC1143 public cohort

This example downloads and analyzes three paired-end HCC1143 low-pass whole-genome sequencing libraries: six FASTQ files totaling about **1.08 GiB**. Complete [QuickStart Example 1](quick_start.md) first.

The commands below use Docker. On an HPC system configured with Singularity or Apptainer, replace `--docker` with `--singularity`. See [Installation](installation.md) and the maintained [Docker image](https://hub.docker.com/r/carlosfarkas/oncotracer).

This cohort is a software example. It is not a matched tumor/normal study and should not be used to infer treatment effects.

## Public data

The libraries come from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

| OncoTracer sample | Treatment | Run accession | Files used |
| --- | --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` | paired R1/R2 FASTQs |
| `HCC1143_BEZ235` | 1 uM BEZ235 | `SRR7085655` | paired R1/R2 FASTQs |
| `HCC1143_TRAMETINIB` | 1 uM Trametinib | `SRR7085657` | paired R1/R2 FASTQs |

All rows are labeled `TUMOR`. DMSO is a treatment control, not a normal genome. Exact URLs, byte counts, and checksums are stored in [`examples/hcc1143_lpwgs/manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/hcc1143_lpwgs/manifest.tsv).

The sample table is:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

## Estimated time for this analysis

Plan for at least 40 GiB of free working space, 16 CPU cores, and at least 80 GiB of addressable RAM. The first uncached run downloads hg38 and creates a BWA index, which commonly takes **30–60 minutes** before alignment begins.

## 1. Clone the repository and create the data folder

```bash
# Clone OncoTracer into the example location.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository and create the public-data folder.
cd /home/student/oncotracer
mkdir -p /home/student/oncotracer/test/public/hcc1143_lpwgs
```

Skip the `git clone` command when the repository already exists.

<a id="2-download-the-six-fastq-files"></a>

## 2. Download the six FASTQ files

Each command can continue a partial download because it uses `--continue-at -`.

```bash
# Download HCC1143_DMSO read 1.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_DMSO_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz

# Download HCC1143_DMSO read 2.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_DMSO_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz

# Download HCC1143_BEZ235 read 1.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_BEZ235_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz

# Download HCC1143_BEZ235 read 2.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_BEZ235_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz

# Download HCC1143_TRAMETINIB read 1.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_TRAMETINIB_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz

# Download HCC1143_TRAMETINIB read 2.
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_TRAMETINIB_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz
```

## 3. Verify the FASTQs and copy the sample table

```bash
# Enter the downloaded FASTQ directory.
cd /home/student/oncotracer/test/public/hcc1143_lpwgs

# Check all six MD5 values.
md5sum -c /home/student/oncotracer/examples/hcc1143_lpwgs/checksums.md5

# Check that all six gzip files are complete.
gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \
  HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \
  HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz

# Copy the exact three-row sample table used by this example.
cp /home/student/oncotracer/examples/hcc1143_lpwgs/samples.csv \
  /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv

# Display the copied table.
sed -n '1,10p' /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv
```

`md5sum` should print `OK` six times. `gzip -t` is silent when all files are valid.

## 4. Generate the YAML automatically

`--auto_params` matches the three sample names to their R1/R2 files, validates the FASTQs, writes `illumina.samplesheet.csv`, and writes `illumina.auto.yml`. It does not start alignment or CNA calling.

```bash
# Enter the repository.
cd /home/student/oncotracer

# Generate the Illumina YAML and samplesheet from the FASTQs and sample table.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs
```

Inspect the generated files:

```bash
# Display the generated YAML.
sed -n '1,120p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml

# Display the generated R1/R2 samplesheet.
sed -n '1,10p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.samplesheet.csv

# Display the sample counts and file hashes.
cat /home/student/oncotracer/test/configs/hcc1143_lpwgs/auto_params_manifest.tsv
```

The samplesheet must contain three data rows, each with one R1 and one R2 path.

## 5. Optional wiring check

```bash
# Check the generated workflow connections without running the analysis tools.
nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs_stub
```

## 6. Run the analysis

```bash
# Run the generated HCC1143 configuration with Docker and resume support.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

Keep the terminal open until Nextflow returns to the prompt.

## 7. Check the outputs

```bash
# List the three aligned BAM files.
find /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/alignment \
  -maxdepth 1 -type f -name '*.bam' -print

# Confirm that each sample appears in the qDNAseq segment table.
grep -Fq HCC1143_DMSO \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_DMSO: found'
grep -Fq HCC1143_BEZ235 \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_BEZ235: found'
grep -Fq HCC1143_TRAMETINIB \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_TRAMETINIB: found'

# Display the workflow summary.
sed -n '1,40p' \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/06_workflow_summary/workflow_summary.txt
```

Important outputs include:

- `01_samurai_illumina/alignment/*.bam`;
- `01_samurai_illumina/qdnaseq/all_segments.seg`;
- `03_cna_codification/cna_events.tsv`;
- `04_cna_custom_plots/cna_per_sample_pages.pdf`;
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`;
- `06_workflow_summary/workflow_summary.txt`.

## Resume an interrupted run

Use the same YAML and work directory:

```bash
# Return to the repository.
cd /home/student/oncotracer

# Resume the existing HCC1143 analysis.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

## Limitations

This example verifies multi-sample execution. It is not a matched tumor/normal design, does not establish treatment causality, and is not a clinical validation study.

The [Other Example Run: six tumors and four controls](six_tumor_four_control.md) shows a local panel-of-normals command pattern. Its `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` FASTQs are not included or downloaded; that page requires the user to supply all 20 files.
