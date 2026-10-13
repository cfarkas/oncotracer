"""GLIMPSE2 is mandatory for the full Illumina browser/terminal workflow."""
import contextlib
import io
import json
import re
import shutil
import subprocess
import unittest
from argparse import Namespace
from collections import Counter
from html.parser import HTMLParser
from unittest.mock import patch

from oncotracer_cli.variant_web_ui import PAGE
from oncotracer_cli.web_ui import PAGE as FASTQ_PAGE
from oncotracer_cli.setup import VARIANT_FIELDS, _variant_values


class Elements(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.items = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


class GlimpseUiTests(unittest.TestCase):
    def test_shared_forms_have_metadata_confidence_and_no_imputation_off_switch(self):
        for page in (PAGE, FASTQ_PAGE):
            items = Elements(page).items
            ids = Counter(attrs['id'] for _, attrs in items if 'id' in attrs)
            self.assertFalse([key for key, count in ids.items() if count > 1])
            for field in ('variant_patients', 'variant_confidence_gwas', 'variant_glimpse_reference_dir',
                          'variant_glimpse_bin', 'variant_resource_dir', 'variant_reuse_from',
                          'variant_nextflow', 'variant_workflow_source'):
                self.assertEqual(ids[field], 1)
                self.assertIn(field, VARIANT_FIELDS)
            self.assertFalse(any(attrs.get('id') == 'variant_engine' for _, attrs in items))
            self.assertIn('mandatory germline imputation', page)
            self.assertIn('unknown age/sex allowed', page)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_generated_javascript_parses_in_both_forms(self):
        for page in (PAGE, FASTQ_PAGE):
            for script in re.findall(r'<script>(.*?)</script>', page, re.S):
                result = subprocess.run([shutil.which('node'), '-e', 'new Function('+json.dumps(script)+');'],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_payload_forces_full_workflow_and_ffpe_while_ont_retains_its_models(self):
        function = PAGE.split('function guidedVariantPayload(data){', 1)[1].split(
            "for(const id of ['variant-clair3-source'", 1)[0]
        script = r'''
const assert=require('node:assert/strict');let mode='illumina';
const nodes={variant_confidence_gwas:{value:'medium'},'variant-clair3-source':{value:'auto'},
variant_ont_profile:{value:'r1041_e82_400bps_sup_v500'},variant_download_resources:{checked:true},
variant_accept_ffperase_license:{checked:true},'variant-scenario-mode':{value:'standard'}};
for(const key of ['variant_patients','variant_glimpse_reference_dir','variant_glimpse_bin','variant_resource_dir','variant_reuse_from','variant_nextflow','variant_workflow_source'])nodes[key]={value:'/fixture/'+key};
const document={getElementById:id=>nodes[id]};function variantPairPayload(){return {};}
''' + 'function guidedVariantPayload(data){' + function + r'''
let value=guidedVariantPayload({variant_specimen_type:'ffpe',variant_callers:'clair3',variant_engine:'legacy',variant_ffperase:'off',variant_annovar:'off',variant_varlociraptor:'required'});
assert.equal(value.variant_engine,'glimpse2');assert.equal(value.variant_callers,'bcftools,mutect2');
assert.equal(value.variant_ffperase,'required');assert.equal(value.variant_annovar,'auto');assert.equal(value.variant_varlociraptor,'off');
assert.equal(value.variant_confidence_gwas,'medium');assert.equal(value.variant_download_resources,true);
assert.equal(value.variant_patients,'/fixture/variant_patients');
value=guidedVariantPayload({variant_specimen_type:'fresh',variant_callers:'bcftools'});
assert.equal(value.variant_ffperase,'off');assert.equal(value.variant_download_resources,false);
mode='ont';value=guidedVariantPayload({variant_callers:'clair3',variant_ffperase:'off'});
assert.equal(value.variant_engine,'legacy');assert.equal(value.variant_clair3_model,'auto');
assert.equal(value.variant_ont_profile,'r1041_e82_400bps_sup_v500');assert.equal(value.variant_confidence_gwas,undefined);
'''
        result = subprocess.run([shutil.which('node'), '-e', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_platform_and_preservation_toggling_never_exposes_illumina_caller_subset(self):
        function = PAGE.split('function syncVariantChoices(platform,preservation){', 1)[1].split('function syncVariantModelControls(){', 1)[0]
        script = r'''
const assert=require('node:assert/strict');const nodes={};
const document={getElementById:id=>nodes[id]||(nodes[id]={value:'off',hidden:true,dataset:{},textContent:''}),querySelectorAll:()=>[]};
function show(id,yes){document.getElementById(id).hidden=!yes;}
''' + 'function syncVariantChoices(platform,preservation){' + function + r'''
syncVariantChoices('illumina','ffpe');assert.equal(nodes['variant-caller-settings'].hidden,true);
assert.equal(nodes['variant-glimpse-settings'].hidden,false);assert.equal(nodes.variant_ffperase.value,'required');
assert.equal(nodes.variant_ffperase.hidden,true);assert.equal(nodes['variant-advanced-settings'].open,false);
syncVariantChoices('illumina','fresh');assert.equal(nodes.variant_ffperase.value,'off');
syncVariantChoices('ont','fresh');assert.equal(nodes['variant-caller-settings'].hidden,false);
assert.equal(nodes['variant-glimpse-settings'].hidden,true);assert.equal(nodes['variant-advanced-settings'].open,true);
syncVariantChoices('illumina',null);assert.equal(nodes['variant-glimpse-settings'].hidden,true);
'''
        result = subprocess.run([shutil.which('node'), '-e', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_terminal_collects_only_report_options_and_cannot_disable_glimpse(self):
        args = Namespace(variants=True, analysis='cna', backend='host', variant_specimen_type='fresh',
                         variant_engine='legacy', variant_callers='freebayes', variant_annovar='off')
        prompts = []
        with patch('builtins.input', side_effect=lambda prompt: prompts.append(prompt) or ''), \
             patch('oncotracer_cli.variants.resolve_variant_request') as validate, \
             contextlib.redirect_stdout(io.StringIO()):
            values = _variant_values(args, 'illumina', interactive=True)
        self.assertEqual(values['variant_engine'], 'glimpse2')
        self.assertEqual(values['variant_callers'], 'bcftools,mutect2')
        self.assertEqual(values['variant_confidence_gwas'], 'medium')
        self.assertEqual(values['variant_annovar'], 'auto')
        self.assertEqual(len(prompts), 3)
        self.assertFalse(any('callers' in p or 'Varlociraptor' in p for p in prompts))
        self.assertNotIn('variant_patients', values)
        validate.assert_called_once_with(values, mode='illumina')

    def test_terminal_ffpe_keeps_requirement_and_explicit_existing_license(self):
        args = Namespace(variants=True, analysis='cna', backend='host', variant_specimen_type='ffpe',
                         variant_ffperase='off', variant_accept_ffperase_license=True)
        with patch('oncotracer_cli.variants.resolve_variant_request'), contextlib.redirect_stdout(io.StringIO()):
            values = _variant_values(args, 'illumina', interactive=False)
        self.assertEqual(values['variant_ffperase'], 'required')
        self.assertTrue(values['variant_accept_ffperase_license'])
        self.assertTrue(values['variant_download_resources'])


if __name__ == '__main__':
    unittest.main()
