#!/usr/bin/env python3
"""Strictly verify one successful, exact-run hosted-resource preflight record."""

from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath


STATIC_KEYS = {
    "resource_preflight_schema",
    "resource_preflight_status",
    "resource_preflight_run_id",
    "resource_preflight_run_attempt",
    "resource_preflight_suite",
    "resource_preflight_candidate_sha",
    "resource_preflight_purpose",
    "resource_preflight_minimum_available_kib",
    "resource_preflight_checked_path_count",
    "resource_preflight_unique_device_count",
    "resource_preflight_required_free_gib",
    "resource_preflight_mem_total_kib",
    "resource_preflight_required_physical_gib",
    "resource_preflight_swap_total_kib",
    "resource_preflight_planned_swap_gib",
    "resource_preflight_expected_swap_file",
    "resource_preflight_required_addressable_gib",
    "resource_preflight_standard_contract_free_gib",
}


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def integer(values: dict[str, str], key: str) -> int:
    value = values.get(key, "")
    if re.fullmatch(r"[0-9]+", value) is None:
        fail(f"{key} is not a nonnegative integer")
    return int(value)


def parse(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        fail(f"evidence is missing, non-regular, or a symlink: {path}")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "=" not in raw:
            fail(f"evidence line {line_number} is not key=value")
        key, value = raw.split("=", 1)
        if not key or key in values:
            fail(f"duplicate or empty evidence key on line {line_number}")
        values[key] = value
    if not STATIC_KEYS <= set(values):
        fail(f"evidence static key inventory mismatch: {set(values)!r}")
    return values


def checked_filesystems(
    values: dict[str, str], expected_paths: list[str], minimum_free_gib: int
) -> list[tuple[str, str, int]]:
    count = integer(values, "resource_preflight_checked_path_count")
    if count != len(expected_paths) or count < 1:
        fail("checked filesystem count does not match exact invocation paths")
    dynamic_keys: set[str] = set()
    records: list[tuple[str, str, int]] = []
    for index, expected_path in enumerate(expected_paths):
        prefix = f"resource_preflight_checked_path_{index:03d}_"
        path_key = prefix + "path"
        device_key = prefix + "device"
        available_key = prefix + "available_kib"
        dynamic_keys.update((path_key, device_key, available_key))
        if values.get(path_key) != expected_path:
            fail(f"{path_key} does not match the exact invocation path")
        device = values.get(device_key, "")
        if not device or "\n" in device or "\r" in device:
            fail(f"{device_key} is missing or malformed")
        available = integer(values, available_key)
        if available < minimum_free_gib * 1024 * 1024:
            fail(f"{available_key} does not satisfy the bound threshold")
        records.append((expected_path, device, available))
    if set(values) != STATIC_KEYS | dynamic_keys:
        fail(f"evidence key inventory mismatch: {set(values)!r}")
    if integer(values, "resource_preflight_minimum_available_kib") != min(
        record[2] for record in records
    ):
        fail("recorded minimum free storage does not match checked filesystems")
    if integer(values, "resource_preflight_unique_device_count") != len(
        {record[1] for record in records}
    ):
        fail("recorded unique-device count does not match checked filesystems")
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--min-free-gib", type=int, required=True)
    parser.add_argument("--min-physical-gib", type=int, required=True)
    parser.add_argument("--min-addressable-gib", type=int, required=True)
    parser.add_argument("--planned-swap-gib", type=int, required=True)
    parser.add_argument("--standard-contract-free-gib", type=int, required=True)
    parser.add_argument("--expected-swap-file", required=True)
    parser.add_argument("--path", action="append", default=[], required=True)
    args = parser.parse_args()
    values = parse(args.evidence)

    expected_identity = {
        "resource_preflight_schema": "oncotracer-hosted-resource-preflight-v3",
        "resource_preflight_status": "PASS",
        "resource_preflight_run_id": args.run_id,
        "resource_preflight_run_attempt": args.run_attempt,
        "resource_preflight_suite": args.suite,
        "resource_preflight_candidate_sha": args.candidate_sha,
        "resource_preflight_expected_swap_file": args.expected_swap_file,
    }
    for key, expected in expected_identity.items():
        if values.get(key) != expected:
            fail(f"{key} does not match the exact workflow invocation")
    if (
        re.fullmatch(r"[1-9][0-9]*", args.run_id) is None
        or re.fullmatch(r"[1-9][0-9]*", args.run_attempt) is None
        or re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.suite) is None
        or re.fullmatch(r"[0-9a-f]{40}", args.candidate_sha) is None
    ):
        fail("expected workflow identity is malformed")

    expected_numbers = {
        "resource_preflight_required_free_gib": args.min_free_gib,
        "resource_preflight_required_physical_gib": args.min_physical_gib,
        "resource_preflight_required_addressable_gib": args.min_addressable_gib,
        "resource_preflight_planned_swap_gib": args.planned_swap_gib,
        "resource_preflight_standard_contract_free_gib": args.standard_contract_free_gib,
    }
    for key, expected in expected_numbers.items():
        if expected < 0 or integer(values, key) != expected:
            fail(f"{key} threshold does not match the workflow model")
    kib_per_gib = 1024 * 1024
    checked_filesystems(values, args.path, args.min_free_gib)
    memory = integer(values, "resource_preflight_mem_total_kib")
    current_swap = integer(values, "resource_preflight_swap_total_kib")
    if memory < args.min_physical_gib * kib_per_gib:
        fail("recorded physical memory does not satisfy the bound threshold")
    if (
        memory + current_swap + args.planned_swap_gib * kib_per_gib
        < args.min_addressable_gib * kib_per_gib
    ):
        fail("recorded addressable memory does not satisfy the bound threshold")
    if args.planned_swap_gib == 0:
        if args.expected_swap_file != "none":
            fail("zero-swap preflight must bind the literal none swap path")
    else:
        expected_name = f"oncotracer-swap-{args.run_id}-{args.run_attempt}"
        swap = PurePosixPath(args.expected_swap_file)
        if not swap.is_absolute() or swap.name != expected_name or ".." in swap.parts:
            fail("planned swap is not an exact run-attempt-owned absolute path")
    print("RESOURCE_PREFLIGHT_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
