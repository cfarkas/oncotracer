"""Read-only runtime checks before reference downloads or sample computation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .runtime import OncoTracerError
from .nextflow_runtime import isolated_nextflow_environment, prepare_nextflow_environment

MODULES = ('numpy', 'scipy', 'pysam', 'openpyxl', 'reportlab')


class ReadyTools(dict):
    """Keep the executable-path mapping compatible with browser inventories."""

    def __init__(self, tools, environment):
        super().__init__(tools)
        self.nextflow_environment = dict(environment)


def _environment():
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    return env


def probe(command, label, *, timeout=60, marker=None, env=None):
    try:
        result = subprocess.run(list(map(str, command)), text=True, capture_output=True,
                                env={**_environment(), **(env or {})}, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OncoTracerError(f'{label} is not ready: {error}') from error
    output = (result.stdout or '') + '\n' + (result.stderr or '')
    if result.returncode or (marker is not None and marker not in output):
        detail = output.strip()[-1500:]
        raise OncoTracerError(f'{label} is not ready (exit {result.returncode}): {detail}')
    return output


def analysis_python(request, toolchain=None):
    candidates = [sys.executable]
    core = getattr(toolchain, 'core_prefix', None)
    if core:
        candidates.append(str(Path(core) / 'bin/python'))
    # The lightweight CLI may run outside the scientific environment. Reuse the
    # installed scientific interpreter without requiring a controller override.
    settings = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'oncotracer/config.json'
    if settings.is_file():
        try:
            installed = json.loads(settings.read_text())
            if installed.get('core_prefix'):
                candidates.append(str(Path(installed['core_prefix']) / 'bin/python'))
        except (OSError, ValueError, TypeError):
            pass
    if request.tool_prefix:
        candidates.append(str(request.tool_prefix / 'bin/python'))
    candidates.extend(filter(None, [shutil.which('python3'), '/usr/bin/python3']))
    code = ('import sys,importlib; '
            'assert (3,10) <= sys.version_info[:2] < (3,14), "Python 3.10–3.13 required"; '
            f'[importlib.import_module(name) for name in {MODULES!r}]; '
            'print("ONCOTRACER_SCIENTIFIC_PYTHON_OK")')
    errors = []
    for candidate in dict.fromkeys(candidates):
        if not Path(candidate).is_file():
            continue
        try:
            probe([candidate, '-I', '-B', '-c', code], f'Scientific Python {candidate}',
                  marker='ONCOTRACER_SCIENTIFIC_PYTHON_OK')
            # Do not resolve symlinks: virtual environments may depend on the
            # executable path to locate pyvenv.cfg.
            return str(Path(candidate).absolute())
        except OncoTracerError as error:
            errors.append(str(error))
    raise OncoTracerError('No ready GLIMPSE2 analysis Python with ' + ', '.join(MODULES) +
                         '. Prepare the managed core environment.\n' + '\n'.join(errors))


def check(request, tools, toolchain, bundle, *, environment=None):
    tools = dict(tools)
    for name in ('samtools', 'bcftools', 'gatk'):
        if name in tools:
            output = probe([tools[name], '--version'], name)
            if name == 'bcftools':
                spec = importlib.util.spec_from_file_location('_oncotracer_caller_contract', bundle / 'bin/bcftools_compat.py')
                contract = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(contract)
                try:
                    runtime = contract.require_version(output, tools[name])
                except ValueError as error:
                    raise OncoTracerError(str(error)) from error
                print(contract.version_diagnostic(runtime), file=sys.stderr, flush=True)
    child = _environment() if environment is None else dict(environment)
    prepared = prepare_nextflow_environment(tools['nextflow'], child)
    with isolated_nextflow_environment({**child, **prepared}) as nextflow_env:
        probe([tools['nextflow'], '-version'], 'Nextflow', env={**child, **prepared, **nextflow_env})
    probe([shutil.which('java') or 'java', '-version'], 'Java')
    tools['python'] = analysis_python(request, toolchain)
    spec = importlib.util.spec_from_file_location('_oncotracer_report_readiness', bundle / 'bin/llm_reporting.py')
    reporting = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporting)
    options = request.glimpse_config or {}
    config = {'interpretation_report': True, 'explain_findings': True, 'report_llm': True,
              'report_pdf': True, 'threads': 1, 'llm_threads': 1, 'output': str(Path.cwd())}
    for key in ('llm_python', 'llm_model'):
        if options.get(key):
            config[key] = options[key]
    try:
        reporting.prepare(config)
        # Parse the local tokenizer/config without loading multi-GB model weights,
        # contacting a model service, or generating text.
        code = ('import sys; from transformers import AutoConfig,AutoTokenizer,AutoModelForCausalLM; '
                'AutoConfig.from_pretrained(sys.argv[1],local_files_only=True,trust_remote_code=False); '
                'AutoTokenizer.from_pretrained(sys.argv[1],local_files_only=True,trust_remote_code=False); '
                'print("ONCOTRACER_LOCAL_REPORT_OK")')
        probe([config['llm_python'], '-I', '-B', '-c', code, config['llm_model_directory']],
              'Local report environment and model', marker='ONCOTRACER_LOCAL_REPORT_OK')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise OncoTracerError(f'Local report resources are not ready: {error}') from error
    tools['llm_python'] = config['llm_python']
    tools['llm_model'] = config['llm_model_directory']
    return ReadyTools(tools, prepared)
