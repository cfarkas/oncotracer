# Parallel samples: quickstart and technical parity checks

The complete Illumina short-variant workflow runs GLIMPSE2 for every selected sample. `--parallel_samples` controls how many sample workflows can run together; `--threads` controls the **total CPU budget**, shared between them. The native CLI spelling is `--parallel-samples`.

## Open the visual setup

From a local checkout:

```bash
cd /path/to/oncotracer
nextflow run main.nf --interactive --threads 64 --parallel_samples 4
```

Or fetch the migration branch from GitHub and execute locally:

```bash
nextflow run cfarkas/oncotracer \
  -r feat/glimpse2-complete-short-variants -latest \
  --interactive --threads 64 --parallel_samples 4
```

`--interactive` opens the browser interface. Select Illumina and complete short-variant analysis, then open **Resources and advanced settings**. Set **Total analysis memory** to `256GB` and **Samples running at once** to `4`, review the samples and start. With at least four samples, this allocates 16 CPU threads and 64GB to each active sample. Choose totals appropriate to the server, leaving room for the operating system and other work.

The complete workflow reserves at least **32GB per active sample**, including its local report model. The default total memory is `32GB`, so requesting four simultaneous samples without increasing memory still runs one at a time. Active slots are capped by the requested concurrency, sample count, CPU budget and memory budget. These are task scheduling budgets; controller and filesystem-cache overhead are additional. GLIMPSE2 remains enabled.

`--python` is optional: the default is `python3` from the active environment. Add `--python /path/to/env/bin/python` when you need a particular installed interpreter. It must have OncoTracer's Python dependencies. Required external tools, local report weights and optional licensed annotation databases are described in the [migration guide](GLIMPSE_MIGRATION.md).

## Run existing BAMs from a configuration

Create `/data/analysis/samples.bam.tsv` with literal tab separators:

```tsv
sample	bam	status
SAMPLE_A	/data/alignments/SAMPLE_A.bam	normal
SAMPLE_B	/data/alignments/SAMPLE_B.bam	tumor
```

The BAMs must be coordinate sorted and their read-group sample names must match the manifest. Roles do not imply tumor–normal pairing: configure a matched normal only when the specimens are known to come from the same individual. Add further rows to use more sample slots.

Create `/data/analysis/run.yml`:

```yaml
mode: illumina
run_variants: true
outdir: /data/analysis/results
threads: 64
variant_parallel_samples: 4
variant_memory: 256GB
variant_specimen_type: fresh
variant_bam_manifest: /data/analysis/samples.bam.tsv
variant_reference: /data/reference/GRCh38/genome.fa
variant_resource_dir: /data/analysis/public_resources
```

This minimal configuration assumes required tools and the default report model are already discoverable. Supply `variant_tool_prefix`, `variant_llm_python`, `variant_llm_model`, ANNOVAR paths or patient metadata when your installation needs them; see the migration guide. For FFPE, choose `variant_specimen_type: ffpe` and supply the required FFPErase environment/resources and applicable license acceptance.

Launch locally:

```bash
cd /path/to/oncotracer
nextflow run main.nf --analysis variants \
  --config /data/analysis/run.yml --threads 64 --parallel_samples 4
```

Or use the same configuration through GitHub:

```bash
nextflow run cfarkas/oncotracer \
  -r feat/glimpse2-complete-short-variants -latest \
  --analysis variants --config /data/analysis/run.yml \
  --threads 64 --parallel_samples 4
```

For reproducibility, replace the branch with the commit SHA you tested. Remote invocation downloads pipeline code and runs on your machine; it does not upload sequencing data. `--analysis full` uses an existing full OncoTracer configuration and applies this concurrency setting to its Illumina short-variant branch. Values above one are rejected for ONT or a full analysis without that branch.

For visual review of an existing BAM configuration:

```bash
nextflow run main.nf --interactive --config /data/analysis/run.yml \
  --threads 64 --parallel_samples 4
```

The browser preserves the source inputs and prepares a new project directory.

## Reuse and restart

An ordinary restart uses **the same launch command and configuration**, with the same compatible code revision and output directory. The backend automatically requests `--continue` for each sample. Do not delete its work directories, and do not use `--force` for continuation. Stop the previous controller before launching another instance.

When migrating to a new output directory, these optional YAML fields have different scopes:

| Setting | Reusable content | Required identity |
|---|---|---|
| `variant_analysis_reuse_from: /data/previous_glimpse_results` | Each sample's own verified completed workflow tasks, including eligible imputation chunks | Sample, input BAM/reference, analysis method, executable settings and completed outputs must match |
| `variant_reuse_from: /data/previous_oncotracer_run` | Native direct-call germline/somatic VCFs with completed receipts | VCF/index hashes and source BAM identity must agree; original filters are preserved |
| `variant_reference_reuse_from: /data/completed_glimpse_sample` | Prepared GLIMPSE2 reference panels shared between samples | Reference/panel checksums, preparation code/settings, excluded reference samples and output validation must agree |

Allocation changes alone do not invalidate scientifically identical tasks. Changing the actual GLIMPSE thread count, read-quality settings, inputs or other analysis parameters may require recomputation. Never reuse another person's phased or imputed sample genotypes. Keep donor directories available while the new run references their files.

The overview is:

```text
/data/analysis/results/08_variants/glimpse2/START_HERE.html
```

Individual reports are in `samples/SAMPLE/START_HERE.html`; logs and launch parameters are in `launch/SAMPLE/`. A joint cohort VCF is produced under `cohort/` only after every requested sample succeeds. This merges per-sample calls; it is not joint variant discovery. Missing genotypes remain missing. See [cohort semantics](GLIMPSE_COHORT.md).

## Technical parity checks

Run from the checkout using a Python environment with the test dependencies installed. The following regression tests cover preserved behavior and the new scheduling contract:

| Contract checked | Exact test |
|---|---|
| Local Nextflow still routes `--interactive` to the visual setup and propagates child failures | `tests.test_nextflow_gateway.NextflowGatewayTests.test_real_nextflow_visual_routing_and_failed_analysis_propagation` |
| Concurrency flags reach visual setup, variants and full-run commands | `tests.test_nextflow_gateway.NextflowGatewayTests.test_parallel_sample_override_reaches_visual_variants_and_full_cli` |
| Every Illumina sample retains the complete set of enabled modules | `tests.test_glimpse_pipeline.GlimpsePipelineTests.test_sample_parameters_enable_every_module_and_keep_paths` |
| Bounded overlap, visible queued samples and cohort merge only after all complete | `tests.test_glimpse_parallel.ParallelSamplesTests.test_overlap_respects_limit_all_statuses_visible_and_merge_after_all` |
| Stopping prevents queued launches and terminates the real child process group | `tests.test_glimpse_parallel.ParallelSamplesTests.test_sigterm_prevents_queued_launches_and_restores_signal_handlers` and `test_independent_runner_environment_and_real_orphan_child_cancellation` in the same class |
| A failed sample preserves successful samples and suppresses an incomplete cohort | `tests.test_glimpse_parallel.ParallelSamplesTests.test_one_failure_keeps_other_samples_and_suppresses_partial_cohort` |
| Eligible completed imputation survives a larger scheduler budget | `tests.test_glimpse_multisample_reuse.MultisampleReuseTests.test_completed_chunks_and_ligation_survive_larger_scheduler_budget` |
| Sample genotypes cannot be reused across individuals | `tests.test_glimpse_multisample_reuse.MultisampleReuseTests.test_sample_specific_chunks_never_cross_individuals` |
| Actual GLIMPSE thread changes and read-quality changes invalidate reuse | `tests.test_glimpse_multisample_reuse.MultisampleReuseTests.test_actual_glimpse_thread_count_change_requires_recomputation` and `test_read_quality_change_requires_recomputation` in the same class |
| Imported direct calls preserve their genotype and original filter | `tests.test_glimpse_pipeline.RealBamImportTests.test_normalized_call_import_keeps_original_filter_and_genotype` |

Reproduce the focused checks:

```bash
python3 -m unittest \
  tests.test_nextflow_gateway tests.test_glimpse_parallel \
  tests.test_glimpse_multisample_reuse tests.test_glimpse_pipeline \
  tests.test_glimpse_ui tests.test_glimpse_ffpe tests.test_glimpse_joint
```

Run the full suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests requiring unavailable external tools may be skipped; inspect the final test summary. An additional real Nextflow/GLIMPSE2 smoke run uses pre-existing **synthetic** integration fixtures:

```bash
python3 tests/run_glimpse_bam_smoke.py \
  --fixture-workflow /path/to/workflow_with_completed_synthetic_fixtures \
  --output /path/to/new_synthetic_smoke
```

That fixture directory must contain `tests/fixture`, completed `tests/comprehensive_integration` outputs and installed GLIMPSE2 binaries. The smoke checks real BAM/VCF import, imputation and downstream reports while checking that source input hashes stay unchanged. The focused reuse tests above separately check continuation; repeat the generated Nextflow command in the smoke output to exercise a real restart. It does not assess real ancestry projection, licensed gene annotation or local LLM inference.

These checks establish technical behavior, not completed biological equivalence of a ten-person real cohort. A completed real-cohort comparison has not been claimed. Parallel scheduling does not itself improve clinical validity, and GLIMPSE2 support does not prove somatic origin or validate an FFPE artifact rescue.
