"""Route mixed ONT cohorts from explicit tissue metadata, never predictions."""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import html
from pathlib import Path
import re

from .runtime import OncoTracerError, atomic_write_json, atomic_write_text, sha256_file, utc_now


def classifier_for_tissue(value):
    tissue = re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    if tissue in {"peripheral blood", "pb", "pbmc", "pbmcs", "peripheral blood mononuclear cells",
                  "bone marrow", "marrow", "bm", "peripheral blood / bone marrow", "peripheral blood/bone marrow"}:
        return "marlin"
    if tissue in {"cns", "cns tissue", "central nervous system", "brain", "spinal cord", "cerebrospinal fluid", "csf"}:
        return "sturgeon"
    return None


@dataclass(frozen=True)
class TissueMethylationRequest:
    assignments: tuple
    requests: tuple
    metadata: Path
    metadata_sha256: str
    classifier: str = "tissue"
    gpu: bool = False
    modbam_alignment: str = "preserve"
    memory: float | str = "auto"


def resolve(config, *, mode, gpu_override=None):
    from .engine import parse_ont_samples
    from .methylation import resolve_methylation_request
    if mode != "ont":
        raise OncoTracerError("Tissue-routed methylation requires ONT modified-base BAMs")
    value = config.get("methylation_sample_metadata")
    if not value:
        raise OncoTracerError("Tissue routing requires methylation_sample_metadata: a TSV with sample,tissue,modbam columns")
    path = Path(str(value)).expanduser().resolve()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not {"sample", "tissue", "modbam"}.issubset(reader.fieldnames or []):
            raise OncoTracerError("Methylation metadata requires sample, tissue and modbam columns")
        records = list(reader)
    samples = parse_ont_samples(config)
    names = {sample.sample for sample in samples}
    if len(records) != len({r["sample"] for r in records}) or {r["sample"] for r in records} != names:
        raise OncoTracerError("Methylation metadata must contain exactly one row for every selected ONT sample")
    requests, assignments, seen_bams = [], [], set()
    for row in records:
        classifier = classifier_for_tissue(row["tissue"])
        assignment = dict(sample=row["sample"], tissue=row["tissue"], classifier=classifier,
                          status="pending" if classifier else "not_applicable",
                          reason="Tissue assignment" if classifier else "Tissue is outside peripheral blood, marrow and CNS, or is unspecified")
        assignments.append(assignment)
        if not classifier:
            continue
        if not row["modbam"].strip():
            raise OncoTracerError("Missing modified-base BAM for " + row["sample"])
        bam = Path(row["modbam"]).expanduser()
        if not bam.is_absolute():
            bam = path.parent / bam
        bam = bam.resolve()
        if not bam.is_file() or bam.suffix.lower() != ".bam":
            raise OncoTracerError("Missing modified-base BAM for " + row["sample"])
        identity = (bam.stat().st_dev, bam.stat().st_ino)
        if identity in seen_bams:
            raise OncoTracerError("One modified-base BAM cannot represent multiple selected samples")
        seen_bams.add(identity)
        child = {**config, "methylation": True, "methylation_classifier": classifier,
                 "methylation_modbam": str(bam), "ont_named_samples": False,
                 "methylation_modbam_alignment": config.get("methylation_modbam_alignment", "preserve")}
        child.pop("methylation_pod5_dir", None)
        request = resolve_methylation_request(child, mode=mode, enabled_override=True, gpu_override=gpu_override)
        requests.append((row["sample"], request))
    from .sample_resources import memory_gb
    memory = memory_gb(config.get("analysis_memory", "auto"))
    return TissueMethylationRequest(tuple(assignments), tuple(requests), path, sha256_file(path),
                                   gpu=any(child.gpu for _, child in requests), memory=memory)


def plan(request):
    from .methylation import methylation_plan
    return {"enabled": True, "classifier": "tissue", "input_kind": "modbam",
            "metadata": str(request.metadata), "metadata_sha256": request.metadata_sha256,
            "assignments": list(request.assignments), "threads_per_sample": 1, "memory_budget_gb": request.memory,
            "stages": ["tissue-routing", "reference-CpG-processing", "eligible-classifier"],
            "samples": [{"sample": name, **methylation_plan(child)} for name, child in request.requests]}


def publish(outdir, request, records):
    from .methylation import _status_payload, CPG_POLICY
    from types import SimpleNamespace
    description = SimpleNamespace(classifier="tissue", modbam_alignment=request.modbam_alignment,
                                  gpu=request.gpu, dorado_device="cpu")
    payload = _status_payload(description, records)
    payload.update(metadata=str(request.metadata), metadata_sha256=request.metadata_sha256,
                   not_applicable_samples=[r["sample"] for r in records if r["status"] == "not_applicable"],
                   threads_per_sample=1)
    directory = outdir / "07_methylation"
    atomic_write_json(directory / "methylation_status.json", payload)
    atomic_write_json(directory / "methylation_provenance.json",
                      {"schema": "oncotracer-tissue-methylation-v1", "created_at": utc_now(),
                       "cpg_processing_policy": CPG_POLICY, "routing": plan(request),
                       "samples": records, "overall_status": payload["overall_status"]})
    cells = "".join("<tr>" + "".join("<td>" + html.escape(str(r.get(k, ""))) + "</td>"
                                     for k in ("sample", "tissue", "classifier", "status", "reason")) + "</tr>" for r in records)
    atomic_write_text(directory / "index.html", '<!doctype html><meta charset="utf-8"><title>Methylation</title>'
                      '<h1>Tissue-routed methylation</h1><p>MARLIN: peripheral blood or marrow. Sturgeon: CNS. '
                      'Unspecified or other tissues remain unassigned. Scores do not establish diagnoses.</p>'
                      '<table><tr><th>Sample</th><th>Tissue</th><th>Model</th><th>Status</th><th>Reason</th></tr>' + cells + '</table>')
    return payload


def run(root, request, samples, reference, outdir, runner, ledger, *, threads, force):
    from .methylation import run_methylation
    from .runtime import CommandRunner, StageLedger
    if sha256_file(request.metadata) != request.metadata_sha256:
        raise OncoTracerError("Methylation metadata changed after validation")
    selected = {s.sample: s for s in samples}
    records = {r["sample"]: dict(r) for r in request.assignments}
    if set(records) != set(selected):
        raise OncoTracerError("Tissue assignments do not match the selected samples")
    publish(outdir, request, list(records.values()))

    from .sample_resources import allocate, worker_environment
    workers = max(1, min(threads, len(request.requests) or 1))
    environment = dict(runner.environment)
    if request.requests:
        allocation = allocate(threads, len(request.requests), parallel=workers,
                              memory=request.memory, minimum_gb=8, preferred_threads=1)
        workers = min(workers, allocation['total_threads'])
        if workers != allocation['parallel_samples']:
            allocation = allocate(threads, len(request.requests), parallel=workers,
                                  memory=request.memory, minimum_gb=8, preferred_threads=1)
        allocation.update(threads_per_sample=1, allocated_threads=workers,
                          unallocated_threads=allocation['total_threads']-workers)
        environment.update(worker_environment(allocation))
        environment.update({key:'1' for key in ('TF_NUM_INTRAOP_THREADS','TF_NUM_INTEROP_THREADS',
                           'OMP_THREAD_LIMIT','R_DATATABLE_NUM_THREADS','RAYON_NUM_THREADS','NUMBA_NUM_THREADS')})
        atomic_write_json(outdir/'07_methylation/resource_allocation.json',allocation)

    def process(name, child):
        directory = outdir / "07_methylation" / "samples" / name
        directory.mkdir(parents=True, exist_ok=True)
        child_runner = CommandRunner(directory / "trace.tsv", validators=runner.validators,
                                     environment=environment, protected_environment=runner.protected_environment,
                                     dry_run=runner.dry_run, echo=runner.echo)
        child_ledger = StageLedger(directory / ".oncotracer-native/stages.json")
        try:
            status = run_methylation(root, child, [selected[name]], reference, directory,
                                     child_runner, child_ledger, threads=1, force=force)
            record = {**records[name], **status["samples"][0], "classifier": child.classifier}
            for key in ("classification", "bedmethyl", "probe_bed"):
                if record.get(key):
                    record[key] = str((directory / record[key]).relative_to(outdir))
            record["native_receipt"] = str((directory / "07_methylation/sample_status" / (name + ".json")).relative_to(outdir))
            record.pop("output_sha256", None)
            return record
        except (OSError, ValueError, OncoTracerError) as error:
            return {**records[name], "status": "failed", "error": str(error), "reason": str(error)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, name, child): name for name, child in request.requests}
        for future in as_completed(futures):
            name = futures[future]
            records[name] = future.result()
            publish(outdir, request, list(records.values()))
    return publish(outdir, request, list(records.values()))


def failure(outdir, request, error):
    records = [{**r, **({"status": "failed", "error": str(error)} if r["classifier"] else {})}
               for r in request.assignments]
    return publish(outdir, request, records)
