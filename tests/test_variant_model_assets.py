"""Offline contracts for explicit, checksum-verified RUN-time variant assets."""
import contextlib
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import variant_model_assets as assets
from oncotracer_cli.cli import main
from oncotracer_cli.runtime import OncoTracerError, load_flat_yaml, render_flat_yaml
from oncotracer_cli.variants import resolve_variant_request, variant_plan

PROFILE = 'r1041_e82_400bps_sup_v500'


def configuration(**updates):
    return dict(run_variants=True, variant_specimen_type='fresh', variant_callers='clair3',
                variant_annovar='off', variant_clair3_model='auto', variant_ont_profile=PROFILE, **updates)


class VariantModelAssetsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {key: '' for key in ('ONCOTRACER_FFPERASE_ROOT', 'ONCOTRACER_FFPERASE_MODELS',
                    'ONCOTRACER_FFPERASE_PREFIX', 'ONCOTRACER_FFPERASE_SIF')}).start()
        patch.object(Path, 'home', return_value=self.root / 'home').start()
        patch.object(assets.platform, 'system', return_value='Linux').start()
        patch.object(assets.platform, 'machine', return_value='x86_64').start()

    def archive(self, entries=None):
        stream = io.BytesIO()
        if entries is None:
            entries = [(PROFILE + '/' + name, b'synthetic ' + name.encode(), tarfile.REGTYPE)
                       for name in assets.CLAIR3_FILES]
        with tarfile.open(fileobj=stream, mode='w:gz') as handle:
            for name, data, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                if kind == tarfile.SYMTYPE:
                    item.linkname = '/outside/model'
                item.size = len(data) if kind == tarfile.REGTYPE else 0
                handle.addfile(item, io.BytesIO(data))
        payload = stream.getvalue()
        info = dict(assets.CLAIR3_PROFILES[PROFILE], size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                    file_sha256={name.split('/')[-1]: hashlib.sha256(data).hexdigest() for name, data, kind in entries})
        return payload, info

    def network(self, payload):
        mock = patch.object(assets.urllib.request, 'build_opener').start().return_value
        mock.open.side_effect = lambda *args, **kwargs: io.BytesIO(payload)
        return mock

    def test_catalog_has_pinned_finite_official_assets_for_tensorflow_runtime(self):
        for name, asset in assets.CLAIR3_PROFILES.items():
            self.assertTrue(asset['url'].startswith(assets.CLAIR3_BASE))
            self.assertTrue(asset['url'].endswith(name + '.tar.gz'))
            self.assertRegex(asset['sha256'], r'^[0-9a-f]{64}$')
            self.assertEqual(asset['caller_version'], '1.2.0')
            self.assertLess(asset['size'], 100 * 1024 * 1024)
            self.assertEqual(asset['files'], list(assets.CLAIR3_FILES))
        self.assertIn('ont_r10_dorado_sup_5khz_ssrs', assets.CLAIRSTO_PLATFORMS)

    def test_auto_requires_explicit_known_profile_independent_of_preservation(self):
        for profile in ('', 'auto', 'ffpe', 'r1041_e82_400bps_sup_v600', '../model'):
            values = configuration()
            values['variant_ont_profile'] = profile
            with self.subTest(profile=profile), self.assertRaisesRegex(OncoTracerError, 'explicit supported'):
                resolve_variant_request(values, mode='ont')
        values = configuration()
        values['variant_specimen_type'] = 'ffpe'
        request = resolve_variant_request(values, mode='ont')
        self.assertEqual(request.ont_profile, PROFILE)

    def test_plan_is_read_only_and_contains_checksum_and_download_size(self):
        with patch.object(assets, '_download', side_effect=AssertionError('download during check')):
            request = resolve_variant_request(configuration(), mode='ont')
            plan = variant_plan(request)
        self.assertTrue(request.clair3_auto)
        self.assertIsNone(request.clair3_model)
        self.assertEqual(plan['resource_downloads'][0]['sha256'], assets.CLAIR3_PROFILES[PROFILE]['sha256'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_custom_model_paths_remain_authoritative_without_fetching(self):
        model = self.root / 'custom model'
        model.mkdir()
        (model / 'checkpoint').write_text('custom')
        values = configuration()
        values.update(variant_clair3_model=str(model), variant_ont_profile='user-trained')
        request = resolve_variant_request(values, mode='ont')
        with patch.object(assets, '_download', side_effect=AssertionError('custom model download')):
            resolved = assets.prepare_variant_resources(request, self.root / 'output')
        self.assertEqual(resolved.clair3_model, model)
        self.assertFalse(resolved.clair3_auto)
        self.assertFalse((self.root / 'output').exists())
        values['variant_clair3_model'] = str(self.root / 'missing')
        with self.assertRaisesRegex(OncoTracerError, 'nonempty'):
            resolve_variant_request(values, mode='ont')

    def test_verified_model_is_atomic_reusable_and_request_is_immutable(self):
        payload, asset = self.archive()
        network = self.network(payload)
        request = resolve_variant_request(configuration(), mode='ont')
        with patch.dict(assets.CLAIR3_PROFILES, {PROFILE: asset}):
            resolved = assets.prepare_variant_resources(request, self.root / 'variants')
            again = assets.prepare_variant_resources(request, self.root / 'variants')
        self.assertIsNone(request.clair3_model)
        self.assertEqual(resolved.clair3_model, again.clair3_model)
        self.assertEqual(network.open.call_count, 1)
        self.assertEqual(set(p.name for p in resolved.clair3_model.iterdir()), set(assets.CLAIR3_FILES) | {'resource.json'})
        provenance = json.loads((resolved.clair3_model / 'resource.json').read_text())
        self.assertEqual(provenance['sha256'], asset['sha256'])
        self.assertEqual(provenance['profile'], PROFILE)

    def test_bad_checksum_and_truncation_leave_no_usable_cache(self):
        payload, asset = self.archive()
        for received in (payload[:-1], payload + b'extra', b'X' + payload[1:]):
            with self.subTest(length=len(received)), tempfile.TemporaryDirectory(dir=self.root) as folder:
                self.network(received)
                with patch.dict(assets.CLAIR3_PROFILES, {PROFILE: asset}), self.assertRaisesRegex(OncoTracerError, 'bound|verification'):
                    assets.ensure_clair3_model(PROFILE, Path(folder))
                self.assertFalse((Path(folder) / PROFILE).exists())
                self.assertFalse(any(p.is_dir() for p in Path(folder).iterdir()))

    def test_incomplete_archive_and_unsafe_members_are_rejected(self):
        examples = (
            [(PROFILE + '/pileup.index', b'only-one', tarfile.REGTYPE)],
            [('../outside', b'bad', tarfile.REGTYPE)],
            [(PROFILE + '/pileup.index', b'', tarfile.SYMTYPE)],
            [('/outside', b'bad', tarfile.REGTYPE)],
        )
        for entries in examples:
            payload, asset = self.archive(entries)
            with self.subTest(entries=entries), tempfile.TemporaryDirectory(dir=self.root) as folder:
                self.network(payload)
                with patch.dict(assets.CLAIR3_PROFILES, {PROFILE: asset}), self.assertRaisesRegex(OncoTracerError, 'archive'):
                    assets.ensure_clair3_model(PROFILE, Path(folder))
                self.assertFalse((Path(folder) / PROFILE).exists())
        self.assertFalse((self.root / 'outside').exists())

    def test_corrupt_cache_is_not_silently_overwritten_or_redownloaded(self):
        payload, asset = self.archive()
        network = self.network(payload)
        with patch.dict(assets.CLAIR3_PROFILES, {PROFILE: asset}):
            model = assets.ensure_clair3_model(PROFILE, self.root)
            changed = model / 'pileup.index'
            changed.write_bytes(b'changed')
            with self.assertRaisesRegex(OncoTracerError, 'incomplete or changed'):
                assets.ensure_clair3_model(PROFILE, self.root)
        self.assertEqual(changed.read_bytes(), b'changed')
        self.assertEqual(network.open.call_count, 1)

    def test_symlink_cache_and_symlink_model_are_rejected(self):
        real = self.root / 'existing'
        real.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, 'symlink'):
            assets.ensure_clair3_model(PROFILE, alias)
        (real / PROFILE).symlink_to(self.root / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, 'symlink'):
            assets.ensure_clair3_model(PROFILE, real)
        self.assertFalse((self.root / 'outside').exists())

    def test_non_https_downloads_and_redirects_are_rejected(self):
        with self.assertRaisesRegex(OncoTracerError, 'HTTPS'):
            assets._download({'url': 'http://example.test/model'}, self.root / 'model')
        with self.assertRaisesRegex(OncoTracerError, 'HTTPS'):
            assets._HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'file:///tmp/model')

    def ffpe_request(self, **updates):
        values = dict(run_variants=True, variant_specimen_type='ffpe', variant_callers='mutect2',
                      variant_download_resources=True, variant_accept_ffperase_license=True,
                      variant_annovar='off')
        values.update(updates)
        return resolve_variant_request(values, mode='illumina')

    def custom_ffpe(self):
        source, models = self.root / 'source', self.root / 'models'
        for folder, names in ((source, assets.FFPERASE_SOURCE_FILES), (models, assets.FFPERASE_MODEL_FILES)):
            for name in names:
                if name == 'LICENSE':
                    continue
                file = folder / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'custom fixture not executed')
        return source, models

    def test_missing_ffperase_requires_explicit_download_and_terms_acknowledgment(self):
        with self.assertRaisesRegex(OncoTracerError, 'variant_accept_ffperase_license'):
            self.ffpe_request(variant_accept_ffperase_license=False)
        manual = self.ffpe_request(variant_download_resources=False, variant_accept_ffperase_license=False)
        self.assertEqual(assets.resource_download_plan(manual), [])
        auto = self.ffpe_request()
        self.assertEqual({item['resource'] for item in assets.resource_download_plan(auto)}, {'ffperase_source', 'ffperase_models'})
        self.assertFalse(any(self.root.iterdir()))

    def test_existing_ffperase_assets_require_no_new_download_ack_and_are_preserved(self):
        source, models = self.custom_ffpe()
        request = self.ffpe_request(variant_ffperase_root=str(source), variant_ffperase_models=str(models),
                                   variant_accept_ffperase_license=False)
        with patch.object(assets, '_download', side_effect=AssertionError('downloaded custom FFPErase')):
            result = assets.prepare_variant_resources(request, self.root / 'output')
        self.assertEqual((result.ffperase_root, result.ffperase_models), (source, models))
        self.assertFalse((self.root / 'output').exists())

    def test_invalid_explicit_ffperase_path_is_not_replaced_by_download(self):
        with self.assertRaisesRegex(OncoTracerError, 'will not be overwritten'):
            self.ffpe_request(variant_ffperase_root=str(self.root / 'missing'))

    def test_ffperase_downloads_only_missing_models_and_keeps_custom_source(self):
        source, _ = self.custom_ffpe()
        prefix = self.root / 'runtime'
        (prefix / 'bin').mkdir(parents=True)
        (prefix / 'bin/python').write_bytes(b'fixture runtime; never invoked')
        content = b'synthetic joblib fixture; never deserialized'
        descriptor = {'url': 'https://example.test/pinned/model', 'size': len(content),
                      'sha256': hashlib.sha256(content).hexdigest()}
        models = {name: dict(descriptor) for name in assets.FFPERASE_MODEL_FILES}
        network = self.network(content)
        request = self.ffpe_request(variant_ffperase_root=str(source), variant_ffperase_prefix=str(prefix))
        with patch.dict(assets.FFPERASE_MODEL_FILES, models, clear=True), \
             patch('subprocess.run', side_effect=AssertionError('resource preparation executed code')):
            result = assets.prepare_variant_resources(request, self.root / 'output')
        self.assertEqual(result.ffperase_root, source)
        self.assertIsNone(request.ffperase_models)
        self.assertEqual(network.open.call_count, 2)
        self.assertEqual((result.ffperase_models / 'model.snvs.joblib').read_bytes(), content)
        self.assertFalse(any(p.name.startswith('ffperase-source') for p in (self.root / 'output/resources').iterdir()))

    def test_ffperase_requires_existing_runtime_before_any_download(self):
        request = self.ffpe_request()
        with patch.object(assets, '_download', side_effect=AssertionError('download before runtime')), self.assertRaisesRegex(OncoTracerError, 'runtime'):
            assets.prepare_variant_resources(request, self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_ffperase_auto_source_rejects_wrong_architecture(self):
        with patch.object(assets.platform, 'machine', return_value='aarch64'), self.assertRaisesRegex(OncoTracerError, 'Linux x86_64'):
            self.ffpe_request()

    def test_audited_file_bundle_verifies_checksums_preserves_mode_and_reuses(self):
        content = b'synthetic executable; never run'
        files = {'bin/tool': {'url': 'https://example.test/pinned/tool', 'sha256': hashlib.sha256(content).hexdigest(),
                              'size': len(content), 'executable': True}}
        network = self.network(content)
        with patch('subprocess.run', side_effect=AssertionError('resource prep executed code')):
            source = assets.ensure_file_bundle('synthetic-source', files, self.root)
            again = assets.ensure_file_bundle('synthetic-source', files, self.root)
        self.assertEqual(source, again)
        self.assertTrue(os.access(source / 'bin/tool', os.X_OK))
        self.assertEqual(network.open.call_count, 1)
        # Changing both the file and local provenance must still fail the pinned digest.
        changed = source / 'bin/tool'
        changed.write_bytes(b'tampered')
        marker = source / 'resource.json'
        provenance = json.loads(marker.read_text())
        provenance['files']['bin/tool'] = hashlib.sha256(changed.read_bytes()).hexdigest()
        marker.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(OncoTracerError, 'incomplete or changed'):
            assets.ensure_file_bundle('synthetic-source', files, self.root)

    def test_setup_and_existing_bam_dry_run_save_auto_without_downloads(self):
        reads = self.root / 'reads/barcode01/reads.fastq.gz'
        reads.parent.mkdir(parents=True)
        with gzip.open(reads, 'wt') as handle:
            handle.write('@synthetic\nACGT\n+\nIIII\n')
        project = self.root / 'project'
        flags = ['setup', '--non-interactive', '--project', str(project), '--mode', 'ont',
                 '--reads-folder', str(reads.parent.parent), '--barcodes', 'barcode01', '--sample-names', 'SYNTHETIC',
                 '--backend', 'conda', '--variants', '--variant-specimen-type', 'fresh', '--variant-callers', 'clair3',
                 '--variant-clair3-model', 'auto', '--variant-ont-profile', PROFILE, '--variant-annovar', 'off']
        output = io.StringIO()
        with patch.object(assets, '_download', side_effect=AssertionError('download during setup')), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(flags), 0, output.getvalue())
            config = project / 'config/run.yml'
            self.assertEqual(main(['check', '--config', str(config)]), 0, output.getvalue())
            self.assertEqual(main(['run', '--backend', 'conda', '--config', str(config), '--dry-run']), 0, output.getvalue())
        saved = load_flat_yaml(config)
        self.assertEqual(saved['variant_clair3_model'], 'auto')
        self.assertEqual(saved['variant_ont_profile'], PROFILE)
        self.assertFalse((project / 'results').exists())
        self.assertFalse((project / 'reference').exists())
        reference = self.root / 'ref.fa'; reference.write_text('>chr1\nACGT\n')
        bam = self.root / 'sample.bam'; bam.write_bytes(b'path fixture; never decoded')
        sheet = self.root / 'bams.tsv'; sheet.write_text('sample\tbam\tstatus\nSYNTHETIC\tsample.bam\ttumor\n')
        config = self.root / 'variants.yml'
        values = configuration()
        values.update(mode='ont', outdir=str(self.root / 'bam-results'), variant_reference=str(reference), variant_bam_manifest=str(sheet))
        config.write_text(render_flat_yaml(values))
        with patch.object(assets, '_download', side_effect=AssertionError('download during dry-run')), contextlib.redirect_stdout(output):
            self.assertEqual(main(['variants', '--config', str(config), '--dry-run']), 0)
        self.assertFalse((self.root / 'bam-results').exists())

    def test_docker_preflight_defers_assets_but_checks_ffperase_runtime(self):
        from oncotracer_cli.docker_runtime import preflight
        values = dict(run_variants=True, mode='illumina', variant_specimen_type='ffpe', variant_callers='mutect2',
                      variant_download_resources=True, variant_accept_ffperase_license=True, variant_annovar='off')
        config = self.root / 'docker.yml'
        config.write_text(render_flat_yaml(values))
        with patch('oncotracer_cli.variants.preflight_variant_tools'), \
             patch.object(assets, 'preflight_ffperase_runtime') as runtime, \
             patch('oncotracer_cli.ffperase.discover', side_effect=AssertionError('assets required before run')), \
             patch.object(assets, '_download', side_effect=AssertionError('preflight downloaded')), \
             contextlib.redirect_stdout(io.StringIO()):
            preflight(str(config))
        runtime.assert_called_once()
        self.assertEqual(list(self.root.iterdir()), [config])

    def test_discovery_treats_selected_auto_profile_as_deferred_not_a_missing_path(self):
        from oncotracer_cli.variant_resources import discover_variant_resources
        environment = {'HOME': str(self.root), 'PATH': '', 'CONDA_PREFIX': None,
                       'ONCOTRACER_VARIANTS_PREFIX': None, 'ANNOVAR_HOME': None, 'ANNOVAR_DB': None}
        payload = dict(mode='ont', backend='docker', callers=['clair3'], specimen_type='fresh',
                       values={'variant_clair3_model': 'auto', 'variant_ont_profile': PROFILE,
                               'variant_download_resources': False, 'variant_accept_ffperase_license': False,
                               'variant_annovar': 'off'})
        with patch.object(assets, '_download', side_effect=AssertionError('autodetect downloaded')):
            result = discover_variant_resources(payload, roots=[self.root], environment=environment)
        model, = [item for item in result['resources'] if item['id'] == 'clair3_model']
        self.assertEqual(model['status'], 'unverified')
        self.assertIn('RUN only', model['detail'])
        self.assertEqual(result['fields']['variant_clair3_model'], 'auto')
        self.assertNotIn('clair3_model', [item['id'] for item in result['install_guides']])

    def test_browser_roundtrip_preserves_auto_and_boolean_options_without_fetch(self):
        from oncotracer_cli.web import WebState
        from tests.test_wizard import HARDWARE
        reads = self.root / 'reads/barcode01/read.fastq.gz'
        reads.parent.mkdir(parents=True)
        with gzip.open(reads, 'wt') as handle:
            handle.write('@synthetic\nACGT\n+\nIIII\n')
        state = WebState(self.root)
        state.hardware = HARDWARE
        scan = state.scan({'mode': 'ont', 'folder': str(reads.parent.parent)})
        data = {'scan_id': scan['scan_id'], 'project': str(self.root / 'browser'), 'mode': 'ont', 'analysis': 'cna',
                'backend': 'conda', 'threads': 1, 'samples': [{'id': scan['samples'][0]['id'], 'name': 'SYNTHETIC', 'type': 'cancer'}],
                'variants': True, 'variant_specimen_type': 'fresh', 'variant_callers': 'clair3',
                'variant_clair3_model': 'auto', 'variant_ont_profile': PROFILE, 'variant_annovar': 'off',
                'variant_download_resources': False, 'variant_accept_ffperase_license': False}
        with patch.object(assets, '_download', side_effect=AssertionError('browser preparation downloaded')), \
             contextlib.redirect_stdout(io.StringIO()):
            saved = state.prepare(data)
        self.assertTrue(saved['valid'], saved['check'])
        config = load_flat_yaml(Path(saved['config_path']))
        self.assertEqual(config['variant_clair3_model'], 'auto')
        self.assertEqual(config['variant_ont_profile'], PROFILE)
        self.assertIs(config['variant_download_resources'], False)
        self.assertFalse((self.root / 'browser/results').exists())

    def test_boolean_download_settings_reject_truthy_strings(self):
        for field in ('variant_download_resources', 'variant_accept_ffperase_license'):
            for value in ('false', 'true', 1, []):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(OncoTracerError, 'true or false'):
                    resolve_variant_request(configuration(**{field: value}), mode='ont')


if __name__ == '__main__':
    unittest.main()
