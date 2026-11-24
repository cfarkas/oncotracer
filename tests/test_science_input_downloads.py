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
