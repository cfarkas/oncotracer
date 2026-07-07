#!/usr/bin/env python3
"""Create publication-oriented CNA classifier plots and an HTML report.

Version note: this script keeps the classification logic unchanged and only improves
visualization/reporting. It saves high-resolution PNG plus vector PDF, avoiding slow
SVG output for large CNA heatmaps.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import warnings
from pathlib import Path
from textwrap import shorten, wrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

STATE_COLORS = {
    "loss": "#67A9CF",
    "deep_loss": "#2166AC",
    "deep loss": "#2166AC",
    "gain": "#F4A582",
    "amplification": "#B2182B",
    "amp": "#B2182B",
}

BURDEN_COLORS = {
    "CNA-flat_or_no_high-confidence_CNA": "#BDBDBD",
    "CNA-flat": "#BDBDBD",
    "CNA-low": "#80B1D3",
    "CNA-intermediate": "#FDB462",
    "CNA-high_complex": "#FB8072",
    "CNA-ultracomplex": "#B2182B",
    "unknown": "#999999",
}

ONCOPRINT_COLORS = {
    -2: "#2166AC",
    -1: "#67A9CF",
     0: "#F7F7F7",
     1: "#F4A582",
     2: "#B2182B",
}


def setup_paper_theme() -> None:
    """Use conservative Matplotlib defaults with safe PDF font embedding.

    The matrix-style plots intentionally use the simpler previous layout
    (90-degree feature labels, standard axes, standard sans-serif font) plus
    explicit margins to avoid clipped borders in PNG/PDF output.
    """
    plt.rcParams.update({
        "figure.dpi": 120,
        "savefig.dpi": 360,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "font.family": "sans-serif",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "legend.title_fontsize": 8,
    })

def read_tsv(path: str | Path, index_col=None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(p, sep="\t", index_col=index_col)


def ensure_dirs() -> tuple[Path, Path]:
    figdir = Path("figures")
    tdir = Path("report_tables")
    figdir.mkdir(exist_ok=True)
    tdir.mkdir(exist_ok=True)
    return figdir, tdir


def clean_label(label: object, max_len: int = 52) -> str:
    s = str(label)
    s = s.replace("driver__", "D: ").replace("gistic__", "G: ").replace("_", " ")
    s = " ".join(s.split())
    return shorten(s, width=max_len, placeholder="…")


def wrapped_label(label: object, max_len: int = 44, width: int = 18) -> str:
    """Compact long genomic-feature labels without hiding the key region.

    This is mainly for oncoprint/heatmap x-axis labels.  It avoids huge one-line
    labels that force Matplotlib to clip borders or create unreadable text piles.
    """
    s = clean_label(label, max_len=max_len)
    parts = wrap(s, width=width, break_long_words=False, break_on_hyphens=False)
    return "\n".join(parts) if parts else s


def pretty_class_label(value: object) -> str:
    s = str(value) if pd.notna(value) else "unknown"
    mapping = {
        "CNA-flat_or_no_high-confidence_CNA": "CNA-flat / no high-conf.",
        "CNA-flat": "CNA-flat",
        "CNA-low": "CNA-low",
        "CNA-intermediate": "CNA-intermediate",
        "CNA-high_complex": "CNA-high",
        "CNA-ultracomplex": "CNA-ultra",
        "unknown": "unknown",
    }
    return mapping.get(s, shorten(s.replace("_", " "), width=34, placeholder="…"))


def class_color(value: object) -> str:
    s = str(value) if pd.notna(value) else "unknown"
    return BURDEN_COLORS.get(s, "#999999")


def despine(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", length=3, width=0.7, color="#333333")


def xgrid(ax: plt.Axes) -> None:
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.7)
    ax.set_axisbelow(True)


def xygrid(ax: plt.Axes) -> None:
    ax.grid(axis="both", color="#E5E5E5", linewidth=0.7)
    ax.set_axisbelow(True)


def savefig(path: Path, tight: bool = False, pad_inches: float = 0.18) -> None:
    fig = plt.gcf()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            fig.tight_layout()
        except Exception:
            pass
    # Dense heatmaps can be slow with bbox_inches='tight', so it is opt-in.
    # Matrix-style plots with long labels use tight=True plus explicit padding so
    # axis borders, labels, and legends are not cropped.
    kwargs = {"bbox_inches": "tight", "pad_inches": pad_inches} if tight else {}
    fig.savefig(path, **kwargs)
    fig.savefig(path.with_suffix(".pdf"), **kwargs)
    plt.close(fig)


def class_legend(classes: list[str]) -> list[Patch]:
    ordered = []
    for c in classes:
        if c not in ordered and c and c != "nan":
            ordered.append(c)
    return [Patch(facecolor=class_color(c), edgecolor="none", label=pretty_class_label(c)) for c in ordered]


def plot_burden(classification: pd.DataFrame, figdir: Path) -> list[str]:
    outputs = []
    if classification.empty:
        return outputs
    df = classification.copy()
    df["n_cna_events"] = pd.to_numeric(df.get("n_cna_events", 0), errors="coerce").fillna(0)
    df = df.sort_values(["n_cna_events", "sample"], ascending=[True, True])
    colors = [class_color(x) for x in df["cna_burden_class"].fillna("unknown")]

    fig, ax = plt.subplots(figsize=(8.2, max(3.2, min(14, 0.30 * len(df) + 1.8))))
    bars = ax.barh(df["sample"].astype(str), df["n_cna_events"], color=colors, edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Number of CNA events")
    ax.set_ylabel("")
    ax.set_title("CNA event burden per sample", loc="left")
    xmax = max(float(df["n_cna_events"].max()), 1.0)
    ax.set_xlim(0, xmax * 1.15)
    if len(df) <= 45:
        ax.bar_label(bars, labels=[f"{int(v)}" for v in df["n_cna_events"]], padding=2, fontsize=7)
    xgrid(ax); despine(ax)
    handles = class_legend(df["cna_burden_class"].astype(str).tolist())
    if handles:
        ax.legend(handles=handles, title="Burden class", frameon=False, loc="lower right")
    out = figdir / "cna_event_burden.png"
    savefig(out); outputs.append(str(out))

    counts = classification["cna_burden_class"].fillna("unknown").value_counts().sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(7.2, max(2.8, 0.42 * len(counts) + 1.3)))
    bars = ax.barh(counts.index.astype(str), counts.values, color=[class_color(x) for x in counts.index], edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Number of samples")
    ax.set_ylabel("")
    ax.set_title("CNA burden classes", loc="left")
    ax.set_xlim(0, max(float(counts.max()), 1.0) * 1.20)
    ax.bar_label(bars, labels=[str(int(v)) for v in counts.values], padding=2, fontsize=8)
    xgrid(ax); despine(ax)
    out = figdir / "cna_burden_classes.png"
    savefig(out); outputs.append(str(out))
    return outputs


def plot_state_counts(summary: pd.DataFrame, figdir: Path) -> list[str]:
    outputs = []
    if summary.empty:
        return outputs
    df = summary.copy()
    cols = ["n_loss", "n_deep_loss", "n_gain", "n_amplification"]
    for col in cols:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0)
    df["total"] = df[cols].sum(axis=1)
    df = df.sort_values(["total", "sample"], ascending=[True, True])
    y = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(8.6, max(3.4, min(14, 0.30 * len(df) + 1.8))))
    left = np.zeros(len(df))
    for col, label in [("n_loss", "loss"), ("n_deep_loss", "deep loss"), ("n_gain", "gain"), ("n_amplification", "amplification")]:
        vals = df[col].to_numpy(dtype=float)
        ax.barh(y, vals, left=left, label=label, color=STATE_COLORS[label], edgecolor="white", linewidth=0.35)
        left += vals
    ax.set_yticks(y); ax.set_yticklabels(df["sample"].astype(str))
    ax.set_xlabel("Number of CNA events")
    ax.set_ylabel("")
    ax.set_title("CNA event state composition", loc="left")
    ax.legend(frameon=False, ncol=4, loc="lower right", bbox_to_anchor=(1, 1.005), borderaxespad=0)
    xgrid(ax); despine(ax)
    out = figdir / "cna_state_composition.png"
    savefig(out); outputs.append(str(out))
    return outputs


def plot_complexity_scatter(classification: pd.DataFrame, figdir: Path) -> list[str]:
    outputs = []
    need = {"sample", "n_cna_events", "altered_mb", "n_chromosomes_affected", "cna_burden_class"}
    if classification.empty or not need.issubset(classification.columns):
        return outputs
    df = classification.copy()
    for c in ["n_cna_events", "altered_mb", "n_chromosomes_affected"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    for cls, sub in df.groupby("cna_burden_class", dropna=False):
        sizes = 46 + 8 * np.sqrt(sub["n_chromosomes_affected"].clip(lower=0))
        ax.scatter(sub["n_cna_events"], sub["altered_mb"], s=sizes, color=class_color(cls), edgecolor="white", linewidth=0.8, alpha=0.92, label=str(cls))
        if len(df) <= 35:
            for _, r in sub.iterrows():
                ax.annotate(str(r["sample"]), (r["n_cna_events"], r["altered_mb"]), xytext=(3, 3), textcoords="offset points", fontsize=6.8)
    ax.set_xlabel("Number of CNA events")
    ax.set_ylabel("Altered genome size (Mb)")
    ax.set_title("CNA complexity landscape", loc="left")
    xygrid(ax); despine(ax)
    ax.legend(frameon=False, title="Burden class", bbox_to_anchor=(1.01, 1.0), loc="upper left", borderaxespad=0)
    out = figdir / "cna_complexity_landscape.png"
    savefig(out); outputs.append(str(out))
    return outputs


def state_color(value: object) -> str:
    s = str(value).lower().replace(" ", "_")
    if s in STATE_COLORS:
        return STATE_COLORS[s]
    if "amp" in s:
        return STATE_COLORS["amplification"]
    if "gain" in s:
        return STATE_COLORS["gain"]
    if "loss" in s or "del" in s:
        return STATE_COLORS["loss"]
    return "#777777"


def plot_recurrence(recurrent: pd.DataFrame, figdir: Path, top_n: int = 30) -> list[str]:
    outputs = []
    if recurrent.empty or "event_label" not in recurrent.columns:
        return outputs
    df = recurrent.copy()
    df["n_samples"] = pd.to_numeric(df.get("n_samples", 0), errors="coerce").fillna(0)
    df["max_abs_log2"] = pd.to_numeric(df.get("max_abs_log2", 0), errors="coerce").fillna(0)
    df = df.sort_values(["n_samples", "max_abs_log2"], ascending=[False, False]).head(top_n)
    df = df.sort_values(["n_samples", "event_label"], ascending=[True, True])
    labels = [clean_label(x, 52) for x in df["event_label"].astype(str)]
    colors = [state_color(x) for x in df.get("state", pd.Series([""] * len(df)))]

    fig, ax = plt.subplots(figsize=(8.4, max(4.0, 0.27 * len(df) + 1.5)))
    bars = ax.barh(labels, df["n_samples"], color=colors, edgecolor="white", linewidth=0.4)
    ax.set_xlabel("Samples with event")
    ax.set_ylabel("")
    ax.set_title(f"Top {len(df)} recurrent CNA event labels", loc="left")
    xmax = max(float(df["n_samples"].max()), 1.0)
    ax.set_xlim(0, xmax * 1.18)
    ax.bar_label(bars, labels=[str(int(v)) for v in df["n_samples"]], padding=2, fontsize=7)
    xgrid(ax); despine(ax)
    state_handles = [Patch(facecolor=STATE_COLORS[x], edgecolor="none", label=x) for x in ["loss", "deep loss", "gain", "amplification"]]
    ax.legend(handles=state_handles, frameon=False, title="State", loc="lower right")
    out = figdir / "top_recurrent_cna_events.png"
    savefig(out); outputs.append(str(out))
    return outputs


def plot_gistic_summary(gistic_summary: pd.DataFrame, figdir: Path, top_n: int = 30) -> list[str]:
    outputs = []
    if gistic_summary.empty or "gistic_feature" not in gistic_summary.columns:
        return outputs
    df = gistic_summary.copy()
    df["n_samples"] = pd.to_numeric(df.get("n_samples", 0), errors="coerce").fillna(0)
    df["q_value"] = pd.to_numeric(df.get("q_value", np.nan), errors="coerce")
    df = df[df["n_samples"] > 0].copy()
    if df.empty:
        return outputs
    df = df.sort_values(["n_samples", "q_value"], ascending=[False, True]).head(top_n)
    df = df.sort_values(["n_samples", "gistic_feature"], ascending=[True, True])
    labels = [clean_label(x, 52) for x in df["gistic_feature"]]
    colors = ["#B2182B" if ("amp" in str(x).lower() or "ampl" in str(x).lower()) else "#2166AC" for x in df["gistic_feature"]]
    fig, ax = plt.subplots(figsize=(8.4, max(3.5, 0.27 * len(df) + 1.4)))
    bars = ax.barh(labels, df["n_samples"], color=colors, edgecolor="white", linewidth=0.4)
    ax.set_xlabel("Samples with GISTIC2 lesion")
    ax.set_title(f"Top {len(df)} GISTIC2 recurrent lesions", loc="left")
    xmax = max(float(df["n_samples"].max()), 1.0)
    ax.set_xlim(0, xmax * 1.18)
    ax.bar_label(bars, labels=[str(int(v)) for v in df["n_samples"]], padding=2, fontsize=7)
    xgrid(ax); despine(ax)
    out = figdir / "gistic_top_lesions.png"
    savefig(out); outputs.append(str(out))
    return outputs


def class_annotation(samples: list[str], classification: pd.DataFrame) -> tuple[np.ndarray, ListedColormap, list[Patch]]:
    if classification.empty or "sample" not in classification.columns:
        classes = ["unknown"] * len(samples)
    else:
        cmap = classification.set_index("sample").get("cna_burden_class", pd.Series(dtype=str)).astype(str).to_dict()
        classes = [cmap.get(s, "unknown") for s in samples]
    unique = []
    for c in classes:
        if c not in unique:
            unique.append(c)
    codes = np.array([[unique.index(c)] for c in classes], dtype=float)
    listed = ListedColormap([class_color(c) for c in unique])
    patches = [Patch(facecolor=class_color(c), edgecolor="none", label=pretty_class_label(c)) for c in unique]
    return codes, listed, patches


def plot_binary_matrix(X: pd.DataFrame, classification: pd.DataFrame, figdir: Path, out_name: str, title: str, max_features: int) -> list[str]:
    """Simple previous-style binary heatmap with extra margins.

    This intentionally avoids angled 45/60-degree labels and wrapped labels.
    It keeps feature names vertical (90 degrees), uses the standard Matplotlib
    font, and relies on bbox_inches='tight' plus explicit subplot margins so
    labels, legends, and plot borders are not clipped.
    """
    outputs = []
    if X.empty or X.shape[0] < 1 or X.shape[1] < 1:
        return outputs
    X = X.apply(pd.to_numeric, errors="coerce").fillna(0)
    cols = X.abs().sum(axis=0).sort_values(ascending=False).index.tolist()[:max_features]
    X = (X[cols] != 0).astype(int)
    if X.shape[1] == 0:
        return outputs

    samples = X.index.astype(str).tolist()
    n_samples, n_features = X.shape
    sample_fs = 7.4 if n_samples <= 35 else (6.5 if n_samples <= 65 else 5.6)
    feature_fs = 6.8 if n_features <= 60 else (5.8 if n_features <= 90 else 4.8)

    width = max(8.2, min(19.5, 0.20 * n_features + 4.1))
    height = max(3.8, min(16.5, 0.27 * n_samples + 2.2))
    fig = plt.figure(figsize=(width, height))
    gs = fig.add_gridspec(nrows=1, ncols=2, width_ratios=[0.24, 5.0], wspace=0.035)
    ax_ann = fig.add_subplot(gs[0, 0])
    ax = fig.add_subplot(gs[0, 1])

    codes, ann_cmap, class_patches = class_annotation(samples, classification)
    ax_ann.imshow(codes, aspect="auto", interpolation="nearest", cmap=ann_cmap)
    ax_ann.set_xticks([0])
    ax_ann.set_xticklabels(["Class"], rotation=90, fontsize=max(sample_fs, 6.0), fontweight="bold")
    ax_ann.set_yticks(np.arange(n_samples))
    ax_ann.set_yticklabels(samples, fontsize=sample_fs)
    ax_ann.tick_params(axis="both", length=0, pad=2)
    for sp in ax_ann.spines.values():
        sp.set_visible(False)

    bin_cmap = ListedColormap(["#F7F7F7", "#2C7FB8"])
    ax.imshow(X.values, aspect="auto", interpolation="nearest", cmap=bin_cmap, vmin=0, vmax=1)
    ax.set_yticks(np.arange(n_samples))
    ax.set_yticklabels([])
    ax.set_xticks(np.arange(n_features))
    labels = [clean_label(c, 34) for c in X.columns]
    ax.set_xticklabels(labels, rotation=90, ha="center", va="top", fontsize=feature_fs)
    ax.set_title(title, loc="left", pad=8)
    ax.set_xlabel("CNA features", labelpad=12)
    ax.set_xlim(-0.5, n_features - 0.5)
    ax.set_ylim(n_samples - 0.5, -0.5)
    ax.set_xticks(np.arange(-.5, n_features, 1), minor=True)
    ax.set_yticks(np.arange(-.5, n_samples, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.45)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.tick_params(axis="x", length=0, pad=4)
    ax.tick_params(axis="y", length=0)
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_color("#333333")
        sp.set_linewidth(0.8)

    legend_handles = [
        Patch(facecolor="#2C7FB8", edgecolor="none", label="present"),
        Patch(facecolor="#F7F7F7", edgecolor="#CCCCCC", label="absent"),
    ] + class_patches
    ax.legend(handles=legend_handles, frameon=False, title="Annotation", bbox_to_anchor=(1.01, 1.0), loc="upper left", borderaxespad=0)

    # Explicit margins keep borders visible while bbox_inches='tight' keeps long
    # vertical feature labels and outside legends inside the exported file.
    fig.subplots_adjust(left=0.105, right=0.82, bottom=0.34, top=0.91, wspace=0.035)
    out = figdir / out_name
    savefig(out, tight=True, pad_inches=0.32)
    outputs.append(str(out))
    return outputs


def plot_heatmap(heatmap_matrix: pd.DataFrame, classification: pd.DataFrame, figdir: Path, max_features: int) -> list[str]:
    return plot_binary_matrix(heatmap_matrix, classification, figdir, "cna_feature_heatmap.png", "CNA feature heatmap", max_features)


def plot_driver_oncoprint(driver_matrix: pd.DataFrame, classification: pd.DataFrame, figdir: Path, max_features: int = 40) -> list[str]:
    """Previous-style driver-region oncoprint with unclipped margins.

    The prior 90-degree-label style was retained because it is cleaner for
    genomic coordinates and avoids the clutter introduced by 45/60-degree
    wrapped labels. Only margins, padding, and exported bounding boxes were
    changed.
    """
    outputs = []
    if driver_matrix.empty or driver_matrix.shape[0] < 1 or driver_matrix.shape[1] < 1:
        return outputs
    X = driver_matrix.copy().apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    X = X.loc[:, X.abs().sum(axis=0) > 0]
    if X.empty:
        return outputs
    cols = X.abs().sum(axis=0).sort_values(ascending=False).index.tolist()[:max_features]
    X = X[cols]
    if not classification.empty and "sample" in classification.columns:
        order = [s for s in classification.sort_values(["cna_burden_class", "n_cna_events"], ascending=[True, False])["sample"].astype(str) if s in X.index]
        if order:
            X = X.loc[order]

    samples = X.index.astype(str).tolist()
    n_samples, n_features = X.shape
    sample_fs = 7.8 if n_samples <= 35 else (6.7 if n_samples <= 65 else 5.8)
    feature_fs = 6.9 if n_features <= 45 else (5.9 if n_features <= 70 else 4.9)

    width = max(8.6, min(17.5, 0.28 * n_features + 4.2))
    height = max(3.8, min(14.5, 0.30 * n_samples + 2.1))
    fig = plt.figure(figsize=(width, height))
    gs = fig.add_gridspec(nrows=1, ncols=2, width_ratios=[0.24, 5.0], wspace=0.035)
    ax_ann = fig.add_subplot(gs[0, 0])
    ax = fig.add_subplot(gs[0, 1])

    codes, ann_cmap, class_patches = class_annotation(samples, classification)
    ax_ann.imshow(codes, aspect="auto", interpolation="nearest", cmap=ann_cmap)
    ax_ann.set_xticks([0])
    ax_ann.set_xticklabels(["Class"], rotation=90, fontsize=max(sample_fs, 6.0), fontweight="bold")
    ax_ann.set_yticks(np.arange(n_samples))
    ax_ann.set_yticklabels(samples, fontsize=sample_fs)
    ax_ann.tick_params(axis="both", length=0, pad=2)
    for sp in ax_ann.spines.values():
        sp.set_visible(False)

    cmap = ListedColormap([ONCOPRINT_COLORS[i] for i in [-2, -1, 0, 1, 2]])
    norm = BoundaryNorm([-2.5, -1.5, -0.5, 0.5, 1.5, 2.5], cmap.N)
    ax.imshow(X.clip(-2, 2).values, aspect="auto", interpolation="nearest", cmap=cmap, norm=norm)
    ax.set_yticks(np.arange(n_samples))
    ax.set_yticklabels([])
    ax.set_xticks(np.arange(n_features))
    labels = [clean_label(c, 30) for c in X.columns]
    ax.set_xticklabels(labels, rotation=90, ha="center", va="top", fontsize=feature_fs)
    ax.set_title("Pan-cancer driver-region CNA oncoprint", loc="left", pad=8)
    ax.set_xlabel("Driver / canonical CNA regions", labelpad=12)
    ax.set_xlim(-0.5, n_features - 0.5)
    ax.set_ylim(n_samples - 0.5, -0.5)
    ax.set_xticks(np.arange(-.5, n_features, 1), minor=True)
    ax.set_yticks(np.arange(-.5, n_samples, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.50)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.tick_params(axis="x", length=0, pad=4)
    ax.tick_params(axis="y", length=0)
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_color("#333333")
        sp.set_linewidth(0.8)

    event_handles = [
        Patch(facecolor=ONCOPRINT_COLORS[-2], edgecolor="none", label="deep loss"),
        Patch(facecolor=ONCOPRINT_COLORS[-1], edgecolor="none", label="loss"),
        Patch(facecolor=ONCOPRINT_COLORS[1], edgecolor="none", label="gain"),
        Patch(facecolor=ONCOPRINT_COLORS[2], edgecolor="none", label="amplification"),
    ]
    ax.legend(handles=event_handles + class_patches, frameon=False, title="CNA state / burden", bbox_to_anchor=(1.01, 1.0), loc="upper left", borderaxespad=0)

    fig.subplots_adjust(left=0.105, right=0.82, bottom=0.34, top=0.91, wspace=0.035)
    out = figdir / "driver_region_oncoprint.png"
    savefig(out, tight=True, pad_inches=0.32)
    outputs.append(str(out))
    return outputs

def plot_pca(pca: pd.DataFrame, classification: pd.DataFrame, figdir: Path) -> list[str]:
    outputs = []
    if pca.empty or not {"PC1", "PC2"}.issubset(pca.columns):
        return outputs
    df = pca.merge(classification[["sample", "cna_burden_class", "rule_based_cna_class"]], on="sample", how="left")
    fig, ax = plt.subplots(figsize=(7.4, 5.5))
    for cls, sub in df.groupby("cna_burden_class", dropna=False):
        ax.scatter(sub["PC1"], sub["PC2"], label=str(cls), s=58, color=class_color(cls), edgecolor="white", linewidth=0.8, alpha=0.95)
        if len(df) <= 35:
            for _, row in sub.iterrows():
                ax.annotate(str(row["sample"]), (row["PC1"], row["PC2"]), xytext=(3, 3), textcoords="offset points", fontsize=6.8)
    pc1v = df["PC1_variance_explained"].dropna().iloc[0] * 100 if "PC1_variance_explained" in df.columns and df["PC1_variance_explained"].notna().any() else np.nan
    pc2v = df["PC2_variance_explained"].dropna().iloc[0] * 100 if "PC2_variance_explained" in df.columns and df["PC2_variance_explained"].notna().any() else np.nan
    ax.set_xlabel(f"PC1 ({pc1v:.1f}% var.)" if np.isfinite(pc1v) else "PC1")
    ax.set_ylabel(f"PC2 ({pc2v:.1f}% var.)" if np.isfinite(pc2v) else "PC2")
    ax.set_title("PCA of CNA event / driver matrix", loc="left")
    xygrid(ax); despine(ax)
    ax.legend(frameon=False, title="Burden class", bbox_to_anchor=(1.01, 1.0), loc="upper left", borderaxespad=0)
    out = figdir / "cna_pca.png"
    savefig(out); outputs.append(str(out))
    return outputs


def copy_tables(paths: list[Path], tdir: Path) -> None:
    for p in paths:
        if p.exists():
            shutil.copy2(p, tdir / p.name)


def html_table_preview(df: pd.DataFrame, n: int = 20) -> str:
    if df.empty:
        return "<p>No rows.</p>"
    return df.head(n).to_html(index=False, escape=True, border=0, classes="table")


def figure_card(fig: str) -> str:
    p = Path(fig)
    title = html.escape(p.stem.replace("_", " ").title())
    png = html.escape(str(p))
    pdf = html.escape(str(p.with_suffix(".pdf")))
    return f"""
    <section class="fig-card">
      <h3>{title}</h3>
      <a href="{png}"><img src="{png}" alt="{title}"></a>
      <p class="downloads"><a href="{pdf}">PDF</a> · <a href="{png}">PNG</a></p>
    </section>
    """


def sample_slug(sample: object) -> str:
    s = str(sample)
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")
    return s or "sample"


def fmt_value(value: object, digits: int = 3) -> str:
    if pd.isna(value):
        return ""
    try:
        f = float(value)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.{digits}f}"
    except Exception:
        return str(value)


def compact_table(df: pd.DataFrame, n: int | None = None, classes: str = "table") -> str:
    if df is None or df.empty:
        return "<p>No rows.</p>"
    d = df.copy()
    if n is not None:
        d = d.head(n)
    return d.to_html(index=False, escape=True, border=0, classes=classes)


def row_to_key_value_table(row: pd.Series, preferred: list[str] | None = None) -> str:
    if row is None or row.empty:
        return "<p>No summary available.</p>"
    if preferred is None:
        preferred = list(row.index)
    records = []
    for key in preferred:
        if key in row.index:
            records.append({"field": key, "value": fmt_value(row.get(key))})
    return pd.DataFrame(records).to_html(index=False, escape=True, border=0, classes="table kv-table")


def agreement_label(call: object) -> str:
    labels = {
        "AGREEMENT": "Agreement: CNA supports reported pathology",
        "PARTIAL_AGREEMENT": "Partial agreement: broadly compatible, not subtype-definitive",
        "AGREEMENT_NON_LYMPHOMA": "Agreement with reported non-lymphoid / non-lymphoma pathology",
        "PARTIAL_AGREEMENT_NON_LYMPHOMA": "Partial agreement with reported non-lymphoid / non-lymphoma pathology",
        "DISAGREEMENT_REVIEW": "Potential discordance: manual review recommended",
        "NOT_ASSESSABLE": "Not assessable from CNA-only data",
        "NO_MATCH": "No matching pathology row found",
    }
    return labels.get(str(call), str(call) if str(call) else "Not assessable")


def agreement_color(call: object) -> str:
    return {
        "AGREEMENT": "#1B7837",
        "AGREEMENT_NON_LYMPHOMA": "#1B7837",
        "PARTIAL_AGREEMENT": "#B36B00",
        "PARTIAL_AGREEMENT_NON_LYMPHOMA": "#B36B00",
        "DISAGREEMENT_REVIEW": "#B2182B",
        "NOT_ASSESSABLE": "#6B7280",
        "NO_MATCH": "#6B7280",
    }.get(str(call), "#6B7280")


def pathology_score_method_text() -> str:
    return (
        "When --pathology is supplied, the agreement score is calculated only in that pathology-enabled branch. The baseline model is a local token agreement model: it extracts tokens from pathology diagnosis/IHC/site fields (for example Hodgkin, tumor, B-cell, follicular, leukemia, carcinoma, CNS tumor, or other terms present in the supplied pathology text) and from CNA-derived features (for example CNA burden, TP53-region loss, CDKN2A/B loss, 2p16/REL-BCL11A, 9p24/JAK2-PD-L1/PD-L2, 18q21/BCL2-MALT1, or other regions allowed by the selected --sample_set catalog). "
        "The token-only score sums base matched-pathology evidence, pathology-CNA token overlap, diagnosis-specific CNA biomarker support, CNA-burden context, IHC-token support, and penalties for discordant patterns. If --pathology_use_biomed_models true, three optional biomedical transformer language models compare pathology text against CNA-evidence text; when at least one succeeds, final score = 0.70 × token-only score + 0.30 × mean biomedical semantic score. "
        "The score is an explainability/compatibility score and not a final diagnosis. A probability is truly calibrated only if --score_calibration_table supplies labelled reference outcomes; otherwise the probability shown is an uncalibrated sigmoid-derived probability-like estimate."
    )


def probable_cna_score_method_text() -> str:
    return (
        "The probable CNA-based classification is calculated for every sample, even without pathology, from CNA tokens alone. The score uses base informative-CNA evidence, CNA-burden context, canonical driver-region tokens, pattern-specificity support, and penalties for flat/ambiguous or discordant patterns. This is a molecular CNA-pattern suggestion, not an integrated tumor diagnosis."
    )


def evidence_tier_method_text() -> str:
    return (
        "Evidence-tier labels describe how a CNA region is used in this research report. driver-CNA means a canonical/recurrent copy-number region in the built-in context-specific CNA catalog that can support a biologically meaningful CNA pattern. supportive-CNA means compatible but weaker copy-number evidence that supports context rather than a subtype call. CNA-context means broad information such as CNA burden, aneuploidy, arm-level change, chromosomal instability, or complexity. driver-CNA/high-risk-context means the CNA touches a high-risk axis such as TP53-region loss, but does not prove mutation or biallelic inactivation. driver-CNA/actionability-context means the locus can be clinically or biologically relevant in some diseases, but requires clinical-grade confirmation and the correct tumor context. context-dependent means the same CNA has different meanings depending on the selected --sample_set and the pathology context. These evidence tiers are research-reporting tiers, not AMP/ASCO/CAP clinical actionability tiers."
    )


def low_pass_wgs_capability_text() -> str:
    return (
        "Low-pass WGS can screen the whole genome for copy-number gains, losses, deep losses and amplifications; estimate CNA burden, altered genome size, chromosomal/arm-level complexity and aneuploidy; flag recurrent context-specific CNA regions; and support cohort recurrence analysis with GISTIC2 when enough samples are available. It cannot replace SNV/indel testing, fusion/translocation assays, methylation, expression, IHC or expert pathology review."
    )


def pathology_section_for_sample(sample: str, pathology_concordance: pd.DataFrame | None) -> str:
    if pathology_concordance is None or pathology_concordance.empty or "sample" not in pathology_concordance.columns:
        return ""
    pc = pathology_concordance.copy()
    pc["sample"] = pc["sample"].astype(str)
    m = pc[pc["sample"] == str(sample)]
    if m.empty:
        return ""
    r = m.iloc[0]
    call = str(r.get("agreement_call", ""))
    if call in {"", "PATHOLOGY_NOT_PROVIDED"}:
        return ""
    pairs = [
        ("agreement_call", agreement_label(call)),
        ("agreement_score", r.get("agreement_score", "")),
        ("token_only_score", r.get("agreement_score_token_only", "")),
        ("final_score_source", r.get("agreement_score_final_source", "")),
        ("probability_estimate", r.get("agreement_probability_estimate", "")),
        ("probability_calibration_status", r.get("agreement_probability_calibration_status", "")),
        ("probability_method", r.get("agreement_probability_method", "")),
        ("biomedical_model_consensus_score", r.get("agreement_biomed_consensus_score", "")),
        ("biomedical_model_trial_scores", r.get("agreement_biomed_model_scores", "")),
        ("biomedical_model_status", r.get("agreement_biomed_model_status", "")),
        ("reported_pathology_diagnosis", r.get("pathology_final_diagnosis", "")),
        ("pathology_category", "; ".join([x for x in [str(r.get("pathology_diagnosis_category_1", "") or ""), str(r.get("pathology_diagnosis_category_2", "") or "")] if x and x.lower() != "nan"])),
        ("inferred_pathology_lineage_subtype", f"{r.get('pathology_lineage','')} / {r.get('pathology_subtype_inferred','')}"),
        ("CNA_knowledge_pattern", r.get("cna_knowledge_pattern", "")),
        ("probable_CNA_based_classification", r.get("probable_cna_classification", "")),
        ("why_this_call_was_made", r.get("agreement_summary", "")),
        ("numeric_score_breakdown", r.get("agreement_score_breakdown", "")),
        ("token_overlap", r.get("agreement_token_overlap", "")),
        ("pathology_tokens", r.get("agreement_pathology_tokens", "")),
        ("CNA_tokens", r.get("agreement_cna_tokens", "")),
        ("explainability_rationale", r.get("agreement_rationale", "")),
        ("supporting_evidence", r.get("supporting_evidence", "")),
        ("cautionary_evidence", r.get("cautionary_evidence", "")),
        ("IHC_pathology_highlights", r.get("pathology_ihc_highlights", "")),
        ("model", r.get("agreement_score_model", "")),
        ("how_score_was_assessed", pathology_score_method_text()),
    ]
    table = pd.DataFrame([{"field": k, "value": fmt_value(v)} for k, v in pairs]).to_html(index=False, escape=True, border=0, classes="table kv-table")
    color = agreement_color(call)
    return f"""
<div class="card pathology-card"><h2>Pathology agreement assessment</h2>
<div class="agreement-banner" style="background:{html.escape(color)}"><strong>{html.escape(agreement_label(call))}</strong><span>Score: <strong>{html.escape(fmt_value(r.get('agreement_score', '')))}</strong></span></div>
<div class="table-wrap">{table}</div>
<div class="note"><strong>Caveat:</strong> This is a compatibility assessment between CNA-derived patterns and the provided pathology text. It does not override the pathologist diagnosis and cannot resolve morphology, immunophenotype, SNVs/indels, balanced translocations, expression, methylation, or clinical context.</div></div>
"""


def probable_cna_section_for_sample(sample: str, pathology_concordance: pd.DataFrame | None) -> str:
    if pathology_concordance is None or pathology_concordance.empty or "sample" not in pathology_concordance.columns:
        return ""
    pc = pathology_concordance.copy()
    pc["sample"] = pc["sample"].astype(str)
    m = pc[pc["sample"] == str(sample)]
    if m.empty:
        return ""
    r = m.iloc[0]
    probable = str(r.get("probable_cna_classification", "") or "")
    if not probable:
        return ""
    pairs = [
        ("probable_CNA_based_classification", probable),
        ("probable_CNA_score", r.get("probable_cna_score", "")),
        ("probability_estimate", r.get("probable_cna_probability_estimate", "")),
        ("probability_calibration_status", r.get("probable_cna_probability_calibration_status", "")),
        ("probability_method", r.get("probable_cna_probability_method", "")),
        ("why_this_classification_was_assigned", r.get("probable_cna_rationale", "")),
        ("numeric_score_breakdown", r.get("probable_cna_score_breakdown", "")),
        ("CNA_tokens_used", r.get("probable_cna_tokens", "")),
        ("model", r.get("probable_cna_model", "local token CNA-pattern score")),
    ]
    table = pd.DataFrame([{"field": k, "value": fmt_value(v)} for k, v in pairs]).to_html(index=False, escape=True, border=0, classes="table kv-table")
    return f"""
<div class="card probable-card"><h2>Probable CNA-based classification</h2>
<div class="note"><strong>Scope:</strong> This is calculated from CNA tokens only. It is shown even when no pathology table is supplied. It should be treated as a molecular pattern suggestion, not as a final pathology diagnosis.</div>
<div class="table-wrap">{table}</div></div>
"""


def split_semicolon_field(value: object) -> list[str]:
    if pd.isna(value):
        return []
    s = str(value).strip()
    if not s or s.lower() in {"none", "none_detected", "none_detected_or_not_run", "nan"}:
        return []
    parts = [x.strip() for x in re.split(r";|,", s) if x.strip()]
    return parts


def driver_flag_interpretation(flag: str) -> str:
    mapping = {
        "2p16_REL_BCL11A_gain_amp": "Gain/amplification of the 2p16 REL/BCL11A axis; recurrent in B-cell lymphomas and selected other tumors and useful as a driver-region flag.",
        "9p21_CDKN2A_B_loss": "Loss affecting the CDKN2A/B tumor-suppressor locus; interpret with pathology and sequencing context.",
        "17p13_TP53_loss": "17p13/TP53-region loss; this supports a TP53-axis CNA pattern but does not prove TP53 mutation.",
        "18q21_BCL2_MALT1_gain_amp": "18q21 gain/amplification involving the BCL2/MALT1 region; relevant to B-cell lymphoma biology when pathology is compatible.",
        "8q24_MYC_gain_amp": "8q24/MYC-region gain or amplification; should be correlated with MYC rearrangement/overexpression if available.",
        "6q_loss_PRDM1_TNFAIP3_axis": "6q loss involving PRDM1/TNFAIP3-related driver regions, especially relevant to lymphoma when pathology is compatible.",
        "10q23_PTEN_loss": "10q23/PTEN-region loss; a PI3K/AKT pathway-related CNA flag.",
        "9p24_JAK2_PDL1_PDL2_gain_amp": "9p24 gain/amplification involving JAK2/PD-L1/PD-L2; potentially relevant to immune-evasion biology.",
        "15q21_B2M_loss": "B2M-region loss; may suggest altered antigen-presentation biology when confirmed.",
        "12q15_MDM2_CDK4_gain_amp": "12q15 MDM2/CDK4-region gain/amplification; interpret with focality and tumor type.",
        "chr7_gain_pattern": "Chromosome 7 gain pattern detected.",
        "22q_loss_pattern": "22q loss pattern detected.",
        "1p_loss_pattern": "1p loss pattern detected.",
        "1q_gain_pattern": "1q gain pattern detected.",
    }
    return mapping.get(flag, "Driver/canonical CNA flag detected; correlate with pathology and orthogonal molecular data.")


def build_sample_interpretation(row: pd.Series, events: pd.DataFrame, driver_hits: pd.DataFrame, gistic_calls: pd.DataFrame) -> str:
    if row is None or row.empty:
        return "<p>No classification row was available for this sample.</p>"
    sample = html.escape(str(row.get("sample", "sample")))
    rule = html.escape(str(row.get("rule_based_cna_class", "not_available")))
    burden = html.escape(str(row.get("cna_burden_class", "not_available")))
    direction = html.escape(str(row.get("gain_loss_direction_class", "not_available")))
    breadth = html.escape(str(row.get("focal_broad_class", "not_available")))
    n_events = int(float(row.get("n_cna_events", 0) or 0))
    altered = fmt_value(row.get("altered_mb", 0), digits=1)
    n_chr = fmt_value(row.get("n_chromosomes_affected", 0))
    n_arms = fmt_value(row.get("n_arms_affected", 0))
    flags = split_semicolon_field(row.get("driver_region_flags", ""))

    bullets = []
    if n_events == 0 or burden.startswith("CNA-flat"):
        bullets.append(f"<li><strong>{sample}</strong> has no high-confidence CNA under the current SAMURAI/low-pass WGS thresholds and is classified as <strong>{rule}</strong>.</li>")
    else:
        bullets.append(f"<li><strong>{sample}</strong> is classified as <strong>{rule}</strong> with burden class <strong>{burden}</strong>.</li>")
        bullets.append(f"<li>The sample has <strong>{n_events}</strong> CNA events, approximately <strong>{altered} Mb</strong> altered, affecting <strong>{n_chr}</strong> chromosomes and <strong>{n_arms}</strong> chromosome arms.</li>")
        bullets.append(f"<li>Copy-number direction is <strong>{direction}</strong>; event breadth is <strong>{breadth}</strong>.</li>")

    if flags:
        bullets.append("<li>Canonical/driver CNA flags: <strong>" + html.escape(", ".join(flags)) + "</strong>.</li>")
    else:
        bullets.append("<li>No canonical context-specific driver-region flag was detected by the current region catalog.</li>")

    try:
        n_gistic = int(float(row.get("n_gistic_significant_lesions", 0) or 0))
        n_gistic_hi = int(float(row.get("n_gistic_high_level_lesions", 0) or 0))
        if n_gistic > 0:
            bullets.append(f"<li>GISTIC2 contributed <strong>{n_gistic}</strong> recurrent lesion calls for this sample, including <strong>{n_gistic_hi}</strong> high-level calls.</li>")
    except Exception:
        pass

    flag_rows = []
    for flag in flags:
        flag_rows.append({"driver_flag": flag, "interpretation": driver_flag_interpretation(flag)})
    flag_html = "" if not flag_rows else "<h3>Driver-region interpretation</h3>" + compact_table(pd.DataFrame(flag_rows))

    return "<ul>" + "\n".join(bullets) + "</ul>" + flag_html + "<p class='small muted'><strong>Caution:</strong> this is a CNA-only interpretation from low-pass WGS/SAMURAI calls. It is not a formal tumor/LymphGen subtype and should be integrated with histology, immunophenotype, SNVs/indels, SV/translocations, and clinical data.</p>"


def sample_state_counts(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "state" not in events.columns:
        return pd.DataFrame(columns=["state", "n_events", "altered_mb"])
    d = events.copy()
    d["size_mb"] = pd.to_numeric(d.get("size_mb", 0), errors="coerce").fillna(0)
    return d.groupby("state", dropna=False).agg(n_events=("state", "size"), altered_mb=("size_mb", "sum")).reset_index().sort_values("n_events", ascending=False)


def make_sample_reports(
    classification: pd.DataFrame,
    summary: pd.DataFrame,
    clean_events: pd.DataFrame,
    driver_hits: pd.DataFrame,
    driver_matrix: pd.DataFrame,
    gistic_matrix: pd.DataFrame,
    gistic_long: pd.DataFrame,
    pathology_concordance: pd.DataFrame | None = None,
) -> None:
    outdir = Path("sample_reports")
    outdir.mkdir(exist_ok=True)
    if classification.empty or "sample" not in classification.columns:
        (outdir / "index.html").write_text("<html><body><h1>No sample reports generated</h1></body></html>")
        return

    events = clean_events.copy()
    if not events.empty and "sample" in events.columns:
        events["sample"] = events["sample"].astype(str)
    dh = driver_hits.copy()
    if not dh.empty and "sample" in dh.columns:
        dh["sample"] = dh["sample"].astype(str)
    gl = gistic_long.copy()
    if not gl.empty and "sample" in gl.columns:
        gl["sample"] = gl["sample"].astype(str)
    summ = summary.copy()
    if not summ.empty and "sample" in summ.columns:
        summ["sample"] = summ["sample"].astype(str)

    sample_links = []
    preferred_cols = [
        "sample", "rule_based_cna_class", "cna_burden_class", "gain_loss_direction_class", "focal_broad_class",
        "driver_region_flags", "n_gistic_significant_lesions", "n_gistic_high_level_lesions", "gistic_lesion_flags",
        "n_cna_events", "n_gain", "n_amplification", "n_loss", "n_deep_loss", "altered_mb", "gain_mb", "loss_mb",
        "n_chromosomes_affected", "n_arms_affected", "n_focal_events_leq_focal_mb", "n_broad_events_geq_broad_mb",
        "max_abs_log2", "max_gain_log2", "min_loss_log2", "hierarchical_cluster", "nmf_cluster",
    ]
    event_cols = [c for c in [
        "sample", "state", "chrom", "start", "end", "size_mb", "cytoband", "n_bins", "mean_log2", "median_log2",
        "estimated_total_copy_number", "copy_code", "molecular_piece", "cna_shorthand", "source", "input_source_file"
    ] if c in events.columns]

    for _, row in classification.sort_values("sample").iterrows():
        sample = str(row.get("sample", "sample"))
        slug = sample_slug(sample)
        ev = events[events["sample"] == sample].copy() if not events.empty and "sample" in events.columns else pd.DataFrame()
        sample_dh = dh[dh["sample"] == sample].copy() if not dh.empty and "sample" in dh.columns else pd.DataFrame()
        sample_gl = gl[gl["sample"] == sample].copy() if not gl.empty and "sample" in gl.columns else pd.DataFrame()
        sample_summary = summ[summ["sample"] == sample].copy() if not summ.empty and "sample" in summ.columns else pd.DataFrame()
        sample_gm = pd.DataFrame()
        if not gistic_matrix.empty and sample in gistic_matrix.index.astype(str).tolist():
            gm_row = gistic_matrix.copy()
            gm_row.index = gm_row.index.astype(str)
            vals = gm_row.loc[[sample]].T.reset_index()
            vals.columns = ["gistic_feature", "call_value"]
            vals = vals[pd.to_numeric(vals["call_value"], errors="coerce").fillna(0) != 0]
            sample_gm = vals

        if not ev.empty:
            ev["chrom_sort"] = ev["chrom"].astype(str).str.replace("chr", "", regex=False).replace({"X":"23", "Y":"24"}) if "chrom" in ev.columns else 0
            try:
                ev["chrom_sort"] = pd.to_numeric(ev["chrom_sort"], errors="coerce").fillna(999)
                ev["start_sort"] = pd.to_numeric(ev.get("start", 0), errors="coerce").fillna(0)
                ev = ev.sort_values(["chrom_sort", "start_sort", "state"]).drop(columns=["chrom_sort", "start_sort"])
            except Exception:
                pass

        state_counts = sample_state_counts(ev)
        interpretation = build_sample_interpretation(row, ev, sample_dh, sample_gm)
        summary_html = row_to_key_value_table(row, preferred_cols)
        sample_summary_html = compact_table(sample_summary.drop(columns=[], errors="ignore")) if not sample_summary.empty else "<p>No sample summary table row available.</p>"
        state_counts_html = compact_table(state_counts)
        driver_hits_html = compact_table(sample_dh)
        gistic_calls_html = compact_table(sample_gl if not sample_gl.empty else sample_gm)
        events_html = compact_table(ev[event_cols] if event_cols and not ev.empty else ev)
        pathology_html = pathology_section_for_sample(sample, pathology_concordance)
        probable_html = probable_cna_section_for_sample(sample, pathology_concordance)

        driver_matrix_html = "<p>No driver-region matrix row available.</p>"
        if not driver_matrix.empty:
            dm = driver_matrix.copy()
            dm.index = dm.index.astype(str)
            if sample in dm.index:
                vals = dm.loc[sample].reset_index()
                vals.columns = ["driver_region", "signed_call"]
                vals = vals[pd.to_numeric(vals["signed_call"], errors="coerce").fillna(0) != 0]
                driver_matrix_html = compact_table(vals)

        text = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CNA sample report - {html.escape(sample)}</title>
<style>
body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; color: #1f2933; background: #f8fafc; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 30px 34px 60px; }}
h1 {{ margin-bottom: 2px; }}
h2 {{ margin-top: 28px; font-size: 18px; border-bottom: 1px solid #e5e7eb; padding-bottom: 7px; }}
h3 {{ margin-top: 18px; font-size: 14px; }}
.note {{ background: #fff7d6; border-left: 5px solid #d6a300; padding: 12px 16px; margin: 16px 0; border-radius: 6px; }}
.card {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 16px; margin: 14px 0; }}
.pathology-card h2 {{ margin-top: 0; border-bottom: none; padding-bottom: 0; }}
.agreement-banner {{ color: white; display: flex; justify-content: space-between; gap: 18px; padding: 10px 14px; border-radius: 7px; margin: 10px 0 12px; }}
.table-wrap {{ overflow-x: auto; background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 8px; }}
.table {{ border-collapse: collapse; font-size: 12px; min-width: 100%; }}
.table th {{ background: #f3f4f6; }}
.table th, .table td {{ border: 1px solid #d1d5db; padding: 5px 7px; text-align: left; vertical-align: top; }}
.kv-table th:first-child, .kv-table td:first-child {{ white-space: nowrap; font-weight: bold; }}
.small {{ font-size: 12px; }}
.muted {{ color: #6b7280; }}
a {{ color: #1f77b4; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
ul {{ line-height: 1.6; }}
</style>
</head>
<body><main>
<p><a href="../cna_classifier_report.html">← Cohort report</a> · <a href="index.html">Sample index</a></p>
<h1>CNA sample report: {html.escape(sample)}</h1>
<p class="muted">Low-pass WGS / SAMURAI CNA-only report.</p>
<div class="note"><strong>Scope:</strong> this report summarizes CNA burden, CNA state composition, canonical driver-region flags, and probable CNA-pattern interpretation for one sample. It does not replace integrated molecular/pathology classification.</div>
<div class="note"><strong>What low-pass WGS can do:</strong> {html.escape(low_pass_wgs_capability_text())}</div>
<div class="note"><strong>Probable CNA score:</strong> {html.escape(probable_cna_score_method_text())}</div>
<div class="note"><strong>Evidence tiers:</strong> {html.escape(evidence_tier_method_text())}</div>
<div class="note"><strong>PubMed / LLM fallback:</strong> when --knowledge_web true, feature-level literature is retrieved from Europe-PMC/PubMed-style metadata using the --sample_set context. When --knowledge_literature_llm true, retrieved titles/abstracts are processed by local Hugging Face summarization/text-generation models; if no model completes, the pipeline falls back to deterministic PubMed-text extraction and reports the model/status in the biomarker cards.</div>
{probable_html}
{pathology_html}
<div class="card"><h2>Interpretation</h2>{interpretation}</div>
<h2>Classification fields</h2><div class="table-wrap">{summary_html}</div>
<h2>SAMURAI CNA summary row</h2><div class="table-wrap">{sample_summary_html}</div>
<h2>CNA state counts</h2><div class="table-wrap">{state_counts_html}</div>
<h2>Driver-region calls</h2><div class="table-wrap">{driver_matrix_html}</div>
<h2>Driver-region hit table</h2><div class="table-wrap">{driver_hits_html}</div>
<h2>Full CNA events for this sample</h2><div class="table-wrap">{events_html}</div>
</main></body></html>
"""
        out = outdir / f"{slug}_CNA_report.html"
        out.write_text(text)
        path_call = ""
        if pathology_concordance is not None and not pathology_concordance.empty and "sample" in pathology_concordance.columns:
            mm = pathology_concordance[pathology_concordance["sample"].astype(str) == sample]
            if not mm.empty:
                path_call = mm.iloc[0].get("agreement_call", "")
        sample_links.append({
            "sample": sample,
            "report": f"{slug}_CNA_report.html",
            "pathology_agreement": path_call,
            "probable_cna_classification": (mm.iloc[0].get("probable_cna_classification", "") if pathology_concordance is not None and not pathology_concordance.empty and "sample" in pathology_concordance.columns and not mm.empty else ""),
            "probable_cna_score": (mm.iloc[0].get("probable_cna_score", "") if pathology_concordance is not None and not pathology_concordance.empty and "sample" in pathology_concordance.columns and not mm.empty else ""),
            "rule_based_cna_class": row.get("rule_based_cna_class", ""),
            "cna_burden_class": row.get("cna_burden_class", ""),
            "n_cna_events": row.get("n_cna_events", ""),
            "driver_region_flags": row.get("driver_region_flags", ""),
        })

    index_df = pd.DataFrame(sample_links)
    links_rows = []
    for _, r in index_df.iterrows():
        links_rows.append({
            "sample": f"<a href='{html.escape(str(r['report']))}'>{html.escape(str(r['sample']))}</a>",
            "pathology_agreement": html.escape(str(r.get("pathology_agreement", ""))),
            "probable_cna_classification": html.escape(str(r.get("probable_cna_classification", ""))),
            "probable_cna_score": html.escape(fmt_value(r.get("probable_cna_score", ""))),
            "rule_based_cna_class": html.escape(str(r.get("rule_based_cna_class", ""))),
            "cna_burden_class": html.escape(str(r.get("cna_burden_class", ""))),
            "n_cna_events": html.escape(fmt_value(r.get("n_cna_events", ""))),
            "driver_region_flags": html.escape(str(r.get("driver_region_flags", ""))),
        })
    # Build index manually so sample report links remain clickable.
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{row[col]}</td>" for col in ["sample", "pathology_agreement", "probable_cna_classification", "probable_cna_score", "rule_based_cna_class", "cna_burden_class", "n_cna_events", "driver_region_flags"]) + "</tr>"
        for row in links_rows
    )
    index_html = f"""
<!DOCTYPE html><html><head><meta charset='utf-8'><title>CNA sample reports</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:32px;color:#1f2933}}table{{border-collapse:collapse;font-size:12px}}th,td{{border:1px solid #d1d5db;padding:5px 7px;text-align:left;vertical-align:top}}th{{background:#f3f4f6}}a{{color:#1f77b4;text-decoration:none}}a:hover{{text-decoration:underline}}</style>
</head><body><h1>CNA sample reports</h1><p><a href='../cna_classifier_report.html'>← Cohort report</a></p><table><thead><tr><th>sample</th><th>pathology_agreement</th><th>probable_CNA_classification</th><th>probable_CNA_score</th><th>rule_based_cna_class</th><th>cna_burden_class</th><th>n_cna_events</th><th>driver_region_flags</th></tr></thead><tbody>{rows_html}</tbody></table></body></html>
"""
    (outdir / "index.html").write_text(index_html)

def make_report(figures: list[str], classification: pd.DataFrame, recurrent: pd.DataFrame, gistic_status: pd.DataFrame, gistic_summary: pd.DataFrame, pathology_concordance: pd.DataFrame | None = None) -> None:
    fig_html = "\n".join(figure_card(fig) for fig in figures)
    if classification.empty:
        class_summary = "<p>No classification table produced.</p>"
        n_samples = 0
        single_note = ""
    else:
        n_samples = int(classification["sample"].nunique()) if "sample" in classification.columns else len(classification)
        class_counts = classification["rule_based_cna_class"].value_counts().rename_axis("class").reset_index(name="n_samples")
        class_summary = html_table_preview(class_counts, n=50)
        single_note = "" if n_samples != 1 else "<div class='note'><strong>Single-sample mode:</strong> recurrence, GISTIC2 significance, PCA, and unsupervised clustering are limited with one sample. Rule-based CNA burden and driver-region annotations remain available.</div>"
    pathology_summary = ""
    if pathology_concordance is not None and not pathology_concordance.empty and "agreement_call" in pathology_concordance.columns:
        path_counts = pathology_concordance["agreement_call"].value_counts(dropna=False).rename_axis("agreement_call").reset_index(name="n_samples")
        probable_counts = pathology_concordance["probable_cna_classification"].value_counts(dropna=False).rename_axis("probable_cna_classification").reset_index(name="n_samples") if "probable_cna_classification" in pathology_concordance.columns else pd.DataFrame()
        cols = [c for c in ["sample", "probable_cna_classification", "probable_cna_score", "probable_cna_probability_estimate", "agreement_call", "agreement_score", "agreement_probability_estimate", "agreement_score_token_only", "agreement_biomed_consensus_score", "agreement_biomed_model_status", "agreement_score_breakdown", "pathology_final_diagnosis", "agreement_summary"] if c in pathology_concordance.columns]
        pathology_summary = "<h2>Probable CNA-based classification summary</h2><div class='note'><strong>Model:</strong> local token CNA-pattern model. This classification is calculated even without --pathology and is not a final diagnosis.</div><div class='table-wrap'>" + html_table_preview(probable_counts, n=50) + "</div><h2>Pathology agreement summary</h2><div class='note'><strong>Score method:</strong> " + html.escape(pathology_score_method_text()) + "</div><div class='table-wrap'>" + html_table_preview(path_counts, n=50) + "</div><h2>Pathology/probable-classification table preview</h2><div class='table-wrap'>" + html_table_preview(pathology_concordance[cols], n=30) + "</div>"
    text = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Cancer-agnostic CNA classifier report</title>
<style>
:root {{ --ink:#1f2933; --muted:#6b7280; --line:#e5e7eb; --panel:#ffffff; --bg:#f8fafc; --accent:#1f77b4; }}
body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; color: var(--ink); background: var(--bg); }}
main {{ max-width: 1240px; margin: 0 auto; padding: 34px 36px 60px; }}
h1 {{ font-size: 28px; margin: 0 0 6px; letter-spacing: -0.02em; }}
h2 {{ margin-top: 30px; font-size: 18px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
h3 {{ font-size: 14px; margin: 0 0 12px; }}
.subtitle {{ color: var(--muted); margin-top: 0; }}
.note {{ background: #fff7d6; border-left: 5px solid #d6a300; padding: 12px 16px; margin: 18px 0; border-radius: 6px; }}
.table-wrap {{ overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 8px; }}
.table {{ border-collapse: collapse; font-size: 12px; width: max-content; min-width: 100%; }}
.table th {{ background: #f3f4f6; }}
.table th, .table td {{ border: 1px solid #d1d5db; padding: 5px 7px; text-align: left; vertical-align: top; }}
.fig-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; }}
.fig-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.035); }}
.fig-card img {{ width: 100%; height: auto; display: block; border: 1px solid #eef0f2; border-radius: 8px; background: white; }}
.downloads {{ font-size: 12px; margin: 8px 0 0; color: var(--muted); }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{ background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 4px; padding: 1px 4px; }}
ul {{ line-height: 1.65; }}
</style>
</head>
<body>
<main>
<h1>Cancer-agnostic CNA classifier report</h1>
<p class="subtitle">Publication-oriented CNA-only summary from low-pass WGS / SAMURAI CNA codification. Samples analyzed: <strong>{n_samples}</strong>.</p>
<div class="note"><strong>Interpretation note:</strong> this is Cancer-agnostic CNA-only classification from low-pass WGS/SAMURAI CNA calls. It is useful for CNA burden, recurrent event patterns, and exploratory patient grouping. It should not be interpreted as a formal molecular tumor subtype without SNVs/indels, SV/translocation/fusion data, pathology, and clinical context.</div>
<div class="note"><strong>What low-pass WGS can do:</strong> {html.escape(low_pass_wgs_capability_text())}</div>
<div class="note"><strong>Probable CNA score:</strong> {html.escape(probable_cna_score_method_text())}</div>
<div class="note"><strong>Evidence tiers:</strong> {html.escape(evidence_tier_method_text())}</div>
<div class="note"><strong>PubMed / LLM fallback:</strong> when --knowledge_web true, feature-level literature is retrieved from Europe-PMC/PubMed-style metadata using the --sample_set context. When --knowledge_literature_llm true, retrieved titles/abstracts are processed by local Hugging Face summarization/text-generation models; if no model completes, the pipeline falls back to deterministic PubMed-text extraction and reports the model/status in the biomarker cards.</div>
{single_note}
{pathology_summary}
<h2>Rule-based class counts</h2><div class="table-wrap">{class_summary}</div>
<h2>Patient classification table preview</h2><div class="table-wrap">{html_table_preview(classification, n=30)}</div>
<h2>Top recurrent CNA events from SAMURAI codification</h2><div class="table-wrap">{html_table_preview(recurrent, n=20)}</div>
<h2>GISTIC2 status</h2><div class="table-wrap">{html_table_preview(gistic_status, n=10)}</div>
<h2>Top GISTIC2 lesions</h2><div class="table-wrap">{html_table_preview(gistic_summary, n=25)}</div>
<h2>Single-sample reports</h2><p>Open <a href="sample_reports/index.html">sample_reports/index.html</a> for one HTML report per sample with per-sample interpretation, context-specific driver-region calls, probable CNA classification, pathology agreement when provided, and the full CNA event table. If PDF/HTML knowledge reports were enabled, open <a href="pdf_reports/index.html">pdf_reports/index.html</a> for matched report-style HTML and PDF files generated from the same source tables. If clinician reports were enabled, open <a href="clinician_reports/index.html">clinician_reports/index.html</a> for concise driver/probable-classification summaries.</p>
<h2>Figures</h2><div class="fig-grid">{fig_html}</div>
<h2>Key output tables</h2>
<ul>
  <li><code>report_tables/cna_patient_classification.tsv</code></li>
  <li><code>report_tables/sample_cna_summary.tsv</code></li>
  <li><code>report_tables/recurrent_events.tsv</code></li>
  <li><code>report_tables/driver_region_hits.tsv</code></li>
  <li><code>report_tables/driver_region_matrix.tsv</code></li>
  <li><code>report_tables/event_matrix_binary.tsv</code></li>
  <li><code>report_tables/gistic2_status.tsv</code></li>
  <li><code>report_tables/gistic_lesions_summary.tsv</code></li>
  <li><code>report_tables/gistic_lesions_matrix.tsv</code></li>
  <li><code>report_tables/gistic_full.seg</code></li>
  <li><code>sample_reports/index.html</code></li>
  <li><code>pdf_reports/index.html</code> when PDF/HTML knowledge reports are enabled</li>
  <li><code>clinician_reports/index.html</code> when clinician driver summaries are enabled</li>
</ul>
</main>
</body>
</html>
"""
    Path("cna_classifier_report.html").write_text(text)


def main() -> None:
    setup_paper_theme()
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-events", required=True)
    ap.add_argument("--sample-summary", required=True)
    ap.add_argument("--event-matrix", required=True)
    ap.add_argument("--driver-matrix", required=True)
    ap.add_argument("--recurrent-events", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--gistic-full-seg", required=True)
    ap.add_argument("--gistic-markers", required=True)
    ap.add_argument("--gistic-status", required=True)
    ap.add_argument("--gistic-command", required=True)
    ap.add_argument("--gistic-matrix", required=True)
    ap.add_argument("--gistic-long", required=True)
    ap.add_argument("--gistic-summary", required=True)
    ap.add_argument("--classification", required=True)
    ap.add_argument("--unsupervised-clusters", required=True)
    ap.add_argument("--heatmap-matrix", required=True)
    ap.add_argument("--pca-coordinates", required=True)
    ap.add_argument("--plot-top-features", type=int, default=60)
    ap.add_argument("--pathology-concordance", default="", help="Optional pathology_concordance.tsv for report preview and sample report sections.")
    ap.add_argument("--pathology-records", default="", help="Optional matched pathology record table; copied by Nextflow.")
    args = ap.parse_args()

    figdir, tdir = ensure_dirs()
    clean_events = read_tsv(args.clean_events)
    summary = read_tsv(args.sample_summary)
    driver_matrix = read_tsv(args.driver_matrix, index_col=0)
    recurrent = read_tsv(args.recurrent_events)
    gistic_status = read_tsv(args.gistic_status)
    gistic_summary = read_tsv(args.gistic_summary)
    gistic_matrix = read_tsv(args.gistic_matrix, index_col=0)
    gistic_long = read_tsv(args.gistic_long)
    driver_hits = read_tsv(args.driver_hits)
    classification = read_tsv(args.classification)
    heatmap_matrix = read_tsv(args.heatmap_matrix, index_col=0)
    pca = read_tsv(args.pca_coordinates)
    pathology_concordance = read_tsv(args.pathology_concordance) if args.pathology_concordance else pd.DataFrame()
    if not pathology_concordance.empty and "sample" in pathology_concordance.columns:
        pathology_concordance["sample"] = pathology_concordance["sample"].astype(str)

    figures: list[str] = []
    figures += plot_burden(classification, figdir)
    figures += plot_state_counts(summary, figdir)
    figures += plot_complexity_scatter(classification, figdir)
    figures += plot_recurrence(recurrent, figdir, top_n=30)
    figures += plot_gistic_summary(gistic_summary, figdir, top_n=30)
    figures += plot_driver_oncoprint(driver_matrix, classification, figdir, max_features=min(args.plot_top_features, 40))
    figures += plot_heatmap(heatmap_matrix, classification, figdir, max_features=args.plot_top_features)
    figures += plot_pca(pca, classification, figdir)

    copy_tables([
        Path(args.clean_events), Path(args.sample_summary), Path(args.event_matrix), Path(args.driver_matrix),
        Path(args.recurrent_events), Path(args.driver_hits), Path(args.gistic_full_seg), Path(args.gistic_markers),
        Path(args.gistic_status), Path(args.gistic_command), Path(args.gistic_matrix), Path(args.gistic_long),
        Path(args.gistic_summary), Path(args.classification), Path(args.unsupervised_clusters), Path(args.heatmap_matrix),
        Path(args.pca_coordinates),
    ] + ([Path(args.pathology_concordance)] if args.pathology_concordance else []) + ([Path(args.pathology_records)] if args.pathology_records else []), tdir)
    make_sample_reports(classification, summary, clean_events, driver_hits, driver_matrix, gistic_matrix, gistic_long, pathology_concordance)
    make_report(figures, classification, recurrent, gistic_status, gistic_summary, pathology_concordance)


if __name__ == "__main__":
    main()
