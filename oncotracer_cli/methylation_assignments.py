"""Explicit per-sample CpG classifiers, independent of CNA sample roles."""
from __future__ import annotations

import csv
from dataclasses import replace
import io
from pathlib import Path

from .runtime import OncoTracerError, sha256_file


def validate(assignments, names):
    names = list(names)
    if not isinstance(assignments, dict) or set(assignments) != set(names):
        raise OncoTracerError("Methylation assignments must contain exactly one row for every selected sample.")
    if any(not isinstance(value, str) or value not in {"marlin", "sturgeon", ""}
           for value in assignments.values()):
        raise OncoTracerError("Choose marlin, sturgeon or skip for each methylation sample.")
    if not any(assignments.values()):
        raise OncoTracerError("No samples are assigned to methylation. Choose CNA without methylation, or assign at least one sample.")
    return {name: assignments[name] for name in names}


def read(path, names):
    path = Path(path).expanduser().resolve()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["sample", "classifier"]:
            raise OncoTracerError("Methylation assignments need a TSV with sample and classifier columns.")
        assignments = {}
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise OncoTracerError("Methylation assignments have a wrong column count.")
            name, classifier = row["sample"].strip(), row["classifier"].strip().lower()
            if not name or name in assignments:
                raise OncoTracerError("Methylation assignments contain an empty or repeated sample name.")
            assignments[name] = "" if classifier == "skip" else classifier
    return validate(assignments, names)


def render(assignments):
    handle = io.StringIO(newline="")
    writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
    writer.writerow(["sample", "classifier"])
    writer.writerows((name, classifier or "skip") for name, classifier in assignments.items())
    return handle.getvalue()


def resolve(config, *, mode, gpu_override=None):
    from .engine import parse_ont_samples
    from .methylation import resolve_methylation_request
    from .methylation_routing import TissueMethylationRequest
    from .methylation_selection import excluded_samples, excluded_record
    from .rockfish import resolve_caller
    from .sample_resources import memory_gb

    if mode != "ont":
        raise OncoTracerError("Per-sample methylation classification requires ONT inputs.")
    path = config.get("methylation_sample_metadata")
    if not path:
        raise OncoTracerError("Provide methylation_sample_metadata with each sample's classifier assignment.")
    path = Path(str(path)).expanduser().resolve()
    names = [sample.sample for sample in parse_ont_samples(config)]
    assignments = read(path, names)
    excluded = excluded_samples(config, names)
    for name in excluded:
        if assignments[name]:
            raise OncoTracerError(f"Conflicting methylation settings for {name}: assigned to a classifier and excluded.")
    requests, records = [], []
    for name, classifier in assignments.items():
        if not classifier:
            records.append({**excluded_record(name), "tissue": "", "classifier": None})
            continue
        # Keep the original sample identities while selecting this library's signal.
        child = {**config, "methylation_classifier": classifier,
                 "methylation_excluded_samples": [other for other in names if other != name]}
        request = resolve_methylation_request(child, mode=mode, enabled_override=True,
                                              gpu_override=gpu_override)
        # The routing runner supplies exactly this sample to the child branch.
        requests.append((name, replace(request, excluded_samples=())))
        records.append({"sample": name, "tissue": "", "classifier": classifier,
                        "methylation_enabled": True, "status": "pending",
                        "reason": "User-selected classifier"})
    return TissueMethylationRequest(tuple(records), tuple(requests), path, sha256_file(path),
        classifier="assigned", gpu=any(child.gpu for _, child in requests),
        modbam_alignment=requests[0][1].modbam_alignment,
        memory=memory_gb(config.get("analysis_memory", "auto")),
        caller=resolve_caller(config.get("methylation_caller")))


def summary(config, names):
    """Display saved assignments and exclusions without re-calling data."""
    from .methylation_selection import excluded_samples
    from .methylation_routing import classifier_for_tissue
    if not (config.get("methylation") or config.get("methylation_only")):
        return {name: "off" for name in names}
    classifier = config.get("methylation_classifier")
    if classifier == "assigned":
        return {name: value or "skip" for name, value in read(config["methylation_sample_metadata"], names).items()}
    if classifier in {"auto", "tissue"}:
        from .methylation_selection import row_enabled
        with Path(config["methylation_sample_metadata"]).open(newline="") as handle:
            records = list(csv.DictReader(handle, delimiter="\t"))
        excluded = excluded_samples(config, names)
        return {row["sample"]: (classifier_for_tissue(row["tissue"]) or "skip")
                if row["sample"] not in excluded and row_enabled(row.get("methylation", ""), row["sample"])
                else "skip" for row in records}
    excluded = excluded_samples(config, names)
    return {name: "skip" if name in excluded else str(classifier) for name in names}
