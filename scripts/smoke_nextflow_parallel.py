#!/usr/bin/env python3
"""Run simultaneous real Nextflow JVMs against synthetic one-task workflows."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotracer_cli.nextflow_runtime import isolated_nextflow_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--nextflow', default='nextflow')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--rounds', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 2 or args.rounds < 1:
        parser.error('Use at least two jobs and one round')
    root = args.work_dir.resolve(); root.mkdir(parents=True, exist_ok=False)
    temp_parent = root / 'temporary with spaces'; temp_parent.mkdir()
    workflow = root / 'main.nf'
    workflow.write_text('''nextflow.enable.dsl = 2
process NATIVE_CHECK {
    input:
    val sample
    output:
    path 'result.txt'
    script:
    """
    printf '%s\\n' '${sample}' > result.txt
    """
}
workflow {
    println "JAVA_TMP=" + System.getProperty('java.io.tmpdir')
    NATIVE_CHECK(params.sample).view { file -> "RESULT=" + file.text.trim() }
}
''')
    executable = shutil.which(args.nextflow)
    if executable is None:
        raise RuntimeError('Nextflow executable not found')
    base = dict(os.environ, NXF_TEMP=str(temp_parent), NXF_OFFLINE='true',
                NXF_DISABLE_CHECK_LATEST='true', NXF_ANSI_LOG='false',
                NXF_OPTS='-Xms32m -Xmx256m -XX:ActiveProcessorCount=2 -XX:-CreateCoredumpOnCrash')
    records = []
    for wave in range(args.rounds):
        barrier = threading.Barrier(args.jobs)
        def run(index):
            sample = f'wave{wave}_sample{index}'
            launch = root / sample; launch.mkdir()
            with isolated_nextflow_environment(base) as overrides:
                scratch = overrides['NXF_TEMP']
                command = [executable, 'run', str(workflow), '-ansi-log', 'false',
                           '-work-dir', str(launch/'work'), '--sample', sample]
                barrier.wait(timeout=30)
                with (launch/'launch.log').open('w') as log:
                    result = subprocess.run(command, cwd=launch, env={**base, **overrides},
                                            stdout=log, stderr=log, timeout=180)
                output = (launch/'launch.log').read_text()
                passed = result.returncode == 0 and f'RESULT={sample}' in output and f'JAVA_TMP={scratch}' in output
            return {'sample': sample, 'exit_code': result.returncode, 'passed': passed,
                    'temporary_directory': scratch, 'cleaned': not Path(scratch).exists(),
                    'log': str(launch/'launch.log')}
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            records.extend(pool.map(run, range(args.jobs)))
    receipt = {'synthetic_only': True, 'simultaneous_jobs': args.jobs, 'rounds': args.rounds,
               'passed': all(r['passed'] and r['cleaned'] for r in records)
                         and len({r['temporary_directory'] for r in records}) == len(records),
               'runs': records}
    (root/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'passed': receipt['passed'], 'runs': len(records), 'receipt': str(root/'receipt.json')}))
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
