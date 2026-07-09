#!/usr/bin/env python3

import argparse
import gzip
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import FuncFormatter


###############################################################################
# Constants
###############################################################################

CHR_ORDER = {str(i): i for i in range(1, 23)}
CHR_ORDER.update({"X": 23, "Y": 24, "M": 25, "MT": 25})

STATE_ORDER = ["deep_loss", "loss", "gain", "amplification"]

STATE_LABELS = {
    "deep_loss": "Deep loss",
    "loss": "Loss",
    "gain": "Gain",
    "amplification": "Amplification",
}

# Existing event-level colors used by the original script.
PAPER_COLORS = {
    "deep_loss": "#3B6FB6",
    "loss": "#E68613",
    "gain": "#3FA34D",
    "amplification": "#C43C39",
}

# Reference-style colors for the requested panels.
REFERENCE_GAIN_COLOR = "#E41A1C"
REFERENCE_LOSS_COLOR = "#B8B8B8"
REFERENCE_HEADER_COLOR = "#C9B99D"
REFERENCE_GRID_COLOR = "#E6E6E6"
REFERENCE_DASH_COLOR = "#4B3F72"

GAIN_STATES = {"gain", "amplification"}
LOSS_STATES = {"loss", "deep_loss"}

GENE_COLUMN_CANDIDATES = [
    "gene",
    "genes",
    "gene_name",
    "gene_names",
    "symbol",
    "hugo_symbol",
    "hgnc_symbol",
    "overlapping_gene",
    "overlapping_genes",
    "overlap_gene",
    "overlap_genes",
]

LOG2_COLUMN_CANDIDATES = [
    "log2",
    "log2_ratio",
    "log2ratio",
    "log2.copy.ratio",
    "copy_ratio_log2",
    "cnlr",
    "logr",
    "logR",
    "mean_log2",
    "median_log2",
    "bin_log2",
    "bin_log2ratio",
    "copy_number_log2",
    "copy.number.log2",
    "copynumber",
    "copy_number",
    "copy.number",
    "corrected",
    "normalized",
    "value",
    "seg.mean",
    "seg_mean",
    "segment_mean",
    "segmented",
    "ratio",
    "copy_ratio",
]


###############################################################################
# Style
###############################################################################

def apply_paper_style(font_scale=1.0):
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "font.family": "DejaVu Sans",

        "font.size": 9 * font_scale,
        "axes.titlesize": 11 * font_scale,
        "axes.labelsize": 9.5 * font_scale,
        "xtick.labelsize": 8.5 * font_scale,
        "ytick.labelsize": 8.5 * font_scale,
        "legend.fontsize": 8.5 * font_scale,

        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.major.size": 3.5,
        "ytick.major.size": 3.5,
    })


def state_colors():
    return PAPER_COLORS.copy()


def style_axes(ax, hide_top_right=True):
    if hide_top_right:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", which="major", width=0.8, length=3.5)


def save_figure(fig, outdir, stem, dpi=400):
    fig.savefig(outdir / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(outdir / f"{stem}.png", dpi=dpi, bbox_inches="tight")
    fig.savefig(outdir / f"{stem}.svg", bbox_inches="tight")


###############################################################################
# I/O helpers
###############################################################################

def open_text(path):
    path = Path(path)
    return gzip.open(path, "rt") if str(path).endswith(".gz") else path.open("rt")


def norm_chrom(chrom):
    chrom = str(chrom).strip()
    chrom = re.sub(r"^chr", "", chrom, flags=re.I)

    if chrom == "MT":
        chrom = "M"

    return chrom


def chrom_key(chrom):
    chrom = norm_chrom(chrom)
    return (CHR_ORDER.get(chrom, 999), chrom)


def normalize_state(value):
    if pd.isna(value):
        return np.nan

    x = str(value).strip().lower()
    x = re.sub(r"[\s\-]+", "_", x)

    synonyms = {
        "deep_loss": "deep_loss",
        "deep_deletion": "deep_loss",
        "homozygous_deletion": "deep_loss",
        "homdel": "deep_loss",
        "deletion": "loss",
        "del": "loss",
        "loss": "loss",
        "shallow_loss": "loss",
        "copy_loss": "loss",
        "cn_loss": "loss",
        "gain": "gain",
        "copy_gain": "gain",
        "cn_gain": "gain",
        "duplication": "gain",
        "dup": "gain",
        "amplification": "amplification",
        "amp": "amplification",
        "high_gain": "amplification",
        "high_level_gain": "amplification",
    }

    return synonyms.get(x, x)


def detect_sep(path):
    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            if "\t" in line:
                return "\t"
            if "," in line:
                return ","
            return r"\s+"
    return "\t"


def read_table_flexible(path, dtype=None):
    path = Path(path)
    sep = detect_sep(path)
    return pd.read_csv(
        path,
        sep=sep,
        dtype=dtype,
        engine="python",
        comment="#",
        on_bad_lines="warn",
    )


def normalized_name(name):
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def find_column(df, candidates, required=False, label="column"):
    if not df.columns.size:
        if required:
            raise SystemExit(f"Input table has no columns; cannot find {label}.")
        return None

    lookup = {normalized_name(c): c for c in df.columns}

    for candidate in candidates:
        key = normalized_name(candidate)
        if key in lookup:
            return lookup[key]

    if required:
        raise SystemExit(
            f"Could not find {label}. Tried: {', '.join(candidates)}. "
            f"Available columns: {', '.join(map(str, df.columns))}"
        )

    return None


def read_cytoband(path):
    rows = []

    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue

            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue

            chrom = norm_chrom(parts[0])
            if chrom not in CHR_ORDER:
                continue

            rows.append({
                "chrom": chrom,
                "start": int(parts[1]),
                "end": int(parts[2]),
                "band": parts[3],
            })

    cytoband = pd.DataFrame(rows)

    if cytoband.empty:
        raise SystemExit(f"No usable cytobands read from {path}")

    chrom_sizes = cytoband.groupby("chrom")["end"].max().to_dict()

    ordered_chroms = [
        c for c in [str(i) for i in range(1, 23)] + ["X", "Y"]
        if c in chrom_sizes
    ]

    offsets = {}
    x = 0

    for chrom in ordered_chroms:
        offsets[chrom] = x
        x += int(chrom_sizes[chrom])

    genome_size = x

    return cytoband, chrom_sizes, offsets, ordered_chroms, genome_size


def read_events(path):
    """Read cna_events.tsv with flexible separators and canonicalize columns."""
    df = read_table_flexible(path, dtype=str)

    sample_col = find_column(df, ["sample", "sample_id", "sampleid", "tumor", "tumour"], required=True, label="event sample column")
    state_col = find_column(df, ["state", "alteration", "event", "type", "cna", "call"], required=True, label="event state column")
    chrom_col = find_column(df, ["chrom", "chr", "chromosome", "seqnames"], required=True, label="event chromosome column")
    start_col = find_column(df, ["start", "loc.start", "begin", "left"], required=True, label="event start column")
    end_col = find_column(df, ["end", "loc.end", "stop", "right"], required=True, label="event end column")

    # Keep all original columns because source/gene columns are useful downstream.
    if sample_col != "sample":
        df["sample"] = df[sample_col]
    if state_col != "state":
        df["state"] = df[state_col]
    if chrom_col != "chrom":
        df["chrom"] = df[chrom_col]
    if start_col != "start":
        df["start"] = df[start_col]
    if end_col != "end":
        df["end"] = df[end_col]

    df["sample"] = df["sample"].astype(str)
    df["state"] = df["state"].map(normalize_state)
    df["chrom"] = df["chrom"].map(norm_chrom)
    df["start"] = pd.to_numeric(df["start"], errors="coerce")
    df["end"] = pd.to_numeric(df["end"], errors="coerce")

    if "size_mb" in df.columns:
        df["size_mb"] = pd.to_numeric(df["size_mb"], errors="coerce")
    else:
        df["size_mb"] = np.nan

    if "cytoband" not in df.columns:
        df["cytoband"] = df["chrom"]

    if "mean_log2" in df.columns:
        df["mean_log2"] = pd.to_numeric(df["mean_log2"], errors="coerce")
    elif "median_log2" in df.columns:
        df["mean_log2"] = pd.to_numeric(df["median_log2"], errors="coerce")
    else:
        df["mean_log2"] = np.nan

    if "median_log2" in df.columns:
        df["median_log2"] = pd.to_numeric(df["median_log2"], errors="coerce")
    else:
        df["median_log2"] = df["mean_log2"]

    if "estimated_total_copy_number" in df.columns:
        df["estimated_total_copy_number"] = pd.to_numeric(
            df["estimated_total_copy_number"],
            errors="coerce",
        )
    else:
        df["estimated_total_copy_number"] = np.nan

    df = df.dropna(subset=["sample", "state", "chrom", "start", "end"])
    df = df[df["chrom"].isin(CHR_ORDER)]
    df = df[df["state"].isin(STATE_ORDER)]
    df = df[df["end"] > df["start"]]

    df["start"] = df["start"].astype(int)
    df["end"] = df["end"].astype(int)
    df["size_mb"] = df["size_mb"].fillna((df["end"] - df["start"]) / 1e6)

    return df


def read_summary(path):
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=["sample"])

    df = pd.read_csv(path, sep="\t", dtype=str)

    if "sample" not in df.columns:
        return pd.DataFrame(columns=["sample"])

    df["sample"] = df["sample"].astype(str)
    return df


def looks_like_header(tokens):
    if len(tokens) < 3:
        return True
    second_numeric = re.fullmatch(r"[-+]?\d+(\.\d+)?", tokens[1] or "") is not None
    third_numeric = re.fullmatch(r"[-+]?\d+(\.\d+)?", tokens[2] or "") is not None
    return not (second_numeric and third_numeric)


def infer_header(path):
    sep = detect_sep(path)
    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            tokens = re.split(sep if sep != "\t" else "\t", line.strip()) if sep != "," else line.strip().split(",")
            return 0 if looks_like_header(tokens) else None
    return 0


def safe_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def choose_log2_value_column(df, log2_col=None, protected_cols=None, label="bin log2-ratio column"):
    protected_cols = set(protected_cols or [])

    if log2_col:
        if log2_col in df.columns:
            return log2_col
        lookup = {normalized_name(c): c for c in df.columns}
        key = normalized_name(log2_col)
        if key in lookup:
            return lookup[key]
        raise SystemExit(f"Requested --bins-log2-col/--bin-log2-col '{log2_col}' was not found. Available columns: {', '.join(map(str, df.columns))}")

    # First use explicit column names.
    chosen = find_column(df, LOG2_COLUMN_CANDIDATES, required=False, label=label)
    if chosen is not None and chosen not in protected_cols:
        vals = safe_numeric(df[chosen])
        if vals.notna().sum() >= max(5, int(0.05 * len(vals))):
            return chosen

    # Fallback: for headerless BED-like files, choose a numeric column whose distribution
    # resembles log2 ratios rather than coordinates or read counts.
    best = None
    best_score = float("inf")
    for col in df.columns:
        if col in protected_cols:
            continue
        vals = safe_numeric(df[col])
        valid = vals.dropna()
        if len(valid) < max(5, int(0.05 * len(df))):
            continue
        med = float(valid.median())
        q01 = float(valid.quantile(0.01))
        q99 = float(valid.quantile(0.99))
        std = float(valid.std()) if len(valid) > 1 else 0.0
        # Penalize very large ranges/read-count-like columns, but allow copy-number
        # values around 1 or 2 because they can be converted to log2 ratios.
        range_penalty = max(0.0, abs(q01) - 6.0) + max(0.0, abs(q99) - 6.0)
        center_penalty = min(abs(med), abs(med - 1.0), abs(med - 2.0))
        variance_penalty = 3.0 if std == 0 else 0.0
        score = center_penalty * 10.0 + range_penalty * 4.0 + variance_penalty
        if score < best_score:
            best_score = score
            best = col

    if best is None:
        raise SystemExit(
            f"Could not detect {label}. Use --bins-log2-col or --bin-log2-col. "
            f"Available columns: {', '.join(map(str, df.columns))}"
        )
    return best


def normalize_log2_values(values, column_name=""):
    """Convert copy-number or ratio columns to log2 ratios when needed."""
    x = pd.to_numeric(values, errors="coerce").astype(float)
    valid = x.dropna()
    if valid.empty:
        return x

    med = float(valid.median())
    q05 = float(valid.quantile(0.05))
    q95 = float(valid.quantile(0.95))
    name = normalized_name(column_name)

    # Already log2-like: median around zero and negative values are possible.
    if abs(med) < 0.8 and q05 < 0.4 and q95 < 4.0:
        return x

    # Absolute total copy number, often centered around 2.
    if ("copy" in name or "cn" in name or "copynumber" in name) and q05 >= 0 and 1.3 <= med <= 2.8:
        return np.log2(x / 2.0)

    # Copy ratio, often centered around 1.
    if ("ratio" in name or "normalized" in name or "corrected" in name or "value" in name) and q05 >= 0 and 0.55 <= med <= 1.45:
        return np.log2(x)

    # Headerless fallback: decide based on the observed center.
    if q05 >= 0 and 1.3 <= med <= 2.8:
        return np.log2(x / 2.0)
    if q05 >= 0 and 0.55 <= med <= 1.45:
        return np.log2(x)

    return x


def read_bins(path, sample_name=None, log2_col=None):
    """
    Read bin-level log2-ratio / copy-ratio points.

    This accepts the bin files referenced by the cna_events.tsv 'source' column,
    QDNAseq/SAMURAI BED-like outputs, and generic TSV/CSV tables. The log2 column
    is auto-detected, but can be forced with --bins-log2-col / --bin-log2-col.
    """
    if path is None:
        return pd.DataFrame(columns=["sample", "chrom", "start", "end", "log2"])

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    sep = detect_sep(path)
    header = infer_header(path)
    df = pd.read_csv(
        path,
        sep=sep,
        dtype=str,
        engine="python",
        comment="#",
        on_bad_lines="warn",
        header=header,
    )

    if header is None:
        df.columns = [f"col{i}" for i in range(df.shape[1])]

    sample_col = find_column(df, ["sample", "sample_id", "sampleid", "tumor", "tumour"], required=False)
    chrom_col = find_column(df, ["chrom", "chr", "chromosome", "seqnames", "col0"], required=True, label="bin chromosome column")
    start_col = find_column(df, ["start", "loc.start", "begin", "left", "pos", "position", "col1"], required=True, label="bin start/position column")
    end_col = find_column(df, ["end", "loc.end", "stop", "right", "col2"], required=False)

    protected = {chrom_col, start_col}
    if end_col is not None:
        protected.add(end_col)
    if sample_col is not None:
        protected.add(sample_col)

    value_col = choose_log2_value_column(df, log2_col=log2_col, protected_cols=protected)

    out = pd.DataFrame({
        "chrom": df[chrom_col].map(norm_chrom),
        "start": pd.to_numeric(df[start_col], errors="coerce"),
        "log2": normalize_log2_values(df[value_col], value_col),
    })

    if end_col is not None:
        out["end"] = pd.to_numeric(df[end_col], errors="coerce")
    else:
        out["end"] = out["start"] + 1

    if sample_col is not None:
        out["sample"] = df[sample_col].astype(str)
    else:
        out["sample"] = str(sample_name) if sample_name else path.stem.replace(".tsv", "").replace(".csv", "")

    out = out.dropna(subset=["sample", "chrom", "start", "end", "log2"])
    out = out[out["chrom"].isin(CHR_ORDER)]
    out = out[out["end"] >= out["start"]]
    out["start"] = out["start"].astype(int)
    out["end"] = out["end"].astype(int)
    out["source_file"] = str(path)

    return out


def sanitize_filename(value, max_len=120):
    x = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    if not x:
        x = "sample"
    return x[:max_len]


def source_column_name(events, requested=None):
    if requested:
        if requested in events.columns:
            return requested
        lookup = {normalized_name(c): c for c in events.columns}
        key = normalized_name(requested)
        if key in lookup:
            return lookup[key]
        return None
    return find_column(events, ["source", "bin_source", "bins", "bin_file", "bins_file", "qdnaseq_bins", "path", "file"], required=False)


def unique_existing_paths(values):
    out = []
    seen = set()
    for value in values:
        if pd.isna(value):
            continue
        raw = str(value).strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        p = Path(raw)
        if p.exists():
            out.append(p)
    return out


def find_sample_bin_file(sample, events, bin_root=None, source_col=None, bin_pattern=None):
    """Find the bin-level file for one sample.

    Priority:
      1. Existing paths in the events source column.
      2. Recursive search under --bin-root using --bin-pattern.
    """
    sample = str(sample)
    sub = events[events["sample"].astype(str) == sample]

    if source_col and source_col in sub.columns:
        paths = unique_existing_paths(sub[source_col].dropna().unique())
        if paths:
            return paths[0]

    if bin_root:
        root = Path(bin_root)
        if root.exists():
            patterns = []
            if bin_pattern:
                patterns.append(bin_pattern.replace("{sample}", sample))
            patterns.extend([
                f"{sample}*_bins.bed",
                f"{sample}*_bins.bed.gz",
                f"{sample}*bins*.bed",
                f"{sample}*bins*.bed.gz",
                f"*{sample}*bins*.bed",
                f"*{sample}*bins*.bed.gz",
                f"{sample}*.tsv",
                f"{sample}*.tsv.gz",
                f"*{sample}*.tsv",
                f"*{sample}*.tsv.gz",
            ])
            for pattern in patterns:
                hits = sorted(root.rglob(pattern))
                hits = [h for h in hits if h.is_file()]
                if hits:
                    return hits[0]

    return None


def read_bins_for_sample(sample, events, bins=None, bin_root=None, source_col=None, bin_pattern=None, log2_col=None):
    """Read bins for one sample from --bins, the events source column, or --bin-root."""
    sample = str(sample)

    # Explicit --bins can be a folder, a single sample file, or a multi-sample table.
    if bins is not None:
        bins = Path(bins)
        if bins.exists() and bins.is_dir():
            found = find_sample_bin_file(sample, events, bin_root=bins, source_col=None, bin_pattern=bin_pattern)
            if found is not None:
                try:
                    return read_bins(found, sample_name=sample, log2_col=log2_col)
                except Exception as exc:
                    print(f"WARNING: failed to read bins for {sample} from {found}: {exc}")
        elif bins.exists() and bins.is_file():
            try:
                tmp = read_bins(bins, sample_name=sample, log2_col=log2_col)
                if "sample" in tmp.columns and tmp["sample"].astype(str).nunique() > 1:
                    tmp = tmp[tmp["sample"].astype(str) == sample].copy()
                else:
                    tmp["sample"] = sample
                if not tmp.empty:
                    return tmp
            except Exception as exc:
                print(f"WARNING: failed to read --bins {bins}: {exc}")

    found = find_sample_bin_file(sample, events, bin_root=bin_root, source_col=source_col, bin_pattern=bin_pattern)
    if found is not None:
        try:
            return read_bins(found, sample_name=sample, log2_col=log2_col)
        except Exception as exc:
            print(f"WARNING: failed to read bins for {sample} from {found}: {exc}")

    return pd.DataFrame(columns=["sample", "chrom", "start", "end", "log2", "source_file"])


def pseudo_bins_from_events(sample, events):
    """Fallback profile points when raw bin files are unavailable."""
    sub = events[events["sample"].astype(str) == str(sample)].copy()
    rows = []
    for _, ev in sub.iterrows():
        y = event_profile_y(ev)
        rows.append({
            "sample": str(sample),
            "chrom": ev["chrom"],
            "start": int(ev["start"]),
            "end": int(ev["end"]),
            "log2": y,
            "source_file": "event_level_fallback",
        })
    return pd.DataFrame(rows)


###############################################################################
# Sample order
###############################################################################

def make_sample_order(events, summary, sort_mode):
    all_samples = set(events["sample"].unique())

    if not summary.empty:
        all_samples |= set(summary["sample"].unique())

    if sort_mode == "alphabetical":
        return sorted(all_samples)

    if sort_mode == "summary" and not summary.empty:
        ordered = list(summary["sample"].drop_duplicates())
        rest = sorted(all_samples - set(ordered))
        return ordered + rest

    burden = events.groupby("sample")["size_mb"].sum().to_dict()

    return sorted(all_samples, key=lambda s: (-burden.get(s, 0), s))


###############################################################################
# Genome axis
###############################################################################

def add_chromosome_axis(ax, offsets, chrom_sizes, ordered_chroms, genome_size):
    centers = []

    for i, chrom in enumerate(ordered_chroms):
        start = offsets[chrom]
        end = offsets[chrom] + chrom_sizes[chrom]

        if i % 2 == 0:
            ax.axvspan(start, end, color="#F8FAFC", zorder=0)

        ax.axvline(start, color="#D7DEE8", linewidth=0.7, zorder=1)

        centers.append((start + end) / 2)

    ax.axvline(genome_size, color="#D7DEE8", linewidth=0.7, zorder=1)

    ax.set_xlim(0, genome_size)
    ax.set_xticks(centers)
    ax.set_xticklabels(ordered_chroms)
    ax.set_xlabel("Chromosome")


###############################################################################
# Genome-wide overview plot
###############################################################################

def plot_genome_overview(
    events,
    sample_order,
    chrom_sizes,
    offsets,
    ordered_chroms,
    genome_size,
    outdir,
    dpi=400,
    width=11.5,
    height_per_sample=0.24,
    min_height=4.8,
    max_height=8.8,
):
    colors = state_colors()

    fig_height = max(min_height, min(height_per_sample * len(sample_order) + 1.8, max_height))
    fig, ax = plt.subplots(figsize=(width, fig_height))

    y_positions = {sample: i for i, sample in enumerate(sample_order)}

    # Subtle row guides.
    for i in range(len(sample_order)):
        ax.axhline(i, color="#EEF2F6", linewidth=0.45, zorder=0)

    for _, row in events.iterrows():
        chrom = row["chrom"]
        sample = row["sample"]

        if chrom not in offsets or sample not in y_positions:
            continue

        x0 = offsets[chrom] + int(row["start"]) - 1
        width_bp = int(row["end"]) - int(row["start"]) + 1
        y = y_positions[sample]

        ax.broken_barh(
            [(x0, width_bp)],
            (y - 0.43, 0.86),
            facecolors=colors[row["state"]],
            edgecolors="white",
            linewidth=0.12,
            alpha=0.97,
            zorder=3,
        )

    add_chromosome_axis(ax, offsets, chrom_sizes, ordered_chroms, genome_size)

    ax.set_yticks(range(len(sample_order)))
    ax.set_yticklabels(sample_order)
    ax.set_ylim(-0.6, len(sample_order) - 0.4)
    ax.invert_yaxis()

    ax.set_ylabel("Sample")
    ax.set_title("Genome-wide CNA overview", pad=8)

    handles = [Patch(facecolor=colors[s], edgecolor="none", label=STATE_LABELS[s]) for s in STATE_ORDER]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.12),
        ncol=len(handles),
        frameon=False,
        handlelength=1.6,
        columnspacing=1.2,
    )

    style_axes(ax, hide_top_right=True)

    fig.tight_layout(pad=0.5)
    save_figure(fig, outdir, "cna_genome_overview", dpi=dpi)
    plt.close(fig)


###############################################################################
# New reference-like log2-ratio profile plot
###############################################################################

def event_profile_y(row):
    if pd.notna(row.get("mean_log2", np.nan)):
        return float(row["mean_log2"])

    state = row["state"]
    if state == "amplification":
        return 0.45
    if state == "gain":
        return 0.25
    if state == "loss":
        return -0.35
    if state == "deep_loss":
        return -0.75
    return 0.0


def plot_one_log2_ratio_profile(
    bins,
    events,
    sample,
    chrom_sizes,
    offsets,
    ordered_chroms,
    genome_size,
    outdir,
    dpi=400,
    width=11.4,
    height=3.2,
    y_min=-3.2,
    y_max=3.2,
    stem="cna_log2_ratio_profile",
):
    sub_bins = bins[bins["sample"].astype(str) == str(sample)].copy()
    sub_events = events[events["sample"].astype(str) == str(sample)].copy()

    if sub_bins.empty:
        print(f"WARNING: no bin-level log2-ratio rows found for sample '{sample}'. Skipping profile plot.")
        return False

    sub_bins = sub_bins[sub_bins["chrom"].isin(offsets)].copy()
    sub_bins["x"] = sub_bins.apply(
        lambda r: offsets[r["chrom"]] + int((int(r["start"]) + int(r["end"])) / 2),
        axis=1,
    )

    colors = state_colors()

    fig, ax = plt.subplots(figsize=(width, height))

    # Thin chromosome panels and gridlines.
    centers = []
    for chrom in ordered_chroms:
        c0 = offsets[chrom]
        c1 = offsets[chrom] + chrom_sizes[chrom]
        ax.axvline(c0, color="#D8D8D8", linewidth=0.75, zorder=0)
        centers.append((c0 + c1) / 2)
    ax.axvline(genome_size, color="#D8D8D8", linewidth=0.75, zorder=0)

    ax.scatter(
        sub_bins["x"],
        sub_bins["log2"],
        s=1.6,
        c="black",
        alpha=0.58,
        linewidths=0,
        rasterized=True,
        zorder=2,
    )

    # Overlay high-confidence CNA calls as colored horizontal segments.
    for _, row in sub_events.iterrows():
        chrom = row["chrom"]
        if chrom not in offsets:
            continue
        x0 = offsets[chrom] + int(row["start"]) - 1
        x1 = offsets[chrom] + int(row["end"])
        y = event_profile_y(row)
        ax.hlines(
            y,
            x0,
            x1,
            colors=colors.get(row["state"], "#666666"),
            linewidth=3.4,
            alpha=0.95,
            zorder=4,
        )

    ax.axhline(0, color="black", linewidth=0.85, zorder=3)
    ax.grid(axis="y", color=REFERENCE_GRID_COLOR, linewidth=0.65)
    ax.grid(axis="x", color="#ECECEC", linewidth=0.5)

    ax.set_xlim(0, genome_size)
    ax.set_ylim(y_min, y_max)
    ax.set_xticks(centers)
    ax.set_xticklabels(ordered_chroms)
    ax.set_ylabel(r"Log$_2$ ratio")
    ax.set_xlabel("Chr")
    ax.set_title(str(sample), pad=6)

    # Keep a boxed look, like the reference image.
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#222222")

    ax.tick_params(axis="both", which="major", width=0.8, length=3.0)

    fig.tight_layout(pad=0.35)
    save_figure(fig, outdir, stem, dpi=dpi)
    plt.close(fig)
    return True


def plot_log2_ratio_profiles(
    bins,
    events,
    sample_order,
    chrom_sizes,
    offsets,
    ordered_chroms,
    genome_size,
    outdir,
    profile_sample=None,
    profile_max_samples=20,
    dpi=400,
    width=11.4,
    height=3.2,
    y_min=-3.2,
    y_max=3.2,
):
    if bins is None or bins.empty:
        print("Skipped log2-ratio profile panel: provide --bins with bin-level log2-ratio data.")
        return

    if profile_sample and str(profile_sample).lower() == "all":
        samples = [s for s in sample_order if str(s) in set(bins["sample"].astype(str))]
        if profile_max_samples and profile_max_samples > 0:
            samples = samples[:profile_max_samples]
        if not samples:
            print("WARNING: --profile-sample all requested, but no sample names matched --bins.")
            return

        out_pdf = outdir / "cna_log2_ratio_profiles_all_samples.pdf"
        with PdfPages(out_pdf) as pdf:
            for sample in samples:
                sub_bins = bins[bins["sample"].astype(str) == str(sample)].copy()
                sub_events = events[events["sample"].astype(str) == str(sample)].copy()
                if sub_bins.empty:
                    continue
                sub_bins = sub_bins[sub_bins["chrom"].isin(offsets)].copy()
                sub_bins["x"] = sub_bins.apply(
                    lambda r: offsets[r["chrom"]] + int((int(r["start"]) + int(r["end"])) / 2),
                    axis=1,
                )

                fig, ax = plt.subplots(figsize=(width, height))
                centers = []
                for chrom in ordered_chroms:
                    c0 = offsets[chrom]
                    c1 = offsets[chrom] + chrom_sizes[chrom]
                    ax.axvline(c0, color="#D8D8D8", linewidth=0.75, zorder=0)
                    centers.append((c0 + c1) / 2)
                ax.axvline(genome_size, color="#D8D8D8", linewidth=0.75, zorder=0)
                ax.scatter(
                    sub_bins["x"],
                    sub_bins["log2"],
                    s=1.6,
                    c="black",
                    alpha=0.58,
                    linewidths=0,
                    rasterized=True,
                    zorder=2,
                )
                colors = state_colors()
                for _, row in sub_events.iterrows():
                    chrom = row["chrom"]
                    if chrom not in offsets:
                        continue
                    x0 = offsets[chrom] + int(row["start"]) - 1
                    x1 = offsets[chrom] + int(row["end"])
                    y = event_profile_y(row)
                    ax.hlines(y, x0, x1, colors=colors.get(row["state"], "#666666"), linewidth=3.4, alpha=0.95, zorder=4)
                ax.axhline(0, color="black", linewidth=0.85, zorder=3)
                ax.grid(axis="y", color=REFERENCE_GRID_COLOR, linewidth=0.65)
                ax.grid(axis="x", color="#ECECEC", linewidth=0.5)
                ax.set_xlim(0, genome_size)
                ax.set_ylim(y_min, y_max)
                ax.set_xticks(centers)
                ax.set_xticklabels(ordered_chroms)
                ax.set_ylabel(r"Log$_2$ ratio")
                ax.set_xlabel("Chr")
                ax.set_title(str(sample), pad=6)
                for spine in ax.spines.values():
                    spine.set_visible(True)
                    spine.set_linewidth(1.0)
                    spine.set_color("#222222")
                fig.tight_layout(pad=0.35)
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        print(f"Wrote: {out_pdf}")
        return

    if profile_sample:
        sample = str(profile_sample)
    else:
        matched = [s for s in sample_order if str(s) in set(bins["sample"].astype(str))]
        sample = str(matched[0]) if matched else str(bins["sample"].iloc[0])

    safe_sample = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(sample))
    plot_one_log2_ratio_profile(
        bins=bins,
        events=events,
        sample=sample,
        chrom_sizes=chrom_sizes,
        offsets=offsets,
        ordered_chroms=ordered_chroms,
        genome_size=genome_size,
        outdir=outdir,
        dpi=dpi,
        width=width,
        height=height,
        y_min=y_min,
        y_max=y_max,
        stem=f"cna_log2_ratio_profile_{safe_sample}",
    )


###############################################################################
# Stacked bar plots
###############################################################################

def plot_stacked_bar(events, sample_order, value_col, title, ylabel, outfile_prefix, outdir, dpi=400):
    colors = state_colors()

    pivot = (
        events.pivot_table(
            index="sample",
            columns="state",
            values=value_col,
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(sample_order, fill_value=0)
        .reindex(columns=STATE_ORDER, fill_value=0)
    )

    fig_width = max(8.2, min(0.24 * len(sample_order) + 3.0, 11.2))
    fig, ax = plt.subplots(figsize=(fig_width, 4.4))

    x = np.arange(len(pivot.index))
    bottom = np.zeros(len(pivot.index))

    for state in STATE_ORDER:
        values = pivot[state].to_numpy(dtype=float)

        ax.bar(
            x,
            values,
            bottom=bottom,
            label=STATE_LABELS[state],
            color=colors[state],
            width=0.78,
            edgecolor="white",
            linewidth=0.2,
        )

        bottom += values

    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index, rotation=90)
    ax.set_ylabel(ylabel)
    ax.set_title(title, pad=8)

    ax.legend(
        ncol=len(STATE_ORDER),
        loc="upper center",
        bbox_to_anchor=(0.5, 1.17),
        frameon=False,
        handlelength=1.5,
        columnspacing=1.1,
    )

    ax.grid(axis="y", color="#E5EAF0", linewidth=0.6)
    ax.grid(axis="x", visible=False)

    style_axes(ax, hide_top_right=True)

    fig.tight_layout(pad=0.5)
    save_figure(fig, outdir, outfile_prefix, dpi=dpi)
    plt.close(fig)


###############################################################################
# Recurrent cytoband plot
###############################################################################

def plot_recurrent_cytobands(events, outdir, top_n, dpi=400):
    colors = state_colors()

    x = events.copy()
    x["event_label"] = x["state"].map(STATE_LABELS) + " " + x["cytoband"].astype(str)

    recurrence = (
        x.groupby(["event_label", "state"])
        .agg(
            n_samples=("sample", "nunique"),
            total_mb=("size_mb", "sum"),
            median_log2=("mean_log2", "median"),
        )
        .reset_index()
        .sort_values(["n_samples", "total_mb"], ascending=[False, False])
        .head(top_n)
    )

    if recurrence.empty:
        return

    recurrence = recurrence.iloc[::-1]

    fig_height = max(4.5, min(0.28 * len(recurrence) + 1.4, 10.8))
    fig, ax = plt.subplots(figsize=(8.8, fig_height))

    y = np.arange(len(recurrence))
    bar_colors = [colors.get(s, "#777777") for s in recurrence["state"]]

    ax.barh(
        y,
        recurrence["n_samples"],
        color=bar_colors,
        edgecolor="white",
        linewidth=0.25,
        height=0.72,
    )

    ax.set_yticks(y)
    ax.set_yticklabels(recurrence["event_label"])
    ax.set_xlabel("Number of samples")
    ax.set_title(f"Top {len(recurrence)} recurrent CNA cytobands", pad=8)

    ax.grid(axis="x", color="#E5EAF0", linewidth=0.6)
    ax.grid(axis="y", visible=False)

    style_axes(ax, hide_top_right=True)

    fig.tight_layout(pad=0.5)
    save_figure(fig, outdir, "cna_recurrent_cytobands", dpi=dpi)
    plt.close(fig)


###############################################################################
# New reference-like Panel A/B figure: cohort frequency + recurrent genes
###############################################################################

def cyto_arm_label(cytoband, chrom):
    band = str(cytoband).strip()
    chrom = str(chrom).strip()
    band_nochr = re.sub(r"^chr", "", band, flags=re.I)

    m = re.match(r"^([0-9XYM]+)([pq])", band_nochr, flags=re.I)
    if m:
        return f"{m.group(1)}{m.group(2).lower()}"

    m = re.match(r"^([pq])", band_nochr, flags=re.I)
    if m:
        return f"{chrom}{m.group(1).lower()}"

    return band


def chromosome_frequency_bins(events, chrom, chrom_size, sample_order, bin_mb=5.0):
    n_samples = max(len(sample_order), 1)
    sample_set = set(map(str, sample_order))
    bin_bp = max(int(bin_mb * 1e6), 1)
    starts = np.arange(1, int(chrom_size) + 1, bin_bp, dtype=int)
    ends = np.minimum(starts + bin_bp - 1, int(chrom_size))

    gain_freq = np.zeros(len(starts), dtype=float)
    loss_freq = np.zeros(len(starts), dtype=float)

    sub = events[(events["chrom"] == chrom) & (events["sample"].astype(str).isin(sample_set))]
    if sub.empty:
        x_mid_mb = (starts + ends) / 2e6
        widths_mb = (ends - starts + 1) / 1e6
        return x_mid_mb, widths_mb, gain_freq, loss_freq

    for i, (start, end) in enumerate(zip(starts, ends)):
        hit = sub[(sub["end"] >= start) & (sub["start"] <= end)]
        if hit.empty:
            continue
        gain_samples = hit[hit["state"].isin(GAIN_STATES)]["sample"].astype(str).unique()
        loss_samples = hit[hit["state"].isin(LOSS_STATES)]["sample"].astype(str).unique()
        gain_freq[i] = 100.0 * len(gain_samples) / n_samples
        loss_freq[i] = 100.0 * len(loss_samples) / n_samples

    x_mid_mb = (starts + ends) / 2e6
    widths_mb = (ends - starts + 1) / 1e6
    return x_mid_mb, widths_mb, gain_freq, loss_freq


def top_cytoband_annotations(events, sample_order, top_n=4):
    if top_n <= 0 or events.empty or "cytoband" not in events.columns:
        return pd.DataFrame()

    n_samples = max(len(sample_order), 1)
    sample_set = set(map(str, sample_order))
    x = events[events["sample"].astype(str).isin(sample_set)].copy()
    if x.empty:
        return pd.DataFrame()

    x["class"] = np.where(x["state"].isin(GAIN_STATES), "gain", "loss")
    x["arm_label"] = x.apply(lambda r: cyto_arm_label(r["cytoband"], r["chrom"]), axis=1)
    x["mid"] = (x["start"].astype(float) + x["end"].astype(float)) / 2.0

    ann = (
        x.groupby(["chrom", "arm_label", "class"])
        .agg(
            n_samples=("sample", "nunique"),
            median_mid=("mid", "median"),
            total_mb=("size_mb", "sum"),
        )
        .reset_index()
    )
    ann["frequency"] = 100.0 * ann["n_samples"] / n_samples
    ann = ann.sort_values(["frequency", "total_mb"], ascending=[False, False]).head(top_n)
    return ann


def split_gene_string(value):
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", ".", "na", "n/a"}:
        return []
    parts = re.split(r"[;,|/]", text)
    genes = []
    for p in parts:
        g = p.strip()
        if not g or g.lower() in {"nan", "none", ".", "na", "n/a"}:
            continue
        genes.append(g)
    return genes


def read_gene_bed(path):
    rows = []
    path = Path(path)

    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = re.split(r"\t|,|\s+", line.strip())
            if len(parts) < 4:
                continue
            chrom = norm_chrom(parts[0])
            try:
                start = int(float(parts[1]))
                end = int(float(parts[2]))
            except ValueError:
                # Header line.
                continue
            gene = str(parts[3]).strip()
            if not gene or gene == ".":
                continue
            if chrom not in CHR_ORDER or end <= start:
                continue
            rows.append({"chrom": chrom, "start": start, "end": end, "gene": gene})

    return pd.DataFrame(rows)


def build_gene_events_from_bed(events, gene_bed):
    genes = read_gene_bed(gene_bed)
    if genes.empty:
        print(f"WARNING: no usable genes read from --gene-bed {gene_bed}")
        return pd.DataFrame(columns=["sample", "gene", "state"])

    rows = []
    genes_by_chrom = {chrom: sub.copy() for chrom, sub in genes.groupby("chrom")}

    for _, ev in events.iterrows():
        chrom = ev["chrom"]
        if chrom not in genes_by_chrom:
            continue
        g = genes_by_chrom[chrom]
        hit = g[(g["end"] >= int(ev["start"])) & (g["start"] <= int(ev["end"]))]
        if hit.empty:
            continue
        for gene in hit["gene"].dropna().astype(str).unique():
            rows.append({"sample": ev["sample"], "gene": gene, "state": ev["state"]})

    return pd.DataFrame(rows)


def read_gene_events_table(path):
    df = read_table_flexible(path, dtype=str)

    sample_col = find_column(df, ["sample", "sample_id", "sampleid", "tumor", "tumour"], required=True, label="gene-event sample column")
    gene_col = find_column(df, GENE_COLUMN_CANDIDATES, required=True, label="gene-event gene column")
    state_col = find_column(df, ["state", "alteration", "event", "type", "cna", "call"], required=True, label="gene-event state column")

    out = pd.DataFrame({
        "sample": df[sample_col].astype(str),
        "gene": df[gene_col].astype(str),
        "state": df[state_col].map(normalize_state),
    })
    out = out.dropna(subset=["sample", "gene", "state"])
    out = out[out["state"].isin(STATE_ORDER)]
    return out


def build_gene_events(events, gene_events_path=None, gene_col=None, gene_bed=None):
    if gene_events_path is not None:
        gene_events_path = Path(gene_events_path)
        if gene_events_path.exists():
            return read_gene_events_table(gene_events_path)
        raise SystemExit(f"--gene-events not found: {gene_events_path}")

    if gene_col is not None:
        if gene_col not in events.columns:
            raise SystemExit(f"--gene-column '{gene_col}' was not found in events file.")
        chosen_col = gene_col
    else:
        chosen_col = find_column(events, GENE_COLUMN_CANDIDATES, required=False)

    if chosen_col is not None:
        rows = []
        for _, row in events.iterrows():
            for gene in split_gene_string(row[chosen_col]):
                rows.append({"sample": row["sample"], "gene": gene, "state": row["state"]})
        if rows:
            return pd.DataFrame(rows)

    if gene_bed is not None:
        return build_gene_events_from_bed(events, gene_bed)

    return pd.DataFrame(columns=["sample", "gene", "state"])


def load_gene_list(value):
    if value is None:
        return None

    p = Path(value)
    if p.exists():
        genes = []
        with p.open("r") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Accept one gene per line or first column in TSV/CSV.
                genes.append(re.split(r"\t|,|\s+", line)[0])
        return genes

    return [g.strip() for g in re.split(r"[,;|]", value) if g.strip()]


def compute_gene_recurrence(gene_events, sample_order, top_genes=10, gene_list=None):
    n_samples = max(len(sample_order), 1)
    sample_set = set(map(str, sample_order))
    x = gene_events.copy()
    if x.empty:
        return pd.DataFrame(columns=["gene", "gain_count", "loss_count", "gain_pct", "loss_pct", "total_count", "total_pct"])

    x["sample"] = x["sample"].astype(str)
    x = x[x["sample"].isin(sample_set)]
    if x.empty:
        return pd.DataFrame(columns=["gene", "gain_count", "loss_count", "gain_pct", "loss_pct", "total_count", "total_pct"])

    x["gene"] = x["gene"].astype(str).str.strip()
    x = x[x["gene"] != ""]

    gain = (
        x[x["state"].isin(GAIN_STATES)]
        .groupby("gene")["sample"]
        .nunique()
        .rename("gain_count")
    )
    loss = (
        x[x["state"].isin(LOSS_STATES)]
        .groupby("gene")["sample"]
        .nunique()
        .rename("loss_count")
    )

    rec = pd.concat([gain, loss], axis=1).fillna(0).astype(int).reset_index()
    if rec.empty:
        return pd.DataFrame(columns=["gene", "gain_count", "loss_count", "gain_pct", "loss_pct", "total_count", "total_pct"])

    rec["total_count"] = rec["gain_count"] + rec["loss_count"]
    rec["gain_pct"] = 100.0 * rec["gain_count"] / n_samples
    rec["loss_pct"] = 100.0 * rec["loss_count"] / n_samples
    rec["total_pct"] = 100.0 * rec["total_count"] / n_samples

    if gene_list:
        gene_list = list(dict.fromkeys([str(g).strip() for g in gene_list if str(g).strip()]))
        base = pd.DataFrame({"gene": gene_list})
        rec = base.merge(rec, on="gene", how="left").fillna({
            "gain_count": 0,
            "loss_count": 0,
            "total_count": 0,
            "gain_pct": 0.0,
            "loss_pct": 0.0,
            "total_pct": 0.0,
        })
        rec[["gain_count", "loss_count", "total_count"]] = rec[["gain_count", "loss_count", "total_count"]].astype(int)
        return rec.head(top_genes)

    rec = rec.sort_values(["total_count", "total_pct", "gene"], ascending=[False, False, True]).head(top_genes)
    return rec


def fmt_pct(value):
    value = float(value)
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))}%"
    return f"{value:.1f}%"


def nice_count_limit(values, floor=5):
    maxv = float(np.nanmax(values)) if len(values) else 0.0
    if maxv <= 0:
        return floor
    step = 5 if maxv <= 50 else 10
    return max(floor, int(math.ceil((maxv + step * 0.8) / step) * step))


def plot_frequency_gene_panels(
    events,
    gene_events,
    sample_order,
    chrom_sizes,
    ordered_chroms,
    outdir,
    top_genes=10,
    gene_list=None,
    frequency_bin_mb=5.0,
    frequency_ylim=50.0,
    annotate_cytobands=4,
    gene_panel_units="count",
    dpi=400,
    width=12.0,
    height=7.2,
):
    sample_order = list(sample_order)
    n_samples = max(len(sample_order), 1)
    chroms = [c for c in ordered_chroms if c in chrom_sizes]

    # Use only chromosomes with cytoband sizes and events-compatible order.
    fig = plt.figure(figsize=(width, height))
    outer = GridSpec(
        2,
        1,
        height_ratios=[2.28, 1.38],
        hspace=0.36,
        figure=fig,
    )

    # ----------------------------- Panel A ---------------------------------
    top_gs = outer[0].subgridspec(
        1,
        len(chroms),
        width_ratios=[max(chrom_sizes[c], 1) for c in chroms],
        wspace=0.055,
    )

    axes = []
    freq_ylim = float(frequency_ylim)

    for i, chrom in enumerate(chroms):
        ax = fig.add_subplot(top_gs[0, i], sharey=axes[0] if axes else None)
        axes.append(ax)

        x_mid_mb, widths_mb, gain_freq, loss_freq = chromosome_frequency_bins(
            events=events,
            chrom=chrom,
            chrom_size=chrom_sizes[chrom],
            sample_order=sample_order,
            bin_mb=frequency_bin_mb,
        )

        ax.bar(
            x_mid_mb,
            gain_freq,
            width=widths_mb * 0.96,
            color=REFERENCE_GAIN_COLOR,
            edgecolor=REFERENCE_GAIN_COLOR,
            linewidth=0,
            align="center",
            zorder=3,
        )
        ax.bar(
            x_mid_mb,
            -loss_freq,
            width=widths_mb * 0.96,
            color=REFERENCE_LOSS_COLOR,
            edgecolor=REFERENCE_LOSS_COLOR,
            linewidth=0,
            align="center",
            zorder=3,
        )

        ax.axhline(0, color="#9A9A9A", linewidth=0.7, zorder=2)
        ax.axhline(25, color=REFERENCE_DASH_COLOR, linestyle=(0, (2, 2)), linewidth=0.85, zorder=1)
        ax.axhline(-25, color=REFERENCE_DASH_COLOR, linestyle=(0, (2, 2)), linewidth=0.85, zorder=1)
        ax.grid(axis="y", color=REFERENCE_GRID_COLOR, linewidth=0.45, zorder=0)

        ax.set_xlim(0, chrom_sizes[chrom] / 1e6)
        ax.set_ylim(-freq_ylim, freq_ylim)
        ax.set_xticks([])

        # Beige chromosome header.
        ax.add_patch(
            Rectangle(
                (0, 1.006),
                1,
                0.085,
                transform=ax.transAxes,
                facecolor=REFERENCE_HEADER_COLOR,
                edgecolor="#6E6E6E",
                linewidth=0.75,
                clip_on=False,
                zorder=5,
            )
        )
        ax.text(
            0.5,
            1.048,
            str(chrom),
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=8.5,
            color="black",
            zorder=6,
        )

        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("#6F6F6F")
            spine.set_linewidth(0.75)

        if i == 0:
            ax.set_ylabel("Frequency (%)")
            ax.set_yticks([-50, -25, 0, 25, 50])
            ax.set_yticklabels(["50", "25", "0", "25", "50"])
        else:
            ax.tick_params(axis="y", which="both", left=False, labelleft=False)

    annotations = top_cytoband_annotations(events, sample_order, top_n=annotate_cytobands)
    if not annotations.empty:
        chrom_to_ax = {chrom: ax for chrom, ax in zip(chroms, axes)}
        for _, row in annotations.iterrows():
            chrom = row["chrom"]
            if chrom not in chrom_to_ax:
                continue
            ax = chrom_to_ax[chrom]
            x = float(row["median_mid"]) / 1e6
            freq = min(float(row["frequency"]), freq_ylim * 0.82)
            if row["class"] == "gain":
                y = max(freq, 4)
                ytext = min(freq_ylim * 0.88, y + freq_ylim * 0.13)
                va = "bottom"
            else:
                y = -max(freq, 4)
                ytext = max(-freq_ylim * 0.88, y - freq_ylim * 0.13)
                va = "top"
            ax.annotate(
                str(row["arm_label"]),
                xy=(x, y),
                xytext=(x, ytext),
                textcoords="data",
                ha="center",
                va=va,
                fontsize=8,
                color="black",
                arrowprops=dict(arrowstyle="-", color="#333333", linewidth=0.75, shrinkA=0, shrinkB=0),
                zorder=7,
            )

    # Panel A label and legend.
    fig.text(0.012, 0.967, "A", fontsize=12, fontweight="bold", ha="left", va="top")
    fig.legend(
        handles=[
            Patch(facecolor=REFERENCE_GAIN_COLOR, edgecolor=REFERENCE_GAIN_COLOR, label="Gain"),
            Patch(facecolor=REFERENCE_LOSS_COLOR, edgecolor=REFERENCE_LOSS_COLOR, label="Loss"),
        ],
        loc="upper right",
        bbox_to_anchor=(0.985, 0.982),
        ncol=2,
        frameon=False,
        handlelength=1.0,
        handletextpad=0.35,
        columnspacing=0.8,
    )

    # ----------------------------- Panel B ---------------------------------
    axb = fig.add_subplot(outer[1])
    gene_list_loaded = load_gene_list(gene_list)
    rec = compute_gene_recurrence(
        gene_events=gene_events,
        sample_order=sample_order,
        top_genes=top_genes,
        gene_list=gene_list_loaded,
    )

    if rec.empty:
        axb.text(
            0.5,
            0.5,
            "No gene-level CNA data found.\nUse --gene-events, --gene-bed, or a gene column in --events.",
            transform=axb.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="#666666",
        )
        axb.set_axis_off()
    else:
        x = np.arange(len(rec))
        if gene_panel_units == "percent":
            gain_values = rec["gain_pct"].to_numpy(dtype=float)
            loss_values = rec["loss_pct"].to_numpy(dtype=float)
            ylabel = "Patients with alterations (%)"
        else:
            gain_values = rec["gain_count"].to_numpy(dtype=float)
            loss_values = rec["loss_count"].to_numpy(dtype=float)
            ylabel = "Number of patients with alterations"

        axb.bar(
            x,
            gain_values,
            color=REFERENCE_GAIN_COLOR,
            edgecolor="white",
            linewidth=0.35,
            width=0.92,
            label="Gain",
            zorder=3,
        )
        axb.bar(
            x,
            -loss_values,
            color=REFERENCE_LOSS_COLOR,
            edgecolor="white",
            linewidth=0.35,
            width=0.92,
            label="Loss",
            zorder=3,
        )

        # Percentage labels, exactly like the reference paper-style panel.
        y_top = nice_count_limit(gain_values, floor=5 if gene_panel_units == "count" else 10)
        y_bottom = nice_count_limit(loss_values, floor=5 if gene_panel_units == "count" else 10)
        axb.set_ylim(-y_bottom, y_top)

        gain_offset = max(y_top * 0.045, 0.8)
        loss_offset = max(y_bottom * 0.045, 0.8)
        for i, row in rec.reset_index(drop=True).iterrows():
            if gain_values[i] > 0:
                axb.text(
                    i,
                    gain_values[i] + gain_offset,
                    fmt_pct(row["gain_pct"]),
                    ha="center",
                    va="bottom",
                    fontsize=8.5,
                    color="black",
                )
            else:
                axb.text(
                    i,
                    max(gain_offset * 0.8, y_top * 0.04),
                    "0%",
                    ha="center",
                    va="bottom",
                    fontsize=8.5,
                    color="black",
                )

            if loss_values[i] > 0:
                axb.text(
                    i,
                    -loss_values[i] - loss_offset,
                    fmt_pct(row["loss_pct"]),
                    ha="center",
                    va="top",
                    fontsize=8.5,
                    color="black",
                )
            else:
                axb.text(
                    i,
                    -max(loss_offset * 0.8, y_bottom * 0.04),
                    "0%",
                    ha="center",
                    va="top",
                    fontsize=8.5,
                    color="black",
                )

        axb.axhline(0, color="#9A9A9A", linewidth=0.8, zorder=2)
        axb.grid(axis="y", color=REFERENCE_GRID_COLOR, linewidth=0.55, zorder=0)
        axb.set_xlim(-0.6, len(rec) - 0.4)
        axb.set_xticks(x)
        axb.set_xticklabels(rec["gene"], rotation=0, ha="center")
        axb.set_ylabel(ylabel)
        axb.yaxis.set_major_formatter(
            FuncFormatter(lambda y, pos: str(abs(int(round(y)))) if abs(y - round(y)) < 1e-6 else f"{abs(y):g}")
        )

        for spine in axb.spines.values():
            spine.set_visible(True)
            spine.set_color("#6F6F6F")
            spine.set_linewidth(0.75)

        axb.legend(
            loc="upper right",
            bbox_to_anchor=(1.0, 1.23),
            ncol=2,
            frameon=False,
            handlelength=1.0,
            handletextpad=0.35,
            columnspacing=0.8,
        )

    fig.text(0.012, 0.41, "B", fontsize=12, fontweight="bold", ha="left", va="top")

    save_figure(fig, outdir, "cna_frequency_gene_panels", dpi=dpi)
    plt.close(fig)

    if not rec.empty:
        rec.to_csv(outdir / "plot_table_recurrent_genes.tsv", sep="\t", index=False)


###############################################################################
# Per-sample CNA pages
###############################################################################

def plot_per_sample_pages(
    events,
    sample_order,
    chrom_sizes,
    outdir,
    dpi=400,
    width=7.2,
    height=5.1,
):
    colors = state_colors()
    out_pdf = outdir / "cna_per_sample_pages.pdf"

    chroms = [c for c in [str(i) for i in range(1, 23)] + ["X", "Y"] if c in chrom_sizes]
    chrom_y = {chrom: i for i, chrom in enumerate(chroms)}
    max_len_mb = max(chrom_sizes.values()) / 1e6

    with PdfPages(out_pdf) as pdf:
        for sample in sample_order:
            sub = events[events["sample"] == sample].copy()

            fig, ax = plt.subplots(figsize=(width, height))

            # Chromosome backbones.
            for chrom in chroms:
                y = chrom_y[chrom]

                ax.broken_barh(
                    [(0, chrom_sizes[chrom] / 1e6)],
                    (y - 0.23, 0.46),
                    facecolors="#F3F5F7",
                    edgecolors="#AEB7C2",
                    linewidth=0.85,
                    zorder=1,
                )

            # CNA event bars.
            for _, row in sub.iterrows():
                chrom = row["chrom"]

                if chrom not in chrom_y:
                    continue

                y = chrom_y[chrom]
                x0 = (int(row["start"]) - 1) / 1e6
                width_mb = (int(row["end"]) - int(row["start"]) + 1) / 1e6

                ax.broken_barh(
                    [(x0, width_mb)],
                    (y - 0.35, 0.70),
                    facecolors=colors[row["state"]],
                    edgecolors="white",
                    linewidth=0.25,
                    alpha=0.98,
                    zorder=3,
                )

            if sub.empty:
                ax.text(
                    0.5,
                    0.5,
                    "No high-confidence CNA\nby current thresholds",
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="#6B7280",
                )

            ax.set_yticks(range(len(chroms)))
            ax.set_yticklabels(chroms)
            ax.set_xlim(0, max_len_mb)
            ax.set_xlabel("Position within chromosome (Mb)")
            ax.set_ylabel("Chromosome")
            ax.set_title(f"{sample}: CNA intervals", pad=8)

            # Chromosome 1 at top, Y at bottom.
            ax.invert_yaxis()

            ax.grid(axis="x", color="#E5EAF0", linewidth=0.6)
            ax.grid(axis="y", visible=False)

            handles = [Patch(facecolor=colors[s], edgecolor="none", label=STATE_LABELS[s]) for s in STATE_ORDER]
            ax.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 1.11),
                ncol=len(handles),
                frameon=False,
                handlelength=1.5,
                columnspacing=1.0,
            )

            style_axes(ax, hide_top_right=True)

            fig.tight_layout(pad=0.5)
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    print(f"Wrote: {out_pdf}")



###############################################################################
# Per-sample top affected genes + log2 profile PDFs
###############################################################################

def parse_gtf_attributes(attr_text):
    attrs = {}
    for part in str(attr_text).strip().split(";"):
        part = part.strip()
        if not part:
            continue
        if " " in part:
            key, value = part.split(" ", 1)
            attrs[key] = value.strip().strip('"')
        elif "=" in part:
            key, value = part.split("=", 1)
            attrs[key] = value.strip().strip('"')
    return attrs


def read_gene_gtf(path, feature="gene"):
    rows = []
    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            if feature and parts[2] != feature:
                continue
            chrom = norm_chrom(parts[0])
            if chrom not in CHR_ORDER:
                continue
            attrs = parse_gtf_attributes(parts[8])
            gene = attrs.get("gene_name") or attrs.get("Name") or attrs.get("gene") or attrs.get("gene_id")
            if not gene:
                continue
            rows.append({
                "chrom": chrom,
                "start": int(parts[3]),
                "end": int(parts[4]),
                "gene": str(gene),
            })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(["chrom", "start", "end", "gene"])
    return out


def auto_gene_annotation_candidates(cytoband_path=None):
    candidates = []
    if cytoband_path:
        root = Path(cytoband_path).parent
        candidates.extend([
            root / "hg38_genes.bed",
            root / "hg38.refseq.genes.bed",
            root / "gencode.hg38.genes.bed",
            root / "genes.bed",
            root / "gencode.v46.annotation.gtf.gz",
            root / "gencode.v47.annotation.gtf.gz",
            root / "gencode.v48.annotation.gtf.gz",
            root / "gencode.v49.annotation.gtf.gz",
            root / "gencode.annotation.gtf.gz",
        ])
    candidates.extend([
        Path("cna_codification/resources/hg38_genes.bed"),
        Path("cna_codification/resources/genes.bed"),
        Path("cna_codification/resources/gencode.hg38.genes.bed"),
    ])
    return candidates


def load_gene_annotation(gene_bed=None, gene_gtf=None, cytoband_path=None):
    if gene_bed:
        gene_bed = Path(gene_bed)
        if not gene_bed.exists():
            raise SystemExit(f"--gene-bed not found: {gene_bed}")
        genes = read_gene_bed(gene_bed)
        return genes, str(gene_bed)

    if gene_gtf:
        gene_gtf = Path(gene_gtf)
        if not gene_gtf.exists():
            raise SystemExit(f"--gene-gtf/--gtf not found: {gene_gtf}")
        genes = read_gene_gtf(gene_gtf)
        return genes, str(gene_gtf)

    for candidate in auto_gene_annotation_candidates(cytoband_path):
        if candidate.exists():
            if str(candidate).endswith(('.gtf', '.gtf.gz')):
                genes = read_gene_gtf(candidate)
            else:
                genes = read_gene_bed(candidate)
            if not genes.empty:
                print(f"Auto-detected gene annotation: {candidate}")
                return genes, str(candidate)

    return pd.DataFrame(columns=["chrom", "start", "end", "gene"]), None


def build_gene_hits_from_annotation(events, genes):
    if genes is None or genes.empty:
        return pd.DataFrame(columns=[
            "sample", "gene", "state", "chrom", "gene_start", "gene_end", "event_start", "event_end",
            "overlap_bp", "gene_length_bp", "coverage", "event_mean_log2", "event_size_mb", "cytoband",
        ])

    genes = genes.copy()
    genes["chrom"] = genes["chrom"].map(norm_chrom)
    genes["start"] = pd.to_numeric(genes["start"], errors="coerce")
    genes["end"] = pd.to_numeric(genes["end"], errors="coerce")
    genes = genes.dropna(subset=["chrom", "start", "end", "gene"])
    genes = genes[genes["chrom"].isin(CHR_ORDER)]
    genes["start"] = genes["start"].astype(int)
    genes["end"] = genes["end"].astype(int)
    genes["gene_length_bp"] = np.maximum(1, genes["end"] - genes["start"] + 1)

    genes_by_chrom = {chrom: sub.copy() for chrom, sub in genes.groupby("chrom")}
    rows = []
    for _, ev in events.iterrows():
        chrom = ev["chrom"]
        if chrom not in genes_by_chrom:
            continue
        g = genes_by_chrom[chrom]
        hit = g[(g["start"] <= int(ev["end"])) & (g["end"] >= int(ev["start"]))].copy()
        if hit.empty:
            continue
        ov_start = np.maximum(hit["start"].to_numpy(dtype=int), int(ev["start"]))
        ov_end = np.minimum(hit["end"].to_numpy(dtype=int), int(ev["end"]))
        overlap_bp = np.maximum(0, ov_end - ov_start + 1)
        for idx, (_, gene_row) in enumerate(hit.iterrows()):
            if overlap_bp[idx] <= 0:
                continue
            glen = int(gene_row["gene_length_bp"])
            rows.append({
                "sample": ev["sample"],
                "gene": str(gene_row["gene"]),
                "state": ev["state"],
                "chrom": chrom,
                "gene_start": int(gene_row["start"]),
                "gene_end": int(gene_row["end"]),
                "event_start": int(ev["start"]),
                "event_end": int(ev["end"]),
                "overlap_bp": int(overlap_bp[idx]),
                "gene_length_bp": glen,
                "coverage": float(min(1.0, overlap_bp[idx] / max(1, glen))),
                "event_mean_log2": float(event_profile_y(ev)),
                "event_size_mb": float(ev.get("size_mb", np.nan)),
                "cytoband": str(ev.get("cytoband", chrom)),
            })
    return pd.DataFrame(rows)


def build_gene_hits_from_event_gene_column(events, gene_col=None):
    chosen_col = None
    if gene_col:
        if gene_col not in events.columns:
            lookup = {normalized_name(c): c for c in events.columns}
            chosen_col = lookup.get(normalized_name(gene_col))
            if chosen_col is None:
                raise SystemExit(f"--gene-column '{gene_col}' was not found in events file.")
        else:
            chosen_col = gene_col
    else:
        chosen_col = find_column(events, GENE_COLUMN_CANDIDATES, required=False)

    if chosen_col is None:
        return pd.DataFrame()

    rows = []
    for _, ev in events.iterrows():
        for gene in split_gene_string(ev.get(chosen_col, "")):
            rows.append({
                "sample": ev["sample"],
                "gene": gene,
                "state": ev["state"],
                "chrom": ev["chrom"],
                "gene_start": np.nan,
                "gene_end": np.nan,
                "event_start": int(ev["start"]),
                "event_end": int(ev["end"]),
                "overlap_bp": np.nan,
                "gene_length_bp": np.nan,
                "coverage": np.nan,
                "event_mean_log2": float(event_profile_y(ev)),
                "event_size_mb": float(ev.get("size_mb", np.nan)),
                "cytoband": str(ev.get("cytoband", ev["chrom"])),
            })
    return pd.DataFrame(rows)


def build_gene_hits_from_gene_events(path):
    ge = read_gene_events_table(path)
    if ge.empty:
        return pd.DataFrame()
    ge = ge.copy()
    ge["chrom"] = np.nan
    ge["gene_start"] = np.nan
    ge["gene_end"] = np.nan
    ge["event_start"] = np.nan
    ge["event_end"] = np.nan
    ge["overlap_bp"] = np.nan
    ge["gene_length_bp"] = np.nan
    ge["coverage"] = np.nan
    ge["event_mean_log2"] = ge["state"].map({"amplification": 0.75, "gain": 0.35, "loss": -0.35, "deep_loss": -0.75}).fillna(0.0)
    ge["event_size_mb"] = np.nan
    ge["cytoband"] = ""
    return ge


def build_gene_hits(events, gene_events_path=None, gene_col=None, gene_bed=None, gene_gtf=None, cytoband_path=None):
    if gene_events_path is not None:
        gene_events_path = Path(gene_events_path)
        if not gene_events_path.exists():
            raise SystemExit(f"--gene-events not found: {gene_events_path}")
        return build_gene_hits_from_gene_events(gene_events_path)

    hits = build_gene_hits_from_event_gene_column(events, gene_col=gene_col)
    if hits is not None and not hits.empty:
        return hits

    genes, annotation_source = load_gene_annotation(gene_bed=gene_bed, gene_gtf=gene_gtf, cytoband_path=cytoband_path)
    if genes.empty:
        print("WARNING: no gene annotation found. Per-sample PDFs will be created, but the top-gene panel will be empty. Provide --gene-bed or --gene-gtf/--gtf for gene ranking.")
        return pd.DataFrame(columns=["sample", "gene", "state"])

    hits = build_gene_hits_from_annotation(events, genes)
    if not hits.empty:
        hits["gene_annotation_source"] = annotation_source
    return hits


def summarize_top_genes_per_sample(gene_hits, top_n=10):
    columns = [
        "sample", "gene", "state", "chrom", "gene_start", "gene_end", "cytoband",
        "n_events", "total_overlap_bp", "max_coverage", "mean_log2_weighted", "max_abs_log2", "score",
    ]
    if gene_hits is None or gene_hits.empty:
        return pd.DataFrame(columns=columns)

    gh = gene_hits.copy()
    gh = gh.dropna(subset=["sample", "gene", "state"])
    gh["gene"] = gh["gene"].astype(str).str.strip()
    gh = gh[gh["gene"] != ""]
    if gh.empty:
        return pd.DataFrame(columns=columns)

    gh["event_mean_log2"] = pd.to_numeric(gh.get("event_mean_log2", np.nan), errors="coerce")
    gh["event_mean_log2"] = gh["event_mean_log2"].fillna(gh["state"].map({"amplification": 0.75, "gain": 0.35, "loss": -0.35, "deep_loss": -0.75}))
    gh["abs_log2"] = gh["event_mean_log2"].abs().fillna(0)
    gh["overlap_bp"] = pd.to_numeric(gh.get("overlap_bp", np.nan), errors="coerce").fillna(1)
    gh["coverage"] = pd.to_numeric(gh.get("coverage", np.nan), errors="coerce").fillna(0)
    gh["event_size_mb"] = pd.to_numeric(gh.get("event_size_mb", np.nan), errors="coerce").fillna(0)
    state_weight = {"amplification": 4.0, "deep_loss": 4.0, "gain": 2.0, "loss": 2.0}
    gh["state_weight"] = gh["state"].map(state_weight).fillna(1.0)
    gh["row_score"] = gh["state_weight"] * 2.0 + gh["abs_log2"] * 8.0 + gh["coverage"].clip(0, 1) * 2.0 + np.log1p(gh["event_size_mb"].clip(lower=0))

    rows = []
    for (sample, gene), sub in gh.groupby(["sample", "gene"], sort=False):
        # Pick the state/position of the highest-scoring event for display.
        best = sub.sort_values(["row_score", "abs_log2", "coverage"], ascending=[False, False, False]).iloc[0]
        weights = sub["overlap_bp"].replace(0, 1).astype(float)
        mean_log2_weighted = float(np.average(sub["event_mean_log2"].astype(float), weights=weights))
        rows.append({
            "sample": sample,
            "gene": gene,
            "state": best["state"],
            "chrom": best.get("chrom", ""),
            "gene_start": best.get("gene_start", np.nan),
            "gene_end": best.get("gene_end", np.nan),
            "cytoband": ";".join(sorted(set(map(str, sub.get("cytoband", pd.Series(dtype=str)).dropna()))))[:250],
            "n_events": int(len(sub)),
            "total_overlap_bp": float(sub["overlap_bp"].sum()),
            "max_coverage": float(sub["coverage"].max()),
            "mean_log2_weighted": mean_log2_weighted,
            "max_abs_log2": float(sub["abs_log2"].max()),
            "score": float(sub["row_score"].max() + min(5.0, len(sub) * 0.1)),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=columns)

    out = out.sort_values(["sample", "score", "max_abs_log2", "n_events"], ascending=[True, False, False, False])
    out = out.groupby("sample", group_keys=False).head(top_n).reset_index(drop=True)
    return out


def write_per_sample_top_gene_tables(top_gene_df, outdir):
    if top_gene_df is None:
        top_gene_df = pd.DataFrame()
    combined = outdir / "per_sample_top_genes.tsv"
    top_gene_df.to_csv(combined, sep="\t", index=False)
    for sample, sub in top_gene_df.groupby("sample") if not top_gene_df.empty else []:
        safe = sanitize_filename(sample)
        sub.to_csv(outdir / f"{safe}_top_genes.tsv", sep="\t", index=False)
    print(f"Wrote: {combined}")


def assign_bin_event_colors(sub_bins, sub_events):
    """Return a color vector for bins: black baseline, gains/losses highlighted."""
    colors = np.array(["black"] * len(sub_bins), dtype=object)
    if sub_bins.empty or sub_events.empty:
        return colors
    # Loss-like bins in orange and gain-like bins in blue to mimic the reference log2 plot.
    event_colors = {
        "deep_loss": "#E68613",
        "loss": "#E68613",
        "gain": "#3B6FB6",
        "amplification": "#3B6FB6",
    }
    for _, ev in sub_events.iterrows():
        chrom = ev["chrom"]
        mask = (
            (sub_bins["chrom"].astype(str) == str(chrom)) &
            (sub_bins["end"].astype(int) >= int(ev["start"])) &
            (sub_bins["start"].astype(int) <= int(ev["end"]))
        )
        colors[mask.to_numpy()] = event_colors.get(ev["state"], "black")
    return colors


def plot_gene_bar_panel(ax, top_genes, top_n=10):
    colors = {
        "deep_loss": "#E68613",
        "loss": "#E68613",
        "gain": "#3B6FB6",
        "amplification": "#3B6FB6",
    }
    if top_genes is None or top_genes.empty:
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            "No gene-level annotation available\n(use --gene-bed or --gene-gtf/--gtf)",
            ha="center",
            va="center",
            fontsize=9,
            color="#555555",
            transform=ax.transAxes,
        )
        return

    data = top_genes.copy().head(top_n)
    data = data.iloc[::-1]
    y = np.arange(len(data))
    values = pd.to_numeric(data["mean_log2_weighted"], errors="coerce").fillna(0.0).to_numpy()
    bar_colors = [colors.get(s, "#777777") for s in data["state"]]
    labels = []
    for _, r in data.iterrows():
        extra = str(r.get("cytoband", ""))
        if extra and extra != "nan":
            labels.append(f"{r['gene']} ({extra.split(';')[0]})")
        else:
            labels.append(str(r["gene"]))

    ax.barh(y, values, color=bar_colors, edgecolor="white", linewidth=0.4, height=0.72)
    ax.axvline(0, color="#444444", linewidth=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel(r"CNA log$_2$ ratio")
    ax.set_title(f"Top {min(top_n, len(data))} affected genes", pad=5)
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.6)
    ax.grid(axis="y", visible=False)
    lim = max(0.6, float(np.nanmax(np.abs(values))) * 1.25 if len(values) else 0.6)
    ax.set_xlim(-lim, lim)
    style_axes(ax, hide_top_right=True)


def plot_one_sample_log2_gene_pdf(
    sample,
    bins,
    events,
    top_genes,
    chrom_sizes,
    offsets,
    ordered_chroms,
    genome_size,
    outdir,
    top_n=10,
    dpi=400,
    width=11.5,
    height=6.0,
    y_min=-3.2,
    y_max=3.2,
    write_png=False,
):
    sample = str(sample)
    safe = sanitize_filename(sample)
    sub_events = events[events["sample"].astype(str) == sample].copy()
    sub_bins = bins.copy() if bins is not None else pd.DataFrame()
    if not sub_bins.empty:
        sub_bins = sub_bins[sub_bins["chrom"].isin(offsets)].copy()
        sub_bins["x"] = sub_bins.apply(lambda r: offsets[r["chrom"]] + int((int(r["start"]) + int(r["end"])) / 2), axis=1)
        sub_bins = sub_bins.sort_values(["chrom", "start"])

    fig = plt.figure(figsize=(width, height))
    gs = GridSpec(2, 1, height_ratios=[2.1, 1.3], hspace=0.38, figure=fig)
    ax = fig.add_subplot(gs[0, 0])
    axg = fig.add_subplot(gs[1, 0])

    centers = []
    for chrom in ordered_chroms:
        c0 = offsets[chrom]
        c1 = offsets[chrom] + chrom_sizes[chrom]
        ax.axvline(c0, color="#D8D8D8", linewidth=0.75, zorder=0)
        centers.append((c0 + c1) / 2)
    ax.axvline(genome_size, color="#D8D8D8", linewidth=0.75, zorder=0)

    if not sub_bins.empty:
        bin_colors = assign_bin_event_colors(sub_bins, sub_events)
        # Draw baseline black first, then CNA-colored points on top.
        base_mask = bin_colors == "black"
        ax.scatter(sub_bins.loc[base_mask, "x"], sub_bins.loc[base_mask, "log2"], s=1.5, c="black", alpha=0.55, linewidths=0, rasterized=True, zorder=2)
        for col in ["#E68613", "#3B6FB6"]:
            mask = bin_colors == col
            if np.any(mask):
                ax.scatter(sub_bins.loc[mask, "x"], sub_bins.loc[mask, "log2"], s=2.0, c=col, alpha=0.82, linewidths=0, rasterized=True, zorder=3)
    else:
        ax.text(0.5, 0.50, "No raw bin file found; showing event-level segments only", ha="center", va="center", transform=ax.transAxes, fontsize=9, color="#555555")

    # CNA event calls as colored horizontal segment means.
    seg_colors = {"deep_loss": "#E68613", "loss": "#E68613", "gain": "#3B6FB6", "amplification": "#3B6FB6"}
    for _, ev in sub_events.iterrows():
        chrom = ev["chrom"]
        if chrom not in offsets:
            continue
        x0 = offsets[chrom] + int(ev["start"]) - 1
        x1 = offsets[chrom] + int(ev["end"])
        y = event_profile_y(ev)
        ax.hlines(y, x0, x1, colors=seg_colors.get(ev["state"], "#666666"), linewidth=3.2, alpha=0.95, zorder=4)

    ax.axhline(0, color="black", linewidth=0.85, zorder=3)
    ax.grid(axis="y", color=REFERENCE_GRID_COLOR, linewidth=0.65)
    ax.grid(axis="x", color="#ECECEC", linewidth=0.5)
    ax.set_xlim(0, genome_size)
    ax.set_ylim(y_min, y_max)
    ax.set_xticks(centers)
    ax.set_xticklabels(ordered_chroms)
    ax.set_ylabel(r"Log$_2$ ratio")
    ax.set_xlabel("Chr")
    n_events = len(sub_events)
    n_bins = len(sub_bins)
    source = ""
    if not sub_bins.empty and "source_file" in sub_bins.columns:
        srcs = [str(x) for x in sub_bins["source_file"].dropna().unique()]
        source = Path(srcs[0]).name if srcs else ""
    title = f"{sample}: CNA log2-ratio profile"
    if source:
        title += f"  |  bins: {source}"
    title += f"  |  events: {n_events:,}, bins: {n_bins:,}"
    ax.set_title(title, pad=6)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#222222")
    ax.tick_params(axis="both", which="major", width=0.8, length=3.0)

    plot_gene_bar_panel(axg, top_genes, top_n=top_n)

    pdf_path = outdir / f"{safe}_log2_cna_top{top_n}_genes.pdf"
    fig.savefig(pdf_path, bbox_inches="tight")
    if write_png:
        fig.savefig(outdir / f"{safe}_log2_cna_top{top_n}_genes.png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return pdf_path


def plot_per_sample_log2_cna_pdfs(
    events,
    sample_order,
    chrom_sizes,
    offsets,
    ordered_chroms,
    genome_size,
    outdir,
    gene_hits=None,
    top_genes=10,
    bins=None,
    bin_root=None,
    source_col=None,
    bin_pattern=None,
    bin_log2_col=None,
    profile_max_samples=0,
    dpi=400,
    width=11.5,
    height=6.0,
    y_min=-3.2,
    y_max=3.2,
    write_png=False,
):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    top_gene_df = summarize_top_genes_per_sample(gene_hits, top_n=top_genes)
    write_per_sample_top_gene_tables(top_gene_df, outdir)

    samples = list(sample_order)
    if profile_max_samples and profile_max_samples > 0:
        samples = samples[:profile_max_samples]

    written = []
    for sample in samples:
        sample = str(sample)
        sub_bins = read_bins_for_sample(
            sample=sample,
            events=events,
            bins=bins,
            bin_root=bin_root,
            source_col=source_col,
            bin_pattern=bin_pattern,
            log2_col=bin_log2_col,
        )
        if sub_bins.empty:
            sub_bins = pseudo_bins_from_events(sample, events)
        sub_top = top_gene_df[top_gene_df["sample"].astype(str) == sample].copy() if not top_gene_df.empty else pd.DataFrame()
        pdf = plot_one_sample_log2_gene_pdf(
            sample=sample,
            bins=sub_bins,
            events=events,
            top_genes=sub_top,
            chrom_sizes=chrom_sizes,
            offsets=offsets,
            ordered_chroms=ordered_chroms,
            genome_size=genome_size,
            outdir=outdir,
            top_n=top_genes,
            dpi=dpi,
            width=width,
            height=height,
            y_min=y_min,
            y_max=y_max,
            write_png=write_png,
        )
        written.append(str(pdf))

    manifest = pd.DataFrame({"sample": [str(s) for s in samples], "pdf": written})
    manifest.to_csv(outdir / "per_sample_log2_cna_pdf_manifest.tsv", sep="\t", index=False)
    print(f"Wrote {len(written)} per-sample log2 CNA PDFs to: {outdir}")
    return written, top_gene_df


###############################################################################
# Summary tables
###############################################################################

def write_summary_tables(events, sample_order, outdir):
    burden = (
        events.pivot_table(
            index="sample",
            columns="state",
            values="size_mb",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(sample_order, fill_value=0)
        .reindex(columns=STATE_ORDER, fill_value=0)
    )

    counts = (
        events.pivot_table(
            index="sample",
            columns="state",
            values="cytoband",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(sample_order, fill_value=0)
        .reindex(columns=STATE_ORDER, fill_value=0)
    )

    burden["total_altered_mb"] = burden.sum(axis=1)
    counts["total_events"] = counts.sum(axis=1)

    burden.to_csv(outdir / "plot_table_burden_mb.tsv", sep="\t")
    counts.to_csv(outdir / "plot_table_event_counts.tsv", sep="\t")


###############################################################################
# Main
###############################################################################

def main():
    parser = argparse.ArgumentParser(
        description="Create paper-style CNA plots from cna_events.tsv."
    )

    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--cytoband", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)

    parser.add_argument(
        "--sort",
        choices=["burden", "summary", "alphabetical"],
        default="burden",
        help="Sample order. Default: burden.",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="Number of recurrent cytobands to show in the original cytoband plot. Default: 40.",
    )

    parser.add_argument(
        "--max-samples",
        type=int,
        default=0,
        help="Limit overview/per-sample plots to top N samples after sorting. 0 = all.",
    )

    parser.add_argument(
        "--font-scale",
        type=float,
        default=1.08,
        help="Global font scaling. Default: 1.08.",
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=400,
        help="PNG resolution. Default: 400.",
    )

    parser.add_argument(
        "--overview-width",
        type=float,
        default=11.5,
        help="Genome overview figure width in inches. Default: 11.5.",
    )

    parser.add_argument(
        "--overview-height-per-sample",
        type=float,
        default=0.24,
        help="Genome overview height per sample in inches. Default: 0.24.",
    )

    parser.add_argument(
        "--overview-max-height",
        type=float,
        default=8.8,
        help="Maximum genome overview height in inches. Default: 8.8.",
    )

    parser.add_argument(
        "--per-sample-width",
        type=float,
        default=7.2,
        help="Per-sample page width in inches. Default: 7.2.",
    )

    parser.add_argument(
        "--per-sample-height",
        type=float,
        default=5.1,
        help="Per-sample page height in inches. Default: 5.1.",
    )

    # New: first reference image, bin-level log2-ratio profile.
    parser.add_argument(
        "--bins",
        type=Path,
        default=None,
        help="Optional bin-level log2-ratio/copy-ratio table for the reference-like log2 profile panel.",
    )
    parser.add_argument(
        "--bins-log2-col",
        dest="bins_log2_col",
        default=None,
        help="Column in --bins containing log2-ratio values. If omitted, common names are auto-detected.",
    )
    parser.add_argument(
        "--profile-sample",
        default=None,
        help="Sample to plot for the log2-ratio panel. Use 'all' for a multi-page PDF. Default: first matching sample.",
    )
    parser.add_argument(
        "--profile-max-samples",
        type=int,
        default=20,
        help="Maximum samples when --profile-sample all. 0 = all. Default: 20.",
    )
    parser.add_argument("--profile-width", type=float, default=11.4)
    parser.add_argument("--profile-height", type=float, default=3.2)
    parser.add_argument("--profile-y-min", type=float, default=-3.2)
    parser.add_argument("--profile-y-max", type=float, default=3.2)

    parser.add_argument(
        "--bin-root",
        type=Path,
        default=None,
        help="Optional folder used to find per-sample QDNAseq/SAMURAI bin files if paths in the events source column are missing.",
    )
    parser.add_argument(
        "--bin-pattern",
        default=None,
        help="Optional recursive filename pattern for --bin-root, e.g. '{sample}_markdup_bins.bed'.",
    )
    parser.add_argument(
        "--source-column",
        default=None,
        help="Column in --events containing per-row/per-sample bin-file paths. Default: auto-detect source/bin_file/path.",
    )
    parser.add_argument(
        "--bin-log2-col",
        dest="bin_log2_col",
        default=None,
        help="Log2/copy-ratio column in per-sample bin files referenced by the source column or --bin-root.",
    )
    parser.add_argument(
        "--per-sample-log2-subdir",
        default="per_sample_log2_cna",
        help="Subfolder created inside --outdir for one PDF per sample. Default: per_sample_log2_cna.",
    )
    parser.add_argument(
        "--skip-per-sample-log2-pdfs",
        action="store_true",
        help="Disable the per-sample log2 CNA PDFs with top affected genes.",
    )
    parser.add_argument(
        "--write-per-sample-png",
        action="store_true",
        help="Also write PNG copies of the per-sample PDFs.",
    )

    # New: requested Panel A/B reference-like figure.
    parser.add_argument(
        "--top_genes",
        "--top-genes",
        dest="top_genes",
        type=int,
        default=10,
        help="Number of genes to show in Panel B. Default: 10.",
    )
    parser.add_argument(
        "--gene-events",
        type=Path,
        default=None,
        help="Optional gene-level CNA table with sample/gene/state columns. Overrides gene columns in --events.",
    )
    parser.add_argument(
        "--gene-column",
        default=None,
        help="Optional column in --events containing gene names. If omitted, common gene column names are auto-detected.",
    )
    parser.add_argument(
        "--gene-bed",
        type=Path,
        default=None,
        help="Optional BED-like gene annotation: chrom start end gene. Used if no gene table/column exists.",
    )
    parser.add_argument(
        "--gene-gtf",
        "--gtf",
        dest="gene_gtf",
        type=Path,
        default=None,
        help="Optional GTF/GTF.GZ gene annotation. Used for per-sample top affected genes if no gene column exists.",
    )
    parser.add_argument(
        "--gene-list",
        default=None,
        help="Optional comma-separated gene list or file. If provided, Panel B uses this order before --top_genes truncation.",
    )
    parser.add_argument(
        "--frequency-bin-mb",
        type=float,
        default=5.0,
        help="Bin size for Panel A cohort frequency. Default: 5 Mb.",
    )
    parser.add_argument(
        "--frequency-ylim",
        type=float,
        default=50.0,
        help="Symmetric y limit for Panel A frequency plot. Default: 50.",
    )
    parser.add_argument(
        "--annotate-cytobands",
        type=int,
        default=4,
        help="Number of recurrent cytoband/arm annotations in Panel A. 0 disables. Default: 4.",
    )
    parser.add_argument(
        "--gene-panel-units",
        choices=["count", "percent"],
        default="count",
        help="Panel B bar heights. Default count, with percent labels as in the reference figure.",
    )
    parser.add_argument("--panels-width", type=float, default=12.0)
    parser.add_argument("--panels-height", type=float, default=7.2)

    args = parser.parse_args()

    apply_paper_style(font_scale=args.font_scale)

    args.outdir.mkdir(parents=True, exist_ok=True)

    events = read_events(args.events)
    summary = read_summary(args.summary)

    _, chrom_sizes, offsets, ordered_chroms, genome_size = read_cytoband(args.cytoband)

    if events.empty:
        bins = read_bins(args.bins, sample_name=args.profile_sample, log2_col=args.bins_log2_col) if args.bins else pd.DataFrame()
        sample_names = sorted(bins["sample"].dropna().astype(str).unique()) if not bins.empty and "sample" in bins.columns else ["all"]
        message = "No CNA events detected after filtering."
        for pdf_name in ["cna_per_sample_pages.pdf", "cna_log2_ratio_profiles_all_samples.pdf"]:
            with PdfPages(args.outdir / pdf_name) as pdf:
                fig, ax = plt.subplots(figsize=(8.5, 4.8))
                ax.axis("off")
                ax.text(0.5, 0.58, message, ha="center", va="center", fontsize=14, weight="bold")
                ax.text(0.5, 0.43, "Samples: " + ", ".join(sample_names[:12]), ha="center", va="center", fontsize=9)
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        pd.DataFrame({"note": [message], "samples": [",".join(sample_names)]}).to_csv(args.outdir / "cna_plot_no_events_summary.tsv", sep="\t", index=False)
        print(message)
        print(f"Wrote placeholder CNA plot PDFs to: {args.outdir}")
        return

    sample_order = make_sample_order(events, summary, args.sort)

    if args.max_samples and args.max_samples > 0:
        sample_order = sample_order[:args.max_samples]

    events = events[events["sample"].isin(sample_order)].copy()

    write_summary_tables(events, sample_order, args.outdir)

    # Original plots.
    plot_genome_overview(
        events=events,
        sample_order=sample_order,
        chrom_sizes=chrom_sizes,
        offsets=offsets,
        ordered_chroms=ordered_chroms,
        genome_size=genome_size,
        outdir=args.outdir,
        dpi=args.dpi,
        width=args.overview_width,
        height_per_sample=args.overview_height_per_sample,
        max_height=args.overview_max_height,
    )

    events_for_counts = events.copy()
    events_for_counts["event_count"] = 1

    plot_stacked_bar(
        events=events,
        sample_order=sample_order,
        value_col="size_mb",
        title="Total altered genome per sample",
        ylabel="Altered size (Mb)",
        outfile_prefix="cna_event_burden_by_sample",
        outdir=args.outdir,
        dpi=args.dpi,
    )

    plot_stacked_bar(
        events=events_for_counts,
        sample_order=sample_order,
        value_col="event_count",
        title="CNA event count per sample",
        ylabel="Number of CNA events",
        outfile_prefix="cna_event_counts_by_sample",
        outdir=args.outdir,
        dpi=args.dpi,
    )

    plot_recurrent_cytobands(
        events=events,
        outdir=args.outdir,
        top_n=args.top,
        dpi=args.dpi,
    )

    plot_per_sample_pages(
        events=events,
        sample_order=sample_order,
        chrom_sizes=chrom_sizes,
        outdir=args.outdir,
        dpi=args.dpi,
        width=args.per_sample_width,
        height=args.per_sample_height,
    )

    # New first reference-like log2-ratio profile panel.
    bins = read_bins(args.bins, sample_name=args.profile_sample, log2_col=args.bins_log2_col) if args.bins else pd.DataFrame()
    plot_log2_ratio_profiles(
        bins=bins,
        events=events,
        sample_order=sample_order,
        chrom_sizes=chrom_sizes,
        offsets=offsets,
        ordered_chroms=ordered_chroms,
        genome_size=genome_size,
        outdir=args.outdir,
        profile_sample=args.profile_sample,
        profile_max_samples=args.profile_max_samples,
        dpi=args.dpi,
        width=args.profile_width,
        height=args.profile_height,
        y_min=args.profile_y_min,
        y_max=args.profile_y_max,
    )

    # Gene-level hits are used for both the cohort Panel B and the new per-sample
    # top-gene panels. They can come from a gene column, --gene-events, --gene-bed,
    # --gene-gtf, or an auto-detected local resources/hg38_genes.bed file.
    gene_hits = build_gene_hits(
        events=events,
        gene_events_path=args.gene_events,
        gene_col=args.gene_column,
        gene_bed=args.gene_bed,
        gene_gtf=args.gene_gtf,
        cytoband_path=args.cytoband,
    )
    gene_events = gene_hits[["sample", "gene", "state"]].drop_duplicates() if gene_hits is not None and not gene_hits.empty else pd.DataFrame(columns=["sample", "gene", "state"])

    # New Panel A/B reference-like cohort figure.
    plot_frequency_gene_panels(
        events=events,
        gene_events=gene_events,
        sample_order=sample_order,
        chrom_sizes=chrom_sizes,
        ordered_chroms=ordered_chroms,
        outdir=args.outdir,
        top_genes=args.top_genes,
        gene_list=args.gene_list,
        frequency_bin_mb=args.frequency_bin_mb,
        frequency_ylim=args.frequency_ylim,
        annotate_cytobands=args.annotate_cytobands,
        gene_panel_units=args.gene_panel_units,
        dpi=args.dpi,
        width=args.panels_width,
        height=args.panels_height,
    )

    # Requested in this update: one log2 CNA PDF per sample, using the bin files
    # pointed to by the events 'source' column when available, plus the top N
    # affected genes per sample.
    if not args.skip_per_sample_log2_pdfs:
        per_sample_dir = args.outdir / args.per_sample_log2_subdir
        src_col = source_column_name(events, requested=args.source_column)
        if src_col:
            print(f"Using events column '{src_col}' to locate per-sample bin files.")
        elif args.bin_root is None and args.bins is None:
            print("WARNING: no source/bin column detected and no --bin-root/--bins supplied. Per-sample PDFs will use event-level fallback points.")

        plot_per_sample_log2_cna_pdfs(
            events=events,
            sample_order=sample_order,
            chrom_sizes=chrom_sizes,
            offsets=offsets,
            ordered_chroms=ordered_chroms,
            genome_size=genome_size,
            outdir=per_sample_dir,
            gene_hits=gene_hits,
            top_genes=args.top_genes,
            bins=args.bins,
            bin_root=args.bin_root,
            source_col=src_col,
            bin_pattern=args.bin_pattern,
            bin_log2_col=args.bin_log2_col or args.bins_log2_col,
            profile_max_samples=args.profile_max_samples if args.profile_sample and str(args.profile_sample).lower() == "all" else args.max_samples,
            dpi=args.dpi,
            width=args.profile_width,
            height=max(5.6, args.profile_height + 2.5),
            y_min=args.profile_y_min,
            y_max=args.profile_y_max,
            write_png=args.write_per_sample_png,
        )

    print("Done. Wrote paper-style CNA plots to:")
    print(f"  {args.outdir}")
    print("New requested outputs include:")
    print(f"  {args.per_sample_log2_subdir}/<sample>_log2_cna_top{args.top_genes}_genes.pdf")
    print(f"  {args.per_sample_log2_subdir}/per_sample_top_genes.tsv")
    print("  cna_frequency_gene_panels.pdf/.png/.svg")
    print("  cna_log2_ratio_profile_<sample>.pdf/.png/.svg, if --bins was supplied")


if __name__ == "__main__":
    main()
