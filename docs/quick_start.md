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

## 2. Set up interactively (recommended)

Choose Illumina or ONT, or follow both sections using their separate project folders.
Setup asks for missing answers and saves `config/run.yml`. It does not start analysis
or download genomes. `--threads 4` requests four CPU workers.

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --threads 4
```

Example answers (replace `/path/to/my/analyses_dir/` with your actual directory):

```text
Analysis (--analysis; cna=copy-number) (cna/methylation/both) [cna]: cna
Sample name (--sample-name): ERR12341627
Read 1 FASTQ (--fastq-1): /path/to/my/analyses_dir/oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz
Read 2 FASTQ (--fastq-2; Enter for single-end): /path/to/my/analyses_dir/oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz
```

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --threads 4
```

Example answers:

```text
Analysis (--analysis; cna=copy-number) (cna/methylation/both) [cna]: cna
FASTQ parent folder (--reads-folder): /path/to/my/analyses_dir/oncotracer-quickstart1/input/fastq_pass
FASTQ folder: /path/to/my/analyses_dir/oncotracer-quickstart1/input/fastq_pass
Available folders: barcode01
Barcode folders to include, comma separated (--barcodes): barcode01
Sample names in the same order (--sample-names) [barcode01]: DRR165691
```

At prompts, enter actual paths without quotes; `$PWD` is expanded in shell commands,
not in your typed answers. Enter accepts the displayed default.

For scripts, [supply answers as flags with `--non-interactive`](setup.md#optional-scripted-setup-without-prompts).
That flag skips questions, accepts defaults and errors on missing required inputs.
Leave it off for interactive setup. To change saved settings, edit `config/run.yml`;
setup never overwrites it.

## Optional: reuse prepared genome indexes

By default, run downloads prebuilt indexes automatically: about 8.0 GiB for Illumina
and 9.7 GiB for ONT, under each project's `reference/` folder. Completed downloads
are reused. **No reference flag is needed** for the steps above.

If a prepared reference already exists at `/data/shared-reference`, use one of these
commands **instead of the corresponding step 2 command**, before creating its config:

### Illumina with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --threads 4 --hg38_build /data/shared-reference
```

### ONT with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --threads 4 --hg38_build /data/shared-reference
```

Answer the same prompts from step 2, then continue with step 3. Replace
`/data/shared-reference` with your existing reference parent; its
`references/samurai_hg38` folder also works. It must contain the prepared genome,
indexes and manifests. Illumina needs BWA indexes; ONT needs minimap2. One reference
shared between platforms needs both.

`--hg38_build` **without a path** means automatic download, just like omitting it.
To build locally, replace `--hg38_build /data/shared-reference` in either example
with `--build_reference`. Run then downloads source files as needed and builds
indexes on CPU. Choose one option. Local indexing needs more RAM, temporary disk
and time. See [reference details](reference_indexes.md) and
[RAM requirements](installation.md#requirements).

## 3. Check and run

Use the block matching the project you configured. Resolve any check errors before
running. `--config` selects saved settings; `--backend conda` selects installed
analysis tools.

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
```

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
```

Docker and Apptainer use `--backend docker` and `--backend singularity`, after
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
