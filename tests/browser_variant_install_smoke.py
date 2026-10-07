"""Firefox installation workflow test using a harmless local package-manager fixture.

The real HTTP API, worker, cancellation, tool checks and form updates run. No
packages are downloaded and no sequencing analysis is started.
"""
import argparse
import base64
import gzip
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.browser_setup_smoke import free_port, http, wait
from tests.test_web import HARDWARE
from oncotracer_cli.runtime import load_flat_yaml, render_flat_yaml
from oncotracer_cli.variant_installer import VariantInstaller
from oncotracer_cli.web import WebState, WebServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    root = parser.parse_args().output.resolve(); root.mkdir(parents=True, exist_ok=False)
    reads = root / 'reads'; reads.mkdir()
    for mate in (1, 2):
        with gzip.open(reads / f'SYNTHETIC_R{mate}.fastq.gz', 'wt') as handle:
            handle.write('@test\nACGT\n+\nIIII\n')
    original = {p: p.read_bytes() for p in reads.iterdir()}
    manager = root / 'conda-fixture'
    manager.write_text(f'#!{sys.executable}\n' + r'''
import pathlib, sys, time
root = pathlib.Path(__file__).parent
(root / 'called').touch()
print('Fixture package download started', flush=True)
while (root / 'wait').exists(): time.sleep(0.1)
time.sleep(2)
if (root / 'fail').exists(): print('Fixture network failure', flush=True); sys.exit(17)
if '--prefix' in sys.argv:
    prefix = pathlib.Path(sys.argv[sys.argv.index('--prefix')+1]); (prefix / 'bin').mkdir(parents=True)
    for name in ('samtools', 'bcftools', 'gatk', 'freebayes', 'varlociraptor'):
        tool = prefix / 'bin' / name; tool.write_text('#!/bin/sh\necho "Fixture executable checked"\n'); tool.chmod(0o755)
print('Fixture installation complete', flush=True)
''')
    manager.chmod(0o755)
    incomplete = root / 'missing/bin'; incomplete.mkdir(parents=True)
    for name in ('samtools','bcftools'):
        tool = incomplete / name; tool.write_text('#!/bin/sh\nexit 0\n'); tool.chmod(0o755)
    (root / 'reference.fa').write_text('>chr1\nACGT\n')
    (root / 'sample.bam').write_text('Synthetic placeholder, never processed')
    (root / 'samples.tsv').write_text('sample\tbam\tstatus\nSYNTHETIC\tsample.bam\ttumor\n')
    config = root / 'variants.yml'
    config.write_text(render_flat_yaml({'mode':'illumina','variant_reference':'reference.fa','variant_bam_manifest':'samples.tsv',
        'outdir':'old-results','run_variants':True,'variant_specimen_type':'fresh','variant_callers':'freebayes','variant_annovar':'off'}))
    state = WebState(root); state.hardware = HARDWARE
    state.variant_installer = VariantInstaller(root, install_root=root / 'installs')
    server = WebServer(0, state); threading.Thread(target=server.serve_forever, daemon=True).start()
    profiles = root / 'profiles'; profiles.mkdir()
    log = (root / 'geckodriver.log').open('w'); port = free_port()
    driver = subprocess.Popen(['/snap/bin/geckodriver', '--host', '127.0.0.1', '--port', str(port),
                               '--profile-root', str(profiles), '--log', 'error'], stdout=log, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'; session = None; checks = []
    executables = patch('oncotracer_cli.variant_installer._executable', side_effect=lambda name: str(manager) if name in {'conda','docker'} else None)
    picker = patch('oncotracer_cli.web.choose_path', return_value=reads)
    executables.start(); picker.start()
    try:
        wait(lambda: http('GET', base+'/status'), 'WebDriver')
        result = http('POST', base+'/session', {'capabilities':{'alwaysMatch':{'browserName':'firefox','moz:firefoxOptions':{'args':['-headless']}}}})
        session = base+'/session/'+result['value']['sessionId']
        def wd(method, path, data=None): return http(method, session+path, data).get('value')
        def js(script, *values): return wd('POST','/execute/sync',{'script':script,'args':list(values)})
        def click(selector):
            js("const e=document.querySelector(arguments[0]);for(let p=e?.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;e.scrollIntoView({block:'center'});",selector)
            element=wd('POST','/element',{'using':'css selector','value':selector})
            wd('POST','/element/'+element['element-6066-11e4-a52e-4f735466cecf']+'/click',{})
        def fill(selector, value): js("const e=document.querySelector(arguments[0]);e.value=arguments[1];e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));",selector,value)
        def value(selector): return js('return document.querySelector(arguments[0]).value',selector)
        def screenshot(name): (root/name).write_bytes(base64.b64decode(wd('GET','/screenshot')))
        def detect():
            click('#variant-autodetect')
            wait(lambda: js("return !document.querySelector('#variant-detection-results').hidden&&!document.querySelector('#variant-autodetect').disabled&&!document.querySelector('main').inert"), 'detection results')
        def choose_install(method):
            click('#variant-detection-results [data-variant-install='+method+']')
            wait(lambda: value('#variant-install-method')==method and js("return !document.querySelector('#variant-install-start').disabled"), method+' installation plan')
        def install_start():
            detect();choose_install('conda')
            click('#variant-install-start')
            wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state==='running'"), 'background installation')
        def finish(status='complete'):
            wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state===arguments[0]",status),status)
        wd('POST','/window/rect',{'width':1440,'height':1000})
        wd('POST','/url',{'url':server.origin+'/#'+state.token})
        wait(lambda: js("return typeof hardware!=='undefined'&&hardware!==null"),'setup')
        click('#choose-illumina'); click('#variant-fresh'); fill('#input-folder',str(reads))
        wait(lambda: js("return !document.querySelector('#settings-card').hidden&&!document.querySelector('main').inert"),'samples')
        click('#variants'); fill('#backend','conda'); fill('#variant_annovar','off')
        js("for(const field of document.querySelectorAll('[data-variant-caller]'))field.checked=['mutect2','freebayes'].includes(field.value);variantSettings();")
        fill('#variant_tool_prefix',str(root / 'missing'))
        js("for(const row of sampleRows()){const field=row.querySelector('.type-select');field.value='cancer';field.dispatchEvent(new Event('change',{bubbles:true}));}")
        fill('#project-parent',str(root));fill('#project-name','saved-after-install');click('#prepare')
        wait(lambda: js("return !document.querySelector('#review-card').hidden&&document.querySelector('#check-badge').textContent==='Needs attention'&&!document.querySelector('main').inert"),'missing GATK check')
        assert js("return document.querySelector('#check-messages').textContent.includes('Variant tool gatk is unavailable')&&document.querySelector('#run').disabled")
        saved_config = root / 'saved-after-install/config/run.yml'; before_install = saved_config.read_bytes()
        previous_log = root / 'saved-after-install/logs/keep.txt'
        previous_log.parent.mkdir();previous_log.write_text('Existing logs retained')
        screenshot('missing-gatk-check.png')
        checks.append('Missing GATK saves a failed check and keeps Run disabled before installation')
        click('#variant-tools-browse'); wait(lambda: value('#variant-tools-folder')==str(reads),'tools folder chooser')
        assert js("return document.querySelector('#variant-autodetect').textContent==='detect_tools'&&getComputedStyle(document.querySelector('#variant-autodetect')).boxShadow!=='none'")
        assert js("return document.querySelector('#variant-install-choices').hidden")
        js("document.querySelector('#variant-autodetect').scrollIntoView({block:'center'})")
        screenshot('detect-tools.png')
        checks.append('Glowing detect_tools is visible; installation choices stay hidden before detection')
        detect()
        assert js("return !document.querySelector('#variant-install-choices').hidden&&[...document.querySelectorAll('#variant-detection-results [data-variant-install]')].map(e=>e.textContent).join('|')==='Install with Conda|Install with Docker'")
        assert not (root / 'called').exists()
        screenshot('missing-tools-choices.png')
        checks.append('Missing tools reveal separate Conda and Docker buttons without starting installation')
        click('#variant-resource-close');fill('#variant_tool_prefix',str(root / 'edited-missing'))
        assert js("return document.querySelector('#variant-install-choices').hidden")
        detect();click('#variant-resource-close');click('#variant-install-conda')
        wait(lambda: value('#variant-install-method')=='conda' and js("return !document.querySelector('#variant-install-start').disabled"),'explicit Conda plan')
        assert js("return document.querySelector('#variant-install-start').textContent==='Install with Conda'")
        checks.append('Resource edits hide stale install choices; the Conda button selects Conda explicitly')
        assert not (root / 'called').exists()
        assert 'native-variants.yml' in js("return document.querySelector('#variant-install-commands').textContent")
        screenshot('installation-plan.png'); checks.append('Tools chooser and reviewable plan do not execute installation commands')
        click('#variant-install-start');wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state==='running'"),'running')
        assert js("return document.querySelector('#prepare').disabled")
        wait(lambda: js("return document.querySelector('#variant-install-log').textContent.includes('Fixture package download')"),'live log')
        screenshot('installation-running.png')
        click('#variant-resource-close'); fill('#threads','3')
        finish(); wait(lambda: '/installs/' in value('#variant_tool_prefix'),'automatic path selection')
        wait(lambda: js("return !document.querySelector('#variant-detection-results').hidden&&document.querySelector('#variant-install-choices').hidden"),'all tools found')
        assert js("return document.querySelectorAll('#variant-detection-results [data-variant-install]').length===0")
        checks.append('Installation choices disappear when detection finds all selected tools')
        assert value('#threads')=='3'
        checks.append('Background progress and logs remain live; successful installation selects paths and preserves unrelated edits')
        screenshot('installation-complete.png'); click('#variant-resource-close')
        click('#prepare')
        wait(lambda: js("return document.querySelector('#check-badge').textContent==='Configuration checked'&&!document.querySelector('#run').disabled"),'save/check using installed tools')
        assert load_flat_yaml(saved_config)['variant_tool_prefix']==value('#variant_tool_prefix')
        assert next((saved_config.parent / 'backups').glob('*/run.yml')).read_bytes()==before_install
        assert previous_log.read_text()=='Existing logs retained'
        assert not js("return document.querySelector('#check-messages').textContent.includes('Variant tool gatk is unavailable')")
        screenshot('resaved-gatk-ready.png')
        checks.append('Same-project save replaces stale GATK settings, retains a backup and existing logs, clears the error and enables Run')
        fill('#variant_tool_prefix',str(root / 'cancelled-missing')); (root / 'wait').touch()
        install_start(); click('#variant-install-stop');finish('cancelled');(root / 'wait').unlink()
        assert value('#variant_tool_prefix')==str(root / 'cancelled-missing')
        checks.append('Stop cancels only the installer and does not select partial outputs')
        click('#variant-resource-close'); (root / 'fail').touch();install_start();finish('failed')
        assert js("return document.querySelector('#variant-install-error').textContent.includes('exit 17')")
        failed=state.variant_installer.job['id']; (root / 'fail').unlink()
        click('#variant-install-start');wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state==='running'"),'retry');finish()
        assert state.variant_installer.job['id']!=failed
        checks.append('Failures expose their log and retry creates a fresh installation')
        click('#variant-resource-close'); fill('#variant_tool_prefix',str(root / 'changed-missing')); (root / 'wait').touch()
        install_start();click('#variant-resource-close');fill('#variant_tool_prefix',str(root / 'manual-edit'));(root / 'wait').unlink();finish()
        assert value('#variant_tool_prefix')==str(root / 'manual-edit')
        click('#variant-install-review');click('#variant-install-apply')
        wait(lambda: '/installs/' in value('#variant_tool_prefix'),'explicit path application')
        checks.append('Resource edits during installation survive; Use installed tools applies paths explicitly')
        click('#variant-resource-close');fill('#docker_image','carlosfarkas/oncotracer:fastq-variants-20260922')
        fill('#variant_tool_prefix',str(root / 'docker-missing'));detect();choose_install('docker')
        wait(lambda: js("return !document.querySelector('#variant-install-start').disabled&&document.querySelector('#variant-install-start').textContent==='Install with Docker'"),'Docker plan')
        click('#variant-install-start');wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state==='running'"),'Docker installation');finish()
        wait(lambda: value('#backend')=='docker','Docker backend applied')
        assert value('#docker_image')=='carlosfarkas/oncotracer:fastq-variants-20260922'
        assert js("return !('variant_tool_prefix' in variantPayload())")
        checks.append('Docker choice prepares an image, switches the FASTQ backend and omits host caller prefixes')
        next_window=wd('POST','/window/new',{'type':'tab'})
        wd('POST','/window',{'handle':next_window['handle']})
        wd('POST','/url',{'url':server.origin+'/variants#'+state.token})
        wait(lambda: js("return document.querySelectorAll('#browser-shortcuts button').length>0"),'BAM form')
        wait(lambda: js("return document.querySelector('#variant-install-panel').dataset.state==='complete'"),'installation restored')
        assert js("return document.querySelector('#variant-install-apply').hidden")
        fill('#config-path',str(config));assert value('#config-path')==str(config);click('#load-config')
        wait(lambda: js("return !document.querySelector('#workflow').hidden&&!document.querySelector('#load-config').disabled"),'BAM config')
        fill('#variant_tool_prefix',str(root / 'bam-missing'));detect()
        previous=state.variant_installer.job['id']
        click('#variant-detection-results [data-variant-install=docker]')
        wait(lambda: value('#variant-install-method')=='docker' and js("return document.querySelector('#variant-install-plan-note').textContent.includes('existing-BAM form')"),'unavailable Docker explanation')
        assert js("return document.querySelector('#variant-install-start').disabled")
        assert state.variant_installer.job['id']==previous
        choose_install('conda');click('#variant-install-start');finish()
        wait(lambda: '/installs/' in value('#variant_tool_prefix'),'BAM paths')
        assert next(o for o in state.variant_installer.job['plan']['options'] if o['method']=='docker')['available'] is False
        checks.append('Existing-BAM setup installs native tools and explains its Docker limitation')
        click('#variant-resource-close');fill('#variant_tool_prefix',str(root / 'mobile-missing'))
        wd('POST','/window/rect',{'width':390,'height':844})
        js("document.querySelector('#variant-autodetect').scrollIntoView({block:'center'})")
        screenshot('detect-tools-mobile.png');detect()
        assert js('return document.documentElement.scrollWidth<=window.innerWidth')
        screenshot('installation-mobile.png');checks.append('Glowing detection and both installation choices fit mobile width')
        assert state.job is None and all(path.read_bytes()==content for path,content in original.items())
        report={'passed':True,'checks':checks,'real_packages_installed':False,'analysis_started':False}
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    except Exception:
        if session:
            screenshot('failure.png')
            print(js("return {errorText:document.querySelector('#error')?.textContent,installError:document.querySelector('#variant-install-error')?.textContent,installStatus:document.querySelector('#variant-install-status')?.textContent}"))
        raise
    finally:
        state.variant_installer.close();server.shutdown();server.server_close();executables.stop();picker.stop()
        if session:
            try:http('DELETE',session)
            except Exception:pass
        driver.terminate();driver.wait(timeout=10);log.close()


if __name__=='__main__':main()
