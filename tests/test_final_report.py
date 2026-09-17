"""Combined reports preserve status, source scores and scientific files."""
import json
import tempfile
import unittest
from pathlib import Path

from oncotracer_cli.results import write_results_index
from oncotracer_cli.runtime import OncoTracerError


class FinalReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'results'
        self.put('06_workflow_summary/workflow_summary.json', json.dumps({'workflow_status':'complete'}))

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def generate(self):
        write_results_index(self.root)
        return ((self.root / '06_workflow_summary/final_report.html').read_text(),
                json.loads((self.root / '06_workflow_summary/final_report.json').read_text()))

    def test_absent_llm_is_visible_and_cna_counts_are_exact(self):
        events = self.put('03_cna_codification/cna_events.tsv', 'sample\tstate\ns1\tgain\ns1\tgain\ns2\tloss\n')
        before = events.read_bytes()
        page, data = self.generate()
        self.assertEqual(data['cna_events'], {'s1':{'gain':2}, 's2':{'loss':1}})
        self.assertIn('No literature/LLM evidence was produced', page)
        self.assertEqual(events.read_bytes(), before)
        root_page = (self.root / 'index.html').read_text()
        self.assertIn('05 · Interpretation', root_page)
        self.assertIn('not_requested', root_page)
        self.assertIn('final_report.html', root_page)
        self.assertNotIn('3. Methylation results', page)

    def test_methylation_scores_and_failed_samples_are_separate(self):
        self.put('07_methylation/good/marlin.tsv', 'sample\tAML\tALL\ngood\t0.8\t0.2\n')
        self.put('07_methylation/bad/stale.tsv', 'sample\tstale_prediction\nbad\t0.99\n')
        self.put('07_methylation/methylation_status.json', json.dumps({
            'overall_status':'partial_failure', 'classifier':'marlin', 'samples':[
                {'sample':'good', 'status':'complete', 'covered_cpg_rows':1200,
                 'covered_classifier_probes':50,'classification':'07_methylation/good/marlin.tsv'},
                {'sample':'bad', 'status':'no_cpg_modifications', 'covered_cpg_rows':0,
                 'classification':'07_methylation/bad/stale.tsv'}]}))
        page, data = self.generate()
        self.assertIn('0.8', page)
        self.assertIn('1200', page)
        self.assertIn('no_cpg_modifications', page)
        self.assertNotIn('stale_prediction', page)
        self.assertNotIn('classification',data['methylation']['samples'][1])
        self.assertEqual(data['methylation']['status'],'partial_failure')

    def test_sturgeon_csv_raw_scores_and_unsafe_paths(self):
        self.put('07_methylation/good/sturgeon.csv', 'sample,GBM,other\ngood,0.76,0.24\n')
        secret = Path(self.tmp.name) / 'secret.tsv'
        secret.write_text('private\nsecret_score\n')
        (self.root / '07_methylation/link.tsv').symlink_to(secret)
        self.put('07_methylation/methylation_status.json', json.dumps({
            'overall_status':'complete','classifier':'sturgeon','samples':[
                {'sample':'good','status':'complete','classification':'07_methylation/good/sturgeon.csv'},
                {'sample':'external','status':'complete','classification':str(secret)},
                {'sample':'traversal','status':'complete','classification':'../secret.tsv'},
                {'sample':'symlink','status':'complete','classification':'07_methylation/link.tsv'}]}))
        page,data = self.generate()
        self.assertIn('0.76',page)
        self.assertNotIn('secret_score',page)
        for record in data['methylation']['samples'][1:]:
            self.assertIn('prediction_error',record)

    def test_missing_methylation_artifact_preserves_requested_status(self):
        self.put('06_workflow_summary/workflow_summary.json', json.dumps({
            'workflow_status':'failed', 'cna_status':'not_requested', 'methylation_status':'failed'}))
        self.put('03_cna_codification/cna_events.tsv', 'sample\tstate\nstale\tgain\n')
        page,data=self.generate()
        self.assertEqual(data['methylation']['status'],'failed')
        self.assertEqual(data['cna_events'],{})
        self.assertIn('methylation status artifact is unavailable',page)

    def test_complete_sample_without_prediction_is_flagged(self):
        self.put('07_methylation/empty.tsv','')
        self.put('07_methylation/methylation_status.json',json.dumps({
            'overall_status':'complete','classifier':'marlin','samples':[
                {'sample':'missing','status':'complete'},
                {'sample':'empty','status':'complete','classification':'07_methylation/empty.tsv'}]}))
        page,data=self.generate()
        self.assertEqual(len(data['methylation']['samples']),2)
        self.assertTrue(all('prediction_error' in row for row in data['methylation']['samples']))
        self.assertIn('prediction file is unavailable',page)

    def test_literature_status_fallback_and_html_escape(self):
        self.put('05_cna_classifier/06_knowledge/knowledge_metrics.json', json.dumps({
            'literature_llm_completed_features':0,'literature_llm_failed_trials':2,
            'literature_source_counts':{'deterministic_pubmed_text_fallback':1},
            'web_errors':['timeout']}))
        self.put('05_cna_classifier/06_knowledge/sample_knowledge.tsv',
                 'sample\tfeature_id\tliterature_synthesis\tliterature_synthesis_source\n'
                 's1\tMYC\t<script>unsafe</script>\tdeterministic_pubmed_text_fallback\n')
        page,data = self.generate()
        self.assertIn('No accepted literature LLM draft', page)
        self.assertIn('Some literature requests failed', page)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>',page)
        self.assertEqual(data['literature']['failed_llm_trials'],2)

    def test_final_report_collision_fails_before_writing_pages(self):
        for name,value in [('06_workflow_summary/final_report.json','{"schema":"mine"}'),
                           ('06_workflow_summary/final_report.html','my report')]:
            path=self.put(name,value)
            with self.assertRaises(OncoTracerError):
                self.generate()
            self.assertFalse((self.root/'index.html').exists())
            self.assertEqual(path.read_text(),value)
            path.unlink()

    def test_initial_and_refined_results_share_file_roles(self):
        for name in ['01_samurai_illumina/qdnaseq/all_segments.seg',
                     '01_samurai_illumina/qdnaseq/qdnaseq_sample_status.json',
                     '01_samurai_illumina/alignment/s1.bam',
                     '02_bam_refinement/data/04_final_results/final_segments.tsv',
                     '02_bam_refinement/data/01_tables/sample_refinement_summary.csv',
                     '02_bam_refinement/diagnostics/data/raw.tsv']:
            self.put(name,'{}' if name.endswith('.json') else 'example\n')
        self.generate()
        catalog=json.loads((self.root/'06_workflow_summary/results_catalog.json').read_text())
        stages=[stage for stage in catalog['stages'] if stage['stage'].startswith(('01_','02_'))]
        self.assertEqual(len(stages),2)
        for stage in stages:
            self.assertEqual(len(stage['primary_files']),1)
            self.assertEqual(len(stage['quality_control_files']),1)
            self.assertEqual(stage['diagnostic_file_count'],1)
            page=(self.root/stage['index']).read_text()
            self.assertIn('Primary results',page)
            self.assertIn('Quality control',page)
            self.assertIn('Diagnostics and intermediate',page)


if __name__ == '__main__':
    unittest.main()
