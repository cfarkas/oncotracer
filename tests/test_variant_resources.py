"""Resource discovery must be useful without running or modifying anything."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.variant_resources import MAX_CHILDREN, MAX_SEARCHED, discover_variant_resources


class VariantResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oncotracer-resources-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.env = {"HOME": str(self.home), "PATH": str(self.bin)}
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        system_patch = patch("oncotracer_cli.variant_resources.SYSTEM_PREFIXES", ())
        system_patch.start()
        self.addCleanup(system_patch.stop)
        self.default = {"mode": "illumina", "backend": "host", "callers": ["freebayes"],
                        "specimen_type": "fresh", "values": {"variant_annovar": "off"}}

    def file(self, path, text="fixture\n", executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if executable:
            path.chmod(0o755)
        return path

    def tools(self, prefix, *names):
        for name in names:
            self.file(prefix / "bin" / name, "#!/bin/sh\nexit 99\n", executable=True)
        return prefix

    def detect(self, **changes):
        data = dict(self.default)
        data.update(changes)
        return discover_variant_resources(data, roots=(self.project,), environment=self.env)

    def resources(self, result):
        return {row["id"]: row for row in result["resources"]}

    def ffpe_source(self, path):
        for name in ("annotate_w_pileup", "annotate_variants.py", "classify_w_random_forest.py", "microrep_python3.py"):
            self.file(path / "bin" / name)
        return path

    def ffpe_models(self, path):
        for kind in ("snvs", "indels"):
            self.file(path / f"model.{kind}.joblib")
        return path

    def annovar(self, path, build="hg38"):
        self.file(self.bin / "perl", "#!/bin/sh\nexit 99\n", executable=True)
        for name in ("table_annovar.pl", "annotate_variation.pl", "convert2annovar.pl", "coding_change.pl"):
            self.file(path / name, "#!/usr/bin/env perl\n# $Revision: fixture $\n", executable=True)
        self.file(path / "humandb" / f"{build}_refGene.txt")
        self.file(path / "humandb" / f"{build}_refGeneMrna.fa")
        return path

    def test_missing_resources_return_guides_and_json_without_side_effects(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        with patch("subprocess.run", side_effect=AssertionError("Executed command")), \
             patch("subprocess.Popen", side_effect=AssertionError("Executed command")), \
             patch("os.system", side_effect=AssertionError("Executed shell")):
            result = self.detect()
        self.assertEqual(set(result), {"backend", "fields", "resources", "candidates", "install_guides", "searched", "notes"})
        self.assertEqual(result["fields"], {})
        self.assertEqual(self.resources(result)["variant_tools"]["status"], "missing")
        self.assertIn("variant_tools", [g["id"] for g in result["install_guides"]])
        self.assertTrue(all(isinstance(g["commands"], str) for g in result["install_guides"]))
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))
        json.dumps(result)

    def test_registered_complete_environment_is_selected(self):
        prefix = self.tools(self.root / "registered", "samtools", "bcftools", "freebayes", "varlociraptor")
        self.file(self.home / ".conda/environments.txt", str(prefix) + "\n")
        result = self.detect(values={"variant_annovar": "off", "variant_varlociraptor": "required"})
        self.assertEqual(result["fields"]["variant_tool_prefix"], str(prefix))
        self.assertEqual(self.resources(result)["varlociraptor"]["status"], "found")
        self.assertFalse(result["install_guides"])

    def test_saved_managed_environment_and_recipe_locations_are_discovered(self):
        for mechanism in ("saved", "managed", "optional"):
            with self.subTest(mechanism=mechanism):
                prefix = self.tools(self.home / ".local/share/oncotracer" /
                                    ("optional-tools/variants" if mechanism == "optional" else "2.1.0/envs/core"),
                                    "samtools", "bcftools", "freebayes")
                if mechanism == "saved":
                    config = self.file(self.home / ".config/oncotracer/config.json", json.dumps({"core_prefix": str(prefix)}))
                result = self.detect()
                self.assertEqual(result["fields"]["variant_tool_prefix"], str(prefix))
                if mechanism == "saved":
                    config.unlink()
                for tool in (prefix / "bin").iterdir():
                    tool.unlink()

    def test_direct_project_tools_prefix_is_detected(self):
        prefix = self.tools(self.project / "tools/oncotracer-variants-env", "samtools", "bcftools", "gatk", "varlociraptor")
        runtime = self.tools(self.project / "tools/oncotracer-ffperase-env", "python")
        self.tools(self.home / "anaconda3/envs/ffpe_unrelated", "python")
        result = self.detect(callers=["mutect2"], specimen_type="ffpe",
                             values={"variant_annovar": "off", "variant_varlociraptor": "required"})
        self.assertEqual(result["fields"]["variant_tool_prefix"], str(prefix))
        self.assertEqual(result["fields"]["variant_ffperase_prefix"], str(runtime))
        self.assertIn("For resources elsewhere", " ".join(result["notes"]))

    def test_explicit_invalid_prefix_is_preserved_without_fallback(self):
        self.tools(self.project / "envs/good", "samtools", "bcftools", "freebayes")
        self.tools(self.home, "samtools", "bcftools", "freebayes")
        bad = str(self.root / "missing")
        result = self.detect(values={"variant_tool_prefix": bad, "variant_annovar": "off"})
        self.assertEqual(result["fields"]["variant_tool_prefix"], bad)
        self.assertEqual(self.resources(result)["variant_tools"]["status"], "missing")
        self.env["ONCOTRACER_VARIANTS_PREFIX"] = bad
        self.assertEqual(self.detect()["fields"]["variant_tool_prefix"], bad)

    def test_path_is_used_without_inventing_a_prefix(self):
        self.tools(self.home, "samtools", "bcftools", "freebayes")
        result = self.detect()
        self.assertNotIn("variant_tool_prefix", result["fields"])
        self.assertEqual(self.resources(result)["variant_tools"]["status"], "found")

    def test_incomplete_environments_are_not_combined(self):
        self.tools(self.project / "envs/a", "samtools", "bcftools")
        self.tools(self.project / "envs/b", "freebayes")
        result = self.detect()
        self.assertNotIn("variant_tool_prefix", result["fields"])
        self.assertEqual(self.resources(result)["variant_tools"]["status"], "missing")

    def test_ffpe_files_are_discovered_without_reading_models(self):
        optional = self.home / ".local/share/oncotracer/optional-tools"
        source = self.ffpe_source(optional / "nf-ffperase")
        models = self.ffpe_models(optional / "ffperase-models")
        runtime = self.tools(optional / "ffperase", "python")
        self.tools(optional / "variants", "samtools", "bcftools", "freebayes", "gatk")
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("Read model bytes")), \
             patch("oncotracer_cli.ffperase.discover", side_effect=AssertionError("Heavy discovery")):
            result = self.detect(specimen_type="ffpe")
        self.assertEqual(result["fields"]["variant_ffperase_root"], str(source))
        self.assertEqual(result["fields"]["variant_ffperase_models"], str(models))
        self.assertEqual(result["fields"]["variant_ffperase_prefix"], str(runtime))
        self.assertEqual(self.resources(result)["ffperase_prefix"]["status"], "candidate")
        self.assertFalse(result["install_guides"])

    def test_fresh_or_unknown_preservation_does_not_enable_ffperase(self):
        self.ffpe_source(self.project / "tools/nf-ffperase")
        for specimen in ("fresh", ""):
            result = self.detect(specimen_type=specimen)
            self.assertEqual(self.resources(result)["ffperase"]["status"], "not_needed")
            self.assertNotIn("variant_specimen_type", result["fields"])
            self.assertNotIn("variant_ffperase_root", result["fields"])
        self.assertIn("Choose Fresh", " ".join(result["notes"]))

    def test_ffperase_sif_is_candidate_and_requires_local_runtime(self):
        sif = self.file(self.project / "tools/containers/ffperase_runtime.sif")
        result = self.detect(specimen_type="ffpe")
        self.assertEqual(result["fields"]["variant_ffperase_sif"], str(sif))
        self.assertEqual(self.resources(result)["ffperase_sif"]["status"], "candidate")
        self.assertEqual(self.resources(result)["container_runtime"]["status"], "missing")
        self.assertIn("container_runtime", [g["id"] for g in result["install_guides"]])

    def test_clairsto_sif_auto_candidate_and_native_preference(self):
        sif = self.file(self.project / "tools/containers/clairs-to_latest.sif")
        self.tools(self.home, "samtools", "bcftools", "singularity")
        result = self.detect(mode="ont", callers=["clairs_to"])
        self.assertEqual(result["fields"]["variant_clairsto_sif"], str(sif))
        self.assertEqual(self.resources(result)["container_runtime"]["status"], "found")
        self.assertNotIn("variant_clairsto_platform", result["fields"])
        self.assertEqual(self.resources(result)["clairsto_model"]["status"], "missing")
        self.tools(self.home, "run_clairs_to")
        result = self.detect(mode="ont", callers=["clairs_to"])
        self.assertNotIn("variant_clairsto_sif", result["fields"])
        self.assertEqual(self.resources(result)["clairs_to"]["status"], "found")

    def test_multiple_sifs_are_not_selected_implicitly(self):
        self.file(self.project / "tools/containers/clairs-to_a.sif")
        self.file(self.project / "tools/containers/clairs-to_b.sif")
        result = self.detect(mode="ont", callers=["clairs_to"])
        self.assertNotIn("variant_clairsto_sif", result["fields"])
        self.assertEqual(self.resources(result)["clairsto_sif"]["status"], "candidate")
        self.assertEqual(self.resources(result)["clairs_to"]["status"], "missing")
        self.assertIn("Multiple ClairS", " ".join(result["notes"]))

    def test_clair3_model_is_candidate_and_chemistry_is_never_guessed(self):
        folder = self.project / "tools/clair3/models/synthetic_chemistry"
        self.file(folder / "pileup.index")
        self.file(folder / "full_alignment.index")
        result = self.detect(mode="ont", callers="clair3")
        self.assertEqual(result["fields"]["variant_clair3_model"], str(folder))
        self.assertEqual(self.resources(result)["clair3_model"]["status"], "candidate")
        second = folder.parent / "another_chemistry"
        self.file(second / "pileup.index")
        self.file(second / "full_alignment.index")
        result = self.detect(mode="ont", callers="clair3")
        self.assertNotIn("variant_clair3_model", result["fields"])
        result = self.detect(mode="ont", callers="clair3", values={"variant_clair3_model": str(folder / "missing"), "variant_annovar": "off"})
        self.assertEqual(result["fields"]["variant_clair3_model"], str(folder / "missing"))
        self.assertEqual(self.resources(result)["clair3_model"]["status"], "missing")

    def test_docker_never_fills_host_prefixes_or_opens_an_image(self):
        prefix = self.tools(self.project / "envs/variants", "samtools", "bcftools", "freebayes", "gatk")
        ffpe = self.tools(self.project / "envs/ffperase", "python")
        source = self.ffpe_source(self.project / "tools/nf-ffperase")
        self.ffpe_models(self.project / "resources/ffperase-models")
        self.file(self.bin / "docker", "#!/bin/sh\nexit 99\n", executable=True)
        with patch("subprocess.run", side_effect=AssertionError("Container executed")):
            result = self.detect(backend="docker", docker_image="synthetic/image:test", specimen_type="ffpe",
                                 values={"variant_tool_prefix": str(prefix), "variant_ffperase_prefix": str(ffpe),
                                         "variant_clairsto_sif": "/synthetic/host.sif", "variant_annovar": "off"})
        for field in ("variant_tool_prefix", "variant_ffperase_prefix", "variant_clairsto_sif"):
            self.assertNotIn(field, result["fields"])
        self.assertEqual(result["fields"]["variant_ffperase_root"], str(source))
        self.assertEqual(self.resources(result)["samtools"]["status"], "unverified")
        self.assertEqual(self.resources(result)["docker_image"]["status"], "unverified")
        self.assertNotIn("docker_image", [g["id"] for g in result["install_guides"]])

    def test_docker_missing_executable_has_explicit_manual_guide(self):
        result = self.detect(backend="docker", docker_image="synthetic/image:test")
        self.assertEqual(self.resources(result)["docker_runtime"]["status"], "missing")
        self.assertIn("docker_image", [g["id"] for g in result["install_guides"]])

    def test_annovar_matches_build_and_honors_invalid_override(self):
        install = self.annovar(self.project / "tools/annovar", build="hg19")
        result = self.detect(values={"variant_reference_build": "hg38"})
        self.assertEqual(self.resources(result)["annovar"]["status"], "missing")
        result = self.detect(values={"variant_reference_build": "hg19"})
        self.assertEqual(result["fields"]["variant_annovar_dir"], str(install))
        self.assertEqual(self.resources(result)["annovar"]["status"], "found")
        bad = str(self.root / "missing")
        result = self.detect(values={"variant_reference_build": "hg19", "variant_annovar_db": bad})
        self.assertEqual(result["fields"]["variant_annovar_db"], bad)
        self.assertEqual(self.resources(result)["annovar"]["status"], "missing")

    def test_annovar_uses_bounded_protocols_and_removed_environment(self):
        install = self.annovar(self.home / "annovar")
        self.file(install / "humandb/hg38_clinvar_20260101.txt")
        self.file(install / "humandb/hg38_clinvar_20240101.txt")
        self.env["ANNOVAR_HOME"] = None
        with patch.dict(os.environ, {"ANNOVAR_HOME": "/missing/ambient/annovar"}), \
             patch("pathlib.Path.glob", side_effect=AssertionError("Unbounded glob")):
            result = self.detect(values={})
        self.assertEqual(self.resources(result)["annovar"]["status"], "found")
        self.assertIn("clinvar_20260101", self.resources(result)["annovar"]["detail"])

    def test_annovar_large_helper_is_not_hashed(self):
        install = self.annovar(self.home / "annovar")
        with (install / "table_annovar.pl").open("ab") as handle:
            handle.truncate(2 * 1024 * 1024)
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("Read huge helper")):
            result = self.detect(values={})
        self.assertEqual(self.resources(result)["annovar"]["status"], "unverified")

    def test_docker_annovar_does_not_require_host_perl(self):
        install = self.annovar(self.home / "annovar")
        (self.bin / "perl").unlink()
        result = self.detect(backend="docker", docker_image="synthetic/image:test", values={})
        self.assertEqual(result["fields"]["variant_annovar_dir"], str(install))
        self.assertEqual(self.resources(result)["annovar"]["status"], "candidate")

    def test_search_is_shallow_and_caps_large_directories(self):
        self.tools(self.project / "deep/unrelated/envs/hidden", "samtools", "bcftools", "freebayes")
        folder = self.project / "resources"
        for number in range(MAX_CHILDREN + 20):
            self.file(folder / f"unrelated_{number:03}.txt")
        with patch("pathlib.Path.rglob", side_effect=AssertionError("Recursive scan")), \
             patch("os.walk", side_effect=AssertionError("Whole-tree scan")):
            result = self.detect()
        self.assertNotIn("variant_tool_prefix", result["fields"])
        self.assertLessEqual(len(result["searched"]), MAX_SEARCHED)
        self.assertTrue(any("first 64" in n for n in result["notes"]))
        self.assertFalse(any("deep/unrelated" in p for p in result["searched"]))

    def test_singularity_backend_explains_host_discovery(self):
        result = self.detect(backend="singularity")
        self.assertEqual(result["backend"], "singularity")
        self.assertIn("uses host tools", " ".join(result["notes"]))

    def test_resource_rows_identify_corresponding_form_fields(self):
        result = self.detect(specimen_type="ffpe", values={"variant_targets_bed": "/synthetic/missing.bed"})
        rows = self.resources(result)
        expected = {"samtools": "variant_tool_prefix", "variant_tools": "variant_tool_prefix",
                    "ffperase_root": "variant_ffperase_root", "ffperase_models": "variant_ffperase_models",
                    "ffperase_prefix": "variant_ffperase_prefix", "ffperase_sif": "variant_ffperase_sif",
                    "annovar": "variant_annovar", "annovar_dir": "variant_annovar_dir",
                    "annovar_db": "variant_annovar_db", "targets_bed": "variant_targets_bed"}
        for identity, field in expected.items():
            self.assertEqual(rows[identity]["field"], field, identity)
        self.assertTrue(all("field" in r for r in result["resources"]))

    def test_multiple_complete_environments_require_choice(self):
        prefixes = [self.tools(self.project / f"envs/{name}", "samtools", "bcftools", "freebayes") for name in ("first", "second")]
        result = self.detect()
        choices = [c for c in result["candidates"] if c["field"] == "variant_tool_prefix"]
        self.assertEqual({c["path"] for c in choices}, set(map(str, prefixes)))
        self.assertTrue(all(c["status"] == "candidate" and c["detail"] for c in choices))
        self.assertNotIn("variant_tool_prefix", result["fields"])
        self.assertEqual(self.resources(result)["variant_tools"]["status"], "candidate")
        self.assertNotIn("variant_tools", [g["id"] for g in result["install_guides"]])
        result = self.detect(values={"variant_tool_prefix": str(prefixes[1]), "variant_annovar": "off"})
        self.assertEqual(result["fields"]["variant_tool_prefix"], str(prefixes[1]))
        self.assertFalse([c for c in result["candidates"] if c["field"] == "variant_tool_prefix"])

    def test_clair3_conda_bin_models_and_multiple_model_choices(self):
        prefix = self.tools(self.project / "tools/ont-tools", "samtools", "bcftools", "run_clair3.sh")
        folders = [prefix / "bin/models" / name for name in ("synthetic_a", "synthetic_b")]
        for folder in folders:
            self.file(folder / "pileup.index")
            self.file(folder / "full_alignment.index")
        result = self.detect(mode="ont", callers=["clair3"])
        choices = [c for c in result["candidates"] if c["field"] == "variant_clair3_model"]
        self.assertEqual({c["path"] for c in choices}, set(map(str, folders)))
        self.assertNotIn("variant_clair3_model", result["fields"])
        self.assertTrue(all("chemistry" in c["detail"] for c in choices))

    def test_ffperase_native_and_sif_alternatives_are_both_visible(self):
        prefix = self.tools(self.project / "tools/oncotracer-ffperase-env", "python")
        sif = self.file(self.project / "tools/containers/ffperase.sif")
        result = self.detect(specimen_type="ffpe")
        self.assertEqual(result["fields"]["variant_ffperase_prefix"], str(prefix))
        self.assertNotIn("variant_ffperase_sif", result["fields"])
        self.assertIn(str(sif), [c["path"] for c in result["candidates"] if c["field"] == "variant_ffperase_sif"])
        result = self.detect(specimen_type="ffpe", values={"variant_ffperase_sif": str(sif), "variant_annovar": "off"})
        self.assertEqual(result["fields"]["variant_ffperase_sif"], str(sif))
        self.assertNotIn("variant_ffperase_prefix", result["fields"])
        self.assertIn(str(prefix), [c["path"] for c in result["candidates"] if c["field"] == "variant_ffperase_prefix"])

    def test_annovar_install_can_be_found_without_databases(self):
        install = self.annovar(self.project / "tools/annovar")
        for file in (install / "humandb").iterdir():
            file.unlink()
        result = self.detect(values={})
        rows = self.resources(result)
        self.assertEqual(result["fields"]["variant_annovar_dir"], str(install))
        self.assertNotIn("variant_annovar_db", result["fields"])
        self.assertEqual(rows["annovar_dir"]["status"], "found")
        self.assertEqual(rows["annovar_db"]["status"], "missing")
        self.assertEqual(rows["annovar"]["status"], "missing")

    def test_annovar_databases_can_be_found_independently(self):
        database = self.project / "resources/humandb"
        self.file(database / "hg38_refGene.txt")
        self.file(database / "hg38_refGeneMrna.fa")
        result = self.detect(values={})
        self.assertNotIn("variant_annovar_dir", result["fields"])
        self.assertEqual(result["fields"]["variant_annovar_db"], str(database))
        self.assertEqual(self.resources(result)["annovar_db"]["status"], "found")
        self.assertEqual(self.resources(result)["annovar"]["status"], "missing")
        install = self.annovar(self.project / "tools/annovar", build="hg19")
        result = self.detect(values={})
        self.assertEqual(result["fields"]["variant_annovar_dir"], str(install))
        self.assertEqual(result["fields"]["variant_annovar_db"], str(database))
        self.assertEqual(self.resources(result)["annovar"]["status"], "found")

    def test_multiple_annovar_installations_and_databases_offer_separate_choices(self):
        installs = [self.annovar(p) for p in (self.home / "annovar", self.project / "tools/annovar")]
        result = self.detect(values={})
        for field in ("variant_annovar_dir", "variant_annovar_db"):
            self.assertNotIn(field, result["fields"])
        choices = result["candidates"]
        self.assertEqual({c["path"] for c in choices if c["field"] == "variant_annovar_dir"}, set(map(str, installs)))
        self.assertEqual({c["path"] for c in choices if c["field"] == "variant_annovar_db"}, {str(p / "humandb") for p in installs})
        self.assertEqual(self.resources(result)["annovar"]["status"], "candidate")
        self.assertTrue(all("Matching hg38 database pair" in c["detail"] for c in choices if c["field"] == "variant_annovar_dir"))
        result = self.detect(values={"variant_annovar_dir": str(installs[0]), "variant_annovar_db": str(installs[1] / "humandb")})
        self.assertEqual(self.resources(result)["annovar"]["status"], "found")
        self.assertFalse([c for c in result["candidates"] if c["field"].startswith("variant_annovar_")])

    def test_explicit_invalid_annovar_install_does_not_offer_replacement(self):
        self.annovar(self.home / "annovar")
        bad = str(self.root / "missing")
        result = self.detect(values={"variant_annovar_dir": bad})
        self.assertEqual(result["fields"]["variant_annovar_dir"], bad)
        self.assertEqual(self.resources(result)["annovar_dir"]["status"], "missing")
        self.assertFalse([c for c in result["candidates"] if c["field"] == "variant_annovar_dir"])

    def test_clinical_files_are_not_guessed_from_names(self):
        self.file(self.project / "resources/targets.bed")
        self.file(self.project / "resources/scenario.yaml")
        self.file(self.project / "resources/genome.fa")
        result = self.detect()
        for field in ("variant_targets_bed", "variant_varlociraptor_scenario", "variant_reference"):
            self.assertNotIn(field, result["fields"])
            self.assertFalse([c for c in result["candidates"] if c["field"] == field])
        self.assertIn("Clinical target BEDs", " ".join(result["notes"]))

    def test_invalid_payloads_fail_predictably(self):
        for changes in ({"mode": "unknown"}, {"backend": "shell"}, {"callers": ["clair3"]},
                        {"callers": ["freebayes", "freebayes"]}, {"specimen_type": "guess"},
                        {"values": []}, {"values": {"variant_tool_prefix": []}},
                        {"values": {"unsupported": "x"}}, {"docker_image": "bad\nimage"}):
            with self.subTest(changes=changes), self.assertRaises(OncoTracerError):
                self.detect(**changes)
        with self.assertRaises(OncoTracerError):
            discover_variant_resources([], environment=self.env)
        with self.assertRaises(OncoTracerError):
            discover_variant_resources(self.default, roots="/", environment=self.env)
        with self.assertRaises(OncoTracerError):
            discover_variant_resources(self.default, roots=(12,), environment=self.env)


if __name__ == "__main__":
    unittest.main()
