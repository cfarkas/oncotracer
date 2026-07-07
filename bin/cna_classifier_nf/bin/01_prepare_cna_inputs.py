#!/usr/bin/env python3
"""
Prepare CNA classifier inputs from SAMURAI/QDNAseq CNA codification tables.

Required input:
  cna_events.tsv with at least: sample, state, chrom, start, end.
Expected columns from the user's current SAMURAI codification include:
  sample, state, chrom, start, end, size_mb, cytoband, n_bins, mean_log2,
  median_log2, estimated_total_copy_number, copy_code, molecular_piece,
  cna_shorthand, source.

Optional input:
  cna_cytogenomic_notation.tsv to retain samples with no high-confidence CNA.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

AUTOSOMES = [str(i) for i in range(1, 23)]
SEX_CHROMS = ["X", "Y"]
STATE_ORDER = ["deep_loss", "loss", "gain", "amplification"]
STATE_SIGNED_CODE = {
    "deep_loss": -2,
    "loss": -1,
    "neutral": 0,
    "gain": 1,
    "amplification": 2,
}


def _clean_col(col: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(col).strip().lower()).strip("_")


def _find_header_line(lines: list[str], required_first: str = "sample") -> int:
    """Find the first plausible TSV header line in a possibly pasted shell transcript."""
    for i, line in enumerate(lines):
        raw = line.strip("\n")
        fields = re.split(r"\t| {2,}", raw.strip())
        lower_fields = [_clean_col(x) for x in fields]
        if required_first in lower_fields and ("state" in lower_fields or "n_cna_events" in lower_fields):
            return i
    # fallback: first non-empty line that starts with sample
    for i, line in enumerate(lines):
        if line.lstrip().lower().startswith("sample"):
            return i
    raise ValueError("Could not find a table header line beginning with 'sample'.")


def read_pasted_or_tsv(path: str | Path, table_type: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    header_idx = _find_header_line(lines)
    table_text = "\n".join(lines[header_idx:]) + "\n"

    # Try real tab-separated first. If only one column was parsed, fall back to whitespace.
    df = pd.read_csv(io.StringIO(table_text), sep="\t", dtype=str, comment=None, engine="python")
    if df.shape[1] == 1:
        df = pd.read_csv(io.StringIO(table_text), sep=r"\s+", dtype=str, engine="python")

    df.columns = [_clean_col(c) for c in df.columns]

    # Drop accidental prompt/footer rows after parsing.
    if "sample" in df.columns:
        df = df[~df["sample"].astype(str).str.contains(r"^\(|^The file is|^\[|^base\)", regex=True, na=False)]
        df = df[df["sample"].notna()]
        df = df[df["sample"].astype(str).str.strip() != ""]

    if table_type == "events" and "state" not in df.columns:
        raise ValueError(f"{path} does not look like cna_events.tsv; 'state' column missing.")
    return df


SKIP_INPUT_DIR_NAMES = {
    ".nextflow", "work", "tmp", "temp", "logs",
    "cna_classifier_nf_results", "cna_classifier_results",
    "01_prepared", "02_classification", "03_report", "04_gistic2", "05_gistic2_parsed",
    "report_tables", "figures", "gistic2_out", "gistic2_input_files", "gistic2_refgene",
}


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _is_in_skipped_dir(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts[:-1]
    except Exception:
        rel_parts = path.parts[:-1]
    return any(part in SKIP_INPUT_DIR_NAMES for part in rel_parts)


def discover_table_files(input_path: str | Path, table_type: str) -> list[Path]:
    """Discover CNA event/notation TSV files from a file or directory.

    The pipeline keeps the old behavior when a single TSV is supplied, but also
    accepts a folder. For folders, root-level canonical files are preferred to
    avoid accidentally re-reading old pipeline outputs. Recursive search is used
    for cohort folders with many per-sample subdirectories.
    """
    p = Path(str(input_path)).expanduser()
    if p.is_file():
        return [p]
    if not p.exists():
        raise FileNotFoundError(f"Input path not found: {p}")
    if not p.is_dir():
        raise ValueError(f"Input path is neither a file nor a directory: {p}")

    if table_type == "events":
        exact = ["cna_events.tsv", "cna_events.txt"]
        patterns = ["*cna_events*.tsv", "*cna_events*.txt", "*CNA*events*.tsv", "*events.tsv"]
        exclude_name_parts = ("notation", "cytogenomic", "summary", "classification", "recurrent", "matrix", "driver", "gistic", "sample_cna_summary", "dlbclass", "metrics")
    elif table_type == "notation":
        exact = ["cna_cytogenomic_notation.tsv", "cna_cytogenomic_notation.txt", "cna_notation.tsv"]
        patterns = ["*cytogenomic*notation*.tsv", "*cytogenomic*notation*.txt", "*notation*.tsv", "*notation*.txt"]
        exclude_name_parts = ("events", "classification", "matrix", "gistic", "metrics")
    else:
        raise ValueError(f"Unknown table_type: {table_type}")

    def ok(q: Path) -> bool:
        name = q.name.lower()
        return q.is_file() and q.stat().st_size > 0 and not _is_in_skipped_dir(q, p) and not any(x in name for x in exclude_name_parts)

    # Root-level exact/canonical files are the common case and should win.
    root_candidates: list[Path] = []
    for name in exact:
        q = p / name
        if q.is_file() and q.stat().st_size > 0:
            root_candidates.append(q)
    for pat in patterns:
        root_candidates.extend([q for q in p.glob(pat) if ok(q)])
    root_candidates = _unique_paths(root_candidates)
    if root_candidates:
        return root_candidates

    # Recursive mode: useful for one folder containing one subfolder per lymphoma.
    rec_candidates: list[Path] = []
    for name in exact:
        rec_candidates.extend([q for q in p.rglob(name) if ok(q)])
    for pat in patterns:
        rec_candidates.extend([q for q in p.rglob(pat) if ok(q)])
    return _unique_paths(rec_candidates)


def read_cna_events_input(input_path: str | Path) -> pd.DataFrame:
    """Read a single CNA events table or merge all CNA events tables in a folder."""
    files = discover_table_files(input_path, table_type="events")
    if not files:
        raise FileNotFoundError(
            f"No CNA event TSVs found in {input_path}. Expected a cna_events.tsv file or a folder containing one."
        )
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    required = {"sample", "state", "chrom", "start", "end"}
    for f in files:
        try:
            d = read_pasted_or_tsv(f, table_type="events")
            if not required.issubset(set(d.columns)):
                raise ValueError("required columns sample/state/chrom/start/end were not all present")
            d["input_source_file"] = str(f)
            frames.append(d)
        except Exception as e:
            errors.append(f"{f}: {e}")
    if not frames:
        msg = "\n".join(errors[:20])
        raise ValueError(f"No readable CNA event tables found from {input_path}. Errors:\n{msg}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.drop_duplicates().reset_index(drop=True)
    out.attrs["input_event_tables"] = [str(f) for f in files]
    return out


def load_notation_samples_auto(cna_events_path: str | Path, cna_notation_path: str | Path) -> tuple[set[str], list[str]]:
    """Load samples from notation table(s), auto-discovering inside an events folder."""
    notation_files: list[Path] = []

    # Explicit notation path, if supplied and real.
    try:
        npth = Path(str(cna_notation_path)).expanduser()
        if npth.exists():
            if npth.is_file():
                notation_files.append(npth)
            elif npth.is_dir():
                notation_files.extend(discover_table_files(npth, table_type="notation"))
    except Exception:
        pass

    # If the CNA events input is a directory, automatically scan it for notation.
    try:
        epth = Path(str(cna_events_path)).expanduser()
        if epth.exists() and epth.is_dir():
            notation_files.extend(discover_table_files(epth, table_type="notation"))
    except Exception:
        pass

    notation_files = _unique_paths(notation_files)
    samples: set[str] = set()
    used: list[str] = []
    for f in notation_files:
        loaded = load_samples_from_notation(f)
        if loaded:
            samples |= loaded
            used.append(str(f))
    return samples, used


def normalize_chrom(x: object) -> str:
    s = str(x).strip()
    s = re.sub(r"^chr", "", s, flags=re.IGNORECASE)
    if s in {"23", "x"}: return "X"
    if s in {"24", "y"}: return "Y"
    return s.upper() if s.upper() in {"X", "Y"} else s


def normalize_state(x: object) -> str:
    s = str(x).strip().lower()
    s = s.replace(" ", "_").replace("-", "_")
    mapping = {
        "amp": "amplification",
        "amplified": "amplification",
        "amplification": "amplification",
        "high_gain": "amplification",
        "gain": "gain",
        "copy_gain": "gain",
        "loss": "loss",
        "deletion": "loss",
        "del": "loss",
        "copy_loss": "loss",
        "deep_loss": "deep_loss",
        "homdel": "deep_loss",
        "homozygous_deletion": "deep_loss",
    }
    return mapping.get(s, s)


def as_numeric(series: pd.Series, default: float | None = np.nan) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def sanitize_feature(s: object) -> str:
    x = str(s).strip()
    x = re.sub(r"[^A-Za-z0-9_.:+-]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "NA"


def chrom_sort_key(chrom: str) -> tuple[int, str]:
    c = normalize_chrom(chrom)
    if c.isdigit():
        return (int(c), "")
    if c == "X":
        return (23, "")
    if c == "Y":
        return (24, "")
    return (99, c)


def load_chrom_sizes(path: str | Path, include_sex: bool = False) -> dict[str, int]:
    """Load chromosome sizes for full-genome neutral-gap SEG construction."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Chromosome sizes file not found: {p}")
    raw = pd.read_csv(p, sep="\t", dtype=str, comment="#")
    raw.columns = [_clean_col(c) for c in raw.columns]
    if "chrom" not in raw.columns:
        raw = pd.read_csv(p, sep=r"\s+", dtype=str, comment="#", header=None, names=["chrom", "size"], engine="python")
    if "size" not in raw.columns:
        candidates = [c for c in raw.columns if c in {"length", "chrom_size", "chromlength", "end"}]
        if candidates:
            raw = raw.rename(columns={candidates[0]: "size"})
    if not {"chrom", "size"}.issubset(raw.columns):
        raise ValueError(f"Chromosome sizes file must contain chrom and size columns: {p}")
    raw["chrom"] = raw["chrom"].map(normalize_chrom)
    raw["size"] = pd.to_numeric(raw["size"], errors="coerce").astype("Int64")
    allowed = set(AUTOSOMES + (SEX_CHROMS if include_sex else []))
    raw = raw[raw["chrom"].isin(allowed) & raw["size"].notna()].copy()
    raw = raw.sort_values("chrom", key=lambda x: x.map(lambda c: chrom_sort_key(c)[0]))
    return {str(r.chrom): int(r.size) for r in raw.itertuples(index=False)}


def make_gistic_marker_file(chrom_sizes: dict[str, int], window_bp: int, out_path: str = "gistic_markers.tsv") -> int:
    """Create a pseudo-marker file for GISTIC2 at approximately the SAMURAI/QDNAseq bin size."""
    window_bp = max(int(window_bp), 1)
    n = 0
    with open(out_path, "w") as out:
        out.write("Marker Name\tChromosome\tMarker Position\n")
        for chrom in sorted(chrom_sizes, key=lambda c: chrom_sort_key(c)[0]):
            size = int(chrom_sizes[chrom])
            pos = max(1, window_bp // 2)
            while pos <= size:
                n += 1
                out.write(f"m_chr{chrom}_{pos}\t{chrom}\t{pos}\n")
                pos += window_bp
            if size % window_bp != 0:
                n += 1
                out.write(f"m_chr{chrom}_{size}\t{chrom}\t{size}\n")
    return n


def make_full_genome_gistic_seg(
    df: pd.DataFrame,
    all_samples: list[str],
    chrom_sizes: dict[str, int],
    window_bp: int,
    out_path: str = "gistic_full.seg",
) -> dict[str, int]:
    """Create full-genome segmentation with neutral gaps for GISTIC2.

    SAMURAI codification tables usually contain only altered CNA events. GISTIC2 is
    more stable when the input also contains neutral regions. This function fills
    one neutral segment between called events instead of exploding the genome into
    every 100 kb bin, so segment counts remain reasonable.
    """
    rows: list[dict[str, object]] = []
    window_bp = max(int(window_bp), 1)
    work = df.copy()
    work["_chrom_sort"] = work["chrom"].map(lambda c: chrom_sort_key(c)[0])
    work = work.sort_values(["sample", "_chrom_sort", "start", "end", "mean_log2"]).drop(columns=["_chrom_sort"])

    for sample in all_samples:
        sample_events = work[work["sample"].astype(str) == str(sample)]
        for chrom in sorted(chrom_sizes, key=lambda c: chrom_sort_key(c)[0]):
            chrom_len = int(chrom_sizes[chrom])
            cur = 1
            chrom_events = sample_events[sample_events["chrom"].astype(str) == str(chrom)].copy()
            for ev in chrom_events.itertuples(index=False):
                ev_start = max(1, int(getattr(ev, "start")))
                ev_end = min(chrom_len, int(getattr(ev, "end")))
                if ev_end < cur:
                    continue
                if ev_start > cur:
                    neutral_end = ev_start - 1
                    neutral_len = neutral_end - cur + 1
                    rows.append({
                        "Sample": sample,
                        "Chromosome": chrom,
                        "Start": cur,
                        "End": neutral_end,
                        "Num_Probes": max(1, int(math.ceil(neutral_len / window_bp))),
                        "Segment_Mean": 0.0,
                    })
                if ev_start < cur:
                    ev_start = cur
                if ev_start <= ev_end:
                    num_probes = int(getattr(ev, "n_bins")) if not pd.isna(getattr(ev, "n_bins")) else max(1, int(math.ceil((ev_end - ev_start + 1) / window_bp)))
                    segmean = float(getattr(ev, "mean_log2")) if not pd.isna(getattr(ev, "mean_log2")) else 0.0
                    rows.append({
                        "Sample": sample,
                        "Chromosome": chrom,
                        "Start": ev_start,
                        "End": ev_end,
                        "Num_Probes": max(1, num_probes),
                        "Segment_Mean": round(segmean, 6),
                    })
                    cur = ev_end + 1
            if cur <= chrom_len:
                neutral_len = chrom_len - cur + 1
                rows.append({
                    "Sample": sample,
                    "Chromosome": chrom,
                    "Start": cur,
                    "End": chrom_len,
                    "Num_Probes": max(1, int(math.ceil(neutral_len / window_bp))),
                    "Segment_Mean": 0.0,
                })
    seg = pd.DataFrame(rows)
    seg.to_csv(out_path, sep="\t", index=False)
    sample_counts = seg.groupby("Sample").size().to_dict() if not seg.empty else {}
    return {
        "gistic_full_segments": int(len(seg)),
        "max_segments_per_sample": int(max(sample_counts.values()) if sample_counts else 0),
        "n_samples_in_gistic_full_seg": int(len(sample_counts)),
    }


def overlap_bp(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start) + 1)


def states_match(event_state: str, preferred: str) -> bool:
    prefs = {x.strip().lower() for x in str(preferred).split(",") if x.strip()}
    if not prefs or "any" in prefs:
        return True
    if event_state == "amplification" and ("gain" in prefs or "amplification" in prefs):
        return True
    if event_state == "deep_loss" and ("loss" in prefs or "deep_loss" in prefs):
        return True
    return event_state in prefs


def load_samples_from_notation(path: Path) -> set[str]:
    """Extract sample IDs from cna_cytogenomic_notation.tsv, including pasted shell transcripts.

    The notation table often contains long free-text cytogenomic strings with spaces, so a
    strict whitespace table parser can fail. For this purpose we only need the first
    token after the header line, which is the sample ID.
    """
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return set()
    lines = text.splitlines()
    try:
        header_idx = _find_header_line(lines, required_first="sample")
    except Exception:
        return set()
    samples: set[str] = set()
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("(") or stripped.startswith("[") or stripped.startswith("The file"):
            continue
        token = stripped.split()[0]
        if token.lower() in {"sample", "the", "base)"}:
            continue
        if re.match(r"^[A-Za-z0-9_.-]+$", token):
            samples.add(token)
    return samples


def make_event_label(row: pd.Series) -> str:
    state = row.get("state", "event")
    chrom = normalize_chrom(row.get("chrom", "NA"))
    cytoband = row.get("cytoband", "")
    if pd.notna(cytoband) and str(cytoband).strip() not in {"", "nan", "None"}:
        locus = str(cytoband).strip()
    else:
        locus = f"{int(row.get('start', 0))}-{int(row.get('end', 0))}"
    return sanitize_feature(f"{state}_chr{chrom}_{locus}")


def infer_arm(cytoband: object) -> str:
    cb = str(cytoband).strip()
    m = re.match(r"^([0-9XY]+)([pq])", cb, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1)}{m.group(2).lower()}"
    return "NA"




def parse_sample_filter(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "all", "false"}:
        return []
    parts = [x.strip() for x in re.split(r"[,;\s]+", text) if x.strip()]
    return list(dict.fromkeys(parts))


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare CNA matrices from SAMURAI CNA event codification.")
    ap.add_argument("--cna-events", required=True)
    ap.add_argument("--cna-notation", required=True)
    ap.add_argument("--region-catalog", required=True)
    ap.add_argument("--chrom-sizes", required=True, help="Two-column chrom/size file used to create full-genome neutral-gap GISTIC SEG.")
    ap.add_argument("--gistic-window-bp", type=int, default=100000, help="Pseudo-marker spacing and neutral segment probe approximation for GISTIC.")
    ap.add_argument("--min-bins", type=int, default=3)
    ap.add_argument("--min-size-mb", type=float, default=0.5)
    ap.add_argument("--min-abs-log2", type=float, default=0.25)
    ap.add_argument("--focal-mb", type=float, default=30.0)
    ap.add_argument("--broad-mb", type=float, default=30.0)
    ap.add_argument("--include-sex", action="store_true")
    ap.add_argument("--sample", default="", help="Backward-compatible optional sample ID or comma-separated sample IDs to subset from a cohort table/folder.")
    ap.add_argument("--samples", default="", help="Preferred optional comma/semicolon/space-separated sample IDs to subset from a cohort table/folder.")
    args = ap.parse_args()

    sample_filter = parse_sample_filter(args.samples or args.sample)

    events_raw = read_cna_events_input(args.cna_events)
    required = {"sample", "state", "chrom", "start", "end"}
    missing = required - set(events_raw.columns)
    if missing:
        raise ValueError(f"cna_events.tsv is missing required columns: {sorted(missing)}")

    df = events_raw.copy()
    df["sample"] = df["sample"].astype(str).str.strip()
    if sample_filter:
        df = df[df["sample"].isin(sample_filter)].copy()
    df["state"] = df["state"].map(normalize_state)
    df["chrom"] = df["chrom"].map(normalize_chrom)

    for col in ["start", "end", "n_bins", "size_mb", "mean_log2", "median_log2", "estimated_total_copy_number"]:
        if col not in df.columns:
            df[col] = np.nan
    df["start"] = as_numeric(df["start"]).astype("Int64")
    df["end"] = as_numeric(df["end"]).astype("Int64")
    df = df[df["start"].notna() & df["end"].notna()].copy()
    df["start"] = df["start"].astype(int)
    df["end"] = df["end"].astype(int)
    swap = df["start"] > df["end"]
    if swap.any():
        df.loc[swap, ["start", "end"]] = df.loc[swap, ["end", "start"]].to_numpy()

    df["n_bins"] = as_numeric(df["n_bins"], default=1).astype(int)
    df["size_mb"] = as_numeric(df["size_mb"])
    missing_size = df["size_mb"].isna()
    df.loc[missing_size, "size_mb"] = (df.loc[missing_size, "end"] - df.loc[missing_size, "start"] + 1) / 1e6
    df["mean_log2"] = as_numeric(df["mean_log2"])
    df["median_log2"] = as_numeric(df["median_log2"])
    df["estimated_total_copy_number"] = as_numeric(df["estimated_total_copy_number"])

    if "cytoband" not in df.columns:
        df["cytoband"] = ""
    if "cna_shorthand" not in df.columns:
        df["cna_shorthand"] = df.apply(lambda r: f"{r['state']}(chr{r['chrom']})({r['start']}_{r['end']})", axis=1)
    if "copy_code" not in df.columns:
        df["copy_code"] = ""
    if "source" not in df.columns:
        df["source"] = ""

    allowed_chroms = set(AUTOSOMES + (SEX_CHROMS if args.include_sex else []))
    before_n = len(df)
    df = df[df["chrom"].isin(allowed_chroms)].copy()
    df = df[df["state"].isin(STATE_ORDER)].copy()

    # Filtering: for amplification/deep_loss, keep even if min_abs_log2 is not met because state itself carries confidence.
    strong_state = df["state"].isin(["amplification", "deep_loss"])
    filter_mask = (
        (df["n_bins"] >= args.min_bins)
        & (df["size_mb"] >= args.min_size_mb)
        & (strong_state | (df["mean_log2"].abs() >= args.min_abs_log2))
    )
    df = df[filter_mask].copy()

    df["arm"] = df["cytoband"].map(infer_arm)
    if df.empty:
        df["event_label"] = pd.Series(dtype=str)
        df["signed_code"] = pd.Series(dtype=int)
        df["abs_code"] = pd.Series(dtype=int)
    else:
        df["event_label"] = df.apply(make_event_label, axis=1)
        df["signed_code"] = df["state"].map(STATE_SIGNED_CODE).astype(int)
        df["abs_code"] = df["signed_code"].abs().astype(int)

    # Stable sorting.
    if not df.empty:
        df["_chrom_sort"] = df["chrom"].map(lambda c: chrom_sort_key(c)[0])
        df = df.sort_values(["sample", "_chrom_sort", "start", "end", "state"]).drop(columns=["_chrom_sort"])

    notation_samples, notation_tables_used = load_notation_samples_auto(args.cna_events, args.cna_notation)
    if sample_filter:
        notation_samples = notation_samples & set(sample_filter)
    event_samples = set(df["sample"].astype(str).unique())
    if sample_filter:
        # Include requested samples even if they have zero events after filtering.
        all_samples = list(sample_filter)
    else:
        all_samples = sorted(notation_samples | event_samples)
    if not all_samples:
        raise ValueError("No samples found in CNA events or notation table.")

    # Clean events table.
    clean_cols = [
        "sample", "state", "chrom", "start", "end", "size_mb", "cytoband", "arm",
        "n_bins", "mean_log2", "median_log2", "estimated_total_copy_number", "copy_code",
        "cna_shorthand", "event_label", "signed_code", "source", "input_source_file",
    ]
    existing_clean_cols = [c for c in clean_cols if c in df.columns]
    df[existing_clean_cols].to_csv("clean_events.tsv", sep="\t", index=False)
    pd.DataFrame({"sample": all_samples}).to_csv("samples.tsv", sep="\t", index=False)

    # SEG formats for GISTIC-like tools.
    # 1) samurai_events.seg / gistic_events.seg: altered-event-only representation.
    # 2) gistic_full.seg: altered events plus neutral gaps for every sample/chromosome.
    seg = pd.DataFrame({
        "Sample": df["sample"],
        "Chromosome": df["chrom"],
        "Start": df["start"].astype(int),
        "End": df["end"].astype(int),
        "Num_Probes": df["n_bins"].astype(int),
        "Segment_Mean": df["mean_log2"].round(6),
    })
    seg.to_csv("samurai_events.seg", sep="\t", index=False)
    seg.to_csv("gistic_events.seg", sep="\t", index=False)
    chrom_sizes = load_chrom_sizes(args.chrom_sizes, include_sex=args.include_sex)
    marker_count = make_gistic_marker_file(chrom_sizes, args.gistic_window_bp, out_path="gistic_markers.tsv")
    gistic_full_metrics = make_full_genome_gistic_seg(
        df=df,
        all_samples=all_samples,
        chrom_sizes=chrom_sizes,
        window_bp=args.gistic_window_bp,
        out_path="gistic_full.seg",
    )

    # Sample-level summary.
    rows = []
    for sample in all_samples:
        sub = df[df["sample"] == sample]
        total = int(len(sub))
        gains = int((sub["state"] == "gain").sum())
        amps = int((sub["state"] == "amplification").sum())
        losses = int((sub["state"] == "loss").sum())
        deep_losses = int((sub["state"] == "deep_loss").sum())
        altered_mb = float(sub["size_mb"].sum()) if total else 0.0
        gain_mb = float(sub.loc[sub["state"].isin(["gain", "amplification"]), "size_mb"].sum()) if total else 0.0
        loss_mb = float(sub.loc[sub["state"].isin(["loss", "deep_loss"]), "size_mb"].sum()) if total else 0.0
        n_chr = int(sub["chrom"].nunique()) if total else 0
        n_arms = int(sub["arm"].replace("NA", np.nan).dropna().nunique()) if total else 0
        focal = int((sub["size_mb"] <= args.focal_mb).sum()) if total else 0
        broad = int((sub["size_mb"] >= args.broad_mb).sum()) if total else 0
        max_abs = float(sub["mean_log2"].abs().max()) if total else 0.0
        max_gain = float(sub.loc[sub["state"].isin(["gain", "amplification"]), "mean_log2"].max()) if gains + amps else 0.0
        min_loss = float(sub.loc[sub["state"].isin(["loss", "deep_loss"]), "mean_log2"].min()) if losses + deep_losses else 0.0
        rows.append({
            "sample": sample,
            "n_cna_events": total,
            "n_gain": gains,
            "n_amplification": amps,
            "n_loss": losses,
            "n_deep_loss": deep_losses,
            "altered_mb": round(altered_mb, 4),
            "gain_mb": round(gain_mb, 4),
            "loss_mb": round(loss_mb, 4),
            "n_chromosomes_affected": n_chr,
            "n_arms_affected": n_arms,
            "n_focal_events_leq_focal_mb": focal,
            "n_broad_events_geq_broad_mb": broad,
            "max_abs_log2": round(max_abs, 6),
            "max_gain_log2": round(max_gain, 6),
            "min_loss_log2": round(min_loss, 6),
        })
    summary = pd.DataFrame(rows)
    summary.to_csv("sample_cna_summary.tsv", sep="\t", index=False)

    # Binary and weighted event matrices.
    labels = sorted(df["event_label"].unique())
    binary = pd.DataFrame(0, index=all_samples, columns=labels, dtype=int)
    weighted = pd.DataFrame(0, index=all_samples, columns=labels, dtype=int)
    for _, r in df.iterrows():
        sample = r["sample"]
        label = r["event_label"]
        code = int(r["signed_code"])
        binary.loc[sample, label] = 1
        if abs(code) > abs(int(weighted.loc[sample, label])):
            weighted.loc[sample, label] = code
    binary.index.name = "sample"
    weighted.index.name = "sample"
    binary.to_csv("event_matrix_binary.tsv", sep="\t")
    weighted.to_csv("event_matrix_weighted.tsv", sep="\t")

    # Recurrence at event-label level.
    if len(df):
        rec = (
            df.groupby(["event_label", "state", "chrom", "cytoband", "arm"], dropna=False)
            .agg(
                n_samples=("sample", "nunique"),
                n_events=("sample", "size"),
                start_min=("start", "min"),
                end_max=("end", "max"),
                median_size_mb=("size_mb", "median"),
                median_mean_log2=("mean_log2", "median"),
                max_abs_log2=("mean_log2", lambda x: float(np.nanmax(np.abs(x))) if len(x) else np.nan),
            )
            .reset_index()
        )
        rec["freq_pct"] = (100.0 * rec["n_samples"] / max(len(all_samples), 1)).round(2)
        rec["_chrom_sort"] = rec["chrom"].map(lambda c: chrom_sort_key(c)[0])
        rec = rec.sort_values(["n_samples", "max_abs_log2", "_chrom_sort", "start_min"], ascending=[False, False, True, True]).drop(columns=["_chrom_sort"])
    else:
        rec = pd.DataFrame(columns=["event_label", "state", "chrom", "cytoband", "arm", "n_samples", "n_events", "start_min", "end_max", "median_size_mb", "median_mean_log2", "max_abs_log2", "freq_pct"])
    rec.to_csv("recurrent_events.tsv", sep="\t", index=False)

    # Driver/lymphoma-region overlap matrices.
    regions = pd.read_csv(args.region_catalog, sep="\t", dtype=str)
    regions.columns = [_clean_col(c) for c in regions.columns]
    for c in ["start", "end", "weight"]:
        if c in regions.columns:
            regions[c] = pd.to_numeric(regions[c], errors="coerce")
    regions["chrom"] = regions["chrom"].map(normalize_chrom)

    feature_ids = list(regions["feature_id"].astype(str))
    driver = pd.DataFrame(0, index=all_samples, columns=feature_ids, dtype=int)
    driver_hits = []
    for _, ev in df.iterrows():
        ev_chr = normalize_chrom(ev["chrom"])
        ev_start, ev_end = int(ev["start"]), int(ev["end"])
        ev_state = str(ev["state"])
        ev_code = int(ev["signed_code"])
        candidates = regions[regions["chrom"] == ev_chr]
        for _, reg in candidates.iterrows():
            if not states_match(ev_state, str(reg.get("preferred_state", "any"))):
                continue
            ov = overlap_bp(ev_start, ev_end, int(reg["start"]), int(reg["end"]))
            if ov <= 0:
                continue
            ev_len = max(1, ev_end - ev_start + 1)
            reg_len = max(1, int(reg["end"] - reg["start"] + 1))
            frac_event = ov / ev_len
            frac_region = ov / reg_len
            # For broad low-pass events, any genomic overlap with a canonical driver interval is useful.
            if ov < 1:
                continue
            fid = str(reg["feature_id"])
            old = int(driver.loc[ev["sample"], fid])
            if abs(ev_code) > abs(old):
                driver.loc[ev["sample"], fid] = ev_code
            driver_hits.append({
                "sample": ev["sample"],
                "feature_id": fid,
                "feature_label": reg.get("label", fid),
                "genes": reg.get("genes", ""),
                "event_state": ev_state,
                "event_chrom": ev_chr,
                "event_start": ev_start,
                "event_end": ev_end,
                "event_cytoband": ev.get("cytoband", ""),
                "event_label": ev.get("event_label", ""),
                "mean_log2": ev.get("mean_log2", np.nan),
                "overlap_bp": ov,
                "overlap_fraction_event": round(frac_event, 4),
                "overlap_fraction_region": round(frac_region, 4),
            })
    driver.index.name = "sample"
    driver.to_csv("driver_region_matrix.tsv", sep="\t")
    hits_df = pd.DataFrame(driver_hits)
    if hits_df.empty:
        hits_df = pd.DataFrame(columns=["sample", "feature_id", "feature_label", "genes", "event_state", "event_chrom", "event_start", "event_end", "event_cytoband", "event_label", "mean_log2", "overlap_bp", "overlap_fraction_event", "overlap_fraction_region"])
    hits_df.to_csv("driver_region_hits.tsv", sep="\t", index=False)

    # DLBclass-style CNA matrix: rows are features, columns are samples; values signed CNA code.
    gsm = driver.T.copy()
    gsm.index.name = "CNA_feature"
    gsm.to_csv("dlbclass_cna_gsm_like.tsv", sep="\t")

    metrics = {
        "input_events_rows": int(len(events_raw)),
        "events_after_filtering": int(len(df)),
        "samples_total": int(len(all_samples)),
        "samples_with_events": int(len(event_samples)),
        "samples_from_notation": int(len(notation_samples)),
        "sample_filter": sample_filter,
        "filtered_out_events": int(before_n - len(df)),
        "filter_min_bins": args.min_bins,
        "filter_min_size_mb": args.min_size_mb,
        "filter_min_abs_log2": args.min_abs_log2,
        "include_sex": bool(args.include_sex),
        "gistic_window_bp": int(args.gistic_window_bp),
        "gistic_marker_count": int(marker_count),
        "input_event_tables": events_raw.attrs.get("input_event_tables", [str(args.cna_events)]),
        "notation_tables_used": notation_tables_used,
        **gistic_full_metrics,
        "warning": "gistic_full.seg includes neutral gaps and is the default GISTIC2 input; gistic_events.seg is retained as the altered-event-only SAMURAI codification SEG.",
    }
    Path("prepare_metrics.json").write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
