import csv
import os
from pathlib import Path
import tempfile
import unittest

from oncotracer_cli.engine import parse_ont_samples
from oncotracer_cli.sample_manifest import read_manifest
from oncotracer_cli.runtime import OncoTracerError


class SampleManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for sample in ('A', 'B'):
            folder = self.root / sample
            (folder / 'fastq_pass').mkdir(parents=True)
            (folder / 'fastq_pass/read.fastq').write_text('@read-' + sample + '\nACGT\n+\nIIII\n')
            (folder / 'pod5_pass').mkdir()
            (folder / 'pod5_pass/signal.pod5').write_bytes(b'fixture-' + sample.encode())
        self.table = self.root / 'samples.csv'

    def write(self, rows, fields=('sample', 'library', 'status', 'tissue')):
        with self.table.open('w', newline='') as h:
            w = csv.writer(h); w.writerow(fields); w.writerows(rows)
        return self.table

    def test_mixed_tissues_and_independent_controls_keep_ids_order_and_relative_paths(self):
        self.write([['Brain', 'A', 'tumor', 'CNS'], ['Control', 'B', 'normal', 'Peripheral blood']])
        rows = read_manifest(self.table, methylation=True)
        self.assertEqual([r.sample for r in rows], ['Brain', 'Control'])
        self.assertEqual(rows[1].pod5, self.root/'B/pod5_pass')
        native = parse_ont_samples({'ont_samplesheet': str(self.table)})
        self.assertEqual([s.status for s in native], ['tumor', 'normal'])
        self.assertEqual(native[0].fastq_dir, self.root/'A/fastq_pass')

    def test_duplicate_names_are_rejected(self):
        self.write([['A', 'A', 'tumor', 'CNS'], ['A', 'B', 'normal', 'CNS']])
        with self.assertRaisesRegex(OncoTracerError, 'Duplicate sample'):read_manifest(self.table)

    def test_same_read_in_two_samples_is_rejected_even_through_hardlink(self):
        (self.root/'B/fastq_pass/read.fastq').unlink()
        os.link(self.root/'A/fastq_pass/read.fastq', self.root/'B/fastq_pass/read.fastq')
        self.write([['A', 'A', 'tumor', 'CNS'], ['B', 'B', 'normal', 'CNS']])
        with self.assertRaisesRegex(OncoTracerError, 'FASTQ is reused'):read_manifest(self.table)

    def test_roles_and_tissues_are_never_inferred(self):
        for role,tissue,error in [('', 'CNS', 'status'), ('tumor', '', 'tissue'), ('tumor', 'AML', 'tissue')]:
            self.write([['A', 'A', role, tissue]])
            with self.assertRaisesRegex(OncoTracerError, error):read_manifest(self.table, methylation=True)

    def test_duplicate_signal_is_rejected(self):
        self.write([['A', 'A', 'tumor', 'CNS', 'A/pod5_pass'], ['B', 'B', 'normal', 'CNS', 'A/pod5_pass']],
                   ('sample','library','status','tissue','pod5'))
        with self.assertRaisesRegex(OncoTracerError, 'Signal is shared'):read_manifest(self.table,methylation=True)

    def test_illumina_paths_are_relative_to_table_and_pairing_is_validated(self):
        fields=('sample','fastq_1','fastq_2','status')
        self.write([['Pair','A/fastq_pass/read.fastq','B/fastq_pass/read.fastq','tumor']],fields)
        row=read_manifest(self.table)[0]
        self.assertEqual(row.mode,'illumina');self.assertEqual(row.fastq_2,(self.root/'B/fastq_pass/read.fastq',))
        self.write([['Pair','A/fastq_pass/read.fastq','A/fastq_pass/read.fastq','tumor']],fields)
        with self.assertRaisesRegex(OncoTracerError, 'FASTQ is reused'):read_manifest(self.table)

    def test_bad_headers_and_truncated_rows_fail_before_outputs(self):
        for text in ['sample,status,status,library\nA,tumor,tumor,A\n', 'sample,status,library\nA,tumor\n', 'sample,status\n']:
            self.table.write_text(text)
            with self.assertRaises(OncoTracerError):read_manifest(self.table)

    def test_explicit_directory_cannot_pool_multiple_barcodes(self):
        folder=self.root/'A/fastq_pass'
        (folder/'barcode01').mkdir();(folder/'barcode02').mkdir()
        (folder/'barcode01/read.fastq').write_text('@a\nAC\n+\nII\n')
        (folder/'barcode02/read.fastq').write_text('@b\nAC\n+\nII\n')
        self.write([['A','A/fastq_pass','tumor']],('sample','fastq_dir','status'))
        with self.assertRaisesRegex(OncoTracerError,'multiple barcodes'):read_manifest(self.table)

    def test_manifest_and_legacy_sample_lists_cannot_disagree_silently(self):
        self.write([['Control','A','normal','blood']])
        with self.assertRaisesRegex(OncoTracerError,'not both'):
            parse_ont_samples({'ont_samplesheet':str(self.table),'ont_barcodes':'other'})

    def test_normal_only_manifest_is_supported(self):
        self.write([['Control','A','normal','Peripheral blood']])
        self.assertEqual(parse_ont_samples({'ont_samplesheet':str(self.table)})[0].status,'normal')

if __name__ == '__main__':unittest.main()
