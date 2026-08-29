#!/usr/bin/env python3
"""Adversarial ownership and rollback tests for native installers."""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import venv
import unittest
from pathlib import Path
from unittest import mock

from oncotracer_cli import cli, install_safety
from oncotracer_cli.runtime import OncoTracerError


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ACTIVE_PROCESSES = install_safety._active_processes


SOURCE = {
    "oncotracer_version": "2.0.0",
    "source_commit": "a" * 40,
    "source_sha256": "b" * 64,
}


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _snapshot(root: Path) -> dict[str, tuple[str, int, bytes]]:
    if not os.path.lexists(root):
        return {}
    paths = [root, *sorted(root.rglob("*"))]
    observed: dict[str, tuple[str, int, bytes]] = {}
    for path in paths:
        relative = "." if path == root else str(path.relative_to(root))
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            observed[relative] = ("symlink", mode, os.fsencode(os.readlink(path)))
        elif stat.S_ISDIR(metadata.st_mode):
            observed[relative] = ("directory", mode, b"")
        elif stat.S_ISREG(metadata.st_mode):
            observed[relative] = ("file", mode, path.read_bytes())
        else:
            observed[relative] = ("special", mode, b"")
    return observed


class InstallerSourceIdentityTests(unittest.TestCase):
    def test_mutating_install_requires_matching_clean_source_identity(self) -> None:
        valid = {
            **SOURCE,
            "source_tree_dirty": False,
        }
        with mock.patch.object(install_safety, "get_provenance", return_value=valid):
            self.assertEqual(install_safety._source_identity(), SOURCE)
        for changed in (
            {**valid, "source_tree_dirty": True},
            {**valid, "oncotracer_version": "1.1.0"},
        ):
            with (
                self.subTest(changed=changed),
                mock.patch.object(
                    install_safety, "get_provenance", return_value=changed
                ),
                self.assertRaises(OncoTracerError),
            ):
                install_safety._source_identity()


class ManagedInstallerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer-install-safe-")
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        self.log = self.scratch / "installer.log"
        self.conda = _write_executable(
            self.scratch / "fake-conda",
            """#!/usr/bin/env python3
import os
import re
import pathlib
import sys
with open(os.environ["FAKE_INSTALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write("conda\\t" + "\\t".join(sys.argv[1:]) + "\\n")
prefix = pathlib.Path(sys.argv[sys.argv.index("--prefix") + 1])
(prefix / "conda-meta").mkdir(parents=True)
(prefix / "conda-meta" / "history").write_text("created\\n", encoding="utf-8")
(prefix / "bin").mkdir()
python = prefix / "bin" / "python"
python.write_text("#!/usr/bin/env python3\\nprint('CORE_OK CLASSIFIER_OK')\\n", encoding="utf-8")
python.chmod(0o755)
rscript = prefix / "bin" / "Rscript"
rscript.write_text("#!/bin/sh\\necho QDNASEQ_OK ICHORCNA_OK\\n", encoding="utf-8")
rscript.chmod(0o755)
gistic = prefix / "bin" / "gistic2"
libraries = [
    prefix / "share" / "mcr-8.3-0" / "v83" / "runtime" / "glnxa64",
    prefix / "share" / "mcr-8.3-0" / "v83" / "bin" / "glnxa64",
    prefix / "share" / "mcr-8.3-0" / "v83" / "sys" / "os" / "glnxa64",
]
for library in libraries:
    library.mkdir(parents=True, exist_ok=True)
sibling = prefix / "bin" / "gistic-probe-sibling"
sibling.write_text(
    "#!/bin/sh\\necho 'Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file'\\n",
    encoding="utf-8",
)
sibling.chmod(0o755)
expected = os.pathsep.join(str(path) for path in libraries)
gistic.write_text(
    "#!/bin/sh\\n"
    + "test \\\"$LD_LIBRARY_PATH\\\" = " + repr(expected) + " || exit 88\\n"
    + "test \\\"${LD_LIBRARY_PATH_MCR+x}:$LD_LIBRARY_PATH_MCR\\\" = 'x:' || exit 89\\n"
    + '"$(dirname "$0")/gistic-probe-sibling"\\n',
    encoding="utf-8",
)
gistic.chmod(0o755)
probe = prefix / "bin" / "prefix-probe"
probe.write_text(
    "#!" + str(prefix / "bin" / "python") + "\\n",
    encoding="utf-8",
)
probe.chmod(0o755)
""",
        )
        self.poetry = _write_executable(
            self.scratch / "fake-poetry",
            r"""#!/usr/bin/env python3
import base64
import csv
import hashlib
import io
import os
import re
import pathlib
import sys
import zipfile
args = sys.argv[1:]
with open(os.environ["FAKE_INSTALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write("poetry\t" + "\t".join(args) + "\n")
if args == ["--version"]:
    print("Poetry (version 2.4.1)")
    raise SystemExit(0)
if not args or args[0] != "build":
    raise SystemExit(93)
out = pathlib.Path(args[args.index("--output") + 1])
out.mkdir(parents=True, exist_ok=True)
wheel = out / "oncotracer-2.0.0-py3-none-any.whl"
record = {
    "oncotracer_version": os.environ["FAKE_SOURCE_VERSION"],
    "source_commit": os.environ["FAKE_SOURCE_COMMIT"],
    "source_sha256": os.environ["FAKE_SOURCE_SHA256"],
    "source_tree_dirty": False,
}
files = {
    "oncotracer_cli/__init__.py": b'__version__ = "2.0.0"\n',
    "oncotracer_cli/cli.py": (
        "import json,sys\n"
        f"record={record!r}\n"
        "def main():\n"
        " a=sys.argv[1:]\n"
        " if a==['--version']: print('OncoTracer 2.0.0'); return 0\n"
        " if a==['provenance','--json']: print(json.dumps(record)); return 0\n"
        " return 2\n"
    ).encode(),
    "bin/scripts/native_qdnaseq.R": b"cat('native')\n",
    "environments/native-core.yml": b"name: core\n",
    "provenance/native-v2-sources.json": b"{}\n",
    "oncotracer-2.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: oncotracer\nVersion: 2.0.0\n",
    "oncotracer-2.0.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    "oncotracer-2.0.0.dist-info/entry_points.txt": b"[console_scripts]\noncotracer = oncotracer_cli.cli:main\n",
}
rows=[]
with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
    for name,data in files.items():
        archive.writestr(name,data)
        digest=hashlib.sha256(data).digest()
        rows.append((name,"sha256="+base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),str(len(data))))
    record_name="oncotracer-2.0.0.dist-info/RECORD"
    rows.append((record_name,"",""))
    output=io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    archive.writestr(record_name,output.getvalue().encode())
""",
        )
        self.apptainer = _write_executable(
            self.scratch / "fake-apptainer",
            """#!/usr/bin/env python3
import json
import os
import re
import pathlib
import sys
args = sys.argv[1:]
with open(os.environ["FAKE_INSTALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write("apptainer\\t" + "\\t".join(args) + "\\n")
if args[0] == "pull":
    pathlib.Path(args[1]).write_bytes(os.environ.get("FAKE_SIF_CONTENT", "sif-one").encode())
elif "doctor" in args:
    print(json.dumps({"success": os.environ.get("FAKE_DOCTOR_FAIL") != "1"}))
elif "provenance" in args:
    print(json.dumps({
        "oncotracer_version": os.environ["FAKE_SOURCE_VERSION"],
        "source_commit": os.environ["FAKE_SOURCE_COMMIT"],
        "source_sha256": os.environ["FAKE_SOURCE_SHA256"],
        "source_tree_dirty": False,
    }))
else:
    raise SystemExit(91)
""",
        )
        self.environment = {
            "FAKE_INSTALL_LOG": str(self.log),
            "FAKE_SOURCE_VERSION": str(SOURCE["oncotracer_version"]),
            "FAKE_SOURCE_COMMIT": str(SOURCE["source_commit"]),
            "FAKE_SOURCE_SHA256": str(SOURCE["source_sha256"]),
        }
        self.enterContext(mock.patch.dict(os.environ, self.environment, clear=False))
        self.enterContext(
            mock.patch.object(install_safety, "_source_identity", return_value=SOURCE)
        )
        self.enterContext(
            mock.patch.object(install_safety, "_active_processes", return_value=[])
        )
        self.poetry_snapshot: install_safety._PoetrySourceSnapshot | None = None

        def verified_poetry_snapshot(*_args, **_kwargs):
            if self.poetry_snapshot is None:
                archive = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(ROOT),
                        "-c",
                        "tar.umask=0002",
                        "archive",
                        "--format=tar",
                        "HEAD",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                self.poetry_snapshot = install_safety._poetry_snapshot_from_archive(
                    archive
                )
            return self.poetry_snapshot

        self.enterContext(
            mock.patch.object(
                install_safety,
                "_verify_poetry_source_checkout",
                side_effect=verified_poetry_snapshot,
            )
        )

    def _conda_install(
        self,
        base: Path,
        *,
        force: bool = False,
        poetry: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Path]:
        return install_safety.install_conda_managed(
            ROOT,
            base,
            conda=str(self.conda),
            force=force,
            dry_run=dry_run,
            poetry=str(self.poetry) if poetry else None,
        )

    def _sif_install(
        self, destination: Path, *, force: bool = False, dry_run: bool = False
    ) -> dict[str, object]:
        return install_safety.install_sif_managed(
            destination,
            executable=str(self.apptainer),
            image="ghcr.io/cfarkas/oncotracer:2.0.0",
            force=force,
            dry_run=dry_run,
        )

    def _log_lines(self) -> list[str]:
        return (
            self.log.read_text(encoding="utf-8").splitlines()
            if self.log.exists()
            else []
        )

    def test_conda_unowned_target_is_preserved_with_and_without_force(self) -> None:
        for force in (False, True):
            with self.subTest(force=force):
                base = self.scratch / f"unowned-{force}"
                base.mkdir()
                (base / "patient-sentinel.txt").write_bytes(b"preserve exactly")
                before = _snapshot(base)
                with self.assertRaisesRegex(OncoTracerError, "unowned Conda"):
                    self._conda_install(base, force=force)
                self.assertEqual(_snapshot(base), before)
        self.assertEqual(self._log_lines(), [])

    def test_conda_rejects_broad_symlink_hardlink_and_foreign_lock(self) -> None:
        broad = self.scratch / "broad-data-root"
        broad.mkdir()
        (broad / "sentinel").write_bytes(b"broad")
        broad_before = _snapshot(broad)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(broad)}, clear=False):
            with self.assertRaisesRegex(OncoTracerError, "dedicated child"):
                self._conda_install(broad, force=True)
        self.assertEqual(_snapshot(broad), broad_before)

        mount_root = self.scratch / "simulated-storage-mount"
        mount_root.mkdir()
        (mount_root / "sentinel").write_bytes(b"mount root")
        mount_before = _snapshot(mount_root)
        with (
            mock.patch.object(
                install_safety.os.path,
                "ismount",
                side_effect=lambda value: Path(value) == mount_root,
            ),
            self.assertRaisesRegex(OncoTracerError, "dedicated child"),
        ):
            self._conda_install(mount_root, force=True)
        self.assertEqual(_snapshot(mount_root), mount_before)
        target = self.scratch / "symlink-target"
        target.mkdir()
        (target / "sentinel").write_bytes(b"symlink")
        link = self.scratch / "symlink-prefix"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, "symlink"):
            self._conda_install(link, force=True)
        self.assertEqual((target / "sentinel").read_bytes(), b"symlink")

        hardlinked = self.scratch / "hardlinked-marker"
        hardlinked.mkdir()
        marker_source = self.scratch / "marker-source"
        marker_source.write_text("{}\n", encoding="utf-8")
        os.link(marker_source, hardlinked / install_safety.BASE_MARKER)
        before = _snapshot(hardlinked)
        with self.assertRaisesRegex(OncoTracerError, "non-hardlinked"):
            self._conda_install(hardlinked, force=True)
        self.assertEqual(_snapshot(hardlinked), before)

        base = self.scratch / "lock-collision"
        lock = install_safety._lock_path(base, "conda")
        lock.write_bytes(b"unrelated lock sentinel")
        before_lock = lock.read_bytes()
        with self.assertRaisesRegex(
            OncoTracerError, "lock is malformed|lock is foreign"
        ):
            self._conda_install(base)
        self.assertEqual(lock.read_bytes(), before_lock)
        self.assertFalse(base.exists())
        self.assertEqual(self._log_lines(), [])

    def test_transaction_cleanup_refuses_foreign_filesystem_member(self) -> None:
        target = self.scratch / "managed-target"
        transaction_id, transaction = install_safety._new_transaction(target, "conda")
        foreign = transaction / "foreign-filesystem"
        foreign.mkdir()
        (foreign / "sentinel").write_bytes(b"preserve")
        before = _snapshot(transaction)
        real_lstat = Path.lstat

        def foreign_device(path):
            metadata = real_lstat(path)
            if Path(path) == foreign:
                values = list(metadata)
                values[2] = metadata.st_dev + 1
                return os.stat_result(values)
            return metadata

        with (
            mock.patch.object(Path, "lstat", new=foreign_device),
            self.assertRaisesRegex(OncoTracerError, "crosses filesystems"),
        ):
            install_safety._preserve_staging_transaction(
                transaction, target, transaction_id, "conda"
            )
        self.assertEqual(_snapshot(transaction), before)

    def test_conda_fresh_reuse_force_and_unrelated_sibling(self) -> None:
        base = self.scratch / "managed-envs"
        result = self._conda_install(base)
        self.assertEqual(set(result), set(install_safety.CONDA_NAMES))
        self.assertEqual(len(self._log_lines()), 5)
        marker = json.loads((base / install_safety.BASE_MARKER).read_text())
        install_id = marker["install_id"]
        for name in install_safety.CONDA_NAMES:
            self.assertTrue((base / name / "conda-meta" / "history").is_file())
            child = json.loads(
                (base / name / install_safety.ENV_MARKER).read_text(encoding="utf-8")
            )
            self.assertEqual(child["install_id"], install_id)
            self.assertEqual(child["canonical_path"], str(base / name))

        prefix_probe = base / "core" / "bin" / "prefix-probe"
        self.assertEqual(
            prefix_probe.read_text(encoding="utf-8").splitlines()[0],
            f"#!{base / 'core' / 'bin' / 'python'}",
        )
        executed = subprocess.run(
            [prefix_probe], text=True, capture_output=True, check=False
        )
        self.assertEqual(executed.returncode, 0, executed.stderr)

        unrelated = base / "unrelated-project"
        unrelated.mkdir()
        (unrelated / "sentinel").write_bytes(b"do not alter")
        unrelated_before = _snapshot(unrelated)
        self._conda_install(base)
        self.assertEqual(len(self._log_lines()), 5)
        self._conda_install(base, force=True)
        self.assertEqual(len(self._log_lines()), 10)
        marker_after = json.loads((base / install_safety.BASE_MARKER).read_text())
        self.assertEqual(marker_after["install_id"], install_id)
        self.assertEqual(_snapshot(unrelated), unrelated_before)

    def test_conda_foreign_child_entry_fails_closed_before_subprocess(self) -> None:
        base = self.scratch / "foreign-child-envs"
        self._conda_install(base)
        sentinel = base / "core" / "patient-sentinel.txt"
        sentinel.write_bytes(b"preserve exact foreign bytes")
        before = _snapshot(base)
        calls_before = len(self._log_lines())

        for force in (False, True):
            with (
                self.subTest(force=force),
                self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"),
            ):
                self._conda_install(base, force=force)
            self.assertEqual(_snapshot(base), before)
            self.assertEqual(len(self._log_lines()), calls_before)

    def test_managed_runtime_rejects_foreign_entry_during_and_after_use(self) -> None:
        base = self.scratch / "runtime-foreign-envs"
        self._conda_install(base)
        foreign = base / "core" / "var" / "cache" / "fontconfig" / "foreign-cache"

        with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
            with install_safety.managed_conda_runtime_lock(base, require_poetry=False):
                foreign.parent.mkdir(parents=True)
                foreign.write_bytes(b"preserve foreign runtime bytes")

        self.assertEqual(foreign.read_bytes(), b"preserve foreign runtime bytes")
        with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
            with install_safety.managed_conda_runtime_lock(base, require_poetry=False):
                self.fail("a dirty managed environment must fail before yielding")

        root_base = self.scratch / "runtime-foreign-root"
        self._conda_install(root_base)
        root_foreign = root_base / "foreign-runtime-entry"
        with install_safety.managed_conda_runtime_lock(root_base, require_poetry=False):
            root_foreign.write_bytes(b"preserve unrelated root-level bytes")
        self.assertEqual(
            root_foreign.read_bytes(), b"preserve unrelated root-level bytes"
        )
        with install_safety.managed_conda_runtime_lock(root_base, require_poetry=False):
            self.assertEqual(
                root_foreign.read_bytes(), b"preserve unrelated root-level bytes"
            )
        self.assertEqual(
            root_foreign.read_bytes(), b"preserve unrelated root-level bytes"
        )

    def test_managed_runtime_post_use_rejects_identity_replacement(self) -> None:
        child_base = self.scratch / "runtime-replaced-child"
        self._conda_install(child_base)
        child = child_base / "core"
        saved_child = self.scratch / "saved-runtime-core"
        with self.assertRaisesRegex(OncoTracerError, "child identity changed"):
            with install_safety.managed_conda_runtime_lock(
                child_base, require_poetry=False
            ):
                os.rename(child, saved_child)
                shutil.copytree(saved_child, child, symlinks=True)
        self.assertTrue(saved_child.is_dir())
        self.assertTrue(child.is_dir())

        marker_base = self.scratch / "runtime-replaced-marker"
        self._conda_install(marker_base)
        marker = marker_base / "core" / install_safety.ENV_MARKER
        saved_marker = self.scratch / "saved-runtime-marker.json"
        with self.assertRaisesRegex(OncoTracerError, "metadata identity changed"):
            with install_safety.managed_conda_runtime_lock(
                marker_base, require_poetry=False
            ):
                os.rename(marker, saved_marker)
                marker.write_bytes(saved_marker.read_bytes())
        self.assertTrue(saved_marker.is_file())
        self.assertTrue(marker.is_file())

    def test_managed_runtime_validates_optional_poetry_and_chains_body_error(
        self,
    ) -> None:
        poetry_base = self.scratch / "runtime-optional-poetry"
        self._conda_install(poetry_base, poetry=True)
        poetry_foreign = poetry_base / "poetry-runtime" / "foreign-runtime-file"
        with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
            with install_safety.managed_conda_runtime_lock(
                poetry_base, require_poetry=False
            ):
                poetry_foreign.write_bytes(b"preserve optional runtime bytes")
        self.assertEqual(
            poetry_foreign.read_bytes(), b"preserve optional runtime bytes"
        )

        body_base = self.scratch / "runtime-body-and-post-error"
        self._conda_install(body_base)
        body_foreign = body_base / "core" / "foreign-after-body-error"
        with self.assertRaises(OncoTracerError) as caught:
            with install_safety.managed_conda_runtime_lock(
                body_base, require_poetry=False
            ):
                body_foreign.write_bytes(b"preserve after body failure")
                raise RuntimeError("injected runtime body failure")
        self.assertIsInstance(caught.exception.__context__, RuntimeError)
        self.assertIn(
            "injected runtime body failure", str(caught.exception.__context__)
        )
        self.assertEqual(body_foreign.read_bytes(), b"preserve after body failure")

    def test_conda_child_source_must_match_the_owned_base(self) -> None:
        base = self.scratch / "split-source-envs"
        self._conda_install(base)
        marker_path = base / install_safety.BASE_MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["source"]["source_commit"] = "c" * 40
        marker_path.write_text(json.dumps(marker) + "\n", encoding="utf-8")
        before = _snapshot(base)
        calls_before = len(self._log_lines())

        with self.assertRaisesRegex(OncoTracerError, "ownership is invalid"):
            self._conda_install(base, force=True)
        self.assertEqual(_snapshot(base), before)
        self.assertEqual(len(self._log_lines()), calls_before)

    def test_conda_committed_cleanup_preserves_changed_backup(self) -> None:
        base = self.scratch / "changed-committed-backup-envs"
        self._conda_install(base)
        real_write = install_safety._write_journal
        sentinel: Path | None = None

        def inject_foreign_backup(path, value):
            nonlocal sentinel
            result = real_write(path, value)
            if value.get("phase") == "committed" and sentinel is None:
                transaction = Path(str(value["canonical_transaction"]))
                sentinel = transaction / "backups" / "core" / "patient-sentinel"
                sentinel.write_bytes(b"preserve changed backup bytes")
            return result

        with (
            mock.patch.object(
                install_safety,
                "_write_journal",
                side_effect=inject_foreign_backup,
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._conda_install(base, force=True)
        self.assertIsNotNone(sentinel)
        assert sentinel is not None
        self.assertEqual(sentinel.read_bytes(), b"preserve changed backup bytes")
        self.assertTrue(install_safety._journal_path(base, "conda").is_file())

    def test_conda_recovery_is_idempotent_after_backup_restore_crash(self) -> None:
        base = self.scratch / "rollback-reentry-envs"
        self._conda_install(base)
        before = _snapshot(base)
        real_rename = install_safety._rename_noreplace
        phase = "publish"

        def crash_after_backup_restore(source, destination, label):
            nonlocal phase
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                phase == "publish"
                and source_path.parent.name == "empty"
                and destination_path == base / "qdnaseq"
            ):
                phase = "rollback"
                raise OSError("trigger rollback")
            if (
                phase == "rollback"
                and source_path.parent.name == "backups"
                and destination_path == base / "qdnaseq"
            ):
                real_rename(source, destination, label)
                phase = "crashed"
                raise KeyboardInterrupt
            return real_rename(source, destination, label)

        with (
            mock.patch.object(
                install_safety,
                "_rename_noreplace",
                side_effect=crash_after_backup_restore,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._conda_install(base, force=True)
        self.assertEqual(phase, "crashed")
        journal_path = install_safety._journal_path(base, "conda")
        self.assertTrue(journal_path.is_file())

        # Recovery must accept qdnaseq as already restored, restore every
        # earlier child once, and leave the exact prior owned tree in place.
        self._conda_install(base)
        self.assertEqual(_snapshot(base), before)
        self.assertFalse(journal_path.exists())

    def test_transaction_and_operational_subdirs_must_be_physical(self) -> None:
        external = self.scratch / "external-transaction-sentinel"
        external.mkdir()
        (external / "sentinel").write_bytes(b"never touch")
        external_before = _snapshot(external)

        target = self.scratch / "transaction-target"
        transaction_id, transaction = install_safety._new_transaction(target, "conda")
        saved = self.scratch / "saved-owned-transaction"
        transaction.rename(saved)
        transaction.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, "physical directory"):
            install_safety._require_transaction(
                transaction, target, transaction_id, "conda"
            )
        self.assertEqual(_snapshot(external), external_before)
        transaction = self.scratch / transaction.absolute().relative_to(
            self.scratch.absolute()
        )
        transaction.unlink()
        saved.rename(transaction)

        for name in ("backups", "discarded"):
            with self.subTest(name=name):
                path = transaction / name
                path.symlink_to(external, target_is_directory=True)
                with self.assertRaisesRegex(OncoTracerError, "physical directory"):
                    install_safety._require_transaction_subdir(transaction, name)
                self.assertEqual(_snapshot(external), external_before)
                path = self.scratch / path.absolute().relative_to(
                    self.scratch.absolute()
                )
                path.unlink()

        install_safety._preserve_staging_transaction(
            transaction, target, transaction_id, "conda"
        )

    def test_changed_unsealed_target_metadata_is_never_discarded(self) -> None:
        target = self.scratch / "partial-final-prefix"
        target.mkdir()
        (target / "payload").write_bytes(b"installer bytes")
        transaction_id, transaction = install_safety._new_transaction(target, "conda")
        install_safety._require_transaction_subdir(transaction, "claims", create=True)
        discarded = install_safety._require_transaction_subdir(
            transaction, "discarded", create=True
        )

        (target / install_safety.ENV_MARKER).write_bytes(b"initial metadata")
        install_safety._write_target_claim(
            transaction,
            transaction_id,
            "core",
            target,
        )
        claim = json.loads(
            install_safety._target_claim_path(transaction, "core").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(claim["state"], "unsealed")
        self.assertNotIn("partial_inventory", claim)
        (target / install_safety.ENV_MARKER).write_bytes(
            b"foreign metadata must survive"
        )
        before = _snapshot(target)
        install_safety._discard_claimed_target(
            transaction,
            transaction_id,
            "core",
            target,
            discarded,
            {},
        )
        preserved = target.parent.parent / (
            f".{target.parent.name}-{target.name}.oncotracer-preserved-"
            f"{transaction_id}-core"
        )
        self.assertFalse(target.exists())
        self.assertEqual(_snapshot(preserved), before)
        install_safety._preserve_staging_transaction(
            transaction, target, transaction_id, "conda"
        )

    def test_real_size_partial_conda_prefix_keeps_claim_bounded(self) -> None:
        base = self.scratch / "large-partial-envs"
        entry_count = 12_000

        def fail_after_real_size_create(destination, name):
            self.assertEqual(name, "core")
            records = destination / "conda-meta" / "package-records"
            records.mkdir()
            for index in range(entry_count):
                (records / f"package-{index:05d}.json").write_bytes(b"{}\n")
            raise OncoTracerError("injected post-create validation failure")

        with (
            mock.patch.object(
                install_safety,
                "_verify_conda_runtime",
                side_effect=fail_after_real_size_create,
            ),
            self.assertRaisesRegex(
                OncoTracerError, "injected post-create validation failure"
            ),
        ):
            self._conda_install(base)

        preserved = list(
            self.scratch.glob(f".{base.name}-core.oncotracer-preserved-*-core")
        )
        self.assertEqual(len(preserved), 1)
        records = preserved[0] / "conda-meta" / "package-records"
        self.assertEqual(sum(1 for _ in records.iterdir()), entry_count)
        self.assertEqual((records / "package-00000.json").read_bytes(), b"{}\n")
        self.assertEqual((records / "package-11999.json").read_bytes(), b"{}\n")

        # This is larger than the reader bound that exposed the parity failure
        # when the full inventory was embedded in claims/core.json.
        legacy_inventory = install_safety._tree_inventory(
            preserved[0], include_installer_metadata=True
        )
        legacy_payload = json.dumps(
            {"partial_inventory": legacy_inventory}, indent=2, sort_keys=True
        ).encode("utf-8")
        self.assertGreater(len(legacy_payload), 1024 * 1024)

        retained = list(
            self.scratch.glob(
                f".{base.name}.oncotracer-conda-txn-*.oncotracer-retained"
            )
        )
        self.assertEqual(len(retained), 1)
        claim_path = retained[0] / "claims" / "core.json"
        self.assertLessEqual(
            claim_path.stat().st_size,
            install_safety.MAX_TARGET_CLAIM_JSON_BYTES,
        )
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        self.assertEqual(claim["schema"], install_safety.TARGET_CLAIM_SCHEMA)
        self.assertEqual(claim["state"], "unsealed")
        self.assertIsNone(claim["marker"])
        self.assertNotIn("partial_inventory", claim)

    def test_target_claim_rejects_oversize_and_embedded_inventory(self) -> None:
        for attack in ("oversize", "embedded-inventory"):
            with self.subTest(attack=attack):
                target = self.scratch / f"claim-attack-{attack}"
                target.mkdir()
                (target / "patient-sentinel").write_bytes(b"PRESERVE")
                transaction_id, transaction = install_safety._new_transaction(
                    target, "conda"
                )
                install_safety._require_transaction_subdir(
                    transaction, "claims", create=True
                )
                discarded = install_safety._require_transaction_subdir(
                    transaction, "discarded", create=True
                )
                install_safety._write_target_claim(
                    transaction, transaction_id, "core", target
                )
                claim_path = install_safety._target_claim_path(transaction, "core")
                if attack == "oversize":
                    claim_path.write_bytes(
                        b"{" + b" " * install_safety.MAX_TARGET_CLAIM_JSON_BYTES
                    )
                    expected = "unexpectedly large"
                else:
                    claim = json.loads(claim_path.read_text(encoding="utf-8"))
                    claim["partial_inventory"] = {
                        "schema": install_safety.INVENTORY_SCHEMA,
                        "entries": [],
                    }
                    claim_path.write_text(json.dumps(claim) + "\n", encoding="utf-8")
                    expected = "claim is malformed"

                before = _snapshot(target)
                with self.assertRaisesRegex(OncoTracerError, expected):
                    install_safety._discard_claimed_target(
                        transaction,
                        transaction_id,
                        "core",
                        target,
                        discarded,
                        {},
                    )
                self.assertEqual(_snapshot(target), before)
                self.assertFalse(any(discarded.iterdir()))

    def test_atomic_json_writer_cap_precedes_publication(self) -> None:
        history = self.scratch / "writer-history"
        history.mkdir()
        existing = self.scratch / "existing-bounded.json"
        existing.write_bytes(b"prior bytes\n")
        before = (existing.read_bytes(), existing.lstat().st_ino)
        with self.assertRaisesRegex(OncoTracerError, "unexpectedly large"):
            install_safety._atomic_write_json(
                existing,
                {"payload": "x" * 128},
                retention_parent=history,
                max_bytes=32,
            )
        self.assertEqual((existing.read_bytes(), existing.lstat().st_ino), before)
        self.assertFalse(any(history.iterdir()))

        absent = self.scratch / "absent-bounded.json"
        with self.assertRaisesRegex(OncoTracerError, "unexpectedly large"):
            install_safety._atomic_write_json(
                absent,
                {"payload": "x" * 128},
                retention_parent=history,
                max_bytes=32,
            )
        self.assertFalse(os.path.lexists(absent))
        self.assertFalse(any(history.iterdir()))

    def test_target_claim_strict_schema_and_state_types_fail_closed(self) -> None:
        attacks = {
            "v1-schema": {"schema": "oncotracer-install-target-claim-v1"},
            "sealed-null": {"state": "sealed", "marker": None},
            "unsealed-marker": {"state": "unsealed", "marker": {}},
            "device-bool": {"device": True},
            "device-negative": {"device": -1},
            "inode-zero": {"inode": 0},
        }
        for attack, mutation in attacks.items():
            with self.subTest(attack=attack):
                target = self.scratch / f"strict-claim-{attack}"
                target.mkdir()
                (target / "patient-sentinel").write_bytes(b"PRESERVE")
                transaction_id, transaction = install_safety._new_transaction(
                    target, "conda"
                )
                install_safety._require_transaction_subdir(
                    transaction, "claims", create=True
                )
                discarded = install_safety._require_transaction_subdir(
                    transaction, "discarded", create=True
                )
                install_safety._write_target_claim(
                    transaction, transaction_id, "core", target
                )
                claim_path = install_safety._target_claim_path(transaction, "core")
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
                claim.update(mutation)
                claim_path.write_text(json.dumps(claim) + "\n", encoding="utf-8")
                before = _snapshot(target)
                with self.assertRaisesRegex(OncoTracerError, "claim is malformed"):
                    install_safety._discard_claimed_target(
                        transaction, transaction_id, "core", target, discarded, {}
                    )
                self.assertEqual(_snapshot(target), before)
                self.assertFalse(any(discarded.iterdir()))

    def test_conda_semantic_probes_accept_internal_symlinks_and_hardlinks(
        self,
    ) -> None:
        for name in install_safety.CONDA_NAMES:
            with self.subTest(name=name):
                destination = self.scratch / f"probe-{name}"
                binaries = destination / "bin"
                binaries.mkdir(parents=True)
                if name in {"core", "classifier"}:
                    final = _write_executable(
                        binaries / "python3.11",
                        "#!/bin/sh\necho CORE_OK CLASSIFIER_OK\n",
                    )
                    os.link(final, destination / "conda-package-cache-python")
                    self.assertGreater(final.stat().st_nlink, 1)
                    (binaries / "python3").symlink_to("python3.11")
                    (binaries / "python").symlink_to("python3")
                elif name in {"qdnaseq", "ichorcna"}:
                    _write_executable(
                        binaries / "R",
                        "#!/bin/sh\necho QDNASEQ_OK ICHORCNA_OK\n",
                    )
                    (binaries / "Rscript").symlink_to("R")
                else:
                    libraries = [
                        destination
                        / "share"
                        / "mcr-8.3-0"
                        / "v83"
                        / relative
                        / "glnxa64"
                        for relative in (Path("runtime"), Path("bin"), Path("sys/os"))
                    ]
                    for library in libraries:
                        library.mkdir(parents=True)
                    _write_executable(
                        binaries / "gistic-help",
                        "#!/bin/sh\necho 'Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file'\n",
                    )
                    expected = os.pathsep.join(str(path) for path in libraries)
                    _write_executable(
                        binaries / "gistic2.bin",
                        "#!/bin/sh\n"
                        f"test \"$LD_LIBRARY_PATH\" = '{expected}' || exit 88\n"
                        "test \"${LD_LIBRARY_PATH_MCR+x}:$LD_LIBRARY_PATH_MCR\" = 'x:' || exit 89\n"
                        '"$(dirname "$0")/gistic-help"\n',
                    )
                    (binaries / "gistic2").symlink_to("gistic2.bin")
                install_safety._verify_conda_runtime(destination, name)

    def test_named_physical_python_preserves_real_prefix_discovery(self) -> None:
        destination = self.scratch / "real-prefix-probe"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(destination)
        logical = destination / "bin" / "python"
        descriptor, resolved = install_safety._open_managed_probe(
            destination, logical, "real managed Python probe"
        )
        try:
            result = install_safety._run_checked(
                [
                    resolved,
                    "-I",
                    "-B",
                    "-c",
                    (
                        "import pathlib,sys; "
                        f"assert pathlib.Path(sys.prefix).resolve() == pathlib.Path({str(destination)!r}); "
                        f"assert pathlib.Path(sys.executable).resolve() == pathlib.Path({str(resolved)!r}); "
                        "print('REAL_PREFIX_OK')"
                    ),
                ],
                env=install_safety._managed_conda_probe_environment(
                    destination, "core"
                ),
            )
        finally:
            os.close(descriptor)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.stdout.strip(), "REAL_PREFIX_OK")

    def test_semantic_probe_executable_swap_fails_postcheck(self) -> None:
        destination = self.scratch / "probe-swap"
        binaries = destination / "bin"
        binaries.mkdir(parents=True)
        final = _write_executable(binaries / "python3.11", "#!/bin/sh\necho CORE_OK\n")
        (binaries / "python").symlink_to("python3.11")

        def swap_after_apparent_success(*_args, **_kwargs):
            final.rename(binaries / "original-python3.11")
            _write_executable(final, "#!/bin/sh\necho CORE_OK\n")

        with (
            mock.patch.object(
                install_safety,
                "_run_semantic_probe",
                side_effect=swap_after_apparent_success,
            ),
            self.assertRaisesRegex(OncoTracerError, "changed while.*probe ran"),
        ):
            install_safety._verify_conda_runtime(destination, "core")

    def test_gistic_mcr_directory_swap_fails_postcheck(self) -> None:
        destination = self.scratch / "gistic-mcr-swap"
        binaries = destination / "bin"
        binaries.mkdir(parents=True)
        _write_executable(
            binaries / "gistic2",
            "#!/bin/sh\necho 'Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file'\n",
        )
        root = destination / "share" / "mcr-8.3-0" / "v83"
        libraries = [
            root / "runtime" / "glnxa64",
            root / "bin" / "glnxa64",
            root / "sys" / "os" / "glnxa64",
        ]
        for library in libraries:
            library.mkdir(parents=True)
        victim = libraries[0]

        def swap_after_apparent_success(*_args, **_kwargs):
            victim.rename(victim.with_name("original-glnxa64"))
            victim.mkdir()

        with (
            mock.patch.object(
                install_safety,
                "_run_semantic_probe",
                side_effect=swap_after_apparent_success,
            ),
            self.assertRaisesRegex(OncoTracerError, "MATLAB runtime changed"),
        ):
            install_safety._verify_conda_runtime(destination, "gistic")

    def test_conda_semantic_probe_symlinks_fail_closed(self) -> None:
        external = self.scratch / "external-probe"
        _write_executable(external, "#!/bin/sh\necho EXTERNAL\n")
        external_before = external.read_bytes()

        def prepare(attack):
            destination = self.scratch / f"probe-attack-{attack}"
            binaries = destination / "bin"
            binaries.mkdir(parents=True)
            probe = binaries / "python"
            if attack == "absolute":
                probe.symlink_to(external)
            elif attack == "parent":
                outside = self.scratch / "outside-runtime"
                outside.mkdir()
                _write_executable(outside / "python", "#!/bin/sh\necho EXTERNAL\n")
                runtimes = binaries / "runtimes"
                runtimes.mkdir()
                (runtimes / "current").symlink_to(outside, target_is_directory=True)
                probe.symlink_to("runtimes/current/python")
            elif attack == "dotdot":
                probe.symlink_to("../outside-python")
            elif attack == "loop":
                probe.symlink_to("python3")
                (binaries / "python3").symlink_to("python")
            elif attack == "special":
                os.mkfifo(probe)
                probe.chmod(0o755)
            elif attack == "hardlinked-symlink":
                _write_executable(binaries / "python3.11", "#!/bin/sh\necho CORE_OK\n")
                probe.symlink_to("python3.11")
                os.link(
                    probe,
                    binaries / "python-copy",
                    follow_symlinks=False,
                )
                self.assertGreater(probe.lstat().st_nlink, 1)
            else:
                raise AssertionError(attack)
            return destination, probe

        for attack in (
            "absolute",
            "parent",
            "dotdot",
            "loop",
            "special",
            "hardlinked-symlink",
        ):
            with self.subTest(attack=attack):
                destination, probe = prepare(attack)
                with self.assertRaises(OncoTracerError):
                    install_safety._open_managed_probe(
                        destination, probe, "test managed probe"
                    )
                self.assertEqual(external.read_bytes(), external_before)

    def test_sealed_conda_target_rolls_back_once_and_preserves_original_error(
        self,
    ) -> None:
        base = self.scratch / "sealed-rollback-envs"
        original = OncoTracerError("injected qdnaseq semantic failure")

        def fail_second(_destination, name):
            if name == "qdnaseq":
                raise original

        with (
            mock.patch.object(
                install_safety,
                "_verify_conda_runtime",
                side_effect=fail_second,
            ),
            self.assertRaises(OncoTracerError) as raised,
        ):
            self._conda_install(base)

        self.assertIs(raised.exception, original)
        self.assertEqual(len(self._log_lines()), 2)
        preserved = list(
            self.scratch.glob(f".{base.name}-qdnaseq.oncotracer-preserved-*-qdnaseq")
        )
        self.assertEqual(len(preserved), 1)
        self.assertTrue((preserved[0] / "conda-meta" / "history").is_file())
        retained = list(
            self.scratch.glob(
                f".{base.name}.oncotracer-conda-txn-*.oncotracer-retained"
            )
        )
        self.assertEqual(len(retained), 1)
        discarded_core = retained[0] / "discarded" / "core"
        self.assertTrue((discarded_core / "conda-meta" / "history").is_file())
        core_claim = json.loads(
            (retained[0] / "claims" / "core.json").read_text(encoding="utf-8")
        )
        qdnaseq_claim = json.loads(
            (retained[0] / "claims" / "qdnaseq.json").read_text(encoding="utf-8")
        )
        self.assertEqual(core_claim["state"], "sealed")
        self.assertIsNotNone(core_claim["marker"])
        self.assertEqual(qdnaseq_claim["state"], "unsealed")
        self.assertIsNone(qdnaseq_claim["marker"])

    def test_empty_conda_root_mode_survives_failed_first_install(self) -> None:
        base = self.scratch / "preexisting-empty-envs"
        base.mkdir(mode=0o711)
        before_mode = stat.S_IMODE(base.lstat().st_mode)
        real_rename = install_safety._rename_noreplace
        fired = False

        def fail_first_child(source, destination, label):
            nonlocal fired
            if (
                not fired
                and Path(source).parent.name == "empty"
                and Path(destination) == base / "core"
            ):
                fired = True
                raise OSError("injected first-prefix publication failure")
            return real_rename(source, destination, label)

        with (
            mock.patch.object(
                install_safety, "_rename_noreplace", side_effect=fail_first_child
            ),
            self.assertRaisesRegex(OSError, "injected first-prefix"),
        ):
            self._conda_install(base)
        self.assertTrue(fired)
        self.assertTrue(base.is_dir())
        self.assertEqual(stat.S_IMODE(base.lstat().st_mode), before_mode)
        self.assertEqual(list(base.iterdir()), [])
        self.assertFalse(install_safety._journal_path(base, "conda").exists())

    def test_conda_active_use_refuses_before_staging(self) -> None:
        base = self.scratch / "active-envs"
        self._conda_install(base)
        calls_before = len(self._log_lines())
        with mock.patch.object(
            install_safety, "_active_processes", return_value=[4321]
        ):
            with self.assertRaisesRegex(OncoTracerError, "active process"):
                self._conda_install(base, force=True)
        self.assertEqual(len(self._log_lines()), calls_before)

        empty = self.scratch / "active-empty-envs"
        empty.mkdir()
        empty_before = _snapshot(empty)
        with mock.patch.object(
            install_safety, "_active_processes", return_value=[4322]
        ):
            with self.assertRaisesRegex(OncoTracerError, "active process"):
                self._conda_install(empty)
        self.assertEqual(_snapshot(empty), empty_before)
        self.assertEqual(len(self._log_lines()), calls_before)

    def test_conda_publication_oserror_and_interrupt_restore_exact_tree(self) -> None:
        base = self.scratch / "rollback-envs"
        self._conda_install(base)
        before = _snapshot(base)
        real_rename = install_safety._rename_noreplace
        for failure in (OSError("injected replace error"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                fired = False

                def fail_once(source, destination, label):
                    nonlocal fired
                    source_path = Path(source)
                    destination_path = Path(destination)
                    if (
                        not fired
                        and destination_path == base / "qdnaseq"
                        and "empty" in source_path.parts
                    ):
                        fired = True
                        raise failure
                    return real_rename(source, destination, label)

                with mock.patch.object(
                    install_safety, "_rename_noreplace", side_effect=fail_once
                ):
                    with self.assertRaises(type(failure)):
                        self._conda_install(base, force=True)
                self.assertTrue(fired)
                self.assertEqual(_snapshot(base), before)
                self.assertFalse(install_safety._journal_path(base, "conda").exists())

    def test_conda_committed_journal_write_failure_rolls_back(self) -> None:
        base = self.scratch / "commit-journal-envs"
        self._conda_install(base)
        before = _snapshot(base)
        real_write = install_safety._write_journal

        def fail_committed(path, value):
            if value.get("phase") == "committed":
                raise OSError("injected committed-journal failure")
            return real_write(path, value)

        with (
            mock.patch.object(
                install_safety, "_write_journal", side_effect=fail_committed
            ),
            self.assertRaises(OSError),
        ):
            self._conda_install(base, force=True)
        self.assertEqual(_snapshot(base), before)
        self.assertFalse(install_safety._journal_path(base, "conda").exists())

    def test_conda_retained_root_then_journal_recovery_is_idempotent(self) -> None:
        base = self.scratch / "retained-recovery-envs"
        self._conda_install(base)
        journal_path = install_safety._journal_path(base, "conda")

        def interrupt_journal_retention(path, journal, kind):
            self.assertEqual((path, kind), (journal_path, "conda"))
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                install_safety,
                "_retain_transaction_journal",
                side_effect=interrupt_journal_retention,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._conda_install(base, force=True)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "committed")
        self.assertFalse(os.path.lexists(journal["canonical_transaction"]))
        retained = install_safety._committed_retained_path(
            Path(str(journal["canonical_transaction"]))
        )
        self.assertTrue(retained.is_dir())
        retained_before = _snapshot(retained)
        self.assertEqual(install_safety._classify_base(base)[0], "owned")

        calls_before_recovery = len(self._log_lines())
        self._conda_install(base)
        self.assertEqual(len(self._log_lines()), calls_before_recovery)
        self.assertFalse(journal_path.exists())
        self.assertEqual(_snapshot(retained), retained_before)
        audit = install_safety._retained_journal_path(
            journal_path, str(journal["transaction_id"]), "conda"
        )
        self.assertTrue(audit.is_file())
        self._conda_install(base, force=True)

    def test_conda_dry_run_has_zero_writes_and_no_subprocess(self) -> None:
        base = self.scratch / "dry-run-envs"
        before = _snapshot(self.scratch)
        with (
            mock.patch.object(
                install_safety,
                "_source_identity",
                side_effect=AssertionError("source queried"),
            ),
            mock.patch.object(
                install_safety.subprocess,
                "run",
                side_effect=AssertionError("subprocess ran"),
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self._conda_install(base, force=True, dry_run=True)
        self.assertEqual(_snapshot(self.scratch), before)
        self.assertFalse(base.exists())

    def test_poetry_runtime_is_isolated_owned_and_unowned_child_is_preserved(
        self,
    ) -> None:
        base = self.scratch / "poetry-envs"
        source_venv_before = os.path.lexists(ROOT / ".venv")
        self._conda_install(base, poetry=True)
        runtime = base / "poetry-runtime"
        pip = runtime / "bin" / "pip"
        shebang = pip.read_text(encoding="utf-8").splitlines()[0]
        self.assertRegex(
            shebang,
            rf"^#!{re.escape(str(runtime / 'bin' / 'python'))}(?:3(?:\.(?:10|11|12|13))?)?$",
        )
        executed = subprocess.run([pip, "--version"], text=True, capture_output=True)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertIn(str(runtime), executed.stdout)
        self.assertTrue((runtime / "bin" / "python").is_file())
        self.assertTrue(os.access(runtime / "bin" / "oncotracer", os.X_OK))
        marker = json.loads(
            (runtime / install_safety.POETRY_MARKER).read_text(encoding="utf-8")
        )
        self.assertEqual(marker["canonical_path"], str(runtime))
        self.assertEqual(os.path.lexists(ROOT / ".venv"), source_venv_before)

        second = self.scratch / "poetry-unowned-child"
        self._conda_install(second)
        unowned = second / "poetry-runtime"
        unowned.mkdir()
        (unowned / "patient-sentinel").write_bytes(b"preserve")
        before = _snapshot(unowned)
        calls_before = len(self._log_lines())
        with self.assertRaisesRegex(OncoTracerError, "unowned managed child"):
            self._conda_install(second, poetry=True, force=True)
        self.assertEqual(_snapshot(unowned), before)
        self.assertEqual(len(self._log_lines()), calls_before)

        launcher = runtime / "bin" / "oncotracer"
        launcher.write_bytes(b"#!/bin/sh\nexit 99\n")
        launcher.chmod(0o755)
        corrupt_before = _snapshot(runtime)
        calls_before = len(self._log_lines())
        with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
            self._conda_install(base, poetry=True, force=True)
        self.assertEqual(_snapshot(runtime), corrupt_before)
        self.assertEqual(len(self._log_lines()), calls_before)

    def test_conda_does_not_strand_an_owned_poetry_runtime_on_source_change(
        self,
    ) -> None:
        base = self.scratch / "poetry-source-upgrade"
        self._conda_install(base, poetry=True)
        before = _snapshot(base)
        calls_before = len(self._log_lines())
        upgraded_source = {
            **SOURCE,
            "source_commit": "d" * 40,
            "source_sha256": "e" * 64,
        }

        with (
            mock.patch.object(
                install_safety, "_source_identity", return_value=upgraded_source
            ),
            self.assertRaisesRegex(OncoTracerError, "update it with install --poetry"),
        ):
            self._conda_install(base, force=True)
        self.assertEqual(_snapshot(base), before)
        self.assertEqual(len(self._log_lines()), calls_before)

    def test_sif_unowned_symlink_hardlink_and_malformed_sidecar_are_preserved(
        self,
    ) -> None:
        unowned = self.scratch / "unowned.sif"
        unowned.write_bytes(b"patient sentinel")
        before = unowned.read_bytes()
        for force in (False, True):
            with self.assertRaisesRegex(OncoTracerError, "incomplete or unowned"):
                self._sif_install(unowned, force=force)
            self.assertEqual(unowned.read_bytes(), before)

        target = self.scratch / "symlink-target.sif"
        target.write_bytes(b"external")
        linked = self.scratch / "linked.sif"
        linked.symlink_to(target)
        install_safety._sif_sidecar(linked).write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(OncoTracerError, "symlink|regular file"):
            self._sif_install(linked, force=True)
        self.assertEqual(target.read_bytes(), b"external")

        first = self.scratch / "hardlink-source.sif"
        second = self.scratch / "hardlinked.sif"
        first.write_bytes(b"hardlink")
        os.link(first, second)
        install_safety._sif_sidecar(second).write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(OncoTracerError, "non-hardlinked"):
            self._sif_install(second, force=True)
        self.assertEqual(first.read_bytes(), b"hardlink")

        malformed = self.scratch / "malformed.sif"
        malformed.write_bytes(b"malformed")
        malformed_sidecar = install_safety._sif_sidecar(malformed)
        malformed_sidecar.write_bytes(b"foreign sidecar sentinel")
        malformed_before = (malformed.read_bytes(), malformed_sidecar.read_bytes())
        with self.assertRaisesRegex(OncoTracerError, "could not verify|malformed"):
            self._sif_install(malformed, force=True)
        self.assertEqual(
            (malformed.read_bytes(), malformed_sidecar.read_bytes()), malformed_before
        )
        self.assertEqual(self._log_lines(), [])

    def test_sif_fresh_verified_reuse_and_force_are_owned(self) -> None:
        destination = self.scratch / "managed.sif"
        self._sif_install(destination)
        first_marker = json.loads(
            install_safety._sif_sidecar(destination).read_text(encoding="utf-8")
        )
        self.assertEqual(destination.read_bytes(), b"sif-one")
        self.assertEqual(len(self._log_lines()), 3)

        self._sif_install(destination)
        self.assertEqual(len(self._log_lines()), 5)
        with mock.patch.dict(os.environ, {"FAKE_SIF_CONTENT": "sif-two"}, clear=False):
            self._sif_install(destination, force=True)
        second_marker = json.loads(
            install_safety._sif_sidecar(destination).read_text(encoding="utf-8")
        )
        self.assertEqual(destination.read_bytes(), b"sif-two")
        self.assertEqual(second_marker["install_id"], first_marker["install_id"])
        self.assertNotEqual(second_marker["sif_sha256"], first_marker["sif_sha256"])
        self.assertFalse(install_safety._journal_path(destination, "sif").exists())

    def test_sif_candidate_failure_and_active_use_preserve_owned_pair(self) -> None:
        destination = self.scratch / "safe.sif"
        self._sif_install(destination)
        before = (
            destination.read_bytes(),
            install_safety._sif_sidecar(destination).read_bytes(),
        )
        calls_before = len(self._log_lines())
        with (
            mock.patch.object(install_safety, "_active_processes", return_value=[7654]),
            self.assertRaisesRegex(OncoTracerError, "active process"),
        ):
            self._sif_install(destination, force=True)
        self.assertEqual(len(self._log_lines()), calls_before)

        sidecar = install_safety._sif_sidecar(destination)
        with mock.patch.object(
            install_safety,
            "_active_processes",
            side_effect=lambda path: [7655] if path == sidecar else [],
        ):
            with self.assertRaisesRegex(OncoTracerError, "active process"):
                self._sif_install(destination, force=True)
        self.assertEqual(len(self._log_lines()), calls_before)

        with mock.patch.dict(os.environ, {"FAKE_DOCTOR_FAIL": "1"}, clear=False):
            with self.assertRaisesRegex(
                OncoTracerError, "failed the native host doctor"
            ):
                self._sif_install(destination, force=True)
        self.assertEqual(
            (
                destination.read_bytes(),
                install_safety._sif_sidecar(destination).read_bytes(),
            ),
            before,
        )
        self.assertFalse(install_safety._journal_path(destination, "sif").exists())

    def test_sif_partial_publication_failures_restore_old_pair(self) -> None:
        destination = self.scratch / "rollback.sif"
        sidecar = install_safety._sif_sidecar(destination)
        self._sif_install(destination)
        before = (destination.read_bytes(), sidecar.read_bytes())
        real_rename = install_safety._rename_noreplace
        failure_points = ("old-sidecar-backup", "new-sidecar-publish")
        for point in failure_points:
            with (
                self.subTest(point=point),
                mock.patch.dict(
                    os.environ, {"FAKE_SIF_CONTENT": f"new-{point}"}, clear=False
                ),
            ):
                fired = False

                def fail_once(source, target, label):
                    nonlocal fired
                    source_path = Path(source)
                    target_path = Path(target)
                    old_sidecar_backup = (
                        source_path == sidecar
                        and target_path.name == "backup.sidecar.json"
                    )
                    new_sidecar_publish = (
                        source_path.name == "candidate.sidecar.json"
                        and target_path == sidecar
                    )
                    selected = (
                        point == "old-sidecar-backup" and old_sidecar_backup
                    ) or (point == "new-sidecar-publish" and new_sidecar_publish)
                    if not fired and selected:
                        fired = True
                        if point == "old-sidecar-backup":
                            raise OSError("injected partial backup failure")
                        raise KeyboardInterrupt
                    return real_rename(source, target, label)

                with mock.patch.object(
                    install_safety, "_rename_noreplace", side_effect=fail_once
                ):
                    with self.assertRaises(
                        OSError if point == "old-sidecar-backup" else KeyboardInterrupt
                    ):
                        self._sif_install(destination, force=True)
                self.assertTrue(fired)
                self.assertEqual(
                    (destination.read_bytes(), sidecar.read_bytes()), before
                )
                self.assertFalse(
                    install_safety._journal_path(destination, "sif").exists()
                )

    def test_sif_next_invocation_recovers_crashed_partial_pair(self) -> None:
        destination = self.scratch / "crash-recovery.sif"
        sidecar = install_safety._sif_sidecar(destination)
        self._sif_install(destination)
        before = (destination.read_bytes(), sidecar.read_bytes())
        real_rename = install_safety._rename_noreplace
        failed = False

        def crash_between_backup_renames(source, target, label):
            nonlocal failed
            if not failed and Path(source) == sidecar:
                failed = True
                raise KeyboardInterrupt
            return real_rename(source, target, label)

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "candidate-after-crash"}, clear=False
            ),
            mock.patch.object(
                install_safety,
                "_rename_noreplace",
                side_effect=crash_between_backup_renames,
            ),
            mock.patch.object(
                install_safety,
                "_restore_sif_transaction",
                side_effect=OSError("simulated process loss before rollback"),
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._sif_install(destination, force=True)
        self.assertTrue(failed)
        self.assertFalse(destination.exists())
        self.assertTrue(sidecar.exists())
        journal_path = install_safety._journal_path(destination, "sif")
        self.assertTrue(journal_path.exists())

        # Preflight must recognize the strict journal before classifying the
        # intentionally incomplete pair, then recover under the managed lock.
        self._sif_install(destination)
        self.assertEqual((destination.read_bytes(), sidecar.read_bytes()), before)
        self.assertFalse(journal_path.exists())

    def test_sif_committed_journal_write_failure_rolls_back(self) -> None:
        destination = self.scratch / "commit-journal.sif"
        sidecar = install_safety._sif_sidecar(destination)
        self._sif_install(destination)
        before = (destination.read_bytes(), sidecar.read_bytes())
        real_write = install_safety._write_journal

        def fail_committed(path, value):
            if value.get("phase") == "committed":
                raise OSError("injected committed-journal failure")
            return real_write(path, value)

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "replacement"}, clear=False
            ),
            mock.patch.object(
                install_safety, "_write_journal", side_effect=fail_committed
            ),
            self.assertRaises(OSError),
        ):
            self._sif_install(destination, force=True)
        self.assertEqual((destination.read_bytes(), sidecar.read_bytes()), before)
        self.assertFalse(install_safety._journal_path(destination, "sif").exists())

    def test_sif_retained_root_then_journal_recovery_is_idempotent(self) -> None:
        destination = self.scratch / "retained-recovery.sif"
        self._sif_install(destination)
        journal_path = install_safety._journal_path(destination, "sif")

        def interrupt_journal_retention(path, journal, kind):
            self.assertEqual((path, kind), (journal_path, "sif"))
            raise KeyboardInterrupt

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "committed-new"}, clear=False
            ),
            mock.patch.object(
                install_safety,
                "_retain_transaction_journal",
                side_effect=interrupt_journal_retention,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._sif_install(destination, force=True)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "committed")
        self.assertFalse(os.path.lexists(journal["canonical_transaction"]))
        retained = install_safety._committed_retained_path(
            Path(str(journal["canonical_transaction"]))
        )
        retained_before = _snapshot(retained)
        self.assertEqual(destination.read_bytes(), b"committed-new")

        calls_before_recovery = len(self._log_lines())
        self._sif_install(destination)
        self.assertEqual(len(self._log_lines()), calls_before_recovery + 2)
        self.assertFalse(journal_path.exists())
        self.assertEqual(_snapshot(retained), retained_before)
        self.assertEqual(destination.read_bytes(), b"committed-new")
        audit = install_safety._retained_journal_path(
            journal_path, str(journal["transaction_id"]), "sif"
        )
        self.assertTrue(audit.is_file())
        with mock.patch.dict(
            os.environ, {"FAKE_SIF_CONTENT": "future-install"}, clear=False
        ):
            self._sif_install(destination, force=True)

    def test_sif_committed_cleanup_preserves_unexpected_transaction_entry(
        self,
    ) -> None:
        destination = self.scratch / "changed-committed-backup.sif"
        self._sif_install(destination)
        real_write = install_safety._write_journal
        sentinel: Path | None = None

        def inject_foreign_entry(path, value):
            nonlocal sentinel
            result = real_write(path, value)
            if value.get("phase") == "committed" and sentinel is None:
                transaction = Path(str(value["canonical_transaction"]))
                sentinel = transaction / "patient-sentinel"
                sentinel.write_bytes(b"preserve unexpected transaction bytes")
            return result

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "safe-new-sif"}, clear=False
            ),
            mock.patch.object(
                install_safety,
                "_write_journal",
                side_effect=inject_foreign_entry,
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._sif_install(destination, force=True)
        self.assertIsNotNone(sentinel)
        assert sentinel is not None
        self.assertEqual(
            sentinel.read_bytes(), b"preserve unexpected transaction bytes"
        )
        self.assertTrue(install_safety._journal_path(destination, "sif").is_file())

    def test_sif_dry_run_has_zero_writes_and_no_subprocess(self) -> None:
        destination = self.scratch / "dry-run.sif"
        before = _snapshot(self.scratch)
        with (
            mock.patch.object(
                install_safety,
                "_source_identity",
                side_effect=AssertionError("source queried"),
            ),
            mock.patch.object(
                install_safety.subprocess,
                "run",
                side_effect=AssertionError("subprocess ran"),
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self._sif_install(destination, force=True, dry_run=True)
        self.assertEqual(_snapshot(self.scratch), before)
        self.assertFalse(destination.exists())

    def test_backend_irrelevant_install_flags_fail_before_dispatch(self) -> None:
        cases = (
            ["install", "--docker", "--prefix", "/dedicated/prefix"],
            ["install", "--docker", "--force"],
            ["install", "--conda", "--image", "example:test"],
            ["install", "--conda", "--sif", "/dedicated/image.sif"],
            ["install", "--singularity", "--prefix", "/dedicated/prefix"],
            ["install", "--singularity", "--root", str(ROOT)],
            ["install", "--poetry", "--image", "example:test"],
        )
        with (
            mock.patch.object(cli, "_install_conda") as conda,
            mock.patch.object(cli, "_install_docker") as docker,
            mock.patch.object(cli, "_install_singularity") as singularity,
            mock.patch.object(cli, "_install_poetry") as poetry,
        ):
            for arguments in cases:
                with (
                    self.subTest(arguments=arguments),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(cli.main(arguments), 2)
            conda.assert_not_called()
            docker.assert_not_called()
            singularity.assert_not_called()
            poetry.assert_not_called()

    def test_validator_uses_source_bound_root_without_adopting_legacy_envs(
        self,
    ) -> None:
        script = (ROOT / "scripts" / "validate_v2_release.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'readonly ENV_ROOT="$VALIDATION_ROOT/managed-envs-by-source/$SOURCE_COMMIT"',
            script,
        )
        self.assertNotIn('readonly ENV_ROOT="$VALIDATION_ROOT/envs"', script)

    def test_poetry_wheel_manifest_includes_native_payload_only(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        includes = {Path(item["path"]) for item in project["tool"]["poetry"]["include"]}
        required = {
            Path("bin/scripts/native_qdnaseq.R"),
            Path("bin/scripts/native_ichorcna.R"),
            Path("environments/native-core.yml"),
            Path("provenance/native-v2-sources.json"),
        }
        forbidden = {
            Path("bin/cna_classifier_nf/main.nf"),
            Path("bin/cna_classifier_nf/nextflow.config"),
            Path("bin/scripts/native_qdnaseq_pon.R"),
            Path("bin/scripts/qdnaseq_local_pon.R"),
            Path("bin/scripts/run_qdnaseq_local_pon.sh"),
        }
        for path in required:
            self.assertTrue(
                any(include == path or include in path.parents for include in includes),
                path,
            )
        for path in forbidden:
            self.assertFalse(
                any(include == path or include in path.parents for include in includes),
                path,
            )
            self.assertFalse(install_safety._poetry_payload_allowed(path), path)

    def test_poetry_v1_fails_before_any_target_or_lock_mutation(self) -> None:
        poetry_v1 = _write_executable(
            self.scratch / "poetry-v1",
            "#!/bin/sh\nprintf 'Poetry (version 1.8.4)\\n'\n",
        )
        base = self.scratch / "poetry-v1-target"
        before = _snapshot(self.scratch)
        with self.assertRaisesRegex(OncoTracerError, "requires Poetry >=2"):
            install_safety.install_conda_managed(
                ROOT,
                base,
                conda=str(self.conda),
                force=False,
                dry_run=False,
                poetry=str(poetry_v1),
            )
        self.assertEqual(_snapshot(self.scratch), before)
        self.assertFalse(base.exists())
        self.assertFalse(install_safety._lock_path(base, "conda").exists())
        self.assertFalse(install_safety._journal_path(base, "conda").exists())

    def test_active_use_includes_self_and_ignores_non_path_proc_links(self) -> None:
        with mock.patch.object(
            install_safety, "_active_processes", ORIGINAL_ACTIVE_PROCESSES
        ):
            self.assertIn(os.getpid(), install_safety._active_processes(Path.cwd()))
            proc = self.scratch / "proc"
            process = proc / "4242"
            (process / "fd").mkdir(parents=True)
            (process / "cwd").symlink_to(self.scratch / "elsewhere")
            (process / "fd" / "1").symlink_to("pipe:[12345]")
            (process / "fd" / "2").symlink_to("socket:[98765]")
            (process / "cmdline").write_bytes(b"relative-command\0")
            (process / "maps").write_text("", encoding="utf-8")
            self.assertEqual(
                install_safety._active_processes(Path.cwd(), proc=proc), []
            )

    def test_shared_consumer_lock_excludes_installer_writer(self) -> None:
        target = self.scratch / "consumer-base"
        lock = install_safety._lock_path(target, "conda")
        with install_safety._shared_install_lock(lock, target, "conda"):
            descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def test_durable_staging_journal_precedes_transaction_creation(self) -> None:
        conda_base = self.scratch / "prejournal-conda"
        conda_seen: list[dict[str, object]] = []

        def stop_conda(target: Path, kind: str, transaction_id: str) -> Path:
            journal = install_safety._safe_read_json(
                install_safety._journal_path(target, kind), "test journal"
            )
            conda_seen.append(journal)
            self.assertEqual(journal["phase"], "staging")
            self.assertEqual(journal["transaction_id"], transaction_id)
            self.assertFalse(
                install_safety._transaction_path(target, kind, transaction_id).exists()
            )
            raise RuntimeError("stop after durable journal")

        with (
            mock.patch.object(
                install_safety, "_create_transaction", side_effect=stop_conda
            ),
            self.assertRaisesRegex(RuntimeError, "durable journal"),
        ):
            self._conda_install(conda_base)
        self.assertEqual(len(conda_seen), 1)
        self.assertFalse(install_safety._journal_path(conda_base, "conda").exists())

        sif = self.scratch / "prejournal.sif"
        sif_seen: list[dict[str, object]] = []

        def stop_sif(target: Path, kind: str, transaction_id: str) -> Path:
            journal = install_safety._safe_read_json(
                install_safety._journal_path(target, kind), "test journal"
            )
            sif_seen.append(journal)
            self.assertEqual(journal["phase"], "staging")
            self.assertIsNone(journal["new_marker"])
            self.assertFalse(
                install_safety._transaction_path(target, kind, transaction_id).exists()
            )
            raise RuntimeError("stop after durable SIF journal")

        with (
            mock.patch.object(
                install_safety, "_create_transaction", side_effect=stop_sif
            ),
            self.assertRaisesRegex(RuntimeError, "durable SIF journal"),
        ):
            self._sif_install(sif)
        self.assertEqual(len(sif_seen), 1)
        self.assertFalse(install_safety._journal_path(sif, "sif").exists())

    def test_sigkill_recovery_preserves_unsealed_prefix_and_restarts(self) -> None:
        base = self.scratch / "kill-envs"
        started = self.scratch / "package-manager-started"
        slow_conda = _write_executable(
            self.scratch / "slow-conda",
            f"""#!/usr/bin/env python3
import os
import pathlib
import sys
import time
prefix = pathlib.Path(sys.argv[sys.argv.index("--prefix") + 1])
(prefix / "foreign-during-kill.txt").write_bytes(b"must survive SIGKILL")
pathlib.Path({str(started)!r}).write_text(str(os.getpid()) + "\\n", encoding="utf-8")
time.sleep(60)
""",
        )
        child_code = f"""from pathlib import Path
from unittest import mock
from oncotracer_cli import install_safety
source={SOURCE!r}
with mock.patch.object(install_safety, "_source_identity", return_value=source):
    install_safety.install_conda_managed(
        Path({str(ROOT)!r}), Path({str(base)!r}),
        conda={str(slow_conda)!r}, force=False, dry_run=False
    )
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=ROOT,
            env=environment,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not started.exists():
            if child.poll() is not None:
                self.fail(f"installer child exited before SIGKILL: {child.returncode}")
            time.sleep(0.05)
        self.assertTrue(started.exists(), "package manager did not reach kill point")
        self.assertTrue(install_safety._journal_path(base, "conda").is_file())
        package_pid = int(started.read_text(encoding="utf-8").strip())
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        os.kill(package_pid, 0)
        try:
            with (
                mock.patch.object(
                    install_safety, "_active_processes", ORIGINAL_ACTIVE_PROCESSES
                ),
                self.assertRaisesRegex(OncoTracerError, "used by active process"),
            ):
                self._conda_install(base)
            self.assertTrue(install_safety._journal_path(base, "conda").is_file())
            self.assertTrue((base / "core" / "foreign-during-kill.txt").is_file())
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(package_pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and Path(f"/proc/{package_pid}").exists():
            time.sleep(0.05)
        self.assertFalse(Path(f"/proc/{package_pid}").exists())

        with mock.patch.object(
            install_safety, "_active_processes", ORIGINAL_ACTIVE_PROCESSES
        ):
            self._conda_install(base)
        preserved = sorted(
            self.scratch.glob(".kill-envs-core.oncotracer-preserved-*-core")
        )
        self.assertEqual(len(preserved), 1)
        self.assertEqual(
            (preserved[0] / "foreign-during-kill.txt").read_bytes(),
            b"must survive SIGKILL",
        )
        self.assertTrue((base / "core" / install_safety.ENV_MARKER).is_file())
        self.assertFalse(install_safety._journal_path(base, "conda").exists())

    def _run_unmocked_cli_install(
        self, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        archive = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "-c",
                "tar.umask=0002",
                "archive",
                "--format=tar",
                "HEAD",
            ],
            check=True,
            capture_output=True,
        ).stdout
        snapshot = self.scratch / "source-snapshot.tar"
        snapshot.write_bytes(archive)
        code = r"""
import pathlib, shutil, sys
from unittest import mock
from oncotracer_cli import cli, install_safety
source = {
    "oncotracer_version": "2.0.0",
    "source_commit": "a" * 40,
    "source_sha256": "b" * 64,
}
snapshot = install_safety._poetry_snapshot_from_archive(
    pathlib.Path(sys.argv.pop(1)).read_bytes()
)
mapping = {
    "conda": sys.argv.pop(1),
    "poetry": sys.argv.pop(1),
    "apptainer": sys.argv.pop(1),
}
real_which = shutil.which
def exact_which(name):
    return mapping.get(name) or real_which(name)
with (
    mock.patch.object(install_safety, "_source_identity", return_value=source),
    mock.patch.object(
        install_safety, "_verify_poetry_source_checkout", return_value=snapshot
    ),
    mock.patch.object(cli.shutil, "which", side_effect=exact_which),
):
    raise SystemExit(cli.main())
"""
        environment = os.environ.copy()
        environment.update(self.environment)
        environment["XDG_CONFIG_HOME"] = str(self.scratch / "cli-config")
        environment["XDG_DATA_HOME"] = str(self.scratch / "cli-data")
        return subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(snapshot),
                str(self.conda),
                str(self.poetry),
                str(self.apptainer),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_real_cli_target_arguments_do_not_mask_true_active_use_scan(self) -> None:
        cases = [
            (
                "conda",
                [
                    "install",
                    "--conda",
                    "--root",
                    str(ROOT),
                    "--prefix",
                    str(self.scratch / "cli-conda"),
                ],
            ),
            (
                "poetry",
                [
                    "install",
                    "--poetry",
                    "--root",
                    str(ROOT),
                    "--prefix",
                    str(self.scratch / "cli-poetry"),
                ],
            ),
            (
                "sif",
                ["install", "--singularity", "--sif", str(self.scratch / "cli.sif")],
            ),
        ]
        for label, command in cases:
            with self.subTest(label=label):
                first = self._run_unmocked_cli_install(*command)
                self.assertEqual(first.returncode, 0, first.stderr)
                second = self._run_unmocked_cli_install(*command, "--force")
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertNotIn("used by active process", second.stderr)

    def test_conda_hardlinked_backup_is_retained_without_touching_external_name(
        self,
    ) -> None:
        base = self.scratch / "hardlinked-retained-envs"
        self._conda_install(base)
        history = base / "core" / "conda-meta" / "history"
        external = self.scratch / "external-history-hardlink"
        os.link(history, external)
        external_inode = external.stat().st_ino
        external_bytes = external.read_bytes()

        self._conda_install(base, force=True)

        backups = [
            path / "backups" / "core" / "conda-meta" / "history"
            for path in base.parent.glob(
                f".{base.name}.oncotracer-conda-txn-*.oncotracer-retained"
            )
            if (path / "backups" / "core" / "conda-meta" / "history").is_file()
        ]
        self.assertEqual(len(backups), 1)
        self.assertEqual(external.read_bytes(), external_bytes)
        self.assertEqual(external.stat().st_ino, external_inode)
        self.assertEqual(backups[0].stat().st_ino, external_inode)
        self.assertEqual(external.stat().st_nlink, 2)
        self.assertNotEqual(history.stat().st_ino, external_inode)

    def test_sif_prior_bytes_and_sidecar_are_retained_after_force(self) -> None:
        destination = self.scratch / "retained-backup.sif"
        sidecar = install_safety._sif_sidecar(destination)
        self._sif_install(destination)
        old_sif = destination.read_bytes()
        old_sidecar = sidecar.read_bytes()

        with mock.patch.dict(
            os.environ, {"FAKE_SIF_CONTENT": "replacement-sif"}, clear=False
        ):
            self._sif_install(destination, force=True)

        retained = [
            path
            for path in destination.parent.glob(
                f".{destination.name}.oncotracer-sif-txn-*.oncotracer-retained"
            )
            if (path / "backup.sif").is_file()
        ]
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "backup.sif").read_bytes(), old_sif)
        self.assertEqual(
            (retained[0] / "backup.sidecar.json").read_bytes(), old_sidecar
        )
        self.assertEqual(destination.read_bytes(), b"replacement-sif")

    def test_retention_collision_preserves_both_roots(self) -> None:
        target = self.scratch / "retention-collision-target"
        transaction_id, transaction = install_safety._new_transaction(target, "conda")
        (transaction / "payload").write_bytes(b"authenticated rollback bytes")
        inventory = install_safety._cleanup_inventory(
            transaction, target, transaction_id, "conda"
        )
        retained = install_safety._committed_retained_path(transaction)
        real_rename = install_safety._rename_noreplace_at
        injected = False

        def collide(source_fd, source, destination_fd, destination, label):
            nonlocal injected
            if destination == retained.name and not injected:
                retained.mkdir()
                (retained / "foreign").write_bytes(b"foreign collision bytes")
                injected = True
            return real_rename(source_fd, source, destination_fd, destination, label)

        with (
            mock.patch.object(
                install_safety, "_rename_noreplace_at", side_effect=collide
            ),
            self.assertRaisesRegex(OncoTracerError, "destination already exists"),
        ):
            install_safety._retain_committed_transaction(
                transaction,
                target,
                transaction_id,
                "conda",
                {".oncotracer-transaction-owner.json", "payload"},
                inventory,
            )
        self.assertTrue(injected)
        self.assertEqual(
            (transaction / "payload").read_bytes(), b"authenticated rollback bytes"
        )
        self.assertEqual(
            (retained / "foreign").read_bytes(), b"foreign collision bytes"
        )

    def test_root_swap_after_authentication_preserves_owned_and_foreign_trees(
        self,
    ) -> None:
        target = self.scratch / "retention-root-swap-target"
        transaction_id, transaction = install_safety._new_transaction(target, "conda")
        (transaction / "payload").write_bytes(b"authenticated rollback bytes")
        inventory = install_safety._cleanup_inventory(
            transaction, target, transaction_id, "conda"
        )
        retained = install_safety._committed_retained_path(transaction)
        saved_owned = self.scratch / "saved-authenticated-transaction"
        foreign = self.scratch / "foreign-replacement"
        foreign.mkdir()
        (foreign / "sentinel").write_bytes(b"foreign bytes must survive")
        real_require = install_safety._require_cleanup_root_identity
        swapped = False

        def swap_after_authentication(parent_fd, name, root_fd, expected):
            nonlocal swapped
            result = real_require(parent_fd, name, root_fd, expected)
            if name == transaction.name and not swapped:
                os.rename(
                    name,
                    saved_owned.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.rename(
                    foreign.name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                swapped = True
            return result

        with (
            mock.patch.object(
                install_safety,
                "_require_cleanup_root_identity",
                side_effect=swap_after_authentication,
            ),
            self.assertRaisesRegex(OncoTracerError, "root identity changed"),
        ):
            install_safety._retain_committed_transaction(
                transaction,
                target,
                transaction_id,
                "conda",
                {".oncotracer-transaction-owner.json", "payload"},
                inventory,
            )
        self.assertTrue(swapped)
        self.assertEqual(
            (saved_owned / "payload").read_bytes(), b"authenticated rollback bytes"
        )
        self.assertEqual(
            (retained / "sentinel").read_bytes(), b"foreign bytes must survive"
        )

    def test_retained_tree_tamper_fails_closed_without_mutation(self) -> None:
        target = self.scratch / "retained-tamper-target"
        transaction_id, transaction = install_safety._new_transaction(target, "sif")
        payload = transaction / "backup.sif"
        payload.write_bytes(b"authenticated backup bytes")
        inventory = install_safety._cleanup_inventory(
            transaction, target, transaction_id, "sif"
        )
        retained = install_safety._retain_committed_transaction(
            transaction,
            target,
            transaction_id,
            "sif",
            {".oncotracer-transaction-owner.json", "backup.sif"},
            inventory,
        )
        payload = retained / "backup.sif"
        payload.write_bytes(b"modified backup bytes")
        before = _snapshot(retained)
        with self.assertRaisesRegex(OncoTracerError, "changed"):
            install_safety._retain_committed_transaction(
                transaction,
                target,
                transaction_id,
                "sif",
                {".oncotracer-transaction-owner.json", "backup.sif"},
                inventory,
            )
        self.assertEqual(_snapshot(retained), before)

    def test_conda_retained_root_rejects_added_nested_file_after_rename(self) -> None:
        base = self.scratch / "post-retention-foreign-envs"
        self._conda_install(base)
        real_rename = install_safety._rename_noreplace_at
        retained: Path | None = None
        sentinel: Path | None = None

        def inject_after_retention(
            source_fd, source, destination_fd, destination, label
        ):
            nonlocal retained, sentinel
            result = real_rename(source_fd, source, destination_fd, destination, label)
            if (
                retained is None
                and label == "committed installer retained rollback material"
                and source.startswith(f".{base.name}.oncotracer-conda-txn-")
            ):
                retained = base.parent / destination
                sentinel = retained / "backups" / "core" / "patient-sentinel"
                sentinel.write_bytes(b"foreign nested bytes must survive")
                raise KeyboardInterrupt
            return result

        with (
            mock.patch.object(
                install_safety,
                "_rename_noreplace_at",
                side_effect=inject_after_retention,
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._conda_install(base, force=True)
        assert retained is not None and sentinel is not None
        journal = install_safety._journal_path(base, "conda")
        before = _snapshot(retained)
        with self.assertRaisesRegex(OncoTracerError, "added=.*patient-sentinel"):
            self._conda_install(base)
        self.assertEqual(_snapshot(retained), before)
        self.assertEqual(sentinel.read_bytes(), b"foreign nested bytes must survive")
        self.assertTrue(journal.is_file())

    def test_sif_retained_root_rejects_modified_backup_after_rename(self) -> None:
        destination = self.scratch / "post-retention-modified.sif"
        self._sif_install(destination)
        real_rename = install_safety._rename_noreplace_at
        retained: Path | None = None
        backup: Path | None = None

        def inject_after_retention(source_fd, source, destination_fd, target, label):
            nonlocal retained, backup
            result = real_rename(source_fd, source, destination_fd, target, label)
            if (
                retained is None
                and label == "committed installer retained rollback material"
                and source.startswith(f".{destination.name}.oncotracer-sif-txn-")
            ):
                retained = destination.parent / target
                backup = retained / "backup.sif"
                backup.write_bytes(b"modified retained SIF backup")
                raise KeyboardInterrupt
            return result

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "replacement"}, clear=False
            ),
            mock.patch.object(
                install_safety,
                "_rename_noreplace_at",
                side_effect=inject_after_retention,
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._sif_install(destination, force=True)
        assert retained is not None and backup is not None
        journal = install_safety._journal_path(destination, "sif")
        before = _snapshot(retained)
        with self.assertRaisesRegex(OncoTracerError, "changed=.*backup.sif"):
            self._sif_install(destination)
        self.assertEqual(_snapshot(retained), before)
        self.assertEqual(backup.read_bytes(), b"modified retained SIF backup")
        self.assertTrue(journal.is_file())

    def test_fresh_sif_publication_collision_is_never_overwritten(self) -> None:
        destination = self.scratch / "foreign-collision.sif"
        foreign_bytes = b"FOREIGN PATIENT BYTES"
        real_rename = install_safety._rename_noreplace_at
        injected = False

        def collide(source_fd, source, destination_fd, target, label):
            nonlocal injected
            if label == "verified SIF publication" and not injected:
                destination.write_bytes(foreign_bytes)
                injected = True
            return real_rename(source_fd, source, destination_fd, target, label)

        with (
            mock.patch.object(
                install_safety, "_rename_noreplace_at", side_effect=collide
            ),
            self.assertRaisesRegex(
                OncoTracerError, "automatic rollback could not complete"
            ),
        ):
            self._sif_install(destination)
        self.assertTrue(injected)
        self.assertEqual(destination.read_bytes(), foreign_bytes)
        self.assertTrue(install_safety._journal_path(destination, "sif").is_file())

    def test_conda_rolled_back_retention_interrupt_recovers(self) -> None:
        base = self.scratch / "rolled-back-interrupt-envs"
        self._conda_install(base)
        before = _snapshot(base)
        real_rename = install_safety._rename_noreplace
        real_retain = install_safety._retain_transaction_journal
        failed = False
        interrupted = False

        def fail_publication(source, destination, label):
            nonlocal failed
            if (
                label == "Conda final-prefix publication"
                and Path(destination) == base / "qdnaseq"
                and not failed
            ):
                failed = True
                raise OSError("injected Conda publication failure")
            return real_rename(source, destination, label)

        def interrupt_after_root_retention(path, journal, kind):
            nonlocal interrupted
            if journal.get("phase") == "rolled_back" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return real_retain(path, journal, kind)

        with (
            mock.patch.object(
                install_safety, "_rename_noreplace", side_effect=fail_publication
            ),
            mock.patch.object(
                install_safety,
                "_retain_transaction_journal",
                side_effect=interrupt_after_root_retention,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._conda_install(base, force=True)
        self.assertTrue(failed)
        self.assertTrue(interrupted)
        journal_path = install_safety._journal_path(base, "conda")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rolled_back")
        transaction = Path(str(journal["canonical_transaction"]))
        retained = install_safety._committed_retained_path(transaction)
        self.assertFalse(transaction.exists())
        self.assertTrue(retained.is_dir())
        self.assertEqual(_snapshot(base), before)
        retained_before = _snapshot(retained)

        self._conda_install(base)
        self.assertFalse(journal_path.exists())
        self.assertEqual(_snapshot(base), before)
        self.assertEqual(_snapshot(retained), retained_before)

    def test_sif_rolled_back_retention_interrupt_recovers(self) -> None:
        destination = self.scratch / "rolled-back-interrupt.sif"
        sidecar = install_safety._sif_sidecar(destination)
        self._sif_install(destination)
        before = (destination.read_bytes(), sidecar.read_bytes())
        real_rename = install_safety._rename_noreplace
        real_retain = install_safety._retain_transaction_journal
        failed = False
        interrupted = False

        def fail_publication(source, target, label):
            nonlocal failed
            if label == "verified SIF sidecar publication" and not failed:
                failed = True
                raise OSError("injected SIF sidecar publication failure")
            return real_rename(source, target, label)

        def interrupt_after_root_retention(path, journal, kind):
            nonlocal interrupted
            if journal.get("phase") == "rolled_back" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return real_retain(path, journal, kind)

        with (
            mock.patch.dict(
                os.environ, {"FAKE_SIF_CONTENT": "replacement"}, clear=False
            ),
            mock.patch.object(
                install_safety, "_rename_noreplace", side_effect=fail_publication
            ),
            mock.patch.object(
                install_safety,
                "_retain_transaction_journal",
                side_effect=interrupt_after_root_retention,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._sif_install(destination, force=True)
        self.assertTrue(failed)
        self.assertTrue(interrupted)
        journal_path = install_safety._journal_path(destination, "sif")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rolled_back")
        transaction = Path(str(journal["canonical_transaction"]))
        retained = install_safety._committed_retained_path(transaction)
        self.assertFalse(transaction.exists())
        self.assertTrue(retained.is_dir())
        self.assertEqual((destination.read_bytes(), sidecar.read_bytes()), before)
        retained_before = _snapshot(retained)

        self._sif_install(destination)
        self.assertFalse(journal_path.exists())
        self.assertEqual((destination.read_bytes(), sidecar.read_bytes()), before)
        self.assertEqual(_snapshot(retained), retained_before)

    def test_fresh_conda_absent_rolled_back_double_interrupt_recovers(self) -> None:
        base = self.scratch / "absent-double-interrupt-envs"
        journal_path = install_safety._journal_path(base, "conda")
        real_write = install_safety._write_journal
        real_restore = install_safety._restore_conda_transaction
        publishing_written = False
        restore_interrupted = False

        def interrupt_after_publishing_write(path, value):
            nonlocal publishing_written
            real_write(path, value)
            if value.get("phase") == "publishing" and not publishing_written:
                publishing_written = True
                raise KeyboardInterrupt

        def interrupt_first_restore(target, path, journal):
            nonlocal restore_interrupted
            if journal.get("phase") == "publishing" and not restore_interrupted:
                restore_interrupted = True
                raise KeyboardInterrupt
            return real_restore(target, path, journal)

        with (
            mock.patch.object(
                install_safety,
                "_write_journal",
                side_effect=interrupt_after_publishing_write,
            ),
            mock.patch.object(
                install_safety,
                "_restore_conda_transaction",
                side_effect=interrupt_first_restore,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._conda_install(base)
        self.assertTrue(publishing_written)
        self.assertTrue(restore_interrupted)
        self.assertFalse(os.path.lexists(base))
        self.assertEqual(
            json.loads(journal_path.read_text(encoding="utf-8"))["phase"],
            "publishing",
        )

        real_retain = install_safety._retain_transaction_journal
        retention_interrupted = False

        def interrupt_after_root_retention(path, journal, kind):
            nonlocal retention_interrupted
            if journal.get("phase") == "rolled_back" and not retention_interrupted:
                retention_interrupted = True
                raise KeyboardInterrupt
            return real_retain(path, journal, kind)

        with (
            mock.patch.object(
                install_safety,
                "_retain_transaction_journal",
                side_effect=interrupt_after_root_retention,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self._conda_install(base)
        self.assertTrue(retention_interrupted)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rolled_back")
        transaction = Path(str(journal["canonical_transaction"]))
        retained = install_safety._committed_retained_path(transaction)
        self.assertFalse(os.path.lexists(base))
        self.assertFalse(transaction.exists())
        self.assertTrue(retained.is_dir())
        retained_before = _snapshot(retained)

        installed = self._conda_install(base)
        self.assertFalse(journal_path.exists())
        self.assertEqual(set(installed), set(install_safety.CONDA_NAMES))
        self.assertEqual(_snapshot(retained), retained_before)

    def test_poetry_build_metadata_preserves_symlink_target(self) -> None:
        staging = self.scratch / "poetry-state" / "source"
        (staging / "provenance").mkdir(parents=True)
        (staging / "oncotracer_cli").mkdir()
        (staging / "provenance" / "native-v2-sources.json").write_bytes(b"{}\n")
        external = self.scratch / "patient-sentinel"
        external.write_bytes(b"PATIENT BYTES")
        metadata = staging / "oncotracer_cli" / "_build_metadata.py"
        metadata.symlink_to(external)

        install_safety._write_poetry_build_metadata(staging, SOURCE)

        self.assertEqual(external.read_bytes(), b"PATIENT BYTES")
        self.assertTrue(metadata.is_file())
        self.assertFalse(metadata.is_symlink())
        self.assertIn(SOURCE["source_commit"], metadata.read_text(encoding="utf-8"))
        retained = list((staging.parent / "metadata-history").iterdir())
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].readlink(), external)

    def test_installer_has_no_automatic_deletion_primitive(self) -> None:
        source = Path(install_safety.__file__).read_text(encoding="utf-8")
        for primitive in (
            ".unlink(",
            ".rmdir(",
            "os.unlink(",
            "os.rmdir(",
            "os.replace(",
            "shutil.rmtree(",
        ):
            with self.subTest(primitive=primitive):
                self.assertNotIn(primitive, source)


class InstallerActiveUseArgumentTests(unittest.TestCase):
    def _process(self, proc: Path, pid: int, *, arguments: list[Path]) -> Path:
        process = proc / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "cwd").symlink_to("/var/empty")
        (process / "exe").symlink_to(sys.executable)
        (process / "cmdline").write_bytes(
            b"oncotracer\0--prefix\0"
            + b"\0".join(os.fsencode(path) for path in arguments)
            + b"\0"
        )
        (process / "maps").write_text("", encoding="utf-8")
        return process

    def test_only_current_pid_exact_cli_target_argument_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oncotracer-proc-") as directory:
            scratch = Path(directory)
            proc = scratch / "proc"
            proc.mkdir()
            target = scratch / "managed"
            target.mkdir()
            current = self._process(proc, os.getpid(), arguments=[target])
            self.assertEqual(
                install_safety._active_processes(target, proc=proc), [os.getpid()]
            )
            with install_safety.installer_cli_target_arguments(target):
                self.assertEqual(
                    install_safety._active_processes(target, proc=proc), []
                )

            nested = target / "nested"
            nested.mkdir()
            (current / "cmdline").write_bytes(
                b"oncotracer\0--prefix\0"
                + os.fsencode(target)
                + b"\0--other\0"
                + os.fsencode(nested)
                + b"\0"
            )
            with install_safety.installer_cli_target_arguments(target):
                self.assertEqual(
                    install_safety._active_processes(target, proc=proc), [os.getpid()]
                )

            (current / "cmdline").write_bytes(
                b"oncotracer\0--prefix\0" + os.fsencode(target) + b"\0"
            )
            current = proc / current.absolute().relative_to(proc.absolute())
            (current / "cwd").unlink()
            (current / "cwd").symlink_to(nested)
            with install_safety.installer_cli_target_arguments(target):
                self.assertEqual(
                    install_safety._active_processes(target, proc=proc), [os.getpid()]
                )

            (current / "cwd").unlink()
            (current / "cwd").symlink_to("/var/empty")
            other_pid = os.getpid() + 100000
            self._process(proc, other_pid, arguments=[target])
            with install_safety.installer_cli_target_arguments(target):
                self.assertEqual(
                    install_safety._active_processes(target, proc=proc), [other_pid]
                )


class PoetryAuthenticatedSnapshotTests(unittest.TestCase):
    def test_verified_archive_remains_bound_after_live_checkout_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oncotracer-poetry-source-"
        ) as directory:
            root = Path(directory) / "repository"
            root.mkdir()
            files = {
                "pyproject.toml": b"[tool.poetry]\nname='oncotracer'\nversion='2.0.0'\n",
                "poetry.lock": b"lock-version = '2.1'\n",
                "README.md": b"readme\n",
                "oncotracer_cli/cli.py": b"ORIGINAL = True\n",
                "bin/native": b"native\n",
                "examples/example": b"example\n",
                "params/default.yml": b"mode: ont\n",
                "environments/native-core.yml": b"name: core\n",
                "provenance/native-v2-sources.json": b"{}\n",
            }
            for relative, contents in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            subprocess.run(["git", "init", "-q", root], check=True)
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=OncoTracer Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "fixture",
                ],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            archive = subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "tar.umask=0002",
                    "archive",
                    "--format=tar",
                    commit,
                ],
                check=True,
                capture_output=True,
            ).stdout
            source = {
                **SOURCE,
                "source_commit": commit,
                "source_sha256": install_safety.hashlib.sha256(archive).hexdigest(),
            }
            snapshot = install_safety._verify_poetry_source_checkout(root, source)
            (root / "oncotracer_cli" / "cli.py").write_bytes(b"MUTATED = True\n")
            staging = Path(directory) / "staging"
            staging.mkdir()
            install_safety._copy_poetry_project(snapshot, staging)
            self.assertEqual(
                (staging / "oncotracer_cli" / "cli.py").read_bytes(),
                b"ORIGINAL = True\n",
            )
            self.assertEqual(
                snapshot.pyproject_sha256,
                install_safety.hashlib.sha256(files["pyproject.toml"]).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
