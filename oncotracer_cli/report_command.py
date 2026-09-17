"""Add interpretation reports to authenticated, completed native CNA results."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

from .classifier import DEFAULTS, _bool, run_native_classifier
from .engine import Toolchain
from .install_safety import managed_conda_runtime_lock
from .output_safety import (
    OUTPUT_LOCK_RELATIVE, OUTPUT_OWNER_RELATIVE, _canonical_path_sha256,
    _parse_owner, _reject_broad_target, _reject_symlink_components,
    _validate_reserved_tree, current_runtime_identity,
)
from .results import write_results_index
from .runtime import (
    CommandRunner, OncoTracerError, StageLedger, atomic_write_json,
    atomic_write_text, atomic_write_workflow_summary, load_flat_yaml,
    render_flat_yaml, require_file, runtime_root, sha256_file, utc_now,
)

SCHEMA = "oncotracer-report-generation-v1"
CNA_INPUTS = ("03_cna_codification/cna_events.tsv",
              "03_cna_codification/cna_cytogenomic_notation.tsv")
MANIFEST = "06_workflow_summary/native_run_manifest.json"


def _json(path: Path) -> dict:
    try:
        value = json.loads(require_file(path, "report provenance").read_text())
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value
    except (OSError, ValueError) as error:
        raise OncoTracerError(f"Invalid report provenance: {path}: {error}") from error


def _authenticate(outdir: Path, config_path: Path) -> tuple[dict, dict, dict]:
    _reject_broad_target(outdir)
    _reject_symlink_components(outdir)
    owner = _parse_owner(outdir / OUTPUT_OWNER_RELATIVE)
    if owner["canonical_path_sha256"] != _canonical_path_sha256(outdir):
        raise OncoTracerError("Native output owner path mismatch")
    _validate_reserved_tree(outdir)
    for name in ("03_cna_codification", "05_cna_classifier", "06_workflow_summary"):
        if any(path.is_symlink() for path in (outdir / name).rglob("*")):
            raise OncoTracerError(f"Reports will not follow symlinks in {name}")
    manifest = _json(outdir / MANIFEST)
    if (manifest.get("schema") != "oncotracer-native-run-manifest-v1"
            or manifest.get("engine") != "native"
            or (manifest.get("cna_status") or manifest.get("workflow_status")) != "complete"):
        raise OncoTracerError("Reports require a completed native CNA run manifest")
    if manifest.get("config_sha256") != sha256_file(config_path):
        raise OncoTracerError("Original run config changed; use report options without editing run.yml")
    records = manifest.get("files")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise OncoTracerError("Native manifest has invalid file records")
    if any(not isinstance(row.get("path"), str) or not isinstance(row.get("sha256"), str) for row in records):
        raise OncoTracerError("Native manifest has invalid file identities")
    hashes = {row["path"]: row["sha256"] for row in records}
    require_file(outdir / "06_workflow_summary/workflow_summary.json", "workflow summary")
    inputs = {}
    for relative in (*CNA_INPUTS, OUTPUT_OWNER_RELATIVE.as_posix()):
        actual = sha256_file(require_file(outdir / relative, "completed CNA input"))
        if hashes.get(relative) != actual:
            raise OncoTracerError(f"Native manifest input checksum changed or missing: {relative}")
        inputs[relative] = actual
    return owner, manifest, inputs


def _claim_reports(outdir: Path, owner: dict, manifest: dict, inputs: dict) -> tuple[Path, dict]:
    output = outdir / "05_cna_classifier"
    marker = output / "report_provenance.json"
    identity = {"schema": SCHEMA, "output_id": owner["output_id"],
                "source_manifest_sha256": sha256_file(outdir / MANIFEST), "inputs": inputs}
    if output.exists():
        if marker.exists():
            previous = _json(marker)
            if any(previous.get(key) != value for key, value in identity.items()):
                raise OncoTracerError("Report provenance does not match this completed native run")
        else:
            relative = "05_cna_classifier/native_classifier_summary.json"
            expected = next((row.get("sha256") for row in manifest["files"]
                             if row.get("path") == relative), None)
            if not expected or not (outdir / relative).is_file() or sha256_file(outdir / relative) != expected:
                raise OncoTracerError("Refusing foreign 05_cna_classifier directory without report ownership")
            prior = _json(outdir / relative)
            if prior.get("schema") != "oncotracer-native-classifier-v1" or prior.get("classifier_outdir") != str(output):
                raise OncoTracerError("Prior classifier output identity does not match this directory")
            if (output / ".reports").exists():
                raise OncoTracerError("Refusing foreign report audit directory")
    output.mkdir(exist_ok=True)
    audit = output / ".reports"
    audit.mkdir(exist_ok=True)
    original = audit / "native_run_manifest.original.json"
    if original.exists() and sha256_file(original) != identity["source_manifest_sha256"]:
        raise OncoTracerError("Saved original native manifest changed")
    if not original.exists():
        shutil.copyfile(outdir / MANIFEST, original)
    return output, identity


def _effective(config: dict, args) -> dict:
    result = dict(DEFAULTS, **config)
    result.update(run_cna_classifier=True, run_pdf_reports=True, run_clinician_reports=True,
                  knowledge_web=args.literature, knowledge_literature_llm=args.literature,
                  knowledge_deep_literature=args.literature and args.deep_literature, knowledge_catalog_llm=False,
                  knowledge_literature_llm_max_features=args.max_features,
                  knowledge_max_papers=args.max_papers,
                  knowledge_literature_llm_max_new_tokens=args.max_new_tokens,
                  knowledge_deep_enable_llm_ranker=False,
                  knowledge_literature_reference_llm_selection=False,
                  pathology_use_biomed_models=False, knowledge_hf_ner=False,
                  knowledge_llm_threads=args.threads,
                  knowledge_literature_llm_local_files_only=not args.allow_model_download,
                  knowledge_deep_llm_ranker_local_files_only=not args.allow_model_download,
                  pathology_biomed_local_files_only=True,
                  run_gistic="run_gistic" in config and _bool(config, "run_gistic"))
    if args.model:
        result["knowledge_literature_llm_models"] = args.model
    return result


def command_reports(args) -> int:
    from .cli import _load_install_config, _managed_conda_base

    if min(args.threads, args.max_features, args.max_papers, args.max_new_tokens) < 1:
        raise OncoTracerError("Report threads and feature/paper/token limits must be positive integers")
    config_path = require_file(Path(args.config).expanduser().absolute(), "original run config")
    config = load_flat_yaml(config_path)
    if not config.get("outdir"):
        raise OncoTracerError("Original run config requires outdir")
    outdir = Path(os.path.abspath(os.path.expanduser(str(config["outdir"]))))
    _authenticate(outdir, config_path)
    lock_path = outdir / OUTPUT_LOCK_RELATIVE
    _reject_symlink_components(lock_path)
    if not lock_path.is_file():
        raise OncoTracerError("Native output run lock is missing")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OncoTracerError("Native output run lock must be one regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OncoTracerError("An analysis or report generation is already running in this directory") from error
        owner, manifest, inputs = _authenticate(outdir, config_path)
        install = _load_install_config()
        base = _managed_conda_base(install, require_poetry=False)
        with managed_conda_runtime_lock(base, require_poetry=False, semantic=False) as prefixes:
            root = runtime_root(Path(args.root) if args.root else None)
            effective = _effective(config, args)
            # GISTIC is only applicable to an explicitly enabled, multi-sample run.
            effective["run_gistic"] = effective["run_gistic"] and len(manifest.get("completed_samples", [])) >= 2
            runtime_identity = current_runtime_identity(root)
            output, provenance = _claim_reports(outdir, owner, manifest, inputs)
            generation = output / ".reports" / uuid.uuid4().hex
            generation.mkdir()
            effective_path = generation / "effective_config.yml"
            atomic_write_text(effective_path, render_flat_yaml(effective))
            summary_path = outdir / "06_workflow_summary/workflow_summary.json"
            shutil.copyfile(summary_path, generation / "workflow_summary.before.json")
            provenance.update(status="running", started_at=utc_now(),
                              generation=str(generation.relative_to(outdir)),
                              effective_config_sha256=sha256_file(effective_path),
                              runtime_identity=runtime_identity)
            marker = output / "report_provenance.json"
            atomic_write_json(marker, provenance)
            try:
                toolchain = Toolchain(classifier_prefix=prefixes["classifier"],
                                      gistic_prefix=prefixes["gistic"],
                                      runtime_cache=Path(tempfile.mkdtemp(prefix="runtime-", dir=generation)))
                def validate_inputs():
                    if sha256_file(config_path) != manifest["config_sha256"] or sha256_file(outdir / MANIFEST) != provenance["source_manifest_sha256"]:
                        raise OncoTracerError("Original config or native manifest changed during report generation")
                    for relative, digest in inputs.items():
                        _reject_symlink_components(outdir / relative)
                        if sha256_file(outdir / relative) != digest:
                            raise OncoTracerError(f"Report input changed during execution: {relative}")
                runner = CommandRunner(generation / "trace.tsv",
                                       validators=(validate_inputs, toolchain.validate_environment))
                ledger = StageLedger(output / ".reports" / "state.json")
                run_native_classifier(root, effective, outdir, generation, runner, ledger, toolchain, force=args.force)
                validate_inputs()
                metrics_path = output / "06_knowledge/knowledge_metrics.json"
                metrics = _json(metrics_path) if metrics_path.exists() else {}
                accepted = metrics.get("literature_llm_completed_features")
                provenance["literature_llm"] = {"requested": args.literature,
                    "attempted_features": metrics.get("literature_llm_attempted_features"),
                    "accepted_drafts": accepted,
                    "status": "not_requested" if not args.literature else (
                        "unknown" if accepted is None else "accepted_drafts" if accepted else "no_accepted_drafts")}
                summary = _json(summary_path)
                if summary.get("cna_classifier_status") != "complete":
                    raise OncoTracerError("Classifier reports are incomplete; inspect report trace and GISTIC status")
                summary.update(reports_status="complete")
                summary.pop("report_error", None)
                if manifest.get("workflow_status") == "complete":
                    summary["workflow_status"] = "complete"
                atomic_write_workflow_summary(summary_path.parent, summary)
                write_results_index(outdir)
                provenance.update(status="complete", finished_at=utc_now())
            finally:
                provenance["files"] = [{"path": str(path.relative_to(outdir)), "sha256": sha256_file(path)}
                                       for path in sorted(output.rglob("*"))
                                       if path.is_file() and path != marker and ".reports" not in path.relative_to(output).parts]
                atomic_write_json(generation / "provenance.json", provenance)
                atomic_write_json(marker, provenance)
    except BaseException as error:
        # Include failures from runtime-lock exit validation and audit setup.
        if "generation" in locals() and generation.is_dir():
            provenance.update(status="failed", finished_at=utc_now(), error=str(error))
            atomic_write_json(generation / "provenance.json", provenance)
            atomic_write_json(output / "report_provenance.json", provenance)
            summary_path = outdir / "06_workflow_summary/workflow_summary.json"
            summary = _json(summary_path)
            summary.setdefault("cna_status", "complete")
            summary.update(cna_classifier_status="partial_failure", cna_classifier_completed=False,
                           reports_status="failed", workflow_status="partial_failure", report_error=str(error))
            atomic_write_workflow_summary(summary_path.parent, summary)
            try:
                write_results_index(outdir)
            except Exception:
                pass  # Preserve the original report failure if presentation also fails.
        raise
    finally:
        os.close(descriptor)
    print(f"Reports: {output / '03_report'}\nProvenance: {marker}")
    if args.literature:
        llm = provenance["literature_llm"]
        print(f"Literature LLM: {llm['status']}; accepted drafts: {llm['accepted_drafts']}; "
              f"attempted features: {llm['attempted_features']}. See 05_cna_classifier/06_knowledge/knowledge_metrics.json.")
    return 0


def add_reports_command(subparsers) -> None:
    parser = subparsers.add_parser("reports", help="Add interpretation reports to completed native CNA outputs")
    parser.add_argument("--config", required=True, help="unchanged original run.yml containing the completed outdir")
    parser.add_argument("--backend", choices=("conda",), default="conda", help="managed Conda runtime (default: conda)")
    parser.add_argument("--literature", action="store_true", help="retrieve literature and summarize with LLMs; default: catalog reports only")
    parser.add_argument("--deep-literature", action="store_true", help="add deep literature retrieval (requires --literature)")
    parser.add_argument("--max-features", type=int, default=8, help="maximum LLM feature summaries (default: 8)")
    parser.add_argument("--max-papers", type=int, default=8, help="maximum retrieved papers per feature (default: 8)")
    parser.add_argument("--max-new-tokens", type=int, default=192, help="maximum generated tokens per summary (default: 192)")
    parser.add_argument("--model", help="literature model ID, optional @revision; overrides models in run.yml")
    parser.add_argument("--allow-model-download", action="store_true", help="allow model downloads; default: cached models only")
    parser.add_argument("--threads", type=int, default=4, help="CPU threads for LLM inference (default: 4)")
    parser.add_argument("--force", action="store_true", help="regenerate interpretation outputs, reusing the literature HTTP cache")
    parser.add_argument("--root", help="explicit runtime payload directory")
    parser.set_defaults(func=command_reports)
