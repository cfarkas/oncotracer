#!/usr/bin/env python3
"""Parse GISTIC2 outputs into sample × lesion matrices.

The parser is intentionally tolerant. It understands the standard
all_lesions.conf_XX.txt first section, but it also creates empty placeholder
outputs when GISTIC was skipped, failed, or produced no all_lesions file.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def sanitize(x: Any) -> str:
    s = str(x).strip()
    if s.startswith("+"):
        s = "amp_" + s[1:]
    elif s.startswith("-"):
        s = "del_" + s[1:]
    s = s.replace("+", "plus")
    s = re.sub(r"[^A-Za-z0-9_.:+-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "NA"


def read_status(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame([{"status": "unknown", "reason": "missing_status_file", "segmentation": "NA", "command": "NA"}])
    try:
        return pd.read_csv(path, sep="\t", dtype=str).fillna("NA")
    except Exception:
        return pd.DataFrame([{"status": "unknown", "reason": "unreadable_status_file", "segmentation": "NA", "command": "NA"}])


def write_empty(status: pd.DataFrame, reason: str) -> None:
    pd.DataFrame(index=pd.Index([], name="sample")).to_csv("gistic_lesions_matrix.tsv", sep="\t")
    pd.DataFrame(columns=[
        "sample", "gistic_feature", "value", "unique_name", "descriptor", "wide_peak_limits",
        "peak_limits", "region_limits", "q_value", "residual_q_value", "broad_or_focal",
        "amplitude_threshold", "direction",
    ]).to_csv("gistic_lesions_long.tsv", sep="\t", index=False)
    pd.DataFrame(columns=[
        "gistic_feature", "unique_name", "descriptor", "wide_peak_limits", "peak_limits",
        "region_limits", "q_value", "residual_q_value", "broad_or_focal", "amplitude_threshold",
        "direction", "n_samples", "n_high_level", "sample_list",
    ]).to_csv("gistic_lesions_summary.tsv", sep="\t", index=False)
    metrics = {
        "status": status.to_dict(orient="records"),
        "parser_status": "empty",
        "reason": reason,
        "n_lesions": 0,
        "n_samples": 0,
    }
    Path("gistic_parse_metrics.json").write_text(json.dumps(metrics, indent=2))


def find_all_lesions(gistic_dir: Path) -> Path | None:
    candidates = sorted(gistic_dir.glob("all_lesions.conf_*.txt"))
    if not candidates:
        candidates = sorted(gistic_dir.glob("*all*lesion*.txt"))
    if not candidates:
        return None
    # Prefer the largest file, because partially written files can exist after failed runs.
    candidates = sorted(candidates, key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return candidates[0]


def split_line(line: str) -> list[str]:
    if "\t" in line:
        return [x.strip() for x in line.rstrip("\n").split("\t")]
    return [x.strip() for x in re.split(r"\s{2,}", line.rstrip("\n"))]


def infer_direction(unique_name: str, descriptor: str) -> str:
    u = str(unique_name).strip().lower()
    d = str(descriptor).strip().lower()
    if u.startswith("+") or u.startswith("amp") or "ampl" in d:
        return "amp"
    if u.startswith("-") or u.startswith("del") or "delet" in d:
        return "del"
    return "unknown"


def to_float_or_nan(x: Any) -> float:
    try:
        s = str(x).strip()
        if s in {"", "NA", "nan", "NaN"}:
            return np.nan
        return float(s)
    except Exception:
        return np.nan


def parse_all_lesions(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    lines = path.read_text(errors="replace").splitlines()
    header_idx = None
    header = None
    for i, line in enumerate(lines):
        fields = split_line(line)
        lower = [f.lower() for f in fields]
        if len(fields) >= 10 and ("unique name" in lower[0] or lower[0].replace("_", " ") == "unique name") and any("amplitude" in f for f in lower):
            header_idx = i
            header = fields
            break
    if header_idx is None or header is None:
        raise ValueError(f"Could not find standard all_lesions header in {path}")

    # Standard GISTIC all_lesions first nine columns are lesion metadata; sample columns follow.
    meta_cols = header[:9]
    sample_cols = header[9:]
    rows = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        if line.lower().startswith("actual copy change"):
            break
        if line.lower().startswith("actual copy"):
            break
        fields = split_line(line)
        if len(fields) < 9:
            continue
        # Stop if the next section header starts.
        if fields[0].lower().startswith("actual copy"):
            break
        if len(fields) < len(header):
            fields = fields + [""] * (len(header) - len(fields))
        elif len(fields) > len(header):
            # Some descriptors can contain accidental tabs. Keep rightmost values as sample cells.
            fields = fields[:8] + [" ".join(fields[8:len(fields) - len(sample_cols)])] + fields[-len(sample_cols):]
        rows.append(fields[:len(header)])

    if not rows:
        return pd.DataFrame(index=pd.Index([], name="sample")), pd.DataFrame(), pd.DataFrame()

    raw = pd.DataFrame(rows, columns=header)
    rename = {
        meta_cols[0]: "unique_name",
        meta_cols[1]: "descriptor",
        meta_cols[2]: "wide_peak_limits",
        meta_cols[3]: "peak_limits",
        meta_cols[4]: "region_limits",
        meta_cols[5]: "q_value",
        meta_cols[6]: "residual_q_value",
        meta_cols[7]: "broad_or_focal",
        meta_cols[8]: "amplitude_threshold",
    }
    raw = raw.rename(columns=rename)
    for col in ["q_value", "residual_q_value"]:
        if col in raw.columns:
            raw[col] = raw[col].map(to_float_or_nan)

    raw["direction"] = raw.apply(lambda r: infer_direction(r.get("unique_name", ""), r.get("descriptor", "")), axis=1)
    raw["gistic_feature"] = raw.apply(
        lambda r: sanitize(f"gistic_{r.get('direction', 'event')}_{r.get('unique_name', '')}_{r.get('wide_peak_limits', '')}"),
        axis=1,
    )

    matrix = pd.DataFrame(index=sample_cols)
    long_rows = []
    summary_rows = []
    for _, r in raw.iterrows():
        feature = str(r["gistic_feature"])
        vals = pd.to_numeric(r[sample_cols], errors="coerce").fillna(0).astype(int)
        matrix[feature] = vals.values
        positive = vals[vals != 0]
        high = vals[vals.abs() >= 2]
        summary_rows.append({
            "gistic_feature": feature,
            "unique_name": r.get("unique_name", ""),
            "descriptor": r.get("descriptor", ""),
            "wide_peak_limits": r.get("wide_peak_limits", ""),
            "peak_limits": r.get("peak_limits", ""),
            "region_limits": r.get("region_limits", ""),
            "q_value": r.get("q_value", np.nan),
            "residual_q_value": r.get("residual_q_value", np.nan),
            "broad_or_focal": r.get("broad_or_focal", ""),
            "amplitude_threshold": r.get("amplitude_threshold", ""),
            "direction": r.get("direction", "unknown"),
            "n_samples": int((vals != 0).sum()),
            "n_high_level": int((vals.abs() >= 2).sum()),
            "sample_list": ",".join(positive.index.astype(str).tolist()),
        })
        for sample, val in vals.items():
            if int(val) == 0:
                continue
            long_rows.append({
                "sample": sample,
                "gistic_feature": feature,
                "value": int(val),
                "unique_name": r.get("unique_name", ""),
                "descriptor": r.get("descriptor", ""),
                "wide_peak_limits": r.get("wide_peak_limits", ""),
                "peak_limits": r.get("peak_limits", ""),
                "region_limits": r.get("region_limits", ""),
                "q_value": r.get("q_value", np.nan),
                "residual_q_value": r.get("residual_q_value", np.nan),
                "broad_or_focal": r.get("broad_or_focal", ""),
                "amplitude_threshold": r.get("amplitude_threshold", ""),
                "direction": r.get("direction", "unknown"),
            })

    matrix.index.name = "sample"
    summary = pd.DataFrame(summary_rows).sort_values(["n_samples", "q_value"], ascending=[False, True])
    long = pd.DataFrame(long_rows)
    return matrix, long, summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse GISTIC2 all_lesions output into matrices.")
    ap.add_argument("--gistic-dir", required=True)
    ap.add_argument("--gistic-status", required=True)
    ap.add_argument("--gistic-command", required=True)
    args = ap.parse_args()

    gdir = Path(args.gistic_dir)
    status = read_status(Path(args.gistic_status))
    last_status = str(status.iloc[-1].get("status", "unknown")) if not status.empty else "unknown"
    if last_status != "completed":
        reason = str(status.iloc[-1].get("reason", f"gistic_status_{last_status}")) if not status.empty else "not_completed"
        write_empty(status, reason=reason)
        return

    all_lesions = find_all_lesions(gdir)
    if all_lesions is None:
        write_empty(status, reason="completed_but_no_all_lesions_file_found")
        return

    try:
        matrix, long, summary = parse_all_lesions(all_lesions)
    except Exception as exc:
        write_empty(status, reason=f"parse_error: {exc}")
        return

    matrix.to_csv("gistic_lesions_matrix.tsv", sep="\t")
    if long.empty:
        long = pd.DataFrame(columns=[
            "sample", "gistic_feature", "value", "unique_name", "descriptor", "wide_peak_limits",
            "peak_limits", "region_limits", "q_value", "residual_q_value", "broad_or_focal",
            "amplitude_threshold", "direction",
        ])
    long.to_csv("gistic_lesions_long.tsv", sep="\t", index=False)
    if summary.empty:
        summary = pd.DataFrame(columns=[
            "gistic_feature", "unique_name", "descriptor", "wide_peak_limits", "peak_limits",
            "region_limits", "q_value", "residual_q_value", "broad_or_focal", "amplitude_threshold",
            "direction", "n_samples", "n_high_level", "sample_list",
        ])
    summary.to_csv("gistic_lesions_summary.tsv", sep="\t", index=False)

    metrics = {
        "status": status.to_dict(orient="records"),
        "parser_status": "completed",
        "all_lesions_file": str(all_lesions),
        "n_lesions": int(matrix.shape[1]),
        "n_samples": int(matrix.shape[0]),
        "n_long_rows": int(len(long)),
    }
    Path("gistic_parse_metrics.json").write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
