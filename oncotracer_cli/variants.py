"""Native, optional small-variant calling with caller-specific evidence retained.

Fresh/FFPE describes specimen handling, independently of sequencing platform.
Tumor-only calls and sparse germline-style calls are research evidence; this
module does not manufacture consensus genotypes or population frequencies.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.resources
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .runtime import OncoTracerError, atomic_write_json, require_file, sha256_file, utc_now

SUPPORTED_CALLERS = {'illumina': ('mutect2', 'freebayes', 'bcftools'), 'ont': ('clair3', 'clairs_to')}
DEFAULT_CALLERS = {'illumina': ('mutect2',), 'ont': ('clair3',)}
SOMATIC_CALLERS = frozenset({'mutect2', 'clairs_to'})
SCHEMA = 'oncotracer-native-variants-v1'


@dataclass(frozen=True)
class VariantRequest:
    mode: str
    specimen_type: str
    callers: tuple[str, ...]
    targets_bed: Path | None = None
    annovar: str = 'auto'
    annovar_dir: Path | None = None
    annovar_db: Path | None = None
    clair3_model: Path | None = None
    clairsto_platform: str = ''
    tool_prefix: Path | None = None
    min_mapping_quality: int = 20
    min_base_quality: int = 20
    min_alt_count: int = 2
    min_alt_fraction: float = 0.05
    reference_build: str = 'hg38'
    clairsto_sif: Path | None = None
    ffperase: str = 'off'
    ffperase_root: Path | None = None
    ffperase_models: Path | None = None
    ffperase_sif: Path | None = None
    ffperase_prefix: Path | None = None
    varlociraptor: str = 'off'
    varlociraptor_fdr: float = 0.05
    varlociraptor_scenario: Path | None = None
    varlociraptor_events: tuple[str, ...] = ('PRESENT',)
    varlociraptor_sample: str = 'sample'

    def as_dict(self):
        return {k: str(v) if isinstance(v, Path) else list(v) if isinstance(v, tuple) else v
                for k, v in asdict(self).items()}


def resolve_variant_request(config: Mapping[str, object], *, mode: str) -> VariantRequest | None:
    from .docker_runtime import container_variant_config
    config = container_variant_config(config)
    enabled = config.get('run_variants', False)
    if str(enabled).lower() not in {'true', '1', 'yes', 'on'}:
        if str(enabled).lower() not in {'false', '0', 'no', 'off', '', 'none'}:
            raise OncoTracerError('run_variants must be true or false')
        return None
    if mode not in SUPPORTED_CALLERS:
        raise OncoTracerError('Variant platform must be illumina or ont')
    specimen = str(config.get('variant_specimen_type') or '').strip().lower()
    if specimen not in {'fresh', 'ffpe'}:
        raise OncoTracerError('Select variant_specimen_type: fresh or ffpe')
    raw = config.get('variant_callers') or DEFAULT_CALLERS[mode]
    callers = tuple(x.strip().lower() for x in raw.split(',')) if isinstance(raw, str) else tuple(raw)
    if not callers or len(set(callers)) != len(callers) or any(c not in SUPPORTED_CALLERS[mode] for c in callers):
        raise OncoTracerError(f'Supported {mode} variant callers: {", ".join(SUPPORTED_CALLERS[mode])}; select each at most once')
    def path(key):
        v = config.get(key)
        return Path(str(v)).expanduser().resolve() if v else None
    annotation = str(config.get('variant_annovar') or 'auto').lower()
    if annotation not in {'auto', 'off'}:
        raise OncoTracerError('variant_annovar must be auto or off')
    model = path('variant_clair3_model')
    if 'clair3' in callers and (model is None or not model.is_dir() or not any(model.iterdir())):
        raise OncoTracerError('Clair3 requires an existing, nonempty variant_clair3_model directory matching the basecaller/chemistry')
    platform = str(config.get('variant_clairsto_platform') or '').strip()
    if 'clairs_to' in callers and not re.fullmatch(r'ont_[A-Za-z0-9_]+', platform):
        raise OncoTracerError('ClairS-TO requires variant_clairsto_platform matching the ONT basecaller/chemistry')
    targets = path('variant_targets_bed')
    if targets:
        require_file(targets, 'Variant target BED')
    prefix = path('variant_tool_prefix') or (Path(os.environ['ONCOTRACER_VARIANTS_PREFIX']).resolve() if os.environ.get('ONCOTRACER_VARIANTS_PREFIX') else None)
    if prefix and not config.get('_docker_skip_host_tools') and not (prefix / 'bin').is_dir():
        raise OncoTracerError('variant_tool_prefix must contain a bin directory')
    try:
        mq = int(config.get('variant_min_mapping_quality', 20))
        bq = int(config.get('variant_min_base_quality', 20))
        count = int(config.get('variant_min_alt_count', 2))
        fraction = float(config.get('variant_min_alt_fraction', .05))
    except (ValueError, TypeError) as error:
        raise OncoTracerError('Variant quality/allele thresholds must be numeric') from error
    if min(mq, bq) < 0 or count < 1 or not 0 < fraction <= 1:
        raise OncoTracerError('Variant quality thresholds must be nonnegative; alt count >=1 and fraction in (0,1]')
    build = str(config.get('variant_reference_build') or 'hg38')
    if build not in {'hg19','hg38'}:
        raise OncoTracerError('variant_reference_build must be hg19 or hg38')
    clairsto_sif = path('variant_clairsto_sif')
    if clairsto_sif:
        if 'clairs_to' not in callers:
            raise OncoTracerError('variant_clairsto_sif requires selecting the clairs_to caller')
        require_file(clairsto_sif, 'ClairS-TO SIF image')
    ffpe_mode = str(config.get('variant_ffperase') or ('required' if mode == 'illumina' and specimen == 'ffpe' else 'off')).lower()
    vl_mode = str(config.get('variant_varlociraptor') or 'off').lower()
    if ffpe_mode not in {'required','off'} or vl_mode not in {'required','off'}:
        raise OncoTracerError('variant_ffperase and variant_varlociraptor must be required or off')
    if ffpe_mode != 'off' and (mode != 'illumina' or specimen != 'ffpe'):
        raise OncoTracerError('FFPErase is supported only for Illumina FFPE specimens')
    try:
        fdr = float(config.get('variant_varlociraptor_fdr', 0.05))
    except (TypeError, ValueError) as exc:
        raise OncoTracerError('Varlociraptor FDR must be numeric') from exc
    if not math.isfinite(fdr) or not 0 < fdr < 1:
        raise OncoTracerError('Varlociraptor FDR must be between 0 and 1')
    scenario = path('variant_varlociraptor_scenario')
    if scenario:
        require_file(scenario, 'Varlociraptor scenario')
    events = tuple(str(config.get('variant_varlociraptor_events') or 'PRESENT').split(','))
    vl_sample = str(config.get('variant_varlociraptor_sample') or 'sample')
    if not events or any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', e) for e in events) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', vl_sample):
        raise OncoTracerError('Use comma-separated uppercase Varlociraptor event names and a safe scenario sample name')
    if not scenario and (events != ('PRESENT',) or vl_sample != 'sample'):
        raise OncoTracerError('Nondefault Varlociraptor events/sample require an explicit scenario')
    return VariantRequest(mode, specimen, callers, targets, annotation, path('variant_annovar_dir'),
                          path('variant_annovar_db'), model, platform, prefix, mq, bq, count, fraction, build, clairsto_sif,
                          ffpe_mode, path('variant_ffperase_root'), path('variant_ffperase_models'),
                          path('variant_ffperase_sif'), path('variant_ffperase_prefix'),
                          vl_mode, fdr, scenario, events, vl_sample)


def variant_plan(request: VariantRequest) -> dict:
    return {**request.as_dict(), 'engine': 'native', 'nextflow_used': False,
            'stages': ['variant-bam-validation', *[f'variant-{c}' for c in request.callers],
                       'variant-normalization', *(['ffperase-assessment'] if request.ffperase != 'off' else []), *(['varlociraptor-local-fdr'] if request.varlociraptor != 'off' else []), 'variant-evidence', 'annovar-autodetect' if request.annovar == 'auto' else 'annotation-off'],
            'ffpe_handling': (('Mutect2 orientation model; ' if 'mutect2' in request.callers else '') + 'C>T/G>A review flag without automatic exclusion' + ('; independent FFPErase artifact assessment required' if request.ffperase != 'off' else '')) if request.specimen_type == 'ffpe' else 'caller-native filtering',
            'genotypes': 'caller GT preserved; missing remains missing; no AF-derived consensus',
            'call_semantics': {c: 'tumor_only_candidates' if c in SOMATIC_CALLERS else 'germline_style_independent_calls' for c in request.callers}}


def preflight_variant_tools(request: VariantRequest, toolchain=None) -> dict[str, str]:
    def find(name, alternatives=()):
        for candidate in (name, *alternatives):
            if request.tool_prefix:
                p = request.tool_prefix / 'bin' / candidate
                if p.is_file() and os.access(p, os.X_OK):
                    return str(p)
            else:
                if candidate in {'samtools', 'bcftools'} and toolchain is not None:
                    try:
                        return toolchain.executable('core', candidate)
                    except OncoTracerError:
                        if getattr(toolchain, 'core_prefix', None) is not None:
                            raise
                found = shutil.which(candidate)
                if found:
                    return str(Path(found).resolve())
        if os.environ.get('ONCOTRACER_CONTAINER_RUNTIME') == 'docker' and name in {'run_clair3.sh', 'run_clairs_to'}:
            wrapper = Path('/usr/local/bin') / name
            if wrapper.is_file() and os.access(wrapper, os.X_OK):
                return str(wrapper)
        raise OncoTracerError(f'Variant tool {name} is unavailable; install it or configure variant_tool_prefix before running')
    result = {name: find(name) for name in ('samtools', 'bcftools')}
    for caller in request.callers:
        if caller == 'mutect2':
            result['gatk'] = find('gatk')
        elif caller == 'freebayes':
            result['freebayes'] = find('freebayes')
        elif caller == 'clair3':
            result['clair3'] = find('run_clair3.sh', ('run_clair3.py',))
        elif caller == 'clairs_to':
            if request.clairsto_sif:
                # The host prefix supplies samtools/bcftools. The caller and its
                # model dependencies come from the explicitly selected SIF.
                runtime = shutil.which('apptainer') or shutil.which('singularity')
                if not runtime:
                    raise OncoTracerError('variant_clairsto_sif requires local Apptainer or Singularity')
                result['clairs_to_runtime'] = str(Path(runtime).resolve())
                result['clairs_to_sif'] = str(require_file(request.clairsto_sif, 'ClairS-TO SIF image'))
            else:
                result['clairs_to'] = find('run_clairs_to')
    if request.ffperase != 'off':
        result.setdefault('gatk', find('gatk'))
    if request.varlociraptor != 'off':
        from .varlociraptor import discover
        result['varlociraptor'] = discover(request.tool_prefix)
    return result


def _module_digest(name='variants.py') -> str:
    # Resource readers also work when OncoTracer runs directly from its zipapp.
    return hashlib.sha256(importlib.resources.files(__package__).joinpath(name).read_bytes()).hexdigest()


def _identity(path: Path):
    stat = path.stat()
    return {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _read_vcf(path: Path):
    opener = gzip.open if path.name.endswith('.gz') else open
    header_seen = False
    with opener(path, 'rt') as f:
        for line in f:
            if line.startswith('#CHROM\t'):
                fields = line.rstrip('\r\n').split('\t')
                if len(fields) != 10:
                    raise OncoTracerError(f'Expected a single-sample VCF: {path}')
                header_seen = True
            if line.startswith('#'):
                yield line, None
                continue
            if not line.strip():
                continue
            fields = line.rstrip('\r\n').split('\t')
            if not header_seen or len(fields) != 10 or not fields[1].isdigit():
                raise OncoTracerError(f'Malformed variant record: {path}')
            yield line, fields
    if not header_seen:
        raise OncoTracerError(f'VCF lacks #CHROM header: {path}')


def validate_vcf(path: Path, *, expected_samples=None) -> int:
    try:
        count = 0
        for line, fields in _read_vcf(path):
            if line.startswith('#CHROM\t') and expected_samples is not None:
                observed = line.rstrip('\r\n').split('\t')[9]
                if observed not in expected_samples:
                    raise OncoTracerError(f'VCF sample {observed!r} does not match the accepted BAM/caller sample identity')
            count += fields is not None
        return count
    except (OSError, EOFError, UnicodeError) as error:
        raise OncoTracerError(f'Cannot decode complete VCF {path}: {error}') from error


def _genotype_digest(path: Path) -> str:
    """ANNOVAR may add INFO fields, but must preserve alleles and sample evidence."""
    digest = hashlib.sha256()
    for _, fields in _read_vcf(path):
        if fields is not None:
            digest.update(json.dumps(fields[:7] + fields[8:], separators=(',', ':')).encode())
            digest.update(b'\n')
    return digest.hexdigest()


def _owned_outputs(destination: Path, previous) -> dict:
    if previous is None:
        if destination.exists() and any(destination.iterdir()):
            raise OncoTracerError(f'Refusing an unowned caller output directory: {destination}')
        return {}
    if previous.get('schema') != SCHEMA or not isinstance(previous.get('outputs'), dict):
        raise OncoTracerError('Invalid variant completion manifest')
    owned = previous['outputs']
    for name, digest in owned.items():
        if Path(name).name != name or name in {'.', '..', 'complete.json'}:
            raise OncoTracerError('Invalid owned variant artifact name')
        target = destination / name
        if target.is_symlink() or (target.exists() and (not target.is_file() or sha256_file(target) != digest)):
            raise OncoTracerError(f'Refusing to overwrite an edited variant output: {target}')
    return owned


def _publish(destination: Path, artifacts, previous, signature: str, final_name: str, result):
    """Replace owned artifacts together, restoring the previous generation on failure."""
    owned = _owned_outputs(destination, previous)
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        target = destination / artifact.name
        if target.is_symlink() or (target.exists() and artifact.name not in owned):
            raise OncoTracerError(f'Refusing to overwrite an unowned variant output: {target}')
    completion = destination / 'complete.json'
    if completion.is_symlink():
        raise OncoTracerError('Variant completion manifest must not be a symlink')
    hashes = {p.name: sha256_file(p) for p in artifacts}
    with tempfile.TemporaryDirectory(prefix='.previous-', dir=destination.parent) as temp:
        backup = Path(temp)
        saved, installed = [], []
        try:
            for name in [*owned, 'complete.json']:
                target = destination / name
                if target.exists():
                    os.replace(target, backup / name)
                    saved.append(name)
            for artifact in artifacts:
                os.replace(artifact, destination / artifact.name)
                installed.append(artifact.name)
            atomic_write_json(completion, {'schema': SCHEMA, 'signature': signature, 'vcf': final_name,
                                         'result': result.copy(), 'outputs': hashes})
        except BaseException:
            for name in installed:
                (destination / name).unlink(missing_ok=True)
            for name in saved:
                os.replace(backup / name, destination / name)
            raise


def _evidence(vcf: Path, text_vcf: Path, table: Path, request: VariantRequest, sample: str, caller: str):
    """Add an INFO review flag without changing FILTER or FORMAT/genotypes."""
    count = 0
    with text_vcf.open('w') as out, table.open('w', newline='') as evidence:
        fields_out = ['sample', 'caller', 'call_semantics', 'chrom', 'pos', 'ref', 'alt', 'qual', 'filter',
                      'GT', 'DP', 'AD', 'AF', 'GQ', 'PL', 'GL', 'ffpe_deamination_review']
        writer = csv.DictWriter(evidence, fields_out, delimiter='\t')
        writer.writeheader()
        for line, fields in _read_vcf(vcf):
            if fields is None:
                if line.startswith('#CHROM') and request.specimen_type == 'ffpe':
                    out.write('##INFO=<ID=OC_FFPE_DEAMINATION,Number=0,Type=Flag,Description="C-to-T or G-to-A change in an FFPE specimen; review flag only, not proof of artifact">\n')
                out.write(line)
                continue
            count += 1
            review = request.specimen_type == 'ffpe' and (fields[3], fields[4]) in {('C', 'T'), ('G', 'A')}
            if review:
                fields[7] = (fields[7] + ';' if fields[7] not in {'', '.'} else '') + 'OC_FFPE_DEAMINATION'
            out.write('\t'.join(fields) + '\n')
            fmt = dict(zip(fields[8].split(':'), fields[9].split(':')))
            row = dict(zip(['chrom', 'pos', 'ref', 'alt', 'qual', 'filter'], [fields[i] for i in (0,1,3,4,5,6)]))
            row.update(sample=sample, caller=caller, call_semantics='tumor_only_candidates' if caller in SOMATIC_CALLERS else 'germline_style_independent_calls', ffpe_deamination_review=str(review).lower())
            row.update({key: fmt.get(key, '.') for key in ('GT','DP','AD','AF','GQ','PL','GL')})
            writer.writerow(row)
    return count


def _validate_bed(path: Path, lengths: dict[str, int], destination: Path):
    intervals = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith(('#', 'track ', 'browser ')):
            continue
        fields = line.split()
        try:
            chrom, start, end = fields[0], int(fields[1]), int(fields[2])
        except (IndexError, ValueError) as error:
            raise OncoTracerError(f'Invalid target BED row {n}') from error
        if chrom not in lengths or start < 0 or end <= start or end > lengths[chrom]:
            raise OncoTracerError(f'BED row {n} is incompatible with the reference')
        intervals.append((chrom,start,end))
    if not intervals:
        raise OncoTracerError('Target BED contains no intervals')
    merged = []
    for chrom,start,end in sorted(intervals):
        if merged and chrom == merged[-1][0] and start <= merged[-1][2]:
            merged[-1] = (chrom,merged[-1][1],max(end,merged[-1][2]))
        else:
            merged.append((chrom,start,end))
    destination.write_text(''.join(f'{c}\t{s}\t{e}\n' for c,s,e in merged))


def _clairsto_command_prefix(request, tools, directory: Path, bam: Path, ref: Path):
    """Keep caller dependencies inside the SIF and bind only required input roots.

    Private reference/BAM paths may be symlinks, so their resolved parent paths
    must also be readable in the container. The private work tree is writable;
    the original input directories are mounted read-only.
    """
    if not request.clairsto_sif:
        return [tools['clairs_to']]
    work = directory.parent.resolve()
    readonly = sorted({Path(bam).resolve().parent, Path(ref).resolve().parent} - {work}, key=str)
    mounts = [(p, 'ro') for p in readonly] + [(work, 'rw')]
    command = [tools['clairs_to_runtime'], 'exec', '--cleanenv', '--env', 'CUDA_VISIBLE_DEVICES=']
    for path, access in mounts:
        if any(char in str(path) for char in (',', ':', '\n', '\r')):
            raise OncoTracerError('Container bind paths cannot contain comma, colon, or newline')
        command += ['--bind', f'{path}:{path}:{access}']
    return [*command, tools['clairs_to_sif'], '/opt/bin/run_clairs_to']


def _call(request, caller, sample, bam, ref, directory, tools, runner, threads, env, targets):
    def run(label, argv, **kwargs):
        with (directory / f'{label}.stderr.log').open('w') as err:
            runner.run(f'variant-{sample}-{caller}-{label}', argv, env=env, stderr=err, **kwargs)
    raw = directory / 'raw.vcf.gz'
    if caller == 'bcftools':
        bcf = directory / 'pileup.bcf'
        cmd = [tools['bcftools'], 'mpileup', '-f', ref, '-q', str(request.min_mapping_quality), '-Q', str(request.min_base_quality),
               '-a', 'FORMAT/DP,FORMAT/AD', '-Ob', '-o', bcf]
        if targets:
            cmd += ['-R', targets]
        run('pileup', [*cmd, bam])
        run('call', [tools['bcftools'], 'call', '-m', '-v', '-Oz', '-o', raw, bcf])
    elif caller == 'freebayes':
        text = directory / 'raw.vcf'
        cmd = [tools['freebayes'], '-f', ref, '-m', str(request.min_mapping_quality), '-q', str(request.min_base_quality),
               '--min-alternate-count', str(request.min_alt_count), '--min-alternate-fraction', str(request.min_alt_fraction)]
        if targets:
            cmd += ['-t', targets]
        with text.open('w') as out:
            run('call', [*cmd, bam], stdout=out)
        raw = text
    elif caller == 'mutect2':
        unfiltered = directory / 'unfiltered.vcf.gz'
        f1r2, priors = directory/'f1r2.tar.gz', directory/'orientation-priors.tar.gz'
        cmd = [tools['gatk'], 'Mutect2', '-R', ref, '-I', bam, '-O', unfiltered,
               '--native-pair-hmm-threads', str(threads), '--f1r2-tar-gz', f1r2,
               '--minimum-mapping-quality', str(request.min_mapping_quality), '--min-base-quality-score', str(request.min_base_quality)]
        if targets:
            cmd += ['-L', targets]
        run('call', cmd)
        run('orientation', [tools['gatk'], 'LearnReadOrientationModel', '-I', f1r2, '-O', priors])
        run('filter', [tools['gatk'], 'FilterMutectCalls', '-R', ref, '-V', unfiltered,
                       '--stats', str(unfiltered)+'.stats', '--ob-priors', priors, '-O', raw])
    elif caller == 'clair3':
        results = directory / 'clair3'
        cmd = [tools['clair3'], f'--bam_fn={bam}', f'--ref_fn={ref}', f'--threads={threads}', '--platform=ont',
               f'--model_path={request.clair3_model}', f'--output={results}', f'--sample_name={sample}']
        if targets:
            cmd.append(f'--bed_fn={targets}')
        run('call', cmd)
        raw = results / 'merge_output.vcf.gz'
    else:
        results = directory / 'clairs_to'
        cmd = [*_clairsto_command_prefix(request, tools, directory, bam, ref),
               '--tumor_bam_fn', bam, '--ref_fn', ref, '--threads', str(threads),
               '--platform', request.clairsto_platform, '--output_dir', results, '--sample_name', sample,
               '--snv_output_prefix', 'oncotracer_snv', '--indel_output_prefix', 'oncotracer_indel', '--disable_verdict']
        if targets:
            cmd += ['--bed_fn', targets]
        # Explicit non-default prefixes avoid the sample-name suffix introduced
        # by newer ClairS-TO releases. Do not pass the host tool prefix into SIF.
        run('call', cmd)
        parts = [results / 'oncotracer_snv.vcf.gz', results / 'oncotracer_indel.vcf.gz']
        for part in parts:
            validate_vcf(part)
        run('concat', [tools['bcftools'], 'concat', '-a', '-Oz', '-o', raw, *parts])
    validate_vcf(raw)
    normalized = directory / 'normalized.vcf.gz'
    run('normalize', [tools['bcftools'], 'norm', '-f', ref, '-m', '-any', '-Oz', '-o', normalized, raw])
    annotated_text, evidence = directory/'review.vcf', directory/'evidence.tsv'
    count = _evidence(normalized, annotated_text, evidence, request, sample, caller)
    final = directory / f'{sample}.{caller}.vcf.gz'
    run('sort', [tools['bcftools'], 'sort', '-Oz', '-o', final, annotated_text])
    run('index', [tools['bcftools'], 'index', '-t', final])
    validate_vcf(final)
    return final, evidence, count


def run_variants(request: VariantRequest, bams: Mapping[str, Path], reference, outdir: Path, runner, ledger,
                 *, threads=1, force=False, sample_statuses=None, toolchain=None) -> dict:
    """Run selected callers independently. Persist per-sample failures and results."""
    if threads < 1 or not bams:
        raise OncoTracerError('Variants require BAM inputs and positive threads')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', s) for s in bams):
        raise OncoTracerError('Unsafe variant sample identifier')
    tools = preflight_variant_tools(request, toolchain)
    fasta = require_file(Path(reference['fasta'] if isinstance(reference, Mapping) else reference), 'Variant reference FASTA')
    fai = Path(str(fasta)+'.fai')
    root = Path(outdir) / '08_variants'
    if root.is_symlink():
        raise OncoTracerError('Variant output directory must not be a symlink')
    marker = root / 'owner.json'
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise OncoTracerError('Existing variant output directory is not owned by OncoTracer')
    root.mkdir(parents=True, exist_ok=True)
    if marker.is_file() and json.loads(marker.read_text()).get('schema') != SCHEMA:
        raise OncoTracerError('Variant output ownership mismatch')
    atomic_write_json(marker, {'schema':SCHEMA})
    status = {'schema': SCHEMA, 'requested': variant_plan(request), 'started_at': utc_now(), 'samples':[], 'tools': tools,
              'overall_status':'running', 'completed_samples':[], 'failed_samples':[], 'annotation':'off'}
    atomic_write_json(root/'variant_status.json', status)
    from .annovar import discover_annovar, build_annovar_command, expected_outputs
    annotation = discover_annovar(request.reference_build, annovar_dir=request.annovar_dir, database_dir=request.annovar_db) if request.annovar == 'auto' else None
    status['annovar_detection'] = annotation.as_dict() if annotation else {'available':False,'reason':'disabled'}
    status['annotation'] = 'available' if annotation and annotation.available else 'skipped'
    env = {}
    if request.tool_prefix:
        env = {'PATH': str(request.tool_prefix/'bin')+os.pathsep+os.environ.get('PATH',''), 'CONDA_PREFIX':str(request.tool_prefix)}
    from . import ffperase, varlociraptor
    detection = ffperase.discover(request.ffperase_root, request.ffperase_models, request.ffperase_sif,
                                 request.ffperase_prefix) if request.ffperase != 'off' else None
    status['ffperase_detection'] = detection or {'status':'off'}
    for sample, source_bam in bams.items():
        sample_result = {'sample':sample, 'input_bam':str(source_bam), 'callers':[], 'status':'running'}
        status['samples'].append(sample_result)
        try:
            source_bam = require_file(Path(source_bam), 'Variant input BAM')
            with tempfile.TemporaryDirectory(prefix=f'.{sample}-', dir=root) as temp:
                work = Path(temp)
                ref = work/'reference.fa'
                ref.symlink_to(fasta.resolve())
                local_fai = Path(str(ref)+'.fai')
                if fai.is_file():
                    shutil.copy2(fai, local_fai)
                else:
                    runner.run(f'variant-{sample}-faidx', [tools['samtools'],'faidx',ref], env=env)
                lengths = {f[0]: int(f[1]) for line in local_fai.read_text().splitlines() if (f := line.split('\t')) and len(f)>1}
                if not lengths:
                    raise OncoTracerError('Reference FAI contains no contigs')
                runner.run(f'variant-{sample}-quickcheck', [tools['samtools'], 'quickcheck', '-v', source_bam], env=env)
                header = work/'header.sam'
                with header.open('w') as f:
                    runner.run(f'variant-{sample}-header', [tools['samtools'], 'view', '-H', source_bam], env=env, stdout=f)
                sm = set()
                coordinate_sorted = False
                for line in header.read_text().splitlines():
                    values = dict(x.split(':',1) for x in line.split('\t')[1:] if ':' in x)
                    if line.startswith('@HD'):
                        coordinate_sorted = values.get('SO') == 'coordinate'
                    if line.startswith('@SQ') and (values.get('SN') not in lengths or int(values.get('LN','0')) != lengths[values['SN']]):
                        raise OncoTracerError('BAM/reference contig lengths differ; no automatic contig renaming is performed')
                    if line.startswith('@RG') and values.get('SM'):
                        sm.add(values['SM'])
                if len(sm) > 1:
                    raise OncoTracerError('BAM contains multiple sample SM values')
                if not sm and request.mode == 'illumina':
                    raise OncoTracerError('Illumina variant calling requires BAM read groups with a sample SM; repair explicitly before calling')
                sample_result['bam_sample_names'] = sorted(sm)
                bam = work/f'{sample}.bam'
                if coordinate_sorted:
                    bam.symlink_to(source_bam.resolve())
                else:
                    runner.run(f'variant-{sample}-sort', [tools['samtools'],'sort','-@',str(threads),'-o',bam,source_bam], env=env)
                runner.run(f'variant-{sample}-index', [tools['samtools'],'index','-@',str(threads),bam], env=env)
                if 'mutect2' in request.callers:
                    runner.run(f'variant-{sample}-dict', [tools['gatk'],'CreateSequenceDictionary','-R',ref,'-O',work/'reference.dict'],env=env)
                targets = work/'targets.bed' if request.targets_bed else None
                if targets:
                    _validate_bed(request.targets_bed, lengths, targets)
                for caller in request.callers:
                    result = {'caller':caller, 'status':'running'}
                    sample_result['callers'].append(result)
                    if caller in SOMATIC_CALLERS and (sample_statuses or {}).get(sample) == 'normal':
                        result.update(status='not_applicable', reason='Tumor-only caller omitted for a sample explicitly marked normal')
                        continue
                    dest = root/'samples'/sample/caller
                    if any(p.is_symlink() for p in (root/'samples',root/'samples'/sample,dest)):
                        raise OncoTracerError('Variant output path contains a symlink')
                    signature_inputs = [source_bam,fasta,fai,*([request.targets_bed] if request.targets_bed else []), *map(Path,tools.values())]
                    if request.clair3_model and caller == 'clair3':
                        signature_inputs += sorted(p for p in request.clair3_model.rglob('*') if p.is_file())
                    signature = ledger.signature(f'variants-{sample}-{caller}', [json.dumps(request.as_dict(),sort_keys=True),_module_digest(),json.dumps(status['annovar_detection'],sort_keys=True),_module_digest('annovar.py'),_module_digest('ffperase.py'),_module_digest('varlociraptor.py'),_module_digest('variant_filters.py'),json.dumps(detection,sort_keys=True)], signature_inputs + ([request.varlociraptor_scenario] if request.varlociraptor_scenario else []))
                    completed = dest/'complete.json'
                    if completed.is_symlink():
                        raise OncoTracerError('Variant completion manifest must not be a symlink')
                    previous = json.loads(completed.read_text()) if completed.is_file() else None
                    _owned_outputs(dest, previous)
                    accepted_samples = {sample} if request.mode == 'ont' else sm
                    if not force and previous and previous.get('signature') == signature and previous.get('result', {}).get('status') == 'complete':
                        if all((dest/f).is_file() and sha256_file(dest/f)==digest for f,digest in previous['outputs'].items()):
                            validate_vcf(dest/previous['vcf'], expected_samples=accepted_samples)
                            result.update(previous['result'], resumed=True)
                            continue
                    scratch = work/caller
                    scratch.mkdir()
                    try:
                        final,evidence,count = _call(request,caller,sample,bam,ref,scratch,tools,runner,threads,env,targets)
                        validate_vcf(final, expected_samples=accepted_samples)
                        assessments, assessment_files = {}, []
                        if request.ffperase != 'off' or request.varlociraptor != 'off':
                            def assess_run(label, argv, **kwargs):
                                with (scratch/f'{label}.stderr.log').open('a') as err:
                                    runner.run(f'variant-{sample}-{caller}-{label}',argv,env=env,stderr=err,**kwargs)
                            from .variant_filters import evidence_identity
                            identity = evidence_identity(final)
                            original_filter = scratch/'before_assessment.vcf.gz'
                            shutil.copy2(final,original_filter)
                            assessment_files.append(original_filter)
                            current = final
                            for name, enabled in [('ffperase',request.ffperase),('varlociraptor',request.varlociraptor)]:
                                if enabled == 'off':
                                    continue
                                assessed_vcf = scratch/f'{name}.assessed.vcf'
                                if name == 'ffperase':
                                    assessed = ffperase.assess(current,assessed_vcf,detection=detection,bam=bam,reference=ref,
                                        directory=scratch,run=assess_run,tools=tools,min_mapping_quality=request.min_mapping_quality,
                                        min_base_quality=request.min_base_quality)
                                else:
                                    assessed = varlociraptor.assess(current,assessed_vcf,bam=bam,reference=ref,directory=scratch,
                                        run=assess_run,tools=tools,platform=request.mode,fdr=request.varlociraptor_fdr,
                                        scenario=request.varlociraptor_scenario,events=request.varlociraptor_events,sample_name=request.varlociraptor_sample)
                                if evidence_identity(assessed_vcf) != identity:
                                    raise OncoTracerError(f'{name} changed original alleles or genotype evidence')
                                assessments[name] = assessed
                                current = assessed_vcf
                            assess_run('assessment-compress',[tools['bcftools'],'view','-Oz','-o',final,current])
                            assess_run('assessment-index',[tools['bcftools'],'index','-t','-f',final])
                            # Rebuild the evidence table with final FILTERs, preserving original genotypes.
                            _evidence(final,scratch/'assessment.review.vcf',evidence,request,sample,caller)
                            assessment_files += [p for p in scratch.iterdir() if p.is_file() and p.name.startswith(('ffperase.','varlociraptor.'))]
                        annotation_result = {'status':'skipped','reason':status['annovar_detection'].get('reason','disabled')}
                        annotation_files = []
                        if count == 0:
                            annotation_result = {'status': 'not_applicable', 'reason': 'Successful caller emitted no variant records'}
                        if count and annotation and annotation.available:
                            prefix = scratch/'annovar'
                            try:
                                command = build_annovar_command(annotation,final,prefix)
                                with (scratch/'annovar.stdout.log').open('w') as log, (scratch/'annovar.stderr.log').open('w') as err:
                                    runner.run(f'variant-{sample}-{caller}-annovar',command,env=env,stdout=log,stderr=err)
                                annotation_files = expected_outputs(annotation,prefix)
                                for p in annotation_files:
                                    require_file(p,'ANNOVAR output')
                                annotated_vcf = next(p for p in annotation_files if p.name.endswith('.vcf'))
                                validate_vcf(annotated_vcf, expected_samples=accepted_samples)
                                if _genotype_digest(final) != _genotype_digest(annotated_vcf):
                                    raise OncoTracerError('ANNOVAR changed variant alleles or genotype evidence')
                                annotation_result = {'status':'complete','outputs':[str(dest/p.name) for p in annotation_files]}
                            except (OSError,OncoTracerError,ValueError) as error:
                                annotation_files = []
                                annotation_result = {'status':'failed','reason':str(error)}
                        result.update(status='complete' if annotation_result['status']!='failed' and not any(a['status'] == 'not_assessed' for a in assessments.values()) else 'partial_failure',
                                      assessments=assessments,
                                      vcf=str(dest/final.name), evidence=str(dest/evidence.name), variant_records=count,
                                      annotation=annotation_result, call_semantics='tumor_only_candidates' if caller in SOMATIC_CALLERS else 'germline_style_independent_calls')
                        # Preserve the original caller evidence and small orientation diagnostics.
                        raw_candidates = [scratch/'raw.vcf.gz', scratch/'raw.vcf',
                                          scratch/'clair3/merge_output.vcf.gz']
                        original = next((p for p in raw_candidates if p.is_file()), None)
                        diagnostics = []
                        if original:
                            raw_copy = scratch/('caller_raw.vcf.gz' if original.name.endswith('.gz') else 'caller_raw.vcf')
                            shutil.copy2(original, raw_copy)
                            diagnostics.append(raw_copy)
                        diagnostics += [p for p in scratch.iterdir() if p.is_file() and
                                        (p.name.startswith('unfiltered.vcf') or p.name in {'f1r2.tar.gz','orientation-priors.tar.gz'})]
                        result['bam_sample_names'] = sorted(sm)
                        result['vcf_sample_names'] = sorted(accepted_samples)
                        result['diagnostics'] = [str(dest/p.name) for p in diagnostics]
                        artifacts = [final,Path(str(final)+'.tbi'),evidence,*annotation_files,*assessment_files,*diagnostics,*scratch.glob('*.log')]
                        _publish(dest, artifacts, previous, signature, final.name, result)
                    except (OSError,OncoTracerError,ValueError) as error:
                        result.update(status='failed',error=str(error))
                        logs = root/'failed_logs'/sample/caller
                        logs.mkdir(parents=True,exist_ok=True)
                        for p in scratch.glob('*.log'):
                            shutil.copy2(p,logs/p.name)
                states = [r['status'] for r in sample_result['callers'] if r['status']!='not_applicable']
                sample_result['status'] = ('not_applicable' if not states else 'complete' if all(s=='complete' for s in states)
                                          else 'partial_failure' if any(s in {'complete','partial_failure'} for s in states) else 'failed')
        except (OSError,OncoTracerError,ValueError) as error:
            sample_result.update(status='failed',error=str(error))
        key = 'completed_samples' if sample_result['status'] in {'complete','not_applicable'} else 'failed_samples'
        status[key].append(sample)
        atomic_write_json(root/'variant_status.json',status)
    any_success = any(r.get('status') in {'complete','partial_failure'} for s in status['samples'] for r in s['callers'])
    status['overall_status'] = 'complete' if not status['failed_samples'] else 'partial_failure' if any_success else 'failed'
    status['finished_at'] = utc_now()
    atomic_write_json(root/'variant_status.json',status)
    atomic_write_json(root/'variant_provenance.json',{'schema':SCHEMA,'module_sha256':_module_digest(),
                      'request':request.as_dict(),'reference':_identity(fasta),'fai_sha256':sha256_file(fai) if fai.is_file() else None,
                      'tools':{k:_identity(Path(v)) for k,v in tools.items()},'annovar':status['annovar_detection'],'ffperase':status['ffperase_detection']})
    return status
