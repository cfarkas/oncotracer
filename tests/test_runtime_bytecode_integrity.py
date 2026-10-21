"""Only source-equivalent regenerated package caches may pass sealed inventory."""
from __future__ import annotations

import json
import hashlib
import os
import py_compile
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oncotracer_cli import install_safety as safety
from oncotracer_cli.runtime import OncoTracerError

_RUN = subprocess.run


class RegeneratedBytecodeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer-bytecode-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package = self.root / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/example"
        # Bind dynamic interpreter paths to this fixture's owned temporary tree.
        self.package = self.root / package.relative_to(self.root)
        self.package.mkdir(parents=True)
        self.source = self.package / "__config__.py"
        # Executing the package is forbidden; compiling it is harmless.
        self.source.write_text("raise RuntimeError('must never be executed')\nVALUE = 42\n")
        binary = self.root / "bin/python"
        binary.parent.mkdir()
        binary.write_bytes(b"sealed interpreter placeholder\n")
        binary.chmod(0o755)
        cache = Path(py_compile.compile(str(self.source), dfile="/build/__config__.py"))
        self.cache = self.root / cache.relative_to(self.root)
        self.seal_fixture()

    def seal_fixture(self):
        inventory = self.root / safety.CHILD_INVENTORY
        (self.root / inventory.relative_to(self.root)).unlink(missing_ok=True)
        self.marker = {"inventory_sha256": safety._write_child_inventory(self.root)}
        self.seal = (self.root / safety.CHILD_INVENTORY).read_bytes()

    def run_real_probe(self, command, **kwargs):
        # The inventory fixture uses a placeholder executable; execute the exact
        # compile-only script with this test interpreter, not the fake binary.
        self.assertEqual(command[1:4], ["-I", "-S", "-B"])
        self.assertEqual(command[4], "-c")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        request = json.loads(kwargs["input"])
        self.assertEqual(request["prefix"], str(self.root))
        request["prefix"] = sys.base_prefix
        kwargs["input"] = json.dumps(request).encode()
        return _RUN([sys.executable, *command[1:]], **kwargs)

    def verify(self):
        with mock.patch.object(safety.subprocess, "run", side_effect=self.run_real_probe) as runner:
            safety._verify_child_inventory(self.root, self.marker)
        self.assertEqual((self.root / safety.CHILD_INVENTORY).read_bytes(), self.seal)
        return runner

    def expect_rejected(self, *, probe=False):
        with mock.patch.object(safety.subprocess, "run", side_effect=self.run_real_probe) as runner:
            with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
                safety._verify_child_inventory(self.root, self.marker)
        self.assertEqual(bool(runner.call_count), probe)
        self.assertEqual((self.root / safety.CHILD_INVENTORY).read_bytes(), self.seal)

    def test_timestamp_and_hash_regeneration_are_verified_without_resealing(self):
        for mode in py_compile.PycInvalidationMode:
            with self.subTest(mode=mode):
                py_compile.compile(str(self.source), invalidation_mode=mode)
                before = self.cache.read_bytes()
                self.assertEqual(self.verify().call_count, 1)
                self.assertEqual(self.cache.read_bytes(), before)

    def test_interpreter_hashing_works_without_python_311_file_digest(self):
        py_compile.compile(str(self.source))
        with mock.patch.object(hashlib, "file_digest", side_effect=AssertionError("Python 3.11-only API"), create=True):
            self.verify()

    def test_optimized_caches_use_the_declared_optimization(self):
        self.cache.unlink()
        for optimization in (1, 2):
            with self.subTest(optimization=optimization):
                compiled = Path(py_compile.compile(str(self.source), dfile="/build/config.py", optimize=optimization))
                cache = self.root / compiled.relative_to(self.root)
                self.seal_fixture()
                py_compile.compile(str(self.source), optimize=optimization)
                self.verify()
                cache.unlink()

    def test_modified_source_is_rejected_before_starting_any_interpreter(self):
        self.source.write_text("VALUE = 'changed'\n")
        py_compile.compile(str(self.source))
        self.expect_rejected()

    def test_modified_interpreter_is_rejected_before_execution(self):
        py_compile.compile(str(self.source))
        (self.root / "bin/python").write_bytes(b"different executable\n")
        self.expect_rejected()

    def test_modified_bytecode_body_is_not_executed_or_accepted(self):
        py_compile.compile(str(self.source))
        data = bytearray(self.cache.read_bytes())
        data[-1] ^= 1
        self.cache.write_bytes(data)
        self.expect_rejected(probe=True)

    def test_bad_magic_flags_timestamp_hash_and_trailing_bytes_are_rejected(self):
        for corruption in ("magic", "flags", "timestamp", "hash", "trailing"):
            with self.subTest(corruption=corruption):
                mode = py_compile.PycInvalidationMode.CHECKED_HASH if corruption == "hash" else py_compile.PycInvalidationMode.TIMESTAMP
                py_compile.compile(str(self.source), invalidation_mode=mode)
                data = bytearray(self.cache.read_bytes())
                if corruption == "magic":
                    data[0] ^= 1
                elif corruption == "flags":
                    data[4:8] = struct.pack("<I", 2)
                elif corruption in ("timestamp", "hash"):
                    data[8] ^= 1
                else:
                    data.extend(b"foreign payload")
                self.cache.write_bytes(data)
                self.expect_rejected(probe=True)

    def test_missing_and_added_cache_entries_are_rejected_without_probe(self):
        original = self.cache.read_bytes()
        self.cache.unlink()
        self.expect_rejected()
        self.cache.write_bytes(original)
        foreign = self.cache.with_name("other.cpython-311.pyc")
        foreign.write_bytes(original)
        self.expect_rejected()

    def test_cache_permission_changes_remain_rejected(self):
        py_compile.compile(str(self.source))
        self.cache.chmod(0o777)
        self.expect_rejected()

    def test_changed_cache_symlink_is_rejected_without_probe(self):
        target = self.root / "target"
        target.write_bytes(self.cache.read_bytes())
        # Seal target first so the sole difference is the symlink substitution.
        self.seal_fixture()
        self.cache.unlink()
        self.cache.symlink_to(target)
        self.expect_rejected()

    def test_changed_source_symlink_is_rejected_without_probe(self):
        target = self.root / "target.py"
        target.write_bytes(self.source.read_bytes())
        self.seal_fixture()
        self.source.unlink()
        self.source.symlink_to(target)
        self.expect_rejected()

    def test_changed_package_directory_mode_is_rejected_without_probe(self):
        py_compile.compile(str(self.source))
        self.package.chmod(0o700)
        self.expect_rejected()

    def test_external_interpreter_symlink_is_rejected_without_probe(self):
        py_compile.compile(str(self.source))
        binary = self.root / "bin/python"
        binary.unlink()
        binary.symlink_to(sys.executable)
        self.expect_rejected()

    def test_new_foreign_file_beside_benign_cache_is_rejected_without_probe(self):
        py_compile.compile(str(self.source))
        (self.package / "foreign.py").write_text("raise RuntimeError('foreign')\n")
        self.expect_rejected()

    def test_probe_timeout_leaves_original_error_and_inventory_intact(self):
        py_compile.compile(str(self.source))
        with mock.patch.object(safety.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 30)):
            with self.assertRaisesRegex(OncoTracerError, "changed or foreign entries"):
                safety._verify_child_inventory(self.root, self.marker)
        self.assertEqual((self.root / safety.CHILD_INVENTORY).read_bytes(), self.seal)

    def test_version_mismatch_is_rejected_by_environment_interpreter(self):
        other_tag = f"{sys.version_info.major}{sys.version_info.minor + 1}"
        other_root = self.root / f"lib/python{sys.version_info.major}.{sys.version_info.minor + 1}/site-packages/example"
        other_root.mkdir(parents=True)
        other_source = other_root / "__config__.py"
        other_source.write_bytes(self.source.read_bytes())
        other_cache = other_root / "__pycache__" / f"__config__.cpython-{other_tag}.pyc"
        other_cache.parent.mkdir()
        py_compile.compile(str(other_source), cfile=str(other_cache), dfile="/build/config.py")
        self.seal_fixture()
        py_compile.compile(str(other_source), cfile=str(other_cache))
        self.expect_rejected(probe=True)


if __name__ == "__main__":
    unittest.main()
