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


class ReportInventoryResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys
        source = Path(__file__).resolve().parents[1] / "oncotracer_cli/lpwgs/bin/reporting.py"
        spec = importlib.util.spec_from_file_location("inventory_resume_reporting", source)
        cls.reporting = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules), patch.object(sys, "path", [str(source.parent), *sys.path]):
            spec.loader.exec_module(cls.reporting)

    def fixture(self, root):
        source = root / "ancestry/TEST.qc.html"
        source.parent.mkdir(parents=True)
        source.write_text("<html><body>Sequencing quality</body></html>")
        folder = root / "deliverables"
        self.reporting.inventory(root, folder, "TEST")
        return source, folder, folder / "02_ancestry/ancestry/TEST.qc.html"

    def test_resume_preserves_actual_ffpe_review_banner_and_redirect_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, folder, destination = self.fixture(root)
            vote = {"counts": {"records": 10, "accepted": 8, "ffpe_filtered": 2}}
            for key in ("all_vcf", "accepted_vcf", "evidence_tsv"):
                path = root / key
                path.write_text("synthetic fixture")
                vote[key] = str(path)
            render_ffpe_review(root, "TEST", vote)
            before = destination.read_bytes(), destination.stat().st_mtime_ns
            source_before = source.read_bytes()
            self.assertIn(b"ONCOTRACER_FFPE_REVIEW_BEGIN", before[0])
            for _ in range(2):
                entries = self.reporting.inventory(root, folder, "TEST")
                self.assertTrue(any(row["source_relative_to_output"] == "ancestry/TEST.qc.html" for row in entries))
                self.assertEqual((destination.read_bytes(), destination.stat().st_mtime_ns), before)
                self.assertEqual(source.read_bytes(), source_before)

    def test_unrecognized_or_altered_regular_file_is_retained_and_rejected(self):
        for altered_redirect in (False, True):
            with self.subTest(altered_redirect=altered_redirect), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, folder, destination = self.fixture(root)
                text = destination.read_text().replace("../../../ancestry/TEST.qc.html", "different.html") if altered_redirect else "User-owned report"
                destination.write_text("<!-- review note -->" + text)
                before = destination.read_bytes()
                with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                    self.reporting.inventory(root, folder, "TEST")
                self.assertEqual(destination.read_bytes(), before)

    def test_unchanged_generated_redirect_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, folder, destination = self.fixture(root)
            import os
            os.utime(destination, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
            before = destination.read_bytes(), destination.stat().st_mtime_ns
            self.reporting.inventory(root, folder, "TEST")
            self.assertEqual((destination.read_bytes(), destination.stat().st_mtime_ns), before)

if __name__ == '__main__': unittest.main()
