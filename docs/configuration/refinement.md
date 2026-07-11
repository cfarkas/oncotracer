# Advanced Refinement Settings

These settings control BAM-supported CNA boundary refinement. Leave defaults unchanged for standard runs.

```yaml
fine_bin_kb_ont: 25
fine_bin_kb_illumina: 10
search_radius_bins: 2
min_mapq: 20
min_local_log2_diff_ont: 0.12
min_local_log2_diff_illumina: 0.10
min_adjacent_seg_delta: 0.10
min_bic_gain: 6
permutations: 300
permutation_p: 0.05
accept_rule: p_and_bic
max_ci_fraction_of_coarse: 1.0
zipcnv_mode: adapted
zipcnv_window_bins: 5
zipcnv_k: 0.05
zipcnv_min_segment_bins: 3
zipcnv_min_abs_log2: 0.25
zipcnv_compare_min_overlap: 0.50
```

Change these only for a documented methods experiment.
