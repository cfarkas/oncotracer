#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GIB_KIB = 1024 * 1024
GIB_BYTES = 1024**3


class ParityResourceGuardTests(unittest.TestCase):
    def test_swap_inspection_uses_supported_read_only_options(self) -> None:
        driver = (ROOT / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        commands = [
            ["swapon", "--show=NAME,SIZE,USED", "--bytes", "--noheadings", "--raw"],
            ["swapon", "--show=NAME,SIZE,USED,PRIO", "--bytes"],
        ]
        for command in commands:
            self.assertIn(" ".join(command), driver)
        if not shutil.which("swapon"):
            self.skipTest("read-only swapon query unavailable on this platform")
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def run_guard(
        self,
        *,
        phase: str = "native-runs-complete",
        available_gib: int = 8,
        memory_gib: int = 15,
        swap_gib: int = 32,
        active_swap_gib: int = 32,
        swap_header_bytes: int = 0,
        swap_required: int = 1,
        planned_swap_gib: int = 32,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(ROOT / "scripts/ci_resource_phase_guard.sh"),
                phase,
                str(available_gib * GIB_KIB),
                str(memory_gib * GIB_KIB),
                str(swap_gib * GIB_KIB),
                str(active_swap_gib * GIB_BYTES - swap_header_bytes),
                str(swap_required),
                "15",
                "47",
                str(planned_swap_gib),
                "8",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_phase_guard_rejects_seven_gib_before_evidence_is_written(self) -> None:
        failed = self.run_guard(available_gib=7)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("filesystem reserve is 8 GiB", failed.stderr)

        driver = (ROOT / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        body = driver[
            driver.index("record_phase_resources() {") : driver.index(
                "\n\nrecord_image_ownership()",
                driver.index("record_phase_resources() {"),
            )
        ]
        self.assertLess(
            body.index("ci_resource_phase_guard.sh"),
            body.index("RESOURCE_PHASE_INDEX=$(("),
        )
        self.assertLess(
            body.index("RESOURCE_PHASE_INDEX=$(("),
            body.index('output="$RESOURCE_PHASE_ROOT/'),
        )

    def test_phase_guard_enforces_physical_addressable_and_active_swap(self) -> None:
        self.assertEqual(self.run_guard().returncode, 0)
        self.assertNotEqual(self.run_guard(memory_gib=14).returncode, 0)
        self.assertNotEqual(self.run_guard(swap_gib=0).returncode, 0)
        self.assertNotEqual(self.run_guard(active_swap_gib=31).returncode, 0)
        self.assertEqual(self.run_guard(swap_header_bytes=4096).returncode, 0)
        self.assertNotEqual(self.run_guard(swap_header_bytes=8192).returncode, 0)
        preflight = self.run_guard(
            phase="preflight-passed",
            swap_gib=0,
            active_swap_gib=0,
            swap_required=0,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)

    def test_ample_physical_memory_selects_zero_swap_and_low_memory_selects_32(
        self,
    ) -> None:
        selector = ROOT / "scripts/ci_select_parity_swap.sh"
        high = subprocess.run(
            [
                "bash",
                str(selector),
                str(995 * GIB_KIB),
                "47",
                "/runner/temp",
                "123",
                "2",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(high.stdout, "0\tnone\n")
        low = subprocess.run(
            [
                "bash",
                str(selector),
                str(16 * GIB_KIB),
                "47",
                "/runner/temp",
                "123",
                "2",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(low.stdout, "32\t/runner/temp/oncotracer-swap-123-2\n")

        zero = self.run_guard(
            available_gib=40,
            memory_gib=995,
            swap_gib=0,
            active_swap_gib=0,
            swap_required=0,
            planned_swap_gib=0,
        )
        self.assertEqual(zero.returncode, 0, zero.stderr)

    def test_zero_swap_cleanup_never_invokes_sudo_or_swapon(self) -> None:
        driver = (ROOT / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        start = driver.index("cleanup_job_swap() {")
        end = driver.index("\n\nwrite_ichor_asset_manifest()", start)
        function = driver[start:end]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            log = root / "sudo.log"
            fake = root / "bin"
            fake.mkdir()
            sudo = fake / "sudo"
            sudo.write_text(
                f"#!/bin/sh\necho called >> {log}\nexit 99\n", encoding="utf-8"
            )
            sudo.chmod(0o755)
            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    "PARITY_SWAP_GIB=0",
                    "SWAP_FILE=none",
                    f"RUNNER_TEMP={root}",
                    "GITHUB_RUN_ID=123",
                    "GITHUB_RUN_ATTEMPT=2",
                    function,
                    "cleanup_job_swap",
                )
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", command],
                env={"PATH": f"{fake}:/usr/bin:/bin"},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(log.exists(), "zero-swap cleanup invoked sudo")

    def _write_fake_commands(self, directory: Path, *, omit: str | None = None) -> None:
        for name in ("samtools", "bwa", "minimap2", "pigz", "curl", "wget", "git"):
            if name == omit:
                continue
            path = directory / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

    def test_preinstalled_prerequisites_need_no_sudo(self) -> None:
        helper = ROOT / "scripts/ci_parity_prerequisites.sh"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = root / "bin"
            fake.mkdir()
            conda_prefix = root / "preinstalled-conda-bin"
            conda_prefix.mkdir()
            self._write_fake_commands(conda_prefix)
            sudo_log = root / "sudo.log"
            sudo = fake / "sudo"
            sudo.write_text(
                f"#!/bin/sh\necho called >> {sudo_log}\nexit 99\n", encoding="utf-8"
            )
            sudo.chmod(0o755)
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    f'. "{helper}"; ensure_frozen_comparator_prerequisites "{conda_prefix}"',
                ],
                env={"PATH": str(fake)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("PREINSTALLED", completed.stdout)
            self.assertFalse(sudo_log.exists())

    def test_missing_prerequisite_without_sudo_fails_actionably(self) -> None:
        helper = ROOT / "scripts/ci_parity_prerequisites.sh"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = root / "bin"
            fake.mkdir()
            self._write_fake_commands(fake, omit="minimap2")
            sudo_log = root / "sudo.log"
            sudo = fake / "sudo"
            sudo.write_text(
                f'#!/bin/sh\necho "$*" >> {sudo_log}\nexit 1\n', encoding="utf-8"
            )
            sudo.chmod(0o755)
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    f'. "{helper}"; ensure_frozen_comparator_prerequisites',
                ],
                env={"PATH": str(fake)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("minimap2", completed.stderr)
            self.assertIn("no passwordless sudo", completed.stderr)
            self.assertEqual(sudo_log.read_text(encoding="utf-8"), "-n true\n")
            self.assertNotIn("apt-get", sudo_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
