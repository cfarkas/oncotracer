"""Real Firefox test of both Autodetect forms, with synthetic discovery responses.

Run: python tests/browser_variant_resources_smoke.py --output /shared/writable/path
No sequencing analysis, model load, installation or external request is performed.
"""
import argparse
import base64
import gzip
import json
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.browser_setup_smoke import free_port, http, wait
from tests.test_web import HARDWARE
from oncotracer_cli.runtime import render_flat_yaml
from oncotracer_cli.web import WebServer, WebState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    profiles = root / 'profiles'; profiles.mkdir()
    reads = root / 'reads'; reads.mkdir()
    for mate in (1, 2):
        with gzip.open(reads / f'SYNTHETIC_R{mate}.fastq.gz', 'wt') as handle:
            handle.write('@read\nACGT\n+\nIIII\n')
    (root / 'reference.fa').write_text('>chr1\nACGT\n')
    (root / 'sample.bam').write_text('Synthetic placeholder; never processed')
    (root / 'samples.tsv').write_text('sample\tbam\tstatus\nSYNTHETIC\tsample.bam\ttumor\n')
    config = root / 'variants.yml'
    config.write_text(render_flat_yaml({'mode': 'illumina', 'variant_reference': 'reference.fa',
        'variant_bam_manifest': 'samples.tsv', 'outdir': 'old-results', 'run_variants': True,
        'variant_specimen_type': 'ffpe', 'variant_callers': 'bcftools', 'variant_annovar': 'auto'}))
    state = WebState(root); state.hardware = HARDWARE
    requests = []
    def discovery(payload):
        requests.append(payload)
        docker = payload['backend'] == 'docker'
        return {'backend': payload['backend'],
            'fields': {'variant_annovar_dir': '/synthetic/annovar', 'variant_annovar_db': '/synthetic/humandb',
                       **({} if docker else {'variant_tool_prefix': '/synthetic/variant-env'})},
            'resources': [{'label': '<img src=x onerror="window.injected=true">', 'status': 'missing',
                           'detail': 'Synthetic missing resource', 'path': '/synthetic/<path>'}],
            'install_guides': [{'id': 'test', 'title': 'Install example', 'reason': 'Synthetic installation guidance',
                'commands': 'printf \'example only\\n\'\n# <script>window.injected=true</script>',
                'links': [{'label': 'Invalid scheme', 'url': 'javascript:window.injected=true'}]}],
            'notes': ['Synthetic response; no tools inspected.'], 'searched': ['/synthetic/tools']}
    state.variant_resources = discovery
    server = WebServer(0, state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = free_port(); log = (root / 'geckodriver.log').open('w')
    driver = subprocess.Popen(['/snap/bin/geckodriver', '--host', '127.0.0.1', '--port', str(port),
        '--profile-root', str(profiles), '--log', 'error'], stdout=log, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'; session = None; checks = []
    try:
        wait(lambda: http('GET', base+'/status'), 'WebDriver')
        created = http('POST', base+'/session', {'capabilities': {'alwaysMatch': {'browserName': 'firefox',
            'moz:firefoxOptions': {'args': ['-headless']}}}})
        session = base+'/session/'+created['value']['sessionId']
        def wd(method, path, data=None): return http(method, session+path, data).get('value')
        def js(script, *values): return wd('POST', '/execute/sync', {'script': script, 'args': list(values)})
        def click(selector):
            element = wd('POST', '/element', {'using': 'css selector', 'value': selector})
            wd('POST', '/element/'+element['element-6066-11e4-a52e-4f735466cecf']+'/click', {})
        def fill(selector, value):
            js("const e=document.querySelector(arguments[0]);e.value=arguments[1];e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));", selector, value)
        def detect():
            click('#variant-autodetect')
            wait(lambda: js("return !document.querySelector('#variant-detection-results').hidden && !document.querySelector('main').inert && !document.querySelector('#variant-autodetect').disabled"), 'resource results')
        def screenshot(name): (root/name).write_bytes(base64.b64decode(wd('GET', '/screenshot')))
        wd('POST', '/window/rect', {'width': 1440, 'height': 1000})
        wd('POST', '/url', {'url': server.origin+'/#'+state.token})
        wait(lambda: js("return typeof hardware!=='undefined' && hardware!==null"), 'main setup')
        click('#choose-illumina');fill('#input-folder', str(reads));click('#scan')
        wait(lambda: js("return !document.querySelector('#settings-card').hidden && !document.querySelector('main').inert"), 'sample scan')
        click('#variants');click('#variant-ffpe');fill('#backend', 'conda')
        fill('#variant_annovar_dir', '/manual/annovar');detect()
        assert js("return document.querySelector('#variant_annovar_dir').value") == '/manual/annovar'
        assert js("return document.querySelector('#variant_annovar_db').value") == '/synthetic/humandb'
        assert js("return document.querySelector('#variant_tool_prefix').value") == '/synthetic/variant-env'
        assert requests[-1]['specimen_type'] == 'ffpe' and requests[-1]['backend'] == 'conda'
        assert not js("return !!window.injected || !!document.querySelector('#variant-detection-results img,#variant-detection-results script,#variant-detection-results a')")
        assert 'printf' in js("return document.querySelector('.resource-install pre').textContent")
        # Exercise the copy fallback, which is also available without clipboard permission.
        js("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('clipboard unavailable')}}})")
        click('.resource-install button')
        wait(lambda: js("return document.querySelector('.resource-install [role=status]').textContent.includes('selected')"), 'copy command fallback')
        assert 'printf' in js('return window.getSelection().toString()')
        checks.append('Main form fills blank fields, preserves manual paths, renders safe codeboxes, and copies via selection fallback')
        screenshot('main-autodetect.png')
        fill('#backend', 'docker');assert js("return document.querySelector('#variant-detection-results').hidden")
        fill('#variant_tool_prefix', '');detect()
        assert js("return document.querySelector('#variant_tool_prefix').value") == ''
        assert requests[-1]['backend'] == 'docker'
        checks.append('Backend change invalidates results; Docker discovery does not fill host environments')
        click('#variant-fresh');assert js("return document.querySelector('#variant-detection-results').hidden")
        checks.append('Fresh/FFPE change clears stale resource results')
        next_window = wd('POST', '/window/new', {'type': 'tab'})
        wd('POST', '/window', {'handle': next_window['handle']})
        wd('POST', '/url', {'url': server.origin+'/variants#'+state.token})
        wait(lambda: js("return document.querySelectorAll('#browser-shortcuts button').length>0"), 'BAM form')
        fill('#config-path', str(config))
        assert js("return document.querySelector('#config-path').value") == str(config)
        click('#load-config')
        wait(lambda: js("return !document.querySelector('#workflow').hidden && !document.querySelector('#load-config').disabled"), 'load synthetic BAM manifest')
        fill('#variant_annovar_dir', '/manual/bam-annovar');detect()
        assert js("return document.querySelector('#variant_annovar_dir').value") == '/manual/bam-annovar'
        assert js("return document.querySelector('#variant_tool_prefix').value") == '/synthetic/variant-env'
        assert requests[-1]['backend'] == 'host' and requests[-1]['callers'] == ['bcftools']
        checks.append('Existing-BAM form calls discovery with loaded caller settings and preserves manual resources')
        screenshot('bam-autodetect.png')
        wd('POST', '/window/rect', {'width': 390, 'height': 844})
        assert js('return document.documentElement.scrollWidth <= window.innerWidth')
        screenshot('mobile-autodetect.png')
        checks.append('Mobile codeboxes scroll without widening the page')
        click('#specimen-fresh');assert js("return document.querySelector('#variant-detection-results').hidden")
        assert state.job is None and state.projects == {}
        checks.append('No project was created and no analysis was started')
        report = {'passed': True, 'checks': checks, 'discovery_requests': requests}
        (root/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'passed': True, 'checks': checks}, indent=2))
    except Exception:
        if session:
            try:
                screenshot('failure.png')
                print(js("return {url:location.href,error:document.querySelector('#error')?.textContent,title:document.title,system:typeof system,script:document.querySelectorAll('script').length,button:!!document.querySelector('#variant-autodetect')}"))
            except Exception: pass
        raise
    finally:
        if session:
            try: http('DELETE', session)
            except Exception: pass
        driver.terminate();driver.wait(timeout=15)
        server.shutdown();server.server_close();log.close()


if __name__ == '__main__': main()
