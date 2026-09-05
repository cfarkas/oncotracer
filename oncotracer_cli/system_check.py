"""Read-only, dependency-free hardware guidance, not a promise of completion."""

from __future__ import annotations

import argparse
import math
import os
import platform
import shutil
import sys
from pathlib import Path

from .runtime import load_flat_yaml, require_file

GIB = 1024**3


def _number(path: Path) -> int | None:
    try:
        value = int(path.read_text().strip())
        return value if 0 <= value < 2**60 else None
    except (OSError, ValueError):
        return None


def _cgroup_paths(proc: Path, cgroup: Path, controller: str) -> list[Path]:
    """Include visible ancestors and the mount root, including container remaps."""
    roots = [cgroup, cgroup / controller]
    try:
        for line in (proc / "self/cgroup").read_text().splitlines():
            _hierarchy, controllers, relative = line.split(":", 2)
            if not controllers or controller in controllers.split(","):
                # A namespace may expose a parent-relative path. Never walk
                # outside the cgroup mount or infer unlimited host resources.
                parts = Path(relative.lstrip("/")).parts
                if ".." in parts:
                    continue
                parent = cgroup if not controllers else cgroup / controller
                current = parent.joinpath(*parts)
                roots.extend(
                    [
                        current,
                        *[
                            p
                            for p in current.parents
                            if p == parent or parent in p.parents
                        ],
                    ]
                )
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(roots))


def inspect_hardware(
    *, proc: Path = Path("/proc"), cgroup: Path = Path("/sys/fs/cgroup")
) -> dict:
    memory = {}
    try:
        for line in (proc / "meminfo").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                memory[fields[0].rstrip(":")] = int(fields[1]) * 1024
    except OSError:
        pass
    total = memory.get("MemTotal")
    available = memory.get("MemAvailable")
    limits = []
    for root in _cgroup_paths(proc, cgroup, "memory"):
        for maximum, current in (
            ("memory.max", "memory.current"),
            ("memory.limit_in_bytes", "memory.usage_in_bytes"),
        ):
            limit, used = _number(root / maximum), _number(root / current)
            if limit is not None:
                limits.append(limit)
                total = min(total, limit) if total is not None else limit
                if used is not None:
                    headroom = max(0, limit - used)
                    available = (
                        min(available, headroom) if available is not None else headroom
                    )
                elif available is not None:
                    available = min(available, limit)
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:
        cpus = os.cpu_count() or 1
    for root in _cgroup_paths(proc, cgroup, "cpu"):
        try:
            quota, period = (root / "cpu.max").read_text().split()
            if quota != "max" and int(period) > 0:
                cpus = min(cpus, max(1, math.floor(int(quota) / int(period))))
        except (OSError, ValueError):
            pass
        quota, period = _number(root / "cpu.cfs_quota_us"), _number(
            root / "cpu.cfs_period_us"
        )
        if quota is not None and period and quota > 0:
            cpus = min(cpus, max(1, quota // period))
    return {
        "os": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "python_supported": (3, 10) <= sys.version_info[:2] <= (3, 13),
        "cpu_workers_available": cpus,
        "ram_total_bytes": total,
        "ram_available_bytes": available,
        "cgroup_memory_limits_bytes": sorted(set(limits)),
        "swap_total_bytes": memory.get("SwapTotal"),
        "swap_note": "Host swap is informational; it is not counted as available RAM or guaranteed container memory.",
    }


def _disk(path: Path) -> dict:
    requested = path.expanduser().absolute()
    existing = requested
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    try:
        return {
            "path": str(requested),
            "checked_parent": str(existing),
            "free_bytes": shutil.disk_usage(existing).free,
            "device": existing.stat().st_dev,
        }
    except OSError as error:
        return {"path": str(requested), "free_bytes": None, "error": str(error)}


def resource_report(
    config: dict | None = None,
    *,
    path: Path | None = None,
    hardware: dict | None = None,
) -> dict:
    config = config or {}
    hardware = inspect_hardware() if hardware is None else dict(hardware)
    available = hardware.get("ram_available_bytes")
    cpus = hardware["cpu_workers_available"]
    try:
        requested_threads = max(1, int(config.get("threads", min(4, cpus))))
    except (ValueError, TypeError):
        requested_threads = min(4, cpus)
    mode = config.get("mode")
    methylation_only = config.get("methylation_only") is True
    supported = (
        hardware["os"] == "Linux"
        and hardware["architecture"] in {"x86_64", "amd64"}
        and hardware["python_supported"]
    )
    capabilities = []

    def capability(name, ram_gib, selected, note):
        state = "not_supported" if not supported else "not_assessed"
        if supported and available is not None and ram_gib is not None:
            state = (
                "likely_feasible" if available >= ram_gib * GIB else "limited_memory"
            )
        capabilities.append(
            {
                "task": name,
                "status": state,
                "selected": selected,
                "planning_ram_gib": ram_gib,
                "note": note,
            }
        )

    capability(
        "Configure runs and read existing reports",
        0.5,
        True,
        "Does not perform genome alignment or model inference.",
    )
    capability(
        "Download/import a prebuilt genome",
        1,
        False,
        "Streaming avoids index construction; sufficient disk space and a validated compatible bundle are still required.",
    )
    capability(
        "Illumina CNA analysis",
        max(16, 6 + 0.75 * requested_threads),
        mode == "illumina" and not methylation_only,
        "Planning estimate for small LP-WGS runs; includes alignment/sorting headroom. Native OncoTracer uses BWA, not BWA-MEM2.",
    )
    capability(
        "ONT CNA analysis",
        max(24, 10 + 0.75 * requested_threads),
        mode == "ont" and not methylation_only,
        "The full hg38 minimap2 index still loads into memory. A prebuilt index does not remove mapping or R-stage memory needs.",
    )
    methylation = methylation_only or config.get("methylation") is True
    capability(
        "ONT methylation models",
        None,
        methylation,
        "Model-dependent; not certified by RAM alone. Existing MM/ML BAMs avoid raw-signal basecalling, not alignment or classifier memory.",
    )
    reports = config.get("run_cna_classifier") is True
    llm = reports and (
        config.get("knowledge_literature_llm", True) is True
        or config.get("knowledge_deep_enable_llm_ranker", True) is True
    )
    capability(
        "Local report LLM",
        None,
        llm,
        "CPU only. Check the selected model's weight size and runtime memory; turn off both knowledge_literature_llm and knowledge_deep_enable_llm_ranker to avoid report LLMs.",
    )
    disks = [
        _disk(Path(str(config.get(key) or path or Path.cwd())))
        for key in ("lpwgs_root", "outdir")
    ]
    disks = list({row["path"]: row for row in disks}.values())
    warnings = []
    if not supported:
        warnings.append(
            "The packaged analysis environments target Linux x86-64 with Python 3.10–3.13. Other systems are not verified by this check."
        )
    if requested_threads > cpus:
        warnings.append(
            f"threads: {requested_threads} exceeds the detected CPU allowance ({cpus}); lower this YAML setting."
        )
    for row in disks:
        if row["free_bytes"] is not None and row["free_bytes"] < 40 * GIB:
            warnings.append(
                f"Less than 40 GiB free at {row['path']}. Reserve space for environments/reference plus FASTQs, BAMs and temporary copies; large runs need more."
            )
    for row in capabilities:
        if row["selected"] and row["status"] == "limited_memory":
            warnings.append(
                f"{row['task']}: below the {row['planning_ram_gib']:g} GiB planning estimate. Reduce threads, close other workloads, or use a larger machine."
            )
    if methylation and not config.get("methylation_modbam"):
        warnings.append(
            "Raw POD5 basecalling can be very slow on CPU. This check does not test CUDA, GPU availability, or model compatibility."
        )
    return {
        "schema": "oncotracer-system-v1",
        "hardware": hardware,
        "requested_threads": requested_threads,
        "suggested_threads": min(4, cpus),
        "backends_found": {
            name: shutil.which(name)
            for name in ("conda", "docker", "apptainer", "singularity")
        },
        "disk": disks,
        "capabilities": capabilities,
        "warnings": warnings,
        "limits": "Read-only planning estimates, not measured peak requirements or a guarantee. Tool installation, sample size, coverage, model suitability, permissions and container runtime limits need separate checks. No analysis, downloads, GPU calls or system changes were made.",
    }


def print_resource_report(report: dict) -> None:
    hardware = report["hardware"]
    available = hardware["ram_available_bytes"]
    ram = (
        f"{available / GIB:.1f} GiB available"
        if available is not None
        else "available RAM unknown"
    )
    print(
        f"System: {hardware['os']} {hardware['architecture']}; {hardware['cpu_workers_available']} CPU workers; {ram}"
    )
    found = ", ".join(
        name for name, executable in report["backends_found"].items() if executable
    )
    print(
        "Backend commands found: "
        + (found or "none; install Conda, Docker or Apptainer/Singularity")
        + ". Runtime health is checked separately by doctor."
    )
    for row in report["capabilities"]:
        selected = " [selected]" if row["selected"] else ""
        print(f"  {row['task']}{selected}: {row['status'].replace('_', ' ')}")
        print(f"    {row['note']}")
    for row in report["disk"]:
        amount = (
            f"{row['free_bytes'] / GIB:.1f} GiB free"
            if row["free_bytes"] is not None
            else "free space unknown"
        )
        print(f"  Disk: {row['path']} — {amount}")
    for warning in report["warnings"]:
        print(f"  Note: {warning}")
    print(f"Suggested starting setting: threads: {report['suggested_threads']}")
    print(report["limits"])


def command_system(args: argparse.Namespace) -> int:
    import json

    config = (
        load_flat_yaml(require_file(Path(args.config).expanduser(), "configuration"))
        if args.config
        else {}
    )
    report = resource_report(config, path=Path(args.path))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_resource_report(report)
        print(
            "Next: oncotracer check --config /path/to/run.yml; oncotracer doctor --backend conda"
        )
    return 0


def add_system_command(subparsers) -> None:
    parser = subparsers.add_parser(
        "system",
        help="Explain hardware capacity and limits before installing or running",
    )
    parser.add_argument(
        "--config", help="optional project YAML to highlight requested analyses"
    )
    parser.add_argument(
        "--path",
        default=".",
        help="directory where data/tools will be stored (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable hardware and capability report",
    )
    parser.set_defaults(func=command_system)
