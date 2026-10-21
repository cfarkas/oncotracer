"""Verify published native partial results before classifying a subprocess exit."""
from __future__ import annotations

import json
from pathlib import Path

from .runtime import sha256_file


def published_native_partial(outdir, config_sha256, prior_manifest_mtime=None) -> bool:
    """Accept only fresh, manifest-backed reports for the reviewed configuration.

    Exit status 2 alone also covers invalid inputs and runtime failures. A partial
    completion requires a published index and a consistent final native manifest.
    """
    try:
        outdir = Path(outdir)
        summary_path = outdir / "06_workflow_summary/workflow_summary.json"
        manifest_path = outdir / "06_workflow_summary/native_run_manifest.json"
        index = outdir / "index.html"
        manifest_time = manifest_path.stat().st_mtime_ns
        if prior_manifest_mtime is not None and manifest_time <= prior_manifest_mtime:
            return False
        if not index.is_file() or not summary_path.stat().st_mtime_ns <= manifest_time <= index.stat().st_mtime_ns:
            return False
        summary = json.loads(summary_path.read_text())
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("schema") != "oncotracer-native-run-manifest-v1"
                or manifest.get("engine") != "native"
                or summary.get("engine") != "native"
                or summary.get("mode") not in {"illumina", "ont"}
                or manifest.get("config_sha256") != config_sha256
                or manifest.get("workflow_status") != "partial_failure"
                or summary.get("workflow_status") != "partial_failure"):
            return False
        for branch in ("cna_status", "variant_status", "methylation_status"):
            if manifest.get(branch) != summary.get(branch):
                return False
        hashes = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
        if hashes.get(str(summary_path.relative_to(outdir))) != sha256_file(summary_path):
            return False
        variant_path = outdir / "08_variants/variant_status.json"
        if variant_path.exists():
            status = json.loads(variant_path.read_text())
            if (variant_path.stat().st_mtime_ns > manifest_time
                    or status.get("overall_status") != summary.get("variant_status")
                    or hashes.get(str(variant_path.relative_to(outdir))) != sha256_file(variant_path)):
                return False
        elif summary.get("variant_status") in {"complete", "partial_failure"}:
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def published_completion(outdir, config_sha256, prior_manifest_mtime=None):
    """Return counts only from this attempt's finalized, manifest-backed summary."""
    from .completion import summarize_completion
    try:
        root = Path(outdir)
        path = root / "06_workflow_summary/workflow_summary.json"
        manifest_path = path.with_name("native_run_manifest.json")
        index = root / "index.html"
        stamp = manifest_path.stat().st_mtime_ns
        if prior_manifest_mtime is not None and stamp <= prior_manifest_mtime:
            return None
        if not path.stat().st_mtime_ns <= stamp <= index.stat().st_mtime_ns:
            return None
        summary = json.loads(path.read_text())
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("schema") != "oncotracer-native-run-manifest-v1"
                or manifest.get("config_sha256") != config_sha256
                or manifest.get("workflow_status") != summary.get("workflow_status")
                or summary.get("workflow_status") in {None, "running", "in_progress"}):
            return None
        hashes = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
        if hashes.get(str(path.relative_to(root))) != sha256_file(path):
            return None
        for relative, expected_hash in hashes.items():
            if relative in {
                "01_samurai_ont/results/ichorcna/ichorcna_sample_status.json",
                "01_samurai_ont/qdnaseq/qdnaseq_sample_status.json",
                "01_samurai_illumina/qdnaseq/qdnaseq_sample_status.json",
                "08_variants/variant_status.json", "07_methylation/methylation_status.json",
            }:
                status_path = root / relative
                if status_path.stat().st_mtime_ns > stamp or sha256_file(status_path) != expected_hash:
                    return None
        completion = summarize_completion(summary)
        if completion is None or completion != summary.get("completion") or completion != manifest.get("completion"):
            return None
        return completion
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
