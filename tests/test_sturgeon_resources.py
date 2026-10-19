import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.sturgeon_resources import prepare_sturgeon, validate_model


class SturgeonResourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "models"

    def download(self, url, destination):
        if destination.suffix == ".zip":
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("model.onnx", b"synthetic-model-fixture")
                archive.writestr("decoding.json", '{"0":"synthetic"}')
                archive.writestr("probes.csv", "ID_REF\ncg1\n")
        else:
            destination.write_text("chr start end ID_REF\n1 10 11 cg1\n")
        return destination

    def test_no_acknowledgment_never_writes_or_downloads(self):
        with patch("oncotracer_cli.sturgeon_resources.download") as download:
            for value in (False, None, "true", 1):
                with self.assertRaisesRegex(OncoTracerError, "acknowledge"):
                    prepare_sturgeon(acknowledged=value, cache=self.cache)
            download.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_download_records_hashes_and_reuses_only_verified_files(self):
        with patch("oncotracer_cli.sturgeon_resources.download", side_effect=self.download) as download:
            result = prepare_sturgeon(acknowledged=True, cache=self.cache)
            self.assertEqual(download.call_count, 3)
            again = prepare_sturgeon(acknowledged=True, cache=self.cache)
            self.assertEqual(download.call_count, 3)
            self.assertEqual(result, again)
        receipt = json.loads((self.cache / "resources.json").read_text())
        self.assertTrue(receipt["license_acknowledged"])
        self.assertEqual(len(result["sturgeon_model_sha256"]), 64)
        (self.cache / "general.zip").write_bytes(b"corrupt")
        with patch("oncotracer_cli.sturgeon_resources.download") as download:
            with self.assertRaisesRegex(OncoTracerError, "changed"):
                prepare_sturgeon(acknowledged=True, cache=self.cache)
            download.assert_not_called()

    def test_html_or_incomplete_zip_rejected_without_receipt(self):
        def bad(url, destination):
            destination.write_text("<html>Unavailable</html>")
            return destination
        with patch("oncotracer_cli.sturgeon_resources.download", side_effect=bad):
            with self.assertRaisesRegex(OncoTracerError, "Invalid Sturgeon model"):
                prepare_sturgeon(acknowledged=True, cache=self.cache)
        self.assertFalse((self.cache / "resources.json").exists())
        self.assertFalse((self.cache / "general.zip").exists())

    def test_archive_must_match_upstream_contract(self):
        path = Path(self.temporary.name) / "missing.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("model.onnx", b"model")
        with self.assertRaises(OncoTracerError):
            validate_model(path)

    def test_browser_endpoint_needs_explicit_ack(self):
        from oncotracer_cli.web import WebState
        with patch("oncotracer_cli.sturgeon_resources.download") as download:
            with self.assertRaises(OncoTracerError):
                WebState(Path(self.temporary.name)).sturgeon_resources({})
            download.assert_not_called()

    def test_browser_controls_visible_in_cpg_mode(self):
        from oncotracer_cli.web_ui import PAGE
        self.assertIn('id="prepare-sturgeon"', PAGE)
        self.assertIn("$('cpg-license').append($('license').closest('label'),$('sturgeon-download'))", PAGE)
        self.assertIn('id="resume-project-button"', PAGE)
        self.assertIn("/api/resume", PAGE)


if __name__ == "__main__":
    unittest.main()
