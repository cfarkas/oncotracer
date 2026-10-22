#!/usr/bin/env python3
"""Verify real Docker resume dispatch; substitute dry-run only at analysis entry.

Uses synthetic ONT reads and a new, explicitly selected test directory. Docker
identity queries, output checks, mounts and caller preflight execute normally.
No reference download, alignment or patient analysis is started.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotracer_cli import cli
from oncotracer_cli.output_safety import _create_owner, OUTPUT_OWNER_RELATIVE
from oncotracer_cli.project_resume import _runtime, audit_intermediates
from oncotracer_cli.runtime import StageLedger, render_flat_yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.work_dir.absolute()
    root.mkdir(parents=True, exist_ok=False)
    project = root / 'synthetic-project'
    (project / 'config').mkdir(parents=True)
    output = project / 'results'
    output.mkdir()
    reads = root / 'reads' / 'SYNTHETIC' / 'fastq_pass'
    reads.mkdir(parents=True)
    fastq = reads / 'reads.fastq'
    fastq.write_text('@synthetic\nACGT\n+\nIIII\n')
    config = dict(mode='ont', execution_backend='docker', docker_image=args.image,
                  lpwgs_root=str(project / 'reference'), outdir=str(output),
                  ont_folder=str(reads.parents[1]), ont_named_samples=True,
                  ont_barcodes='SYNTHETIC', ont_sample_names='SYNTHETIC',
                  ont_caller='ichorcna', ont_binsize_kb=500, threads=2,
                  run_variants=True, variant_specimen_type='fresh',
                  variant_callers='clairs_to', variant_annovar='off',
                  variant_clairsto_platform='ont_r10_dorado_sup_5khz_ssrs')
    config_path = project / 'config/run.yml'
    config_path.write_text(render_flat_yaml(config))
    identity = _runtime('docker', config)
    _create_owner(output, identity)
    product = output / 'counts.txt'
    product.write_text('synthetic completed counts\n')
    ledger = StageLedger(output / '.oncotracer-native/state.json')
    ledger.complete('synthetic-counts', ledger.signature('count', ['count'], [fastq]), [product])
    recorded = ledger.path.read_bytes()
    dispatch = cli._run
    calls = []

    def safe_dispatch(command, **kwargs):
        command = list(command)
        if 'internal-run' in command:
            # Acquire/release the actual output lock with the image's verified
            # identity, without entering a scientific stage. The binary dry-run
            # below independently checks that identity against its own payload.
            lease_code = ("import sys,pathlib;sys.path.insert(0,'/opt/oncotracer');"
                          "from oncotracer_cli.output_safety import claim_output_run\n"
                          f"with claim_output_run(pathlib.Path({str(output)!r}), "
                          f"config_path=pathlib.Path({str(config_path)!r}), identity={identity!r}):\n"
                          " print('CONTAINER_OUTPUT_LEASE_OK')\n")
            prefix = command[:command.index(args.image)]
            dispatch([*prefix, '--entrypoint', '/opt/conda/bin/python', args.image,
                      '-I', '-B', '-c', lease_code], **kwargs)
            command.append('--dry-run')
            calls.append('container-analysis-dry-run')
        elif 'preflight(sys.argv[1])' in str(command):
            calls.append('real-container-preflight')
        else:
            raise AssertionError('Unexpected scientific command: ' + str(command))
        return dispatch(command, **kwargs)

    with patch.object(cli, '_run', side_effect=safe_dispatch):
        code = cli.main(['setup', '--project', str(project), '--backend', 'docker',
                         '--image', args.image, '--run'])
    assert code == 0, 'Saved setup failed before Docker dispatch'
    assert calls == ['real-container-preflight', 'container-analysis-dry-run'], calls
    assert ledger.path.read_bytes() == recorded, 'Recorded stage state changed'
    assert product.read_text() == 'synthetic completed counts\n'
    assert audit_intermediates(project)['integrity_passed'] == 1
    assert json.loads((output / OUTPUT_OWNER_RELATIVE).read_text())['runtime_identity'] == identity
    # The ordinary dry-run path must also work without rewriting the owner.
    owner = (output / OUTPUT_OWNER_RELATIVE).read_bytes()
    with contextlib.redirect_stdout(io.StringIO()):
        code = cli.main(['run', '--config', str(config_path), '--backend', 'docker', '--dry-run'])
    assert code == 0
    assert (output / OUTPUT_OWNER_RELATIVE).read_bytes() == owner
    assert ledger.path.read_bytes() == recorded
    # CNA-only continuation uses the simpler project-mount path as well.
    config['run_variants'] = False
    config_path.write_text(render_flat_yaml(config))
    calls.clear()
    with patch.object(cli, '_run', side_effect=safe_dispatch):
        code = cli.main(['setup', '--project', str(project), '--backend', 'docker',
                         '--image', args.image, '--run'])
    assert code == 0 and calls == ['container-analysis-dry-run']
    assert ledger.path.read_bytes() == recorded
    print(json.dumps(dict(passed=True, synthetic_only=True, analysis_started=False,
                          image=args.image, stages_retained=1,
                          variant_and_cna_resume=True, real_container_leases=True)))


if __name__ == '__main__':
    main()
