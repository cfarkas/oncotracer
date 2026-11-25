# Configurations from the version 5 benchmark

These examples come from the three real runs measured on 19–23 November 2026.
The original YAML hashes match the configuration hashes recorded at launch.
The [benchmark report](../../docs/benchmarks/2026-11-23.md) describes the build,
hardware, timings and QC results; the [front-page commands](../../README.md#real-benchmark-runs)
use these settings with the released version 5 image.

| Run | Configuration | Input tables | Samples |
| --- | --- | --- | ---: |
| ONT | [run.yml](ONT/config/run.yml) | [sample metadata](ONT/config/sample_metadata.csv), [tissue and POD5 routing](ONT/config/methylation_tissues.tsv) | 21 |
| FFPE Illumina | [run.yml](FFPE/config/run.yml) | [samplesheet](FFPE/config/samplesheet.csv), [sample metadata](FFPE/config/sample_metadata.csv), [patient-context template](FFPE/config/patients.tsv) | 26 |
| Fresh Illumina | [run.yml](Fresh/config/run.yml) | [samplesheet](Fresh/config/samplesheet.csv), [sample metadata](Fresh/config/sample_metadata.csv), [patient-context template](Fresh/config/patients.tsv) | 11 |

## Use with your data

1. Copy the appropriate `ONT`, `FFPE` or `Fresh` directory into your project.
2. Replace every `/path/to/...` value in its YAML and input tables with your own
   absolute paths. Update the sample identifiers consistently in every file.
   The ONT layout has one named library folder per sample, containing
   `fastq_pass/` and `pod5_pass/`. The Illumina sheets list paired FASTQs.
3. Install the chosen backend and prepare the references, callers and models
   described in the [version 5 run guide](../../docs/releases/5.0.0.md).
   The examples retain `variant_download_resources: false`, as the benchmark
   used resources prepared before the timed run.
4. Adjust the total thread, sample-concurrency and memory budgets for your
   machine. Set matching values in the YAML and command line. The benchmark's
   Linux CPU affinities and memory ceilings were applied separately.
5. Validate the edited configuration, then use its front-page run command:

```bash
oncotracer check --config /path/to/ONT/config/run.yml
oncotracer check --config /path/to/FFPE/config/run.yml
oncotracer check --config /path/to/Fresh/config/run.yml
```

Run the check for the project you selected. Placeholder paths must be replaced
before validation can succeed. Raw study reads and patient records are not
distributed with these examples.

For ONT, `gpu_usage: false` keeps the main workflow on CPUs while
`methylation_gpu_usage: true` enables Dorado/Rockfish GPU execution. Do not add
`--cpu` to that command: it overrides the methylation GPU setting too.
The tissue table preserves the benchmark's 13 blood/marrow and eight CNS routes;
replace those tissues with your own sample metadata. MARLIN handles blood/marrow
and Sturgeon handles CNS. These are routing choices, not predicted diagnoses.

## Changes made for the public examples

Analysis settings, model checksums, sample order, tumor/normal roles and cohort
sizes are retained. Server paths and sample identifiers are replaced with
placeholders. FASTQ entries show the expected file layout; patient-context
templates leave names, ages and sex blank. The original clinical context is
not included.

Both Illumina YAMLs explicitly include `cna_profile: standard`, which the
recorded commands supplied as an override. The ONT YAML retains the benchmark's
`cna_profile: sensitive`. The public ONT example sets
`sturgeon_license_acknowledged: false`; the benchmark had already acknowledged
that license. Review the Sturgeon terms and set it to `true` only after your
own acknowledgement.

The [configuration provenance](provenance.json) records the original hashes,
benchmarked source commit and changed YAML fields. These are adaptations of
the measured configurations, not byte-identical copies of private projects.
