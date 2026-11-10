"""Scientific gates and failure retention for automatic coarse CNA rescue."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from oncotracer_cli.ichor_rescue import publish_rescue, rescue_summary
from oncotracer_cli.runtime import OncoTracerError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_ichor_fallback',ROOT/'bin/scripts/native_ichor_fallback.py')
qc=importlib.util.module_from_spec(spec);spec.loader.exec_module(qc)

class EvidenceTests(unittest.TestCase):
    def test_short_high_amplitude_region_is_not_a_broad_rescue(self):
        self.assertFalse(qc.support([1]*19,19,1,9500000)['retained'])
    def test_low_amplitude_native_hmm_call_is_not_accepted(self):
        self.assertEqual(qc.support([.05]*40,40,1,20000000)['reason'],'below_relative_log2_threshold')
    def test_odd_even_disagreement_is_rejected(self):
        self.assertFalse(qc.support([.9,-.3]*20,40,1,20000000)['retained'])
    def test_supported_broad_region_is_deterministic(self):
        a=qc.support([.25,.35,.4,.38,.29]*8,40,1,20000000)
        self.assertTrue(a['retained']);self.assertEqual(a,qc.support([.25,.35,.4,.38,.29]*8,40,1,20000000))
    def test_missing_bins_reject_apparent_broad_event(self):
        self.assertFalse(qc.support([.4]*20,40,1,20000000)['retained'])
    def test_second_resolution_must_agree_in_sign_and_amplitude(self):
        positions=[(i*1000000+1,(i+1)*1000000,-.4) for i in range(20)]
        self.assertFalse(qc.sensitivity(positions,1,20000000,1)['second_passed'])
        self.assertTrue(qc.sensitivity(positions,1,20000000,-1)['second_passed'])
    def test_second_resolution_requires_wholly_contained_bins(self):
        bins=[(i*1000000+1,(i+1)*1000000,.4) for i in range(10)]
        evidence=qc.sensitivity(bins,500001,8500000,1)
        self.assertEqual(evidence['second_finite_bins'],7);self.assertFalse(evidence['second_passed'])
    def test_noisy_profile_is_not_a_rescue(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'depth.tsv';p.write_text('chr\tstart\tend\tlog2_TNratio_corrected\n'+''.join(f'chr1\t{i*500000+1}\t{(i+1)*500000}\t{(-1)**i}\n' for i in range(50)))
            _,_,metrics=qc.read_depth(p);self.assertFalse(metrics['passed'])
    def test_adjacent_events_merge_only_when_direction_matches(self):
        events=[dict(chrom='1',state='gain',start=1,end=10000000),dict(chrom='1',state='gain',start=10000001,end=20000000),dict(chrom='1',state='loss',start=20000001,end=30000000)]
        merged=qc.merge_events(events,{'chr1':[(1,10000000,.3),(10000001,20000000,.3),(20000001,30000000,-.3)]})
        self.assertEqual(len(merged),2);self.assertEqual(merged[0]['native_segments'],2)
    def test_corrupted_native_result_invalidates_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'event.tsv').write_text('original')
            receipt=dict(fingerprint={'bam':'A'},result={'status':'assessable_coarse_relative_profile'},outputs={'event.tsv':qc.sha(p/'event.tsv')})
            qc.dump(p/'receipt.json',receipt)
            self.assertIsNotNone(qc.verified_receipt(p/'receipt.json',{'bam':'A'}))
            self.assertIsNone(qc.verified_receipt(p/'receipt.json',{'bam':'B'}))
            (p/'event.tsv').write_text('tampered')
            self.assertIsNone(qc.verified_receipt(p/'receipt.json',{'bam':'A'}))
    def test_labels_do_not_change_the_scientific_policy(self):
        self.assertNotIn('sample_role',qc.INPUT_POLICY);self.assertNotIn('pathology',qc.POLICY)
    def test_manifest_rejects_duplicate_and_traversing_samples(self):
        for rows in [[dict(sample='../sample',primary_assessment='not_assessable',bam='a')],[dict(sample='S',primary_assessment='assessable')]*2]:
            with self.assertRaises(ValueError):qc.validate_manifest(rows)

class IntegrationTests(unittest.TestCase):
    def test_zero_events_with_passed_primary_never_launches_rescue(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            class Runner:
                def run(self,*a,**k):raise AssertionError('A passing primary must not run ichorCNA')
            data=publish_rescue(ROOT,{},dict(samples=[dict(sample='FFPE',assessment='assessable',accepted_events=0)]),root/'missing.csv',root,Runner(),None)
            self.assertEqual(data['samples'][0]['status'],'primary_retained')
            self.assertEqual(rescue_summary(data)['cna_rescued_samples'],[])
    def test_missing_failed_bam_is_explicit_without_losing_primary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);assessment=dict(samples=[dict(sample='P',assessment='assessable'),dict(sample='F',assessment='not_assessable')])
            data=publish_rescue(ROOT,{},assessment,root/'missing.csv',root,None,None)
            self.assertEqual([r['status'] for r in data['samples']],['primary_retained','failed'])
            self.assertEqual(assessment['samples'][1]['assessment'],'not_assessable')
    def test_partial_helper_failure_retains_successful_rescue(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=root/'bams.csv';manifest.write_text('sample,bam,status\nA,/tmp/a.bam,tumor\nB,/tmp/b.bam,normal\n')
            class Tools:
                ichorcna_prefix=None
                def executable(self,g,n):return '/usr/bin/'+n
                def wrap(self,g,c):return c
                def environment(self,g):return {}
            class Runner:
                def run(self,*a,**k):
                    qc.dump(root/'08_ichorcna_fallback/summary.json',dict(samples=[dict(sample='A',status='assessable_coarse_relative_profile'),dict(sample='B',status='failed')]))
                    raise OncoTracerError('one fit failed')
            data=publish_rescue(ROOT,{},dict(samples=[dict(sample=s,assessment='not_assessable') for s in ['A','B']]),manifest,root,Runner(),Tools())
            self.assertEqual(rescue_summary(data)['cna_rescued_samples'],['A'])

if __name__=='__main__':unittest.main()
