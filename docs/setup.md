# Set up your own data

[Install OncoTracer](installation.md), then open the local browser setup below.
Replace example paths with your own.

## 1. Configure interactively (recommended)

```bash
oncotracer web
```

Open the local URL printed in the terminal. **Browse** lists folders on
the OncoTracer computer. Keep that terminal open.

1. **Choose ONT or Illumina first.** Then browse to your FASTQ folder and discover
   samples. ONT barcode folders each form one sample containing all FASTQ batches.
   A nonbarcoded ligation folder forms one sample. Select one sequencing run.
   Illumina detects pairs such as `sampleA_R1.fastq.gz` and `sampleA_R2.fastq.gz`;
   consolidate multiple lanes per library first.
2. **Name and label selected samples.** Review paths and counts; `unclassified`
   starts excluded. Choose Cancer, Normal/control, or a custom label with an explicit
   study/control role. Labels are never inferred diagnoses. Controls are analyzed
   independently, without pooling or subtraction.
3. **Choose analysis and resources.** Select CNA, or ONT methylation/both. Review
   detected CPUs, RAM and GPUs, then choose threads. CNA uses CPU. Illumina uses qDNAseq; ONT offers ichorCNA (500 kb) or
   solid-biopsy qDNAseq with selectable bins. ONT controls require qDNAseq and at
   least one study sample. Optional CNA reports offer local catalog text, local
   language-model drafts from that catalog, or literature plus local models.
   Catalog drafts use no publication searches and identify their catalog source. Literature retrieves public
   papers using feature/context terms. GISTIC cohort analysis is a separate choice.
   Selecting GISTIC requires at least two samples and a successful GISTIC result.
4. **For methylation, choose the classifier deliberately.** MARLIN targets leukemia
   research; Sturgeon targets CNS-tumor research. Supply modified-base BAMs or raw
   POD5, plus the [required tool/model resources](configuration/methylation.md).
   FASTQs alone cannot supply methylation calls.
5. **Choose tools, reference and project.** Select Conda, then reference download,
   reuse, or local indexing. Choose a project parent and new folder name, such as
   `illumina-study`. Existing configurations are protected.
6. **Save, review and run.** Click **Save configuration and check**. Review the
   saved YAML and resolve errors, then click **Run analysis** to prepare missing
   tools and begin. Progress and the log appear on the page. After completion,
   click **Open results** for the summary, CNA tables, plots and report links.
   Saving starts no analysis or genome download.

Settings go to `PROJECT/config/run.yml`, with sample metadata alongside them.
Illumina also gets `config/samplesheet.csv`. Study/control map to tumor/normal in
analysis tables; custom labels are retained.

### Terminal wizard alternative

`setup` is interactive by default. Run `oncotracer setup` to start with the
platform question, or prefill answers:

### Illumina

```bash
oncotracer setup --project /work/illumina-study --mode illumina \
  --input-folder /data/illumina
```

### ONT

```bash
oncotracer setup --project /work/ont-study --mode ont \
  --input-folder /data/run/fastq_pass
```

Review numbered samples, then answer the name, `cancer`/`control`/`other`, analysis,
threads, caller, reports, backend and reference questions. Enter accepts a displayed
default. The final choice is `run` or `save` (default). Paths entered at prompts
need no quotes; quote shell paths containing spaces. For individual file prompts or single-end Illumina,
use `--manual` instead of `--input-folder PATH`. A project cannot mix paired-end and single-end
Illumina libraries.

## 2. Check and run your platform

If you saved without running, use the matching block. Resolve check errors first.

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
Repeat `run` to resume without `--force`.

To change settings, edit `config/run.yml` and check again. Setup never overwrites
an existing configuration. `--run` starts after setup without the final menu;
`setup --project PATH --run` resumes saved settings.

## Optional: reuse prepared genome indexes

Run downloads prebuilt indexes into `PROJECT/reference/`: about 8.0 GiB for
Illumina or 9.7 GiB for ONT. Completed downloads are reused.

Suppose your prepared reference lives at `/data/shared-reference`, with files
under `references/samurai_hg38/`. Choose reference reuse in the browser or terminal
wizard and enter that path, or prefill it using either complete example for a
**new** project:

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

Finish setup, then run that project's `config/run.yml`. The reference needs the
prepared genome, indexes and manifests. Its `references/samurai_hg38` folder is
also accepted. Illumina needs BWA indexes;
ONT needs minimap2; sharing between platforms requires both.

`--hg38_build` **without a path** selects automatic download. Scripted setup also
uses automatic download when neither reference flag is supplied. To build locally,
replace `--hg38_build /data/shared-reference` with `--build_reference`.
Choose one option; local indexing needs more RAM, disk and time.
See [reference details](reference_indexes.md).

## Optional: scripted setup without prompts

`--non-interactive` uses flags/defaults without questions and errors on missing
required answers. Omit it for interactive setup.

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

Check and run the saved `config/run.yml`.

## Illumina: multiple libraries

Each row is one library. Replace the paths; `cat >` overwrites its CSV.

```bash
mkdir -p "/data/illumina"
cat > "/data/illumina/samplesheet.csv" <<'CSV'
sample,fastq_1,fastq_2,status
sampleA,"/data/illumina/sampleA_R1.fastq.gz","/data/illumina/sampleA_R2.fastq.gz",tumor
sampleB,"/data/illumina/sampleB_R1.fastq.gz","/data/illumina/sampleB_R2.fastq.gz",tumor
CSV
```

Use existing paths; leave `fastq_2` empty for single-end libraries:

```bash
oncotracer setup --project /work/illumina-batch --mode illumina --analysis cna \
  --samplesheet /data/illumina/samplesheet.csv --threads 4
oncotracer check --config /work/illumina-batch/config/run.yml
oncotracer run --backend conda --config /work/illumina-batch/config/run.yml
```

Results stay separate per sample.
[Batch setup](auto_params.md) can generate CSVs from filenames and configure ONT
tumor/normal groups.
