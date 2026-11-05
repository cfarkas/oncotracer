"""Conservative continuation across application-only maintenance releases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root):
    result = {}
    for path in Path(root).rglob('*'):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('Workflow bundle contains a symlink')
        if path.is_file():
            result[str(path.relative_to(root))] = _digest(path)
    return result


def previous_run(root):
    """Read before the new controller publishes its running state."""
    try:
        status = json.loads((Path(root).parent / 'variant_status.json').read_text())
        if status.get('schema') != 'oncotracer-glimpse2-v1':
            return {}, {}
        rows = {row['sample']: row for row in status.get('samples', [])}
        runtimes = {}
        for sample in rows:
            if not sample or Path(sample).name != sample or sample in {'.', '..'}:
                raise ValueError('Unsafe historical sample')
            path = Path(root) / 'samples' / sample / 'pipeline_info/runtime.json'
            if path.is_file():
                runtimes[sample] = json.loads(path.read_text())
        return rows, runtimes
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}


def identical_bundle(current, runtimes):
    """Keep identical code/data paths so a new zipapp path is not new biology.

    The complete source/data inventory must match the newly installed bundle.
    Changed or missing files, extra files and symlinks disable this optimization.
    """
    current = Path(current).resolve()
    roots = {value.get('root') for value in runtimes.values()}
    if len(roots) != 1 or None in roots:
        return current
    prior = Path(next(iter(roots)))
    if prior == current or not prior.is_dir():
        return current
    try:
        expected = _inventory(current)
        if expected and _inventory(prior) == expected:
            return prior.resolve()
    except (OSError, ValueError):
        pass
    return current


def retain_memory_budget(params, prior):
    """Retain an earlier smaller memory cap, never exceed the current budget."""
    from .sample_resources import memory_gb
    try:
        previous = prior['memory']
        current = memory_gb(params['memory'])
        if (prior['threads'] == params['threads'] and type(previous) in (int, float)
                and 0 < previous <= current):
            params['memory'] = str(int(previous * 1024)) + 'MB'
    except (KeyError, TypeError, ValueError):
        pass


def completed_export(sample_root, prior_row, prior_runtime, current_runtime, validation):
    """Reuse a successfully completed sample only under exactly the same runtime.

    This applies to the Nextflow export, before cohort construction. FFPE review
    remains separately validated by its own implementation. All core/imputation
    inputs have already passed the normal continuation preflight. The full final
    VCF hash must match its completion summary and that summary must bind the
    exact runtime; presence alone never authorizes reuse.
    """
    if (prior_row.get('status') != 'complete' or not prior_runtime
            or prior_runtime != current_runtime or not validation
            or validation.get('rejected') or prior_row.get('ffpe_review')):
        return None
    sample = current_runtime['sample']
    root = Path(sample_root)
    path = root / 'cohort_vcf' / (sample + '.cohort_vcf') / (sample + '.annotated.vcf.gz')
    try:
        if Path(prior_row.get('annotated_vcf', '')).resolve() != path.resolve():
            return None
        summary = json.loads(path.with_name(sample + '.cohort_vcf.json').read_text())
        signature = hashlib.sha256(json.dumps(current_runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (summary.get('sample') != sample or summary.get('runtime_signature') != signature
                or summary.get('vcf_sha256') != _digest(path)
                or not Path(str(path) + '.tbi').is_file()
                or not (root / 'START_HERE.html').is_file()):
            return None
        return path
    except (OSError, ValueError, TypeError):
        return None
