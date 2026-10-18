#!/usr/bin/env python3
"""Record the actual CpG assignment UI and saves using isolated synthetic fixtures.

A loopback WebServer performs real discovery, setup and configuration checks.
Tiny test inputs/resources are deliberately not scientific data or trained models.
The server and browser both forbid analysis; all temporary writes use --work-dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from record_quickstart_videos import OVERLAY, Recording, run_checked, stamp

NAME = "post-ont-cpg"
SAMPLE_NAMES = ["DEMO_LEUKEMIA", "DEMO_CNS", "DEMO_UNKNOWN"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/assets/tutorial")
    parser.add_argument("--playwright-path", type=Path, required=True)
    parser.add_argument("--playwright-browsers", type=Path, required=True)
    parser.add_argument("--browser", choices=("firefox", "chromium"), default="firefox")
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe or args.pace <= 0:
        parser.error("ffmpeg, ffprobe and a positive pace are required")
    args.work_dir.mkdir(parents=True, exist_ok=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith(("PYTHON", "CONDA_", "ONCOTRACER_")):
            os.environ.pop(key, None)
    for key, name in [("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                      ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                      ("TMPDIR", "tmp")]:
        folder = args.work_dir / "isolation" / name
        folder.mkdir(parents=True)
        os.environ[key] = str(folder)
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                      PLAYWRIGHT_BROWSERS_PATH=str(args.playwright_browsers))
    sys.path.append(str(args.playwright_path))
    from playwright.sync_api import sync_playwright
    from oncotracer_cli import web
    from oncotracer_cli.cli import build_parser
    from oncotracer_cli.reference_bundle import _files, reference_paths
    from oncotracer_cli.runtime import load_flat_yaml, render_flat_yaml
    from oncotracer_cli.setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
    from tests.test_native_methylation import Fixture
    from tests.test_web import HARDWARE

    demo = args.work_dir / "demo"
    demo.mkdir()
    import gzip
    for number in (1, 2, 3):
        folder = demo / "ont-run/fastq_pass" / f"barcode{number:02d}"
        folder.mkdir(parents=True)
        with gzip.open(folder / "synthetic.fastq.gz", "wt") as handle:
            handle.write("@synthetic-read\nACGT\n+\nIIII\n")
    bam = demo / "ont-run/bam_pass/synthetic.mod.bam"
    bam.parent.mkdir()
    bam.write_bytes(b"synthetic BAM path fixture; never processed")
    pod5 = demo / "ont-run/pod5_pass"
    pod5.mkdir()
    (pod5 / "synthetic.pod5").write_bytes(b"synthetic POD5 path fixture; never processed")
    reference = demo / "prepared-hg38"
    for name in _files():
        path = reference_paths(reference)[0] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic reference path fixture; never processed")
    resources = {}
    for classifier in ("marlin", "sturgeon"):
        directory = demo / "resources" / classifier
        directory.mkdir(parents=True)
        fixture = Fixture(directory, classifier)
        allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES[classifier])
        resources.update({key: value for key, value in fixture.config().items()
                          if key in allowed or key.endswith(("_sha256", "_interface_contract_commit"))})
    resource_yaml = demo / "resources/methylation.yml"
    resource_yaml.write_text(render_flat_yaml(resources))
    projects = demo / "projects"
    projects.mkdir()
    state = web.WebState(demo)
    state.hardware = dict(HARDWARE, gpu_note="Synthetic recording hardware; no GPU job is used.")
    state.setup_args = build_parser().parse_args([
        "setup", "--mode", "ont", "--analysis", "methylation", "--backend", "conda",
        "--input-folder", str(demo / "ont-run/fastq_pass"),
        "--project", str(projects / "cpg-marlin"), "--threads", "4"])
    forbidden = []

    def forbid_run(*_args, **_kwargs):
        forbidden.append("server analysis request")
        raise RuntimeError("Recording safeguard: analysis is disabled")

    state.run = forbid_run
    original_page = web.PAGE
    overlay = OVERLAY.replace("OncoTracer · browser walkthrough", "OncoTracer · post-ONT CpG groups").replace(
        "DEMO · synthetic inventory and checks · no analysis run",
        "DEMO · synthetic reads/resources · no analysis run")
    page_html = original_page.replace("<body>", "<body>" + overlay, 1)
    files_before = {str(path.relative_to(demo)): digest(path) for path in demo.rglob("*") if path.is_file()}
    checks, calls, requests, drags, browser_errors = [], [], [], [], []
    server = None
    try:
        with patch.object(web, "PAGE", page_html), patch("oncotracer_cli.cli._load_install_config", return_value={}):
            server = web.WebServer(0, state)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            with sync_playwright() as playwright:
                launch = {"headless": True, "env": os.environ.copy()}
                if args.browser_path:
                    launch["executable_path"] = str(args.browser_path)
                browser = getattr(playwright, args.browser).launch(**launch)
                version = browser.version
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    record_video_dir=str(args.work_dir / "raw"),
                    record_video_size={"width": 1280, "height": 900},
                    locale="en-US", color_scheme="light")
                page = context.new_page()
                page.on("pageerror", lambda error: browser_errors.append(str(error)))

                def request_seen(request):
                    path = request.url.split(server.origin)[-1].split("#")[0]
                    calls.append(path)
                    if path == "/api/prepare":
                        requests.append(request.post_data_json)

                page.on("request", request_seen)

                def route_request(route):
                    if not route.request.url.startswith(server.origin + "/"):
                        return route.abort()
                    if route.request.url.split("?")[0].endswith(("/api/run", "/api/stop")):
                        forbidden.append(route.request.url.rsplit("/", 1)[-1])
                        return route.abort()
                    return route.continue_()

                page.route("**/*", route_request)
                recording = Recording(page, args.pace)
                page.goto(server.origin + "/#" + state.token, wait_until="load")
                page.locator("main:not([inert])").wait_for(state="attached")
                recording.caption("After your ONT run", "Start a new methylation-only project. This is the real UI with synthetic files; no analysis is run.")
                page.evaluate("window.scrollTo(0,0)")
                recording.pause(5)
                recording.caption("Original sample membership", "Choose preservation from specimen records, then discover the original barcode FASTQs. These are synthetic examples.")
                recording.click("#variant-fresh")
                recording.focus("#input-folder")
                recording.pause(2)
                recording.click("#scan")
                page.locator("#samples-card:not([hidden])").wait_for()
                recording.caption("Name the samples", "These fictional tumour samples have leukemia, CNS or unknown clinical context. Assign the main sample role first.")
                for identifier, name in enumerate(SAMPLE_NAMES):
                    recording.fill(f'.sample[data-id="{identifier}"] .sample-name', name)
                    recording.select(f'.sample[data-id="{identifier}"] .type-select', "cancer")
                recording.caption("Use original methylation calls", "Keep methylation-only analysis. Link the original modified-base BAMs with MM/ML tags, not an ordinary CNA alignment.")
                recording.focus("#ont-modbam")
                recording.pause(3)
                recording.focus("#analysis")
                assert page.locator("#analysis").input_value() == "methylation"
                recording.pause(2)
                recording.caption("POD5 is the alternative", "POD5 requires compatible Dorado basecalling and modified-base models. Existing modified-base BAMs avoid basecalling again.")
                recording.select("#methylation-source", "pod5")
                recording.focus("#dorado-model-fields", "start")
                recording.pause(3)
                recording.select("#methylation-source", "modbam")
                recording.caption("Drag from documented clinical context", "Drag leukemia to MARLIN and CNS tumour to Sturgeon. A previous classifier prediction is not the selection rule.")

                def drag(identifier, target):
                    recording.focus("#methylation-assignment-board .sample-board", "center")
                    recording.clear_focus()
                    # Let native smooth scrolling settle before measuring pointer coordinates.
                    page.wait_for_timeout(700)
                    card = page.locator(f'.methylation-sample[data-sample-id="{identifier}"] .drag-handle')
                    column = page.locator(f'#methylation-assignment-board [data-methylation-classifier="{target}"]')
                    box, dest = card.bounding_box(), column.bounding_box()
                    assert box and dest
                    start = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    end = (dest["x"] + dest["width"] / 2, dest["y"] + 75)
                    page.mouse.move(*start, steps=12)
                    recording.pause(0.6)
                    page.mouse.down()
                    page.mouse.move(*end, steps=35)
                    recording.pause(0.4)
                    page.mouse.up()
                    page.locator(f'#methylation-{target or "unassigned"} .methylation-sample[data-sample-id="{identifier}"]').wait_for(state="visible")
                    drags.append({"sample_id": identifier, "classifier": target, "pointer_events": True})
                    recording.pause(2)

                drag(0, "marlin")
                drag(1, "sturgeon")
                assert page.locator('#methylation-unassigned .methylation-sample[data-sample-id="2"]').count() == 1
                recording.caption("Unknown stays unassigned", "Leave unknown or unsuitable clinical context unassigned. The dropdown on each card offers the same accessible choice.")
                recording.focus("#methylation-assignment-board .sample-board", "center")
                recording.clear_focus()
                recording.pause(5)
                page.screenshot(path=str(args.output_dir / (NAME + ".jpg")), type="jpeg", quality=90)
                recording.caption("Reuse local tools and reference", "Supply installed tool and model paths, or a resource YAML. This demonstration uses synthetic path fixtures, not trained models.")
                recording.fill("#resources", str(resource_yaml))
                recording.select("#reference", "reuse")
                recording.fill("#reference-path", str(reference))
                recording.pause(2)
                for classifier, identifier in [("marlin", 0), ("sturgeon", 1)]:
                    recording.caption("One classifier per project", "Choose " + ("MARLIN" if classifier == "marlin" else "Sturgeon") + ". Only its assigned sample enters this project; the other two are excluded and are not queued.")
                    recording.select("#classifier", classifier)
                    recording.focus("#methylation-group-note")
                    assert "1 sample(s)" in page.locator("#methylation-group-note").inner_text()
                    assert "2 other/unassigned" in page.locator("#methylation-group-note").inner_text()
                    recording.pause(3)
                    if classifier == "sturgeon":
                        recording.caption("Sturgeon resources and license", "Use your installed Sturgeon assets and confirm its terms only after accepting them. This checkbox is demonstrated with synthetic fixtures.")
                        recording.click("#license")
                    recording.caption("Separate destination", "Save to a new cpg-" + classifier + " project. Keep the completed ONT project and the other classifier's project unchanged.")
                    recording.fill("#project-name", "cpg-" + classifier)
                    recording.pause(2)
                    recording.caption("Save and check only", "Save the selected group's configuration and inspect the included names. These checks validate synthetic paths, not biological model quality.")
                    with page.expect_response(lambda response: response.url.endswith("/api/prepare")) as response:
                        recording.click("#prepare")
                    data = response.value.json()
                    assert response.value.status == 200, data
                    assert data["valid"], data["check"]
                    config_path = Path(data["config_path"])
                    config = load_flat_yaml(config_path)
                    expected = SAMPLE_NAMES[identifier]
                    assert config["ont_sample_names"] == expected
                    assert config["ont_barcodes"] == f"barcode{identifier + 1:02d}"
                    assert config["methylation_only"] is True and config["run_variants"] is False
                    assert config["methylation_classifier"] == classifier
                    assert config["methylation_modbam"] == str(bam.parent)
                    assert config["lpwgs_root"] == str(reference) and config["hg38_auto_download"] is False
                    assert data["reviewed_summary"]["methylation_group"] == {"classifier": classifier, "included": 1, "excluded": 2}
                    assert [row["name"] for row in data["reviewed_summary"]["samples"]] == [expected]
                    project = Path(data["project"])
                    assert not (project / "results").exists() and not (project / "reference").exists()
                    checks.append({"classifier": classifier, "sample": expected, "included": 1, "excluded": 2,
                                   "configuration_saved": True, "configuration_checked": True,
                                   "config_sha256": digest(config_path), "analysis_started": False,
                                   "results_created": False, "reference_created": False})
                    recording.focus("#review-card", "start")
                    recording.pause(5)
                recording.caption("Ready for a real project", "Resolve errors before Run analysis in your real project. Review CpG coverage and classifier scores afterward. This recording ends without running analysis.")
                recording.focus("#run")
                recording.pause(7)
                recording.clear_focus()
                cues = recording.finish()
                assert state.job is None and not forbidden and not browser_errors
                assert not any(path in ("/api/run", "/api/stop") for path in calls)
                assert len(requests) == 2
                expected_map = [{"id": 0, "classifier": "marlin"}, {"id": 1, "classifier": "sturgeon"}, {"id": 2, "classifier": ""}]
                assert all(item["methylation_assignments"] == expected_map for item in requests)
                video = page.video
                context.close()
                raw = Path(video.path())
                browser.close()
    finally:
        if server:
            server.shutdown()
            server.server_close()
    assert all(digest(demo / path) == sha for path, sha in files_before.items())
    mp4 = args.output_dir / (NAME + ".mp4")
    run_checked([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(raw),
                 "-an", "-c:v", "libx264", "-threads", "4", "-preset", "medium", "-crf", "25",
                 "-pix_fmt", "yuv420p", "-r", "24", "-movflags", "+faststart", "-y", str(mp4)])
    probe = json.loads(run_checked([args.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp4)]).stdout)
    duration = float(probe["format"]["duration"])
    stream = next(row for row in probe["streams"] if row["codec_type"] == "video")
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert [stream["width"], stream["height"]] == [1280, 900]
    content = mp4.read_bytes()
    assert content.index(b"moov") < content.index(b"mdat")
    run_checked([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", str(mp4), "-f", "null", "-"])
    cues[-1]["end"] = min(cues[-1]["end"], duration)
    vtt = ["WEBVTT", ""]
    transcript = ["Post-ONT CpG classification — synthetic demonstration", "",
                  "Actual UI, pointer drags, temporary configuration saves and checks.",
                  "Synthetic reads/resources only; no analysis, downloads or patient data.", ""]
    for index, cue in enumerate(cues, 1):
        vtt.extend([str(index), stamp(cue["start"]) + " --> " + stamp(cue["end"]), cue["title"] + ": " + cue["text"], ""])
        transcript.extend([cue["title"], cue["text"], ""])
    (args.output_dir / (NAME + ".vtt")).write_text("\n".join(vtt).rstrip() + "\n")
    (args.output_dir / (NAME + ".txt")).write_text("\n".join(transcript).rstrip() + "\n")
    source_names = ["oncotracer_cli/web.py", "oncotracer_cli/web_ui.py", "oncotracer_cli/setup.py",
                    "scripts/record_post_ont_cpg_video.py", "scripts/record_quickstart_videos.py",
                    "tests/test_native_methylation.py", "tests/test_web.py"]
    manifest = {"schema": 1, "passed": True, "analysis_started": False,
                "synthetic_inputs": True, "real_reads_downloaded": False, "patient_data": False,
                "api": "real loopback WebServer with synthetic temporary files; analysis route disabled",
                "resource_validation_scope": "Path and configuration checks only; fixture models and BAMs are not scientifically valid",
                "browser": {"name": args.browser, "version": version}, "pace": args.pace,
                "ui_page_sha256": hashlib.sha256(original_page.encode()).hexdigest(),
                "source_files": {name: digest(ROOT / name) for name in source_names},
                "pointer_drags": drags, "unknown_sample_unassigned": True,
                "saved_groups": checks, "prepare_requests": requests, "forbidden_actions": forbidden,
                "page_errors": browser_errors, "original_fixture_files_unchanged": True,
                "videos": [{"name": NAME, "duration_seconds": duration, "codec": "h264",
                            "pixel_format": "yuv420p", "faststart": True, "resolution": [1280, 900],
                            "cues": cues, "files": {suffix: {"name": NAME + suffix,
                                 "sha256": digest(args.output_dir / (NAME + suffix)),
                                 "bytes": (args.output_dir / (NAME + suffix)).stat().st_size}
                                 for suffix in (".mp4", ".jpg", ".vtt", ".txt")}}]}
    (args.output_dir / (NAME + "-recording.json")).write_text(json.dumps(manifest, indent=2) + "\n")
    (args.work_dir / "receipt.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"passed": True, "browser": manifest["browser"], "seconds": duration,
                      "saved_groups": len(checks), "pointer_drags": len(drags),
                      "video": str(mp4), "manifest": str(args.output_dir / (NAME + "-recording.json"))}))


if __name__ == "__main__":
    main()
