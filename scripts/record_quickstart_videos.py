#!/usr/bin/env python3
"""Record silent QuickStart walkthroughs of the real UI using a synthetic API.

Requires Playwright, a browser, ffmpeg and ffprobe. All browser profiles and raw
recordings stay in --work-dir. No local setup server, reads, tools, downloads or
analysis are used; /api/run is forbidden. Default outputs are docs media only.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs/assets/tutorial"

OVERLAY = r"""
<style>
html{scroll-padding-top:70px;scroll-padding-bottom:140px}body{padding-top:44px;padding-bottom:120px}
#recording-label{position:fixed;inset:0 0 auto;z-index:9999;background:#123c45;color:#fff;padding:10px 24px;font:600 14px/24px system-ui;display:flex;justify-content:space-between;gap:20px}
#recording-label span{font-weight:400;color:#d9eceb}
#recording-caption{position:fixed;inset:auto 0 0;z-index:9998;background:#102e39;color:#fff;min-height:112px;padding:17px max(30px,calc((100vw - 1100px)/2));border-top:4px solid #6ed8c0;pointer-events:none}
#recording-step{font:700 13px/22px system-ui;text-transform:uppercase;letter-spacing:1px;color:#86e4cd;margin-bottom:5px}
#recording-text{font:500 21px/1.45 system-ui;max-width:1130px}
#recording-pointer{position:fixed;z-index:10001;width:18px;height:18px;border-radius:50%;border:3px solid #112f39;background:#ffd673;box-shadow:0 0 0 3px #ffffffbb;pointer-events:none;transform:translate(-50%,-50%);left:-30px;top:-30px}
#recording-focus{position:fixed;z-index:9996;border:3px solid #db8d12;border-radius:8px;box-shadow:0 0 0 4px #ffe5a355;pointer-events:none;display:none}
</style>
<div id="recording-label"><strong>OncoTracer · browser walkthrough</strong><span>DEMO · synthetic inventory and checks · no analysis run</span></div>
<div id="recording-caption"><div id="recording-step"></div><div id="recording-text"></div></div>
<div id="recording-pointer"></div><div id="recording-focus"></div>
<script>document.addEventListener('mousemove',e=>{const p=document.getElementById('recording-pointer');p.style.left=e.clientX+'px';p.style.top=e.clientY+'px';});</script>
"""


def fixture(kind):
    if kind == "resume":
        return {"mode": "ont", "project": "/demo/oncotracer_outputs/ONT_fresh",
                "folder": "/demo/ONT", "records": []}
    if kind == "hcc":
        folder = "/demo/oncotracer-quickstart2/input"
        names = ["HCC1143_DMSO", "HCC1143_BEZ235", "HCC1143_TRAMETINIB"]
        records = [
            {
                "id": i,
                "name": n,
                "barcode": "",
                "files": [f"{folder}/{n}_R{m}.fastq.gz" for m in (1, 2)],
                "file_count": 2,
                "layout": "paired-end",
            }
            for i, n in enumerate(names)
        ]
        return {
            "mode": "illumina",
            "folder": folder,
            "project": "/demo/oncotracer-quickstart2/analysis",
            "records": records,
        }
    folder = "/demo/oncotracer-quickstart1/input/" + (
        "fastq_pass" if kind == "ont" else "illumina"
    )
    record = {
        "id": 0,
        "name": "barcode01" if kind == "ont" else "ERR12341627",
        "barcode": "barcode01" if kind == "ont" else "",
        "files": (
            [folder + "/barcode01/DRR165691_1.fastq.gz"]
            if kind == "ont"
            else [folder + f"/ERR12341627_{m}.fastq.gz" for m in (1, 2)]
        ),
        "file_count": 1 if kind == "ont" else 2,
        "layout": "ONT batches" if kind == "ont" else "paired-end",
    }
    return {
        "mode": kind,
        "folder": folder,
        "project": "/demo/oncotracer-quickstart1/" + kind,
        "records": [record],
    }


def document(kind):
    from oncotracer_cli.web_ui import PAGE
    from oncotracer_cli.variant_install_help import (
        installation_guides,
        official_resource_help,
    )

    mock = (ROOT / "docs/assets/setup-demo/mock-api.js").read_text()
    mock = mock.replace(
        "__VARIANT_INSTALL_GUIDES__",
        json.dumps(
            {
                b: installation_guides(["annovar"], backend=b, mode="illumina")
                for b in ("host", "docker")
            }
        ),
    )
    mock = mock.replace(
        "__ANNOVAR_RESOURCE_HELP__", json.dumps(official_resource_help("annovar"))
    )
    values = fixture(kind)
    adapter = r"""
const recordingFixture=__FIXTURE__;
const recordingOriginalApi=demoApi;
demoApi=function(path,payload){
 const name=new URL(path,location.href).pathname;
 if(name==='/api/run')throw Error('Recording safeguard: analysis and simulation execution are disabled.');
 if(name==='/api/system'){
  const system=recordingOriginalApi(path,payload);
  system.start_dir='/demo';system.suggested_threads=8;
  system.defaults={mode:recordingFixture.mode,input_folder:recordingFixture.folder,project:recordingFixture.project,backend:'conda',reference:'download',analysis:'cna',variants:false};
  return system;
 }
 if(name==='/api/resume'&&recordingFixture.mode==='ont'&&recordingFixture.records.length===0){
  if(payload.project!==recordingFixture.project)throw Error('Select the synthetic output project shown in this walkthrough.');
  demoState.prepared={id:'synthetic-resume',project:payload.project,
   config_path:payload.project+'/config/run.yml',outdir:payload.project+'/results',
   backend:'conda',valid:true,
   config:'mode: ont\nexecution_backend: conda\nont_caller: ichorcna\nont_binsize_kb: 500\nthreads: 4\n',
   check:{errors:[],warnings:['DEMO: 2 recorded stages verified; 1 incomplete stage needs recomputation. Real projects check completion records and outputs before reuse.']},
   reviewed_summary:{samples:[{name:'ONCO-001',sample_type:'cancer'},{name:'CTRL-001',sample_type:'normal'}]}};
  return demoState.prepared;
 }
 if(name==='/api/scan'){
  if(payload.mode!==recordingFixture.mode||payload.folder!==recordingFixture.folder)throw Error('The walkthrough only contains its named synthetic inventory.');
  demoState.scan={scan_id:'recording-synthetic-'+(++demoState.revision),mode:payload.mode,root:payload.folder,samples:recordingFixture.records,warnings:['Recording demo: synthetic inventory with the QuickStart filenames. No public reads were downloaded or scanned.']};
  return demoState.scan;
 }
 if(name==='/api/ont-inputs')return {run:'',fastq:recordingFixture.folder,pod5:'',modbam:'',notes:['This FASTQ-only example does not contain methylation inputs.']};
 return recordingOriginalApi(path,payload);
};
""".replace(
        "__FIXTURE__", json.dumps(values)
    )
    # The application PAGE and its controls remain unchanged; only its service
    # responses are synthetic. The visible recording layer states that boundary.
    result = PAGE.replace(
        "<script>", "<script>\n" + mock + "\n" + adapter + "\n</script>\n<script>", 1
    )
    result = result.replace("<body>", "<body>" + OVERLAY, 1)
    return result


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}"


class Recording:
    def __init__(self, page, pace):
        self.page = page
        self.pace = pace
        self.started = time.monotonic()
        self.cues = []
        self.states = []
        self.scene_windows = []
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))

    def pause(self, seconds):
        self.page.wait_for_timeout(seconds * 1000 * self.pace)

    def caption(self, title, text):
        now = time.monotonic() - self.started
        if self.cues:
            self.cues[-1]["end"] = now
        self.cues.append({"start": now, "end": None, "title": title, "text": text})
        self.page.evaluate(
            "([title,text])=>{document.getElementById('recording-step').textContent=title;document.getElementById('recording-text').textContent=text;}",
            [title, text],
        )

    def focus(self, selector, block="center"):
        loc = self.page.locator(selector).first
        loc.evaluate("(e,b)=>e.scrollIntoView({behavior:'smooth',block:b})", block)
        self.pause(0.65)
        loc.evaluate(
            "e=>{let r=e.getBoundingClientRect(),f=document.getElementById('recording-focus');Object.assign(f.style,{display:'block',left:(r.x-5)+'px',top:(r.y-5)+'px',width:(r.width+10)+'px',height:(r.height+10)+'px'});}"
        )
        box = loc.bounding_box()
        if box:
            self.page.mouse.move(
                box["x"] + box["width"] * 0.65,
                box["y"] + min(box["height"] / 2, 30),
                steps=12,
            )
        return loc

    def clear_focus(self):
        self.page.locator("#recording-focus").evaluate("e=>e.style.display='none'")

    def click(self, selector):
        loc = self.focus(selector)
        self.pause(0.45)
        loc.click()
        self.pause(0.5)
        self.clear_focus()

    def select(self, selector, value):
        loc = self.focus(selector)
        self.pause(0.55)
        loc.select_option(value)
        self.pause(1.15)
        self.clear_focus()

    def fill(self, selector, value):
        loc = self.focus(selector)
        loc.click()
        loc.press("ControlOrMeta+A")
        loc.press_sequentially(value, delay=65 * self.pace)
        loc.press("Tab")
        self.pause(0.65)
        self.clear_focus()

    def scene(self, kind, poster=None):
        self.page.route(
            "**/*",
            lambda route: (
                route.fulfill(status=200, content_type="text/html", body=document(kind))
                if route.request.is_navigation_request()
                and route.request.url.startswith("https://oncotracer-demo.invalid/")
                else route.abort()
            ),
        )
        self.page.goto(
            "https://oncotracer-demo.invalid/" + kind + "#recording-only",
            wait_until="load",
        )
        self.page.wait_for_function("!document.querySelector('main').inert")
        assert self.page.locator("#error").is_hidden(), self.page.locator(
            "#error"
        ).inner_text()
        self.page.evaluate("window.scrollTo(0,0)")
        self.page.mouse.move(1215, 90)
        self.pause(0.8)
        title = {
            "illumina": "QuickStart 1 · Illumina",
            "ont": "QuickStart 1 · Oxford Nanopore",
            "hcc": "QuickStart 2 · HCC1143 cohort",
            "resume": "Continue an existing analysis",
        }[kind]
        self.caption(
            title,
            "Run oncotracer --interactive --backend conda. Choose New analysis or Continue an existing analysis. This is a synthetic demo.",
        )
        scene_start = self.cues[-1]["start"]
        self.pause(5)
        if poster:
            self.page.screenshot(path=str(poster), type="jpeg", quality=90)
        assert self.page.locator('#resume-card').is_hidden()
        assert self.page.locator('#platform-card').is_hidden()
        if kind == "resume":
            self.caption(title + " · open the saved project",
                         "Stop the project's current run. Choose Continue an existing analysis, then select its output project containing config/run.yml and results.")
            self.click('#choose-existing-analysis')
            assert self.page.locator('#platform-card').is_hidden()
            self.fill('#resume-project', fixture(kind)['project'])
            self.pause(3)
            self.caption(title + " · validate completed work",
                         "Load saved settings and check. Saved samples and settings stay together. Incomplete alignments are not accepted as completed work.")
            self.click('#resume-project-button')
            self.page.wait_for_selector('#review-card:not([hidden])')
            self.focus('#review-card', 'start')
            assert self.page.locator('#run').is_enabled()
            self.pause(5)
            self.caption(title + " · resume when ready",
                         "Review the checks, then choose Run analysis to continue. This recording stops here; its checks are simulated and no analysis runs.")
            self.focus('#run')
            self.pause(5)
            state = self.page.evaluate('({requests:demoState.requests,valid:demoState.prepared.valid,config:demoState.prepared.config})')
            assert '/api/run' not in state['requests'] and not self.errors
            self.states.append({'scene': kind, **state})
            self.clear_focus()
            self.scene_windows.append({'scene': kind, 'start': scene_start, 'end': time.monotonic() - self.started})
            self.page.unroute('**/*')
            return
        self.caption(title + " · new analysis",
                     "Choose New analysis to open sample setup. Continue an existing analysis is for a saved output project.")
        self.click('#choose-new-analysis')
        assert self.page.locator('#resume-card').is_hidden()
        self.caption(
            title + " · preservation",
            "Choose preservation from specimen records. Fresh is selected only as demonstration metadata here.",
        )
        self.focus("#specimen-card")
        self.pause(2)
        self.click("#variant-fresh")
        self.pause(2)
        self.caption(
            title + " · discover inputs",
            (
                "Review the prefilled FASTQ folder, then choose Discover samples. Each Illumina read pair stays together."
                if kind != "ont"
                else "Choose fastq_pass. Each barcode stays one sample, even when it contains several FASTQ batches."
            ),
        )
        self.focus("#input-folder")
        self.pause(3)
        self.click("#scan")
        self.page.wait_for_selector("#samples-card:not([hidden])")
        self.pause(1)
        self.focus("#fastq-preview", "start")
        self.pause(2)
        self.click("#fastq-rows details summary")
        self.pause(3)
        self.caption(
            title + " · sample groups",
            (
                "Assign all three libraries to Cancer. DMSO is a treatment control, not a normal genome."
                if kind == "hcc"
                else "Assign the example sample to Cancer. Use the type dropdown; you can also drag cards between groups."
            ),
        )
        self.focus("#samples-card", "start")
        self.pause(2)
        for record in fixture(kind)["records"]:
            self.select(f'.sample[data-id="{record["id"]}"] .type-select', "cancer")
        if kind == "ont":
            self.caption(
                title + " · sample name",
                "Rename barcode01 to DRR165691. The barcode still identifies the same FASTQ input.",
            )
            self.fill('.sample[data-id="0"] .sample-name', "DRR165691")
            self.pause(2)
        elif kind == "hcc":
            self.focus("#cancer-samples", "start")
            self.pause(4)
        else:
            self.pause(2)
        self.caption(
            title + " · CNA settings",
            (
                "Keep Copy-number analysis and set a CPU budget. This Illumina example uses QDNAseq with 100 kb bins."
                if kind != "ont"
                else "Keep Copy-number analysis, ichorCNA and 500 kb bins. FASTQs alone do not provide methylation calls."
            ),
        )
        self.focus("#settings-card", "start")
        self.pause(2)
        self.fill("#threads", "4")
        assert self.page.locator("#analysis").input_value() == "cna"
        assert self.page.locator("#binsize").input_value() == (
            "500" if kind == "ont" else "100"
        )
        assert not self.page.locator("#variants").is_checked()
        self.focus("#binsize")
        self.pause(3)
        self.caption(
            title + " · tools and reference",
            "Choose the backend you installed. Keep prepared hg38 download; real downloads begin only when you start analysis.",
        )
        self.select("#backend", "conda")
        self.focus("#reference")
        self.pause(3)
        self.caption(
            title + " · separate project",
            "Confirm a distinct project folder. Keep optional short-variant analysis off for this CNA QuickStart.",
        )
        self.focus("#project-parent")
        self.pause(2)
        self.focus("#project-name")
        self.pause(3)
        self.caption(
            title + " · save and review",
            "Save configuration and check. Resolve any errors before Run analysis. These displayed checks are simulated.",
        )
        self.click("#prepare")
        assert self.page.locator("#error").is_hidden(), self.page.locator(
            "#error"
        ).inner_text()
        self.page.wait_for_selector("#review-card:not([hidden])")
        self.focus("#review-card", "start")
        self.pause(4)
        assert self.page.locator("#run").is_enabled()
        state = self.page.evaluate(
            "({requests:demoState.requests,payload:demoState.lastPayload,valid:demoState.prepared.valid,config:demoState.prepared.config})"
        )
        assert "/api/run" not in state["requests"]
        assert all(s["type"] == "cancer" for s in state["payload"]["samples"])
        assert state["payload"]["threads"] == 4 and not state["payload"]["variants"]
        assert (
            state["payload"]["backend"] == "conda"
            and state["payload"]["reference"] == "download"
        )
        assert not self.errors, self.errors
        self.states.append({"scene": kind, **state})
        self.caption(
            title + " · ready for your run",
            "In a real project, choose Run analysis after checks pass and keep its terminal open. This demo stops here; no analysis was run.",
        )
        self.focus("#run")
        self.pause(6)
        self.clear_focus()
        self.scene_windows.append(
            {
                "scene": kind,
                "start": scene_start,
                "end": time.monotonic() - self.started,
            }
        )
        self.page.unroute("**/*")

    def finish(self):
        if self.cues:
            self.cues[-1]["end"] = time.monotonic() - self.started
        return self.cues


def run_checked(command):
    return subprocess.run(
        command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def derive_platform_videos(raw_video, output_dir, manifest, windows, ffmpeg, ffprobe):
    """Export each platform from the original capture, leaving approved media intact."""
    source = next(
        v for v in manifest["videos"] if v["name"] == "quick-start-illumina-ont"
    )
    originals = {
        v["name"]: copy.deepcopy(v)
        for v in manifest["videos"]
        if v["name"] in {"quick-start-illumina-ont", "quick-start-hcc1143"}
    }
    for video in originals.values():
        for asset in video["files"].values():
            assert (
                hashlib.sha256((output_dir / asset["name"]).read_bytes()).hexdigest()
                == asset["sha256"]
            )
    assert (
        raw_video.suffix == ".webm" and raw_video.is_file()
    ), "Use the original Playwright WebM capture."
    capture_sha = hashlib.sha256(raw_video.read_bytes()).hexdigest()
    windows = {w["scene"]: w for w in windows}
    additions = []
    for scene, title in (
        ("illumina", "QuickStart 1 · Illumina"),
        ("ont", "QuickStart 1 · Oxford Nanopore"),
    ):
        window = windows[scene]
        start, end = float(window["start"]), float(window["end"])
        assert 0 <= start < end <= source["duration_seconds"] + 0.1
        name = "quick-start-" + scene
        output = output_dir / (name + ".mp4")
        run_checked(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(start),
                "-i",
                str(raw_video),
                "-t",
                str(end - start),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "25",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "24",
                "-threads",
                "4",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        probe = json.loads(
            run_checked(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(output),
                ]
            ).stdout
        )
        stream = probe["streams"][0]
        duration = float(probe["format"]["duration"])
        assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
        assert (stream["width"], stream["height"]) == (1280, 900)
        data = output.read_bytes()
        assert len(data) < 25 * 1024 * 1024 and 0 < data.find(b"moov") < data.find(
            b"mdat"
        )
        poster_time = start + min(2.0, (end - start) / 2)
        run_checked(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(poster_time),
                "-i",
                str(raw_video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_dir / (name + ".jpg")),
            ]
        )
        cues = []
        for original in source["cues"]:
            if not original["title"].startswith(title):
                continue
            cue = copy.deepcopy(original)
            cue["start"] = max(0.0, original["start"] - start)
            cue["end"] = min(duration, end - start, original["end"] - start)
            if cue["end"] > cue["start"]:
                cues.append(cue)
        assert cues and all(0 <= c["start"] < c["end"] <= duration for c in cues)
        vtt = "WEBVTT\n\n" + "".join(
            f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["title"]}\n{c["text"]}\n\n'
            for i, c in enumerate(cues, 1)
        )
        (output_dir / (name + ".vtt")).write_text(vtt.rstrip("\n") + "\n")
        transcript = (
            "Silent browser walkthrough. Synthetic inventory and checks; no analysis or downloads.\n\n"
            + "".join(
                f'{stamp(c["start"])}  {c["title"]}\n{c["text"]}\n\n' for c in cues
            )
        )
        (output_dir / (name + ".txt")).write_text(transcript.rstrip("\n") + "\n")
        files = {
            suffix: {
                "name": name + suffix,
                "sha256": hashlib.sha256(
                    (output_dir / (name + suffix)).read_bytes()
                ).hexdigest(),
                "bytes": (output_dir / (name + suffix)).stat().st_size,
            }
            for suffix in (".mp4", ".jpg", ".vtt", ".txt")
        }
        additions.append(
            {
                "name": name,
                "duration_seconds": duration,
                "codec": "h264",
                "pixel_format": "yuv420p",
                "faststart": True,
                "resolution": [1280, 900],
                "cues": cues,
                "scenes": [
                    copy.deepcopy(s) for s in source["scenes"] if s["scene"] == scene
                ],
                "files": files,
                "derivation": {
                    "method": "transcoded_from_original_browser_capture",
                    "source_capture_name": raw_video.name,
                    "source_capture_sha256": capture_sha,
                    "approved_combined_video": source["files"][".mp4"],
                    "source_window_seconds": [start, end],
                    "captions_rebased": True,
                    "inter_scene_navigation_excluded": True,
                    "poster_source_seconds": poster_time,
                },
            }
        )
        print(
            json.dumps(
                {
                    "video": name,
                    "seconds": duration,
                    "bytes": output.stat().st_size,
                    "analysis_started": False,
                }
            ),
            flush=True,
        )
    for video in originals.values():
        for asset in video["files"].values():
            assert (
                hashlib.sha256((output_dir / asset["name"]).read_bytes()).hexdigest()
                == asset["sha256"]
            )
    manifest["videos"] = [
        v for v in manifest["videos"] if v["name"] not in {a["name"] for a in additions}
    ] + additions
    assert all(
        next(v for v in manifest["videos"] if v["name"] == name) == old
        for name, old in originals.items()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--split-existing",
        type=Path,
        help="Derive separate platform videos from an existing original WebM; no browser is started.",
    )
    parser.add_argument(
        "--illumina-clip", type=float, nargs=2, metavar=("START", "END")
    )
    parser.add_argument("--ont-clip", type=float, nargs=2, metavar=("START", "END"))
    parser.add_argument("--output-dir", type=Path, default=MEDIA)
    parser.add_argument("--playwright-path", type=Path)
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--browser", choices=("firefox", "chromium"), default="firefox")
    parser.add_argument(
        "--playwright-browsers",
        type=Path,
        help="Existing Playwright browser/recorder cache, if outside the default location.",
    )
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument(
        "--pace",
        type=float,
        default=1.0,
        help="Use 1 for publication; smaller values test interactions only.",
    )
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        parser.error("ffmpeg and ffprobe are required")
    if args.pace <= 0:
        parser.error("--pace must be positive")
    if args.split_existing:
        if not args.illumina_clip or not args.ont_clip:
            parser.error(
                "--split-existing requires both --illumina-clip and --ont-clip"
            )
        manifest_path = args.output_dir / "quick-start-recording.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["passed"] and not manifest["analysis_started"]
        windows = [
            {"scene": scene, "start": bounds[0], "end": bounds[1]}
            for scene, bounds in (
                ("illumina", args.illumina_clip),
                ("ont", args.ont_clip),
            )
        ]
        derive_platform_videos(
            args.split_existing,
            args.output_dir,
            manifest,
            windows,
            args.ffmpeg,
            args.ffprobe,
        )
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print("PASS: platform clips derived; existing approved assets preserved.")
        return
    if not args.work_dir:
        parser.error("--work-dir is required when recording the browser")
    args.work_dir.mkdir(parents=True, exist_ok=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.playwright_path:
        sys.path.insert(0, str(args.playwright_path.resolve()))
    sys.path.insert(0, str(ROOT))
    if args.playwright_browsers:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(args.playwright_browsers.resolve())
    from playwright.sync_api import sync_playwright
    from oncotracer_cli.web_ui import PAGE

    isolation = args.work_dir / "isolation"
    isolated_env = os.environ.copy()
    for variable, relative in [
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"),
    ]:
        directory = isolation / relative
        directory.mkdir(parents=True)
        isolated_env[variable] = str(directory.resolve())
    manifest = {
        "schema": 1,
        "passed": False,
        "analysis_started": False,
        "real_reads_downloaded": False,
        "api": "synthetic browser demo",
        "ui_page_sha256": hashlib.sha256(PAGE.encode()).hexdigest(),
        "mock_api_sha256": hashlib.sha256(
            (ROOT / "docs/assets/setup-demo/mock-api.js").read_bytes()
        ).hexdigest(),
        "source_files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in ('scripts/record_quickstart_videos.py', 'oncotracer_cli/resume_ui.py')},
        "pace": args.pace,
        "videos": [],
    }
    combined_capture = None
    combined_windows = None
    with sync_playwright() as p:
        launch = {"headless": True, "env": isolated_env}
        if args.browser_path:
            launch["executable_path"] = str(args.browser_path)
        browser = getattr(p, args.browser).launch(**launch)
        manifest['browser'] = {'name': args.browser, 'version': browser.version}
        try:
            for name, scenes in [
                ("quick-start-illumina-ont", ["illumina", "ont"]),
                ("quick-start-hcc1143", ["hcc"]),
                ("continue-analysis", ["resume"]),
            ]:
                raw = args.work_dir / name
                raw.mkdir()
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    record_video_dir=str(raw),
                    record_video_size={"width": 1280, "height": 900},
                    locale="en-US",
                    color_scheme="light",
                    reduced_motion="no-preference",
                )
                page = context.new_page()
                recording = Recording(page, args.pace)
                for i, kind in enumerate(scenes):
                    recording.scene(
                        kind, args.output_dir / (name + ".jpg") if i == 0 else None
                    )
                cues = recording.finish()
                video = page.video
                context.close()
                raw_video = Path(video.path())
                if name == "quick-start-illumina-ont":
                    combined_capture = raw_video
                    combined_windows = recording.scene_windows
                output = args.output_dir / (name + ".mp4")
                run_checked(
                    [
                        args.ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(raw_video),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "4",
                        "-preset",
                        "medium",
                        "-crf",
                        "25",
                        "-pix_fmt",
                        "yuv420p",
                        "-r",
                        "24",
                        "-movflags",
                        "+faststart",
                        str(output),
                    ]
                )
                probe = json.loads(
                    run_checked(
                        [
                            args.ffprobe,
                            "-v",
                            "error",
                            "-show_format",
                            "-show_streams",
                            "-of",
                            "json",
                            str(output),
                        ]
                    ).stdout
                )
                stream = probe["streams"][0]
                assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
                assert (stream["width"], stream["height"]) == (1280, 900)
                assert (
                    output.stat().st_size < 25 * 1024 * 1024
                ), "Video exceeds the per-file size budget"
                data = output.read_bytes()
                assert (
                    0 < data.find(b"moov") < data.find(b"mdat")
                ), "faststart metadata must precede media"
                # Video framing can end a fraction before the wall-clock cue.
                cues[-1]["end"] = min(
                    cues[-1]["end"], float(probe["format"]["duration"])
                )
                vtt = "WEBVTT\n\n" + "".join(
                    f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["title"]}\n{c["text"]}\n\n'
                    for i, c in enumerate(cues, 1)
                )
                (args.output_dir / (name + ".vtt")).write_text(vtt.rstrip("\n") + "\n")
                transcript = (
                    "Silent browser walkthrough. Synthetic inventory and checks; no analysis or downloads.\n\n"
                    + "".join(
                        f'{stamp(c["start"])}  {c["title"]}\n{c["text"]}\n\n'
                        for c in cues
                    )
                )
                (args.output_dir / (name + ".txt")).write_text(
                    transcript.rstrip("\n") + "\n"
                )
                files = {
                    suffix: {
                        "name": name + suffix,
                        "sha256": hashlib.sha256(
                            (args.output_dir / (name + suffix)).read_bytes()
                        ).hexdigest(),
                        "bytes": (args.output_dir / (name + suffix)).stat().st_size,
                    }
                    for suffix in (".mp4", ".jpg", ".vtt", ".txt")
                }
                manifest["videos"].append(
                    {
                        "name": name,
                        "duration_seconds": float(probe["format"]["duration"]),
                        "codec": "h264",
                        "pixel_format": "yuv420p",
                        "faststart": True,
                        "resolution": [1280, 900],
                        "cues": cues,
                        "scenes": recording.states,
                        "files": files,
                    }
                )
                print(
                    json.dumps(
                        {
                            "video": name,
                            "seconds": probe["format"]["duration"],
                            "bytes": output.stat().st_size,
                            "analysis_started": False,
                        }
                    ),
                    flush=True,
                )
        finally:
            browser.close()
    derive_platform_videos(
        combined_capture,
        args.output_dir,
        manifest,
        combined_windows,
        args.ffmpeg,
        args.ffprobe,
    )
    manifest["passed"] = True
    (args.output_dir / "quick-start-recording.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(
        "PASS: walkthroughs recorded and platform clips derived; all requests and saved demo selections verified.",
        flush=True,
    )


if __name__ == "__main__":
    main()
