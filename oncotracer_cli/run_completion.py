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
