"""End-of-run counts cover the selected cohort and every requested branch."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from oncotracer_cli.completion import summarize_completion, completion_lines
from oncotracer_cli.engine import write_run_manifest
from oncotracer_cli.results import write_results_index
from oncotracer_cli.run_completion import published_completion
from oncotracer_cli.runtime import atomic_write_workflow_summary, sha256_file
from oncotracer_cli.web import WebState
from oncotracer_cli.web_progress import progress_for_job


def partial_summary():
    names = [f'S{i}' for i in range(1, 9)]
    return {'engine': 'native', 'mode': 'illumina', 'workflow_status': 'partial_failure',
            'input_samples': names, 'requested_branches': ['cna', 'variants'],
            'cna_status': 'complete', 'completed_samples': names, 'failed_samples': [],
            'variant_status': 'partial_failure', 'variant_completed_samples': names[:6],
            'variant_failed_samples': ['S7'],
            'variants_sample_statuses': {**{name: 'complete' for name in names[:6]},
                                        'S7': 'partial_failure', 'S8': 'not_started'}}


class CompletionCountsTests(unittest.TestCase):
    def test_six_of_eight_complete_with_partial_and_not_started_samples(self):
        completion = summarize_completion(partial_summary())
        self.assertEqual((completion['completed_count'], completion['incomplete_count'], completion['total_samples']), (6, 2, 8))
        self.assertEqual(completion['incomplete_samples'], ['S7', 'S8'])
        self.assertEqual(completion['branches']['cna']['completed_count'], 8)
        self.assertEqual(completion['branches']['variants']['completed_count'], 6)
        lines = '\n'.join(completion_lines(completion))
        self.assertIn('Samples: completed 6/8; incomplete 2/8.', lines)
        self.assertRegex(lines, r'S7\s+INCOMPLETE\s+Small variants \(partial_failure\)')
        self.assertRegex(lines, r'S8\s+INCOMPLETE\s+Small variants \(not_started\)')
        for name in completion['completed_samples']:
            self.assertRegex(lines, name + r'\s+COMPLETE')

    def test_missing_branch_records_and_alignments_never_imply_completion(self):
        summary = partial_summary()
        summary.update(variant_status='failed', variant_completed_samples=[], variants_sample_statuses={},
                       aligned_samples=summary['input_samples'])
        completion = summarize_completion(summary)
        self.assertEqual(completion['completed_count'], 0)
        self.assertEqual(completion['incomplete_count'], 8)
        self.assertEqual(completion['branches']['cna']['completed_count'], 8)

    def test_methylation_no_cpg_and_no_probes_are_incomplete(self):
        summary = partial_summary()
        summary.update(requested_branches=['methylation'], methylation_status='partial_failure',
                       methylation_completed_samples=summary['input_samples'][:6],
                       methylation_no_cpg_samples=['S7'], methylation_no_classifier_probe_samples=['S8'])
        completion = summarize_completion(summary)
        self.assertEqual(completion['incomplete_count'], 2)
        self.assertEqual(completion['branches']['methylation']['sample_statuses']['S8'], 'no_classifier_probes')

    def test_failed_requested_reports_prevent_overall_sample_completion(self):
        summary = partial_summary()
        summary.update(requested_branches=['cna', 'reports'], cna_classifier_status='failed')
        self.assertEqual(summarize_completion(summary)['completed_count'], 0)

    def test_cohort_failure_is_reported_even_if_all_samples_finished(self):
        summary = partial_summary()
        summary['variants_sample_statuses'] = dict.fromkeys(summary['input_samples'], 'complete')
        completion = summarize_completion(summary)
        self.assertEqual(completion['completed_count'], 8)
        self.assertEqual(completion['incomplete_count'], 0)
        self.assertEqual(completion['outstanding_cohort_work'], ['Small variants cohort outputs (partial_failure)'])

    def test_gistic_failure_keeps_finished_reports_but_lists_cohort_work(self):
        summary = partial_summary()
        summary.update(requested_branches=['cna', 'reports'], cna_reports_completed=True,
                       cna_classifier_status='partial_failure', gistic_status='failed')
        completion = summarize_completion(summary)
        self.assertEqual(completion['completed_count'], 8)
        self.assertEqual(completion['outstanding_cohort_work'], ['CNA interpretation reports cohort outputs (partial_failure)'])

    def test_not_applicable_is_separate_from_failed_or_completed(self):
        summary = partial_summary()
        summary['requested_branches'] = ['variants']
        summary['variants_sample_statuses']['S8'] = 'not_applicable'
        completion = summarize_completion(summary)
        self.assertEqual((completion['completed_count'], completion['incomplete_count'], completion['not_applicable_count']), (6, 1, 1))
        self.assertIn('not applicable 1/8', completion_lines(completion)[0])

    def test_legacy_summary_does_not_invent_a_cohort_denominator(self):
        self.assertIsNone(summarize_completion({'completed_samples': ['S1']}))
        summary = partial_summary()
        summary['requested_branches'].append('future_branch')
        self.assertIsNone(summarize_completion(summary))

    def test_all_failed_and_all_complete_have_consistent_totals(self):
        for state, complete in [('failed', 0), ('complete', 8)]:
            summary = partial_summary()
            summary['variants_sample_statuses'] = dict.fromkeys(summary['input_samples'], state)
            counts = summarize_completion(summary)
            self.assertEqual((counts['completed_count'], counts['incomplete_count']), (complete, 8-complete))

    def test_final_files_browser_and_manifest_share_counts_and_reject_old_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root/'results'
            config = root/'run.yml'
            config.write_text('mode: illumina\n')
            summary = partial_summary()
            atomic_write_workflow_summary(out/'06_workflow_summary', summary)
            variant_status = out/'08_variants/variant_status.json'
            variant_status.parent.mkdir()
            variant_status.write_text(json.dumps({'overall_status': 'partial_failure', 'samples': []}))
            write_run_manifest(out, config, out/'trace.tsv')
            write_results_index(out)
            manifest = out/'06_workflow_summary/native_run_manifest.json'
            published = published_completion(out, sha256_file(config))
            self.assertEqual(published['completed_count'], 6)
            self.assertIsNone(published_completion(out, sha256_file(config), manifest.stat().st_mtime_ns))
            self.assertIsNone(published_completion(out, 'other config'))
            for name in ('index.html', '06_workflow_summary/final_report.html', '06_workflow_summary/workflow_summary.txt'):
                self.assertIn('Samples: completed 6/8; incomplete 2/8.', (out/name).read_text())
            report = json.loads((out/'06_workflow_summary/final_report.json').read_text())
            self.assertEqual(report['completion'], published)
            state = WebState(root)
            state.projects['fixture'] = {'id':'fixture', 'outdir':str(out), 'config_path':str(config),
                                        'fingerprint':{str(config):sha256_file(config)}}
            process = Mock()
            process.wait.return_value = 2
            job = {'project_id':'fixture', 'process':process, 'status':'running'}
            state._wait(job)
            self.assertEqual(job['completion'], published)
            self.assertEqual(job['status'], 'partial_failure')
            self.assertIn('Samples: completed 6/8; incomplete 2/8.', progress_for_job(job, '')['note'])
            # A previous successful report may still exist after an aborted resume.
            job['_prior_manifest_mtime'] = manifest.stat().st_mtime_ns
            process.wait.return_value = 1
            state._wait(job)
            self.assertIsNone(job['completion'])
            variant_status.write_text('{}')
            self.assertIsNone(published_completion(out, sha256_file(config)))
            path = out/'06_workflow_summary/workflow_summary.json'
            path.write_text(path.read_text().replace('S8', 'OTHER'))
            self.assertIsNone(published_completion(out, sha256_file(config)))

    def test_stopped_browser_does_not_count_an_old_successful_run(self):
        result = progress_for_job({'status':'stopped'}, '')
        self.assertIn('counts are unavailable for this attempt', result['note'])
        self.assertNotIn('completed 0/', result['note'])


if __name__ == '__main__':
    unittest.main()
