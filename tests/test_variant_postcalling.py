"""Post-calling boundaries, integrity and genome-wide correction regressions."""
import csv
from dataclasses import replace
import gzip
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import pysam
from oncotracer_cli import variants, variant_postcalling as post
from oncotracer_cli.runtime import OncoTracerError, CommandRunner
from tests.test_haplotype_evidence import BamTests

WORKERS=Path(__file__).resolve().parents[1]/'oncotracer_cli/lpwgs/bin'
with patch.object(sys,'path',[str(WORKERS),*sys.path]):
    genome=importlib.import_module('haplotype_genome')
    quilt=importlib.import_module('quilt2_worker')


class CorrectionTests(unittest.TestCase):
    def test_dense_windows_split_without_losing_or_duplicating_pairs(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE variants(id INTEGER PRIMARY KEY,chrom TEXT,pos INTEGER,eligible INTEGER,data TEXT)')
        for pos in (99990,99995,100001,100006):
            db.execute('INSERT INTO variants(chrom,pos,eligible,data) VALUES(?,?,1,?)',('chr1',pos,json.dumps({'pos':pos})))
        pairs=[]
        for left,right in ((1,100001),(100001,200001)):
            for ids,variants,batch in genome.bounded_windows(db,'chr1',left,right,5000,3):
                self.assertLessEqual(len(batch),3)
                pairs.extend((ids[i],ids[j]) for i,j in batch)
        self.assertEqual(len(pairs),6)
        self.assertEqual(len(set(pairs)),6)
        db.close()

    def test_disk_correction_matches_global_by_including_ties(self):
        h=genome.h
        rows=[{'exact_linkage_p':p} for p in (.7,.001,.04,.04,.0002)]
        h.adjust_by(rows)
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE pairs(id INTEGER PRIMARY KEY,p REAL,q REAL)')
        db.executemany('INSERT INTO pairs(p) VALUES(?)',((r['exact_linkage_p'],) for r in rows))
        db.execute('INSERT INTO pairs(p) VALUES(NULL)')
        genome.adjust_database(db)
        qs=[r[0] for r in db.execute('SELECT q FROM pairs ORDER BY id')]
        for row,q in zip(rows,qs):self.assertAlmostEqual(row['linkage_q_by'],q,places=12)
        self.assertIsNone(qs[-1]);db.close()

    def test_options_do_not_change_default_checkpoint_contract(self):
        request=variants.VariantRequest('ont','fresh',('clair3',))
        self.assertNotIn('monte_carlo_linkage',request.as_dict())
        self.assertNotIn('ont_imputation',request.as_dict())
        enabled=replace(request,monte_carlo_linkage=True,ont_imputation='quilt2')
        self.assertTrue(enabled.as_dict()['monte_carlo_linkage'])
        self.assertIn('quilt2-diploid-snp-imputation',variants.variant_plan(enabled)['stages'])


class QuiltOutputTests(unittest.TestCase):
    def test_probabilities_and_sample_identity_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'S1.quilt2.vcf'
            header='##fileformat=VCFv4.2\n##contig=<ID=chr20,length=1000>\n'
            header+='##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n'
            header+='##FORMAT=<ID=GP,Number=G,Type=Float,Description="GP">\n'
            header+='##FORMAT=<ID=DS,Number=1,Type=Float,Description="DS">\n'
            header+='#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n'
            p.write_text(header+'chr20\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP:DS\t0|1:0.1,0.8,0.1:1\n')
            self.assertEqual(quilt.validate_vcf(p,'S1')['imputed_genotypes'],1)
            with self.assertRaisesRegex(ValueError,'identity'):quilt.validate_vcf(p,'S2')
            p.write_text(p.read_text().replace('0.1,0.8,0.1','0.1,0.2,0.1'))
            with self.assertRaisesRegex(ValueError,'GP'):quilt.validate_vcf(p,'S1')

    def test_corrupted_intermediate_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);artifact=root/'prepared.RData';artifact.write_bytes(b'complete panel')
            receipt=root/'complete.json';quilt.atomic(receipt,dict(signature='s',files={artifact.name:quilt.sha(artifact)}))
            self.assertTrue(quilt.valid_receipt(receipt,'s'))
            artifact.write_bytes(b'interrupted')
            self.assertFalse(quilt.valid_receipt(receipt,'s'))
            self.assertFalse(quilt.valid_receipt(receipt,'new-input'))


class PipelineLinkageTests(unittest.TestCase):
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

    tearDown=BamTests.tearDown
    read=BamTests.read
    bam=BamTests.bam
    def test_integrated_linkage_reuses_only_verified_reports(self):
        bam=self.bam([self.read(f'r{i}',i%2==1,16 if i%4>=2 else 0) for i in range(20)])
        request=variants.VariantRequest('ont','fresh',('clair3',),monte_carlo_linkage=True)
        runner=CommandRunner(self.root/'commands.tsv')
        with patch.object(post,'core_python',return_value=sys.executable):
            first=post.linkage(request,'S1',bam,{'clair3':self.vcf},self.ref,self.root/'linkage',runner,1)
            # Simulate termination after worker publication, before the parent receipt.
            (self.root/'linkage/S1/clair3/S1.linkage.complete.json').unlink()
            second=post.linkage(request,'S1',bam,{'clair3':self.vcf},self.ref,self.root/'linkage',runner,1)
        self.assertEqual(first['clair3']['pairs'],1)
        self.assertTrue(second['clair3']['resumed'])
        self.assertIsNone(first['clair3']['germline_probability'])
        self.assertEqual(first['clair3']['statistics_status'],'complete')

    def test_interrupted_owned_stage_is_preserved_then_recomputed(self):
        bam=self.bam([self.read(f'r{i}',i%2==1) for i in range(20)])
        request=variants.VariantRequest('ont','fresh',('clair3',),monte_carlo_linkage=True)
        runner=CommandRunner(self.root/'commands.tsv')
        with patch.object(post,'core_python',return_value=sys.executable):
            post.linkage(request,'S1',bam,{'clair3':self.vcf},self.ref,self.root/'linkage',runner,1)
            dest=self.root/'linkage/S1/clair3'
            (dest/'S1.linkage.complete.json').unlink()
            (dest/'S1.manifest.json').unlink()
            result=post.linkage(request,'S1',bam,{'clair3':self.vcf},self.ref,self.root/'linkage',runner,1)
        self.assertEqual(result['clair3']['status'],'complete')
        self.assertEqual(len(list(dest.parent.glob('clair3.superseded.*'))),1)

    def test_window_boundary_counts_pair_once_and_keeps_unassessable_variants(self):
        self.ref.write_text('>chr1\n'+'A'*200000+'\n')
        (self.root/'reference.fa.fai').unlink();pysam.faidx(str(self.ref))
        self.header['SQ'][0]['LN']=200000
        self.vcf.write_text(self.vcf.read_text().replace('length=1000','length=200000').replace('\t101\t','\t99991\t').replace('\t121\t','\t100011\t')+
                           'chr1\t180001\t.\tA\tC\t60\tPASS\t.\tGT\t0/1\n')
        reads=[self.read(f'r{i}',i%2==1) for i in range(20)]
        for read in reads:read.reference_start=99970
        bam=self.bam(reads)
        request=variants.VariantRequest('ont','fresh',('clair3',),monte_carlo_linkage=True)
        runner=CommandRunner(self.root/'commands.tsv')
        with patch.object(post,'core_python',return_value=sys.executable):
            result=post.linkage(request,'S1',bam,{'clair3':self.vcf},self.ref,self.root/'linkage',runner,1)
        self.assertEqual(result['clair3']['input_records'],3)
        self.assertEqual(result['clair3']['pairs'],1)
        with gzip.open(self.root/'linkage/S1/clair3/S1.variant_assessability.tsv.gz','rt') as stream:
            rows=list(csv.DictReader(stream,delimiter='\t'))
        self.assertEqual(rows[-1]['status'],'no_linkable_variant_in_distance')
