"""Terminal browser states retain raw logs without presenting partial work as a crash."""
import shutil
import subprocess
import unittest

from oncotracer_cli.variant_web_ui import PAGE


class VariantBrowserStatusTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for browser state checks")
    def test_partial_failure_retains_results_and_collapses_only_raw_log(self):
        function = PAGE.split("function renderJobStatus(job){", 1)[1].split("async function poll(){", 1)[0]
        script = r"""
const assert=require('node:assert/strict');
const nodes={};
for(const name of ['progress-card','job-status','job-badge','job-note','log-details','logs','error'])nodes[name]={dataset:{},textContent:'',hidden:true,open:true};
const $=name=>nodes[name],show=(name,yes)=>nodes[name].hidden=!yes;
nodes.logs.textContent='ERROR: Variant analysis is incomplete; completed outputs and failure details are retained.';
const originalLog=nodes.logs.textContent;
""" + "function renderJobStatus(job){" + function + r"""
renderJobStatus({project_id:'one',status:'running'});
assert.equal(nodes['log-details'].open,true);
renderJobStatus({project_id:'one',status:'partial_failure',exit_code:2});
assert.equal(nodes['job-status'].textContent,'Partial failure');
assert.equal(nodes['progress-card'].dataset.state,'partial_failure');
assert.match(nodes['job-badge'].textContent,/retained/);
assert.match(nodes['job-note'].textContent,/required assessments could not be completed/);
assert.equal(nodes['job-note'].hidden,false);
assert.equal(nodes['log-details'].open,false);
assert.equal(nodes.logs.textContent,originalLog);
assert.equal(nodes.error.hidden,true);
// Opening the raw log remains a user choice across another status poll.
nodes['log-details'].open=true;
renderJobStatus({project_id:'one',status:'partial_failure',exit_code:2});
assert.equal(nodes['log-details'].open,true);
// Another partial run defaults to a collapsed raw log again.
renderJobStatus({project_id:'two',status:'partial_failure',exit_code:2});
assert.equal(nodes['log-details'].open,false);
// Exit 2 alone must not relabel a genuine failed run as partial.
renderJobStatus({project_id:'three',status:'failed',exit_code:2});
assert.equal(nodes['job-status'].textContent,'Variant analysis failed');
assert.equal(nodes['progress-card'].dataset.state,'failed');
assert.equal(nodes['log-details'].open,true);
assert.match(nodes['job-note'].textContent,/error details/);
renderJobStatus({project_id:'four',status:'complete',exit_code:0});
assert.equal(nodes['job-status'].textContent,'Variant analysis completed');
assert.equal(nodes['job-note'].hidden,true);
assert.equal(nodes.logs.textContent,originalLog);
"""
        result = subprocess.run([shutil.which("node"), "-e", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_log_is_accessible_under_a_named_disclosure(self):
        self.assertIn('<details id="log-details" open><summary>Raw execution log</summary>', PAGE)
        self.assertIn('id="logs" class="logs" tabindex="0" aria-label="Analysis log"', PAGE)
        self.assertIn('#progress-card[data-state=partial_failure]', PAGE)


if __name__ == "__main__":
    unittest.main()
