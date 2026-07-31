<a id="three-sample-hcc1143-public-cohort"></a>

# QuickStart Example 2: three-sample HCC1143 public cohort

This optional example runs three paired-end low-pass whole-genome sequencing libraries—six physical FASTQ files—through the Illumina workflow. Complete [QuickStart Example 1](quick_start.md) first. The read download is approximately **1.08 GiB**.

!!! important "Run OncoTracer through Nextflow"
    Every OncoTracer configuration, workflow test, and analysis command below
    begins with `nextflow run`. Ordinary file download and checksum commands
    are shown separately. The `--docker` option tells Nextflow which runtime
    to manage. Do not type `docker run`, `docker exec`, `apptainer run`,
    `apptainer exec`, `singularity run`, or `singularity exec`.

!!! warning "Workflow demonstration, not a biological conclusion"
    This cohort is a reproducible software example. Do not infer treatment effects or clinical meaning from this three-library demonstration.

## Public data and provenance

The libraries come from the HCC1143 triple-negative breast-cancer cell line in public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

| OncoTracer sample | Treatment | Run accession | Files used |
| --- | --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` | paired R1/R2 FASTQs |
| `HCC1143_BEZ235` | 1 uM BEZ235 | `SRR7085655` | paired R1/R2 FASTQs |
| `HCC1143_TRAMETINIB` | 1 uM Trametinib | `SRR7085657` | paired R1/R2 FASTQs |

All three samples are labeled `TUMOR`. DMSO is the experimental treatment control, but its DNA still comes from a cancer cell line; it is not a matched normal genome. Tiny unpaired singleton files exposed by ENA are deliberately excluded.

Exact URLs, byte counts, and MD5 checksums are recorded in [`manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/hcc1143_lpwgs/manifest.tsv). The six selected files total 1,158,812,143 bytes.

## Requirements

Use Linux with Java 17 or newer, Nextflow, and Docker. Plan for at least 40 GiB
of free working space, 16 CPU cores, and at least 80 GiB of addressable RAM;
the pinned BWA task alone requests 72 GB. The first analysis also downloads
the hg38 reference and creates its BWA index, which can take 30–60 minutes.

## 1. Clone the repository and create the data folder

If the repository already exists at `/home/student/oncotracer`, skip the `git clone` line.

```bash
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer
cd /home/student/oncotracer
mkdir -p /home/student/oncotracer/test/public/hcc1143_lpwgs
```

## 2. Download the six FASTQ files

Copy these commands exactly. A repeated command continues a partial download because it includes `--continue-at -`.

```bash
curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_DMSO_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz

curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_DMSO_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz

curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_BEZ235_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz

curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_BEZ235_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz

curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_TRAMETINIB_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz

curl --fail --location --continue-at - \
  --output /home/student/oncotracer/test/public/hcc1143_lpwgs/HCC1143_TRAMETINIB_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz
```

## 3. Verify the downloads and copy the sample table

The checksum command should print `OK` six times. `gzip -t` is silent when all six files are intact.

```bash
cd /home/student/oncotracer/test/public/hcc1143_lpwgs
md5sum -c /home/student/oncotracer/examples/hcc1143_lpwgs/checksums.md5
gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \
  HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \
  HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz
cp /home/student/oncotracer/examples/hcc1143_lpwgs/samples.csv \
  /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv
sed -n '1,10p' /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv
```

The displayed table must be:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

## 4. Ask Nextflow to generate the YAML and samplesheet

This command checks the FASTQ names and writes the run plan. It does not start alignment or CNA calling.

```bash
cd /home/student/oncotracer
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs
```

Inspect the two generated files:

```bash
sed -n '1,120p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml
sed -n '1,10p' /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.samplesheet.csv
```

The samplesheet must contain three data rows and each row must have an R1 and R2 path.

## 5. Optional fast wiring check

This starts a Nextflow stub run. It checks workflow wiring without performing the expensive analysis.

```bash
nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs_stub
```

## 6. Run the real analysis

This is the actual resumable command:

```bash
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

Nextflow downloads and starts the required containers. Keep this terminal open until the prompt returns.

On an HPC system configured for Apptainer/Singularity, change only `--docker` to `--singularity` in the **Nextflow command**. Do not run the container runtime yourself.

## 7. Check the outputs

These commands list the BAM files, check each sample name separately in the
segment table, and print the readable workflow summary:

```bash
find /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/alignment \
  -maxdepth 1 -type f -name '*.bam' -print
grep -Fq HCC1143_DMSO \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_DMSO: found'
grep -Fq HCC1143_BEZ235 \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_BEZ235: found'
grep -Fq HCC1143_TRAMETINIB \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/01_samurai_illumina/qdnaseq/all_segments.seg \
  && echo 'HCC1143_TRAMETINIB: found'
sed -n '1,40p' \
  /home/student/oncotracer/test/runs/hcc1143_lpwgs/06_workflow_summary/workflow_summary.txt
```

Important outputs include:

- three BAM files in `01_samurai_illumina/alignment/`;
- the three sample names in `01_samurai_illumina/qdnaseq/all_segments.seg`;
- `03_cna_codification/cna_events.tsv`;
- `04_cna_custom_plots/cna_per_sample_pages.pdf`;
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`; and
- `06_workflow_summary/workflow_summary.txt`.

## Resume an interrupted run

Do not create a new work directory. Return to the repository and repeat the exact real-analysis command:

```bash
cd /home/student/oncotracer
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

Valid downloads and unchanged completed tasks are reused.

## Generated layout

```text
/home/student/oncotracer/test/
├── public/hcc1143_lpwgs/       # six FASTQs plus samples.csv
├── configs/hcc1143_lpwgs/      # generated YAML and samplesheet
├── work/hcc1143_lpwgs/         # Nextflow resume cache
└── runs/hcc1143_lpwgs/         # final outputs
```

## Attribution and limitations

When presenting results, cite both the source study and OncoTracer as described in [Citation and Research Use](citation_research_use.md). Record the exact OncoTracer commit, container digest, caller, bin size, reference, and warnings alongside generated figures.

This small public cohort tests multi-sample execution and teaches input semantics. It is not a matched tumor/normal design, does not establish treatment causality, and is not a substitute for an appropriately powered biological or clinical study.

For the paired tumor/normal pattern requested by many local projects, continue
to [QuickStart Example 3: six tumors and four controls](six_tumor_four_control.md).
