#!/usr/bin/env python3

import argparse
import csv
import gzip
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CHROM_ORDER = {str(i): i for i in range(1, 23)}
CHROM_ORDER.update({"X": 23, "Y": 24, "M": 25, "MT": 25})

QDNASEQ_PATTERNS = (
    "*segments*.bed",
    "*segments*.bed.gz",
    "*_segments.bed",
    "*_segments.bed.gz",
    "*bins*.bed",
    "*bins*.bed.gz",
    "*_bins.bed",
    "*_bins.bed.gz",
    "*copyNumbers*.bed",
    "*copyNumbers*.bed.gz",
    "*copynumber*.bed",
    "*copynumber*.bed.gz",
    "*calls*.bed",
    "*calls*.bed.gz",
)

# SAMURAI explicitly treats *.seg.txt as ichorCNA segment-level calls.
# *.cna.seg files are bin-level calls and are intentionally not selected.
ICHORCNA_PATTERNS = (
    "*.seg.txt",
    "*.seg.txt.gz",
)

NA_TOKENS = {"", ".", "na", "nan", "null", "none", "n/a", "inf", "+inf", "-inf"}


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def open_text(path):
    return gzip.open(path, "rt") if str(path).lower().endswith(".gz") else open(path, "rt")


def norm_chrom(chrom):
    chrom = str(chrom).strip()
    chrom = re.sub(r"^chr", "", chrom, flags=re.I)
    if chrom.upper() == "MT":
        chrom = "M"
    if chrom.lower() == "x":
        chrom = "X"
    if chrom.lower() == "y":
        chrom = "Y"
    return chrom


def chrom_key(chrom):
    chrom = norm_chrom(chrom)
    return (CHROM_ORDER.get(chrom, 999), chrom)


def strip_known_suffixes(name):
    suffixes = (
        ".seg.txt.gz",
        ".seg.txt",
        ".cna.seg.gz",
        ".cna.seg",
        ".seg.gz",
        ".seg",
        ".bed.gz",
        ".bed",
    )
    lowered = name.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def parse_sample_name(path, mode):
    sample = None

    if mode == "qdnaseq":
        try:
            with open_text(path) as handle:
                first = handle.readline().strip()
            match = re.search(r'name\s*=\s*"?([^"\s]+)"?', first, flags=re.I)
            if match:
                sample = match.group(1)
        except Exception:
            pass

    if not sample:
        sample = strip_known_suffixes(Path(path).name)

    sample = re.sub(r"_?markdup$", "", sample, flags=re.I)
    sample = re.sub(
        r"_?(bins|segments|copyNumbers|copynumber|calls)$",
        "",
        sample,
        flags=re.I,
    )
    sample = re.sub(r"\(.*\)$", "", sample).strip()
    return sample


def read_cytobands(path):
    bands = {}

    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue

            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue

            chrom = norm_chrom(parts[0])
            try:
                start0 = int(parts[1])
                end = int(parts[2])
            except ValueError:
                continue
            band = parts[3]

            bands.setdefault(chrom, []).append((start0, end, band))

    for chrom in bands:
        bands[chrom].sort()

    return bands


def band_span(chrom, start0, end, bands):
    chrom = norm_chrom(chrom)
    hits = []

    for band_start, band_end, band_name in bands.get(chrom, []):
        if band_end <= start0:
            continue
        if band_start >= end:
            break
        hits.append(band_name)

    if not hits:
        return f"{chrom}:{start0 + 1}-{end}", ""

    deduplicated = []
    for hit in hits:
        if not deduplicated or deduplicated[-1] != hit:
            deduplicated.append(hit)

    if len(deduplicated) == 1:
        span = deduplicated[0]
    else:
        span = f"{deduplicated[0]}{deduplicated[-1]}"

    return f"{chrom}{span}", span


def call_state_from_log2(log2_value, loss, gain, deep_loss, amp):
    if log2_value <= deep_loss:
        return "deep_loss"
    if log2_value <= loss:
        return "loss"
    if log2_value >= amp:
        return "amplification"
    if log2_value >= gain:
        return "gain"
    return "neutral"


def copy_code_from_log2(mean_log2):
    estimated_cn = 2.0 * (2.0 ** mean_log2)

    if estimated_cn > 4.0:
        return estimated_cn, "amp"

    nearest = int(round(estimated_cn))
    if abs(estimated_cn - nearest) <= 0.15:
        return estimated_cn, f"x{max(0, nearest)}"

    low = max(0, int(math.floor(estimated_cn)))
    high = max(low + 1, int(math.ceil(estimated_cn)))
    return estimated_cn, f"x{low}~{high}"


def copy_code_from_model_cn(copy_number):
    nearest = int(round(copy_number))
    if abs(copy_number - nearest) <= 0.05:
        return f"x{max(0, nearest)}"

    low = max(0, int(math.floor(copy_number)))
    high = max(low + 1, int(math.ceil(copy_number)))
    return f"x{low}~{high}"


def float_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in NA_TOKENS:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def int_or_none(value):
    number = float_or_none(value)
    if number is None:
        return None
    return int(round(number))


def read_qdnaseq_bed(path, args):
    rows = []

    with open_text(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            lowered = line.lower()
            if line.startswith("track") or line.startswith("#") or lowered.startswith("chrom"):
                continue

            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue

            chrom = norm_chrom(parts[0])
            if chrom not in CHROM_ORDER:
                continue

            try:
                start0 = int(parts[1])
                end = int(parts[2])
                log2_value = float(parts[4])
            except ValueError:
                continue

            if not math.isfinite(log2_value) or end <= start0:
                continue

            state = call_state_from_log2(
                log2_value,
                args.loss,
                args.gain,
                args.deep_loss,
                args.amp,
            )

            if state == "neutral":
                continue

            rows.append(
                {
                    "chrom": chrom,
                    "start0": start0,
                    "end": end,
                    "state": state,
                    "log2_values": [log2_value],
                    "copy_numbers": [],
                    "n_bins": 1,
                    "raw_calls": [state],
                    "subclone_statuses": [],
                    "classification_sources": ["qdnaseq_log2_thresholds"],
                }
            )

    rows.sort(key=lambda row: (chrom_key(row["chrom"]), row["start0"], row["end"]))
    return rows


def merge_qdnaseq_rows(rows, max_gap_bp):
    events = []
    current = None

    for row in rows:
        if current is None:
            current = dict(row)
            continue

        can_merge = (
            row["chrom"] == current["chrom"]
            and row["state"] == current["state"]
            and row["start0"] <= current["end"] + max_gap_bp
        )

        if can_merge:
            current["end"] = max(current["end"], row["end"])
            current["log2_values"].extend(row["log2_values"])
            current["n_bins"] += row["n_bins"]
            current["raw_calls"].extend(row["raw_calls"])
            current["classification_sources"].extend(row["classification_sources"])
        else:
            events.append(current)
            current = dict(row)

    if current is not None:
        events.append(current)

    return events


def canonical_header(header):
    return re.sub(r"[^a-z0-9]+", "", str(header).strip().lower())


def find_column(headers, *candidates):
    canonical_to_original = {canonical_header(header): header for header in headers}
    for candidate in candidates:
        key = canonical_header(candidate)
        if key in canonical_to_original:
            return canonical_to_original[key]
    return None


def read_table_rows(path):
    lines = []
    with open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            lines.append(line.rstrip("\n"))

    if not lines:
        return [], []

    header_line = lines[0]
    delimiter = "\t" if "\t" in header_line else None

    if delimiter:
        headers = header_line.split("\t")
    else:
        headers = re.split(r"\s+", header_line.strip())

    rows = []
    for line in lines[1:]:
        parts = line.split("\t") if delimiter else re.split(r"\s+", line.strip())
        if len(parts) < len(headers):
            parts.extend([""] * (len(headers) - len(parts)))
        elif len(parts) > len(headers):
            parts = parts[: len(headers)]
        rows.append(dict(zip(headers, parts)))

    return headers, rows


def normalize_ichorcna_call(call_value, copy_number, log2_value, args):
    call = "" if call_value is None else str(call_value).strip().upper()

    if call.startswith("HOMD"):
        return "deep_loss", "ichorcna_call"
    if call.startswith("HETD") or call in {"LOSS", "DEL", "DELETION"}:
        return "loss", "ichorcna_call"
    if call in {"NEUT", "NORMAL", "DIPLOID", "NEUTRAL"}:
        return "neutral", "ichorcna_call"
    if call.startswith("GAIN"):
        return "gain", "ichorcna_call"
    if call.startswith("AMP") or call.startswith("HLAMP"):
        return "amplification", "ichorcna_call"

    if copy_number is not None:
        rounded = int(round(copy_number))
        if rounded <= 0:
            return "deep_loss", "ichorcna_copy_number"
        if rounded == 1:
            return "loss", "ichorcna_copy_number"
        if rounded == 2:
            return "neutral", "ichorcna_copy_number"
        if rounded == 3:
            return "gain", "ichorcna_copy_number"
        return "amplification", "ichorcna_copy_number"

    if log2_value is not None:
        return (
            call_state_from_log2(
                log2_value,
                args.loss,
                args.gain,
                args.deep_loss,
                args.amp,
            ),
            "ichorcna_log2_threshold_fallback",
        )

    return None, None


def read_ichorcna_segments(path, args):
    headers, table_rows = read_table_rows(path)
    fallback_sample = parse_sample_name(path, "ichorcna")

    if not headers:
        return {fallback_sample}, defaultdict(list)

    sample_col = find_column(headers, "ID", "sample", "sample_id")
    chrom_col = find_column(headers, "chrom", "chr", "chromosome")
    start_col = find_column(headers, "start", "loc.start", "loc_start")
    end_col = find_column(headers, "end", "loc.end", "loc_end")
    n_bins_col = find_column(headers, "num.mark", "num_mark", "bins", "num.probes", "num_probes")
    log2_col = find_column(
        headers,
        "seg.median.logR",
        "seg.mean",
        "segment_mean",
        "median",
        "median.logR",
        "logR",
    )
    original_cn_col = find_column(headers, "copy.number", "copy_number", "copynumber")
    corrected_cn_col = find_column(headers, "Corrected_Copy_Number", "corrected.copy.number")
    original_call_col = find_column(headers, "call", "event")
    corrected_call_col = find_column(headers, "Corrected_Call", "corrected.call")
    subclone_col = find_column(headers, "subclone.status", "subclone_status")

    required = {
        "chromosome": chrom_col,
        "start": start_col,
        "end": end_col,
    }
    missing = [name for name, column in required.items() if column is None]
    if missing:
        raise ValueError(
            f"{path}: missing required ichorCNA column(s): {', '.join(missing)}; "
            f"observed columns: {', '.join(headers)}"
        )

    samples_seen = set()
    sample_events = defaultdict(list)

    for row in table_rows:
        sample = str(row.get(sample_col, "")).strip() if sample_col else fallback_sample
        if not sample or sample.lower() in NA_TOKENS:
            sample = fallback_sample
        samples_seen.add(sample)

        chrom = norm_chrom(row.get(chrom_col, ""))
        if chrom not in CHROM_ORDER:
            continue

        start1 = int_or_none(row.get(start_col))
        end1 = int_or_none(row.get(end_col))
        if start1 is None or end1 is None or end1 < start1:
            continue

        # ichorCNA uses 1-based inclusive segment coordinates. Convert to
        # internal 0-based half-open coordinates, matching BED internally.
        start0 = max(0, start1 - 1)
        end = end1

        n_bins = int_or_none(row.get(n_bins_col)) if n_bins_col else None
        if n_bins is None or n_bins < 1:
            n_bins = 1

        log2_value = float_or_none(row.get(log2_col)) if log2_col else None

        corrected_cn = float_or_none(row.get(corrected_cn_col)) if corrected_cn_col else None
        original_cn = float_or_none(row.get(original_cn_col)) if original_cn_col else None
        model_cn = corrected_cn if corrected_cn is not None else original_cn

        corrected_call = str(row.get(corrected_call_col, "")).strip() if corrected_call_col else ""
        original_call = str(row.get(original_call_col, "")).strip() if original_call_col else ""
        selected_call = corrected_call if corrected_call and corrected_call.lower() not in NA_TOKENS else original_call

        state, classification_source = normalize_ichorcna_call(
            selected_call,
            model_cn,
            log2_value,
            args,
        )
        if state is None:
            warn(f"{path}: skipped segment without usable call, copy number, or logR")
            continue
        if state == "neutral":
            continue

        subclone_status = str(row.get(subclone_col, "")).strip() if subclone_col else ""

        sample_events[sample].append(
            {
                "chrom": chrom,
                "start0": start0,
                "end": end,
                "state": state,
                "log2_values": [log2_value] if log2_value is not None else [],
                "copy_numbers": [model_cn] if model_cn is not None else [],
                "n_bins": n_bins,
                "raw_calls": [selected_call] if selected_call else [],
                "subclone_statuses": [subclone_status] if subclone_status else [],
                "classification_sources": [classification_source],
            }
        )

    if not samples_seen:
        samples_seen.add(fallback_sample)

    for sample in sample_events:
        sample_events[sample].sort(
            key=lambda row: (chrom_key(row["chrom"]), row["start0"], row["end"])
        )

    return samples_seen, sample_events


def ordered_unique(values):
    output = []
    seen = set()
    for value in values:
        if value is None:
            continue
        value = str(value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def state_to_shorthand(state, chrom, band):
    if state in ("loss", "deep_loss"):
        return f"del({chrom})({band})" if band else f"del({chrom})"
    if state == "gain":
        return f"gain({chrom})({band})" if band else f"gain({chrom})"
    if state == "amplification":
        return f"amp({chrom})({band})" if band else f"amp({chrom})"
    return state


def finalize_events(raw_events, sample, source, caller, bands, args):
    finalized = []

    for event in raw_events:
        size_bp = event["end"] - event["start0"]
        size_mb = size_bp / 1e6

        if event["n_bins"] < args.min_bins:
            continue
        if size_mb < args.min_mb:
            continue

        log2_values = [value for value in event["log2_values"] if value is not None]
        if log2_values:
            mean_log2 = sum(log2_values) / len(log2_values)
            median_log2 = statistics.median(log2_values)
        else:
            mean_log2 = None
            median_log2 = None

        copy_numbers = [value for value in event["copy_numbers"] if value is not None]
        model_copy_number = statistics.median(copy_numbers) if copy_numbers else None

        if caller == "ichorcna" and model_copy_number is not None:
            estimated_cn = model_copy_number
            copy_code = copy_code_from_model_cn(model_copy_number)
        elif mean_log2 is not None:
            estimated_cn, copy_code = copy_code_from_log2(mean_log2)
        else:
            estimated_cn = None
            copy_code = {
                "deep_loss": "x0",
                "loss": "x1",
                "gain": "x3",
                "amplification": "amp",
            }.get(event["state"], "CNA")

        cytoband, band_only = band_span(event["chrom"], event["start0"], event["end"], bands)
        molecular_piece = f"{cytoband}({event['start0'] + 1}_{event['end']}){copy_code}"
        shorthand = state_to_shorthand(event["state"], event["chrom"], band_only)

        raw_calls = ordered_unique(event["raw_calls"])
        subclone_statuses = ordered_unique(event["subclone_statuses"])
        classification_sources = ordered_unique(event["classification_sources"])

        finalized.append(
            {
                "sample": sample,
                "state": event["state"],
                "chrom": event["chrom"],
                "start": event["start0"] + 1,
                "end": event["end"],
                "size_mb": round(size_mb, 3),
                "cytoband": cytoband,
                "n_bins": event["n_bins"],
                "mean_log2": round(mean_log2, 4) if mean_log2 is not None else "",
                "median_log2": round(median_log2, 4) if median_log2 is not None else "",
                "estimated_total_copy_number": round(estimated_cn, 3) if estimated_cn is not None else "",
                "copy_code": copy_code,
                "molecular_piece": molecular_piece,
                "cna_shorthand": shorthand,
                "source": str(source),
                "caller": caller,
                "raw_call": ",".join(raw_calls),
                "model_copy_number": round(model_copy_number, 3) if model_copy_number is not None else "",
                "subclone_status": ",".join(subclone_statuses),
                "classification_source": ",".join(classification_sources),
            }
        )

    finalized.sort(key=lambda row: (chrom_key(row["chrom"]), int(row["start"])))
    return finalized


def find_input_files(input_dir, patterns):
    found = []
    for pattern in patterns:
        found.extend(input_dir.rglob(pattern))
    return found


def deduplicate_existing_paths(paths, parser):
    unique = []
    seen = set()

    for path in paths:
        path = Path(path)
        if not path.exists():
            parser.error(f"input file does not exist: {path}")
        if not path.is_file():
            continue

        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path.resolve())

    unique.sort(key=lambda item: str(item))
    return unique


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert SAMURAI QDNAseq BED bins or SAMURAI ichorCNA segment calls "
            "into cytoband-level molecular cytogenomic CNA notation. Select exactly "
            "one input mode: --qdnaseq or --ichorcna."
        )
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--qdnaseq",
        dest="caller",
        action="store_const",
        const="qdnaseq",
        help="Read QDNAseq BED bins/segments and apply the supplied log2 thresholds.",
    )
    mode_group.add_argument(
        "--ichorcna",
        dest="caller",
        action="store_const",
        const="ichorcna",
        help=(
            "Read per-sample ichorCNA *.seg.txt segment calls. Corrected_Call and "
            "Corrected_Copy_Number are preferred when present; *.cna.seg bin files "
            "and aggregate GISTIC files are not selected."
        ),
    )

    parser.add_argument(
        "--input_dir",
        "--input-dir",
        dest="input_dir",
        type=Path,
        help="Directory searched recursively for input files appropriate to the selected mode.",
    )
    parser.add_argument("--cytoband", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--genome-label", default="GRCh38")
    parser.add_argument(
        "--prefix",
        default="seq",
        help="Notation prefix, for example seq, arr, or lpwgs. Default: seq",
    )

    # These thresholds are used directly in QDNAseq mode. In ichorCNA mode,
    # model calls are preferred and thresholds are only a last-resort fallback.
    parser.add_argument("--loss", type=float, default=-0.30)
    parser.add_argument("--gain", type=float, default=0.25)
    parser.add_argument("--deep-loss", type=float, default=-1.00)
    parser.add_argument("--amp", type=float, default=0.70)
    parser.add_argument("--min-bins", type=int, default=3)
    parser.add_argument("--min-mb", type=float, default=1.0)
    parser.add_argument("--max-gap-bp", type=int, default=500000)
    parser.add_argument(
        "--input-pattern",
        "--bed-pattern",
        dest="input_patterns",
        action="append",
        default=[],
        help=(
            "Additional recursive glob pattern. Can be supplied multiple times. "
            "The legacy name --bed-pattern is retained as an alias."
        ),
    )
    parser.add_argument(
        "input_files",
        nargs="*",
        type=Path,
        help="Optional explicit input files; these can be combined with --input_dir.",
    )
    args = parser.parse_args()

    if args.input_dir is None and not args.input_files:
        parser.error("provide --input_dir and/or one or more explicit input files")

    if args.input_dir is not None:
        if not args.input_dir.exists():
            parser.error(f"--input_dir does not exist: {args.input_dir}")
        if not args.input_dir.is_dir():
            parser.error(f"--input_dir is not a directory: {args.input_dir}")

    if not args.cytoband.exists():
        parser.error(f"--cytoband does not exist: {args.cytoband}")

    if args.min_bins < 1:
        parser.error("--min-bins must be at least 1")
    if args.min_mb < 0:
        parser.error("--min-mb cannot be negative")
    if args.max_gap_bp < 0:
        parser.error("--max-gap-bp cannot be negative")

    args.outdir.mkdir(parents=True, exist_ok=True)

    default_patterns = QDNASEQ_PATTERNS if args.caller == "qdnaseq" else ICHORCNA_PATTERNS
    patterns = list(default_patterns) + list(args.input_patterns)
    candidate_files = []

    if args.input_dir is not None:
        candidate_files.extend(find_input_files(args.input_dir, patterns))
    candidate_files.extend(args.input_files)

    input_files = deduplicate_existing_paths(candidate_files, parser)
    if not input_files:
        searched = str(args.input_dir) if args.input_dir is not None else "explicit input list"
        expected = "QDNAseq BED files" if args.caller == "qdnaseq" else "per-sample *.seg.txt files"
        parser.error(f"no {expected} found in: {searched}")

    bands = read_cytobands(args.cytoband)
    all_events = []
    sample_to_events = {}
    sample_to_caller = {}
    manifest_rows = []

    for input_file in input_files:
        if args.caller == "qdnaseq":
            sample = parse_sample_name(input_file, "qdnaseq")
            raw_rows = read_qdnaseq_bed(input_file, args)
            raw_events = merge_qdnaseq_rows(raw_rows, args.max_gap_bp)
            events = finalize_events(
                raw_events,
                sample,
                input_file,
                "qdnaseq",
                bands,
                args,
            )

            if sample in sample_to_events:
                raise RuntimeError(
                    f"duplicate sample name '{sample}' from more than one QDNAseq input file; "
                    "use a narrower --input_dir or explicit files"
                )

            sample_to_events[sample] = events
            sample_to_caller[sample] = "qdnaseq"
            all_events.extend(events)
            manifest_rows.append(
                {"sample": sample, "caller": "qdnaseq", "input_file": str(input_file)}
            )

        else:
            samples_seen, raw_events_by_sample = read_ichorcna_segments(input_file, args)

            for sample in sorted(samples_seen):
                if sample in sample_to_events:
                    raise RuntimeError(
                        f"duplicate sample name '{sample}' from more than one ichorCNA input file; "
                        "SAMURAI may have published duplicate copies. Use the top-level ichorCNA "
                        "directory containing one <sample>.seg.txt per sample, or pass explicit files."
                    )

                raw_events = raw_events_by_sample.get(sample, [])
                events = finalize_events(
                    raw_events,
                    sample,
                    input_file,
                    "ichorcna",
                    bands,
                    args,
                )
                sample_to_events[sample] = events
                sample_to_caller[sample] = "ichorcna"
                all_events.extend(events)
                manifest_rows.append(
                    {"sample": sample, "caller": "ichorcna", "input_file": str(input_file)}
                )

    input_manifest_path = args.outdir / "input_cna_files.tsv"
    event_path = args.outdir / "cna_events.tsv"
    sample_path = args.outdir / "cna_cytogenomic_notation.tsv"

    with input_manifest_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample", "caller", "input_file"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    # Backward-compatible manifest retained for existing QDNAseq recipes.
    legacy_manifest_path = None
    if args.caller == "qdnaseq":
        legacy_manifest_path = args.outdir / "input_bed_files.tsv"
        with legacy_manifest_path.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["sample", "bed_file"])
            for row in manifest_rows:
                writer.writerow([row["sample"], row["input_file"]])

    event_fields = [
        "sample",
        "state",
        "chrom",
        "start",
        "end",
        "size_mb",
        "cytoband",
        "n_bins",
        "mean_log2",
        "median_log2",
        "estimated_total_copy_number",
        "copy_code",
        "molecular_piece",
        "cna_shorthand",
        "source",
        "caller",
        "raw_call",
        "model_copy_number",
        "subclone_status",
        "classification_source",
    ]
    with event_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=event_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(all_events)

    with sample_path.open("w", newline="") as handle:
        fields = [
            "sample",
            "n_cna_events",
            "molecular_cytogenomic_notation",
            "cna_shorthand",
            "caller",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()

        for sample in sorted(sample_to_events):
            events = sample_to_events[sample]
            events.sort(key=lambda row: (chrom_key(row["chrom"]), int(row["start"])))

            pieces = [event["molecular_piece"] for event in events]
            shorthand_parts = [event["cna_shorthand"] for event in events]

            if pieces:
                notation = f"{args.prefix}[{args.genome_label}] " + "; ".join(pieces)
                shorthand = ", ".join(shorthand_parts)
            else:
                notation = (
                    f"{args.prefix}[{args.genome_label}] "
                    "no high-confidence CNA by current filters"
                )
                shorthand = "no high-confidence CNA"

            writer.writerow(
                {
                    "sample": sample,
                    "n_cna_events": len(events),
                    "molecular_cytogenomic_notation": notation,
                    "cna_shorthand": shorthand,
                    "caller": sample_to_caller[sample],
                }
            )

    print(f"Mode: {args.caller}")
    print(f"Input dir: {args.input_dir if args.input_dir else 'not used'}")
    print(f"Input files: {len(input_files)}")
    print(f"Samples: {len(sample_to_events)}")
    if args.caller == "ichorcna":
        print("ichorCNA input policy: per-sample *.seg.txt only; *.cna.seg and aggregate GISTIC files ignored")
        print("ichorCNA classification: Corrected_Call/Corrected_Copy_Number preferred when available")
    print(f"Wrote: {input_manifest_path}")
    if legacy_manifest_path is not None:
        print(f"Wrote: {legacy_manifest_path}")
    print(f"Wrote: {event_path}")
    print(f"Wrote: {sample_path}")


if __name__ == "__main__":
    main()
