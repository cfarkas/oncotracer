# ONT methylation: leukemia or CNS classification

Version 6 defaults to **Rockfish**. The simplest route is [one sample table with Docker](../launch.md#ont-with-methylation). Tools and models are prepared automatically; CNS samples require Sturgeon license acknowledgement. The advanced examples below explicitly select **Modkit**.

Choose **MARLIN** for leukemia or **Sturgeon** for CNS-tumor research.

[Install first](../installation.md). See [resources](methylation_reference.md) and [SSH/headless](../headless.md).

Finished an ONT copy-number run? Follow the [post-ONT CpG video guide](../post_ont_cpg.md) to reuse original inputs and assign separate MARLIN/Sturgeon projects.

## What you need

Provide matching FASTQs and raw POD5 or modified-base BAMs. Docker prepares the tools and classifier resources; [the first-run guide](../first_run.md) separates your inputs from automatic preparation.

**Modified-base BAMs** contain `MM`/`ML` tags; reusing them avoids basecalling. Ordinary BAMs or FASTQs alone are insufficient. Raw POD5 needs compatible Dorado basecalling and modification models.

Use completed files; exclude duplicates and alternate basecalls. Keep one intended library per named folder. Select legacy barcodes explicitly; include `unclassified` only with a justified assignment, never pooled across patients.

## If methylation was not recorded

Matching raw POD5 can still support Rockfish calling. If only FASTQs or ordinary
BAMs remain, turn methylation off; those files cannot reconstruct the lost signal.
Matching reads without `MM`/`ML` calls are reported as `methylation_unavailable`,
with no classifier prediction. This differs from an input with incorrect read
IDs and from measured CpGs with no usable classifier probes. Other analyses
and usable samples continue.

For scripted launch, add a `methylation` column with `false` for excluded rows.
For advanced saved YAMLs, set `methylation_excluded_samples: sampleB,sampleC`.
Terminal setup accepts `--methylation-exclude sampleB,sampleC`. Tissue-routing TSVs
also accept a `methylation` column; retain a row for every selected sample and
leave excluded signal paths blank.

## 1. Prepare the tools once

With Docker, run `oncotracer --interactive --backend docker` and leave managed resources selected. Setup prepares pinned downloads and reuses your cache. For advanced Conda/host installations, tools and classifier assets remain separate; see [local resources](methylation_reference.md#obtain-the-optional-resources).

## 2. Link inputs in the browser

```bash
oncotracer setup
```

Choose **Oxford Nanopore**, then **Fresh or FFPE** from specimen records. Select
[named library folders](ont.md), or **Browse run** for a legacy MinKNOW run.
Named libraries automatically link `pod5_pass`; saved/explicit modified BAMs stay
selected. Failed siblings are excluded. Legacy barcode/ligation layouts also work.
Assign and name the samples, then choose **Methylation classification** or **CNA and methylation**.

Choose the linked POD5 or modified-base BAM input. For methylation-only analysis,
drag included samples into the MARLIN or Sturgeon classifier group according to
documented clinical context, then select **Classifier group to save and run**.
Other groups and unassigned samples are excluded, not queued; save each classifier
to a separate project. **CNA and methylation** uses one chosen classifier for the samples enabled under
**Methylation for each sample**. Uncheck other cancers or samples without signal;
they remain in CNA and selected variants. **Continue without methylation** turns
off the methylation branch for the whole project. For mixed CNS/blood routing,
use [sample-table launch](../launch.md#ont-with-methylation).

Docker prepares these paths automatically. For advanced external installations,
browse to the tools/model files or reuse a resource YAML. Modkit
extracts CpG methylation for either classifier. Selected libraries and their
FASTQ read IDs determine which signal reads belong to each sample. Save, check, and click
**Run analysis** for the last saved project.

### Terminal example: leukemia using existing BAMs

Replace paths, barcode and sample name:

```bash
oncotracer setup --methylation-caller modkit --terminal \
  --project /work/leukemia-study --backend conda \
  --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names sampleA \
  --classifier marlin \
  --modbam /data/run/bam_pass \
  --cpu --threads 8
```

This asks for remaining tool/model paths, then saves
`/work/leukemia-study/config/run.yml`.

For unattended reuse, use a complete resource YAML from a previous project.
Missing tools or assets stop setup. Check and run automatically:

```bash
oncotracer setup --methylation-caller modkit --non-interactive --run \
  --project /work/new-leukemia-study --backend conda \
  --resources /work/previous-study/config/run.yml \
  --mode ont --analysis methylation --classifier marlin \
  --reads-folder /data/new-run/fastq_pass \
  --barcodes barcode01 --sample-names sampleB \
  --modbam /data/new-run/bam_pass --cpu --threads 8
```

### Terminal / headless: CNS using existing BAMs

```bash
oncotracer setup --methylation-caller modkit --terminal --run --project /work/cns-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier sturgeon --modbam /data/run/bam_pass --cpu --threads 8
```

## If you only have raw POD5

These advanced Modkit alternatives ask for matching Dorado/modification models and classifier resources. FASTQs define sample membership. Use a separate project for each alternative.

### Terminal / headless: leukemia using POD5

```bash
oncotracer setup --methylation-caller modkit --terminal --run --project /work/leukemia-pod5-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier marlin --pod5-dir /data/run/pod5_pass --cpu --threads 8
```

### Terminal / headless: CNS using POD5

```bash
oncotracer setup --methylation-caller modkit --terminal --run --project /work/cns-pod5-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier sturgeon --pod5-dir /data/run/pod5_pass --cpu --threads 8
```

CPU basecalling can be slow for large inputs. `--gpu` enables GPU basecalling/MARLIN; keep `--cpu` when the GPU is busy. Modkit and Sturgeon use CPU.

## 3. Check and run

For the saved leukemia-BAM example above (the `--run` alternatives already execute):

```bash
oncotracer check --config /work/leukemia-study/config/run.yml
oncotracer run --backend conda \
  --config /work/leukemia-study/config/run.yml --cpu
```

`check` verifies paths/settings, not biological quality. Run checks MARLIN dependencies before processing and may download hg38 into the project reference cache.

Reuse tool/model settings with `setup --resources /work/leukemia-study/config/run.yml`; supply new sample paths. See `oncotracer setup --help` for resource flags.

## Read the result

Within your project, open `results/07_methylation/methylation_status.json` first. Each sample has a status and paths to any outputs:

| Status | What it means | Next step |
| --- | --- | --- |
| `complete` | The requested classifier produced output | Review scores and data quality; completion alone does not make the call reliable |
| `no_cpg_modifications` | No usable modified-CpG calls were found | Check the modified-base calls, human alignment, and read yield |
| `no_classifier_probes` | CpG calls exist, but none overlap the supplied MARLIN probes | Check hg38 coordinates and usable coverage; no leukemia prediction was made |
| `methylation_unavailable` | Matching reads lack recorded MM/ML calls | Supply POD5 or turn methylation off; no prediction is made |
| `not_applicable` | Methylation was excluded or tissue is unsupported | Other selected analyses remain enabled |
| `failed` | A tool, resource, or input check failed | Read the error and the logs in `07_methylation/logs/` |

For MARLIN, inspect `covered_classifier_probes`. A missing prediction does not establish that a sample is normal.

If you requested both branches, the summary records methylation and copy-number outcomes separately. Successful outputs are kept even when another requested branch fails. [Detailed processing and resource reference](methylation_reference.md).
