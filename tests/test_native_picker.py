"""Native chooser arguments, cancellation and sequencing-name validation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.native_picker import choose_path, has_sequencing_files
from oncotracer_cli.runtime import OncoTracerError


class NativePickerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_linux_folder_uses_system_dialog_and_literal_path_arguments(self):
        folder = self.root / "patient's $(literal) folder"
        folder.mkdir()
        with patch.dict(os.environ, {'DISPLAY': ':1'}), patch('sys.platform', 'linux'), \
                patch('oncotracer_cli.native_picker.shutil.which', side_effect=lambda name: '/usr/bin/zenity' if name == 'zenity' else None), \
                patch('oncotracer_cli.native_picker.subprocess.run', return_value=subprocess.CompletedProcess([], 0, str(folder)+'\n', '')) as run:
            self.assertEqual(choose_path(folder / 'not-created-yet'), folder)
        command = run.call_args.args[0]
        self.assertEqual(command[0], '/usr/bin/zenity')
        self.assertIn('--directory', command)
        self.assertIn('--filename='+str(folder)+'/', command)
        self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_cancel_timeout_failure_and_wrong_file_type(self):
        with patch.dict(os.environ, {'DISPLAY': ':1'}), patch('sys.platform', 'linux'), \
                patch('oncotracer_cli.native_picker.shutil.which', return_value='/usr/bin/zenity'), \
                patch('oncotracer_cli.native_picker.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', '')
            self.assertIsNone(choose_path(self.root))
            run.return_value = subprocess.CompletedProcess([], 2, '', 'error')
            with self.assertRaisesRegex(OncoTracerError, 'Cannot open'):
                choose_path(self.root)
            run.side_effect = subprocess.TimeoutExpired(['zenity'], 900)
            with self.assertRaisesRegex(OncoTracerError, 'timed out'):
                choose_path(self.root)
            run.side_effect = None
            note = self.root / 'note.txt'
            note.write_text('fixture')
            run.return_value = subprocess.CompletedProcess([], 0, str(note)+'\n', '')
            with self.assertRaisesRegex(OncoTracerError, 'Select a BAM'):
                choose_path(self.root, 'bam')
            with self.assertRaisesRegex(OncoTracerError, 'Select a YAML'):
                choose_path(self.root, 'file')

    def test_headless_does_not_open_a_chooser(self):
        with patch.dict(os.environ, {}, clear=True), patch('sys.platform', 'linux'), \
                patch('oncotracer_cli.native_picker.subprocess.run') as run:
            with self.assertRaisesRegex(OncoTracerError, 'needs a desktop'):
                choose_path(self.root)
            run.assert_not_called()

    def test_windows_uses_native_tk_dialog_in_its_own_process(self):
        with patch('sys.platform', 'win32'), patch('oncotracer_cli.native_picker.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps(str(self.root)), '')
            self.assertEqual(choose_path(self.root), self.root)
            options = json.loads(run.call_args.args[0][-1])
            self.assertEqual(options['initialdir'], str(self.root))
            self.assertTrue(options['folder'])
            self.assertIn('filedialog.askdirectory', run.call_args.args[0][-2])
            run.return_value = subprocess.CompletedProcess([], 0, '""\n', '')
            self.assertIsNone(choose_path(self.root))

    def test_sequencing_detection_includes_nested_files_without_following_loops(self):
        (self.root / 'empty').mkdir()
        (self.root / 'notes.txt').write_text('not sequencing')
        (self.root / 'empty/loop').symlink_to(self.root, target_is_directory=True)
        self.assertFalse(has_sequencing_files(self.root))
        for name in ('reads.FASTQ.GZ', 'reads.fq', 'signal.POD5', 'calls.bam'):
            with self.subTest(name=name):
                path = self.root / 'empty' / name
                path.write_bytes(b'filename fixture')
                self.assertTrue(has_sequencing_files(self.root))
                path.unlink()
        (self.root / 'empty/broken.fastq').symlink_to(self.root / 'missing')
        self.assertFalse(has_sequencing_files(self.root))
