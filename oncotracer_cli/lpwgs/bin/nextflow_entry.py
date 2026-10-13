#!/usr/bin/env python3
"""JSON bridge for native Nextflow startup; prepares inputs without launching Nextflow."""
import argparse
import contextlib
import json
from pathlib import Path
import re
import shlex
import sys

import launch


def boolean(value, name):
    if isinstance(value, bool): return value
    if str(value).lower() in {'true', 'false'}: return str(value).lower() == 'true'
    raise ValueError(f'--{name}: expected true or false, got {value!r}')


def fastq_inputs(value, extra=()):
    if isinstance(value, (list, tuple)):
        paths = list(value)
    elif isinstance(value, str) and value.strip():
        if value.lstrip().startswith('['):
            paths = json.loads(value)
            if not isinstance(paths, list): raise ValueError('--fastq JSON must be a list of paths')
        elif ',' in value and not Path(value).expanduser().exists():
            paths = [p.strip() for p in value.split(',')]
        else:
            paths = [value]
    else:
        raise ValueError('Supply --fastq /path/to/patient_folder or /path/to/reads_R1.fastq.gz; see --help')
    paths.extend(extra)
    if not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError('--fastq requires nonempty file/folder paths')
    return paths



def normalize(parameters):
    # Nextflow 26 converts --traits-panel to traitsPanel; params files commonly
    # use traits_panel. Explicit aliases override underscore defaults.
    def name(key):
        return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key.replace('-', '_')).lower()
    values = {}
    for key, value in sorted(parameters.items(), key=lambda kv: name(kv[0]) != kv[0]):
        if value is not None: values[name(key)] = value
    return values


def arguments(parameters, extra=()):
    """Use the same types, defaults, and validation as the optional shell launcher."""
    parameters = normalize(parameters)
    parser = launch.parser()
    actions = {a.dest: a for a in parser._actions if a.dest != 'help'}
    aliases = {opt[2:].replace('-', '_'): a.dest for a in actions.values() for opt in a.option_strings}
    values = {}
    # Dashed aliases override their default underscore spelling from nextflow.config.
    for key, value in sorted(parameters.items(), key=lambda kv: '-' in kv[0]):
        name = key.replace('-', '_')
        if name in {'help', 'manifest', 'runtime', 'memory_gb'}: continue
        if name not in aliases: raise ValueError(f'Unknown parameter --{key}; see nextflow run main.nf --help')
        dest = aliases[name]
        if value is not None:
            action=actions[dest]
            values[dest] = not boolean(value,name) if isinstance(action,argparse.BooleanOptionalAction) and name.startswith('no_') else value
    if boolean(values.get('resume', False), 'resume'):
        raise ValueError('Use the Nextflow option -resume (one hyphen), not --resume')
    if parameters.get('memory_gb') is not None:
        values['memory'] = str(parameters['memory_gb']) + 'GB'
    if values.get('bam'):
        if values.get('fastq') or extra: raise ValueError('Supply --bam or --fastq, not both')
        values.pop('fastq',None)
    else: values['fastq'] = fastq_inputs(values.get('fastq'), extra)
    argv = []
    for name, value in values.items():
        action = actions[name]
        option = action.option_strings[0]
        if isinstance(action, argparse.BooleanOptionalAction):
            argv.append(option if boolean(value, option[2:]) else '--no-'+option[2:])
        elif isinstance(action, argparse._StoreTrueAction):
            if boolean(value, option[2:]): argv.append(option)
        elif name == 'fastq':
            argv.extend([option, *value])
        elif name == 'glimpse_chromosomes' and isinstance(value,(list,tuple)):
            argv.extend([option,','.join(map(str,value))])
        elif name == 'panel_of_normals' and isinstance(value,(list,tuple)):
            argv.extend([option,json.dumps(value)])
        else:
            argv.extend([option, str(value)])
    return parser.parse_args(argv)



def positional_fastqs(request):
    extra = list(request.get('extra_args', []))
    tokens = shlex.split(request.get('command_line', ''))
    # Nextflow 26 leaves a named -resume value in `args` even after consuming it
    # as an engine option. It must not become an additional patient FASTQ.
    for index, token in enumerate(tokens[:-1]):
        if token == '-resume' and not tokens[index + 1].startswith('-'):
            target = tokens[index + 1]
            if target in extra: extra.remove(target)
    return extra


def prepare_request(request):
    parameters = normalize(request.get('parameters', {}))
    if boolean(parameters.get('help', False), 'help'):
        p = launch.parser()
        p.prog = 'nextflow run main.nf'
        for action in p._actions:
            if action.dest in {'resume', 'work_dir'}: action.help = argparse.SUPPRESS
        p.epilog += ('\nNative Nextflow options: -resume to resume; -work-dir DIR to set the work folder; '
                    '-params-file FILE for JSON/YAML parameters. --fastq also accepts comma-separated '
                    'paths, a JSON array, or additional space-separated paths. No manifest is needed.')
        return {'run': False, 'help': p.format_help()}
    has_legacy = parameters.get('manifest') or parameters.get('runtime')
    if has_legacy:
        if parameters.get('fastq'): raise ValueError('Use --fastq or the legacy manifest/runtime parameters, not both')
        if not parameters.get('manifest') or not parameters.get('runtime'):
            raise ValueError('Legacy mode requires both --manifest and --runtime; prefer --fastq')
        # Refresh old generated configurations through current preflight checks/code fingerprint.
        prior = json.loads(launch.checked_path(parameters['runtime']).read_text())
        names = {a.dest for a in launch.parser()._actions}
        inputs = {k: v for k, v in prior.items() if k in names and k not in {'help', 'resume', 'work_dir'}}
        if any(not Path(p).is_absolute() for p in inputs.get('fastq', [])):
            inputs['fastq'] = [record['path'] for record in prior['inputs']]
        for name in ['output', 'threads', 'memory_gb', 'preflight_only', 'continue', 'glimpse2', 'python']:
            if parameters.get(name) is not None: inputs[name] = parameters[name]
        parameters = inputs
    args = arguments(parameters, positional_fastqs(request))
    with contextlib.redirect_stdout(sys.stderr):
        prepared = launch.prepare(args)
    return {**prepared, 'run': not args.preflight_only}


def main():
    try:
        result = prepare_request(json.load(sys.stdin))
        json.dump(result, sys.stdout)
        sys.stdout.write('\n')
        return 0
    except (ValueError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__': sys.exit(main())
