"""Regression for QUILT's unanchored .gz substitution and interrupted chunks."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pysam

WORKERS = Path(__file__).resolve().parents[1] / 'oncotracer_cli/lpwgs/bin'
with patch.object(sys, 'path', [str(WORKERS), *sys.path]):
    quilt = importlib.import_module('quilt2_worker')


class QuiltPathTests(unittest.TestCase):
    def test_relative_output_survives_gz_in_parent_and_sample_concurrently(self):
        with tempfile.TemporaryDirectory(prefix='quilt-gz-parent ') as directory:
            root = Path(directory)
            tool = root / 'QUILT2.R'
            # Reproduce the actual upstream path transformation in a process.
            tool.write_text('#!' + sys.executable + '\n' + '''
import json, os, re, sys
from pathlib import Path
a = dict(arg[2:].split('=', 1) for arg in sys.argv[1:])
output = os.path.join(a['outputdir'], a['output_filename'])
uncompressed = re.sub('.gz', '', output)
sample = Path(a['sampleNames_file']).read_text().strip()
Path(uncompressed).write_text(sample)
Path(output).write_text(json.dumps({'sample': sample, 'cwd': os.getcwd(), 'output': output}))
''')
            tool.chmod(0o755)
            reference = root / 'reference.gz-name.RData'
            reference.write_bytes(b'reference')
            before = Path.cwd()

            def run(number):
                temporary = root / f'job-{number}-30gz8eos'; temporary.mkdir()
                names = temporary / 'sample-names.txt'
                names.write_text(f'sample{number}gz\n')
                bamlist = temporary / 'bamlist.txt'; bamlist.write_text('/input.bam\n')
                chunk = dict(prepared_reference=str(reference), chr='chr11',
                             regionStart=10, regionEnd=100, buffer=5, nGen=100)
                result = quilt.run_imputation_chunk({'QUILT2.R': str(tool)}, chunk,
                    bamlist, names, temporary, 1, 20)
                data = json.loads(result.read_text())
                self.assertEqual(data['sample'], f'sample{number}gz')
                self.assertEqual(data['cwd'], str(temporary))
                self.assertEqual(data['output'], './imputed.vcf.gz')
                self.assertTrue((temporary / 'imputed.vcf').is_file())
                return result

            with ThreadPoolExecutor(max_workers=2) as pool:
                outputs = list(pool.map(run, (1, 2)))
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertEqual(Path.cwd(), before)


class QuiltRecoveryTests(unittest.TestCase):
    def test_previous_worker_reuse_requires_matching_inputs_and_valid_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plain = root / 'S1.vcf'
            plain.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr11,length=1000>\n'
                '##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n'
                '##FORMAT=<ID=GP,Number=G,Type=Float,Description="GP">\n'
                '##FORMAT=<ID=DS,Number=1,Type=Float,Description="DS">\n'
                '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n'
                'chr11\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP:DS\t0|1:0.1,0.8,0.1:1\n')
            final = root / 'S1.vcf.gz'
            pysam.tabix_compress(str(plain), str(final)); pysam.tabix_index(str(final), preset='vcf')
            index = Path(str(final) + '.tbi'); receipt = root / 'complete.json'
            bam = dict(path='/data/S1.bam', bytes=100, mtime_ns=100, device=1, inode=2)
            chunk = dict(chr='chr11', regionStart=1, regionEnd=1000, sha256='reference-hash')
            def cache(producer):
                quilt.atomic(receipt, dict(signature=quilt.imputation_signature(bam, chunk, 20, producer),
                    files={f.name: quilt.sha(f) for f in (final, index)}))
            def reuse(**changes):
                args = dict(receipt=receipt, final=final, bam=bam, chunk=chunk,
                            min_base_quality=20, sample='S1', code='new-worker')
                args.update(changes)
                return quilt.reusable_imputation_chunk(**args)
            cache(quilt.PATH_COMPATIBLE_WORKERS[0])
            self.assertTrue(reuse())
            self.assertFalse(reuse(bam={**bam, 'mtime_ns': 101}))
            self.assertFalse(reuse(chunk={**chunk, 'sha256': 'different-reference'}))
            self.assertFalse(reuse(min_base_quality=30))
            self.assertFalse(reuse(sample='S2'))
            cache('unreviewed-worker'); self.assertFalse(reuse())
            cache('new-worker'); self.assertTrue(reuse())
            index.write_bytes(b'interrupted index'); self.assertFalse(reuse())
            receipt.unlink(); self.assertFalse(reuse())


if __name__ == '__main__':
    unittest.main()
