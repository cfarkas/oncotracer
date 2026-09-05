"""Guided configuration using the same public YAML consumed by `run`."""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import shlex
import shutil
from pathlib import Path

from .engine import (
    _resolve_fastq_pass,
    _safe_sample,
    parse_illumina_samplesheet,
    parse_ont_samples,
    run_native,
)
from .methylation import SUPPORTED_CLASSIFIER_INTERFACE_COMMITS, directory_sha256
from .runtime import (
    OncoTracerError,
    load_flat_yaml,
    render_flat_yaml,
    require_directory,
    require_file,
    sha256_file,
)


RESOURCE_FILES = {
    "marlin": {
        "marlin_model": "MARLIN model (.hdf5)",
        "marlin_features": "MARLIN feature order (.RData)",
        "marlin_class_annotations": "MARLIN class names (.xlsx)",
        "marlin_probe_bed": "MARLIN hg38 probe coordinates (uncompressed .bed)",
    },
    "sturgeon": {
        "sturgeon_model": "Sturgeon model (.zip)",
        "sturgeon_probes": "Sturgeon hg38 probe coordinates (.bed)",
    },
}
EXECUTABLES = {
    "methylation_dorado_executable": (
        "dorado",
        "Dorado executable (alignment/basecalling)",
    ),
    "methylation_modkit_executable": (
        "modkit",
        "Modkit executable (methylation extraction)",
    ),
    "methylation_samtools_executable": (
        "samtools",
        "samtools executable (BAM processing)",
    ),
    "marlin_rscript": ("Rscript", "Rscript in your MARLIN environment"),
    "marlin_python": ("python", "Python in your MARLIN environment"),
    "sturgeon_executable": ("sturgeon", "Sturgeon executable"),
}
RESOURCE_FLAGS = {
    "methylation_dorado_executable": "--dorado",
    "methylation_modkit_executable": "--modkit",
    "methylation_samtools_executable": "--samtools",
    "methylation_dorado_model": "--dorado-model",
    "methylation_dorado_modbase_model": "--modified-base-model",
}
COMMENTS = {
    "mode": "Sequencing platform: illumina or ont.",
    "lpwgs_root": "Reference cache. Downloads and reusable hg38 files go here.",
    "outdir": "Analysis results. Use a new directory for a different analysis.",
    "threads": "CPU worker threads requested; some tools also use helper threads.",
    "illumina_samplesheet": "CSV linking sample names to existing FASTQ files.",
    "ont_folder": "Existing FASTQ parent directory; each selected barcode is a subfolder.",
    "ont_barcodes": "Comma-separated barcode folders. Only these samples are analyzed.",
    "ont_sample_names": "Names in the same order as ont_barcodes. Tumor samples are analyzed independently.",
    "methylation": "Request methylation analysis in addition to the default copy-number workflow.",
    "methylation_only": "true skips copy-number analysis; false runs both requested branches.",
    "methylation_classifier": "marlin: leukemia research; sturgeon: CNS-tumor research.",
    "methylation_gpu": "false keeps methylation on CPU. Existing BAM calls never require GPU basecalling.",
    "methylation_modbam": "Existing modified-base BAM file or directory. Reuses MM/ML calls; aligns to hg38 on CPU.",
    "methylation_pod5_dir": "Existing raw-signal directory. Only FASTQ-selected read IDs are re-basecalled.",
    "run_cna_classifier": "Optional interpretation of copy-number changes; separate from methylation classification.",
}


def _ask(
    value, label: str, *, default=None, choices=None, interactive: bool, required=True
):
    if value is not None:
        return value
    if not interactive:
        if default is not None or not required:
            return default
        raise OncoTracerError(
            f"setup needs {label}; provide the matching flag shown in 'oncotracer setup --help', or omit --non-interactive"
        )
    while True:
        suffix = f" [{default}]" if default is not None else ""
        if choices:
            suffix = f" ({'/'.join(choices)})" + suffix
        try:
            answer = input(f"{label}{suffix}: ").strip()
        except EOFError as error:
            raise OncoTracerError(
                "setup input ended; use --non-interactive with explicit flags in scripts"
            ) from error
        answer = answer or default
        if not answer and not required:
            return None
        if answer and (not choices or answer in choices):
            return answer
        print("Enter " + (", ".join(choices) if choices else "a value") + ".")


def _executable(value: str, label: str) -> str:
    candidate = (
        str(Path(value).expanduser()) if os.sep in value else shutil.which(value)
    )
    if (
        not candidate
        or not Path(candidate).is_file()
        or not os.access(candidate, os.X_OK)
    ):
        raise OncoTracerError(
            f"{label} is unavailable: {value}. Install it first, then supply its executable path."
        )
    # Preserve environment symlinks: resolving a Python symlink can leave its venv.
    return str(Path(candidate).absolute())


def _resource_keys(classifier: str) -> list[str]:
    return [
        *list(EXECUTABLES)[:3],
        *(
            ["marlin_rscript", "marlin_python"]
            if classifier == "marlin"
            else ["sturgeon_executable"]
        ),
    ]


def _check_ont_fastqs(samples) -> None:
    for sample in samples:
        if not any(
            p.is_file() and p.stat().st_size
            for pattern in ("*.fastq", "*.fastq.gz", "*.fq", "*.fq.gz")
            for p in sample.fastq_dir.rglob(pattern)
        ):
            raise OncoTracerError(
                f"No FASTQs in {sample.fastq_dir}; select a folder containing completed FASTQ batches"
            )


def _render_config(values: dict[str, object]) -> str:
    blocks = [
        "# Generated by oncotracer setup. Edit paths here, then run oncotracer check.\n"
    ]
    for key, value in values.items():
        if key in COMMENTS:
            blocks.append(f"# {COMMENTS[key]}\n")
        blocks.append(render_flat_yaml({key: value}))
    return "".join(blocks)


def command_setup(args: argparse.Namespace) -> int:
    try:
        return _command_setup(args)
    except OSError as error:
        raise OncoTracerError(
            f"setup could not access a file or folder: {error}"
        ) from error


def _command_setup(args: argparse.Namespace) -> int:
    interactive = not args.non_interactive
    mode = _ask(
        args.mode,
        "Sequencing platform (--mode)",
        choices=("illumina", "ont"),
        interactive=interactive,
    )
    analysis = _ask(
        args.analysis,
        "Analysis (--analysis; cna=copy-number)",
        default="cna",
        choices=("cna", "methylation", "both"),
        interactive=interactive,
    )
    if mode == "illumina" and analysis != "cna":
        raise OncoTracerError(
            "methylation requires ONT reads; choose --mode ont or --analysis cna"
        )
    incompatible = (
        ("reads_folder", "barcodes", "sample_names")
        if mode == "illumina"
        else ("samplesheet", "sample_name", "fastq_1", "fastq_2", "status")
    )
    for key in incompatible:
        if getattr(args, key):
            raise OncoTracerError(
                f"--{key.replace('_', '-')} does not apply to --mode {mode}; check the selected platform"
            )
    if args.samplesheet and any(
        (args.sample_name, args.fastq_1, args.fastq_2, args.status)
    ):
        raise OncoTracerError(
            "choose --samplesheet or single-library flags (--sample-name, --fastq-1, --fastq-2, --status), not both"
        )
    if analysis == "cna" and any(
        getattr(args, key)
        for key in (
            "classifier",
            "modbam",
            "pod5_dir",
            "resources",
            "gpu",
            "accept_sturgeon_license",
            *EXECUTABLES,
            *RESOURCE_FLAGS,
            *RESOURCE_FILES["marlin"],
            *RESOURCE_FILES["sturgeon"],
        )
    ):
        raise OncoTracerError(
            "methylation flags need --analysis methylation or --analysis both; --analysis cna runs copy-number analysis only"
        )
    project = (
        Path(
            _ask(args.project, "Project directory (--project)", interactive=interactive)
        )
        .expanduser()
        .resolve()
    )
    config_path = project / "config" / "run.yml"
    sheet = project / "config" / "samplesheet.csv"
    for path in (config_path, sheet):
        if path.exists() or path.is_symlink():
            raise OncoTracerError(
                f"setup will not overwrite {path}; choose a new --project or edit the existing YAML"
            )
    values: dict[str, object] = {
        "mode": mode,
        "lpwgs_root": str(project / "reference"),
        "outdir": str(project / "results"),
        "threads": args.threads,
        "force": False,
        "run_cna_classifier": False,
        "knowledge_web": False,
    }
    if args.threads < 1:
        raise OncoTracerError("--threads must be positive")
    sample_rows = None
    if mode == "illumina":
        if args.samplesheet:
            supplied = require_file(Path(args.samplesheet), "Illumina samplesheet")
            parse_illumina_samplesheet(supplied)
            values["illumina_samplesheet"] = str(supplied)
        else:
            name = _safe_sample(
                _ask(
                    args.sample_name,
                    "Sample name (--sample-name)",
                    interactive=interactive,
                )
            )
            fastq1 = require_file(
                Path(
                    _ask(
                        args.fastq_1,
                        "Read 1 FASTQ (--fastq-1)",
                        interactive=interactive,
                    )
                ),
                "Read 1 FASTQ",
            )
            mate = _ask(
                args.fastq_2,
                "Read 2 FASTQ (--fastq-2; Enter for single-end)",
                interactive=interactive,
                required=False,
            )
            fastq2 = require_file(Path(mate), "Read 2 FASTQ") if mate else None
            sample_rows = [
                [
                    name,
                    str(fastq1),
                    str(fastq2) if fastq2 else "",
                    args.status or "tumor",
                ]
            ]
            values["illumina_samplesheet"] = str(sheet)
        values.update(illumina_caller="qdnaseq", illumina_binsize_kb=100)
    else:
        folder = _resolve_fastq_pass(
            Path(
                _ask(
                    args.reads_folder,
                    "FASTQ parent folder (--reads-folder)",
                    interactive=interactive,
                )
            )
        )
        available = [
            p.name
            for p in sorted(folder.iterdir())
            if p.is_dir() and (p.name.startswith("barcode") or p.name == "unclassified")
        ]
        print(
            f"FASTQ folder: {folder}\nAvailable folders: {', '.join(available) or 'none'}"
        )
        selected = _ask(
            args.barcodes,
            "Barcode folders to include, comma separated (--barcodes)",
            interactive=interactive,
        )
        names = _ask(
            args.sample_names,
            "Sample names in the same order (--sample-names)",
            default=selected,
            interactive=interactive,
        )
        values.update(
            ont_folder=str(folder),
            ont_barcodes=selected,
            ont_sample_names=names,
            ont_caller="ichorcna",
            ont_binsize_kb=500,
        )
        samples = parse_ont_samples(values)
        _check_ont_fastqs(samples)
    if analysis != "cna":
        classifier = _ask(
            args.classifier,
            "Methylation classifier (--classifier; marlin=leukemia, sturgeon=CNS)",
            choices=("marlin", "sturgeon"),
            interactive=interactive,
        )
        if args.backend not in {"host", "conda", "poetry"}:
            raise OncoTracerError(
                "methylation needs --backend conda, host, or poetry; the current containers do not include its tools"
            )
        resources = (
            load_flat_yaml(require_file(Path(args.resources), "Resource YAML"))
            if args.resources
            else {}
        )
        values.update(
            methylation=True,
            methylation_only=analysis == "methylation",
            methylation_classifier=classifier,
            methylation_gpu=bool(args.gpu),
        )
        source_kind = (
            "modbam"
            if args.modbam
            else (
                "pod5"
                if args.pod5_dir
                else _ask(
                    None,
                    "Methylation input (modbam=existing calls; pod5=raw signal)",
                    default="modbam",
                    choices=("modbam", "pod5"),
                    interactive=interactive,
                )
            )
        )
        source = args.modbam if source_kind == "modbam" else args.pod5_dir
        source = (
            Path(
                _ask(
                    source,
                    f"Input path (--{'modbam' if source_kind == 'modbam' else 'pod5-dir'})",
                    interactive=interactive,
                )
            )
            .expanduser()
            .resolve()
        )
        values[
            "methylation_modbam" if source_kind == "modbam" else "methylation_pod5_dir"
        ] = str(source)
        for key in _resource_keys(classifier):
            program, label = EXECUTABLES[key]
            supplied = getattr(args, key) or resources.get(key)
            # A MARLIN environment must be selected explicitly; system R/Python often lack its packages.
            default = shutil.which(program) if not key.startswith("marlin_") else None
            flag = RESOURCE_FLAGS.get(key, "--" + key.replace("_", "-"))
            values[key] = _executable(
                _ask(
                    supplied,
                    f"{label} ({flag})",
                    default=default,
                    interactive=interactive,
                ),
                label,
            )
        files = dict(RESOURCE_FILES[classifier])
        if source_kind == "pod5":
            files.update(
                methylation_dorado_model="Dorado basecalling model directory",
                methylation_dorado_modbase_model="Matching Dorado 5mCG/5hmCG model directory",
            )
            if not args.gpu:
                print(
                    "CPU POD5 basecalling can take days. Existing modified-base BAMs avoid this step."
                )
        for key, label in files.items():
            supplied = getattr(args, key) or resources.get(key)
            flag = RESOURCE_FLAGS.get(key, "--" + key.replace("_", "-"))
            value = Path(_ask(supplied, f"{label} ({flag})", interactive=interactive))
            is_model_tree = key in {
                "methylation_dorado_model",
                "methylation_dorado_modbase_model",
            }
            value = (
                require_directory(value, label)
                if is_model_tree
                else require_file(value, label)
            )
            digest = directory_sha256(value) if is_model_tree else sha256_file(value)
            expected = resources.get(key + "_sha256")
            if expected and str(expected).lower() != digest:
                raise OncoTracerError(
                    f"{key} differs from the hash in --resources; verify the asset before using it"
                )
            values[key] = str(value)
            values[key + "_sha256"] = digest
        values[f"{classifier}_interface_contract_commit"] = (
            SUPPORTED_CLASSIFIER_INTERFACE_COMMITS[classifier]
        )
        if classifier == "sturgeon":
            acknowledged = (
                args.accept_sturgeon_license
                or resources.get("sturgeon_license_acknowledged") is True
            )
            if not acknowledged:
                acknowledged = (
                    _ask(
                        None,
                        "Have you obtained Sturgeon and accepted its applicable license?",
                        default="no",
                        choices=("yes", "no"),
                        interactive=interactive,
                    )
                    == "yes"
                )
            if not acknowledged:
                raise OncoTracerError(
                    "Sturgeon license acknowledgement is required; setup cannot grant the license"
                )
            values["sturgeon_license_acknowledged"] = True
        # Validate all source files, tools and local hashes before writing the project.
        from .methylation import resolve_methylation_request

        resolve_methylation_request(values, mode=mode)

    project.joinpath("config").mkdir(parents=True, exist_ok=True)
    if sample_rows is not None:
        with sheet.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
            writer.writerows(sample_rows)
    with config_path.open("x", encoding="utf-8") as handle:
        handle.write(_render_config(values))
    print(
        f"\nConfiguration saved: {config_path}\nInputs stay in their existing folders. Results will be written to: {values['outdir']}"
    )
    print("Review the commented YAML, check it, then start the analysis:")
    print(shlex.join(["oncotracer", "check", "--config", str(config_path)]))
    print(
        shlex.join(
            [
                "oncotracer",
                "run",
                "--backend",
                args.backend,
                "--config",
                str(config_path),
            ]
        )
    )
    print("Setup has not run an analysis or installed optional resources.")
    return 0


def command_check(args: argparse.Namespace) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    plan = None
    path = Path(args.config).expanduser().resolve()
    try:
        config = load_flat_yaml(require_file(path, "Configuration YAML"))
        mode = config.get("mode")
        if mode not in {"illumina", "ont"}:
            errors.append("mode: choose illumina or ont")
        if not config.get("outdir"):
            errors.append("outdir: specify where results should be saved")
        enabled = (
            config.get("methylation") is True or config.get("methylation_only") is True
        )
        if enabled:
            classifier = config.get("methylation_classifier")
            if classifier not in RESOURCE_FILES:
                errors.append(
                    "methylation_classifier: choose marlin (leukemia) or sturgeon (CNS)"
                )
            else:
                fields = dict(RESOURCE_FILES[classifier])
                if not config.get("methylation_modbam"):
                    fields.update(
                        methylation_pod5_dir="raw signal directory",
                        methylation_dorado_model="Dorado base model",
                        methylation_dorado_modbase_model="Dorado modified-base model",
                    )
                else:
                    fields["methylation_modbam"] = (
                        "existing modified-base BAM file or directory"
                    )
                for key, label in fields.items():
                    value = config.get(key)
                    if not value:
                        errors.append(f"{key}: missing {label}")
                    elif not Path(str(value)).expanduser().exists():
                        errors.append(f"{key}: path does not exist: {value}")
                    if key in RESOURCE_FILES[classifier] and not config.get(
                        key + "_sha256"
                    ):
                        errors.append(
                            f"{key}_sha256: missing; setup computes this from your local file"
                        )
                for key in _resource_keys(classifier):
                    program, label = EXECUTABLES[key]
                    try:
                        _executable(str(config.get(key) or program), label)
                    except OncoTracerError as error:
                        errors.append(f"{key}: {error}")
            if not config.get("methylation_gpu") and not config.get(
                "methylation_modbam"
            ):
                warnings.append(
                    "CPU POD5 basecalling can take days. Consider existing modified-base BAMs if available."
                )
            warnings.append(
                "Configuration validity does not establish methylome quality. Classification still needs covered classifier probes."
            )
        if mode in {"illumina", "ont"}:
            try:
                if mode == "ont":
                    _check_ont_fastqs(parse_ont_samples(config))
                elif config.get("illumina_samplesheet"):
                    parse_illumina_samplesheet(
                        Path(str(config["illumina_samplesheet"]))
                    )
                else:
                    errors.append(
                        "illumina_samplesheet: specify the CSV linking sample names to FASTQs"
                    )
            except (OncoTracerError, OSError) as error:
                errors.append(str(error))
        if not errors:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                run_native(
                    path, dry_run=True, root=Path(args.root) if args.root else None
                )
            plan = json.loads(output.getvalue())
    except (OncoTracerError, OSError, ValueError) as error:
        errors.append(str(error))
    result = {
        "valid": not errors,
        "config": str(path),
        "errors": errors,
        "warnings": warnings,
        "plan": plan,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            "Configuration OK"
            if not errors
            else f"Configuration needs {len(errors)} correction(s):"
        )
        for error in errors:
            print(f"  - {error}")
        for warning in warnings:
            print(f"  Note: {warning}")
        if plan:
            print(
                f"  Samples: {', '.join(plan['samples'])}\n  Results: {plan['outdir']}\n  CPU threads: {plan['threads']}"
            )
            print("  Analysis: " + " -> ".join(plan["stages"]))
            print(
                "No analysis or downloads were started. Check the installed tools with oncotracer doctor before running."
            )
        else:
            print(
                "Use oncotracer setup to create a commented configuration, or edit the fields above and check again."
            )
    return 2 if errors else 0


def add_setup_commands(subparsers) -> None:
    parser = subparsers.add_parser(
        "setup", help="Create a readable configuration with prompts or explicit flags"
    )
    parser.add_argument(
        "--project", help="project folder to create (config/, reference/, results/)"
    )
    parser.add_argument(
        "--mode", choices=("illumina", "ont"), help="sequencing platform"
    )
    parser.add_argument(
        "--analysis",
        choices=("cna", "methylation", "both"),
        help="cna=copy-number; methylation=ONT methylation only; both=both branches",
    )
    parser.add_argument(
        "--backend",
        choices=("host", "conda", "docker", "singularity", "poetry"),
        default="conda",
        help="backend used in the printed run command (default: conda)",
    )
    parser.add_argument(
        "--threads", type=int, default=8, help="CPU worker threads (default: 8)"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="require flags for missing answers; never prompt",
    )
    ont = parser.add_argument_group("ONT samples")
    ont.add_argument(
        "--reads-folder",
        help="parent of barcode FASTQ folders, or a MinKNOW run containing fastq_pass",
    )
    ont.add_argument(
        "--barcodes",
        help="comma-separated barcode folders; choose unclassified explicitly if appropriate",
    )
    ont.add_argument(
        "--sample-names",
        help="comma-separated names, in barcode order (default: barcode names)",
    )
    illumina = parser.add_argument_group("Illumina samples")
    illumina.add_argument(
        "--samplesheet",
        help="existing CSV with sample,fastq_1,fastq_2,status; for multiple libraries",
    )
    illumina.add_argument("--sample-name", help="name for one Illumina library")
    illumina.add_argument("--fastq-1", help="read 1 FASTQ file")
    illumina.add_argument("--fastq-2", help="read 2 FASTQ file; omit for single-end")
    illumina.add_argument(
        "--status",
        choices=("tumor", "normal"),
        help="single Illumina library status (default: tumor)",
    )
    meth = parser.add_argument_group("ONT methylation")
    meth.add_argument(
        "--classifier",
        choices=("marlin", "sturgeon"),
        help="marlin=leukemia; sturgeon=CNS-tumor research",
    )
    inputs = meth.add_mutually_exclusive_group()
    inputs.add_argument(
        "--modbam", help="existing modified-base BAM file or folder; reuse calls on CPU"
    )
    inputs.add_argument(
        "--pod5-dir", help="raw POD5 directory; needs local Dorado basecalling models"
    )
    meth.add_argument(
        "--resources",
        help="reuse tool/model paths and hashes from a previous setup YAML",
    )
    device = meth.add_mutually_exclusive_group()
    device.add_argument(
        "--gpu", action="store_true", help="allow GPU basecalling and MARLIN inference"
    )
    device.add_argument(
        "--cpu", dest="gpu", action="store_false", help="CPU only (default)"
    )
    parser.set_defaults(gpu=False)
    meth.add_argument(
        "--accept-sturgeon-license",
        action="store_true",
        help="confirm that you obtained and accepted the applicable Sturgeon license",
    )
    resources = parser.add_argument_group(
        "Local resource paths (setup records hashes automatically)"
    )
    for key, (_, label) in EXECUTABLES.items():
        resources.add_argument(
            RESOURCE_FLAGS.get(key, "--" + key.replace("_", "-")),
            dest=key,
            metavar="PATH",
            help=label,
        )
    for key, label in {
        **RESOURCE_FILES["marlin"],
        **RESOURCE_FILES["sturgeon"],
        "methylation_dorado_model": "Dorado base model directory (POD5 only)",
        "methylation_dorado_modbase_model": "Matching Dorado 5mCG/5hmCG directory (POD5 only)",
    }.items():
        resources.add_argument(
            RESOURCE_FLAGS.get(key, "--" + key.replace("_", "-")),
            dest=key,
            metavar="PATH",
            help=label,
        )
    parser.set_defaults(func=command_setup)
    check = subparsers.add_parser(
        "check",
        help="Check a configuration and explain corrections without starting analysis",
    )
    check.add_argument("--config", required=True, help="YAML file to check")
    check.add_argument("--json", action="store_true", help="machine-readable report")
    check.add_argument("--root", help=argparse.SUPPRESS)
    check.set_defaults(func=command_check)
