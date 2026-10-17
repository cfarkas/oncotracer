"""Real two-sample VCF fixtures exercising missingness and allele evidence."""
from pathlib import Path
import os
import shutil
import tempfile
import unittest

try:
    import pysam
except ImportError:
    pysam = None

from oncotracer_cli.glimpse_joint import merge_cohort

BCFTOOLS = os.environ.get('ONCOTRACER_BCFTOOLS') or shutil.which('bcftools')


@unittest.skipUnless(pysam and BCFTOOLS and Path(BCFTOOLS).is_file(), 'pysam and bcftools required')
class GlimpseJointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reference = self.root / 'reference.fa'
        self.reference.write_text('>chr1\n' + 'A' * 300 + '\n>chr2\n' + 'C' * 200 + '\n')
        pysam.faidx(str(self.reference))

    def vcf(self, sample, rows, *, sample_info=False, imputed=False, length=300, reverse_header=False):
        path = self.root / (sample + ('.imputed' if imputed else '.input') + '.vcf.gz')
        header = pysam.VariantHeader()
        for chrom, size in ([('chr2', 200), ('chr1', length)] if reverse_header else [('chr1', length), ('chr2', 200)]):
            header.contigs.add(chrom, length=size)
        header.formats.add('GT', 1, 'String', 'Genotype')
        if imputed:
            header.formats.add('GP', 'G', 'Float', 'Genotype posterior')
            header.formats.add('DS', 'A', 'Float', 'Dosage')
            header.info.add('INFO', 1, 'Float', 'Imputation quality')
        else:
            collection = header.info if sample_info else header.formats
            collection.add('EVCLASS', 1, 'String', 'Evidence')
            collection.add('CARRIER', 1, 'String', 'Carrier context')
            header.formats.add('FT', 1, 'String', 'Evidence filter')
            header.formats.add('DS', 'A', 'Float', 'Dosage')
            header.info.add('CLNSIG', '.', 'String', 'ClinVar')
            header.info.add('ANN_GENE', 1, 'String', 'Gene')
            header.info.add('AC', 'A', 'Integer', 'Stale count')
            header.info.add('AN', 1, 'Integer', 'Stale count')
            header.info.add('AF', 'A', 'Float', 'Stale frequency')
        header.add_sample(sample)
        with pysam.VariantFile(str(path), 'wz', header=header) as out:
            for data in rows:
                chrom, pos, ref, alt, gt = data[:5]
                category = data[5] if len(data) > 5 else 'germline_supported'
                rec = out.new_record(contig=chrom, start=pos-1, alleles=(ref, *alt.split(',')))
                call = rec.samples[sample]
                call['GT'] = gt
                call.phased = bool(len(data) > 6 and data[6])
                if imputed:
                    call['GP'] = data[7] if len(data) > 7 else (0.99, 0.005, 0.005)
                    call['DS'] = (0.01,)
                    rec.info['INFO'] = data[8] if len(data) > 8 else 0.99
                else:
                    destination = rec.info if sample_info else call
                    destination['EVCLASS'] = category
                    destination['CARRIER'] = 'possible_recessive_carrier' if category == 'germline_supported' else 'unknown'
                    call['FT'] = 'PASS' if category == 'germline_supported' else 'Unconfirmed'
                    call['DS'] = tuple(1.0 for _ in rec.alts)
                    rec.info['CLNSIG'] = ('Pathogenic',)
                    rec.info['ANN_GENE'] = 'TEST'
                    rec.info['AC'] = tuple(99 for _ in rec.alts)
                    rec.info['AN'] = 999
                    rec.info['AF'] = tuple(0.9 for _ in rec.alts)
                out.write(rec)
        pysam.tabix_index(str(path), preset='vcf', force=True)
        return path

    def merge(self, paths, imputed=None, roles=None, exclusions=None):
        return merge_cohort(paths, self.root / 'cohort', BCFTOOLS, self.reference,
                            imputed_by_sample=imputed, sample_roles=roles, population_excluded_samples=exclusions)

    def records(self, manifest, kind='all_evidence'):
        with pysam.VariantFile(manifest['outputs'][kind]) as reader:
            return {(r.contig, r.pos, r.ref, r.alts): r.copy() for r in reader}

    def test_two_samples_exact_alleles_phase_annotations_and_no_fake_reference(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1), 'germline_supported', True),
                           ('chr1', 20, 'A', 'C', (0, 1), 'somatic_candidate'),
                           ('chr1', 30, 'A', 'G', (None, None), 'unconfirmed')])
        b = self.vcf('B', [('chr1', 10, 'A', 'G', (1, 1)),
                           ('chr1', 20, 'A', 'T', (0, 1)),
                           ('chr2', 15, 'C', 'T', (0, 1))], sample_info=True)
        manifest = self.merge({'B': b, 'A': a})
        all_records = self.records(manifest)
        self.assertEqual(len(all_records), 5)
        shared = all_records['chr1', 10, 'A', ('G',)]
        self.assertEqual(shared.samples['A']['GT'], (0, 1))
        self.assertTrue(shared.samples['A'].phased)
        self.assertEqual(shared.samples['B']['GT'], (1, 1))
        self.assertEqual(shared.samples['B']['CARRIER'], 'possible_recessive_carrier')
        self.assertNotIn('EVCLASS', shared.info)
        self.assertEqual(shared.info['CLNSIG'], ('Pathogenic',))
        self.assertEqual(shared.info['AC'], (3,))
        self.assertEqual(shared.info['AN'], 4)
        self.assertAlmostEqual(shared.info['AF'][0], .75)
        somatic = all_records['chr1', 20, 'A', ('C',)]
        self.assertEqual(somatic.samples['B']['GT'], (None, None))
        self.assertEqual(somatic.samples['A']['EVCLASS'], 'somatic_candidate')
        self.assertEqual(somatic.info['AN'], 2)
        self.assertEqual(somatic.info['AC'], (1,))
        population = self.records(manifest, 'germline_population')
        self.assertNotIn(('chr1', 20, 'A', ('C',)), population)
        self.assertNotIn(('chr1', 30, 'A', ('G',)), population)
        self.assertEqual(population['chr1', 20, 'A', ('T',)].samples['A']['GT'], (None, None))
        self.assertEqual(population['chr1', 20, 'A', ('T',)].samples['B']['DS'], (1.0,))
        self.assertTrue(Path(manifest['outputs']['all_evidence'] + '.tbi').is_file())
        self.assertTrue(self.merge({'B': b, 'A': a})['reused'])

    def test_backfill_requires_exact_allele_quality_and_never_overrides_existing_hold(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1)), ('chr1', 20, 'A', 'G', (0, 1)),
                           ('chr1', 30, 'A', 'G', (0, 1)), ('chr1', 40, 'A', 'G', (0, 1))])
        b = self.vcf('B', [('chr1', 40, 'A', 'G', (None, None), 'unconfirmed')])
        imp = self.vcf('B', [('chr1', 10, 'A', 'G', (0, 0), '', True),
                             ('chr1', 20, 'A', 'T', (0, 0)),
                             ('chr1', 30, 'A', 'G', (0, 0), '', False, (0.70, 0.15, 0.15), .99),
                             ('chr1', 40, 'A', 'G', (0, 0))], imputed=True)
        manifest = self.merge({'A': a, 'B': b}, {'B': [imp]})
        records = self.records(manifest)
        first = records['chr1', 10, 'A', ('G',)]
        self.assertEqual(first.samples['B']['GT'], (0, 0))
        self.assertTrue(first.samples['B'].phased)
        self.assertEqual(first.info['AN'], 4)
        self.assertEqual(first.samples['B']['GTS'], 'imputation_exact_allele_backfill')
        for pos in (20, 30, 40):
            self.assertEqual(records['chr1', pos, 'A', ('G',)].samples['B']['GT'], (None, None))
        self.assertEqual(manifest['sample_counts']['B']['imputed_exact_allele_backfills'], 1)

    def test_multiallelic_other_alternate_becomes_missing_not_reference(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G,T', (1, 2), 'germline_supported', True)])
        b = self.vcf('B', [('chr1', 10, 'A', 'G', (0, 1))])
        manifest = self.merge({'A': a, 'B': b})
        records = self.records(manifest)
        self.assertEqual(records['chr1', 10, 'A', ('G',)].samples['A']['GT'], (1, None))
        self.assertEqual(records['chr1', 10, 'A', ('T',)].samples['A']['GT'], (None, 1))
        self.assertTrue(records['chr1', 10, 'A', ('G',)].samples['A'].phased)
        self.assertEqual(records['chr1', 10, 'A', ('G',)].info['AN'], 3)
        self.assertEqual(records['chr1', 10, 'A', ('T',)].samples['B']['GT'], (None, None))

    def test_ref_mismatch_fails_without_publishing_vcf(self):
        a = self.vcf('A', [('chr1', 10, 'C', 'G', (0, 1))])
        with self.assertRaisesRegex(RuntimeError, 'Reference allele mismatch'):
            self.merge({'A': a})
        self.assertFalse((self.root / 'cohort' / 'cohort.manifest.json').exists())
        self.assertFalse((self.root / 'cohort' / 'cohort.all_evidence.vcf.gz').exists())

    def test_sample_duplicate_and_reference_dictionary_are_rejected(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))])
        with self.assertRaisesRegex(ValueError, 'Duplicate sample VCF'):
            self.merge({'A': a, 'B': a})
        with self.assertRaisesRegex(ValueError, 'expected unique sample'):
            self.merge({'B': a})
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))], length=301)
        with self.assertRaisesRegex(ValueError, 'Reference dictionary mismatch'):
            self.merge({'A': a})

    def test_reference_order_is_canonical_even_if_input_header_is_reversed(self):
        a = self.vcf('A', [('chr2', 10, 'C', 'T', (0, 1)),
                           ('chr1', 20, 'A', 'G', (0, 1))], reverse_header=True)
        b = self.vcf('B', [('chr1', 20, 'A', 'G', (1, 1))])
        manifest = self.merge({'A': a, 'B': b})
        self.assertEqual(list(self.records(manifest)), [('chr1', 20, 'A', ('G',)), ('chr2', 10, 'C', ('T',))])

    def test_duplicate_normalized_allele_is_rejected(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1)), ('chr1', 10, 'A', 'G', (1, 1))])
        with self.assertRaisesRegex(ValueError, 'Duplicate or unsorted normalized allele'):
            self.merge({'A': a})
        self.assertFalse((self.root / 'cohort' / 'cohort.manifest.json').exists())

    def test_population_masks_one_sample_but_preserves_other_sample_dosage(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1), 'somatic_candidate')])
        b = self.vcf('B', [('chr1', 10, 'A', 'G', (0, 1))])
        manifest = self.merge({'A': a, 'B': b})
        record = self.records(manifest, 'germline_population')['chr1', 10, 'A', ('G',)]
        self.assertEqual(record.samples['A']['GT'], (None, None))
        self.assertEqual(record.samples['B']['GT'], (0, 1))
        self.assertEqual(record.samples['B']['DS'], (1.0,))
        self.assertEqual(record.info['AC'], (1,))
        self.assertEqual(record.info['AN'], 2)
        self.assertEqual(record.samples['A']['MASKREASON'], 'somatic_candidate')

    def test_ffpe_rejected_and_imputation_rescue_pending_are_masked(self):
        samples = {}
        for sample, accepted, rescued in [('A', 0, 0), ('B', 1, 1), ('C', 1, 0), ('D', 1, 0)]:
            path = self.vcf(sample, [('chr1', 10, 'A', 'G', (0, 1))])
            with pysam.VariantFile(str(path)) as reader:
                header = reader.header.copy()
                record = next(reader).copy()
            for tag in ('GFACCEPT', 'GFRESCUE', 'GFPOP'):
                header.formats.add(tag, 1, 'Integer', 'FFPE assessment')
            record.translate(header)
            record.samples[sample]['GFACCEPT'] = accepted
            record.samples[sample]['GFRESCUE'] = rescued
            record.samples[sample]['GFPOP'] = 0 if sample == 'D' else 1
            with pysam.VariantFile(str(path), 'wz', header=header) as writer:
                writer.write(record)
            samples[sample] = path
        manifest = self.merge(samples)
        all_record = self.records(manifest)['chr1', 10, 'A', ('G',)]
        self.assertEqual(all_record.info['AN'], 8)
        population = self.records(manifest, 'germline_population')['chr1', 10, 'A', ('G',)]
        self.assertEqual(population.samples['A']['GT'], (None, None))
        self.assertEqual(population.samples['B']['GT'], (None, None))
        self.assertEqual(population.samples['C']['GT'], (0, 1))
        self.assertEqual(population.samples['D']['GT'], (None, None))
        self.assertEqual(population.samples['D']['MASKREASON'], 'ffpe_population_ineligible')
        self.assertEqual(population.samples['A']['MASKREASON'], 'ffpe_artifact_excluded')
        self.assertEqual(population.samples['B']['MASKREASON'], 'ffpe_imputation_rescue_unconfirmed')
        self.assertEqual(population.info['AN'], 2)
        self.assertEqual(population.info['AC'], (1,))

    def test_distinct_heterozygous_alleles_never_invent_reference_or_cross_match(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1), 'germline_supported', True)])
        b = self.vcf('B', [('chr1', 10, 'A', 'T', (1, 0), 'germline_supported', True)])
        manifest = self.merge({'A': a, 'B': b})
        records = self.records(manifest)
        self.assertEqual(records['chr1', 10, 'A', ('G',)].samples['B']['GT'], (None, None))
        self.assertEqual(records['chr1', 10, 'A', ('T',)].samples['A']['GT'], (None, None))
        self.assertEqual(records['chr1', 10, 'A', ('T',)].samples['B']['GT'], (1, 0))
        self.assertTrue(records['chr1', 10, 'A', ('T',)].samples['B'].phased)
        for record in records.values():
            self.assertEqual(record.info['AC'], (1,))
            self.assertEqual(record.info['AN'], 2)

    def test_roles_persist_in_both_vcf_headers_calls_and_standalone_manifest(self):
        import json
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))])
        b = self.vcf('B', [('chr1', 20, 'A', 'G', (0, 1))])
        manifest = self.merge({'A': a, 'B': b}, roles={'A': 'tumor', 'B': 'normal'})
        for kind in ('all_evidence', 'germline_population'):
            with pysam.VariantFile(manifest['outputs'][kind]) as reader:
                self.assertIn('##SAMPLE=<ID=A,Role="tumor">', str(reader.header))
                for record in reader:
                    self.assertEqual(record.samples['A']['ROLE'], 'tumor')
                    self.assertEqual(record.samples['B']['ROLE'], 'normal')
        saved = json.loads((self.root / 'cohort' / 'cohort.manifest.json').read_text())
        self.assertEqual(saved['sample_roles'], {'A': 'tumor', 'B': 'normal'})
        self.assertTrue(self.merge({'A': a, 'B': b}, roles={'A': 'tumor', 'B': 'normal'})['reused'])

    def test_resume_rejects_changed_index_bytes(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))])
        manifest = self.merge({'A': a})
        index = Path(manifest['outputs']['all_evidence'] + '.tbi')
        index.write_bytes(b'corrupted-index')
        repaired = self.merge({'A': a})
        self.assertFalse(repaired['reused'])
        with pysam.VariantFile(repaired['outputs']['all_evidence']) as reader:
            self.assertEqual(len(list(reader.fetch('chr1', 9, 10))), 1)

    def test_invalid_posterior_vector_or_info_never_supplies_reference_call(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1)), ('chr1', 20, 'A', 'G', (0, 1))])
        b = self.vcf('B', [('chr1', 40, 'A', 'G', (0, 1))])
        imp = self.vcf('B', [('chr1', 10, 'A', 'G', (0, 0), '', False, (.99, .99, .99), .99),
                             ('chr1', 20, 'A', 'G', (0, 0), '', False, (.99, .005, .005), 1.5)], imputed=True)
        records = self.records(self.merge({'A': a, 'B': b}, {'B': [imp]}))
        for pos in (10, 20):
            self.assertEqual(records['chr1', pos, 'A', ('G',)].samples['B']['GT'], (None, None))

    def test_explicit_paired_individual_exclusion_masks_only_population(self):
        import json
        tumor = self.vcf('T', [('chr1', 10, 'A', 'G', (1, 1))])
        normal = self.vcf('N', [('chr1', 10, 'A', 'G', (0, 1))])
        reason = 'matched_normal_from_same_individual_included'
        manifest = self.merge({'T': tumor, 'N': normal}, roles={'T': 'tumor', 'N': 'normal'}, exclusions={'T': reason})
        all_record = self.records(manifest)['chr1', 10, 'A', ('G',)]
        population = self.records(manifest, 'germline_population')['chr1', 10, 'A', ('G',)]
        self.assertEqual(all_record.samples['T']['GT'], (1, 1))
        self.assertEqual(all_record.info['AN'], 4)
        self.assertEqual(population.samples['T']['GT'], (None, None))
        self.assertEqual(population.samples['N']['GT'], (0, 1))
        self.assertEqual(population.info['AN'], 2)
        self.assertEqual(population.info['AC'], (1,))
        self.assertEqual(population.samples['T']['MASKREASON'], reason)
        self.assertIn('##OncoTracerPopulationExclusion=<ID=T,Reason="' + reason + '">', str(population.header))
        saved = json.loads((self.root / 'cohort' / 'cohort.manifest.json').read_text())
        self.assertEqual(saved['population_excluded_samples'], {'T': reason})
        # Role by itself must not discard a tumor-only sample's germline calls.
        no_exclusion = self.merge({'T': tumor, 'N': normal}, roles={'T': 'tumor', 'N': 'normal'})
        self.assertFalse(no_exclusion['reused'])
        self.assertEqual(self.records(no_exclusion, 'germline_population')['chr1', 10, 'A', ('G',)].info['AN'], 4)

    def test_resume_rejects_changed_output_bytes(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))])
        manifest = self.merge({'A': a})
        output = Path(manifest['outputs']['all_evidence'])
        output.write_bytes(b'corrupted')
        repaired = self.merge({'A': a})
        self.assertFalse(repaired['reused'])
        self.assertEqual(len(self.records(repaired)), 1)

    def test_conflicting_annotations_are_not_silently_taken_from_first_sample(self):
        a = self.vcf('A', [('chr1', 10, 'A', 'G', (0, 1))])
        b = self.vcf('B', [('chr1', 10, 'A', 'G', (1, 1))])
        with pysam.VariantFile(str(b)) as reader:
            header = reader.header.copy()
            row = next(reader).copy()
        row.info['CLNSIG'] = ('Benign',)
        with pysam.VariantFile(str(b), 'wz', header=header) as writer:
            writer.write(row)
        with self.assertRaisesRegex(ValueError, 'Conflicting common annotation CLNSIG'):
            self.merge({'A': a, 'B': b})


if __name__ == '__main__':
    unittest.main()
