"""Advisory live progress. Never used as evidence for output reuse or success."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import uuid

JOURNAL = ".oncotracer-native/terminal-progress.json"


def clean(value):
    """Keep sample names and tool messages from controlling the terminal."""
    return "".join(c if c.isprintable() else " " for c in str(value))


class ProgressJournal:
    def __init__(self, outdir):
        self.path = Path(outdir) / JOURNAL
        self.lock = threading.RLock()
        self.data = {"attempt": uuid.uuid4().hex, "started_ns": time.time_ns(),
                     "samples": [], "phase": "Preparing analysis", "task": "Checking inputs and tools",
                     "allocation": {}, "jobs": {}}

    def _save(self):
        from .runtime import atomic_write_json
        # The caller has already acquired the output lease. Display failures must
        # not invalidate scientific results or replace an original worker error.
        try:
            atomic_write_json(self.path, self.data)
        except OSError:
            pass

    def samples(self, names):
        with self.lock:
            self.data["samples"] = list(dict.fromkeys(map(str, names)))
            self._save()

    def queue(self, phase, names, allocation):
        with self.lock:
            self.data.update(phase=phase, task="", allocation=dict(allocation),
                             jobs={str(name): {"state": "queued", "task": "Waiting", "commands": 0}
                                   for name in names})
            self._save()

    def state(self, name, state):
        with self.lock:
            row = self.data["jobs"].get(str(name))
            if row is None or row["state"] == state:
                return
            row["state"] = state
            if state == "running":
                self.data["task"] = ""
                row.setdefault("started", time.time())
            elif state not in {"queued", "not_started"}:
                row["finished"] = time.time()
            self._save()

    def task(self, stage, sample=None):
        with self.lock:
            row = self.data["jobs"].get(str(sample)) if sample is not None else None
            if row is not None:
                row["task"] = str(stage)
            else:
                self.data["task"] = str(stage)
            self._save()

    def command_done(self, sample, returncode):
        if sample is None or returncode != 0:
            return
        with self.lock:
            row = self.data["jobs"].get(str(sample))
            if row is not None:
                row["commands"] += 1
                self._save()

    def interrupted(self):
        with self.lock:
            for row in self.data["jobs"].values():
                if row["state"] == "running":
                    row.update(state="interrupted", finished=time.time())
                elif row["state"] == "queued":
                    row["state"] = "not_started"
            self._save()


def is_terminal(stream):
    try:
        return stream.isatty() and os.environ.get("TERM") != "dumb"
    except (AttributeError, OSError):
        return False


def elapsed(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def progress_lines(snapshot, *, now=None, tick=0):
    """Known queue totals get a bar; running tasks have no invented percentage."""
    now = time.time() if now is None else now
    if not snapshot:
        return ["OncoTracer | preparing inputs, tools and reference…", "Waiting for sample workers"]
    jobs = snapshot.get("jobs", {})
    states = [row["state"] for row in jobs.values()]
    done = sum(s in {"complete", "not_applicable"} for s in states)
    failed = sum(s in {"failed", "partial_failure", "interrupted", "not_started"} for s in states)
    running = states.count("running")
    queued = states.count("queued")
    total = len(states)
    size = 20
    filled = (done + failed) * size // total if total else 0
    bar = "=" * filled + " " * (size - filled)
    allocation = snapshot.get("allocation", {})
    lines = [f"OncoTracer | {snapshot['phase']} | elapsed {elapsed(now - snapshot['started_ns'] / 1e9)}"]
    if total:
        lines.append(f"[{bar}] {done}/{total} done | {running} running | {queued} queued | {failed} incomplete")
        lines.append(f"Sample slots: {allocation.get('parallel_samples', '?')} | CPU threads/sample: "
                     f"{allocation.get('threads_per_sample', '?')} | memory/sample: {allocation.get('memory_per_sample', '?')}")
        lines.append("SAMPLE                        TASK / STATE                              ELAPSED")
        active = [(name, row) for name, row in jobs.items() if row["state"] == "running"]
        for name, row in active:
            pulse = ["=     ", " =    ", "  =   ", "   =  ", "    = ", "     ="][tick % 6]
            task = clean(row.get("task", "Running"))[:35]
            label = clean(name)
            label = label[:25] + "..." if len(label) > 28 else label
            lines.append(f"{label:<28} [{pulse}] {task:<35} {elapsed(now - row.get('started', now))}")
        if not active:
            lines.append("No sample workers active; preparing or finalizing cohort work.")
        lines.append("Queue progress is for this stage; final sample results appear below when the run ends.")
    if snapshot.get("task"):
        lines.append("Current cohort task: " + clean(snapshot["task"]))
    return lines


class TerminalDisplay:
    def __init__(self, outdir, log_path, *, stream=None, started_ns=None):
        self.outdir, self.log_path = Path(outdir), Path(log_path)
        self.stream = sys.stderr if stream is None else stream
        self.started_ns = time.time_ns() if started_ns is None else started_ns
        self.stop = threading.Event()
        self.thread = None
        self.rows = 0
        self.latest = None
        self.tick = 0

    def snapshot(self):
        try:
            data = json.loads((self.outdir / JOURNAL).read_text())
            if data["started_ns"] >= self.started_ns:
                self.latest = data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return self.latest

    def _clear(self):
        if self.rows:
            self.stream.write(f"\x1b[{self.rows}A\r\x1b[J")
            self.rows = 0

    def draw(self):
        try:
            size = os.get_terminal_size(self.stream.fileno())
        except (OSError, AttributeError, ValueError):
            size = shutil.get_terminal_size((100, 30))
        if size.columns < 1 or size.lines < 1:
            size = os.terminal_size((100, 30))
        lines = progress_lines(self.snapshot(), tick=self.tick)
        self.tick += 1
        # Keep the entire live region on screen so cursor movement is bounded.
        limit = max(3, size.lines - 3)
        if len(lines) > limit:
            lines = lines[:limit - 1] + [f"More active samples: see {self.log_path}"]
        lines.append("Full log: " + str(self.log_path))
        self._clear()
        self.stream.write("\n".join(clean(line)[:max(1, size.columns - 1)] for line in lines) + "\n")
        self.stream.flush()
        self.rows = len(lines)

    def start(self):
        def refresh():
            try:
                while not self.stop.is_set():
                    self.draw()
                    self.stop.wait(.5)
            except (OSError, ValueError):
                pass  # A closed/disconnected terminal must not stop analysis.
        self.thread = threading.Thread(target=refresh, name="oncotracer-terminal", daemon=True)
        self.thread.start()

    def finish(self, completion=None, *, status="incomplete", error=None):
        from .completion import completion_lines, sample_table
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        self._clear()
        snapshot = self.snapshot()
        title = "Analysis complete" if status == "complete" and completion else ("Analysis stopped" if status == "stopped" else "Analysis incomplete")
        lines = [title]
        if completion:
            lines.extend(completion_lines(completion))
        else:
            names = (snapshot or {}).get("samples", [])
            lines.append("Final sample results were not published for this attempt. Retained stages are checked on resume.")
            lines.extend(sample_table([(name, "INCOMPLETE", "Not finalized in this attempt") for name in names]))
        if error:
            message = str(error).strip() or type(error).__name__
            lines.append("Reason: " + clean(message.splitlines()[0])[:400])
        lines.append("Full log: " + str(self.log_path))
        lines.append("Outputs retained: " + str(self.outdir))
        self.stream.write("\n".join(lines) + "\n")
        self.stream.flush()


@contextmanager
def terminal_session(config_path, *, enabled=True):
    """Capture inherited tool FDs only in the foreground CLI, never the web server.

    Redirected/non-TTY commands keep plain output for schedulers and log readers.
    The browser already owns a persistent child log and uses TerminalDisplay.
    """
    if not enabled or not is_terminal(sys.stderr) or threading.current_thread() is not threading.main_thread():
        yield
        return
    from .runtime import load_flat_yaml, sha256_file
    from .run_completion import published_completion
    config_path = Path(config_path).expanduser().resolve()
    config = load_flat_yaml(config_path)
    if not config.get("outdir"):
        yield
        return
    outdir = Path(str(config["outdir"])).expanduser().absolute()
    manifest = outdir / "06_workflow_summary/native_run_manifest.json"
    try:
        prior = manifest.stat().st_mtime_ns
    except OSError:
        prior = None
    digest = sha256_file(config_path)
    logs = config_path.parent.parent / "logs"
    directory = logs if logs.is_dir() and not logs.is_symlink() else None
    fd, path = tempfile.mkstemp(prefix="terminal-analysis-", suffix=".log", dir=directory)
    saved = {}
    console = None
    display = None
    error = None
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        console = os.fdopen(os.dup(sys.stderr.fileno()), "w", buffering=1)
        display = TerminalDisplay(outdir, path, stream=console)
        for target in (1, 2):
            saved[target] = os.dup(target)
            os.dup2(fd, target)
        display.start()
        try:
            yield
        except BaseException as caught:
            error = caught
            print(f"Analysis ended: {type(caught).__name__}: {caught}", file=sys.stderr, flush=True)
            raise
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for target, original in saved.items():
            os.dup2(original, target)
            os.close(original)
        os.close(fd)
        try:
            if display:
                # Resume may legitimately relocate paths in the saved config.
                try:
                    digest = sha256_file(config_path)
                except OSError:
                    pass
                completion = published_completion(outdir, digest, prior)
                success = bool(completion and not completion["incomplete_count"] and not completion["outstanding_cohort_work"])
                status = "stopped" if isinstance(error, KeyboardInterrupt) else "complete" if success and error is None else "incomplete"
                display.finish(completion, status=status, error=error)
                if error is not None:
                    error.terminal_reported = True
        except (OSError, ValueError):
            # Presentation must not mask a worker error or change an exit code.
            if display:
                display.stop.set()
        finally:
            if console:
                console.close()
