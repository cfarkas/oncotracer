# ONT SNP imputation with QUILT2

QUILT2 is an optional **research** addition to ONT variant calling. It uses the
alleles observed along ONT reads and a population haplotype panel to impute
biallelic SNPs on GRCh38 autosomes. Clair3 and ClairS-TO outputs remain separate
and unchanged. Illumina continues to use GLIMPSE2.

## Choose it in the browser

```bash
oncotracer --interactive --backend conda
# From a source checkout, the same setup is available through Nextflow:
nextflow run main.nf --interactive --backend conda
```

Select **Oxford Nanopore**, choose **Small variants**, and set **ONT population
imputation → QUILT2**. Optionally enable **Add Monte Carlo linkage testing**.
For Docker, use `--backend docker` with an image built from this source.
The same choices are available when reviewing a saved analysis. Analysis starts
only after saving, checking and choosing **Run analysis**.

For terminal setup, add these options to your usual `oncotracer setup` command:

```bash
--variants --variant-ont-imputation quilt2 --monte-carlo-linkage
```

The saved YAML records:

```yaml
run_variants: true
variant_ont_imputation: quilt2
variant_monte_carlo_linkage: true
```

The Conda variant recipe pins QUILT2 **2.0.4**, alongside the required bcftools
and HTSlib versions. Installation verifies the R package and the **QUILT2.R**
interface; the older `QUILT.R` entry is not used. The Dockerfile installs the
same recipe. QUILT2 and the linkage calculation use CPU. GPU selection continues
to apply to supported ONT callers and local model inference.

## Reference preparation and reuse

OncoTracer verifies the publisher's checksums for the public GRCh38 high-coverage
1000 Genomes **3202-sample phased panel**, retains biallelic SNPs with minor allele
count at least two, and checks their REF alleles against your FASTA. It uses the
QUILT genetic maps from a pinned upstream commit. This integration prepares its
own panel; it does not claim to use the separately distributed HGDP+1KG panel.

QUILT's map-based chunking uses its default minimum 3 Mb / 4 cM regions, with
500 kb buffers and `nGen=100`. Panel samples whose identifiers match the study
sample names are excluded before preparation. When validating against a public
sample under a different alias, use its original identifier to prevent reference
leakage. No accuracy claim should be made using a sample retained in its reference
panel.

Preparation can take substantial time and disk space. The prepared panel is
shared across the study, not rebuilt for every sample. For reuse across projects,
choose **Prepared reference cache** under the QUILT2 resource settings, or set:

```yaml
variant_quilt2_cache: /path/to/shared/quilt2-cache
# Optional: existing public files with their original CCDG filenames.
variant_quilt2_reference_dir: /path/to/public-3202-phased-vcfs
```

The cache must be empty or owned by this OncoTracer preparation stage. Existing
public files are verified and remain unchanged. Docker binds supplied panels
read-only and the preparation cache writable. Completed chunks have input
signatures and output checksums; interrupted or changed chunks are recomputed.
An existing file alone is never proof of completion.

If a run stops, select **Continue an existing analysis** and load the complete
project folder. Completed alignment stages and compatible QUILT2 chunks are
validated before reuse; unfinished chunks run again. The QUILT2 summary records
`reused_chunks` and `computed_chunks` for that attempt. Keep the project and its
reference cache; do not erase them to recover from an interrupted analysis.

The current launcher also handles QUILT2 2.0.4's output-path substitution bug,
which could fail when a project or temporary directory contained `gz`. It writes
inside an isolated working directory, validates the VCF, and then gives the
published files their sample prefixes. This repair does not change imputation
parameters or probabilities. Verified chunks from the preceding local candidate
remain reusable when their inputs and output checksums match.

## Outputs and interpretation

Each sample has `08_variants/samples/SAMPLE/quilt2/`, including:

- `SAMPLE.quilt2.imputed.vcf.gz` and its index: autosomal imputed SNPs.
- `SAMPLE.quilt2.chrN.vcf.gz`: chromosome outputs.
- `SAMPLE.quilt2.summary.json`: counts, panel identity and limitations.

The VCF preserves QUILT's **GP** genotype posterior and **DS** dosage. These are
model outputs, not calibrated probabilities of inherited origin or pathogenicity.
After chunk ligation, **HD** is removed because bcftools updates phased genotypes
but does not update QUILT haplotype dosages. Missing genotypes remain missing.
Imputation never replaces the original caller VCFs or removes FFPE warnings.

QUILT2 assumes diploid population haplotypes. Tumor admixture, CNAs, unusual
ploidy, ancestry mismatch and insufficient reads can invalidate those assumptions.
Treat tumor results as germline-style hypotheses requiring independent confirmation.
Indels, structural variants and sex chromosomes are outside this integration.
[Monte Carlo linkage](haplotype_evidence.md) tests **observed caller SNVs**, not
imputed alleles; this prevents using population imputation as if it were new
read evidence. It cannot establish germline origin either.

## Upstream acknowledgement

QUILT2 is developed by Li, Albrechtsen, Davies and collaborators and distributed
under GPL-3.0. Cite the [QUILT2 paper](https://www.nature.com/articles/s41467-025-67218-1)
and consult the [upstream documentation](https://github.com/rwdavies/QUILT/blob/master/README_QUILT2.md).
Its published normal-sample benchmarks support choosing a read-aware imputer for
ONT; they do not validate OncoTracer's tumor cohorts or imply clinical accuracy.
The reference is from the [1000 Genomes high-coverage phased release](https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/working/20201028_3202_phased/).

## Share available CPU and RAM

Dynamic scheduling is the default for ONT variants in every installation route.
Clair3 CPU chunk queues and independent QUILT2 chunks receive capacity released
by completed samples. The controller checks CPU affinity, container quotas and
available RAM every five seconds, within the analysis CPU and memory budgets.
It reserves 20% of available RAM and admits at most one chunk per 8 GiB. Running
chunks finish before a reduced allowance takes full effect. These admission
limits are estimates, not an operating-system memory cap.

Choose **Resource scheduling** in browser setup, answer the terminal prompt, or
set `variant_resource_mode: fixed` in YAML to retain initial allocations.
An explicit CLI option overrides YAML for that invocation:

```bash
oncotracer run --config project/config/run.yml --resource-mode dynamic
oncotracer variants --config variants.yml --resource-mode fixed
nextflow run cfarkas/oncotracer --analysis variants --config variants.yml --resource_mode dynamic
```

Dynamic mode bounds the requested sample count by CPU and RAM. It expands CPU
Clair3's supported GNU Parallel chunk queues and runs QUILT2 chunks with one
thread each, preserving the existing seed, model, filters and validation.
Completed chunks retain their original integrity receipts and are checked before
reuse. New work waits when RAM is scarce; controller loss stops new admission.
Inspect `08_variants/resource_scheduler/*/status.json` for live allowances.

The configured budget applies to one analysis. Independent projects need separate
budgets; the controller does not increase Docker limits or coordinate unrelated
processes. GPU callers, ClairS-TO, alignment, GLIMPSE2 and final ligation retain
their current scheduling. Existing running studies keep their pinned version.
