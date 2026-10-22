"""Final sample counts from current run outcomes, never from intermediate files."""
from __future__ import annotations

import html

SCHEMA = "oncotracer-sample-completion-v1"
BRANCHES = {
    "cna": ("CNA", "cna_status", "completed_samples", "failed_samples"),
    "variants": ("Small variants", "variant_status", "variant_completed_samples", "variant_failed_samples"),
    "methylation": ("Methylation", "methylation_status", "methylation_completed_samples", "methylation_failed_samples"),
    "reports": ("CNA interpretation reports", "cna_classifier_status", "", ""),
}


def summarize_completion(summary):
    """Require the selected cohort, including samples that never reached a caller.

    Existing completed_samples remains the historical CNA field. This separate
    object intersects all requested branches and does not change resume evidence.
    """
    names = summary.get("input_samples")
    requested = summary.get("requested_branches")
    if not isinstance(names, list) or not names or not isinstance(requested, list) or not requested:
        return None
    names = list(dict.fromkeys(str(name) for name in names))
    branches, outstanding = {}, []
    for key in requested:
        if key not in BRANCHES:
            return None  # An unknown requested branch must not imply success.
        label, status_key, complete_key, failed_key = BRANCHES[key]
        status = summary.get(status_key, "unknown")
        completed = set(summary.get(complete_key) or [])
        failed = set(summary.get(failed_key) or [])
        recorded = summary.get(key + "_sample_statuses") or {}
        states = {}
        for name in names:
            if (key == "reports" and status in {"complete", "partial_failure"}
                    and summary.get("cna_reports_completed") is True
                    and (name in summary.get("completed_samples", [])
                         or ("completed_samples" not in summary and summary.get("cna_status") == "complete"))):
                state = "complete"
            elif name in recorded:
                state = recorded[name]
            elif name in failed:
                state = "failed"
            elif status != "failed" and name in completed:
                state = "complete"
            elif status == "complete" and (not complete_key or complete_key not in summary):
                state = "complete"
            else:
                state = "incomplete"
            states[name] = str(state)
        if key == "methylation":
            for field, reason in (("methylation_no_cpg_samples", "no_cpg_modifications"),
                                  ("methylation_no_classifier_probe_samples", "no_classifier_probes")):
                for name in summary.get(field, []):
                    if name in states:
                        states[name] = reason
        branches[key] = {"label": label, "status": status, **_counts(states), "sample_statuses": states}
        if status != "complete" and not branches[key]["incomplete_count"]:
            outstanding.append(label + " cohort outputs (" + str(status) + ")")
    states, remaining = {}, []
    for name in names:
        missing = [branch["label"] + " (" + branch["sample_statuses"][name] + ")"
                   for branch in branches.values()
                   if branch["sample_statuses"][name] not in {"complete", "not_applicable"}]
        if missing:
            states[name] = "incomplete"
            remaining.append({"sample": name, "remaining": missing})
        else:
            states[name] = ("not_applicable" if all(branch["sample_statuses"][name] == "not_applicable"
                                                  for branch in branches.values()) else "complete")
    return {"schema": SCHEMA, **_counts(states), "branches": branches,
            "remaining_samples": remaining, "outstanding_cohort_work": outstanding}


def _counts(states):
    completed = [name for name, status in states.items() if status == "complete"]
    skipped = [name for name, status in states.items() if status == "not_applicable"]
    incomplete = [name for name in states if name not in completed and name not in skipped]
    return {"total_samples": len(states), "completed_count": len(completed),
            "incomplete_count": len(incomplete), "not_applicable_count": len(skipped),
            "completed_samples": completed, "incomplete_samples": incomplete,
            "not_applicable_samples": skipped}


def _count_text(counts):
    total = counts["total_samples"]
    text = f"completed {counts['completed_count']}/{total}; incomplete {counts['incomplete_count']}/{total}"
    if counts["not_applicable_count"]:
        text += f"; not applicable {counts['not_applicable_count']}/{total}"
    return text


def completion_lines(completion, *, details=True):
    if not completion:
        return ["Sample completion counts are unavailable for this attempt. Retained stages are checked on resume."]
    lines = ["Samples: " + _count_text(completion) + "."]
    for branch in completion["branches"].values():
        lines.append(branch["label"] + ": " + _count_text(branch) + ".")
    if details:
        lines.extend(sample_table(completion_rows(completion)))
    if completion["outstanding_cohort_work"]:
        lines.append("Still incomplete: " + "; ".join(completion["outstanding_cohort_work"]) + ".")
    return lines


def completion_rows(completion):
    """Every selected sample, including those that never started a caller."""
    names = list(dict.fromkeys(name for branch in completion["branches"].values()
                              for name in branch["sample_statuses"]))
    remaining = {row["sample"]: "; ".join(row["remaining"]) for row in completion["remaining_samples"]}
    return [(name, "COMPLETE" if name in completion["completed_samples"] else
             "NOT APPLICABLE" if name in completion["not_applicable_samples"] else "INCOMPLETE",
             remaining.get(name, "")) for name in names]


def sample_table(rows):
    from .terminal_progress import clean
    rows = [(clean(name), state, clean(reason)) for name, state, reason in rows]
    if not rows:
        return []
    width = max(len("SAMPLE"), *(len(name) for name, _, _ in rows))
    return ["", f"{'SAMPLE':<{width}}  STATUS          REMAINING ANALYSIS",
            *[f"{name:<{width}}  {state:<14}  {reason}".rstrip() for name, state, reason in rows]]


def completion_html(completion):
    if not completion:
        return ""
    body = '<section class="card"><h2>Sample completion</h2>'
    body += ''.join('<p>' + html.escape(line) + '</p>' for line in completion_lines(completion, details=False))
    body += '<p>A sample is complete only when all its requested analyses are complete. An alignment alone is an intermediate result. Incomplete includes failed, interrupted, pending and unavailable assessments.</p>'
    if completion["remaining_samples"]:
        body += '<div class="scroll"><table><thead><tr><th>Incomplete sample</th><th>Remaining analysis</th></tr></thead><tbody>'
        for row in completion["remaining_samples"]:
            body += '<tr><td>' + html.escape(row["sample"]) + '</td><td>' + html.escape('; '.join(row["remaining"])) + '</td></tr>'
        body += '</tbody></table></div>'
    return body + '</section>'
