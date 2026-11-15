"""Removed Podman entry points must reject requests before starting work."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oncotracer_cli import cli, container_runtime, project_resume, web_ui, resume_ui, variant_install_ui
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.variant_installer import VariantInstaller

class RemovedPodmanTests(unittest.TestCase):
    def test_public_commands_reject_removed_backend_before_dispatch(self):
        commands=[['install','--podman'],['doctor','--backend','podman'],
                  ['run','--config','unused.yml','--backend','podman'],
                  ['setup','--terminal','--backend','podman'],['--interactive','--backend','podman']]
        with patch.object(cli,'_run') as dispatch:
            for command in commands:
                with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:cli.build_parser().parse_args(command)
                    self.assertEqual(error.exception.code,2)
            dispatch.assert_not_called()

    def test_saved_podman_project_is_not_silently_resumed_with_another_engine(self):
        with patch('subprocess.run') as run:
            with self.assertRaises(OncoTracerError):project_resume._runtime('podman',{})
            run.assert_not_called()

    def test_container_helpers_reject_podman(self):
        for function,args in [(container_runtime.user_arguments,('podman',)),
                              (container_runtime.image_reference,(None,'podman'))]:
            with self.subTest(function=function.__name__), self.assertRaisesRegex(OncoTracerError,'Unsupported container backend'):
                function(*args)

    def test_docker_image_reference_and_ownership_remain_available(self):
        self.assertEqual(container_runtime.image_reference(None,'docker'),cli.DEFAULT_IMAGE)
        self.assertEqual(container_runtime.image_reference('localhost/image:test','docker'),'localhost/image:test')
        with patch('os.getuid',return_value=1234),patch('os.getgid',return_value=2345):
            self.assertEqual(container_runtime.user_arguments('docker'),['--user','1234:2345'])

    def test_browser_never_offers_podman(self):
        for module in (web_ui,resume_ui,variant_install_ui):
            text=Path(module.__file__).read_text()
            self.assertNotIn('<option value="podman">',text)
            self.assertNotIn('Install with Podman',text)

    def test_removed_install_method_cannot_start(self):
        with tempfile.TemporaryDirectory() as directory:
            installer=VariantInstaller(Path(directory))
            with self.assertRaisesRegex(OncoTracerError,"Choose Conda or Docker installation"):
                installer.plan(dict(mode='illumina',backend='docker',flow='fastq',specimen_type='fresh',callers=['freebayes'],method='podman'))

if __name__=='__main__':unittest.main()
