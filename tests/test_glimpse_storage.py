"""Retirement of completed scratch must preserve real outputs and continuation."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from oncotracer_cli.glimpse_pipeline import BUNDLE, validate_options
from oncotracer_cli.glimpse_storage import PhaseScratchCompactor, _validate_bcf
from oncotracer_cli.runtime import OncoTracerError

BCFTOOLS = shutil.which('bcftools')


def real_bcf(path, sample='SAMPLE'):
    vcf = path.with_suffix('.source.vcf')
    vcf.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n'
                   '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
                   '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t' + sample + '\n'
                   'chr1\t10\t.\tA\tC\t50\tPASS\t.\tGT\t0/1\n')
    subprocess.run([BCFTOOLS, 'view', '-Ob', '-o', str(path), str(vcf)], check=True, capture_output=True)
    subprocess.run([BCFTOOLS, 'index', '-f', str(path)], check=True, capture_output=True)


@unittest.skipUnless(BCFTOOLS, 'bcftools required for real BCF/CSI integrity tests')
class CompactStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'pipeline_info').mkdir()
        self.work = self.root/'work/ab/012345'
        self.work.mkdir(parents=True)
        (self.work/'bin').symlink_to(BUNDLE/'bin', target_is_directory=True)
        config = {'sample':'SAMPLE','glimpse_chromosomes':['chr1'],'root':str(BUNDLE)}
        raw = json.dumps(config).encode()
        self.runtime = self.work/(hashlib.sha256(raw).hexdigest()+'.json')
        self.runtime.write_bytes(raw)
        (self.work/'.command.begin').write_text('')
        self.command = '/usr/bin/python3 bin/glimpse2.py phase ' + shlex.join([
            '--config', self.runtime.name, '--chrom', 'chr1', '--threads', '1',
            '--chunk', '0', '--input-region', 'chr1:1-1000', '--output-region', 'chr1:1-1000',
            '--reference', 'reference.chr1.bcf', '--map', 'map.chr1.txt', '--bam', 'SAMPLE.bam'])
        (self.work/'.command.sh').write_text('#!/bin/bash -euo pipefail\n'+self.command+'\n')
        self.bcf = self.work/'SAMPLE.chr1.0.bcf'
        real_bcf(self.bcf)
        self.binary = self.work/'split_chr1_1_1000.bin'
        self.binary.write_bytes(b'reproducible split reference'*1000)
        self.end = self.work/'.exitcode'
        self.end.write_text('0')
        self.compactor = PhaseScratchCompactor(self.root, 'SAMPLE', BUNDLE, mode='compact', interval=.02)

    def test_audit_is_read_only_and_apply_keeps_every_non_scratch_file(self):
        untouched = {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.work.iterdir()
                     if p.is_file() and p != self.binary}
        result = self.compactor.scan()
        self.assertEqual(result['candidate_files'],1)
        self.assertTrue(self.binary.exists())
        self.assertFalse(self.compactor.audit_path.exists())
        self.compactor.scan(apply=True)
        self.assertFalse(self.binary.exists())
        for p, identity in untouched.items():self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),identity)
        self.assertEqual(subprocess.check_output([BCFTOOLS,'query','-f','[%GT]\n',self.bcf],text=True),'0/1\n')
        self.assertEqual(json.loads((self.work/'.oncotracer-storage.json').read_text())['state'],'removed')
        self.assertEqual(self.compactor.summary['removed_files'],1)
        self.compactor.scan(apply=True)
        self.assertEqual(self.compactor.summary['removed_files'],1)

    def test_full_mode_never_retires_scratch(self):
        with PhaseScratchCompactor(self.root,'SAMPLE',BUNDLE):pass
        self.assertTrue(self.binary.exists())
        self.assertFalse(self.compactor.audit_path.exists())

    def test_failed_and_running_tasks_are_retained(self):
        self.end.write_text('1');self.compactor.scan(apply=True)
        self.assertTrue(self.binary.exists())
        self.end.unlink();self.compactor.scan(apply=True)
        self.assertTrue(self.binary.exists())

    def test_corrupt_output_or_index_is_retained(self):
        for p in (self.bcf,Path(str(self.bcf)+'.csi')):
            with self.subTest(p=p):
                raw=p.read_bytes(); stamp=p.stat().st_mtime_ns
                p.write_bytes(raw[:len(raw)//2]);os.utime(p,ns=(stamp,stamp))
                self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())
                p.write_bytes(raw);os.utime(p,ns=(stamp,stamp))

    def test_changed_output_and_wrong_sample_are_retained(self):
        os.utime(self.bcf,ns=(self.end.stat().st_mtime_ns+1000000,)*2)
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())
        real_bcf(self.bcf,'OTHER');self.end.write_text('0')
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())

    def test_symlink_scratch_output_and_work_directory_are_retained(self):
        for p in (self.binary,self.bcf):
            with self.subTest(p=p):
                saved=self.root/(p.name+'.saved');p.rename(saved);p.symlink_to(saved)
                self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())
                p.unlink();saved.rename(p)
        moved=self.root/'elsewhere';self.work.rename(moved);self.work.symlink_to(moved,target_is_directory=True)
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())

    def test_other_stage_or_untrusted_runtime_is_retained(self):
        script=self.work/'.command.sh';script.write_text(self.command.replace(' phase ',' reference '))
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())
        script.write_text(self.command);self.runtime.write_text('{}')
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())

    def test_custom_worker_or_workflow_is_retained(self):
        (self.work/'bin').unlink();(self.work/'bin').mkdir()
        (self.work/'bin/glimpse2.py').write_text('different worker')
        self.compactor.scan(apply=True);self.assertTrue(self.binary.exists())

    def test_mutation_during_output_validation_is_retained(self):
        def change(path,sample):
            _validate_bcf(path,sample);self.binary.write_bytes(b'new task scratch')
        with patch('oncotracer_cli.glimpse_storage._validate_bcf',side_effect=change):
            self.compactor.scan(apply=True)
        self.assertTrue(self.binary.exists())

    def test_watcher_cleans_a_completed_task_while_workflow_stays_open(self):
        self.end.unlink()
        with self.compactor:
            self.assertTrue(self.binary.exists())
            self.end.write_text('0')
            deadline=time.monotonic()+3
            while self.binary.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertFalse(self.binary.exists())
        self.assertEqual(self.compactor.summary['removed_files'],1)

    def test_controller_cancellation_stops_optional_cleanup(self):
        import threading
        cancel=threading.Event();cancel.set()
        with PhaseScratchCompactor(self.root,'SAMPLE',BUNDLE,mode='compact',cancel=cancel):pass
        self.assertTrue(self.binary.exists())

    def test_failure_to_record_intent_keeps_scratch(self):
        with patch('oncotracer_cli.glimpse_storage.atomic_write_json',side_effect=OSError('disk full')):
            self.compactor._scan_safely()
        self.assertTrue(self.binary.exists())

    def test_existing_continuation_keeps_phase_and_ligation_after_retirement(self):
        from test_glimpse_multisample_reuse import MultisampleReuseTests
        helper=MultisampleReuseTests()
        with tempfile.TemporaryDirectory() as folder:
            fixture=helper.fixture(Path(folder))
            config,code,donor,core,shared,phase,ligate=fixture
            shutil.copy2(BUNDLE/'main.nf',code/'main.nf')
            bcf=phase/'SAMPLE.chr1.0.bcf';stamp=bcf.stat().st_mtime_ns
            real_bcf(bcf)
            for p in (bcf,Path(str(bcf)+'.csi')):os.utime(p,ns=(stamp,stamp))
            binary=phase/'split_chr1_1_1000.bin';binary.write_bytes(b'reproducible scratch')
            os.utime(binary,ns=(stamp,stamp))
            before=helper.collect(fixture)
            compactor=PhaseScratchCompactor(donor,'SAMPLE',code,mode='compact')
            compactor.scan(apply=True)
            self.assertFalse(binary.exists())
            self.assertEqual(before,helper.collect(fixture))
            self.assertEqual(len(before['phases']),1);self.assertEqual(len(before['ligated']),1)


class StorageChoiceTests(unittest.TestCase):
    def test_cli_overrides_reach_containers_and_host_without_rewriting_yaml(self):
        from oncotracer_cli.cli import parse_arguments, _run_docker, _run_host
        for entry in [['run','--config','run.yml'],['internal-run','--config','run.yml'],
                      ['variants','--config','run.yml'],['setup'],['--interactive']]:
            self.assertIsNone(parse_arguments(entry).variant_storage_mode)
            for mode in ('full','compact'):
                self.assertEqual(parse_arguments([*entry,'--storage-mode',mode]).variant_storage_mode,mode)
        config=Path('/data/study/config/run.yml')
        args=parse_arguments(['run','--config',str(config),'--backend','docker','--dry-run','--storage-mode','compact'])
        with patch('oncotracer_cli.cli._load_install_config',return_value={}),patch('oncotracer_cli.cli._project_mounts',return_value=[config.parent]),patch('oncotracer_cli.cli.load_flat_yaml',return_value={}),patch('oncotracer_cli.cli._run') as execute:
            _run_docker(config,args)
        argv=execute.call_args.args[0];internal=list(map(str,argv[argv.index('internal-run'):]))
        self.assertEqual(parse_arguments(internal).variant_storage_mode,'compact')
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'run.yml';config.write_text('mode: illumina\nrun_variants: false\nvariant_storage_mode: full\n')
            before=config.read_bytes();args=parse_arguments(['run','--config',str(config),'--backend','host','--dry-run','--storage-mode','compact'])
            with patch('oncotracer_cli.cli._load_install_config',return_value={}),patch('oncotracer_cli.cli.run_native',return_value=Path(folder)/'results') as native:
                _run_host(config,args)
            self.assertEqual(native.call_args.kwargs['storage_mode'],'compact');self.assertEqual(config.read_bytes(),before)

    def test_configuration_is_opt_in_and_strict(self):
        self.assertEqual(validate_options({})['storage_mode'],'full')
        self.assertEqual(validate_options({'variant_storage_mode':'compact'})['storage_mode'],'compact')
        for value in (True,False,0,'zip','delete',{},[]):
            with self.subTest(value=value),self.assertRaises(OncoTracerError):
                validate_options({'variant_storage_mode':value})
        with self.assertRaisesRegex(OncoTracerError,'bundled'):
            validate_options({'variant_storage_mode':'compact','variant_workflow_source':'/custom'})
