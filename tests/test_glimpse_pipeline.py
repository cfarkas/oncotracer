"""Full workflow dispatch, safe reuse and real small alignment/VCF imports."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import glimpse_pipeline as pipeline
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.variants import VariantRequest, resolve_variant_request, run_variants, variant_plan


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GlimpsePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = resolve_variant_request({'run_variants': True, 'variant_specimen_type': 'fresh'}, mode='illumina')

    def test_illumina_always_dispatches_complete_glimpse_workflow(self):
        self.assertEqual(self.request.engine, 'glimpse2')
        self.assertEqual(self.request.callers, ('bcftools', 'mutect2'))
        planned = variant_plan(self.request)
        self.assertTrue(planned['glimpse2_required'])
        self.assertTrue(planned['nextflow_used'])
        with patch.object(pipeline, 'run', return_value={'overall_status': 'complete'}) as run:
            result = run_variants(self.request, {'ONE': self.root / 'one.bam'}, self.root / 'genome.fa',
                                  self.root / 'out', None, None, threads=2)
        self.assertEqual(result['overall_status'], 'complete')
        self.assertEqual(run.call_args.args[0], self.request)
        for settings in ({'variant_engine': 'native'}, {'variant_glimpse2': False}):
            with self.subTest(settings=settings), self.assertRaises(OncoTracerError):
                resolve_variant_request({'run_variants': True, 'variant_specimen_type': 'fresh', **settings}, mode='illumina')

    def test_supported_confidence_reference_and_path_options(self):
        for value in ('strict', 'medium', 'exploratory'):
            self.assertEqual(pipeline.validate_options({'variant_confidence_gwas': value})['confidence_gwas'], value)
        self.assertEqual(pipeline.validate_options({})['confidence_gwas'], 'medium')
        for config in ({'variant_confidence_gwas': 'high'}, {'variant_reference_build': 'hg19'},
                       {'variant_patients': 17}, {'variant_patients': 'bad\npath'}):
            with self.subTest(config=config), self.assertRaises(OncoTracerError):
                pipeline.validate_options(config)
        self.assertEqual(pipeline.validate_options({'variant_patients': str(self.root/'patients.tsv')})['patients'],
                         str(self.root/'patients.tsv'))

    def test_sample_parameters_enable_every_module_and_keep_paths(self):
        patients = self.root / 'patients.tsv'
        req = replace(self.request, glimpse_config={'confidence_gwas': 'medium', 'patients': str(patients)},
                      tool_prefix=self.root / 'tools', annovar_dir=self.root/'annovar', annovar_db=self.root/'humandb')
        params, audit = pipeline.sample_parameters(req, 'SAMPLE2', self.root/'two.bam', self.root/'ref.fa',
                                                   self.root/'sample results', self.root/'cache', 12)
        for key in ('glimpse2', 'gwas', 'annovar', 'imputed_clinvar', 'polygenic', 'explain_findings',
                    'ancestry_explorer', 'interpretation_report', 'report_llm', 'homozygosity_report',
                    'cancer_report', 'cardiomyopathy_report', 'unified_vcf', 'pgs_reference_stats', 'continue'):
            self.assertIs(params[key], True, key)
        self.assertEqual(params['sample'], 'SAMPLE2')
        self.assertEqual(params['patients'], str(patients))
        self.assertEqual(params['bam'], str(self.root/'two.bam'))
        self.assertEqual(params['threads'], 12)
        self.assertEqual(params['llm_threads'], 4)
        self.assertEqual(params['pgs_cache'], str(self.root/'cache/pgs'))
        self.assertEqual(audit, [])
        self.assertNotIn('allow_test_reference', params)

    def donor(self):
        parent = self.root/'previous/results/08_variants/samples/ONE/bcftools'
        parent.mkdir(parents=True)
        vcf = parent/'ONE.normalized.vcf.gz'; vcf.write_bytes(b'normalized callset, original LowEvidence preserved')
        index = Path(str(vcf)+'.tbi'); index.write_bytes(b'index')
        receipt = {'result': {'status': 'complete'}, 'vcf': vcf.name, 'signature': 'old-signature',
                   'outputs': {vcf.name: digest(vcf), index.name: digest(index)}}
        (parent/'complete.json').write_text(json.dumps(receipt))
        return parent, vcf, index, receipt

    def test_verified_calls_accept_only_completed_hash_matched_vcf_and_index(self):
        directory, vcf, index, receipt = self.donor()
        before = vcf.read_bytes()
        found, audit = pipeline.verified_calls(self.root/'previous', 'ONE')
        self.assertEqual(found, {'germline_vcf': str(vcf)})
        self.assertEqual(audit[0]['original_signature'], 'old-signature')
        self.assertEqual(vcf.read_bytes(), before)
        index.write_bytes(b'changed')
        self.assertEqual(pipeline.verified_calls(self.root/'previous', 'ONE'), ({}, []))
        index.write_bytes(b'index'); vcf.write_bytes(b'changed')
        self.assertEqual(pipeline.verified_calls(self.root/'previous', 'ONE'), ({}, []))
        receipt['vcf'] = '../foreign.vcf.gz'
        (directory/'complete.json').write_text(json.dumps(receipt))
        self.assertEqual(pipeline.verified_calls(self.root/'previous', 'ONE'), ({}, []))

    def test_same_output_reuses_verified_calls_before_status_is_replaced_and_on_second_run(self):
        bam = self.root/'one.bam'; bam.write_bytes(b'unchanged BAM fixture')
        directory, vcf, index, receipt = self.donor()
        output = self.root/'previous/results'
        old_status = output/'08_variants/variant_status.json'
        old_status.write_text(json.dumps({'samples':[{'sample':'ONE','input_bam':str(bam)}]}))
        reference = self.root/'ref.fa'; reference.write_text('>chr1\nA\n')
        request = replace(self.request, glimpse_config={'reuse_from':str(output)})
        captured = []
        class Runner:
            def run(self, stage, command, **kwargs):
                params = json.loads(Path(command[command.index('-params-file')+1]).read_text())
                captured.append(params)
                sample_output = Path(params['output'])
                annotated = sample_output/'cohort_vcf/ONE.cohort_vcf/ONE.annotated.vcf.gz'
                annotated.parent.mkdir(parents=True, exist_ok=True); annotated.write_bytes(b'completed VCF fixture')
                chromosome = sample_output/'imputation/ONE.chr1.imputed.bcf'
                chromosome.parent.mkdir(parents=True, exist_ok=True); chromosome.write_bytes(b'imputation fixture')
        with patch.object(pipeline, 'preflight', return_value={'nextflow':'nextflow','bcftools':'bcftools'}), \
                patch.object(pipeline, 'ensure_resources'), \
                patch('oncotracer_cli.glimpse_joint.merge_cohort', return_value={'outputs':{}}):
            for _ in range(2):
                result = pipeline.run(request, {'ONE':bam}, reference, output, Runner(), None)
                self.assertEqual(result['overall_status'], 'complete')
                self.assertEqual(result['samples'][0]['reused_calls'][0]['vcf'], str(vcf))
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(params['germline_vcf'] == str(vcf) for params in captured))
        self.assertEqual(json.loads(old_status.read_text())['samples'][0]['input_bam'], str(bam))
        self.assertEqual(vcf.read_bytes(), b'normalized callset, original LowEvidence preserved')

    def test_ownership_rejects_nonempty_unowned_tree_without_modifying_it(self):
        output = self.root/'existing'
        unowned = output/'08_variants/glimpse2'
        unowned.mkdir(parents=True)
        foreign = unowned/'results.txt'; foreign.write_text('existing work')
        with self.assertRaisesRegex(OncoTracerError, 'nonempty, unowned'):
            pipeline._claim_output_root(output, ['ONE'])
        self.assertEqual(foreign.read_text(), 'existing work')
        self.assertFalse((unowned/'owner.json').exists())
        self.assertFalse((output/'08_variants/variant_status.json').exists())

    def test_ownership_creates_empty_root_and_reuses_marker_without_rewriting(self):
        output = self.root/'owned'
        base, root = pipeline._claim_output_root(output, ['ONE'])
        marker = root/'owner.json'
        contents, stamp = marker.read_bytes(), marker.stat().st_mtime_ns
        (root/'existing_result').write_text('preserved')
        self.assertEqual(pipeline._claim_output_root(output, ['ONE']), (base, root))
        self.assertEqual(marker.read_bytes(), contents)
        self.assertEqual(marker.stat().st_mtime_ns, stamp)
        self.assertEqual((root/'existing_result').read_text(), 'preserved')

    def test_ownership_rejects_marker_symlinks_even_if_valid_or_broken(self):
        for name, contents in [('valid', json.dumps({'schema': pipeline.SCHEMA})), ('broken', None)]:
            output = self.root/name
            root = output/'08_variants/glimpse2'; root.mkdir(parents=True)
            outside = self.root/(name+'.json')
            if contents is not None: outside.write_text(contents)
            (root/'owner.json').symlink_to(outside)
            with self.subTest(name=name), self.assertRaisesRegex(OncoTracerError, 'owner marker.*symlink'):
                pipeline._claim_output_root(output, ['ONE'])
            if contents is not None: self.assertEqual(outside.read_text(), contents)

    def test_ownership_rejects_wrong_invalid_and_relocated_markers(self):
        output = self.root/'invalid'; root = output/'08_variants/glimpse2'; root.mkdir(parents=True)
        marker = root/'owner.json'
        for contents in ('not-json', '[]', '{}', json.dumps({'schema':'other'}),
                         json.dumps({'schema':pipeline.SCHEMA,'canonical_path':'/other/run'})):
            marker.write_text(contents)
            with self.subTest(contents=contents), self.assertRaises(OncoTracerError):
                pipeline._claim_output_root(output, ['ONE'])
            self.assertEqual(marker.read_text(), contents)

    def test_ownership_rejects_reserved_sample_and_report_symlinks(self):
        for relative in ('samples/ONE', 'launch/ONE', 'cohort', 'START_HERE.html'):
            output = self.root/relative.replace('/','_')
            _, root = pipeline._claim_output_root(output, ['ONE'])
            target = root/relative; target.parent.mkdir(parents=True, exist_ok=True)
            destination = self.root/(relative.replace('/','_')+'.foreign')
            if relative.endswith('.html'): destination.write_text('outside report')
            else: destination.mkdir()
            target.symlink_to(destination)
            with self.subTest(relative=relative), self.assertRaisesRegex(OncoTracerError, 'symlink|linked'):
                pipeline._claim_output_root(output, ['ONE'])
            self.assertTrue(target.is_symlink())

    def test_run_rejects_unowned_namespace_before_writing_status_or_running(self):
        output = self.root/'run'; root = output/'08_variants/glimpse2'; root.mkdir(parents=True)
        (root/'existing').write_text('preserve')
        ref = self.root/'ref.fa'; ref.write_text('>chr1\nA\n')
        with patch.object(pipeline, 'preflight', return_value={'nextflow':'nextflow'}), \
                patch.object(pipeline, 'ensure_resources') as resources, \
                self.assertRaisesRegex(OncoTracerError, 'nonempty, unowned'):
            pipeline.run(self.request, {'ONE':self.root/'one.bam'}, ref, output, None, None)
        resources.assert_not_called()
        self.assertFalse((output/'08_variants/variant_status.json').exists())

    def test_preflight_checks_native_dependencies_and_nextflow_without_recursion(self):
        with patch('oncotracer_cli.variants.preflight_variant_tools', return_value={'bcftools': '/fixture/bcftools'}) as native, \
             patch.object(pipeline.shutil, 'which', side_effect=lambda name: '/fixture/'+name):
            tools = pipeline.preflight(self.request)
        self.assertEqual(native.call_args.args[0].engine, 'native')
        self.assertEqual(tools['nextflow'], '/fixture/nextflow')
        with patch('oncotracer_cli.variants.preflight_variant_tools', return_value={}), \
             patch.object(pipeline.shutil, 'which', return_value=None), self.assertRaisesRegex(OncoTracerError, 'Nextflow'):
            pipeline.preflight(self.request)


@unittest.skipUnless(importlib.util.find_spec('pysam') and shutil.which('samtools') and shutil.which('bcftools'),
                     'Real import tests need pysam, samtools and bcftools')
class RealBamImportTests(unittest.TestCase):
    def setUp(self):
        import pysam
        self.pysam = pysam
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'inputs'; self.source.mkdir()
        self.work = self.root/'work'; self.work.mkdir()
        self.ref = self.source/'genome.fa'; self.ref.write_text('>chr1\n'+'A'*1000+'\n')
        pysam.faidx(str(self.ref))
        self.bam = self.source/'sample.bam'
        header = {'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'SN': 'chr1', 'LN': 1000}],
                  'RG': [{'ID': 'lib1', 'SM': 'ONE'}]}
        with pysam.AlignmentFile(str(self.bam), 'wb', header=header) as handle:
            read=pysam.AlignedSegment();read.query_name='read1';read.query_sequence='A'*50
            read.flag=0;read.reference_id=0;read.reference_start=20;read.mapping_quality=60
            read.cigar=[(0,50)];read.query_qualities=pysam.qualitystring_to_array('I'*50);read.set_tag('RG','lib1')
            handle.write(read)
        self.config = self.root/'runtime.json'
        self.config.write_text(json.dumps({'reference':str(self.ref),'sample':'ONE',
            'tools':{'samtools':shutil.which('samtools'),'bcftools':shutil.which('bcftools')},
            'min_mapping_quality':20,'min_base_quality':20,'test_mode':True}))
        self.script = pipeline.BUNDLE/'bin/import_alignment.py'

    def invoke(self, stage, path, *extra):
        return subprocess.run([sys.executable, str(self.script), stage, '--input', str(path), '--sample', 'ONE',
                               '--config', str(self.config), *extra], cwd=self.work, text=True, capture_output=True)

    def test_bam_import_validates_sample_and_indexes_without_modifying_source(self):
        before=digest(self.bam)
        result=self.invoke('bam', self.bam)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(digest(self.bam),before)
        self.assertTrue((self.work/'ONE.bam').is_symlink())
        self.assertTrue((self.work/'ONE.bam.bai').is_file())
        qc=json.loads((self.work/'ONE.qc.json').read_text())
        self.assertTrue(qc['alignment_reused'])
        self.assertTrue((self.work/'.resume.json').is_file())
        self.assertFalse(Path(str(self.bam)+'.bai').exists())

    def test_bam_mismatch_fails_before_outputs(self):
        with self.pysam.AlignmentFile(str(self.bam),'rb') as src:
            header=src.header.to_dict();reads=list(src)
        header['RG'][0]['SM']='OTHER'
        with self.pysam.AlignmentFile(str(self.bam),'wb',header=header) as dst:
            for read in reads:dst.write(read)
        result=self.invoke('bam',self.bam)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('BAM SM must equal',result.stderr)
        self.assertFalse((self.work/'ONE.bam').exists())

    def test_same_basename_staged_input_is_never_overwritten(self):
        source=self.source/'ONE.germline.vcf.gz'
        source.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tONE\n'
            'chr1\t50\t.\tA\tC\t20\tPASS\t.\tGT\t0/1\n')
        staged=self.work/source.name;staged.symlink_to(source)
        before=source.read_bytes();before_mtime=source.stat().st_mtime_ns
        result=self.invoke('calls',staged,'--kind','germline')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('must never be overwritten',result.stderr)
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual(source.stat().st_mtime_ns,before_mtime)
        self.assertTrue(staged.is_symlink())

    def test_normalized_call_import_keeps_original_filter_and_genotype(self):
        source=self.source/'original.vcf'
        source.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n'
            '##FILTER=<ID=LowEvidence,Description="Sparse support">\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tONE\n'
            'chr1\t50\t.\tA\tC\t20\tLowEvidence\t.\tGT:DP\t0/1:2\n')
        before=source.read_bytes()
        result=self.invoke('calls',source,'--kind','germline')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(source.read_bytes(),before)
        with self.pysam.VariantFile(str(self.work/'ONE.germline.vcf.gz')) as vcf:
            record=next(vcf)
            self.assertEqual(record.samples['ONE']['GT'],(0,1))
            self.assertEqual(record.samples['ONE']['DP'],2)
            self.assertEqual(list(record.filter),['LowEvidence'])
        self.assertTrue((self.work/'ONE.germline.vcf.gz.tbi').is_file())
        self.assertFalse(json.loads((self.work/'ONE.germline.import.json').read_text())['caller_equivalence_verified'])


if __name__=='__main__':
    unittest.main()
