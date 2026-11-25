"""Dependency inventory and approved installation shared by both setup interfaces."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .runtime import OncoTracerError
from .variant_installer import VariantInstaller, _executable

BACKENDS = ('docker', 'conda', 'host', 'poetry', 'singularity')
OFFICIAL_REPOSITORIES = ('carlosfarkas/oncotracer', 'ghcr.io/cfarkas/oncotracer')
TOOLS = ('docker', 'conda', 'mamba', 'micromamba', 'poetry', 'apptainer', 'singularity',
         'bwa', 'minimap2', 'samtools', 'bcftools', 'pigz', 'picard', 'Rscript',
         'gatk', 'freebayes', 'varlociraptor', 'run_clair3.sh', 'run_clairs_to',
         'configureStrelkaSomaticWorkflow.py', 'readCounter', 'gistic2', 'nextflow',
         'dorado', 'rockfish', 'modkit', 'sturgeon', 'aria2c', '7z')
CONTAINER_INVENTORY = """import json,os,shutil,sys
from pathlib import Path
folders=[Path(v)/'bin' for k,v in os.environ.items() if k.startswith('ONCOTRACER_') and k.endswith('_PREFIX')]
folders += [Path('/opt/oncotracer-envs')/name/'bin' for name in ('core','qdnaseq','ichorcna','classifier','gistic','variants','strelka2','ffperase','marlin','rockfish','sturgeon')]
rows=[]
for name in json.loads(sys.argv[1]):
    path=shutil.which(name) or next((str(folder/name) for folder in folders if (folder/name).is_file() and os.access(folder/name,os.X_OK)), '')
    rows.append({'name':name,'path':path,'status':'found' if path else 'not_found','scope':'Docker image'})
print(json.dumps({'tools':rows}))
"""
IMAGE_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}\Z')
_RELEASE_CACHE = None
_RELEASE_LOCK = threading.Lock()


def launcher():
    from .web import _launcher
    return _launcher()


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    return tuple(map(int, match.groups())) if match else None


def _json_url(url, limit=512 * 1024):
    request = urllib.request.Request(url, headers={'User-Agent': 'OncoTracer-setup', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Release metadata exceeds the size limit')
    return json.loads(raw), raw


def latest_release():
    """Read the stable release and its image digest; never download or execute tools."""
    global _RELEASE_CACHE
    with _RELEASE_LOCK:
        if _RELEASE_CACHE and time.monotonic() - _RELEASE_CACHE[0] < (600 if _RELEASE_CACHE[1]['status'] == 'checked' else 15):
            return copy.deepcopy(_RELEASE_CACHE[1])
        try:
            release, _ = _json_url('https://api.github.com/repos/cfarkas/oncotracer/releases/latest')
            version = str(release.get('tag_name', '')).removeprefix('v')
            if release.get('draft') or release.get('prerelease') or not version_tuple(version):
                raise ValueError('The latest release is not a stable semantic version')
            expected_url = f'https://github.com/cfarkas/oncotracer/releases/download/v{version}/release-provenance.json'
            asset = next((a for a in release.get('assets', []) if a.get('name') == 'release-provenance.json'), {})
            if asset.get('browser_download_url') != expected_url:
                raise ValueError('Published image provenance is unavailable')
            provenance, raw = _json_url(expected_url)
            checksum = asset.get('digest')
            if checksum and checksum != 'sha256:' + hashlib.sha256(raw).hexdigest():
                raise ValueError('Release metadata checksum does not match GitHub')
            digest = provenance.get('container', {}).get('digest', '')
            if provenance.get('version') != version or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Published image identity is invalid')
            result = {'status': 'checked', 'version': version, 'digest': digest,
                      'url': f'https://github.com/cfarkas/oncotracer/releases/tag/v{version}'}
        except (OSError, ValueError, StopIteration, urllib.error.URLError) as error:
            result = {'status': 'unavailable', 'message': 'Could not check online updates: ' + str(error)}
        _RELEASE_CACHE = (time.monotonic(), result)
        return copy.deepcopy(result)


def probe(command, *, timeout=120):
    """Run only a known diagnostic, with a deadline and no terminal input."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr[-5000:]}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return {'returncode': 124, 'stdout': stdout, 'stderr': 'Dependency check timed out. Retry the check; no installation was started.'}


def _report(result):
    try:
        value = json.loads(result['stdout'])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def selection(data=None):
    from .cli import DEFAULT_IMAGE, _load_install_config
    data = data or {}
    if not isinstance(data, dict) or set(data) - {'backend', 'image'}:
        raise OncoTracerError('Dependency checks accept only backend and image settings.')
    for key in ('backend', 'image'):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise OncoTracerError(f'{key} must be text.')
    install = _load_install_config()
    backend = data.get('backend') or install.get('backend') or ('docker' if _executable('docker') else 'conda')
    image = data.get('image') or install.get('image') or DEFAULT_IMAGE
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise OncoTracerError('Choose Docker, Conda, existing host tools, Poetry or Singularity.')
    if not isinstance(image, str) or not IMAGE_PATTERN.fullmatch(image):
        raise OncoTracerError('Provide a Docker image name or digest, without command options.')
    return {'backend': backend, 'image': image}


def inspect_dependencies(data=None, *, progress=lambda message: None):
    from .cli import _configured_native_prefixes, _load_install_config
    chosen = selection(data)
    backend, image = chosen['backend'], chosen['image']
    progress('Finding installed tools and environments')
    install = _load_install_config()
    prefixes = _configured_native_prefixes(install)
    from .variant_resources import _Search
    search = _Search(None, ())
    directories = [path / 'bin' for path in prefixes.values() if path]
    directories.extend(path / 'bin' for path in search.prefixes())
    for key in ('strelka', 'ffperase', 'marlin', 'rockfish', 'sturgeon'):
        value = install.get(key + '_prefix') or os.environ.get('ONCOTRACER_' + key.upper() + '_PREFIX')
        if value:
            directories.append(Path(value).expanduser() / 'bin')
    directories = list(dict.fromkeys(directories))
    tools = []
    for name in TOOLS:
        path = _executable(name)
        if not path:
            path = next((str(folder / name) for folder in directories
                         if (folder / name).is_file() and os.access(folder / name, os.X_OK)), None)
        tools.append({'name': name, 'path': path or '', 'status': 'found' if path else 'not_found'})
    report = {'schema': 'oncotracer-setup-dependencies-v1', 'launcher_version': __version__,
              'selection': chosen, 'tools': tools, 'environments': [], 'actions': [],
              'warnings': list(search.notes), 'ready': False, 'image': {}, 'container_tools': [], 'checked_at': time.time()}
    progress('Checking the latest published OncoTracer release')
    release = latest_release()
    report['release'] = release
    if release['status'] != 'checked':
        report['warnings'].append(release['message'] + '. Installed tools can still be checked offline.')
    elif version_tuple(__version__) and version_tuple(release['version']) > version_tuple(__version__):
        report['warnings'].append(f"OncoTracer {release['version']} is available. This setup process is running {__version__}; upgrading the launcher requires restarting setup.")
    paths = {row['name']: row['path'] for row in tools}
    health = {}
    image_present = False
    if backend == 'docker':
        docker = paths['docker']
        if not docker:
            report['warnings'].append('Docker is not installed. Install Docker Engine, or select a detected Conda installation below.')
        else:
            progress('Checking access to Docker')
            access = probe([docker, 'info', '--format', '{{json .ServerVersion}}'], timeout=15)
            if access['returncode']:
                report['warnings'].append('Docker is installed but its engine is unavailable: ' + access['stderr'].strip())
            else:
                inspected = probe([docker, 'image', 'inspect', image, '--format', '{{json .}}'], timeout=15)
                info = _report(inspected)
                image_present = inspected['returncode'] == 0 and bool(info)
                labels = info.get('Config', {}).get('Labels') or {}
                report['image'] = {'reference': image, 'present': image_present,
                    'version': labels.get('org.opencontainers.image.version', ''),
                    'id': info.get('Id', ''), 'digests': info.get('RepoDigests', []),
                    'update_status': 'unknown' if release['status'] != 'checked' else 'selected'}
                if image_present:
                    progress('Verifying tools inside the installed Docker image')
                    container = 'oncotracer-dependency-check-' + secrets.token_hex(8)
                    command = [docker, 'run', '--rm', '--pull=never', '--network', 'none',
                        '--name', container, '--env', 'PYTHONDONTWRITEBYTECODE=1',
                        image, 'doctor', '--backend', 'host', '--json']
                    checked = probe(command, timeout=180)
                    if checked['returncode'] == 124:
                        probe([docker, 'rm', '--force', container], timeout=10)
                    health = _report(checked)
                    if health:
                        inventory = probe([docker, 'run', '--rm', '--pull=never', '--network', 'none',
                            '--entrypoint', 'python3', image, '-B', '-c', CONTAINER_INVENTORY, json.dumps(TOOLS)], timeout=30)
                        report['container_tools'] = _report(inventory).get('tools', [])
                    if not health:
                        report['warnings'].append('The image check did not return a valid report: ' + checked['stderr'].strip())
                official = any(image.startswith(repo + ':') or image.startswith(repo + '@') for repo in OFFICIAL_REPOSITORIES)
                target, label, reason = image, 'Install or repair the selected Docker image', 'The selected image or its required tools are missing.'
                if official and release['status'] == 'checked' and '@' not in image:
                    current = version_tuple(report['image'].get('version')) or version_tuple(image.rsplit(':', 1)[-1])
                    newest = version_tuple(release['version'])
                    if current is None or newest >= current:
                        target = f"carlosfarkas/oncotracer:{release['version']}@{release['digest']}"
                        matches = any(value.endswith('@' + release['digest']) for value in report['image']['digests'])
                        report['image']['update_status'] = 'current' if matches else 'update_available'
                        if not matches:
                            label = f"Install verified OncoTracer {release['version']} image"
                            reason = 'The latest stable image is not installed with its published digest.'
                if not image_present or not health.get('success') or report['image']['update_status'] == 'update_available':
                    report['actions'].append({'id': 'docker', 'label': label, 'backend': 'docker', 'image': target, 'reason': reason})
                if not official and not health.get('success') and release['status'] == 'checked':
                    report['actions'].append({'id': 'official-docker',
                        'label': f"Use verified OncoTracer {release['version']} image", 'backend': 'docker',
                        'image': f"carlosfarkas/oncotracer:{release['version']}@{release['digest']}",
                        'reason': 'Use the official image instead of repairing this custom image. The custom image is retained.'})
    else:
        progress('Verifying the selected native installation')
        checked = probe([*launcher(), 'doctor', '--backend', backend, '--json'], timeout=180)
        health = _report(checked)
        if not health:
            report['warnings'].append('The installation check did not return a valid report: ' + checked['stderr'].strip())
    report['ready'] = bool(health.get('success'))
    report['health'] = health
    for name, value in health.get('environments', {}).items():
        report['environments'].append({'name': name, 'status': 'verified' if value.get('success') else 'missing',
            'path': health.get('prefixes', {}).get(name, {}).get('path', ''),
            'message': value.get('error', ''), 'probes': value.get('probes', {})})
    environments_ready = bool(report['environments']) and all(item['status'] == 'verified' for item in report['environments'])
    if health.get('source', {}).get('success') is False:
        report['warnings'].append('The launcher or image source identity could not be verified. Use an official release; reinstalling analysis tools alone will not fix this check.')
    if not report['ready'] and not environments_ready and backend != 'docker':
        manager = paths['conda']
        if manager:
            report['actions'].append({'id': 'native', 'label': 'Install analysis environments with Conda',
                'backend': 'conda',
                'reason': 'One or more required analysis environments are unavailable.'})
        else:
            report['warnings'].append('Conda is required for automatic installation of the full native runtime.')
    if backend == 'docker' and not report['ready'] and paths['conda']:
        report['actions'].append({'id': 'native', 'label': 'Install analysis environments with Conda',
            'backend': 'conda', 'reason': 'Build a separate native runtime with the detected Conda installation.'})
    report['note'] = ('Found executables are listed separately from verified environments. '
                      'Models, references and licensed ANNOVAR/Sturgeon assets are checked when their workflow is selected.')
    return report


class DependencySetup(VariantInstaller):
    """Server-owned reports and approval plans; reuse the existing background worker."""
    def __init__(self, start_dir, *, install_root=None):
        super().__init__(start_dir, install_root=install_root)
        self.check = {'status': 'idle'}
        self.check_thread = None

    def inspect(self, data):
        chosen = selection(data)
        with self.lock:
            self.require_idle()
            if self.check['status'] == 'checking':
                if self.check['selection'] != chosen:
                    raise OncoTracerError('A dependency check is already running. Wait, then check the new selection.')
                return self.check_status()
            self.plans.clear()
            self.check = {'id': secrets.token_hex(12), 'status': 'checking', 'selection': chosen,
                          'stage': 'Checking installed dependencies', 'started_at': time.time()}
            self.check_thread = threading.Thread(target=self._inspect, args=(chosen,), daemon=True,
                                                name='oncotracer-dependency-check')
            self.check_thread.start()
            return self.check_status()

    def _inspect(self, chosen):
        try:
            def progress(message):
                with self.lock:
                    self.check['stage'] = message
            report = inspect_dependencies(chosen, progress=progress)
            with self.lock:
                self.check.update(status='complete', report=report, stage='Dependency check complete')
        except Exception as error:
            with self.lock:
                self.check.update(status='failed', error=str(error), stage='Dependency check failed')

    def check_status(self):
        with self.lock:
            return copy.deepcopy(self.check)

    def plan(self, data):
        with self.lock:
            self.require_idle()
            if not isinstance(data, dict):
                raise OncoTracerError('Review a current dependency installation plan.')
            if self.check['status'] != 'complete' or data.get('check_id') != self.check.get('id'):
                raise OncoTracerError('Run the dependency check again before reviewing installation.')
            if time.time() - self.check['started_at'] > 900:
                raise OncoTracerError('The dependency check expired. Check again before installing.')
            action = next((row for row in self.check['report']['actions'] if row['id'] == data.get('action_id')), None)
            if not action:
                raise OncoTracerError('Choose an installation from the current dependency report.')
            identity = secrets.token_hex(12)
            directory = self.install_root / identity
            backend = action['backend']
            command = [*launcher(), 'install', '--' + backend, '--no-launcher']
            fields = {'backend': backend}
            if backend == 'docker':
                command += ['--image', action['image']]
                fields['docker_image'] = action['image']
            else:
                command += ['--prefix', str(directory / 'runtime')]
            plan = {'id': identity, 'method': backend, 'available': True, 'reason': '',
                'directory': str(directory), 'created_at': time.time(), 'check_id': self.check['id'],
                'fields': fields, 'options': [], 'steps': [{'label': action['label'], 'command': command}],
                'note': action['reason'] + ' Installation starts only after your confirmation. Existing environments are retained.'}
            self.plans = {identity: plan}
            return copy.deepcopy(plan)

    def start(self, data):
        with self.lock:
            if not isinstance(data, dict) or data.get('confirmed') is not True:
                raise OncoTracerError('Confirm the displayed installation plan before installing tools.')
            plan_id = data.get('plan_id')
            if not isinstance(plan_id, str):
                raise OncoTracerError('Choose a current installation plan.')
            plan = self.plans.get(plan_id)
            if not plan or time.time() - plan['created_at'] > 900 or plan['check_id'] != self.check.get('id'):
                raise OncoTracerError('Installation plan expired. Check dependencies and review a new plan.')
            return super().start(data)


def terminal_setup(args):
    """Run once before terminal questions; noninteractive configuration stays read-only."""
    if getattr(args, '_dependency_setup_done', False) or getattr(args, 'non_interactive', False):
        return
    args._dependency_setup_done = True
    service = DependencySetup(Path.cwd())
    print(f'OncoTracer {__version__}: checking installed dependencies before setup.', flush=True)
    service.inspect({key: getattr(args, key, None) for key in ('backend', 'image')})
    last = None
    while service.check_status()['status'] == 'checking':
        stage = service.check_status()['stage']
        if stage != last:
            print('  ' + stage, flush=True)
            last = stage
        time.sleep(0.2)
    check = service.check_status()
    if check['status'] != 'complete':
        print('Dependency check failed: ' + check.get('error', 'unknown error'))
        return
    report = check['report']
    args.backend = report['selection']['backend']
    if args.backend == 'docker':
        args.image = report['selection']['image']
    print('  Ready.' if report['ready'] else '  Some analysis dependencies need attention.')
    for item in report['environments']:
        print(f"  {item['name']}: {item['status']}")
    for warning in report['warnings']:
        print('  ' + warning)
    for action in report['actions']:
        plan = service.plan({'check_id': check['id'], 'action_id': action['id']})
        print('\n' + action['label'] + '\n' + plan['note'])
        if plan['fields'].get('docker_image'):
            print('Docker image: ' + plan['fields']['docker_image'])
        print('Installation log folder: ' + plan['directory'])
        try:
            answer = input('Install and verify these tools now? [y/N]: ').strip().lower()
        except EOFError as error:
            raise OncoTracerError('Input ended before installation approval. No installation was started.') from error
        if answer not in ('y', 'yes'):
            continue
        try:
            job = service.start({'plan_id': plan['id'], 'confirmed': True})
            last = None
            while job['status'] in ('running', 'stopping'):
                if job['stage'] != last:
                    print('  ' + job['stage'] + '\n  Log: ' + job['log_path'], flush=True)
                    last = job['stage']
                time.sleep(0.2)
                job = service.status()
        except KeyboardInterrupt:
            service.close()
            raise
        if job['status'] != 'complete':
            raise OncoTracerError(job.get('error', 'Installation did not complete.') + ' Log: ' + job['log_path'])
        args.backend = job['fields']['backend']
        args.image = job['fields'].get('docker_image')
        print('  Installation and verification completed. Checking the updated selection…', flush=True)
        updated = inspect_dependencies({'backend': args.backend, 'image': args.image})
        if not updated['ready']:
            raise OncoTracerError('Installation completed, but the final dependency check failed. Run oncotracer doctor --json and inspect ' + job['log_path'])
        print('  Updated analysis tools verified.')
        break
