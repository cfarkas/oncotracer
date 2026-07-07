#!/usr/bin/env python3
"""ZIPcnv-adapted comparison for BAM boundary-refined CNA outputs.

The official ZIPcnv implementation is designed around base-level depth arrays and
large normal baselines. For SAMURAI qDNAseq/ichorCNA outputs, this helper applies
an adapted CUSUM detector to the already normalized bin-level log2 signal and
compares the resulting candidate CNV regions with the BAM-refined final segments.
"""
import argparse
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def log(msg: str):
    print(msg, flush=True)


def mkdir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def clean_token(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return str(x).strip().strip('"').strip("'").strip('`').strip()


def norm_chrom(x) -> str:
    s = clean_token(x)
    if not s:
        return s
    s = re.sub(r"^chromosome", "", s, flags=re.I)
    if not s.startswith("chr"):
        s = "chr" + s
    return s.replace("chrMT", "chrM").replace("chrmt", "chrM")


def chrom_order(chrom: str) -> int:
    c = norm_chrom(chrom)
    m = re.match(r"chr(\d+)$", c)
    if m:
        return int(m.group(1))
    if c == "chrX":
        return 23
    if c == "chrY":
        return 24
    if c in ("chrM", "chrMT"):
        return 25
    return 100


def state_from_log2(x: float, gain_thr: float, loss_thr: float) -> str:
    try:
        v = float(x)
    except Exception:
        return "neutral"
    if np.isnan(v):
        return "neutral"
    if v >= gain_thr:
        return "gain"
    if v <= loss_thr:
        return "loss"
    return "neutral"


def read_tsv(path: Path) -> pd.DataFrame:
    if str(path).endswith(".gz"):
        return pd.read_csv(path, sep="\t", low_memory=False, compression="gzip")
    return pd.read_csv(path, sep="\t", low_memory=False)


def safe_numeric(s):
    return pd.to_numeric(s, errors="coerce")


def rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    if len(arr) == 0:
        return arr
    window = max(1, int(window))
    return pd.Series(arr).rolling(window=window, center=True, min_periods=max(1, window // 2)).mean().to_numpy()


def cusum_scores(y: np.ndarray, k: float):
    up = np.zeros(len(y), dtype=float)
    down = np.zeros(len(y), dtype=float)
    for i, v in enumerate(y):
        if not np.isfinite(v):
            v = 0.0
        if i == 0:
            up[i] = max(0.0, v - k)
            down[i] = min(0.0, v + k)
        else:
            up[i] = max(0.0, v - k + up[i - 1])
            down[i] = min(0.0, v + k + down[i - 1])
    return up, down


def merge_short_gaps(states, merge_gap_bins: int):
    states = list(states)
    n = len(states)
    if n == 0 or merge_gap_bins <= 0:
        return states
    i = 0
    while i < n:
        if states[i] != "neutral":
            i += 1
            continue
        j = i
        while j < n and states[j] == "neutral":
            j += 1
        gap_len = j - i
        left = states[i - 1] if i > 0 else None
        right = states[j] if j < n else None
        if gap_len <= merge_gap_bins and left in ("gain", "loss") and left == right:
            for k in range(i, j):
                states[k] = left
        i = j
    return states


def segments_from_states(bg: pd.DataFrame, states, smooth, up, down, args):
    rows = []
    n = len(bg)
    i = 0
    while i < n:
        st = states[i]
        j = i + 1
        while j < n and states[j] == st:
            j += 1
        if st in ("gain", "loss") and (j - i) >= args.zipcnv_min_segment_bins:
            sub = bg.iloc[i:j]
            vals = safe_numeric(sub["zip_input_log2"]).to_numpy(dtype=float)
            med = float(np.nanmedian(vals)) if np.isfinite(vals).any() else np.nan
            if np.isfinite(med) and abs(med) >= args.zipcnv_min_abs_log2:
                rows.append({
                    "sample": sub["sample"].iloc[0],
                    "chrom": sub["chrom"].iloc[0],
                    "start": int(sub["start"].iloc[0]),
                    "end": int(sub["end"].iloc[-1]),
                    "n_bins": int(j - i),
                    "zipcnv_state": st,
                    "zipcnv_median_log2": med,
                    "zipcnv_mean_log2": float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan,
                    "zipcnv_smooth_mean": float(np.nanmean(smooth[i:j])) if len(smooth[i:j]) else np.nan,
                    "zipcnv_cusum_peak": float(np.nanmax(up[i:j]) if st == "gain" else abs(np.nanmin(down[i:j]))),
                    "zipcnv_window_bins": int(args.zipcnv_window_bins),
                    "zipcnv_k": float(args.zipcnv_k),
                    "zipcnv_h_threshold": float(args.zipcnv_h),
                    "method": "ZIPcnv_adapted_bin_CUSUM",
                })
        i = j
    return rows


def run_zipcnv_adapted(refined_bins: pd.DataFrame, args) -> pd.DataFrame:
    df = refined_bins.copy()
    df["sample"] = df["sample"].map(clean_token)
    df["chrom"] = df["chrom"].map(norm_chrom)
    df["start"] = safe_numeric(df["start"]).astype("Int64")
    df["end"] = safe_numeric(df["end"]).astype("Int64")
    if "input_log2" in df.columns:
        df["zip_input_log2"] = safe_numeric(df["input_log2"])
    elif "final_log2" in df.columns:
        df["zip_input_log2"] = safe_numeric(df["final_log2"])
    else:
        raise ValueError("refined_bins.tsv.gz must contain input_log2 or final_log2")
    df["chrom_order"] = df["chrom"].map(chrom_order)
    df = df.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    rows = []
    for (_, _), bg in df.groupby(["sample", "chrom"], sort=False):
        bg = bg.sort_values(["start", "end"]).reset_index(drop=True)
        y = safe_numeric(bg["zip_input_log2"]).to_numpy(dtype=float)
        if len(y) < max(2, args.zipcnv_min_segment_bins):
            continue
        med = np.nanmedian(y) if np.isfinite(y).any() else 0.0
        y = np.where(np.isfinite(y), y, med)
        smooth = rolling_mean(y, args.zipcnv_window_bins)
        smooth = np.where(np.isfinite(smooth), smooth, y)
        baseline = np.nanmedian(smooth) if np.isfinite(smooth).any() else 0.0
        z = smooth - baseline
        up, down = cusum_scores(z, args.zipcnv_k)
        states = []
        for zi, ui, di in zip(z, up, down):
            if ((ui >= args.zipcnv_h) and (zi > args.zipcnv_k)) or (zi >= args.zipcnv_min_abs_log2):
                states.append("gain")
            elif ((di <= -args.zipcnv_h) and (zi < -args.zipcnv_k)) or (zi <= -args.zipcnv_min_abs_log2):
                states.append("loss")
            else:
                states.append("neutral")
        states = merge_short_gaps(states, args.zipcnv_merge_gap_bins)
        rows.extend(segments_from_states(bg, states, smooth, up, down, args))
    cols = ["sample", "chrom", "start", "end", "n_bins", "zipcnv_state", "zipcnv_median_log2", "zipcnv_mean_log2", "zipcnv_smooth_mean", "zipcnv_cusum_peak", "zipcnv_window_bins", "zipcnv_k", "zipcnv_h_threshold", "method"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)
    out["chrom_order"] = out["chrom"].map(chrom_order)
    return out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])


def prepare_primary_segments(final_segments: pd.DataFrame, args) -> pd.DataFrame:
    seg = final_segments.copy()
    seg["sample"] = seg["sample"].map(clean_token)
    seg["chrom"] = seg["chrom"].map(norm_chrom)
    seg["start"] = safe_numeric(seg["start"]).astype("Int64")
    seg["end"] = safe_numeric(seg["end"]).astype("Int64")
    if "seg_log2" not in seg.columns:
        seg["seg_log2"] = safe_numeric(seg["seg.mean"]) if "seg.mean" in seg.columns else np.nan
    seg["seg_log2"] = safe_numeric(seg["seg_log2"])
    seg["primary_state"] = seg["seg_log2"].apply(lambda x: state_from_log2(x, args.state_gain_threshold, args.state_loss_threshold))
    seg = seg[seg["primary_state"].isin(["gain", "loss"])].copy()
    if "num_mark" not in seg.columns:
        seg["num_mark"] = np.nan
    if "final_source" not in seg.columns:
        seg["final_source"] = "primary_bam_boundary_refinement"
    return seg


def interval_overlap(a_start, a_end, b_start, b_end):
    return max(0, min(int(a_end), int(b_end)) - max(int(a_start), int(b_start)))


def compare_methods(primary: pd.DataFrame, zipseg: pd.DataFrame, args):
    rows = []
    for _, p in primary.iterrows() if not primary.empty else []:
        cand = zipseg[(zipseg["sample"] == p["sample"]) & (zipseg["chrom"] == p["chrom"]) & (zipseg["zipcnv_state"] == p["primary_state"])] if not zipseg.empty else pd.DataFrame()
        p_len = max(1, int(p["end"]) - int(p["start"]))
        best = None
        best_ov = 0
        for _, z in cand.iterrows():
            ov = interval_overlap(p["start"], p["end"], z["start"], z["end"])
            if ov > best_ov:
                best_ov = ov
                best = z
        if best is not None:
            z_len = max(1, int(best["end"]) - int(best["start"]))
            frac_primary = best_ov / p_len
            frac_zip = best_ov / z_len
            supported = (frac_primary >= args.zipcnv_compare_min_overlap) or (frac_zip >= args.zipcnv_compare_min_overlap)
            rows.append({
                "record_type": "primary_segment",
                "sample": p["sample"], "chrom": p["chrom"], "start": int(p["start"]), "end": int(p["end"]),
                "primary_state": p["primary_state"], "primary_seg_log2": p["seg_log2"],
               "primary_final_source": p.get("final_source", "primary_bam_boundary_refinement"),
                "zipcnv_supported": bool(supported), "same_state_overlap_bp": int(best_ov),
                "overlap_fraction_of_primary": frac_primary, "overlap_fraction_of_zipcnv": frac_zip,
                "best_zipcnv_start": int(best["start"]), "best_zipcnv_end": int(best["end"]),
                "best_zipcnv_state": best["zipcnv_state"], "best_zipcnv_median_log2": best["zipcnv_median_log2"],
                "interpretation": "Primary BAM-refined segment has ZIPcnv-adapted CUSUM support" if supported else "Primary BAM-refined segment has weak ZIPcnv-adapted overlap",
            })
        else:
            rows.append({
                "record_type": "primary_segment",
                "sample": p["sample"], "chrom": p["chrom"], "start": int(p["start"]), "end": int(p["end"]),
                "primary_state": p["primary_state"], "primary_seg_log2": p["seg_log2"],
                "primary_final_source": p.get("final_source", "primary_bam_boundary_refinement"),
                "zipcnv_supported": False, "same_state_overlap_bp": 0,
                "overlap_fraction_of_primary": 0.0, "overlap_fraction_of_zipcnv": 0.0,
                "best_zipcnv_start": np.nan, "best_zipcnv_end": np.nan,
                "best_zipcnv_state": "none", "best_zipcnv_median_log2": np.nan,
                "interpretation": "Primary BAM-refined segment has no same-state ZIPcnv-adapted overlap",
            })
    for _, z in zipseg.iterrows() if not zipseg.empty else []:
        cand = primary[(primary["sample"] == z["sample"]) & (primary["chrom"] == z["chrom"]) & (primary["primary_state"] == z["zipcnv_state"])] if not primary.empty else pd.DataFrame()
        z_len = max(1, int(z["end"]) - int(z["start"]))
        best_ov = 0
        for _, p in cand.iterrows():
            best_ov = max(best_ov, interval_overlap(z["start"], z["end"], p["start"], p["end"]))
        if best_ov / z_len < args.zipcnv_compare_min_overlap:
            rows.append({
                "record_type": "zipcnv_adapted_only",
                "sample": z["sample"], "chrom": z["chrom"], "start": int(z["start"]), "end": int(z["end"]),
                "primary_state": "none", "primary_seg_log2": np.nan, "primary_final_source": "none",
                "zipcnv_supported": np.nan, "same_state_overlap_bp": int(best_ov),
                "overlap_fraction_of_primary": np.nan, "overlap_fraction_of_zipcnv": best_ov / z_len,
                "best_zipcnv_start": int(z["start"]), "best_zipcnv_end": int(z["end"]),
                "best_zipcnv_state": z["zipcnv_state"], "best_zipcnv_median_log2": z["zipcnv_median_log2"],
                "interpretation": "ZIPcnv-adapted CUSUM region without matching same-state primary segment",
            })
    comp = pd.DataFrame(rows)
    if not comp.empty:
        comp["chrom_order"] = comp["chrom"].map(chrom_order)
        comp = comp.sort_values(["sample", "chrom_order", "start", "end", "record_type"]).drop(columns=["chrom_order"])
    return comp


def sample_summary(primary, zipseg, comp, bstats):
    samples = sorted(set(primary["sample"].unique() if not primary.empty else []) | set(zipseg["sample"].unique() if not zipseg.empty else []) | set(bstats["sample"].unique() if not bstats.empty and "sample" in bstats.columns else []))
    rows = []
    for s in samples:
        ps = primary[primary["sample"] == s] if not primary.empty else pd.DataFrame()
        zs = zipseg[zipseg["sample"] == s] if not zipseg.empty else pd.DataFrame()
        cs = comp[comp["sample"] == s] if not comp.empty else pd.DataFrame()
        bs = bstats[bstats["sample"] == s] if not bstats.empty and "sample" in bstats.columns else pd.DataFrame()
        rows.append({
            "sample": s,
            "n_primary_gain_loss_segments": len(ps),
            "n_zipcnv_adapted_segments": len(zs),
            "n_primary_segments_with_zipcnv_support": int(((cs["record_type"] == "primary_segment") & (cs["zipcnv_supported"] == True)).sum()) if not cs.empty else 0,
            "n_primary_segments_without_zipcnv_support": int(((cs["record_type"] == "primary_segment") & (cs["zipcnv_supported"] == False)).sum()) if not cs.empty else 0,
            "n_zipcnv_adapted_only_segments": int((cs["record_type"] == "zipcnv_adapted_only").sum()) if not cs.empty else 0,
            "n_boundaries_refined_by_bam_method": int((bs["final_decision"] == "refined_boundary").sum()) if not bs.empty and "final_decision" in bs.columns else 0,
            "n_boundaries_retained_by_bam_method": int((bs["final_decision"].astype(str).str.contains("kept|fallback", case=False, na=False)).sum()) if not bs.empty and "final_decision" in bs.columns else 0,
            "recommended_primary_downstream_method": "BAM_boundary_refined_primary",
            "zipcnv_role": "independent_CUSUM_comparison_not_primary_boundary_refinement",
        })
    return pd.DataFrame(rows)


def write_status(consolidated: Path, args, repo_dir: Path):
    repo_present = repo_dir.exists() if str(repo_dir) else False
    readme_present = (repo_dir / "README.md").exists() if str(repo_dir) else False
    status = []
    if args.zipcnv_mode in ("adapted", "both"):
        status.append({"component": "ZIPcnv_adapted_bin_CUSUM", "status": "run", "message": "Implemented on normalized SAMURAI/qDNAseq/ichorCNA bins using the ZIPcnv CUSUM concept."})
    if args.zipcnv_mode in ("official", "both"):
        st = "not_run_requires_official_baseline_config" if args.zipcnv_official_run else "skipped_by_default"
        msg = "Official ZIPcnv execution was not performed automatically because the official code expects base-level depth arrays and a large normal baseline."
        status.append({"component": "official_ZIPcnv_repository", "status": st, "message": msg})
    status.append({"component": "ZIPcnv_repository", "status": "present" if repo_present else "not_present", "message": f"repo_dir={repo_dir}; README_present={readme_present}"})
    pd.DataFrame(status).to_csv(consolidated / "zipcnv_status.csv", index=False)


def copy_primary_outputs(dataset_dir: Path, consolidated: Path):
    primary = consolidated / "samurai_compatible_primary"
    if primary.exists():
        shutil.rmtree(primary)
    src = dataset_dir / "02_samurai_compatible"
    if src.exists():
        shutil.copytree(src, primary)
    for rel in ["02_samurai_compatible/all_segments.seg", "02_samurai_compatible/segments_logR_corrected_gistic.seg", "01_tables/refined_bins.tsv.gz", "01_tables/final_segments.tsv", "01_tables/final_segments.bed", "01_tables/boundary_refinement_statistics.csv", "01_tables/sample_refinement_summary.csv"]:
        p = dataset_dir / rel
        if p.exists():
            shutil.copy2(p, consolidated / Path(rel).name)


def main():
    ap = argparse.ArgumentParser(description="ZIPcnv-adapted comparison for BAM-refined CNA outputs")
    ap.add_argument("--dataset-name", required=True)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--zipcnv-mode", choices=["off", "adapted", "official", "both"], default="adapted")
    ap.add_argument("--zipcnv-repo-dir", default="")
    ap.add_argument("--zipcnv-window-bins", type=int, default=5)
    ap.add_argument("--zipcnv-k", type=float, default=0.05)
    ap.add_argument("--zipcnv-h-mult", type=float, default=1.0)
    ap.add_argument("--zipcnv-min-segment-bins", type=int, default=3)
    ap.add_argument("--zipcnv-min-abs-log2", type=float, default=0.25)
    ap.add_argument("--zipcnv-merge-gap-bins", type=int, default=1)
    ap.add_argument("--zipcnv-compare-min-overlap", type=float, default=0.50)
    ap.add_argument("--zipcnv-official-run", action="store_true")
    ap.add_argument("--state-gain-threshold", type=float, default=0.25)
    ap.add_argument("--state-loss-threshold", type=float, default=-0.25)
    args = ap.parse_args()

    if args.zipcnv_mode == "off":
        log("ZIPcnv comparison disabled (--zipcnv-mode off)")
        return 0

    dataset_dir = Path(args.dataset_dir)
    tables = dataset_dir / "01_tables"
    consolidated = dataset_dir / "03_consolidated"
    mkdir(consolidated)
    args.zipcnv_h = max(0.25, args.zipcnv_window_bins * args.zipcnv_k * args.zipcnv_h_mult)

    refined_bins_file = tables / "refined_bins.tsv.gz"
    final_segments_file = tables / "final_segments.tsv"
    bstats_file = tables / "boundary_refinement_statistics.csv"
    if not refined_bins_file.exists() or not final_segments_file.exists():
        raise FileNotFoundError("Boundary refinement outputs not found; run the primary method first")

    refined_bins = read_tsv(refined_bins_file)
    final_segments = read_tsv(final_segments_file)
    bstats = pd.read_csv(bstats_file) if bstats_file.exists() else pd.DataFrame()

    zipseg = run_zipcnv_adapted(refined_bins, args) if args.zipcnv_mode in ("adapted", "both") else pd.DataFrame()
    primary = prepare_primary_segments(final_segments, args)
    comp = compare_methods(primary, zipseg, args)
    summ = sample_summary(primary, zipseg, comp, bstats)

    zipseg.to_csv(consolidated / "zipcnv_adapted_segments.tsv", sep="\t", index=False)
    comp.to_csv(consolidated / "method_comparison_by_segment.csv", index=False)
    summ.to_csv(consolidated / "method_comparison_by_sample.csv", index=False)

    annotated = final_segments.copy()
    if not comp.empty:
        prim = comp[comp["record_type"] == "primary_segment"].copy()
        key_cols = ["sample", "chrom", "start", "end"]
        for c in key_cols:
            if c in prim.columns and c in annotated.columns:
                prim[c] = prim[c].astype(str)
                annotated[c] = annotated[c].astype(str)
        keep = [c for c in key_cols + ["zipcnv_supported", "same_state_overlap_bp", "overlap_fraction_of_primary", "best_zipcnv_start", "best_zipcnv_end", "best_zipcnv_state", "best_zipcnv_median_log2", "interpretation"] if c in prim.columns]
        annotated = annotated.merge(prim[keep], on=key_cols, how="left")
    annotated.to_csv(consolidated / "consolidated_segments_annotated.tsv", sep="\t", index=False)

    pd.DataFrame([{
        "dataset": args.dataset_name,
        "primary_downstream_segmentation": "BAM_boundary_refined_primary",
        "primary_segments_file": "02_samurai_compatible/all_segments.seg",
        "primary_bins_folder": "02_samurai_compatible/bins/",
        "comparison_method": "ZIPcnv_adapted_bin_CUSUM" if args.zipcnv_mode in ("adapted", "both") else "official_ZIPcnv_status_only",
        "interpretation": "Use BAM boundary-refined SAMURAI-compatible files for downstream analyses; use ZIPcnv-adapted comparison files as an independent CUSUM support layer.",
        "zipcnv_window_bins": args.zipcnv_window_bins,
        "zipcnv_k": args.zipcnv_k,
        "zipcnv_min_abs_log2": args.zipcnv_min_abs_log2,
        "zipcnv_compare_min_overlap": args.zipcnv_compare_min_overlap,
    }]).to_csv(consolidated / "method_decision_summary.csv", index=False)

    write_status(consolidated, args, Path(args.zipcnv_repo_dir) if args.zipcnv_repo_dir else Path(""))
    copy_primary_outputs(dataset_dir, consolidated)

    pd.DataFrame([
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/refined_bins.tsv.gz", "description": "Primary final bin table from BAM-supported boundary refinement with fallback to prior binning when unsupported."},
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/final_segments.tsv", "description": "Primary final segment table from BAM-supported boundary refinement."},
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/final_segments.bed", "description": "BED-style primary final segment table from BAM-supported boundary refinement."},
        {"dataset": args.dataset_name, "category": "primary", "path": "02_samurai_compatible/all_segments.seg", "description": "Primary SAMURAI/GISTIC-compatible segment file for downstream analyses."},
        {"dataset": args.dataset_name, "category": "zipcnv", "path": "03_consolidated/zipcnv_adapted_segments.tsv", "description": "ZIPcnv-adapted bin-level CUSUM CNV regions."},
        {"dataset": args.dataset_name, "category": "comparison", "path": "03_consolidated/method_comparison_by_segment.csv", "description": "Segment-level overlap between BAM-refined primary segments and ZIPcnv-adapted CUSUM regions."},
        {"dataset": args.dataset_name, "category": "comparison", "path": "03_consolidated/method_comparison_by_sample.csv", "description": "Sample-level comparison summary."},
        {"dataset": args.dataset_name, "category": "consolidated", "path": "03_consolidated/consolidated_segments_annotated.tsv", "description": "Primary final segments annotated with ZIPcnv-adapted support where applicable."},
        {"dataset": args.dataset_name, "category": "consolidated", "path": "03_consolidated/samurai_compatible_primary/", "description": "Copy of primary downstream SAMURAI-compatible outputs."},
        {"dataset": args.dataset_name, "category": "status", "path": "03_consolidated/zipcnv_status.csv", "description": "ZIPcnv repository/adapted/official status."},
    ]).to_csv(consolidated / "consolidated_manifest.csv", index=False)

    log(f"ZIPcnv comparison complete: {consolidated}")
    log(f"  ZIPcnv adapted segments: {consolidated / 'zipcnv_adapted_segments.tsv'}")
    log(f"  Method comparison:       {consolidated / 'method_comparison_by_segment.csv'}")
    log(f"  Consolidated manifest:   {consolidated / 'consolidated_manifest.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
