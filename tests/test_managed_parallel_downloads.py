import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli import managed_resources as m
from oncotracer_cli.runtime import OncoTracerError


class ParallelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.target=self.root/'model.bin';self.payload=b'verified model bytes'
        self.item=dict(url='https://example.invalid/model.bin',bytes=len(self.payload),sha256=hashlib.sha256(self.payload).hexdigest(),filename='model.bin')
        self.patchers=[patch.object(m,'PARALLEL_DOWNLOAD_MIN_BYTES',1),patch.object(m,'BUNDLED_DOWNLOADER',self.root/'absent-downloader'),patch.object(m.shutil,'which',return_value='/test/aria2c')]
        for p in self.patchers:p.start();self.addCleanup(p.stop)
    def stage(self):return self.target.with_name('.'+self.target.name+'.parts-'+self.item['sha256'])
    def fake_run(self,command,check=False):
        directory=Path(next(x.split('=',1)[1] for x in command if x.startswith('--dir=')))
        (directory/'payload').write_bytes(self.payload)
        (directory/'payload.aria2').write_bytes(b'completed control')
        return subprocess.CompletedProcess(command,0)
    def test_verified_transfer_is_published_atomically_with_pinned_checks(self):
        with patch.object(m.subprocess,'run',side_effect=self.fake_run) as run:
            self.assertTrue(m._parallel_fetch(self.item,self.target))
        self.assertEqual(self.target.read_bytes(),self.payload)
        command=run.call_args.args[0]
        for value in ['--no-conf=true','--check-certificate=true','--continue=true','--max-connection-per-server=8','--checksum=sha-256='+self.item['sha256']]:self.assertIn(value,command)
        self.assertFalse((self.stage()/'payload.aria2').exists())
    def test_external_success_with_corrupt_bytes_does_not_replace_existing_target(self):
        self.target.write_bytes(b'previous cache')
        def corrupt(command,check=False):
            (self.stage()/'payload').write_bytes(b'wrong model');return subprocess.CompletedProcess(command,0)
        with patch.object(m.subprocess,'run',side_effect=corrupt):self.assertFalse(m._parallel_fetch(self.item,self.target))
        self.assertEqual(self.target.read_bytes(),b'previous cache');self.assertFalse((self.stage()/'payload').exists())
    def test_interrupted_chunks_are_retained_for_resume_without_publication(self):
        def interrupted(command,check=False):
            (self.stage()/'payload').write_bytes(b'partial');(self.stage()/'payload.aria2').write_bytes(b'chunk map')
            return subprocess.CompletedProcess(command,7)
        with patch.object(m.subprocess,'run',side_effect=interrupted):self.assertFalse(m._parallel_fetch(self.item,self.target))
        self.assertFalse(self.target.exists());self.assertTrue((self.stage()/'payload.aria2').exists())
        with patch.object(m.subprocess,'run',side_effect=self.fake_run):self.assertTrue(m._parallel_fetch(self.item,self.target))
        self.assertEqual(self.target.read_bytes(),self.payload)
    def test_cached_verified_asset_needs_no_external_process(self):
        self.target.write_bytes(self.payload)
        with patch.object(m.subprocess,'run',side_effect=AssertionError('unexpected download')):self.assertTrue(m._parallel_fetch(self.item,self.target))
    def test_missing_accelerator_leaves_standard_downloader_available(self):
        with patch.object(m.shutil,'which',return_value=None):self.assertFalse(m._parallel_fetch(self.item,self.target))
        self.assertFalse(self.stage().exists())
    def test_external_state_cannot_redirect_to_another_directory(self):
        elsewhere=self.root/'elsewhere';elsewhere.mkdir();(elsewhere/'payload').write_bytes(b'keep')
        self.stage().symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError,'symlinks'):m._parallel_fetch(self.item,self.target)
        self.assertEqual((elsewhere/'payload').read_bytes(),b'keep')
    def test_standard_fallback_cleans_chunk_data_after_verified_success(self):
        self.stage().mkdir();(self.stage()/'payload').write_bytes(b'incomplete');(self.stage()/'payload.aria2').write_bytes(b'control')
        def fallback(url,path,**kw):
            self.assertEqual(kw['expected_sha256'],self.item['sha256']);path.write_bytes(self.payload);return path
        with patch.object(m,'assets',return_value={'test':self.item}),patch.object(m,'_parallel_fetch',return_value=False),patch.object(m,'download',side_effect=fallback):
            self.assertEqual(m._fetch('test',self.root),self.target)
        self.assertEqual(self.target.read_bytes(),self.payload);self.assertFalse((self.stage()/'payload').exists());self.assertFalse((self.stage()/'payload.aria2').exists())
    def test_digest_changes_use_different_resume_state(self):
        self.stage().mkdir();(self.stage()/'payload').write_bytes(b'old bytes')
        changed=dict(self.item,sha256=hashlib.sha256(b'other bytes').hexdigest())
        seen=[]
        def failed(command,check=False):seen.append(next(x for x in command if x.startswith('--dir=')));return subprocess.CompletedProcess(command,7)
        with patch.object(m.subprocess,'run',side_effect=failed):self.assertFalse(m._parallel_fetch(changed,self.target))
        self.assertIn(changed['sha256'],seen[0]);self.assertEqual((self.stage()/'payload').read_bytes(),b'old bytes')

if __name__=='__main__':unittest.main()
