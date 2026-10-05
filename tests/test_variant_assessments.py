"""Assessment joins preserve independent caller evidence and make gaps explicit."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli import ffperase, variants, varlociraptor
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.variant_filters import add_assessment, evidence_identity, records
from tests.test_variant_setup import VariantSetupTests

HEADER = '##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
DATA = ['chr1\t10\t.\tC\tT\t30\tstrand_bias\t.\tGT:AD:AF\t1|0:10,3:0.23\n',
        'chr1\t20\t.\tA\tG\t60\t.\t.\tGT:AD:AF\t./.:1,1:0.5\n',
        'chr1\t30\t.\tC\tA\t60\tPASS\t.\tGT:AD:AF\t0/1:8,9:0.53\n']


def assess_fixture(directory, candidates, assessed, selected):
    """Exercise the assessment join with synthetic external-tool output."""
    source = directory / 'input.vcf'
    source.write_text(HEADER + ''.join(candidates))
    reference = directory / 'reference.fa'
    bases = list('A' * 1000)
    for line in candidates:
        fields = line.split('\t')
        start = int(fields[1]) - 1
        bases[start:start + len(fields[3])] = fields[3].upper()
    reference.write_text('>chr1\n' + ''.join(bases) + '\n')
    Path(str(reference) + '.fai').write_text('chr1\t1000\t6\t1000\t1001\n')

    def run(label, command, **kwargs):
        if label == 'varlociraptor-estimate':
            kwargs['stdout'].write('{}')
        elif label == 'varlociraptor-view':
            output = Path(command[command.index('-o') + 1])
            rows = selected if 'selected' in output.name else assessed
            output.write_text(HEADER + ''.join(rows))
        else:
            kwargs['stdout'].write(b'fixture-bcf')

    destination = directory / 'result.vcf'
    result = varlociraptor.assess(
        source, destination, bam=directory / 'input.bam', reference=reference,
        directory=directory, run=run,
        tools={'varlociraptor': 'varlociraptor', 'bcftools': 'bcftools'})
    return source, destination, result


class AssessmentTests(unittest.TestCase):
    def test_join_retains_genotypes_all_alleles_and_prior_failure(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); source=d/'input.vcf'; out=d/'out.vcf'; source.write_text(HEADER+''.join(DATA))
            counts=add_assessment(source,out,{('chr1',10,'C','T'):('REJECTED',0.9,'artifact'),
                ('chr1',20,'A','G'):('REAL',0.1,'real')}, tag='TEST',description='test',table=d/'evidence.tsv')
            self.assertEqual(evidence_identity(source),evidence_identity(out))
            filters=[f[6] for _,f in records(out) if f]
            self.assertEqual(filters,['strand_bias;TEST_REJECTED','.','TEST_NOT_EVALUATED'])
            self.assertEqual(counts,{'REJECTED':1,'REAL':1,'NOT_EVALUATED':1})
    def test_ffperase_boolean_not_probability_cutoff_determines_label(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'class.tsv';p.write_text('CHR\tSTART\tREF\tALT\toncotracer_raw_predicts\toncotracer_predicts\nchr1\t10\tC\tT\t0.4\tTrue\n')
            self.assertEqual(ffperase.parse_classifications(p)[('chr1',10,'C','T')][0],'REJECTED')
    def test_ffperase_rejects_nan_model_probability(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'class.tsv';p.write_text('CHR\tSTART\tREF\tALT\toncotracer_raw_predicts\toncotracer_predicts\nchr1\t10\tC\tT\tnan\tTrue\n')
            with self.assertRaises(OncoTracerError):ffperase.parse_classifications(p)
    def test_ffpe_default_requires_model_but_fresh_does_not(self):
        for preservation, expected in [('fresh','off'),('ffpe','required')]:
            req=variants.resolve_variant_request({'run_variants':True,'variant_specimen_type':preservation,'variant_callers':'bcftools'},mode='illumina')
            self.assertEqual(req.ffperase,expected)
        with self.assertRaises(OncoTracerError):
            variants.resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh','variant_ffperase':'required'},mode='illumina')
    def test_invalid_fdr_and_custom_event_without_model_rejected(self):
        for updates in [{'variant_varlociraptor_fdr':float('nan')},{'variant_varlociraptor_fdr':0},{'variant_varlociraptor_events':'SOMATIC'}]:
            with self.assertRaises(OncoTracerError):
                variants.resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh',**updates},mode='illumina')
    def test_low_coverage_never_inflated_or_sent_to_model(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);work=d/'bcftools';work.mkdir();shared=d/'ffperase_shared';shared.mkdir()
            (shared/'coverage.tsv').write_text('#rname\tstartpos\tendpos\tmeandepth\nchr1\t1\t1000\t1.2\n')
            ref=d/'ref.fa';ref.write_text('>chr1\n'+'A'*1000+'\n');Path(str(ref)+'.fai').write_text('chr1\t1000\t6\t1000\t1001\n')
            source=d/'calls.vcf';source.write_text(HEADER+''.join(DATA))
            def forbidden(*a,**k):raise AssertionError('Low coverage must not invoke model or inflate depth')
            result=ffperase.assess(source,work/'out.vcf',detection={},bam=d/'bam',reference=ref,directory=work,run=forbidden,tools={})
            self.assertEqual(result['status'],'not_assessed');self.assertEqual(result['upstream_integer_coverage'],1)
            self.assertEqual(result['counts']['NOT_EVALUATED'],3)
            self.assertEqual(evidence_identity(source),evidence_identity(work/'out.vcf'))

    def test_varlociraptor_commands_and_dropped_alleles(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); source=d/'input.vcf';source.write_text(HEADER+''.join(DATA))
            commands=[]
            def run(label,command,**kwargs):
                commands.append([str(v) for v in command])
                if label=='varlociraptor-estimate': kwargs['stdout'].write('{}')
                elif label=='varlociraptor-view':
                    output=Path(command[command.index('-o')+1])
                    chosen=DATA[:1] if 'selected' in str(output) else DATA[:2]
                    output.write_text(HEADER+''.join(chosen))
                else: kwargs['stdout'].write(b'fixture-bcf')
            out=d/'result.vcf'
            result=varlociraptor.assess(source,out,bam=d/'bam',reference=d/'ref',directory=d,run=run,
                                       tools={'varlociraptor':'varlociraptor','bcftools':'bcftools'},platform='ont')
            self.assertEqual(result['counts'],{'REAL':1,'REJECTED':1,'NOT_EVALUATED':1})
            self.assertEqual(evidence_identity(source),evidence_identity(out))
            preprocess=next(c for c in commands if 'preprocess' in c)
            self.assertIn('--candidates',preprocess); self.assertIn('homopolymer',preprocess)
            self.assertIn('--bams',commands[0])
    def test_varlociraptor_failure_is_not_empty_success(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);source=d/'input.vcf';source.write_text(HEADER+DATA[0])
            def run(*a,**k):raise OncoTracerError('tool failed')
            with self.assertRaisesRegex(OncoTracerError,'tool failed'):
                varlociraptor.assess(source,d/'result.vcf',bam=d/'bam',reference=d/'ref',directory=d,
                                    run=run,tools={'varlociraptor':'binary'})
            self.assertFalse((d/'result.vcf').exists())

    def test_varlociraptor_joins_uppercased_alleles_without_rewriting_caller_evidence(self):
        candidates = [
            'chr1\t10\t.\tc\tt\t30\tstrand_bias\t.\tGT:AD:AF\t1|0:10,3:0.23\n',
            'chr1\t60\t.\tacgt\ta\t60\tPASS\t.\tGT:AD:AF\t0/1:8,9:0.53\n',
            'chr1\t80\t.\ta\taT\t60\t.\t.\tGT:AD:AF\t./.:1,1:0.5\n',
        ]
        assessed = [
            'chr1\t10\t.\tC\tT\t.\tPASS\tPROB_ARTIFACT=30\n',
            'chr1\t60\t.\tACGT\tA\t.\tPASS\tPROB_ARTIFACT=2\n',
            'chr1\t80\t.\tA\tAT\t.\tPASS\tPROB_ARTIFACT=40\n',
        ]
        with tempfile.TemporaryDirectory() as d:
            source, destination, result = assess_fixture(
                Path(d), candidates, assessed, [assessed[0], assessed[2]])
            self.assertEqual(result['counts'], {'REAL': 2, 'REJECTED': 1})
            self.assertEqual(evidence_identity(source), evidence_identity(destination))
            output = [fields for _, fields in records(destination) if fields]
            self.assertEqual([fields[6] for fields in output],
                             ['strand_bias', 'VARLOCIRAPTOR_REJECTED', '.'])

    def test_varlociraptor_joins_symbolic_deletions_by_length_and_retains_dropped_candidates(self):
        candidates = [
            f'chr1\t100\t.\tA{"C" * length}\tA\t50\tPASS\t.\tGT:AD\t0/1:12,7\n'
            for length in (55, 60)
        ] + ['chr1\t300\t.\tA\t<DUP>\t40\tPASS\tEND=350;SVTYPE=DUP\tGT:AD\t0/1:9,4\n']
        assessed = [
            f'chr1\t100\t.\tA\t<DEL>\t.\tPASS\tSVLEN=-{length};SVTYPE=DEL;PROB_ARTIFACT={score}\n'
            for length, score in ((55, 30), (60, 2))
        ]
        with tempfile.TemporaryDirectory() as d:
            source, destination, result = assess_fixture(
                Path(d), candidates, assessed, assessed[:1])
            self.assertEqual(result['counts'], {'REAL': 1, 'REJECTED': 1, 'NOT_EVALUATED': 1})
            self.assertEqual(result['unassessed_records'], 1)
            self.assertEqual(evidence_identity(source), evidence_identity(destination))
            output = [fields for _, fields in records(destination) if fields]
            self.assertEqual([fields[6] for fields in output],
                             ['PASS', 'VARLOCIRAPTOR_REJECTED', 'VARLOCIRAPTOR_NOT_EVALUATED'])
            with (Path(d) / 'varlociraptor.evidence.tsv').open() as handle:
                rows = list(csv.DictReader(handle, delimiter='\t'))
            self.assertEqual([(row['ref'], row['alt'], row['decision']) for row in rows],
                             [('A' + 'C' * 55, 'A', 'REAL'),
                              ('A' + 'C' * 60, 'A', 'REJECTED'), ('A', '<DUP>', 'NOT_EVALUATED')])
            self.assertEqual([row['score'] for row in rows], ['30.0', '2.0', '.'])

    def test_varlociraptor_unmatched_or_duplicate_alleles_are_errors(self):
        assessed = 'chr1\t10\t.\tC\tT\t.\tPASS\tPROB_ARTIFACT=30\n'
        unexpected = 'chr1\t10\t.\tC\tG\t.\tPASS\tPROB_ARTIFACT=30\n'
        for label, output in [('unexpected', [unexpected]), ('duplicate', [assessed, assessed])]:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(OncoTracerError):
                    assess_fixture(Path(d), DATA[:1], output, [])
                self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_symbolic_deletion_cannot_match_wrong_length(self):
        candidate = f'chr1\t100\t.\tA{"C" * 55}\tA\t50\tPASS\t.\tGT:AD\t0/1:12,7\n'
        unexpected = 'chr1\t100\t.\tA\t<DEL>\t.\tPASS\tSVLEN=-54;SVTYPE=DEL\n'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), [candidate], [unexpected], [unexpected])
            self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_multiallelic_candidates_or_assessments_are_errors(self):
        multiallelic = 'chr1\t10\t.\tC\tT,G\t30\tPASS\t.\tGT:AD\t1/2:1,3,4\n'
        for candidates, assessed in [([multiallelic], DATA[:1]), (DATA[:1], [multiallelic])]:
            with self.subTest(candidates=candidates), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(OncoTracerError):
                    assess_fixture(Path(d), candidates, assessed, [])
                self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_case_colliding_candidates_are_ambiguous(self):
        candidates = [DATA[0], DATA[0].replace('\tC\tT\t', '\tc\tt\t')]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), candidates, DATA[:1], DATA[:1])
            self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_duplicate_selection_is_not_silently_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), DATA[:1], DATA[:1], [DATA[0], DATA[0]])
            self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_duplicate_deletion_representations_are_errors(self):
        candidate = f'chr1\t100\t.\tA{"C" * 55}\tA\t50\tPASS\t.\tGT:AD\t0/1:12,7\n'
        symbolic = 'chr1\t100\t.\tA\t<DEL>\t.\tPASS\tSVLEN=-55;SVTYPE=DEL\n'
        for assessed, selected in [([candidate, symbolic], []), ([candidate], [candidate, symbolic])]:
            with self.subTest(assessed=assessed), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(OncoTracerError):
                    assess_fixture(Path(d), [candidate], assessed, selected)
                self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_literal_deletions_still_require_matching_sequence(self):
        candidate = f'chr1\t100\t.\tA{"C" * 55}\tA\t50\tPASS\t.\tGT:AD\t0/1:12,7\n'
        wrong_sequence = f'chr1\t100\t.\tA{"G" * 55}\tA\t.\tPASS\tPROB_ARTIFACT=30\n'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), [candidate], [wrong_sequence], [wrong_sequence])
            self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_symbolic_deletion_metadata_must_be_valid_and_consistent(self):
        candidate = f'chr1\t100\t.\tA{"C" * 55}\tA\t50\tPASS\t.\tGT:AD\t0/1:12,7\n'
        for info in ['SVTYPE=DEL', 'SVLEN=55;SVTYPE=DEL', 'SVLEN=0;SVTYPE=DEL',
                     'SVLEN=-55.0;SVTYPE=DEL', 'SVLEN=-55,-60;SVTYPE=DEL',
                     'SVLEN=-55;SVTYPE=INS', 'SVLEN=-55;END=154;SVTYPE=DEL']:
            with self.subTest(info=info), tempfile.TemporaryDirectory() as d:
                symbolic = f'chr1\t100\t.\tA\t<DEL>\t.\tPASS\t{info}\n'
                with self.assertRaises(OncoTracerError):
                    assess_fixture(Path(d), [candidate], [symbolic], [symbolic])
                self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_selection_cannot_introduce_an_unassessed_allele(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), DATA[:2], DATA[:1], DATA[:2])
            self.assertFalse((Path(d) / 'result.vcf').exists())

    def test_varlociraptor_breakend_contig_case_is_not_folded(self):
        candidate = 'chr1\t100\t.\tA\tA]chrX:200]\t50\tPASS\t.\tGT\t0/1\n'
        wrong_contig = candidate.replace('chrX:200', 'CHRX:200')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OncoTracerError):
                assess_fixture(Path(d), [candidate], [wrong_contig], [wrong_contig])
            self.assertFalse((Path(d) / 'result.vcf').exists())

class AssessmentSetupTests(VariantSetupTests):
    def test_new_assessment_controls_roundtrip(self):
        prepared=self.prepare(self.state(),variants=True,variant_specimen_type='ffpe',variant_callers='bcftools',
                     variant_ffperase='off',variant_varlociraptor='required',variant_varlociraptor_fdr='0.10')
        from oncotracer_cli.runtime import load_flat_yaml
        config=load_flat_yaml(Path(prepared['config_path']))
        self.assertEqual(config['variant_ffperase'],'off')
        self.assertEqual(float(config['variant_varlociraptor_fdr']),0.10)

if __name__=='__main__':unittest.main()
