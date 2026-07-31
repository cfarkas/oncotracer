# Boundary Refinement

Boundary refinement is stage `02_bam_refinement` of every standard Illumina and ONT run. After qDNAseq or ichorCNA finds broad CNA segments, this stage uses local BAM read depth to test whether each coarse boundary should move.

When the evidence is insufficient, OncoTracer keeps the original boundary. Refinement does not by itself prove that a CNA is biologically real.

## Keep the defaults for routine runs

A minimal YAML already runs boundary refinement:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/sample_a
illumina_samplesheet: /home/student/oncotracer/project/config/illumina.samplesheet.csv
force: false
```

Main outputs are written below:

```text
outdir/02_bam_refinement/
└── illumina_qdnaseq_100kb/ or ONT_ichorcna_500kb/
    ├── 01_tables/
    └── 04_final_results/
        └── final_segments.tsv
```

Change refinement settings only for a predefined methods comparison, and use a new `outdir`.

## Optional settings

Add non-default settings to the same run YAML:

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

| Setting | Default | Purpose |
| --- | ---: | --- |
| `fine_bin_kb_illumina` | `10` | Local Illumina bin width in kb |
| `fine_bin_kb_ont` | `25` | Local ONT bin width in kb |
| `search_radius_bins` | `2` | Coarse bins searched on each side |
| `min_mapq` | `20` | Minimum read mapping quality |
| `min_local_log2_diff_illumina` | `0.10` | Minimum Illumina local depth step |
| `min_local_log2_diff_ont` | `0.12` | Minimum ONT local depth step |
| `min_bic_gain` | `6` | Minimum local model improvement |
| `permutations` | `300` | Empirical permutations |
| `permutation_p` | `0.05` | Largest accepted empirical p-value |
| `accept_rule` | `p_and_bic` | Boundary acceptance rule |

See the [Parameter Reference](parameter_reference.md) for all refinement and ZIPcnv settings.

## Public-data comparison example

This example prepares the public Illumina QuickStart, copies its generated YAML, changes only the refinement settings and output directory, and runs a second analysis.

```bash
# Clone the repository.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository.
cd /home/student/oncotracer

# Download the public QuickStart reads and generate the default YAML files.
nextflow run main.nf --make_test \
  --test_root /home/student/oncotracer/test

# Copy the generated Illumina YAML for a separate refinement experiment.
cp /home/student/oncotracer/test/configs/illumina.quickstart.yml \
  params/illumina.conservative.yml

# Edit the copied YAML.
nano params/illumina.conservative.yml
```

Change `outdir` to `/home/student/oncotracer/test/runs/illumina_conservative`, then add:

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
# Inspect the complete edited YAML.
sed -n '1,200p' params/illumina.conservative.yml

# Check workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/illumina.conservative.yml

# Run or resume the conservative refinement analysis.
nextflow run main.nf --docker \
  -params-file params/illumina.conservative.yml \
  -work-dir /home/student/oncotracer/test/work/illumina_conservative \
  -resume
```

Use `--singularity` instead of `--docker` on HPC.

## Compare the default and experimental results

```bash
# Set the two final-segment paths.
DEFAULT=/home/student/oncotracer/test/runs/illumina/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv
EXPERIMENT=/home/student/oncotracer/test/runs/illumina_conservative/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv

# Confirm that both result files exist.
ls -lh "$DEFAULT" "$EXPERIMENT"

# Compare the final segment tables.
diff -u "$DEFAULT" "$EXPERIMENT"
```

Predefine the comparison, keep the default run, retain both YAML files, and report every non-default setting. Do not tune parameters toward a preferred diagnosis or visual result.
