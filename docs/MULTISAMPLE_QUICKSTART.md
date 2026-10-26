# Run multiple samples together

OncoTracer queues samples and starts the next one when a slot is free. This works
for **Illumina and ONT alignment**, **ONT variant calling**, and complete
**Illumina/FFPE variant analysis with GLIMPSE2**. Illumina variants retain their
Nextflow workflows; native stages use cancellable sample workers.

### Safe startup for parallel Illumina and FFPE jobs

Version 2.1.1 prepares Nextflow once in the environment the sample workers will
actually use, before starting their workflows. It checks the distribution,
publishes it atomically, and gives each JVM a separate temporary directory.
This covers an empty cache and installations where `/tmp` and the output folder
are on different filesystems. Conda and Docker use the same preparation code.

An interrupted download is never marked ready. If a managed cached JAR changes,
OncoTracer prepares a new generation without overwriting files used by another
run. Preparation failures stop variant sample launch with an error. Sequencing
results and Nextflow work directories are separate from this runtime cache.

The regression tests include simultaneous startup with an empty private cache,
damaged downloads, separate controller processes and real Nextflow tasks.

## Detect sample-job combinations first

```bash
oncotracer --detect-resources
# Equivalent spelling:
oncotracer --detect_resources
```

This read-only command suggests **parallel sample jobs × cores per sample** using
available CPUs and RAM. For example, a machine with 40 available CPU threads and
enough RAM can offer **5 sample jobs × 8 cores each** or **4 sample jobs × 10 cores each**.
These are choices of simultaneous samples, not a count of “parallel cores”.
Cores here mean CPU threads available to OncoTracer. No analysis starts and no
configuration is changed. Requires the current GitHub installation; PyPI 2.1.0
predates these aliases.

To plan within a shared-server budget or limit to your cohort size:

```bash
oncotracer --detect-resources --threads 40 --memory 160GB --samples 10
```

Use `--json` for machine-readable results, or `--config /path/to/project/config/run.yml`
to plan for saved settings. The browser shows the same allocation rules and lets
you choose; the command does not select or launch a combination for you.

## Choose resources in the browser

```bash
oncotracer --interactive --backend conda
```

From a source checkout, Nextflow opens the same interface:

```bash
nextflow run main.nf --interactive --backend conda
```

For Docker, use `--backend docker` with the current installed image.

1. Choose **New analysis** or **Continue an existing analysis**.
2. For a new project, discover and assign samples. For continuation, choose the
   **output project** containing `config/run.yml` and `results`, then **Load saved settings and check**.
3. In **Choose parallel sample jobs**, inspect the sample-job combinations.
   For continuation these controls appear inside **Review and run**, beside Run.
   Set a **total** CPU budget and a RAM budget (`auto` or, for example, `64GB`).
4. Select the number of parallel samples. Each option shows **sample jobs × cores each**,
   plus RAM per sample. Or select **Manual** and enter any positive whole number.
   Manual counts are kept even beyond the recommended CPU/RAM fit; warnings explain
   the risk of slower processing or memory failures. **Detect sample-job options**
   refreshes suggestions without replacing your manual count.
5. Choose **Run analysis**. For continuation, Run applies and checks your resource choices
   before starting. **Apply resources and check** is an optional preview; it does not
   launch analysis. Loading a project also does not start analysis.

For example, 20 CPU threads, 64GB and two concurrent samples give each active
sample 10 CPU threads and 32GB. The mapper and sorter share their sample's CPU
allocation. Alignment and variant calling use this queue; cohort-wide CNA, merging
and report stages may have fewer or no independent sample jobs. A configured count
is the maximum number of active sample workflows, not a claim that every slot is
currently computing. Native sample commands are retained in the main trace and in separate
sample logs.

## Follow parallel jobs in the terminal

The current GitHub version shows a live progress display automatically in a
terminal, including when you click **Run analysis** in the browser. It works with
Conda and Docker. No extra option or `tqdm` installation is required.

```text
OncoTracer | Alignment | elapsed 00:12:30
[======              ] 3/10 done | 2 running | 5 queued | 0 incomplete
Sample slots: 2 | CPU threads/sample: 10 | memory/sample: 32GB
SAMPLE                        TASK / STATE                              ELAPSED
SAMPLE_D                     [  =   ] illumina-align-SAMPLE_D             00:02:14
SAMPLE_E                     [   =  ] illumina-index-SAMPLE_E             00:00:46
```

This is an illustrative display. The bar counts finished **sample workflows in
the current stage**. Each running row shows its actual current command and elapsed
time. Moving bars indicate activity; individual commands do not report a reliable
percentage or finish time. Illumina variants show one active Nextflow workflow
per sample, followed by FFPE review when selected. The full nested task output
remains in that sample's Nextflow log. Cohort preparation, CNA and reporting can
run between sample queues.

Tool messages go to the **Full log** path printed by the display. Browser launches
keep `logs/web-analysis*.log`; direct launches create `logs/terminal-analysis-*.log`
when the project already has a logs folder, otherwise a temporary log at the
printed path. Redirecting output to a file keeps ordinary text without animation.

At the end, **every selected sample** appears in a table:

```text
Samples: completed 2/3; incomplete 1/3.
SAMPLE    STATUS          REMAINING ANALYSIS
SAMPLE_A  COMPLETE
SAMPLE_B  COMPLETE
SAMPLE_C  INCOMPLETE      Small variants (not_started)
```

Complete means all requested analyses for that sample are complete; alignment
alone is not enough. Failed, interrupted and queued work is incomplete. Any
unfinished cohort outputs are listed separately. If stopped before final results
are published, samples are marked incomplete for that attempt and retained stages
are checked on continuation. The progress display never authorizes output reuse.

## How limits are selected

OncoTracer checks CPU affinity, Linux container/cgroup quotas and currently
available RAM. It leaves 20% of available RAM for the system, caches and
controllers. Automatic memory budgets are proportional to the requested share of
CPUs, with enough for at least one sample when available. Automatic concurrency
prefers at least eight CPU threads per sample. An explicit count overrides the
CPU/RAM recommendation, capped only by the number of selected samples. Each job
receives at least one CPU thread, so selecting more jobs than CPU threads
oversubscribes the CPU budget.

Complete Illumina variants use a **32 GiB per sample recommendation** for
the genomic workflow and local report model. Native alignment and ONT calling use
an **8 GiB planning minimum**. These are scheduling estimates, not measured upper
bounds on every tool. Other processes share the server: independent analyses do
not reserve CPUs against each other. Set totals accordingly. Resources are checked
again at execution. Less available RAM can reduce automatic concurrency or prevent
automatic launch. Manual concurrency stays as requested; each job receives a
smaller RAM share and may fail. A manual override does not guarantee tool success.

Existing explicit counts such as `variant_parallel_samples: 1` remain valid.
Choosing and applying new resources in Continue updates those limits while keeping
sample assignments and scientific settings. Valid completed stages are checked
before reuse. A BAM without a verified completion record and index is not a
completed alignment; it is recomputed. Changing only resource allocations does
not invalidate a verified alignment or native variant result from the supported
previous release.

## Headless and scripted use

Terminal setup accepts the same total limits:

```bash
oncotracer --interactive --terminal --backend conda \
  --threads 20 --parallel-samples 2 --variant-memory 64GB
```

A saved configuration can also use:

```yaml
threads: 20
parallel_samples: 2
analysis_memory: 64GB
variant_parallel_samples: 2
variant_memory: 64GB
```

To override concurrency for an existing configuration:

```bash
oncotracer run --config /path/to/project/config/run.yml --backend conda --parallel-samples 5
```

For Nextflow, use `--parallel_samples 5`. Use `auto` instead of a number to restore
automatic selection. CPU and RAM budgets are shared across
samples, not assigned in full to each sample. GLIMPSE2 remains enabled for every
Illumina variant sample. Only completed, compatible outputs are reused.

`--python` is optional: the controller defaults to `python3` from the active environment. Add `--python /path/to/env/bin/python` to select a different controller. Before analysis, OncoTracer tests scientific Python imports and can select the installed managed core interpreter when the lightweight CLI environment lacks those packages. The selected interpreter is passed to sample tasks, resource preparation, FFPE review and cohort export. Required external tools, local report weights and optional licensed annotation databases are described in the [migration guide](GLIMPSE_MIGRATION.md).

## Environment readiness before starting

Each real host, Conda or Poetry full run checks the runtime before preparing hg38 references or starting alignment. This applies to browser runs and both local and GitHub Nextflow commands. Managed installations undergo inventory validation and tool/library import probes; selected caller executables are tested before sequencing work. GLIMPSE2 startup also checks Nextflow/Java, scientific Python imports, the report interpreter and the local model configuration/tokenizer. Model checks stay offline and do not generate a report or load all model weights. Public analysis resources are still prepared by their workflow stages.

A broken executable, missing Python package or unavailable local report model stops startup with an environment error. Configuration validation and `--dry_run` remain separate, lightweight checks; a successful configuration check does not certify installed tools. Runtime checks cannot guarantee future network transfers or sample-specific analysis success.

Regenerated Python bytecode is accepted only when the existing cache compiles exactly from an unchanged inventoried source file using the verified interpreter. Source or binary changes, added/missing files, changed permissions and unverifiable caches still fail. No environment inventory is rewritten, and runtime Python processes disable bytecode writes.

## Run existing BAMs from a configuration

Create `/data/analysis/samples.bam.tsv` with literal tab separators:

```bash
mkdir -p /data/analysis
cat > /data/analysis/samples.bam.tsv <<'EOF'
sample	bam	status
SAMPLE_A	/data/alignments/SAMPLE_A.bam	normal
SAMPLE_B	/data/alignments/SAMPLE_B.bam	tumor
EOF
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
  -r main -latest \
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
| Native sample overlap, bounded budgets, incomplete alignment rejection and checkpoint reuse | `tests.test_sample_parallelism` |
| Browser API resource choices preserve sample assignments | `tests.test_sample_resource_web` |
| Firefox: new setup, both paths into continuation, editable jobs, invalid RAM recovery and Run dispatch | `tests.test_sample_resource_browser` |
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
  tests.test_nextflow_gateway tests.test_run_readiness \
  tests.test_runtime_bytecode_integrity tests.test_glimpse_readiness \
  tests.test_sample_parallelism tests.test_sample_resource_web tests.test_glimpse_parallel \
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

For a real two-sample alignment smoke test with an installed core prefix:

```bash
python3 scripts/smoke_parallel_alignment.py \
  --tool-prefix /path/to/core/environment \
  --work-dir /path/to/new/synthetic-check
```

This creates tiny synthetic Illumina and ONT reads, validates BAMs and sample
identities, and verifies reuse after changing CPU allocations. It requires at
least 20 GiB of available RAM for the production scheduler's two-slot minimum.
It does not replace whole-genome QuickStart parity or validate biological accuracy.

At the end, [sample completion counts](outputs.md#completed-and-incomplete-samples)
show completed/total and incomplete/total for the whole analysis and each
requested branch. Queued or interrupted samples remain in the total.
