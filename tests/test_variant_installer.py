"""Explicit tool installs run separately from analysis and never execute recipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.variant_installer import VariantInstaller, tools_roots
from oncotracer_cli.variant_resources import discover_variant_resources
from oncotracer_cli.web import WebState


class VariantInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = self.root / "conda"
        self.manager.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys, time
root = pathlib.Path(__file__).parent
with (root / 'commands.jsonl').open('a') as handle: handle.write(json.dumps(sys.argv[1:]) + '\n')
if (root / 'fail').exists(): print('fixture download failure', flush=True); sys.exit(19)
if (root / 'wait').exists(): print('fixture downloading', flush=True); time.sleep(30)
if '--prefix' in sys.argv:
    prefix = pathlib.Path(sys.argv[sys.argv.index('--prefix') + 1]); (prefix / 'bin').mkdir(parents=True)
    names = ['samtools', 'bcftools', 'gatk', 'freebayes', 'varlociraptor', 'run_clair3.sh',
             'python2.7', 'configureStrelkaGermlineWorkflow.py', 'configureStrelkaSomaticWorkflow.py', 'python']
    for name in names:
        path = prefix / 'bin' / name
        path.write_text('#!/bin/sh\nprintf "fixture tool verified\\n"\n')
        path.chmod(0o755)
print('fixture installation completed', flush=True)
''')
        self.manager.chmod(0o755)
        self.installer = VariantInstaller(self.root, install_root=self.root / "installed")
        self.addCleanup(self.installer.close)
        self.payload = {"mode": "illumina", "backend": "host", "flow": "fastq", "specimen_type": "fresh",
                        "callers": ["mutect2", "freebayes"], "values": {"variant_tool_prefix": str(self.root / "missing"),
                        "variant_annovar": "off", "variant_varlociraptor": "required"}}
        self.executables = {"conda": str(self.manager), "docker": str(self.manager), "apptainer": str(self.manager)}
        patcher = patch("oncotracer_cli.variant_installer._executable", side_effect=lambda name: self.executables.get(name))
        patcher.start(); self.addCleanup(patcher.stop)

    def finish(self):
        limit = time.monotonic() + 15
        while self.installer.active() and time.monotonic() < limit:
            time.sleep(0.05)
        self.assertFalse(self.installer.active(), self.installer.status())
        self.installer.worker.join(timeout=2)
        return self.installer.status()

    def test_plan_is_read_only_and_injected_commands_are_not_used(self):
        before = set(self.root.rglob("*"))
        with patch("subprocess.Popen", side_effect=AssertionError("planning executed a process")):
            plan = self.installer.plan({**self.payload, "command": ["touch", str(self.root / "unwanted")]})
        self.assertTrue(plan["available"])
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertTrue(all(step["command"][0] != "touch" for step in plan["steps"]))
        self.assertNotEqual(plan["fields"]["variant_tool_prefix"], self.payload["values"]["variant_tool_prefix"])
        for invalid in ({}, {"plan_id": []}, {"plan_id": "unknown", "command": ["touch", "unwanted"]}):
            with self.assertRaises(OncoTracerError): self.installer.start(invalid)

    def test_success_creates_fresh_prefix_verifies_tools_and_returns_paths(self):
        preserved = self.root / "missing"; preserved.mkdir(); (preserved / "keep").write_text("existing environment")
        plan = self.installer.plan(self.payload)
        job = self.installer.start({"plan_id": plan["id"]})
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["fields"], {})
        finished = self.finish()
        self.assertEqual(finished["status"], "complete", finished)
        self.assertIn("fixture tool verified", finished["log"])
        self.assertEqual(finished["completed_steps"], finished["total_steps"])
        self.assertEqual((preserved / "keep").read_text(), "existing environment")
        prefix = Path(finished["fields"]["variant_tool_prefix"])
        self.assertTrue((prefix / "bin/gatk").is_file())
        self.assertEqual(json.loads((prefix.parent / "status.json").read_text())["status"], "complete")
        with self.assertRaises(OncoTracerError): self.installer.start({"plan_id": plan["id"]})

    def test_failure_keeps_log_does_not_publish_paths_and_retry_uses_new_folder(self):
        (self.root / "fail").touch()
        plan = self.installer.plan(self.payload)
        self.installer.start({"plan_id": plan["id"]})
        failed = self.finish()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["fields"], {})
        self.assertIn("exit 19", failed["error"])
        self.assertIn("fixture download failure", failed["log"])
        (self.root / "fail").unlink()
        next_plan = self.installer.plan(self.payload)
        self.assertNotEqual(next_plan["directory"], plan["directory"])
        self.installer.start({"plan_id": next_plan["id"]})
        self.assertEqual(self.finish()["status"], "complete")
        self.assertTrue(Path(failed["log_path"]).is_file())

    def test_stop_and_duplicate_start_never_touch_an_analysis_process(self):
        (self.root / "wait").touch()
        plan = self.installer.plan(self.payload)
        self.installer.start({"plan_id": plan["id"]})
        with self.assertRaisesRegex(OncoTracerError, "still running"):
            self.installer.start({"plan_id": plan["id"]})
        with self.assertRaisesRegex(OncoTracerError, "current tool installation"):
            self.installer.stop({"job_id": "different"})
        self.installer.stop({"job_id": plan["id"]})
        self.assertEqual(self.finish()["status"], "cancelled")
        self.assertEqual(self.installer.status()["fields"], {})

    def test_prepare_and_run_wait_for_installer(self):
        state = WebState(self.root); state.variant_installer = self.installer
        (self.root / "wait").touch()
        plan = self.installer.plan(self.payload); self.installer.start({"plan_id": plan["id"]})
        for action in (state.prepare, state.variant_prepare, state.run):
            with self.assertRaisesRegex(OncoTracerError, "Tool installation is still running"): action({})

    def test_docker_plan_checks_daemon_image_and_selected_callers_without_host_prefixes(self):
        plan = self.installer.plan({**self.payload, "method": "docker", "docker_image": "example/oncotracer:tested"})
        self.assertEqual(plan["fields"], {"backend": "docker", "docker_image": "example/oncotracer:tested"})
        self.assertEqual(plan["steps"][1]["command"], [str(self.manager), "pull", "example/oncotracer:tested"])
        self.assertIn("Check selected callers in Docker", [step["label"] for step in plan["steps"]])
        self.installer.start({"plan_id": plan["id"]})
        self.assertEqual(self.finish()["status"], "complete")
        for image in ("--privileged", "repo/image; touch /tmp/no", "repo/image\nanything"):
            with self.assertRaises(OncoTracerError): self.installer.plan({**self.payload, "method": "docker", "docker_image": image})

    def test_unsupported_routes_offer_reason_and_do_not_start(self):
        for extra in ({"method": "docker", "flow": "bam"}, {"method": "docker", "analysis": "both"}):
            plan = self.installer.plan({**self.payload, **extra})
            self.assertFalse(plan["available"])
            self.assertTrue(plan["reason"])
            with self.assertRaises(OncoTracerError): self.installer.start({"plan_id": plan["id"]})
        self.executables.clear()
        plan = self.installer.plan(self.payload)
        self.assertFalse(any(option["available"] for option in plan["options"]))

    def test_ffpe_and_strelka_are_separate_pinned_environments(self):
        plan = self.installer.plan({**self.payload, "specimen_type": "ffpe", "callers": ["strelka2_germline"],
            "values": {**self.payload["values"], "variant_strelka_prefix": str(self.root / "no-strelka"),
                       "variant_ffperase_prefix": str(self.root / "no-ffpe"), "variant_ffperase": "required"}})
        self.assertEqual(set(plan["fields"]), {"backend", "variant_tool_prefix", "variant_strelka_prefix", "variant_ffperase_prefix"})
        self.assertEqual({step.get("specification") for step in plan["steps"] if step.get("specification")},
                         {"native-variants.yml", "native-strelka2.yml", "native-ffperase.yml"})

    def test_ont_plan_keeps_chemistry_and_models_separate(self):
        plan = self.installer.plan({**self.payload, "mode": "ont", "callers": ["clair3", "clairs_to"],
                                   "values": {**self.payload["values"], "variant_clair3_model": "auto"}})
        self.assertTrue(plan["available"], plan)
        self.assertIn("clair3=1.2.0", plan["steps"][0]["command"])
        self.assertTrue(plan["fields"]["variant_clairsto_sif"].endswith("clairs-to_v0.4.4.sif"))
        self.assertNotIn("variant_clair3_model", plan["fields"])
        self.assertNotIn("variant_clairsto_platform", plan["fields"])

    def test_tools_folder_finds_direct_prefixes_and_rejects_invalid_folder(self):
        self.assertEqual(tools_roots(self.root, {"tools_folder": str(self.root)}), (self.root, self.root))
        with self.assertRaises(OncoTracerError): tools_roots(self.root, {"tools_folder": []})
        with self.assertRaises(OncoTracerError): tools_roots(self.root, {"tools_folder": str(self.root / "absent")})
        plan = self.installer.plan(self.payload); self.installer.start({"plan_id": plan["id"]}); finished = self.finish()
        prefix = Path(finished["fields"]["variant_tool_prefix"])
        with patch.dict(os.environ, {"PATH": "", "ONCOTRACER_VARIANTS_PREFIX": ""}):
            result = discover_variant_resources({**self.payload, "values": {"variant_annovar": "off"}}, roots=(prefix,))
        self.assertIn(str(prefix), [result["fields"].get("variant_tool_prefix"), *[c["path"] for c in result["candidates"]]])

    def test_symlink_destination_is_rejected(self):
        destination = self.root / "actual"; destination.mkdir()
        self.installer.install_root.symlink_to(destination, target_is_directory=True)
        plan = self.installer.plan(self.payload)
        with self.assertRaisesRegex(OncoTracerError, "symlinks"): self.installer.start({"plan_id": plan["id"]})
        self.assertEqual(list(destination.iterdir()), [])

    def test_only_completed_installs_are_discovered_after_restart(self):
        fake_home = self.root / "isolated-home"
        job = fake_home / ".local/share/oncotracer/optional-tools/installs/job"
        prefix = job / "variants"; (prefix / "bin").mkdir(parents=True)
        for name in ("samtools", "bcftools", "freebayes"):
            tool = prefix / "bin" / name; tool.write_text("#!/bin/sh\nexit 0\n"); tool.chmod(0o755)
        (fake_home / ".conda").mkdir()
        (fake_home / ".conda/environments.txt").write_text(str(prefix) + "\n")
        request = {"mode": "illumina", "backend": "host", "callers": ["freebayes"], "values": {"variant_annovar": "off"}}
        with patch.dict(os.environ, {"HOME": str(fake_home), "PATH": ""}, clear=True):
            for status in ("running", "failed", "cancelled", "complete"):
                (job / "status.json").write_text(json.dumps({"installer": "oncotracer-optional-tools-v1", "status": status}))
                result = discover_variant_resources(request)
                if status == "complete": self.assertEqual(result["fields"]["variant_tool_prefix"], str(prefix))
                else: self.assertNotIn("variant_tool_prefix", result["fields"])


if __name__ == "__main__":
    unittest.main()
