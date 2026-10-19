#!/usr/bin/env python3
"""Record the actual CpG assignment UI and saves using isolated synthetic fixtures.

A loopback WebServer performs real discovery, setup and configuration checks.
Tiny test inputs/resources are deliberately not scientific data or trained models.
The server and browser both forbid analysis; all temporary writes use --work-dir.
"""
from __future__ import annotations

import argparse
import csv
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
SAMPLE_NAMES = ["ONCO-001", "ONCO-002", "ONCO-003"]


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
    from oncotracer_cli.cli import parse_arguments
    from oncotracer_cli.reference_bundle import _files, reference_paths
    from oncotracer_cli.runtime import load_flat_yaml, render_flat_yaml
    from oncotracer_cli.setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES
    from tests.test_native_methylation import Fixture
    from tests.test_web import HARDWARE

    demo = args.work_dir / "demo"
    demo.mkdir()
    import gzip
    cohort = demo / "ONT"
    for name in SAMPLE_NAMES:
        folder = cohort / name / "fastq_pass/barcode01"
        folder.mkdir(parents=True)
        with gzip.open(folder / "synthetic.fastq.gz", "wt") as handle:
            handle.write(f"@synthetic-{name}\nACGT\n+\nIIII\n")
        for directory, filename in (("pod5_pass/barcode01", "synthetic.pod5"),
                                    ("modified_bam", "synthetic.mod.bam"),
                                    ("pod5_fail", "failed.pod5")):
            signal = cohort / name / directory / filename
            signal.parent.mkdir(parents=True, exist_ok=True)
            signal.write_bytes(b"synthetic signal path fixture; never processed")
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
    source_project = demo / "ONT-project"
    source_config_dir = source_project / "config"
    source_config_dir.mkdir(parents=True)
    metadata = source_config_dir / "sample_metadata.csv"
    with metadata.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "sample_type", "analysis_role", "fastq_files"])
        writer.writeheader()
        for identifier, name in enumerate(SAMPLE_NAMES, 1):
            writer.writerow({"sample": name, "sample_type": "cancer", "analysis_role": "tumor",
                             "fastq_files": json.dumps([str(cohort / name / "fastq_pass/barcode01/synthetic.fastq.gz")])})
    prior = {"mode": "ont", "ont_folder": str(cohort), "ont_named_samples": True,
             "ont_barcodes": ",".join(SAMPLE_NAMES), "ont_sample_names": ",".join(SAMPLE_NAMES),
             "ont_caller": "ichorcna", "ont_binsize_kb": 500, "threads": 4,
             "outdir": str(source_project / "results"), "lpwgs_root": str(reference),
             "hg38_auto_download": False, "execution_backend": "conda", "variant_specimen_type": "fresh",
             "sample_metadata": str(metadata), "run_variants": False, "methylation": False,
             "sturgeon_license_acknowledged": True,
             **resources}
    (source_config_dir / "run.yml").write_text(render_flat_yaml(prior))
    files_before = {str(path.relative_to(demo)): digest(path) for path in demo.rglob("*") if path.is_file()}
    source_before = {str(path.relative_to(source_project)): digest(path) for path in source_project.rglob("*") if path.is_file()}
    launch_args = parse_arguments(["--interactive", "--cpg-from", str(source_project)])
    state = web.WebState(demo, launch_args)
    state.hardware = dict(HARDWARE, gpu_note="Synthetic recording hardware; no GPU job is used.")
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
                recording.caption("One named folder per ONT library", "Each ONCO-style library has fastq_pass and pod5_pass. This demo restores three libraries from their saved ONT project.")
                page.locator("#cpg-assign-card").wait_for(state="visible")
                assert page.locator("#analysis-choice").is_hidden()
                assert page.locator("#resume-card").is_hidden()
                recording.focus("#methylation-assignment-board .sample-board", "center")
                recording.clear_focus()
                recording.pause(2)
                assert page.locator("#methylation-source").input_value() == "pod5"
                assert page.locator("#ont-pod5").input_value() == str(cohort)
                recording.caption("Matching POD5 is selected", "Each library keeps its own passed signal reads. Compatible Dorado and classifier models are required; these are synthetic fixtures.")
                recording.click("#cpg-advanced > summary")
                recording.focus("#cpg-signal-inputs", "center")
                recording.pause(3)
                recording.click("#cpg-advanced > summary")
                recording.caption("1 · Drag samples", "Synthetic example: ONCO-001 → MARLIN; ONCO-002 → Sturgeon. For real data use clinical records; never infer diagnosis from names.")

                def drag(identifier, target):
                    recording.focus("#methylation-assignment-board .sample-board", "center")
                    recording.clear_focus()
                    page.wait_for_timeout(700)
                    card = page.locator(f'.methylation-sample[data-sample-id="{identifier}"] .drag-handle')
                    column = page.locator(f'#methylation-assignment-board [data-methylation-classifier="{target}"]')
                    box, dest = card.bounding_box(), column.bounding_box()
                    assert box and dest
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
                    recording.pause(0.3)
                    page.mouse.down()
                    page.mouse.move(dest["x"] + dest["width"] / 2, dest["y"] + 75, steps=35)
                    recording.pause(0.2)
                    page.mouse.up()
                    page.locator(f'#methylation-{target or "unassigned"} .methylation-sample[data-sample-id="{identifier}"]').wait_for(state="visible")
                    drags.append({"sample_id": identifier, "classifier": target, "pointer_events": True})
                    recording.pause(1)

                drag(0, "marlin")
                drag(1, "sturgeon")
                recording.caption("Unknown is separate from Cancer or Normal", "Cancer/Normal records the clinical role. These columns only choose a CpG classifier. Return unsuitable samples to Unknown / do not classify.")
                drag(2, "marlin")
                drag(2, "")
                assert page.locator('#methylation-unassigned .methylation-sample[data-sample-id="2"]').count() == 1
                assert page.locator('#methylation-unassigned .methylation-classifier-select').input_value() == ""
                page.screenshot(path=str(args.output_dir / (NAME + ".jpg")), type="jpeg", quality=90)
                for classifier, identifier in [("marlin", 0), ("sturgeon", 1)]:
                    recording.caption("2 · Save the chosen group", ("Choose MARLIN and save. Its new project contains only ONCO-001." if classifier == "marlin" else "Choose Sturgeon and save ONCO-002 separately. The other group and Unknown sample are excluded."))
                    recording.select("#classifier", classifier)
                    recording.pause(1)
                    if classifier == "sturgeon":
                        assert not page.locator("#license").is_checked()
                        recording.caption("Confirm your Sturgeon license", "Only acknowledge this after obtaining Sturgeon and accepting its terms. This demonstration uses synthetic resources.")
                        recording.click("#license")
                        recording.pause(2)
                    with page.expect_response(lambda response: response.url.endswith("/api/prepare")) as response:
                        recording.click("#prepare")
                    data = response.value.json()
                    assert response.value.status == 200, data
                    assert data["valid"], data["check"]
                    config_path = Path(data["config_path"])
                    config = load_flat_yaml(config_path)
                    expected = SAMPLE_NAMES[identifier]
                    assert config["ont_sample_names"] == expected
                    assert config["ont_named_samples"] is True
                    assert config["ont_barcodes"] == expected
                    assert config["methylation_only"] is True and config["run_variants"] is False
                    assert config["methylation_classifier"] == classifier
                    assert config["methylation_pod5_dir"] == str(cohort)
                    assert not config.get("methylation_modbam")
                    from oncotracer_cli.methylation import named_signal_groups
                    groups = named_signal_groups(config, cohort, "pod5")
                    assert len(groups) == 1 and groups[0].sample == expected
                    selected_signal = [str(path.relative_to(demo)) for path in groups[0].files]
                    assert selected_signal == [f"ONT/{expected}/pod5_pass/barcode01/synthetic.pod5"]
                    assert config["lpwgs_root"] == str(reference) and config["hg38_auto_download"] is False
                    assert data["reviewed_summary"]["methylation_group"] == {"classifier": classifier, "included": 1, "excluded": 2}
                    assert [row["name"] for row in data["reviewed_summary"]["samples"]] == [expected]
                    project = Path(data["project"])
                    assert project != source_project and project.parent == source_project.parent
                    assert not (project / "results").exists() and not (project / "reference").exists()
                    checks.append({"classifier": classifier, "sample": expected, "included": 1, "excluded": 2,
                                   "unknown_sample_excluded": "ONCO-003",
                                   "selected_signal_files": selected_signal, "signal_source": "pod5",
                                   "configuration_saved": True, "configuration_checked": True,
                                   "config_sha256": digest(config_path), "analysis_started": False,
                                   "results_created": False, "reference_created": False,
                                   "separate_project": True, "project_name": project.name})
                    recording.focus("#review-card", "start")
                    recording.pause(2)
                recording.caption("3 · Run only when ready", "Use valid POD5 and compatible models, resolve checks and license requirements, then click Run explicitly. This demo stops after saving.")
                recording.focus("#run")
                recording.pause(4)
                recording.clear_focus()
                cues = recording.finish()
                assert state.job is None and not forbidden and not browser_errors
                assert not any(path in ("/api/run", "/api/stop") for path in calls)
                assert len(requests) == 2
                expected_map = [{"id": 0, "classifier": "marlin"}, {"id": 1, "classifier": "sturgeon"}, {"id": 2, "classifier": ""}]
                assert all(item["methylation_assignments"] == expected_map for item in requests)
                assert all([(row["name"], row["type"], row["role"]) for row in item["samples"]] ==
                           [(name, "cancer", "tumor") for name in SAMPLE_NAMES] for item in requests)
                video = page.video
                context.close()
                raw = Path(video.path())
                browser.close()
    finally:
        if server:
            server.shutdown()
            server.server_close()
    assert all(digest(demo / path) == sha for path, sha in files_before.items())
    assert source_before == {str(path.relative_to(source_project)): digest(path) for path in source_project.rglob("*") if path.is_file()}
    mp4 = args.output_dir / (NAME + ".mp4")
    run_checked([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(raw),
                 "-an", "-c:v", "libx264", "-threads", "4", "-preset", "medium", "-crf", "25",
                 "-pix_fmt", "yuv420p", "-r", "24", "-movflags", "+faststart", "-y", str(mp4)])
    probe = json.loads(run_checked([args.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp4)]).stdout)
    duration = float(probe["format"]["duration"])
    stream = next(row for row in probe["streams"] if row["codec_type"] == "video")
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    if args.pace == 1:
        assert 30 <= duration <= 60, f"Short walkthrough must last 30–60 seconds, got {duration}"
    assert [stream["width"], stream["height"]] == [1280, 900]
    content = mp4.read_bytes()
    assert content.index(b"moov") < content.index(b"mdat")
    run_checked([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", str(mp4), "-f", "null", "-"])
    cues[-1]["end"] = min(cues[-1]["end"], duration)
    vtt = ["WEBVTT", ""]
    transcript = ["Post-ONT CpG classification — named libraries and POD5", "",
                  "Actual UI, pointer drags, temporary configuration saves and checks.",
                  "Synthetic reads/resources only; no analysis, downloads or patient data.", ""]
    for index, cue in enumerate(cues, 1):
        vtt.extend([str(index), stamp(cue["start"]) + " --> " + stamp(cue["end"]), cue["title"] + ": " + cue["text"], ""])
        transcript.extend([cue["title"], cue["text"], ""])
    (args.output_dir / (NAME + ".vtt")).write_text("\n".join(vtt).rstrip() + "\n")
    (args.output_dir / (NAME + ".txt")).write_text("\n".join(transcript).rstrip() + "\n")
    source_names = ["oncotracer_cli/web.py", "oncotracer_cli/web_ui.py", "oncotracer_cli/setup.py", "oncotracer_cli/cpg_setup.py",
                    "oncotracer_cli/resume_ui.py", "scripts/record_post_ont_cpg_video.py", "scripts/record_quickstart_videos.py",
                    "tests/test_native_methylation.py", "tests/test_web.py"]
    manifest = {"schema": 1, "passed": True, "analysis_started": False,
                "entrypoint": "oncotracer --interactive --cpg-from /path/to/ONT-project",
                "restored_previous_project": True, "source_project_unchanged": True,
                "synthetic_inputs": True, "real_reads_downloaded": False, "patient_data": False,
                "api": "real loopback WebServer with synthetic temporary files; analysis route disabled",
                "resource_validation_scope": "Path, configuration and selected-library signal checks only; fixture models, POD5 and BAMs are not scientifically valid",
                "named_sample_layout": True, "sample_names": SAMPLE_NAMES,
                "auto_selected_signal": "pod5", "source_signal_was_unset": True,
                "explicit_classifier_assignments": True, "unknown_sample_unassigned": True,
                "clinical_roles_unchanged": True, "unknown_sample": "ONCO-003",
                "browser": {"name": args.browser, "version": version}, "pace": args.pace,
                "ui_page_sha256": hashlib.sha256(original_page.encode()).hexdigest(),
                "source_files": {name: digest(ROOT / name) for name in source_names},
                "pointer_drags": drags,
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
