<a id="three-sample-hcc1143-public-cohort"></a>

# QuickStart Example 2: three-sample HCC1143 public cohort

This example downloads and analyzes three paired-end LP-WGS libraries from the HCC1143 breast-cancer cell line: six public FASTQ files totaling approximately **1.08 GiB**.

Use `--docker` with [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer), or replace it with `--singularity` on a configured HPC system.

## Public data

| Sample | Treatment | Run accession | Layout |
| --- | --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` | paired R1/R2 |
| `HCC1143_BEZ235` | 1 uM BEZ235 | `SRR7085655` | paired R1/R2 |
| `HCC1143_TRAMETINIB` | 1 uM Trametinib | `SRR7085657` | paired R1/R2 |

The files come from [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with Ben-David et al., *Nature Communications* (2018). All three rows are labeled `TUMOR`; DMSO is a treatment control, not a matched normal genome.

Exact URLs, byte counts, and MD5 values are recorded in [`examples/hcc1143_lpwgs/manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/hcc1143_lpwgs/manifest.tsv).

## Estimated time for this analysis

Plan for at least **40 GiB** of working space, 16 CPU cores, and **80 GiB RAM**. The first uncached analysis downloads hg38 and builds its BWA index; indexing commonly takes **30–60 minutes** and the full workflow can take longer.

## 1. Clone the repository and create the data folder

```bash
# Clone OncoTracer into the example directory.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository.
cd /home/student/oncotracer

# Create the folder that will contain the six public FASTQs.
mkdir -p /home/student/oncotracer/test/public/hcc1143_lpwgs
```

Skip the clone command when the repository already exists.

## 2. Download the six FASTQ files

Each `curl` command can continue a partial download because it uses `--continue-at -`.

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

## 3. Verify the FASTQs and create the sample table

```bash
# Enter the FASTQ directory.
cd /home/student/oncotracer/test/public/hcc1143_lpwgs

# Check the six published MD5 values; each line should finish with OK.
md5sum -c /home/student/oncotracer/examples/hcc1143_lpwgs/checksums.md5

# Check gzip integrity; success produces no output.
gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \
  HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \
  HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz

# Create the sample table in Nano.
nano /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv
```

Paste exactly this content:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

```bash
# Display the saved sample table.
sed -n '1,10p' /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv
```

## 4. Generate the YAML automatically

`--auto_params` checks the FASTQ names and gzip files, writes the Illumina samplesheet and YAML, and then stops. It does not align reads or call CNAs.

```bash
# Return to the repository.
cd /home/student/oncotracer

# Generate the HCC1143 Illumina YAML and samplesheet.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs
```

```bash
# Inspect the generated YAML.
sed -n '1,120p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml

# Inspect the generated FASTQ-to-sample mapping.
sed -n '1,10p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.samplesheet.csv
```

The samplesheet should contain three data rows, each with an R1 and R2 path.

## 5. Run the analysis

```bash
# Optionally check the workflow wiring without running the scientific tools.
nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs_stub

# Run or resume the real HCC1143 analysis with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

On HPC, use the same commands with `--singularity` instead of `--docker`.

## 6. Check the outputs

```bash
# List the three aligned BAM files.
find /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/alignment \
  -maxdepth 1 -type f -name '*.bam' -print

# Confirm that HCC1143_DMSO appears in the segment table.
grep -Fq HCC1143_DMSO \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_DMSO: found'

# Confirm that HCC1143_BEZ235 appears in the segment table.
grep -Fq HCC1143_BEZ235 \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_BEZ235: found'

# Confirm that HCC1143_TRAMETINIB appears in the segment table.
grep -Fq HCC1143_TRAMETINIB \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_TRAMETINIB: found'

# Read the workflow summary.
sed -n '1,40p' \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/06_workflow_summary/workflow_summary.txt
```

Important outputs include `03_cna_codification/cna_events.tsv`, `04_cna_custom_plots/cna_per_sample_pages.pdf`, `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`, and `06_workflow_summary/workflow_summary.txt`.

## Resume an interrupted run

```bash
# Return to the repository.
cd /home/student/oncotracer

# Repeat the same command with the same YAML and work directory.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

This cohort is a software demonstration, not a matched tumor/normal study and not evidence of treatment causality.

For a six-tumor/four-control configuration template using files that are **not included** in the repository, see [Other Example Run](six_tumor_four_control.md).
