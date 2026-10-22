# Complete Illumina short variants with GLIMPSE2

Selecting short variants in OncoTracer now runs the bundled complete Nextflow workflow. GLIMPSE2 is mandatory for Illumina; the ONT branch retains its technology-specific callers. The native CNA workflow still calls this backend when short variants are enabled.

## Launch

```bash
oncotracer --interactive --backend conda
nextflow run main.nf --interactive --backend conda
nextflow run main.nf --analysis variants --config /absolute/path/run.yml
nextflow run main.nf --analysis full --config /absolute/path/run.yml
```

The interactive commands open the browser for either Fresh or FFPE specimens. Choose or confirm Illumina, then select **Fresh** or **FFPE** and enable **Small variants**. Use `--backend docker` for your installed image. Save and check before explicitly starting analysis. The variants route starts from existing coordinate-sorted BAM files; the full route performs configured upstream analyses first. Remote `nextflow run cfarkas/oncotracer -r REVISION` uses exactly the same entry. It downloads code to the local machine, not patient data to a remote service. Use a published revision containing this migration. See [entry details](nextflow_entry.md).

## Requirements

Use Linux, Java/Nextflow, samtools, bcftools, GATK, Perl, curl or aria2c, and Python with the variants/report dependencies. Alignment additionally requires bwa and fastp. FFPErase has a separate environment and model requirements described in [variants](variants.md). ANNOVAR and its licensed local database must be supplied separately; the workflow can use an installed offline VEP database as the annotation fallback. It does not send patient variants to online annotation services.

```bash
python3 -m pip install '.[variants,reports]'
```

Choose that interpreter with `--python /absolute/path/to/env/bin/python`. Local report generation requires cached Safetensors weights or `variant_llm_model: /path/to/model`. The default is Qwen/Qwen3-4B-Instruct-2507, revision cdbee75f17c01a7cc42f958dc650907174af0554. If needed, provision the public weights before analysis:

```bash
hf download Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554
```

Model inference is local and uses only supplied evidence. Rejected generated paragraphs fall back to deterministic interpretations. Model metadata belongs to provenance rather than the clinical narrative.

## Existing BAM configuration

```yaml
mode: illumina
run_variants: true
outdir: /data/new_results
threads: 64
variant_parallel_samples: 4
variant_specimen_type: fresh
variant_bam_manifest: /data/samples.bam.tsv
variant_reference: /data/GRCh38/genome.fa
variant_resource_dir: /data/public_resources
variant_reuse_from: /data/previous_oncotracer_run
variant_reference_reuse_from: /data/completed_glimpse_sample
variant_confidence_gwas: medium
variant_memory: 256GB
variant_annovar_dir: /tools/annovar
variant_annovar_db: /tools/annovar/humandb
variant_patients: /data/patients.tsv
```

Manifest columns are `sample`, `bam`, `status`; status is `normal`, `tumor`, or `unknown`. BAMs must have a matching unique sample in their read groups. Patient metadata accepts sample, name, age/birth year and sex. Missing age/sex does not discard a sample. Sex inferred from sequencing is recorded as inferred. Age is never inferred from sequence.

`variant_reuse_from` imports completed direct-call VCFs only when their receipt hashes, indexes and BAM identity match. Input BAMs and donor callsets remain unchanged. Existing original filters remain available in the evidence trail. An explicit tumor/normal pairing recomputes somatic calling using that normal instead of importing tumor-only calls.

`variant_reference_reuse_from` shares only verified prepared GLIMPSE reference panels: code, reference, panel checksums, target-sample exclusion and outputs must agree. No other individual's phased or imputed genotypes are reused. `variant_analysis_reuse_from: /data/previous_glimpse_results` also imports each sample's own verified completed tasks into a new output directory after a code upgrade. Source sample identities, inputs, methods and output receipts must match; resource allocation changes alone do not invalidate scientific results. The previous output directory must remain available. Normal continuation independently verifies each sample's own work. Do not use `--force` for an ordinary restart.

## Analysis and output

Each sample runs direct calling/imports, GLIMPSE2 phase/ligate, ClinVar and functional annotations, direct-read/imputation comparison, traits/GWAS, polygenic scoring with exact-allele matching and reference comparisons, ancestry and lineage evidence, mitochondrial/archaic/somatic annotations, ROH, focused cancer/cardiomyopathy reports, and annotated VCF export. Resources are downloaded when absent with resumable transfers and integrity checks.

Main entry: `OUTDIR/08_variants/glimpse2/START_HERE.html`. Sample reports are below `samples/SAMPLE/START_HERE.html`, task logs below `launch/SAMPLE/`, and cohort VCFs below `cohort/`. FFPE samples add `samples/SAMPLE/ffpe_review/START_HERE.html`, the final FFPE decision view. Earlier narrative reports are explicitly identified as preceding that adjudication.

Samples can execute concurrently with `variant_parallel_samples` (default `auto`), also available as `nextflow run main.nf --parallel_samples 4` or native `oncotracer variants --parallel-samples 4`. `threads` and `variant_memory` are total analysis budgets, divided between concurrent samples; 64 threads and 256 GB with four samples allocate 16 threads and 64 GB to each. Automatic mode allows 32 GiB per concurrent sample for local report inference and adapts concurrency to available CPU/RAM. An explicit numeric count overrides these recommendations up to the cohort size; insufficient memory can cause failures. These are task scheduling budgets, with additional controller and filesystem cache overhead. Each sample retains its own Nextflow cache and reports. The cohort VCF is created only after all requested samples finish successfully. The repository gateway is a foreground local controller; it is not a cloud scheduler. Nested Nextflow owns work reuse rather than caching the entire controller invocation.

## FFPE decisions

FFPErase is applied to directly observed calls. Imputed-only sites are not substituted as model input. A flagged variant is excluded from the accepted VCF unless all rescue criteria pass: exact allele/reference match, alternate imputed genotype, GP at least 0.99, INFO at least 0.9, at least three alternate reads, depth at least eight, compatible allele balance and PASS caller evidence. Such a result remains a germline rescue candidate and is excluded from population summaries pending confirmation. All calls remain in the audit VCF with explicit votes/reasons. Sequencing and imputation are not independent experiments.

GLIMPSE2 cannot establish somatic origin or prove a C>T artifact false. FFPErase was evaluated at substantially higher depths than approximately 2x sequencing; this combined rescue rule is a transparent research heuristic requiring validation, not a demonstrated diagnostic improvement. See the [GLIMPSE2 study](https://www.nature.com/articles/s41588-023-01438-3) and [FFPErase study](https://www.nature.com/articles/s41467-025-65654-7).

## Cohort semantics

The all-evidence VCF preserves sample-specific calls/filters/annotations; the population view masks somatic, unconfirmed and excluded FFPE calls and recalculates AC/AN/AF. Missing genotypes are not replaced with 0/0. Exact-allele high-quality GLIMPSE genotypes can fill absent sites only under documented safeguards. Matched tumor/normal pairs retain both columns in all-evidence, but the tumor is excluded from the population count to avoid counting one individual twice. Unpaired tumors remain explicitly labeled; they are not assumed to be independent people when pairing is unknown.

This is a validated merge of per-sample calls, not joint variant discovery. Indels and alleles absent from the SNP imputation panel require additional cohort genotyping if a dense population matrix is needed. See [cohort details](GLIMPSE_COHORT.md).

## Validation scope

Tests cover browser routing through Nextflow, compulsory GLIMPSE2, source-preserving BAM/VCF import, continuation, checksum rejection, shared-reference reuse, FFPE votes, exact-allele merging and missingness. `tests/run_glimpse_bam_smoke.py` exercises a small artificial chromosome through the real native Nextflow workflow, actual GLIMPSE2, report generation and a second continuation run. It is a technical integration test, not biological or clinical validation.
