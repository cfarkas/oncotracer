#!/usr/bin/env python3
"""Apply guarded beginner-runtime fixes to the native v2 source tree."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "oncotracer_cli" / "cli.py"
ENGINE = ROOT / "oncotracer_cli" / "engine.py"
RUNTIME = ROOT / "oncotracer_cli" / "runtime.py"


def replace_exact(path: Path, old: str, new: str, *, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    observed = text.count(old)
    if observed != count:
        raise SystemExit(f"{path}: expected {count} exact match(es), found {observed}")
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"{path}: start marker not found: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{path}: end marker not found: {end!r}")
    if text.find(start, start_index + 1) >= 0:
        raise SystemExit(f"{path}: start marker is not unique: {start!r}")
    path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


def patch_cli() -> None:
    replace_exact(
        CLI,
        '        destination.parent.mkdir(parents=True, exist_ok=True)\n',
        '        if not args.dry_run:\n            destination.parent.mkdir(parents=True, exist_ok=True)\n',
    )
    replace_exact(
        CLI,
        '    docker = require_command("docker")\n',
        '    docker = shutil.which("docker") or ("docker" if args.dry_run else require_command("docker"))\n',
        count=2,
    )
    replace_exact(
        CLI,
        '    executable = _singularity_command()\n    if not executable:\n        raise OncoTracerError("Apptainer or Singularity is required for --singularity")\n',
        '    executable = _singularity_command() or ("apptainer" if args.dry_run else "")\n    if not executable:\n        raise OncoTracerError("Apptainer or Singularity is required for --singularity")\n',
    )
    replace_exact(
        CLI,
        '    destination.parent.mkdir(parents=True, exist_ok=True)\n    if args.force and destination.exists() and not args.dry_run:\n',
        '    if not args.dry_run:\n        destination.parent.mkdir(parents=True, exist_ok=True)\n    if args.force and destination.exists() and not args.dry_run:\n',
    )
    replace_exact(
        CLI,
        '    executable = str(install.get("singularity_command") or _singularity_command())\n'
        '    if not executable:\n'
        '        raise OncoTracerError("Apptainer or Singularity is required")\n'
        '    sif_value = args.sif or install.get("sif")\n'
        '    if not sif_value:\n'
        '        raise OncoTracerError("no SIF is configured; run \'oncotracer install --singularity\'")\n'
        '    sif = require_file(Path(str(sif_value)), "OncoTracer SIF")\n',
        '    executable = str(install.get("singularity_command") or _singularity_command() or ("apptainer" if args.dry_run else ""))\n'
        '    if not executable:\n'
        '        raise OncoTracerError("Apptainer or Singularity is required")\n'
        '    sif_value = args.sif or install.get("sif")\n'
        '    if not sif_value and not args.dry_run:\n'
        '        raise OncoTracerError("no SIF is configured; run \'oncotracer install --singularity\'")\n'
        '    sif_candidate = Path(str(sif_value or "/path/to/oncotracer-2.0.0.sif")).expanduser().resolve()\n'
        '    sif = sif_candidate if args.dry_run else require_file(sif_candidate, "OncoTracer SIF")\n',
    )

    quickstart1 = '''def prepare_quickstart1(root_path: Path, *, dry_run: bool = False) -> tuple[Path, Path]:
    from .runtime import render_flat_yaml

    root_path = root_path.expanduser().resolve()
    configs = root_path / "configs"
    illumina_config = configs / "illumina.quickstart.yml"
    ont_config = configs / "ont.quickstart.yml"
    if dry_run:
        for url, relative, size, md5 in QS1_FILES:
            print(
                f"Would download and validate {url} -> {root_path / relative} "
                f"(bytes={size}, md5={md5})",
                file=sys.stderr,
            )
        print(f"Would write {illumina_config}", file=sys.stderr)
        print(f"Would write {ont_config}", file=sys.stderr)
        return illumina_config, ont_config

    for url, relative, size, md5 in QS1_FILES:
        download(url, root_path / relative, expected_bytes=size, expected_md5=md5)
    illumina_dir = root_path / "public" / "illumina_ERR12341627"
    sheet = illumina_dir / "illumina.samplesheet.csv"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample", "fastq_1", "fastq_2", "status"])
        writer.writerow(
            [
                "ERR12341627",
                illumina_dir / "ERR12341627_1.fastq.gz",
                illumina_dir / "ERR12341627_2.fastq.gz",
                "tumor",
            ]
        )
    configs.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        illumina_config,
        render_flat_yaml(
            {
                "mode": "illumina",
                "lpwgs_root": root_path,
                "outdir": root_path / "runs" / "illumina",
                "illumina_samplesheet": sheet,
                "illumina_analysis_type": "solid_biopsy",
                "illumina_caller": "qdnaseq",
                "illumina_binsize_kb": 100,
                "run_cna_classifier": False,
                "force": False,
            }
        ),
    )
    atomic_write_text(
        ont_config,
        render_flat_yaml(
            {
                "mode": "ont",
                "lpwgs_root": root_path,
                "outdir": root_path / "runs" / "ont",
                "ont_folder": root_path / "public" / "ont_DRR165691" / "fastq_pass",
                "ont_barcodes": "barcode01",
                "ont_sample_names": "DRR165691",
                "ont_analysis_type": "liquid_biopsy",
                "ont_caller": "ichorcna",
                "ont_binsize_kb": 500,
                "ont_min_age_minutes": 0,
                "run_cna_classifier": False,
                "force": False,
            }
        ),
    )
    return illumina_config, ont_config


'''
    replace_between(CLI, "def prepare_quickstart1(", "def prepare_quickstart2(", quickstart1)

    quickstart2 = '''def prepare_quickstart2(root: Path, test_root: Path, *, dry_run: bool = False) -> Path:
    manifest = require_file(root / "examples" / "hcc1143_lpwgs" / "manifest.tsv", "HCC1143 manifest")
    reads = test_root / "public" / "hcc1143_lpwgs"
    samples = reads / "samples.csv"
    config_dir = test_root / "configs" / "hcc1143_lpwgs"
    outdir = test_root / "runs" / "hcc1143_lpwgs"
    if not dry_run:
        reads.mkdir(parents=True, exist_ok=True)
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\\t"):
            if dry_run:
                print(
                    f"Would download and validate {row['url']} -> {reads / row['filename']}",
                    file=sys.stderr,
                )
            else:
                download(
                    row["url"],
                    reads / row["filename"],
                    expected_bytes=int(row["bytes"]),
                    expected_md5=row["md5"],
                )
    if dry_run:
        print(f"Would write {samples}", file=sys.stderr)
    else:
        with samples.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_name", "status"])
            for sample in ("HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"):
                writer.writerow([sample, "TUMOR"])
    script = require_file(root / "bin" / "scripts" / "generate_auto_params.sh", "Automatic Setup script")
    _run(
        [
            "bash",
            script,
            "--mode",
            "illumina",
            "--reads-folder",
            reads,
            "--sample-table",
            samples,
            "--config-dir",
            config_dir,
            "--outdir",
            outdir,
        ],
        cwd=root,
        dry_run=dry_run,
    )
    return config_dir / "illumina.auto.yml"


'''
    replace_between(CLI, "def prepare_quickstart2(", "def command_quickstart(", quickstart2)

    command_quickstart = '''def command_quickstart(args: argparse.Namespace) -> int:
    root = runtime_root(args.root)
    test_root = Path(args.test_root).expanduser().resolve()
    if args.number == "1":
        illumina, ont = prepare_quickstart1(test_root, dry_run=args.dry_run)
        configs = (illumina, ont)
    else:
        configs = (prepare_quickstart2(root, test_root, dry_run=args.dry_run),)

    if args.dry_run:
        if not args.download_only:
            backend = _backend_from(args)
            for config in configs:
                print(
                    f"Would run oncotracer with backend={backend} and config={config}",
                    file=sys.stderr,
                )
        print(f"QuickStart {args.number} dry-run completed without writing files: {test_root}")
        return 0

    if not args.download_only:
        for config in configs:
            execute_run(config, args)
        if args.number == "1":
            verify = require_file(root / "examples" / "quickstart" / "verify_outputs.py", "QuickStart verifier")
            _run([sys.executable, verify, "--test-root", test_root], cwd=root)
        else:
            required = [
                test_root / "runs" / "hcc1143_lpwgs" / "06_workflow_summary" / "workflow_summary.txt",
                test_root / "runs" / "hcc1143_lpwgs" / "03_cna_codification" / "cna_events.tsv",
                test_root / "runs" / "hcc1143_lpwgs" / "04_cna_custom_plots" / "cna_per_sample_pages.pdf",
            ]
            for path in required:
                require_file(path, "QuickStart 2 output")
    print(f"QuickStart {args.number} completed: {test_root}")
    return 0


'''
    replace_between(CLI, "def command_quickstart(", "def _check_process(", command_quickstart)

    replace_exact(
        CLI,
        '''def command_run(args: argparse.Namespace) -> int:
    outdir = execute_run(Path(args.config), args)
    if outdir is not None:
        print(f"OncoTracer native analysis completed: {outdir}")
    return 0
''',
        '''def command_run(args: argparse.Namespace) -> int:
    outdir = execute_run(Path(args.config), args)
    if args.dry_run:
        target = outdir if outdir is not None else Path(args.config).expanduser().resolve()
        print(f"OncoTracer dry-run validation completed without analysis: {target}")
    elif outdir is not None:
        print(f"OncoTracer native analysis completed: {outdir}")
    return 0
''',
    )


def patch_runtime() -> None:
    replace_exact(
        RUNTIME,
        '        if not key:\n'
        '            raise OncoTracerError(f"empty YAML key ({path}:{line_number})")\n'
        '        # Generated OncoTracer values never contain unquoted inline comments.\n',
        '        if not key:\n'
        '            raise OncoTracerError(f"empty YAML key ({path}:{line_number})")\n'
        '        if key in values:\n'
        '            raise OncoTracerError(f"duplicate YAML key {key!r} ({path}:{line_number})")\n'
        '        # Generated OncoTracer values never contain unquoted inline comments.\n',
    )


def patch_engine() -> None:
    helper = '''def _validate_native_dry_run(
    config: Mapping[str, object],
    config_path: Path,
    mode: str,
    lpwgs_root: Path,
    outdir: Path,
    force_run: bool,
    threads: int,
) -> None:
    plan: dict[str, object] = {
        "schema": "oncotracer-native-dry-run-v1",
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "config": str(config_path),
        "mode": mode,
        "lpwgs_root": str(lpwgs_root),
        "outdir": str(outdir),
        "threads": threads,
        "force": force_run,
        "run_cna_classifier": _as_bool(config.get("run_cna_classifier"), False),
    }
    if mode == "illumina":
        samplesheet_value = config.get("illumina_samplesheet")
        if not samplesheet_value:
            raise OncoTracerError("Illumina config requires illumina_samplesheet")
        samples = parse_illumina_samplesheet(Path(str(samplesheet_value)))
        binsize = _as_int(config.get("illumina_binsize_kb"), 100)
        if binsize < 1:
            raise OncoTracerError("illumina_binsize_kb must be positive")
        plan.update(
            {
                "samples": [sample.sample for sample in samples],
                "tumor_samples": [sample.sample for sample in samples if sample.status == "tumor"],
                "normal_samples": [sample.sample for sample in samples if sample.status == "normal"],
                "paired_end": all(sample.fastq_2 is not None for sample in samples),
                "caller": str(config.get("illumina_caller") or "qdnaseq"),
                "binsize_kb": binsize,
                "stages": [
                    "reference-validation",
                    "illumina-alignment",
                    "qdnaseq",
                    "bam-refinement",
                    "cna-codification",
                    "cna-plots",
                    "workflow-summary",
                ],
            }
        )
    else:
        samples = parse_ont_samples(config)
        binsize = _as_int(config.get("ont_binsize_kb"), 500)
        if binsize < 1:
            raise OncoTracerError("ont_binsize_kb must be positive")
        plan.update(
            {
                "samples": [sample.sample for sample in samples],
                "barcodes": [sample.barcode for sample in samples],
                "caller": str(config.get("ont_caller") or "ichorcna"),
                "binsize_kb": binsize,
                "stages": [
                    "reference-validation",
                    "ont-fastq-validation",
                    "ont-alignment",
                    "hmmcopy-ichorcna",
                    "bam-refinement",
                    "cna-codification",
                    "cna-plots",
                    "workflow-summary",
                ],
            }
        )
    pathology = config.get("pathology_csv")
    if pathology:
        require_file(Path(str(pathology)), "Pathology CSV")
        plan["pathology_csv"] = str(Path(str(pathology)).expanduser().resolve())
    print(json.dumps(plan, indent=2, sort_keys=True))


'''
    replace_exact(ENGINE, "\ndef run_native(\n", "\n" + helper + "def run_native(\n")

    old_prefix = '''    root = runtime_root(root)
    config_path = require_file(config_path, "OncoTracer YAML config")
    config = load_flat_yaml(config_path)
    mode = str(config.get("mode") or "").strip().lower()
    if mode not in {"illumina", "ont"}:
        raise OncoTracerError("config mode must be illumina or ont")
    lpwgs_root = Path(str(config.get("lpwgs_root") or root / "project")).expanduser().resolve()
    outdir_value = config.get("outdir")
    if not outdir_value:
        raise OncoTracerError("config requires outdir")
    outdir = Path(str(outdir_value)).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    native_dir = outdir / ".oncotracer-native"
'''
    new_prefix = '''    explicit_root = root
    config_path = require_file(config_path, "OncoTracer YAML config")
    config = load_flat_yaml(config_path)
    mode = str(config.get("mode") or "").strip().lower()
    if mode not in {"illumina", "ont"}:
        raise OncoTracerError("config mode must be illumina or ont")
    payload_root: Path | None = None
    lpwgs_value = config.get("lpwgs_root")
    if lpwgs_value:
        lpwgs_root = Path(str(lpwgs_value)).expanduser().resolve()
    elif dry_run:
        root_hint = Path(explicit_root).expanduser().resolve() if explicit_root else Path.cwd().resolve()
        lpwgs_root = (root_hint / "project").resolve()
    else:
        payload_root = runtime_root(explicit_root)
        lpwgs_root = (payload_root / "project").resolve()
    outdir_value = config.get("outdir")
    if not outdir_value:
        raise OncoTracerError("config requires outdir")
    outdir = Path(str(outdir_value)).expanduser().resolve()
    cpu = threads or max(1, min(os.cpu_count() or 1, 16))
    force_run = _as_bool(config.get("force"), False) if force is None else force
    if dry_run:
        _validate_native_dry_run(
            config, config_path, mode, lpwgs_root, outdir, force_run, cpu
        )
        return outdir
    root = payload_root or runtime_root(explicit_root)
    outdir.mkdir(parents=True, exist_ok=True)
    native_dir = outdir / ".oncotracer-native"
'''
    replace_exact(ENGINE, old_prefix, new_prefix)
    replace_exact(
        ENGINE,
        '    toolchain = Toolchain.from_environment()\n'
        '    cpu = threads or max(1, min(os.cpu_count() or 1, 16))\n'
        '    force_run = _as_bool(config.get("force"), False) if force is None else force\n',
        '    toolchain = Toolchain.from_environment()\n',
    )


def main() -> None:
    patch_cli()
    patch_runtime()
    patch_engine()
    print("Applied guarded beginner runtime patch")


if __name__ == "__main__":
    main()
