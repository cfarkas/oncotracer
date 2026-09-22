# Call small variants

Add single-base changes and short insertions/deletions to a CNA project, or analyze
existing BAMs separately. Each caller produces its own variant call format (VCF)
file and evidence table. You can configure CNA and variants in **one browser session**.

Try the controls in the [synthetic setup demo](browser_demo.md), then
[install OncoTracer](installation.md) to analyze your data. Low-pass variant calls
are research candidates: a successful run does not establish clinical sensitivity
or prove that a tumor-only candidate is somatic.

## Add calling during setup

```bash
oncotracer setup --variants
```

1. Choose **Illumina or Oxford Nanopore**, discover your FASTQs and assign samples
   to Normal or Cancer. Unassigned samples are excluded.
2. Keep **Copy-number analysis (CNA)** selected and enable **Add small-variant calling**.
3. Select **Fresh or FFPE**, then choose compatible callers below.
4. Choose your analysis tools and supply any requested model/resource folders.
5. Optionally enable **Varlociraptor** and local **ANNOVAR annotation**.
6. Choose a new project folder, click **Save configuration and check**, then
   **Run analysis**. Review CNA and variant results from the same page.

Missing required callers or models stop preflight. The variant option does not
install external models or licensed annotation resources. CNA plus methylation
can also include variants on supported host backends; methylation-only analysis
cannot, and Docker methylation is unavailable.

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

## Read the results

Open the dashboard and select **08 · Small variants**. Inspect each caller's
VCF, `evidence.tsv`, assessment tables, annotation outputs and logs. The dashboard
separates **zero calls**, **skipped annotation**, **partial failure** and **failed calls**.

For example, insufficient FFPE depth can leave FFPERASE `not_assessed` while
retaining completed caller, Varlociraptor and ANNOVAR outputs. Read the recorded
reason and available evidence. [Output paths and status details](variants_reference.md#read-the-outputs)
explain what was produced.
