"""Open a desktop file chooser without exposing a browser-supplied shell command."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .runtime import OncoTracerError

KINDS = ("folder", "file", "asset", "bam")
SEQUENCING_SUFFIXES = (".fastq", ".fastq.gz", ".fq", ".fq.gz", ".pod5", ".bam")
NO_SEQUENCING = "No sequencing files found. Check your paths."

# Tk delegates to the operating system's dialogs on Windows/macOS. Run it in a
# separate process: request handlers are threads, whereas Tk needs a main thread.
_TK_PICKER = """
import json, sys, tkinter as tk
from tkinter import filedialog
options=json.loads(sys.argv[1])
folder=options.pop('folder')
root=tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
try:
    selected=(filedialog.askdirectory(parent=root, mustexist=True, **options) if folder
              else filedialog.askopenfilename(parent=root, **options))
    print(json.dumps(selected or ''))
finally:
    root.destroy()
"""


def choose_path(start: Path, kind: str = "folder") -> Path | None:
    if kind not in KINDS:
        raise OncoTracerError("Choose a folder or a supported file type.")
    initial = start.expanduser().resolve()
    if initial.is_file():
        initial = initial.parent
    while not initial.is_dir() and initial != initial.parent:
        initial = initial.parent
    title = "OncoTracer - Select a folder" if kind == "folder" else "OncoTracer - Select a file"
    patterns = "*.yaml *.yml" if kind == "file" else "*.bam *.BAM" if kind == "bam" else "*"
    tk_dialog = sys.platform in {"win32", "darwin"}
    if tk_dialog:
        options = {"folder": kind == "folder", "title": title, "initialdir": str(initial)}
        if kind != "folder":
            options["filetypes"] = [("Supported files", patterns), ("All files", "*")]
        command = [sys.executable, "-I", "-B", "-c", _TK_PICKER, json.dumps(options)]
    else:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise OncoTracerError("The system file chooser needs a desktop on the computer running OncoTracer. Enter the full path in the field instead.")
        zenity, kdialog = shutil.which("zenity"), shutil.which("kdialog")
        if zenity:
            command = [zenity, "--file-selection", "--title=" + title,
                       "--filename=" + str(initial) + os.sep, "--width=900", "--height=650"]
            command += ["--directory"] if kind == "folder" else ["--file-filter=Supported files | " + patterns, "--file-filter=All files | *"]
        elif kdialog:
            command = [kdialog, "--title", title, "--getexistingdirectory" if kind == "folder" else "--getopenfilename", str(initial)]
            if kind != "folder":
                command.append(patterns)
        else:
            raise OncoTracerError("The system file chooser is unavailable. Install Zenity or KDialog on this computer, or enter the full path in the field.")
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", timeout=900, check=False)
    except subprocess.TimeoutExpired as error:
        raise OncoTracerError("Folder selection timed out. Click Browse to try again.") from error
    except OSError as error:
        raise OncoTracerError("Cannot open the system file chooser. Enter the full path in the field instead.") from error
    if not tk_dialog and result.returncode == 1 and "cannot open display" not in result.stderr.lower():
        return None  # Cancel / window close.
    if result.returncode:
        raise OncoTracerError("Cannot open the system file chooser. Check that a desktop is available, or enter the full path in the field.")
    selected = json.loads(result.stdout) if tk_dialog else result.stdout.removesuffix("\n")
    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if not (path.is_dir() if kind == "folder" else path.is_file()):
        raise OncoTracerError("The selected path is no longer accessible. Check your paths.")
    if kind == "file" and path.suffix.lower() not in {".yaml", ".yml"}:
        raise OncoTracerError("Select a YAML file (.yaml or .yml).")
    if kind == "bam" and path.suffix.lower() != ".bam":
        raise OncoTracerError("Select a BAM file (.bam).")
    return path


def has_sequencing_files(path: Path) -> bool:
    """Check names, including barcode subfolders, without reading sequence data."""
    if path.is_file():
        return path.name.lower().endswith(SEQUENCING_SUFFIXES)

    def fail(error):
        raise OncoTracerError("Cannot inspect the selected folder. Check your paths and folder permissions.") from error

    for parent, _directories, names in os.walk(path, followlinks=False, onerror=fail):
        if any(name.lower().endswith(SEQUENCING_SUFFIXES) and (Path(parent) / name).is_file() for name in names):
            return True
    return False
