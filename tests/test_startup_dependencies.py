"""Startup inventory is read-only; only an approved, server-owned plan installs."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from oncotracer_cli import startup_dependencies as dependency
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.web import WebState

DIGEST = 'sha256:' + 'a' * 64
RELEASE = {'status': 'checked', 'version': '6.2.0', 'digest': DIGEST}


def ready_report(data=None, **kwargs):
    chosen = {'backend': 'host', 'image': 'test/image:1', **(data or {})}
    return {'schema': 'oncotracer-setup-dependencies-v1', 'launcher_version': '6.2.0',
            'selection': chosen, 'release': RELEASE, 'ready': True, 'tools': [],
            'environments': [{'name': 'core', 'status': 'verified'}], 'actions': [], 'warnings': [], 'image': {}}


class DependencyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('oncotracer_cli.cli._load_install_config', return_value={}))
        self.stack.enter_context(patch('oncotracer_cli.cli._configured_native_prefixes', return_value={}))
        self.stack.enter_context(patch('oncotracer_cli.variant_resources._Search.prefixes', return_value=[]))
        self.stack.enter_context(patch.object(dependency, '_executable', side_effect=lambda name: '/test/' + name if name in ('docker', 'conda') else None))
        self.stack.enter_context(patch.object(dependency, 'latest_release', return_value=RELEASE))
        self.health = {'success': True, 'environments': {'core': {'success': True}}}
        self.present = True; self.version = '6.2.0'; self.digests = ['test/image@' + DIGEST]
        self.commands = []
        self.stack.enter_context(patch.object(dependency, 'probe', side_effect=self.probe))

    def probe(self, command, **kwargs):
        self.commands.append(command)
        if command[1] == 'info': result = '29.2.0'
        elif command[1:3] == ['image', 'inspect']:
            if not self.present: return {'returncode': 1, 'stdout': '', 'stderr': 'image not found'}
            result = {'Config': {'Labels': {'org.opencontainers.image.version': self.version}}, 'RepoDigests': self.digests}
        else: result = self.health
        return {'returncode': 0, 'stdout': json.dumps(result), 'stderr': ''}

    def test_current_image_is_checked_without_pulling_or_installing(self):
        report = dependency.inspect_dependencies({'backend': 'docker', 'image': 'carlosfarkas/oncotracer:6.2.0'})
        self.assertTrue(report['ready']); self.assertEqual(report['actions'], [])
        self.assertEqual(report['image']['update_status'], 'current')
        self.assertIn('--pull=never', self.commands[-1]); self.assertIn('--network', self.commands[-1])
        self.assertFalse(any('pull' in cmd or 'install' in cmd for cmd in self.commands))

    def test_missing_image_offers_published_immutable_image(self):
        self.present = False
        report = dependency.inspect_dependencies({'backend': 'docker', 'image': 'carlosfarkas/oncotracer:6.2.0'})
        self.assertFalse(report['ready']); self.assertNotIn('run', [cmd[1] for cmd in self.commands])
        self.assertEqual(report['actions'][0]['image'], 'carlosfarkas/oncotracer:6.2.0@' + DIGEST)

    def test_broken_custom_image_has_explicit_official_alternative(self):
        self.health = {'success': False, 'environments': {'core': {'success': False, 'error': 'samtools missing'}}}
        report = dependency.inspect_dependencies({'backend': 'docker', 'image': 'local/removed-samtools:test'})
        self.assertFalse(report['ready']); self.assertEqual(report['environments'][0]['status'], 'missing')
        self.assertEqual(report['actions'][0]['image'], 'local/removed-samtools:test')
        self.assertEqual(report['actions'][1]['id'], 'official-docker')
        self.assertIn(DIGEST, report['actions'][1]['image'])

    def test_report_names_failed_tools(self):
        self.health = {'success': False, 'environments': {'core': {'success': False,
                       'probes': {'bwa': {'success': False}, 'samtools': {'success': True}}}}}
        report = dependency.inspect_dependencies({'backend': 'docker', 'image': 'local/broken:test'})
        self.assertEqual(report['environments'][0]['message'], 'Failed checks: bwa')

    def test_pinned_image_and_newer_version_are_not_silently_replaced(self):
        for image in ('carlosfarkas/oncotracer:6.1.0@sha256:' + 'b' * 64, 'custom/image:test', 'carlosfarkas/oncotracer:9.0.0'):
            self.version = image.split(':')[-1]; self.digests = []
            report = dependency.inspect_dependencies({'backend': 'docker', 'image': image})
            self.assertEqual(report['actions'], [], report)

    def test_offline_check_uses_local_image(self):
        with patch.object(dependency, 'latest_release', return_value={'status': 'unavailable', 'message': 'network unreachable'}):
            report = dependency.inspect_dependencies({'backend': 'docker', 'image': 'local/working:test'})
        self.assertTrue(report['ready']); self.assertIn('offline', report['warnings'][-1])
        self.assertEqual(report['actions'], [])

    def test_source_failure_does_not_request_reinstall_of_healthy_environments(self):
        self.health.update(success=False, source={'success': False})
        report = dependency.inspect_dependencies({'backend': 'conda'})
        self.assertFalse(report['ready']); self.assertEqual(report['actions'], [])
        self.assertTrue(any('source identity' in warning for warning in report['warnings']))

    def test_invalid_selection_cannot_inject_commands(self):
        for data in ({'backend': 'shell'}, {'image': '--privileged'}, {'image': 'x;id'}, {'image': []}, {'command': ['touch', 'x']}):
            with self.assertRaises(OncoTracerError): dependency.selection(data)
        self.assertEqual(self.commands, [])


class ReleaseMetadataTests(unittest.TestCase):
    def test_official_asset_and_digest_are_required(self):
        asset_url = 'https://github.com/cfarkas/oncotracer/releases/download/v6.2.0/release-provenance.json'
        provenance = {'version': '6.2.0', 'container': {'digest': DIGEST}}
        asset = {'name': 'release-provenance.json', 'browser_download_url': asset_url}
        for change, expected in (({}, 'checked'), ({'browser_download_url': 'https://example.test/file'}, 'unavailable'), ({'digest': 'sha256:' + '0'*64}, 'unavailable')):
            release = {'tag_name': 'v6.2.0', 'assets': [{**asset, **change}]}
            with patch.object(dependency, '_RELEASE_CACHE', None), patch.object(dependency, '_json_url', side_effect=[(release, b'{}'), (provenance, b'{}')]):
                self.assertEqual(dependency.latest_release()['status'], expected)


class ApprovedInstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'installer.py'
        self.script.write_text("import pathlib,sys,time\nr=pathlib.Path(__file__).parent\n"
                               "print('installation test worker',flush=True)\n"
                               "if (r/'wait').exists(): time.sleep(30)\n"
                               "if (r/'fail').exists(): sys.exit(17)\n"
                               "(r/'installed').write_text('verified fixture')\n")
        self.service = dependency.DependencySetup(self.root, install_root=self.root / 'jobs')
        self.addCleanup(self.service.close)
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(dependency, 'launcher', return_value=[sys.executable, str(self.script)]))
        self.stack.enter_context(patch('oncotracer_cli.cli._load_install_config', return_value={}))
        self.stack.enter_context(patch.object(dependency, 'inspect_dependencies', side_effect=self.inventory))

    def inventory(self, data=None, **kwargs):
        report = ready_report(data)
        if not (self.root/'installed').exists():
            report.update(ready=False, actions=[{'id': 'docker', 'backend': 'docker', 'image': 'test/image@' + DIGEST,
                'label': 'Install verified test tools', 'reason': 'Test tools are missing.'}])
        return report

    def checked(self):
        self.service.inspect({'backend': 'docker', 'image': 'test/image:1'})
        self.service.check_thread.join(timeout=5)
        self.assertEqual(self.service.check_status()['status'], 'complete')
        return self.service.check_status()

    def planned(self):
        check = self.checked()
        return self.service.plan({'check_id': check['id'], 'action_id': 'docker', 'command': ['touch', 'unwanted']})

    def finished(self):
        self.service.worker.join(timeout=8)
        self.assertFalse(self.service.active())
        return self.service.status()

    def test_check_and_plan_never_install_and_confirmation_is_mandatory(self):
        plan = self.planned()
        self.assertFalse((self.root/'installed').exists()); self.assertFalse(Path(plan['directory']).exists())
        self.assertNotIn('touch', plan['steps'][0]['command'])
        for value in ({}, {'plan_id': plan['id']}, {'plan_id': plan['id'], 'confirmed': 'yes'}, {'plan_id': [], 'confirmed': True}):
            with self.assertRaises(OncoTracerError): self.service.start(value)
        self.service.start({'plan_id': plan['id'], 'confirmed': True, 'command': ['touch', 'unwanted']})
        result = self.finished(); self.assertEqual(result['status'], 'complete', result)
        self.assertTrue((self.root/'installed').exists()); self.assertFalse((self.root/'unwanted').exists())
        self.assertTrue(self.checked()['report']['ready'])

    def test_expired_or_rechecked_plan_cannot_install(self):
        plan = self.planned(); self.checked()
        with self.assertRaises(OncoTracerError): self.service.start({'plan_id': plan['id'], 'confirmed': True})
        plan = self.planned()
        with patch.object(dependency.time, 'time', return_value=plan['created_at'] + 901):
            with self.assertRaises(OncoTracerError): self.service.start({'plan_id': plan['id'], 'confirmed': True})

    def test_failed_install_is_visible_and_retry_uses_new_folder(self):
        (self.root/'fail').touch(); plan = self.planned()
        self.service.start({'plan_id': plan['id'], 'confirmed': True})
        result = self.finished(); self.assertEqual(result['status'], 'failed'); self.assertEqual(result['fields'], {})
        self.assertIn('exit 17', result['error']); (self.root/'fail').unlink()
        retry = self.planned(); self.assertNotEqual(retry['directory'], plan['directory'])
        self.service.start({'plan_id': retry['id'], 'confirmed': True}); self.assertEqual(self.finished()['status'], 'complete')

    def test_install_is_exclusive_with_analysis_and_other_installer(self):
        (self.root/'wait').touch(); plan = self.planned()
        state = WebState(self.root); state.dependency_setup = self.service
        state.job = {'status': 'running'}
        with self.assertRaisesRegex(OncoTracerError, 'current analysis'): state.dependency_install({'plan_id': plan['id'], 'confirmed': True})
        state.job = None; state.dependency_install({'plan_id': plan['id'], 'confirmed': True})
        for action in (state.prepare, state.variant_prepare, state.run, state.variant_install):
            with self.assertRaisesRegex(OncoTracerError, 'still running'): action({})
        self.service.stop({'job_id': plan['id']}); self.assertEqual(self.finished()['status'], 'cancelled')

    def test_terminal_decline_keeps_tools_and_yes_installs_then_rechecks(self):
        for answer, expected in (('no', False), ('yes', True)):
            args = argparse.Namespace(backend='docker', image='test/image:1')
            with patch.object(dependency, 'DependencySetup', return_value=self.service), patch('builtins.input', return_value=answer), contextlib.redirect_stdout(io.StringIO()) as output:
                dependency.terminal_setup(args)
            self.assertEqual((self.root/'installed').exists(), expected)
            if expected:
                self.assertIn('Updated analysis tools verified', output.getvalue())
                self.assertIn(DIGEST, args.image)
            with patch('builtins.input', side_effect=AssertionError('asked twice')):
                dependency.terminal_setup(args)


if __name__ == '__main__': unittest.main()
