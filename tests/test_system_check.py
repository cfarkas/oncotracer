from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oncotracer_cli.cli import main
from oncotracer_cli.system_check import (
    GIB, _inspect_gpus, inspect_hardware, print_resource_report, resource_report,
)


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
            {"methylation_only": True, "run_cna_classifier": True,
             "knowledge_literature_llm_models": "custom/model",
             "knowledge_deep_llm_ranker_models": "custom/model"},
            hardware=self.hardware(1000),
        )
        for row in report["capabilities"]:
            if row["task"] in {"Local report LLM", "ONT methylation models"}:
                self.assertEqual(row["status"], "not_assessed")

    def test_catalog_drafts_report_default_model_memory_without_web(self):
        config = {"run_cna_classifier": True, "knowledge_catalog_llm": True,
                  "knowledge_literature_llm": False, "knowledge_deep_enable_llm_ranker": False,
                  "knowledge_web": False}
        for ram, status in ((16, "limited_memory"), (32, "likely_feasible")):
            with self.subTest(ram=ram):
                report = resource_report(config, hardware=self.hardware(ram))
                task = next(row for row in report["capabilities"] if row["task"] == "Local report LLM")
                self.assertTrue(task["selected"])
                self.assertEqual(task["planning_ram_gib"], 24)
                self.assertEqual(task["status"], status)

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


class GPUInventoryTests(unittest.TestCase):
    def test_gpu_inventory_is_opt_in_for_hardware_and_resource_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detected = {"gpus": [{"index": 0, "name": "NVIDIA example"}],
                        "gpu_detection_status": "detected", "gpu_note": "Inventory"}
            with patch("oncotracer_cli.system_check._inspect_gpus", return_value=detected) as probe:
                hardware = inspect_hardware(proc=root / "proc", cgroup=root / "cgroup")
                self.assertEqual(hardware["gpu_detection_status"], "not_checked")
                self.assertEqual(hardware["gpus"], [])
                report = resource_report(path=root)
                self.assertIn("No analysis, downloads, GPU calls", report["limits"])
                probe.assert_not_called()
                hardware = inspect_hardware(
                    proc=root / "proc", cgroup=root / "cgroup", include_gpus=True
                )
                probe.assert_called_once_with()
                self.assertEqual(hardware["gpus"], detected["gpus"])
                report = resource_report(path=root, hardware=hardware)
                self.assertIn("No analysis, downloads, GPU workloads", report["limits"])

    def test_gpu_models_and_vram_use_only_bounded_inventory_query(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout="0, NVIDIA RTX A5000, 24564, 22000\n1, NVIDIA GPU, [N/A], [N/A]\n",
        )
        with (
            patch("oncotracer_cli.system_check.shutil.which", return_value="/tools/nvidia-smi"),
            patch("oncotracer_cli.system_check.subprocess.run", return_value=completed) as run,
            patch.dict(os.environ, {}, clear=True),
        ):
            inventory = _inspect_gpus()
        run.assert_called_once_with(
            ["/tools/nvidia-smi", "--query-gpu=index,name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        self.assertEqual(inventory["gpu_detection_status"], "detected")
        self.assertEqual(len(inventory["gpus"]), 2)
        self.assertEqual(inventory["gpus"][0]["name"], "NVIDIA RTX A5000")
        self.assertEqual(inventory["gpus"][0]["memory_total_bytes"], 24564 * 1024**2)
        self.assertEqual(inventory["gpus"][0]["memory_free_bytes"], 22000 * 1024**2)
        self.assertIsNone(inventory["gpus"][1]["memory_total_bytes"])
        self.assertIn("core counts are not reported", inventory["gpu_note"])
        self.assertNotIn("cuda_cores", inventory["gpus"][0])

    def test_missing_inventory_tool_never_launches_a_command(self):
        with (
            patch("oncotracer_cli.system_check.shutil.which", return_value=None),
            patch("oncotracer_cli.system_check.subprocess.run") as run,
        ):
            inventory = _inspect_gpus()
        run.assert_not_called()
        self.assertEqual(inventory["gpu_detection_status"], "unavailable")
        self.assertEqual(inventory["gpus"], [])
        self.assertIn("Other GPU vendors", inventory["gpu_note"])

    def test_timeout_and_driver_failure_allow_hardware_reporting(self):
        for failure in (subprocess.TimeoutExpired("nvidia-smi", 3), OSError("unavailable")):
            with (
                self.subTest(failure=failure),
                patch("oncotracer_cli.system_check.shutil.which", return_value="nvidia-smi"),
                patch("oncotracer_cli.system_check.subprocess.run", side_effect=failure),
            ):
                inventory = _inspect_gpus()
            self.assertEqual(inventory["gpu_detection_status"], "failed")
            self.assertEqual(inventory["gpus"], [])
        for completed in (
            SimpleNamespace(returncode=9, stdout=""),
            SimpleNamespace(returncode=0, stdout="unexpected output"),
        ):
            with (
                self.subTest(completed=completed),
                patch("oncotracer_cli.system_check.shutil.which", return_value="nvidia-smi"),
                patch("oncotracer_cli.system_check.subprocess.run", return_value=completed),
            ):
                inventory = _inspect_gpus()
            self.assertEqual(inventory["gpu_detection_status"], "failed")
            self.assertEqual(inventory["gpus"], [])

    def test_visibility_restrictions_do_not_claim_cuda_availability(self):
        with (
            patch("oncotracer_cli.system_check.shutil.which", return_value="nvidia-smi"),
            patch("oncotracer_cli.system_check.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout="0, NVIDIA GPU, 24000, 23000\n",
            )),
            patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""}, clear=True),
        ):
            inventory = _inspect_gpus()
        self.assertIn("CUDA_VISIBLE_DEVICES=''", inventory["gpu_note"])
        self.assertIn("may not all be accessible", inventory["gpu_note"])
        self.assertIn("CUDA access and model compatibility are not tested", inventory["gpu_note"])

    def test_public_summary_includes_gpu_model_and_vram(self):
        hardware = SystemCheckTests().hardware(64)
        hardware.update(
            gpus=[{"index": 0, "name": "NVIDIA example", "memory_total_bytes": 24 * GIB,
                   "memory_free_bytes": 20 * GIB}],
            gpu_detection_status="detected", gpu_note="Driver inventory only.",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_resource_report(resource_report(hardware=hardware))
        self.assertIn("NVIDIA example; 24.0 GiB VRAM total, 20.0 GiB free", output.getvalue())
        self.assertIn("Driver inventory only", output.getvalue())

    def test_cpu_affinity_error_falls_back_without_failing_hardware_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("os.sched_getaffinity", side_effect=OSError("unavailable")),
                patch("os.cpu_count", return_value=8),
                patch("oncotracer_cli.system_check._inspect_gpus", return_value={"gpus": []}),
            ):
                hardware = inspect_hardware(proc=root / "proc", cgroup=root / "cgroup")
            self.assertEqual(hardware["cpu_workers_available"], 8)
            self.assertEqual(hardware["gpus"], [])


if __name__ == "__main__":
    unittest.main()
