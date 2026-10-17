"""Regression for NCBI ClinVar releases without ##contig declarations."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BIN = Path(__file__).parents[1] / 'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import clinvar_resource

BCFTOOLS = shutil.which('bcftools')


class ClinvarResourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / 'genome.fa'
        self.reference.write_text('>chr1\n' + 'A' * 20 + '\n>chrM\n' + 'A' * 10 + '\n')
        Path(str(self.reference) + '.fai').write_text('chr1\t20\t6\t20\t21\nchrM\t10\t33\t10\t11\n')
        self.source = self.root / 'clinvar.vcf'
        self.source.write_text('##fileformat=VCFv4.2\n##reference=GRCh38\n'
                               '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
                               '1\t5\t101\tA\tC\t.\t.\t.\n'
                               'MT\t2\t102\tA\tG\t.\t.\t.\n')

    def test_missing_declarations_discover_record_chromosomes_and_aliases(self):
        _, names, mapping, _, missing = clinvar_resource.inspect(self.source, self.reference)
        self.assertTrue(missing)
        self.assertEqual(names, ['1', 'MT'])
        self.assertEqual(mapping, {'1':'chr1', 'MT':'chrM'})

    def test_incompatible_or_wrong_build_resource_fails_before_tool_execution(self):
        for body in ('##reference=GRCh37\n1\t1', '##reference=GRCh38\nOTHER\t1'):
            build, record = body.split('\n')
            self.source.write_text('##fileformat=VCFv4.2\n' + build + '\n'
                                   '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n' +
                                   record + '\t.\tA\tC\t.\t.\t.\n')
            with self.subTest(body=body), patch.object(clinvar_resource.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    clinvar_resource.prepare(self.source, self.reference, 'bcftools', self.root/'cache')
                run.assert_not_called()

    def test_empty_records_do_not_invent_chromosomes(self):
        self.source.write_text('##fileformat=VCFv4.2\n##reference=GRCh38\n'
                               '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n')
        with self.assertRaisesRegex(ValueError, 'no matching contigs'):
            clinvar_resource.inspect(self.source, self.reference)

    @unittest.skipUnless(BCFTOOLS, 'bcftools required')
    def test_repair_preserves_source_and_every_record_then_reuses_verified_cache(self):
        source_bytes = self.source.read_bytes()
        source_mtime = self.source.stat().st_mtime_ns
        prepared = clinvar_resource.prepare(self.source, self.reference, BCFTOOLS, self.root/'cache')
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.source.stat().st_mtime_ns, source_mtime)
        records = lambda text: [line for line in text.splitlines() if not line.startswith('#')]
        self.assertEqual(records(prepared.read_text()), records(source_bytes.decode()))
        self.assertIn('##contig=<ID=1,length=20>', prepared.read_text())
        with patch.object(clinvar_resource.subprocess, 'run', side_effect=AssertionError('cache miss')):
            self.assertEqual(clinvar_resource.prepare(self.source, self.reference, BCFTOOLS, self.root/'cache'), prepared)
        prepared.write_text('damaged output')
        restored = clinvar_resource.prepare(self.source, self.reference, BCFTOOLS, self.root/'cache')
        self.assertEqual(records(restored.read_text()), records(source_bytes.decode()))

    @unittest.skipUnless(BCFTOOLS, 'bcftools required')
    def test_actual_bgzip_release_format_passes_full_clinvar_normalization(self):
        # bcftools emits a BGZF VCF while retaining the missing contig metadata.
        compressed = self.root / 'clinvar.vcf.gz'
        subprocess.run([BCFTOOLS, 'view', '-Oz', '-o', str(compressed), str(self.source)],
                       check=True, capture_output=True)
        before = compressed.read_bytes()
        config = self.root / 'config.json'
        config.write_text(json.dumps({'clinvar':str(compressed), 'reference':str(self.reference),
                                      'tools':{'bcftools':BCFTOOLS}}))
        subprocess.run([sys.executable, str(BIN/'pipeline.py'), 'prepare_clinvar', '--config', str(config)],
                       cwd=self.root, check=True, capture_output=True, text=True)
        normalized = self.root/'clinvar.norm.vcf.gz'
        self.assertTrue(Path(str(normalized)+'.tbi').is_file())
        rows = subprocess.check_output([BCFTOOLS, 'query', '-f', '%CHROM\t%POS\t%REF\t%ALT\n',
                                        str(normalized)], text=True).splitlines()
        self.assertEqual(rows, ['chr1\t5\tA\tC', 'chrM\t2\tA\tG'])
        self.assertEqual(compressed.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
