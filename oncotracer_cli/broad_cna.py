"""Experimental, independent broad read-depth screen for sparse CNA samples.

This is a research screen, not a clinically calibrated caller. It does not use
QDNAseq/ichorCNA normalized copy numbers, segments, or sample disease labels.
Raw counts can be shared with either counting workflow. Results never enter
the authoritative CNA event table or diagnostic interpretation.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import html
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

from .runtime import atomic_write_text

METHOD = "experimental-broad-read-depth-v1"
MIN_TOTAL = 5000
MIN_REGION = 200
MIN_REFERENCE_CHROMOSOMES = 8
NOTICE = (
    "Experimental research screen: approximate uncertainty, no validated sensitivity or specificity. "
    "Candidate broad changes require independent confirmation. No supported broad change does not mean normal. "
    "This screen assumes most autosomes provide a stable relative baseline; it cannot estimate absolute "
    "ploidy, tumor fraction, focal deletions, or distinguish germline from somatic changes. "
    "Primary CNA assessability remains unchanged."
)


def read_wig(path):
    """Return autosomal fixedStep bins, with one-based inclusive coordinates."""
    values = {}
    opener = gzip.open if str(path).endswith(".gz") else open
    chrom, position, step, span = None, 0, 0, 0
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith(("track", "#", "browser")):
                continue
            if line.startswith("fixedStep"):
                fields = dict(part.split("=", 1) for part in line.split()[1:])
                chrom = fields["chrom"].removeprefix("chr")
                position, step = int(fields["start"]), int(fields["step"])
                span = int(fields.get("span", step))
                continue
            if chrom is None or min(position, step, span) <= 0:
                raise ValueError("Invalid fixedStep count/reference WIG")
            value = float(line)
            if chrom in {str(i) for i in range(1, 23)}:
                values[(chrom, position, position + span - 1)] = value
            position += step
    return values


def export_wig_counts(wig, gc_wig, map_wig, output):
    """Save portable raw evidence; no absolute reference paths in the table."""
    counts, gc, mapping = (read_wig(p) for p in (wig, gc_wig, map_wig))
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["chromosome", "start", "end", "gc", "mappability", "bases", "reads"])
        for key, count in counts.items():
            writer.writerow([*key, gc.get(key, "nan"), mapping.get(key, "nan"), 1, count])


def _arms(path):
    arms = defaultdict(dict)
    with gzip.open(path, "rt") as handle:
        for line in handle:
            chrom, start, end, band, *_ = line.split()
            chrom = chrom.removeprefix("chr")
            if band[0] in {"p", "q"}:
                old = arms[chrom].get(band[0], (int(start) + 1, int(end)))
                arms[chrom][band[0]] = (min(old[0], int(start) + 1), max(old[1], int(end)))
    return arms


def _mad(values):
    center = median(values)
    return 1.4826 * median(abs(value - center) for value in values)


def screen_bins(rows, arms=None):
    """GC/mappability matched, leave-one-chromosome-out baseline and broad tests.

    Bin GC/map/base fractions must be in [0,1]. A broad region must have 200
    observed AND expected reads; zeros alone can never produce a loss call.
    A 0.15 log2 uncertainty floor and empirical block dispersion guard against
    treating nominal Poisson counting precision as biological precision.
    """
    bins = []
    seen = set()
    for row in rows:
        chrom = str(row["chromosome"]).removeprefix("chr")
        try:
            start, end = int(row["start"]), int(row["end"])
            reads, gc, mapping, bases = (float(row[k]) for k in ("reads", "gc", "mappability", "bases"))
        except (ValueError, TypeError):
            # R reference exports can contain NA, never treat it as zero coverage.
            continue
        if chrom not in {str(i) for i in range(1, 23)} or not all(map(math.isfinite, (reads, gc, mapping, bases))):
            continue
        if not (reads >= 0 and .2 <= gc <= .8 and .75 <= mapping <= 1 and .75 <= bases <= 1 and 0 < start <= end):
            continue
        key = (chrom, start, end)
        if key in seen:
            raise ValueError("Duplicate raw count bins cannot be screened")
        seen.add(key)
        bins.append(dict(chromosome=chrom, start=start, end=end, reads=reads,
                         exposure=(end-start+1)*mapping*bases,
                         stratum=(min(15, int(gc*20)), min(9, int(mapping*10)))))
    bins.sort(key=lambda b: (int(b["chromosome"]), b["start"]))
    for a, b in zip(bins, bins[1:]):
        if a["chromosome"] == b["chromosome"] and a["end"] >= b["start"]:
            raise ValueError("Overlapping raw count bins cannot be screened")
    total = sum(b["reads"] for b in bins)
    result = {"status": "not_assessable", "reason": "Insufficient usable raw read support",
              "total_usable_reads": total, "usable_bins": len(bins), "regions": []}
    chroms = sorted({b["chromosome"] for b in bins}, key=int)
    if total < MIN_TOTAL or len(bins) < 100 or len(chroms) < 10:
        return result
    grouped = defaultdict(lambda: [0., 0., 0])
    for b in bins:
        value = grouped[(b["chromosome"], b["stratum"])]
        value[0] += b["reads"]; value[1] += b["exposure"]; value[2] += 1
    for chrom in chroms:
        target = [b for b in bins if b["chromosome"] == chrom]
        expectations = {}
        for stratum in {b["stratum"] for b in target}:
            refs = [v for (c, s), v in grouped.items()
                    if c != chrom and s == stratum and v[0] >= 20 and v[2] >= 10]
            if len(refs) < MIN_REFERENCE_CHROMOSOMES or sum(v[0] for v in refs) < 500:
                continue
            rates = [v[0]/v[1] for v in refs]
            rate = median(rates)
            # Reference variation is retained, not divided away as if bias were random.
            uncertainty = max(.15, _mad([math.log2(r/rate) for r in rates]))
            expectations[stratum] = (rate, uncertainty)
        matched = [dict(b, expected=b["exposure"]*expectations[b["stratum"]][0],
                        uncertainty=expectations[b["stratum"]][1])
                   for b in target if b["stratum"] in expectations]
        if not matched or len(matched)/len(target) < .8:
            result["regions"].append({"region": chrom, "status": "not_assessable", "reason": "Insufficient matched reference bins"})
            continue
        # Model extra counting variation with independent, approximately 100-read blocks.
        residuals, obs, exp = [], 0., 0.
        for b in matched:
            obs += b["reads"]; exp += b["expected"]
            if exp >= 100:
                residuals.append((obs-exp)/math.sqrt(exp)); obs, exp = 0., 0.
        dispersion = max(1., _mad(residuals)**2) if len(residuals) >= 10 else 4.
        arm_bins = {}
        for arm, (start, end) in (arms or {}).get(chrom, {}).items():
            arm_bins[arm] = [b for b in matched if start <= (b["start"]+b["end"])/2 <= end]
        def supported(values):
            return (len(values) >= 10 and sum(b["reads"] for b in values) >= MIN_REGION
                    and sum(b["expected"] for b in values) >= MIN_REGION
                    and sum(b["end"]-b["start"]+1 for b in values) >= 10_000_000)
        # Adapt resolution without overlapping arm and chromosome hypothesis tests.
        regions = [(chrom + a, v) for a, v in sorted(arm_bins.items())] if len(arm_bins) == 2 and all(map(supported, arm_bins.values())) else [(chrom, matched)]
        for name, values in regions:
            region = {"region": name, "start": min(b["start"] for b in values), "end": max(b["end"] for b in values),
                      "usable_bins": len(values), "observed_reads": sum(b["reads"] for b in values),
                      "expected_reads": sum(b["expected"] for b in values), "status": "not_assessable",
                      "reason": "Too little support at chromosome/arm scale"}
            if supported(values):
                ratio = math.log2(region["observed_reads"]/region["expected_reads"])
                baseline_se = sum(b["expected"]*b["uncertainty"] for b in values)/region["expected_reads"]
                se = math.sqrt(dispersion/region["observed_reads"]/math.log(2)**2 + baseline_se**2)
                region.update(log2_ratio=ratio, lower_99=ratio-2.576*se, upper_99=ratio+2.576*se,
                              dispersion=dispersion, approximate_p=math.erfc(abs(ratio)/se/math.sqrt(2)),
                              status="no_supported_broad_change", reason="")
            result["regions"].append(region)
    tested = sorted([r for r in result["regions"] if "approximate_p" in r], key=lambda r: r["approximate_p"])
    q = 1.
    for rank in range(len(tested), 0, -1):
        r = tested[rank-1]; q = min(q, r["approximate_p"]*len(tested)/rank)
        r["approximate_q"] = q
        if q <= .01 and r["lower_99"] > .3:
            r["status"] = "candidate_broad_gain"
        elif q <= .01 and r["upper_99"] < -.3:
            r["status"] = "candidate_broad_loss"
    if len(tested) < 10 or sum(r["status"].startswith("candidate") for r in tested) > len(tested)/3:
        result["reason"] = "Insufficient or unstable genome-wide relative baseline"
        for r in result["regions"]:
            r.update(status="not_assessable", reason=result["reason"])
    else:
        result.update(status="experimental_screen_available", reason="")
    return result


def publish_screen(outdir, counts_dir, caller, assessment, cytoband):
    """Always regenerate from current evidence, retaining primary failure status."""
    rows = [r for r in assessment["samples"] if r["assessment"] != "assessable"]
    directory = outdir / "06_workflow_summary/experimental_broad_cna"
    directory.mkdir(parents=True, exist_ok=True)
    arms = _arms(cytoband)
    inputs, samples = {}, []
    wide = counts_dir / "broad_cna_counts.tsv.gz"
    table = None
    if caller == "qdnaseq" and wide.is_file():
        with gzip.open(wide, "rt") as handle:
            table = list(csv.DictReader(handle, delimiter="\t"))
        inputs[wide.name] = hashlib.sha256(wide.read_bytes()).hexdigest()
    for sample in rows:
        name = sample["sample"]
        path = counts_dir / "diagnostics/broad_counts" / (name + ".tsv.gz")
        raw = []
        if table and "sample:" + name in table[0]:
            raw = [dict(r, reads=r["sample:" + name]) for r in table]
        elif caller == "ichorcna" and path.is_file():
            with gzip.open(path, "rt") as handle:
                raw = list(csv.DictReader(handle, delimiter="\t"))
            inputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        result = screen_bins(raw, arms)
        result["sample"] = name
        if not raw:
            result["reason"] = "Raw count evidence unavailable; resume the CNA stage to regenerate it"
        samples.append(result)
    data = {"schema": METHOD, "experimental": True, "notice": NOTICE,
            "policy": {"minimum_total_reads": MIN_TOTAL, "minimum_region_reads": MIN_REGION,
                       "minimum_reference_chromosomes": MIN_REFERENCE_CHROMOSOMES,
                       "minimum_region_bases": 10_000_000, "interval": "approximate 99%",
                       "log2_uncertainty_floor": .15, "candidate_log2_margin": .3, "maximum_approximate_q": .01},
            "input_sha256": inputs, "samples": samples}
    atomic_write_text(directory/"screen.json", json.dumps(data, indent=2, sort_keys=True, allow_nan=False)+"\n")
    fields = ["sample", "region", "status", "observed_reads", "expected_reads", "log2_ratio", "lower_99", "upper_99", "approximate_q", "reason"]
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    for sample in samples:
        for r in sample["regions"] or [{"status": sample["status"], "reason": sample["reason"]}]:
            writer.writerow(dict(r, sample=sample["sample"]))
    atomic_write_text(directory/"regions.tsv", buffer.getvalue())
    def cell(v):
        return html.escape(f"{v:.3f}" if isinstance(v, float) else str(v))
    body = ['<!doctype html><meta charset="utf-8"><title>Experimental broad CNA screen</title>',
            '<style>body{font:16px sans-serif;margin:2em}td,th{padding:.5em;text-align:left;border-bottom:1px solid #ddd}</style>',
            '<h1>Experimental broad CNA screen</h1><p>'+NOTICE+'</p><p>Raw counts are matched by GC and mappability to other autosomes. '
            'Arms are tested when sufficiently supported; otherwise the whole chromosome is tested. Missing reads alone cannot yield a deletion.</p>']
    for sample in samples:
        body.append('<h2>'+cell(sample["sample"])+'</h2><p>'+cell(sample["status"].replace('_',' '))+': '+cell(sample['reason'])+'</p>')
        body.append('<table><tr><th>Region</th><th>Screen</th><th>Reads</th><th>log2 ratio</th><th>Approximate 99% interval</th><th>Reason</th></tr>')
        for r in sample["regions"]:
            interval = f'{r["lower_99"]:.3f} to {r["upper_99"]:.3f}' if "lower_99" in r else "unavailable"
            values = (r['region'], r['status'].replace('_', ' '), r.get('observed_reads', ''), r.get('log2_ratio', ''), interval, r['reason'])
            body.append('<tr>'+''.join('<td>'+cell(v)+'</td>' for v in values)+'</tr>')
        body.append('</table>')
    atomic_write_text(directory/"index.html", '\n'.join(body))
    assessment["experimental_broad_screen"] = "06_workflow_summary/experimental_broad_cna/index.html"
    return data
