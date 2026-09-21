"""Terminal folder-to-analysis wizard using the public setup and run paths."""

from __future__ import annotations

import copy
import json
import shlex
from pathlib import Path

from .discovery import discover_fastqs
from .engine import QDNASEQ_HG38_SOURCE_SHA256, _safe_sample
from .runtime import OncoTracerError, load_flat_yaml
from .system_check import GIB, inspect_hardware, resource_report


def _ask(value, label, *, default=None, choices=None):
    from .setup import _ask as ask

    return ask(value, label, default=default, choices=choices, interactive=True)


def _integer(value, label, default, maximum):
    while True:
        answer = _ask(value, label, default=str(default))
        try:
            number = int(answer)
            if 1 <= number <= maximum:
                return number
        except (ValueError, TypeError):
            pass
        message = f"Enter a whole number from 1 to {maximum}."
        if value is not None:
            raise OncoTracerError(f"{label}: {message}")
        print(message)


def _selection(text: str, count: int) -> list[int]:
    if text.lower() == "all":
        return list(range(count))
    selected = set()
    try:
        for token in text.split(","):
            bounds = token.strip().split("-")
            if len(bounds) == 1:
                first = last = int(bounds[0])
            elif len(bounds) == 2:
                first, last = map(int, bounds)
            else:
                raise ValueError
            if not 1 <= first <= last <= count:
                raise ValueError
            selected.update(range(first - 1, last))
    except ValueError as error:
        raise OncoTracerError(f"Choose sample numbers from 1 to {count}, e.g. 1,3 or 1-3, or all.") from error
    if not selected:
        raise OncoTracerError("Select at least one sample.")
    return sorted(selected)


def _sample_name(default, used):
    while True:
        name = _ask(None, "Sample name", default=default)
        try:
            name = _safe_sample(name)
            if name in used:
                raise OncoTracerError(f"Sample name already selected: {name}")
            return name
        except OncoTracerError as error:
            print(error)


def _show_hardware(hardware, suggested):
    print(f"\nAvailable CPU workers: {hardware['cpu_workers_available']}")
    for key, label in (("ram_total_bytes", "RAM total"), ("ram_available_bytes", "RAM available")):
        value = hardware.get(key)
        print(f"{label}: {value / GIB:.1f} GiB" if value is not None else f"{label}: unknown")
    devices = hardware.get("gpus", [])
    print(f"GPU devices reported: {len(devices)}" if devices else "GPU inventory: " + hardware.get("gpu_detection_status", "unavailable"))
    for device in devices:
        total, free = device.get("memory_total_bytes"), device.get("memory_free_bytes")
        memory = f"; {total / GIB:.1f} GiB VRAM" if total is not None else ""
        if free is not None:
            memory += f" ({free / GIB:.1f} GiB free)"
        print(f"  GPU {device['index']}: {device['name']}{memory}")
    if hardware.get("gpu_note"):
        print(hardware["gpu_note"])
    print(f"Suggested CPU threads: {suggested}. CNA analysis uses CPU.")


def command_wizard(original_args) -> int:
    from . import cli
    from .setup import _command_setup, _existing_hg38_parent, _run_setup, _variant_values, command_check

    args = copy.copy(original_args)
    run_requested = args.run
    args.run = False
    print("OncoTracer setup: select FASTQs, assign sample types, choose settings, then save or run.")
    def new_project(path):
        for name in ("run.yml", "samplesheet.csv", "sample_metadata.csv"):
            target = path / "config" / name
            if target.exists() or target.is_symlink():
                raise OncoTracerError(f"setup will not overwrite {target}; choose a new --project or edit the existing YAML")
        return path

    project = new_project(Path(args.project).expanduser().resolve()) if args.project else None
    args.mode = _ask(args.mode, "Sequencing platform (--mode)", choices=("ont", "illumina"))
    if project is None:
        project = new_project(Path(_ask(None, "Project directory (--project)")).expanduser().resolve())
    args.project = str(project)
    folder = Path(_ask(args.input_folder or args.reads_folder, "FASTQ folder (--input-folder)")).expanduser()
    discovered = discover_fastqs(folder, args.mode)
    file_count = sum(len(sample.files) for sample in discovered.samples)
    print(f"\nDetected {file_count} FASTQ files in {len(discovered.samples)} samples under {discovered.root}:")
    for number, sample in enumerate(discovered.samples, 1):
        layout = ("nonbarcoded library batches" if discovered.single_sample else "barcode batches") if args.mode == "ont" else ("paired-end" if sample.fastq_2 else "single-end")
        print(f"  {number}. {sample.sample}: {len(sample.files)} FASTQs, {layout}")
        for path in sample.files[:2]:
            print(f"       {path.relative_to(discovered.root)}")
        if len(sample.files) > 2:
            print(f"       ... and {len(sample.files) - 2} more FASTQs")
    for warning in discovered.warnings:
        print(f"Note: {warning}")
    default_selection = ",".join(str(i) for i, sample in enumerate(discovered.samples, 1)
                                 if sample.barcode != "unclassified") or None
    while True:
        selection = _ask(None, "Samples to include (numbers, ranges, or all)", default=default_selection)
        try:
            indices = _selection(selection, len(discovered.samples))
            selected = [discovered.samples[i] for i in indices]
            if args.mode == "illumina" and len({sample.fastq_2 is not None for sample in selected}) > 1:
                raise OncoTracerError("Select either paired-end or single-end libraries for one project; use a separate project for the other layout.")
            break
        except OncoTracerError as error:
            print(error)
    print("\nAssign sample types explicitly. Controls are analyzed independently; they are not pooled or subtracted.")
    entries, used = [], set()
    for sample in selected:
        print(f"\nSelected: {sample.sample} ({len(sample.files)} FASTQs)", flush=True)
        name = _sample_name(sample.sample, used)
        used.add(name)
        while True:
            kind = _ask(None, f"Type for {name} (cancer/normal/control/other)").casefold()
            if kind in ("cancer", "normal", "control", "other"):
                break
            print("Choose cancer, normal, control, or other (any capitalization).")
        if kind == "other":
            sample_type = _ask(None, "Other sample type", default="other")
            print("Study uses the workflow's tumor group; control uses normal. Your sample type is kept.")
            role = _ask(None, f"Analysis group for {name}", choices=("study", "control"))
            status = "normal" if role == "control" else "tumor"
        else:
            sample_type, status = kind, ("normal" if kind in ("normal", "control") else "tumor")
        entries.append({"source": sample, "sample": name, "sample_type": sample_type, "analysis_role": status})

    args.analysis = _ask(args.analysis, "Analysis (--analysis; cna=copy-number)", default="cna",
                         choices=("cna",) if args.mode == "illumina" else ("cna", "methylation", "both"))
    if args.mode == "illumina" and args.analysis != "cna":
        raise OncoTracerError("Illumina supports CNA analysis; methylation needs ONT inputs.")
    hardware = inspect_hardware(include_gpus=True)
    report = resource_report({"mode": args.mode}, path=project, hardware=hardware)
    _show_hardware(hardware, report["suggested_threads"])
    args.threads = _integer(args.threads, "CPU threads (--threads)", report["suggested_threads"], hardware["cpu_workers_available"])
    values = {"ont_single_sample": True} if discovered.single_sample else {}
    if args.mode == "illumina":
        args.reads_folder = None
        args._wizard_rows = [[row["sample"], str(row["source"].fastq_1),
                              str(row["source"].fastq_2) if row["source"].fastq_2 else "", row["analysis_role"]]
                             for row in entries]
    else:
        study = [row for row in entries if row["analysis_role"] == "tumor"]
        controls = [row for row in entries if row["analysis_role"] == "normal"]
        if not study:
            raise OncoTracerError("An ONT project needs at least one study sample. All-control ONT projects are not supported; revise the sample selection/types.")
        args.reads_folder = str(discovered.root)
        args.barcodes = ",".join(row["source"].barcode for row in study)
        args.sample_names = ",".join(row["sample"] for row in study)
        if controls:
            values.update(ont_normal_folder=str(discovered.root),
                          ont_normal_barcodes=",".join(row["source"].barcode for row in controls),
                          ont_normal_sample_names=",".join(row["sample"] for row in controls))
            print("ONT controls require the solid-biopsy QDNAseq workflow; every selected sample is analyzed independently.")
        choices = ("qdnaseq",) if controls else ("ichorcna", "qdnaseq")
        caller = "qdnaseq" if controls else "ichorcna"
        if args.analysis != "methylation":
            print("ONT CNA methods: ichorcna uses 500 kb bins; qdnaseq is for solid biopsies.")
            caller = _ask(None, "ONT CNA method", default=caller, choices=choices)
        values.update(ont_caller=caller, ont_binsize_kb=500)
        if caller == "qdnaseq":
            values.update(ont_analysis_type="solid_biopsy", ont_binsize_kb=100)
    if args.analysis != "methylation":
        if args.mode == "illumina" or values.get("ont_caller") == "qdnaseq":
            binsize = _ask(None, "CNA bin size (kb)", default="100",
                           choices=tuple(str(n) for n in sorted(QDNASEQ_HG38_SOURCE_SHA256)))
            values[f"{args.mode}_binsize_kb"] = int(binsize)
        reports = _ask(None, "Add CNA interpretation reports?", default="no", choices=("yes", "no")) == "yes"
        values["run_cna_classifier"] = reports
        if reports:
            values.update(
                cna_classifier_sample_set=_ask(None, "Study context for CNA reports", default="broad_cancer"),
                cna_classifier_samples=",".join(row["sample"] for row in entries),
                knowledge_web=False, knowledge_literature_llm=False,
                knowledge_deep_literature=False, knowledge_deep_enable_llm_ranker=False,
                pathology_use_biomed_models=False,
                knowledge_catalog_llm=_ask(None, "Use local language models for CNA catalog interpretations?", default="no", choices=("yes", "no")) == "yes",
                run_gistic=(len(entries) >= 2 and _ask(None, "Add GISTIC recurrence analysis?", default="no", choices=("yes", "no")) == "yes"),
            )
            values["gistic_required"] = values["run_gistic"]
            if len(entries) < 2:
                print("GISTIC is unavailable for one sample; it needs at least two selected samples. Continuing with your CNA reports.")
    backend = args.backend or str(cli._load_install_config().get("backend") or "conda")
    args.backend = _ask(args.backend, "Analysis tools (--backend)", default=backend,
                        choices=("conda", "docker", "singularity", "poetry", "host"))
    if not getattr(args, "variants", False):
        args.variants = _ask(None, "Add small-variant calling?", default="no", choices=("yes", "no")) == "yes"
    if args.variants:
        print("Choose one preservation type per project. Mutect2 and ClairS-TO make tumor-only candidate calls; other callers use germline-style models. Normal labels do not define matched tumor/normal pairs.")
        print("FFPE damage, low coverage, and tumor copy-number changes require artifact review; calls do not establish somatic origin.")
    values.update(_variant_values(args, args.mode, interactive=True))
    if args.analysis != "cna" and args.gpu is None:
        args.gpu = _ask(None, "Methylation compute device", default="cpu", choices=("cpu", "gpu")) == "gpu"
    reference_chosen = args.hg38_build is not None or args.reference_root is not None or args.build_reference
    if not reference_chosen:
        reference = _ask(None, "hg38 reference", default="download", choices=("download", "reuse", "build"))
        if reference == "reuse":
            while True:
                existing = Path(_ask(None, "Prepared OncoTracer reference (--hg38_build)")).expanduser()
                try:
                    args.hg38_build = str(_existing_hg38_parent(existing.resolve(), args.mode))
                    break
                except OncoTracerError as error:
                    print(error)
        elif reference == "build":
            args.build_reference = True
    args._wizard_values = values
    args._wizard_metadata = [
        {key: row[key] for key in ("sample", "sample_type", "analysis_role")}
        | {"fastq_files": json.dumps([str(path) for path in row["source"].files])}
        for row in entries
    ]
    # Existing setup validates scientific options and methylation resources,
    # then writes all files with exclusive creation and without running tools.
    _command_setup(args)
    config_path = project / "config/run.yml"
    config = load_flat_yaml(config_path)
    print("\nReview analysis")
    for row in entries:
        role = "control" if row["analysis_role"] == "normal" else "study"
        print(f"  {row['sample']}: {row['sample_type']} ({role}); {len(row['source'].files)} FASTQs")
    print(f"Platform: {args.mode}; analysis: {args.analysis}; threads: {args.threads}; tools: {args.backend}")
    if args.analysis != "methylation":
        print(f"CNA method: {config.get(args.mode + '_caller')}; bin size: {config.get(args.mode + '_binsize_kb')} kb")
        print("CNA interpretation reports: " + ("enabled" if config.get("run_cna_classifier") else "disabled"))
        if config.get("run_cna_classifier"):
            print(f"Report context: {config['cna_classifier_sample_set']}; local catalog models: {config['knowledge_catalog_llm']}; GISTIC: {config['run_gistic']}")
    if args.analysis != "cna":
        print(f"Methylation classifier: {config['methylation_classifier']}; GPU allowed: {config['methylation_gpu']}")
    if config.get("run_variants"):
        print(f"Small variants: {config['variant_callers']}; preservation: {config['variant_specimen_type']}; ANNOVAR: {config['variant_annovar']}")
    reference = "download prebuilt indexes when needed" if config["hg38_auto_download"] else ("build missing indexes locally" if args.build_reference else "reuse prepared reference")
    print(f"Reference: {reference}\nReference folder: {config['lpwgs_root']}\nSample types: {config['sample_metadata']}")
    print("Analysis may download reference files and prepare missing tools when it starts.")
    parser = cli.build_parser()
    checked = command_check(parser.parse_args(["check", "--config", str(config_path)]))
    if checked:
        print("Settings are saved. Correct the reported errors and run check again before analysis.")
        return checked
    action = "run" if run_requested else _ask(None, "Final action", default="save", choices=("run", "save"))
    if action == "run":
        return _run_setup(config_path, args)
    print("Saved without starting analysis. Run later with:")
    print(shlex.join(["oncotracer", "run", "--backend", args.backend, "--config", str(config_path)]))
    return 0
