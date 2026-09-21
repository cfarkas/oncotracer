"""Paper reports dispatch independently of native analysis and its installers."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oncotracer_cli.cli import _legacy_to_modern, build_parser, main
from oncotracer_cli.runtime import OncoTracerError


class PaperReportCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "paper.json"
        self.manifest.write_text('{"schema":"test"}\n')
        self.output = self.root / "figures"
        self.renderer = Mock(return_value=self.output / "index.html")
        module = types.ModuleType("oncotracer_cli.paper_report")
        module.generate_paper_report = self.renderer
        modules = patch.dict(sys.modules, {"oncotracer_cli.paper_report": module})
        modules.start()
        self.addCleanup(modules.stop)

    def test_command_and_requested_flag_aliases_render_without_analysis(self):
        original = self.manifest.read_bytes()
        for command in ("paper-report", "--paper_report", "--paper-report"):
            with self.subTest(command=command), patch("oncotracer_cli.cli.run_native") as analysis, patch("oncotracer_cli.cli._load_install_config") as install, contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.renderer.reset_mock()
                self.assertEqual(main([command, "--manifest", str(self.manifest),
                                       "--outdir", str(self.output)]), 0)
                self.renderer.assert_called_once_with(self.manifest, self.output)
                self.assertIn(str(self.output / "index.html"), stdout.getvalue())
                analysis.assert_not_called()
                install.assert_not_called()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_manifest_option_aliases(self):
        for option in ("--manifest", "--paper-manifest", "--paper_manifest"):
            args = build_parser().parse_args(["paper-report", option, str(self.manifest),
                                               "--outdir", str(self.output)])
            self.assertEqual(args.manifest, str(self.manifest))

    def test_missing_manifest_fails_before_renderer_or_output_creation(self):
        with contextlib.redirect_stderr(io.StringIO()):
            status = main(["--paper_report", "--manifest", str(self.root / "missing.json"),
                           "--outdir", str(self.output)])
        self.assertEqual(status, 2)
        self.renderer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_renderer_error_is_reported_as_cli_error(self):
        self.renderer.side_effect = OncoTracerError("manifest contains incompatible evidence")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            status = main(["paper-report", "--manifest", str(self.manifest),
                           "--outdir", str(self.output)])
        self.assertEqual(status, 2)
        self.assertIn("manifest contains incompatible evidence", stderr.getvalue())

    def test_existing_run_syntax_and_defaults_are_unchanged(self):
        values = ["run", "--config", "run.yml", "--backend", "conda"]
        self.assertEqual(_legacy_to_modern(values.copy()), values)
        args = build_parser().parse_args(values)
        self.assertEqual(args.command, "run")
        self.assertEqual(args.backend, "conda")
        self.assertFalse(hasattr(args, "manifest"))
        self.renderer.assert_not_called()

    def test_help_does_not_import_optional_renderer(self):
        with patch.dict(sys.modules, {"oncotracer_cli.paper_report": None}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_status:
                main(["--paper_report", "--help"])
        self.assertEqual(exit_status.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
