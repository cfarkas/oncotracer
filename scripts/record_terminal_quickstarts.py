#!/usr/bin/env python3
"""Capture real save-only setup sessions and render terminal tutorial videos.

Uses synthetic FASTQs and isolated HOME/XDG folders below --work-dir. The real
wizard is executed in a PTY without --run. Its unmodified output is saved as
asciicast v2; the video replays the same output at a readable pace. Requires
Playwright/Chromium, ffmpeg, ffprobe and the application's Python dependencies.
No browser setup server, scientific tools, reference download or analysis runs.
"""
from __future__ import annotations

import argparse
import csv
import errno
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import struct
import subprocess
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs/assets/tutorial"
SOURCE_FILES = (
    "oncotracer_cli/wizard.py",
    "oncotracer_cli/setup.py",
    "oncotracer_cli/cli.py",
    "oncotracer_cli/discovery.py",
    "scripts/record_terminal_quickstarts.py",
)
NAMES = {
    "illumina": ["ERR12341627"],
    "ont": ["DRR165691"],
    "hcc1143": ["HCC1143_BEZ235", "HCC1143_DMSO", "HCC1143_TRAMETINIB"],
}
TITLES = {
    "illumina": "Illumina · terminal setup",
    "ont": "Oxford Nanopore · terminal setup",
    "hcc1143": "HCC1143 · terminal setup",
}
HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#e9f0f1;color:#173641;font-family:Arial,sans-serif}
header{height:68px;padding:17px 30px;background:#123c45;color:white;display:flex;align-items:center;justify-content:space-between}
header strong{font-size:24px}header span{font-size:14px;color:#d6e9e7}
main{padding:22px 28px 0}.bar{height:40px;background:#233b45;border-radius:12px 12px 0 0;display:flex;align-items:center;padding:0 18px;color:#d5e2e6;font-size:15px;gap:9px}.dot{width:10px;height:10px;border-radius:50%;background:#a4c7c8}.location{margin-left:10px}
#terminal{height:616px;overflow:hidden;background:#0e2029;color:#e5eef1;padding:19px 22px;border-radius:0 0 12px 12px;box-shadow:0 8px 18px #123c451a}
pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:18px/26px 'DejaVu Sans Mono',monospace;tab-size:4}#cursor{display:inline-block;width:10px;height:20px;background:#8de1c9;vertical-align:text-bottom}
footer{height:148px;padding:18px 34px 22px}#step{font-size:13px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;color:#17725f;margin-bottom:7px}#caption{font-size:22px;line-height:1.35;max-width:1190px}
</style></head><body><header><strong id="title"></strong><span>DEMO · synthetic FASTQs · actual wizard · no analysis</span></header><main><div class="bar"><i class="dot"></i><i class="dot"></i><i class="dot"></i><span class="location">Terminal · temporary tutorial workspace</span></div><div id="terminal"><pre><span id="output"></span><span id="cursor"></span></pre></div></main><footer><div id="step"></div><div id="caption"></div></footer><script>
window.appendTerminal=(text)=>{document.getElementById('output').appendChild(document.createTextNode(text));const t=document.getElementById('terminal');t.scrollTop=t.scrollHeight;};
window.setCaption=(c)=>{document.getElementById('step').textContent=c.title;document.getElementById('caption').textContent=c.text;};
</script></body></html>"""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def isolated_env(base):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("ONCOTRACER_", "CONDA_", "PYTHON")) or key in (
            "VIRTUAL_ENV",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "DISPLAY",
        ):
            env.pop(key, None)
    for key, name in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
        ("TMPDIR", "tmp"),
    ):
        target = base / name
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target)
    env.update(TERM="dumb", NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def make_fixture(work, kind):
    cwd = work / kind
    cwd.mkdir(parents=True, exist_ok=False)
    if kind == "hcc1143":
        project = "oncotracer-quickstart2/analysis"
        folder = "oncotracer-quickstart2/input"
    else:
        project = "oncotracer-quickstart1/" + kind
        folder = "oncotracer-quickstart1/input/" + (
            "fastq_pass" if kind == "ont" else "illumina"
        )
    files = []
    for name in NAMES[kind]:
        for mate in (1,) if kind == "ont" else (1, 2):
            suffix = f"R{mate}" if kind == "hcc1143" else str(mate)
            relative = (
                "barcode01/" if kind == "ont" else ""
            ) + f"{name}_{suffix}.fastq.gz"
            path = cwd / folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as handle:
                handle.write(
                    f"@synthetic_{name}_{mate}\nACGTACGTACGT\n+\nIIIIIIIIIIII\n".encode()
                )
            files.append(path)
    mode = "ont" if kind == "ont" else "illumina"
    command = (
        "oncotracer setup --terminal --backend conda \\\n"
        f'  --project "$PWD/{project}" \\\n'
        f"  --mode {mode} \\\n"
        f'  --input-folder "$PWD/{folder}"'
    )
    return cwd, project, folder, command, files


def answer_for(prompt, kind):
    if prompt.startswith("Sample name ["):
        return "DRR165691" if kind == "ont" else ""
    if prompt.startswith("Type for "):
        return "cancer"
    answers = {
        "Samples to include (numbers, ranges, or all)": "all",
        "Analysis (--analysis; cna=copy-number)": "cna",
        "CPU threads (--threads)": "4",
        "ONT CNA method": "ichorcna",
        "CNA bin size (kb)": "100",
        "Add CNA interpretation reports?": "no",
        "Add small-variant calling?": "no",
        "GPU usage for supported stages": "no",
        "hg38 reference": "download",
        "Final action": "save",
    }
    for label, answer in answers.items():
        if prompt.startswith(label):
            return answer
    raise RuntimeError(f"Unrecognized wizard prompt: {prompt!r}")


def normal(text):
    return text.replace("\r\n", "\n").replace("\r", "")


def capture(work, kind):
    cwd, project, folder, command, inputs = make_fixture(work, kind)
    state = work / (kind + "-state")
    env = isolated_env(state)
    private_bin = state / "bin"
    private_bin.mkdir()
    bootstrap = (
        "import sys; sys.path.insert(0, " + repr(str(ROOT)) + "); "
        "from oncotracer_cli.cli import main; raise SystemExit(main())"
    )
    launcher = private_bin / "oncotracer"
    launcher.write_text(
        "#!/bin/sh\nexec "
        + shlex.join([sys.executable, "-I", "-B", "-c", bootstrap])
        + ' "$@"\n'
    )
    launcher.chmod(0o755)
    env["PATH"] = str(private_bin) + ":/usr/bin:/bin"
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 120, 0, 0))
    start = time.monotonic()
    events = []
    turns = []
    pending = ""
    previous_answer = None
    process = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-c", "exec " + command],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    try:
        while True:
            if time.monotonic() - start > 90:
                raise RuntimeError("Wizard capture timed out")
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                text = data.decode("utf-8", errors="strict")
                events.append([round(time.monotonic() - start, 6), "o", text])
                pending += text
                line = normal(pending).split("\n")[-1]
                if line.endswith(": "):
                    answer = answer_for(line, kind)
                    display = normal(pending)
                    if previous_answer is not None:
                        expected = previous_answer + "\n"
                        if not display.startswith(expected):
                            raise RuntimeError("PTY echo differs from submitted answer")
                        display = display[len(expected) :]
                    turns.append({"output": display, "prompt": line, "answer": answer})
                    events.append(
                        [round(time.monotonic() - start, 6), "i", answer + "\n"]
                    )
                    os.write(master, (answer + "\n").encode())
                    previous_answer = answer
                    pending = ""
            elif process.poll() is not None:
                break
        code = process.wait(timeout=10)
    finally:
        os.close(master)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    tail = normal(pending)
    expected = (previous_answer or "") + "\n"
    if not tail.startswith(expected):
        raise RuntimeError("Final PTY echo is missing")
    tail = tail[len(expected) :]
    transcript = "".join(event[2] for event in events if event[1] == "o")
    if code != 0 or not transcript.endswith("\r\n"):
        raise RuntimeError(f"Wizard failed with {code}: {transcript}")
    if (
        not turns[-1]["prompt"].startswith("Final action")
        or turns[-1]["answer"] != "save"
    ):
        raise RuntimeError("Wizard did not explicitly save")
    if "Saved without starting analysis" not in transcript:
        raise RuntimeError("Save-only completion was not observed")
    import yaml

    config_path = cwd / project / "config/run.yml"
    config = yaml.safe_load(config_path.read_text())
    mode = "ont" if kind == "ont" else "illumina"
    checks = {
        "saved": config_path.is_file(),
        "analysis_started": False,
        "reference_created": Path(config["lpwgs_root"]).exists(),
        "results_created": Path(config["outdir"]).exists(),
        "mode": config["mode"],
        "caller": config[mode + "_caller"],
        "bin_size_kb": config[mode + "_binsize_kb"],
        "threads": config["threads"],
        "samples": NAMES[kind],
        "run_variants": config["run_variants"],
        "hg38_auto_download": config["hg38_auto_download"],
        "metadata": list(csv.DictReader(Path(config["sample_metadata"]).open())),
    }
    assert (
        checks["saved"]
        and not checks["reference_created"]
        and not checks["results_created"]
    )
    assert (
        checks["mode"] == mode and checks["threads"] == 4 and not checks["run_variants"]
    )
    assert checks["caller"] == ("ichorcna" if kind == "ont" else "qdnaseq")
    assert checks["bin_size_kb"] == (500 if kind == "ont" else 100)
    assert [row["sample"] for row in checks["metadata"]] == NAMES[kind]
    assert all(row["sample_type"] == "cancer" for row in checks["metadata"])
    replay = "".join(turn["output"] + turn["answer"] + "\n" for turn in turns) + tail
    if replay != normal(transcript):
        raise RuntimeError("Terminal replay differs from captured PTY output")
    capture_data = {
        "kind": kind,
        "command": command,
        "exit_code": code,
        "answers": [
            {"prompt": turn["prompt"], "answer": turn["answer"]} for turn in turns
        ],
        "turns": turns,
        "tail": tail,
        "config_checks": checks,
        "input_sha256": {str(path.relative_to(cwd)): sha256(path) for path in inputs},
        "events": events,
        "transcript": normal(transcript),
    }
    write_json(work / (kind + "-capture.json"), capture_data)
    print(
        f"Captured {kind}: {len(turns)} actual prompts, explicit save, no results/reference",
        flush=True,
    )
    return capture_data


def caption_for(prompt, kind):
    if prompt.startswith("Samples to include"):
        return (
            "Choose libraries",
            "The wizard discovers the synthetic FASTQs. Enter all to include every listed library.",
        )
    if prompt.startswith("Sample name"):
        if kind == "ont":
            return (
                "Name the ONT library",
                "Rename barcode01 to DRR165691. The barcode folder remains the input location.",
            )
        return (
            "Keep the library identity",
            "Press Enter to keep the discovered sample name and its existing FASTQ pair.",
        )
    if prompt.startswith("Type for"):
        if "DMSO" in prompt:
            return (
                "DMSO is still a cancer sample",
                "DMSO is a treatment control, not a normal genome. Keep its sample type as cancer.",
            )
        return (
            "Assign the sample type",
            "These tutorial libraries are cancer samples. Choose the type from your own sample records.",
        )
    if prompt.startswith("Analysis"):
        return (
            "Choose copy-number analysis",
            "Enter cna. This walkthrough configures copy-number analysis only.",
        )
    if prompt.startswith("CPU threads"):
        return (
            "Set the CPU budget",
            "The wizard reports the current machine. Choose 4 threads here; budget resources for your own server.",
        )
    if prompt.startswith("ONT CNA"):
        return (
            "Choose the ONT caller",
            "Select ichorcna. The ONT ichorCNA route uses 500 kb bins automatically.",
        )
    if prompt.startswith("CNA bin"):
        return (
            "Choose Illumina bins",
            "Use QDNAseq at 100 kb for this tutorial. The wizard records the selected resolution.",
        )
    if prompt.startswith("Add CNA"):
        return (
            "Keep the example focused",
            "Choose no for additional CNA interpretation reports.",
        )
    if prompt.startswith("Add small"):
        return (
            "Keep small variants off",
            "Choose no. This CNA-only setup does not request variant or FFPE assessment.",
        )
    if prompt.startswith("GPU usage"):
        return "Choose CPU or GPU", "On an NVIDIA GPU server, choose Yes for supported stages or No to use CPU. This walkthrough keeps CPU."
    if prompt.startswith("hg38"):
        return (
            "Select reference handling",
            "Choose download for prebuilt hg38 indexes when analysis runs. Saving does not download them.",
        )
    if prompt.startswith("Final action"):
        return (
            "Review and save only",
            "Review the saved configuration and input checks, then explicitly enter save. No analysis starts.",
        )
    raise RuntimeError(prompt)


def timestamp(seconds):
    millis = round(seconds * 1000)
    return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02}.{millis % 1000:03}"


def render_capture(capture_data, work, output, browser, pace):
    kind = capture_data["kind"]
    name = "quick-start-terminal-" + kind
    raw_dir = work / (kind + "-video")
    raw_dir.mkdir(exist_ok=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1280, "height": 900},
        color_scheme="light",
        locale="en-US",
        timezone_id="UTC",
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.route(
        "**/*",
        lambda route: (
            route.fulfill(status=200, content_type="text/html", body=HTML)
            if route.request.url == "https://terminal-tutorial.invalid/"
            else route.abort()
        ),
    )
    page.goto("https://terminal-tutorial.invalid/", wait_until="load")
    page.locator("#title").evaluate("(el,text)=>el.textContent=text", TITLES[kind])
    cues = []
    started = time.monotonic()

    def hold(seconds):
        page.wait_for_timeout(seconds * 1000 * pace)

    def caption(title, text):
        when = time.monotonic() - started
        if cues:
            cues[-1]["end"] = when
        cues.append({"start": when, "end": when, "title": title, "text": text})
        page.evaluate("setCaption", {"title": title, "text": text})

    def append(text):
        page.evaluate("appendTerminal", text)

    caption(
        "A terminal is enough",
        "Run setup --terminal on a headless server. These are real wizard prompts using tiny synthetic FASTQs.",
    )
    append("$ " + capture_data["command"] + "\n")
    hold(7)
    for turn in capture_data["turns"]:
        title, text = caption_for(turn["prompt"], kind)
        caption(title, text)
        output_text = turn["output"]
        if turn["prompt"].startswith("Final action"):
            # Replay the long saved-config/check output in readable chunks.
            lines = output_text.splitlines(keepends=True)
            for offset in range(0, len(lines), 12):
                append("".join(lines[offset : offset + 12]))
                hold(2.8)
            hold(2)
        else:
            append(output_text)
            hold(3.2)
        for char in turn["answer"]:
            append(char)
            hold(0.11)
        if not turn["answer"]:
            hold(0.7)
        hold(1)
        append("\n")
        hold(0.3)
    caption(
        "Configuration saved",
        "The wizard confirms: saved without starting analysis. Keep the project and launch the analysis separately when ready.",
    )
    append(capture_data["tail"])
    hold(7)
    cues[-1]["end"] = time.monotonic() - started
    video = page.video
    context.close()
    raw = Path(video.path())
    target = output / (name + ".mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw),
            "-an",
            "-vf",
            "fps=24,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
    )
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(target),
            ],
            text=True,
        )
    )
    stream = probe["streams"][0]
    duration = float(probe["format"]["duration"])
    for cue in cues:
        cue["start"] = round(min(cue["start"], duration), 3)
        cue["end"] = round(min(cue["end"], duration), 3)
    cues[-1]["end"] = round(duration, 3)
    poster = output / (name + ".jpg")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "9",
            "-i",
            str(target),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(poster),
        ],
        check=True,
    )
    subtitles = "WEBVTT\n\n" + "\n\n".join(
        f"{index}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n{cue['title']}: {cue['text']}"
        for index, cue in enumerate(cues, 1)
    )
    (output / (name + ".vtt")).write_text(subtitles.rstrip() + "\n")
    transcript = (
        TITLES[kind]
        + "\n\nDEMO: synthetic FASTQs; real setup wizard; explicit save; no analysis.\n"
        "Video presentation replays captured output at a readable pace.\n\n"
        "$ " + capture_data["command"] + "\n" + capture_data["transcript"]
    )
    (output / (name + ".txt")).write_text(transcript.rstrip() + "\n")
    header = {
        "version": 2,
        "width": 120,
        "height": 28,
        "title": TITLES[kind],
        "command": capture_data["command"],
        "env": {"TERM": "dumb"},
        "synthetic_inputs": True,
        "analysis_started": False,
    }
    cast = [json.dumps(header)] + [
        json.dumps(event, ensure_ascii=False) for event in capture_data["events"]
    ]
    (output / (name + ".cast")).write_text("\n".join(cast) + "\n")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(target),
            "-f",
            "null",
            "-",
        ],
        check=True,
    )
    data = target.read_bytes()
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert data.find(b"moov") < data.find(b"mdat")
    files = {}
    for extension in (".mp4", ".jpg", ".vtt", ".txt", ".cast"):
        path = output / (name + extension)
        files[extension] = {
            "name": path.name,
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
    result = {
        "name": name,
        "duration_seconds": duration,
        "codec": stream["codec_name"],
        "pixel_format": stream["pix_fmt"],
        "faststart": True,
        "resolution": [stream["width"], stream["height"]],
        "cues": cues,
        "command": capture_data["command"],
        "exit_code": capture_data["exit_code"],
        "answers": capture_data["answers"],
        "config_checks": capture_data["config_checks"],
        "input_sha256": capture_data["input_sha256"],
        "files": files,
        "capture": "Unmodified PTY output in asciicast v2; video normalizes CRLF and replays at a readable pace.",
    }
    print(
        f"Rendered {name}: {duration:.1f}s, {target.stat().st_size:,} bytes", flush=True
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=MEDIA)
    parser.add_argument("--playwright-path", type=Path)
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--reuse-captures", action="store_true")
    parser.add_argument("--pace", type=float, default=1.0)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    captures = [
        (
            json.loads((work / (kind + "-capture.json")).read_text())
            if args.reuse_captures
            else capture(work, kind)
        )
        for kind in NAMES
    ]
    if args.capture_only:
        return 0
    if args.playwright_path:
        sys.path.insert(0, str(args.playwright_path))
    from playwright.sync_api import sync_playwright

    browser_env = isolated_env(work / "browser-state")
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        browser_env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(
            headless=True,
            executable_path=str(args.browser_path) if args.browser_path else None,
            env=browser_env,
        )
        try:
            videos = [
                render_capture(data, work, args.output_dir, browser, args.pace)
                for data in captures
            ]
        finally:
            browser.close()
    manifest = {
        "schema": 1,
        "passed": True,
        "analysis_started": False,
        "synthetic_inputs": True,
        "real_reads_downloaded": False,
        "recording": "Actual terminal setup wizard, save only; browser used solely to render captured terminal output.",
        "source_files": {name: sha256(ROOT / name) for name in SOURCE_FILES},
        "pace": args.pace,
        "videos": videos,
    }
    write_json(args.output_dir / "quick-start-terminal-recording.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
