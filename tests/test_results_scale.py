import unittest
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli import results


class ResultIndexScaleTests(unittest.TestCase):
    def test_partition_preserves_old_categories_order_and_duplicates(self):
        for stage in ['08_variants', '05_cna_classifier', '07_methylation', '01_samurai_ont']:
            directory=Path('/analysis')/stage
            files=[directory/name for name in ['index.html','variant_status.json','sample_status.tsv','cohort.vcf.gz','sample.bam','diagnostics/fit.tsv','work/task/log.txt','tables/calls.tsv','figures/plot.png','qc/metrics.txt','notes.txt','notes.txt']]
            diagnostic=[p for p in files if results._diagnostic(stage,p.relative_to(directory))]
            visible=[p for p in files if p not in diagnostic]
            quality=[p for p in visible if results._quality(stage,p.relative_to(directory))]
            primary=[p for p in visible if p not in quality and results._important(stage,p.relative_to(directory))]
            remaining=[p for p in visible if p not in primary and p not in quality]
            self.assertEqual(results._partition_result_files(stage,directory,files),(diagnostic,quality,primary,remaining))

    def test_large_inventory_never_compares_paths_pairwise(self):
        class Entry:
            def __init__(self,index):self.index=index
            def relative_to(self,root):return self.index
            def __eq__(self,other):raise AssertionError('Pairwise path comparison makes large catalogs quadratic')
        files=[Entry(i) for i in range(10000)]
        with patch.object(results,'_diagnostic',side_effect=lambda stage,index:index%4==0) as diagnostic, patch.object(results,'_quality',side_effect=lambda stage,index:index%4==1) as quality, patch.object(results,'_important',side_effect=lambda stage,index:index%4==2) as important:
            groups=results._partition_result_files('08_variants',None,files)
        self.assertEqual([len(group) for group in groups],[2500]*4)
        self.assertEqual([diagnostic.call_count,quality.call_count,important.call_count],[10000,7500,5000])
        for offset,group in enumerate(groups):
            self.assertIs(group[0],files[offset]);self.assertIs(group[-1],files[9996+offset])

    def test_empty_inventory(self):
        self.assertEqual(results._partition_result_files('08_variants',Path('/analysis'),[]),([],[],[],[]))
