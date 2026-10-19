"""Explicit continuation of an owned project, including a moved project tree."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .runtime import OncoTracerError, atomic_write_json, load_flat_yaml, sha256_file, utc_now


def project_path(value):
    path = Path(value).expanduser().absolute()
    if path.name == "results" and (path.parent / "config/run.yml").is_file():
        path = path.parent
    if not (path / "config/run.yml").is_file():
        raise OncoTracerError("Choose one complete OncoTracer project containing config/run.yml and results, not the parent of several projects.")
    from .output_safety import _reject_symlink_components
    _reject_symlink_components(path)
    return path


def _rebase(value, old, new):
    if isinstance(value, str):
        return str(new) if value == str(old) else value.replace(str(old) + "/", str(new) + "/")
    if isinstance(value, list):
        return [_rebase(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _rebase(item, old, new) for key, item in value.items()}
    return value


def _runtime(backend, config):
    from .output_safety import current_runtime_identity, _IDENTITY_KEYS
    from .runtime import runtime_root
    if backend == "docker":
        from .cli import DEFAULT_IMAGE, _load_install_config
        image = config.get("docker_image") or _load_install_config().get("image") or DEFAULT_IMAGE
        # Use the image's real launcher: importing its source with python loses
        # the standalone binary identity and selects a different runtime payload.
        command = ["docker", "run", "--rm", "--network", "none", "--user",
                   f"{os.getuid()}:{os.getgid()}", "--env", "HOME=/tmp", str(image),
                   "provenance", "--runtime-identity"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OncoTracerError(f"Cannot verify the selected Docker runtime before resuming: {error}") from error
        if result.returncode:
            raise OncoTracerError("Cannot verify the selected Docker runtime before resuming. Select an image built from the current source: " + result.stderr[-1000:])
        try:
            identity = json.loads(result.stdout)
        except ValueError as error:
            raise OncoTracerError("Docker returned an invalid runtime identity") from error
        if not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS or not identity.get("runtime_payload_sha256"):
            raise OncoTracerError("Docker returned an incomplete runtime identity")
        return identity
    if backend not in {"host", "conda", "poetry"}:
        raise OncoTracerError("Existing-project resume currently supports Conda, host, Poetry and Docker.")
    return current_runtime_identity(runtime_root())


def sample_snapshot(config):
    from .engine import parse_illumina_samplesheet, parse_ont_samples
    from .fastq_inputs import fastq_paths
    from .ont_layout import pass_fastq_files
    samples = (parse_ont_samples(config) if config["mode"] == "ont" else
               parse_illumina_samplesheet(Path(config["illumina_samplesheet"])))
    snapshot, reviewed = {}, []
    labels = {}
    if config.get("sample_metadata"):
        import csv
        with Path(config["sample_metadata"]).open() as handle:
            labels = {row["sample"]: row.get("sample_type") for row in csv.DictReader(handle)}
    for sample in samples:
        files = (pass_fastq_files(sample.fastq_dir) if config["mode"] == "ont" else
                 (*fastq_paths(sample.fastq_1), *fastq_paths(sample.fastq_2)))
        for path in files:
            metadata = path.stat()
            snapshot[str(path)] = (metadata.st_size, metadata.st_mtime_ns, metadata.st_ino, metadata.st_dev)
        reviewed.append({"name": sample.sample, "analysis_role": sample.status,
                         "sample_type": labels.get(sample.sample) or ("cancer" if sample.status == "tumor" else "normal"),
                         "file_count": len(files), "barcode": getattr(sample, "barcode", None)})
    return snapshot, reviewed


def resume_project(value, *, backend=None, image=None):
    """Back up configuration, rebase owned paths, and authorize the selected runtime.

    Never start tools, delete results, bless a stage, or accept a classifier license.
    Configuration and the existing run lock are both held through the transaction.
    """
    from .output_safety import (OUTPUT_LOCK_RELATIVE, OUTPUT_OWNER_RELATIVE, _parse_owner,
                                _canonical_path_sha256, _validate_reserved_tree, configuration_lock)
    from .setup import _save_setup_files_locked, _render_config
    project = project_path(value)
    config_path = project / "config/run.yml"
    with contextlib.ExitStack() as stack:
        stack.enter_context(configuration_lock(project / "config", exclusive=True, directory=True))
        stack.enter_context(configuration_lock(config_path, exclusive=True))
        config = load_flat_yaml(config_path)
        backend = backend or str(config.get("execution_backend") or "conda")
        if image:
            if backend != "docker":
                raise OncoTracerError("An image override requires the Docker backend")
            config["docker_image"] = str(image)
        old_output = Path(str(config.get("outdir") or ""))
        if not old_output.is_absolute() or old_output.name != "results":
            raise OncoTracerError("Resume requires a setup project with an absolute PROJECT/results output path.")
        old = old_output.parent
        output = project / "results"
        moved = old != project
        if moved and old.exists():
            raise OncoTracerError("The original project still exists. Move the entire project, or choose the original; copied projects are not silently adopted.")
        if output.is_symlink():
            raise OncoTracerError("Project results must not be a symlink")
        owner_path = output / OUTPUT_OWNER_RELATIVE
        owner = None
        if output.exists() and any(output.iterdir()):
            owner = _parse_owner(owner_path)
            old_path_hash = hashlib.sha256(os.fsencode(str(old_output))).hexdigest()
            if owner["canonical_path_sha256"] != old_path_hash:
                raise OncoTracerError("Output ownership does not match the saved configuration; nothing was changed.")
            lock_path = output / OUTPUT_LOCK_RELATIVE
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stack.callback(os.close, descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OncoTracerError("Unsafe project run lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OncoTracerError("This project is still running. Stop its analysis before resuming or moving it.") from error
        elif output.exists() and not output.is_dir():
            raise OncoTracerError("Project results is not a directory")
        identity = _runtime(backend, config) if owner else None
        if owner and str(owner["runtime_identity"].get("oncotracer_version", "")).split(".")[0] != str(identity.get("oncotracer_version", "")).split(".")[0]:
            raise OncoTracerError("Resume across different major versions requires an explicit data migration.")
        updated = _rebase(config, old, project) if moved else dict(config)
        updated["execution_backend"] = backend
        changes = {"run.yml": _render_config(updated)}
        for name in ("samplesheet.csv", "sample_metadata.csv"):
            file = project / "config" / name
            if file.exists():
                changes[name] = _rebase(file.read_text(), old, project) if moved else file.read_text()
        receipt_path = project / "config/resume.json"
        if receipt_path.is_symlink():
            raise OncoTracerError("Resume receipt must not be a symlink")
        previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        canonical = previous.get("canonical_project", str(old))
        links = []
        if moved and output.exists():
            # Relink only absolute symlinks into this same project; never follow them.
            for directory, dirs, files in os.walk(output, followlinks=False):
                for name in dirs + files:
                    path = Path(directory) / name
                    if path.is_symlink():
                        target = os.readlink(path)
                        rebased = _rebase(target, old, project)
                        if rebased != target:
                            links.append((path, target, rebased))
        originals = {name: (project / "config" / name).read_bytes() for name in changes}
        original_owner = owner_path.read_bytes() if owner else None
        original_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
        replaced = []
        try:
            for path, target, rebased in links:
                path.unlink()
                path.symlink_to(rebased)
                replaced.append((path, target))
            if owner:
                _validate_reserved_tree(output)
            backup = _save_setup_files_locked(project, changes)
            receipt = {"schema": "oncotracer-project-resume-v1", "prepared_at": utc_now(),
                       "canonical_project": canonical, "project": str(project), "previous_project": str(old),
                       "project_locations": list(dict.fromkeys([*previous.get("project_locations", []), str(old), str(project)])),
                       "config_sha256": sha256_file(config_path), "backend": backend,
                       "previous_owner": owner, "moved": moved,
                       "backup": str(backup) if backup else None,
                       "note": "Stage signatures and output integrity are checked at Run; missing or incompatible work is recomputed."}
            atomic_write_json(receipt_path, receipt)
            if owner:
                atomic_write_json(owner_path, dict(owner, canonical_path_sha256=_canonical_path_sha256(output), runtime_identity=identity))
        except Exception:
            for name, content in originals.items():
                (project / "config" / name).write_bytes(content)
            if original_owner is not None:
                owner_path.write_bytes(original_owner)
            if original_receipt is None:
                receipt_path.unlink(missing_ok=True)
            else:
                receipt_path.write_bytes(original_receipt)
            for path, target in reversed(replaced):
                path.unlink()
                path.symlink_to(target)
            raise
    return {"project": str(project), "config_path": str(config_path), "outdir": str(output),
            "backend": backend, "moved": moved, "backup": receipt["backup"]}
