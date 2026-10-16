"""Reject corrupt outputs and dependent reuse before any task can be scheduled."""
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'oncotracer_cli/lpwgs/bin'))
from reuse_validation import Validator
from bcftools_compat import trait_call_options
from oncotracer_cli.runtime import StageLedger
from oncotracer_cli.glimpse_pipeline import preflight_jobs


class ReuseValidationTests(unittest.TestCase):
    def test_truncated_variant_and_wrong_sample_are_rejected(self):
        import pysam
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);vcf=root/'s.vcf';compressed=root/'s.vcf.gz'
            vcf.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\nchr1\t10\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n')
            pysam.tabix_compress(str(vcf),str(compressed));pysam.tabix_index(str(compressed),preset='vcf')
            files=[compressed,Path(str(compressed)+'.tbi')]
            self.assertTrue(Validator().group('phase','chr1:0',files,'SAMPLE'))
            self.assertFalse(Validator().group('phase','chr1:0',files,'OTHER'))
            index=files[1];original_index=index.read_bytes();index.write_bytes(b'corrupt index')
            self.assertFalse(Validator().group('phase','chr1:0',files,'SAMPLE'))
            index.write_bytes(original_index)
            compressed.write_bytes(compressed.read_bytes()[:-28])
            validator=Validator();self.assertFalse(validator.group('phase','chr1:0',files,'SAMPLE'))
            self.assertIn('BGZF',validator.rejected[0]['reason'])

    def test_corrupt_compressed_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.tsv.gz';p.write_bytes(gzip.compress(b'column\nvalue\n')[:-4])
            self.assertFalse(Validator().group('report','SAMPLE',[p]))

    def test_rejected_phase_invalidates_ligated_parent(self):
        validator=Validator()
        reused={'references':{'chr1':['reference']},'phases':{'chr1:0':['bad']},'ligated':{'chr1':['ligated']}}
        def validate(stage,key,files,sample=None):
            if stage=='phases':validator.rejected.append({'stage':stage,'key':key});return False
            return True
        with patch.object(validator,'group',side_effect=validate):validator.imputation(reused,'SAMPLE')
        self.assertFalse(reused['phases']);self.assertFalse(reused['ligated'])

    def test_native_ledger_detects_same_size_tampering_even_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'result';p.write_bytes(b'good');ledger=StageLedger(Path(d)/'state.json')
            ledger.complete('stage','signature',[p]);stamp=p.stat().st_mtime_ns
            self.assertTrue(ledger.reusable('stage','signature',[p]))
            p.write_bytes(b'evil');os.utime(p,ns=(stamp,stamp))
            self.assertFalse(ledger.reusable('stage','signature',[p]))

    def test_caller_flags_are_selected_from_capabilities(self):
        from types import SimpleNamespace
        trait_call_options.cache_clear()
        help_text='--multiallelic-caller --keep-alts --ploidy --annotate'
        with patch('bcftools_compat.subprocess.run',return_value=SimpleNamespace(stdout=help_text)):
            self.assertNotIn('--keep-unseen-allele',trait_call_options('old-caller'))
            self.assertIn('-a',trait_call_options('old-caller'))
        with patch('bcftools_compat.subprocess.run',return_value=SimpleNamespace(stdout=help_text+' --keep-unseen-allele --format-fields')):
            self.assertIn('--keep-unseen-allele',trait_call_options('new-caller'))
            self.assertIn('-f',trait_call_options('new-caller'))
        with patch('bcftools_compat.subprocess.run',return_value=SimpleNamespace(stdout='unusable caller')):
            with self.assertRaises(ValueError):trait_call_options('bad-caller')

    def test_completed_pgs_preparation_does_not_require_unrelated_annotations(self):
        import analysis_continuation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'work/aa/completed';work.mkdir(parents=True)
            bundle=Path(__file__).resolve().parents[1]/'oncotracer_cli/lpwgs'
            (work/'bin').symlink_to(bundle/'bin',target_is_directory=True)
            config={'sample':'SAMPLE','output':str(root),'glimpse2':True,'glimpse_chromosomes':['chr1'],
                    'pgs_ids':['PGS000001'],'resources':[]}
            (work/'runtime.json').write_text(json.dumps(config))
            (work/'.command.sh').write_text('python bin/polygenic.py prepare --config runtime.json --pgs-id PGS000001')
            (work/'.command.begin').touch()
            (work/'PGS000001.weights.tsv.gz').write_bytes(gzip.compress(b'chr\tpos\tweight\nchr1\t10\t0.1\n'))
            (work/'PGS000001.model.json').write_text('{}')
            (work/'.exitcode').write_text('0')
            result=analysis_continuation.collect(config,bundle,[root],{'merge:SAMPLE':[]},
                                                 {'ligated':{'chr1':[]}},publish=False)
            self.assertIn('PGS000001',result['pgs_prepare'])
            (work/'.exitcode').write_text('1')
            rejected=analysis_continuation.collect(config,bundle,[root],{'merge:SAMPLE':[]},
                                                   {'ligated':{'chr1':[]}},publish=False)
            self.assertFalse(rejected['pgs_prepare'])

    def test_native_resume_uses_validated_continuation(self):
        import nextflow_entry
        from types import SimpleNamespace
        args=SimpleNamespace(preflight_only=True)
        with patch.object(nextflow_entry,'arguments',return_value=args) as parse, \
             patch.object(nextflow_entry.launch,'prepare',return_value={}):
            nextflow_entry.prepare_request({'parameters':{},'command_line':'nextflow run main.nf -resume previous','extra_args':['previous']})
        self.assertTrue(parse.call_args.args[0]['continue'])
        self.assertEqual(parse.call_args.args[1],[])
        config=Path(__file__).resolve().parents[1]/'oncotracer_cli/lpwgs/nextflow.config'
        self.assertIn('cache = false',config.read_text())

    def test_browser_resume_summary_is_visible_in_terminal_and_job_log(self):
        import contextlib,io,subprocess,threading
        from oncotracer_cli.web import WebState
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'job.log';message='Resume check SYNTHETIC: verified before analysis\n'
            with log.open('w') as output:
                process=subprocess.Popen([sys.executable,'-c','print('+repr(message)+',end="",flush=True)'],stdout=output)
            state=object.__new__(WebState);state.lock=threading.RLock()
            state.projects={'test':{'outdir':str(root/'outputs')}};state.result_roots={}
            job={'process':process,'log_path':str(log),'project_id':'test'}
            terminal=io.StringIO()
            with contextlib.redirect_stdout(terminal):state._wait(job)
            self.assertEqual(log.read_text(),message)
            self.assertEqual(terminal.getvalue(),message)
            self.assertEqual(job['status'],'complete')

    def test_cohort_preflight_checks_all_jobs_before_returning(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);jobs=[]
            for sample in ('A','B'):
                launch=root/'launch'/sample;launch.mkdir(parents=True)
                jobs.append((sample,root/'reads.bam',root/sample,launch,{'sample':sample,'python':sys.executable},{}))
            class Runner:
                def run(self,stage,command,**kwargs):
                    requests=json.load(kwargs['stdin'])['requests']
                    assert all(r['parameters']['preflight_only'] for r in requests)
                    json.dump({'samples':[{'run':False,'reuse_validation':{'sample':r['parameters']['sample']}} for r in requests]},kwargs['stdout'])
            self.assertEqual(preflight_jobs(jobs,Runner(),root),[{'sample':'A'},{'sample':'B'}])


if __name__=='__main__':unittest.main()
