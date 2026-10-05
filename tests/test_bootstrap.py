"""Ordinary unittest runs must not mutate managed Python package inventories."""

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from oncotracer_cli import install_safety


BOOTSTRAP = Path(__file__).with_name("__init__.py")
PROBE = '''\
import subprocess
import sys
import unittest

# Imports happen while unittest loads this module, before any test executes.
from managed_fixture import parent_stale, parent_uncached


class ImportProbe(unittest.TestCase):
    def test_parent_and_child_read_current_sources(self):
        self.assertEqual(parent_stale.VALUE, "current value")
        self.assertEqual(parent_uncached.VALUE, "current value")
        child = subprocess.run(
            [sys.executable, "-c",
             "from managed_fixture import child_stale, child_uncached; "
             "assert child_stale.VALUE == 'current value'; "
             "assert child_uncached.VALUE == 'current value'"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
'''


class TestBootstrapTests(unittest.TestCase):
    def test_unittest_imports_preserve_stale_and_missing_bytecode_in_parent_and_child(self) -> None:
        for arguments in ([], ["tests.test_probe"]):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                suite = root / "suite"
                tests = suite / "tests"
                tests.mkdir(parents=True)
                # Use the repository bootstrap verbatim in a small disposable suite.
                (tests / "__init__.py").write_bytes(BOOTSTRAP.read_bytes())
                (tests / "test_probe.py").write_text(textwrap.dedent(PROBE), encoding="utf-8")
                prefix = root / "managed-prefix"
                packages = prefix / "site-packages"
                package = packages / "managed_fixture"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("", encoding="utf-8")
                for name in ("parent_stale", "child_stale"):
                    source = package / f"{name}.py"
                    source.write_text('VALUE = "old"\n', encoding="utf-8")
                    cache = package / "__pycache__" / f"{name}.{sys.implementation.cache_tag}.pyc"
                    py_compile.compile(str(source), cfile=str(cache), doraise=True)
                    # Different source size invalidates the cache even on coarse clocks.
                    source.write_text('VALUE = "current value"\n', encoding="utf-8")
                for name in ("parent_uncached", "child_uncached"):
                    (package / f"{name}.py").write_text('VALUE = "current value"\n', encoding="utf-8")

                marker = {"inventory_sha256": install_safety._write_child_inventory(prefix)}
                inventory_path = prefix / install_safety.CHILD_INVENTORY
                inventory_before = inventory_path.read_bytes()
                environment = os.environ.copy()
                # The subprocess must exercise the bootstrap, not inherit our guard.
                environment.pop("PYTHONDONTWRITEBYTECODE", None)
                environment.pop("PYTHONPYCACHEPREFIX", None)
                environment["PYTHONPATH"] = str(packages)
                environment["PYTHONNOUSERSITE"] = "1"
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", *arguments],
                    cwd=suite, env=environment, capture_output=True, text=True,
                    check=False, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Ran 1 test", result.stderr)
                # Verify against the original seal, without excluding or resealing caches.
                self.assertEqual(inventory_path.read_bytes(), inventory_before)
                install_safety._verify_child_inventory(prefix, marker)


if __name__ == "__main__":
    unittest.main()
