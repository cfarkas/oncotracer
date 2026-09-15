# Set up your own data

[Install OncoTracer](installation.md), then start the terminal wizard below.
`setup` is interactive by default; no `--interactive` flag is needed.
Replace example paths with your own. At prompts, type paths without quotes;
in shell commands, quote paths containing spaces. Enter accepts a displayed default.

## 1. Configure interactively (recommended)

Choose your platform and a new project folder:

### Illumina

```bash
oncotracer setup --project /work/illumina-study --mode illumina \
  --input-folder /data/illumina
```

The folder can contain multiple libraries, for example `sampleA_R1.fastq.gz` and
`sampleA_R2.fastq.gz`. Setup detects names and pairs for review. Consolidate multiple sequencing lanes
per library first; use `--manual` for intentional R1-only single-end inputs.

### ONT

```bash
oncotracer setup --project /work/ont-study --mode ont \
  --input-folder /data/run/fastq_pass
```

Use the parent containing `barcode01`, `barcode02`, etc. Completed FASTQ batches
within each selected barcode form one sample.

### Follow the questions

Omit `--input-folder` to enter the folder interactively; omit `--mode` to review
the detected platform. Supplied flags prefill their answers.

1. **Review detected inputs.** Setup lists samples and FASTQ counts. Select the
   numbered samples to include. `unclassified` is excluded by default. Check
   Illumina pairs; a run cannot mix paired-end and single-end libraries.
2. **Name and describe samples.** Confirm each name and choose `cancer`, `control`
   or `other`. For `other`, supply a label and explicitly choose its study/control
   analysis role. Labels describe your samples; they are never inferred diagnoses.
   Controls are analyzed independently, without pooling or subtraction. Study/control
   map to tumor/normal in analysis tables; custom labels are retained.
3. **Choose analysis and reports.** `cna` means copy-number analysis. ONT also offers
   methylation or both; these require [additional inputs and tools](configuration/methylation.md).
   FASTQ alone cannot supply methylation calls. Optional CNA interpretation reports
   are a separate choice.
4. **Review resources.** Setup detects usable CPUs, available RAM and NVIDIA GPU model/memory,
   then asks for worker threads. Accept its suggestion or enter a number such as
   `4`. CNA uses CPU; GPU selection applies to supported methylation steps.
5. **Choose CNA settings.** Illumina uses qDNAseq. ONT offers ichorCNA or
   solid-biopsy qDNAseq. ONT controls require qDNAseq and at least one study sample;
   review this caller choice before continuing. Bin size is selectable for qDNAseq;
   ichorCNA uses 500 kb.
6. **Choose backend and reference.** Select `conda` for the installation above.
   Choose `download` for automatic prebuilt hg38 indexes, `reuse` for an existing
   reference, or `build` for local indexing.
7. **Review and finish.** Setup saves and checks the configuration, then offers
   `run` or `save` (default). Choose `run` to prepare tools and begin immediately,
   or `save` to use the commands below. Saving starts no analysis or genome download.

Settings go to `PROJECT/config/run.yml`, with sample metadata alongside them.
Illumina also gets `config/samplesheet.csv`. For individual input-path prompts,
replace `--input-folder PATH` with `--manual`.

## 2. Check and run your platform

If you chose `save`, use the matching block. Resolve check errors before running.

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

`--config` selects saved settings; `--backend conda` selects installed tools.
For [containers](containers.md), use `--backend docker` or `--backend singularity`.

A successful run prints `OncoTracer native analysis completed:`. Open
`results/06_workflow_summary/workflow_summary.txt` in your project first.
Repeat `run` to resume; leave `--force` off.

To change settings, edit `config/run.yml` and check again. Setup never overwrites
an existing configuration. `--run` starts after setup without the final menu;
`setup --project PATH --run` resumes saved settings.

## Optional: reuse prepared genome indexes

With the default download choice, run downloads prebuilt indexes into
`PROJECT/reference/`: about 8.0 GiB for Illumina or 9.7 GiB for ONT.
Completed downloads are reused.

Suppose your prepared reference lives at `/data/shared-reference`, with files
under `references/samurai_hg38/`. Choose `reuse` in the wizard and enter that path,
or prefill it using either complete example for a **new** project:

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

Finish the wizard, then run directly or check/run that project's `config/run.yml`.
The reference must contain the prepared genome, indexes and manifests.
Its `references/samurai_hg38` folder is also accepted. Illumina needs BWA indexes;
ONT needs minimap2; sharing between platforms requires both.

`--hg38_build` **without a path** selects automatic download. Scripted setup also
uses automatic download when neither reference flag is supplied. To build locally,
replace `--hg38_build /data/shared-reference` with `--build_reference`.
Choose one option; local indexing needs more RAM, disk and time.
See [reference details](reference_indexes.md).

## Optional: scripted setup without prompts

`--non-interactive` disables questions, uses flags and defaults, and errors when a
required answer is missing. It is useful in scripts. Omit it for interactive setup.

### Illumina

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-scripted --mode illumina --analysis cna \
  --sample-name sampleA \
  --fastq-1 /data/illumina/sampleA_R1.fastq.gz \
  --fastq-2 /data/illumina/sampleA_R2.fastq.gz --threads 4
```

Omit `--fastq-2` for single-end reads in this non-interactive command.

### ONT

```bash
oncotracer setup --non-interactive \
  --project /work/ont-scripted --mode ont --analysis cna \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01,barcode02 --sample-names sampleA,sampleB --threads 4
```

Check and run using the scripted project's `config/run.yml`.

## Illumina: multiple libraries

Replace paths and names, then paste this block. Each row is one library. `cat >`
overwrites the named CSV; choose a new filename if it already exists.

```bash
mkdir -p "/data/illumina"
cat > "/data/illumina/samplesheet.csv" <<'CSV'
sample,fastq_1,fastq_2,status
sampleA,"/data/illumina/sampleA_R1.fastq.gz","/data/illumina/sampleA_R2.fastq.gz",tumor
sampleB,"/data/illumina/sampleB_R1.fastq.gz","/data/illumina/sampleB_R2.fastq.gz",tumor
CSV
```

Each file field is one existing path; leave `fastq_2` empty for single-end libraries.
The flags below supply every answer:

```bash
oncotracer setup --project /work/illumina-batch --mode illumina --analysis cna \
  --samplesheet /data/illumina/samplesheet.csv --threads 4
oncotracer check --config /work/illumina-batch/config/run.yml
oncotracer run --backend conda --config /work/illumina-batch/config/run.yml
```

Results stay separate per sample.
[Batch setup](auto_params.md) can generate CSVs from filenames and configure ONT
tumor/normal groups.
