"""Resource offers, overlapping samples, cancellation, and unchanged checkpoint reuse."""
import contextlib
import gzip
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from oncotracer_cli import engine, variants
from oncotracer_cli.runtime import OncoTracerError, StageLedger
from oncotracer_cli.sample_resources import allocate, offers, selected_config, planning_suggestions

HARDWARE = {'cpu_workers_available': 32, 'ram_available_bytes': 256 * 1024**3}


class SampleResourcesTests(unittest.TestCase):
    def test_detected_limits_override_requested_totals(self):
        limited = {'cpu_workers_available': 6, 'ram_available_bytes': 80 * 1024**3}
        plan = allocate(64, 20, parallel=10, memory='128GB', minimum_gb=32, hardware=limited)
        self.assertEqual(plan['parallel_samples'], 2)
        self.assertEqual(plan['threads_per_sample'], 3)
        self.assertEqual(plan['allocated_memory_gb'], 64)
        with self.assertRaisesRegex(OncoTracerError, 'at least 32'):
            allocate(4, 2, memory='16GB', minimum_gb=32, hardware=limited)

    def test_auto_shares_host_memory_and_offers_real_choices(self):
        plan = allocate(16, 10, hardware=HARDWARE)
        self.assertEqual(plan['parallel_samples'], 2)
        self.assertLessEqual(plan['total_memory_gb'], 256 * .8 * 16 / 32)
        choices = offers({'mode': 'illumina', 'run_variants': True, 'threads': 16},
                         10, hardware=HARDWARE)
        self.assertEqual([x['parallel_samples'] for x in choices['choices']], [1, 2, 3])
        for x in choices['choices']:
            self.assertLessEqual(x['allocated_threads'], 16)
            self.assertGreaterEqual(x['memory_gb_per_sample'], 32)

    def test_job_suggestions_adapt_to_machine_memory_and_cohort(self):
        for cpus, ram, samples in ((12, 96, None), (48, 80, 20), (4, 48, 2), (128, 1024, 3)):
            hardware = {'cpu_workers_available': cpus, 'ram_available_bytes': ram * 1024**3}
            plans = planning_suggestions({}, hardware=hardware, sample_count=samples)
            for plan in plans:
                self.assertTrue(plan['suggestions'])
                for choice in plan['suggestions']:
                    self.assertLessEqual(choice['allocated_threads'], cpus)
                    self.assertLessEqual(choice['allocated_memory_gb'], .8 * ram)
                    self.assertLessEqual(choice['parallel_samples'], samples or cpus)
                    self.assertGreaterEqual(choice['memory_gb_per_sample'], choice['minimum_memory_gb_per_sample'])
            if cpus == 48:
                # RAM, not the many CPU threads, limits complete Illumina variants.
                self.assertLessEqual(max(c['parallel_samples'] for c in plans[1]['suggestions']), 2)
        plans = planning_suggestions({}, hardware={'cpu_workers_available': 16, 'ram_available_bytes': 24 * 1024**3})
        self.assertTrue(plans[0]['suggestions'])
        self.assertEqual(plans[1]['suggestions'], [])
        self.assertIn('at least 32', plans[1]['error'])

    def test_resource_selection_cannot_change_scientific_settings(self):
        with patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=HARDWARE):
            result = selected_config({'mode': 'ont', 'threads': 16}, 4,
                                     {'parallel_samples': 2, 'memory': '32GB'})
            self.assertEqual(result['parallel_samples'], 2)
            self.assertEqual(result['variant_parallel_samples'], 2)
            with self.assertRaises(OncoTracerError):
                selected_config({'threads': 16}, 4, {'ont_caller': 'different'})


class AlignmentParallelTests(unittest.TestCase):
    def test_two_samples_overlap_and_resume_survives_resource_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            samples = []
            for name in ('A', 'B', 'C'):
                read = root / (name + '.fastq'); read.write_text('@r\nACGT\n+\nIIII\n')
                samples.append(engine.IlluminaSample(name, read, None, 'tumor'))
            ledger = StageLedger(root / 'state.json')
            lock, barrier = threading.Lock(), threading.Barrier(2)
            active = peak = calls = 0
            class Runner:
                def pipeline(self, stage, left, right):
                    nonlocal active, peak, calls
                    with lock:
                        active += 1; calls += 1; peak = max(peak, active); call = calls
                    if call <= 2: barrier.wait(timeout=3)
                    self_cpu = int(left[left.index('-t') + 1]) + int(right[right.index('-@') + 1]) + 1
                    if self_cpu > 4: raise AssertionError('Mapper and sorter oversubscribed slot')
                    time.sleep(.03)
                    Path(right[right.index('-o') + 1]).write_bytes(b'complete-bam')
                    with lock: active -= 1
                def run(self, stage, argv):
                    argv = list(map(str, argv))
                    if 'index' in argv: Path(argv[-1] + '.bai').write_bytes(b'index')
                    elif 'MarkDuplicates' in argv:
                        out = next(x[2:] for x in argv if x.startswith('O='))
                        Path(out).write_bytes(b'duplicates'); Path(out + '.bai').write_bytes(b'index')
                        Path(next(x[2:] for x in argv if x.startswith('M='))).write_text('metrics')
            class Tools:
                def executable(self, group, name): return name
            def align(cpu=8):
                return engine.align_illumina(samples, {'bwa_prefix': root/'ref'}, root/'output',
                    Runner(), ledger, Tools(), threads=cpu, force=False, parallel_samples=2, memory='32GB')
            with patch.object(engine, '_validated_bwa_reader', return_value=contextlib.nullcontext()), \
                 patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=HARDWARE):
                result = align()
                self.assertEqual(peak, 2)
                self.assertEqual(set(result), {'A', 'B', 'C'})
                self.assertEqual(len(ledger.data['stages']), 6)
                align(4)
                self.assertEqual(calls, 3, 'Changing sample CPU budgets repeated valid alignments')
                Path(str(result['B']) + '.bai').write_bytes(b'')
                align(4)
                self.assertEqual(calls, 4, 'An incomplete BAM/index pair was reused')
                self.assertEqual(json.loads((root/'output/alignment_status.json').read_text()),
                                 {'A': 'complete', 'B': 'complete', 'C': 'complete'})

    def test_legacy_checkpoint_migration_checks_both_outputs_and_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            read, bam, bai = [root/name for name in ('read.fastq', 'bam', 'bam.bai')]
            for path in (read, bam, bai): path.write_text('fixture')
            ledger = StageLedger(root/'state.json')
            def command(cpu): return ['bwa', 'mem', '-t', str(cpu), str(read), '|', 'samtools', 'sort', '-@', str(max(1, cpu//2)), '-o', str(bam), '-']
            old = ledger.signature('illumina-align-A', command(20), [read])
            ledger.complete('illumina-align-A', old, [bam, bai])
            args = (ledger, 'illumina-align-A', command(5), [read], [bam, bai], False, command)
            signature, reused = engine._alignment_checkpoint(*args)
            self.assertTrue(reused)
            self.assertNotEqual(signature, old)
            read.write_text('changed reads')
            self.assertFalse(engine._alignment_checkpoint(*args)[1])


class NativeVariantsParallelTests(unittest.TestCase):
    def test_parallel_calling_isolated_results_and_reuse(self):
        self.check_parallel_reuse()

    def test_previous_release_variant_checkpoints_survive_resource_changes(self):
        self.check_parallel_reuse(legacy=True)

    def check_parallel_reuse(self, legacy=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref = root/'ref.fa'; ref.write_text('>chr1\nAAAA\n')
            Path(str(ref)+'.fai').write_text('chr1\t4\t6\t4\t5\n')
            bams = {name: root/(name+'.bam') for name in ('A', 'B', 'C')}
            for bam in bams.values(): bam.write_bytes(b'fixture')
            request = variants.VariantRequest('ont', 'fresh', ('clairs_to',), annovar='off',
                       parallel_samples=2, memory='32GB', clairsto_platform='ont_r10_dorado_sup_5khz')
            lock, barrier = threading.Lock(), threading.Barrier(2)
            active = peak = calls = 0
            class Runner:
                def run(self, stage, argv, **kwargs):
                    if stage.endswith('-header'):
                        name = stage.split('-')[1]
                        kwargs['stdout'].write('@HD\tSO:coordinate\n@SQ\tSN:chr1\tLN:4\n@RG\tSM:'+name+'\n')
            def call(request, caller, sample, bam, ref, directory, tools, runner, threads, env, targets):
                nonlocal active, peak, calls
                with lock: active += 1; peak = max(peak, active); calls += 1; call_number = calls
                if call_number <= 2: barrier.wait(timeout=3)
                self.assertEqual(threads, 4)
                time.sleep(.03)
                vcf = directory / (sample+'.vcf.gz')
                with gzip.open(vcf, 'wt') as handle:
                    handle.write('##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t'+sample+'\nchr1\t2\t.\tA\tT\t40\tPASS\t.\tGT\t0/1\n')
                Path(str(vcf)+'.tbi').write_bytes(b'index')
                evidence = directory/'evidence.tsv'; evidence.write_text('sample\n'+sample+'\n')
                with lock: active -= 1
                return vcf, evidence, 1
            ledger = StageLedger(root/'ledger.json')
            with patch('oncotracer_cli.sample_resources.inspect_hardware', return_value=HARDWARE), \
                 patch.object(variants, 'preflight_variant_tools', return_value={'samtools': '/usr/bin/true'}), \
                 patch.object(variants, '_call', side_effect=call) as caller:
                digest = variants._module_digest
                def legacy_digest(name='variants.py'):
                    return 'c71529f4c2a04afc126fb0b00a39486b73aea022ab285eecde01beab2d3794c8' if name == 'variants.py' else digest(name)
                historical = patch.object(variants, '_module_digest', side_effect=legacy_digest) if legacy else contextlib.nullcontext()
                with historical:
                    result = variants.run_variants(request, bams, ref, root/'output', Runner(), ledger, threads=8)
                self.assertEqual(result['overall_status'], 'complete')
                self.assertEqual(peak, 2)
                self.assertEqual(set(result['completed_samples']), set(bams))
                caller.reset_mock()
                second = variants.run_variants(replace(request, parallel_samples=3, memory='64GB'),
                           bams, ref, root/'output', Runner(), ledger, threads=12)
                self.assertEqual(second['overall_status'], 'complete')
                caller.assert_not_called()


if __name__ == '__main__':
    unittest.main()
