#!/usr/bin/env python3
"""Exercise two real alignments per platform on tiny synthetic reads only.

Requires an installed core prefix and at least 20 GiB available RAM. It checks
sample identities, BAM/index readability, and reuse after a resource change.
The synthetic reference bypasses the production hg38 reference contract only.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotracer_cli import engine
from oncotracer_cli.runtime import CommandRunner, StageLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool-prefix', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    prefix = args.tool_prefix.resolve()
    environment = dict(os.environ, PATH=str(prefix/'bin')+os.pathsep+os.environ.get('PATH', ''),
                       CONDA_PREFIX=str(prefix), JAVA_HOME=str(prefix))
    class Tools:
        def executable(self, group, name):
            return str(prefix/'bin'/name)
    tools = Tools()
    randomizer = random.Random(2701)
    bases = ''.join(randomizer.choices('ACGT', k=50000))
    fasta = root/'reference.fa'
    fasta.write_text('>chr1\n'+bases+'\n')
    index = root/'reference.mmi'
    with (root/'reference-build.log').open('w') as log:
        for command in ([tools.executable('core', 'bwa'), 'index', str(fasta)],
                        [tools.executable('core', 'minimap2'), '-d', str(index), str(fasta)]):
            subprocess.run(command, env=environment, stdout=log, stderr=log, check=True)
    receipt = {'synthetic_only': True, 'platforms': {}}
    for mode in ('illumina', 'ont'):
        output = root/mode
        output.mkdir()
        samples = []
        for name in ('SAMPLE_A', 'SAMPLE_B'):
            folder = output/name
            folder.mkdir()
            read = folder/'reads.fastq'
            length = 150 if mode == 'illumina' else 2000
            read.write_text(''.join('@read'+str(n)+'\n'+bases[n:n+length]+'\n+\n'+'I'*length+'\n'
                                    for n in range(1000, 6000, 250)))
            samples.append(engine.IlluminaSample(name, read, None, 'tumor') if mode == 'illumina'
                           else engine.OntSample(name, name, folder))
        ledger = StageLedger(output/'stages.json')
        runner = CommandRunner(output/'trace.tsv', environment=environment, echo=False)
        reader = '_validated_bwa_reader' if mode == 'illumina' else '_validated_minimap_reader'
        with patch.object(engine, reader, return_value=contextlib.nullcontext()):
            def align(cpu):
                common = dict(threads=cpu, force=False, parallel_samples=2, memory='16GB')
                if mode == 'illumina':
                    return engine.align_illumina(samples, {'bwa_prefix': fasta}, output, runner, ledger, tools, **common)
                return engine.align_ont(samples, {'minimap2_index': index}, output, runner, ledger, tools,
                                        min_age_minutes=0, **common)
            results = align(4)
            resources = json.loads((output/'alignment_resources.json').read_text())
            assert resources['parallel_samples'] == 2, resources
            trace = runner.trace_path.read_bytes()
            align(6)
            assert runner.trace_path.read_bytes() == trace, 'Resource change repeated an alignment'
        for name, bam in results.items():
            subprocess.run([tools.executable('core', 'samtools'), 'quickcheck', str(bam)], check=True)
            header = subprocess.check_output([tools.executable('core', 'samtools'), 'view', '-H', str(bam)], text=True)
            assert 'SM:'+name in header and Path(str(bam)+'.bai').is_file()
            assert int(subprocess.check_output([tools.executable('core', 'samtools'), 'view', '-c', str(bam), 'chr1'])) == 20
        receipt['platforms'][mode] = {'samples': sorted(results), 'parallel_samples': 2, 'reuse_after_cpu_change': True}
    (root/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
