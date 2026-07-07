#!/usr/bin/env python3
"""Rule-based and unsupervised CNA patient classification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage, leaves_list
from scipy.spatial.distance import pdist
from sklearn.decomposition import NMF, PCA
from sklearn.preprocessing import StandardScaler


def read_tsv(path: str | Path, index_col=None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(p, sep="\t", dtype=None, index_col=index_col)


def safe_int(x, default=0) -> int:
    try:
        if pd.isna(x):
            return default
        return int(x)
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def burden_class(row: pd.Series, args) -> str:
    n = safe_int(row.get("n_cna_events"))
    altered = safe_float(row.get("altered_mb"))
    n_chr = safe_int(row.get("n_chromosomes_affected"))
    if n == 0:
        return "CNA-flat_or_no_high-confidence_CNA"
    if n >= args.ultra_events or altered >= args.ultra_altered_mb:
        return "CNA-ultracomplex"
    if n >= args.high_events or altered >= args.high_altered_mb or n_chr >= args.high_chromosomes:
        return "CNA-high_complex"
    if n <= args.low_events:
        return "CNA-low"
    return "CNA-intermediate"


def direction_class(row: pd.Series) -> str:
    n = safe_int(row.get("n_cna_events"))
    if n == 0:
        return "flat"
    gain_mb = safe_float(row.get("gain_mb"))
    loss_mb = safe_float(row.get("loss_mb"))
    amps = safe_int(row.get("n_amplification"))
    deep = safe_int(row.get("n_deep_loss"))
    gain_events = safe_int(row.get("n_gain")) + amps
    loss_events = safe_int(row.get("n_loss")) + deep
    if amps >= 3 or safe_float(row.get("max_gain_log2")) >= 0.8:
        return "amplification-rich"
    if deep >= 3 or safe_float(row.get("min_loss_log2")) <= -0.8:
        return "deep-deletion-rich"
    if gain_mb > 2.0 * max(loss_mb, 1e-9) and gain_events > loss_events:
        return "gain-dominant"
    if loss_mb > 2.0 * max(gain_mb, 1e-9) and loss_events > gain_events:
        return "deletion-dominant"
    return "mixed_gain_loss"


def breadth_class(row: pd.Series) -> str:
    n = safe_int(row.get("n_cna_events"))
    if n == 0:
        return "flat"
    broad = safe_int(row.get("n_broad_events_geq_broad_mb"))
    focal = safe_int(row.get("n_focal_events_leq_focal_mb"))
    if broad / max(n, 1) >= 0.40:
        return "broad_arm-level_or_aneuploidy-dominant"
    if focal / max(n, 1) >= 0.75:
        return "focal_fragmented_event-dominant"
    return "mixed_focal_broad"


def driver_flags_for_sample(driver_row: pd.Series) -> list[str]:
    """Return concise pan-cancer driver/canonical CNA flags for one sample.

    The underlying driver matrix is generated from the configured region catalog.
    This function preserves the original lymphoma-relevant flags while adding
    carcinoma, CNS, and leukemia/myeloid-relevant CNA regions.  The labels are
    intentionally CNA-pattern labels, not diagnostic calls.
    """
    flags: list[str] = []

    def present(feature: str) -> bool:
        return feature in driver_row.index and abs(safe_int(driver_row.get(feature))) > 0

    def positive(feature: str) -> bool:
        return feature in driver_row.index and safe_int(driver_row.get(feature)) > 0

    def negative(feature: str) -> bool:
        return feature in driver_row.index and safe_int(driver_row.get(feature)) < 0

    # Broad CNA context.
    if positive("1q_gain"):
        flags.append("1q_gain_pattern")
    if negative("1p_loss"):
        flags.append("1p_loss_pattern")
    if negative("3p_loss"):
        flags.append("3p_loss_pattern")
    if positive("5q_gain_RCC_context"):
        flags.append("5q_gain_RCC_pattern")
    if negative("4q_loss"):
        flags.append("4q_loss_pattern")
    if positive("5p15_TERT_gain_amp"):
        flags.append("5p15_TERT_gain_amp")
    if positive("7_gain"):
        flags.append("chr7_gain_pattern")
    if negative("7q_loss"):
        flags.append("7q_loss_pattern")
    if positive("7q31_MET_gain_amp"):
        flags.append("7q31_MET_gain_amp")
    if positive("7q34_BRAF_KIAA1549_gain"):
        flags.append("7q34_BRAF_KIAA1549_gain")
    if negative("8p_loss"):
        flags.append("8p_loss_pattern")
    if positive("8q_gain"):
        flags.append("8q_gain_pattern")
    if negative("10_loss_GBM_context"):
        flags.append("chr10_loss_pattern")
    if negative("11q_loss"):
        flags.append("11q_loss_pattern")
    if negative("9q_loss_bladder_context"):
        flags.append("9q_loss_bladder_pattern")
    if positive("12p_gain_germ_cell_context"):
        flags.append("12p_gain_germ_cell_pattern")
    if positive("13q_gain_colon_context"):
        flags.append("13q_gain_pattern")
    if negative("14q_loss_RCC_context"):
        flags.append("14q_loss_RCC_pattern")
    if negative("16q_loss_breast_context"):
        flags.append("16q_loss_pattern")
    if negative("18q_loss_SMAD4_DCC"):
        flags.append("18q_loss_SMAD4_DCC_pattern")
    if positive("19q12_CCNE1_gain_amp"):
        flags.append("19q12_CCNE1_gain_amp")
    if positive("19q_gain"):
        flags.append("19q_gain_pattern")
    if positive("20q_gain_colon_context"):
        flags.append("20q_gain_pattern")
    if negative("22q_loss"):
        flags.append("22q_loss_pattern")

    # Oncogene gain/amplification regions.
    if positive("2p16_REL_BCL11A_gain_amp"):
        flags.append("2p16_REL_BCL11A_gain_amp")
    if positive("2p24_MYCN_gain_amp"):
        flags.append("2p24_MYCN_gain_amp")
    if positive("3q26_PIK3CA_SOX2_TERC_gain_amp"):
        flags.append("3q26_PIK3CA_SOX2_TERC_gain_amp")
    if present("3q27_BCL6_alteration"):
        flags.append("3q27_BCL6_alteration")
    if positive("4q12_KIT_PDGFRA_KDR_gain_amp"):
        flags.append("4q12_KIT_PDGFRA_KDR_gain_amp")
    if positive("10q26_FGFR2_gain_amp"):
        flags.append("10q26_FGFR2_gain_amp")
    if positive("7p11_EGFR_gain_amp"):
        flags.append("7p11_EGFR_gain_amp")
    if positive("8q24_MYC_gain_amp"):
        flags.append("8q24_MYC_gain_amp")
    if positive("9p24_JAK2_PDL1_PDL2_gain_amp"):
        flags.append("9p24_JAK2_PDL1_PDL2_gain_amp")
    if positive("14q13_NKX2_1_gain_amp"):
        flags.append("14q13_NKX2_1_gain_amp")
    if positive("11q13_CCND1_FGF_gain_amp"):
        flags.append("11q13_CCND1_FGF_gain_amp")
    if positive("12q15_MDM2_CDK4_gain_amp"):
        flags.append("12q15_MDM2_CDK4_gain_amp")
    if positive("17q12_ERBB2_gain_amp"):
        flags.append("17q12_ERBB2_HER2_gain_amp")
    if positive("18q21_BCL2_MALT1_gain_amp"):
        flags.append("18q21_BCL2_MALT1_gain_amp")

    # Tumor-suppressor / hematologic regions.
    if negative("5q_loss_MDS_AML"):
        flags.append("5q_loss_MDS_AML_pattern")
    if negative("6q21_PRDM1_loss") or negative("6q23_TNFAIP3_loss"):
        flags.append("6q_loss_PRDM1_TNFAIP3_axis")
    if negative("9p21_CDKN2A_B_loss"):
        flags.append("9p21_CDKN2A_B_loss")
    if negative("10q23_PTEN_loss"):
        flags.append("10q23_PTEN_loss")
    if negative("12p13_ETV6_loss"):
        flags.append("12p13_ETV6_loss")
    if negative("13q14_loss"):
        flags.append("13q14_RB1_region_loss")
    if negative("15q21_B2M_loss"):
        flags.append("15q21_B2M_loss")
    if negative("16p13_CIITA_loss"):
        flags.append("16p13_CIITA_loss")
    if negative("17p13_TP53_loss"):
        flags.append("17p13_TP53_loss")
    if positive("17q_gain_neuroblastoma_context"):
        flags.append("17q_gain_neuroblastoma_pattern")
    if negative("19p13_STK11_KEAP1_loss"):
        flags.append("19p13_STK11_KEAP1_loss")
    if positive("Xq12_AR_gain_amp"):
        flags.append("Xq12_AR_gain_amp")
    if present("Xp11_TFE3_region_CNA"):
        flags.append("Xp11_TFE3_region_CNA")
    if present("21q_RUNX1_region_CNA"):
        flags.append("21q_RUNX1_region_CNA")

    return flags


def final_class(burden: str, direction: str, flags: list[str]) -> str:
    """Pan-cancer CNA pattern class.

    This is deliberately agnostic to histology: it labels molecular CNA patterns
    such as HER2/ERBB2 amplification, EGFR/chr7/chr10 glioma-like context,
    myeloid-type 5q/7q losses, colorectal-like 20q/18q patterns, and lymphoma-
    associated patterns.  It must not be read as a formal tumor diagnosis.
    """
    if burden.startswith("CNA-flat"):
        return "CNA-flat"
    flagset = set(flags)

    if "2p24_MYCN_gain_amp" in flagset:
        return "MYCN_gain_amp_neuroblastoma_or_embryonal_tumor_CNA_pattern"
    if "19q12_CCNE1_gain_amp" in flagset:
        return "CCNE1_gain_amp_ovarian_endometrial_gastric_CNA_pattern"
    if "Xq12_AR_gain_amp" in flagset:
        return "AR_gain_amp_prostate_CNA_pattern"
    if burden in {"CNA-high_complex", "CNA-ultracomplex"} and "17p13_TP53_loss" in flagset:
        return "CNA-high_complex__TP53_axis_CNA_pattern"
    if "17q12_ERBB2_HER2_gain_amp" in flagset:
        return "ERBB2_HER2_gain_amp_CNA_pattern"
    if "7p11_EGFR_gain_amp" in flagset or ({"chr7_gain_pattern", "chr10_loss_pattern"}.issubset(flagset)):
        return "EGFR_chr7_chr10_CNS_glioma_like_CNA_pattern"
    if "10q26_FGFR2_gain_amp" in flagset or "7q31_MET_gain_amp" in flagset:
        return "MET_FGFR2_receptor_tyrosine_kinase_gain_amp_CNA_pattern"
    if "11q13_CCND1_FGF_gain_amp" in flagset:
        return "11q13_CCND1_FGF_gain_amp_CNA_pattern"
    if "12q15_MDM2_CDK4_gain_amp" in flagset:
        return "MDM2_CDK4_gain_amp_CNA_pattern"
    if "12p_gain_germ_cell_pattern" in flagset:
        return "germ_cell_12p_gain_CNA_pattern"
    if "5q_loss_MDS_AML_pattern" in flagset or "7q_loss_pattern" in flagset or "21q_RUNX1_region_CNA" in flagset:
        return "myeloid_leukemia_MDS_compatible_CNA_pattern"
    if "8q_gain_pattern" in flagset and "17p13_TP53_loss" in flagset and "4q_loss_pattern" in flagset:
        return "liver_HCC_like_8q_17p_CNA_pattern"
    if "20q_gain_pattern" in flagset and ("18q_loss_SMAD4_DCC_pattern" in flagset or "8q24_MYC_gain_amp" in flagset or "13q_gain_pattern" in flagset):
        return "colorectal_like_20q_18q_CNA_pattern"
    if "9q_loss_bladder_pattern" in flagset and "9p21_CDKN2A_B_loss" in flagset:
        return "urothelial_9p_9q_loss_CNA_pattern"
    if "5q_gain_RCC_pattern" in flagset and "3p_loss_pattern" in flagset:
        return "renal_cell_carcinoma_3p_loss_5q_gain_CNA_pattern"
    if "9p21_CDKN2A_B_loss" in flagset and "18q_loss_SMAD4_DCC_pattern" in flagset and "17p13_TP53_loss" in flagset:
        return "pancreatic_colorectal_tumor_suppressor_loss_CNA_pattern"
    if "2p16_REL_BCL11A_gain_amp" in flagset and "18q21_BCL2_MALT1_gain_amp" in flagset:
        return "B_cell_lymphoma_oncogene_gain_CNA_pattern"
    if "9p21_CDKN2A_B_loss" in flagset:
        return "CDKN2A_B_loss_CNA_pattern"
    if "8q24_MYC_gain_amp" in flagset:
        return "MYC_gain_amp_CNA_pattern"
    if direction == "amplification-rich":
        return "amplification-rich_CNA"
    if direction == "deletion-dominant" or direction == "deep-deletion-rich":
        return "deletion-dominant_CNA"
    if direction == "gain-dominant":
        return "gain-dominant_CNA"
    if burden in {"CNA-high_complex", "CNA-ultracomplex"}:
        return "CNA-high_complex"
    return "mixed_CNA_pattern"


def build_unsupervised_matrix(
    event_matrix: pd.DataFrame,
    driver_matrix: pd.DataFrame,
    recurrent_events: pd.DataFrame,
    top_regions: int,
    gistic_matrix: pd.DataFrame | None = None,
) -> pd.DataFrame:
    # Event matrix can be very wide. Keep top recurrent regions plus all driver columns.
    em = event_matrix.copy()
    em = em.apply(pd.to_numeric, errors="coerce").fillna(0).astype(float)
    if not recurrent_events.empty and "event_label" in recurrent_events.columns:
        top = recurrent_events.sort_values(["n_samples", "max_abs_log2"], ascending=[False, False])["event_label"].astype(str).tolist()[:top_regions]
        top = [c for c in top if c in em.columns]
        em = em[top] if top else pd.DataFrame(index=em.index)
    else:
        # fallback: most recurrent columns
        top = em.sum(axis=0).sort_values(ascending=False).index.tolist()[:top_regions]
        em = em[top]

    dm = driver_matrix.copy()
    dm = dm.apply(pd.to_numeric, errors="coerce").fillna(0).astype(float)
    dm = (dm != 0).astype(float)

    matrices = [em, dm.add_prefix("driver__")]
    if gistic_matrix is not None and not gistic_matrix.empty:
        gm = gistic_matrix.copy()
        gm = gm.apply(pd.to_numeric, errors="coerce").fillna(0).astype(float)
        gm = (gm != 0).astype(float)
        # Prefix to keep GISTIC-derived lesions separate from raw SAMURAI/driver features.
        matrices.append(gm.add_prefix("gistic__"))

    X = pd.concat(matrices, axis=1)
    X = X.loc[:, X.sum(axis=0) > 0]
    X = X.fillna(0)
    return X


def unsupervised_clustering(X: pd.DataFrame, n_clusters: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    samples = list(X.index)
    clusters = pd.DataFrame({"sample": samples})
    pca_df = pd.DataFrame({"sample": samples})

    if X.shape[0] < 2 or X.shape[1] < 1:
        clusters["hierarchical_cluster"] = "not_enough_data"
        clusters["nmf_cluster"] = "not_enough_data"
        return clusters, X, pca_df

    Xbin = (X.values > 0).astype(float)

    # Hierarchical clustering on Jaccard distance; if all-zero impossible after filtering.
    try:
        d = pdist(Xbin, metric="jaccard")
        if np.all(np.isfinite(d)) and not np.all(d == 0):
            Z = linkage(d, method="average")
            k = min(max(2, n_clusters), X.shape[0])
            hcl = fcluster(Z, t=k, criterion="maxclust")
            order = leaves_list(Z)
        else:
            hcl = np.ones(X.shape[0], dtype=int)
            order = np.arange(X.shape[0])
    except Exception:
        hcl = np.ones(X.shape[0], dtype=int)
        order = np.arange(X.shape[0])
    clusters["hierarchical_cluster"] = [f"H{int(x)}" for x in hcl]

    # NMF on non-negative binary CNA event matrix.
    try:
        k = min(max(2, n_clusters), X.shape[0], X.shape[1])
        model = NMF(n_components=k, init="nndsvda", random_state=12345, max_iter=1000)
        W = model.fit_transform(Xbin)
        nmf = np.argmax(W, axis=1) + 1
        clusters["nmf_cluster"] = [f"NMF{x}" for x in nmf]
    except Exception:
        clusters["nmf_cluster"] = "not_available"

    # PCA coordinates for plotting. Use standardized binary matrix if enough columns.
    try:
        x_scaled = StandardScaler(with_mean=True, with_std=True).fit_transform(Xbin)
        n_pc = min(3, X.shape[0], X.shape[1])
        pca = PCA(n_components=n_pc, random_state=12345)
        coords = pca.fit_transform(x_scaled)
        pca_df = pd.DataFrame({"sample": samples})
        for i in range(n_pc):
            pca_df[f"PC{i+1}"] = coords[:, i]
            pca_df[f"PC{i+1}_variance_explained"] = pca.explained_variance_ratio_[i]
    except Exception:
        pass

    heatmap = X.iloc[order, :].copy()
    return clusters, heatmap, pca_df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-events", required=True)
    ap.add_argument("--sample-summary", required=True)
    ap.add_argument("--event-matrix", required=True)
    ap.add_argument("--weighted-event-matrix", required=True)
    ap.add_argument("--driver-matrix", required=True)
    ap.add_argument("--recurrent-events", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--gistic-matrix", required=True)
    ap.add_argument("--gistic-long", required=True)
    ap.add_argument("--gistic-summary", required=True)
    ap.add_argument("--low-events", type=int, default=10)
    ap.add_argument("--high-events", type=int, default=50)
    ap.add_argument("--ultra-events", type=int, default=100)
    ap.add_argument("--high-chromosomes", type=int, default=8)
    ap.add_argument("--high-altered-mb", type=float, default=500)
    ap.add_argument("--ultra-altered-mb", type=float, default=1000)
    ap.add_argument("--nmf-clusters", type=int, default=3)
    ap.add_argument("--top-regions", type=int, default=80)
    args = ap.parse_args()

    events = read_tsv(args.clean_events)
    summary = read_tsv(args.sample_summary)
    event_matrix = read_tsv(args.event_matrix, index_col=0)
    weighted_event_matrix = read_tsv(args.weighted_event_matrix, index_col=0)
    driver_matrix = read_tsv(args.driver_matrix, index_col=0)
    recurrent_events = read_tsv(args.recurrent_events)
    driver_hits = read_tsv(args.driver_hits)
    gistic_matrix = read_tsv(args.gistic_matrix, index_col=0)
    gistic_long = read_tsv(args.gistic_long)
    gistic_summary = read_tsv(args.gistic_summary)
    _ = weighted_event_matrix, driver_hits, gistic_summary  # retained for future extension

    # Ensure sample index sync.
    samples = summary["sample"].astype(str).tolist()
    event_matrix = event_matrix.reindex(samples).fillna(0)
    driver_matrix = driver_matrix.reindex(samples).fillna(0)
    if not gistic_matrix.empty:
        gistic_matrix = gistic_matrix.reindex(samples).fillna(0)

    class_rows = []
    for _, row in summary.iterrows():
        sample = str(row["sample"])
        b = burden_class(row, args)
        d = direction_class(row)
        br = breadth_class(row)
        if sample in driver_matrix.index:
            flags = driver_flags_for_sample(driver_matrix.loc[sample])
        else:
            flags = []
        f = final_class(b, d, flags)
        if not gistic_matrix.empty and sample in gistic_matrix.index:
            gvals = pd.to_numeric(gistic_matrix.loc[sample], errors="coerce").fillna(0).astype(int)
            n_gistic = int((gvals != 0).sum())
            n_gistic_high = int((gvals.abs() >= 2).sum())
            gistic_flags = gvals[gvals != 0].index.astype(str).tolist()[:25]
        else:
            n_gistic = 0
            n_gistic_high = 0
            gistic_flags = []
        class_rows.append({
            "sample": sample,
            "rule_based_cna_class": f,
            "cna_burden_class": b,
            "gain_loss_direction_class": d,
            "focal_broad_class": br,
            "driver_region_flags": ";".join(flags) if flags else "none_detected",
            "n_gistic_significant_lesions": n_gistic,
            "n_gistic_high_level_lesions": n_gistic_high,
            "gistic_lesion_flags": ";".join(gistic_flags) if gistic_flags else "none_detected_or_not_run",
            "interpretation_note": (
                "CNA-only pan-cancer pattern classification. GISTIC2 lesions are cohort-level recurrent CNA evidence when available; do not treat as a formal tumor diagnosis or subtype without SNVs/indels, SV/translocations/fusions, methylation/expression when relevant, pathology, and clinical context."
            ),
        })
    classification = pd.DataFrame(class_rows)
    classification = classification.merge(summary, on="sample", how="left")

    X = build_unsupervised_matrix(event_matrix, driver_matrix, recurrent_events, args.top_regions, gistic_matrix=gistic_matrix)
    clusters, heatmap, pca_df = unsupervised_clustering(X, args.nmf_clusters)
    classification = classification.merge(clusters, on="sample", how="left")

    classification.to_csv("cna_patient_classification.tsv", sep="\t", index=False)
    clusters.to_csv("unsupervised_clusters.tsv", sep="\t", index=False)
    heatmap.index.name = "sample"
    heatmap.to_csv("heatmap_matrix.tsv", sep="\t")
    pca_df.to_csv("pca_coordinates.tsv", sep="\t", index=False)

    metrics = {
        "n_samples": int(len(samples)),
        "n_events": int(len(events)),
        "n_features_for_clustering": int(X.shape[1]),
        "n_gistic_lesion_features": int(gistic_matrix.shape[1]) if not gistic_matrix.empty else 0,
        "n_gistic_long_rows": int(len(gistic_long)),
        "nmf_clusters_requested": int(args.nmf_clusters),
        "rule_classes": classification["rule_based_cna_class"].value_counts().to_dict(),
        "burden_classes": classification["cna_burden_class"].value_counts().to_dict(),
    }
    Path("classification_metrics.json").write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
