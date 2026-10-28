"""Statistical, molecule-level and annotation-boundary regressions."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import urllib.error

import pysam
from oncotracer_cli.lpwgs.bin import haplotype_evidence as h
from oncotracer_cli.lpwgs.bin import haplotype_annotations as a

ROOT = Path(__file__).resolve().parents[1]


class StatisticsTests(unittest.TestCase):
    def test_exact_tail_and_monte_carlo_agree_and_are_reproducible(self):
        result = h.linkage_test([[5,0,0,5]],100000,17)
        self.assertAlmostEqual(result['exact_linkage_p'],1/math.comb(10,5),places=12)
        self.assertLess(abs(result['mc_linkage_p']-result['exact_linkage_p']),.001)
        self.assertEqual(result,h.linkage_test([[5,0,0,5]],100000,17))
        self.assertLess(result['mc_ci95_low'],result['exact_linkage_p'])
        self.assertGreater(result['mc_ci95_high'],result['exact_linkage_p'])

    def test_strand_confounding_is_not_linkage(self):
        result = h.linkage_test([[5,0,0,0],[0,0,0,5]],10000,17)
        self.assertEqual(result['exact_linkage_p'],1)
        self.assertEqual(result['mc_linkage_p'],1)

    def test_stratified_exact_tail_and_no_zero_mc_p(self):
        result = h.linkage_test([[3,0,0,3],[3,0,0,3]],100000,9)
        self.assertAlmostEqual(result['exact_linkage_p'],1/400,places=12)
        self.assertGreater(h.linkage_test([[100,0,0,100]],1000,9)['mc_linkage_p'],0)

    def test_dependent_multiple_testing_adjustment(self):
        rows = [dict(exact_linkage_p=p) for p in (.001,.01,.7)]
        h.adjust_by(rows)
        self.assertAlmostEqual(rows[0]['linkage_q_by'],.001*3*(1+1/2+1/3))
        self.assertTrue(all(r['linkage_q_by'] >= r['exact_linkage_p'] for r in rows))

    def test_one_alt_fragment_cannot_establish_germline_or_linkage(self):
        variants = [dict(variant=f'1:{p}:C:T',chrom='1',pos=p,ref='C',alt='T',phased=False,phase_set='',gt=[0,1]) for p in (100,120)]
        result = h.evaluate(variants,[(0,1)],{(0,1):[[0,0,0,1]]},simulations=1000,seed=17,min_fragments=6,ffpe=True)[0]
        self.assertEqual(result['status'],'insufficient_evidence')
        self.assertIsNone(result['germline_probability'])
        self.assertIsNone(result['exact_linkage_p'])
        self.assertTrue(result['ffpe_damage_context'])

    def test_no_reference_alleles_is_unassessable(self):
        variants = [dict(variant=str(p),chrom='1',pos=p,ref='C',alt='T',phased=False,phase_set='',gt=[0,1]) for p in (100,120)]
        result = h.evaluate(variants,[(0,1)],{(0,1):[[0,0,0,20]]},simulations=1000,seed=17,min_fragments=6,ffpe=False)[0]
        self.assertEqual(result['status'],'insufficient_evidence')


class BamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ref = self.root/'reference.fa'
        self.ref.write_text('>chr1\n'+'A'*1000+'\n')
        pysam.faidx(str(self.ref))
        self.header = dict(HD={'VN':'1.6','SO':'coordinate'},SQ=[{'SN':'chr1','LN':1000}],RG=[{'ID':'library','SM':'S1'}])
        self.vcf = self.root/'calls.vcf'
        self.vcf.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n'
            'chr1\t101\t.\tA\tC\t60\tPASS\t.\tGT\t0/1\n'
            'chr1\t121\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n')

    def tearDown(self):
        self.temp.cleanup()

    def read(self,name,alt=False,flag=0,mapq=60,quality=40):
        r = pysam.AlignedSegment(pysam.AlignmentHeader.from_dict(self.header))
        r.query_name=name; r.flag=flag; r.reference_id=0; r.reference_start=80
        r.mapping_quality=mapq; r.cigarstring='80M'
        sequence=list('A'*80)
        if alt: sequence[20]='C'; sequence[40]='G'
        r.query_sequence=''.join(sequence);r.query_qualities=[quality]*80;r.set_tag('RG','library')
        if r.is_paired:
            r.next_reference_id=0;r.next_reference_start=80;r.template_length=80 if r.is_read1 else -80
        return r

    def bam(self,reads):
        path=self.root/'reads.bam'
        with pysam.AlignmentFile(str(path),'wb',header=self.header) as output:
            for r in sorted(reads,key=lambda r:r.reference_start):output.write(r)
        pysam.index(str(path))
        return path

    def assess(self,path):
        with pysam.AlignmentFile(str(path),'rb') as bam,pysam.FastaFile(str(self.ref)) as fasta:
            variants,audit=h.load_variants(self.vcf,'S1',dict(zip(bam.references,bam.lengths)),fasta)
            pairs=h.candidate_pairs(variants,5000,100)
            return h.fragment_tables(bam,variants,pairs,30,20)

    def test_overlapping_mates_count_once_and_bad_reads_are_excluded(self):
        reads=[self.read('pair',True,99),self.read('pair',True,147),self.read('reference')]
        reads += [self.read('dup',True,1024),self.read('secondary',True,256),self.read('supp',True,2048),
                  self.read('qc',True,512),self.read('lowmap',True,mapq=10),self.read('unknownmap',True,mapq=255),
                  self.read('lowbase',True,quality=10)]
        tables=self.assess(self.bam(reads))
        counts=[sum(s[i] for s in tables[(0,1)]) for i in range(4)]
        self.assertEqual(counts,[1,0,0,1])

    def test_conflicting_mates_do_not_manufacture_a_haplotype(self):
        tables=self.assess(self.bam([self.read('pair',True,99),self.read('pair',False,147)]))
        self.assertEqual(tables[(0,1)],[])

    def test_reference_mismatch_and_wrong_sample_fail(self):
        with pysam.FastaFile(str(self.ref)) as fasta:
            with self.assertRaisesRegex(ValueError,'sample'):
                h.load_variants(self.vcf,'OTHER',{'chr1':1000},fasta)
            self.vcf.write_text(self.vcf.read_text().replace('\tA\tC\t','\tT\tC\t'))
            with self.assertRaisesRegex(ValueError,'REF'):
                h.load_variants(self.vcf,'S1',{'chr1':1000},fasta)

    def test_full_command_writes_named_reports_without_modifying_inputs(self):
        bam=self.bam([self.read(f'r{i}',i%2==1,16 if i%4>=2 else 0) for i in range(20)])
        out=self.root/'report'
        before=[h.fingerprint(p,True) for p in (bam,self.vcf,self.ref)]
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
        env.pop('ONCOTRACER_CORE_PREFIX',None)
        command=[sys.executable,'-B',str(ROOT/'oncotracer'),'haplotype-evidence','--backend','host',
                 '--bam',str(bam),'--vcf',str(self.vcf),'--reference',str(self.ref),'--sample','S1',
                 '--outdir',str(out),'--simulations','1000']
        p=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        result=json.loads((out/'S1.haplotype_evidence.json').read_text())
        self.assertIsNone(result['germline_probability'])
        self.assertEqual(result['online_annotation_status'],'offline_links_only')
        self.assertEqual(result['pairs'],1)
        self.assertEqual(before,[h.fingerprint(p,True) for p in (bam,self.vcf,self.ref)])
        self.assertTrue(all(p.name.startswith('S1.') for p in out.iterdir()))
        # Repeating cannot overwrite a completed assessment.
        repeated=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertNotEqual(repeated.returncode,0)

    def test_non_pass_and_indels_remain_explicitly_unassessed(self):
        self.vcf.write_text(self.vcf.read_text().replace('##FORMAT=','##FILTER=<ID=LowQual,Description="Low quality">\n##FORMAT=').replace('\tA\tC\t60\tPASS','\tA\tC\t60\tLowQual').replace('\tA\tG\t','\tA\tAG\t'))
        with pysam.FastaFile(str(self.ref)) as fasta:
            variants,audit=h.load_variants(self.vcf,'S1',{'chr1':1000},fasta)
        self.assertEqual(variants,[])
        self.assertEqual([r['status'] for r in audit],['not_assessed_filter_not_pass','not_assessed_non_snv'])


class AnnotationTests(unittest.TestCase):
    variant=dict(variant='chr7:140453136:A:T',chrom='chr7',pos=140453136,ref='A',alt='T')

    def test_only_coordinates_leave_machine_and_token_not_in_url(self):
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"chromosome":"7","pos":140453136,"ref":"A","alt":"T","clinvar":{"classification":"Pathogenic"}}'
        opener=mock.Mock();opener.open.return_value=response
        result=a.query_variant(self.variant,'hg19',token='test-secret',opener=opener)
        request=opener.open.call_args.args[0]
        self.assertNotIn('test-secret',request.full_url)
        self.assertEqual(request.headers['Authorization'],'Token test-secret')
        self.assertIsNone(request.data)
        self.assertNotIn('test-secret',json.dumps(result))
        self.assertIn('retrieved_at',result)

    def test_redirects_and_invalid_payloads_are_rejected(self):
        with self.assertRaises(ValueError):a.NoRedirect().redirect_request(None,None,None,None,None,None)
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"error":"quota"}'
        opener=mock.Mock();opener.open.return_value=response
        with self.assertRaises(ValueError):a.query_variant(self.variant,'hg19',opener=opener)
        response.__enter__.return_value.read.return_value=b'{"chromosome":"7","pos":1,"ref":"A","alt":"T"}'
        with self.assertRaisesRegex(ValueError,'coordinates'):a.query_variant(self.variant,'hg19',opener=opener)

    def test_offline_makes_no_requests_and_429_stops_queries(self):
        variants=[{**self.variant,'variant':str(i),'pos':140453136+i} for i in range(3)]
        with tempfile.TemporaryDirectory() as d,mock.patch.object(a,'query_variant') as lookup:
            data=a.annotate(variants,[],Path(d),'hg19',online=False,limit=2)
            lookup.assert_not_called();self.assertEqual(data['status'],'offline_links_only')
            lookup.side_effect=urllib.error.HTTPError('https://api.varsome.com',429,'quota',{},None)
            data=a.annotate(variants,[],Path(d),'hg19',online=True,limit=2)
            self.assertEqual(lookup.call_count,1);self.assertEqual(data['status'],'partial')

    def test_llm_cannot_invent_probability_or_annotations(self):
        facts=[{'id':'annotation_0001'}]
        self.assertEqual(a.validate_selection('{"selected_annotation_ids":["annotation_0001"]}',facts),['annotation_0001'])
        for value in ('{"selected_annotation_ids":["invented"]}',
                      '{"selected_annotation_ids":[],"germline_probability":0.99}',
                      'This variant is definitely inherited.',
                      '{"selected_annotation_ids":["annotation_0001","annotation_0001"]}'):
            with self.subTest(value=value),self.assertRaises(ValueError):a.validate_selection(value,facts)

    def test_annotation_facts_preserve_exact_values_and_sources(self):
        facts=a.annotation_facts({'clinvar':{'classification':'Likely pathogenic'},'secret_irrelevant':'omit'},'v','https://api.varsome.com/test')
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['value'],'Likely pathogenic')
        self.assertEqual(facts[0]['field'],'clinvar.classification')


if __name__=='__main__':unittest.main()
