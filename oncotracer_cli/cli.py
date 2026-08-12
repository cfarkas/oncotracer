#!/usr/bin/env python3
"""Global native command-line interface for OncoTracer v2."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .engine import Toolchain, run_native
from .install_safety import (
    install_conda_managed,
    install_sif_managed,
    installer_cli_target_arguments,
    managed_conda_runtime_lock,
    managed_sif_runtime_lock,
    verify_managed_conda_runtime,
    verify_managed_sif_runtime,
)
from .provenance import ProvenanceError, get_provenance
from .runtime import (
    OncoTracerError,
    atomic_write_json,
    atomic_write_text,
    download,
    isolated_payload_cache,
    load_flat_yaml,
    require_command,
    require_file,
    runtime_root,
    utc_now,
)

DEFAULT_IMAGE = "ghcr.io/cfarkas/oncotracer:2.0.0"
CONFIG_SCHEMA = "oncotracer-install-config-v1"


def _config_home() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "oncotracer"
    )


def _data_home() -> Path:
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        / "oncotracer"
        / __version__
    )


def _config_file() -> Path:
    return _config_home() / "config.json"


def _load_install_config() -> dict[str, object]:
    path = _config_file()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OncoTracerError(
            f"invalid installation config: {path}: {error}"
        ) from error
    return value if isinstance(value, dict) else {}


def _save_install_config(value: dict[str, object]) -> None:
    value = dict(value)
    value["schema"] = CONFIG_SCHEMA
    value["oncotracer_version"] = __version__
    value["updated_at"] = utc_now()
    atomic_write_json(_config_file(), value)


def _run(
    command: Sequence[str | Path], *, cwd: Path | None = None, dry_run: bool = False
) -> None:
    argv = [str(item) for item in command]
    print(f"OncoTracer command: {shlex.join(argv)}", file=sys.stderr, flush=True)
    if dry_run:
        return
    completed = subprocess.run(
        argv, cwd=cwd, stdout=sys.stderr, stderr=sys.stderr, check=False
    )
    if completed.returncode:
        raise OncoTracerError(
            f"command failed with exit code {completed.returncode}: {shlex.join(argv)}"
        )


def _conda_prefixes(prefix: Path | None = None) -> dict[str, Path]:
    base = Path(
        os.path.abspath(os.fspath((prefix or (_data_home() / "envs")).expanduser()))
    )
    return {
        name: base / name
        for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
    }


def _install_conda(
    root: Path, args: argparse.Namespace, *, save: bool = True
) -> dict[str, object]:
    conda = shutil.which("conda") or (
        "conda" if args.dry_run else require_command("conda")
    )
    base = Path(args.prefix) if args.prefix else (_data_home() / "envs")
    with installer_cli_target_arguments(base):
        prefixes = install_conda_managed(
            root,
            base,
            conda=conda,
            force=args.force,
            dry_run=args.dry_run,
        )
    result: dict[str, object] = {
        "backend": "conda",
        "core_prefix": str(prefixes["core"]),
        "qdnaseq_prefix": str(prefixes["qdnaseq"]),
        "ichorcna_prefix": str(prefixes["ichorcna"]),
        "classifier_prefix": str(prefixes["classifier"]),
        "gistic_prefix": str(prefixes["gistic"]),
    }
    if save and not args.dry_run:
        _save_install_config(result)
    return result


def _install_docker(args: argparse.Namespace) -> dict[str, object]:
    docker = shutil.which("docker") or (
        "docker" if args.dry_run else require_command("docker")
    )
    image = args.image or DEFAULT_IMAGE
    _run([docker, "info"], dry_run=args.dry_run)
    _run([docker, "pull", image], dry_run=args.dry_run)
    _run(
        [docker, "run", "--rm", image, "doctor", "--backend", "host"],
        dry_run=args.dry_run,
    )
    result: dict[str, object] = {"backend": "docker", "image": image}
    if not args.dry_run:
        _save_install_config(result)
    return result


def _singularity_command() -> str:
    return shutil.which("apptainer") or shutil.which("singularity") or ""


def _install_singularity(args: argparse.Namespace) -> dict[str, object]:
    executable = _singularity_command() or ("apptainer" if args.dry_run else "")
    if not executable:
        raise OncoTracerError("Apptainer or Singularity is required for --singularity")
    image = args.image or DEFAULT_IMAGE
    destination = (
        Path(args.sif)
        if args.sif
        else (_data_home() / "images" / "oncotracer-2.0.0.sif")
    )
    with installer_cli_target_arguments(destination):
        installed = install_sif_managed(
            destination,
            executable=executable,
            image=image,
            force=args.force,
            dry_run=args.dry_run,
        )
    result: dict[str, object] = {
        **installed,
        "backend": "singularity",
        "singularity_command": executable,
    }
    if not args.dry_run:
        _save_install_config(result)
    return result


def _install_poetry(root: Path, args: argparse.Namespace) -> dict[str, object]:
    poetry = shutil.which("poetry") or (
        "poetry" if args.dry_run else require_command("poetry")
    )
    conda = shutil.which("conda") or (
        "conda" if args.dry_run else require_command("conda")
    )
    base = Path(args.prefix) if args.prefix else (_data_home() / "envs")
    with installer_cli_target_arguments(base):
        prefixes = install_conda_managed(
            root,
            base,
            conda=conda,
            force=args.force,
            dry_run=args.dry_run,
            poetry=poetry,
        )
    result: dict[str, object] = {
        "backend": "poetry",
        "repository": str(root),
        "poetry_prefix": str(base.expanduser().absolute() / "poetry-runtime"),
        "scientific_backend": "conda",
        **{f"{name}_prefix": str(path) for name, path in prefixes.items()},
    }
    if not args.dry_run:
        _save_install_config(result)
    return result


def command_install(args: argparse.Namespace) -> int:
    selected = [
        name
        for name in ("docker", "singularity", "poetry", "conda")
        if getattr(args, name)
    ]
    if len(selected) != 1:
        raise OncoTracerError(
            "install requires exactly one backend flag: --docker, --singularity, --poetry, or --conda"
        )
    backend = selected[0]
    provided = {
        "--prefix": args.prefix is not None,
        "--image": args.image is not None,
        "--sif": args.sif is not None,
        "--force": bool(args.force),
        "--root": args.root is not None,
    }
    allowed = {
        "conda": {"--prefix", "--force", "--root"},
        "poetry": {"--prefix", "--force", "--root"},
        "docker": {"--image"},
        "singularity": {"--image", "--sif", "--force"},
    }[backend]
    irrelevant = sorted(
        flag for flag, present in provided.items() if present and flag not in allowed
    )
    if irrelevant:
        raise OncoTracerError(
            f"install --{backend} does not accept backend-irrelevant option(s): "
            f"{', '.join(irrelevant)}"
        )
    if backend == "conda":
        result = _install_conda(runtime_root(args.root), args)
    elif backend == "docker":
        result = _install_docker(args)
    elif backend == "singularity":
        result = _install_singularity(args)
    else:
        result = _install_poetry(runtime_root(args.root), args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _backend_from(args: argparse.Namespace) -> str:
    if getattr(args, "backend", None):
        return str(args.backend)
    config = _load_install_config()
    return str(config.get("backend") or "host")


def _project_mounts(config_path: Path) -> list[Path]:
    config = load_flat_yaml(config_path)
    candidates = [config_path.parent]
    for key in (
        "lpwgs_root",
        "outdir",
        "illumina_samplesheet",
        "ont_folder",
        "ont_normal_folder",
        "pathology_csv",
        "methylation_pod5_dir",
        "methylation_dorado_model",
        "methylation_dorado_modbase_model",
        "sturgeon_executable",
        "sturgeon_model",
        "sturgeon_probes",
        "marlin_rscript",
        "marlin_python",
        "marlin_model",
        "marlin_features",
        "marlin_class_annotations",
        "marlin_probe_bed",
    ):
        value = config.get(key)
        if not value:
            continue
        path = Path(str(value)).expanduser().resolve()
        candidates.append(path if path.is_dir() else path.parent)
    roots: list[Path] = []
    for candidate in sorted(set(candidates), key=lambda item: len(item.parts)):
        if any(candidate == root or root in candidate.parents for root in roots):
            continue
        roots.append(candidate)
    return roots


def _managed_conda_base(config: dict[str, object], *, require_poetry: bool) -> Path:
    prefixes = _configured_native_prefixes(config)
    if any(prefix is None for prefix in prefixes.values()):
        missing = sorted(name for name, prefix in prefixes.items() if prefix is None)
        raise OncoTracerError(
            f"managed runtime configuration lacks prefix(es): {', '.join(missing)}"
        )
    core = prefixes["core"]
    assert core is not None
    base = core.parent
    for name, prefix in prefixes.items():
        if prefix != base / name:
            raise OncoTracerError(
                f"managed runtime {name} prefix is not the fixed child of {base}: {prefix}"
            )
    poetry = config.get("poetry_prefix")
    if require_poetry and (
        not poetry
        or Path(str(poetry)).expanduser().resolve() != base / "poetry-runtime"
    ):
        raise OncoTracerError(
            f"Poetry runtime is not configured at the fixed managed child {base / 'poetry-runtime'}"
        )
    return base


def _managed_install_backend(
    config: dict[str, object], requested_backend: str
) -> str | None:
    """Return the ownership-managed backend, excluding external/container layouts."""
    if requested_backend in {"conda", "poetry"}:
        return requested_backend
    configured = config.get("backend")
    return str(configured) if configured in {"conda", "poetry"} else None


def _native_environment(config: dict[str, object]) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if config.get("core_prefix"):
        core = Path(str(config["core_prefix"])).expanduser().resolve()
        environment["ONCOTRACER_CORE_PREFIX"] = str(core)
        environment["PATH"] = f"{core / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    if config.get("qdnaseq_prefix"):
        environment["ONCOTRACER_QDNASEQ_PREFIX"] = str(config["qdnaseq_prefix"])
    if config.get("ichorcna_prefix"):
        environment["ONCOTRACER_ICHORCNA_PREFIX"] = str(config["ichorcna_prefix"])
    if config.get("classifier_prefix"):
        environment["ONCOTRACER_CLASSIFIER_PREFIX"] = str(config["classifier_prefix"])
    if config.get("gistic_prefix"):
        environment["ONCOTRACER_GISTIC_PREFIX"] = str(config["gistic_prefix"])
    return environment


def _run_host(config_path: Path, args: argparse.Namespace) -> Path:
    install = _load_install_config()
    backend = _backend_from(args)
    lock = contextlib.nullcontext()
    managed_backend = _managed_install_backend(install, backend)
    if not args.dry_run and managed_backend is not None:
        require_poetry = managed_backend == "poetry"
        base = _managed_conda_base(install, require_poetry=require_poetry)
        lock = managed_conda_runtime_lock(
            base, require_poetry=require_poetry, semantic=False
        )
    with lock:
        old = os.environ.copy()
        try:
            os.environ.update(_native_environment(install))
            return run_native(
                config_path,
                root=Path(args.root).expanduser().resolve() if args.root else None,
                threads=args.threads,
                force=args.force if args.force else None,
                dry_run=args.dry_run,
                methylation=args.methylation,
                methylation_classifier=args.methylation_classifier,
                methylation_pod5_dir=Path(args.pod5_dir) if args.pod5_dir else None,
                methylation_gpu=args.gpu,
            )
        finally:
            os.environ.clear()
            os.environ.update(old)


def _run_docker(config_path: Path, args: argparse.Namespace) -> None:
    docker = shutil.which("docker") or (
        "docker" if args.dry_run else require_command("docker")
    )
    install = _load_install_config()
    image = args.image or str(install.get("image") or DEFAULT_IMAGE)
    command: list[str | Path] = [
        docker,
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    command.extend(["--env", "HOME=/tmp", "--env", "MPLCONFIGDIR=/tmp/matplotlib"])
    for mount in _project_mounts(config_path):
        command.extend(["--volume", f"{mount}:{mount}"])
    command.extend(
        [image, "internal-run", "--config", config_path, "--backend", "host"]
    )
    if args.threads:
        command.extend(["--threads", str(args.threads)])
    if args.force:
        command.append("--force")
    if args.methylation:
        command.append("--methylation")
    if args.methylation_classifier:
        command.append(f"--{args.methylation_classifier}")
    if args.pod5_dir:
        command.extend(["--pod5-dir", Path(args.pod5_dir).expanduser().resolve()])
    if args.gpu:
        command.append("--gpu")
    _run(command, dry_run=args.dry_run)


def _run_singularity(config_path: Path, args: argparse.Namespace) -> None:
    install = _load_install_config()
    executable = str(
        install.get("singularity_command")
        or _singularity_command()
        or ("apptainer" if args.dry_run else "")
    )
    if not executable:
        raise OncoTracerError("Apptainer or Singularity is required")
    sif_value = args.sif or install.get("sif")
    if not sif_value and not args.dry_run:
        raise OncoTracerError(
            "no SIF is configured; run 'oncotracer install --singularity'"
        )
    sif_candidate = (
        Path(str(sif_value or "/path/to/oncotracer-2.0.0.sif")).expanduser().resolve()
    )
    sif = (
        sif_candidate if args.dry_run else require_file(sif_candidate, "OncoTracer SIF")
    )
    command: list[str | Path] = [executable, "exec", "--cleanenv"]
    for mount in _project_mounts(config_path):
        command.extend(["--bind", f"{mount}:{mount}"])
    command.extend(
        [
            sif,
            "oncotracer",
            "internal-run",
            "--config",
            config_path,
            "--backend",
            "host",
        ]
    )
    if args.threads:
        command.extend(["--threads", str(args.threads)])
    if args.force:
        command.append("--force")
    if args.methylation:
        command.append("--methylation")
    if args.methylation_classifier:
        command.append(f"--{args.methylation_classifier}")
    if args.pod5_dir:
        command.extend(["--pod5-dir", Path(args.pod5_dir).expanduser().resolve()])
    if args.gpu:
        command.append("--gpu")
    if args.dry_run:
        _run(command, dry_run=True)
    else:
        with managed_sif_runtime_lock(sif, executable=executable, semantic=False):
            _run(command)


def _methylation_requested(config_path: Path, args: argparse.Namespace) -> bool:
    if args.methylation is not None:
        return bool(args.methylation)
    value = load_flat_yaml(config_path).get("methylation")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "on", "1"}


def execute_run(config_path: Path, args: argparse.Namespace) -> Path | None:
    config_path = require_file(config_path, "OncoTracer YAML config")
    backend = _backend_from(args)
    if backend in {"docker", "singularity", "apptainer"} and _methylation_requested(
        config_path, args
    ):
        raise OncoTracerError(
            "the optional POD5 methylation branch requires backend host, conda, "
            "or poetry with explicit user-installed Dorado/Modkit/classifier assets; "
            "the stable OncoTracer container does not redistribute those licensed resources"
        )
    if backend in {"host", "poetry", "conda"}:
        if backend in {"conda", "poetry"} and not args.dry_run:
            install = _load_install_config()
            required = {
                "core_prefix",
                "qdnaseq_prefix",
                "ichorcna_prefix",
                "classifier_prefix",
                "gistic_prefix",
            }
            missing = sorted(key for key in required if not install.get(key))
            if missing:
                raise OncoTracerError(
                    f"{backend.capitalize()} backend is not installed; run "
                    f"'oncotracer install --{backend}' "
                    f"(missing: {', '.join(missing)})"
                )
        return _run_host(config_path, args)
    if backend == "docker":
        outdir = _run_host(config_path, args) if args.dry_run else None
        _run_docker(config_path, args)
        return outdir
    if backend in {"singularity", "apptainer"}:
        outdir = _run_host(config_path, args) if args.dry_run else None
        _run_singularity(config_path, args)
        return outdir
    raise OncoTracerError(
        f"unsupported backend {backend!r}; choose host, conda, docker, singularity, or poetry"
    )


def command_run(args: argparse.Namespace) -> int:
    outdir = execute_run(Path(args.config), args)
    if args.dry_run:
        target = (
            outdir if outdir is not None else Path(args.config).expanduser().resolve()
        )
        print(f"OncoTracer dry-run validation completed without analysis: {target}")
    elif outdir is not None:
        print(f"OncoTracer native analysis completed: {outdir}")
    return 0


def command_auto(args: argparse.Namespace) -> int:
    root = runtime_root(args.root)
    script = require_file(
        root / "bin" / "scripts" / "generate_auto_params.sh", "Automatic Setup script"
    )
    command: list[str | Path] = [
        "bash",
        script,
        "--mode",
        args.mode,
        "--reads-folder",
        Path(args.reads_folder).expanduser().resolve(),
        "--sample-table",
        Path(args.sample_table).expanduser().resolve(),
    ]
    if args.config_dir:
        command.extend(["--config-dir", Path(args.config_dir).expanduser().resolve()])
    if args.outdir:
        command.extend(["--outdir", Path(args.outdir).expanduser().resolve()])
    if args.run_cna_classifier:
        command.extend(["--run-cna-classifier", "true"])
    _run(command, cwd=root, dry_run=args.dry_run)
    return 0


QS1_FILES = (
    (
        "https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_1.fastq.gz",
        "public/illumina_ERR12341627/ERR12341627_1.fastq.gz",
        105996523,
        "4c96d551152694b3893ea98b7781a3ae",
    ),
    (
        "https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_2.fastq.gz",
        "public/illumina_ERR12341627/ERR12341627_2.fastq.gz",
        23748473,
        "1b20d9eb98f755244f6383ea1354bd40",
    ),
    (
        "https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz",
        "public/ont_DRR165691/fastq_pass/barcode01/DRR165691_1.fastq.gz",
        101734666,
        "55a3984cb0334aa4cb0a38255cb71c06",
    ),
)


def prepare_quickstart1(root_path: Path, *, dry_run: bool = False) -> tuple[Path, Path]:
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


def prepare_quickstart2(root: Path, test_root: Path, *, dry_run: bool = False) -> Path:
    manifest = require_file(
        root / "examples" / "hcc1143_lpwgs" / "manifest.tsv", "HCC1143 manifest"
    )
    reads = test_root / "public" / "hcc1143_lpwgs"
    samples = reads / "samples.csv"
    config_dir = test_root / "configs" / "hcc1143_lpwgs"
    outdir = test_root / "runs" / "hcc1143_lpwgs"
    if not dry_run:
        reads.mkdir(parents=True, exist_ok=True)
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
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
    script = require_file(
        root / "bin" / "scripts" / "generate_auto_params.sh", "Automatic Setup script"
    )
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


def command_quickstart(args: argparse.Namespace) -> int:
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
        print(
            f"QuickStart {args.number} dry-run completed without writing files: {test_root}"
        )
        return 0

    if not args.download_only:
        for config in configs:
            execute_run(config, args)
        if args.number == "1":
            verify = require_file(
                root / "examples" / "quickstart" / "verify_outputs.py",
                "QuickStart verifier",
            )
            _run([sys.executable, verify, "--test-root", test_root], cwd=root)
        else:
            required = [
                test_root
                / "runs"
                / "hcc1143_lpwgs"
                / "06_workflow_summary"
                / "workflow_summary.txt",
                test_root
                / "runs"
                / "hcc1143_lpwgs"
                / "03_cna_codification"
                / "cna_events.tsv",
                test_root
                / "runs"
                / "hcc1143_lpwgs"
                / "04_cna_custom_plots"
                / "cna_per_sample_pages.pdf",
            ]
            for path in required:
                require_file(path, "QuickStart 2 output")
    print(f"QuickStart {args.number} completed: {test_root}")
    return 0


def _check_process(
    command: Sequence[str | Path],
    *,
    env: dict[str, str] | None = None,
    accepted_returncodes: set[int] | frozenset[int] | None = None,
    required_output: str | None = None,
) -> dict[str, object]:
    argv = [str(item) for item in command]
    completed = subprocess.run(
        argv, text=True, capture_output=True, env=env, check=False
    )
    output = f"{completed.stdout or ''}{completed.stderr or ''}".strip()
    lines = output.splitlines()
    accepted = set(accepted_returncodes or {0})
    output_matched = (
        required_output is None
        or re.search(required_output, output, flags=re.IGNORECASE | re.MULTILINE)
        is not None
    )
    return {
        "command": shlex.join(argv),
        "returncode": completed.returncode,
        "accepted_returncodes": sorted(accepted),
        "required_output": required_output,
        "output_matched": output_matched,
        "success": completed.returncode in accepted and output_matched,
        "first_line": lines[0] if lines else "",
        "output_excerpt": output[:4000],
    }


def _missing_probe(path: Path | str, reason: str) -> dict[str, object]:
    return {
        "command": str(path),
        "returncode": None,
        "accepted_returncodes": [],
        "required_output": None,
        "output_matched": False,
        "present": False,
        "success": False,
        "first_line": "",
        "output_excerpt": reason,
    }


def _probe_executable(
    prefix: Path,
    name: str,
    arguments: Sequence[str],
    *,
    accepted_returncodes: set[int] | frozenset[int] | None = None,
    required_output: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    executable = prefix / "bin" / name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return _missing_probe(executable, f"missing executable: {executable}")
    result = _check_process(
        [executable, *arguments],
        env=env,
        accepted_returncodes=accepted_returncodes,
        required_output=required_output,
    )
    result["present"] = True
    return result


def _configured_native_prefixes(install: dict[str, object]) -> dict[str, Path | None]:
    definitions = {
        "core": ("core_prefix", "ONCOTRACER_CORE_PREFIX"),
        "qdnaseq": ("qdnaseq_prefix", "ONCOTRACER_QDNASEQ_PREFIX"),
        "ichorcna": ("ichorcna_prefix", "ONCOTRACER_ICHORCNA_PREFIX"),
        "classifier": ("classifier_prefix", "ONCOTRACER_CLASSIFIER_PREFIX"),
        "gistic": ("gistic_prefix", "ONCOTRACER_GISTIC_PREFIX"),
    }
    prefixes: dict[str, Path | None] = {}
    for group, (config_key, environment_key) in definitions.items():
        value = os.environ.get(environment_key) or install.get(config_key)
        prefixes[group] = Path(str(value)).expanduser().resolve() if value else None
    return prefixes


def _prefix_environment(
    prefix: Path,
    *,
    clean_r: bool = False,
    cpu_only: bool = False,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PATH"] = f"{prefix / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    if clean_r:
        for name in ("R_HOME", "R_LIBS", "R_LIBS_USER", "R_LIBS_SITE"):
            environment.pop(name, None)
    if cpu_only:
        environment["CUDA_VISIBLE_DEVICES"] = ""
        environment["NVIDIA_VISIBLE_DEVICES"] = "void"
    return environment


def _probe_core(prefix: Path | None) -> dict[str, object]:
    definitions: dict[str, tuple[list[str], frozenset[int], str]] = {
        "bwa": ([], frozenset({1}), r"Program:\s*bwa"),
        "samtools": (["--version"], frozenset({0}), r"\bsamtools\b"),
        "minimap2": (["--version"], frozenset({0}), r"(minimap2|^[0-9]+\.[0-9]+)"),
        "pigz": (["--version"], frozenset({0}), r"\bpigz\b"),
        "picard": (["-h"], frozenset({1}), r"(Picard|USAGE|CommandLineProgram)"),
    }
    probes: dict[str, dict[str, object]] = {}
    for name, (arguments, accepted, expected) in definitions.items():
        if prefix is not None:
            result = _probe_executable(
                prefix,
                name,
                arguments,
                accepted_returncodes=accepted,
                required_output=expected,
                env=_prefix_environment(prefix),
            )
        else:
            executable = shutil.which(name)
            if executable:
                result = _check_process(
                    [executable, *arguments],
                    accepted_returncodes=accepted,
                    required_output=expected,
                )
                result["present"] = True
            else:
                result = _missing_probe(name, f"{name} was not found on PATH")
        probes[name] = result
    return {
        "success": all(bool(probe["success"]) for probe in probes.values()),
        "probes": probes,
    }


def _probe_native_prefixes(
    prefixes: dict[str, Path | None]
) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    core = prefixes["core"]
    results["core"] = (
        _probe_core(core)
        if core is not None
        else {"success": False, "probes": {}, "error": "core prefix is not configured"}
    )

    qdnaseq = prefixes["qdnaseq"]
    if qdnaseq is None:
        results["qdnaseq"] = {
            "success": False,
            "probes": {},
            "error": "qdnaseq prefix is not configured",
        }
    else:
        r_probe = _probe_executable(
            qdnaseq,
            "Rscript",
            [
                "--vanilla",
                "-e",
                'suppressPackageStartupMessages(library(Biobase)); suppressPackageStartupMessages(library(QDNAseq)); cat("QDNASEQ_OK\\n")',
            ],
            required_output=r"QDNASEQ_OK",
            env=_prefix_environment(qdnaseq, clean_r=True),
        )
        results["qdnaseq"] = {
            "success": bool(r_probe["success"]),
            "probes": {"Rscript": r_probe},
        }

    ichorcna = prefixes["ichorcna"]
    if ichorcna is None:
        results["ichorcna"] = {
            "success": False,
            "probes": {},
            "error": "ichorcna prefix is not configured",
        }
    else:
        ichor_r = _probe_executable(
            ichorcna,
            "Rscript",
            [
                "--vanilla",
                "-e",
                'suppressPackageStartupMessages(library(ichorCNA)); cat("ICHORCNA_OK\\n")',
            ],
            required_output=r"ICHORCNA_OK",
            env=_prefix_environment(ichorcna, clean_r=True),
        )
        readcounter = _probe_executable(
            ichorcna,
            "readCounter",
            [],
            accepted_returncodes=frozenset({255}),
            required_output=r"Please specify a BAM file\.\s*Usage:",
            env=_prefix_environment(ichorcna),
        )
        results["ichorcna"] = {
            "success": bool(ichor_r["success"]) and bool(readcounter["success"]),
            "probes": {"Rscript": ichor_r, "readCounter": readcounter},
        }

    classifier = prefixes["classifier"]
    if classifier is None:
        results["classifier"] = {
            "success": False,
            "probes": {},
            "error": "classifier prefix is not configured",
        }
    else:
        imports = (
            "import pandas,openpyxl,numpy,scipy,sklearn,matplotlib,jinja2,requests,"
            "reportlab,pypdf,transformers,torch,safetensors,huggingface_hub; "
            'print("CLASSIFIER_OK")'
        )
        python_probe = _probe_executable(
            classifier,
            "python",
            ["-c", imports],
            required_output=r"CLASSIFIER_OK",
            env=_prefix_environment(classifier, cpu_only=True),
        )
        results["classifier"] = {
            "success": bool(python_probe["success"]),
            "probes": {"python": python_probe},
        }

    gistic = prefixes["gistic"]
    if gistic is None:
        results["gistic"] = {
            "success": False,
            "probes": {},
            "error": "gistic prefix is not configured",
        }
    else:
        try:
            gistic_environment = _prefix_environment(gistic)
            gistic_environment.update(
                Toolchain(gistic_prefix=gistic).environment("gistic")
            )
        except OncoTracerError as error:
            gistic_probe = _missing_probe(gistic / "bin" / "gistic2", str(error))
        else:
            gistic_probe = _probe_executable(
                gistic,
                "gistic2",
                ["-h"],
                accepted_returncodes=frozenset({0}),
                required_output=r"Usage:\s*gp_gistic2_from_seg\b",
                env=gistic_environment,
            )
        results["gistic"] = {
            "success": bool(gistic_probe["success"]),
            "probes": {"gistic2": gistic_probe},
        }
    return results


def command_doctor(args: argparse.Namespace) -> int:
    backend = _backend_from(args)
    checks: dict[str, object] = {
        "schema": "oncotracer-doctor-v1",
        "oncotracer_version": __version__,
        "backend": backend,
        "python": sys.version.split()[0],
        "executable": sys.argv[0],
        "nextflow_required": False,
        "checked_at": utc_now(),
    }
    source_success = False
    try:
        provenance = get_provenance()
        source_success = (
            bool(provenance.get("source_commit"))
            and bool(provenance.get("source_sha256"))
            and provenance.get("source_tree_dirty") is False
        )
        checks["source"] = {
            "source_commit": provenance.get("source_commit"),
            "source_sha256": provenance.get("source_sha256"),
            "source_sha256_definition": provenance.get("source_sha256_definition"),
            "source_metadata_origin": provenance.get("source_metadata_origin"),
            "source_tree_dirty": provenance.get("source_tree_dirty"),
            "success": source_success,
        }
    except ProvenanceError as error:
        checks["source"] = {
            "source_commit": None,
            "source_sha256": None,
            "source_metadata_origin": "error",
            "source_tree_dirty": None,
            "success": False,
            "error": str(error),
        }
    success = True
    if backend in {"host", "poetry", "conda"}:
        install = _load_install_config()
        prefixes = _configured_native_prefixes(install)
        any_prefix = any(prefix is not None for prefix in prefixes.values())
        require_matrix = backend in {"poetry", "conda"} or any_prefix
        if require_matrix:
            managed_success = True
            managed_backend = _managed_install_backend(install, backend)
            if managed_backend is not None:
                try:
                    require_poetry = managed_backend == "poetry"
                    base = _managed_conda_base(install, require_poetry=require_poetry)
                    managed = verify_managed_conda_runtime(
                        base, require_poetry=require_poetry
                    )
                    checks["managed_install"] = {
                        "success": True,
                        "backend": managed_backend,
                        "base": str(base),
                        "children": {name: str(path) for name, path in managed.items()},
                    }
                except OncoTracerError as error:
                    managed_success = False
                    checks["managed_install"] = {
                        "success": False,
                        "backend": managed_backend,
                        "error": str(error),
                    }
            else:
                checks["managed_install"] = {
                    "success": True,
                    "required": False,
                    "provisioning": "external",
                }
            prefix_checks = {
                group: {
                    "path": str(prefix) if prefix is not None else "",
                    "configured": prefix is not None,
                    "exists": prefix.is_dir() if prefix is not None else False,
                }
                for group, prefix in prefixes.items()
            }
            environments = _probe_native_prefixes(prefixes)
            checks["prefixes"] = prefix_checks
            checks["environments"] = environments
            checks["commands"] = environments["core"].get("probes", {})
            success = all(
                item["configured"] and item["exists"] for item in prefix_checks.values()
            )
            success = success and all(
                bool(item["success"]) for item in environments.values()
            )
            success = success and managed_success
        else:
            core = _probe_core(None)
            checks["prefixes"] = {}
            checks["environments"] = {"core": core}
            checks["commands"] = core["probes"]
            success = bool(core["success"])
    elif backend == "docker":
        docker = require_command("docker")
        image = args.image or str(_load_install_config().get("image") or DEFAULT_IMAGE)
        result = _check_process(
            [docker, "run", "--rm", image, "doctor", "--backend", "host"]
        )
        checks["docker"] = result
        success = bool(result["success"])
    elif backend in {"singularity", "apptainer"}:
        install = _load_install_config()
        executable = str(install.get("singularity_command") or _singularity_command())
        sif = str(install.get("sif") or "")
        checks["runtime"] = executable
        checks["sif"] = sif
        try:
            if not executable or not sif:
                raise OncoTracerError(
                    "managed Singularity runtime and SIF are not configured"
                )
            marker = verify_managed_sif_runtime(
                Path(sif).expanduser().resolve(), executable=executable
            )
            checks["managed_sif"] = {"success": True, "marker": marker}
            success = True
        except OncoTracerError as error:
            checks["managed_sif"] = {"success": False, "error": str(error)}
            success = False
    success = bool(success) and source_success
    checks["success"] = success
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if success else 1


def command_provenance(args: argparse.Namespace) -> int:
    try:
        record = get_provenance()
    except ProvenanceError as error:
        raise OncoTracerError(f"could not read build provenance: {error}") from error
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        for key in (
            "oncotracer_version",
            "source_commit",
            "source_sha256",
            "source_sha256_definition",
            "source_tree_dirty",
            "binary_sha256",
        ):
            value = record[key]
            print(f"{key}={value if value is not None else 'unavailable'}")
    return 0


def _add_common_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend", choices=("host", "conda", "docker", "singularity", "poetry")
    )
    parser.add_argument("--threads", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", help="Repository or extracted payload root")
    parser.add_argument("--image")
    parser.add_argument("--sif")
    parser.add_argument(
        "--methylation",
        action="store_true",
        default=None,
        help="enable the optional ONT-only POD5 methylation branch",
    )
    classifier = parser.add_mutually_exclusive_group()
    classifier.add_argument(
        "--sturgeon",
        dest="methylation_classifier",
        action="store_const",
        const="sturgeon",
        help="classify CNS-tumor methylation with a licensed Sturgeon install",
    )
    classifier.add_argument(
        "--marlin",
        dest="methylation_classifier",
        action="store_const",
        const="marlin",
        help="classify leukemia methylation with explicit MARLIN resources",
    )
    parser.add_argument(
        "--pod5-dir",
        help="required explicit non-empty POD5 directory for --methylation",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        default=None,
        help="use GPU for methylation Dorado and expose it to MARLIN",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oncotracer",
        description="Native LP-WGS CNA analysis.",
    )
    parser.add_argument(
        "--version", action="version", version=f"OncoTracer {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="Prepare one execution backend")
    group = install.add_mutually_exclusive_group(required=True)
    group.add_argument("--docker", action="store_true")
    group.add_argument("--singularity", action="store_true")
    group.add_argument("--poetry", action="store_true")
    group.add_argument("--conda", action="store_true")
    install.add_argument("--prefix")
    install.add_argument("--image")
    install.add_argument("--sif")
    install.add_argument("--force", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--root")
    install.set_defaults(func=command_install)

    run = subparsers.add_parser("run", help="Run a native analysis from YAML")
    run.add_argument("--config", required=True)
    _add_common_run_options(run)
    run.set_defaults(func=command_run)

    internal = subparsers.add_parser("internal-run", help=argparse.SUPPRESS)
    internal.add_argument("--config", required=True)
    _add_common_run_options(internal)
    internal.set_defaults(func=command_run)

    auto = subparsers.add_parser("auto", help="Create YAML and samplesheet from FASTQs")
    auto.add_argument("--mode", choices=("illumina", "ont"), required=True)
    auto.add_argument("--reads-folder", required=True)
    auto.add_argument("--sample-table", required=True)
    auto.add_argument("--config-dir")
    auto.add_argument("--outdir")
    auto.add_argument("--run-cna-classifier", action="store_true")
    auto.add_argument("--dry-run", action="store_true")
    auto.add_argument("--root")
    auto.set_defaults(func=command_auto)

    quickstart = subparsers.add_parser(
        "quickstart", help="Run a complete public validation example"
    )
    quickstart.add_argument("number", choices=("1", "2"))
    quickstart.add_argument("--test-root", required=True)
    quickstart.add_argument("--download-only", action="store_true")
    _add_common_run_options(quickstart)
    quickstart.set_defaults(func=command_quickstart)

    doctor = subparsers.add_parser("doctor", help="Verify the selected backend")
    doctor.add_argument(
        "--backend", choices=("host", "conda", "docker", "singularity", "poetry")
    )
    doctor.add_argument("--image")
    doctor.set_defaults(func=command_doctor)

    provenance = subparsers.add_parser(
        "provenance", help="Report exact source and binary provenance"
    )
    provenance.add_argument(
        "--json", action="store_true", help="Emit the complete JSON record"
    )
    provenance.set_defaults(func=command_provenance)
    return parser


def _legacy_to_modern(values: list[str]) -> list[str]:
    """Translate v1 launcher syntax while keeping v2 execution native."""
    if (
        not values
        or values[0]
        in {
            "install",
            "run",
            "internal-run",
            "auto",
            "quickstart",
            "doctor",
            "provenance",
        }
        or values[0] in {"-h", "--help", "--version"}
    ):
        return values
    backend = None
    for flag, name in (
        ("--docker", "docker"),
        ("--singularity", "singularity"),
        ("--conda", "conda"),
    ):
        if flag in values:
            values.remove(flag)
            backend = name
    if "-params-file" in values:
        index = values.index("-params-file")
        if index + 1 >= len(values):
            raise OncoTracerError("-params-file requires a YAML path")
        config = values[index + 1]
        del values[index : index + 2]
        translated = ["run", "--config", config]
        if backend:
            translated.extend(["--backend", backend])
        if "-resume" in values:
            values.remove("-resume")
        if values:
            raise OncoTracerError(f"unsupported legacy arguments: {shlex.join(values)}")
        return translated
    if "--auto_params" in values:
        values.remove("--auto_params")
        mapping = {
            "--reads_folder": "--reads-folder",
            "--sample_table": "--sample-table",
            "--auto_config_dir": "--config-dir",
            "--auto_outdir": "--outdir",
        }
        return ["auto", *[mapping.get(value, value) for value in values]]
    raise OncoTracerError("use an OncoTracer v2 subcommand; run 'oncotracer --help'")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        values = _legacy_to_modern(list(sys.argv[1:] if argv is None else argv))
        if not values:
            parser.print_help()
            return 0
        args = parser.parse_args(values)
        if not hasattr(args, "func"):
            parser.print_help()
            return 2
        with isolated_payload_cache(bool(getattr(args, "dry_run", False))):
            return int(args.func(args))
    except OncoTracerError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
