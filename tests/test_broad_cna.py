import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from oncotracer_cli.broad_cna import screen_bins, publish_screen, export_wig_counts, METHOD

ROOT = Path(__file__).resolve().parents[1]


def synthetic(seed=12, changes=None, mean=1., biased=False):
    rng = np.random.default_rng(seed)
    rows = []
    for chrom in range(1, 23):
        for index in range(1000):
            # Different GC composition by chromosome; the matched baseline must handle it.
            gc = [.31, .46, .61][int(rng.choice(3, p=([.7,.2,.1] if chrom % 2 else [.1,.2,.7])))] if biased else .46
            mapping = .85 if index % 2 else .95
            rate = mean * mapping * (np.exp(4*(gc-.46)) if biased else 1.) * (changes or {}).get(str(chrom), 1.)
            rows.append(dict(chromosome=str(chrom), start=index*50000+1, end=(index+1)*50000,
                             gc=gc, mappability=mapping, bases=1., reads=int(rng.poisson(rate))))
    return rows


class BroadCnaTests(unittest.TestCase):
    def test_sparse_neutral_controls_do_not_become_losses(self):
        for seed in (12, 27, 49):
            with self.subTest(seed=seed):
                result = screen_bins(synthetic(seed))
                self.assertEqual(result['status'], 'experimental_screen_available')
                self.assertTrue(all(r['status'] == 'no_supported_broad_change' for r in result['regions']))

    def test_broad_changes_detected_with_intervals_and_arm_resolution(self):
        arms = {str(i): {'p': (1,25000000), 'q': (25000001,50000000)} for i in range(1,23)}
        result = screen_bins(synthetic(changes={'2': 2.2, '7': .35}), arms)
        regions = {r['region']: r for r in result['regions']}
        self.assertEqual(regions['2p']['status'], 'candidate_broad_gain')
        # A depleted arm may have too few reads; adaptation then tests the chromosome.
        self.assertEqual(regions['7']['status'], 'candidate_broad_loss')
        self.assertLess(regions['7']['upper_99'], -.3)
        self.assertLessEqual(regions['7']['approximate_q'], .01)
        self.assertNotIn('7p', regions)

    def test_gc_and_mappability_bias_do_not_create_broad_calls(self):
        result = screen_bins(synthetic(biased=True, mean=3))
        self.assertEqual(result['status'], 'experimental_screen_available')
        self.assertFalse(any(r['status'].startswith('candidate') for r in result['regions']))

    def test_near_empty_or_absent_chromosome_remains_unassessable(self):
        result = screen_bins(synthetic(mean=.01))
        self.assertEqual(result['status'], 'not_assessable')
        result = screen_bins(synthetic(changes={'2': 0}))
        row = next(r for r in result['regions'] if r['region'] == '2')
        self.assertEqual(row['status'], 'not_assessable')
        self.assertNotIn('log2_ratio', row)

    def test_invalid_reference_and_duplicated_bins_are_not_treated_as_evidence(self):
        rows = synthetic()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            screen_bins(rows + rows[:1])
        for r in rows:
            r['gc'] = 'NA'
        self.assertEqual(screen_bins(rows)['status'], 'not_assessable')

    def test_publish_is_separate_from_primary_assessment_and_refreshes_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); counts = root/'counts'; counts.mkdir()
            path = counts/'broad_cna_counts.tsv.gz'
            rows = synthetic(changes={'2':2.2})
            def save():
                with gzip.open(path,'wt') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['chromosome','start','end','gc','mappability','bases','sample:case'],delimiter='\t')
                    writer.writeheader()
                    for row in rows:
                        data = dict(row);data['sample:case'] = data.pop('reads');writer.writerow(data)
            save()
            assessment = {'samples':[{'sample':'case','assessment':'not_assessable'}]}
            cyto = ROOT/'bin/cna_codification/resources/hg38.cytoBand.txt.gz'
            first = publish_screen(root,counts,'qdnaseq',assessment,cyto)
            self.assertEqual(first['schema'],METHOD)
            self.assertEqual(assessment['samples'][0]['assessment'],'not_assessable')
            self.assertFalse((root/'03_cna_codification').exists())
            for row in rows:
                row['reads'] = 0
            save()
            second = publish_screen(root,counts,'qdnaseq',assessment,cyto)
            self.assertNotEqual(first['input_sha256'],second['input_sha256'])
            self.assertEqual(second['samples'][0]['status'],'not_assessable')
            page=(root/'06_workflow_summary/experimental_broad_cna/index.html').read_text()
            self.assertIn('no validated sensitivity',page)
            self.assertNotIn('candidate broad gain</td>',page)

    def test_ichor_raw_wig_export_preserves_coordinates_and_zero_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name, values in [('counts',[0,20,30]),('gc',[.5,.5,.5]),('map',[.8,.8,.8])]:
                (root/(name+'.wig')).write_text('fixedStep chrom=chr1 start=1 step=500000 span=500000\n'+'\n'.join(map(str,values))+'\n')
            output=root/'raw.tsv.gz'
            export_wig_counts(root/'counts.wig',root/'gc.wig',root/'map.wig',output)
            with gzip.open(output,'rt') as handle:
                rows=list(csv.DictReader(handle,delimiter='\t'))
            self.assertEqual(rows[0]['reads'],'0.0')
            self.assertEqual(rows[1]['start'],'500001')
            self.assertEqual(rows[1]['end'],'1000000')


if __name__ == '__main__':
    unittest.main()
