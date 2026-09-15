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

Choose Illumina or ONT, or follow both using separate project folders.
The terminal wizard scans the supplied folder and asks you to review samples,
analysis settings, hardware, backend and reference choice.

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina"
```

1. Include the detected paired-end library; keep its name `ERR12341627`.
2. Choose sample type `cancer` and copy-number analysis (`cna`).
3. Review CPU/RAM/GPU information and choose threads, for example `4`, then keep
   100 kb bins for qDNAseq.
4. Leave optional CNA interpretation reports off, choose backend `conda` and
   reference `download`, then choose **save** to continue to step 3.

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass"
```

1. Include `barcode01` and name it `DRR165691`.
2. Choose sample type `cancer` and analysis `cna`.
3. Review hardware and choose threads, for example `4`, then caller `ichorcna`
   (500 kb bins).
4. Leave optional CNA interpretation reports off, choose `conda`, reference
   `download`, then **save**.

The sample type above is example metadata, not a diagnosis. Enter accepts a
shown default. At path prompts, use actual paths without quotes; `$PWD` expands
in shell commands, not typed answers.

Setup saves `config/run.yml` and sample metadata. **Save** starts no analysis or
genome download; **run** starts directly from the wizard instead of step 3.
Setup never overwrites a configuration; edit the YAML to change saved settings.

For scripts, [supply answers with `--non-interactive`](setup.md#optional-scripted-setup-without-prompts).
It skips questions, uses flags/defaults and errors on missing required inputs.
Omit it for the interactive wizard. [Your own samples](setup.md) shows control
roles, methylation options and manual input paths.

## Optional: reuse prepared genome indexes

With the default download choice, run downloads prebuilt indexes automatically: about 8.0 GiB for Illumina
and 9.7 GiB for ONT, under each project's `reference/` folder. Completed downloads
are reused. **No reference flag is needed** for the steps above.

If a prepared reference already exists at `/data/shared-reference`, use one of these
commands **instead of the corresponding step 2 command**, before creating its config:

### Illumina with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina" \
  --hg38_build /data/shared-reference
```

### ONT with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --hg38_build /data/shared-reference
```

Answer the same prompts from step 2, then continue with step 3. Replace
`/data/shared-reference` with your existing reference parent; its
`references/samurai_hg38` folder also works. It must contain the prepared genome,
indexes and manifests. Illumina needs BWA indexes; ONT needs minimap2. One reference
shared between platforms needs both.

`--hg38_build` **without a path** selects automatic download. In the wizard,
choosing `reuse` and entering the prepared reference path has the same effect
as supplying that path in these commands.
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
