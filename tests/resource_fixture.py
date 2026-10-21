"""Hardware for tiny/simulated scientific workflows, independent of CI host size.

Resource detection and low-memory rejection have separate real-unit coverage in
 test_system_check and test_sample_parallelism. No production override is used.
"""
import unittest
from unittest.mock import patch

class ResourceFixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        hardware = {'cpu_workers_available': 128, 'ram_available_bytes': 1024 * 1024**3}
        mocked = patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=hardware)
        mocked.start()
        cls.addClassCleanup(mocked.stop)
