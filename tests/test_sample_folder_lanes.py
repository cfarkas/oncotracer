"""Sample-folder lane lists must survive configuration, alignment, and resume."""
import csv
from pathlib import Path
import tempfile
import unittest

from oncotracer_cli.engine import IlluminaSample, _align_illumina_locked, parse_illumina_samplesheet
from oncotracer_cli.fastq_inputs import encode_fastq_field
from oncotracer_cli.runtime import OncoTracerError, StageLedger


class SampleFolderLaneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.first, self.second = [], []
        for lane in range(2):
            for mate, paths in ((1, self.first), (2, self.second)):
                path = self.root / f'lane {lane}, R{mate}.fq.gz'
                path.write_bytes(f'lane{lane}-mate{mate}\n'.encode())
                paths.append(path)

    def test_samplesheet_lane_lists_are_ordered_and_validate_every_file(self):
        sheet = self.root / 'samples.csv'
        def write(first, second):
            with sheet.open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['sample', 'fastq_1', 'fastq_2', 'status'])
                writer.writerow(['sample', encode_fastq_field(tuple(first)), encode_fastq_field(tuple(second)), 'tumor'])
        write(self.first, self.second)
        sample, = parse_illumina_samplesheet(sheet)
        self.assertEqual((sample.fastq_1, sample.fastq_2), (tuple(self.first), tuple(self.second)))
        for first, second, message in ((self.first, self.second[:1], 'lane counts'),
                                       ([self.first[0]] * 2, self.second, 'more than once'),
                                       (self.first, self.first, 'both R1 and R2'),
                                       (self.first, [self.second[0], self.root / 'missing.fq'], 'missing or empty')):
            with self.subTest(message=message):
                write(first, second)
                with self.assertRaisesRegex(OncoTracerError, message):
                    parse_illumina_samplesheet(sheet)

    def test_all_lane_pairs_merge_once_per_sample_and_resume_tracks_each_input(self):
        class Tools:
            def executable(self, group, name):
                return name

        class Runner:
            def __init__(self):
                self.alignments, self.merges, self.marked = [], [], []

            def pipeline(self, stage, left, right):
                reads = tuple(Path(path) for path in left[-2:])
                self.alignments.append((stage, reads, left[left.index('-R') + 1]))
                output = Path(right[right.index('-o') + 1])
                output.write_bytes(b''.join(path.read_bytes() for path in reads))

            def run(self, stage, command):
                command = [str(value) for value in command]
                if command[1] == 'merge':
                    output_index = command.index('-o') + 1
                    inputs = [Path(path) for path in command[output_index + 1:]]
                    self.merges.append(inputs)
                    Path(command[output_index]).write_bytes(b''.join(path.read_bytes() for path in inputs))
                elif command[1] == 'index':
                    Path(command[-1] + '.bai').write_bytes(b'index')
                elif command[1] == 'MarkDuplicates':
                    values = dict(value.split('=', 1) for value in command[2:])
                    self.marked.append(Path(values['I']))
                    Path(values['O']).write_bytes(Path(values['I']).read_bytes())
                    Path(values['O'] + '.bai').write_bytes(b'index')
                    Path(values['M']).write_bytes(b'metrics')
                else:
                    raise AssertionError(command)

        sample = IlluminaSample('sample', tuple(self.first), tuple(self.second), 'tumor')
        runner = Runner()
        output = self.root / 'results'
        ledger = StageLedger(self.root / 'state.json')
        def align():
            return _align_illumina_locked([sample], {'bwa_prefix': self.root / 'reference'},
                                         output, runner, ledger, Tools(), threads=2, force=False)
        results = align()
        expected = b''.join(path.read_bytes() for pair in zip(self.first, self.second) for path in pair)
        self.assertEqual(results['sample'].read_bytes(), expected)
        self.assertEqual([reads for _, reads, _ in runner.alignments], list(zip(self.first, self.second)))
        self.assertEqual(len({rg for _, _, rg in runner.alignments}), 2)
        self.assertTrue(all('\\tSM:sample\\tLB:sample\\t' in rg for _, _, rg in runner.alignments))
        self.assertEqual(len(runner.merges), 1)
        self.assertEqual(runner.marked, [results['sample']])
        align()
        self.assertEqual((len(runner.alignments), len(runner.merges), len(runner.marked)), (2, 1, 1))
        self.first[1].write_bytes(b'changed lane input\n')
        align()
        self.assertEqual((len(runner.alignments), len(runner.merges), len(runner.marked)), (3, 2, 2))
        self.assertEqual(runner.alignments[-1][1], (self.first[1], self.second[1]))
