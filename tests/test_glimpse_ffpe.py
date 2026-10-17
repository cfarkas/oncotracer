"""Missing imputation never establishes artifact or somatic origin."""
import gzip
import hashlib
import json
import subprocess
import sys
import zipfile
import tempfile
import unittest
from pathlib import Path
from oncotracer_cli.glimpse_ffpe import adjudicate_evidence, apply_vote_annotations
from oncotracer_cli.runtime import OncoTracerError

class EvidencePolicyTests(unittest.TestCase):
    def vote(self, **changes):
        args = dict(ffpe_status='ARTIFACT', imputed_gt='0|1', gp=.999, info=.99,
                    read_alt_count=5, read_dp=12, caller_filter='PASS')
        args.update(changes)
        return adjudicate_evidence(**args)

    def test_rescue_is_germline_research_candidate(self):
        d = self.vote()
        self.assertEqual(d['status'], 'germline_rescue_candidate')
        self.assertTrue(d['accepted']); self.assertTrue(d['rescued']); self.assertTrue(d['research_only']); self.assertFalse(d['eligible_for_population'])
        self.assertEqual(d['votes']['ffperase'], 'artifact')

    def test_missing_quality_never_rescues(self):
        for key in ('gp', 'info'):
            for value in (None, '.', 'NaN', float('inf'), -1, 2):
                with self.subTest(key=key, value=value):
                    self.assertFalse(self.vote(**{key: value})['accepted'])

    def test_off_panel_or_reference_does_not_establish_somatic(self):
        for gt in (None, '.', './.', '0/0', '0', '0/.'):
            with self.subTest(gt=gt):
                d = self.vote(imputed_gt=gt, ffpe_status='REAL')
                self.assertEqual(d['status'], 'unconfirmed_origin')
                self.assertTrue(d['accepted']); self.assertFalse(d['eligible_for_population'])

    def test_flagged_reference_or_offpanel_stays_in_audit_only(self):
        for gt in (None, '0/0'):
            d = self.vote(imputed_gt=gt)
            self.assertFalse(d['accepted']); self.assertEqual(d['status'], 'ffpe_filtered')

    def test_low_reads_do_not_rescue_even_gp_one(self):
        for alt, dp in [(0, 12), (1, 12), (2, 12), (3, 7), (99, 12), (3.5, 12), (None, 12), (5, None)]:
            with self.subTest(alt=alt, dp=dp):
                self.assertFalse(self.vote(gp=1, read_alt_count=alt, read_dp=dp)['accepted'])

    def test_discordant_read_fraction_does_not_rescue(self):
        for gt, alt, dp in [('1/1', 3, 20), ('0/1', 19, 20), ('0/1', 3, 100)]:
            self.assertFalse(self.vote(imputed_gt=gt, read_alt_count=alt, read_dp=dp)['accepted'])

    def test_caller_failure_cannot_be_erased(self):
        for flag in ('strand_bias', 'LowEvidence', '.', None):
            self.assertFalse(self.vote(caller_filter=flag)['accepted'])

    def test_reference_mismatch_disqualifies(self):
        for ffpe in ('REAL', 'ARTIFACT', 'NOT_EVALUATED'):
            d = self.vote(reference_match=False, ffpe_status=ffpe)
            self.assertFalse(d['accepted']); self.assertFalse(d['eligible_for_population'])

    def test_no_assessment_preserves_uncertainty_without_discard(self):
        d = self.vote(ffpe_status='NOT_EVALUATED', gp=None, read_alt_count=None)
        self.assertEqual(d['status'], 'ffpe_not_assessed')
        self.assertTrue(d['accepted']); self.assertFalse(d['rescued'])

    def test_real_not_discarded_for_low_read_support(self):
        self.assertTrue(self.vote(ffpe_status='REAL', read_alt_count=0, read_dp=1)['accepted'])

    def test_malformed_numeric_genotype_does_not_rescue(self):
        for gt in [(0, 1.1), (False, True), '0/2', '1/1/1']:
            self.assertFalse(self.vote(imputed_gt=gt)['rescued'])

    def test_reference_vote_requires_quality(self):
        self.assertEqual(self.vote(imputed_gt='0/0', gp=.2)['votes']['glimpse2'], 'unresolved')

    def test_called_gp_selected_not_maximum(self):
        self.assertFalse(self.vote(gp=[.999, .001, 0])['accepted'])
        self.assertTrue(self.vote(gp=[.001, .999, 0])['accepted'])
        self.assertFalse(self.vote(gp=[1, 1, 1])['accepted'])

HEADER = '''##fileformat=VCFv4.2
##contig=<ID=chr1,length=1000>
##FILTER=<ID=FFPERASE_REJECTED,Description="Artifact">
##INFO=<ID=FFPERASE_DECISION,Number=1,Type=String,Description="Decision">
##INFO=<ID=CLNSIG,Number=1,Type=String,Description="Clinical label">
##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">
##FORMAT=<ID=GDP,Number=1,Type=Integer,Description="Read depth">
##FORMAT=<ID=GAD,Number=R,Type=Integer,Description="Allele depths">
##FORMAT=<ID=GFT,Number=1,Type=String,Description="Caller filter">
##FORMAT=<ID=IGT,Number=1,Type=String,Description="Imputed GT">
##FORMAT=<ID=IGP,Number=1,Type=Float,Description="Called GP">
##FORMAT=<ID=IINFO,Number=1,Type=Float,Description="Imputation INFO">
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	TEST
'''
FMT = 'GT:GDP:GAD:GFT:IGT:IGP:IINFO'
ROWS = [
    f'chr1\t10\trs1\tC\tT\t60\tFFPERASE_REJECTED\tCLNSIG=Pathogenic;FFPERASE_DECISION=REJECTED\t{FMT}\t./.:12:7,5:PASS:C|T:1:1\n',
    f'chr1\t20\trs2\tA\tG\t30\tFFPERASE_REJECTED\tFFPERASE_DECISION=REJECTED\t{FMT}\t0/1:2:1,1:PASS:A|G:1:1\n',
    f'chr1\t30\t.\tG\tA\t40\tPASS\tFFPERASE_DECISION=NOT_EVALUATED\t{FMT}\t0/1:3:1,2:PASS:.:.:.\n',
]

class VcfAdapterTests(unittest.TestCase):
    def test_preserves_all_annotations_filters_and_missing_primary_gt(self):
        import pysam
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source.vcf'; source.write_text(HEADER + ''.join(ROWS))
            result = apply_vote_annotations(source, root/'out', 'TEST')
            with pysam.VariantFile(result['all_vcf']) as v:
                records = list(v)
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0].samples['TEST']['GT'], (None, None))
            self.assertEqual(records[0].info['CLNSIG'], 'Pathogenic')
            self.assertIn('FFPERASE_REJECTED', records[0].filter)
            self.assertEqual(records[0].samples['TEST']['GFSTATUS'], 'germline_rescue_candidate')
            self.assertEqual(records[1].samples['TEST']['GFACCEPT'], 0)
            with pysam.VariantFile(result['accepted_vcf']) as v:
                self.assertEqual([r.pos for r in v], [10, 30])
            self.assertTrue(Path(result['all_vcf'] + '.tbi').exists())
            with gzip.open(result['all_vcf'], 'rt') as f:
                lines = [l.rstrip().split('\t') for l in f if not l.startswith('#')]
            for original, updated in zip(ROWS, lines):
                old = original.rstrip().split('\t')
                self.assertEqual(old[:8], updated[:8])
                self.assertEqual(old[9], ':'.join(updated[9].split(':')[:7]))

    def test_sample_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source.vcf'; source.write_text(HEADER + ''.join(ROWS))
            with self.assertRaises(OncoTracerError): apply_vote_annotations(source, root/'out', 'WRONG')

    def test_reference_mismatch(self):
        import pysam
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source.vcf'; source.write_text(HEADER + ROWS[0])
            reference = root/'ref.fa'; reference.write_text('>chr1\n' + 'A'*1000 + '\n')
            pysam.faidx(str(reference))
            result = apply_vote_annotations(source, root/'out', 'TEST', reference=reference)
            self.assertEqual(result['counts']['reference_mismatch'], 1)
            self.assertEqual(result['counts'].get('accepted', 0), 0)

    def test_external_imputation_exact_allele_not_position(self):
        import pysam
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source.vcf'; source.write_text(HEADER.replace('##FORMAT=<ID=IGT,Number=1,Type=String,Description="Imputed GT">\n', '') + ROWS[0].replace(':IGT:IGP:IINFO', ':OTHERGT:IGP:IINFO').replace('C|T:1:1', '.:.:.'))
            header = '##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n##INFO=<ID=INFO,Number=1,Type=Float,Description="INFO">\n##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n##FORMAT=<ID=GP,Number=G,Type=Float,Description="GP">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTEST\n'
            paths = []
            for i, alt in enumerate(['G', 'T']):
                imp = root/f'imp{i}.vcf'; imp.write_text(header + f'chr1\t10\t.\tC\t{alt}\t.\tPASS\tINFO=1\tGT:GP\t0|1:0,1,0\n')
                pysam.tabix_compress(str(imp), str(imp)+'.gz'); pysam.tabix_index(str(imp)+'.gz', preset='vcf')
                paths.append(str(imp)+'.gz')
            r = apply_vote_annotations(source, root/'out', 'TEST', [paths[0]])
            self.assertEqual(r['counts']['ffpe_filtered'], 1)
            r = apply_vote_annotations(source, root/'out2', 'TEST', [paths[1]])
            self.assertEqual(r['counts']['germline_rescue_candidate'], 1)
            with self.assertRaises(OncoTracerError): apply_vote_annotations(source, root/'duplicate', 'TEST', [paths[1]]*2)
            self.assertFalse((root/'duplicate'/'TEST.ffpe_glimpse.all.vcf.gz').exists())


class FFPEExecutionAdapterTests(unittest.TestCase):
    def fixture(self, directory):
        import pysam
        source = directory/'unified.vcf'
        header = '\n'.join(l for l in HEADER.splitlines() if not l.startswith(('##INFO=<ID=FFPERASE_', '##FILTER=<ID=FFPERASE_')))
        header = header.replace('#CHROM', '##FORMAT=<ID=GGT,Number=1,Type=String,Description="Observed GT">\n#CHROM') + '\n'
        fmt = 'GT:GGT:GDP:GAD:GFT:IGT:IGP:IINFO'
        source.write_text(header +
            f'chr1\t10\trs1\tC\tT\t60\tPASS\tCLNSIG=Pathogenic\t{fmt}\t./.:C/T:12:7,5:PASS:C|T:1:1\n' +
            f'chr1\t20\trs2\tA\tG\t30\tPASS\t.\t{fmt}\t0/1:A/G:2:1,1:PASS:A|G:1:1\n' +
            f'chr1\t30\t.\tG\tA\t.\t.\t.\t{fmt}\t0|1:.:.:.:.:G|A:1:1\n')
        reference = directory/'ref.fa'; bases = list('A'*1000); bases[9] = 'C'; bases[29] = 'G'
        reference.write_text('>chr1\n'+''.join(bases)+'\n'); pysam.faidx(str(reference))
        bam = directory/'reads.bam'; bam.write_bytes(b'fixture-only')
        return source, reference, bam

    def test_only_observed_calls_assessed_then_exact_join_and_reuse(self):
        import pysam
        from unittest.mock import patch
        from oncotracer_cli.glimpse_ffpe import assess_and_vote
        from oncotracer_cli.variant_filters import add_assessment, records
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source, reference, bam = self.fixture(root)
            assessed_positions = []
            def fake_assess(source, dest, **kwargs):
                positions = [int(f[1]) for _, f in records(source) if f]
                assessed_positions.append(positions)
                decisions = {('chr1', 10, 'C', 'T'): ('REJECTED', .9, 'fixture'),
                             ('chr1', 20, 'A', 'G'): ('REJECTED', .9, 'fixture')}
                add_assessment(source, dest, decisions, tag='FFPERASE', description='test',
                               table=kwargs['directory']/'ffperase.evidence.tsv')
                return {'status': 'complete'}
            with patch('oncotracer_cli.ffperase.assess', side_effect=fake_assess):
                result = assess_and_vote(source, root/'out', 'TEST', bam=bam, reference=reference, detection={}, tools={})
                self.assertEqual(result['read_candidates'], 2)
                self.assertEqual(assessed_positions, [[10, 20]])
                with pysam.VariantFile(result['all_vcf']) as v:
                    data = list(v)
                self.assertEqual(data[0].samples['TEST']['GT'], (None, None))
                self.assertEqual(data[0].samples['TEST']['GFSTATUS'], 'germline_rescue_candidate')
                self.assertEqual(data[0].samples['TEST']['FFPERASE_DECISION'], 'REJECTED')
                self.assertEqual(data[1].samples['TEST']['GFACCEPT'], 0)
                self.assertEqual(data[2].samples['TEST']['FFPERASE_DECISION'], 'NOT_EVALUATED')
                result2 = assess_and_vote(source, root/'out', 'TEST', bam=bam, reference=reference, detection={}, tools={})
                self.assertTrue(result2['reused']); self.assertEqual(len(assessed_positions), 1)
                Path(result['evidence_tsv']).write_text('corrupted cache artifact')
                result3 = assess_and_vote(source, root/'out', 'TEST', bam=bam, reference=reference, detection={}, tools={})
                self.assertFalse(result3['reused']); self.assertEqual(len(assessed_positions), 2)

    def test_no_direct_calls_never_invokes_model_tools(self):
        from oncotracer_cli.glimpse_ffpe import assess_and_vote
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source, reference, bam = self.fixture(root)
            lines = source.read_text().splitlines(True)
            source.write_text(''.join(l for l in lines if l.startswith('#')) + lines[-1])
            result = assess_and_vote(source, root/'out', 'TEST', bam=bam, reference=reference, detection={}, tools={})
            self.assertEqual(result['read_candidates'], 0)
            self.assertEqual(result['ffperase']['status'], 'not_applicable')
            self.assertEqual(result['counts']['ffpe_not_assessed'], 1)

if __name__ == '__main__':
    unittest.main()


class PackagedSourceDigestTests(unittest.TestCase):
    def test_digest_reads_packaged_sources_from_zip(self):
        package = Path(__file__).resolve().parents[1] / 'oncotracer_cli'
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'runtime.pyz'
            with zipfile.ZipFile(archive, 'w') as output:
                for source in package.glob('*.py'):
                    output.write(source, 'oncotracer_cli/' + source.name)
            code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                    "from oncotracer_cli import glimpse_ffpe as m;"
                    "assert m.__file__.startswith(sys.argv[1]);"
                    "print(json.dumps([m._implementation_sha256(n) for n in "
                    "('glimpse_ffpe.py','ffperase.py')]))")
            result = subprocess.run([sys.executable, '-c', code, str(archive)],
                                    cwd=directory, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), [
                hashlib.sha256((package / name).read_bytes()).hexdigest()
                for name in ('glimpse_ffpe.py', 'ffperase.py')])
