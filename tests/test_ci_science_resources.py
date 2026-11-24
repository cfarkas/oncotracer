#!/usr/bin/env python3
"""Resource isolation and immutable reference-cache identities for public CI."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_native_science', ROOT / 'scripts/ci_native_science.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ScientificCiResources(unittest.TestCase):
    def test_cpu_budget_uses_worker_affinity_not_vm_cpu_count(self):
        with patch.object(ci.os, 'sched_getaffinity', return_value=set(range(40))), patch.object(ci.os, 'cpu_count', return_value=120):
            self.assertEqual(ci.allocated_threads({}), 40)

    def test_repository_budget_cannot_exceed_hosted_runner_allocation(self):
        with patch.object(ci.os, 'sched_getaffinity', return_value={0, 1, 2, 3}):
            self.assertEqual(ci.allocated_threads({'ONCOTRACER_CI_THREADS': '40'}), 4)

    def test_explicit_lower_budget_is_respected(self):
        with patch.object(ci.os, 'sched_getaffinity', return_value=set(range(40))):
            self.assertEqual(ci.allocated_threads({'ONCOTRACER_CI_THREADS': '12'}), 12)

    def test_invalid_budgets_stop_before_work(self):
        for value in ('0', '-1', 'many', '1.5'):
            with self.subTest(value=value), self.assertRaises((ValueError, RuntimeError, AssertionError)):
                ci.allocated_threads({'ONCOTRACER_CI_THREADS': value})

    def test_reference_cache_persists_across_fresh_attempts(self):
        identities = {'bwa': 'a' * 64, 'minimap2': 'b' * 64}
        env = {'RUNNER_TOOL_CACHE': '/cache'}
        a = ci.reference_cache_location(Path('/run-1'), env, identities)
        b = ci.reference_cache_location(Path('/run-2'), env, identities)
        self.assertEqual(a, b)
        self.assertTrue(a[2])
        self.assertTrue(str(a[0]).startswith('/cache/oncotracer-reference-cache/hg38-'))

    def test_changed_index_tool_requires_another_cache(self):
        env = {'ONCOTRACER_CI_REFERENCE_CACHE': '/shared/references'}
        a = ci.reference_cache_location(Path('/run'), env, {'bwa': 'a' * 64, 'minimap2': 'b' * 64})
        b = ci.reference_cache_location(Path('/run'), env, {'bwa': 'c' * 64, 'minimap2': 'b' * 64})
        self.assertNotEqual(a[0], b[0])

    def test_uncached_local_runs_keep_independent_reference_roots(self):
        identities = {'bwa': 'a' * 64, 'minimap2': 'b' * 64}
        a = ci.reference_cache_location(Path('/run-1'), {}, identities)
        b = ci.reference_cache_location(Path('/run-2'), {}, identities)
        self.assertFalse(a[2])
        self.assertNotEqual(a[0], b[0])

    def test_relative_shared_cache_is_rejected(self):
        with self.assertRaises((ValueError, RuntimeError, AssertionError)):
            ci.reference_cache_location(Path('/run'), {'ONCOTRACER_CI_REFERENCE_CACHE': '../cache'}, {'bwa': 'a' * 64, 'minimap2': 'b' * 64})


if __name__ == '__main__':
    unittest.main()
