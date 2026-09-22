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
from .system_check import resource_report
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
    "hg38_auto_download": "true downloads prebuilt hg38 indexes at run time; false reuses existing references or builds missing indexes locally.",
    "outdir": "Analysis results. Use a new directory for a different analysis.",
    "threads": "CPU worker threads requested; some tools also use helper threads.",
    "illumina_samplesheet": "CSV linking sample names to existing FASTQ files.",
    "ont_folder": "Existing FASTQ parent directory; with ont_single_sample, this is one nonbarcoded library.",
    "ont_single_sample": "true treats ont_folder itself as one nonbarcoded library; ont_barcodes must be a single dot.",
    "ont_barcodes": "Comma-separated barcode folders. Only these samples are analyzed.",
    "ont_sample_names": "Names in the same order as ont_barcodes. Tumor samples are analyzed independently.",
    "methylation": "Request methylation analysis in addition to the default copy-number workflow.",
    "methylation_only": "true skips copy-number analysis; false runs both requested branches.",
    "methylation_classifier": "marlin: leukemia research; sturgeon: CNS-tumor research.",
    "methylation_gpu": "false keeps methylation on CPU. Existing BAM calls never require GPU basecalling.",
    "methylation_modbam": "Existing modified-base BAM file or directory. Reuses MM/ML calls; aligns to hg38 on CPU.",
    "methylation_pod5_dir": "Existing raw-signal directory. Only FASTQ-selected read IDs are re-basecalled.",
    "run_cna_classifier": "Optional interpretation of copy-number changes; separate from methylation classification.",
    "run_variants": "Add native small-variant calling using locally installed tools.",
    "variant_specimen_type": "Documented preservation: fresh or ffpe. FFPE calls require artifact review.",
    "variant_callers": "Comma-separated callers compatible with the selected sequencing platform.",
    "variant_annovar": "auto uses an existing local ANNOVAR installation/database when available; off skips annotation.",
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
                f"setup input ended at {label}; use a terminal for interactive setup, "
                "or --non-interactive with explicit flags in scripts"
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


def _existing_hg38_parent(path: Path, mode: str) -> Path:
    """Accept an OncoTracer reference parent or its actual hg38 build folder."""
    from .reference_bundle import INDEX_FILES, MANIFESTS, reference_paths

    path = require_directory(
        path,
        "hg38 build (--hg38_build); omit PATH to download automatically when run starts",
    )
    if path.name == "samurai_hg38" and path.parent.name == "references":
        path = path.parents[1]
    elif (
        path.parent.name == "reference-cache"
        and path.parent.parent.name == ".oncotracer"
        and path == reference_paths(path.parents[2])[1]
    ):
        path = path.parents[2]
    candidates = reference_paths(path)
    reference = next(
        (candidate for candidate in candidates if os.path.lexists(candidate)),
        candidates[0],
    )
    kind = "bwa" if mode == "illumina" else "minimap2"
    required = (
        "genome.fa", "genome.fa.fai", "genome.dict",
        *INDEX_FILES[kind], MANIFESTS[kind],
    )
    missing = [
        name for name in required
        if not (reference / name).is_file() or not (reference / name).stat().st_size
    ]
    if missing:
        raise OncoTracerError(
            f"--hg38_build does not contain a prepared {mode} hg38 build: {reference}; "
            f"missing {', '.join(missing)}. Supply a compatible OncoTracer reference, "
            "or omit PATH to download one."
        )
    return path


def command_setup(args: argparse.Namespace) -> int:
    try:
        explicit_samples = any((args.samplesheet, args.fastq_1, args.fastq_2, args.sample_name,
                                args.barcodes, args.sample_names, args.status))
        if getattr(args, "variant_config", None):
            if (args.terminal or args.non_interactive or args.manual or args.run or
                    args.input_folder or args.reads_folder or explicit_samples):
                raise OncoTracerError(
                    "--variant-config opens the existing-BAM browser form; omit terminal, "
                    "manual, non-interactive, run and FASTQ input flags"
                )
            from .web import command_web

            args.start_dir = str(Path.cwd())
            return command_web(args)
        resuming = (args.run and args.project and
                    (Path(args.project).expanduser() / "config/run.yml").is_file())
        if args.input_folder and args.reads_folder:
            raise OncoTracerError("choose --input-folder or --reads-folder, not both")
        if args.input_folder and (args.non_interactive or args.manual or explicit_samples):
            raise OncoTracerError("--input-folder uses the interactive folder wizard; omit --non-interactive, --manual and explicit sample flags")
        if not args.non_interactive and not args.manual and not explicit_samples and not resuming:
            if not args.terminal and not args.run:
                from .web import command_web

                args.start_dir = str(Path.cwd())
                return command_web(args)
            from .wizard import command_wizard

            return command_wizard(args)
        return _command_setup(args)
    except OSError as error:
        raise OncoTracerError(
            f"setup could not access a file or folder: {error}"
        ) from error


def _run_setup(config_path: Path, args: argparse.Namespace) -> int:
    from . import cli

    if args.threads is not None and args.threads < 1:
        raise OncoTracerError("--threads must be positive")
    parser = cli.build_parser()
    check = parser.parse_args(["check", "--config", str(config_path)])
    check_output = io.StringIO()
    with contextlib.redirect_stdout(check_output):
        check_code = command_check(check)
    if check_code:
        print(check_output.getvalue(), end="")
        raise OncoTracerError("setup saved the configuration, but validation failed; correct it before running")
    print("Configuration OK. Preparing to run…", flush=True)
    # Installation is explicit through --run; reuse configured tools when present.
    install = cli._load_install_config()
    backend = args.backend
    image = getattr(args, 'image', None) or load_flat_yaml(config_path).get('docker_image')
    if getattr(args, 'image', None) and backend != 'docker':
        raise OncoTracerError('--image in setup requires --backend docker')
    if backend in {"conda", "poetry"}:
        required = [install.get(name + "_prefix") for name in
                    ("core", "qdnaseq", "ichorcna", "classifier", "gistic")]
        ready = all(value and Path(str(value)).is_dir() for value in required)
        if backend == "poetry":
            ready = ready and bool(install.get("poetry_prefix")) and Path(str(install["poetry_prefix"])).is_dir()
    elif backend == 'docker':
        ready = bool(shutil.which('docker'))
    elif backend == "singularity":
        ready = bool(install.get("sif")) and Path(str(install["sif"])).is_file()
    else:
        ready = backend == "host" or install.get("backend") == backend
    if not ready:
        print(f"Preparing the {backend} tools required for this run…", flush=True)
        install_flags = ['install', '--' + backend]
        if backend == 'docker' and image:
            install_flags += ['--image', str(image)]
        cli.command_install(parser.parse_args(install_flags))
    run = parser.parse_args(["run", "--config", str(config_path), "--backend", backend])
    if backend == 'docker' and image:
        run.image = str(image)
    if args.threads is not None:
        run.threads = args.threads
    return cli.command_run(run)



from .variants import SUPPORTED_CALLERS as VARIANT_CALLERS_BY_MODE, DEFAULT_CALLERS

VARIANT_DEFAULT_CALLER = {mode: ",".join(callers) for mode, callers in DEFAULT_CALLERS.items()}
VARIANT_RESOURCE_FIELDS = (
    "variant_targets_bed", "variant_clair3_model", "variant_clairsto_platform", "variant_clairsto_sif",
    "variant_tool_prefix", "variant_annovar_dir", "variant_annovar_db",
    "variant_ffperase_root", "variant_ffperase_models", "variant_ffperase_sif", "variant_ffperase_prefix",
    "variant_varlociraptor_scenario",
)
VARIANT_ASSESSMENT_FIELDS = ("variant_ffperase", "variant_varlociraptor", "variant_varlociraptor_fdr", "variant_varlociraptor_events", "variant_varlociraptor_sample")
VARIANT_BOOLEAN_FIELDS = ("variant_download_resources", "variant_accept_ffperase_license")
VARIANT_FIELDS = ("variant_specimen_type", "variant_callers", "variant_annovar", "variant_ont_profile", *VARIANT_BOOLEAN_FIELDS, *VARIANT_ASSESSMENT_FIELDS, *VARIANT_RESOURCE_FIELDS)


def _variant_values(args, mode: str, *, interactive: bool, analysis: str | None = None) -> dict[str, object]:
    """Collect native variant options; the engine owns their scientific validation."""
    enabled = getattr(args, "variants", False)
    if not isinstance(enabled, bool):
        raise OncoTracerError("--variants must be a boolean selection")
    if not enabled:
        if any(getattr(args, key, None) not in (None, "") for key in VARIANT_FIELDS):
            raise OncoTracerError("Variant options require --variants (Add small-variant calling).")
        return {"run_variants": False}
    if (analysis or args.analysis) == "methylation":
        raise OncoTracerError("Small-variant calling needs aligned reads: choose CNA or CNA and methylation, rather than methylation-only analysis.")
    if args.backend and args.backend not in {"host", "conda", "poetry", "docker"}:
        raise OncoTracerError("Small-variant calling needs --backend conda, host, poetry, or docker; Singularity remains unsupported.")
    if args.backend == 'docker' and (analysis or args.analysis) != 'cna':
        raise OncoTracerError('Docker small-variant calling currently supports --analysis cna only.')
    specimen = _ask(getattr(args, "variant_specimen_type", None),
                    "Sample preservation (--variant-specimen-type)",
                    choices=("fresh", "ffpe"), interactive=interactive)
    callers = _ask(getattr(args, "variant_callers", None),
                   "Variant callers (--variant-callers; comma-separated: " + ", ".join(VARIANT_CALLERS_BY_MODE[mode]) + ")",
                   default=VARIANT_DEFAULT_CALLER[mode], interactive=interactive)
    selected = [item.strip() for item in str(callers).split(",")]
    if not selected or any(item not in VARIANT_CALLERS_BY_MODE[mode] for item in selected):
        raise OncoTracerError(f"Variant callers for {mode}: {', '.join(VARIANT_CALLERS_BY_MODE[mode])}.")
    if len(selected) != len(set(selected)):
        raise OncoTracerError("Choose each variant caller only once.")
    values = {"run_variants": True, "variant_specimen_type": specimen,
              "variant_callers": ",".join(selected),
              "variant_annovar": _ask(getattr(args, "variant_annovar", None),
                  "ANNOVAR annotation (--variant-annovar; auto uses an existing local installation)",
                  default="auto", choices=("auto", "off"), interactive=interactive)}
    for key in VARIANT_BOOLEAN_FIELDS:
        value = getattr(args, key, None)
        if value is not None:
            if type(value) is not bool:
                raise OncoTracerError(f"{key} must be true or false")
            values[key] = value
    profile = getattr(args, "variant_ont_profile", None)
    if profile:
        values["variant_ont_profile"] = str(profile)
    for key in VARIANT_ASSESSMENT_FIELDS:
        value = getattr(args, key, None)
        if key == 'variant_ffperase' and specimen == 'ffpe' and mode == 'illumina':
            value = _ask(value, 'FFPErase artifact assessment', default='required', choices=('required','off'), interactive=interactive)
        if key == 'variant_varlociraptor':
            value = _ask(value, 'Varlociraptor local FDR assessment', default='off', choices=('required','off'), interactive=interactive)
        if value is not None:
            values[key] = value
    for key in VARIANT_RESOURCE_FIELDS:
        value = getattr(args, key, None)
        if key == "variant_clair3_model" and "clair3" in selected:
            value = _ask(value, "Chemistry-compatible Clair3 model folder or auto (--variant-clair3-model)", interactive=interactive)
            if str(value).strip().lower() == "auto":
                from .variant_model_assets import CLAIR3_PROFILES
                values["variant_ont_profile"] = _ask(profile, "ONT basecaller model profile (--variant-ont-profile)", choices=tuple(CLAIR3_PROFILES), interactive=interactive)
        if key == "variant_clairsto_platform" and "clairs_to" in selected:
            value = _ask(value, "ClairS-TO platform/model preset (--variant-clairsto-platform)", interactive=interactive)
        if value:
            from .variant_model_assets import is_auto_resource
            values[key] = str(value) if key == "variant_clairsto_platform" or is_auto_resource(key, value) else str(Path(value).expanduser().resolve())
    if args.backend == 'docker':
        from .docker_runtime import validate_docker_variants
        validate_docker_variants(dict(values, mode=mode))
    else:
        from .variants import resolve_variant_request
        resolve_variant_request(values, mode=mode)
    return values


def _command_setup(args: argparse.Namespace) -> int:
    from .cli import _load_install_config

    explicit_backend = args.backend
    args.backend = args.backend or str(_load_install_config().get("backend") or "conda")
    interactive = not args.non_interactive
    if getattr(args, "run", False) and args.project:
        existing = Path(args.project).expanduser().resolve() / "config/run.yml"
        if existing.is_file():
            if explicit_backend is None:
                args.backend = str(load_flat_yaml(existing).get('execution_backend') or args.backend)
            selection = ("mode", "analysis", "hg38_build", "reference_root", "build_reference",
                         "reference_cache", "input_folder", "reads_folder", "barcodes", "sample_names",
                         "samplesheet", "sample_name", "fastq_1", "fastq_2", "status",
                         "variants", *VARIANT_FIELDS,
                         "classifier", "modbam", "pod5_dir", "resources", "gpu",
                         "accept_sturgeon_license", *EXECUTABLES, *RESOURCE_FLAGS,
                         *RESOURCE_FILES["marlin"], *RESOURCE_FILES["sturgeon"])
            if args.gpu is not None or any(getattr(args, name, None) is not None and getattr(args, name) is not False
                   for name in selection):
                raise OncoTracerError(f"project already configured: {existing}; resume with setup --project "
                                     f"{shlex.quote(str(existing.parents[1]))} --run, or edit its YAML")
            print(f"Using saved configuration: {existing}")
            return _run_setup(existing, args)
    if getattr(args, 'image', None) and args.backend != 'docker':
        raise OncoTracerError('--image in setup requires --backend docker')
    if args.threads is None:
        args.threads = 8
    inferred_mode = "illumina" if args.fastq_1 or args.samplesheet else (
        "ont" if args.reads_folder or args.modbam or args.pod5_dir else None
    )
    mode = _ask(
        args.mode or inferred_mode,
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
    metadata_path = project / "config" / "sample_metadata.csv"
    protected = [config_path, sheet]
    if getattr(args, "_wizard_metadata", None):
        protected.append(metadata_path)
    for path in protected:
        if path.exists() or path.is_symlink():
            raise OncoTracerError(
                f"setup will not overwrite {path}; choose a new --project or edit the existing YAML"
            )
    supplied_reference = args.hg38_build or args.reference_root
    reference_root = (
        Path(supplied_reference).expanduser().resolve()
        if supplied_reference
        else project / "reference"
    )
    if reference_root.exists() and not reference_root.is_dir():
        raise OncoTracerError(
            f"--hg38_build must be a directory, not a FASTA or index file: {reference_root}"
        )
    if args.hg38_build:
        reference_root = _existing_hg38_parent(reference_root, mode)
    values: dict[str, object] = {
        "mode": mode,
        "lpwgs_root": str(reference_root),
        "hg38_auto_download": not (bool(supplied_reference) or args.build_reference),
        "reference_download_cache": str(
            Path(args.reference_cache).expanduser().absolute() if args.reference_cache
            else project.parent / ".oncotracer-reference-downloads"
        ),
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
        if getattr(args, "_wizard_rows", None):
            sample_rows = args._wizard_rows
            values["illumina_samplesheet"] = str(sheet)
        elif args.samplesheet:
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
        selected_folder = Path(
            _ask(args.reads_folder, "FASTQ parent folder (--reads-folder)", interactive=interactive)
        )
        if getattr(args, "_wizard_values", {}).get("ont_single_sample"):
            from .engine import _single_ont_sample_folder

            folder = _single_ont_sample_folder(selected_folder)
        else:
            folder = _resolve_fastq_pass(selected_folder)
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
        values.update(getattr(args, "_wizard_values", {}))
        samples = parse_ont_samples(values)
        _check_ont_fastqs(samples)
    values.update(getattr(args, "_wizard_values", {}))
    # Validate variant configuration before creating any project files. Browser
    # and terminal discoveries use exactly the same flat YAML contract.
    if "run_variants" not in getattr(args, "_wizard_values", {}):
        values.update(_variant_values(args, mode, interactive=interactive, analysis=analysis))
    elif values.get("run_variants"):
        from .variants import resolve_variant_request
        if args.backend not in {"host", "conda", "poetry", "docker"}:
            raise OncoTracerError("Small-variant calling needs --backend conda, host, poetry, or docker.")
        if args.backend == 'docker':
            if analysis != 'cna':
                raise OncoTracerError('Docker small-variant calling currently supports --analysis cna only.')
            from .docker_runtime import validate_docker_variants
            validate_docker_variants(dict(values, mode=mode))
        else:
            resolve_variant_request(values, mode=mode)

    values['execution_backend'] = args.backend
    if args.backend == 'docker' and getattr(args, 'image', None):
        values['docker_image'] = args.image

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
    if getattr(args, "_wizard_metadata", None):
        with metadata_path.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["sample", "sample_type", "analysis_role", "fastq_files"])
            writer.writeheader()
            writer.writerows(args._wizard_metadata)
        values["sample_metadata"] = str(metadata_path)
    with config_path.open("x", encoding="utf-8") as handle:
        handle.write(_render_config(values))
    print(
        f"\nConfiguration saved: {config_path}\nInputs stay in their existing folders. Results will be written to: {values['outdir']}"
    )
    if getattr(args, "_wizard_metadata", None):
        return 0
    if getattr(args, "run", False):
        return _run_setup(config_path, args)
    print("Start the analysis (validation runs automatically):")
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
    if values["hg38_auto_download"]:
        print("The run command will download prebuilt hg38 indexes automatically if needed.")
    elif args.build_reference:
        print(
            "Local hg38 indexing selected: run downloads genome source files as needed "
            "and builds missing indexes on CPU. This needs more RAM, temporary disk "
            "space and time than importing prebuilt indexes."
        )
    return 0


def command_check(args: argparse.Namespace) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    plan = None
    config = {}
    path = Path(args.config).expanduser().resolve()
    try:
        config = load_flat_yaml(require_file(path, "Configuration YAML"))
        mode = config.get("mode")
        if mode not in {"illumina", "ont"}:
            errors.append("mode: choose illumina or ont")
        if not config.get("outdir"):
            errors.append("outdir: specify where results should be saved")
        if "hg38_auto_download" in config and not isinstance(
            config["hg38_auto_download"], bool
        ):
            errors.append("hg38_auto_download: choose true or false")
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
            preview = contextlib.nullcontext()
            if config.get("execution_backend") == "docker":
                from .docker_runtime import docker_host_preview, validate_docker_variants

                validate_docker_variants(config)
                preview = docker_host_preview()
            with contextlib.redirect_stdout(output), preview:
                run_native(
                    path, dry_run=True, root=Path(args.root) if args.root else None
                )
            plan = json.loads(output.getvalue())
    except (OncoTracerError, OSError, ValueError) as error:
        errors.append(str(error))
    resources = resource_report(config)
    warnings.extend(resources["warnings"])
    result = {
        "valid": not errors,
        "config": str(path),
        "errors": errors,
        "warnings": warnings,
        "plan": plan,
        "resources": resources,
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
            hardware = resources["hardware"]
            available = hardware["ram_available_bytes"]
            if available is not None:
                print(f"  Hardware: {available / 1024**3:.1f} GiB RAM available; {hardware['cpu_workers_available']} CPU workers")
            print("  Capacity details: oncotracer system --config " + shlex.quote(str(path)))
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
        "setup",
        help="Open browser setup (use --terminal for terminal prompts)",
        description=(
            "Scan a FASTQ folder, select samples and types, choose analysis settings, "
            "then save or run in your browser at 127.0.0.1. Supplied flags prefill the form. "
            "Use --terminal for terminal prompts, --manual for "
            "per-file prompts or --non-interactive with explicit sample flags for scripts."
        ),
    )
    parser.add_argument("--terminal", action="store_true", help="use the terminal folder wizard instead of the browser")
    parser.add_argument("--port", type=int, default=8888, help="local browser port (default: 8888)")
    parser.add_argument("--no-browser", action="store_true", help="print the local URL without opening a browser automatically")
    parser.add_argument(
        "--variant-config", metavar="PATH",
        help="open existing-BAM variant settings from a variants YAML configuration",
    )
    reference_options = parser.add_mutually_exclusive_group()
    reference_options.add_argument(
        "--hg38_build",
        nargs="?",
        const="",
        metavar="PATH",
        help=(
            "reuse an existing OncoTracer hg38 reference parent; without PATH "
            "select the default prebuilt download into PROJECT/reference at run time"
        ),
    )
    reference_options.add_argument(
        "--build_reference",
        action="store_true",
        help=(
            "build missing hg38 indexes locally on CPU when run starts, under "
            "PROJECT/reference; needs more RAM, disk and time than prebuilt import"
        ),
    )
    reference_options.add_argument(
        "--reference-root",
        dest="reference_root",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--project", help="project folder to create (config/, reference/, results/)"
    )
    parser.add_argument(
        "--input-folder", metavar="PATH",
        help="FASTQ folder to scan in the interactive wizard (either platform)",
    )
    parser.add_argument(
        "--manual", action="store_true",
        help="use per-file/barcode prompts instead of the folder wizard",
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
        help="execution backend (default: saved installation, otherwise conda)",
    )
    parser.add_argument('--image', help='Docker image tag or digest; saved as docker_image and reused by setup --run')
    parser.add_argument(
        "--run", action="store_true",
        help="validate, prepare missing backend tools, and run; also resumes an existing project"
    )
    parser.add_argument(
        "--reference-cache", help="shared verified download cache (default: a sibling of the project)"
    )
    parser.add_argument(
        "--threads", type=int, help="CPU worker threads (wizard suggests from hardware; manual/scripted default: 8)"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; use defaults and require flags for missing required answers",
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
    illumina.add_argument(
        "--fastq-2",
        help="read 2 FASTQ file; for single-end, press Enter at the prompt or omit with --non-interactive",
    )
    illumina.add_argument(
        "--status",
        choices=("tumor", "normal"),
        help="single Illumina library status (default: tumor)",
    )
    variant = parser.add_argument_group("Optional native small-variant calling")
    variant.add_argument("--variants", action="store_true", help="add native small-variant calling to the selected analysis")
    variant.add_argument("--variant-specimen-type", choices=("fresh", "ffpe"),
                         help="sample preservation; required when variants are enabled (one preservation type per project)")
    variant.add_argument("--variant-callers", help="comma-separated callers: Illumina mutect2/freebayes/bcftools; ONT clair3/clairs_to")
    variant.add_argument("--variant-targets-bed", metavar="PATH", help="optional hg38 BED of variant-calling intervals")
    variant.add_argument("--variant-clair3-model", metavar="PATH|auto", help="existing Clair3 model or auto to prepare the explicitly selected profile at run time")
    variant.add_argument("--variant-ont-profile", help="exact supported Clair3 basecaller profile for automatic model preparation")
    variant.add_argument("--variant-download-resources", action="store_true", default=None, help="prepare missing public FFPERASE source/models at RUN only; tools and ANNOVAR are never downloaded")
    variant.add_argument("--variant-accept-ffperase-license", action="store_true", default=None, help="acknowledge upstream FFPERASE terms before automatic source/model download")
    variant.add_argument("--variant-clairsto-platform", help="installed ClairS-TO platform/model preset")
    variant.add_argument("--variant-clairsto-sif", metavar="PATH", help="optional existing ClairS-TO SIF image; uses local Apptainer/Singularity without downloading")
    variant.add_argument("--variant-tool-prefix", metavar="PATH", help="environment prefix containing installed variant tools")
    variant.add_argument("--variant-annovar", choices=("auto", "off"), help="use existing local ANNOVAR if detected (default: auto), or disable annotation")
    variant.add_argument("--variant-annovar-dir", metavar="PATH", help="optional existing ANNOVAR installation folder")
    variant.add_argument("--variant-annovar-db", metavar="PATH", help="optional existing ANNOVAR database folder")
    variant.add_argument('--variant-ffperase', choices=('required','off'), help='FFPErase assessment; default required for Illumina FFPE, off otherwise')
    for key, label in [('root','upstream nf-ffperase source'),('models','directory with SNV and indel joblib models'),('sif','existing FFPErase SIF'),('prefix','compatible native FFPErase Python environment')]:
        variant.add_argument('--variant-ffperase-'+key, metavar='PATH', help=label)
    variant.add_argument('--variant-varlociraptor', choices=('required','off'), help='additional probabilistic assessment and local FDR control (default off)')
    variant.add_argument('--variant-varlociraptor-fdr', type=float, help='local FDR threshold, default 0.05')
    variant.add_argument('--variant-varlociraptor-scenario', metavar='PATH', help='optional scenario YAML; default assesses single-sample presence, not somatic origin')
    variant.add_argument('--variant-varlociraptor-events', help='comma-separated uppercase scenario events; default PRESENT')
    variant.add_argument('--variant-varlociraptor-sample', help='observed sample name in custom scenario; default sample')
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
    parser.set_defaults(gpu=None)
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
