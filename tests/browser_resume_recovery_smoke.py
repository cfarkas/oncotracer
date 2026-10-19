"""Optional Firefox regression: correct a bad image without starting analysis."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_web import WebTests
from oncotracer_cli.cli import DEFAULT_IMAGE
from oncotracer_cli.output_safety import _create_owner, current_runtime_identity
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml
from oncotracer_cli.web import WebServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--firefox', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright, expect
    fixture = WebTests()
    fixture.setUp()
    server = None
    try:
        fixture.fastq('reads/case_R1.fastq.gz')
        fixture.fastq('reads/case_R2.fastq.gz')
        fixture.prepare()
        project = fixture.root / 'project'
        (project / 'results').mkdir()
        identity = current_runtime_identity()
        _create_owner(project / 'results', identity)
        config = project / 'config/run.yml'
        original = config.read_bytes()
        fixture.state.setup_args = SimpleNamespace(backend='docker', image='oncotracer:typo')
        selected = []

        def runtime(backend, configuration):
            selected.append((backend, configuration.get('docker_image')))
            if configuration.get('docker_image') == 'oncotracer:typo':
                raise OncoTracerError('Docker image was not found. Correct Docker image or choose Use current image.')
            return identity

        server = WebServer(0, fixture.state)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(executable_path=args.firefox, headless=True)
            page = browser.new_page(viewport={'width': 1400, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/#' + fixture.state.token)
            page.wait_for_selector('main:not([inert])')
            expect(page.locator('#platform-card')).to_be_hidden()
            expect(page.locator('#resume-card')).to_be_hidden()
            expect(page.locator('#workflow')).to_be_hidden()
            page.screenshot(path=str(args.output / 'analysis-choice.png'), full_page=True)
            page.locator('#choose-new-analysis').click()
            expect(page.locator('#platform-card')).to_be_visible()
            expect(page.locator('#resume-card')).to_be_hidden()
            page.locator('#choose-existing-analysis').click()
            expect(page.locator('#platform-card')).to_be_hidden()
            expect(page.locator('#resume-card')).to_be_visible()
            expect(page.locator('#resume-image')).to_have_value('oncotracer:typo')
            with patch('oncotracer_cli.project_resume._runtime', side_effect=runtime), \
                 patch('oncotracer_cli.web.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{"errors":[]}', '')):
                page.locator('#resume-project').fill(str(fixture.root / 'reads'))
                page.locator('#resume-project-button').click()
                expect(page.locator('#error')).to_contain_text('analysis output project')
                assert selected == []
                page.locator('#resume-project').fill(str(project))
                expect(page.locator('#error')).to_be_hidden()
                page.locator('#resume-project-button').click()
                expect(page.locator('#error')).to_contain_text('Use current image')
                assert config.read_bytes() == original
                assert fixture.state.job is None
                page.locator('#resume-current-image').click()
                expect(page.locator('#resume-image')).to_have_value(DEFAULT_IMAGE)
                page.locator('#resume-project-button').click()
                expect(page.locator('#check-badge')).to_have_text('Resume checked')
                expect(page.locator('#run')).to_be_enabled()
                assert selected[-1] == ('docker', DEFAULT_IMAGE)
                assert load_flat_yaml(config)['docker_image'] == DEFAULT_IMAGE
                page.screenshot(path=str(args.output / 'corrected-image.png'), full_page=True)
                # Clearing the field must retain the saved image, not restore the CLI typo.
                page.locator('#resume-image').fill('')
                expect(page.locator('#run')).to_be_disabled()
                page.locator('#resume-project-button').click()
                expect(page.locator('#check-badge')).to_have_text('Resume checked')
                assert selected[-1] == ('docker', DEFAULT_IMAGE)
                assert fixture.state.job is None
                page.locator('#choose-new-analysis').click()
                expect(page.locator('#review-card')).to_be_hidden()
                expect(page.locator('#run')).to_be_disabled()
                expect(page.locator('#resume-card')).to_be_hidden()
                assert fixture.state.job is None
                assert not errors, errors
            browser.close()
        receipt = {'passed': True, 'raw_folder_rejected_before_docker': True,
                   'bad_tag_corrected_in_browser': True, 'cleared_override_uses_saved_image': True,
                   'edits_invalidate_previous_review': True, 'analysis_started': False}
        receipt['initial_choice_and_exclusive_workflows'] = True
        (args.output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
