"""Advisory cohort progress; never a checkpoint or scientific completion receipt."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

from .terminal_progress import clean, elapsed


class CohortProgress:
    def __init__(self, output_dir, total_samples, *, interval=30):
        self.path = Path(output_dir) / "progress.json"
        self.interval = interval
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.started = time.monotonic()
        self.data = {"schema": "oncotracer-cohort-progress-v1", "started_ns": time.time_ns(),
                     "status": "running", "stage": "Checking inputs and completed outputs",
                     "total_samples": total_samples, "prepared_samples": 0,
                     "counts": {}, "validated_records": None}

    def emit(self):
        from .runtime import atomic_write_json
        with self.lock:
            self.data["elapsed_seconds"] = round(time.monotonic() - self.started, 1)
            self.data["updated_ns"] = time.time_ns()
            value = dict(self.data)
        counts = value["counts"]
        text = (f"[cohort] {clean(value['stage'])} | "
                f"samples prepared {value['prepared_samples']}/{value['total_samples']} | "
                f"merged {counts.get('all_evidence_records', 0):,} variants | "
                f"population {counts.get('germline_population_records', 0):,} | "
                f"elapsed {elapsed(value['elapsed_seconds'])}")
        if value.get("validated_records") is not None:
            text += f" | validated {value['validated_records']:,} records"
        try:
            atomic_write_json(self.path, value)
            print(text, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # Display failures never change analysis results.

    def update(self, *, announce=False, **values):
        with self.lock:
            self.data.update(values)
        if announce:
            self.emit()

    def finish(self, counts, *, reused=False):
        self.update(status="complete", stage="Reused verified merged VCFs" if reused else "Complete",
                    counts=dict(counts), prepared_samples=self.data["total_samples"],
                    reused=reused, announce=True)

    def __enter__(self):
        self.emit()
        def refresh():
            while not self.stop.wait(self.interval):
                self.emit()
        self.thread = threading.Thread(target=refresh, name="oncotracer-cohort-progress", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if kind is not None:
            self.update(status="interrupted" if issubclass(kind, (KeyboardInterrupt, SystemExit)) else "failed",
                        stage="Stopped before cohort publication", announce=True)
        return False
