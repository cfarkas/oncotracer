"""Scientific contract and isolated execution for optional short-read Strelka2."""
import contextlib
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import strelka2, variants
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml
from tests.test_native_variants import request_config


class StrelkaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def request(self, caller='strelka2_somatic', **kwargs):
        settings = request_config(variant_callers=caller)
        if caller == 'strelka2_somatic':
            settings['variant_matched_normals'] = {'T': 'N'}
        settings.update(kwargs)
        return variants.resolve_variant_request(settings, mode='illumina')

    def test_illumina_only_and_explicit_somatic_pair(self):
        self.assertEqual(self.request().matched_normals, (('T', 'N'),))
        self.assertEqual(variants.variant_plan(self.request())['call_semantics']['strelka2_somatic'], 'matched_tumor_normal_somatic')
        self.assertEqual(self.request('strelka2_germline').matched_normals, ())
        for caller in strelka2.CALLERS:
            with self.assertRaises(OncoTracerError):
                variants.resolve_variant_request(request_config(variant_callers=caller), mode='ont')
        with self.assertRaisesRegex(OncoTracerError, 'explicit tumor-to'):
            self.request(variant_matched_normals={})
        with self.assertRaisesRegex(OncoTracerError, 'requires selecting'):
            self.request('bcftools', variant_matched_normals={'T': 'N'})

    def test_mapping_rejects_unsafe_or_ambiguous_types(self):
        for value in ['T:N', ['T', 'N'], {'T': ['N']}, {'T': 'T'}, {'T': '../N'}, True]:
            with self.subTest(value=value), self.assertRaises(OncoTracerError):
                strelka2.parse_matched_normals(value)
        self.assertEqual(strelka2.parse_matched_normals('{"T":"N"}'), {'T':'N'})

    def test_mapping_roundtrips_flat_json_yaml(self):
        path = self.root/'run.yml'
        config = {'variant_matched_normals': {'T':'N', 'T2':'N2'}}
        path.write_text(render_flat_yaml(config))
        self.assertEqual(load_flat_yaml(path), config)

    def test_roles_complete_pairing_and_paired_end(self):
        request = self.request()
        strelka2.validate_samples(request, ['T','N'], {'T':'tumor','N':'normal'}, paired={'T': True, 'N': True})
        for samples, roles in [(['T','X'], {'T':'tumor','X':'normal'}), (['T','N'], {'T':'tumor','N':'tumor'}),
                                (['T','N','X'], {'T':'tumor','N':'normal','X':'tumor'}), (['T','N'], {})]:
            with self.subTest(samples=samples, roles=roles), self.assertRaises(OncoTracerError):
                strelka2.validate_samples(request, samples, roles)
        with self.assertRaisesRegex(OncoTracerError, 'paired-end'):
            strelka2.validate_samples(request, ['T','N'], {'T':'tumor','N':'normal'}, paired={'T':False,'N':True})

    def test_same_physical_normal_rejected_before_tools_or_outputs(self):
        bam = self.root/'same.bam'; bam.write_bytes(b'synthetic')
        with patch.object(variants,'preflight_variant_tools') as tools, self.assertRaisesRegex(OncoTracerError,'different physical'):
            variants.run_variants(self.request(), {'T':bam,'N':bam}, None, self.root/'results', None, None,
                                  sample_statuses={'T':'tumor','N':'normal'})
        tools.assert_not_called()
        self.assertFalse((self.root/'results').exists())

    def test_runtime_uses_explicit_python27_and_config_scripts(self):
        bindir=self.root/'bin'; bindir.mkdir()
        for name in ['python2.7',*strelka2.SCRIPTS.values()]:
            path=bindir/name; path.write_text('synthetic'); path.chmod(0o755)
        with patch('oncotracer_cli.strelka2.platform.system',return_value='Linux'), patch('oncotracer_cli.strelka2.platform.machine',return_value='x86_64'):
            found=strelka2.discover_runtime(self.root,strelka2.CALLERS)
            self.assertEqual(found['strelka_python'],str(bindir/'python2.7'))
            (bindir/'python2.7').chmod(0o644)
            with self.assertRaisesRegex(OncoTracerError,'Python 2.7'):
                strelka2.discover_runtime(self.root,strelka2.CALLERS)

    def test_known_broken_installed_build_is_rejected_for_somatic_before_execution(self):
        bindir=self.root/'bin';bindir.mkdir()
        for name in ['python2.7',*strelka2.SCRIPTS.values()]:
            path=bindir/name;path.write_text('synthetic');path.chmod(0o755)
        metadata=self.root/'conda-meta';metadata.mkdir()
        (metadata/'strelka-2.9.10-hdfd78af_2.json').write_text('{}')
        with patch('oncotracer_cli.strelka2.platform.system',return_value='Linux'), patch('oncotracer_cli.strelka2.platform.machine',return_value='x86_64'):
            with self.assertRaisesRegex(OncoTracerError,'strelka=2.9.10=h9ee0642_1'):
                strelka2.discover_runtime(self.root,['strelka2_somatic'])
            # The identified defect concerns the somatic ELF, not germline.
            self.assertIn('strelka2_germline',strelka2.discover_runtime(self.root,['strelka2_germline']))

    def test_native_tier1_snv_and_indel_evidence(self):
        snv=strelka2.allele_evidence({'AU':'30,50','TU':'10,15'},'A','T')
        self.assertEqual(snv['strelka_tier1_alt_fraction'],.25)
        self.assertEqual(snv['strelka_tier1_ref_count'],30)
        indel=strelka2.allele_evidence({'TAR':'8,9','TIR':'2,7'},'A','AT')
        self.assertEqual(indel['strelka_tier1_alt_fraction'],.2)
        self.assertNotIn('strelka_tier1_alt_fraction',strelka2.allele_evidence({},'A','T'))
        self.assertEqual(strelka2.allele_evidence({'AU':'0,0','TU':'0,0'},'A','T')['strelka_tier1_alt_fraction'],'.')

    def test_evidence_does_not_invent_gt_ad_af_or_clear_filter(self):
        path=self.root/'input.vcf'
        path.write_text('##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tT\nchr1\t10\t.\tC\tT\t.\tLowEVS\t.\tDP:CU:TU\t40:30,30:10,10\n')
        output=self.root/'output.vcf'; table=self.root/'evidence.tsv'
        variants._evidence(path,output,table,self.request(),'T','strelka2_somatic')
        row=next(csv.DictReader(io.StringIO(table.read_text()),delimiter='\t'))
        self.assertEqual((row['GT'],row['AD'],row['AF']),('.','.','.'))
        self.assertEqual(row['strelka_tier1_alt_fraction'],'0.25')
        self.assertEqual(row['filter'],'LowEVS')
        self.assertEqual(path.read_bytes(),output.read_bytes())

    def test_normal_multiple_sample_groups_rejected_before_sort(self):
        bam=self.root/'normal.bam';bam.write_text('synthetic')
        class Runner:
            def run(self,label,command,**kwargs):
                if 'stdout' in kwargs:
                    kwargs['stdout'].write('@SQ\tSN:chr1\tLN:500\n@RG\tID:a\tSM:A\n@RG\tID:b\tSM:B\n')
                if '-sort' in label:
                    raise AssertionError('sort must not start')
        with self.assertRaisesRegex(OncoTracerError,'exactly one'):
            strelka2.stage_normal('N',bam,self.root,{'chr1':500},{'samtools':'samtools'},Runner(),{},1)

    def test_command_contract_preserves_pair_and_indexes_parts_before_concat(self):
        results=self.root/'strelka2/results/variants';results.mkdir(parents=True)
        for kind in ('snvs','indels'):
            (results/f'somatic.{kind}.vcf.gz').write_bytes(b'synthetic paired VCF')
        commands=[]
        def run(label,command,**kwargs):
            commands.append((label,list(map(str,command)),kwargs))
        tools={'strelka_python':'/isolated/bin/python2.7','strelka2_somatic':'/isolated/bin/configureStrelkaSomaticWorkflow.py',
               'bcftools':'bcftools','bgzip':'bgzip','tabix':'tabix'}
        strelka2.call('strelka2_somatic','T','tumor.bam','matched-normal.bam','reference.fa',self.root,tools,run,2,{},self.root/'targets.bed')
        configure=next(c for label,c,_ in commands if label=='configure')
        self.assertIn('--normalBam',configure);self.assertIn('--tumorBam',configure)
        self.assertNotIn('--exome',configure)
        self.assertIn('--callRegions',configure)
        labels=[label for label,_,_ in commands]
        self.assertLess(labels.index('index-snvs'),labels.index('concat'))
        self.assertLess(labels.index('index-indels'),labels.index('concat'))
        self.assertTrue((self.root/'strelka_original.snvs.vcf.gz').is_file())
        select=next(c for label,c,_ in commands if label=='select-snvs')
        self.assertEqual(select[select.index('-s')+1],'TUMOR')
        self.assertEqual((self.root/'strelka.sample-name.txt').read_text(),'T\n')
        self.assertEqual(next(k for l,_,k in commands if l=='configure')['env']['PYTHONNOUSERSITE'],'1')

    def test_cli_setup_and_check_roundtrip_explicit_pairs_without_calling_tools(self):
        from oncotracer_cli.cli import main
        fastqs = []
        for name in ('T1', 'T2', 'N1', 'N2'):
            path = self.root/(name+'.fq.gz')
            with gzip.open(path, 'wt') as out:
                out.write('@synthetic\nACGT\n+\nIIII\n')
            fastqs.append(path)
        sheet = self.root/'samples.csv'
        sheet.write_text('sample,fastq_1,fastq_2,status\nT,%s,%s,tumor\nN,%s,%s,normal\n' % tuple(fastqs))
        project = self.root/'project'
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture), patch('oncotracer_cli.cli._load_install_config', return_value={}), patch.object(variants, 'preflight_variant_tools', side_effect=AssertionError('check must not execute callers')):
            code=main(['setup','--non-interactive','--backend','host','--mode','illumina','--project',str(project),
                       '--samplesheet',str(sheet),'--variants','--variant-specimen-type','fresh',
                       '--variant-callers','strelka2_germline,strelka2_somatic','--variant-annovar','off',
                       '--variant-matched-normals','{"T":"N"}'])
            self.assertEqual(code,0,capture.getvalue())
            config=load_flat_yaml(project/'config/run.yml')
            self.assertEqual(config['variant_matched_normals'],{'T':'N'})
            code=main(['check','--config',str(project/'config/run.yml')])
            self.assertEqual(code,0,capture.getvalue())
        self.assertFalse((project/'results').exists())

    def test_cli_single_end_strelka_fails_before_project_is_created(self):
        from oncotracer_cli.cli import main
        fastq=self.root/'single.fq.gz'
        with gzip.open(fastq,'wt') as out:
            out.write('@synthetic\nACGT\n+\nIIII\n')
        project=self.root/'rejected-project';capture=io.StringIO()
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture), patch('oncotracer_cli.cli._load_install_config',return_value={}):
            code=main(['setup','--non-interactive','--backend','host','--mode','illumina','--project',str(project),
                       '--sample-name','T','--fastq-1',str(fastq),'--variants','--variant-specimen-type','fresh',
                       '--variant-callers','strelka2_germline','--variant-annovar','off'])
        self.assertEqual(code,2,capture.getvalue())
        self.assertIn('paired-end',capture.getvalue())
        self.assertFalse(project.exists())

    def test_germline_uses_single_bam_and_retains_native_vcf(self):
        results=self.root/'strelka2/results/variants';results.mkdir(parents=True)
        (results/'variants.vcf.gz').write_bytes(b'native-germline')
        commands=[]
        raw=strelka2.call('strelka2_germline','S','sample.bam',None,'ref.fa',self.root,
                         {'strelka_python':'/env/bin/python2.7','strelka2_germline':'configure'},
                         lambda label,cmd,**kw:commands.append(list(map(str,cmd))),1,{},None)
        self.assertEqual(raw.read_bytes(),b'native-germline')
        self.assertIn('--bam',commands[0]);self.assertNotIn('--normalBam',commands[0])


if __name__ == '__main__':
    unittest.main()
