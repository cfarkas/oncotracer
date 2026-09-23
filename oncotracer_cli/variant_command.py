"""Native variant calling from existing BAMs, without repeating alignment or CNA."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import textwrap
from pathlib import Path

from . import __version__
from .engine import Toolchain, _safe_sample, write_run_manifest
from .output_safety import claim_output_run, inspect_output_target
from .runtime import (
    CommandRunner, OncoTracerError, StageLedger, atomic_write_json,
    atomic_write_text, atomic_write_workflow_summary, load_flat_yaml,
    require_file, sha256_file, utc_now,
)
from .variants import SCHEMA, preflight_variant_tools, resolve_variant_request, run_variants, variant_plan

COMMAND_SCHEMA = "oncotracer-existing-bam-variants-v1"
MARKER = Path(".oncotracer-native/standalone-variants.json")


def _path(value, parent: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise OncoTracerError(f"Specify {label}")
    path = Path(value).expanduser()
    return path if path.is_absolute() else parent / path


def read_bam_manifest(path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    """Resolve relative BAM paths beside the manifest; never infer pairing."""
    path = require_file(path, "Variant BAM manifest")
    bams, statuses, identities = {}, {}, set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not {"sample", "bam", "status"} <= set(headers):
            raise OncoTracerError("Variant BAM manifest needs unique TSV columns sample, bam, status")
        for number, row in enumerate(reader, 2):
            if None in row:
                raise OncoTracerError(f"Variant BAM manifest row {number} has extra fields")
            sample = _safe_sample(str(row.get("sample") or ""))
            if sample in bams:
                raise OncoTracerError(f"Duplicate sample in variant BAM manifest: {sample}")
            status = str(row.get("status") or "").strip().lower()
            if status not in {"tumor", "normal"}:
                raise OncoTracerError(f"Variant BAM manifest row {number}: status must be tumor or normal")
            bam = require_file(_path(row.get("bam"), path.parent, f"BAM at manifest row {number}"), "Variant input BAM").resolve()
            if bam.suffix.lower() != ".bam":
                raise OncoTracerError(f"Variant input must be a BAM file at manifest row {number}")
            stat = bam.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in identities:
                raise OncoTracerError("A physical BAM is assigned to multiple manifest rows; use one sample per BAM")
            identities.add(identity)
            bams[sample], statuses[sample] = bam, status
    if not bams:
        raise OncoTracerError("Variant BAM manifest contains no samples")
    return bams, statuses


def _snapshot(paths) -> dict[str, tuple[int, int, int, int]]:
    result = {}
    for path in paths:
        stat = path.stat()
        result[str(path)] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def _check_overlap(outdir: Path, files, directories) -> None:
    resolved = outdir.resolve()
    for path in files:
        source = path.resolve()
        if source == resolved or resolved in source.parents or source in resolved.parents:
            raise OncoTracerError(f"Variant output and input paths overlap: {path}")
    for path in directories:
        if path is not None:
            source = path.resolve()
            if source == resolved or resolved in source.parents or source in resolved.parents:
                raise OncoTracerError(f"Variant output overlaps an input/tool directory: {path}")


def _standalone_target(outdir: Path) -> dict | None:
    """An authenticated CNA tree must never become a variant-only run."""
    if not outdir.exists():
        return None
    marker = outdir / MARKER
    conflicting = [p for p in outdir.iterdir() if p.name.startswith(("01_", "02_", "03_", "04_", "05_", "07_"))]
    if conflicting:
        raise OncoTracerError("Existing CNA/methylation outputs need a separate outdir for standalone variants")
    if not marker.exists():
        if (outdir / "08_variants").exists() or (outdir / "06_workflow_summary").exists():
            raise OncoTracerError("Existing analysis outputs lack standalone variant provenance; choose a new outdir")
        return None
    try:
        record = json.loads(marker.read_text())
        if not isinstance(record, dict) or record.get("schema") != COMMAND_SCHEMA or not record.get("output_id"):
            raise ValueError("unrecognized marker")
    except (OSError, ValueError) as error:
        raise OncoTracerError(f"Invalid standalone variant provenance: {marker}") from error
    return record



def _partial_failure_cause(status) -> str:
    """Summarize incomplete work without changing machine-readable statuses."""
    reasons = []
    def add(label, record):
        state = record.get('status', '')
        if state not in {'not_assessed', 'failed', 'partial_failure'}:
            return
        message = f"{label} {state.replace('_', ' ')}"
        detail = record.get('reason') or record.get('error')
        if detail:
            message += ': ' + textwrap.shorten(' '.join(str(detail).split()), width=180, placeholder='...')
        if message not in reasons:
            reasons.append(message)
    for sample in status.get('samples', []):
        callers = sample.get('callers', [])
        for caller in callers:
            assessments = caller.get('assessments', {})
            for name, assessment in assessments.items():
                add(name.upper(), assessment)
            add('ANNOVAR', caller.get('annotation', {}))
            if caller.get('status') == 'failed':
                add(str(caller.get('caller', 'Caller')), caller)
        if not callers:
            add('Sample analysis', sample)
    if not reasons:
        return 'One or more callers or assessments remain incomplete.'
    message = '; '.join(reasons[:3])
    if len(reasons) > 3:
        message += f'; {len(reasons) - 3} additional incomplete steps listed in the saved results'
    return message


def command_variants(args) -> int:
    config_path = require_file(Path(args.config).expanduser(), "Variant configuration").resolve()
    digest = sha256_file(config_path)
    config = load_flat_yaml(config_path)
    if sha256_file(config_path) != digest:
        raise OncoTracerError("Variant configuration changed while being read")
    mode = str(config.get("mode") or "").strip().lower()
    request = resolve_variant_request(config, mode=mode)
    if request is None:
        raise OncoTracerError("Set run_variants: true in the variant configuration")
    outdir = Path(os.path.abspath(_path(config.get("outdir"), config_path.parent, "outdir")))
    manifest = require_file(_path(config.get("variant_bam_manifest"), config_path.parent, "variant_bam_manifest"), "Variant BAM manifest").resolve()
    manifest_digest = sha256_file(manifest)
    bams, statuses = read_bam_manifest(manifest)
    from .strelka2 import validate_samples
    validate_samples(request, bams, statuses)
    if sha256_file(manifest) != manifest_digest:
        raise OncoTracerError("Variant BAM manifest changed while being read")
    reference = require_file(_path(config.get("variant_reference"), config_path.parent, "variant_reference"), "Variant reference FASTA").resolve()
    with reference.open("rb") as handle:
        if handle.read(1) != b">":
            raise OncoTracerError("variant_reference must be an uncompressed FASTA beginning with >")
    value = args.threads if args.threads is not None else config.get("threads", min(os.cpu_count() or 1, 16))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OncoTracerError("threads must be a positive integer")
    threads = value
    force = config.get("force", False) if args.force is None else args.force
    if not isinstance(force, bool):
        raise OncoTracerError("force must be true or false")
    files = [config_path, manifest, reference, *bams.values()]
    fai = Path(str(reference) + ".fai")
    if fai.exists():
        files.append(require_file(fai, "Reference FASTA index"))
    if request.targets_bed:
        files.append(request.targets_bed)
    _check_overlap(outdir, files, (request.clair3_model, request.tool_prefix, request.strelka_prefix, request.annovar_dir, request.annovar_db, request.ffperase_root, request.ffperase_models, request.ffperase_prefix))
    if request.varlociraptor_scenario:
        files.append(request.varlociraptor_scenario)
    input_snapshot = _snapshot(files)
    # Variant-only execution uses packaged Python code and existing external tools;
    # no embedded reference/scripts payload needs extraction, even in a zipapp.
    inspect_output_target(outdir)
    _standalone_target(outdir)
    plan = {"schema": COMMAND_SCHEMA, "engine": "native", "nextflow_used": False,
            "analysis": "variants", "mode": mode, "config": str(config_path), "outdir": str(outdir),
            "threads": threads, "force": force, "reference": str(reference), "bam_manifest": str(manifest),
            "samples": [{"sample": sample, "bam": str(bam), "status": statuses[sample]} for sample, bam in bams.items()],
            "variants": variant_plan(request), "input_validation": "paths and metadata; BAM contents are validated at execution"}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    # Tool discovery is read-only and precedes output acquisition. No implicit
    # installation, annotation download, or modification of the source BAMs.
    preflight_variant_tools(request, Toolchain.from_environment())

    def validate_inputs():
        if sha256_file(config_path) != digest or sha256_file(manifest) != manifest_digest or _snapshot(files) != input_snapshot:
            raise OncoTracerError("Variant configuration, manifest, reference or BAM inputs changed during execution")

    with claim_output_run(outdir, config_path=config_path, expected_config_sha256=digest) as lease:
        previous = _standalone_target(outdir)
        if previous and previous["output_id"] != lease.owner["output_id"]:
            raise OncoTracerError("Standalone variant provenance does not match output ownership")
        validate_inputs()
        native = outdir / ".oncotracer-native"
        atomic_write_json(outdir / MARKER, {"schema": COMMAND_SCHEMA, "output_id": lease.owner["output_id"]})
        atomic_write_json(native / "variant-inputs.json", {**plan, "config_sha256": digest, "manifest_sha256": manifest_digest,
                                                          "input_metadata": input_snapshot})
        atomic_write_text(native / "engine.txt", f"engine=native\nnextflow_used=false\nversion={__version__}\nanalysis=variants\n")
        cache = native / "runtime-cache"
        cache.mkdir(exist_ok=True)
        trace = native / "trace.tsv"
        with tempfile.TemporaryDirectory(prefix="variants-", dir=cache) as temporary:
            toolchain = Toolchain.from_environment(runtime_cache=Path(temporary))
            runner = CommandRunner(trace, protected_environment=toolchain.environment("core"),
                                   validators=(validate_inputs, toolchain.validate_environment))
            ledger = StageLedger(native / "state.json")
            try:
                status = run_variants(request, bams, reference, outdir, runner, ledger,
                                      threads=threads, force=force, sample_statuses=statuses, toolchain=toolchain)
            except (OSError, OncoTracerError, ValueError) as error:
                status = {"schema": SCHEMA, "overall_status": "failed", "completed_samples": [],
                          "failed_samples": list(bams), "error": str(error), "samples": [], "finished_at": utc_now()}
                # The failure may concern an unowned or unsafe stage08 path.
                # Publish only to the authenticated native state directory.
                lease.validate()
                status["status_file"] = str(native / "variant_failure.json")
                atomic_write_json(Path(status["status_file"]), status)
        validate_inputs()
        lease.validate()
        summary = {"oncotracer_version": __version__, "engine": "native", "nextflow_used": False,
                   "analysis": "variants", "mode": mode, "outdir": str(outdir), "completed_at": utc_now(),
                   "workflow_status": status["overall_status"], "cna_status": "not_requested",
                   "methylation_status": "not_requested", "cna_classifier_status": "not_requested",
                   "variant_status": status["overall_status"], "variant_status_file": status.get("status_file", str(outdir / "08_variants/variant_status.json")),
                   "variant_completed_samples": status.get("completed_samples", []),
                   "variant_failed_samples": status.get("failed_samples", []),
                   "completed_samples": status.get("completed_samples", []), "failed_samples": status.get("failed_samples", []),
                   "variant_bam_manifest": str(manifest), "variant_reference": str(reference)}
        if status.get("error"):
            summary["variant_error"] = status["error"]
        atomic_write_workflow_summary(outdir / "06_workflow_summary", summary)
        write_run_manifest(outdir, config_path, trace)
        from .results import write_results_index
        write_results_index(outdir)
        lease.validate()
    if status['overall_status'] == 'partial_failure':
        print(f"PARTIAL FAILURE: {_partial_failure_cause(status)}\nCompleted outputs are preserved: {outdir}\nResults: {outdir / 'index.html'}")
        return 2
    print(f"Native variant analysis {status['overall_status']}: {outdir}\nResults: {outdir / 'index.html'}")
    if status["overall_status"] != "complete":
        raise OncoTracerError(f"Variant analysis is incomplete; completed outputs and failure details are preserved under {outdir}")
    return 0


def add_variants_command(subparsers) -> None:
    parser = subparsers.add_parser("variants", help="Call small variants from existing BAMs without repeating CNA/alignment")
    parser.add_argument("--config", required=True, help="flat YAML with mode, outdir, variant_bam_manifest, variant_reference and variant options")
    parser.add_argument("--threads", type=int, help="override CPU threads from the configuration")
    parser.add_argument("--force", action="store_true", default=None, help="rerun owned variant outputs; never permits overwriting foreign files")
    parser.add_argument("--dry-run", action="store_true", help="validate paths/config and print a plan without writing files or running tools")
    parser.set_defaults(func=command_variants)
