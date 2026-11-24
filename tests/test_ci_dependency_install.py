#!/usr/bin/env python3
"""Exercise CI dependency failures without modifying the host package manager."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/native-v2-ci.yml'


class DependencyInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.job = yaml.safe_load(WORKFLOW.read_text())['jobs']['source-and-binary']
        self.steps = {step.get('name'): step for step in self.job['steps']}
        self.install = self.steps['Install Ubuntu test dependencies']['run']
        self.write_command('sudo', '''#!/bin/bash
set -eu
[[ "$1" == -n ]] && shift
if [[ "$1" == tee && "$2" == /etc/apt/apt.conf.d/99oncotracer-ci ]]; then
  cat > "$CI_INSTALL_TEST_ROOT/apt.conf"
else
  exec "$@"
fi
''')
        self.write_command('apt-get', '''#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$CI_INSTALL_TEST_ROOT/apt.calls"
if [[ "$1" == update ]]; then
  if [[ "${CI_INSTALL_TEST_STALL:-0}" == 1 ]]; then exec sleep 10; fi
  exit "${CI_INSTALL_TEST_UPDATE_EXIT:-0}"
fi
[[ "${DEBIAN_FRONTEND:-}" == noninteractive ]] || exit 93
exit "${CI_INSTALL_TEST_INSTALL_EXIT:-0}"
''')

    def write_command(self, name: str, script: str) -> None:
        path = self.bin / name
        path.write_text(script)
        path.chmod(0o755)

    def run_install(self, script: str | None = None, **environment: str):
        return subprocess.run(
            ['bash', '-c', self.install if script is None else script],
            env={
                **os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                'RUNNER_TEMP': str(self.root), 'CI_INSTALL_TEST_ROOT': str(self.root),
                **environment,
            }, capture_output=True, text=True, timeout=5, check=False,
        )

    def calls(self) -> list[str]:
        return (self.root / 'apt.calls').read_text().splitlines()

    def test_signed_https_repositories_and_noninteractive_install(self) -> None:
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            'update',
            'install -y --no-install-recommends r-base-core parallel bcftools samtools',
        ])
        sources = (self.root / 'oncotracer-ubuntu.list').read_text().splitlines()
        self.assertEqual(len(sources), 3)
        for line in sources:
            self.assertIn('signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg', line)
            self.assertIn('https://', line)
            self.assertNotIn('mirror+', line)
        config = (self.root / 'apt.conf').read_text()
        self.assertIn(f'Dir::Etc::sourcelist "{self.root}/oncotracer-ubuntu.list";', config)
        self.assertIn('APT::Update::Error-Mode "any";', config)
        self.assertIn('Acquire::https::Timeout "30";', config)

    def test_failed_update_never_installs_from_stale_indexes(self) -> None:
        result = self.run_install(CI_INSTALL_TEST_UPDATE_EXIT='100')
        self.assertEqual(result.returncode, 100, result.stderr)
        self.assertEqual(self.calls(), ['update'])

    def test_failed_package_install_is_not_ignored(self) -> None:
        result = self.run_install(CI_INSTALL_TEST_INSTALL_EXIT='100')
        self.assertEqual(result.returncode, 100, result.stderr)

    def test_stalled_update_is_terminated_before_install(self) -> None:
        # Run the actual timeout command with a short test deadline.
        script = self.install.replace('8m apt-get update', '0.1s apt-get update')
        self.assertNotEqual(script, self.install)
        result = self.run_install(script, CI_INSTALL_TEST_STALL='1')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(self.calls(), ['update'])

    def test_network_steps_have_budgets_below_the_job_limit(self) -> None:
        self.assertEqual(self.job['timeout-minutes'], 120)
        for name in (
            'Install Ubuntu test dependencies', 'Install Python test dependencies',
            'Test resource choices and Run dispatch in Firefox',
            'Verify Poetry launcher installation',
            'Verify GPU controls in Firefox without starting analysis',
            'Build documentation strictly',
        ):
            with self.subTest(step=name):
                self.assertLessEqual(self.steps[name]['timeout-minutes'], 25)
        self.assertEqual(self.job['env']['PIP_DEFAULT_TIMEOUT'], '60')
        self.assertEqual(self.job['env']['PIP_RETRIES'], '3')


if __name__ == '__main__':
    unittest.main()
