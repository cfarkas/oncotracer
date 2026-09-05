from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.cli import main
from oncotracer_cli.system_check import GIB, inspect_hardware, resource_report


class SystemCheckTests(unittest.TestCase):
    def hardware(self, ram=8):
        return {
            "os": "Linux",
            "architecture": "x86_64",
            "python_supported": True,
            "cpu_workers_available": 2,
            "ram_available_bytes": ram * GIB,
            "ram_total_bytes": ram * GIB,
        }

    def test_small_machine_gets_specific_limits_not_blanket_failure(self):
        report = resource_report(
            {"mode": "illumina", "threads": 8}, hardware=self.hardware()
        )
        tasks = {row["task"]: row for row in report["capabilities"]}
        self.assertEqual(
            tasks["Configure runs and read existing reports"]["status"],
            "likely_feasible",
        )
        self.assertEqual(tasks["Illumina CNA analysis"]["status"], "limited_memory")
        self.assertEqual(tasks["Local report LLM"]["status"], "not_assessed")
        self.assertTrue(any("threads: 8" in message for message in report["warnings"]))

    def test_unknown_models_never_report_guaranteed_success(self):
        report = resource_report(
            {"methylation_only": True, "run_cna_classifier": True},
            hardware=self.hardware(1000),
        )
        for row in report["capabilities"]:
            if row["task"] in {"Local report LLM", "ONT methylation models"}:
                self.assertEqual(row["status"], "not_assessed")

    def test_cgroup_v2_memory_and_cpu_ancestors_limit_host_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            cgroup = root / "cgroup"
            (proc / "self").mkdir(parents=True)
            leaf = cgroup / "job/child"
            leaf.mkdir(parents=True)
            (proc / "meminfo").write_text(
                f"MemTotal: {1000 * 1024**2} kB\nMemAvailable: {900 * 1024**2} kB\nSwapTotal: {100 * 1024**2} kB\n"
            )
            (proc / "self/cgroup").write_text("0::/job/child\n")
            (cgroup / "job/memory.max").write_text(str(8 * GIB))
            (cgroup / "job/memory.current").write_text(str(3 * GIB))
            (leaf / "memory.max").write_text("max")
            (cgroup / "job/cpu.max").write_text("200000 100000")
            with patch("os.sched_getaffinity", return_value=set(range(64))):
                observed = inspect_hardware(proc=proc, cgroup=cgroup)
            self.assertEqual(observed["ram_total_bytes"], 8 * GIB)
            self.assertEqual(observed["ram_available_bytes"], 5 * GIB)
            self.assertEqual(observed["cpu_workers_available"], 2)

    def test_cgroup_v1_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            cgroup = root / "cgroup"
            (proc / "self").mkdir(parents=True)
            (cgroup / "memory/job").mkdir(parents=True)
            (cgroup / "cpu/job").mkdir(parents=True)
            (proc / "self/cgroup").write_text("2:memory:/job\n3:cpu,cpuacct:/job\n")
            (cgroup / "memory/job/memory.limit_in_bytes").write_text(str(4 * GIB))
            (cgroup / "memory/job/memory.usage_in_bytes").write_text(str(GIB))
            (cgroup / "cpu/job/cpu.cfs_quota_us").write_text("100000")
            (cgroup / "cpu/job/cpu.cfs_period_us").write_text("100000")
            observed = inspect_hardware(proc=proc, cgroup=cgroup)
            self.assertEqual(observed["ram_available_bytes"], 3 * GIB)
            self.assertEqual(observed["cpu_workers_available"], 1)

    def test_public_system_command_is_read_only_and_json_parseable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "not-created"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["system", "--json", "--path", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["schema"], "oncotracer-system-v1"
            )
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
