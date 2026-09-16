"""Progress estimates from measured CLI output; never extrapolate unlike stages."""
from __future__ import annotations

import re
import time

_DOWNLOAD = re.compile(r"^Downloading (\S+\.part) \(")
_ETA = re.compile(r"^\s*(\S+\.part):\s*(\d+)%\s*\|\s*[\d.]+ MiB/s\s*\|\s*ETA (\d+)s")
_COMMAND = re.compile(r"^\[([A-Za-z][\w.-]*)\] ")


def progress_for_job(job, log, *, now=None):
    now = time.monotonic() if now is None else now
    end = job.get("_finished_at", now)
    elapsed = max(0, int(end - job.get("_started_at", end)))
    status = job["status"]
    if status not in {"running", "stopping"}:
        return {"elapsed_seconds": elapsed, "stage": {"complete": "Completed", "stopped": "Stopped", "removed": "Project removed"}.get(status, "Failed"),
                "eta_seconds": 0 if status == "complete" else None,
                "overall_eta_seconds": 0 if status == "complete" else None,
                "eta_scope": "analysis", "note": ""}
    stage, eta, marker, percent = "Preparing analysis", None, None, None
    for line in log.splitlines():
        match = _DOWNLOAD.match(line)
        estimate = _ETA.match(line)
        command = _COMMAND.match(line)
        if match:
            stage, eta, marker, percent = "Downloading reference: " + match[1], None, None, None
        elif estimate:
            stage, eta, marker, percent = "Downloading reference: " + estimate[1], int(estimate[3]), estimate[0], min(100, int(estimate[2]))
        elif line.startswith(("Verified hg38-", "Reusing verified hg38-")):
            stage, eta, marker, percent = "Verifying reference indexes", None, None, None
        elif line.startswith("Downloading verified prebuilt"):
            stage, eta, marker, percent = "Preparing reference indexes", None, None, None
        elif "conda env create" in line or "conda env update" in line:
            stage, eta, marker, percent = "Preparing analysis tools", None, None, None
        elif command:
            name = command[1].lower()
            stage = next((label for term, label in (
                ("align", "Aligning reads"), ("markdup", "Marking duplicate reads"),
                ("refine", "Refining copy-number results"), ("classifier", "Creating interpretation reports"),
                ("methyl", "Analyzing methylation"), ("qdnaseq", "Calling copy-number changes"),
                ("ichorcna", "Calling copy-number changes")) if term in name), "Processing analysis")
            eta, marker, percent = None, None, None
    if status == "stopping":
        stage, eta, marker, percent = "Stopping analysis", None, None, None
    if marker != job.get("_eta_marker"):
        job["_eta_marker"], job["_eta_observed_at"] = marker, now
    if eta is not None:
        remaining = eta - (now - job.get("_eta_observed_at", now))
        # A stalled or slower transfer must not sit at a misleading zero seconds.
        eta = max(1, round(remaining)) if remaining > 0 else None
    note = ("Estimate for this reference download file only. Overall analysis ETA is not yet known."
            if eta is not None else "Waiting for measurable progress. Overall analysis ETA is not yet known.")
    return {"elapsed_seconds": elapsed, "stage": stage, "eta_seconds": eta,
            "overall_eta_seconds": None, "eta_scope": "current reference download file" if marker else "current stage",
            "percent": percent, "note": note if status == "running" else "Waiting for analysis processes to exit."}
