#!/usr/bin/env python3
"""Exercise one-table preparation against the actual image, without analysis.

Synthetic inputs test launch, image identity, configuration validation and
repeat safety. Public-data scientific parity is tested by the separate gates.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oncotracer_cli.runtime import load_flat_yaml,sha256_file


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    args=parser.parse_args()
    work=args.work_dir.absolute();work.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).resolve().parents[1]
    env={**os.environ,'XDG_CONFIG_HOME':str(work/'settings'),'PYTHONDONTWRITEBYTECODE':'1'}
    table=work/'samples.csv';reads=work/'inputs';reads.mkdir()
    for mate in (1,2):(reads/f'R{mate}.fastq').write_text('@SYNTHETIC\nACGT\n+\nIIII\n')
    table.write_text('sample,fastq_1,fastq_2,status,diagnosis\nSYNTHETIC,inputs/R1.fastq,inputs/R2.fastq,tumor,synthetic fixture\n')
    prefix=[sys.executable,'-B',str(source/'oncotracer')]
    command=[*prefix,'launch','--samples',str(table),'--project',str(work/'project'),
             '--specimen','fresh','--analysis','cna','--threads','2','--memory','8GB',
             '--cache',str(work/'cache'),'--image',args.image,'--prepare-only']
    original=table.read_bytes()
    subprocess.run(command,check=True,env=env)
    config=work/'project/config/run.yml';values=load_flat_yaml(config)
    assert values['execution_backend']=='docker' and values['threads']==2
    assert values['ichorcna_fallback'] and not values['run_variants']
    with (work/'project/config/sample_metadata.csv').open() as stream:
        assert next(csv.DictReader(stream))['diagnosis']=='synthetic fixture'
    before={p.name:sha256_file(p) for p in config.parent.iterdir() if p.is_file()}
    subprocess.run([*prefix,'check','--config',str(config)],check=True,env=env)
    subprocess.run([*prefix,'run','--config',str(config),'--backend','docker','--image',args.image,'--dry-run'],check=True,env=env)
    subprocess.run(command,check=True,env=env)
    after={p.name:sha256_file(p) for p in config.parent.iterdir() if p.is_file()}
    assert before==after and table.read_bytes()==original
    assert not (work/'project/results').exists(),'Preparation or dry-run started analysis'
    print(json.dumps(dict(status='passed',actual_image=args.image,synthetic_inputs=True,
        analysis_started=False,sample_table_unchanged=True,resume_unchanged=True,config_sha256=sha256_file(config))))


if __name__=='__main__':main()
