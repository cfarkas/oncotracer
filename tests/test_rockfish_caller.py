"""Caller selection, coordinate conventions and preservation of explicit modkit."""
import csv,gzip,importlib.util,math,re,tempfile,unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli.rockfish import resolve_caller
from oncotracer_cli.methylation import resolve_methylation_request,methylation_plan
from oncotracer_cli.runtime import OncoTracerError
from tests.test_native_methylation import Fixture
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_rockfish',ROOT/'bin/scripts/native_rockfish.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

class CallerTests(unittest.TestCase):
    def test_default_and_explicit_caller(self):
        self.assertEqual(resolve_caller(), 'rockfish')
        self.assertEqual(resolve_caller('modkit'), 'modkit')
        with self.assertRaises(OncoTracerError):resolve_caller('fallback')

    def test_existing_calls_require_explicit_modkit(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Fixture(Path(directory));config=fixture.config();config.pop('methylation_caller')
            config.pop('methylation_pod5_dir');config['methylation_modbam']=str(Path(directory)/'input.bam')
            with self.assertRaisesRegex(OncoTracerError,'methylation-caller modkit'):
                resolve_methylation_request(config,mode='ont')

    def test_default_rockfish_needs_no_modkit_or_modified_base_model(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Fixture(Path(directory));config=fixture.config();config.pop('methylation_caller')
            config.pop('methylation_modkit_executable');config.pop('methylation_dorado_modbase_model')
            source=Path(directory)/'source';source.mkdir();model=Path(directory)/'model.ckpt';model.write_bytes(b'model')
            config.update(methylation_rockfish_python=str(fixture.executables['python']),methylation_rockfish_source=str(source),methylation_rockfish_model=str(model))
            with patch('oncotracer_cli.rockfish.validate_assets',return_value={'rockfish_model':'fixture'}):
                request=resolve_methylation_request(config,mode='ont')
            self.assertEqual(request.caller,'rockfish');self.assertIsNone(request.modkit)
            self.assertIsNone(request.dorado_modbase_model)
            self.assertIn('rockfish-signal-cpg-inference',methylation_plan(request)['stages'])
            legacy=resolve_methylation_request(fixture.config(),mode='ont')
            self.assertEqual(legacy.caller,'modkit');self.assertIsNotNone(legacy.modkit)

    def test_cli_caller_is_forwarded_and_checked(self):
        from oncotracer_cli.cli import build_parser, parse_arguments
        prepared = parse_arguments(['prepare-rockfish', '--directory', '/tmp/example-rockfish', '--verify-only'])
        self.assertTrue(prepared.verify_only)
        parser=build_parser()
        args=parser.parse_args(['run','--config','input.yml','--methylation-caller','rockfish'])
        self.assertEqual(args.methylation_caller,'rockfish')
        args=parser.parse_args(['setup','--methylation-caller','modkit'])
        self.assertEqual(args.methylation_caller,'modkit')

    def test_methylation_acceleration_does_not_enable_variant_gpu(self):
        from oncotracer_cli.gpu_runtime import enabled, methylation_enabled
        config = {'gpu_usage': False, 'methylation_gpu_usage': True}
        self.assertFalse(enabled(config))
        self.assertTrue(methylation_enabled(config))
        self.assertFalse(methylation_enabled(config, False))
        self.assertTrue(methylation_enabled(config, True))
        with self.assertRaises(ValueError):methylation_enabled({'methylation_gpu_usage':'yes'})

    def test_read_coordinates_qc_and_observed_counts(self):
        import pysam
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ref=root/'ref.fa';ref.write_text('>chrTest\nNNNNNAAAACGTTTACGAAANNNNN\n');pysam.faidx(str(ref))
            bam=root/'reads.bam';predictions=[]
            with pysam.AlignmentFile(bam,'wb',header={'HD':{'VN':'1.6'},'SQ':[{'SN':'chrTest','LN':25}]}) as out:
                for rid,reverse,lowq,insertion,prob in [('forward',False,False,False,.9),('reverse',True,False,False,.1),('lowq',False,True,False,.9),('insertion',False,False,True,.9)]:
                    read=pysam.AlignedSegment();read.query_name=rid;read.query_sequence='AAAACCGTTTACGAAA' if insertion else 'AAAACGTTTACGAAA'
                    read.flag=16 if reverse else 0;read.reference_id=0;read.reference_start=5;read.mapping_quality=60
                    read.cigar=[(0,5),(1,1),(0,10)] if insertion else [(0,15)]
                    read.query_qualities=pysam.qualitystring_to_array(('!' if lowq else 'I')*len(read.query_sequence));out.write(read)
                    for match in re.finditer('CG',read.get_forward_sequence()):predictions.append((rid,match.start(),prob))
            raw=root/'predictions.tsv';raw.write_text('read_id\tpos\tprob\n'+''.join(f'{r}\t{p}\t{v}\n' for r,p,v in predictions))
            args=Namespace(sample='test',bam=bam,reference=ref,mapq=20,baseq=20,exclude_flags=3844,confidence=.8,keep_database=False,bedmethyl=root/'native.bed',extract=root/'extract.tsv')
            result=adapter.export_predictions(args,raw,root);adapter.export_native(args,root)
            self.assertEqual(result['counts']['distinct_high_confidence_cpg_dyads'],2)
            self.assertEqual(result['counts']['core_qc_predictions'],5)
            rows=list(csv.reader(args.bedmethyl.open(),delimiter='\t'))
            self.assertEqual({int(r[1]) for r in rows},{9,15})
            self.assertEqual(sum(int(r[9]) for r in rows),5)
            self.assertEqual(sum(int(r[11]) for r in rows),3)
            extracted=list(csv.DictReader(args.extract.open(),delimiter='\t'))
            self.assertEqual(len(extracted),5)
            for row in extracted:
                self.assertEqual(row['ref_kmer'][1:3] if row['ref_mod_strand']=='-' else row['ref_kmer'][2:4],'CG')
            raw.write_text('read_id\tpos\tprob\nforward\t4\tnan\n')
            with self.assertRaisesRegex(ValueError,'Invalid probability'):adapter.export_predictions(args,raw,root)

if __name__=='__main__':unittest.main()
