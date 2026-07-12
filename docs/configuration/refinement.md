# Advanced Refinement Settings

Boundary refinement is an optional evidence-driven step inside every standard OncoTracer run. It starts from the broad qDNAseq or ichorCNA segments, returns to the aligned BAM, and asks whether read-depth evidence supports moving each CNA boundary to a nearby finer bin.

For routine analysis, keep the defaults. Change them only for a planned methods experiment, and write results to a new `outdir` so the default run remains available for comparison.

## Where the settings go

Refinement settings are not a separate configuration file. Add them near the bottom of the same Illumina or ONT YAML passed to `main.nf`.

```text
oncotracer/
├── main.nf
├── params/
│   ├── illumina.minimal.yml
│   └── my_illumina_refinement.yml   # your copied and edited run YAML
└── test/
    └── runs/                         # public-test results
```

## What the parameters control

| Parameter | Default | Practical meaning |
| --- | ---: | --- |
| `fine_bin_kb_illumina` | `10` | BAM read-depth bin size around an Illumina boundary. Smaller bins give finer coordinates but are noisier and slower. |
| `fine_bin_kb_ont` | `25` | BAM read-depth bin size around an ONT boundary. |
| `search_radius_bins` | `2` | Number of coarse caller bins searched on each side of the original boundary. Larger values allow larger moves. |
| `min_mapq` | `20` | Minimum mapping quality for reads contributing to local depth. Raising it removes more ambiguous reads. |
| `min_local_log2_diff_illumina` | `0.10` | Minimum local Illumina depth contrast needed to consider a boundary. |
| `min_local_log2_diff_ont` | `0.12` | Minimum local ONT depth contrast needed to consider a boundary. |
| `min_adjacent_seg_delta` | `0.10` | Minimum difference between neighboring segment levels. |
| `min_bic_gain` | `6` | Minimum improvement in model fit required to accept the proposed split. Higher is more conservative. |
| `permutations` | `300` | Number of permutation tests. More permutations improve p-value resolution but take longer. |
| `permutation_p` | `0.05` | Maximum permutation p-value accepted by rules that use p-values. |
| `accept_rule` | `p_and_bic` | Requires both permutation and BIC evidence. This is the conservative default. |
| `max_ci_fraction_of_coarse` | `1.0` | Limits boundary uncertainty relative to the original coarse bin. |
| `zipcnv_mode` | `adapted` | Enables the bundled adapted ZIPcnv comparison. |
| `zipcnv_window_bins` | `5` | Local ZIPcnv smoothing window. |
| `zipcnv_k` | `0.05` | ZIPcnv tuning constant. |
| `zipcnv_min_segment_bins` | `3` | Minimum supported ZIPcnv segment length in bins. |
| `zipcnv_min_abs_log2` | `0.25` | Minimum absolute ZIPcnv log2 signal. |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum overlap used when comparing refined and ZIPcnv segments. |

These parameters interact. For example, smaller fine bins can increase noise; lowering `min_local_log2_diff` at the same time can admit unstable boundaries. Change one small group at a time.

## Real Illumina example

This example reruns the public Illumina data with a deliberately more conservative refinement experiment and a separate output directory.

```bash
git clone https://github.com/cfarkas/oncotracer.git                    # clone the repository
cd oncotracer                                                          # enter the repository
nextflow run main.nf --make_test                                       # download public FASTQ files and create test YAML files
cp test/configs/illumina.quickstart.yml params/illumina.conservative.yml # copy the generated YAML before editing
nano params/illumina.conservative.yml                                  # edit the copied YAML
```

Inside `nano`, change `outdir` and add the refinement block shown below. Keep `illumina_samurai_outdir` pointing to the already generated public SAMURAI result so both runs start from the same upstream data.

```yaml
mode: illumina
lpwgs_root: /absolute/path/oncotracer/test
outdir: /absolute/path/oncotracer/test/runs/illumina_conservative
illumina_samplesheet: /absolute/path/oncotracer/test/public/illumina_DRR000542/illumina.samplesheet.csv
illumina_samurai_outdir: /absolute/path/oncotracer/test/runs/illumina/01_samurai_illumina
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false

fine_bin_kb_illumina: 20        # use coarser local bins to reduce small-bin noise
search_radius_bins: 2           # keep the default search range
min_mapq: 30                    # require more confidently mapped reads
min_local_log2_diff_illumina: 0.15 # require stronger local depth contrast
min_bic_gain: 8                 # demand stronger model improvement
permutations: 500               # improve permutation p-value resolution
permutation_p: 0.05
accept_rule: p_and_bic          # require both statistical tests
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Validate and run:

```bash
nextflow run main.nf -stub-run --docker -params-file params/illumina.conservative.yml # validate YAML and process wiring
nextflow run main.nf --docker -params-file params/illumina.conservative.yml -resume   # run the conservative experiment
```

Compare the default and experimental boundary tables rather than judging only the plots:

```bash
ls -lh test/runs/illumina/02_bam_refinement                         # default refinement output
ls -lh test/runs/illumina_conservative/02_bam_refinement            # experimental refinement output
diff -u test/runs/illumina/06_workflow_summary/workflow_summary.txt test/runs/illumina_conservative/06_workflow_summary/workflow_summary.txt # compare run locations/settings
```

!!! warning "Method changes need review"
    Do not tune parameters until a desired biological result appears. Predefine the experiment, retain the default run, evaluate known controls, and report every non-default value.
