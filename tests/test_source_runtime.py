"""Child interpreters use the selected release even inside an older checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from oncotracer_cli import source_runtime, web

ROOT = Path(__file__).resolve().parents[1]
PROBE = """import json, os, sys
import oncotracer_cli
print(json.dumps({'package':oncotracer_cli.__file__, 'cwd':os.getcwd(), 'argv':sys.argv[1:]}))
"""


class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.launch = self.root / 'older checkout'
        shadow = self.launch / 'oncotracer_cli'
        shadow.mkdir(parents=True)
        (shadow / '__init__.py').write_text("raise RuntimeError('WRONG_OLD_CHECKOUT')\n")
        self.env = {**os.environ, 'PYTHONPATH':str(self.launch), 'PYTHONDONTWRITEBYTECODE':'1'}

    def invoke(self, command):
        result = subprocess.run(command, cwd=self.launch, env=self.env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def package(self, root):
        package = root / 'oncotracer_cli'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'probe.py').write_text(PROBE)
        return package

    def test_code_uses_current_source_over_shadow_cwd_and_pythonpath(self):
        command = source_runtime.python_code(PROBE)
        self.assertEqual(command[:2], [sys.executable, '-c'])
        result = self.invoke(command + ['argument with spaces', 'literal;$VALUE'])
        self.assertEqual(Path(result['package']), ROOT / 'oncotracer_cli/__init__.py')
        self.assertEqual(result['cwd'], str(self.launch))
        self.assertEqual(result['argv'], ['argument with spaces', 'literal;$VALUE'])

    def test_module_pins_selected_source_without_changing_arguments_or_cwd(self):
        source = self.root / 'selected release'
        self.package(source)
        arguments = ['--config', 'relative/config with spaces.yml', '--parallel-samples', '4']
        result = self.invoke(source_runtime.python_module('oncotracer_cli.probe', source=source) + arguments)
        self.assertEqual(Path(result['package']), source / 'oncotracer_cli/__init__.py')
        self.assertEqual(result['argv'], arguments)
        self.assertEqual(result['cwd'], str(self.launch))

    def test_source_path_and_arguments_with_code_characters_remain_literal(self):
        source = self.root / "release ' \\ \n $(touch UNWANTED) `touch UNWANTED`"
        self.package(source)
        arguments = ["'; raise RuntimeError('injection'); #", '$(touch UNWANTED)', '`touch UNWANTED`']
        for command in (source_runtime.python_code(PROBE, source=source),
                        source_runtime.python_module('oncotracer_cli.probe', source=source)):
            with self.subTest(command=command[2]):
                result = self.invoke(command + arguments)
                self.assertEqual(Path(result['package']), source / 'oncotracer_cli/__init__.py')
                self.assertEqual(result['argv'], arguments)
                self.assertFalse((self.launch / 'UNWANTED').exists())

    def test_code_supports_multiline_body(self):
        code = "if True:\n    " + PROBE.replace('\n', '\n    ').rstrip() + '\n'
        result = self.invoke(source_runtime.python_code(code))
        self.assertEqual(Path(result['package']), ROOT / 'oncotracer_cli/__init__.py')

    def test_browser_wheel_fallback_stays_on_installed_source(self):
        wheel_root = self.root / 'wheel site-packages'
        package = self.package(wheel_root)
        (package / 'cli.py').write_text(PROBE)
        with patch.object(web, '__file__', str(package / 'web.py')), \
             patch.object(source_runtime, '__file__', str(package / 'source_runtime.py')):
            command = web._launcher()
        result = self.invoke(command + ['setup', '--port', '8888'])
        self.assertEqual(Path(result['package']), package / '__init__.py')
        self.assertEqual(result['argv'], ['setup', '--port', '8888'])
        self.assertEqual(result['cwd'], str(self.launch))

    def test_zipapp_helper_keeps_children_on_original_archive(self):
        archive = self.root / "release ' package.pyz"
        entry = """import json, subprocess, sys
from oncotracer_cli.source_runtime import python_code, python_module
commands = [python_code("import json, oncotracer_cli; print(json.dumps({'package':oncotracer_cli.__file__}))"),
            python_module('oncotracer_cli.probe') + ['literal argument']]
results = []
for command in commands:
    child = subprocess.run(command, text=True, capture_output=True)
    if child.returncode:
        raise RuntimeError(child.stderr)
    results.append(json.loads(child.stdout))
print(json.dumps(results))
"""
        with zipfile.ZipFile(archive, 'w') as release:
            release.writestr('__main__.py', entry)
            release.writestr('oncotracer_cli/__init__.py', '')
            release.writestr('oncotracer_cli/probe.py', PROBE)
            release.writestr('oncotracer_cli/source_runtime.py', Path(source_runtime.__file__).read_text())
        results = self.invoke([sys.executable, str(archive)])
        for result in results:
            self.assertEqual(result['package'], str(archive / 'oncotracer_cli/__init__.py'))
        self.assertEqual(results[1]['argv'], ['literal argument'])
        self.assertEqual(results[1]['cwd'], str(self.launch))


if __name__ == '__main__':
    unittest.main()
