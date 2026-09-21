"""Integrated Docker caller isolation and FASTQ/resource binding contracts."""
import os
import io
import json
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import cli, docker_runtime
from oncotracer_cli.runtime import OncoTracerError, OncoTracerPartialFailure, OncoTracerCommandError, render_flat_yaml, sha256_file
from oncotracer_cli.variants import resolve_variant_request


class DockerVariantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.external = self.root / 'outside project'
        self.external.mkdir()
        self.fastq = self.external / 'R1.fastq.gz'
        self.fastq.write_bytes(b'FASTQ path fixture')
        self.link = self.project / 'R1.fastq.gz'
        self.link.symlink_to(self.fastq)
        self.sheet = self.project / 'samples.csv'
        self.sheet.write_text(f'sample,fastq_1,fastq_2,status\nSAMPLE,{self.link},,tumor\n')
        self.bed = self.external / 'targets.bed'
        self.bed.write_text('chr1\t0\t100\n')
        self.outdir = self.project / 'outputs' / 'study'
        self.config_path = self.project / 'run.yml'
        self.config = dict(mode='illumina', lpwgs_root=str(self.project), outdir=str(self.outdir),
                           illumina_samplesheet=str(self.sheet), run_variants=True,
                           variant_specimen_type='fresh', variant_callers='bcftools',
                           variant_annovar='off', variant_targets_bed=str(self.bed),
                           variant_tool_prefix='/host/environment/does-not-exist',
                           docker_image='local/image:tested')
        self.save()

    def save(self):
        self.config_path.write_text(render_flat_yaml(self.config))

    def args(self, *extra):
        return cli.build_parser().parse_args(['run','--config',str(self.config_path),'--backend','docker',*extra])

    def test_preview_preserves_yaml_and_never_requires_host_tools(self):
        original = self.config_path.read_bytes()
        docker_runtime.validate_docker_variants(self.config)
        with docker_runtime.docker_host_preview():
            request = resolve_variant_request(self.config, mode='illumina')
        self.assertEqual(request.tool_prefix, Path('/host/environment/does-not-exist'))
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse(self.outdir.exists())
        with self.assertRaisesRegex(OncoTracerError, 'bin directory'):
            resolve_variant_request(self.config, mode='illumina')

    def test_container_uses_image_prefixes_without_rewriting_host_config(self):
        prefix = self.root/'image-prefix'
        (prefix/'bin').mkdir(parents=True)
        with patch.dict(os.environ, {'ONCOTRACER_CONTAINER_RUNTIME':'docker',
                                     'ONCOTRACER_VARIANTS_PREFIX':str(prefix),
                                     'ONCOTRACER_FFPERASE_PREFIX':'/image/ffperase'}):
            request=resolve_variant_request(self.config, mode='illumina')
        self.assertEqual(request.tool_prefix, prefix)
        self.assertEqual(request.ffperase_prefix,Path('/image/ffperase'))
        self.assertEqual(self.config['variant_tool_prefix'],'/host/environment/does-not-exist')

    def test_external_fastqs_symlinks_and_resources_are_read_only(self):
        resources = self.external/'models'
        resources.mkdir()
        (resources/'model.bin').write_bytes(b'model')
        self.config['variant_ffperase_models']=str(resources)
        self.save()
        mounts=dict(docker_runtime.docker_mounts(self.config_path,environment={}))
        for path in (self.fastq,self.link,self.sheet,self.bed,self.config_path,resources):
            self.assertEqual(mounts[path],'ro',path)
        self.assertEqual(mounts[self.outdir],'rw')
        self.assertEqual(mounts[self.project/'.oncotracer'],'rw')
        self.assertFalse((self.project/'.oncotracer').exists())

    def test_nested_resource_symlink_targets_are_read_only_and_cycles_terminate(self):
        models=self.project/'models'
        models.mkdir()
        external_models=self.external/'shared_models'
        external_models.mkdir()
        archive=self.root/'model_archive'
        archive.mkdir()
        checkpoint=archive/'checkpoint.bin'
        checkpoint.write_bytes(b'model fixture')
        (models/'latest').symlink_to(external_models,target_is_directory=True)
        (external_models/'checkpoint').symlink_to(checkpoint)
        (external_models/'cycle').symlink_to(models,target_is_directory=True)
        self.config['variant_ffperase_models']=str(models)
        self.save()
        mounts=dict(docker_runtime.docker_mounts(self.config_path,environment={}))
        self.assertEqual(mounts[external_models],'ro')
        self.assertEqual(mounts[checkpoint],'ro')
        self.assertEqual(mounts[external_models/'checkpoint'],'ro')
        self.assertFalse(self.outdir.exists())

    def test_symlinked_resource_target_cannot_be_inside_output(self):
        models=self.project/'models'
        models.mkdir()
        self.outdir.mkdir(parents=True)
        checkpoint=self.outdir/'existing-model.bin'
        checkpoint.write_bytes(b'preserved')
        (models/'checkpoint').symlink_to(checkpoint)
        self.config['variant_ffperase_models']=str(models)
        self.save()
        with self.assertRaisesRegex(OncoTracerError,'overlaps a read-only'):
            docker_runtime.docker_mounts(self.config_path,environment={})
        self.assertEqual(checkpoint.read_bytes(),b'preserved')

    def test_output_cannot_overlap_selected_input_directory(self):
        self.config['variant_ffperase_models']=str(self.project)
        self.save()
        with self.assertRaisesRegex(OncoTracerError, 'overlaps a read-only'):
            docker_runtime.docker_mounts(self.config_path,environment={})
        self.assertFalse(self.outdir.exists())

    def test_home_relative_yaml_paths_fail_before_download_or_output_creation(self):
        for key in ('lpwgs_root','outdir','illumina_samplesheet','variant_targets_bed','variant_ffperase_root'):
            with self.subTest(key=key):
                original=self.config[key] if key in self.config else None
                self.config[key]='~/project/input'
                self.save()
                before=self.config_path.read_bytes()
                with patch.object(cli,'_prepare_configured_hg38') as download, patch.object(cli,'_run') as run:
                    with self.assertRaisesRegex(OncoTracerError,"starts with '~'"):
                        cli.execute_run(self.config_path,self.args())
                download.assert_not_called()
                run.assert_not_called()
                self.assertEqual(before,self.config_path.read_bytes())
                self.assertFalse(self.outdir.exists())
                if original is None:
                    self.config.pop(key)
                else:
                    self.config[key]=original
        self.save()

    def test_home_relative_fastq_csv_path_is_rejected_before_download(self):
        self.sheet.write_text('sample,fastq_1,fastq_2,status\nSAMPLE,~/input/R1.fq.gz,,tumor\n')
        with patch.object(cli,'_prepare_configured_hg38') as download:
            with self.assertRaisesRegex(OncoTracerError,'fastq_1 in samplesheet row 2'):
                cli.execute_run(self.config_path,self.args())
        download.assert_not_called()
        self.assertFalse(self.outdir.exists())

    def test_explicit_host_resource_typo_is_rejected_during_validation(self):
        for key in ('variant_ffperase_root', 'variant_ffperase_models', 'variant_annovar_dir', 'variant_annovar_db'):
            with self.subTest(key=key), self.assertRaisesRegex(OncoTracerError, 'existing host directory'):
                docker_runtime.validate_docker_variants({**self.config,key:str(self.root/'missing')})

    def test_sif_is_rejected_before_container_or_download(self):
        self.config['variant_clairsto_sif']='/some/image.sif'
        self.save()
        with patch.object(cli,'_prepare_configured_hg38') as download, patch.object(cli,'_run') as run:
            with self.assertRaisesRegex(OncoTracerError, 'clear variant_clairsto_sif'):
                cli.execute_run(self.config_path,self.args())
        download.assert_not_called()
        run.assert_not_called()

    def test_preflight_and_analysis_use_same_image_mounts_uid_and_yaml(self):
        original=self.config_path.read_bytes()
        with patch.object(cli,'_load_install_config',return_value={'image':'stale/image'}), \
             patch.object(cli,'_run') as run, patch.object(cli.os,'getuid',return_value=1007), \
             patch.object(cli.os,'getgid',return_value=1008):
            cli._run_docker(self.config_path,self.args('--dry-run'))
        self.assertEqual(run.call_count,2)
        first,last = [list(map(str,call.args[0])) for call in run.call_args_list]
        self.assertIn('--entrypoint',first)
        self.assertIn('internal-run',last)
        for command in (first,last):
            self.assertIn('local/image:tested',command)
            self.assertIn('1007:1008',command)
            self.assertIn('ONCOTRACER_CONTAINER_RUNTIME=docker',command)
            self.assertIn(f'{self.fastq}:{self.fastq}:ro',command)
            self.assertIn(f'{self.outdir}:{self.outdir}:rw',command)
            self.assertIn(str(self.config_path),command)
            self.assertNotIn('/host/environment/does-not-exist',command)
        self.assertFalse(self.outdir.exists())
        self.assertEqual(original,self.config_path.read_bytes())

    def test_failed_image_preflight_does_not_start_analysis(self):
        with patch.object(cli,'_load_install_config',return_value={}), \
             patch.object(cli,'_run',side_effect=OncoTracerError('missing caller')) as run:
            with self.assertRaisesRegex(OncoTracerError,'missing caller'):
                cli._run_docker(self.config_path,self.args())
        self.assertEqual(run.call_count,1)
        self.assertFalse((self.outdir/'.oncotracer-native').exists())

    def test_explicit_image_flag_takes_precedence_over_saved_yaml(self):
        with patch.object(cli,'_load_install_config',return_value={}), patch.object(cli,'_run') as run:
            cli._run_docker(self.config_path,self.args('--dry-run','--image','custom/image:new'))
        for call in run.call_args_list:
            self.assertIn('custom/image:new',call.args[0])
            self.assertNotIn('local/image:tested',call.args[0])

    def test_docker_dispatch_runs_with_validated_preview_context(self):
        def host(config_path, args):
            return resolve_variant_request(self.config,mode='illumina')
        with patch.object(cli,'_prepare_configured_hg38'), patch.object(cli,'_run_host',side_effect=host), \
             patch.object(cli,'_run_docker') as run:
            result=cli.execute_run(self.config_path,self.args('--dry-run'))
        self.assertEqual(result.callers,('bcftools',))
        run.assert_called_once()

    def publish_partial(self):
        from oncotracer_cli.output_safety import claim_output_run
        with claim_output_run(self.outdir, config_path=self.config_path):
            pass
        summary_path=self.outdir/'06_workflow_summary/workflow_summary.json'
        summary_path.parent.mkdir(parents=True,exist_ok=True)
        summary={'engine':'native','mode':'illumina','workflow_status':'partial_failure',
                 'cna_status':'complete','variant_status':'failed'}
        summary_path.write_text(json.dumps(summary))
        manifest={**summary,'schema':'oncotracer-native-run-manifest-v1',
                  'config_sha256':sha256_file(self.config_path),
                  'files':[{'path':str(summary_path.relative_to(self.outdir)),'sha256':sha256_file(summary_path)}]}
        (summary_path.parent/'native_run_manifest.json').write_text(json.dumps(manifest))
        (self.outdir/'index.html').write_text('<html>Partial failure; CNA available</html>')

    def test_docker_exit_two_requires_new_verified_partial_publication(self):
        def execute(command, **kwargs):
            if 'internal-run' in command:
                self.publish_partial()
                raise OncoTracerCommandError('exit 2',2)
        with patch.object(cli,'_load_install_config',return_value={}),patch.object(cli,'_run',side_effect=execute):
            with self.assertRaises(OncoTracerPartialFailure):
                cli._run_docker(self.config_path,self.args())
        # A later failure with the same old finalized report remains an error.
        def fail_without_publication(command, **kwargs):
            if 'internal-run' in command:
                raise OncoTracerCommandError('broken runtime',2)
        with patch.object(cli,'_load_install_config',return_value={}),patch.object(cli,'_run',side_effect=fail_without_publication):
            with self.assertRaisesRegex(OncoTracerCommandError,'broken runtime'):
                cli._run_docker(self.config_path,self.args())

    def test_docker_preflight_failure_does_not_reuse_partial_result(self):
        self.publish_partial()
        with patch.object(cli,'_load_install_config',return_value={}), \
             patch.object(cli,'_run',side_effect=OncoTracerCommandError('caller missing',2)) as run:
            with self.assertRaises(OncoTracerCommandError):
                cli._run_docker(self.config_path,self.args())
        self.assertEqual(run.call_count,1)

    def test_cli_partial_has_exit_two_without_error_prefix_and_real_failure_keeps_error(self):
        for outcome, expected in [(OncoTracerPartialFailure('Variants incomplete',self.outdir),'PARTIAL FAILURE:'),
                                  (OncoTracerError('Runtime failure'),'ERROR:')]:
            with self.subTest(expected=expected), patch.object(cli,'execute_run',side_effect=outcome):
                output=io.StringIO()
                with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
                    code=cli.main(['run','--config',str(self.config_path),'--backend','host'])
            self.assertEqual(code,2)
            self.assertIn(expected,output.getvalue())
            if expected=='PARTIAL FAILURE:':
                self.assertNotIn('ERROR:',output.getvalue())
                self.assertIn('Completed outputs are preserved:',output.getvalue())


if __name__ == '__main__':
    unittest.main()
