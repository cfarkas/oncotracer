"""Moving a stopped sample into parallel scheduling must retain valid imputation."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BIN = Path(__file__).parents[1] / 'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import glimpse_continuation as reuse
import shared_reference
from continuation import metadata
import test_glimpse_shared_reference as shared_tests


class MultisampleReuseTests(unittest.TestCase):
    def fixture(self, root):
        c, code, refdonor, refwork, _ = shared_tests.SharedReferenceTests().fixture(root)
        stamp = time.time() - 600
        resources = Path(c['resource_dir'])
        for name in ('GLIMPSE2_split_reference', 'GLIMPSE2_phase', 'GLIMPSE2_ligate', 'SAMPLE.bam', 'SAMPLE.bam.bai'):
            (resources / name).write_bytes(b'controlled test fixture')
            os.utime(resources / name, (stamp, stamp))
        c.update(sample='SAMPLE', threads=4, memory=32, memory_gb=32,
                 min_mapping_quality=30, min_base_quality=20,
                 inputs=[metadata(resources/'SAMPLE.bam')])
        with patch.object(shared_reference, '_samples', return_value={'PANEL1', 'PANEL2'}), patch.object(shared_reference, '_output_contig'):
            shared = shared_reference.collect_shared_references(c, code, [refdonor])['references']
        self.assertEqual(set(shared), {'chr1'})
        donor = root/'sample-donor'
        phase = donor/'work/ab/phase'; phase.mkdir(parents=True)
        ligate = donor/'work/cd/ligate'; ligate.mkdir(parents=True)
        raw = json.dumps(c).encode(); runtime = hashlib.sha256(raw).hexdigest()+'.json'
        for work in (phase, ligate):
            (work/'bin').symlink_to(code/'bin', target_is_directory=True)
            (work/runtime).write_bytes(raw)
            (work/'.command.begin').write_text('')
            os.utime(work/'.command.begin', (stamp+50, stamp+50))
        for source in shared['chr1'][1:]:
            (phase/Path(source).name).symlink_to(source)
        for name in ('SAMPLE.bam', 'SAMPLE.bam.bai'):
            (phase/name).symlink_to(resources/name)
        (phase/'.command.sh').write_text('/usr/bin/python3 bin/glimpse2.py phase '+
            shlex.join(['--config', runtime, '--chrom', 'chr1', '--threads', '4',
                        '--chunk', '0', '--input-region', 'chr1:1-1000', '--output-region', 'chr1:1-1000',
                        '--reference', 'reference.chr1.bcf', '--map', 'map.chr1.txt', '--bam', 'SAMPLE.bam'])+'\n')
        for name in ('SAMPLE.chr1.0.bcf', 'SAMPLE.chr1.0.bcf.csi'):
            (phase/name).write_bytes(b'completed synthetic chunk')
            os.utime(phase/name, (stamp+60, stamp+60))
            (ligate/name).symlink_to(phase/name)
        (phase/'.exitcode').write_text('0'); os.utime(phase/'.exitcode', (stamp+70, stamp+70))
        os.utime(ligate/'.command.begin', (stamp+80, stamp+80))
        (ligate/'.command.sh').write_text('/usr/bin/python3 bin/glimpse2.py ligate '+
            shlex.join(['--config', runtime, '--chrom', 'chr1', '--threads', '4', '--inputs', 'SAMPLE.chr1.0.bcf'])+'\n')
        for name in ('SAMPLE.chr1.imputed.bcf', 'SAMPLE.chr1.imputed.bcf.csi', 'chunks.list'):
            (ligate/name).write_text('SAMPLE.chr1.0.bcf\n' if name=='chunks.list' else 'completed synthetic ligation')
            os.utime(ligate/name, (stamp+90, stamp+90))
        (ligate/'.exitcode').write_text('0'); os.utime(ligate/'.exitcode', (stamp+100, stamp+100))
        current=dict(c, threads=16, memory=64, memory_gb=64, output=str(root/'new-output'))
        core={'merge:SAMPLE':[str(resources/'SAMPLE.bam'),str(resources/'SAMPLE.bam.bai')]}
        return current, code, donor, core, shared, phase, ligate

    def collect(self, fixture):
        c, code, donor, core, shared, _, _ = fixture
        return reuse.collect(c, code, donor, core, reference_reused=shared)

    def test_completed_chunks_and_ligation_survive_larger_scheduler_budget(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(Path(d))
            before={p:p.stat().st_mtime_ns for p in fixture[2].rglob('*') if p.is_file()}
            result=self.collect(fixture)
            self.assertEqual({k:len(v) for k,v in result.items()}, {'references':1,'phases':1,'ligated':1})
            self.assertEqual(before,{p:p.stat().st_mtime_ns for p in before})
            # No reference preparation task exists in this sample's work tree.
            self.assertFalse(list(fixture[2].rglob('reference.*.provenance.json')))

    def test_sample_specific_chunks_never_cross_individuals(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(Path(d)); fixture[0]['sample']='OTHER'
            result=self.collect(fixture)
            self.assertFalse(result['phases']); self.assertFalse(result['ligated'])

    def test_actual_glimpse_thread_count_change_requires_recomputation(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(Path(d)); fixture[0]['threads']=2
            result=self.collect(fixture)
            self.assertFalse(result['phases']); self.assertFalse(result['ligated'])

    def test_read_quality_change_requires_recomputation(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(Path(d)); fixture[0]['min_mapping_quality']=20
            self.assertFalse(self.collect(fixture)['phases'])

    def test_failed_or_unfinished_chunks_not_reused(self):
        for exitcode in ('1', None):
            with self.subTest(exitcode=exitcode), tempfile.TemporaryDirectory() as d:
                fixture=self.fixture(Path(d)); end=fixture[5]/'.exitcode'
                if exitcode is None: end.unlink()
                else: end.write_text(exitcode)
                result=self.collect(fixture)
                self.assertFalse(result['phases']); self.assertFalse(result['ligated'])

    def test_changed_bam_fingerprint_rejects_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(Path(d)); bam=Path(fixture[3]['merge:SAMPLE'][0]);bam.write_bytes(b'changed')
            result=self.collect(fixture)
            self.assertFalse(result['phases']); self.assertFalse(result['references'])

    def test_different_staged_reference_or_bam_not_reused(self):
        for name in ('reference.chr1.bcf','SAMPLE.bam'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                fixture=self.fixture(Path(d)); staged=fixture[5]/name
                other=Path(d)/'different-input';other.write_bytes(staged.read_bytes())
                os.utime(other,(time.time()-600,)*2);staged.unlink();staged.symlink_to(other)
                self.assertFalse(self.collect(fixture)['phases'])

    def test_downstream_trait_resource_relocation_does_not_invalidate_imputation(self):
        old={'threads':4,'memory':32,'memory_gb':32,'traits_panel':'/old/traits.tsv','gwas_catalog':'/old/catalog.tsv',
             'resources':[{'path':'/old/traits.tsv'},{'path':'/old/catalog.tsv'},{'path':'/ref/genome.fa'}],
             'sample':'SAMPLE','reference':'/ref/genome.fa','min_mapping_quality':30}
        new=dict(old,threads=16,memory=64,memory_gb=64,traits_panel='/new/traits.tsv',gwas_catalog='/new/catalog.tsv',
                 resources=[{'path':'/new/traits.tsv'},{'path':'/new/catalog.tsv'},{'path':'/ref/genome.fa'}])
        self.assertEqual(reuse.analysis_config(old),reuse.analysis_config(new))
        self.assertNotEqual(reuse.analysis_config(old),reuse.analysis_config(dict(new,min_mapping_quality=20)))


if __name__ == '__main__': unittest.main()
