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
        drag('.sample[data-id="0"] .drag-handle', '#cancer-samples')
        assert js("return document.querySelectorAll('#cancer-samples .sample').length") == 1
        drag('.sample[data-id="1"] .drag-handle', '#normal-samples')
        assert js("return document.querySelectorAll('#normal-samples .sample').length") == 1
        assert not js("return document.querySelector('#gistic').disabled")
        report["checks"].append("real pointer drag-and-drop assigns Cancer and Normal; cohort enables GISTIC")
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
        scan_ont(fixture / 'ont', 2)
        assert js("return document.querySelector('.sample[data-id=\"0\"] small').textContent").startswith('69 FASTQs')
        fill('.sample[data-id="0"] .type-select', 'cancer')
        fill('.sample[data-id="1"] .type-select', 'normal')
        assert js("return document.querySelector('#caller').value") == 'qdnaseq'
        metadata = prepare('ont-project')
        assert [len(json.loads(r['fastq_files'])) for r in metadata] == [69,2]
        report["checks"].append("ONT barcode batches remain grouped as 69 and 2 files; Normal selects QDNAseq")
        scan_ont(fixture / 'ligation', 1)
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
        click('#use-folder');sample_count(2)
        report["checks"].append("folder navigator selection automatically discovers Illumina pairs")
        assert before == {str(path): path.read_bytes() for path in fixture.rglob('*.gz')}
        report["checks"].append("input FASTQs unchanged; no analysis or reference downloads started")
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
