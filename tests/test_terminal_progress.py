"""Real concurrent workers, PTY output capture, cancellation and fresh verdicts."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from oncotracer_cli.completion import completion_lines, summarize_completion
from oncotracer_cli.runtime import CommandRunner, OncoTracerError
from oncotracer_cli.sample_execution import run_jobs
from oncotracer_cli.terminal_progress import ProgressJournal, TerminalDisplay, progress_lines, terminal_session

ALLOCATION = {'parallel_samples': 2, 'threads_per_sample': 2, 'memory_per_sample': '4GB', 'memory_gb_per_sample': 4}


class TerminalProgressTests(unittest.TestCase):
    def test_concurrent_subprocesses_have_distinct_current_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = CommandRunner(root/'trace.tsv', echo=False)
            journal = runner.progress = ProgressJournal(root)
            journal.samples(['A', 'B', 'C'])
            barrier = threading.Barrier(2)
            seen = []
            def execute(name, worker):
                if name != 'C':
                    worker.progress_task('align-' + name)
                    barrier.wait(timeout=3)
                    snapshot = json.loads(journal.path.read_text())
                    seen.append(snapshot)
                    barrier.wait(timeout=3)
                worker.run('index-' + name, [sys.executable, '-c', 'pass'])
                return {'status': 'complete' if name != 'B' else 'partial_failure'}
            run_jobs([(n, n) for n in ['A', 'B', 'C']], execute, ALLOCATION, runner, root/'alignment_logs')
            self.assertTrue(any(s['jobs']['A']['state'] == s['jobs']['B']['state'] == 'running' for s in seen))
            self.assertTrue(any(s['jobs']['A']['task'] == 'align-A' and s['jobs']['B']['task'] == 'align-B' for s in seen))
            state = json.loads(journal.path.read_text())
            self.assertEqual(state['task'], '')
            self.assertEqual([state['jobs'][n]['state'] for n in ['A', 'B', 'C']], ['complete', 'partial_failure', 'complete'])
            self.assertEqual(state['jobs']['A']['commands'], 1)
            self.assertIn('2/3 done | 0 running | 0 queued | 1 incomplete', '\n'.join(progress_lines(state)))
            # Queue completion alone is not scientific analysis completion.
            stream = io.StringIO()
            display = TerminalDisplay(root, root/'log', stream=stream, started_ns=0)
            display.finish()
            self.assertRegex(stream.getvalue(), r'A\s+INCOMPLETE')
            self.assertIn('Not finalized', stream.getvalue())

    def test_failed_worker_stops_sibling_and_leaves_queued_sample_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = CommandRunner(root/'trace.tsv', echo=False)
            journal = runner.progress = ProgressJournal(root)
            journal.samples(['A', 'B', 'C'])
            barrier = threading.Barrier(2)
            def execute(name, worker):
                barrier.wait(timeout=3)
                if name == 'A':
                    raise OncoTracerError('synthetic failure')
                worker.run('long-running', [sys.executable, '-c', 'import time; time.sleep(20)'])
            started = time.monotonic()
            with self.assertRaisesRegex(OncoTracerError, 'synthetic failure'):
                run_jobs([(n, n) for n in ['A', 'B', 'C']], execute, ALLOCATION, runner, root/'alignment_logs')
            self.assertLess(time.monotonic() - started, 10)
            rows = json.loads(journal.path.read_text())['jobs']
            self.assertEqual({n: rows[n]['state'] for n in rows}, {'A': 'failed', 'B': 'interrupted', 'C': 'not_started'})

    def test_stale_journal_is_ignored_and_display_failure_cannot_fail_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = ProgressJournal(root)
            journal.samples(['OLD'])
            display = TerminalDisplay(root, root/'log', stream=io.StringIO())
            self.assertIsNone(display.snapshot())
            with patch('oncotracer_cli.runtime.atomic_write_json', side_effect=OSError('disk full')):
                journal.task('still executing')
            fresh = ProgressJournal(root)
            fresh.samples(['NEW'])
            self.assertEqual(display.snapshot()['samples'], ['NEW'])

    def test_unknown_task_has_no_fake_percent_and_control_characters_are_removed(self):
        snapshot = {'phase': 'Alignment', 'started_ns': 1, 'allocation': ALLOCATION, 'task': '',
                    'jobs': {'A\x1b[2J': {'state': 'running', 'task': 'map\rnoise', 'started': 2}}}
        text = '\n'.join(progress_lines(snapshot, now=12))
        self.assertIn('1 running', text)
        self.assertIn('00:00:10', text)
        self.assertNotIn('%', text)
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\r', text)

    def test_redirected_output_is_plain_and_does_not_create_a_log(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), terminal_session('/nonexistent/config.yml'):
            print('plain output', file=sys.stderr)
        self.assertEqual(stream.getvalue(), 'plain output\n')

    def test_already_reported_container_partial_does_not_print_a_second_footer(self):
        from oncotracer_cli import cli
        from oncotracer_cli.runtime import OncoTracerPartialFailure
        error = OncoTracerPartialFailure('Already in the container log', Path('/synthetic/results'))
        error.terminal_reported = True
        stream = io.StringIO()
        with patch.object(cli, 'execute_run', side_effect=error), contextlib.redirect_stderr(stream):
            self.assertEqual(cli.main(['run', '--config', '/synthetic/config.yml', '--backend', 'host']), 2)
        self.assertEqual(stream.getvalue(), '')

    def test_browser_tty_displays_final_sample_table_without_mirroring_tool_noise(self):
        from oncotracer_cli.web import WebState
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = WebState(root)
            state.projects['test'] = {'outdir': str(root), 'config_path': 'config', 'fingerprint': {}}
            log = root/'analysis.log'
            log.write_text('NOISY_TOOL_OUTPUT\n')
            job = {'process': Mock(), 'project_id': 'test', 'log_path': str(log)}
            job['process'].wait.return_value = 0
            completion = summarize_completion({'input_samples': ['A'], 'requested_branches': ['cna'], 'cna_status': 'complete'})
            with patch('oncotracer_cli.terminal_progress.is_terminal', return_value=True), \
                 patch('oncotracer_cli.terminal_progress.TerminalDisplay') as display, \
                 patch('oncotracer_cli.run_completion.published_completion', return_value=completion):
                state._wait(job)
            display.return_value.start.assert_called_once()
            display.return_value.finish.assert_called_once_with(completion, status='complete')

    @unittest.skipUnless(os.name == 'posix', 'POSIX terminal descriptors')
    def test_foreground_pty_captures_children_restores_terminal_and_lists_every_sample(self):
        script = r'''
import json, sys, time
from pathlib import Path
from oncotracer_cli.runtime import CommandRunner, atomic_write_workflow_summary, OncoTracerPartialFailure
from oncotracer_cli.sample_execution import run_jobs
from oncotracer_cli.terminal_progress import ProgressJournal, terminal_session
from oncotracer_cli.engine import write_run_manifest
from oncotracer_cli.results import write_results_index
root = Path(sys.argv[1]); mode = sys.argv[2]
(root/'config').mkdir(); (root/'logs').mkdir()
out = root/'results'; config = root/'config/run.yml'
config.write_text('mode: illumina\noutdir: ' + str(out) + '\n')
try:
    with terminal_session(config):
        runner = CommandRunner(out/'trace.tsv')
        runner.progress = ProgressJournal(out); runner.progress.samples(['A', 'B'])
        def work(name, worker):
            worker.run('align-' + name, [sys.executable, '-c', "import time; print('NOISY_CHILD', flush=True); time.sleep(1.1)"])
        run_jobs([(n, n) for n in ['A','B']], work,
                 {'parallel_samples':2,'threads_per_sample':2,'memory_gb_per_sample':4}, runner, out/'alignment_logs')
        if mode == 'cancel': raise KeyboardInterrupt()
        summary = {'engine':'native', 'mode':'illumina', 'workflow_status': 'complete' if mode == 'complete' else 'partial_failure',
                   'input_samples':['A','B'], 'requested_branches':['cna'], 'cna_status': 'complete' if mode == 'complete' else 'partial_failure',
                   'completed_samples':['A','B'] if mode == 'complete' else ['A'], 'failed_samples':[] if mode == 'complete' else ['B']}
        atomic_write_workflow_summary(out/'06_workflow_summary', summary)
        write_run_manifest(out, config, out/'trace.tsv'); write_results_index(out)
        if mode == 'partial': raise OncoTracerPartialFailure('Synthetic incomplete sample', out)
except (KeyboardInterrupt, OncoTracerPartialFailure) as error:
    assert error.terminal_reported
print('TERMINAL_RESTORED', flush=True)
'''
        for mode in ('complete', 'partial', 'cancel'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                master, slave = pty.openpty()
                proc = subprocess.Popen([sys.executable, '-B', '-c', script, directory, mode], stdout=slave, stderr=slave,
                                        env={**os.environ, 'TERM': 'xterm', 'PYTHONDONTWRITEBYTECODE': '1'})
                os.close(slave)
                data = bytearray()
                deadline = time.monotonic() + 20
                try:
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([master], [], [], .2)
                        if ready:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                break
                            if not chunk: break
                            data.extend(chunk)
                        elif proc.poll() is not None:
                            break
                    self.assertEqual(proc.wait(timeout=2), 0, data.decode(errors='replace'))
                finally:
                    if proc.poll() is None:
                        proc.kill(); proc.wait()
                    os.close(master)
                text = data.decode(errors='replace')
                self.assertIn('2 running', text)
                self.assertIn('TERMINAL_RESTORED', text)
                self.assertNotIn('NOISY_CHILD', text)
                self.assertIn('NOISY_CHILD', next((Path(directory)/'logs').glob('terminal-analysis-*.log')).read_text())
                for name in ('A', 'B'):
                    complete = mode == 'complete' or (mode == 'partial' and name == 'A')
                    self.assertRegex(text, name + r'\s+' + ('COMPLETE' if complete else 'INCOMPLETE'))


if __name__ == '__main__':
    unittest.main()
