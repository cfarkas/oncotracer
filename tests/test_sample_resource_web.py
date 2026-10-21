"""Real browser configuration and continuation preserve resource selections."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.runtime import load_flat_yaml
from oncotracer_cli.web import WebState


class ResourceBrowserTests(unittest.TestCase):
    def test_offers_save_and_continue_do_not_launch_analysis(self):
        hardware = {'cpu_workers_available': 8, 'ram_available_bytes': 48 * 1024**3}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads = root/'reads'
            reads.mkdir()
            for name in ('SAMPLE_A', 'SAMPLE_B'):
                (reads/(name+'.fastq')).write_text('@read\nACGT\n+\nIIII\n')
            state = WebState(root)
            scan = state.scan({'mode': 'illumina', 'folder': str(reads)})
            with patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=hardware), \
                 patch('oncotracer_cli.cli._load_install_config', return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                choices = state.resource_options({'scan_id': scan['scan_id'], 'threads': 8, 'memory': '32GB'})
                self.assertEqual([c['parallel_samples'] for c in choices['choices']], [1, 2])
                prepared = state.prepare({'scan_id': scan['scan_id'], 'project': str(root/'project'),
                    'threads': 2, 'backend': 'host', 'samples': [
                        {'id': s['id'], 'name': s['name'], 'type': 'cancer'} for s in scan['samples']],
                    'resources_selection': {'threads': 8, 'parallel_samples': 2, 'memory': '32GB'}})
                self.assertTrue(prepared['valid'], prepared['check'])
                config = Path(prepared['config_path'])
                saved = load_flat_yaml(config)
                self.assertEqual((saved['threads'], saved['parallel_samples'], saved['analysis_memory']), (8, 2, '32GB'))
                sample_sheet = root/'project/config/samplesheet.csv'
                before = sample_sheet.read_text()
                loaded = state.resume({'project': str(root/'project'), 'backend': 'host'})
                self.assertFalse(loaded['resources_confirmed'])
                applied = state.resume({'project': str(root/'project'), 'backend': 'host',
                    'resources': {'threads': 4, 'parallel_samples': 2, 'memory': '24GB'}})
                self.assertTrue(applied['resources_confirmed'])
                saved = load_flat_yaml(config)
                self.assertEqual((saved['threads'], saved['parallel_samples'], saved['analysis_memory']), (4, 2, '24GB'))
                self.assertEqual(sample_sheet.read_text(), before)
                self.assertIsNone(state.job)
                self.assertFalse((root/'project/results').exists())


if __name__ == '__main__':
    unittest.main()
