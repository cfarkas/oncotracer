"""Optional hg38 paths and safe automatic imports for ordinary analysis runs."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import cli, reference_bundle as bundle
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml, sha256_file
from tests import test_reference_bundle


class Hg38SetupTests(unittest.TestCase):
    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = cli.main(list(args))
        return result, output.getvalue()

    def setup(self, base, *, mode="illumina", flags=()):
        reads = base / "input/fastq_pass/barcode01/reads.fastq"
        reads.parent.mkdir(parents=True, exist_ok=True)
        reads.write_text("@read\nACGT\n+\nIIII\n")
        project = base / "project"
        args = ["setup", "--non-interactive", "--project", str(project), "--mode", mode]
        if mode == "illumina":
            args += ["--sample-name", "sample1", "--fastq-1", str(reads)]
        else:
            args += ["--reads-folder", str(reads.parent.parent), "--barcodes", "barcode01", "--sample-names", "sample1"]
        code, output = self.command(*args, *flags)
        return project / "config/run.yml", code, output

    def fake_build(self, root, *, owned=False, omit=()):
        target = root / (".oncotracer/reference-cache/samurai-hg38" if owned else "references/samurai_hg38")
        for name in bundle._files():
            if name in omit:
                continue
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture for path validation only")
        return target

    def test_bare_and_omitted_flag_defer_download_until_run(self):
        for mode in ("illumina", "ont"):
            for flags in ((), ("--hg38_build",)):
                with self.subTest(mode=mode, flags=flags), tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    with patch.object(bundle, "urlopen", side_effect=AssertionError("setup/check/dry-run must not connect")):
                        path, code, output = self.setup(base, mode=mode, flags=flags)
                        self.assertEqual(code, 0, output)
                        config = load_flat_yaml(path)
                        self.assertTrue(config["hg38_auto_download"])
                        self.assertEqual(config["lpwgs_root"], str(base / "project/reference"))
                        for args in (("check", "--config", str(path), "--json"), ("run", "--backend", "conda", "--config", str(path), "--dry-run")):
                            code, output = self.command(*args)
                            self.assertEqual(code, 0, output)
                    self.assertFalse((base / "project/reference").exists())
                    self.assertFalse((base / "project/results").exists())

    def test_supplied_parent_or_build_folder_reuses_each_platform(self):
        for mode in ("illumina", "ont"):
            for owned in (False, True):
                for direct in (False, True):
                    with self.subTest(mode=mode, owned=owned, direct=direct), tempfile.TemporaryDirectory() as temporary:
                        base = Path(temporary)
                        parent = base / "shared hg38"
                        build = self.fake_build(parent, owned=owned)
                        before = {p.relative_to(parent): p.read_bytes() for p in parent.rglob("*") if p.is_file()}
                        path, code, output = self.setup(base, mode=mode, flags=("--hg38_build", str(build if direct else parent)))
                        self.assertEqual(code, 0, output)
                        config = load_flat_yaml(path)
                        self.assertEqual(config["lpwgs_root"], str(parent))
                        self.assertFalse(config["hg38_auto_download"])
                        self.assertEqual(before, {p.relative_to(parent): p.read_bytes() for p in parent.rglob("*") if p.is_file()})

    def test_wrong_or_incomplete_path_fails_before_writing_config(self):
        for kind in ("missing", "empty", "fasta", "wrong-platform"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                source = base / "hg38"
                if kind == "empty":
                    source.mkdir()
                elif kind == "fasta":
                    source.write_text(">chr1\nACGT\n")
                elif kind == "wrong-platform":
                    self.fake_build(source, omit=("genome.fa.map-ont.mmi",))
                path, code, output = self.setup(base, mode="ont", flags=("--hg38_build", str(source)))
                self.assertEqual(code, 2, output)
                self.assertIn("--hg38_build", output)
                self.assertFalse(path.exists())

    def test_reference_spellings_cannot_silently_override_each_other(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["setup", "--hg38_build", "/one", "--reference-root", "/two"])

    def test_automatic_stream_import_precedes_all_backends_and_is_reused(self):
        for backend in ("host", "conda", "poetry", "docker", "singularity"):
            for mode in ("illumina", "ont"):
                with self.subTest(backend=backend, mode=mode), tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    path, code, output = self.setup(base, mode=mode)
                    self.assertEqual(code, 0, output)
                    manifest, value, hashes = test_reference_bundle.ReferenceBundleTests().fixture(base)
                    original = path.read_bytes()
                    events = []
                    args = cli.build_parser().parse_args(["run", "--backend", backend, "--config", str(path)])
                    installation = {name + "_prefix": str(base / name) for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic")}
                    sif = base / "fixture.sif"
                    sif.write_bytes(b"fixture used only with the mocked container runner")
                    installation["sif"] = str(sif)
                    def host(config_path, options):
                        events.append("preflight" if options.dry_run else "run")
                        return base / "project/results"
                    actual_import = bundle.install_bundle
                    def transfer(*args, **kwargs):
                        events.append("download")
                        return actual_import(*args, **kwargs)
                    with (
                        patch.dict(bundle.engine.HG38_ASSETS, hashes, clear=True),
                        patch.object(bundle, "DEFAULT_MANIFEST_URL", str(manifest)),
                        patch.object(bundle, "DEFAULT_MANIFEST_SHA256", sha256_file(manifest)),
                        patch.object(bundle, "_verify_imported_indexes"),
                        patch.object(bundle, "install_bundle", side_effect=transfer) as imported,
                        patch.object(cli, "_load_install_config", return_value=installation),
                        patch.object(cli, "_run_host", side_effect=host),
                        patch.object(cli, "_run_docker", side_effect=lambda *_: events.append("run")),
                        patch.object(cli, "_run_singularity", side_effect=lambda *_: events.append("run")),
                        patch.object(cli, "require_command", return_value="docker"),
                        patch.object(cli, "_singularity_command", return_value="apptainer"),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        cli.execute_run(path, args)
                        self.assertEqual(events, ["preflight", "download", "run"])
                        self.assertEqual(imported.call_args.kwargs["mode"], mode)
                        cli.execute_run(path, args)
                        self.assertEqual(imported.call_count, 1)
                        self.assertEqual(events[-1], "run")
                    self.assertEqual(path.read_bytes(), original)
                    reference = base / "project/reference/references/samurai_hg38"
                    self.assertTrue((reference / "genome.fa").is_file())
                    self.assertTrue((reference / ".oncotracer/reference-bundle.json").is_file())

    def test_unavailable_backends_fail_before_downloading(self):
        for backend, installation in (
            ("docker", {}),
            ("conda", {}),
            ("poetry", {}),
            ("singularity", {}),
            ("singularity", {"singularity_command": "/custom/apptainer"}),
        ):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temporary:
                path, code, output = self.setup(Path(temporary))
                self.assertEqual(code, 0, output)
                args = cli.build_parser().parse_args([
                    "run", "--backend", backend, "--config", str(path),
                ])
                with (
                    patch.object(cli, "_load_install_config", return_value=installation),
                    patch.object(cli, "require_command", side_effect=OncoTracerError("missing docker")),
                    patch.object(cli, "_singularity_command", return_value=None),
                    patch.object(bundle, "install_bundle") as transfer,
                    patch.object(cli, "_run_host") as host,
                    self.assertRaises(OncoTracerError),
                ):
                    cli.execute_run(path, args)
                transfer.assert_not_called()
                host.assert_not_called()

    def test_failed_download_stops_before_analysis_without_index_build_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, code, output = self.setup(Path(temporary))
            args = cli.build_parser().parse_args(["run", "--backend", "host", "--config", str(path)])
            with patch.object(cli, "_run_host") as host, patch.object(bundle, "install_bundle", side_effect=OncoTracerError("SHA-256 mismatch")):
                with self.assertRaisesRegex(OncoTracerError, "SHA-256 mismatch"):
                    cli.execute_run(path, args)
            self.assertEqual(host.call_count, 1)
            self.assertTrue(host.call_args.args[1].dry_run)
            self.assertFalse((path.parent.parent / "results").exists())

    def test_invalid_inputs_and_foreign_outputs_prevent_download(self):
        for failure in ("missing-reads", "foreign-output"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                path, code, output = self.setup(base)
                if failure == "missing-reads":
                    (base / "input/fastq_pass/barcode01/reads.fastq").unlink()
                else:
                    output = base / "project/results"
                    output.mkdir()
                    (output / "foreign-data").write_bytes(b"keep")
                args = cli.build_parser().parse_args(["run", "--backend", "host", "--config", str(path)])
                with patch.object(bundle, "install_bundle", side_effect=AssertionError("must not download")) as transfer:
                    with self.assertRaises(OncoTracerError):
                        cli.execute_run(path, args)
                transfer.assert_not_called()
                self.assertFalse((base / "project/reference").exists())

    def test_default_manifest_pin_matches_the_published_documentation(self):
        docs = (Path(__file__).resolve().parents[1] / "docs/reference_indexes.md").read_text()
        self.assertIn(bundle.DEFAULT_MANIFEST_URL, docs)
        self.assertIn(bundle.DEFAULT_MANIFEST_SHA256, docs)

    def test_check_and_run_reject_non_boolean_download_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, code, output = self.setup(Path(temporary))
            config = load_flat_yaml(path)
            config["hg38_auto_download"] = "maybe"
            path.write_text(render_flat_yaml(config))
            with patch.object(bundle, "urlopen", side_effect=AssertionError("must not connect")):
                code, output = self.command("check", "--config", str(path), "--json")
                self.assertEqual(code, 2, output)
                self.assertIn("hg38_auto_download", " ".join(json.loads(output)["errors"]))
                code, output = self.command("run", "--backend", "host", "--config", str(path), "--dry-run")
                self.assertEqual(code, 2, output)
                self.assertIn("hg38_auto_download must be true or false", output)


if __name__ == "__main__":
    unittest.main()
