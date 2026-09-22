# Set up your own data

[Install once](installation.md), then configure and run from your browser.
Try the [demo](browser_demo.md), or use terminal examples below.
[Headless servers](headless.md) covers scripted runs and remote browsers through SSH.

## 1. Configure interactively (recommended)

```bash
oncotracer setup
```

Keep the terminal open. Open the complete printed **127.0.0.1:8888** URL,
including its `#` session code, if the browser does not open automatically.
**Browse folders** shows files on the computer running OncoTracer.

1. **Choose Illumina or ONT**, then browse to FASTQs. Illumina detects R1/R2
   pairs; consolidate lanes first. Each ONT barcode includes all its batches;
   a nonbarcoded ligation folder is one sample.
2. **Assign samples** to Normal or Cancer using cards or dropdowns. Rename them
   if needed. Unassigned samples are excluded. Controls are analyzed independently,
   without pooling or subtraction; custom tags require a study/control role.
3. **Review settings.** Choose threads and a caller. QDNAseq defaults to **100 kb**;
   ONT ichorCNA uses **500 kb**. ONT controls require QDNAseq and a study sample.
   GISTIC needs at least two assigned samples. CNA uses CPU.
4. **Select tools and a project folder.** Choose your installed Conda or Docker
   backend. Keep automatic reference download for your first run.
5. Click **Save configuration and check**, resolve any check errors, then
   **Run analysis**. Follow progress and open results from the same page.

Settings are saved in `PROJECT/config/run.yml`; Illumina also gets
`config/samplesheet.csv`. Existing configurations are protected.
**Stop analysis** offers to keep the project or remove its folder after path confirmation.

**Terminal equivalent:** ask the same setup questions without a browser, then
validate and run:

```bash
oncotracer setup --terminal --backend conda --run
```

### Optional analyses

Enable [variants](variants.md) to choose Fresh/FFPE and compatible callers.
ONT [methylation](configuration/methylation.md) requires modBAMs or raw POD5 and
classifier resources; FASTQs alone lack methylation calls. Docker methylation
is unavailable. Reports and [paper panels](paper_report.md) are optional.

<details markdown="1">
<summary>Prefer terminal questions?</summary>

Add `--terminal`; Enter accepts the displayed default. Finish with `save` or `run`.
For example, Enter chooses 100 kb here:

```text
CNA bin size (kb) (1/5/10/15/30/50/100/500/1000) [100]:
```
Use `--manual` for individual files or single-end Illumina. A project cannot mix
single-end and paired-end Illumina libraries.

### Illumina

```bash
oncotracer setup --terminal --project /work/illumina-study --mode illumina \
  --input-folder /data/illumina
```

### ONT

```bash
oncotracer setup --terminal --project /work/ont-study --mode ont \
  --input-folder /data/run/fastq_pass
```

</details>

## 2. Check and run your platform

**Already clicked Run analysis? Skip these commands.** They are alternatives for
running a saved project from a terminal. Resolve check errors before running.

<details markdown="1">
<summary>Show terminal check/run commands</summary>

### Illumina

```bash
oncotracer check --config /work/illumina-study/config/run.yml
oncotracer run --backend conda --config /work/illumina-study/config/run.yml
```

### ONT

```bash
oncotracer check --config /work/ont-study/config/run.yml
oncotracer run --backend conda --config /work/ont-study/config/run.yml
```

These examples use Conda. Docker projects keep their saved image; use
`--backend docker`. See [execution backends](containers.md) for other options.

</details>

## 3. Review results and resume

Open the results dashboard from the browser. Start with
`results/06_workflow_summary/workflow_summary.txt` for completion status,
then review plots and event tables. See [outputs](outputs.md).

To resume a saved project:

```bash
oncotracer setup --project /absolute/path/to/my-study --run
```

Matching alignment and CNA results are reused. Do not add `--force` for a normal
resume. See [running and resuming](running.md) for details.

## Optional: reuse prepared genome indexes

Run downloads prebuilt indexes under `PROJECT/reference/`: about 8.0 GiB for
Illumina or 9.7 GiB for ONT. Completed downloads are reused.
Choose **Reuse a prepared OncoTracer reference** to share an existing build.

<details markdown="1">
<summary>Show setup commands using an existing reference</summary>

Use these for a new project. Replace `/data/shared-reference` with your prepared
reference parent or its `references/samurai_hg38` folder.

### Illumina with an existing reference

```bash
oncotracer setup --project /work/illumina-reuse --mode illumina \
  --input-folder /data/illumina --hg38_build /data/shared-reference
```

### ONT with an existing reference

```bash
oncotracer setup --project /work/ont-reuse --mode ont \
  --input-folder /data/run/fastq_pass --hg38_build /data/shared-reference
```

The browser prefills these paths; assign samples, save/check, then run.
Illumina needs BWA indexes; ONT needs minimap2.

**Terminal equivalents** (questions followed by validation and execution):

```bash
oncotracer setup --terminal --run --backend conda \
  --project /work/illumina-reuse --mode illumina \
  --input-folder /data/illumina --hg38_build /data/shared-reference
```

```bash
oncotracer setup --terminal --run --backend conda \
  --project /work/ont-reuse --mode ont \
  --input-folder /data/run/fastq_pass --hg38_build /data/shared-reference
```

</details>

`--hg38_build` without a path selects automatic download. Replace it with
`--build_reference` to build locally using more RAM, disk and time.
See [reference details](reference_indexes.md).

## Optional: scripted setup without prompts

`--non-interactive` uses supplied flags/defaults and stops on missing required
answers. For browser setup, use the earlier browser commands; for folder-selection
questions, use `--terminal`.

<details markdown="1">
<summary>Show one-library Illumina and multi-barcode ONT examples</summary>

### Illumina

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-scripted --mode illumina --analysis cna --backend conda \
  --sample-name sampleA \
  --fastq-1 /data/illumina/sampleA_R1.fastq.gz \
  --fastq-2 /data/illumina/sampleA_R2.fastq.gz --threads 4
oncotracer check --config /work/illumina-scripted/config/run.yml
oncotracer run --backend conda --config /work/illumina-scripted/config/run.yml
```

Omit `--fastq-2` for single-end reads.

### ONT

```bash
oncotracer setup --non-interactive \
  --project /work/ont-scripted --mode ont --analysis cna --backend conda \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01,barcode02 --sample-names sampleA,sampleB --threads 4
oncotracer check --config /work/ont-scripted/config/run.yml
oncotracer run --backend conda --config /work/ont-scripted/config/run.yml
```

Check and run the saved `config/run.yml`.

</details>

## Illumina: multiple libraries

<details markdown="1">
<summary>Use an explicit samplesheet for filenames the browser cannot pair</summary>


Each row is one library. Replace the paths; `cat >` overwrites its CSV.

```bash
mkdir -p "/data/illumina"
cat > "/data/illumina/samplesheet.csv" <<'CSV'
sample,fastq_1,fastq_2,status
sampleA,"/data/illumina/sampleA_R1.fastq.gz","/data/illumina/sampleA_R2.fastq.gz",tumor
sampleB,"/data/illumina/sampleB_R1.fastq.gz","/data/illumina/sampleB_R2.fastq.gz",tumor
CSV
```

This terminal example saves the supplied sample settings, then checks/runs.
Use existing paths; leave `fastq_2` empty for single-end libraries:

```bash
oncotracer setup --project /work/illumina-batch --mode illumina --analysis cna \
  --samplesheet /data/illumina/samplesheet.csv --threads 4 --backend conda
oncotracer check --config /work/illumina-batch/config/run.yml
oncotracer run --backend conda --config /work/illumina-batch/config/run.yml
```

Results stay separate per sample.
[Batch setup](auto_params.md) can generate CSVs from filenames and configure ONT
tumor/normal groups.

</details>
