# Set up your own data

[Install OncoTracer](installation.md), then open the local browser setup below.
Replace example paths with your own.

## 1. Configure interactively (recommended)

```bash
oncotracer web
```

Open the complete local URL printed in the terminal. The page runs on the same
computer as OncoTracer; **Browse** lists that computer's folders.
Keep the terminal open while using the page.

1. **Choose ONT or Illumina first.** Then browse to your FASTQ folder and discover
   samples. For ONT, select the parent of `barcode01`, `barcode02`, etc.; all FASTQ
   batches within each barcode form one sample. A nonbarcoded ligation folder
   forms one sample containing all its FASTQs. Choose one sequencing run at a time.
   Illumina detects pairs such as `sampleA_R1.fastq.gz` and `sampleA_R2.fastq.gz`;
   consolidate multiple lanes per library first.
2. **Select samples and assign names/types.** Review file counts and paths.
   `unclassified` starts excluded. Choose Cancer, Normal/control, or a custom type
   with an explicit study/control role. These are your labels, never inferred
   diagnoses. Controls are analyzed independently, without pooling or subtraction.
3. **Choose features and resources.** Select copy-number analysis (CNA), or ONT
   methylation/both. Review detected CPUs, available RAM and GPUs, then choose
   threads. CNA uses CPU. Illumina uses qDNAseq; ONT offers ichorCNA (500 kb) or
   solid-biopsy qDNAseq with selectable bins. ONT controls require qDNAseq and at
   least one study sample. CNA interpretation reports are optional.
4. **For methylation, choose the classifier deliberately.** MARLIN targets leukemia
   research; Sturgeon targets CNS-tumor research. Supply modified-base BAMs or raw
   POD5, plus the [required tool/model resources](configuration/methylation.md).
   FASTQs alone cannot supply methylation calls.
5. **Choose tools, reference and project.** Select Conda for the installation above.
   Choose automatic reference download, reuse of a prepared reference, or local
   indexing. Browse to a project parent and enter a new folder name, for example
   `illumina-study` or `ont-study`. Existing configurations are protected.
6. **Save, review and run.** Click **Save configuration and check**. Review the
   saved YAML and resolve errors, then click **Run analysis** to prepare missing
   tools and begin. Progress and the log appear on the page. Saving alone starts
   no analysis or genome download; you can also run later with the commands below.

Settings go to `PROJECT/config/run.yml`, with sample metadata alongside them.
Illumina also gets `config/samplesheet.csv`. Study/control map to tumor/normal in
analysis tables; custom labels are retained.

### Terminal wizard alternative

`setup` is interactive by default; no `--interactive` flag is needed. Run
`oncotracer setup` to answer every question, starting with the platform, or prefill
paths and platform using a matching example:

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
need no quotes; quote shell paths containing spaces. For individual file prompts,
replace `--input-folder PATH` with `--manual`; this also allows intentional
R1-only single-end Illumina input. A project cannot mix paired-end and single-end
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
Repeat `run` to resume; leave `--force` off.

To change settings, edit `config/run.yml` and check again. Setup never overwrites
an existing configuration. `--run` starts after setup without the final menu;
`setup --project PATH --run` resumes saved settings.

## Optional: reuse prepared genome indexes

With the default download choice, run downloads prebuilt indexes into
`PROJECT/reference/`: about 8.0 GiB for Illumina or 9.7 GiB for ONT.
Completed downloads are reused.

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
