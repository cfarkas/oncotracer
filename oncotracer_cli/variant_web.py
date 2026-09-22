"""Read-only input review and preparation for browser existing-BAM analyses."""
from __future__ import annotations

import os
from pathlib import Path

from .runtime import OncoTracerError, load_flat_yaml, require_file, sha256_file
from .variant_command import _check_overlap, _path, read_bam_manifest
from .variants import resolve_variant_request
from .variant_model_assets import is_auto_resource, resource_download_plan


PATH_FIELDS = (
    'variant_targets_bed', 'variant_tool_prefix', 'variant_clair3_model',
    'variant_clairsto_sif', 'variant_annovar_dir', 'variant_annovar_db',
    'variant_ffperase_root', 'variant_ffperase_models', 'variant_ffperase_sif',
    'variant_ffperase_prefix', 'variant_varlociraptor_scenario',
)


def snapshot(paths):
    """Track identities without reading large BAMs, references or databases."""
    result = {}
    for value in paths:
        path = Path(value)
        stat = path.stat()
        result[str(path)] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def assert_snapshot(saved, digests):
    try:
        current = snapshot(saved)
        hashes = {path: sha256_file(Path(path)) for path in digests}
    except OSError as error:
        raise OncoTracerError('Reviewed variant inputs or resources are missing. Load and review the configuration again.') from error
    if current != saved or hashes != digests:
        raise OncoTracerError('Reviewed variant inputs or resources changed. Load and review the configuration again.')


def resource_paths(config):
    """Capture model files, tool entry points and environment package records."""
    paths = []
    for key in PATH_FIELDS:
        if not config.get(key) or is_auto_resource(key, config.get(key)):
            continue
        path = Path(str(config[key]))
        if not path.exists():
            # Optional annotation discovery reports absent paths at execution.
            continue
        paths.append(path)
        if not path.is_dir():
            continue
        if key in ('variant_tool_prefix', 'variant_ffperase_prefix'):
            for directory in ('bin', 'conda-meta'):
                parent = path / directory
                if parent.is_dir():
                    paths.append(parent)
                    paths.extend(p for p in parent.iterdir() if p.is_file())
        elif key == 'variant_ffperase_root':
            paths.extend(p for p in (path / 'bin').rglob('*') if p.exists())
        else:
            paths.extend(p for p in path.rglob('*') if p.exists())
    return paths


def load_configuration(config_path):
    config_path = require_file(Path(config_path).expanduser(), 'Variant configuration').resolve()
    digest = sha256_file(config_path)
    config = load_flat_yaml(config_path)
    for key in ('variant_reference', 'variant_bam_manifest', 'outdir', *PATH_FIELDS):
        if config.get(key) and not is_auto_resource(key, config[key]):
            config[key] = str(_path(config[key], config_path.parent, key).resolve())
    mode = str(config.get('mode') or '').strip().lower()
    config['mode'] = mode
    request = resolve_variant_request(config, mode=mode)
    if request is None:
        raise OncoTracerError('Set run_variants: true in the existing-BAM configuration.')
    manifest = require_file(_path(config.get('variant_bam_manifest'), config_path.parent, 'variant_bam_manifest'), 'Variant BAM manifest')
    manifest_digest = sha256_file(manifest)
    bams, statuses = read_bam_manifest(manifest)
    reference = require_file(_path(config.get('variant_reference'), config_path.parent, 'variant_reference'), 'Variant reference FASTA')
    with reference.open('rb') as handle:
        if handle.read(1) != b'>':
            raise OncoTracerError('variant_reference must be an uncompressed FASTA beginning with >')
    threads = config.get('threads', min(os.cpu_count() or 1, 16))
    if type(threads) is not int or threads < 1:
        raise OncoTracerError('threads must be a positive integer')
    config['threads'] = threads
    files = [config_path, manifest, reference, *bams.values(), *resource_paths(config)]
    for source in (reference, *bams.values()):
        suffixes = ('.fai',) if source == reference else ('.bai', '.csi')
        files.extend(Path(str(source) + suffix) for suffix in suffixes if Path(str(source) + suffix).exists())
        if source != reference and source.with_suffix('.bai').exists():
            files.append(source.with_suffix('.bai'))
    hashes = {str(config_path): digest, str(manifest): manifest_digest}
    captured = snapshot(files)
    assert_snapshot(captured, hashes)
    return {'config_path': str(config_path), 'config': config, 'request': request,
            'samples': [{'sample': name, 'bam': str(bam), 'status': statuses[name]} for name, bam in bams.items()],
            'input_snapshot': captured, 'input_digests': hashes}


def protect_project(project, config, inputs):
    """A removable browser project must never contain or sit inside an input."""
    directories = [Path(str(config[key])) for key in PATH_FIELDS
                   if config.get(key) and not is_auto_resource(key, config[key]) and Path(str(config[key])).is_dir()]
    _check_overlap(project, [Path(path) for path in inputs], directories)


def load_for_browser(state, data):
    import secrets
    from .web import _text
    with state.lock:
        loaded = load_configuration(_text(data, 'config_path'))
        load_id = secrets.token_urlsafe(18)
        if len(state.variant_loads) >= 32:
            state.variant_loads.pop(next(iter(state.variant_loads)))
        state.variant_loads[load_id] = loaded
        parent = Path(loaded['config_path']).parent
        stem = Path(loaded['config_path']).stem
        suggested = parent / (stem + '-rerun')
        number = 1
        while suggested.exists():
            number += 1
            suggested = parent / (stem + f'-rerun-{number}')
        return {'load_id': load_id, 'config': loaded['config'].copy(), 'samples': loaded['samples'],
                'suggested_project': str(suggested), 'mode': loaded['config']['mode']}


def prepare_for_browser(state, data):
    import secrets
    from .engine import Toolchain
    from .runtime import atomic_write_text, render_flat_yaml
    from .setup import VARIANT_FIELDS, VARIANT_BOOLEAN_FIELDS
    from .variants import preflight_variant_tools
    from .web import _fingerprint, _text
    with state.lock:
        if state.job and state.job['status'] in {'running', 'stopping'}:
            raise OncoTracerError('An analysis is running. Wait for it to finish before preparing another project.')
        loaded = state.variant_loads.get(_text(data, 'load_id'))
        if loaded is None:
            raise OncoTracerError('Configuration review expired. Load the variant configuration again.')
        assert_snapshot(loaded['input_snapshot'], loaded['input_digests'])
        config = loaded['config'].copy()
        for key in VARIANT_FIELDS:
            if key in data:
                if data[key] == '':
                    config.pop(key, None)
                elif key in VARIANT_BOOLEAN_FIELDS:
                    if type(data[key]) is not bool:
                        raise OncoTracerError(f"{key} must be true or false.")
                    config[key] = data[key]
                else:
                    config[key] = _text(data, key)
        for key in PATH_FIELDS:
            if config.get(key) and not is_auto_resource(key, config[key]):
                config[key] = str(_path(config[key], Path(loaded['config_path']).parent, key).resolve())
        request = resolve_variant_request(config, mode=config['mode'])
        threads = data.get('threads')
        maximum = state.system()['hardware']['cpu_workers_available']
        if type(threads) is not int or not 1 <= threads <= maximum:
            raise OncoTracerError(f'CPU threads must be a whole number from 1 to {maximum}.')
        supplied = Path(_text(data, 'project')).expanduser()
        if supplied.is_symlink():
            raise OncoTracerError('Choose a new project folder, not a symlink.')
        project = supplied.resolve()
        previous = None
        if project.exists():
            previous = next((item for item in state.projects.values()
                             if item.get('kind') == 'existing_bam_variants' and item['project'] == str(project)), None)
            if previous is None:
                raise OncoTracerError('Choose a new project folder; existing folders are protected.')
            current = project.stat()
            config_directory = project / 'config'
            old_config = config_directory / 'run.yml'
            if (state.job and state.job['project_id'] == previous['id']
                    or (current.st_dev, current.st_ino) != previous['project_identity']
                    or config_directory.is_symlink() or old_config.is_symlink()
                    or set(project.iterdir()) != {config_directory}
                    or not config_directory.is_dir() or set(config_directory.iterdir()) != {old_config}
                    or _fingerprint([old_config]) != previous['fingerprint']):
                raise OncoTracerError('This project has started or changed. Choose a new project folder.')
        config.update(outdir=str(project / 'results'), threads=threads, force=False)
        inputs = {**loaded['input_snapshot'], **snapshot(resource_paths(config))}
        protect_project(project, config, inputs)
        pending = resource_download_plan(request)
        check = {'errors': [], 'warnings': ['BAM contents are validated when the analysis starts.'], 'resource_downloads': pending}
        if pending:
            check['warnings'].append('Selected public resources will be verified and prepared when Run starts; setup downloads nothing.')
        try:
            tools = preflight_variant_tools(request, Toolchain.from_environment())
            if any(item['resource'].startswith('ffperase') for item in pending):
                from .variant_model_assets import preflight_ffperase_runtime
                preflight_ffperase_runtime(request)
            inputs.update(snapshot(tools.values()))
            if request.ffperase != 'off' and not any(item['resource'].startswith('ffperase') for item in pending):
                from .ffperase import discover
                detection = discover(request.ffperase_root, request.ffperase_models,
                                     request.ffperase_sif, request.ffperase_prefix)
                inputs.update(snapshot(detection['resources']))
                check['ffperase'] = {'status': 'available', 'root': detection['root'], 'models': detection['models']}
            check['tools'] = tools
        except (OncoTracerError, OSError, ValueError) as error:
            check['errors'].append(str(error))
        # Auto-discovered resources must also remain outside the removable project.
        protect_project(project, config, inputs)
        assert_snapshot(inputs, loaded['input_digests'])
        if previous is None:
            project.mkdir(parents=True, exist_ok=False)
            (project / 'config').mkdir()
        config_path = project / 'config/run.yml'
        if previous is None:
            with config_path.open('x', encoding='utf-8') as handle:
                handle.write(render_flat_yaml(config))
        else:
            atomic_write_text(config_path, render_flat_yaml(config))
            state.projects.pop(previous['id'])
        project_stat = project.stat()
        prepared = {'id': secrets.token_urlsafe(18), 'project': str(project),
                    'config_path': str(config_path), 'config': config_path.read_text(),
                    'backend': 'host', 'valid': not check['errors'], 'check': check,
                    'outdir': str(project / 'results'), 'kind': 'existing_bam_variants',
                    'fingerprint': _fingerprint([config_path]), 'input_snapshot': inputs,
                    'input_digests': loaded['input_digests'].copy(), 'project_created': True,
                    'project_identity': (project_stat.st_dev, project_stat.st_ino)}
        state.projects[prepared['id']] = prepared
        return {key: prepared[key] for key in ('id', 'project', 'config_path', 'config', 'backend', 'valid', 'check', 'outdir')}



def published_partial_result(prepared, prior_status_mtime):
    """Only finalized, manifest-backed results qualify as a partial completion."""
    import json
    try:
        outdir = Path(prepared['outdir'])
        status_path = outdir / '08_variants/variant_status.json'
        summary_path = outdir / '06_workflow_summary/workflow_summary.json'
        manifest_path = outdir / '06_workflow_summary/native_run_manifest.json'
        index = outdir / 'index.html'
        status_time = status_path.stat().st_mtime_ns
        if status_time == prior_status_mtime or not index.is_file():
            return False
        # Presentation is published after the run manifest; reject stale pages.
        if not status_time <= manifest_path.stat().st_mtime_ns <= index.stat().st_mtime_ns:
            return False
        status = json.loads(status_path.read_text())
        summary = json.loads(summary_path.read_text())
        manifest = json.loads(manifest_path.read_text())
        if (status.get('overall_status') != 'partial_failure'
                or summary.get('analysis') != 'variants'
                or summary.get('workflow_status') != 'partial_failure'
                or summary.get('variant_status') != 'partial_failure'
                or manifest.get('schema') != 'oncotracer-native-run-manifest-v1'
                or manifest.get('workflow_status') != 'partial_failure'
                or manifest.get('variant_status') != 'partial_failure'
                or manifest.get('config_sha256') != prepared['fingerprint'][prepared['config_path']]):
            return False
        saved_hashes = {item['path']: item['sha256'] for item in manifest.get('files', [])}
        return all(saved_hashes.get(str(path.relative_to(outdir))) == sha256_file(path)
                   for path in (status_path, summary_path))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
