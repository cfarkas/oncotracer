"""Local-only tests for optional annotation discovery and output protection."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.annovar import build_annovar_command, discover_annovar, expected_outputs
from oncotracer_cli.runtime import OncoTracerError


class AnnovarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oncotracer-annovar-")
        self.root = Path(self.tmp.name)
        self.install = self.root / "annovar"
        self.install.mkdir()
        self.db = self.install / "humandb"
        self.db.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.perl = self.bin / "perl"
        self.perl.write_text("#!/bin/sh\nexit 0\n")
        self.perl.chmod(0o755)
        for name in ("table_annovar.pl", "annotate_variation.pl", "convert2annovar.pl", "coding_change.pl"):
            p = self.install / name
            p.write_text("#!/usr/bin/env perl\n# $Revision: fixture $\n")
            p.chmod(0o755)
        self.env = {"PATH": str(self.bin), "HOME": str(self.root),
                    "ANNOVAR_HOME": None, "ANNOVAR_DIR": None, "ANNOVAR_DB": None,
                    "ANNOVAR_DATABASE_DIR": None}

    def tearDown(self):
        self.tmp.cleanup()

    def gene(self, build="hg38", protocol="refGene"):
        (self.db / f"{build}_{protocol}.txt").write_text("gene\n")
        (self.db / f"{build}_{protocol}Mrna.fa").write_text(">transcript\nACGT\n")

    def detect(self, **kwargs):
        return discover_annovar(environment=self.env, **kwargs)

    def test_optional_missing_installation_does_not_create_or_run_anything(self):
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.run") as run:
            result = discover_annovar(environment={"PATH": str(self.bin), "HOME": str(self.root / "missing")})
        self.assertFalse(result.available)
        self.assertIn("installation", result.reason)
        run.assert_not_called()
        self.assertFalse((self.root / "missing").exists())

    def test_matching_gene_and_latest_clinvar_with_json_provenance(self):
        self.gene()
        for name in ("hg38_clinvar_20240101.txt", "hg38_clinvar_20250101.txt",
                     "hg38_clinvar_20260101.txt.gz", "hg19_clinvar_20270101.txt"):
            (self.db / name).write_text("database\n")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.install.rglob("*") if p.is_file()}
        result = self.detect()
        self.assertTrue(result.available, result.reason)
        self.assertEqual(result.protocols, ("refGene", "clinvar_20250101"))
        self.assertEqual(result.operations, ("g", "f"))
        self.assertEqual(result.version, "fixture")
        json.dumps(result.as_dict())
        self.assertTrue(all("sha256" in p for p in result.file_provenance))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.install.rglob("*") if p.is_file()})

    def test_no_cross_build_fallback_and_complete_gene_required(self):
        self.gene("hg19")
        (self.db / "hg38_refGene.txt").write_text("incomplete gene\n")
        self.assertFalse(self.detect().available)
        self.assertTrue(self.detect(build="hg19").available)
        self.assertFalse(self.detect(build="GRCh38").available)
        self.assertFalse(self.detect(build="hg38;bad").available)

    def test_explicit_missing_install_or_database_never_falls_back(self):
        self.gene()
        self.assertTrue(self.detect().available)
        for kwargs in ({"annovar_dir": self.root / "missing"}, {"database_dir": self.root / "missing"}):
            result = self.detect(**kwargs)
            self.assertFalse(result.available)
            self.assertIn("missing", result.reason)
        env = dict(self.env, ANNOVAR_HOME=str(self.root / "missing"))
        self.assertFalse(discover_annovar(environment=env).available)

    def test_project_tools_and_path_candidates(self):
        self.gene()
        env = dict(self.env, HOME=str(self.root / "other"))
        self.assertTrue(discover_annovar(environment=env, search_roots=[self.root]).available)
        env["PATH"] = str(self.install) + os.pathsep + str(self.bin)
        self.assertTrue(discover_annovar(environment=env).available)

    def test_missing_perl_or_helper_skips_annotation(self):
        self.gene()
        self.assertFalse(discover_annovar(environment=dict(self.env, PATH="")).available)
        (self.install / "coding_change.pl").unlink()
        result = self.detect()
        self.assertFalse(result.available)
        self.assertIn("Incomplete", result.reason)

    def test_requested_protocols_are_exact_and_never_omitted_silently(self):
        self.gene()
        result = self.detect(protocols=["refGene", "clinvar_20250101"])
        self.assertFalse(result.available)
        self.assertIn("clinvar_20250101", result.reason)
        self.assertTrue(self.detect(protocols=["refGene"]).available)
        for protocols in (["../bad"], ["refGene", "refGene"], [], "refGene"):
            self.assertFalse(self.detect(protocols=protocols).available)

    def test_compressed_assets_and_zero_byte_files_are_not_usable(self):
        (self.db / "hg38_refGene.txt.gz").write_bytes(b"compressed")
        (self.db / "hg38_refGeneMrna.fa").write_bytes(b"")
        self.assertFalse(self.detect().available)
        self.assertFalse((self.db / "hg38_refGene.txt").exists())

    def test_large_database_stat_is_not_misrepresented_as_content_hash(self):
        self.gene()
        with (self.db / "hg38_refGeneMrna.fa").open("wb") as handle:
            handle.truncate(1024 * 1024 + 1)
        result = self.detect()
        self.assertTrue(result.available)
        provenance = next(p for p in result.file_provenance if str(p["path"]).endswith("Mrna.fa"))
        self.assertNotIn("sha256", provenance)
        self.assertEqual(provenance["identity_method"], "stat_only_not_content_verified")

    def test_command_and_expected_artifacts_preserve_vcf_mode(self):
        self.gene()
        result = self.detect()
        prefix = self.root / "new_output" / "sample"
        argv = build_annovar_command(result, self.root / "input.vcf.gz", prefix)
        self.assertEqual(argv[:2], [str(self.perl), str(self.install / "table_annovar.pl")])
        self.assertIn("-vcfinput", argv)
        self.assertEqual(argv[argv.index("-buildver") + 1], "hg38")
        self.assertEqual(argv[argv.index("-operation") + 1], "g")
        self.assertEqual(expected_outputs(result, prefix), [Path(str(prefix) + ".hg38_multianno.txt"), Path(str(prefix) + ".hg38_multianno.vcf")])
        self.assertFalse(prefix.parent.exists())
        self.assertNotIn("-downdb", argv)

    def test_existing_outputs_databases_and_unsafe_paths_are_protected(self):
        self.gene()
        result = self.detect()
        prefix = self.root / "output"
        old = Path(str(prefix) + ".avinput")
        old.write_text("existing work\n")
        with self.assertRaises(OncoTracerError):
            build_annovar_command(result, self.root / "input.vcf", prefix)
        self.assertEqual(old.read_text(), "existing work\n")
        for bad_prefix in (self.db / "output", self.install / "output", self.root / "bad path", self.root / "bad;touch_x"):
            with self.assertRaises(OncoTracerError):
                build_annovar_command(result, self.root / "input.vcf", bad_prefix)
        with self.assertRaises(OncoTracerError):
            build_annovar_command(result, self.root / "$(bad).vcf", self.root / "safe")


if __name__ == "__main__":
    unittest.main()
