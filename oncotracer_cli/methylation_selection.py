"""Explicit methylation choices; never remove samples from other analyses."""
from .runtime import OncoTracerError


def row_enabled(value, sample):
    """A missing/blank column preserves the previous opt-in project setting."""
    from .methylation import _strict_bool
    return _strict_bool(value if value not in (None, "") else None,
                        key=f"Sample {sample}: methylation", default=True)


def excluded_samples(config, names=None):
    value = config.get("methylation_excluded_samples", "")
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        raise OncoTracerError("methylation_excluded_samples must be sample IDs separated by commas, or a list")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise OncoTracerError("methylation_excluded_samples contains an empty or invalid sample ID")
    excluded = tuple(item.strip() for item in values)
    if len(set(excluded)) != len(excluded):
        raise OncoTracerError("methylation_excluded_samples contains repeated sample IDs")
    if not excluded:
        return ()
    if names is None:
        from .engine import parse_ont_samples
        names = [sample.sample for sample in parse_ont_samples(config)]
    unknown = set(excluded) - set(names)
    if unknown:
        raise OncoTracerError("Unknown methylation exclusion sample IDs: " + ", ".join(sorted(unknown)))
    if set(excluded) == set(names):
        raise OncoTracerError("Every sample is excluded from methylation. Turn methylation off for the project (choose CNA only) to run the other analyses.")
    return excluded


def excluded_record(sample):
    return {"sample": sample, "status": "not_applicable", "stage": "excluded",
            "reason": "Methylation disabled by user; other selected analyses are retained",
            "methylation_enabled": False, "error": None}


class MissingMethylationCalls(OncoTracerError):
    """Matching reads exist, but no recorded MM/ML calls are available."""


def unavailable_record(sample, reason):
    return {"sample": sample, "status": "methylation_unavailable", "stage": "input_modification_tags",
            "reason": str(reason), "error": None, "classification": None,
            "read_id_count": None, "cpg_rows": None, "modified_cpg_calls": None}


MISSING_CALLS_HELP = (
    "No FASTQ-selected reads with MM/ML modification tags in the BAM input. "
    "Matching primary reads are present, but methylation calls were not recorded or were stripped. "
    "Supply the matching raw POD5 files and choose Rockfish to call methylation, "
    "or turn methylation off for this sample. FASTQs alone cannot recover methylation. "
    "CNA and selected variant analyses can still run; no methylation classification is made."
)
