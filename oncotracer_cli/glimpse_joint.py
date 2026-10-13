"""Allele-exact cohort export of GLIMPSE-supported sample evidence.

This is a merge of independently evaluated genotypes, not a joint variant caller.
Missing records never imply homozygous reference. Optional full GLIMPSE BCFs can
supply explicitly called reference genotypes at the union's exact alleles.
"""
from __future__ import annotations

import hashlib
import importlib.resources
import heapq
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from collections import Counter

VERSION = 'glimpse-cohort-v1'
IMPUTED_MIN_GP = 0.90
IMPUTED_MIN_INFO = 0.80
_COMMON_INFO = {'ALLELEID', 'GENEINFO', 'MC', 'RS', 'ORIGIN', 'NEAND_MARKER',
                'ANN', 'CSQ', 'END', 'SVTYPE', 'SVLEN'}
_RECOUNT = {'AC', 'AN', 'AF', 'NS', 'N_MISSING', 'N_CALLED'}
_SAMPLE_INFO = {'EVCLASS', 'ET', 'ER', 'SRC', 'GTS', 'GGT', 'GDP', 'GAD', 'GGQ',
                'GFT', 'SGT', 'SDP', 'SAD', 'SVAF', 'SFT', 'STLOD', 'IGT', 'IGP',
                'IDS', 'IINFO', 'IST', 'SUP', 'PRI', 'CSTAT', 'CCLASS', 'CARRIER',
                'CARGT', 'CARWHY', 'INHERIT', 'NDC', 'FLAGS', 'ZYG', 'RZYG',
                'GZYG', 'SZYG', 'IZYG', 'ZYG_SRC', 'ZYG_EVID', 'AFFSTAT'}
_EXTRA_FORMATS = {
    'GT': (1, 'String', 'Allele-exact primary genotype; absent records remain missing'),
    'EVCLASS': (1, 'String', 'Per-sample origin evidence; not proof of biological origin'),
    'FT': (1, 'String', 'Per-sample evidence filter'),
    'OFT': (1, 'String', 'Original sample VCF site FILTER before cohort merging'),
    'OQUAL': (1, 'Float', 'Original sample VCF site QUAL before cohort merging'),
    'GTS': (1, 'String', 'Source of selected primary genotype'),
    'IGT': (1, 'String', 'Original imputed genotype in allele letters'),
    'IGP': (1, 'Float', 'Imputation probability of the called genotype'),
    'IDS': ('A', 'Float', 'Imputed alternate allele dosage'),
    'IINFO': (1, 'Float', 'Sample-specific imputation site quality'),
    'IST': (1, 'String', 'Imputation evidence state'),
    'CARRIER': (1, 'String', 'Sample carrier context; unknown is not non-carrier'),
    'ZYG': (1, 'String', 'Zygosity of selected primary GT'),
    'AFFSTAT': (1, 'String', 'Clinical affected status is not established from genotype'),
    'ROLE': (1, 'String', 'Supplied specimen role; unknown when not supplied'),
    'MASKREASON': (1, 'String', 'Reason primary genotype was masked for population analysis'),
}


def _common_info(tag):
    return tag not in _SAMPLE_INFO and (
        tag in _COMMON_INFO or tag.startswith(('CLN', 'ONC', 'SCI', 'ANN_'))
        or tag.endswith(('.refGene', '.ensGene', '.knownGene')))


def _run(command, commands):
    commands.append(list(map(str, command)))
    result = subprocess.run(commands[-1], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Cohort command failed ({result.returncode}): {' '.join(commands[-1])}\n{result.stderr[-8000:]}")


def _identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _implementation_digest():
    source = importlib.resources.files(__package__).joinpath('glimpse_joint.py').read_bytes()
    return hashlib.sha256(source).hexdigest()


def _canonical_header(pysam, source, lengths):
    header = pysam.VariantHeader()
    for chrom, length in lengths.items():
        header.contigs.add(chrom, length=length)
    for record in source.records:
        if record.key not in {'fileformat', 'contig'}:
            header.add_line(str(record).rstrip())
    for sample in source.samples:
        header.add_sample(sample)
    return header


def _add_definition(collection, tag, number, kind, description):
    if tag in collection:
        if (collection[tag].number, collection[tag].type) != (number, kind):
            raise ValueError(f'Incompatible VCF field schema for {tag}')
    else:
        collection.add(tag, number, kind, description)


def _header(pysam, sample_headers, reference, sample_roles, population_excluded_samples):
    out = pysam.VariantHeader()
    with pysam.FastaFile(str(reference)) as fasta:
        lengths = dict(zip(fasta.references, fasta.lengths))
        for chrom, length in lengths.items():
            out.contigs.add(chrom, length=length)
    out.add_meta('source', value=VERSION)
    out.add_meta('reference', value=str(reference))
    out.add_meta('OncoTracerCohortSemantics', value='independent-sample-merge;missing-is-unknown;allele-specific-split-records')
    metadata = {}
    maps = {}
    for sample, header in sample_headers.items():
        for chrom, contig in header.contigs.items():
            if chrom not in lengths or (contig.length is not None and contig.length != lengths[chrom]):
                raise ValueError(f'Reference dictionary mismatch for {sample}: {chrom}')
        for line in str(header).splitlines():
            if line.startswith(('##LPWGSReferenceSequenceSHA256=', '##LPWGSReferenceDictionarySHA256=', '##LPWGSClinVarSHA256=', '##LPWGSSchema=')):
                key, value = line.split('=', 1)
                if key in metadata and metadata[key] != value:
                    raise ValueError(f'Incompatible reference, annotation release or export schema: {key}')
                metadata[key] = value
        for tag, meta in header.formats.items():
            _add_definition(out.formats, tag, meta.number, meta.type, meta.description)
        maps[sample] = {}
        for tag, meta in header.info.items():
            if tag in _RECOUNT:
                continue
            if _common_info(tag):
                _add_definition(out.info, tag, meta.number, meta.type, meta.description)
            else:
                target = tag if tag in _SAMPLE_INFO or tag not in header.formats else 'SRCINFO_' + tag
                kind = 'Integer' if meta.type == 'Flag' else meta.type
                number = 1 if meta.type == 'Flag' else meta.number
                _add_definition(out.formats, target, number, kind, 'Original sample INFO: ' + meta.description)
                maps[sample][tag] = target
        out.add_sample(sample)
        out.add_meta('SAMPLE', items=[('ID', sample), ('Role', sample_roles[sample])])
        if sample in population_excluded_samples:
            out.add_meta('OncoTracerPopulationExclusion', items=[('ID', sample), ('Reason', population_excluded_samples[sample])])
    for tag, (number, kind, description) in _EXTRA_FORMATS.items():
        _add_definition(out.formats, tag, number, kind, description)
    for tag, number, kind, description in [
        ('AC', 'A', 'Integer', 'Alternate allele count among called primary genotype alleles'),
        ('AN', 1, 'Integer', 'Number of called primary genotype alleles; missing alleles excluded'),
        ('AF', 'A', 'Float', 'AC/AN among called primary alleles; not a calibrated population estimate'),
        ('NS', 1, 'Integer', 'Samples with at least one called primary genotype allele'),
        ('N_MISSING', 1, 'Integer', 'Samples without a fully called primary genotype')]:
        out.info.add(tag, number, kind, description)
    for key, value in metadata.items():
        out.add_meta(key[2:], value=value)
    return out, maps, lengths


def _record_key(record, order):
    if record.contig not in order:
        raise ValueError('Unknown reference contig: ' + record.contig)
    return order[record.contig], record.pos, record.ref, tuple(record.alts or ())


def _ordered_records(reader, order):
    """Coordinate sorted input; order same-position alleles without genome buffering."""
    previous = None
    for _, records in itertools.groupby(reader, key=lambda r: (r.contig, r.pos)):
        for record in sorted(records, key=lambda r: (r.ref, tuple(r.alts or ()))):
            key = _record_key(record, order)
            if previous is not None and key <= previous:
                raise ValueError('Duplicate or unsorted normalized allele: ' + str(key))
            previous = key
            yield key, record


def _zygosity(gt):
    if not gt or any(x is None for x in gt):
        return 'unresolved'
    if len(gt) == 1:
        return 'hemizygous_alternate' if gt[0] else 'hemizygous_reference'
    if len(set(gt)) > 1:
        return 'heterozygous'
    return 'homozygous_alternate' if gt[0] else 'homozygous_reference'


def _copy_sample(record, source, sample, info_map):
    old, new = source.samples[sample], record.samples[sample]
    for tag, value in old.items():
        new[tag] = value
    if 'GT' not in old:
        new['GT'] = (None, None)
    new.phased = old.phased
    for old_tag, new_tag in info_map.items():
        if old_tag not in source.info:
            continue
        value = source.info[old_tag]
        if source.header.info[old_tag].type == 'Flag':
            value = int(value)
        if new_tag in old and old.get(new_tag) not in (None, '.') and old[new_tag] != value:
            raise ValueError(f'Conflicting INFO/FORMAT {new_tag} in {sample} at {source.contig}:{source.pos}')
        new[new_tag] = value
    filters = list(source.filter)
    if filters:
        new['OFT'] = '|'.join(filters)
    if source.qual is not None:
        new['OQUAL'] = source.qual
    if not new.get('EVCLASS') or new.get('EVCLASS') == '.':
        new['EVCLASS'] = 'unconfirmed'
    if not new.get('FT') or new.get('FT') == '.':
        new['FT'] = 'Unconfirmed'
    new['ZYG'] = _zygosity(new.get('GT'))
    if not new.get('CARRIER') or new.get('CARRIER') == '.':
        new['CARRIER'] = 'unknown'
    # This exporter has no patient phenotype input.
    new['AFFSTAT'] = 'not_established'


def _imputed_stream(pysam, paths, sample, order, lengths):
    """GLIMPSE ligations are one chromosome per file; no whole-genome RAM cache."""
    ordered = []
    for path in paths:
        with pysam.VariantFile(str(path)) as reader:
            if list(reader.header.samples) != [sample]:
                raise ValueError(f'Imputation VCF sample mismatch: {path}')
            for chrom, contig in reader.header.contigs.items():
                if chrom not in lengths or (contig.length is not None and contig.length != lengths[chrom]):
                    raise ValueError(f'Imputation reference dictionary mismatch: {path}: {chrom}')
            first = next(reader, None)
            if first is not None:
                ordered.append((_record_key(first, order), Path(path)))
    previous = None
    for _, path in sorted(ordered):
        with pysam.VariantFile(str(path)) as reader:
            for key, record in _ordered_records(reader, order):
                if previous is not None and key <= previous:
                    raise ValueError(f'Overlapping or unsorted imputation files for {sample}')
                previous = key
                if len(record.alts or ()) != 1:
                    raise ValueError('GLIMPSE reference backfill requires biallelic imputation records')
                yield key, record


def _finite(value):
    if isinstance(value, tuple):
        value = value[0] if len(value) == 1 else None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _fill_imputed(record, source, sample):
    old = source.samples[sample]
    gt = old.get('GT')
    if not gt or len(gt) != 2 or any(x not in (0, 1) for x in gt):
        return False
    probabilities = old.get('GP')
    gp = None
    if probabilities and len(probabilities) == 3:
        probabilities = [_finite(value) for value in probabilities]
        if all(value is not None and 0 <= value <= 1 for value in probabilities) and abs(sum(probabilities) - 1) <= 0.01:
            lo, hi = sorted(gt)
            gp = probabilities[hi * (hi + 1) // 2 + lo]
    info = _finite(source.info.get('INFO')) if 'INFO' in source.header.info else None
    if info is None and 'R2' in source.header.info:
        info = _finite(source.info.get('R2'))
    if gp is None or info is None or gp < IMPUTED_MIN_GP or not IMPUTED_MIN_INFO <= info <= 1:
        return False
    new = record.samples[sample]
    new['GT'] = gt
    new.phased = old.phased
    new['EVCLASS'] = 'germline_supported'
    new['FT'] = 'PASS'
    new['GTS'] = 'imputation_exact_allele_backfill'
    new['IGT'] = ('|' if old.phased else '/').join(source.alleles[x] for x in gt)
    new['IGP'] = gp
    new['IINFO'] = info
    if 'DS' in old:
        ds = _finite(old.get('DS'))
        if ds is not None:
            new['IDS'] = (ds,)
    new['IST'] = 'reference_supported' if not any(gt) else 'alternate_supported'
    new['ZYG'] = _zygosity(gt)
    # REF confidence does not infer carrier status for unrelated conditions.
    new['CARRIER'] = 'allele_not_supported' if not any(gt) else 'unknown'
    new['AFFSTAT'] = 'not_established'
    return True


def _recount(record):
    ac = [0] * len(record.alts)
    an = ns = missing = 0
    for sample in record.samples.values():
        gt = sample.get('GT') or (None, None)
        called = [x for x in gt if x is not None]
        missing += int(len(called) < len(gt))
        ns += bool(called)
        for allele in called:
            if allele < 0 or allele > len(ac):
                raise ValueError('Invalid allele index in merged GT')
            an += 1
            if allele:
                ac[allele - 1] += 1
    record.info['AC'] = tuple(ac)
    record.info['AN'] = an
    record.info['AF'] = tuple(x / an if an else None for x in ac)
    record.info['NS'] = ns
    record.info['N_MISSING'] = missing
    return an


def merge_cohort(samples: dict[str, Path], output_dir: Path, bcftools: str,
                 reference: Path, threads: int = 1,
                 imputed_by_sample: dict[str, list[Path]] | None = None,
                 sample_roles: dict[str, str] | None = None,
                 population_excluded_samples: dict[str, str] | None = None) -> dict:
    """Create indexed all-evidence and germline population VCFs, returning a manifest.

    Output rows are normalized, exact biallelic alleles. Splitting a multiallelic
    GT uses missing for the other alternate allele, never a fabricated 0. Rows
    lacking any called germline genotype are excluded from the population VCF.
    Explicit population exclusions (for example a paired tumor when its matched
    normal is included) mask that sample only in the population output. Specimen
    role alone does not exclude a sample.
    Optional GLIMPSE calls only fill *absent sample records*, never override an
    existing unconfirmed/somatic/FFPE decision. All input files remain intact.
    """
    try:
        import pysam
    except ImportError as exc:
        raise RuntimeError('Cohort export requires pysam in the GLIMPSE pipeline Python environment') from exc
    if not samples:
        raise ValueError('At least one sample is required for cohort export')
    if not isinstance(threads, int) or threads < 1:
        raise ValueError('threads must be a positive integer')
    for sample in samples:
        if not sample or re.search(r'[\s:]', sample):
            raise ValueError('VCF sample names must be nonempty without whitespace or colons')
    samples = {sample: Path(path).resolve() for sample, path in sorted(samples.items())}
    if len(set(samples.values())) != len(samples):
        raise ValueError('Duplicate sample VCF path')
    sample_roles = sample_roles or {}
    if set(sample_roles) - set(samples):
        raise ValueError('Specimen role supplied for unknown sample')
    sample_roles = {sample: str(sample_roles.get(sample) or 'unknown').lower() for sample in samples}
    if any(not re.fullmatch(r'[a-z][a-z0-9_.-]*', role) for role in sample_roles.values()):
        raise ValueError('Specimen roles must be identifiers without whitespace or VCF delimiters')
    population_excluded_samples = population_excluded_samples or {}
    if set(population_excluded_samples) - set(samples):
        raise ValueError('Population exclusion supplied for unknown sample')
    if any(not isinstance(reason, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', reason)
           for reason in population_excluded_samples.values()):
        raise ValueError('Population exclusion reasons must be nonempty identifiers')
    imputed_by_sample = imputed_by_sample or {}
    if set(imputed_by_sample) - set(samples):
        raise ValueError('Imputation supplied for unknown sample')
    reference = Path(reference).resolve()
    if not Path(str(reference) + '.fai').is_file():
        raise ValueError('Indexed reference FASTA is required; missing .fai')
    input_identity = {
        'schema': VERSION, 'implementation_sha256': _implementation_digest(),
        'samples': {s: _identity(p) for s, p in samples.items()}, 'sample_roles': sample_roles,
        'population_excluded_samples': population_excluded_samples,
        'reference': _identity(reference), 'reference_index': _identity(str(reference) + '.fai'),
        'imputed': {s: [_identity(p) for p in paths] for s, paths in sorted(imputed_by_sample.items())},
        'imputed_min_gp': IMPUTED_MIN_GP, 'imputed_min_info': IMPUTED_MIN_INFO,
    }
    fingerprint = hashlib.sha256(json.dumps(input_identity, sort_keys=True).encode()).hexdigest()
    output_dir = Path(output_dir).resolve()
    filenames = {'all_evidence': 'cohort.all_evidence.vcf.gz', 'germline_population': 'cohort.germline_population.vcf.gz'}
    if any(output_dir / name in samples.values() for name in filenames.values()):
        raise ValueError('Cohort output would overwrite an input VCF')
    manifest_path = output_dir / 'cohort.manifest.json'
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text())
        if previous.get('input_fingerprint') == fingerprint and all(
            (output_dir / name).is_file() and Path(str(output_dir / name) + '.tbi').is_file()
            and previous.get('output_checksums', {}).get(name) == _sha256(output_dir / name)
            and previous.get('output_checksums', {}).get(name + '.tbi') == _sha256(str(output_dir / name) + '.tbi')
            for name in filenames.values()):
            return dict(previous, reused=True)
    headers = {}
    for sample, path in samples.items():
        with pysam.VariantFile(str(path)) as reader:
            if list(reader.header.samples) != [sample]:
                raise ValueError(f'Each input requires exactly the expected unique sample: {sample}: {path}')
            headers[sample] = reader.header.copy()
    header, maps, lengths = _header(pysam, headers, reference, sample_roles, population_excluded_samples)
    order = {chrom: index for index, chrom in enumerate(lengths)}
    output_dir.mkdir(parents=True, exist_ok=True)
    commands, counts = [], Counter()
    sample_counts = {sample: Counter() for sample in samples}
    with tempfile.TemporaryDirectory(prefix='.cohort-build-', dir=output_dir) as work:
        work = Path(work)
        prepared = {}
        for index, (sample, path) in enumerate(samples.items()):
            normalized = work / f'{index}.normalized.bcf'
            ordered = work / f'{index}.sorted.bcf'
            declared_order = [order[chrom] for chrom in headers[sample].contigs]
            canonical = None
            if declared_order != sorted(declared_order):
                canonical = work / f'{index}.canonical.bcf'
                canonical_header = _canonical_header(pysam, headers[sample], lengths)
                with pysam.VariantFile(str(path)) as reader, pysam.VariantFile(str(canonical), 'wb', header=canonical_header) as writer:
                    for record in reader:
                        record.translate(canonical_header)
                        writer.write(record)
            _run([bcftools, 'norm', '-f', reference, '-c', 'e', '-m', '-any',
                  '--multi-overlaps', '.', '-Ob', '--threads', str(threads), '-o', normalized, canonical or path], commands)
            _run([bcftools, 'sort', '-Ob', '-o', ordered, '-T', work / f'sort-{index}', normalized], commands)
            _run([bcftools, 'index', '-f', ordered], commands)
            normalized.unlink()
            if canonical:
                canonical.unlink()
            prepared[sample] = ordered
        readers = {sample: pysam.VariantFile(str(path)) for sample, path in prepared.items()}
        imputed = {sample: iter(_imputed_stream(pysam, paths, sample, order, lengths))
                   for sample, paths in imputed_by_sample.items()}
        imputed_next = {}
        try:
            imputed_next = {sample: next(stream, None) for sample, stream in imputed.items()}
            def stream_for(sample, reader):
                for key, record in _ordered_records(reader, order):
                    yield key, sample, record
            streams = [stream_for(sample, reader) for sample, reader in readers.items()]
            with pysam.VariantFile(str(work / filenames['all_evidence']), 'wz', header=header) as all_writer, \
                    pysam.VariantFile(str(work / filenames['germline_population']), 'wz', header=header) as population_writer, \
                    pysam.FastaFile(str(reference)) as fasta:
                merged = heapq.merge(*streams, key=lambda item: item[0])
                for key, group in itertools.groupby(merged, key=lambda item: item[0]):
                    records = list(group)
                    template = records[0][2]
                    if len(template.alts or ()) != 1 or not re.fullmatch('[ACGTNacgtn]+', template.ref + template.alts[0]):
                        raise ValueError('Cohort short-variant export requires explicit biallelic DNA alleles')
                    if fasta.fetch(template.contig, template.start, template.stop).upper() != template.ref.upper():
                        raise ValueError('Reference allele mismatch after normalization')
                    record = all_writer.new_record(contig=template.contig, start=template.start,
                                                   alleles=template.alleles)
                    ids = sorted({identifier for _, _, source in records for identifier in (source.id or '').split(';') if identifier and identifier != '.'})
                    record.id = ';'.join(ids) or None
                    seen_samples = set()
                    for _, sample, source in records:
                        if sample in seen_samples:
                            raise ValueError('Duplicate normalized allele for ' + sample)
                        seen_samples.add(sample)
                        for tag, value in source.info.items():
                            if not _common_info(tag) or tag in _RECOUNT:
                                continue
                            if tag in record.info and record.info[tag] != value:
                                raise ValueError(f'Conflicting common annotation {tag} at {source.contig}:{source.pos}:{source.ref}:{source.alts[0]}')
                            record.info[tag] = value
                        _copy_sample(record, source, sample, maps[sample])
                    for sample in samples:
                        if sample in seen_samples:
                            continue
                        call = record.samples[sample]
                        call['GT'] = (None, None)
                        call['EVCLASS'] = 'unconfirmed'
                        call['FT'] = 'Missing'
                        call['GTS'] = 'absent_record'
                        call['CARRIER'] = 'unknown'
                        call['ZYG'] = 'unresolved'
                        call['AFFSTAT'] = 'not_established'
                        current = imputed_next.get(sample)
                        while current is not None and current[0] < key:
                            current = next(imputed[sample], None)
                        if sample in imputed:
                            imputed_next[sample] = current
                        if current is not None and current[0] == key and _fill_imputed(record, current[1], sample):
                            sample_counts[sample]['imputed_exact_allele_backfills'] += 1
                        else:
                            sample_counts[sample]['absent_unknown_genotypes'] += 1
                    for sample in samples:
                        record.samples[sample]['ROLE'] = sample_roles[sample]
                    _recount(record)
                    all_writer.write(record)
                    counts['all_evidence_records'] += 1
                    population = record.copy()
                    for sample, call in population.samples.items():
                        excluded_reason = population_excluded_samples.get(sample)
                        ffpe_rejected = _finite(call.get('GFACCEPT')) == 0
                        ffpe_rescue_pending = _finite(call.get('GFRESCUE')) == 1
                        ffpe_population_excluded = _finite(call.get('GFPOP')) == 0
                        if call.get('EVCLASS') == 'germline_supported' and call.get('FT') == 'PASS' and not ffpe_rejected and not ffpe_rescue_pending and not ffpe_population_excluded and not excluded_reason:
                            sample_counts[sample]['population_retained_records'] += 1
                            continue
                        gt = call.get('GT') or (None, None)
                        call['GT'] = (None,) * len(gt)
                        call.phased = False
                        call['ZYG'] = 'unresolved'
                        call['MASKREASON'] = (excluded_reason if excluded_reason else
                                              'ffpe_artifact_excluded' if ffpe_rejected else
                                              'ffpe_imputation_rescue_unconfirmed' if ffpe_rescue_pending else
                                              'ffpe_population_ineligible' if ffpe_population_excluded else
                                              call.get('EVCLASS') or 'unknown_evidence')
                        for tag in ('DS', 'GP', 'PL', 'GL', 'GQ', 'AD', 'DP'):
                            if tag in call:
                                del call[tag]
                        sample_counts[sample]['population_masked_records'] += 1
                    if _recount(population):
                        population_writer.write(population)
                        counts['germline_population_records'] += 1
                    else:
                        counts['population_no_called_germline_records_removed'] += 1
        finally:
            for reader in readers.values():
                reader.close()
            for stream in imputed.values():
                stream.close()
        for name in filenames.values():
            _run([bcftools, 'index', '-f', '-t', '--threads', str(threads), work / name], commands)
            # Full parser pass catches sample/allele/schema corruption before publication.
            with pysam.VariantFile(str(work / name)) as reader:
                if list(reader.header.samples) != list(samples):
                    raise ValueError('Merged sample columns changed')
                for _, record in _ordered_records(reader, order):
                    expected_an = sum(x is not None for call in record.samples.values() for x in (call.get('GT') or ()))
                    if record.info.get('AN') != expected_an:
                        raise ValueError('Merged AN does not match primary genotypes')
        manifest = {
            'schema': VERSION, 'input_fingerprint': fingerprint, 'inputs': input_identity,
            'samples': list(samples), 'sample_roles': sample_roles,
            'population_excluded_samples': population_excluded_samples, 'counts': dict(counts),
            'sample_counts': {sample: dict(value) for sample, value in sample_counts.items()},
            'outputs': {key: str(output_dir / name) for key, name in filenames.items()},
            'semantics': 'Allele-exact merge of independently called samples; not joint variant calling. Missing is unknown, never reference.',
            'population_scope': 'Only germline_supported + FT=PASS primary genotypes; somatic, unresolved, GFACCEPT=0, GFRESCUE=1 and GFPOP=0 genotypes and explicitly excluded duplicate-individual specimens masked; AC/AN/AF recomputed among called alleles.',
            'imputation_backfill': 'Only exact biallelic alleles absent from a sample export, called GP>=0.90 and INFO>=0.80. Existing evidence decisions are never overridden.',
            'limitations': ['AF is conditional on called genotypes and the selected cohort, not a reference-population frequency.',
                            'Multiallelic genotypes are split per alternate allele; other alternate copies become missing, not reference.',
                            'No unseen genomic position is inferred to be callable. Imputation is not independent confirmation of somatic origin or FFPE artifact status.'],
            'commands': commands, 'reused': False,
            'output_checksums': {name + suffix: _sha256(str(work / name) + suffix)
                                 for name in filenames.values() for suffix in ('', '.tbi')},
        }
        for name in filenames.values():
            for suffix in ('', '.tbi'):
                os.replace(str(work / name) + suffix, str(output_dir / name) + suffix)
        temporary_manifest = work / 'manifest.json'
        temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        os.replace(temporary_manifest, manifest_path)
    return manifest
