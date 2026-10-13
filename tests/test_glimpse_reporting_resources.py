"""Resolve cached local model paths before a command runner isolates HOME."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oncotracer_cli import glimpse_pipeline as pipeline
from oncotracer_cli.variants import resolve_variant_request


class ReportResourceTests(unittest.TestCase):
    def test_child_params_keep_absolute_model_and_python_from_original_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'original home';home.mkdir()
            bundle=root/'bundle';(bundle/'bin').mkdir(parents=True)
            (bundle/'main.nf').write_text('// controlled fixture\n')
            (bundle/'bin/llm_reporting.py').write_text(
                'from pathlib import Path\n'
                'def prepare(c):\n'
                '    c["llm_python"]=str(Path.home()/"environment/bin/python3")\n'
                '    c["llm_model_directory"]=str(Path.home()/"cache/local-model")\n')
            resources=root/'resources'
            for relative in ('gwas/associations.GRCh38.tsv.gz','ancestry/reference/complete.json','clinvar.vcf.gz'):
                path=resources/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('cached fixture')
            request=resolve_variant_request({'run_variants':True,'variant_specimen_type':'fresh'},mode='illumina')
            request=replace(request,glimpse_config={'resource_dir':str(resources),'clinvar':str(resources/'clinvar.vcf.gz')})
            bam=root/'SAMPLE.bam';bam.write_bytes(b'fixture BAM');ref=root/'ref.fa';ref.write_text('>chr1\nA\n')
            seen=[];test=self
            class Runner:
                def run(self,stage,command,**kwargs):
                    # Any resource downloader or upload call would fail this test.
                    test.assertEqual(stage,'complete-glimpse2-SAMPLE')
                    with patch.dict('os.environ',{'HOME':str(root/'isolated home')}):
                        params=json.loads(Path(command[command.index('-params-file')+1]).read_text())
                        test.assertEqual(params['llm_python'],str(home/'environment/bin/python3'))
                        test.assertEqual(params['llm_model'],str(home/'cache/local-model'))
                        test.assertTrue(Path(params['llm_python']).is_absolute())
                        test.assertTrue(Path(params['llm_model']).is_absolute())
                        out=Path(params['output'])
                        vcf=out/'cohort_vcf/SAMPLE.cohort_vcf/SAMPLE.annotated.vcf.gz'
                        vcf.parent.mkdir(parents=True);vcf.write_bytes(b'fixture completed VCF')
                        imputed=out/'imputation/SAMPLE.chr1.imputed.bcf'
                        imputed.parent.mkdir();imputed.write_bytes(b'fixture completed imputation')
                        seen.append(params)
            with patch.dict('os.environ',{'HOME':str(home)}), \
                 patch.object(pipeline,'workflow_bundle',return_value=bundle), \
                 patch.object(pipeline,'preflight',return_value={'nextflow':'nextflow','bcftools':'bcftools'}), \
                 patch('oncotracer_cli.glimpse_joint.merge_cohort',return_value={'outputs':{}}):
                result=pipeline.run(request,{'SAMPLE':bam},ref,root/'output',Runner(),None)
            self.assertEqual(result['overall_status'],'complete')
            self.assertEqual(len(seen),1)
            self.assertEqual(bam.read_bytes(),b'fixture BAM')


if __name__=='__main__':unittest.main()
