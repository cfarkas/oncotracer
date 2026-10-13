import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli.glimpse_ffpe_report import render_ffpe_review, _cover_pdf

class ReportTests(unittest.TestCase):
    def fixture(self, base):
        root = base/'sample'; root.mkdir()
        (root/'interpretation').mkdir()
        (root/'START_HERE.html').write_text('<html><body><h1>Previous report</h1></body></html>')
        (root/'interpretation/report.html').write_text('<html><body><h1>Findings</h1></body></html>')
        vote = {'counts': {'records': 10, 'ffpe_filtered': 2, 'germline_rescue_candidate': 1, 'accepted': 8}}
        for key, name in [('all_vcf', 'all.vcf.gz'), ('accepted_vcf', 'accepted.vcf.gz'), ('evidence_tsv', 'evidence.tsv')]:
            target = root/name; target.write_text('fixture'); vote[key] = str(target)
        return root, vote

    def test_index_safe_links_and_idempotent_banners(self):
        with tempfile.TemporaryDirectory() as d:
            root, vote = self.fixture(Path(d))
            first = render_ffpe_review(root, '<SAMPLE>', vote)
            second = render_ffpe_review(root, '<SAMPLE>', vote)
            self.assertEqual(len(second['annotated_html']), 2)
            for relative in second['annotated_html']:
                content = (root/relative).read_text()
                self.assertEqual(content.count('id="oncotracer-ffpe-adjudication"'), 1)
            index = Path(first['index']); content = index.read_text()
            self.assertIn('&lt;SAMPLE&gt;', content); self.assertNotIn('<SAMPLE>', content)
            for link in re.findall('href="([^"]+)"', content):
                if not link.startswith('https:'): self.assertTrue((index.parent/link).is_file())

    def test_historical_html_symlink_target_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, vote = self.fixture(base)
            historical = base/'old.html'; historical.write_text('<html><body>old</body></html>')
            before = historical.read_bytes()
            (root/'historical.html').symlink_to(historical)
            render_ffpe_review(root, 'TEST', vote)
            self.assertEqual(historical.read_bytes(), before)
            self.assertFalse((root/'historical.html').is_symlink())
            self.assertIn('oncotracer-ffpe-adjudication', (root/'historical.html').read_text())

    def test_outside_and_dotdot_paths_not_linked(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, vote = self.fixture(base)
            vote['all_vcf'] = str(root/'..'/'outside.vcf.gz')
            result = render_ffpe_review(root, 'TEST', vote)
            self.assertNotIn('href="../../outside', Path(result['index']).read_text())

    def test_missing_pdf_tool_is_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            root, vote = self.fixture(Path(d))
            pdf = root/'report.pdf'; _cover_pdf(pdf, ['Prior report'])
            before = pdf.read_bytes()
            with patch('oncotracer_cli.glimpse_ffpe_report.shutil.which', return_value=None):
                result = render_ffpe_review(root, 'TEST', vote)
            self.assertEqual(pdf.read_bytes(), before)
            self.assertEqual(len(result['pdf_warning_failures']), 1)
            self.assertIn('before FFPE adjudication', Path(result['index']).read_text())

    @unittest.skipUnless(shutil.which('pdfunite') and shutil.which('pdfinfo'), 'poppler tools unavailable')
    def test_pdf_cover_idempotent_and_preserves_historical_source(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, vote = self.fixture(base)
            historical = base/'historical.pdf'; _cover_pdf(historical, ['Prior report'])
            before = historical.read_bytes(); pdf = root/'report.pdf'; pdf.symlink_to(historical)
            first = render_ffpe_review(root, 'TEST', vote)
            second = render_ffpe_review(root, 'TEST', vote)
            self.assertEqual(historical.read_bytes(), before)
            self.assertFalse(pdf.is_symlink())
            self.assertEqual(first['pdfs_with_warning_cover'], ['report.pdf'])
            self.assertFalse(second['pdf_warning_failures'])
            info = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True, check=True).stdout
            self.assertRegex(info, r'Pages:\s+2\b')

    def test_work_html_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            root, vote = self.fixture(Path(d)); work = root/'work'; work.mkdir()
            page = work/'hidden.html'; page.write_text('<html><body>work</body></html>')
            render_ffpe_review(root, 'TEST', vote)
            self.assertNotIn('oncotracer-ffpe-adjudication', page.read_text())

if __name__ == '__main__': unittest.main()
