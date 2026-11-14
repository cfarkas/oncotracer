"""Current scientific audit rejects false completion and regenerated wrong calls."""
import csv
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.test_compare_native_parity import write_run,PROFILE_RELATIVE
from tests import native_science_audit as audit


class ScientificAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.commit='a'*40;self.source='b'*64;self.binary='c'*64
        for side in ('baseline','candidate'):
            folder=self.root/side;folder.mkdir()
            audit.write(folder/'provenance.json',dict(source_commit=audit.BASELINE if side=='baseline' else self.commit,
                       source_sha256=audit.BASELINE_SOURCE if side=='baseline' else self.source,oncotracer_version='3.0.0' if side=='baseline' else '4.1.0',
                       source_tree_dirty=False,binary_sha256='d'*64 if side=='baseline' else self.binary))
            for dataset,names in audit.SAMPLES['quickstart1'].items():
                root=folder/dataset;write_run(root,native=True)
                for p in root.rglob('*.tsv'):p.write_text(p.read_text().replace('S1',names[0]))
                path=root/PROFILE_RELATIVE
                with gzip.open(path,'rt') as handle:content=handle.read().replace('S1',names[0])
                with gzip.open(path,'wt') as handle:handle.write(content)
                audit.write(root/'06_workflow_summary/workflow_summary.json',dict(engine='native',nextflow_used=False,input_samples=names,workflow_status='complete' if dataset=='illumina' else 'partial_failure'))
                audit.write(root/'06_workflow_summary/cna_assessment.json',dict(samples=[dict(sample=names[0],assessment='assessable' if dataset=='illumina' else 'not_assessable')]))
                (root/'.oncotracer-native/trace.tsv').write_text('stage\tcommand\treturncode\nrun\tRscript native.R\t0\n')
                if dataset=='ont':(root/'03_cna_codification/cna_events.tsv').write_text('sample\tstate\tchrom\tstart\tend\tmean_log2\n')
        audit.write(self.root/'inputs.json',[dict(path='public.fastq.gz',bytes=100,sha256='e'*64)])
        self.record=dict(schema=audit.SCHEMA,suite='quickstart1',input_manifest_sha256=audit.digest(self.root/'inputs.json'),datasets={d:{'baseline_exit_code':0 if d=='illumina' else 2,'candidate_exit_code':0 if d=='illumina' else 2} for d in ('illumina','ont')})
        self.seal()

    def seal(self):
        self.record['files']={str(p.relative_to(self.root)):audit.digest(p) for p in self.root.rglob('*') if p.is_file() and p.relative_to(self.root).parts[0] not in {'native_science.json','comparisons','audit_summary.json','SHA256SUMS'}}
        audit.write(self.root/'native_science.json',self.record)

    def verify(self,create=True):return audit.verify(self.root,'quickstart1',self.commit,self.source,self.binary,write_summary=create)

    def test_complete_and_quality_limited_data_verify_without_rewriting_evidence(self):
        self.assertTrue(self.verify()['passed'])
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertTrue(self.verify(False)['passed'])
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_resealed_changed_event_is_rejected_independently_of_profile_correlation(self):
        path=self.root/'candidate/illumina/03_cna_codification/cna_events.tsv';path.write_text(path.read_text().replace('0.5','0.6'));self.seal()
        with self.assertRaisesRegex(ValueError,'events changed'):self.verify()

    def test_resealed_qc_promotion_and_subprocess_failure_are_rejected(self):
        p=self.root/'candidate/ont/06_workflow_summary/cna_assessment.json';original=p.read_bytes()
        data=audit.read(p);data['samples'][0]['assessment']='assessable';audit.write(p,data);self.seal()
        with self.assertRaisesRegex(ValueError,'promoted'):self.verify()
        p.write_bytes(original)
        trace=self.root/'candidate/ont/.oncotracer-native/trace.tsv';trace.write_text('stage\tcommand\treturncode\nrun\tRscript\t1\n');self.seal()
        with self.assertRaisesRegex(ValueError,'Failed scientific command'):self.verify()

    def test_missing_inventory_entry_and_wrong_baseline_identity_are_rejected(self):
        self.record['files'].pop('baseline/provenance.json');audit.write(self.root/'native_science.json',self.record)
        with self.assertRaisesRegex(ValueError,'inventory'):self.verify()
        self.seal();path=self.root/'baseline/provenance.json';value=audit.read(path);value['source_commit']='0'*40;audit.write(path,value);self.seal()
        with self.assertRaisesRegex(ValueError,'provenance mismatch'):self.verify()
