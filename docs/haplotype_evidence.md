# Read-backed haplotype evidence

After variant calling, `oncotracer haplotype-evidence` assesses whether nearby variants occur together on independent library fragments more often than expected under a conditional random-pairing model. It is an **experimental research assessment**, separate from the variant callers and their filters. It works with completed Illumina Fresh, FFPE or ONT BAMs.

**Linkage does not establish germline origin.** A real somatic variant can share a chromosome with a germline variant. A pair seen on one read is insufficient. The output does not assign a probability of inheritance, true-variant status, pathogenicity, or membership of an ancestral haplogroup. Such probabilities require a calibrated model and independent truth data, preferably including matched normal or family evidence.

## Include linkage in an analysis

In **Small variants**, select **Add Monte Carlo linkage testing**. This works for
ONT, fresh Illumina and FFPE Illumina, with Conda or Docker. Terminal setup accepts
`--monte-carlo-linkage`; the saved configuration contains
`variant_monte_carlo_linkage: true`.

The integrated stage tests the observed caller VCFs against the corresponding
BAM. It never substitutes GLIMPSE2 or QUILT2 imputed genotypes for read evidence.
Each caller is a separate multiple-testing family, recorded in its report. This
is not a cohort-wide family correction. Insufficient fragments produce an
unassessable result, not a failed variant or a reference genotype.

Whole-genome assessment uses a disk-backed table and compressed TSV exports,
with no Excel row limit. It scans 100 kb windows with look-ahead so that pairs
crossing window boundaries are retained exactly once. The BY correction spans
**all assessable pairs in the caller VCF**, not each window. There is no global
100,000-pair truncation; dense windows split automatically to keep each batch
within the memory limit.
The small-region command below retains its explicit total pair limit. Add
`--genome-wide` to that command to use the disk-backed route.

Reports appear under `08_variants/linkage/SAMPLE/CALLER/` for ONT and
`08_variants/glimpse2/linkage/SAMPLE/CALLER/` for Illumina. All published report
filenames begin with the sample name. The report links to the complete pair and
variant-assessability tables. Verified completed reports can be reused; input
changes or altered output checksums trigger recomputation. Integrated analysis
makes no VarSome requests and does not invoke an LLM to generate probabilities.

## Run after calling

Use the completed, coordinate-sorted BAM and its index, a VCF with the exact sample name, and the same indexed reference FASTA used for alignment. Start with a region or candidate VCF; this is not automatically enabled for every whole-genome variant.

```bash
oncotracer haplotype-evidence --backend conda \
  --bam /path/to/SAMPLE01.bam \
  --vcf /path/to/SAMPLE01.vcf.gz \
  --reference /path/to/hg38.fa \
  --sample SAMPLE01 --build hg38 \
  --outdir /path/to/SAMPLE01-haplotypes \
  --threads 2 --simulations 10000 --seed 17
```

Use `--backend docker` for the same command with the current image. Add `--ffpe` for FFPE libraries; C→T/G→A damage-context warnings remain visible. All outputs carry the sample prefix. An existing nonempty output directory is preserved; choose a new directory for another assessment. This command does not rerun alignment or change VCFs, genotypes, caller FILTER values, or the project's completed stages.

The first implementation assesses **PASS, heterozygous, biallelic SNVs**. Indels, multiallelic sites, failed filters and missing/nonheterozygous genotypes receive explicit unassessed statuses. Read-level indel validation needs a separate local-realignment method and is not approximated by this SNV test.

Multiple records at the same chromosome and position are also unassessable by
this biallelic model. For example, a caller may represent `A→C` and `A→T` as two
separate records. OncoTracer retains both in the assessability table with
`not_assessed_multiallelic_site`, excludes that position from linkage testing,
and continues with other eligible sites. Repeated records for the same allele
receive `not_assessed_duplicate_record`. No allele is chosen or silently
deduplicated, and the original caller VCF stays unchanged. These cases do not
fail an analysis. Summary counts report every input record, including sites
excluded from testing; an assessment with no eligible pairs still reports that
absence explicitly. Reference mismatches and damaged input files remain errors.

## What the numbers mean

For each candidate pair within `--max-distance` (default 5,000 bases), OncoTracer counts REF/REF, REF/ALT, ALT/REF and ALT/ALT fragments. It excludes unmapped, secondary, supplementary, duplicate and QC-failed alignments, unknown mapping quality, and bases/alignments below the selected thresholds. Overlapping mates count once; conflicting observations are discarded. Unmarked PCR duplicates can still violate independence.

At least six spanning fragments and two observations of each marginal allele are required. Otherwise the result is **insufficient evidence**, not a false variant or a reference genotype. Variants with no nearby eligible partner are listed separately. Input VCF phase-set agreement is reported, but is not treated as independent truth.

| Output | Meaning |
| --- | --- |
| `mc_linkage_p` | Monte Carlo tail probability of at least the observed ALT/ALT count under random pairing, preserving allele totals within read-group/strand strata. A plus-one correction prevents zero simulation p-values. |
| `mc_ci95_low/high` | Wilson interval for Monte Carlo sampling uncertainty, not a confidence interval for germline probability. |
| `exact_linkage_p` | Exact conditional hypergeometric/convolution cross-check of the same null model. |
| `linkage_q_by` | Benjamini–Yekutieli adjustment across assessable pairs in this invocation, allowing dependent tests. |
| `germline_probability` | Always empty/null: this experimental method does not estimate it. |

Candidate pairs are selected before inspecting their BAM allele counts. The seed is derived reproducibly for each pair. Counts, thresholds, software versions, input fingerprints and method hashes are retained. The default candidate limit is 100,000 pairs; exceeding it stops explicitly instead of silently dropping pairs. `--threads` controls decompression and optional local-model CPU use; the statistical loop is CPU-based.

FFPE damage, mapping errors, repeated sequence, unmarked duplicates and copy-number changes can violate the null model. Neither a small p-value nor a database annotation proves that a call is real or inherited. No low-pass clinical calibration is claimed. Statistical tests include exact-vs-simulation comparisons, strand-confounding controls, sparse coverage, duplicate/mate handling and input-preservation checks. A real ONT validation region produced 2,166 pairs, of which 1,961 were unassessable and 205 had no significant cis linkage after correction; this is a functionality check, not an accuracy benchmark.

## Optional online annotation

By default, the report provides VarSome links without making network requests. Add `--varsome` to explicitly allow coordinate/allele lookups through the [official VarSome API](https://github.com/saphetor/varsome-api-client-python). It sends chromosome, position, reference/alternate alleles and genome build—never BAM reads, sample names, ages or other patient metadata. Variant coordinates themselves are genomic information; enable this only when that sharing is appropriate for your study.

```bash
oncotracer haplotype-evidence --backend conda \
  --bam /path/to/SAMPLE01.bam --vcf /path/to/SAMPLE01.vcf.gz \
  --reference /path/to/hg38.fa --sample SAMPLE01 --build hg38 \
  --outdir /path/to/SAMPLE01-haplotypes-annotated \
  --varsome --max-online-variants 20
```

Single queries can be throttled. If you have API access, set `ONCOTRACER_VARSOME_API_KEY` in your environment; do not put it in a command file, project YAML, or GitHub. Provider access and charges remain governed by your [VarSome plan](https://docs.varsome.com/en/varsome-api-packages-explained). The explicit request cap defaults to 20; variants are ordered by linkage p-value, then variant key. Requests stop on authentication, quota or service errors. Failed lookups remain visible and do not erase the statistical assessment. Full annotation responses, source URLs, response hashes and retrieval dates are retained locally for review.

## Annotation-only local LLM

Add `--llm-report` to the online-annotation command to let the existing local model select useful **annotation fact IDs** for display. Add `--gpu` after [installing and checking GPU support](gpu.md) to use reserved GPU memory for this selection. The LLM receives bounded annotation facts, runs locally, and cannot submit extra queries. It cannot write diagnoses, classifications, probabilities or report prose: unknown IDs and extra fields are rejected. OncoTracer renders the original database values and citations, and prints Monte Carlo results directly from the numerical calculation. If model selection fails, the sourced facts remain available in deterministic order.

## Scientific context

[Read-based phasing](https://pmc.ncbi.nlm.nih.gov/articles/PMC4908360/) links alleles physically; it differs from pedigree-based inheritance inference. [MosaicForecast](https://pmc.ncbi.nlm.nih.gov/articles/PMC7065972/) demonstrates that phasing can help distinguish mosaic variants when combined with additional features and validation. Its published high-depth models are not transferred to low-pass data here. OncoTracer's conditional linkage assessment is a separate experimental implementation and is not endorsed by those projects.
