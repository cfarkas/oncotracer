<a id="quick-start"></a>

# QuickStart 1: Illumina and ONT

Run a complete native analysis using one public Illumina library and one ONT
library. The approximately 225 MB download is small; reference files, tools and
results need additional space. This is copy-number analysis, not methylation:
FASTQ files do not contain methylation calls.

[Install OncoTracer](installation.md) first. These examples use the same
`setup`, `check` and `run` commands as your own samples.

## 1. Download the reads

Replace `/path/to/my/analyses_dir/` with an existing directory you can write to.
`$PWD` means that directory. Keep using the same directory in each block.

```bash
cd /path/to/my/analyses_dir/
mkdir -p oncotracer-quickstart1/input/illumina \
  oncotracer-quickstart1/input/fastq_pass/barcode01

curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz

md5sum -c <<'MD5'
4c96d551152694b3893ea98b7781a3ae  oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz
1b20d9eb98f755244f6383ea1354bd40  oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz
55a3984cb0334aa4cb0a38255cb71c06  oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz
MD5
```

`--output` names the downloaded file; `--continue-at -` resumes an interrupted
download. Continue only when all three checksum lines say `OK`.

The ONT public library is placed in `barcode01` as a sample folder; it is not
a new demultiplexing step.

## 2. Set up both projects

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/illumina" \
  --reference-root "$PWD/oncotracer-quickstart1/reference" \
  --mode illumina --analysis cna --sample-name ERR12341627 \
  --fastq-1 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz" \
  --fastq-2 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz" \
  --threads 4

oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/ont" \
  --reference-root "$PWD/oncotracer-quickstart1/reference" \
  --mode ont --analysis cna \
  --reads-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --barcodes barcode01 --sample-names DRR165691 \
  --threads 4
```

`--project` separates the configurations and results. Both projects use the same
`--reference-root` so they can reuse genome files. `--threads 4` requests four
CPU workers. `--non-interactive` requires the inputs as flags instead of prompts.

## Optional: reuse prepared genome indexes

If you already have an OncoTracer reference directory, use its absolute path
for `--reference-root` in both setup commands. Otherwise you can
[download prebuilt hg38 indexes](reference_indexes.md) using `--mode both` and
`--lpwgs-root` set to the reference directory above.

Skip this step to let the normal run prepare missing reference files. Do not
run a separate genome-build script. Prebuilt indexes save construction time,
but alignment still needs RAM; check [requirements](installation.md#requirements).

## 3. Check and run

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer check --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"

oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
```

Resolve any check errors before running. `--config` selects saved settings;
`--backend conda` selects the installed analysis tools. Docker and Apptainer
use `--backend docker` and `--backend singularity`, after
[installing that backend](containers.md).

## 4. Read the results

Each successful run prints `OncoTracer native analysis completed:`. Look under
`oncotracer-quickstart1/illumina/results/` and `oncotracer-quickstart1/ont/results/`:

| File | Purpose |
| --- | --- |
| `06_workflow_summary/workflow_summary.txt` | Completion status |
| `03_cna_codification/cna_events.tsv` | Copy-number changes |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Plots |
| `.oncotracer-native/trace.tsv` | Recorded commands |

To resume, repeat the same `run` command after fixing the reported error. Do not
repeat setup or add `--force` for a normal resume.

Next: [your own samples](setup.md) or [QuickStart 2](public_cohort.md).
