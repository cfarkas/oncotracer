# Small-variant calling

OncoTracer can run optional SNV/indel callers alongside CNA analysis, or from an explicit manifest of existing BAMs. Each caller keeps its own VCF and evidence table. This research feature does not establish clinical sensitivity, reliable germline genotypes at low coverage, or a validated somatic diagnosis.

## Choose a platform and specimen type

| Platform | Supported caller names | Meaning of the calls |
|---|---|---|
| Illumina | `mutect2` (default) | Tumor-only candidates; without a matched normal they are not confirmed somatic variants. |
| Illumina | `freebayes`, `bcftools` | Independent germline-style calls; tumor purity, copy number and sparse depth still affect interpretation. |
| ONT | `clair3` (default) | Germline-style calls using an existing model appropriate for the chemistry and basecaller. |
| ONT | `clairs_to` | Tumor-only candidates using an explicitly selected compatible platform/model preset. |

See the developers’ [Clair3](https://github.com/HKU-BAL/Clair3) and [ClairS-TO](https://github.com/HKU-BAL/ClairS-TO) documentation for model compatibility and caller scope.

Preservation is a separate choice: `fresh` or `ffpe`. Use one preservation type per project. A sample explicitly labeled normal skips tumor-only callers; it can still use a compatible germline-style caller. Multiple callers remain separate; their agreement is not converted into a synthetic consensus genotype.

## Add calling during setup

In browser setup, enable **Add small-variant calling**, select preservation and platform-compatible callers, and provide any required local tools/models. This branch requires aligned reads, so choose CNA or CNA plus methylation; methylation-only analysis is not eligible. Supported execution backends are `conda`, `host` and `poetry`. Docker supports CNA plus variants from FASTQ; Docker methylation and the Singularity backend with variants remain unavailable.

For example, prefill an Illumina FFPE project:

```bash
oncotracer setup --project "$PWD/ffpe-study" --mode illumina \
  --input-folder /data/illumina --variants \
  --variant-specimen-type ffpe --variant-callers mutect2
```

Replace `/data/illumina` with your FASTQ folder. Review the generated configuration before running. The selected backend must provide the tools; adding this option does not install callers or accept their licenses. Missing requested callers stop preflight rather than silently substituting another caller.

| Setup flag | Purpose |
|---|---|
| `--variant-tool-prefix PATH` | Existing environment containing the required executables in `bin/`. |
| `--variant-targets-bed PATH` | Optional BED intervals matching the reference assembly and contig names. |
| `--variant-clair3-model PATH` | Existing nonempty Clair3 model directory; select its chemistry/basecaller compatibility explicitly. |
| `--variant-clairsto-platform VALUE` | Installed ClairS-TO preset beginning `ont_`, matching the sequencing configuration. |
| `--variant-annovar auto` or `off` | Discover existing annotation resources, or skip annotation. |
| `--variant-annovar-dir PATH` / `--variant-annovar-db PATH` | Explicit existing ANNOVAR installation/database directories. |

### Run CNA and variants with Docker

Choose **Docker** under analysis tools in the browser and enter the **Docker image
tag or digest**. Use the published integration image, or a compatible local build.
The same selection can prefill the form:

```bash
oncotracer setup --project "$PWD/docker-study" --mode illumina \
  --input-folder /data/illumina --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --variants --variant-specimen-type fresh --variant-callers mutect2,bcftools
```

Synthetic hg38 FASTQ integration checks recovered both inserted SNVs with each
Illumina caller (BCFtools, FreeBayes, Mutect2) and native ONT ClairS-TO. CNA,
Varlociraptor and existing ANNOVAR annotation completed. The low-depth FFPE case
correctly retained results with partial-failure status when FFPERASE was not
assessable. These are software integration checks; they do not establish clinical
accuracy. Clair3 is bundled and startup-tested, with a matching external model
required for inference.

The project saves `execution_backend: docker` and `docker_image`; **Run analysis**
and `oncotracer setup --project "$PWD/docker-study" --run` reuse those selections.
The chosen image must include the requested native callers. The run checks their
availability inside that image before alignment begins. **Save and check** validates
input paths and configuration without starting Docker or analysis.

Container environments supply the caller and FFPERASE dependencies, so the browser
hides host environment prefixes and SIF selectors for Docker. Existing host prefix
settings in a reused YAML do not replace the image environments. Use host paths for
target BEDs, a chemistry-compatible Clair3 model, FFPERASE source/models, custom
Varlociraptor scenarios and licensed ANNOVAR resources; the runner mounts them at
the same absolute paths. It discovers available host ANNOVAR resources in `auto`
mode and records whether annotation was available. No annotation database is
downloaded. Use absolute paths (expand `~` before saving manual YAML); browser setup saves absolute paths. For an existing ClairS-TO or FFPERASE SIF, choose host or Conda.

This browser Docker route starts from FASTQ and includes CNA. The **Existing BAMs**
form below runs its standalone variant workflow on the host.

## Call from existing BAMs without rerunning CNA

Create a tab-separated manifest with one sample per BAM. Paths must identify existing files; normal samples are independent controls, not automatically paired normals.

```bash
cat > /data/variant_bams.tsv <<'TSV'
sample	bam	status
TUMOR01	/data/tumor01.bam	tumor
CONTROL01	/data/control01.bam	normal
TSV
```

Use a dedicated new output directory and a flat configuration, for example:

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

If preflight or output ownership fails before stage 08 can be written, the workflow summary points to `.oncotracer-native/variant_failure.json`. The dashboard follows that current status and excludes earlier stage-08 files from the current result listing.

A completed call with zero records, an inapplicable tumor-only caller, a failed call and skipped annotation are distinct outcomes. When a successful caller emits zero variant records, ANNOVAR is `not_applicable`; this does not turn the successful call into a failure. Low-pass CNA success does not establish small-variant accuracy: benchmark depth, allele fraction, preservation and the selected platform/caller before making sensitivity claims.

## Additional assessments: FFPERASE and Varlociraptor

These stages run **after caller-native filtering and normalization**, before ANNOVAR. They retain every candidate, the original GT/FORMAT evidence and existing failure filters. Added FILTER tags identify rejected or unevaluated records. A favorable assessment never converts an original `FILTER=.` into `PASS` or rescues an existing caller failure. `before_assessment.vcf.gz` retains the normalized VCF before these stages; `caller_raw` retains the caller output.

### FFPERASE for Illumina FFPE

Selecting Illumina + FFPE now defaults to `variant_ffperase: required`. Fresh and ONT inputs default to `off`; explicitly requesting FFPERASE for those inputs is rejected. To deliberately retain the previous orientation-model/review-only behavior, set `variant_ffperase: off`.

```yaml
variant_ffperase: required
variant_ffperase_root: /resources/nf-ffperase
variant_ffperase_models: /resources/ffperase-models
variant_ffperase_prefix: /envs/ffperase
# Alternative to a native Python environment:
# variant_ffperase_sif: /resources/ffperase.sif
```

The adapter invokes the external `annotate_w_pileup`, `annotate_variants.py` and `classify_w_random_forest.py` interfaces directly. It does not invoke Nextflow. The tested upstream interface is revision `b0dd56cbd0a939896a966b9ce30c4d719b158170`. Both `model.snvs.joblib` and `model.indels.joblib` must exist. Source/model hashes enter provenance and resume signatures. Equivalent environment variables are `ONCOTRACER_FFPERASE_ROOT`, `ONCOTRACER_FFPERASE_MODELS`, `ONCOTRACER_FFPERASE_PREFIX` and `ONCOTRACER_FFPERASE_SIF`. Inputs/resources remain read-only; metrics, uppercase reference and temporary indexes are generated under the run's private work directory.

The implementation measures genome-wide depth and paired-end insert size, collects Picard sequencing-artifact metrics, extracts pileup features and applies the corresponding SNV/indel model. Unsupported alleles or ambiguous reference contexts are explicitly unevaluated. Models and source must be obtained separately under the [FFPErase upstream terms](https://github.com/papaemmelab/nf-ffperase); they are not redistributed in OncoTracer or silently downloaded.

**Low-pass limitation:** this upstream feature code requires an integer coverage greater than one for its logarithmic depth feature. Measured coverage is truncated as required by that interface, without inventing a larger value. If the resulting value is <=1, all records receive `FFPERASE_NOT_EVALUATED`; the stage reports `not_assessed` and the run reports partial failure. This is a mathematical compatibility check, not a validated minimum sequencing depth. No low-pass accuracy claim follows from a successful model execution.

### Varlociraptor

```yaml
variant_varlociraptor: required
variant_varlociraptor_fdr: 0.05
```

Default: `off`. When enabled, OncoTracer estimates alignment properties, extracts observations from the BAM, calculates event probabilities and applies **local-smart FDR** at the requested threshold. The bundled scenario evaluates the event `PRESENT` in a single observed sample with a continuous allele-frequency universe. It makes no somatic/germline distinction and assumes no tumor purity or Chilean population allele frequencies. ONT uses the homopolymer alignment mode. Normalized candidates are treated as atomic variants.

A custom single-observed-sample scenario can be supplied with:

```yaml
variant_varlociraptor_scenario: /resources/scenario.yaml
variant_varlociraptor_sample: tumor
variant_varlociraptor_events: SOMATIC_TUMOR_HIGH,SOMATIC_TUMOR_LOW,GERMLINE
```

The sample name and events must match that scenario. The adapter currently supplies one observed BAM per call; it does not infer matched tumor/normal pairs. Do not copy tumor-purity assumptions from an example as if they were measured. See the official [calling](https://varlociraptor.github.io/docs/calling/), [FDR filtering](https://varlociraptor.github.io/docs/filtering/) and [output](https://varlociraptor.github.io/docs/output/) documentation.

The original caller genotypes remain in the final annotated VCF. Varlociraptor's own BCF/VCF outputs are saved separately; its estimated allele fraction is not substituted for `GT`. `VARLOCIRAPTOR_SCORE` reports the **PHRED-scaled ARTIFACT posterior** (lower means more probable), whereas `FFPERASE_SCORE` is the **raw artifact model score** (higher means more likely). They are different quantities. Per-allele evidence TSVs and stage summary JSON files document decisions and missing assessments. Stages also appear in the results dashboard.

### Conda and Docker

The pinned variant environment supplies Mutect2, FreeBayes, bcftools, samtools and **Varlociraptor 8.9.5**. FFPERASE uses a separate legacy environment to match its model dependencies:

```bash
conda env create -p /envs/variants -f environments/native-variants.yml
conda env create -p /envs/ffperase -f environments/native-ffperase.yml
export ONCOTRACER_VARIANTS_PREFIX=/envs/variants
export ONCOTRACER_FFPERASE_PREFIX=/envs/ffperase
```

These additional environments are installed explicitly; the existing CNA environment installer is unchanged. `variant_tool_prefix` overrides `ONCOTRACER_VARIANTS_PREFIX`. Existing environments and user installations are never modified by an analysis run. For exact platform-specific reproducibility, save `conda list --explicit -p PREFIX` after creation.

The updated Dockerfile builds both environments and sets these prefixes. Each environment has its own cached build layer. Legacy Bioconda annotation downloads use listed Bioconductor mirrors during the build (TU Dortmund for the archived QDNAseq release and Posit for current data), with the original package checksum verification retained. In Docker, use the native FFPERASE prefix and mount external source/models read-only; no nested container is necessary. ANNOVAR and chemistry-compatible Clair3 models remain separately supplied resources. The pinned ClairS-TO runtime includes its platform models; select the matching preset explicitly. For example:

```bash
docker build -t oncotracer:variant-filters .
docker run --rm -v /data/project:/project \
  -v /data/resources:/resources:ro \
  oncotracer:variant-filters variants --config /project/variants.yml
```

YAML paths must be valid inside the container. No image is published by these commands.
