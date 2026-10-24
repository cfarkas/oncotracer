"""Public variant downloads remain identifiable outside their sample folders."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import variants
from oncotracer_cli.results import _diagnostic, _important


class VariantArtifactNamesTests(unittest.TestCase):
    def test_publish_names_all_downloads_and_rewrites_result_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for sample, caller in [('CTRL-004.1', 'clair3'), ('CTRL-004.1', 'clairs_to'), ('CTRL003', 'clair3')]:
                with self.subTest(sample=sample, caller=caller):
                    prefix = f'{sample}.{caller}'
                    work, dest = root/prefix, root/'published'/sample/caller
                    work.mkdir()
                    files = []
                    for name in [prefix+'.vcf.gz', prefix+'.vcf.gz.tbi', 'annovar.hg38_multianno.txt',
                                 'annovar.hg38_multianno.vcf', 'evidence.tsv', 'ffperase.summary.json',
                                 'ffperase.evidence.tsv', 'caller_raw.vcf.gz', 'call.stderr.log']:
                        path = work/name
                        path.write_bytes(b'unchanged '+name.encode())
                        files.append(path)
                    result = {'status': 'complete', 'vcf': str(dest/(prefix+'.vcf.gz')),
                              'evidence': str(dest/'evidence.tsv'),
                              'annotation': {'outputs': [str(dest/'annovar.hg38_multianno.vcf')]},
                              'assessments': {'table': str(work/'ffperase.evidence.tsv')}}
                    expected = {p.name: p.read_bytes() for p in files}
                    variants._publish(dest, files, None, 'signature', prefix+'.vcf.gz', result, artifact_prefix=prefix)
                    manifest = json.loads((dest/'complete.json').read_text())
                    self.assertEqual(manifest['result'], result)
                    for old_name, data in expected.items():
                        name = old_name if old_name.startswith(prefix+'.') else prefix+'.'+old_name
                        self.assertEqual((dest/name).read_bytes(), data)
                        self.assertEqual(manifest['outputs'][name], hashlib.sha256(data).hexdigest())
                    self.assertEqual(Path(result['evidence']).name, prefix+'.evidence.tsv')
                    self.assertTrue(Path(result['annotation']['outputs'][0]).is_file())
                    self.assertTrue(Path(result['assessments']['table']).is_file())
                    relative = Path('samples')/sample/caller
                    self.assertTrue(_important('08_variants', relative/(prefix+'.evidence.tsv')))
                    self.assertTrue(_diagnostic('08_variants', relative/(prefix+'.caller_raw.vcf.gz')))
                    self.assertFalse(_diagnostic('08_variants', relative/(prefix+'.annovar.hg38_multianno.vcf')))

    def test_legacy_upgrade_keeps_contents_and_refuses_to_overwrite_unowned_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); work = root/'work'; work.mkdir(); dest = root/'output'
            vcf = work/'CTRL003.clair3.vcf.gz'; vcf.write_bytes(b'original VCF')
            table = work/'evidence.tsv'; table.write_bytes(b'sample\tcaller\nCTRL003\tclair3\n')
            result = {'status': 'complete', 'vcf': str(dest/vcf.name), 'evidence': str(dest/table.name)}
            variants._publish(dest, [vcf, table], None, 'old', vcf.name, result)
            previous = json.loads((dest/'complete.json').read_text())
            collision = dest/'CTRL003.clair3.evidence.tsv'; collision.write_bytes(b'user data')
            with self.assertRaisesRegex(variants.OncoTracerError, 'unowned'):
                variants._prefix_verified_outputs(dest, previous, 'new', 'CTRL003.clair3')
            self.assertEqual(collision.read_bytes(), b'user data')
            collision.unlink()
            with patch.object(variants, '_call', side_effect=AssertionError('Must not recall variants')):
                upgraded = variants._prefix_verified_outputs(dest, previous, 'new', 'CTRL003.clair3')
            self.assertEqual(Path(upgraded['vcf']).read_bytes(), b'original VCF')
            self.assertEqual(Path(upgraded['evidence']).read_bytes(), b'sample\tcaller\nCTRL003\tclair3\n')
            self.assertFalse((dest/'evidence.tsv').exists())
            completion = json.loads((dest/'complete.json').read_text())
            self.assertEqual(completion['signature'], 'new')
            self.assertEqual(variants._prefix_verified_outputs(dest, completion, 'new', 'CTRL003.clair3'), upgraded)

    def test_prefix_collisions_are_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = [root/'evidence.tsv', root/'S.clair3.evidence.tsv']
            for p in files:p.write_text('keep')
            with self.assertRaisesRegex(variants.OncoTracerError, 'collide'):
                variants._publish(root/'out', files, None, 'signature', '', {}, artifact_prefix='S.clair3')
            self.assertTrue(all(p.read_text() == 'keep' for p in files))
            self.assertFalse((root/'out').exists())


if __name__ == '__main__':
    unittest.main()
