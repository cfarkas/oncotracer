# Boundary Refinement

Boundary refinement is stage `02_bam_refinement` of **every standard Illumina and ONT run**. It is not an optional add-on and there is no enable flag. After qDNAseq or ichorCNA finds broad CNA segments, this stage examines local read depth in the aligned BAM and tests whether each coarse boundary should move to a finer coordinate.

If the BAM evidence does not meet the acceptance rules, OncoTracer keeps the original coarse boundary. Refinement tests support for moving a boundary; it does not by itself prove that a CNA is biologically real.

## Most users: keep the defaults

You do not need to add refinement settings to a YAML. This minimal file still runs refinement with the tested defaults:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
force: false
```

The main results are written below:

```text
outdir/02_bam_refinement/
└── illumina_qdnaseq_100kb/ or ONT_ichorcna_500kb/
    ├── 01_tables/
    │   └── refined_bins.tsv.gz
    └── 04_final_results/
        └── final_segments.tsv
```

Use the defaults for routine analysis. Change them only for a predefined methods experiment with controls, and write that experiment to a new `outdir`.

## Where optional settings go

Refinement settings are not another YAML. Add them to the same run YAML passed after `-params-file`:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/sample_a_conservative
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
force: false

fine_bin_kb_illumina: 20
min_mapq: 30
min_local_log2_diff_illumina: 0.15
min_bic_gain: 8
permutations: 500
permutation_p: 0.05
accept_rule: p_and_bic
```

## Parameter groups

### Resolution and search area

| Parameter | Type/unit | Default | What changing it does |
| --- | --- | ---: | --- |
| `fine_bin_kb_illumina` | positive integer, kb | `10` | Local Illumina read-depth bin width. Smaller values provide finer coordinates but usually increase noise and work. |
| `fine_bin_kb_ont` | positive integer, kb | `25` | Local ONT read-depth bin width. |
| `search_radius_bins` | non-negative integer, coarse bins per side | `2` | Search distance on each side of the original boundary. Larger values permit larger shifts. |
| `max_ci_fraction_of_coarse` | non-negative number, fraction | `1.0` | Maximum accepted confidence-interval width relative to one original coarse bin. |

### Read and signal filters

| Parameter | Type/unit | Default | What changing it does |
| --- | --- | ---: | --- |
| `min_mapq` | non-negative integer, MAPQ | `20` | Reads below this mapping quality do not contribute to local depth. |
| `min_local_log2_diff_illumina` | non-negative number, log2 ratio | `0.10` | Minimum Illumina local depth step. |
| `min_local_log2_diff_ont` | non-negative number, log2 ratio | `0.12` | Minimum ONT local depth step. |
| `min_adjacent_seg_delta` | non-negative number, log2 ratio | `0.10` | Skips a prior boundary when adjacent coarse segment levels are too similar. |
| `min_bic_gain` | number, BIC units | `6` | Minimum improvement in the local split model. Larger values are more conservative. |

### Statistical acceptance

| Parameter | Type/value | Default | Meaning |
| --- | --- | ---: | --- |
| `permutations` | non-negative integer | `300` | Number of empirical permutations. `0` disables the permutation calculation. More permutations take longer and improve p-value resolution. |
| `permutation_p` | number from `0` to `1` | `0.05` | Largest empirical p-value accepted by `p_and_bic`. |
| `accept_rule` | `p_and_bic`, `bic_only`, or `permissive` | `p_and_bic` | `p_and_bic` requires the empirical and model-fit evidence. The other modes ignore the empirical p-value and should be considered methods experiments. |

### ZIPcnv comparison

| Parameter | Type/value | Default | Meaning |
| --- | --- | ---: | --- |
| `zipcnv_mode` | `off`, `adapted`, `official`, or `both` | `adapted` | Selects the bundled adapted comparison, an attempted official run, both, or neither. |
| `zipcnv_window_bins` | positive integer, fine bins | `5` | Adapted ZIPcnv local window. |
| `zipcnv_k` | non-negative number | `0.05` | Adapted ZIPcnv tuning constant. |
| `zipcnv_min_segment_bins` | positive integer, bins | `3` | Smallest ZIPcnv segment retained. |
| `zipcnv_min_abs_log2` | non-negative number, log2 ratio | `0.25` | Smallest absolute ZIPcnv signal retained. |
| `zipcnv_compare_min_overlap` | number from `0` to `1` | `0.50` | Minimum reciprocal overlap used in the comparison. |

`zipcnv_mode: official` and `both` have additional upstream data expectations and are not recommended as a first run.

### Environment behavior

`refine_skip_install: false` is the default. Setting it to `true` asks the refinement helper to reuse an existing environment rather than update it. A missing environment is still created, and required packages may still be repaired. Leave this setting at its default unless you manage the environment deliberately.

## Reproducible public-data experiment

This example first prepares the public Illumina test, preserves its generated YAML, then creates a second conservative run. All configured paths remain below the generated `lpwgs_root`.

```bash
git clone https://github.com/cfarkas/oncotracer.git                    # clone a current repository
cd oncotracer                                                          # run main.nf from here
nextflow run main.nf --make_test                                       # download/reuse public reads and generate absolute-path YAML files
cp test/configs/illumina.quickstart.yml params/illumina.conservative.yml # preserve the generated default file
nano params/illumina.conservative.yml                                  # edit only the copy
```

In Nano:

1. Change `outdir` from `.../test/runs/illumina` to `.../test/runs/illumina_conservative`.
2. Add the block below at the end.
3. Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`.

```yaml
fine_bin_kb_illumina: 20             # use larger local bins
search_radius_bins: 2                # keep the default search range
min_mapq: 30                         # require more confidently mapped reads
min_local_log2_diff_illumina: 0.15   # require a stronger local depth step
min_bic_gain: 8                      # require more model-fit improvement
permutations: 500                    # improve empirical p-value resolution
permutation_p: 0.05
accept_rule: p_and_bic
```

Inspect the complete file, perform an optional wiring check, and run:

```bash
sed -n '1,180p' params/illumina.conservative.yml                      # verify the new outdir and settings
nextflow run main.nf -stub-run --docker -params-file params/illumina.conservative.yml # workflow-wiring check only
nextflow run main.nf --docker -params-file params/illumina.conservative.yml -resume   # real conservative run
```

Compare both final segment tables while retaining both YAML files as the record of what changed:

```bash
DEFAULT=test/runs/illumina/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv
EXPERIMENT=test/runs/illumina_conservative/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv
ls -lh "$DEFAULT" "$EXPERIMENT"                                      # confirm both results exist
diff -u "$DEFAULT" "$EXPERIMENT"                                    # inspect changed boundaries; no output means identical
```

!!! warning "Do not tune toward a desired diagnosis"
    Predefine the comparison, keep the default run, use known controls, and report every non-default setting. A visually appealing boundary is not evidence that a parameter set is valid.
