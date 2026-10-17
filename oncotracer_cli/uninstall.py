"""Explicit, ownership-checked removal of tools, never project data."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from . import install_safety as safety
from .runtime import OncoTracerError, sha256_file
from .reporting import operation, status


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

    def conda_domain(root: Path, *, expected_family: str | None = None):
        root = safety._guard_dedicated(root, "uninstall target")
        if os.path.lexists(safety._journal_path(root, "conda")):
            raise OncoTracerError(
                "uninstall refuses an interrupted installation; recover it with the matching installer first"
            )
        state, marker = safety._classify_base(root)
        if state != "owned" or marker is None:
            raise OncoTracerError(f"not a managed OncoTracer installation: {root}")
        if expected_family and marker["managed_children"] != [expected_family]:
            raise OncoTracerError(
                f"managed {expected_family} sibling has a different ownership family: {root}"
            )
        children = [
            root / name
            for name in marker["managed_children"]
            if os.path.lexists(root / name)
        ]
        for child in children:
            safety._child_marker_value(root, marker, child.name)
        return root, marker, children

    def inspect():
        if kind == "conda":
            domains = [conda_domain(target)]
            marker = domains[0][1]
            if marker["managed_children"] == list(safety.MANAGED_CHILDREN):
                for suffix, family in (("variants", "variants"), ("poetry", "poetry-runtime")):
                    sibling = safety.managed_sibling_base(target, suffix)
                    if os.path.lexists(sibling) or os.path.lexists(
                        safety._journal_path(sibling, "conda")
                    ):
                        domains.append(conda_domain(sibling, expected_family=family))
            paths = [
                path
                for root, _, children in domains
                for path in [*children, root / safety.BASE_MARKER]
            ]
            return paths, marker, domains
        if kind == "sif":
            if os.path.lexists(safety._journal_path(target, kind)):
                raise OncoTracerError(
                    "uninstall refuses an interrupted installation; recover it with the matching installer first"
                )
            state, marker = safety._classify_sif(target)
            if state != "owned":
                raise OncoTracerError(f"not a managed OncoTracer SIF: {target}")
            return [target, safety._sif_sidecar(target)], marker, []
        return [target], _launcher(target), []

    paths, identity, domains = inspect()
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
    if domains:
        result["managed_roots"] = [str(root) for root, _, _ in domains]
    if dry_run:
        return result
    if purge and not shutil.rmtree.avoids_symlink_attacks:
        raise OncoTracerError(
            "safe purge requires fd-based shutil.rmtree; omit --purge to retain a recoverable backup"
        )
    lock_targets = {root for root, _, _ in domains} or {target}
    if kind == "conda" and identity["managed_children"] == list(safety.MANAGED_CHILDREN):
        # Lock absent canonical siblings too: an additive install must not
        # appear between the final inspection and removal of the parent tools.
        lock_targets.update(
            safety.managed_sibling_base(target, suffix)
            for suffix in ("variants", "poetry")
        )
    lock_targets = sorted(lock_targets, key=str)
    # Inspect every independent domain before moving any asset. Hold all locks
    # through validation, recovery moves and rollback in one deterministic order.
    with contextlib.ExitStack() as stack:
        stack.enter_context(safety.installer_cli_target_arguments(*lock_targets))
        for root in lock_targets:
            safety._assert_inactive(root)
        for root in lock_targets:
            stack.enter_context(safety._exclusive_install_lock(
                safety._lock_path(root, kind), root, kind
            ))
        current, observed, current_domains = inspect()
        if current != paths or observed != identity or current_domains != domains:
            raise OncoTracerError("uninstall target changed during validation")
        for root in lock_targets:
            safety._assert_inactive(root)
        recovery = Path(
            tempfile.mkdtemp(
                prefix=f"{target.name}.oncotracer-uninstalled-", dir=target.parent
            )
        )
        recovery.chmod(0o700)
        destinations = {original: recovery / original.name for original in paths}
        for root, _, children in domains[1:]:
            bucket = recovery / "domains" / root.name
            bucket.mkdir(parents=True, mode=0o700)
            for original in [*children, root / safety.BASE_MARKER]:
                destinations[original] = bucket / original.name
        record = recovery / "uninstall.json"
        recovery_entries = {
            str(original): str(destination.relative_to(recovery))
            for original, destination in destinations.items()
        }
        record.write_text(
            json.dumps({
                **result,
                "original_identity": identity,
                "domain_identities": [
                    {"target": str(root), "marker": marker}
                    for root, marker, _ in domains
                ],
                "recovery_entries": recovery_entries,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        moved = []
        try:
            for index, original in enumerate(paths):
                status(f"Removing {original.name}", completed=index, total=len(paths))
                safety._rename_noreplace(
                    original, destinations[original], "uninstall recovery move"
                )
                moved.append(original)
            # Verify using each domain's original canonical paths and source.
            if kind == "conda":
                for root, marker, children in domains:
                    if safety._safe_read_json(
                        destinations[root / safety.BASE_MARKER],
                        "uninstall root ownership marker",
                    ) != marker:
                        raise OncoTracerError("managed root ownership changed during uninstall")
                    for original in children:
                        safety._child_marker_at(
                            destinations[original], original, marker, original.name
                        )
            elif kind == "sif":
                if sha256_file(destinations[target]) != identity["sif_sha256"]:
                    raise OncoTracerError("SIF changed during uninstall")
            elif sha256_file(destinations[target]) != identity["sha256"]:
                raise OncoTracerError("launcher changed during uninstall")
        except BaseException:
            for original in reversed(moved):
                if not os.path.lexists(original):
                    safety._rename_noreplace(
                        destinations[original], original, "uninstall rollback"
                    )
            raise
        if kind == "conda":
            # Keep roots containing unrelated siblings. Never recurse here.
            for root, _, _ in domains:
                try:
                    root.rmdir()
                except OSError:
                    pass
        result["recovery_directory"] = str(recovery)
        if purge:
            # Only this private, authenticated recovery folder is deleted.
            safety._assert_inactive(recovery)
            shutil.rmtree(recovery)
            result["recovery_directory"] = None
            result["recoverable"] = False
        else:
            result["recoverable"] = True
    return result


def command_uninstall(args) -> int:
    with operation("Checking tools selected for removal", verbose=getattr(args, "verbose", False)):
        return _command_uninstall(args)


def _command_uninstall(args) -> int:
    from .cli import _load_install_config
    from .user_launcher import installation_target, remove_transaction

    explicit_global = bool(getattr(args, "global_launcher", False))
    # Fully specified removal targets do not depend on saved settings, which
    # may be missing or damaged precisely when a user needs to uninstall.
    explicit_target = (
        (args.conda and args.prefix)
        or (args.singularity and args.sif)
        or args.launcher
        or getattr(args, "global_launcher", False)
    )
    install = {} if explicit_target else _load_install_config()
    if not any((args.conda, args.singularity, args.launcher, getattr(args, "global_launcher", False))):
        backend = install.get("backend")
        if backend in {"conda", "poetry"}:
            args.conda = True
        elif backend == "singularity":
            args.singularity = True
        elif backend == "docker":
            args.global_launcher = True
        else:
            raise OncoTracerError("select the tools to remove: --conda, --singularity, --global-launcher, or --launcher PATH")
    preview = args.dry_run or not args.yes
    global_only = getattr(args, "global_launcher", False)
    if global_only:
        if args.sif or args.prefix:
            raise OncoTracerError("--global-launcher does not accept --sif or --prefix")
        target = installation_target(install) if install.get("backend") == "docker" else None
        with remove_transaction(target, dry_run=preview, purge=args.purge, required=explicit_global) as command:
            result = command.detach()
        result.update({"kind": "global-launcher", "preserved": "Scientific tools, Docker images, saved settings and all project data are retained."})
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2))
        else:
            print(f"{'Uninstall preview' if preview else 'Uninstall complete'}: user command")
            for path in result["paths"]:
                print(f"  {path}")
            if result.get("reason"):
                print(result["reason"])
            if result.get("recovery_directory"):
                print(f"Recovery folder: {result['recovery_directory']}")
            print(result["preserved"])
            if preview:
                print("Preview only. Add --yes to remove the owned command.")
        return 0
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
        # A copied archive can also be the exact source of the global command.
        # Removing other copied executables must leave that command untouched.
        command_context = (
            remove_transaction(archive=Path(str(value)), dry_run=preview, purge=args.purge)
            if kind == "launcher" else remove_transaction(
                {"kind": kind, "target": os.path.abspath(os.path.expanduser(str(value)))},
                dry_run=preview, purge=args.purge,
            )
        )
        with command_context as command:
            if command is not None:
                command.detach()
            result = uninstall_target(
                Path(str(value)), kind, dry_run=preview, purge=args.purge,
            )
        if command is not None:
            result["global_launcher"] = command.result
            result["paths"].extend(command.result["paths"])
    except OSError as error:
        raise OncoTracerError(
            f"could not uninstall the selected target: {error}"
        ) from error
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0
    print(f"{'Uninstall preview' if result['dry_run'] else 'Uninstall complete'}: {kind}")
    for path in result["paths"]:
        print(f"  {path}")
    if result.get("recovery_directory"):
        print(f"Recovery folder: {result['recovery_directory']}")
    if result["dry_run"]:
        print(
            "Preview only. Add --yes to uninstall. Add --purge only to delete the tools permanently; otherwise they remain recoverable and still use disk space."
        )
    elif result["recoverable"]:
        print(
            "Tools removed from their installed paths. Recovery folder retained; disk space has not been reclaimed. The uninstall.json record maps each recovery entry to its original path."
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--conda",
        action="store_true",
        help="managed Conda tools, including managed variants and Poetry runtimes",
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
    group.add_argument(
        "--global-launcher",
        action="store_true",
        help="remove only this installation's owned ~/.local/bin/oncotracer; keep scientific tools and container images",
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
    parser.add_argument("--json", action="store_true", help="machine-readable removal plan or result")
    parser.add_argument("--verbose", action="store_true", help="show detailed checks")
    parser.set_defaults(func=command_uninstall)
