"""Managed Illumina tools must never drift to an unrelated saved environment."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oncotracer_cli import cli, variant_command
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml
from oncotracer_cli.variant_resources import discover_variant_resources
from oncotracer_cli.variant_runtime import apply_variant_runtime, managed_variant_runtime
from oncotracer_cli.variants import preflight_variant_tools, resolve_variant_request
from oncotracer_cli.web import WebState
from tests.test_web import HARDWARE


class ManagedVariantRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="oncotracer-managed-variants-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.prefix = self.root / "science-variants/variants"
        self.stale = self.root / "anaconda3/envs/getRNASomaticPy2"
        for prefix in (self.prefix, self.stale):
            (prefix / "bin").mkdir(parents=True)
            for name in ("samtools", "bcftools", "gatk"):
                binary = prefix / "bin" / name
                binary.write_text("#!/bin/sh\nexit 99\n")
                binary.chmod(0o755)
        self.install = {"backend": "conda", "core_prefix": str(self.root / "science/core"),
                        "variants_prefix": str(self.prefix)}
        self.config = {"mode": "illumina", "run_variants": True, "variant_specimen_type": "fresh",
                       "variant_tool_prefix": str(self.stale), "variant_annovar": "off"}
        environment = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
                       "XDG_DATA_HOME": str(self.home / "data"), "PATH": "/usr/bin:/bin",
                       "ONCOTRACER_VARIANTS_PREFIX": str(self.stale)}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, environment, clear=True).start()
        self.install_loader = patch("oncotracer_cli.cli._load_install_config", return_value=self.install).start()
        self.auth = patch("oncotracer_cli.install_safety.managed_variants_prefix",
                          return_value=self.prefix, create=True).start()

    def request(self, config=None, mode="illumina"):
        with contextlib.redirect_stderr(io.StringIO()) as diagnostic:
            request = resolve_variant_request(config or self.config, mode=mode)
        return request, diagnostic.getvalue()

    def saved_install(self, **updates):
        config = dict(self.install, **updates)
        path = self.home / "config/oncotracer/config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config))
        return config

    def test_stale_saved_prefix_and_path_cannot_override_managed_runtime(self):
        with patch.dict(os.environ, {"PATH": str(self.stale / "bin")}):
            request, diagnostic = self.request()
            tools = preflight_variant_tools(replace(request, engine="native"))
        self.assertEqual(request.tool_prefix, self.prefix)
        self.assertEqual(request.tool_prefix_source, "managed_install")
        self.assertIn("getRNASomaticPy2", diagnostic)
        self.assertIn("superseded", diagnostic)
        self.assertEqual(tools["bcftools"], str(self.prefix / "bin/bcftools"))
        self.assertEqual(tools["gatk"], str(self.prefix / "bin/gatk"))
        self.auth.assert_called_with(self.install, semantic=False)
        self.assertEqual(self.config["variant_tool_prefix"], str(self.stale))

    def test_glimpse_sample_parameters_receive_the_authenticated_prefix(self):
        from oncotracer_cli.glimpse_pipeline import sample_parameters
        request, _ = self.request()
        parameters, _audit = sample_parameters(request, "MOCK001", self.root / "input.bam", self.root / "ref.fa",
                                       self.root / "out", self.root / "resources", 2)
        self.assertEqual(parameters["tool_prefix"], str(self.prefix))

    def test_configured_managed_variants_ignore_inherited_prefix(self):
        for backend in ("conda", "poetry"):
            with self.subTest(backend=backend):
                install = dict(self.install, backend=backend)
                self.assertEqual(cli._configured_native_prefixes(install)["variants"], self.prefix)
                install.pop("variants_prefix")
                self.assertIsNone(cli._configured_native_prefixes(install)["variants"])

    def test_external_configured_variants_keep_environment_selection(self):
        for backend in ("host", "docker"):
            with self.subTest(backend=backend):
                install = dict(self.install, backend=backend)
                self.assertEqual(cli._configured_native_prefixes(install)["variants"], self.stale)

    def test_complete_managed_run_supersedes_saved_and_inherited_prefixes(self):
        base = self.root / "science"
        self.install.update({f"{name}_prefix": str(base / name)
                             for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")})
        config = self.root / "run.yml"
        config.write_text(render_flat_yaml(self.config))
        original = config.read_bytes()
        args = cli.build_parser().parse_args(["run", "--config", str(config), "--backend", "conda"])
        active = set()
        semantic_checks = []

        @contextlib.contextmanager
        def core_lock(selected, **_kwargs):
            self.assertEqual(selected, base)
            active.add("core")
            try:
                yield {}
            finally:
                active.remove("core")

        @contextlib.contextmanager
        def variants_lock(selected, *, semantic=False):
            self.assertEqual(selected, self.prefix)
            semantic_checks.append(semantic)
            active.add("variants")
            try:
                yield self.prefix
            finally:
                active.remove("variants")

        def native_dispatch(selected, **_kwargs):
            self.assertEqual(active, {"core", "variants"})
            request = resolve_variant_request(load_flat_yaml(selected), mode="illumina")
            self.assertEqual(request.tool_prefix, self.prefix)
            return self.root / "results"

        with patch.object(cli, "managed_conda_runtime_lock", side_effect=core_lock), \
             patch("oncotracer_cli.install_safety.managed_variants_runtime_lock", side_effect=variants_lock), \
             patch.object(cli, "_probe_core", return_value={"success": True}), \
             patch("oncotracer_cli.variants.preflight_variant_tools", return_value={}) as probe, \
             patch.object(cli, "_prepare_configured_hg38"), \
             patch.object(cli, "run_native", side_effect=native_dispatch) as dispatch, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.execute_run(config, args), self.root / "results")
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[0].tool_prefix, self.prefix)
        dispatch.assert_called_once()
        self.assertEqual(semantic_checks, [True, False])
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(os.environ["ONCOTRACER_VARIANTS_PREFIX"], str(self.stale))
        self.assertFalse((self.root / "results").exists())

    def test_missing_managed_prefix_fails_without_discovering_or_dispatching(self):
        self.install.pop("variants_prefix")
        with patch("shutil.which", side_effect=AssertionError("unexpected PATH fallback")):
            with self.assertRaisesRegex(OncoTracerError, "install --conda"):
                self.request()
        self.auth.assert_not_called()

    def test_invalid_ownership_fails_without_fallback(self):
        self.auth.side_effect = OncoTracerError("inventory changed")
        with patch("shutil.which", side_effect=AssertionError("unexpected PATH fallback")):
            with self.assertRaisesRegex(OncoTracerError, "inventory changed.*install --conda"):
                self.request()

    def test_explicit_unmanaged_host_prefix_is_preserved(self):
        self.install_loader.return_value = {"backend": "host"}
        request, diagnostic = self.request()
        self.assertEqual(request.tool_prefix, self.stale)
        self.assertEqual(request.tool_prefix_source, "configured_or_host")
        self.assertEqual(diagnostic, "")
        self.auth.assert_not_called()

    def test_docker_preview_does_not_authenticate_host_variants(self):
        from oncotracer_cli.docker_runtime import docker_host_preview
        self.auth.side_effect = AssertionError("host authentication for Docker")
        with docker_host_preview():
            request, _ = self.request()
        self.assertNotEqual(request.tool_prefix_source, "managed_install")

    def test_backend_override_uses_managed_tools_despite_saved_docker_backend(self):
        from oncotracer_cli.variant_runtime import variant_runtime_backend
        config = dict(self.config, execution_backend="docker")
        with variant_runtime_backend("conda"):
            request, _ = self.request(config)
        self.assertEqual(request.tool_prefix, self.prefix)
        self.assertEqual(request.tool_prefix_source, "managed_install")
        request, _ = self.request(config)
        self.assertEqual(request.tool_prefix_source, "configured_or_host")

    def test_docker_runtime_keeps_its_own_image_prefix(self):
        image_prefix = self.root / "image-native"
        (image_prefix / "bin").mkdir(parents=True)
        self.auth.side_effect = AssertionError("host authentication for Docker")
        with patch.dict(os.environ, {"ONCOTRACER_CONTAINER_RUNTIME": "docker", "ONCOTRACER_VARIANTS_PREFIX": str(image_prefix)}):
            request, _ = self.request()
        self.assertEqual(request.tool_prefix, image_prefix)

    def test_explicit_ont_caller_environment_is_unchanged(self):
        config = dict(self.config, mode="ont", variant_callers="clairs_to", variant_clairsto_platform="ont_r10_dorado_sup")
        request, diagnostic = self.request(config, mode="ont")
        self.assertEqual(request.tool_prefix, self.stale)
        self.assertEqual(diagnostic, "")
        self.auth.assert_not_called()

    def test_managed_common_prefix_does_not_change_ont_environment_fallback(self):
        environment = cli._native_environment(self.install)
        self.assertEqual(environment["ONCOTRACER_VARIANTS_PREFIX"], str(self.stale))

    def test_diagnostics_do_not_change_scientific_request_identity(self):
        request, _ = self.request()
        same = replace(request, tool_prefix_diagnostics=("another message",), tool_prefix_source="configured_or_host")
        self.assertEqual(request.as_dict(include_runtime_diagnostics=False), same.as_dict(include_runtime_diagnostics=False))
        self.assertNotEqual(request.as_dict(), same.as_dict())

    def test_execution_holds_the_separate_caller_domain_lock(self):
        active = []
        @contextlib.contextmanager
        def lock(prefix, *, semantic=False):
            self.assertEqual(prefix, self.prefix)
            self.assertTrue(semantic)
            active.append(True)
            try:
                yield
            finally:
                active.pop()
        with patch("oncotracer_cli.install_safety.managed_variants_runtime_lock", side_effect=lock, create=True):
            with managed_variant_runtime(self.config, mode="illumina", semantic=True):
                self.assertEqual(active, [True])
        self.assertEqual(active, [])

    def test_existing_bam_command_does_not_dispatch_before_install_validation(self):
        self.install.pop("variants_prefix")
        config = self.root / "variants.yml"
        config.write_text(render_flat_yaml(self.config))
        with patch("oncotracer_cli.variant_command._command_variants") as dispatch:
            with self.assertRaisesRegex(OncoTracerError, "install --conda"):
                variant_command.command_variants(SimpleNamespace(config=str(config), dry_run=False))
        dispatch.assert_not_called()

    def test_discovery_replaces_stale_prefix_and_keeps_optional_runtime_search(self):
        self.saved_install()
        ffpe = self.home / "anaconda3/envs/oncotracer-ffperase-env"
        (ffpe / "bin").mkdir(parents=True)
        python = ffpe / "bin/python"
        python.write_text("#!/bin/sh\nexit 99\n"); python.chmod(0o755)
        with patch("subprocess.run", side_effect=AssertionError("discovery executed a tool")):
            result = discover_variant_resources({"mode": "illumina", "backend": "conda",
                "callers": ["mutect2"], "specimen_type": "ffpe",
                "values": {"variant_tool_prefix": str(self.stale), "variant_annovar": "off"}})
        self.assertEqual(result["fields"]["variant_tool_prefix"], str(self.prefix))
        self.assertEqual(result["managed_variant_prefix"], str(self.prefix))
        self.assertEqual(result["fields"]["variant_ffperase_prefix"], str(ffpe))
        self.assertIn("superseded", " ".join(result["notes"]))
        self.assertFalse([x for x in result["candidates"] if x["field"] == "variant_tool_prefix"])

    def test_missing_managed_discovery_is_actionable_before_candidate_scanning(self):
        saved = self.saved_install()
        saved.pop("variants_prefix")
        (self.home / "config/oncotracer/config.json").write_text(json.dumps(saved))
        with patch("oncotracer_cli.variant_resources._Search.prefixes", side_effect=AssertionError("scanned unrelated Conda")):
            with self.assertRaisesRegex(OncoTracerError, "install --conda"):
                discover_variant_resources({"mode": "illumina", "backend": "conda", "callers": ["bcftools"]})

    def test_browser_defaults_choose_the_managed_prefix(self):
        args = SimpleNamespace(mode="illumina", variant_tool_prefix=str(self.stale))
        state = WebState(self.root, setup_args=args)
        state.hardware = HARDWARE
        defaults = state.system()["defaults"]
        self.assertEqual(defaults["variant_tool_prefix"], str(self.prefix))
        self.assertIn("superseded", defaults["variant_runtime_note"])

    def test_browser_without_platform_does_not_seed_an_ont_caller_path(self):
        state = WebState(self.root)
        state.hardware = HARDWARE
        self.assertNotIn("variant_tool_prefix", state.system()["defaults"])
        self.auth.assert_not_called()

    def test_browser_save_writes_managed_prefix_and_displays_replacement(self):
        reads = self.root / "reads"; reads.mkdir()
        with gzip.open(reads / "MOCK001.fastq.gz", "wt") as stream:
            stream.write("@mock\nACGT\n+\nIIII\n")
        state = WebState(self.root); state.hardware = HARDWARE
        scan = state.scan({"mode": "illumina", "folder": str(reads)})
        data = {"scan_id": scan["scan_id"], "project": str(self.root / "project"), "threads": 2,
                "backend": "conda", "variants": True, "variant_specimen_type": "fresh",
                "variant_tool_prefix": str(self.stale),
                "samples": [{"id": 0, "name": "MOCK001", "type": "cancer"}]}
        checked = subprocess.CompletedProcess([], 0, json.dumps({"errors": [], "warnings": []}), "")
        with patch("oncotracer_cli.web.subprocess.run", return_value=checked), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = state.prepare(data)
        saved = load_flat_yaml(Path(result["config_path"]))
        self.assertEqual(saved["variant_tool_prefix"], str(self.prefix))
        self.assertIn("superseded", " ".join(result["check"]["warnings"]))
        self.assertFalse((self.root / "project/results").exists())
        self.assertFalse((self.root / "project/reference").exists())

    def test_existing_bam_browser_load_and_save_preserve_diagnostic_and_inputs(self):
        bam = self.root / "sample.bam"; bam.write_bytes(b"synthetic BAM metadata fixture")
        fasta = self.root / "reference.fa"; fasta.write_text(">chr1\nACGT\n")
        manifest = self.root / "samples.tsv"
        manifest.write_text("sample\tbam\tstatus\nMOCK001\tsample.bam\ttumor\n")
        original = self.root / "saved.yml"
        original.write_text(render_flat_yaml(dict(self.config, outdir="previous", variant_reference="reference.fa", variant_bam_manifest="samples.tsv")))
        original_bytes = original.read_bytes()
        state = WebState(self.root); state.hardware = HARDWARE
        loaded = state.variant_load({"config_path": str(original)})
        self.assertEqual(loaded["config"]["variant_tool_prefix"], str(self.prefix))
        self.assertIn("superseded", " ".join(loaded["runtime_diagnostics"]))
        with patch("oncotracer_cli.variants.preflight_variant_tools", return_value={"bcftools": str(self.prefix / "bin/bcftools")}):
            result = state.variant_prepare({"load_id": loaded["load_id"], "project": str(self.root / "new-project"), "threads": 2})
        self.assertEqual(load_flat_yaml(Path(result["config_path"]))["variant_tool_prefix"], str(self.prefix))
        self.assertIn("superseded", " ".join(result["check"]["warnings"]))
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertFalse((self.root / "new-project/results").exists())

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser state checks")
    def test_browser_discovery_updates_stale_managed_field_but_preserves_other_paths(self):
        from oncotracer_cli.variant_web_ui import PAGE
        function = PAGE.split("async function detectVariantResources(payload", 1)[1].split("async function detectVariantResourcesBeforeSave", 1)[0]
        script = r"""
const assert=require('node:assert/strict');
const nodes={};
for(const id of ['variant-detection-status','variant-resource-review','variant-install-panel','variant_tool_prefix','variant-resource-title','variant-resource-dialog-status'])nodes[id]={hidden:false,textContent:'',value:'',dataset:{},dispatchEvent:()=>{}};
nodes.variant_tool_prefix.value='/old/getRNASomaticPy2';
const $=id=>nodes[id],document={getElementById:$};
let variantInstallActive=false,variantResourceLastResult=null,variantResourceLastScope=null;
const syncVariantInstallChoices=()=>{},renderVariantResources=()=>{},openVariantResourceDialog=()=>{};
const variantFieldLabel=x=>x,variantScopeFields=()=>['variant_tool_prefix'];
const variantApplyPath=(key,value)=>{if(nodes[key].value)return false;nodes[key].value=value;return true;};
const api=async()=>({fields:{variant_tool_prefix:'/managed/variants'},managed_variant_prefix:'/managed/variants',resources:[]});
""" + "async function detectVariantResources(payload" + function + r"""
(async()=>{await detectVariantResources({},'all',false);assert.equal(nodes.variant_tool_prefix.value,'/managed/variants');assert.match(nodes['variant-detection-status'].textContent,/updated/);assert.match(nodes['variant-resource-dialog-status'].textContent,/Other entered paths were preserved/);})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
