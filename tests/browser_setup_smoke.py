"""Optional real Firefox smoke test; uses synthetic FASTQs and never starts analysis.

Run: python tests/browser_setup_smoke.py --output /absolute/writable/shared/path
Requires Firefox and geckodriver. For Snap Firefox, use a path outside /tmp so
Firefox and geckodriver can share profiles. Results include a screenshot and JSON.
"""
import argparse
import base64
import csv
import gzip
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Permit the documented direct script invocation from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def http(method, url, data=None):
    request = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                     method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode()) from error
    if isinstance(result.get("value"), dict) and result["value"].get("error"):
        raise RuntimeError(str(result))
    return result


def wait(test, label):
    until = time.monotonic() + 45
    while time.monotonic() < until:
        try:
            value = test()
            if value:
                return value
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.15)
    raise AssertionError("Timed out: " + label)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-stop", action="store_true", help="also test Stop and cleanup using a harmless sleeping job")
    parser.add_argument("--test-variants", action="store_true", help="also test Fresh/FFPE buttons and platform-specific variant configuration; no caller execution")
    options = parser.parse_args()
    root = options.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    fixture = root / "inputs"
    for relative, count in (("ont/barcode01", 69), ("ont/barcode02", 2), ("ligation", 3)):
        for n in range(count):
            path = fixture / relative / f"batch_{n}.fastq.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt") as handle:
                handle.write("@read\nACGT\n+\nIIII\n")
    for name in ("case", "healthy"):
        for mate in (1, 2):
            path = fixture / "illumina" / f"{name}_R{mate}.fastq.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt") as handle:
                handle.write("@read\nACGT\n+\nIIII\n")
    before = {str(path): path.read_bytes() for path in fixture.rglob("*.gz")}
    processes, handles = [], []
    session = None
    report = {"passed": False, "checks": [], "analysis_started": False}
    try:
        web_log = root / "web.log"
        handle = web_log.open("w"); handles.append(handle)
        command = [sys.executable, "-m", "oncotracer_cli.cli", "setup", "--no-browser", "--port", str(free_port()),
                   "--project", str(root / "illumina-project"), "--mode", "illumina",
                   "--input-folder", str(fixture / "illumina"), "--threads", "3"]
        if options.test_stop:
            # Use real HTTP/configuration paths with a harmless job instead of
            # executing an analysis. Other commands still call the actual CLI.
            launcher = root / "dummy_analysis.py"
            launcher.write_text("""import subprocess,sys
if 'setup' in sys.argv and '--run' in sys.argv:
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'])
    print('Dummy analysis ready',flush=True)
    print('  hg38-00-0000.part: 50% | 10.0 MiB/s | ETA 60s',flush=True)
    try:
        child.wait()
    except KeyboardInterrupt:
        child.wait()
        raise SystemExit(130)
else:
    from oncotracer_cli.cli import main
    raise SystemExit(main())
""")
            wrapper = "import sys; from oncotracer_cli import web; from oncotracer_cli.cli import main; web._launcher=lambda:[sys.executable," + repr(str(launcher)) + "]; raise SystemExit(main())"
            command = [sys.executable, "-c", wrapper, *command[3:]]
        processes.append(subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
        url = wait(lambda: next((s for s in web_log.read_text().splitlines() if s.startswith("http://127.0.0.1:")), None), "setup server")
        driver = shutil.which("geckodriver") or "/snap/bin/geckodriver"
        profiles = root / "profiles"; profiles.mkdir()
        handle = (root / "geckodriver.log").open("w"); handles.append(handle)
        driver_port = free_port()
        processes.append(subprocess.Popen([driver, "--host", "127.0.0.1", "--port", str(driver_port),
                                           "--profile-root", str(profiles), "--log", "error"],
                                          stdout=handle, stderr=subprocess.STDOUT))
        base = f"http://127.0.0.1:{driver_port}"
        wait(lambda: http("GET", base + "/status"), "geckodriver")
        result = http("POST", base + "/session", {"capabilities": {"alwaysMatch": {
            "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"],
            "prefs": {"browser.shell.checkDefaultBrowser": False}}}}})
        session = base + "/session/" + result["value"]["sessionId"]
        def wd(method, path, data=None):
            return http(method, session + path, data).get("value")
        def js(script, *args):
            return wd("POST", "/execute/sync", {"script": script, "args": list(args)})
        def element(selector):
            return wd("POST", "/element", {"using": "css selector", "value": selector})
        def click(selector):
            identity = element(selector)["element-6066-11e4-a52e-4f735466cecf"]
            wd("POST", "/element/" + identity + "/click", {})
        def fill(selector, value):
            js("const e=document.querySelector(arguments[0]);e.value=arguments[1];e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));", selector, value)
        def drag(selector, target):
            js("document.querySelector('#samples-card').scrollIntoView()")
            time.sleep(.5)

            source, destination = element(selector), element(target)
            wd("POST", "/actions", {"actions": [{"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "origin": source, "x": 0, "y": 0},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 300},
                {"type": "pointerMove", "duration": 250, "origin": source, "x": 8, "y": 8},
                {"type": "pointerMove", "duration": 0, "origin": destination, "x": 0, "y": 0},
                {"type": "pause", "duration": 400},
                {"type": "pointerUp", "button": 0}]}]})
            wd("DELETE", "/actions")
            time.sleep(1)
        def sample_count(count):
            wait(lambda: js("return document.querySelectorAll('.sample').length===arguments[0] && !document.querySelector('#samples-card').hidden && !document.querySelector('main').inert", count), "sample discovery")
        def scan_ont(folder, count):
            click("#choose-ont")
            fill("#input-folder", str(folder))
            sample_count(count)
        def prepare(name):
            fill("#project-parent", str(root)); fill("#project-name", name)
            click("#prepare")
            wait(lambda: js("return !document.querySelector('#review-card').hidden && !document.querySelector('main').inert"), "saved configuration")
            assert not js("return document.querySelector('#run').disabled"), js("return document.querySelector('#check-messages').textContent")
            assert not (root / name / "results").exists()
            with (root / name / "config/sample_metadata.csv").open() as handle:
                return list(csv.DictReader(handle))
        wd("POST", "/window/rect", {"width": 1400, "height": 1100})
        wd("POST", "/url", {"url": url})
        sample_count(2)
        assert js("return document.querySelector('#threads').value") == "3"
        assert js("return document.querySelector('#binsize').value") == "100"
        assert js("return document.querySelectorAll('#normal-samples .sample,#cancer-samples .sample').length") == 0
        assert js("return document.querySelector('#gistic').disabled")
        report["checks"].append("setup opens browser workflow with supplied paths, threads and 100 kb default; groups start empty")
        assert not js("return document.querySelector('#fastq-preview').hidden")
        assert js("return document.querySelectorAll('#fastq-rows tr').length") == 4
        assert js("return document.querySelectorAll('.sample .fastq-files li').length") == 4
        fill('#fastq-filter', 'case_R1')
        assert js("return document.querySelectorAll('#fastq-rows tr').length") == 1
        assert js("return document.querySelectorAll('#normal-samples .sample,#cancer-samples .sample').length") == 0
        fill('#fastq-filter', '')
        report['checks'].append('FASTQ filenames visible by default in folder inventory and sample cards; filtering preserves assignment')
        drag('.sample[data-id="0"] .drag-handle', '#cancer-samples')
        assert js("return document.querySelectorAll('#cancer-samples .sample').length") == 1
        drag('.sample[data-id="1"] .drag-handle', '#normal-samples')
        assert js("return document.querySelectorAll('#normal-samples .sample').length") == 1
        assert not js("return document.querySelector('#gistic').disabled")
        report["checks"].append("real pointer drag-and-drop assigns Cancer and Normal; cohort enables GISTIC")
        fill('.sample[data-id="0"] .sample-name', 'corrected_name')
        drag('.sample[data-id="0"] .drag-handle', '#available-samples')
        assert js("return document.querySelector('.sample[data-id=\"0\"]').parentElement.id") == 'available-samples'
        assert js("return document.querySelector('#gistic').disabled")
        drag('.sample[data-id="0"] .drag-handle', '#normal-samples')
        assert js("return document.querySelector('.sample[data-id=\"0\"]').parentElement.id") == 'normal-samples'
        assert js("return document.querySelector('.sample[data-id=\"0\"] .sample-name').value") == 'corrected_name'
        drag('.sample[data-id="0"] .drag-handle', '#cancer-samples')
        report["checks"].append("assigned samples drag back to Unassigned or across to Normal/Cancer without losing edited names")
        fill('.sample[data-id="0"] .sample-name', 'edited_case')
        for label in ("Normal", "NORMAL", "nORMAl", "Cancer", "CANCER", "cANCER"):
            fill('.sample[data-id="0"] .type-select', 'custom')
            fill('.sample[data-id="0"] .custom-label', label)
            assert js("return document.querySelector('.sample[data-id=\"0\"] .type-select').value") == label.lower()
        click('#reports'); click('#gistic')
        fill('.sample[data-id="1"] .type-select', '')
        assert js("return document.querySelector('#gistic').disabled && !document.querySelector('#gistic').checked")
        fill('.sample[data-id="1"] .type-select', 'normal')
        metadata = prepare('illumina-project')
        assert [(r['sample'], r['sample_type'], r['analysis_role']) for r in metadata] == [('edited_case','cancer','tumor'),('healthy','normal','normal')]
        report["checks"].append("case normalization, editable names, clearing GISTIC after unassignment, save/check and enabled Run button")
        js("document.querySelector('#samples-card').scrollIntoView()")
        (root / "sample-board.png").write_bytes(base64.b64decode(wd("GET", "/screenshot")))
        if options.test_variants:
            from oncotracer_cli.runtime import load_flat_yaml
            click('#variants');click('#variant-ffpe')
            assert js("return document.querySelector('#variant-ffpe').getAttribute('aria-pressed')") == 'true'
            assert js("return [...document.querySelectorAll('[data-variant-caller]')].map(e=>e.value)") == ['mutect2','freebayes','bcftools']
            assert not js("return document.querySelector('#variant-ffperase-fields').hidden")
            fill('#variant_varlociraptor','required');fill('#variant_varlociraptor_fdr','0.05')
            click('[data-variant-caller="freebayes"]');fill('#variant_annovar','off')
            prepare('illumina-variants-project')
            config = load_flat_yaml(root / 'illumina-variants-project/config/run.yml')
            assert config['run_variants'] is True and config['variant_specimen_type'] == 'ffpe'
            assert config['variant_callers'] == 'mutect2,freebayes' and config['variant_annovar'] == 'off'
            assert config['variant_ffperase'] == 'required' and config['variant_varlociraptor'] == 'required'
            # Docker uses its own tools; stale host SIF/prefix values stay out of YAML.
            fill('#variant_tool_prefix', '/host-only/variant-tools')
            fill('#variant_ffperase_prefix', '/host-only/ffperase')
            fill('#variant_ffperase_sif', '/host-only/ffperase.sif')
            assert not js("return document.querySelector('#backend option[value=docker]').disabled")
            fill('#backend', 'docker');fill('#docker_image', 'oncotracer:browser-fixture')
            assert not js("return document.querySelector('#docker-image-field').hidden")
            assert js("return document.querySelector('#variant-tool-prefix-fields').hidden && document.querySelector('#variant-ffperase-prefix-fields').hidden && document.querySelector('#variant-ffperase-sif-fields').hidden")
            prepare('illumina-docker-variants-project')
            config = load_flat_yaml(root / 'illumina-docker-variants-project/config/run.yml')
            assert config['execution_backend'] == 'docker' and config['docker_image'] == 'oncotracer:browser-fixture'
            assert not any(key in config for key in ('variant_tool_prefix','variant_ffperase_prefix','variant_ffperase_sif'))
            fill('#backend', 'conda')
            assert js("return document.querySelector('#docker-image-field').hidden")
            for field in ('variant_tool_prefix','variant_ffperase_prefix','variant_ffperase_sif'):fill('#'+field, '')
            report['checks'].append('Docker CNA+variants saves local image, omits host prefixes/SIFs, checks inputs without tools, and restores host controls')
            click('#variant-fresh')
            assert js("return variantPayload().variant_specimen_type") == 'fresh'
            assert js("return document.querySelector('#variant-ffperase-fields').hidden")
            assert js("return variantPayload().variant_ffperase === undefined")
            assert js("return document.querySelector('#variant-ffpe').getAttribute('aria-pressed')") == 'false'
            js("document.querySelector('#variant-fields').scrollIntoView()")
            (root / 'variant-preservation-form.png').write_bytes(base64.b64decode(wd('GET', '/screenshot')))
            click('#variants')
            report['checks'].append('Fresh/FFPE buttons switch explicitly; Illumina callers and ANNOVAR choice save/check without starting tools')
        scan_ont(fixture / 'ont', 2)
        assert js("return document.querySelector('.sample[data-id=\"0\"] small').textContent").startswith('69 FASTQs')
        assert js("return document.querySelectorAll('#fastq-rows tr').length") == 71
        assert js("return document.querySelectorAll('.sample[data-id=\"0\"] .fastq-files li').length") == 69
        fill('.sample[data-id="0"] .type-select', 'cancer')
        fill('.sample[data-id="1"] .type-select', 'normal')
        assert js("return document.querySelector('#caller').value") == 'qdnaseq'
        metadata = prepare('ont-project')
        assert [len(json.loads(r['fastq_files'])) for r in metadata] == [69,2]
        report["checks"].append("ONT barcode batches remain grouped as 69 and 2 files; Normal selects QDNAseq")
        if options.test_variants:
            model = root / 'clair3-fixture-model';model.mkdir();(model / 'fixture.txt').write_text('model-path fixture; never executed')
            click('#variants');click('#variant-fresh')
            assert js("return [...document.querySelectorAll('[data-variant-caller]')].map(e=>e.value)") == ['clair3','clairs_to']
            assert not js("return document.querySelector('#variant-clair3-field').hidden")
            fill('#variant_clair3_model', str(model));click('[data-variant-caller="clairs_to"]')
            fill('#backend', 'docker');fill('#docker_image', 'oncotracer:browser-fixture')
            assert not js("return document.querySelector('#variant-clairsto-field').hidden")
            fill('#variant_clairsto_platform', 'ont_fixture')
            prepare('ont-variants-project')
            config = load_flat_yaml(root / 'ont-variants-project/config/run.yml')
            assert config['variant_callers'] == 'clair3,clairs_to' and config['variant_specimen_type'] == 'fresh'
            assert config['variant_clair3_model'] == str(model) and config['variant_clairsto_platform'] == 'ont_fixture'
            fill('#backend', 'conda');click('#variants')
            report['checks'].append('ONT exposes only Clair3/ClairS-TO with explicit model/preset fields; config check starts no tools')
        # Exercise the ONT linking and resource form with real config validation.
        from tests.test_native_methylation import Fixture
        from oncotracer_cli.setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
        for classifier, source in (("sturgeon", "pod5"), ("marlin", "modbam")):
            resource_root = root / (classifier + "-resources")
            resource_root.mkdir()
            resources = Fixture(resource_root, classifier)
            bam_dir = resource_root / "bam_pass"
            bam_dir.mkdir()
            (bam_dir / "calls.bam").write_bytes(b"fixture-bam")
            click('#choose-ont')
            assert not js("return document.querySelector('#ont-inputs').hidden || document.querySelector('#ont-signal-inputs').hidden")
            fill('#ont-run-folder', str(resource_root));sample_count(1)
            assert js("return document.querySelector('#input-folder').value") == str(resources.fastq.parent)
            assert js("return document.querySelector('#ont-pod5').value") == str(resources.pod5)
            assert js("return document.querySelector('#ont-modbam').value") == str(bam_dir)
            fill('.sample[data-id="0"] .type-select', 'cancer')
            fill('#analysis', 'methylation');fill('#classifier', classifier);fill('#methylation-source', source)
            assert js("return document.querySelector('#methylation-input-note').textContent").endswith(str(resources.pod5 if source == 'pod5' else bam_dir))
            assert js("return document.querySelector('#dorado-model-fields').hidden") == (source != 'pod5')
            allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[classifier])
            for key, value in resources.config().items():
                if key in allowed:
                    fill('#' + key, str(value))
            if classifier == 'sturgeon':click('#license')
            # Native file navigation must include extensionless executables.
            click('[data-browse="methylation_modkit_executable"]')
            wait(lambda: js("return document.querySelector('#folders').textContent.includes('File · modkit')"), 'executable file picker')
            js("[...document.querySelectorAll('#folders button')].find(b=>b.textContent==='File · modkit').click()")
            prepare(classifier + '-methylation-project')
            from oncotracer_cli.runtime import load_flat_yaml
            config = load_flat_yaml(root / (classifier + '-methylation-project/config/run.yml'))
            assert config['methylation_classifier'] == classifier
            assert config['methylation_modkit_executable'] == str(resources.executables['modkit'])
            assert config['methylation_only'] is True
            assert ('methylation_pod5_dir' in config) == (source == 'pod5')
            assert ('methylation_modbam' in config) == (source == 'modbam')
            js("document.querySelector('#methylation-fields').scrollIntoView()")
            (root / (classifier + '-methylation-form.png')).write_bytes(base64.b64decode(wd('GET', '/screenshot')))
        report['checks'].append('ONT run links fastq_pass/barcodes, POD5 and BAMs; resource file picker and checked Modkit+Sturgeon/POD5 and Modkit+MARLIN/BAM configs')
        scan_ont(fixture / 'ligation', 1)
        assert js("return document.querySelector('#ont-pod5').value==='' && document.querySelector('#ont-modbam').value===''")
        fill('.sample[data-id="0"] .type-select', 'custom')
        fill('.sample[data-id="0"] .custom-label', 'research tag')
        fill('.sample[data-id="0"] .role', 'tumor')
        metadata = prepare('ligation-project')
        assert metadata[0]['sample_type'] == 'research tag'
        assert len(json.loads(metadata[0]['fastq_files'])) == 3
        report["checks"].append("nonbarcoded ligation files stay in one sample; custom tag preserved")
        # Check the real folder navigator and automatic discovery after selection.
        click('#choose-illumina');click('[data-browse="input-folder"]')
        fill('#browser-location', str(fixture / 'illumina'));click('#browser-go')
        wait(lambda: js("return document.querySelector('#browser-path').textContent===arguments[0] && !document.querySelector('#use-folder').disabled", str(fixture / 'illumina')), 'folder navigator')
        assert js("return document.querySelectorAll('#folders .fastq-file').length") == 4
        assert 'Home' in js("return document.querySelector('#browser-shortcuts').textContent")
        assert 'Mounted drives' in js("return document.querySelector('#browser-shortcuts').textContent")
        # Unsaved text must not select the previous directory.
        fill('#browser-location', str(fixture / 'ont'))
        assert js("return document.querySelector('#use-folder').disabled")
        # Clicking a breadcrumb returns to that folder without typing a path.
        js("[...document.querySelectorAll('#browser-breadcrumbs button')].at(-1).click()")
        wait(lambda: js("return !document.querySelector('#use-folder').disabled"), 'breadcrumb navigation')
        (root / 'folder-file-picker.png').write_bytes(base64.b64decode(wd('GET', '/screenshot')))
        click('#use-folder');sample_count(2)
        report["checks"].append("folder navigator selection automatically discovers Illumina pairs")
        js("document.querySelector('#fastq-preview').scrollIntoView()")
        (root / 'visible-fastqs.png').write_bytes(base64.b64decode(wd('GET', '/screenshot')))
        fill('#input-folder', str(fixture / 'missing-folder'))
        wait(lambda: js("return !document.querySelector('main').inert && !document.querySelector('#error').hidden"), 'failed discovery')
        assert js("return document.querySelector('#fastq-preview').hidden && document.querySelector('#samples-card').hidden")
        fill('#input-folder', str(fixture / 'illumina'));sample_count(2)
        report['checks'].append('folder picker lists FASTQs, has Home/mount/breadcrumb navigation, blocks stale selection; failed discovery clears inventory')
        assert before == {str(path): path.read_bytes() for path in fixture.rglob('*.gz')}
        report["checks"].append("input FASTQs unchanged; no analysis or reference downloads started")
        if options.test_stop:
            assert js("return document.querySelector('h1').textContent") == 'Configure and run an analysis'
            for remove in (False, True):
                if remove:
                    click('#new-analysis');click('#choose-illumina')
                    fill('#input-folder', str(fixture / 'illumina'));sample_count(2)
                fill('.sample[data-id="0"] .type-select', 'cancer')
                name = 'stop-remove-project' if remove else 'stop-keep-project'
                prepare(name);click('#run')
                wait(lambda: js("return !document.querySelector('#stop').hidden && !document.querySelector('#stop').disabled"), 'enabled Stop button')
                wait(lambda: js("return document.querySelector('#logs').textContent.includes('Dummy analysis ready')"), 'dummy process readiness')
                assert js("return document.querySelector('#run-step-eta').textContent").startswith('About ')
                assert js("return document.querySelector('#run-overall-eta').textContent") == 'Not yet known'
                assert '50%' in js("return document.querySelector('#run-stage').textContent")
                assert 'file only' in js("return document.querySelector('#run-eta-note').textContent")
                if not remove:
                    (root / 'run-eta.png').write_bytes(base64.b64decode(wd('GET', '/screenshot')))
                    report['checks'].append('elapsed time and scoped download ETA shown; unknown overall ETA is explicit')
                click('#stop')
                wait(lambda: js("return document.querySelector('#cleanup').open"), 'cleanup choice after Stop')
                assert js("return document.activeElement.id") == 'keep-project'
                assert (root / name).is_dir()
                if not remove:
                    click('#keep-project');assert (root / name / 'config/run.yml').is_file()
                else:
                    click('#remove-project');assert js("return document.querySelector('#confirm-remove').disabled")
                    fill('#confirm-path',str(root));assert js("return document.querySelector('#confirm-remove').disabled")
                    fill('#confirm-path',str(root / name));click('#confirm-remove')
                    wait(lambda: js("return !document.querySelector('#cleanup').open"), 'confirmed removal')
                    assert not (root / name).exists()
                report['checks'].append('Stop button with ' + ('confirmed folder removal' if remove else 'default Keep project'))
            assert before == {str(path): path.read_bytes() for path in fixture.rglob('*.gz')}
            click('#new-analysis');click('#choose-illumina')
        # A stopped local server must give actionable recovery instructions.
        processes[0].terminate(); processes[0].wait(timeout=10)
        click('[data-browse="input-folder"]')
        wait(lambda: js("return !document.querySelector('#browser-error').hidden"), 'disconnect instructions')
        message = js("return document.querySelector('#browser-error').textContent")
        assert 'Cannot reach OncoTracer' in message and 'NEW complete URL' in message
        report["checks"].append("stopped server shows reconnect instructions in the folder navigator")
        report["passed"] = True
    finally:
        if session:
            if not report["passed"]:
                try:
                    report["page_error"] = js("return document.querySelector('#error').textContent")
                    report["samples"] = js("return [...document.querySelectorAll('.sample')].map(e=>({id:e.dataset.id,parent:e.parentElement.id,type:e.querySelector('.type-select').value}))")
                    (root / "failure.png").write_bytes(base64.b64decode(wd("GET", "/screenshot")))
                except Exception: pass
            try: http("DELETE", session)
            except Exception: pass
        for process in reversed(processes):
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in handles: handle.close()
        (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
