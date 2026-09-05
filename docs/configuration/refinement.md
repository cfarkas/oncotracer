# Advanced BAM-supported boundary refinement

Boundary refinement is native stage `02_bam_refinement` in every standard Illumina and ONT analysis. qDNAseq or ichorCNA first identifies broad segments. OncoTracer then examines local BAM depth around each coarse boundary and tests whether moving that boundary is supported.

When evidence is insufficient, the original boundary is retained. Refinement improves coordinate resolution; it does not prove that a CNA is biologically real.

## Most analyses should keep the defaults

A minimal run still performs refinement:

```yaml
mode: illumina
lpwgs_root: /absolute/path/project
outdir: /absolute/path/project/results/default
illumina_samplesheet: /absolute/path/project/config/illumina.samplesheet.csv
force: false
```

The main result is beneath:

```text
<outdir>/02_bam_refinement/
└── illumina_qdnaseq_100kb/ or ONT_ichorcna_500kb/
    ├── 01_tables/
    ├── 02_samurai_compatible/
    ├── 03_consolidated/
    └── 04_final_results/
        └── final_segments.tsv
```

Use `04_final_results/final_segments.tsv` as the refined segment table and stage `03_cna_codification` for final event-level results.

## Resolution and search area

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `fine_bin_kb_illumina` | `10` | Local Illumina depth-bin width in kb |
| `fine_bin_kb_ont` | `25` | Local ONT depth-bin width in kb |
| `search_radius_bins` | `2` | Number of coarse bins searched around each boundary |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum accepted confidence-interval width relative to a coarse bin |

## Read and signal filters

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `min_mapq` | `20` | Minimum read mapping quality |
| `min_local_log2_diff_illumina` | `0.10` | Minimum local Illumina depth step |
| `min_local_log2_diff_ont` | `0.12` | Minimum local ONT depth step |
| `min_adjacent_seg_delta` | `0.10` | Minimum difference between adjacent coarse segments |
| `min_bic_gain` | `6` | Minimum local model-fit improvement |

## Statistical acceptance

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `permutations` | `300` | Empirical permutations; `0` disables them |
| `permutation_p` | `0.05` | Largest accepted empirical p-value |
| `accept_rule` | `p_and_bic` | Requires empirical and BIC evidence |

## ZIPcnv comparison

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `zipcnv_mode` | `adapted` | Adapted comparison mode |
| `zipcnv_window_bins` | `5` | Local window |
| `zipcnv_k` | `0.05` | Adapted tuning constant |
| `zipcnv_min_segment_bins` | `3` | Minimum retained segment length |
| `zipcnv_min_abs_log2` | `0.25` | Minimum retained absolute signal |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum overlap for comparison |

## Reproducible public-data methods comparison

Download the reads and create both configurations using the ordinary `setup`
commands in [QuickStart 1](../quick_start.md). Run its `check` commands before
continuing. Use the same analysis directory below.

Run the default Illumina analysis:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/illumina/config/run.yml"
```

Create a separate conservative configuration. The script changes `outdir` and appends non-default refinement values while leaving the original YAML unchanged:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"
DEFAULT_CONFIG="$TEST_ROOT/illumina/config/run.yml"
CONSERVATIVE_CONFIG="$TEST_ROOT/illumina/config/conservative.yml"

python3 - \
  "$DEFAULT_CONFIG" \
  "$CONSERVATIVE_CONFIG" \
  "$TEST_ROOT/illumina-conservative/results" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
outdir = Path(sys.argv[3]).resolve()
lines = source.read_text(encoding="utf-8").splitlines()
updated = []
for line in lines:
    if line.startswith("outdir:"):
        updated.append(f"outdir: {outdir}")
    else:
        updated.append(line)
updated.extend(
    [
        "fine_bin_kb_illumina: 20",
        "search_radius_bins: 2",
        "min_mapq: 30",
        "min_local_log2_diff_illumina: 0.15",
        "min_bic_gain: 8",
        "permutations: 500",
        "permutation_p: 0.05",
        "accept_rule: p_and_bic",
    ]
)
destination.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

sed -n '1,220p' "$CONSERVATIVE_CONFIG"
```

Validate the native command graph before computation:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/illumina/config/conservative.yml" \
  --dry-run
```

Run the comparison:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/illumina/config/conservative.yml"
```

Compare the final tables:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"
DEFAULT="$TEST_ROOT/illumina/results/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv"
EXPERIMENT="$TEST_ROOT/illumina-conservative/results/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv"

ls -lh "$DEFAULT" "$EXPERIMENT"
diff -u "$DEFAULT" "$EXPERIMENT" || true
```

Also compare:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

sed -n '1,20p' \
  "$TEST_ROOT/illumina/results/02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv"
sed -n '1,20p' \
  "$TEST_ROOT/illumina-conservative/results/02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv"
```

## Reporting a comparison

Predefine the comparison, use a new `outdir`, retain the default analysis, and report every non-default setting. Do not tune thresholds toward a desired diagnosis. Review coverage, confidence intervals, segment amplitude, caller uncertainty, and orthogonal evidence before treating a shifted boundary as meaningful.
