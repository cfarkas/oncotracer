"""Explicit, ownership-checked removal of tools, never project data."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from . import install_safety as safety
from .runtime import OncoTracerError, sha256_file


def _launcher(path: Path) -> dict:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OncoTracerError(
            "launcher must be one regular, non-hardlinked file, not a symlink"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            required = {
                "__main__.py",
                "oncotracer_cli/cli.py",
                "oncotracer_cli/_build_metadata.py",
                "payload/provenance/native-v2-sources.json",
            }
            if not required.issubset(archive.namelist()):
                raise ValueError("not an OncoTracer standalone executable")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise OncoTracerError(
            "not a recognized copied OncoTracer executable; for a pip install use that environment's 'python -m pip uninstall oncotracer'"
        ) from error
    return {
        "sha256": sha256_file(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def uninstall_target(
    target: Path, kind: str, *, dry_run: bool, purge: bool = False
) -> dict:
    target = safety._guard_dedicated(target, "uninstall target")

    def inspect():
        if kind in {"conda", "sif"} and os.path.lexists(
            safety._journal_path(target, kind)
        ):
            raise OncoTracerError(
                "uninstall refuses an interrupted installation; recover it with the matching installer first"
            )
        if kind == "conda":
            state, marker = safety._classify_base(target)
            if state != "owned":
                raise OncoTracerError(
                    f"not a managed OncoTracer installation: {target}"
                )
            children = [
                target / name
                for name in safety.MANAGED_CHILDREN
                if os.path.lexists(target / name)
            ]
            for child in children:
                safety._child_marker_value(target, marker, child.name)
            return children + [target / safety.BASE_MARKER], marker
        if kind == "sif":
            state, marker = safety._classify_sif(target)
            if state != "owned":
                raise OncoTracerError(f"not a managed OncoTracer SIF: {target}")
            return [target, safety._sif_sidecar(target)], marker
        return [target], _launcher(target)

    paths, identity = inspect()
    result = {
        "kind": kind,
        "target": str(target),
        "paths": [str(p) for p in paths],
        "action": (
            "delete verified tools"
            if purge
            else "move verified tools to a recovery folder"
        ),
        "dry_run": dry_run,
        "preserved": "FASTQs, POD5s, BAMs, references, project YAML, results, model caches, unrelated environments and saved installation settings are not removed.",
    }
    if dry_run:
        return result
    if purge and not shutil.rmtree.avoids_symlink_attacks:
        raise OncoTracerError(
            "safe purge requires fd-based shutil.rmtree; omit --purge to retain a recoverable backup"
        )
    # Check before taking an exclusive lock so an active analysis fails promptly.
    with safety.installer_cli_target_arguments(target):
        safety._assert_inactive(target)
        with safety._exclusive_install_lock(
            safety._lock_path(target, kind), target, kind
        ):
            current, observed = inspect()
            if current != paths or observed != identity:
                raise OncoTracerError("uninstall target changed during validation")
            safety._assert_inactive(target)
            recovery = Path(
                tempfile.mkdtemp(
                    prefix=f"{target.name}.oncotracer-uninstalled-", dir=target.parent
                )
            )
            recovery.chmod(0o700)
            record = recovery / "uninstall.json"
            record.write_text(
                json.dumps({**result, "original_identity": identity}, indent=2) + "\n",
                encoding="utf-8",
            )
            moved = []
            try:
                for original in paths:
                    safety._rename_noreplace(
                        original, recovery / original.name, "uninstall recovery move"
                    )
                    moved.append(original)
                # Verify again at the new location; never delete a substituted tree.
                if kind == "conda":
                    for original in paths[:-1]:
                        safety._child_marker_at(
                            recovery / original.name, original, identity, original.name
                        )
                elif kind == "sif":
                    if sha256_file(recovery / target.name) != identity["sif_sha256"]:
                        raise OncoTracerError("SIF changed during uninstall")
                elif sha256_file(recovery / target.name) != identity["sha256"]:
                    raise OncoTracerError("launcher changed during uninstall")
            except BaseException:
                for original in reversed(moved):
                    if not os.path.lexists(original):
                        safety._rename_noreplace(
                            recovery / original.name, original, "uninstall rollback"
                        )
                raise
            if kind == "conda":
                # Keep a root containing any unrelated sibling. Never recurse here.
                try:
                    target.rmdir()
                except OSError:
                    pass
            result["recovery_directory"] = str(recovery)
            if purge:
                # Only this newly created, private, authenticated recovery folder
                # is a recursive deletion target. The user explicitly chose purge.
                safety._assert_inactive(recovery)
                shutil.rmtree(recovery)
                result["recovery_directory"] = None
                result["recoverable"] = False
            else:
                result["recoverable"] = True
    return result


def command_uninstall(args) -> int:
    from .cli import _load_install_config

    install = _load_install_config()
    if args.conda:
        if args.sif:
            raise OncoTracerError("--sif applies only to --singularity")
        value = args.prefix or (
            str(Path(str(install["core_prefix"])).parent)
            if install.get("core_prefix")
            else None
        )
        kind = "conda"
    elif args.singularity:
        if args.prefix:
            raise OncoTracerError("--prefix applies only to --conda")
        value = args.sif or install.get("sif")
        kind = "sif"
    else:
        if args.sif or args.prefix:
            raise OncoTracerError("--launcher does not accept --sif or --prefix")
        value, kind = args.launcher, "launcher"
    if not value:
        raise OncoTracerError(
            "no installed target is recorded; supply the exact --prefix or --sif"
        )
    try:
        result = uninstall_target(
            Path(str(value)),
            kind,
            dry_run=args.dry_run or not args.yes,
            purge=args.purge,
        )
    except OSError as error:
        raise OncoTracerError(
            f"could not uninstall the selected target: {error}"
        ) from error
    print(json.dumps(result, indent=2))
    if result["dry_run"]:
        print(
            "Preview only. Add --yes to uninstall. Add --purge only to delete the tools permanently; otherwise they remain recoverable and still use disk space."
        )
    elif result["recoverable"]:
        print(
            "Tools removed from their installed paths. Recovery folder retained; disk space has not been reclaimed. Restore its named entries to their original paths if needed."
        )
    else:
        print("Verified tools permanently removed; no backup retained.")
    print(
        "Saved settings are retained. Run oncotracer install before using this backend again."
    )
    return 0


def add_uninstall_command(subparsers) -> None:
    parser = subparsers.add_parser(
        "uninstall",
        help="Preview or remove explicitly selected OncoTracer tools; preserve project data",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--conda",
        action="store_true",
        help="managed Conda tools, including a managed Poetry runtime if present",
    )
    group.add_argument(
        "--singularity",
        action="store_true",
        help="managed Singularity/Apptainer image and ownership sidecar",
    )
    group.add_argument(
        "--launcher",
        metavar="PATH",
        help="exact path to a copied standalone executable; not a pip launcher",
    )
    parser.add_argument(
        "--prefix",
        help="exact managed Conda parent, otherwise use saved installation settings",
    )
    parser.add_argument(
        "--sif",
        help="exact managed SIF path, otherwise use saved installation settings",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect and show targets without changing files",
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm removal from the installed paths"
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="with --yes, permanently delete verified tools instead of retaining a recovery folder",
    )
    parser.set_defaults(func=command_uninstall)
