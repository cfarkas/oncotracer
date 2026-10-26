"""Owned per-user command publication and removal, using only temporary homes."""
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

from oncotracer_cli import cli, uninstall, user_launcher as launch
from oncotracer_cli.runtime import OncoTracerError

ROOT = Path(__file__).resolve().parents[1]


class UserLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oncotracer command test ")
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        self.home = self.scratch / "home"
        self.environment = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.scratch / "settings"),
            "XDG_DATA_HOME": str(self.scratch / "data"),
            "XDG_CACHE_HOME": str(self.scratch / "cache"),
            "TMPDIR": str(self.scratch),
        }
        self.patch = mock.patch.dict(os.environ, self.environment)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = {"backend": "docker", "image": "example.invalid/oncotracer:test"}

    def install(self, config=None):
        with launch.install_transaction() as transaction:
            transaction.publish(config or self.config)
        return launch.default_path()

    def pair(self):
        path = launch.default_path()
        return path.read_bytes(), (path.parent / launch.MARKER).read_bytes()

    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue() + (errors.getvalue() if code else "")

    def legacy_command(self, binding=None):
        """Independent bytes and marker produced by the pre-bootstrap launcher."""
        binding = binding or launch.current_binding()
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {binding['source']!r}); "
            f"sys.argv[0] = {str(path)!r}; "
            "from oncotracer_cli.cli import main; raise SystemExit(main())"
        )
        command = [binding['python'], '-I', '-B', '-c', bootstrap]
        data = (
            "#!/bin/sh\n"
            "# Managed by oncotracer install; use oncotracer uninstall --global-launcher.\n"
            "unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE "
            "CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER "
            "LD_LIBRARY_PATH LD_PRELOAD LD_LIBRARY_PATH_MCR\n"
            "exec " + shlex.join(command) + ' "$@"\n'
        ).encode()
        path.write_bytes(data)
        path.chmod(0o755)
        marker = path.parent / launch.MARKER
        marker.write_text(json.dumps({
            'schema':'oncotracer-user-launcher-v1', 'path':str(path), 'uid':os.getuid(),
            'binding':binding, 'installation':{'kind':'docker', 'target':self.config['image']},
            'sha256':hashlib.sha256(data).hexdigest(),
        }, sort_keys=True, indent=2) + '\n')
        marker.chmod(0o600)
        return path

    @contextlib.contextmanager
    def pip_console(self, directory_mode=0o755, command_mode=0o755):
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(directory_mode)
        data = b"#!/usr/bin/python3\nfrom oncotracer_cli.cli import main\nmain()\n"
        path.write_bytes(data); path.chmod(command_mode)
        site = self.home / ".local/lib/python/site-packages"
        module = site / "oncotracer_cli/user_launcher.py"
        module.parent.mkdir(parents=True); module.write_text("# installed module\n")
        info = site / "oncotracer-2.1.1.dist-info"; info.mkdir()
        (info / "METADATA").write_text("Name: oncotracer\nVersion: 2.1.1\n")
        (info / "entry_points.txt").write_text("[console_scripts]\noncotracer = oncotracer_cli.cli:main\n")
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        with (info / "RECORD").open("w", newline="") as output:
            csv.writer(output).writerow((os.path.relpath(path, site), "sha256=" + digest, len(data)))
        distribution = importlib.metadata.PathDistribution(info)
        with mock.patch.object(launch, "__file__", str(module)), mock.patch.object(launch, "owning_distribution", return_value=distribution):
            yield path, data

    def test_pip_owned_command_is_retained_without_any_launcher_writes(self):
        for mode in (0o755, 0o775):
            with self.subTest(mode=mode), self.pip_console(mode, mode) as (path, data):
                before = sorted(p.name for p in path.parent.iterdir())
                with launch.install_transaction() as transaction:
                    self.assertEqual(transaction.publish(self.config)["action"], "retain-pip")
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(sorted(p.name for p in path.parent.iterdir()), before)
                with launch.remove_transaction(target=launch.installation_target(self.config)) as transaction:
                    self.assertEqual(transaction.detach()["paths"], [])
                with self.assertRaisesRegex(OncoTracerError, "pip uninstall oncotracer"):
                    with launch.remove_transaction(required=True):
                        self.fail("pip console removal was authorized")
                self.assertEqual(path.read_bytes(), data)
            # Each mode uses a distinct HOME; no deletion of ownership evidence.
            self.home = self.scratch / ("home-" + str(mode))
            os.environ["HOME"] = str(self.home)

    def test_default_docker_uninstall_preserves_real_pip_entry_ownership(self):
        with self.pip_console(0o775, 0o775) as (path, data):
            with mock.patch.object(cli, "_load_install_config", return_value=dict(self.config)):
                code, output = self.invoke(["uninstall", "--yes", "--json"])
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output)["action"], "preserve")
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [launch.COMMAND])
            code, output = self.invoke(["uninstall", "--global-launcher", "--yes"])
            self.assertEqual(code, 2)
            self.assertIn("pip uninstall oncotracer", output)
            self.assertEqual(path.read_bytes(), data)

    def test_pip_read_only_exception_does_not_adopt_foreign_group_writable_command(self):
        with self.pip_console(0o775, 0o775) as (path, data):
            path.write_bytes(data + b"# foreign bytes\n")
            with self.assertRaisesRegex(OncoTracerError, "unowned"):
                with launch.install_transaction():
                    self.fail("foreign command was adopted")
            self.assertFalse((path.parent / launch.MARKER).exists())

    def test_changed_pip_console_is_not_adopted(self):
        with self.pip_console() as (path, data):
            path.write_bytes(data + b"# changed\n")
            with self.assertRaisesRegex(OncoTracerError, "unowned"):
                with launch.install_transaction():
                    self.fail("changed console was adopted")

    def test_pip_console_replacement_during_backend_work_is_detected(self):
        with self.pip_console() as (path, data):
            with self.assertRaisesRegex(OncoTracerError, "unowned|changed"):
                with launch.install_transaction() as transaction:
                    path.write_bytes(data + b"# changed during install\n")
                    transaction.publish(self.config)
            self.assertEqual(path.read_bytes(), data + b"# changed during install\n")
            self.assertFalse((path.parent / launch.MARKER).exists())

    def test_preview_creates_no_home_directory(self):
        with launch.install_transaction(dry_run=True) as transaction:
            result = transaction.publish(self.config)
        self.assertTrue(result["dry_run"])
        with launch.remove_transaction(dry_run=True, required=True) as transaction:
            self.assertEqual(transaction.detach()["paths"], [])
        self.assertFalse(self.home.exists())

    def test_exact_python_venv_path_is_not_resolved(self):
        python = self.scratch / "a venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        with mock.patch.object(launch.sys, "executable", str(python)):
            self.assertEqual(launch.current_binding()["python"], str(python))

    def test_archive_binding_uses_loader_not_payload_or_argv(self):
        archive = self.scratch / "copied app"
        with mock.patch.object(launch, "__loader__", types.SimpleNamespace(archive=str(archive))):
            with mock.patch.object(launch.sys, "argv", ["-c"]):
                binding = launch.current_binding()
        self.assertEqual(binding["kind"], "archive")
        self.assertEqual(binding["source"], str(archive))

    def test_real_command_preserves_cwd_arguments_and_isolates_python(self):
        source = self.scratch / "installed source"
        package = source / "oncotracer_cli"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "cli.py").write_text(
            "import json, os, sys\n"
            "def main():\n"
            " print(json.dumps({'cwd':os.getcwd(),'argv':sys.argv[1:],"
            "'conda':os.environ.get('CONDA_PREFIX'),'pythonpath':os.environ.get('PYTHONPATH')}))\n"
            " return 0\n"
        )
        unrelated = self.scratch / "unrelated ONT folder"
        unrelated.mkdir()
        (unrelated / "oncotracer_cli.py").write_text("raise RuntimeError('cwd hijacked')")
        poison = self.scratch / "foreign python"
        poison.mkdir()
        (poison / "sitecustomize.py").write_text("raise RuntimeError('PYTHONPATH hijacked')")
        fake_python = poison / "python3"
        fake_python.write_text("#!/bin/sh\nexit 91\n")
        fake_python.chmod(0o755)
        binding = {"python": os.path.abspath(sys.executable), "kind": "module", "source": str(source)}
        with mock.patch.object(launch, "current_binding", return_value=binding):
            path = self.install()
        env = {**os.environ, "PATH": str(poison), "PYTHONPATH": str(poison),
               "PYTHONHOME": str(poison), "CONDA_PREFIX": str(poison)}
        result = subprocess.run([str(path), "one argument", "literal;$x"], cwd=unrelated,
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "cwd": str(unrelated), "argv": ["one argument", "literal;$x"],
            "conda": None, "pythonpath": None,
        })

    def test_legacy_launcher_with_argparse_backport_upgrades_to_working_cli(self):
        source = self.scratch / 'installed wheel site-packages'
        shutil.copytree(ROOT / 'oncotracer_cli', source / 'oncotracer_cli',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # Reproduce the old backport's missing `required` argument without an
        # external install, using this interpreter's otherwise compatible parser.
        backport = Path(argparse.__file__).read_text() + """
_modern_subparsers_init = _SubParsersAction.__init__
def _legacy_subparsers_init(self, *args, **kwargs):
    if 'required' in kwargs:
        raise TypeError("_SubParsersAction.__init__() got an unexpected keyword argument 'required'")
    _modern_subparsers_init(self, *args, **kwargs)
_SubParsersAction.__init__ = _legacy_subparsers_init
"""
        (source / 'argparse.py').write_text(backport)
        binding = {'python':os.path.abspath(sys.executable), 'kind':'module', 'source':str(source)}
        path = self.legacy_command(binding)
        before = self.pair()
        broken = subprocess.run([str(path), '--help'], cwd=self.scratch,
                                env=os.environ.copy(), text=True, capture_output=True, timeout=20)
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn("unexpected keyword argument 'required'", broken.stderr)
        with mock.patch.object(launch, 'current_binding', return_value=binding):
            self.install()
        self.assertNotEqual(path.read_bytes(), before[0])
        recovery = list(path.parent.glob('.oncotracer-launcher-recovery-*/old-oncotracer'))
        self.assertEqual([item.read_bytes() for item in recovery], [before[0]])
        for arguments in (['--help'], ['reference', '--help'], ['--version']):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(path), *arguments], cwd=self.scratch,
                                        env=os.environ.copy(), text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('oncotracer', result.stdout.lower())
        self.assertEqual((source / 'argparse.py').read_text(), backport)

    def test_legacy_command_can_remove_itself_with_recoverable_original_bytes(self):
        path = self.legacy_command()
        before = self.pair()
        result = subprocess.run([str(path), 'uninstall', '--global-launcher', '--yes', '--json'],
                                cwd=self.scratch, env=os.environ.copy(), text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(path.exists())
        self.assertFalse((path.parent / launch.MARKER).exists())
        report = json.loads(result.stdout)
        self.assertTrue(report['recoverable'])
        recovery = Path(report['recovery_directory'])
        self.assertEqual((recovery / 'old-oncotracer').read_bytes(), before[0])
        self.assertEqual((recovery / ('old-' + launch.MARKER)).read_bytes(), before[1])

    def test_failed_public_upgrade_restores_exact_legacy_pair(self):
        path = self.legacy_command()
        before = self.pair()
        with mock.patch.object(cli, '_install_docker', return_value=dict(self.config)) as installer:
            with mock.patch.object(cli, '_save_install_config', side_effect=OSError('config save failed')):
                code, output = self.invoke(['install', '--docker'])
        self.assertEqual(code, 2, output)
        installer.assert_called_once()
        self.assertEqual(self.pair(), before)
        self.assertFalse((path.parent / launch.PENDING).exists())

    def test_rehashed_tampered_legacy_template_is_refused_before_backend(self):
        path = self.legacy_command()
        path.write_bytes(path.read_bytes() + b'# modified historical command\n')
        marker_path = path.parent / launch.MARKER
        marker = json.loads(marker_path.read_text())
        marker['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        marker_path.write_text(json.dumps(marker))
        before = self.pair()
        with mock.patch.object(cli, '_install_docker') as installer:
            code, output = self.invoke(['install', '--docker'])
        self.assertEqual(code, 2, output)
        installer.assert_not_called()
        with self.assertRaises(OncoTracerError):
            with launch.remove_transaction(required=True):
                self.fail('tampered legacy template was authorized')
        self.assertEqual(self.pair(), before)

    def test_archive_command_survives_payload_cache_change(self):
        archive = self.scratch / "original copied app"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("__main__.py", "import json,sys; print(json.dumps(sys.argv[1:]))")
        binding = {"python": os.path.abspath(sys.executable), "kind": "archive", "source": str(archive)}
        with mock.patch.object(launch, "current_binding", return_value=binding):
            path = self.install()
        result = subprocess.run([str(path), "archive argument"], cwd=self.scratch,
                                env={**os.environ, "ONCOTRACER_CACHE": str(self.scratch / "empty cache")},
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["archive argument"])

    def test_all_backend_public_installs_publish_after_success(self):
        configurations = {
            "conda": {"backend": "conda", "core_prefix": str(self.scratch / "tools/core")},
            "poetry": {"backend": "poetry", "core_prefix": str(self.scratch / "tools/core")},
            "docker": self.config,
            "singularity": {"backend": "singularity", "sif": str(self.scratch / "image.sif")},
        }
        for backend, config in configurations.items():
            with self.subTest(backend=backend):
                def backend_result(*args, **kwargs):
                    self.assertFalse(kwargs["save"])
                    return dict(config)
                with mock.patch.object(cli, "_install_" + backend, side_effect=backend_result) as installer:
                    with mock.patch.object(cli, "runtime_root", return_value=ROOT):
                        with mock.patch.object(cli, "_save_install_config") as save:
                            code, output = self.invoke(["install", "--" + backend])
                self.assertEqual(code, 0, output)
                installer.assert_called_once()
                save.assert_called_once()
                self.assertEqual(json.loads((launch.default_path().parent / launch.MARKER).read_text())["installation"],
                                 launch.installation_target(config))
                self.assertIn("Command:", output)

    def test_no_launcher_leaves_foreign_command_untouched(self):
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        path.write_text("foreign command")
        with mock.patch.object(cli, "_install_docker", return_value=dict(self.config)):
            with mock.patch.object(cli, "_save_install_config"):
                code, output = self.invoke(["install", "--docker", "--no-launcher"])
        self.assertEqual(code, 0, output)
        self.assertEqual(path.read_text(), "foreign command")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [launch.COMMAND])

    def test_foreign_command_preflight_precedes_backend(self):
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        path.write_text("foreign command")
        path.chmod(0o755)
        with mock.patch.object(cli, "_install_docker") as installer:
            code, output = self.invoke(["install", "--docker"])
        self.assertEqual(code, 2)
        self.assertIn("unowned", output)
        installer.assert_not_called()
        self.assertEqual(path.read_text(), "foreign command")

    def test_backend_failure_creates_no_command_or_saved_config(self):
        with mock.patch.object(cli, "_install_docker", side_effect=OncoTracerError("backend failed")):
            with mock.patch.object(cli, "_save_install_config") as save:
                code, output = self.invoke(["install", "--docker"])
        self.assertEqual(code, 2)
        self.assertIn("backend failed", output)
        save.assert_not_called()
        self.assertFalse(launch.default_path().exists())
        self.assertFalse((launch.default_path().parent / launch.MARKER).exists())

    def test_saved_configuration_failure_restores_previous_pair(self):
        self.install()
        before = self.pair()
        with mock.patch.object(cli, "_install_docker", return_value={"backend": "docker", "image": "new"}):
            with mock.patch.object(cli, "_save_install_config", side_effect=OSError("config save failed")):
                code, output = self.invoke(["install", "--docker"])
        self.assertEqual(code, 2, output)
        self.assertEqual(self.pair(), before)
        self.assertFalse((launch.default_path().parent / launch.PENDING).exists())

    def test_second_publication_failure_restores_pair(self):
        self.install()
        before = self.pair()
        original = launch._move
        def fail_marker(source, name, destination, target):
            if name == "new-" + launch.MARKER:
                raise OSError("injected second publication failure")
            return original(source, name, destination, target)
        with mock.patch.object(launch, "_move", side_effect=fail_marker):
            with self.assertRaisesRegex(OSError, "second publication"):
                with launch.install_transaction() as transaction:
                    transaction.publish({"backend": "docker", "image": "next"})
        self.assertEqual(self.pair(), before)
        self.assertFalse((launch.default_path().parent / launch.PENDING).exists())

    def test_concurrent_installation_is_refused(self):
        with launch.install_transaction():
            with self.assertRaisesRegex(OncoTracerError, "active"):
                with launch.install_transaction():
                    self.fail("second lock acquired")

    def test_late_replacement_is_not_overwritten(self):
        path = self.install()
        with self.assertRaisesRegex(OncoTracerError, "edited"):
            with launch.install_transaction() as transaction:
                path.write_text("foreign late replacement")
                transaction.publish(self.config)
        self.assertEqual(path.read_text(), "foreign late replacement")

    def test_changed_modes_are_not_adopted(self):
        path = self.install()
        path.chmod(0o777)
        with self.assertRaisesRegex(OncoTracerError, "permissions"):
            with launch.install_transaction():
                pass
        self.assertEqual(path.stat().st_mode & 0o777, 0o777)

    def test_hardlinks_are_not_removed(self):
        path = self.install()
        alias = self.scratch / "foreign hardlink"
        os.link(path, alias)
        with self.assertRaisesRegex(OncoTracerError, "single-link"):
            with launch.remove_transaction(required=True):
                pass
        self.assertEqual(path.stat().st_ino, alias.stat().st_ino)

    def test_symlink_command_is_not_followed(self):
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        other = self.scratch / "foreign"
        other.write_text("preserve")
        path.symlink_to(other)
        with self.assertRaisesRegex(OncoTracerError, "symlink"):
            with launch.install_transaction():
                pass
        self.assertEqual(other.read_text(), "preserve")
        self.assertTrue(path.is_symlink())

    def test_symlink_parent_is_rejected_before_mkdir(self):
        self.home.mkdir()
        other = self.scratch / "other"
        other.mkdir()
        (self.home / ".local").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(OncoTracerError, "real directory"):
            with launch.install_transaction():
                pass
        self.assertEqual(list(other.iterdir()), [])

    def test_unrelated_tool_target_preserves_global_command(self):
        self.install({"backend": "conda", "core_prefix": str(self.scratch / "first/core")})
        before = self.pair()
        with launch.remove_transaction({"kind": "conda", "target": str(self.scratch / "second")}) as transaction:
            result = transaction.detach()
        self.assertEqual(result["action"], "preserve")
        self.assertEqual(self.pair(), before)

    def test_other_source_cannot_remove_owned_command(self):
        self.install()
        before = self.pair()
        binding = {**launch.current_binding(), "source": str(self.scratch / "other checkout")}
        with mock.patch.object(launch, "current_binding", return_value=binding):
            with self.assertRaisesRegex(OncoTracerError, "different Python/source"):
                with launch.remove_transaction(required=True):
                    pass
        self.assertEqual(self.pair(), before)

    def test_tool_uninstall_failure_restores_global_command(self):
        base = self.scratch / "tools"
        self.install({"backend": "conda", "core_prefix": str(base / "core")})
        before = self.pair()
        with mock.patch.object(uninstall, "uninstall_target", side_effect=OncoTracerError("tools active")):
            code, output = self.invoke(["uninstall", "--conda", "--prefix", str(base), "--yes"])
        self.assertEqual(code, 2, output)
        self.assertEqual(self.pair(), before)

    def test_no_yes_and_dry_run_are_inert(self):
        self.install()
        before = self.pair()
        for flags in ([], ["--purge"], ["--yes", "--dry-run"]):
            code, output = self.invoke(["uninstall", "--global-launcher", *flags])
            self.assertEqual(code, 0, output)
            self.assertEqual(self.pair(), before)

    def test_docker_default_uninstall_keeps_image_and_tools(self):
        path = self.install()
        with mock.patch.object(cli, "_load_install_config", return_value=dict(self.config)):
            with mock.patch.object(uninstall, "uninstall_target") as tools:
                code, output = self.invoke(["uninstall", "--yes"])
        self.assertEqual(code, 0, output)
        tools.assert_not_called()
        self.assertFalse(path.exists())
        self.assertIn("Docker images", output)

    def test_matching_conda_removal_includes_command(self):
        base = self.scratch / "tools"
        path = self.install({"backend": "poetry", "core_prefix": str(base / "core")})
        result = {"kind": "conda", "dry_run": False, "paths": [str(base)], "recoverable": True}
        with mock.patch.object(uninstall, "uninstall_target", return_value=result):
            code, output = self.invoke(["uninstall", "--conda", "--prefix", str(base), "--yes", "--json"])
        self.assertEqual(code, 0, output)
        self.assertFalse(path.exists())
        self.assertIn(str(path), json.loads(output)["paths"])

    def test_actual_command_can_remove_itself(self):
        path = self.install()
        result = subprocess.run([str(path), "uninstall", "--global-launcher", "--yes", "--json"],
                                cwd=self.scratch, env=os.environ.copy(), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(path.exists())
        self.assertFalse((path.parent / launch.MARKER).exists())
        report = json.loads(result.stdout)
        self.assertTrue(report["recoverable"])
        self.assertTrue(Path(report["recovery_directory"]).is_dir())

    def test_pending_transaction_is_not_overwritten(self):
        path = self.install()
        before = self.pair()
        (path.parent / launch.PENDING).write_text('{"recovery_directory":"/do/not/touch"}')
        (path.parent / launch.PENDING).chmod(0o600)
        with self.assertRaisesRegex(OncoTracerError, "interrupted transaction"):
            with launch.install_transaction():
                pass
        self.assertEqual(self.pair(), before)

    def test_rollback_does_not_overwrite_late_foreign_path(self):
        path = self.install()
        with self.assertRaises(OncoTracerError):
            with launch.remove_transaction(required=True) as transaction:
                transaction.detach()
                path.write_text("foreign replacement during removal")
                raise OncoTracerError("backend stopped")
        self.assertEqual(path.read_text(), "foreign replacement during removal")
        self.assertTrue((path.parent / launch.PENDING).exists())
        self.assertTrue(list(path.parent.glob(".oncotracer-launcher-recovery-*/old-oncotracer")))

    def test_replacement_during_publication_is_not_reported_successful(self):
        path = self.install()
        original = launch._move
        def replace_promoted(source, name, destination, target):
            original(source, name, destination, target)
            if name == "new-" + launch.COMMAND:
                path.write_text("foreign replacement after promotion")
        with mock.patch.object(launch, "_move", side_effect=replace_promoted):
            with self.assertRaises(OncoTracerError):
                with launch.install_transaction() as transaction:
                    transaction.publish(self.config)
        self.assertEqual(path.read_text(), "foreign replacement after promotion")
        self.assertTrue((path.parent / launch.PENDING).exists())

    def test_changed_published_pair_fails_completion(self):
        path = self.install()
        with self.assertRaisesRegex(OncoTracerError, "before completion"):
            with launch.install_transaction() as transaction:
                transaction.publish(self.config)
                path.write_text("changed just before completion")
        self.assertEqual(path.read_text(), "changed just before completion")
        self.assertTrue((path.parent / launch.PENDING).exists())

    def test_copy_removal_also_removes_its_matching_global_command(self):
        archive = self.scratch / "copied standalone"
        with zipfile.ZipFile(archive, "w") as output:
            for name in ("__main__.py", "oncotracer_cli/cli.py", "oncotracer_cli/_build_metadata.py",
                         "payload/provenance/native-v2-sources.json"):
                output.writestr(name, "")
        binding = {"python": os.path.abspath(sys.executable), "kind": "archive", "source": str(archive)}
        with mock.patch.object(launch, "current_binding", return_value=binding):
            path = self.install()
            code, output = self.invoke(["uninstall", "--launcher", str(archive), "--yes", "--json"])
        self.assertEqual(code, 0, output)
        self.assertFalse(archive.exists())
        self.assertFalse(path.exists())

    def test_purge_removes_only_owned_recovery_files(self):
        path = self.install()
        sentinel = path.parent / "unrelated-command"
        sentinel.write_text("preserve")
        with launch.remove_transaction(required=True, purge=True) as transaction:
            report = transaction.detach()
        self.assertFalse(path.exists())
        self.assertFalse(report["recoverable"])
        recovery = Path(report["recovery_directory"])
        self.assertFalse((recovery / ("old-" + launch.COMMAND)).exists())
        self.assertFalse((recovery / ("old-" + launch.MARKER)).exists())
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertTrue((recovery / "transaction.json").exists())

    def test_parent_replacement_during_backend_is_refused(self):
        path = self.install()
        before = self.pair()
        moved = path.parent.with_name("original-bin")
        with self.assertRaisesRegex(OncoTracerError, "parent directory changed"):
            with launch.install_transaction() as transaction:
                path.parent.rename(moved)
                path.parent.mkdir(mode=0o755)
                (path.parent / "foreign-file").write_text("preserve")
                transaction.publish(self.config)
        self.assertEqual((moved / launch.COMMAND).read_bytes(), before[0])
        self.assertEqual((path.parent / "foreign-file").read_text(), "preserve")
        self.assertFalse(path.exists())

    def test_optional_tool_removal_preserves_unmarked_command(self):
        path = launch.default_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o775)
        path.write_text("foreign standalone or pip command")
        base = self.scratch / "tools"
        result = {"kind": "conda", "dry_run": False, "paths": [str(base)], "recoverable": True}
        with mock.patch.object(uninstall, "uninstall_target", return_value=result) as remove:
            code, output = self.invoke(["uninstall", "--conda", "--prefix", str(base), "--yes", "--json"])
        self.assertEqual(code, 0, output)
        remove.assert_called_once()
        self.assertEqual(path.read_text(), "foreign standalone or pip command")
        self.assertEqual(json.loads(output)["global_launcher"]["action"], "preserve")
        path.parent.chmod(0o755)
        path.chmod(0o755)
        code, output = self.invoke(["uninstall", "--global-launcher", "--yes"])
        self.assertEqual(code, 2)
        self.assertIn("unowned", output)


if __name__ == "__main__":
    unittest.main()
