"""Shared references require intact provenance; phases are never borrowed."""
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BIN = Path(__file__).parents[1]/'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import shared_reference as shared
from continuation import metadata


class SharedReferenceTests(unittest.TestCase):
    def fixture(self, root):
        stamp = time.time() - 600
        code = root/'code'; (code/'bin').mkdir(parents=True)
        shutil.copy2(BIN/'glimpse2.py', code/'bin/glimpse2.py')
        resources = root/'resources'; resources.mkdir()
        fasta = resources/'genome.fa'; fasta.write_text('>chr1\n'+'A'*1000+'\n')
        Path(str(fasta)+'.fai').write_text('chr1\t1000\t6\t1000\t1001\n')
        for name in ('bcftools', 'GLIMPSE2_chunk'):
            (resources/name).write_text('fixture binary '+name)
        panel = resources/'chr1.vcf.gz'
        with gzip.open(panel, 'wt') as out: out.write('valid compressed synthetic panel')
        mapfile = resources/'chr1.gmap'; mapfile.write_text('pos chr cM\n1 chr1 0\n1000 chr1 1\n')
        for p in resources.iterdir(): os.utime(p, (stamp, stamp))
        donor = root/'donor'; work = donor/'work/aa/bbbb'; work.mkdir(parents=True)
        (work/'bin').symlink_to(code/'bin', target_is_directory=True)
        old = {'glimpse2': True, 'sample': 'OLD', 'reference': str(fasta), 'resource_dir': str(resources),
               'glimpse_chromosomes': ['chr1'], 'glimpse_bin': str(resources),
               'glimpse_reference_dir': str(resources), 'allow_test_reference': False,
               'versions': {'bcftools': 'fixture1'}, 'tools': {'bcftools': str(resources/'bcftools')},
               'resources': [metadata(fasta)], 'inputs': [], 'root': str(code)}
        raw = json.dumps(old).encode(); runtime = hashlib.sha256(raw).hexdigest()+'.json'
        (work/runtime).write_bytes(raw)
        (work/'.command.sh').write_text(f'#!/bin/bash\n/usr/bin/python3 bin/glimpse2.py reference --config {runtime} --chrom chr1 --threads 1\n')
        (work/'.command.begin').write_text(''); os.utime(work/'.command.begin', (stamp+10, stamp+10))
        (work/'chunks.chr1.tsv').write_text('0\tchr1\tchr1:1-1000\tchr1:1-1000\n')
        (work/'reference.chr1.bcf').write_bytes(b'BCFfixture')
        (work/'reference.chr1.bcf.csi').write_bytes(b'CSIfixture')
        (work/'map.chr1.txt').write_text(mapfile.read_text())
        proof = {'assembly': 'GRCh38', 'version': 'v2.0.0', 'variants': 'biallelic SNPs',
                 'panel': str(panel), 'panel_url': None, 'reference_fasta': str(fasta),
                 'genetic_map': str(mapfile), 'source_contig': 'chr1', 'fasta_contig_length': 1000,
                 'panel_integrity': {'bytes': panel.stat().st_size, 'sha256': shared.digest(panel),
                                     'integrity_version': 2, 'gzip_crc_validated': True}}
        (work/'reference.chr1.provenance.json').write_text(json.dumps(proof))
        for p in work.iterdir():
            if p.is_file() and p.name != '.command.begin': os.utime(p, (stamp+20, stamp+20))
        (work/'.exitcode').write_text('0'); os.utime(work/'.exitcode', (stamp+30, stamp+30))
        current = dict(old, sample='NEW', output='/irrelevant', confidence_gwas='medium')
        return current, code, donor, work, proof

    def collect(self, current, code, donor, panel_samples=None):
        with patch.object(shared, '_samples', return_value=panel_samples or {'PANEL1', 'PANEL2'}), patch.object(shared, '_output_contig'):
            return shared.collect_shared_references(current, code, [donor])

    def test_cross_sample_reuses_reference_only(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, proof = self.fixture(Path(d))
            before = {p: p.stat().st_mtime_ns for p in work.iterdir()}
            result = self.collect(c, code, donor)
            self.assertEqual(set(result['references']), {'chr1'})
            self.assertEqual(len(result['references']['chr1']), 4)
            self.assertEqual(result['audit']['scope'], 'reference_only')
            self.assertNotIn('phases', result); self.assertNotIn('ligated', result)
            self.assertEqual(before, {p: p.stat().st_mtime_ns for p in work.iterdir()})

    def test_published_nested_provenance_link_discovers_historical_work(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d))
            organized = Path(d)/'organized'
            published = organized/'imputation/reference_provenance'
            published.mkdir(parents=True)
            (published/'reference.chr1.provenance.json').symlink_to(work/'reference.chr1.provenance.json')
            self.assertEqual(set(self.collect(c, code, organized)['references']), {'chr1'})

    def test_runtime_json_checksum_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d))
            runtime = next(p for p in work.glob('*.json') if p.name[0] != 'r')
            runtime.write_text(runtime.read_text() + ' ')
            result = self.collect(c, code, donor)
            self.assertFalse(result['references'])
            self.assertTrue(any('runtime checksum' in r['reason'] for r in result['audit']['rejected']))

    def test_unfinished_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d)); (work/'.exitcode').write_text('1')
            self.assertFalse(self.collect(c, code, donor)['references'])

    def test_modified_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d)); (work/'reference.chr1.bcf').write_bytes(b'tamper')
            self.assertFalse(self.collect(c, code, donor)['references'])

    def test_panel_checksum_tamper_rejected_even_old_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, proof = self.fixture(Path(d))
            panel = Path(proof['panel']); stamp = panel.stat().st_mtime
            with gzip.open(panel, 'wt') as out: out.write('changed synthetically')
            os.utime(panel, (stamp, stamp))
            result = self.collect(c, code, donor)
            self.assertFalse(result['references'])
            self.assertTrue(any('SHA-256 mismatch' in r['reason'] for r in result['audit']['rejected']))

    def test_bad_version_or_contig_rejected(self):
        for changes in ({'version': 'v1'}, {'source_contig': 'chr2'}, {'fasta_contig_length': 999}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as d:
                c, code, donor, work, proof = self.fixture(Path(d)); proof.update(changes)
                path = work/'reference.chr1.provenance.json'; stamp = path.stat().st_mtime
                path.write_text(json.dumps(proof)); os.utime(path, (stamp, stamp))
                self.assertFalse(self.collect(c, code, donor)['references'])

    def test_reference_sample_exclusion_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d))
            result = self.collect(c, code, donor, {'OLD', 'PANEL2'})
            self.assertFalse(result['references'])
            self.assertTrue(any('sample exclusion' in r['reason'] for r in result['audit']['rejected']))

    def test_different_fasta_file_not_accepted_from_equal_lengths(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d)); other = Path(d)/'other.fa'
            shutil.copy2(c['reference'], other); c['reference'] = str(other)
            self.assertFalse(self.collect(c, code, donor)['references'])

    def test_hardlinked_identical_fasta_and_index_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d)); alias = Path(d)/'alias.fa'
            os.link(c['reference'], alias); os.link(c['reference']+'.fai', str(alias)+'.fai'); c['reference'] = str(alias)
            self.assertEqual(set(self.collect(c, code, donor)['references']), {'chr1'})

    def test_phase_script_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d))
            script = work/'.command.sh'; script.write_text(script.read_text().replace(' reference ', ' phase '))
            self.assertFalse(self.collect(c, code, donor)['references'])

    def test_code_semantics_change_rejected_but_comments_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            c, code, donor, work, _ = self.fixture(Path(d)); newcode = Path(d)/'newcode'; (newcode/'bin').mkdir(parents=True)
            source = (code/'bin/glimpse2.py').read_text()
            (newcode/'bin/glimpse2.py').write_text('# comment\n'+source)
            self.assertTrue(self.collect(c, newcode, donor)['references'])
            (newcode/'bin/glimpse2.py').write_text(source.replace("['--window-count','20000']", "['--window-count','10000']"))
            self.assertFalse(self.collect(c, newcode, donor)['references'])

if __name__ == '__main__': unittest.main()
