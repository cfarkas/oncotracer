# ONT methylation: leukemia or CNS classification

Choose **MARLIN** for leukemia or **Sturgeon** for CNS-tumor research. Select the classifier explicitly; review predictions alongside other laboratory findings.

[Install first](../installation.md). Browser and terminal routes are below; see
[resources](methylation_reference.md) and [SSH/headless](../headless.md) for details.

## What you need

You need matching FASTQs, methylation input, and an installed classifier:

| Input | Why it is needed |
| --- | --- |
| FASTQs organized by barcode | Select which read IDs belong to each sample |
| Modified-base BAMs **or** raw POD5 files | Supply methylation information |
| Dorado, Modkit, and samtools | Align reads and extract methylation |
| MARLIN or Sturgeon tools and model files | Compare the methylation pattern with known classes |

**Modified-base BAMs** contain `MM`/`ML` tags; reusing them avoids basecalling. Ordinary BAMs or FASTQs alone are insufficient. Raw POD5 needs compatible Dorado basecalling and modification models.

Use completed files or a snapshot. Exclude files MinKNOW is still writing, duplicates and alternate basecalls of the same reads. Select barcodes explicitly; include `unclassified` only with a justified sample assignment, never pooled across patients.

## 1. Prepare the tools once

Use Conda. Methylation tools and classifier assets are separate from `oncotracer install --conda`; see [local resources](methylation_reference.md#obtain-the-optional-resources).

Locate executables, model and probe BED; MARLIN also needs feature-order `.RData` and class-annotation `.xlsx` files. Setup records checksums. Probes are genomic sites recognized by the classifier.

## 2. Link inputs in the browser

Run `oncotracer setup`, choose **Oxford Nanopore**, then **Fresh or FFPE** from
specimen records. **Browse run** fills matching
`fastq_pass`, POD5 and `bam_pass` paths. You can also browse each path separately,
including a single barcode folder or a nonbarcoded ligation FASTQ folder.
Assign and name the samples, then choose **Methylation classification** or **CNA and methylation**.

Choose the linked POD5 or modified-base BAM input, then **Sturgeon · CNS tumours** or
**MARLIN · leukemias**. Browse to the installed tools and model files, or reuse a
resource YAML. Modkit extracts CpG methylation for either classifier. Barcode FASTQ
read IDs keep each sample separate when the signal folder is shared. Save, check,
and click **Run analysis**.

### Terminal example: leukemia using existing BAMs

Replace the paths, barcode, and sample name below with yours:

```bash
oncotracer setup --terminal \
  --project /work/leukemia-study --backend conda \
  --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names sampleA \
  --classifier marlin \
  --modbam /data/run/bam_pass \
  --cpu --threads 8
```

This asks for remaining tool/model paths in the terminal. Check/run the saved
`/work/leukemia-study/config/run.yml` below. `--no-browser` still starts a server.

For unattended reuse, replace `--terminal` with
`--non-interactive --resources /work/previous-study/config/run.yml` and choose a new project. The resource YAML must contain
all tools and assets for the selected classifier; missing settings stop setup.
The explicit FASTQ, barcode and methylation input flags select the new sample.

| Flag | Meaning |
| --- | --- |
| `--analysis methylation` | Run methylation only; use `both` to also request copy-number analysis |
| `--reads-folder` | Parent of the selected barcode FASTQ folder |
| `--barcodes` / `--sample-names` | Which barcode to use and the name for its results |
| `--classifier marlin` | Use the leukemia classifier |
| `--modbam` | Existing BAM file or directory, aligned or unaligned; calls are reused and reads aligned to hg38 on CPU |
| `--cpu` | Keep methylation tools on CPU, including MARLIN |
| `--threads 8` | Request eight CPU worker threads |

For CNS, the explicit terminal counterpart is below. Confirm the applicable
Sturgeon license when prompted; setup does not grant a license.

### Terminal / headless: CNS using existing BAMs

```bash
oncotracer setup --terminal --run --project /work/cns-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier sturgeon --modbam /data/run/bam_pass --cpu --threads 8
```

## If you only have raw POD5

These complete terminal alternatives ask for the classifier resources plus
matching Dorado basecalling/5mCG/5hmCG models, then check and run. FASTQs still
define sample membership. Use a new project for each alternative.

### Terminal / headless: leukemia using POD5

```bash
oncotracer setup --terminal --run --project /work/leukemia-pod5-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier marlin --pod5-dir /data/run/pod5_pass --cpu --threads 8
```

### Terminal / headless: CNS using POD5

```bash
oncotracer setup --terminal --run --project /work/cns-pod5-study \
  --backend conda --mode ont --analysis methylation \
  --reads-folder /data/run/fastq_pass --barcodes barcode01 --sample-names sampleA \
  --classifier sturgeon --pod5-dir /data/run/pod5_pass --cpu --threads 8
```

CPU basecalling can take days. `--gpu` enables GPU basecalling/MARLIN; keep `--cpu` when the GPU is busy. Modkit and Sturgeon use CPU.

## 3. Check and run

```bash
oncotracer check --config /work/leukemia-study/config/run.yml
oncotracer run --backend conda \
  --config /work/leukemia-study/config/run.yml --cpu
```

`check` verifies paths/settings, not biological quality. Run checks MARLIN dependencies before processing and may download hg38 into the project reference cache.

Reuse tool/model settings with `setup --resources /work/leukemia-study/config/run.yml`; supply new sample paths. See `oncotracer setup --help` for resource flags.

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
