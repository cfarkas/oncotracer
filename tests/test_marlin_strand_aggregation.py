"""Execute native R aggregation on strand-specific, uncovered and adjacent sites."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RSCRIPT=os.environ.get('ONCOTRACER_TEST_MARLIN_RSCRIPT')

@unittest.skipUnless(RSCRIPT,'Supply the MARLIN Rscript to test native R aggregation')
class NativeAggregationTests(unittest.TestCase):
    def test_strands_sum_once_and_adjacent_sites_are_not_overlaps(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);probes=p/'probes.bed';probes.write_text('chr1\t100\t101\tp1\nchr1\t101\t102\tp1\nchr1\t200\t202\tp2\nchr1\t300\t301\tp3\n')
            calls=p/'calls.bed';calls.write_text(''.join(f'chr1\t{pos}\t{pos+1}\tm\t0\t+\t0\t0\t0\t{valid}\t0\t{modified}\n' for pos,valid,modified in [(100,10,8),(101,20,2),(201,10,10),(300,0,0)]))
            output=p/'result.bed'
            subprocess.run([RSCRIPT,'--vanilla',str(ROOT/'bin/scripts/native_marlin_prepare.R'),'--bedmethyl',str(calls),'--probes',str(probes),'--output',str(output)],check=True,capture_output=True)
            rows={row[4]:row for line in output.read_text().splitlines() if (row:=line.split('\t'))}
            self.assertEqual(len(rows),3);self.assertAlmostEqual(float(rows['p1'][3]),1/3)
            self.assertEqual(rows['p2'][3],'NA');self.assertEqual(rows['p3'][3],'NA')
    def test_duplicate_coordinate_annotations_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'probes').write_text('chr1\t100\t101\tp1\n'*2);(p/'calls').write_text('chr1\t100\t101\tm\t0\t+\t0\t0\t0\t10\t50\t5\n')
            result=subprocess.run([RSCRIPT,'--vanilla',str(ROOT/'bin/scripts/native_marlin_prepare.R'),'--bedmethyl',str(p/'calls'),'--probes',str(p/'probes'),'--output',str(p/'result')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('Duplicate MARLIN probe coordinate',result.stderr);self.assertFalse((p/'result').exists())

if __name__=='__main__':unittest.main()
