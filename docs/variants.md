# Call small variants

Add single-base changes and short insertions/deletions to a CNA project, or analyze
existing BAMs separately. Each caller produces its own variant call format (VCF)
file and evidence table. You can configure CNA and variants in **one browser session**.

Try the controls in the [synthetic setup demo](browser_demo.md), then
[install OncoTracer](installation.md) to analyze your data. Low-pass variant calls
are research candidates: a successful run does not establish clinical sensitivity
or prove that a tumor-only candidate is somatic.

For a remote server, use the [SSH browser instructions](headless.md), or choose
one of the **Terminal only** alternatives below. `--no-browser` keeps the web
interface running; `--non-interactive` creates a configuration without a web
server or questions. Replace `/data` and `/resources` examples with your paths.

## Add calling during setup

```bash
oncotracer setup --variants
```

1. Choose **Illumina or Oxford Nanopore**, discover your FASTQs and assign samples
   to Normal or Cancer. Unassigned samples are excluded.
2. Keep **Copy-number analysis (CNA)** selected and enable **Add small-variant calling**.
3. Work through the four variant sections below. Start with **Fresh or FFPE**
   and your callers, then check their resources.
4. Optionally enable **Varlociraptor** and local **ANNOVAR annotation**.
5. Review any missing resources or model candidates before continuing.
6. Choose a new project folder, click **Save configuration and check**, then
   **Run analysis**. Review CNA and variant results from the same page.

<details markdown="1">
<summary>Terminal only: Illumina Fresh, Conda, Mutect2</summary>

This is an alternative to the browser steps, using one tumor library. Install
[the optional variant environment](variants_reference.md#conda-and-docker) first
and set its actual prefix below. Use a new project folder.

```bash
oncotracer setup --non-interactive --project "$PWD/variant-study" \
  --mode illumina --analysis cna --backend conda --threads 4 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz \
  --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers mutect2 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/variants" \
  --variant-ffperase off --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/variant-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/variant-study/config/run.yml"
```

The reference downloads when the run starts. For [FFPE](variants_reference.md#add-calling-during-setup)
or [ONT](variants_reference.md#terminal-only-ont-fastqs), use the corresponding
complete terminal example. For several Illumina libraries, replace the
single-library flags with `--samplesheet /data/illumina/samplesheet.csv` using
the [documented CSV layout](setup.md#illumina-multiple-libraries).

</details>

Missing required callers or models stop preflight. The variant option does not
install external models or licensed annotation resources. CNA plus methylation
can also include variants on supported host backends; methylation-only analysis
cannot, and Docker methylation is unavailable.

## Configure variants in four sections

The FASTQ setup and existing-BAM forms use the same order:

| Section | Choose here |
| --- | --- |
| **1 · Specimen and callers** | Fresh or FFPE, and the callers available for your platform. |
| **2 · Caller tools and models** | Caller resources, including a compatible ONT model or preset. |
| **3 · Filtering and FFPE** | Varlociraptor and, for Illumina FFPE, FFPERASE. |
| **4 · Annotation** | Optional ANNOVAR software and databases. |

Common choices stay visible. Open the path controls when you need to inspect or
enter a folder; controls adapt to your platform, callers and backend.

### Find resources

Click **Autodetect resources** to check resources for your selected settings, or
use **Autodetect** beside an individual path. Discovery checks the computer
running the setup server, fills empty paths and preserves paths you entered.
If several candidates are found, review them and choose the one to use. Review
every ONT model against your actual chemistry/basecaller; a detected directory
does not establish compatibility.

The compact resource summary opens detailed results and installation help.
Missing resources include **Copy commands** boxes and official setup links in
that dialog. Review and run the commands yourself in a terminal, then check
again. Discovery does not install tools, download models, accept licenses or run
analysis. If a command exports environment variables, either paste its printed
paths into the browser or restart setup from that terminal before checking again.

A target BED and a custom Varlociraptor scenario describe your intended analysis.
Select these files explicitly; resource discovery does not choose them.

Conda recipes create separate user-owned optional environments and leave existing
folders unchanged. Docker uses the image's caller runtimes; external model/source
folders still need your selection. Container tools marked **Not verified**
are verified when the analysis starts. [ANNOVAR](annovar.md) remains optional and
requires separately obtained software and matching databases.

## Choose a platform and specimen type

| Platform | Caller | What it produces |
| --- | --- | --- |
| Illumina | **Mutect2** (default) | Tumor-only candidates with a learned read-orientation artifact model. |
| Illumina | **FreeBayes**, **bcftools** | Independent germline-style calls. |
| ONT | **Clair3** (default) | Germline-style calls; provide a model matching your chemistry/basecaller. |
| ONT | **ClairS-TO** | Tumor-only candidates; choose a compatible platform/model preset. |

A normal sample skips tumor-only callers. Controls are analyzed independently;
OncoTracer does not infer matched tumor–normal pairs or combine callers into a
consensus genotype. Multiple callers remain separate for comparison.

**Fresh/FFPE describes preservation, not sequencing platform.** Use one preservation
type per project. Illumina FFPE selects FFPERASE by default; provide its external
source and model folders, or explicitly select **Skip FFPERASE**. FFPERASE is not
supported for Fresh or ONT inputs.

## Conda and Docker

The [installation guide](installation.md) covers the launcher and core tools.
For Conda, add the separate variant and FFPERASE environments using the
[complete environment commands](variants_reference.md#conda-and-docker). The
browser's environment field selects the folder containing the caller executables.

### Run CNA and variants with Docker

Select **Docker** and this image in browser settings, or prefill them:

```bash
oncotracer setup --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 --variants
```

<details markdown="1">
<summary>Terminal only: the same Docker workflow</summary>

This example selects one Fresh Illumina tumor library and Mutect2. The same
image runs alignment, CNA and variants; no host Conda environment is needed.

```bash
oncotracer setup --non-interactive --project "$PWD/docker-variant-study" \
  --mode illumina --analysis cna --backend docker --threads 4 \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz \
  --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers mutect2 \
  --variant-ffperase off --variant-varlociraptor off --variant-annovar auto
oncotracer check --config "$PWD/docker-variant-study/config/run.yml"
oncotracer run --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --config "$PWD/docker-variant-study/config/run.yml"
```

</details>

The image supplies native callers and the FFPERASE runtime. Clair3 models,
FFPERASE source/models and ANNOVAR remain external resources. Select their host
paths in the browser; OncoTracer mounts them for analysis. **Save and check**
checks paths/settings; **Run analysis** checks the image tools before alignment.
The saved project retains its backend and image.

## What filtering is applied?

| Step | Behavior |
| --- | --- |
| Caller filters | Mutect2 uses `FilterMutectCalls` and orientation-model priors. Illumina mapping/base-quality defaults are 20; FreeBayes also requires at least two alternate reads and 5% alternate fraction. ONT callers retain their native filtering. |
| Normalization | Splits multiallelic records, normalizes alleles against the reference, sorts and indexes VCFs. |
| FFPE review | Flags C>T/G>A changes for review; this flag alone does not reject them. |
| FFPERASE | Adds an independent artifact assessment for Illumina FFPE when selected and assessable. |
| Varlociraptor | Optional evidence assessment and local false-discovery-rate filtering; default threshold 0.05 when enabled. |

There is no universal post-calling QUAL/DP cutoff applied across every caller.
Assessment steps retain candidates and add FILTER/evidence fields; they do not
rescue an existing caller failure. The default Varlociraptor model assesses
presence, not somatic origin. See the [filtering reference](variants_reference.md#additional-assessments-ffperase-and-varlociraptor)
for exact behavior and low-depth FFPERASE limitations.

## Optional local ANNOVAR

Choose **Automatically use an existing local installation**, or **Skip ANNOVAR annotation**.
Automatic discovery uses existing scripts and matching databases; it downloads
nothing. Annotation adds gene/database descriptions after filtering.
Follow [ANNOVAR setup and output guidance](annovar.md).

## Call from existing BAMs without rerunning CNA

Prepare the [BAM manifest and configuration](variants_reference.md#call-from-existing-bams-without-rerunning-cna), then open:

```bash
oncotracer setup --variant-config /data/variants.yml
```

Replace `/data/variants.yml` with your configuration. The **Call variants from
existing BAMs** link in initial setup opens the same form. Review samples,
Fresh/FFPE, callers and resources; save to a new project and run. This standalone
browser route uses host tools and existing alignments; it does not repeat CNA.
[Container and terminal alternatives](variants_reference.md) remain available.

**Terminal only**, with that same existing-BAM configuration:

```bash
oncotracer variants --config /data/variants.yml --dry-run
oncotracer variants --config /data/variants.yml --threads 4
```

`variants` uses local tools from the configuration/environment and has no
`--backend` argument. The [explicit Docker command](variants_reference.md#conda-and-docker)
is the container alternative. `setup --variant-config` always opens the browser;
it cannot be combined with `--non-interactive` or `--terminal`.


## Read the results

Open the dashboard and select **08 · Small variants**. Inspect each caller's
VCF, `evidence.tsv`, assessment tables, annotation outputs and logs. The dashboard
separates **zero calls**, **skipped annotation**, **partial failure** and **failed calls**.

For example, insufficient FFPE depth can leave FFPERASE `not_assessed` while
retaining completed caller, Varlociraptor and ANNOVAR outputs. Read the recorded
reason and available evidence. [Output paths and status details](variants_reference.md#read-the-outputs)
explain what was produced.
