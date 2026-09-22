# Variant configuration reference

Start with the [browser variant guide](variants.md) or [ANNOVAR setup](annovar.md). This page retains the complete command examples, resource requirements and output contracts.

OncoTracer can run optional SNV/indel callers alongside CNA analysis, or from an explicit manifest of existing BAMs. Each caller keeps its own VCF and evidence table. This research feature does not establish clinical sensitivity, reliable germline genotypes at low coverage, or a validated somatic diagnosis.

## Browser, terminal or remote server

Each browser example below has a terminal-only alternative. Use **one route**
per new project. `setup --non-interactive` takes explicit input/sample flags and
writes the YAML; `check` reviews it and `run` starts analysis. `--input-folder`
belongs to interactive discovery, so the scripted examples use explicit files
or barcodes. `setup --terminal` instead asks questions in your terminal.

For a browser connected to a remote server, follow the [SSH tunnel guide](headless.md).
The remote variant form can start with `oncotracer setup --variants --no-browser`,
or `oncotracer setup --variant-config /data/variants.yml --no-browser` for BAMs.
These still use a browser; `--no-browser` only suppresses automatic launch.
There is no `--headless` flag.

Replace example paths with existing inputs/resources, activate your launcher,
and install the requested tools before running. Explicit backends below avoid
relying on the most recently installed backend.

## Choose a platform and specimen type

| Platform | Supported caller names | Meaning of the calls |
|---|---|---|
| Illumina | `mutect2` (default) | Tumor-only candidates; without a matched normal they are not confirmed somatic variants. |
| Illumina | `freebayes`, `bcftools` | Independent germline-style calls; tumor purity, copy number and sparse depth still affect interpretation. |
| ONT | `clair3` (default) | Germline-style calls using an explicitly selected compatible model, prepared on Run or supplied locally. |
| ONT | `clairs_to` | Tumor-only candidates using an explicitly selected compatible platform/model preset. |

See the developers’ [Clair3](https://github.com/HKU-BAL/Clair3) and [ClairS-TO](https://github.com/HKU-BAL/ClairS-TO) documentation for model compatibility and caller scope.

Preservation is a separate choice: `fresh` or `ffpe`. Use one preservation type per project. A sample explicitly labeled normal skips tumor-only callers; it can still use a compatible germline-style caller. Multiple callers remain separate; their agreement is not converted into a synthetic consensus genotype.

## Add calling during setup

In browser setup, enable **Add small-variant calling**, select preservation and platform-compatible callers, and select automatic model preparation or existing resources. This branch requires aligned reads, so choose CNA or CNA plus methylation; methylation-only analysis is not eligible. Supported execution backends are `conda`, `host` and `poetry`. Docker supports CNA plus variants from FASTQ; Docker methylation and the Singularity backend with variants remain unavailable.

For example, prefill an Illumina FFPE project:

```bash
oncotracer setup --project "$PWD/ffpe-study" --mode illumina --backend conda \
  --input-folder /data/illumina --variants \
  --variant-specimen-type ffpe --variant-callers mutect2
```

Replace `/data/illumina` with your FASTQ folder. Review the generated configuration before running. The selected backend must provide the tools; adding this option does not install callers or accept their licenses. Missing requested callers stop preflight rather than silently substituting another caller.

### Terminal only: the same Illumina FFPE project

Use this instead of the preceding browser command. This example explicitly keeps
FFPERASE enabled with **existing local** source/models and a compatible runtime.
For automatic source/model preparation, use the [licensed-download example](#automatic-ffperase-resources-at-run-time). The example selects one tumor library from the same FASTQ folder.

```bash
oncotracer setup --non-interactive --project "$PWD/ffpe-study" \
  --mode illumina --analysis cna --backend conda --threads 4 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz \
  --hg38_build --variants \
  --variant-specimen-type ffpe --variant-callers mutect2 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/variants" \
  --variant-ffperase required \
  --variant-ffperase-root /resources/nf-ffperase \
  --variant-ffperase-models /resources/ffperase-models \
  --variant-ffperase-prefix "$HOME/.local/share/oncotracer/optional-tools/ffperase" \
  --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/ffpe-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/ffpe-study/config/run.yml"
```

If you intentionally choose **Skip FFPERASE**, set `--variant-ffperase off` and
omit its source/model/runtime flags. To enable the browser's Varlociraptor
option, use `--variant-varlociraptor required --variant-varlociraptor-fdr 0.05`
in the setup command. [Assessment details](#additional-assessments-ffperase-and-varlociraptor)
explain these separate choices.

| Setup flag | Purpose |
|---|---|
| `--variant-tool-prefix PATH` | Existing environment containing the required executables in `bin/`. |
| `--variant-targets-bed PATH` | Optional BED intervals matching the reference assembly and contig names. |
| `--variant-clair3-model PATH` or `auto` | Existing compatible model directory, or prepare the selected profile on Run. |
| `--variant-ont-profile ID` | Exact catalog profile required with automatic Clair3 preparation; see [supported profiles](#automatic-ont-model-preparation). |
| `--variant-download-resources` | Prepare missing FFPERASE source/models on Run; the runtime must already be installed. |
| `--variant-accept-ffperase-license` | Explicit acknowledgment of the pinned FFPERASE terms before downloading its resources. |
| `--variant-clairsto-platform VALUE` | Installed ClairS-TO preset beginning `ont_`, matching the sequencing configuration. |
| `--variant-annovar auto` or `off` | Discover existing annotation resources, or skip annotation. |
| `--variant-annovar-dir PATH` / `--variant-annovar-db PATH` | Explicit existing ANNOVAR installation/database directories. |

### Run CNA and variants with Docker

Choose **Docker** under analysis tools in the browser and enter the **Docker image
tag or digest**. The following retained integration example uses the
20260921 image with existing resources. For new automatic model preparation use
`carlosfarkas/oncotracer:fastq-variants-20260922` consistently in setup and run,
as shown in the [current Docker guide](variants.md#run-cna-and-variants-with-docker).
The same selection can prefill the form:

```bash
oncotracer setup --project "$PWD/docker-study" --mode illumina \
  --input-folder /data/illumina --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --variants --variant-specimen-type fresh --variant-callers mutect2,bcftools
```

**Terminal only**, using the same Fresh Illumina caller/backend settings:

```bash
oncotracer setup --non-interactive --project "$PWD/docker-study" \
  --mode illumina --analysis cna --backend docker --threads 4 \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz \
  --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers mutect2,bcftools \
  --variant-ffperase off --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/docker-study/config/run.yml"
oncotracer run --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --config "$PWD/docker-study/config/run.yml"
```

Synthetic hg38 FASTQ integration checks recovered both inserted SNVs with each
Illumina caller (BCFtools, FreeBayes, Mutect2) and native ONT ClairS-TO. CNA,
Varlociraptor and existing ANNOVAR annotation completed. The low-depth FFPE case
correctly retained results with partial-failure status when FFPERASE was not
assessable. These are software integration checks; they do not establish clinical
accuracy. Clair3 is bundled and startup-tested; inference requires a model matching
the reads. The newer 20260922 image can prepare a selected catalog model on Run.

The project saves `execution_backend: docker` and `docker_image`; **Run analysis**
and `oncotracer setup --project "$PWD/docker-study" --run` reuse those selections.
The chosen image must include the requested native callers. The run checks their
availability inside that image before alignment begins. **Save and check** validates
input paths and configuration without starting Docker or analysis.

Container environments supply the caller and FFPERASE dependencies, so the browser
hides host environment prefixes and SIF selectors for Docker. Existing host prefix
settings in a reused YAML do not replace the image environments. Use host paths for
target BEDs, any existing Clair3 model or FFPERASE source/models, custom
Varlociraptor scenarios and licensed ANNOVAR resources; the runner mounts them at
the same absolute paths. It discovers available host ANNOVAR resources in `auto`
mode and records whether annotation was available. No annotation database is
downloaded. Use absolute paths (expand `~` before saving manual YAML); browser setup saves absolute paths. For an existing ClairS-TO or FFPERASE SIF, choose host or Conda.

This browser Docker route starts from FASTQ and includes CNA. The **Existing BAMs**
form below runs its standalone variant workflow on the host.

### Terminal only: ONT FASTQs

This example selects `barcode01` as one Fresh sample and runs Clair3 after CNA.
Replace the tool prefix and model directory with a compatible installation/model;
the example does not choose a chemistry or model on your behalf.

```bash
oncotracer setup --non-interactive --project "$PWD/ont-variant-study" \
  --mode ont --analysis cna --backend conda --threads 4 \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names TUMOR01 \
  --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers clair3 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/clair3" \
  --variant-clair3-model /resources/clair3-compatible-model \
  --variant-ffperase off --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/ont-variant-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/ont-variant-study/config/run.yml"
```

The explicit barcode/sample lists determine inclusion. Review the same choices
in browser setup if desired; resource autodetection never determines chemistry.

### Automatic ONT model preparation

In the browser, **Clair3 → Automatic** shows readable flow cell/basecaller profiles.
Select the profile matching the sequencing records. Neither preservation nor
finding a folder establishes that match. The supported Clair3 1.2.0 catalog is:

| Profile ID | Sequencing/basecaller profile |
| --- | --- |
| `r1041_e82_400bps_sup_v500` | R10.4.1 E8.2, Dorado SUP v5.0.0, 400 bps, 5 kHz |
| `r1041_e82_400bps_hac_v500` | R10.4.1 E8.2, Dorado HAC v5.0.0, 400 bps, 5 kHz |
| `r1041_e82_400bps_sup_v420` | R10.4.1 E8.2, Dorado SUP v4.2.0, 400 bps, 5 kHz |
| `r1041_e82_400bps_sup_v410` | R10.4.1 E8.2, Dorado SUP v4.1.0, 400 bps, 4 kHz |
| `r1041_e82_400bps_hac_v410` | R10.4.1 E8.2, Dorado HAC v4.1.0, 400 bps, 4 kHz |

The following example assumes the reads actually used **SUP v5.0.0 / 5 kHz**.
Change the profile if needed; for other chemistries/basecallers, use the existing
model route above and verify compatibility with the
[upstream Clair3 model documentation](https://github.com/HKU-BAL/Clair3/tree/v1.2.0#pre-trained-models).

**Browser**, prefilled for that profile:

```bash
oncotracer setup --project "$PWD/ont-auto-model-study" \
  --mode ont --backend conda --input-folder /data/run/fastq_pass --variants \
  --variant-specimen-type fresh --variant-callers clair3 \
  --variant-clair3-model auto --variant-ont-profile r1041_e82_400bps_sup_v500 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/clair3"
```

**Terminal only**, instead of that browser command:

```bash
oncotracer setup --non-interactive --project "$PWD/ont-auto-model-study" \
  --mode ont --analysis cna --backend conda --threads 4 \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names TUMOR01 --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers clair3 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/clair3" \
  --variant-clair3-model auto --variant-ont-profile r1041_e82_400bps_sup_v500 \
  --variant-ffperase off --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/ont-auto-model-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/ont-auto-model-study/config/run.yml"
```

Setup, **Save and check**, `check` and dry runs download no variant resources.
Run fetches the selected approximately 75 MB archive over HTTPS, verifies pinned
size/SHA-256 records and extracts the expected model files. The records are
OncoTracer's pinned verification catalog, not upstream-published checksum claims.
Verified files are reused under `OUTDIR/08_variants/resources/`; changed or
incomplete caches stop with an explanation instead of replacing custom resources.

For existing BAMs, add these keys to a complete ONT configuration:

```yaml
variant_clair3_model: auto
variant_ont_profile: r1041_e82_400bps_sup_v500
```

Save that complete configuration as `/data/ont-auto-model.yml`, with an ONT BAM
manifest, `variant_callers: clair3`, a compatible caller runtime and a new output
folder, then run:

```bash
oncotracer variants --config /data/ont-auto-model.yml --dry-run
oncotracer variants --config /data/ont-auto-model.yml --threads 4
```

**ClairS-TO** uses the selected preset's models from its installation/container.
The browser translates these supported preset IDs into readable choices:

| Preset ID | Flow cell/basecaller profile |
| --- | --- |
| `ont_r10_dorado_sup_5khz_ssrs` | R10.4.1, Dorado SUP v4.2.0, 5 kHz; synthetic + real training |
| `ont_r10_dorado_sup_5khz_ss` | R10.4.1, Dorado SUP v4.2.0, 5 kHz; synthetic training |
| `ont_r10_dorado_sup_5khz` | R10.4.1, Dorado SUP v4.2.0, 5 kHz; legacy preset |
| `ont_r10_dorado_sup_4khz` | R10.4.1, Dorado SUP v4.1.0, 4 kHz |
| `ont_r10_dorado_hac_4khz` | R10.4.1, Dorado HAC v4.1.0, 4 kHz |
| `ont_r10_guppy_sup_4khz` | R10.4.1, Guppy SUP v6.1.5, 4 kHz |
| `ont_r10_guppy_hac_5khz` | R10.4.1, Guppy HAC v6.5.7, 5 kHz |

The catalog matches the tested v0.4.4 distribution. Other installed presets remain
available under **Other installed preset (advanced)**. Use
`--variant-clairsto-platform ID` in terminal setup, or
`variant_clairsto_platform: ID` in YAML. The complete
[existing-SIF example](#use-an-existing-clairs-to-container) is retained below.

## Call from existing BAMs without rerunning CNA

Create a tab-separated manifest with one sample per BAM. Paths must identify existing files; normal samples are independent controls, not automatically paired normals.

```bash
cat > /data/variant_bams.tsv <<'TSV'
sample	bam	status
TUMOR01	/data/tumor01.bam	tumor
CONTROL01	/data/control01.bam	normal
TSV
```

Use a dedicated new output directory. Save this flat configuration as
`/data/variants.yml`, replacing its paths with your existing resources:

```yaml
mode: illumina
outdir: /data/variant-results
variant_bam_manifest: /data/variant_bams.tsv
variant_reference: /data/reference/hg38.fa
run_variants: true
variant_reference_build: hg38
variant_specimen_type: fresh
variant_callers: mutect2,bcftools
variant_annovar: auto
variant_tool_prefix: /data/environments/variant-tools
```

```bash
oncotracer variants --config /data/variants.yml --dry-run
oncotracer variants --config /data/variants.yml --threads 4
```

Relative manifest/reference paths resolve from the configuration folder; relative BAM paths resolve from the manifest folder. The dry run checks the plan without writes or tool execution. The standalone command uses the supplied alignments; it does not rerun alignment, CNA or methylation. BAM/reference contig names and lengths must agree. Mutect2 requires a sample name in the BAM read groups. BED intervals are zero-based, end-exclusive and must lie within the reference. There is no automatic genome-build conversion or contig renaming. Use `hg19` only with matching standalone inputs and databases; the usual native CNA reference is hg38.

### Review and run existing BAMs in Firefox

```bash
oncotracer setup --variant-config /data/variants.yml
# Equivalent browser-only entry point:
oncotracer web --variant-config /data/variants.yml
```

**Terminal only**, using that same configuration:

```bash
oncotracer variants --config /data/variants.yml --dry-run
oncotracer variants --config /data/variants.yml --threads 4
```

This command runs directly with the configured local tool prefix/environment;
it has no `--backend` argument and does not inherit the installed CNA backend.
For Docker, use the [explicit container invocation](#conda-and-docker).
`setup --variant-config` and `web --variant-config` are browser entry points;
`--non-interactive`, `--terminal` and `--run` cannot be combined with that setup mode.

The **Existing BAMs** link in the main setup page opens the same form. Load the
configuration, check the listed samples and platform, select Fresh or FFPE and
compatible callers, and choose a new project directory. **Save and check**
validates settings and installed tools; **Run analysis** starts the standalone
variant workflow and streams its log. Editing settings invalidates the saved
review. Source BAMs, the original configuration and previous outputs are
preserved. Results are written to `PROJECT/results`.

A partially completed run is displayed separately from a failed run, with a
results link when available. For example, a low-pass FFPE input can retain caller,
Varlociraptor and annotation results while FFPERASE reports `not_assessed`.

### Use an existing ClairS-TO container

For ONT calling, an existing local SIF can supply ClairS-TO and its models while host `samtools` and `bcftools` come from the selected tool prefix. Add these configuration keys to the existing-BAM configuration:

```yaml
mode: ont
variant_specimen_type: fresh
variant_callers: clairs_to
variant_clairsto_platform: ont_r10_dorado_sup_5khz
variant_clairsto_sif: /data/containers/clairs-to.sif
variant_tool_prefix: /data/environments/variant-tools
```

Save the complete configuration, with these keys replacing its Illumina
settings and its manifest pointing to ONT BAMs, as `/data/ont-variants.yml`.
Run without a browser:

```bash
oncotracer variants --config /data/ont-variants.yml --dry-run
oncotracer variants --config /data/ont-variants.yml --threads 4
```

`variant_clairsto_sif` is an explicit configuration option. It requires local Apptainer or Singularity and a SIF exposing `/opt/bin/run_clairs_to`; no image is downloaded. Choose a model preset matching the BAM's chemistry/basecaller. The adapter uses CPU execution with a clean container environment, mounts the private working directory writable and the resolved input directories read-only, and keeps the image's own dependencies separate from the host tool prefix. Image path, size and modification time enter provenance and resume checks. Explicit output prefixes support ClairS-TO releases that otherwise add the sample name to VCF filenames.

## FFPE handling and genotype evidence

Mutect2 collects F1R2 evidence, learns a read-orientation model, and passes those priors to `FilterMutectCalls`; this also occurs in fresh mode. In FFPE mode, C>T and G>A changes receive an `OC_FFPE_DEAMINATION` review flag and a corresponding evidence-table field. That flag alone neither excludes a call nor proves it is an artifact. The independent FFPERASE stage described below adds artifact classification; none of these steps repairs damaged DNA or establishes clinical accuracy. [GATK orientation-model guidance](https://gatk.broadinstitute.org/hc/en-us/articles/360035531132--How-to-Call-somatic-mutations-using-GATK4-Mutect2).

VCFs are normalized against the reference, split at multiallelic records, sorted and indexed. The evidence table retains caller-provided `GT`, `DP`, `AD`, `AF`, `GQ`, `PL` and `GL` where present; unavailable values stay missing. Normalization may change allele representation/indexing. OncoTracer does not infer `GT` from allele fraction, turn missing values into `0/0`, calculate Chilean population MAF, or perform joint genotyping across samples/callers. Tumor VAF and population allele frequency are different quantities.

## Optional local ANNOVAR

`variant_annovar: auto` inspects the configured paths, ANNOVAR environment variables, executable search path and `~/annovar`. Detection requires executable helper scripts, Perl, and unpacked databases for the exact requested build. Automatic annotation selects a complete RefSeq gene database and the newest compatible dated ClinVar database if available. It never downloads, decompresses or licenses ANNOVAR/databases.

The inspected development installation provides **hg38 RefSeq (`refGene`) only**, while **hg19 provides `refGeneWithVer` plus `clinvar_20240917`**. This describes that installation, not a bundled resource guarantee. An hg19 ClinVar file is never used for hg38 calls. The saved detection record identifies the actual resources, script revision, hashes for scripts/small assets and explicitly labeled file-size/time provenance for large databases. [Official ANNOVAR command and output guide](https://annovar.openbioinformatics.org/en/latest/user-guide/startup/).

Missing optional resources produce a recorded annotation skip. A runtime annotation error is recorded separately from retained caller output. The installed ANNOVAR scripts construct internal shell commands, so unsupported path characters, including spaces, are rejected; annotation also refuses an occupied output prefix. Read the recorded reason instead of treating an unannotated VCF as an empty callset.

## Read the outputs

Open `index.html`, then **08 · Small variants**. The dashboard lists calling and annotation statuses separately, including successful calls with zero variant records.

| Output under `08_variants/` | Contents |
|---|---|
| `variant_status.json` | Overall, sample and caller status; record counts; annotation completion or skip/failure reasons. |
| `variant_provenance.json` | Requested settings, reference/tool identity and local annotation discovery. |
| `samples/SAMPLE/CALLER/SAMPLE.CALLER.vcf.gz` and `.tbi` | Normalized caller VCF and its index. Inspect FILTER and FORMAT fields. |
| `samples/SAMPLE/CALLER/evidence.tsv` | Per-record caller evidence, call semantics and FFPE review flag. |
| `samples/SAMPLE/CALLER/annovar.BUILD_multianno.txt` and `.vcf` | Optional annotation tables and annotated VCF when annotation succeeds. |
| Caller logs and retained intermediate evidence | Diagnostics for failure review, filtering and reproducibility. |

For a terminal review of the existing-BAM example above:

```bash
cat /data/variant-results/06_workflow_summary/workflow_summary.txt
python3 -m json.tool /data/variant-results/08_variants/variant_status.json
head -n 5 /data/variant-results/08_variants/samples/TUMOR01/mutect2/evidence.tsv
```

For a FASTQ project, use `PROJECT/results` instead of `/data/variant-results`.
A file is available only if its corresponding step produced it.

If preflight or output ownership fails before stage 08 can be written, the workflow summary points to `.oncotracer-native/variant_failure.json`. The dashboard follows that current status and excludes earlier stage-08 files from the current result listing.

A completed call with zero records, an inapplicable tumor-only caller, a failed call and skipped annotation are distinct outcomes. When a successful caller emits zero variant records, ANNOVAR is `not_applicable`; this does not turn the successful call into a failure. Low-pass CNA success does not establish small-variant accuracy: benchmark depth, allele fraction, preservation and the selected platform/caller before making sensitivity claims.

## Additional assessments: FFPERASE and Varlociraptor

These stages run **after caller-native filtering and normalization**, before ANNOVAR. They retain every candidate, the original GT/FORMAT evidence and existing failure filters. Added FILTER tags identify rejected or unevaluated records. A favorable assessment never converts an original `FILTER=.` into `PASS` or rescues an existing caller failure. `before_assessment.vcf.gz` retains the normalized VCF before these stages; `caller_raw` retains the caller output.

### FFPERASE for Illumina FFPE

Selecting Illumina + FFPE defaults to `variant_ffperase: required`. Fresh and ONT inputs default to `off`; explicitly requesting FFPERASE for those inputs is rejected. To deliberately retain the previous orientation-model/review-only behavior, set `variant_ffperase: off`.

For an existing licensed source/model installation, keep explicit paths:

```yaml
variant_ffperase: required
variant_ffperase_root: /resources/nf-ffperase
variant_ffperase_models: /resources/ffperase-models
variant_ffperase_prefix: /envs/ffperase
# Alternative to a native Python environment:
# variant_ffperase_sif: /resources/ffperase.sif
```

The adapter invokes the external `annotate_w_pileup`, `annotate_variants.py` and `classify_w_random_forest.py` interfaces directly. It does not invoke Nextflow. The tested upstream interface is revision `b0dd56cbd0a939896a966b9ce30c4d719b158170`. Both `model.snvs.joblib` and `model.indels.joblib` must exist. Source/model hashes enter provenance and resume signatures. Equivalent environment variables are `ONCOTRACER_FFPERASE_ROOT`, `ONCOTRACER_FFPERASE_MODELS`, `ONCOTRACER_FFPERASE_PREFIX` and `ONCOTRACER_FFPERASE_SIF`. Inputs/resources remain read-only; metrics, uppercase reference and temporary indexes are generated under the run's private work directory.

The implementation measures genome-wide depth and paired-end insert size, collects Picard sequencing-artifact metrics, extracts pileup features and applies the corresponding SNV/indel model. Unsupported alleles or ambiguous reference contexts are explicitly unevaluated. Existing licensed resources remain supported. Alternatively, opt into the verified preparation below; source/models are fetched from upstream only when Run starts and are not bundled in OncoTracer.

**Low-pass limitation:** this upstream feature code requires an integer coverage greater than one for its logarithmic depth feature. Measured coverage is truncated as required by that interface, without inventing a larger value. If the resulting value is <=1, all records receive `FFPERASE_NOT_EVALUATED`; the stage reports `not_assessed` and the run reports partial failure. This is a mathematical compatibility check, not a validated minimum sequencing depth. No low-pass accuracy claim follows from a successful model execution.

### Automatic FFPERASE resources at run time

In the Illumina FFPE browser form, keep **Prepare missing FFPERASE source and
models when the run starts** selected, review the
[pinned nf-ffperase license](https://github.com/papaemmelab/nf-ffperase/blob/b0dd56cbd0a939896a966b9ce30c4d719b158170/LICENSE),
and explicitly accept it only if its terms fit your use. It excludes clinical
use and requires MSK's express written permission for publishing research results.
The download acknowledgment does not provide that permission. Uncheck preparation
to use existing resources exclusively, or choose **Skip FFPERASE**.

The compatible runtime is still required: a local FFPERASE environment/SIF, or
the Docker runtime in the 20260922 image. Automatic pinned binary preparation
supports Linux x86-64. It fetches only missing source/models, verifies file sizes
and SHA-256, and caches them under `OUTDIR/08_variants/resources/`. Source is
pinned to `b0dd56cbd0a939896a966b9ce30c4d719b158170`; the two official Hugging Face
model files are pinned to `dc4a9ab71bde34d084c4cc91d0ec291dc1f04258` and checked
against their LFS SHA-256 identifiers. Explicit invalid paths are not replaced.

**Terminal only**, equivalent to enabling preparation and accepting the license
in the browser. Run this example only after reviewing and accepting those terms;
it uses a new project and one FFPE tumor library:

```bash
oncotracer setup --non-interactive --project "$PWD/ffpe-auto-resources-study" \
  --mode illumina --analysis cna --backend conda --threads 4 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz --hg38_build --variants \
  --variant-specimen-type ffpe --variant-callers mutect2 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/variants" \
  --variant-ffperase required \
  --variant-ffperase-prefix "$HOME/.local/share/oncotracer/optional-tools/ffperase" \
  --variant-download-resources --variant-accept-ffperase-license \
  --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/ffpe-auto-resources-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/ffpe-auto-resources-study/config/run.yml"
```

For an existing-BAM configuration, the equivalent additional YAML fields are:

```yaml
variant_ffperase: required
variant_download_resources: true
variant_accept_ffperase_license: true
```

Add those keys to the complete Illumina configuration, set
`variant_specimen_type: ffpe`, supply a compatible FFPERASE runtime, and save as
`/data/ffpe-auto-resources.yml` with a new output folder. Use the same direct
runner as other existing-BAM examples:

```bash
oncotracer variants --config /data/ffpe-auto-resources.yml --dry-run
oncotracer variants --config /data/ffpe-auto-resources.yml --threads 4
```

These flags do not install caller environments, accept any license on your behalf,
or obtain ANNOVAR. Checks and dry runs report the planned resources without
fetching them.

### Varlociraptor

```yaml
variant_varlociraptor: required
variant_varlociraptor_fdr: 0.05
```

Default: `off`. In the browser, enable **Also assess variant evidence with Varlociraptor**
and leave **Standard variant presence** selected. No user-provided scenario YAML
or sample key is required; the default local false discovery rate is 5%. The
terminal equivalent is `--variant-varlociraptor required --variant-varlociraptor-fdr 0.05`
in any setup example, or the two YAML fields above for an existing-BAM run.

When enabled, OncoTracer estimates alignment properties, extracts observations from the BAM, calculates event probabilities and applies **local-smart FDR** at the requested threshold. The bundled scenario evaluates the event `PRESENT` in a single observed sample with a continuous allele-frequency universe. It makes no somatic/germline distinction and assumes no tumor purity or Chilean population allele frequencies. ONT uses the homopolymer alignment mode. Normalized candidates are treated as atomic variants.

Open **Advanced evidence settings → Custom scenario YAML** only for a custom
single-observed-sample model. The equivalent YAML is:

```yaml
variant_varlociraptor_scenario: /resources/scenario.yaml
variant_varlociraptor_sample: tumor
variant_varlociraptor_events: SOMATIC_TUMOR_HIGH,SOMATIC_TUMOR_LOW,GERMLINE
```

The internal model sample key and event names must match that scenario; the key
is not a patient identifier, barcode or manifest sample name. The adapter currently supplies one observed BAM per call; it does not infer matched tumor/normal pairs. Do not copy tumor-purity assumptions from an example as if they were measured. See the official [calling](https://varlociraptor.github.io/docs/calling/), [FDR filtering](https://varlociraptor.github.io/docs/filtering/) and [output](https://varlociraptor.github.io/docs/output/) documentation.

The original caller genotypes remain in the final annotated VCF. Varlociraptor's own BCF/VCF outputs are saved separately; its estimated allele fraction is not substituted for `GT`. `VARLOCIRAPTOR_SCORE` reports the **PHRED-scaled ARTIFACT posterior** (lower means more probable), whereas `FFPERASE_SCORE` is the **raw artifact model score** (higher means more likely). They are different quantities. Per-allele evidence TSVs and stage summary JSON files document decisions and missing assessments. Stages also appear in the results dashboard.

### Conda and Docker

The pinned variant environment supplies Mutect2, FreeBayes, bcftools, samtools and **Varlociraptor 8.9.5**. FFPERASE uses a separate legacy environment to match its model dependencies. From your `oncotracer-src` checkout, create these user-owned prefixes once (skip creation for an existing compatible environment):

```bash
export ONCOTRACER_VARIANTS_PREFIX="$HOME/.local/share/oncotracer/optional-tools/variants"
export ONCOTRACER_FFPERASE_PREFIX="$HOME/.local/share/oncotracer/optional-tools/ffperase"
conda env create -p "$ONCOTRACER_VARIANTS_PREFIX" -f environments/native-variants.yml
conda env create -p "$ONCOTRACER_FFPERASE_PREFIX" -f environments/native-ffperase.yml
```

The `/envs/...` paths in the illustrative local-resource YAML above must be replaced
with your actual prefixes when running on the host; those paths are used inside
the supplied Docker image. ONT caller installation commands are available from
**Autodetect resources → Copy commands**.

These additional environments are installed explicitly; the existing CNA environment installer is unchanged. `variant_tool_prefix` overrides `ONCOTRACER_VARIANTS_PREFIX`. Existing environments and user installations are never modified by an analysis run. For exact platform-specific reproducibility, save `conda list --explicit -p PREFIX` after creation.

The updated Dockerfile builds both environments and sets these prefixes. Each environment has its own cached build layer. Legacy Bioconda annotation downloads use listed Bioconductor mirrors during the build (TU Dortmund for the archived QDNAseq release and Posit for current data), with the original package checksum verification retained. In Docker, use the native FFPERASE prefix; no nested container is necessary. Existing source/model folders are mounted read-only. A current build or the 20260922 image can instead prepare selected Clair3/FFPERASE resources on Run as described above. ANNOVAR remains separately supplied. The pinned ClairS-TO runtime includes its platform models; select the matching preset explicitly. For example:

```bash
docker build -t oncotracer:variant-filters .
docker run --rm -v /data/project:/project \
  -v /data/resources:/resources:ro \
  oncotracer:variant-filters variants --config /project/variants.yml
```

YAML paths must be valid inside the container. No image is published by these commands.
