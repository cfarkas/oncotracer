"""First-run UX contracts, using disposable files and fake tools."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import cli, reporting
from oncotracer_cli.runtime import load_flat_yaml


class FirstRunExperienceTests(unittest.TestCase):
    def test_setup_run_infers_platform_saves_config_and_installs_missing_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reads = root / "reads.fastq"
            reads.write_text("@read\nACGT\n+\nIIII\n")
            project = root / "project"
            with (patch.object(cli, "_load_install_config", return_value={}),
                  patch.object(cli, "command_install", return_value=0) as install,
                  patch.object(cli, "command_run", return_value=0) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                code = cli.main(["setup", "--project", str(project), "--non-interactive",
                                 "--sample-name", "sample", "--fastq-1", str(reads), "--run"])
            self.assertEqual(code, 0)
            self.assertTrue(install.call_args.args[0].conda)
            config = load_flat_yaml(project / "config/run.yml")
            self.assertEqual(config["mode"], "illumina")
            self.assertEqual(config["reference_download_cache"], str(root / ".oncotracer-reference-downloads"))
            self.assertEqual(run.call_args.args[0].config, str(project / "config/run.yml"))
            self.assertFalse((project / "reference").exists())

    def test_setup_run_resumes_saved_config_and_reuses_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            reads = root / "reads.fastq"
            reads.write_text("@read\nACGT\n+\nIIII\n")
            install = {name + "_prefix": str(root / name) for name in
                       ("core", "qdnaseq", "ichorcna", "classifier", "gistic")}
            install["backend"] = "conda"
            for value in install.values():
                if value != "conda":
                    Path(value).mkdir()
            with (patch.object(cli, "_load_install_config", return_value=install),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(cli.main(["setup", "--project", str(project), "--non-interactive",
                                           "--sample-name", "sample", "--fastq-1", str(reads),
                                           "--threads", "40"]), 0)
                path = project / "config/run.yml"
                original = path.read_bytes()
                with (patch.object(cli, "command_install") as installer,
                      patch.object(cli, "command_run", return_value=0) as run):
                    self.assertEqual(cli.main(["setup", "--project", str(project), "--run"]), 0)
                    installer.assert_not_called()
                    self.assertIsNone(run.call_args.args[0].threads)
                    self.assertEqual(cli.main(["setup", "--project", str(project), "--run", "--threads", "4"]), 0)
                    self.assertEqual(run.call_args.args[0].threads, 4)
                self.assertEqual(path.read_bytes(), original)

    def test_plain_progress_hides_raw_output_but_keeps_it_in_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            log = Path(temporary) / "install.log"
            with contextlib.redirect_stderr(output):
                with reporting.operation("Preparing tools", log_file=log):
                    reporting.status("Installing core", completed=1, total=5)
                    reporting.detail("RAW PACKAGE DIAGNOSTICS")
            self.assertNotIn("RAW PACKAGE", output.getvalue())
            self.assertIn("RAW PACKAGE DIAGNOSTICS", log.read_text())
            self.assertIn("elapsed", output.getvalue())
            self.assertIn("1/5", output.getvalue())
            self.assertNotIn("\033", output.getvalue())

    def test_terminal_progress_colors_and_no_color(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        for no_color in (False, True):
            terminal = Terminal()
            with (patch.dict(os.environ, {"TERM": "xterm"}, clear=True),
                  contextlib.redirect_stderr(terminal)):
                if no_color:
                    os.environ["NO_COLOR"] = "1"
                with reporting.operation("Preparing tools") as progress:
                    progress.status("Installing core", completed=2, total=5)
            output = terminal.getvalue()
            self.assertIn("[=======           ]", output)
            self.assertEqual("\033[32mOK" in output, not no_color)

    def test_failure_points_to_log_and_never_reports_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            log = Path(temporary) / "failed.log"
            with contextlib.redirect_stderr(output):
                with self.assertRaisesRegex(RuntimeError, "probe failed"):
                    with reporting.operation("Preparing tools", log_file=log):
                        raise RuntimeError("probe failed")
            self.assertIn("FAILED", output.getvalue())
            self.assertNotIn("OK  ", output.getvalue())
            self.assertIn(str(log), output.getvalue())
            self.assertIn("probe failed", log.read_text())

    def test_doctor_defaults_to_human_summary_and_keeps_json_opt_in(self):
        provenance = {"source_commit": "a" * 40, "source_sha256": "b" * 64, "source_tree_dirty": False}
        with (patch.object(cli, "_load_install_config", return_value={}),
              patch.object(cli, "get_provenance", return_value=provenance),
              patch.object(cli, "_configured_native_prefixes", return_value={}),
              patch.object(cli, "_probe_core", return_value={"success": True, "probes": {}})):
            for args in (["doctor", "--backend", "host"], ["doctor", "--backend", "host", "--json"]):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(args), 0)
                if "--json" in args:
                    self.assertTrue(json.loads(output.getvalue())["success"])
                else:
                    self.assertIn("Ready to run.", output.getvalue())
                    self.assertNotIn("output_excerpt", output.getvalue())
