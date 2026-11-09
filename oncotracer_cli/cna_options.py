"""Versioned research CNA profiles, independent of sample labels.

Omitted profiles in existing YAML retain the original scientific policy.
New projects save their choice explicitly. These are research operating points,
not calibrated sensitivity/specificity claims.
"""
from copy import deepcopy

from .runtime import OncoTracerError

NEW_PROJECT_PROFILE = "sensitive"
PROFILES = {
    "standard": {
        "profile": "standard", "qc_policy": "coverage-v2",
        "thresholds": {"minimum_usable_bins": 100, "minimum_median_reads_per_bin": 20,
                       "maximum_zero_read_bin_fraction": .05, "minimum_finite_normalized_fraction": .99,
                       "minimum_total_reads": 0},
        "calling": {"loss": -.30, "gain": .25, "deep_loss": -1., "amp": .70,
                    "minimum_event_bins": 3, "minimum_event_mb": 1.},
        "broad": {"minimum_total_reads": 5000, "minimum_region_reads": 200,
                  "minimum_reference_chromosomes": 8, "minimum_region_bases": 10_000_000,
                  "interval_z": 2.576, "interval_percent": 99,
                  "log2_uncertainty_floor": .15, "candidate_log2_margin": .3,
                  "maximum_approximate_q": .01},
    },
    "sensitive": {
        "profile": "sensitive", "qc_policy": "coverage-v3-sensitive",
        "thresholds": {"minimum_usable_bins": 100, "minimum_median_reads_per_bin": 5,
                       "maximum_zero_read_bin_fraction": .20, "minimum_finite_normalized_fraction": .99,
                       "minimum_total_reads": 5000},
        "calling": {"loss": -.20, "gain": .20, "deep_loss": -1., "amp": .70,
                    "minimum_event_bins": 3, "minimum_event_mb": .5},
        "broad": {"minimum_total_reads": 5000, "minimum_region_reads": 100,
                  "minimum_reference_chromosomes": 8, "minimum_region_bases": 5_000_000,
                  "interval_z": 1.96, "interval_percent": 95,
                  "log2_uncertainty_floor": .15, "candidate_log2_margin": .15,
                  "maximum_approximate_q": .05},
    },
}


def validate_profile(value):
    if not isinstance(value, str) or value not in PROFILES:
        raise OncoTracerError("cna_profile must be standard or sensitive")
    return value


def resolve_policy(config=None):
    profile = validate_profile((config or {}).get("cna_profile", "standard"))
    return deepcopy(PROFILES[profile])


def add_arguments(parser):
    parser.add_argument("--cna-profile", "--cna_profile", choices=tuple(PROFILES), default=None,
                        help="CNA research sensitivity, identical for tumors and controls; new projects use sensitive, existing YAML without this field keeps standard")


def codification_arguments(policy):
    values = policy["calling"]
    return [item for flag, key in (("--loss", "loss"), ("--gain", "gain"),
                                  ("--deep-loss", "deep_loss"), ("--amp", "amp"),
                                  ("--min-bins", "minimum_event_bins"), ("--min-mb", "minimum_event_mb"))
            for item in (flag, str(values[key]))]


def r_policy_argument(policy):
    """A fixed, numeric wire format readable with base R (no extra packages)."""
    t = policy["thresholds"]
    return "|".join(map(str, (policy["profile"], policy["qc_policy"],
        t["minimum_usable_bins"], t["minimum_median_reads_per_bin"],
        t["maximum_zero_read_bin_fraction"], t["minimum_finite_normalized_fraction"],
        t["minimum_total_reads"], policy["calling"]["loss"], policy["calling"]["gain"])))
