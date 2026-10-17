"""Reuse completed GLIMPSE tasks when only download/workflow plumbing changes.

Analysis code and runtime settings must match; task inputs, indexes, exit status,
source integrity and timestamps are checked. This reads immutable work folders
and never rewrites old code snapshots or the original Nextflow cache.
"""
import hashlib
import json
import shlex
import shutil
from pathlib import Path

from functools import lru_cache

from continuation import metadata
from llm_reporting import KEYS as REPORT_KEYS
from glimpse_download import INTEGRITY_VERSION, PANEL_EXPECTATIONS


def analysis_config(config):
    # These new downstream-only settings cannot alter GLIMPSE reference/phase/ligation.
    downstream={'traits_panel','gwas_catalog','gwas','trait_mode','trait_min_depth','trait_min_gq','imputed_clinvar','polygenic','pgs_manifest','pgs_cache','pgs_ids','pgs_source_files','pgs_min_coverage','pgs_min_weight_coverage','explain_findings','explanation_evidence','literature_cache','literature_release','literature_offline','confidence_gwas','ancestry_explorer','ancestry_explorer_manifest','ancestry_explorer_cache'}
    data={k:v for k,v in config.items() if k not in downstream | REPORT_KEYS | {'pipeline_source_sha256','root','output','threads','memory','memory_gb'}}
    data['resources']=[r for r in config.get('resources',[]) if r['path'] not in {config.get('traits_panel'),config.get('gwas_catalog'),config.get('pgs_manifest'),config.get('explanation_evidence'),config.get('ancestry_explorer_manifest')}]
    return data


def completed_files(work, names):
    end = work/'.exitcode'
    if not end.is_file() or end.read_text().strip() != '0':
        return False
    finish = end.stat().st_mtime_ns
    return all((work/name).is_file() and (work/name).stat().st_size > 0
               and (work/name).stat().st_mtime_ns <= finish for name in names)


def unchanged_before(path, timestamp):
    path = Path(path)
    return path.is_file() and path.stat().st_size > 0 and path.stat().st_mtime_ns <= timestamp


def executable(config, name):
    if name in config.get('tools', {}):
        return Path(config['tools'][name])
    folders = ([Path(config['glimpse_bin'])] if config.get('glimpse_bin') else [])
    folders.append(Path(config['resource_dir'])/'glimpse2/bin')
    located = shutil.which(name)
    choices = [p/name for p in folders] + ([Path(located)] if located else [])
    for path in choices:
        for candidate in (path, Path(str(path)+'_static')):
            if candidate.is_file():
                return candidate
    raise ValueError(f'Missing {name}')


@lru_cache(maxsize=32)
def file_digest(path, size, mtime):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def usable_tool(config, name, begin, finish):
    path = executable(config, name)
    if unchanged_before(path, begin):
        return True
    # The first task can install a pinned GLIMPSE binary while it is running.
    if path.parent != Path(config['resource_dir'])/'glimpse2/bin' or not unchanged_before(path, finish):
        return False
    receipt = json.loads(Path(str(path)+'.source.json').read_text())
    expected_url = f'https://github.com/odelaneau/GLIMPSE/releases/download/v2.0.0/{name}_static'
    stat = path.stat()
    return (receipt.get('url') == expected_url and receipt.get('bytes') == stat.st_size
            and receipt.get('sha256') == file_digest(str(path), stat.st_size, stat.st_mtime_ns))


def task_description(work, config, code):
    args = shlex.split((work/'.command.sh').read_text(), comments=True)
    if len(args) < 4 or Path(args[1]).name != 'glimpse2.py' or args[2] not in {'reference', 'phase', 'ligate'}:
        return None
    stage = args[2]
    flags = args[3:]
    options = {}
    while flags:
        flag = flags.pop(0)
        if not flag.startswith('--') or flag in options:
            return None
        values = []
        while flags and not flags[0].startswith('--'):
            values.append(flags.pop(0))
        if not values or (flag != '--inputs' and len(values) != 1):
            return None
        options[flag] = values if flag == '--inputs' else values[0]
    expected = {'--config', '--chrom', '--threads'}
    if stage == 'phase':
        expected |= {'--chunk', '--input-region', '--output-region', '--reference', '--map', '--bam'}
    elif stage == 'ligate':
        expected.add('--inputs')
    if set(options) != expected or options['--chrom'] not in config['glimpse_chromosomes']:
        return None
    old_code = work/args[1]
    if old_code.read_bytes() != code:
        return None
    runtime_path = work/options['--config']
    raw = runtime_path.read_bytes()
    if runtime_path.stem != hashlib.sha256(raw).hexdigest():
        return None
    previous = json.loads(raw)
    if analysis_config(previous) != analysis_config(config):
        return None
    # Global scheduler budgets may change while a completed task is retained.
    # GLIMPSE itself keeps the same thread count; do not silently mix stochastic
    # phase runs when a smaller budget changes their actual execution threads.
    expected_threads = 1 if stage == 'reference' else min(4, int(config.get('threads', 4)))
    if int(options['--threads']) != expected_threads:
        return None
    begin = (work/'.command.begin').stat().st_mtime_ns
    # Native staging does not record every external index/tool; reject anything
    # newer than this task rather than assume an unrecorded input is unchanged.
    paths = [config['reference']+'.fai', executable(config, 'bcftools')]
    if not completed_files(work, []):
        return None
    finish = (work/'.exitcode').stat().st_mtime_ns
    names = {'reference': ['GLIMPSE2_chunk'], 'phase': ['GLIMPSE2_split_reference', 'GLIMPSE2_phase'],
             'ligate': ['GLIMPSE2_ligate']}[stage]
    if (not all(unchanged_before(p, begin) for p in paths)
            or not all(usable_tool(config, n, begin, finish) for n in names)):
        return None
    return stage, options, begin


def chunk_regions(path):
    chunks = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[0] in chunks:
            raise ValueError('Invalid or duplicate GLIMPSE chunk')
        chunks[fields[0]] = fields[2:4]
    if not chunks:
        raise ValueError('Empty chunk table')
    return chunks


def reference_outputs(work, chrom, config, begin):
    names = [f'chunks.{chrom}.tsv', f'reference.{chrom}.bcf', f'reference.{chrom}.bcf.csi', f'map.{chrom}.txt']
    provenance = f'reference.{chrom}.provenance.json'
    if not completed_files(work, names+[provenance]):
        return None
    proof = json.loads((work/provenance).read_text())
    integrity = proof.get('panel_integrity', {})
    if (proof.get('reference_fasta') != config['reference']
            or integrity.get('integrity_version') != INTEGRITY_VERSION
            or integrity.get('gzip_crc_validated') is not True):
        return None
    if proof.get('panel_url'):
        expected = PANEL_EXPECTATIONS['chr'+chrom.removeprefix('chr')]
        if integrity.get('md5') != expected['md5'] or integrity.get('bytes') != expected['bytes']:
            return None
    # Panels/maps may have been downloaded during this task. Any later change
    # requires reference preparation again; the validated local BCF stays intact.
    finish = (work/'.exitcode').stat().st_mtime_ns
    if not all(unchanged_before(proof[k], finish) for k in ['panel', 'genetic_map']):
        return None
    chunk_regions(work/names[0])
    return [str(work/name) for name in names]


def collect(config, root, output, core_reused, work_dir=None, *, reference_reused=None):
    # Only reference sets from collect_shared_references may seed this mapping.
    # Phase reuse still requires this sample, identical staged BAM/index/reference
    # files, unchanged analysis code, completed task receipts and input metadata.
    result = {'references': {}, 'phases': {}, 'ligated': {}}
    if not config.get('glimpse2'):
        return result
    # Input fingerprints were captured by preflight; recheck for read-only audit
    # callers too. A modified FASTA/read file invalidates all inherited tasks.
    for record in config.get('inputs', []) + config.get('resources', []):
        try:
            if metadata(record['path']) != record:
                return result
        except OSError:
            return result
    result['references'].update(reference_reused or {})
    work_root = Path(work_dir or Path(output)/'work').resolve()
    code = (Path(root)/'bin/glimpse2.py').read_bytes()
    candidates = []
    for script in sorted(work_root.glob('*/*/.command.sh')):
        work = script.parent
        try:
            description = task_description(work, config, code)
            if description:
                candidates.append((work, *description))
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    for work, stage, options, begin in candidates:
        if stage != 'reference':
            continue
        try:
            chrom = options['--chrom']
            files = reference_outputs(work, chrom, config, begin)
            if files:
                result['references'][chrom] = files
        except (OSError, ValueError, KeyError, TypeError):
            continue
    merged = core_reused.get('merge:'+config['sample'])
    if not merged:
        return result
    desired_bam, desired_bai = [Path(p).resolve() for p in merged[:2]]
    for work, stage, options, begin in candidates:
        if stage != 'phase':
            continue
        try:
            chrom, chunk = options['--chrom'], options['--chunk']
            reference = result['references'].get(chrom)
            if not reference:
                continue
            regions = chunk_regions(reference[0]).get(chunk)
            if regions != [options['--input-region'], options['--output-region']]:
                continue
            inputs = [(work/options['--reference'], Path(reference[1])),
                      (work/(options['--reference']+'.csi'), Path(reference[2])),
                      (work/options['--map'], Path(reference[3])),
                      (work/options['--bam'], desired_bam),
                      (work/(options['--bam']+'.bai'), desired_bai)]
            if any(p.resolve() != wanted.resolve() or not unchanged_before(p, begin) for p, wanted in inputs):
                continue
            names = [f'{config["sample"]}.{chrom}.{chunk}.bcf', f'{config["sample"]}.{chrom}.{chunk}.bcf.csi']
            if completed_files(work, names):
                result['phases'][f'{chrom}:{chunk}'] = [str(work/name) for name in names]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for work, stage, options, begin in candidates:
        if stage != 'ligate':
            continue
        try:
            chrom = options['--chrom']
            reference = result['references'].get(chrom)
            if not reference:
                continue
            chunks = chunk_regions(reference[0])
            phases = [result['phases'].get(f'{chrom}:{chunk}') for chunk in sorted(chunks, key=int)]
            if not all(phases):
                continue
            inputs = options['--inputs']
            desired = {Path(pair[0]).resolve(): Path(pair[1]).resolve() for pair in phases}
            staged = [(work/name).resolve() for name in inputs]
            # Require every expected chunk exactly once, with its validated index.
            if len(staged) != len(desired) or set(staged) != set(desired):
                continue
            if any((work/(name+'.csi')).resolve() != desired[source]
                   or not unchanged_before(source, begin)
                   or not unchanged_before(work/(name+'.csi'), begin)
                   for name, source in zip(inputs, staged)):
                continue
            names = [f'{config["sample"]}.{chrom}.imputed.bcf', f'{config["sample"]}.{chrom}.imputed.bcf.csi']
            if not completed_files(work, names+['chunks.list']):
                continue
            listed = [(work/name).resolve() for name in (work/'chunks.list').read_text().splitlines()]
            if listed != [Path(pair[0]).resolve() for pair in phases]:
                continue
            result['ligated'][chrom] = [str(work/name) for name in names]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
