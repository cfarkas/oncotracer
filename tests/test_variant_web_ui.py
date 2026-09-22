"""Terminal browser states retain raw logs without presenting partial work as a crash."""
import shutil
from collections import Counter
from html.parser import HTMLParser
import subprocess
import unittest

from oncotracer_cli.variant_web_ui import PAGE
from oncotracer_cli.web_ui import PAGE as FASTQ_PAGE
from oncotracer_cli.variant_model_assets import FFPERASE_LICENSE


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

    def test_shared_forms_have_unique_paths_and_scoped_discovery(self):
        class Elements(HTMLParser):
            def __init__(self, page):
                super().__init__()
                self.elements = []
                self.feed(page)
            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))
        for page in (PAGE, FASTQ_PAGE):
            with self.subTest(existing_bam=page is PAGE):
                elements = Elements(page).elements
                ids = Counter(attrs["id"] for _, attrs in elements if "id" in attrs)
                self.assertEqual([key for key, count in ids.items() if count > 1], [])
                paths = ["variant_tool_prefix", "variant_clair3_model", "variant_clairsto_sif",
                         "variant_ffperase_root", "variant_ffperase_models", "variant_ffperase_prefix",
                         "variant_ffperase_sif", "variant_annovar_dir", "variant_annovar_db"]
                discovery = {attrs["data-variant-detect"] for _, attrs in elements if "data-variant-detect" in attrs}
                for key in paths:
                    self.assertEqual(ids[key], 1)
                    self.assertIn(key, discovery)
                self.assertNotIn("variant_targets_bed", discovery)
                self.assertNotIn("variant_varlociraptor_scenario", discovery)
                sections = [attrs["data-variant-section"] for _, attrs in elements if "data-variant-section" in attrs]
                self.assertEqual(sections, [f"variant-{part}-section" for part in ("specimen", "tools", "filter", "annotation")])
                self.assertFalse(any(attrs.get("href", "").startswith("#variant-") for _, attrs in elements))
                self.assertEqual(ids["variant-resource-dialog"], 1)
                for key in ["variant-clair3-source", "variant_ont_profile", "variant-clairsto-preset",
                            "variant-scenario-mode", "variant-custom-scenario", "variant_download_resources",
                            "variant_accept_ffperase_license"]:
                    self.assertEqual(ids[key], 1)
                self.assertIn(FFPERASE_LICENSE, page)
                hidden = {attrs.get("id") for _, attrs in elements if "hidden" in attrs}
                self.assertTrue({"variant-tools-section", "variant-filter-section", "variant-annotation-section",
                                 "variant-caller-settings", "variant-custom-scenario"}.issubset(hidden))

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser state checks")
    def test_saved_runtime_uses_one_alternative_and_docker_status_remains_visible(self):
        runtime = PAGE.split("function filterVariantRuntime", 1)[1].split("function resetVariantResourceResults", 1)[0]
        fields = PAGE.split("function variantResourceField", 1)[1].split("function variantGuideMatches", 1)[0]
        script = """
const assert=require('node:assert/strict');
let choice='native';const document={getElementById:()=>({value:choice})};
""" + "function filterVariantRuntime" + runtime + "function variantResourceField" + fields + """
const data={variant_ffperase_prefix:'/native',variant_ffperase_sif:'/runtime.sif'};
assert.deepEqual(filterVariantRuntime({...data}),{variant_ffperase_prefix:'/native'});
choice='sif';
assert.deepEqual(filterVariantRuntime({...data},true),{variant_ffperase_prefix:'',variant_ffperase_sif:'/runtime.sif'});
assert.equal(data.variant_ffperase_prefix,'/native');
assert.equal(variantResourceField({id:'varlociraptor',field:'docker_image'}),'variant_tool_prefix');
assert.equal(variantResourceField({id:'ffperase_runtime',field:'docker_image'}),'variant_ffperase_prefix');
assert.equal(variantResourceField({id:'annovar_db',field:'variant_annovar_db'}),'variant_annovar_db');
"""
        result = subprocess.run([shutil.which("node"), "-e", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser state checks")
    def test_guided_payload_has_explicit_profiles_and_safe_standard_scenario_defaults(self):
        for page in (PAGE, FASTQ_PAGE):
            with self.subTest(existing_bam=page is PAGE):
                function = page.split("function guidedVariantPayload(data){", 1)[1].split(
                    "for(const id of ['variant-clair3-source'", 1)[0]
                script = r"""
const assert=require('node:assert/strict');
const nodes={
  'variant-clair3-source':{value:'auto'},
  variant_ont_profile:{value:'r1041_e82_400bps_sup_v500'},
  variant_download_resources:{checked:true},
  variant_accept_ffperase_license:{checked:false},
  'variant-scenario-mode':{value:'standard'},
  variant_varlociraptor_scenario:{value:'/kept/advanced.yml'}
};
const document={getElementById:key=>nodes[key]};
""" + "function guidedVariantPayload(data){" + function + r"""
let value=guidedVariantPayload({variant_callers:'clair3',variant_clair3_model:'/previous/model',
  variant_varlociraptor:'required',variant_varlociraptor_scenario:'/previous/scenario.yml',
  variant_varlociraptor_events:'SOMATIC',variant_varlociraptor_sample:'tumor'});
assert.equal(value.variant_clair3_model,'auto');
assert.equal(value.variant_ont_profile,'r1041_e82_400bps_sup_v500');
assert.equal(value.variant_varlociraptor_scenario,'');
assert.equal(value.variant_varlociraptor_events,'PRESENT');
assert.equal(value.variant_varlociraptor_sample,'sample');
assert.equal(value.variant_download_resources,false);
assert.equal(value.variant_accept_ffperase_license,false);
nodes['variant-clair3-source'].value='local';
value=guidedVariantPayload({variant_callers:'clair3',variant_clair3_model:'/existing/model'});
assert.equal(value.variant_clair3_model,'/existing/model');
assert.equal(value.variant_ont_profile,'');
value=guidedVariantPayload({variant_callers:'bcftools',variant_ffperase:'required'});
assert.equal(value.variant_download_resources,true);
assert.equal(value.variant_accept_ffperase_license,false);
nodes.variant_accept_ffperase_license.checked=true;
assert.equal(guidedVariantPayload({variant_ffperase:'required'}).variant_accept_ffperase_license,true);
nodes['variant-scenario-mode'].value='custom';
value=guidedVariantPayload({variant_varlociraptor:'required',variant_varlociraptor_scenario:'/kept/advanced.yml',
  variant_varlociraptor_events:'SOMATIC',variant_varlociraptor_sample:'tumor'});
assert.equal(value.variant_varlociraptor_scenario,'/kept/advanced.yml');
assert.equal(value.variant_varlociraptor_events,'SOMATIC');
assert.equal(value.variant_varlociraptor_sample,'tumor');
nodes.variant_varlociraptor_scenario.value='';
assert.throws(()=>guidedVariantPayload({variant_varlociraptor:'required'}),/Choose a custom scenario YAML/);
nodes['variant-scenario-mode'].value='standard';
value=guidedVariantPayload({variant_varlociraptor:'required',variant_varlociraptor_scenario:'/stale.yml',
  variant_varlociraptor_events:'SOMATIC',variant_varlociraptor_sample:'tumor'});
assert.equal(value.variant_varlociraptor_scenario,'');
assert.equal(value.variant_varlociraptor_events,'PRESENT');
assert.equal(value.variant_varlociraptor_sample,'sample');
"""
                result = subprocess.run([shutil.which("node"), "-e", script], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_log_is_accessible_under_a_named_disclosure(self):
        self.assertIn('<details id="log-details" open><summary>Raw execution log</summary>', PAGE)
        self.assertIn('id="logs" class="logs" tabindex="0" aria-label="Analysis log"', PAGE)
        self.assertIn('#progress-card[data-state=partial_failure]', PAGE)


if __name__ == "__main__":
    unittest.main()
