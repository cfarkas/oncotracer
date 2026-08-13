#!/usr/bin/env python3
"""Focused regression tests for invocation-private Fontconfig state."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from oncotracer_cli.engine import Toolchain
from oncotracer_cli.fontconfig_safety import FontconfigRuntime, _include_path
from oncotracer_cli.runtime import CommandRunner, OncoTracerError


def _write_graph(prefix: Path) -> tuple[Path, Path]:
    fonts = prefix / "etc" / "fonts"
    fragments = fonts / "conf.d"
    fragments.mkdir(parents=True)
    unsafe_cache = prefix / "var" / "cache" / "fontconfig"
    unsafe_cache.mkdir(parents=True)
    (unsafe_cache / "sentinel").write_bytes(b"must remain exact")
    root = fonts / "fonts.conf"
    root.write_text(
        "<fontconfig>\n"
        "  <dir>/usr/share/fonts</dir>\n"
        f"  <cachedir>{unsafe_cache}</cachedir>\n"
        '  <cachedir prefix="xdg">fontconfig</cachedir>\n'
        '  <include ignore_missing="yes">conf.d</include>\n'
        "</fontconfig>\n",
        encoding="utf-8",
    )
    (fragments / "48-fragment.conf").write_text(
        "<selectfont><rejectfont><pattern>"
        '<patelt name="family"><const xsi:nil="true"/>'
        "</patelt></pattern></rejectfont></selectfont>\n",
        encoding="utf-8",
    )
    (fragments / "50-user.conf").write_text(
        "<fontconfig>"
        '<include ignore_missing="yes" prefix="xdg">'
        "fontconfig/fonts.conf</include>"
        '<include ignore_missing="yes" deprecated="yes">'
        "~/.fonts.conf</include>"
        "</fontconfig>\n",
        encoding="utf-8",
    )
    (fragments / "51-local.conf").write_text(
        '<fontconfig><include ignore_missing="yes">'
        "local.conf</include></fontconfig>\n",
        encoding="utf-8",
    )
    (fragments / "not-loaded.conf").write_text(
        "<fontconfig><cachedir>/must/not/be/read</cachedir></fontconfig>\n",
        encoding="utf-8",
    )
    return root, unsafe_cache


def _snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    observed: dict[str, tuple[object, ...]] = {}
    if not os.path.lexists(root):
        return observed
    for path in (root, *sorted(root.rglob("*"))):
        relative = "." if path == root else str(path.relative_to(root))
        metadata = path.lstat()
        common = (stat.S_IMODE(metadata.st_mode), metadata.st_size)
        if stat.S_ISLNK(metadata.st_mode):
            observed[relative] = ("symlink", *common, os.readlink(path))
        elif stat.S_ISDIR(metadata.st_mode):
            observed[relative] = ("directory", *common)
        elif stat.S_ISREG(metadata.st_mode):
            observed[relative] = (
                "file",
                *common,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            observed[relative] = ("special", *common)
    return observed


def _subprocess_environment(values: dict[str, str | None]) -> dict[str, str]:
    environment = os.environ.copy()
    for name, value in values.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return environment


class FontconfigSafetyTests(unittest.TestCase):
    def test_realistic_recursive_graph_publishes_one_private_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            _source, unsafe_cache = _write_graph(prefix)
            runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
            before = _snapshot(unsafe_cache)
            environment = runtime.environment("core")
            wrapper = Path(str(environment["FONTCONFIG_FILE"]))
            self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o400)
            guard_root = Path(str(environment["FONTCONFIG_PATH"]).split(os.pathsep)[0])
            self.assertEqual(stat.S_IMODE(guard_root.stat().st_mode), 0o500)
            self.assertTrue(
                all(
                    stat.S_IMODE(path.stat().st_mode) == 0o400
                    for path in guard_root.iterdir()
                )
            )
            parsed = ET.parse(wrapper).getroot()
            caches = list(parsed.iter("cachedir"))
            self.assertEqual(len(caches), 1)
            private_cache = Path((caches[0].text or "").strip())
            self.assertIn((root / "runtime").resolve(), private_cache.parents)
            self.assertNotIn(prefix.resolve(), private_cache.parents)
            self.assertEqual(stat.S_IMODE(private_cache.stat().st_mode), 0o700)
            self.assertEqual(environment["HOME"], str(root / "runtime" / "home"))
            self.assertIsNone(environment["FONTCONFIG_SYSROOT"])
            runtime.validate()
            self.assertEqual(_snapshot(unsafe_cache), before)

    def test_include_path_semantics_are_exact_and_bounded(self) -> None:
        config = Path("/source/fonts")
        xdg = Path("/private/xdg")
        home = Path("/private/home")
        node = ET.Element("include", {"prefix": "xdg"})
        node.text = "/fontconfig/fonts.conf"
        self.assertEqual(
            _include_path(node, config_path=config, xdg_config=xdg, home=home),
            xdg / "fontconfig" / "fonts.conf",
        )
        node = ET.Element("include")
        node.text = "local.conf"
        self.assertEqual(
            _include_path(node, config_path=config, xdg_config=xdg, home=home),
            config / "local.conf",
        )
        node.text = "~/.fonts.conf"
        self.assertEqual(
            _include_path(node, config_path=config, xdg_config=xdg, home=home),
            home / ".fonts.conf",
        )
        node.text = "~another/.fonts.conf"
        with self.assertRaisesRegex(OncoTracerError, "home expansion"):
            _include_path(node, config_path=config, xdg_config=xdg, home=home)

    def test_unsafe_graph_shapes_fail_closed(self) -> None:
        cases = {
            "included cache target": (
                "<fontconfig><include>50-bad.conf</include></fontconfig>",
                "<fontconfig><cachedir>/unsafe</cachedir></fontconfig>",
                "declares a cache target",
            ),
            "nested root cache": (
                "<fontconfig><match><cachedir>/unsafe</cachedir></match></fontconfig>",
                None,
                "nested cache target",
            ),
            "wrapper relative directory": (
                '<fontconfig><dir prefix="relative">fonts</dir></fontconfig>',
                None,
                "wrapper-relative",
            ),
            "unsupported include prefix": (
                '<fontconfig><include prefix="cwd">other.conf</include></fontconfig>',
                None,
                "unsupported prefix",
            ),
            "malformed fragment": (
                "<fontconfig><include>50-bad.conf</include></fontconfig>",
                "<fontconfig>",
                "malformed",
            ),
            "cycle": (
                "<fontconfig><include>50-bad.conf</include></fontconfig>",
                "<fontconfig><include>50-bad.conf</include></fontconfig>",
                "cycle",
            ),
        }
        for label, (root_xml, fragment_xml, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                prefix = temporary / "prefix"
                fonts = prefix / "etc" / "fonts"
                fonts.mkdir(parents=True)
                (fonts / "fonts.conf").write_text(root_xml, encoding="utf-8")
                if fragment_xml is not None:
                    (fonts / "50-bad.conf").write_text(fragment_xml, encoding="utf-8")
                runtime = FontconfigRuntime(temporary / "runtime", {"core": prefix})
                with self.assertRaisesRegex(OncoTracerError, expected):
                    runtime.environment("core")

    def test_runtime_root_must_be_outside_every_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "prefix"
            prefix.mkdir()
            with self.assertRaisesRegex(OncoTracerError, "outside configured"):
                FontconfigRuntime(prefix / "runtime", {"core": prefix})

    def test_source_wrapper_and_cache_identity_changes_are_rejected(self) -> None:
        mutations = ("source", "wrapper", "cache", "guard")
        for mutation in mutations:
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                prefix = root / "prefix"
                source, _unsafe = _write_graph(prefix)
                runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
                environment = runtime.environment("core")
                wrapper = Path(str(environment["FONTCONFIG_FILE"]))
                if mutation == "source":
                    source.write_text(
                        source.read_text(encoding="utf-8") + "<!-- changed -->\n",
                        encoding="utf-8",
                    )
                elif mutation == "wrapper":
                    saved = wrapper.with_name("saved-wrapper")
                    os.rename(wrapper, saved)
                    wrapper.write_bytes(saved.read_bytes())
                    wrapper.chmod(0o400)
                else:
                    if mutation == "cache":
                        parsed = ET.parse(wrapper).getroot()
                        cache = Path((next(parsed.iter("cachedir")).text or "").strip())
                        saved = cache.with_name("saved-cache")
                        os.rename(cache, saved)
                        cache.mkdir(mode=0o700)
                    else:
                        guard_root = Path(
                            str(environment["FONTCONFIG_PATH"]).split(os.pathsep)[0]
                        )
                        guard_root.chmod(0o700)
                        (guard_root / "foreign.conf").write_text(
                            "<fontconfig/>\n", encoding="utf-8"
                        )
                        (guard_root / "foreign.conf").chmod(0o400)
                        guard_root.chmod(0o500)
                with self.assertRaisesRegex(OncoTracerError, "changed"):
                    runtime.validate()

    def test_missing_nested_search_include_cannot_fall_back_to_compiled_path(
        self,
    ) -> None:
        fc_conflist = shutil.which("fc-conflist")
        if fc_conflist is None:
            self.skipTest("system Fontconfig compiled configuration is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "external-prefix"
            fonts = prefix / "etc" / "fonts"
            fonts.mkdir(parents=True)
            fragment = fonts / "fragment.conf"
            fragment.write_text(
                '<fontconfig><include ignore_missing="yes">'
                "conf.d</include></fontconfig>\n",
                encoding="utf-8",
            )
            (fonts / "fonts.conf").write_text(
                f"<fontconfig><include>{fragment}</include></fontconfig>\n",
                encoding="utf-8",
            )
            runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
            environment = runtime.environment("core")
            guard = Path(str(environment["FONTCONFIG_PATH"]).split(os.pathsep)[0])
            sentinel = guard / "conf.d"
            self.assertTrue(sentinel.is_file())
            completed = subprocess.run(
                [fc_conflist],
                env=_subprocess_environment(environment),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            loaded = [
                line for line in completed.stdout.splitlines() if line.startswith("+ ")
            ]
            allowed = {
                str(Path(str(environment["FONTCONFIG_FILE"]))),
                str(fragment),
                str(sentinel),
            }
            loaded_paths = {line[2:].split(": ", 1)[0] for line in loaded}
            self.assertEqual(loaded_paths, allowed)
            runtime.validate()

    def test_ambiguous_and_shadowing_search_paths_fail_closed(self) -> None:
        cases = {
            "path separator": ("local:conf", "normalized relative path"),
            "trailing slash": ("conf.d/", "normalized relative path"),
            "repeated separator": ("foo//bar.conf", "normalized relative path"),
            "ambiguous home separator": ("~//target.conf", "normalized relative path"),
            "parent traversal": ("../local.conf", "normalized relative path"),
            "nested guard parent": ("foo/local.conf", "unsafe guard parent"),
        }
        for label, (include, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prefix = root / "prefix"
                fonts = prefix / "etc" / "fonts"
                fonts.mkdir(parents=True)
                fragment = fonts / "fragment.conf"
                fragment.write_text(
                    f'<fontconfig><include ignore_missing="yes">{include}</include></fontconfig>\n',
                    encoding="utf-8",
                )
                (fonts / "fonts.conf").write_text(
                    f"<fontconfig><include>{fragment}</include></fontconfig>\n",
                    encoding="utf-8",
                )
                runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
                with self.assertRaisesRegex(OncoTracerError, expected):
                    runtime.environment("core")

        with tempfile.TemporaryDirectory(prefix="oncotracer-fonts:") as directory:
            root = Path(directory)
            prefix = root / "prefix"
            fonts = prefix / "etc" / "fonts"
            fonts.mkdir(parents=True)
            (fonts / "fonts.conf").write_text("<fontconfig/>\n", encoding="utf-8")
            runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
            with self.assertRaisesRegex(OncoTracerError, "path-list separator"):
                runtime.environment("core")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            fonts = prefix / "etc" / "fonts"
            existing = fonts / "foo"
            existing.mkdir(parents=True)
            (existing / "50-source.conf").write_text(
                "<fontconfig/>\n", encoding="utf-8"
            )
            fragment = fonts / "fragment.conf"
            fragment.write_text(
                '<fontconfig><include ignore_missing="yes">foo</include>'
                '<include ignore_missing="yes">foo/missing.conf</include></fontconfig>\n',
                encoding="utf-8",
            )
            (fonts / "fonts.conf").write_text(
                f"<fontconfig><include>{fragment}</include></fontconfig>\n",
                encoding="utf-8",
            )
            runtime = FontconfigRuntime(root / "runtime", {"core": prefix})
            with self.assertRaisesRegex(OncoTracerError, "unsafe guard parent"):
                runtime.environment("core")

    def test_real_fontconfig_and_matplotlib_do_not_touch_prefix_cache(self) -> None:
        fc_cache = shutil.which("fc-cache")
        if fc_cache is None or not Path("/etc/fonts/fonts.conf").is_file():
            self.skipTest("system Fontconfig is unavailable")
        import_probe = subprocess.run(
            [sys.executable, "-c", "import matplotlib"],
            capture_output=True,
            text=True,
            check=False,
        )
        if import_probe.returncode != 0:
            self.skipTest("test interpreter lacks Matplotlib")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "external-prefix"
            shutil.copytree("/etc/fonts", prefix / "etc" / "fonts", symlinks=True)
            unsafe_cache = prefix / "var" / "cache" / "fontconfig"
            unsafe_cache.mkdir(parents=True)
            (unsafe_cache / "sentinel").write_bytes(b"do not mutate")
            source = prefix / "etc" / "fonts" / "fonts.conf"
            original = source.read_text(encoding="utf-8")
            replaced = original.replace(">/var/cache/fontconfig<", f">{unsafe_cache}<")
            self.assertNotEqual(original, replaced)
            source.write_text(replaced, encoding="utf-8")
            before = _snapshot(unsafe_cache)
            toolchain = Toolchain(
                core_prefix=prefix,
                classifier_prefix=prefix,
                runtime_cache=root / "runtime",
            )
            runner = CommandRunner(
                root / "trace.tsv",
                echo=False,
                protected_environment=toolchain.environment("core"),
                validators=(toolchain.validate_environment,),
            )
            for attempt in range(2):
                runner.run(f"fc-cache-{attempt}", [fc_cache, "-f"])
                runner.run(
                    f"matplotlib-{attempt}",
                    [
                        sys.executable,
                        "-c",
                        "import matplotlib; matplotlib.use('Agg'); "
                        "from matplotlib import pyplot as p; "
                        "p.plot([0,1]); p.savefig('plot.pdf'); p.close()",
                    ],
                    cwd=root,
                    containment=toolchain.environment("classifier"),
                )
            toolchain.validate_environment()
            self.assertEqual(_snapshot(unsafe_cache), before)
            runtime_cache = root / "runtime" / "fontconfig-cache"
            self.assertTrue(any(path.is_file() for path in runtime_cache.rglob("*")))


if __name__ == "__main__":
    unittest.main()
