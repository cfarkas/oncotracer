"""Firefox click-through of resource selection; real config APIs, stubbed Run only.

Requires playwright and its Firefox browser. No sequencing analysis is launched.
"""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from oncotracer_cli.cli import parse_arguments
from oncotracer_cli.runtime import load_flat_yaml
from oncotracer_cli.web import WebServer, WebState
from tests.test_web import HARDWARE


class SampleJobBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reads = self.root / 'reads'
        self.reads.mkdir()
        for index in "ABCDEF":
            (self.reads / f'SAMPLE_{index}.fastq').write_text('@read\nACGT\n+\nIIII\n')
        self.hardware = {**HARDWARE, 'cpu_workers_available': 40,
                         'ram_available_bytes': 256 * 1024**3, 'ram_total_bytes': 256 * 1024**3}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('oncotracer_cli.cli._load_install_config', return_value={}))
        self.stack.enter_context(patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=self.hardware))
        args = parse_arguments(['setup', '--mode', 'illumina', '--backend', 'host',
                                '--input-folder', str(self.reads), '--project', str(self.root/'project')])
        self.state = WebState(self.root, args)
        self.state.hardware = self.hardware
        self.runs = []
        def record_run(data):
            reviewed = self.state.projects[data['project_id']]
            self.assertTrue(reviewed['valid'])
            self.runs.append(load_flat_yaml(Path(reviewed['config_path'])))
            return {'test_run_endpoint_reached': True}
        self.state.run = record_run
        self.server = WebServer(0, self.state)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.playwright = self.stack.enter_context(sync_playwright())
        options = {'headless': True}
        if os.environ.get('ONCOTRACER_TEST_FIREFOX'):
            options['executable_path'] = os.environ['ONCOTRACER_TEST_FIREFOX']
        self.browser = self.playwright.firefox.launch(**options)
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1000})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.origin + '/#' + self.state.token)
        self.page.wait_for_selector('main:not([inert])')

    def new_form(self):
        self.page.locator('#choose-new-analysis').click()
        self.page.locator('#variant-fresh').click()
        self.page.locator('#input-folder').fill(str(self.reads))
        self.page.locator('#scan').click()
        self.page.wait_for_selector('#samples-card:not([hidden])')
        for field in self.page.locator('.type-select').all():
            field.select_option('cancer')
        self.page.locator('#threads').fill('40')
        self.page.locator('#threads').press('Tab')
        expect(self.page.locator('#sample-parallel')).to_contain_text('5 sample jobs × 8 cores each')
        expect(self.page.locator('#sample-parallel')).to_contain_text('4 sample jobs × 10 cores each')
        self.page.locator('#sample-parallel').select_option('4')
        self.page.locator('#project-mode').select_option('new')
        self.page.locator('#project-parent').fill(str(self.root))
        self.page.locator('#project-name').fill('project')

    def save_fixture(self):
        scan = self.state.scan({'mode': 'illumina', 'folder': str(self.reads)})
        with contextlib.redirect_stdout(io.StringIO()):
            self.state.prepare({'scan_id': scan['scan_id'], 'project': str(self.root/'project'),
                'threads': 40, 'backend': 'host', 'samples': [
                    {'id': sample['id'], 'name': sample['name'], 'type': 'cancer'} for sample in scan['samples']],
                'resources_selection': {'threads': 40, 'parallel_samples': 1, 'memory': '160GB'}})
        return (self.root/'project/config/samplesheet.csv').read_text()

    def assert_ready_resume(self):
        expect(self.page.locator('#review-card #resume-resources')).to_be_visible()
        expect(self.page.locator('#run')).to_be_enabled()
        expect(self.page.locator('#resume-parallel')).to_have_value('1')
        expect(self.page.locator('#resume-parallel')).to_contain_text('5 sample jobs × 8 cores each')
        self.page.locator('#resume-parallel').select_option('5')
        # Changing this field used to hide the whole review and stall Run.
        expect(self.page.locator('#review-card #resume-resources')).to_be_visible()
        expect(self.page.locator('#run')).to_be_enabled()

    def test_partial_completion_is_visible_at_the_end(self):
        from oncotracer_cli.completion import summarize_completion
        from oncotracer_cli.web_progress import progress_for_job
        from tests.test_completion_counts import partial_summary
        job = {'status': 'partial_failure', 'project_id': 'synthetic-partial',
               'project_path': str(self.root/'project'), 'log_path': '/synthetic/run.log',
               'log': 'Retained completed work', 'exit_code': 2,
               'completion': summarize_completion(partial_summary())}
        job['progress'] = progress_for_job(job, job['log'])
        self.page.route('**/api/status', lambda route: route.fulfill(json=job))
        self.page.reload()
        expect(self.page.locator('#job-status')).to_have_text('Analysis incomplete · exit 2')
        expect(self.page.locator('#run-eta-note')).to_contain_text('Samples: completed 6/8; incomplete 2/8.')
        expect(self.page.locator('#run-eta-note')).to_contain_text('Small variants: completed 6/8; incomplete 2/8.')
        expect(self.page.locator('#run')).to_be_hidden()
        self.page.goto(self.server.origin + '/variants/#' + self.state.token)
        expect(self.page.locator('#job-note')).to_contain_text('Samples: completed 6/8; incomplete 2/8.')
        expect(self.page.locator('#job-note')).to_contain_text('Small variants: completed 6/8; incomplete 2/8.')
        self.assertFalse(self.errors, self.errors)

    def test_new_analysis_saves_sample_job_selection_and_reaches_run(self):
        self.new_form()
        self.page.locator('#prepare').click()
        expect(self.page.locator('#run')).to_be_enabled(timeout=30000)
        with self.page.expect_response(lambda response: response.url.endswith('/api/run')) as dispatched:
            self.page.locator('#run').click()
        self.assertEqual(dispatched.value.status, 200)
        expect(self.page.locator('main')).not_to_have_attribute('inert', '')
        expect(self.page.locator('#run')).to_be_hidden(timeout=30000)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual((self.runs[0]['threads'], self.runs[0]['parallel_samples']), (40, 4))
        self.assertFalse(self.errors, self.errors)
        self.assertFalse((self.root/'project/results').exists())

    def test_continue_run_applies_choices_and_recovers_from_bad_memory(self):
        before = self.save_fixture()
        self.page.locator('#choose-existing-analysis').click()
        self.page.locator('#resume-project').fill(str(self.root/'project'))
        self.page.locator('#resume-project-button').click()
        expect(self.page.locator('#resume-resources')).to_be_visible(timeout=30000)
        self.assert_ready_resume()
        self.page.locator('#resume-memory').fill('invalid')
        self.page.locator('#resume-memory').press('Tab')
        expect(self.page.locator('#resume-resource-note')).to_contain_text('memory must')
        expect(self.page.locator('#run')).to_be_disabled()
        self.assertEqual(self.runs, [])
        self.page.locator('#resume-memory').fill('160GB')
        self.page.locator('#resume-memory').press('Tab')
        expect(self.page.locator('#run')).to_be_enabled()
        # Run applies resources itself; no hidden mandatory Apply step.
        with self.page.expect_response(lambda response: response.url.endswith('/api/run')) as dispatched:
            self.page.locator('#run').click()
        self.assertEqual(dispatched.value.status, 200)
        expect(self.page.locator('main')).not_to_have_attribute('inert', '')
        expect(self.page.locator('#run')).to_be_hidden(timeout=30000)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual((self.runs[0]['threads'], self.runs[0]['parallel_samples']), (40, 5))
        self.assertEqual(before, (self.root/'project/config/samplesheet.csv').read_text())
        self.assertFalse(self.errors, self.errors)

    def test_manual_new_analysis_survives_redetection_and_saves_beyond_fit(self):
        self.new_form()
        self.page.locator('#sample-parallel').select_option('manual')
        self.page.locator('#sample-manual-parallel').fill('6')
        self.page.locator('#sample-memory').fill('16GB')
        self.page.locator('#sample-memory').press('Tab')
        expect(self.page.locator('#sample-manual-note')).to_contain_text('Exceeds the suggested fit')
        self.page.locator('#sample-detect-resources').click()
        expect(self.page.locator('#sample-parallel')).to_have_value('manual')
        expect(self.page.locator('#sample-manual-parallel')).to_have_value('6')
        self.page.locator('#prepare').click()
        expect(self.page.locator('#run')).to_be_enabled(timeout=30000)
        with self.page.expect_response(lambda response: response.url.endswith('/api/run')):
            self.page.locator('#run').click()
        self.assertEqual(self.runs[0]['parallel_samples'], 6)
        self.assertEqual(self.runs[0]['analysis_memory'], '16GB')
        self.assertFalse(self.errors, self.errors)

    def test_manual_resume_with_no_recommended_fit_is_not_stalled(self):
        before = self.save_fixture()
        self.page.locator('#choose-existing-analysis').click()
        self.page.locator('#resume-project').fill(str(self.root/'project'))
        self.page.locator('#resume-project-button').click()
        expect(self.page.locator('#resume-resources')).to_be_visible(timeout=30000)
        self.page.locator('#resume-parallel').select_option('manual')
        self.page.locator('#resume-manual-parallel').fill('6')
        self.page.locator('#resume-memory').fill('4GB')
        self.page.locator('#resume-memory').press('Tab')
        expect(self.page.locator('#resume-resource-note')).to_contain_text('at least 8')
        expect(self.page.locator('#resume-manual-note')).to_contain_text('Exceeds the suggested fit')
        expect(self.page.locator('#run')).to_be_enabled()
        self.page.locator('#resume-apply-resources').click()
        expect(self.page.locator('#resume-manual-parallel')).to_have_value('6')
        expect(self.page.locator('#resume-parallel')).to_have_value('manual')
        expect(self.page.locator('#run')).to_be_enabled(timeout=30000)
        self.page.locator('#resume-manual-parallel').fill('0')
        self.page.locator('#resume-manual-parallel').press('Tab')
        expect(self.page.locator('#run')).to_be_disabled()
        self.page.locator('#resume-manual-parallel').fill('6')
        self.page.locator('#resume-manual-parallel').press('Tab')
        with self.page.expect_response(lambda response: response.url.endswith('/api/run')):
            self.page.locator('#run').click()
        self.assertEqual(self.runs[0]['parallel_samples'], 6)
        self.assertEqual(before, (self.root/'project/config/samplesheet.csv').read_text())
        self.assertFalse(self.errors, self.errors)

    def test_both_new_analysis_paths_to_existing_project_show_resources(self):
        self.save_fixture()
        for destination in ('existing', 'new'):
            with self.subTest(destination=destination):
                self.new_form()
                self.page.locator('#project-mode').select_option(destination)
                if destination == 'existing':
                    self.page.locator('#existing-project').fill(str(self.root/'project'))
                # The fixture contains saved settings, with simulated recorded outputs.
                with patch('oncotracer_cli.project_resume.has_recorded_outputs', return_value=True):
                    self.page.locator('#prepare').click()
                    expect(self.page.locator('#resume-resources')).to_be_visible(timeout=30000)
                    self.assert_ready_resume()
                self.assertFalse(self.errors, self.errors)
        self.page.locator('#resume-apply-resources').click()
        expect(self.page.locator('#run')).to_be_enabled(timeout=30000)
        saved = load_flat_yaml(self.root/'project/config/run.yml')
        self.assertEqual(saved['parallel_samples'], 5)
        self.assertEqual(self.runs, [])


if __name__ == '__main__':
    unittest.main()
