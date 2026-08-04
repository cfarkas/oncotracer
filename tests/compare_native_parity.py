#!/usr/bin/env python3
"""Semantic and auditable v1.1-versus-native-v2 parity gate."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

REQUIRED = (
    "06_workflow_summary/workflow_summary.txt",
    "03_cna_codification/cna_events.tsv",
    "03_cna_codification/cna_cytogenomic_notation.tsv",
    "04_cna_custom_plots/cna_per_sample_pages.pdf",
    "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
)


class ParityError(RuntimeError):
    pass


@dataclass
class Event:
    sample: str
    state: str
    chrom: str
    start: int
    end: int
    mean_log2: float | None


@dataclass
class ProfileRow:
    sample: str
    chrom: str
    start: int
    end: int
    log2: float


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.suffix == ".gz" else path.open("r", encoding="utf-8", errors="replace")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with open_text(path) as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def norm_chrom(value: str) -> str:
    value = value.strip().replace("chromosome", "").replace("Chromosome", "")
    if value.lower().startswith("chr"):
        value = value[3:]
    if value.upper() == "MT":
        value = "M"
    return value.upper()


def number(row: dict[str, str], names: Iterable[str]) -> float | None:
    for name in names:
        value = row.get(name)
        if value is None or str(value).strip().lower() in {"", "na", "nan", "none", "null", "."}:
            continue
        try:
            result = float(value)
        except ValueError:
            continue
        if math.isfinite(result):
            return result
    return None


def text(row: dict[str, str], names: Iterable[str]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip().strip('"')
    return ""


def parse_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for row in read_tsv(path):
        start = number(row, ("start", "START", "start0"))
        end = number(row, ("end", "END", "stop"))
        if start is None or end is None or end <= start:
            continue
        events.append(
            Event(
                sample=text(row, ("sample", "ID", "samplename")),
                state=text(row, ("state", "cna_state", "call", "raw_call")).lower(),
                chrom=norm_chrom(text(row, ("chrom", "chromosome", "chr"))),
                start=int(start),
                end=int(end),
                mean_log2=number(row, ("mean_log2", "median_log2", "seg.mean", "adj.seg", "log2")),
            )
        )
    return events


def reciprocal_overlap(left: Event, right: Event) -> float:
    intersection = max(0, min(left.end, right.end) - max(left.start, right.start))
    if not intersection:
        return 0.0
    return min(intersection / (left.end - left.start), intersection / (right.end - right.start))


def match_events(reference: list[Event], candidate: list[Event], minimum_overlap: float) -> dict[str, object]:
    used: set[int] = set()
    matches: list[dict[str, object]] = []
    for ref in reference:
        best_index = None
        best_overlap = -1.0
        for index, cand in enumerate(candidate):
            if index in used:
                continue
            if (ref.sample, ref.state, ref.chrom) != (cand.sample, cand.state, cand.chrom):
                continue
            overlap = reciprocal_overlap(ref, cand)
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = index
        if best_index is not None and best_overlap >= minimum_overlap:
            used.add(best_index)
            cand = candidate[best_index]
            matches.append(
                {
                    "sample": ref.sample,
                    "state": ref.state,
                    "chrom": ref.chrom,
                    "v1_start": ref.start,
                    "v1_end": ref.end,
                    "v2_start": cand.start,
                    "v2_end": cand.end,
                    "reciprocal_overlap": best_overlap,
                    "abs_log2_difference": (
                        abs(ref.mean_log2 - cand.mean_log2)
                        if ref.mean_log2 is not None and cand.mean_log2 is not None
                        else None
                    ),
                }
            )
    recall = 1.0 if not reference else len(matches) / len(reference)
    precision = 1.0 if not candidate else len(matches) / len(candidate)
    differences = [float(item["abs_log2_difference"]) for item in matches if item["abs_log2_difference"] is not None]
    return {
        "v1_events": len(reference),
        "v2_events": len(candidate),
        "matched_events": len(matches),
        "recall": recall,
        "precision": precision,
        "median_abs_log2_difference": statistics.median(differences) if differences else 0.0,
        "matches": matches,
    }


def parse_summary(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def sample_set(path: Path) -> set[str]:
    return {text(row, ("sample", "samplename", "ID")) for row in read_tsv(path) if text(row, ("sample", "samplename", "ID"))}


def locate_profile(root: Path, summary: dict[str, str]) -> Path:
    dataset = summary.get("dataset")
    candidates = []
    if dataset:
        candidates.append(root / "02_bam_refinement" / dataset / "01_tables" / "refined_bins.tsv.gz")
    candidates.extend((root / "02_bam_refinement").glob("*/01_tables/refined_bins.tsv.gz"))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size:
            return candidate
    raise ParityError(f"refined bin profile not found below: {root}")


def profile_rows(path: Path) -> list[ProfileRow]:
    rows: list[ProfileRow] = []
    for row in read_tsv(path):
        sample = text(row, ("sample", "ID", "samplename"))
        chrom = norm_chrom(text(row, ("chrom", "chromosome", "chr")))
        start = number(row, ("start", "bin_start", "START"))
        end = number(row, ("end", "bin_end", "END"))
        value = number(
            row,
            (
                "log2",
                "log2_ratio",
                "refined_log2",
                "final_log2",
                "seg_log2",
                "value",
                "median_log2",
                "seg.mean",
                "adj.seg",
            ),
        )
        if sample and chrom and start is not None and end is not None and value is not None and end > start:
            rows.append(ProfileRow(sample, chrom, int(start), int(end), value))
    if not rows:
        raise ParityError(f"could not identify sample/chrom/start/end/log2 columns in {path}")
    return rows


def pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return float("nan")
    if len(left) == 1:
        return 1.0 if left[0] == right[0] else 0.0
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    denominator = math.sqrt(sum((x - left_mean) ** 2 for x in left) * sum((y - right_mean) ** 2 for y in right))
    if denominator == 0:
        return 1.0 if all(abs(x - y) < 1e-12 for x, y in zip(left, right, strict=True)) else 0.0
    return numerator / denominator


def compare_profiles(v1: list[ProfileRow], v2: list[ProfileRow]) -> dict[str, object]:
    v1_map = {(row.sample, row.chrom, row.start, row.end): row.log2 for row in v1}
    v2_map = {(row.sample, row.chrom, row.start, row.end): row.log2 for row in v2}
    shared = sorted(v1_map.keys() & v2_map.keys())
    if not shared:
        raise ParityError("v1.1 and v2 refined profiles have no exact shared genomic bins")
    left = [v1_map[key] for key in shared]
    right = [v2_map[key] for key in shared]
    differences = [abs(x - y) for x, y in zip(left, right, strict=True)]
    return {
        "v1_bins": len(v1_map),
        "v2_bins": len(v2_map),
        "shared_bins": len(shared),
        "v1_shared_fraction": len(shared) / len(v1_map),
        "v2_shared_fraction": len(shared) / len(v2_map),
        "pearson": pearson(left, right),
        "median_abs_log2_difference": statistics.median(differences),
        "p95_abs_log2_difference": sorted(differences)[min(len(differences) - 1, math.ceil(0.95 * len(differences)) - 1)],
    }


def validate_required(root: Path) -> list[dict[str, object]]:
    records = []
    for relative in REQUIRED:
        path = root / relative
        ok = path.is_file() and path.stat().st_size > 0
        records.append({"path": relative, "exists": ok, "bytes": path.stat().st_size if ok else 0, "sha256": sha256(path) if ok else None})
    return records


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1", type=Path, required=True)
    parser.add_argument("--v2", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--minimum-event-overlap", type=float, default=0.80)
    parser.add_argument("--minimum-event-recall", type=float, default=0.90)
    parser.add_argument("--minimum-event-precision", type=float, default=0.90)
    parser.add_argument("--minimum-profile-correlation", type=float, default=0.98)
    parser.add_argument("--maximum-profile-median-absolute-difference", type=float, default=0.08)
    parser.add_argument("--minimum-shared-bin-fraction", type=float, default=0.95)
    args = parser.parse_args()
    v1 = args.v1.resolve()
    v2 = args.v2.resolve()
    outdir = args.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    v1_required = validate_required(v1)
    v2_required = validate_required(v2)
    summary1 = parse_summary(v1 / REQUIRED[0])
    summary2 = parse_summary(v2 / REQUIRED[0])
    samples1 = sample_set(v1 / REQUIRED[2])
    samples2 = sample_set(v2 / REQUIRED[2])
    events = match_events(
        parse_events(v1 / REQUIRED[1]),
        parse_events(v2 / REQUIRED[1]),
        args.minimum_event_overlap,
    )
    profiles = compare_profiles(
        profile_rows(locate_profile(v1, summary1)),
        profile_rows(locate_profile(v2, summary2)),
    )
    trace = v2 / ".oncotracer-native" / "trace.tsv"
    trace_text = trace.read_text(encoding="utf-8", errors="replace") if trace.is_file() else ""

    checks = {
        "required_outputs_v1": all(record["exists"] for record in v1_required),
        "required_outputs_v2": all(record["exists"] for record in v2_required),
        "mode_equal": summary1.get("mode") == summary2.get("mode"),
        "dataset_equal": summary1.get("dataset") == summary2.get("dataset"),
        "sample_set_equal": samples1 == samples2,
        "native_summary": summary2.get("engine") == "native" and summary2.get("nextflow_used", "").lower() == "false",
        "native_trace_present": trace.is_file() and trace.stat().st_size > 0,
        "native_trace_has_no_nextflow": "nextflow" not in trace_text.lower(),
        "event_recall": float(events["recall"]) >= args.minimum_event_recall,
        "event_precision": float(events["precision"]) >= args.minimum_event_precision,
        "profile_correlation": float(profiles["pearson"]) >= args.minimum_profile_correlation,
        "profile_median_difference": float(profiles["median_abs_log2_difference"]) <= args.maximum_profile_median_absolute_difference,
        "profile_shared_v1": float(profiles["v1_shared_fraction"]) >= args.minimum_shared_bin_fraction,
        "profile_shared_v2": float(profiles["v2_shared_fraction"]) >= args.minimum_shared_bin_fraction,
    }
    passed = all(checks.values())
    report = {
        "schema": "oncotracer-native-parity-v1",
        "label": args.label,
        "passed": passed,
        "v1_root": str(v1),
        "v2_root": str(v2),
        "thresholds": {
            "minimum_event_overlap": args.minimum_event_overlap,
            "minimum_event_recall": args.minimum_event_recall,
            "minimum_event_precision": args.minimum_event_precision,
            "minimum_profile_correlation": args.minimum_profile_correlation,
            "maximum_profile_median_absolute_difference": args.maximum_profile_median_absolute_difference,
            "minimum_shared_bin_fraction": args.minimum_shared_bin_fraction,
        },
        "checks": checks,
        "v1_summary": summary1,
        "v2_summary": summary2,
        "v1_samples": sorted(samples1),
        "v2_samples": sorted(samples2),
        "events": {key: value for key, value in events.items() if key != "matches"},
        "profiles": profiles,
        "required_v1": v1_required,
        "required_v2": v2_required,
    }
    json_path = outdir / "parity_report.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_tsv(outdir / "event_matches.tsv", list(events["matches"]))
    markdown = [
        f"# OncoTracer native parity: {args.label}",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}**",
        "",
        "## Release checks",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    markdown.extend(f"| `{key}` | {'PASS' if value else 'FAIL'} |" for key, value in checks.items())
    markdown.extend(
        [
            "",
            "## Event concordance",
            "",
            f"- v1.1 events: {events['v1_events']}",
            f"- v2 events: {events['v2_events']}",
            f"- matched events: {events['matched_events']}",
            f"- recall: {events['recall']:.6f}",
            f"- precision: {events['precision']:.6f}",
            "",
            "## Refined-bin concordance",
            "",
            f"- shared bins: {profiles['shared_bins']}",
            f"- Pearson correlation: {profiles['pearson']:.8f}",
            f"- median absolute log2 difference: {profiles['median_abs_log2_difference']:.8f}",
            f"- p95 absolute log2 difference: {profiles['p95_abs_log2_difference']:.8f}",
            "",
            "The native command trace is included in the artifact and is rejected if it contains `nextflow`.",
        ]
    )
    (outdir / "parity_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    checksums = []
    for path in sorted(outdir.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            checksums.append(f"{sha256(path)}  {path.name}")
    (outdir / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
