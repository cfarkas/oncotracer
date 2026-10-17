"""Interpreter portability must preserve virtual-environment symlinks."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
BIN=ROOT/'oncotracer_cli/lpwgs/bin'
spec=importlib.util.spec_from_file_location('lpwgs_launch_python_test',BIN/'launch.py')
launch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class PythonEnvironmentTests(unittest.TestCase):
    def test_python_option_defaults_to_running_interpreter(self):
        with patch.object(sys,'path',[str(BIN),*sys.path]):
            args=launch.parser().parse_args([])
        self.assertEqual(args.python,sys.executable)

    def test_python_path_preserves_environment_symlink_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            env=Path(temp)/'python environment/bin';env.mkdir(parents=True)
            executable=env/'python3';executable.symlink_to(sys.executable)
            self.assertEqual(launch.python_executable(str(executable)),str(executable))
            with patch.dict(os.environ,{'PATH':str(env)}):
                self.assertEqual(launch.python_executable('python3'),str(executable))

    def test_invalid_interpreter_values_are_rejected(self):
        for value in (True,False,17,[],None,'',"bad'path",'bad\npath','bad\rpath','bad\tpath',
                      '/does/not/exist/python3'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                launch.python_executable(value)


if __name__=='__main__':unittest.main()
