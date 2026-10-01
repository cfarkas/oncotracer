"""Exercise folder navigation in the real app and generated demo, without analysis.

Run: python tests/browser_folder_picker_smoke.py --output /tmp/folder-picker-check
Requires Playwright with Chromium and Firefox installed. Uses synthetic files;
the native directory chooser is stubbed, and the directory-input fallback uses
real temporary folders. Screenshots and a JSON report go to --output.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oncotracer_cli.web import WebServer, WebState


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reads = output / 'reads'
    reads.mkdir()
    (reads / 'sample_R1.fastq').write_text('@read\nACGT\n+\nIIII\n')
    (reads / 'sample_R2.fastq').write_text('@read\nACGT\n+\nIIII\n')
    nested = reads / 'barcode01'
    nested.mkdir()
    (nested / 'batch.fq.gz').write_bytes(b'filename-only fixture')
    for name in ('batch.POD5', 'calls.BAM'):
        (reads / name).write_bytes(b'filename-only fixture')
    documents = output / 'documents'
    documents.mkdir()
    (documents / 'notes.txt').write_text('No sequencing files here.')
    empty = output / 'empty'
    empty.mkdir()
    state = WebState(output)
    app = WebServer(0, state)
    demo = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'docs')))
    servers = (app, demo)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {}
    try:
        with sync_playwright() as playwright:
            for name in ('chromium', 'firefox'):
                browser = getattr(playwright, name).launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1100, 'height': 1050})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def open_inputs():
                        page.locator('#choose-illumina').click()
                        page.locator('#variant-fresh').click()
                        page.locator('[data-browse="input-folder"]').click()
                        expect(page.locator('#browser')).to_be_visible()

                    def navigate(path):
                        page.locator('#browser-location').fill(str(path))
                        page.locator('#browser-go').click()
                        expect(page.locator('#browser-path')).to_have_text(str(path))

                    # Real server: file types, empty folders, nested paths and recovery.
                    page.goto(app.origin + '/#' + state.token)
                    open_inputs()
                    navigate(empty)
                    expect(page.locator('#browser-error')).to_have_text('No sequencing files found. Check your paths.')
                    expect(page.locator('#use-folder')).to_be_disabled()
                    navigate(documents)
                    expect(page.locator('#browser-error')).to_be_visible()
                    navigate(reads)
                    expect(page.locator('#browser-error')).to_be_hidden()
                    expect(page.locator('#folder-summary')).to_contain_text('2 FASTQ, 1 POD5, 1 BAM')
                    expect(page.locator('#use-folder')).to_be_enabled()
                    page.locator('#browser-computer').click()
                    expect(page.locator('#browser-path')).to_have_text('/')
                    navigate(output)
                    expect(page.locator('#use-folder')).to_be_enabled()  # Recursive discovery is allowed.
                    navigate(reads)
                    page.locator('#browser-location').fill(str(output / 'missing'))
                    page.locator('#browser-go').click()
                    expect(page.locator('#browser-error')).to_contain_text('does not exist')
                    expect(page.locator('#folders .fastq-file')).to_have_count(0)
                    expect(page.locator('#use-folder')).to_be_disabled()

                    page.goto(f'http://127.0.0.1:{demo.server_port}/assets/setup-demo/index.html')
                    requests = []
                    page.on('request', lambda request: requests.append(request.url))
                    open_inputs()
                    navigate('/demo/projects')
                    expect(page.locator('#browser-error')).to_have_text('No sequencing files found. Check your paths.')
                    expect(page.locator('#use-folder')).to_be_disabled()
                    assert page.evaluate("async()=>{try{await api('/api/scan',{mode:'illumina',folder:'/demo/projects'});return false;}catch(e){return e.message.includes('No sequencing files found. Check your paths.');}}")
                    # The synthetic inventory must respect barcode selection.
                    assert page.evaluate("async()=>(await api('/api/scan',{mode:'ont',folder:'/demo/nanopore/fastq_pass/barcode02'})).samples.map(s=>s.barcode)") == ['barcode02']

                    # Native picker: only entry names are used; accessing file bodies fails.
                    page.evaluate("""() => {
                      const file=name=>({name,kind:'file',getFile(){throw Error('File contents must not be read');}});
                      const directory=(name,entries)=>({name,kind:'directory',async *values(){yield* entries;}});
                      window.showDirectoryPicker=async options=>{
                        if(options.mode!=='read')throw Error('Read-only access required');
                        return directory('selected',[file('A.FASTQ.GZ'),file('B.fq'),file('reads.pod5'),file('calls.bam'),
                          directory('empty',[]),directory('barcode01',[file('batch.fastq')])]);
                      };
                    }""")
                    page.locator('#browser-computer').click()
                    expect(page.locator('#browser-path')).to_have_text('/computer/selected')
                    expect(page.locator('#folder-summary')).to_contain_text('2 FASTQ, 1 POD5, 1 BAM')
                    expect(page.locator('#browser-error')).to_be_hidden()
                    expect(page.locator('#use-folder')).to_be_disabled()
                    page.locator('#folders button').filter(has_text='empty').click()
                    expect(page.locator('#browser-error')).to_have_text('No sequencing files found. Check your paths.')
                    page.locator('#browser-up').click()
                    page.locator('#folders button').filter(has_text='barcode01').click()
                    expect(page.locator('#folder-summary')).to_contain_text('1 FASTQ')
                    expect(page.locator('#browser-error')).to_be_hidden()
                    previous = page.locator('#browser-path').inner_text()
                    page.evaluate("() => {window.showDirectoryPicker=async()=>{throw new DOMException('Cancelled','AbortError');};}")
                    page.locator('#browser-computer').click()
                    expect(page.locator('#browser-path')).to_have_text(previous)
                    expect(page.locator('#browser-error')).to_be_hidden()

                    # Actual browser folder input: nested files, no matching files, empty folder.
                    page.evaluate('window.showDirectoryPicker=undefined')
                    with page.expect_file_chooser() as chooser:
                        page.locator('#browser-computer').click()
                    chooser.value.set_files(str(reads))
                    expect(page.locator('#browser-path')).to_have_text('/computer/reads')
                    expect(page.locator('#folder-summary')).to_contain_text('2 FASTQ, 1 POD5, 1 BAM')
                    page.locator('#folders button').filter(has_text='barcode01').click()
                    expect(page.locator('#folder-summary')).to_contain_text('1 FASTQ')
                    page.locator('#browser-computer-input').set_input_files(str(documents))
                    expect(page.locator('#browser-error')).to_have_text('No sequencing files found. Check your paths.')
                    expect(page.locator('#use-folder')).to_be_disabled()
                    page.screenshot(path=str(output / f'{name}-no-sequencing.png'))
                    page.locator('#browser-computer-input').set_input_files(str(empty))
                    expect(page.locator('#browser-error')).to_have_text('No sequencing files found. Check your paths.')
                    expect(page.locator('#folders .fastq-file')).to_have_count(0)
                    navigate('/demo/illumina/fastq')
                    expect(page.locator('#browser-error')).to_be_hidden()
                    expect(page.locator('#use-folder')).to_be_enabled()
                    page.screenshot(path=str(output / f'{name}-picker.png'))
                    page.locator('#use-folder').click()
                    expect(page.locator('#fastq-summary')).to_contain_text('3 samples · 6 FASTQ files')
                    assert not requests, requests  # The demo's CSP also blocks network connections.
                    assert not errors, errors
                    report[name] = {'passed': True, 'no_network_requests': True, 'page_errors': errors}
                finally:
                    browser.close()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
