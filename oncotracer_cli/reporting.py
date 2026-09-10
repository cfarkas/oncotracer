"""Small terminal progress display with plain logs and no third-party dependency."""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import threading
import time
from contextvars import ContextVar
from pathlib import Path

_CURRENT = ContextVar("oncotracer_reporter", default=None)


def duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class Reporter:
    def __init__(self, title, *, verbose=False, log=None):
        self.stream = sys.stderr
        self.title = self.label = title
        self.verbose = verbose
        self.log = log
        self.tty = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.tty and "NO_COLOR" not in os.environ
        self.started = time.monotonic()
        self.completed = self.total = 0
        self.failed = False
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._animate, daemon=True)

    def paint(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self):
        elapsed = duration(time.monotonic() - self.started)
        if self.total:
            width = 18
            filled = width * self.completed // self.total
            bar = "[" + "=" * filled + " " * (width - filled) + "]"
            prefix = f"{bar} {self.completed}/{self.total}"
        else:
            prefix = "|/-\\"[int((time.monotonic() - self.started) * 4) % 4]
        width = shutil.get_terminal_size(fallback=(100, 24)).columns
        available = max(8, width - len(prefix) - len(elapsed) - 16)
        label = self.label if len(self.label) <= available else self.label[:available - 1] + "…"
        return f"{self.paint(prefix, '36')} {label}  |  elapsed {elapsed}"

    def _draw(self):
        text = self._line()
        if self.tty and not self.verbose:
            self.stream.write("\r\033[2K" + text)
        else:
            self.stream.write(text + "\n")
        self.stream.flush()

    def _animate(self):
        interval = 0.25 if self.tty and not self.verbose else 20
        while not self.stop.wait(interval):
            with self.lock:
                self._draw()

    def status(self, label, *, completed=None, total=None):
        with self.lock:
            self.label = label
            if completed is not None:
                self.completed = completed
            if total is not None:
                self.total = total
            if self.log:
                self.log.write(f"[{duration(time.monotonic() - self.started)}] {label}\n")
                self.log.flush()
            self._draw()

    def detail(self, text, *, echo=False):
        with self.lock:
            if self.log:
                self.log.write(text)
                self.log.flush()
            if self.verbose or echo:
                if self.tty and not self.verbose:
                    self.stream.write("\r\033[2K")
                self.stream.write(text)
                self.stream.flush()

    def finish(self, failed=False):
        self.stop.set()
        self.thread.join()
        with self.lock:
            if self.tty and not self.verbose:
                self.stream.write("\r\033[2K")
            marker = self.paint("FAILED" if failed else "OK", "31" if failed else "32")
            bar = ""
            if self.total and not failed:
                bar = f" [==================] {self.total}/{self.total}"
            self.stream.write(f"{marker}{bar}  {self.title} ({duration(time.monotonic() - self.started)})\n")
            self.stream.flush()


@contextlib.contextmanager
def operation(title, *, verbose=False, log_dir=None, log_file=None):
    """Progress goes to stderr; stdout stays available for JSON or summaries."""
    log = None
    path = None
    if log_dir is not None or log_file is not None:
        directory = Path(log_file).expanduser().absolute().parent if log_file else Path(log_dir)
        # Refuse symlinked log locations before creating anything.
        for parent in (*reversed(directory.parents), directory):
            if parent.is_symlink():
                raise OSError(f"log directory must not contain symlinks: {parent}")
        directory.mkdir(parents=True, exist_ok=True)
        if log_file:
            path = Path(log_file).expanduser().absolute()
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        else:
            descriptor, filename = tempfile.mkstemp(prefix="install-", suffix=".log", dir=directory)
            path = Path(filename)
        log = os.fdopen(descriptor, "w", encoding="utf-8")
    reporter = Reporter(title, verbose=verbose, log=log)
    token = _CURRENT.set(reporter)
    reporter.status(title)
    if path:
        print(f"Detailed log: {path}", file=sys.stderr, flush=True)
    reporter.thread.start()
    try:
        yield reporter
    except BaseException as error:
        reporter.detail(f"ERROR: {error}\n")
        reporter.finish(failed=True)
        if path:
            print(f"Details: {path}", file=sys.stderr, flush=True)
        raise
    else:
        reporter.finish(failed=reporter.failed)
    finally:
        _CURRENT.reset(token)
        if log:
            log.close()


def status(label, *, completed=None, total=None):
    reporter = _CURRENT.get()
    if reporter is not None:
        reporter.status(label, completed=completed, total=total)


def detail(*values, sep=" ", end="\n", file=None, flush=False, echo=False):
    reporter = _CURRENT.get()
    if reporter is None:
        print(*values, sep=sep, end=end, file=file or sys.stderr, flush=flush)
    else:
        reporter.detail(sep.join(str(value) for value in values) + end, echo=echo)


def is_active():
    return _CURRENT.get() is not None
