"""Resumable downloads must keep their retry budget and pinned integrity checks."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from oncotracer_cli import runtime


class Response(io.BytesIO):
    def __init__(self, payload, *, status=200, headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}


class DownloadResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="oncotracer-download-")
        self.addCleanup(temporary.cleanup)
        self.destination = Path(temporary.name) / "reads.fastq.gz"
        self.partial = self.destination.with_name(".reads.fastq.gz.part")
        self.payload = b"fixed public input bytes"
        self.url = "https://example.invalid/reads.fastq.gz"
        self.pins = dict(expected_bytes=len(self.payload), expected_md5=hashlib.md5(self.payload).hexdigest())
        sleeping = patch.object(runtime.time, "sleep")
        self.addCleanup(sleeping.stop)
        self.sleep = sleeping.start()

    def download(self, **kwargs):
        return runtime.download(self.url, self.destination, **self.pins, **kwargs)

    def test_verified_complete_partial_is_published_without_invalid_range(self):
        self.partial.write_bytes(self.payload)
        with patch.object(runtime.urllib.request, "urlopen") as fetch:
            self.assertEqual(self.download(), self.destination)
        fetch.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertFalse(self.partial.exists())

    def test_corrupt_complete_or_oversized_partial_restarts_at_zero(self):
        for payload in (b"x" * len(self.payload), self.payload + b"extra"):
            with self.subTest(size=len(payload)):
                self.destination.unlink(missing_ok=True)
                self.partial.write_bytes(payload)
                with patch.object(runtime.urllib.request, "urlopen", return_value=Response(self.payload)) as fetch:
                    self.download(retries=1)
                self.assertIsNone(fetch.call_args.args[0].get_header("Range"))
                self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_short_partial_resumes_only_at_verified_response_offset(self):
        self.partial.write_bytes(self.payload[:5])
        response = Response(self.payload[5:], status=206, headers={"Content-Range": f"bytes 5-{len(self.payload)-1}/{len(self.payload)}"})
        with patch.object(runtime.urllib.request, "urlopen", return_value=response) as fetch:
            self.download(retries=1)
        self.assertEqual(fetch.call_args.args[0].get_header("Range"), "bytes=5-")
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_wrong_range_offset_or_total_cannot_append_to_a_prefix(self):
        for value in ("bytes 0-3/24", "bytes 5-1/24", "bytes 5-24/24", "invalid", "bytes 5-23/4096"):
            with self.subTest(content_range=value):
                self.destination.unlink(missing_ok=True)
                self.partial.write_bytes(self.payload[:5])
                replies = [Response(b"wrong", status=206, headers={"Content-Range": value}), Response(self.payload)]
                with patch.object(runtime.urllib.request, "urlopen", side_effect=replies) as fetch:
                    self.download(retries=2)
                self.assertEqual([call.args[0].get_header("Range") for call in fetch.call_args_list], ["bytes=5-", None])
                self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_ignored_range_uses_that_whole_response_without_recursive_retry(self):
        self.partial.write_bytes(self.payload[:5])
        with patch.object(runtime.urllib.request, "urlopen", return_value=Response(self.payload)) as fetch:
            self.download(retries=1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_http_416_discards_short_stale_prefix_and_retries_from_zero(self):
        self.partial.write_bytes(b"old")
        unavailable = urllib.error.HTTPError(self.url, 416, "Requested Range Not Satisfiable", {}, None)
        with patch.object(runtime.urllib.request, "urlopen", side_effect=[unavailable, Response(self.payload)]) as fetch:
            self.download(retries=2)
        self.assertEqual([call.args[0].get_header("Range") for call in fetch.call_args_list], ["bytes=3-", None])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_bad_md5_restarts_fresh_and_is_never_promoted(self):
        bad = b"x" * len(self.payload)
        with patch.object(runtime.urllib.request, "urlopen", side_effect=[Response(bad), Response(self.payload)]) as fetch:
            self.download(retries=2)
        self.assertEqual([call.args[0].get_header("Range") for call in fetch.call_args_list], [None, None])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_repeated_bad_md5_exhausts_original_budget_without_publishing(self):
        bad = b"x" * len(self.payload)
        self.destination.write_bytes(b"old destination")
        with patch.object(runtime.urllib.request, "urlopen", side_effect=lambda *_args, **_kwargs: Response(bad)) as fetch:
            with self.assertRaisesRegex(runtime.OncoTracerError, "failed after 3 attempts.*validation failed"):
                self.download(retries=3)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.destination.read_bytes(), b"old destination")
        self.assertFalse(self.partial.exists())

    def test_short_origin_response_reports_pinned_size_mismatch(self):
        with patch.object(runtime.urllib.request, "urlopen", side_effect=lambda *_args, **_kwargs: Response(b"bad", headers={"Content-Length": "3"})) as fetch:
            with self.assertRaisesRegex(runtime.OncoTracerError, f"server reports 3 bytes; expected {len(self.payload)}"):
                self.download(retries=2)
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.partial.exists())

    def test_short_transfer_keeps_prefix_for_range_retry(self):
        replies = [Response(self.payload[:5], headers={"Content-Length": str(len(self.payload))}), Response(self.payload[5:], status=206, headers={"Content-Range": f"bytes 5-{len(self.payload)-1}/{len(self.payload)}"})]
        with patch.object(runtime.urllib.request, "urlopen", side_effect=replies) as fetch:
            self.download(retries=2)
        self.assertEqual([call.args[0].get_header("Range") for call in fetch.call_args_list], [None, "bytes=5-"])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_unpinned_existing_partial_cannot_be_published_without_contacting_source(self):
        self.partial.write_bytes(b"incomplete old bytes")
        with patch.object(runtime.urllib.request, "urlopen", return_value=Response(self.payload)) as fetch:
            runtime.download(self.url, self.destination, retries=1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_sha256_only_corrupt_partial_is_not_accepted_on_http_416(self):
        self.partial.write_bytes(b"wrong")
        unavailable = urllib.error.HTTPError(self.url, 416, "Requested Range Not Satisfiable", {}, None)
        with patch.object(runtime.urllib.request, "urlopen", side_effect=[unavailable, Response(self.payload)]):
            runtime.download(self.url, self.destination, expected_sha256=hashlib.sha256(self.payload).hexdigest(), retries=2)
        self.assertEqual(self.destination.read_bytes(), self.payload)


if __name__ == "__main__":
    unittest.main()
