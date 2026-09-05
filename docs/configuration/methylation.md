# ONT methylation: leukemia or CNS classification

Choose **MARLIN** for leukemia research or **Sturgeon** for CNS-tumor research. You must select the appropriate classifier; OncoTracer does not decide the disease family for you. Predictions need review alongside the other laboratory findings.

This guide uses the new setup workflow from [current source](../installation.md#current-source-for-the-new-setup-workflow). The v2.0.0 executable supports the older POD5 route; see the [detailed resource reference](methylation_reference.md).

## What you need

You need matching FASTQs, methylation input, and an installed classifier:

| Input | Why it is needed |
| --- | --- |
| FASTQs organized by barcode | Select which read IDs belong to each sample |
| Modified-base BAMs **or** raw POD5 files | Supply methylation information |
| Dorado, Modkit, and samtools | Align reads and extract methylation |
| MARLIN or Sturgeon tools and model files | Compare the methylation pattern with known classes |

A **modified-base BAM** is a BAM containing methylation calls in its `MM` and `ML` tags. If MinKNOW saved these, reuse them: this avoids basecalling the signal again. An ordinary BAM without these tags, or FASTQ alone, is insufficient. POD5 is raw signal and needs compatible Dorado basecalling and modification models.

Use completed files from a stopped run, or a separate snapshot of completed batches. Do not use files MinKNOW is still writing, duplicate batches, or different basecalls of the same reads. Select barcodes explicitly. Include `unclassified` only when you can justify assigning those reads to a sample; do not pool it across patients.

## 1. Prepare the tools once

Use the Conda backend for OncoTracer. Optional methylation tools and classifier assets are installed separately; `oncotracer install --conda` does not install them. Their links and expected filenames are in [local resources](methylation_reference.md#obtain-the-optional-resources).

Have the paths to the classifier model, probe BED, and executables ready. For MARLIN, also locate its feature-order `.RData` and class-annotation `.xlsx` files. A **probe** is a genomic site the classifier knows how to use. Setup records file checksums automatically, so you do not need to type hashes into YAML.

## 2. Create a leukemia project using existing BAMs

Replace the paths, barcode, and sample name below with yours:

```bash
oncotracer setup \
  --project /work/leukemia-study \
  --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names sampleA \
  --classifier marlin \
  --modbam /data/run/bam_pass \
  --cpu --threads 8
```

Setup asks only for the remaining tool and model paths. It saves them with explanations in `/work/leukemia-study/config/run.yml`.

| Flag | Meaning |
| --- | --- |
| `--analysis methylation` | Run methylation only; use `both` to also request copy-number analysis |
| `--reads-folder` | Parent of the selected barcode FASTQ folder |
| `--barcodes` / `--sample-names` | Which barcode to use and the name for its results |
| `--classifier marlin` | Use the leukemia classifier |
| `--modbam` | Existing BAM file or directory, aligned or unaligned; calls are reused and reads aligned to hg38 on CPU |
| `--cpu` | Keep methylation tools on CPU, including MARLIN |
| `--threads 8` | Request eight CPU worker threads |

For CNS research, select `--classifier sturgeon` and provide its resources. Setup asks you to confirm that you obtained and accepted the applicable Sturgeon license.

## If you only have raw POD5

Use `--pod5-dir /data/run/pod5_pass` in place of `--modbam`. Setup will also ask for the Dorado basecalling and matching 5mCG/5hmCG model directories. Keep the matching FASTQ input to define sample membership.

CPU basecalling can take days even for a small number of long raw signals. Use existing modified-base BAMs when available. `--gpu` allows GPU basecalling and MARLIN inference; choose `--cpu` if that GPU is busy with live sequencing. Modkit and Sturgeon use CPU.

## 3. Check and run

```bash
oncotracer check --config /work/leukemia-study/config/run.yml
oncotracer run --backend conda \
  --config /work/leukemia-study/config/run.yml --cpu
```

`check` reports missing paths or settings without starting analysis. It does not test the biological quality of the data. During the run, MARLIN's R/Python dependencies are checked before read processing. The first analysis may download the hg38 reference into your project's reference cache.

You can reuse tool and model settings for another project with `setup --resources /work/leukemia-study/config/run.yml`; supply the new sample paths separately. For all explicit resource flags, use `oncotracer setup --help`.

## Read the result

Open `results/07_methylation/methylation_status.json` first. Each sample has a status and paths to any outputs:

| Status | What it means | Next step |
| --- | --- | --- |
| `complete` | The requested classifier produced output | Review scores and data quality; completion alone does not make the call reliable |
| `no_cpg_modifications` | No usable modified-CpG calls were found | Check the modified-base calls, human alignment, and read yield |
| `no_classifier_probes` | CpG calls exist, but none overlap the supplied MARLIN probes | Check hg38 coordinates and usable coverage; no leukemia prediction was made |
| `failed` | A tool, resource, or input check failed | Read the error and the logs in `07_methylation/logs/` |

For MARLIN, inspect `covered_classifier_probes`. Total read count alone does not establish sufficient classifier coverage. A missing prediction is not evidence that the sample is normal.

If you requested both branches, the summary records methylation and copy-number outcomes separately. Successful outputs are kept even when another requested branch fails. [Detailed processing and resource reference](methylation_reference.md).
