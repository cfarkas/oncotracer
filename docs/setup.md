# Set up your own data

[Install OncoTracer](installation.md), then open the local browser setup below.

## 1. Configure interactively (recommended)

```bash
oncotracer setup
```

Open **127.0.0.1:8888** using the complete terminal URL, including its `#` session code. **Browse folders**
lists folders on the OncoTracer computer and selecting one discovers its samples.
Keep that terminal open. `oncotracer web` opens the same interface; `--port 8889`
selects another port, and `--no-browser` only prints the URL.

1. **Choose ONT or Illumina first**, then browse to your FASTQ folder.
   Each ONT barcode is one sample containing all its batches; a nonbarcoded ligation
   folder is one sample. Illumina detects R1/R2 pairs; consolidate lanes first.
2. **Assign and name samples.** Drag detected cards into **Normal** or **Cancer**,
   or use the dropdowns. Edit names as needed. Unassigned samples stay excluded.
   Normal/Cancer capitalization is normalized. Custom tags need an explicit
   study/control role. Controls are analyzed independently, without pooling or subtraction.
3. **Choose settings.** Review CPUs, RAM and GPUs, then choose threads. CNA uses CPU.
   QDNAseq defaults to **100 kb**; ONT ichorCNA uses **500 kb**. ONT controls require
   QDNAseq and at least one study sample. Optional CNA reports offer catalog text,
   local language-model drafts, or literature plus local models. GISTIC is disabled
   until two samples are assigned; reports work for one sample.
4. **For ONT methylation**, choose MARLIN for leukemia research or Sturgeon for CNS-tumor
   research. Supply modified-base BAMs or raw POD5 and the
   [required resources](configuration/methylation.md). FASTQs alone lack methylation calls.
5. **Choose tools, reference and project.** Select Conda, reference download/reuse/build,
   and a project parent with a new folder name. Existing configurations are protected.
6. **Save, review and run.** Click **Save configuration and check**, then **Run analysis**.
   Follow progress and open results here. **Stop analysis** cancels the run, then offers
   **Keep project** (default) or **Remove project folder** with explicit path confirmation.
   Inputs and shared download caches remain.

Settings go to `PROJECT/config/run.yml`, with sample metadata alongside them.
Illumina also gets `config/samplesheet.csv`. Study/control map to tumor/normal in
analysis tables; custom labels are retained.

### Terminal wizard alternative

`setup` opens the browser by default. Use `--terminal` for the terminal wizard,
starting with the platform question, or prefill answers:

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

Review numbered samples, then answer the name, `cancer`/`normal`/`control`/`other`, analysis,
threads, caller, reports, backend and reference questions. Enter accepts a displayed
default. Finish with `run` or `save` (default). Paths entered at prompts
need no quotes; quote shell paths containing spaces. For individual file prompts or single-end Illumina,
use `--manual` instead of `--input-folder PATH`. A project cannot mix paired-end and single-end
Illumina libraries. For example, at this prompt press Enter to use **100 kb**:

```text
CNA bin size (kb) (1/5/10/15/30/50/100/500/1000) [100]:
```

For a single sample, the terminal wizard explains that GISTIC is unavailable and
continues to save your configuration, including any requested CNA reports.

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

The browser prefills these paths. Assign samples, save and check, then run.
Indexes require BWA for Illumina or minimap2 for ONT.
`references/samurai_hg38` is also accepted.

`--hg38_build` **without a path** selects automatic download. Scripted setup also
uses automatic download when neither reference flag is supplied. To build locally,
replace `--hg38_build /data/shared-reference` with `--build_reference`.
Choose one option; local indexing needs more RAM, disk and time.
See [reference details](reference_indexes.md).

## Optional: scripted setup without prompts

`--non-interactive` uses flags/defaults without questions and errors on missing
required answers. Omit it for browser setup, or use `--terminal` for terminal prompts.

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
