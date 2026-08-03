# Boundary Refinement

Boundary refinement is stage `02_bam_refinement` of every standard Illumina and ONT run. After qDNAseq or ichorCNA identifies broad CNA segments, this stage evaluates local BAM depth and tests whether each coarse boundary should move.

When the evidence is insufficient, OncoTracer keeps the original boundary. Refinement does not by itself prove that a CNA is biologically real.

## Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

## Most users: keep the defaults

A minimal Illumina YAML still runs refinement:

```yaml
mode: illumina
lpwgs_root: project
outdir: project/results/sample_a
illumina_samplesheet: project/config/illumina.samplesheet.csv
force: false
```

The main refinement result is:

```text
outdir/02_bam_refinement/
└── illumina_qdnaseq_100kb/ or ONT_ichorcna_500kb/
    ├── 01_tables/
    └── 04_final_results/
        └── final_segments.tsv
```

Use the tested defaults for routine work. Write any methods comparison to a new `outdir`.

## Optional settings

Add refinement settings to the same run YAML:

```yaml
mode: illumina
lpwgs_root: project
outdir: project/results/sample_a_conservative
illumina_samplesheet: project/config/illumina.samplesheet.csv
force: false

fine_bin_kb_illumina: 20
min_mapq: 30
min_local_log2_diff_illumina: 0.15
min_bic_gain: 8
permutations: 500
permutation_p: 0.05
accept_rule: p_and_bic
```

## Main parameter groups

### Resolution and search area

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `fine_bin_kb_illumina` | `10` | Local Illumina depth-bin width in kb |
| `fine_bin_kb_ont` | `25` | Local ONT depth-bin width in kb |
| `search_radius_bins` | `2` | Coarse bins searched on each side |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum accepted confidence-interval width |

### Read and signal filters

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `min_mapq` | `20` | Minimum read mapping quality |
| `min_local_log2_diff_illumina` | `0.10` | Minimum local Illumina depth step |
| `min_local_log2_diff_ont` | `0.12` | Minimum local ONT depth step |
| `min_adjacent_seg_delta` | `0.10` | Minimum adjacent coarse-segment difference |
| `min_bic_gain` | `6` | Minimum model-fit improvement |

### Statistical acceptance

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `permutations` | `300` | Empirical permutations; `0` disables them |
| `permutation_p` | `0.05` | Largest accepted empirical p-value |
| `accept_rule` | `p_and_bic` | Requires empirical and BIC evidence |

### ZIPcnv comparison

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `zipcnv_mode` | `adapted` | Select adapted, official, both, or off |
| `zipcnv_window_bins` | `5` | Local adapted ZIPcnv window |
| `zipcnv_k` | `0.05` | Adapted ZIPcnv tuning constant |
| `zipcnv_min_segment_bins` | `3` | Minimum retained segment length |
| `zipcnv_min_abs_log2` | `0.25` | Minimum retained absolute signal |

## Reproducible public-data comparison

First prepare the public Illumina test and copy its generated YAML:

```bash

# Download and validate the small public test data.
nextflow run main.nf --make_test \
  --test_root "test"

# Copy the generated Illumina YAML to a new methods-comparison file.
cp "test/configs/illumina.quickstart.yml" \
  "params/illumina.conservative.yml"

# Edit only the copied YAML.
nano "params/illumina.conservative.yml"
```

Change `outdir` to `test/runs/illumina_conservative`, then add:

```yaml
fine_bin_kb_illumina: 20
search_radius_bins: 2
min_mapq: 30
min_local_log2_diff_illumina: 0.15
min_bic_gain: 8
permutations: 500
permutation_p: 0.05
accept_rule: p_and_bic
```

```bash

# Inspect, check, and run the conservative comparison.
sed -n '1,180p' "params/illumina.conservative.yml"
nextflow run main.nf -stub-run --docker \
  -params-file "params/illumina.conservative.yml"
nextflow run main.nf --docker \
  -params-file "params/illumina.conservative.yml" \
  -work-dir "test/work/illumina_conservative" \
  -resume
```

Compare the final tables:

```bash
# Set the standard repository and result paths.
DEFAULT="test/runs/illumina/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv"
EXPERIMENT="test/runs/illumina_conservative/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv"

# Confirm and compare both outputs.
ls -lh "$DEFAULT" "$EXPERIMENT"
diff -u "$DEFAULT" "$EXPERIMENT"
```

Predefine the comparison, retain the default run, and report every non-default setting. Do not tune parameters toward a desired diagnosis.
