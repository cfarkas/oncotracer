#!/usr/bin/env python3
"""Exercise production Nextflow task reservations using synthetic timed tasks."""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oncotracer_cli.nextflow_runtime import prepare_nextflow_environment

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    config = ROOT / 'oncotracer_cli/lpwgs/nextflow.config'
    # Labels and names come from the production processes. Only their work is
    # replaced by a timed fixture; the scheduler is the actual Nextflow engine.
    import re
    source = (ROOT / 'oncotracer_cli/lpwgs/main.nf').read_text()
    for threads, memory in ((6, 48), (1, 8)):
        root = a.output / f'{threads}cpu'
        root.mkdir()
        processes = []
        for name in ('GERMLINE', 'SOMATIC', 'GLIMPSE_PHASE'):
            block = re.search(r'process '+name+r' \{(.*?)\n\}', source, re.S)[1]
            label = re.search(r"label '([^']+)'", block)[1]
            processes.append('process '+name+" {\n    label '"+label+"'\n"+'''
    input:
    val number
    output:
    path 'timing.json'
    script:
    """
    python3 -c 'import json,time; start=time.time(); time.sleep(1 if "${task.process}" == "GERMLINE" else 4); json.dump(dict(start=start,end=time.time(),cpus=${task.cpus},memory_mb=${task.memory.toMega()}),open("timing.json","w"))'
    """
}
''')
        workflow = root / 'main.nf'
        workflow.write_text('nextflow.enable.dsl=2\n'+'\n'.join(processes)+'''
workflow {
    GERMLINE(Channel.of(1))
    SOMATIC(Channel.of(1))
    GLIMPSE_PHASE(Channel.of(1,2,3,4))
}
''')
        params = root / 'params.json'
        params.write_text(json.dumps(dict(threads=threads, memory=f'{memory}GB', output=str(root/'output'))))
        env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
                   NXF_ANSI_LOG='false', NXF_OPTS=f'-Xms32m -Xmx512m -XX:ActiveProcessorCount={max(2,threads)}',
                   PYTHONDONTWRITEBYTECODE='1')
        executable = shutil.which('nextflow')
        if not executable:
            raise RuntimeError('A real Nextflow runtime is required')
        env.update(prepare_nextflow_environment(executable, env))
        command = [executable, '-C', str(config), 'run', str(workflow), '-params-file', str(params)]
        with (root/'run.log').open('w') as log:
            run = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        if run.returncode:
            raise RuntimeError((root/'run.log').read_text()[-5000:])
        traces = list((root/'output/pipeline_info').glob('trace-*.tsv'))
        rows = list(csv.DictReader(traces[0].open(), delimiter='\t'))
        assert len(rows)==6 and all(r['status']=='COMPLETED' for r in rows), rows
        timings = []
        for row in rows:
            directory = list((root/'output/work').glob(row['hash']+'*'))
            assert len(directory)==1, row
            timing = json.loads((directory[0]/'timing.json').read_text())
            timing['name'] = row['name']
            timings.append(timing)
        events = sorted({t[k] for t in timings for k in ('start','end')})
        peak = 0
        overlap = False
        for x,y in zip(events, events[1:]):
            midpoint=(x+y)/2
            active=[t for t in timings if t['start']<=midpoint<t['end']]
            assert sum(t['cpus'] for t in active)<=threads, active
            assert sum(t['memory_mb'] for t in active)<=memory*1024, active
            peak=max(peak,len(active))
            overlap |= any(t['name'].startswith('SOMATIC') for t in active) and any(t['name'].startswith('GLIMPSE_PHASE') for t in active)
        assert all(t['cpus']==1 for t in timings if t['name'].startswith(('GERMLINE','GLIMPSE_PHASE')))
        if threads>1:
            assert peak>=3 and overlap, (peak, timings)
        else:
            assert peak==1 and not overlap, timings
        receipt=dict(status='passed', threads=threads, memory_gib=memory, peak_concurrent_tasks=peak,
                     somatic_imputation_overlap=overlap, timings=timings,
                     scope='Synthetic scheduler validation; not a biological speed benchmark')
        (root/'validation.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({k:v for k,v in receipt.items() if k!='timings'}),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
