"""The public scientific benchmark must retain exact inputs across transports."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('science_inputs', ROOT / 'scripts/fetch_native_science_inputs.py')
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)

class PublicInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.receipt = self.root / 'audit.json'
        self.body = b'pinned public sequencing input'
        self.record = dict(url='https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR123/SRR123456_1.fastq.gz',
                           relative_path='public/reads.fastq.gz', bytes=len(self.body), md5=hashlib.md5(self.body).hexdigest())
        p = patch.object(fetcher, 'inputs', return_value=[self.record]);p.start();self.addCleanup(p.stop)
    def write_valid(self, url, destination, **kwargs):
        self.assertEqual(kwargs['expected_bytes'], len(self.body))
        self.assertEqual(kwargs['expected_md5'], self.record['md5'])
        destination.parent.mkdir(parents=True, exist_ok=True);destination.write_bytes(self.body)
        return destination
    def test_ftp_download_is_verified_and_receipted_before_candidate_reuse(self):
        with patch.object(fetcher, 'download', side_effect=self.write_valid) as download:
            rows = fetcher.fetch_inputs('quickstart2', self.root, self.receipt)
        self.assertTrue(download.call_args.args[0].startswith('ftp://ftp.sra.ebi.ac.uk/'))
        self.assertEqual(rows[0]['sha256'], hashlib.sha256(self.body).hexdigest())
        self.assertTrue(json.loads(self.receipt.read_text())['complete'])
    def test_ftp_outage_falls_back_to_https_with_identical_pins(self):
        def transfer(url, destination, **kwargs):
            if url.startswith('ftp:'):raise fetcher.OncoTracerError('temporary FTP outage')
            return self.write_valid(url, destination, **kwargs)
        with patch.object(fetcher, 'download', side_effect=transfer) as download:
            rows = fetcher.fetch_inputs('quickstart2', self.root, self.receipt)
        self.assertEqual(download.call_count, 2);self.assertEqual(rows[0]['transport'], self.record['url'])
        self.assertEqual(len(rows[0]['failed_sources']), 1)
    def test_complete_verified_file_is_reused_without_network(self):
        self.write_valid('', self.root / self.record['relative_path'], expected_bytes=len(self.body), expected_md5=self.record['md5'])
        with patch.object(fetcher, 'download') as download:rows = fetcher.fetch_inputs('quickstart2', self.root, self.receipt)
        download.assert_not_called();self.assertEqual(rows[0]['transport'], 'verified_existing')
    def test_short_file_is_retried_and_wrong_content_is_never_accepted(self):
        target=self.root / self.record['relative_path'];target.parent.mkdir();target.write_bytes(b'bad')
        def bad(url, destination, **kwargs):destination.write_bytes(b'x' * len(self.body));return destination
        with patch.object(fetcher, 'download', side_effect=bad):
            with self.assertRaisesRegex(fetcher.OncoTracerError, 'All ENA transports failed'):
                fetcher.fetch_inputs('quickstart2', self.root, self.receipt)
        self.assertFalse(self.receipt.exists())
    def test_previous_benchmark_input_is_copied_only_after_full_validation(self):
        reuse=self.root/'workspace'
        previous=reuse/'oncotracer-parity-123-1-quickstart2/input'/self.record['relative_path']
        self.write_valid('',previous,expected_bytes=len(self.body),expected_md5=self.record['md5'])
        with patch.object(fetcher,'download') as download:
            rows=fetcher.fetch_inputs('quickstart2',self.root/'new',self.receipt,reuse)
        download.assert_not_called();self.assertEqual(rows[0]['transport'],'verified_previous_benchmark')
        target=self.root/'new'/self.record['relative_path']
        self.assertEqual(target.read_bytes(),self.body);self.assertNotEqual(target.stat().st_ino,previous.stat().st_ino)
    def test_incorrect_previous_benchmark_file_does_not_replace_public_input(self):
        reuse=self.root/'workspace'
        previous=reuse/'oncotracer-parity-123-1-quickstart2/input'/self.record['relative_path']
        previous.parent.mkdir(parents=True);previous.write_bytes(b'x'*len(self.body))
        with patch.object(fetcher,'download',side_effect=self.write_valid) as download:
            rows=fetcher.fetch_inputs('quickstart2',self.root/'new',self.receipt,reuse)
        self.assertEqual(download.call_count,1);self.assertIsNone(rows[0]['reused_from'])
        self.assertEqual(previous.read_bytes(),b'x'*len(self.body))

    def test_persistent_cache_survives_a_new_checkout_and_copies_independently(self):
        cache = self.root / 'tool-cache'
        with patch.object(fetcher, 'download', side_effect=self.write_valid) as download:
            first = fetcher.fetch_inputs('quickstart2', self.root / 'old-checkout', cache_root=cache)
        self.assertEqual(download.call_count, 1)
        self.assertTrue(first[0]['cache_saved'])
        with patch.object(fetcher, 'download') as download:
            second = fetcher.fetch_inputs('quickstart2', self.root / 'new-checkout', self.receipt, cache_root=cache)
        download.assert_not_called()
        self.assertEqual(second[0]['transport'], 'verified_cache')
        cached = cache / self.record['md5'] / 'reads.fastq.gz'
        copied = self.root / 'new-checkout' / self.record['relative_path']
        self.assertNotEqual(cached.stat().st_ino, copied.stat().st_ino)
        copied.write_bytes(b'changed analysis input')
        self.assertTrue(fetcher.valid(cached, self.record))

    def test_same_length_corrupt_cache_is_replaced_only_after_verified_download(self):
        cache = self.root / 'tool-cache'
        cached = cache / self.record['md5'] / 'reads.fastq.gz'
        cached.parent.mkdir(parents=True); cached.write_bytes(b'x' * len(self.body))
        with patch.object(fetcher, 'download', side_effect=self.write_valid) as download:
            rows = fetcher.fetch_inputs('quickstart2', self.root / 'checkout', cache_root=cache)
        self.assertEqual(download.call_count, 1)
        self.assertTrue(rows[0]['cache_saved'])
        self.assertEqual(cached.read_bytes(), self.body)
        self.assertIsNone(rows[0]['reused_from'])

    def test_cache_write_failure_does_not_reject_authenticated_network_input(self):
        cache = self.root / 'tool-cache'
        cache.write_bytes(b'not a directory')
        with patch.object(fetcher, 'download', side_effect=self.write_valid):
            rows = fetcher.fetch_inputs('quickstart2', self.root / 'checkout', self.receipt, cache_root=cache)
        self.assertFalse(rows[0]['cache_saved'])
        self.assertTrue(rows[0]['cache_errors'])
        self.assertTrue(json.loads(self.receipt.read_text())['complete'])

    def test_cache_copy_changed_during_transfer_cannot_replace_input(self):
        source = self.root / 'cached.fastq.gz'; source.write_bytes(self.body)
        target = self.root / 'input.fastq.gz'
        def changed(source, destination):
            Path(destination).write_bytes(b'x' * len(self.body))
        with patch.object(fetcher.shutil, 'copyfile', side_effect=changed):
            with self.assertRaisesRegex(fetcher.OncoTracerError, 'changed while copying'):
                fetcher.copy_verified(source, target, self.record)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('.input.fastq.gz.*')), [])

    def test_cache_directory_escape_is_rejected(self):
        cache = self.root / 'cache'; cache.mkdir()
        external = self.root / 'outside'; external.mkdir()
        (cache / self.record['md5']).symlink_to(external, target_is_directory=True)
        with patch.object(fetcher, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'cache destination escapes'):
                fetcher.fetch_inputs('quickstart2', self.root / 'checkout', cache_root=cache)
        download.assert_not_called()

    def test_fallback_is_not_available_for_arbitrary_hosts_or_credentials(self):
        for url in ['https://example.org/vol1/fastq/a.fastq.gz','https://user:secret@ftp.sra.ebi.ac.uk/vol1/fastq/a.fastq.gz',
                    'https://ftp.sra.ebi.ac.uk/vol1/fastq/../a.fastq.gz','https://ftp.sra.ebi.ac.uk/vol1/fastq/a.fastq.gz?token=x']:
            with self.subTest(url=url),self.assertRaises(ValueError):fetcher.sources(url)
    def test_destination_traversal_is_rejected_before_network(self):
        self.record['relative_path']='../outside.fastq.gz'
        with patch.object(fetcher, 'download') as download:
            with self.assertRaises(ValueError):fetcher.fetch_inputs('quickstart2', self.root)
        download.assert_not_called()
    def test_malformed_publisher_identity_is_not_downgraded(self):
        self.record['md5']='bad'
        with patch.object(fetcher, 'download') as download:
            with self.assertRaises(ValueError):fetcher.fetch_inputs('quickstart2', self.root)
        download.assert_not_called()

if __name__=='__main__':unittest.main()
