#!/usr/bin/env python3
"""Global native command-line interface for OncoTracer v2."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .engine import run_native
from .runtime import (
    OncoTracerError,
    atomic_write_json,
    atomic_write_text,
    download,
    load_flat_yaml,
    require_command,
    require_file,
    runtime_root,
    utc_now,
)

DEFAULT_IMAGE = "ghcr.io/cfarkas/oncotracer:2.0.0"
CONFIG_SCHEMA = "oncotracer-install-config-v1"


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "oncotracer"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "oncotracer" / __version__


def _config_file() -> Path:
    return _config_home() / "config.json"


def _load_install_config() -> dict[str, object]:
    path = _config_file()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OncoTracerError(f"invalid installation config: {path}: {error}") from error
    return value if isinstance(value, dict) else {}


def _save_install_config(value: dict[str, object]) -> None:
    value = dict(value)
    value["schema"] = CONFIG_SCHEMA
    value["oncotracer_version"] = __version__
    value["updated_at"] = utc_now()
    atomic_write_json(_config_file(), value)


def _run(command: Sequence[str | Path], *, cwd: Path | None = None, dry_run: bool = False) -> None:
    argv = [str(item) for item in command]
    print(f"OncoTracer command: {shlex.join(argv)}", file=sys.stderr, flush=True)
    if dry_run:
        return
    completed = subprocess.run(argv, cwd=cwd, check=False)
    if completed.returncode:
        raise OncoTracerError(
            f"command failed with exit code {completed.returncode}: {shlex.join(argv)}"
        )


def _conda_prefixes(prefix: Path | None = None) -> dict[str, Path]:
    base = (prefix or (_data_home() / "envs")).expanduser().resolve()
    return {name: base / name for name in ("core", "qdnaseq", "ichorcna")}


def _install_conda(root: Path, args: argparse.Namespace) -> dict[str, object]:
    conda = require_command("conda")
    prefixes = _conda_prefixes(Path(args.prefix) if args.prefix else None)
    definitions = {
        "core": root / "environments" / "native-core.yml",
        "qdnaseq": root / "environments" / "native-qdnaseq.yml",
        "ichorcna": root / "environments" / "native-ichorcna.yml",
    }
    for name, destination in prefixes.items():
        definition = require_file(definitions[name], f"native {name} environment definition")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_dir() and not args.force:
            command = [conda, "env", "update", "--prefix", destination, "--file", definition, "--prune"]
        else:
            if destination.exists() and args.force and not args.dry_run:
                shutil.rmtree(destination)
            command = [conda, "env", "create", "--prefix", destination, "--file", definition]
        _run(command, dry_run=args.dry_run)
    result: dict[str, object] = {
        "backend": "conda",
        "core_prefix": str(prefixes["core"]),
        "qdnaseq_prefix": str(prefixes["qdnaseq"]),
        "ichorcna_prefix": str(prefixes["ichorcna"]),
    }
    if not args.dry_run:
        _save_install_config(result)
    return result


def _install_docker(args: argparse.Namespace) -> dict[str, object]:
    docker = require_command("docker")
    image = args.image or DEFAULT_IMAGE
    _run([docker, "info"], dry_run=args.dry_run)
    _run([docker, "pull", image], dry_run=args.dry_run)
    _run([docker, "run", "--rm", image, "doctor", "--backend", "host"], dry_run=args.dry_run)
    result: dict[str, object] = {"backend": "docker", "image": image}
    if not args.dry_run:
        _save_install_config(result)
    return result


def _singularity_command() -> str:
    return shutil.which("apptainer") or shutil.which("singularity") or ""


def _install_singularity(args: argparse.Namespace) -> dict[str, object]:
    executable = _singularity_command()
    if not executable:
        raise OncoTracerError("Apptainer or Singularity is required for --singularity")
    image = args.image or DEFAULT_IMAGE
    destination = (
        Path(args.sif).expanduser().resolve()
        if args.sif
        else (_data_home() / "images" / "oncotracer-2.0.0.sif").resolve()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.force and destination.exists() and not args.dry_run:
        destination.unlink()
    if not destination.is_file():
        _run([executable, "pull", destination, f"docker://{image}"], dry_run=args.dry_run)
    _run([executable, "exec", destination, "oncotracer", "doctor", "--backend", "host"], dry_run=args.dry_run)
    result: dict[str, object] = {
        "backend": "singularity",
        "singularity_command": executable,
        "sif": str(destination),
        "image": image,
    }
    if not args.dry_run:
        _save_install_config(result)
    return result


def _install_poetry(root: Path, args: argparse.Namespace) -> dict[str, object]:
    poetry = require_command("poetry")
    _run([poetry, "install", "--no-interaction"], cwd=root, dry_run=args.dry_run)
    result: dict[str, object] = {"backend": "poetry", "repository": str(root)}
    if not args.dry_run:
        _save_install_config(result)
    return result


def command_install(args: argparse.Namespace) -> int:
    root = runtime_root(args.root)
    selected = [name for name in ("docker", "singularity", "poetry", "conda") if getattr(args, name)]
    if len(selected) != 1:
        raise OncoTracerError(
            "install requires exactly one backend flag: --docker, --singularity, --poetry, or --conda"
        )
    backend = selected[0]
    if backend == "conda":
        result = _install_conda(root, args)
    elif backend == "docker":
        result = _install_docker(args)
    elif backend == "singularity":
        result = _install_singularity(args)
    else:
        result = _install_poetry(root, args)
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


def _native_environment(config: dict[str, object]) -> dict[str, str]:
    environment = os.environ.copy()
    if config.get("core_prefix"):
        core = Path(str(config["core_prefix"])).expanduser().resolve()
        environment["ONCOTRACER_CORE_PREFIX"] = str(core)
        environment["PATH"] = f"{core / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    if config.get("qdnaseq_prefix"):
        environment["ONCOTRACER_QDNASEQ_PREFIX"] = str(config["qdnaseq_prefix"])
    if config.get("ichorcna_prefix"):
        environment["ONCOTRACER_ICHORCNA_PREFIX"] = str(config["ichorcna_prefix"])
    return environment


def _run_host(config_path: Path, args: argparse.Namespace) -> Path:
    install = _load_install_config()
    old = os.environ.copy()
    try:
        os.environ.update(_native_environment(install))
        return run_native(
            config_path,
            root=Path(args.root).expanduser().resolve() if args.root else None,
            threads=args.threads,
            force=args.force if args.force else None,
            dry_run=args.dry_run,
        )
    finally:
        os.environ.clear()
        os.environ.update(old)


def _run_docker(config_path: Path, args: argparse.Namespace) -> None:
    docker = require_command("docker")
    install = _load_install_config()
    image = args.image or str(install.get("image") or DEFAULT_IMAGE)
    command: list[str | Path] = [docker, "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
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
    _run(command, dry_run=args.dry_run)


def _run_singularity(config_path: Path, args: argparse.Namespace) -> None:
    install = _load_install_config()
    executable = str(install.get("singularity_command") or _singularity_command())
    if not executable:
        raise OncoTracerError("Apptainer or Singularity is required")
    sif_value = args.sif or install.get("sif")
    if not sif_value:
        raise OncoTracerError("no SIF is configured; run 'oncotracer install --singularity'")
    sif = require_file(Path(str(sif_value)), "OncoTracer SIF")
    command: list[str | Path] = [executable, "exec", "--cleanenv"]
    for mount in _project_mounts(config_path):
        command.extend(["--bind", f"{mount}:{mount}"])
    command.extend([sif, "oncotracer", "internal-run", "--config", config_path, "--backend", "host"])
    if args.threads:
        command.extend(["--threads", str(args.threads)])
    if args.force:
        command.append("--force")
    _run(command, dry_run=args.dry_run)


def execute_run(config_path: Path, args: argparse.Namespace) -> Path | None:
    config_path = require_file(config_path, "OncoTracer YAML config")
    backend = _backend_from(args)
    if backend in {"host", "poetry", "conda"}:
        if backend == "conda":
            install = _load_install_config()
            required = {"core_prefix", "qdnaseq_prefix", "ichorcna_prefix"}
            missing = sorted(key for key in required if not install.get(key))
            if missing:
                raise OncoTracerError(
                    "Conda backend is not installed; run 'oncotracer install --conda' "
                    f"(missing: {', '.join(missing)})"
                )
        return _run_host(config_path, args)
    if backend == "docker":
        _run_docker(config_path, args)
        return None
    if backend in {"singularity", "apptainer"}:
        _run_singularity(config_path, args)
        return None
    raise OncoTracerError(
        f"unsupported backend {backend!r}; choose host, conda, docker, singularity, or poetry"
    )


def command_run(args: argparse.Namespace) -> int:
    outdir = execute_run(Path(args.config), args)
    if outdir is not None:
        print(f"OncoTracer native analysis completed: {outdir}")
    return 0


def command_auto(args: argparse.Namespace) -> int:
    root = runtime_root(args.root)
    script = require_file(root / "bin" / "scripts" / "generate_auto_params.sh", "Automatic Setup script")
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


def prepare_quickstart1(root_path: Path) -> tuple[Path, Path]:
    from .runtime import render_flat_yaml

    root_path = root_path.expanduser().resolve()
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
    configs = root_path / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    illumina_config = configs / "illumina.quickstart.yml"
    ont_config = configs / "ont.quickstart.yml"
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
                "force": True,
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
                "force": True,
            }
        ),
    )
    return illumina_config, ont_config


def prepare_quickstart2(root: Path, test_root: Path, *, dry_run: bool = False) -> Path:
    manifest = require_file(root / "examples" / "hcc1143_lpwgs" / "manifest.tsv", "HCC1143 manifest")
    reads = test_root / "public" / "hcc1143_lpwgs"
    reads.mkdir(parents=True, exist_ok=True)
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if dry_run:
                print(f"Would download {row['url']} -> {reads / row['filename']}")
            else:
                download(
                    row["url"],
                    reads / row["filename"],
                    expected_bytes=int(row["bytes"]),
                    expected_md5=row["md5"],
                )
    samples = reads / "samples.csv"
    with samples.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_name", "status"])
        for sample in ("HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"):
            writer.writerow([sample, "TUMOR"])
    config_dir = test_root / "configs" / "hcc1143_lpwgs"
    outdir = test_root / "runs" / "hcc1143_lpwgs"
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


def command_quickstart(args: argparse.Namespace) -> int:
    root = runtime_root(args.root)
    test_root = Path(args.test_root).expanduser().resolve()
    if args.number == "1":
        illumina, ont = prepare_quickstart1(test_root)
        if not args.download_only:
            execute_run(illumina, args)
            execute_run(ont, args)
            verify = require_file(root / "examples" / "quickstart" / "verify_outputs.py", "QuickStart verifier")
            _run([sys.executable, verify, "--test-root", test_root], cwd=root)
    else:
        config = prepare_quickstart2(root, test_root, dry_run=args.dry_run)
        if not args.download_only:
            execute_run(config, args)
            required = [
                test_root / "runs" / "hcc1143_lpwgs" / "06_workflow_summary" / "workflow_summary.txt",
                test_root / "runs" / "hcc1143_lpwgs" / "03_cna_codification" / "cna_events.tsv",
                test_root / "runs" / "hcc1143_lpwgs" / "04_cna_custom_plots" / "cna_per_sample_pages.pdf",
            ]
            for path in required:
                require_file(path, "QuickStart 2 output")
    print(f"QuickStart {args.number} completed: {test_root}")
    return 0


def _check_process(command: Sequence[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    text = (completed.stdout + completed.stderr).strip().splitlines()
    return {
        "command": shlex.join(command),
        "returncode": completed.returncode,
        "first_line": text[0] if text else "",
    }


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
    if backend in {"host", "poetry", "conda"}:
        install = _load_install_config()
        environment = _native_environment(install)
        commands = ["samtools", "bwa", "minimap2", "pigz"]
        checks["commands"] = {
            command: _check_process([command, "--version"], env=environment)
            for command in commands
            if shutil.which(command, path=environment.get("PATH"))
        }
        prefixes = {}
        for name in ("core", "qdnaseq", "ichorcna"):
            value = install.get(f"{name}_prefix")
            if value:
                path = Path(str(value))
                prefixes[name] = {"path": str(path), "exists": path.is_dir()}
        checks["prefixes"] = prefixes
    elif backend == "docker":
        docker = require_command("docker")
        image = args.image or str(_load_install_config().get("image") or DEFAULT_IMAGE)
        checks["docker"] = _check_process([docker, "run", "--rm", image, "doctor", "--backend", "host"])
    elif backend in {"singularity", "apptainer"}:
        executable = _singularity_command()
        checks["runtime"] = executable
        checks["sif"] = str(_load_install_config().get("sif") or "")
    success = True
    for value in checks.get("commands", {}).values() if isinstance(checks.get("commands"), dict) else []:
        success = success and value.get("returncode") == 0
    checks["success"] = success
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if success else 1


def _add_common_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("host", "conda", "docker", "singularity", "poetry"))
    parser.add_argument("--threads", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", help="Repository or extracted payload root")
    parser.add_argument("--image")
    parser.add_argument("--sif")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oncotracer",
        description="Native LP-WGS CNA analysis without Nextflow.",
    )
    parser.add_argument("--version", action="version", version=f"OncoTracer {__version__}")
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

    quickstart = subparsers.add_parser("quickstart", help="Run a complete public validation example")
    quickstart.add_argument("number", choices=("1", "2"))
    quickstart.add_argument("--test-root", required=True)
    quickstart.add_argument("--download-only", action="store_true")
    _add_common_run_options(quickstart)
    quickstart.set_defaults(func=command_quickstart)

    doctor = subparsers.add_parser("doctor", help="Verify the selected backend")
    doctor.add_argument("--backend", choices=("host", "conda", "docker", "singularity", "poetry"))
    doctor.add_argument("--image")
    doctor.set_defaults(func=command_doctor)
    return parser


def _legacy_to_modern(values: list[str]) -> list[str]:
    """Translate v1 launcher syntax while keeping v2 execution native."""
    if (
        not values
        or values[0] in {"install", "run", "internal-run", "auto", "quickstart", "doctor"}
        or values[0] in {"-h", "--help", "--version"}
    ):
        return values
    backend = None
    for flag, name in (("--docker", "docker"), ("--singularity", "singularity"), ("--conda", "conda")):
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
        return int(args.func(args))
    except OncoTracerError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
