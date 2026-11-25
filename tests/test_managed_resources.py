import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from oncotracer_cli.managed_resources import _extract,_fetch,_installed_tree,assets,prepare_private_sturgeon,validate_installation
from oncotracer_cli.runtime import OncoTracerError


class ManagedResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_archive_traversal_and_symlink_parent_are_rejected(self):
        cases=[('../escape',None),('/absolute',None),('link','../escape')]
        for name,target in cases:
            archive=self.root/'bad.tar'
            with tarfile.open(archive,'w') as t:
                member=tarfile.TarInfo(name)
                if target:member.type=tarfile.SYMTYPE;member.linkname=target
                else:member.size=1
                t.addfile(member,None if target else io.BytesIO(b'x'))
            with self.assertRaises(OncoTracerError):_extract(archive,self.root/'output')
        with tarfile.open(self.root/'bad.tar','w') as t:
            member=tarfile.TarInfo('link');member.type=tarfile.SYMTYPE;member.linkname='directory';t.addfile(member)
            member=tarfile.TarInfo('link/payload');member.size=1;t.addfile(member,io.BytesIO(b'x'))
        with self.assertRaises(OncoTracerError):_extract(self.root/'bad.tar',self.root/'output')

    def test_valid_dorado_library_link_stays_inside_extraction(self):
        archive=self.root/'safe.tar';out=self.root/'out';out.mkdir()
        with tarfile.open(archive,'w') as t:
            member=tarfile.TarInfo('lib/actual.so');member.size=4;t.addfile(member,io.BytesIO(b'data'))
            member=tarfile.TarInfo('lib/alias.so');member.type=tarfile.SYMTYPE;member.linkname='actual.so';t.addfile(member)
        _extract(archive,out)
        self.assertEqual((out/'lib/alias.so').read_bytes(),b'data')
        self.assertTrue((out/'lib/alias.so').resolve().is_relative_to(out))

    def test_zip_traversal_and_symlinks_are_rejected(self):
        archive=self.root/'bad.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape',b'x')
        with self.assertRaises(OncoTracerError):_extract(archive,self.root/'output')
        with zipfile.ZipFile(archive,'w') as z:
            info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16;z.writestr(info,'elsewhere')
        with self.assertRaises(OncoTracerError):_extract(archive,self.root/'output')

    def test_no_licensed_download_or_directory_creation_without_acknowledgement(self):
        with patch('oncotracer_cli.managed_resources.download',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(OncoTracerError,'license'):
                prepare_private_sturgeon(self.root/'private',acknowledged=False)
        self.assertFalse((self.root/'private').exists())

    def test_all_assets_have_public_https_locations_and_pinned_hashes(self):
        records=assets()
        self.assertIn('marlin_model',records);self.assertIn('dorado',records)
        for name,item in records.items():
            self.assertTrue(item['url'].startswith('https://'),name)
            self.assertRegex(item['sha256'],r'^[0-9a-f]{64}$')
            self.assertEqual(Path(item['filename']).name,item['filename'])
        self.assertEqual(records['dorado']['version'],'1.1.1+e72f1492')

    def test_a_corrupt_bundle_cannot_be_copied_into_the_user_cache(self):
        bundle=self.root/'bundle';cache=self.root/'cache';bundle.mkdir();cache.mkdir()
        name=assets()['marlin_features']['filename'];(bundle/name).write_bytes(b'corrupt')
        with patch('oncotracer_cli.managed_resources.download',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(OncoTracerError,'checksum'):_fetch('marlin_features',cache,bundled=bundle)
        self.assertFalse((cache/name).exists())

    def test_managed_execution_checks_private_source_files(self):
        import hashlib
        root=self.root/'runtime'
        def install(stage):stage.mkdir();(stage/'code.py').write_text('original')
        _installed_tree(root,assets()['sturgeon_source']['sha256'],install)
        receipt=root/'.oncotracer-install.json';expected=hashlib.sha256(receipt.read_bytes()).hexdigest()
        self.assertEqual(validate_installation(str(receipt),expected,'sturgeon_source'),expected)
        validate_installation.cache_clear()  # A fresh planning/execution process.
        (root/'code.py').write_text('changed')
        with self.assertRaisesRegex(OncoTracerError,'files changed'):
            validate_installation(str(receipt),expected,'sturgeon_source')

    def test_installation_receipt_cannot_be_redirected(self):
        root=self.root/'installation';root.mkdir()
        outside=self.root/'external.json';outside.write_text('{}')
        (root/'.oncotracer-install.json').symlink_to(outside)
        with self.assertRaisesRegex(OncoTracerError,'symlink'):_installed_tree(root,'id',lambda stage:None)
        self.assertEqual(outside.read_text(),'{}')

if __name__=='__main__':unittest.main()
