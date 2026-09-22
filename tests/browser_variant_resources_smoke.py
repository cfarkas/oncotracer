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
from oncotracer_cli.variant_model_assets import FFPERASE_LICENSE
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
    ont_reads = root / 'ont_reads'; ont_reads.mkdir()
    with gzip.open(ont_reads / 'SYNTHETIC.fastq.gz', 'wt') as handle:
        handle.write('@read\nACGT\n+\nIIII\n')
    (root / 'reference.fa').write_text('>chr1\nACGT\n')
    (root / 'sample.bam').write_text('Synthetic placeholder; never processed')
    (root / 'samples.tsv').write_text('sample\tbam\tstatus\nSYNTHETIC\tsample.bam\ttumor\n')
    config = root / 'variants.yml'
    config.write_text(render_flat_yaml({'mode': 'illumina', 'variant_reference': 'reference.fa',
        'variant_bam_manifest': 'samples.tsv', 'outdir': 'old-results', 'run_variants': True,
        'variant_specimen_type': 'ffpe', 'variant_callers': 'bcftools', 'variant_annovar': 'auto'}))
    ont_config = root / 'ont_variants.yml'
    ont_config.write_text(render_flat_yaml({'mode': 'ont', 'variant_reference': 'reference.fa',
        'variant_bam_manifest': 'samples.tsv', 'outdir': 'old-ont-results', 'run_variants': True,
        'variant_specimen_type': 'fresh', 'variant_callers': 'clair3', 'variant_clair3_model': 'auto',
        'variant_ont_profile': 'r1041_e82_400bps_sup_v500', 'variant_annovar': 'off'}))
    state = WebState(root); state.hardware = HARDWARE
    requests = []
    def discovery(payload):
        requests.append(payload)
        docker = payload['backend'] == 'docker'
        return {'backend': payload['backend'],
            'fields': {'variant_annovar_dir': '/synthetic/annovar', 'variant_annovar_db': '/synthetic/humandb',
                       **({} if docker else {'variant_tool_prefix': '/synthetic/variant-env'})},
            'candidates': [{'field':'variant_annovar_db','path':'/synthetic/hg38-db-a','label':'Database A','status':'candidate','detail':'Review reference build'}, {'field':'variant_annovar_db','path':'/synthetic/hg38-db-b','label':'Database B','status':'candidate','detail':'Review reference build'}],
            'resources': [{'id':'annovar_db','field':'variant_annovar_db','label': '<img src=x onerror="window.injected=true">', 'status': 'missing',
                           'detail': 'Synthetic missing resource', 'path': '/synthetic/<path>'}],
            'install_guides': [{'id': 'annovar', 'title': 'Install example', 'reason': 'Synthetic installation guidance',
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
            js("let e=document.querySelector(arguments[0]);for(let p=e?.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;",selector)
            element = wd('POST', '/element', {'using': 'css selector', 'value': selector})
            wd('POST', '/element/'+element['element-6066-11e4-a52e-4f735466cecf']+'/click', {})
        def fill(selector, value):
            js("const e=document.querySelector(arguments[0]);e.value=arguments[1];e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));", selector, value)
        def visible(selector):
            return js("const e=document.querySelector(arguments[0]);return !!e&&!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';", selector)
        def value(selector): return js("return document.querySelector(arguments[0]).value", selector)
        def saved(form): return js('return '+('payload()' if form=='bam' else 'variantPayload()'))
        def expand(selector):
            js("for(let p=document.querySelector(arguments[0])?.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;",selector)
        def check_illumina_guidance(form):
            prefix='specimen' if form=='bam' else 'variant'
            ffpe='#ffperase-section' if form=='bam' else '#variant-ffperase-fields'
            click('#'+prefix+'-fresh')
            assert not visible(ffpe), form+' Fresh unexpectedly exposes FFPERASE'
            assert value('#variant_varlociraptor')=='off'
            assert saved(form).get('variant_ffperase') in (None,'off')
            assert saved(form)['variant_download_resources'] is False
            assert js("return document.querySelector('#variant-filter-title').textContent")=='Variant filtering'
            click('#'+prefix+'-ffpe')
            assert visible(ffpe) and visible('#variant_download_resources') and visible('#variant-ffperase-license')
            assert value('#variant_ffperase')=='required'
            assert js("return document.querySelector('#variant_download_resources').checked")
            assert not js("return document.querySelector('#variant_accept_ffperase_license').checked")
            assert js("return document.querySelector('#variant-ffperase-license a').href")==FFPERASE_LICENSE
            click('#variant_accept_ffperase_license')
            current=saved(form)
            assert current['variant_ffperase']=='required' and current['variant_download_resources'] is True
            assert current['variant_accept_ffperase_license'] is True
            click('#variant_download_resources')
            assert not visible('#variant-ffperase-license') and saved(form)['variant_download_resources'] is False
            click('#variant_download_resources')
            assert visible('#variant-ffperase-license')
            fill('#variant_varlociraptor','required')
            assert value('#variant-scenario-mode')=='standard'
            assert not visible('#variant-custom-scenario')
            for field in ('scenario','events','sample'):
                assert not visible('#variant_varlociraptor_'+field)
            current=saved(form)
            assert current['variant_varlociraptor_scenario']==''
            assert current['variant_varlociraptor_events']=='PRESENT' and current['variant_varlociraptor_sample']=='sample'
            assert current['variant_varlociraptor_fdr']=='0.05'
            expand('#variant-scenario-mode');fill('#variant-scenario-mode','custom')
            assert visible('#variant-custom-scenario')
            error=js("try { "+('payload()' if form=='bam' else 'variantPayload()')+"; return ''; } catch(error) { return error.message; }")
            assert 'Choose a custom scenario YAML' in error
            fill('#variant_varlociraptor_scenario','/synthetic/custom.yml')
            fill('#variant_varlociraptor_events','SOMATIC')
            fill('#variant_varlociraptor_sample','tumor')
            current=saved(form)
            assert current['variant_varlociraptor_scenario']=='/synthetic/custom.yml'
            assert current['variant_varlociraptor_events']=='SOMATIC' and current['variant_varlociraptor_sample']=='tumor'
            fill('#variant-scenario-mode','standard')
            assert not visible('#variant-custom-scenario')
            current=saved(form)
            assert current['variant_varlociraptor_scenario']==''
            assert current['variant_varlociraptor_events']=='PRESENT' and current['variant_varlociraptor_sample']=='sample'
            assert value('#variant_varlociraptor_scenario')=='/synthetic/custom.yml'
            checks.append(form+': Fresh hides FFPERASE; FFPE exposes automatic preparation and explicit license acknowledgment')
            checks.append(form+': Varlociraptor standard mode hides custom YAML/keys and emits PRESENT/sample; custom opt-in round trip clears stale custom payload')
            screenshot(form+'-guided-ffpe.png')
            fill('#variant_varlociraptor','off')
        def check_ont_guidance(form):
            prefix='specimen' if form=='bam' else 'variant'
            ffpe='#ffperase-section' if form=='bam' else '#variant-ffperase-fields'
            assert value('#variant-clair3-source')=='auto'
            assert visible('#variant-clair3-auto-fields') and not visible('#variant-clair3-local-fields')
            fill('#variant_ont_profile','r1041_e82_400bps_sup_v420')
            current=saved(form)
            assert current['variant_clair3_model']=='auto' and current['variant_ont_profile']=='r1041_e82_400bps_sup_v420'
            fill('#variant-clair3-source','local')
            assert value('#variant-clair3-source')=='local'
            assert visible('#variant-clair3-local-fields') and not visible('#variant-clair3-auto-fields')
            fill('#variant_clair3_model','/synthetic/compatible-model')
            current=saved(form)
            assert current['variant_clair3_model']=='/synthetic/compatible-model' and current['variant_ont_profile']==''
            fill('#variant-clair3-source','auto')
            assert saved(form)['variant_clair3_model']=='auto'
            fill('#variant-clair3-source','local')
            assert value('#variant_clair3_model')=='/synthetic/compatible-model'
            assert saved(form)['variant_clair3_model']=='/synthetic/compatible-model'
            fill('#variant-clair3-source','auto')
            click('#caller-clairs_to' if form=='bam' else '[data-variant-caller=clairs_to]')
            assert visible('#variant-clairsto-preset')
            preset=value('#variant-clairsto-preset')
            assert preset and preset!='custom' and saved(form)['variant_clairsto_platform']==preset
            assert js("return [...document.querySelector('#variant-clairsto-preset').options].filter(e=>e.value!=='custom').every(e=>e.textContent.includes('R10.4.1')&&(e.textContent.includes('Dorado')||e.textContent.includes('Guppy')))")
            fill('#variant-clairsto-preset','ont_r10_dorado_sup_4khz')
            assert saved(form)['variant_clairsto_platform']=='ont_r10_dorado_sup_4khz'
            fill('#variant-clairsto-preset','custom')
            assert visible('#variant-clairsto-custom')
            fill('#variant_clairsto_platform','ont_custom_installed_preset')
            assert saved(form)['variant_clairsto_platform']=='ont_custom_installed_preset'
            fill('#variant-clairsto-preset','ont_r10_dorado_sup_5khz_ssrs')
            assert not visible('#variant-clairsto-custom')
            assert saved(form)['variant_clairsto_platform']=='ont_r10_dorado_sup_5khz_ssrs'
            click('#'+prefix+'-ffpe')
            assert not visible(ffpe), form+' ONT FFPE must not expose Illumina-only FFPERASE'
            assert saved(form).get('variant_ffperase') in (None,'off')
            assert saved(form)['variant_download_resources'] is False
            click('#'+prefix+'-fresh')
            checks.append(form+': ONT auto Clair3 saves auto plus selected chemistry profile; local/auto changes preserve the local model path')
            checks.append(form+': ClairS-TO has readable populated presets and explicit custom entry; ONT FFPE does not enable Illumina FFPERASE')
            screenshot(form+'-guided-ont.png')
        def detect(selector='#variant-autodetect'):
            click(selector)
            wait(lambda: js("return !document.querySelector('#variant-detection-results').hidden && !document.querySelector('main').inert && !document.querySelector('#variant-autodetect').disabled"), 'resource results')
        def screenshot(name): (root/name).write_bytes(base64.b64decode(wd('GET', '/screenshot')))
        wd('POST', '/window/rect', {'width': 1440, 'height': 1000})
        wd('POST', '/url', {'url': server.origin+'/#'+state.token})
        wait(lambda: js("return typeof hardware!=='undefined' && hardware!==null"), 'main setup')
        click('#choose-illumina');fill('#input-folder', str(reads));click('#scan')
        wait(lambda: js("return !document.querySelector('#settings-card').hidden && !document.querySelector('main').inert"), 'sample scan')
        click('#variants')
        assert visible('#variant-specimen-section') and not visible('#variant-caller-settings')
        assert all(not visible('#variant-'+part+'-section') for part in ('tools','filter','annotation'))
        assert js("return [...document.querySelectorAll('[data-variant-section]')].filter(e=>e.dataset.variantSection!=='variant-specimen-section').every(e=>e.disabled)")
        screenshot('main-before-specimen.png')
        checks.append('Main setup shows only the specimen step until preservation is chosen')
        click('#variant-ffpe');fill('#backend', 'conda')
        assert js("return [...document.querySelectorAll('.variant-section-heading h3')].map(e=>e.textContent)")==['Specimen and callers','Caller tools and models','FFPE damage and filtering','Annotation']
        assert not js("return [...document.querySelectorAll('.variant-path-details')].some(e=>e.open)")
        assert js("return document.querySelectorAll('[data-variant-detect]').length")>=12
        assert js("return [...document.querySelectorAll('[data-variant-detect]')].every(e=>!['variant_targets_bed','variant_varlociraptor_scenario'].includes(e.dataset.variantDetect))")
        check_illumina_guidance('main')
        original_hash=js('return location.hash')
        click('[data-variant-section=variant-annotation-section]')
        assert js('return location.hash')==original_hash
        checks.append('Four ordered groups, collapsed manual paths and discovery beside every tool/model/database path; section navigation preserves the session token')
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
        click('#variant-resource-close')
        fill('#variant_annovar_db','');fill('#variant_tool_prefix','')
        detect('[data-variant-detect=variant_annovar_db]')
        assert js("return document.querySelector('#variant_annovar_db').value") == '/synthetic/humandb'
        assert js("return document.querySelector('#variant_tool_prefix').value") == ''
        click('[data-candidate-path="/synthetic/hg38-db-b"]')
        assert js("return document.querySelector('#variant_annovar_db').value") == '/synthetic/hg38-db-b'
        assert js("return document.querySelector('#variant_annovar_dir').value") == '/manual/annovar'
        checks.append('Field-level discovery fills only its field; explicit candidate selection can replace that path')
        click('#variant-resource-close')
        fill('#backend', 'docker');assert js("return document.querySelector('#variant-detection-results').hidden")
        fill('#variant_tool_prefix', '');detect()
        assert js("return document.querySelector('#variant_tool_prefix').value") == ''
        assert requests[-1]['backend'] == 'docker'
        checks.append('Backend change invalidates results; Docker discovery does not fill host environments')
        click('#variant-resource-close')
        click('#variant-fresh');assert js("return document.querySelector('#variant-detection-results').hidden")
        checks.append('Fresh/FFPE change clears stale resource results')
        click('#choose-ont');fill('#input-folder',str(ont_reads));click('#scan')
        wait(lambda: js("return !document.querySelector('#settings-card').hidden && !document.querySelector('main').inert"), 'ONT sample scan')
        click('#variant-fresh');check_ont_guidance('main')
        next_window = wd('POST', '/window/new', {'type': 'tab'})
        wd('POST', '/window', {'handle': next_window['handle']})
        wd('POST', '/url', {'url': server.origin+'/variants#'+state.token})
        wait(lambda: js("return document.querySelectorAll('#browser-shortcuts button').length>0"), 'BAM form')
        assert not visible('#workflow')
        fill('#config-path', str(config))
        assert js("return document.querySelector('#config-path').value") == str(config)
        click('#load-config')
        wait(lambda: js("return !document.querySelector('#workflow').hidden && !document.querySelector('#load-config').disabled"), 'load synthetic BAM manifest')
        check_illumina_guidance('bam')
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
        click('#variant-resource-close')
        fill('#variant_ffperase_prefix','/synthetic/native');fill('#variant_ffperase_sif','/synthetic/ffpe.sif')
        fill('#variant-ffperase-runtime','native')
        assert js("return document.querySelector('#variant-ffperase-sif-fields').hidden && !document.querySelector('#variant-ffperase-prefix-fields').hidden")
        assert js("return document.querySelector('#variant_ffperase_sif').value")=='/synthetic/ffpe.sif'
        assert js("return payload().variant_ffperase_sif") == ''
        assert js("return payload().variant_ffperase_prefix") == '/synthetic/native'
        fill('#variant-ffperase-runtime','sif')
        assert js("return payload().variant_ffperase_prefix") == ''
        assert js("return payload().variant_ffperase_sif") == '/synthetic/ffpe.sif'
        checks.append('Native/SIF runtime choice shows and saves only one alternative, preserving inactive values for editing')
        click('#specimen-fresh');assert js("return document.querySelector('#variant-detection-results').hidden")
        wd('POST', '/window/rect', {'width':1440,'height':1000})
        fill('#config-path',str(ont_config));click('#load-config')
        wait(lambda: js("return !document.querySelector('#workflow').hidden && !document.querySelector('#load-config').disabled"), 'load synthetic ONT config')
        check_ont_guidance('bam')
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
