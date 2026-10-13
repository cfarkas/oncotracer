"""Auditable FFPErase/GLIMPSE2 reconciliation; never a somatic caller.

Votes share read evidence and are not independent probabilities. Rescue retains
research germline candidates without changing original filters or genotypes.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import tempfile
import re
import hashlib
import sqlite3
import subprocess
from collections import Counter
from importlib import resources
from pathlib import Path
from urllib.parse import quote
from .runtime import OncoTracerError, atomic_write_json, sha256_file
from .variant_filters import records

VERSION = 'ffpe-glimpse-evidence-v1'
THRESHOLDS = {'minimum_gp': .99, 'minimum_info': .9, 'minimum_alt_reads': 3,
              'minimum_depth': 8, 'heterozygous_vaf_min': .2,
              'heterozygous_vaf_max': .8, 'homozygous_alt_vaf_min': .8}
SOURCES = ['https://www.nature.com/articles/s41588-023-01438-3',
           'https://odelaneau.github.io/GLIMPSE/docs/documentation/phase/',
           'https://www.nature.com/articles/s41467-025-65654-7',
           'https://github.com/papaemmelab/nf-ffperase']


def _implementation_sha256(filename):
    """Hash shipped source bytes, including when loaded from an immutable zipapp."""
    return hashlib.sha256(resources.files('oncotracer_cli').joinpath(filename).read_bytes()).hexdigest()


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _gt(value):
    parts = value if isinstance(value, (list, tuple)) else re.split(r'[/|]', str(value))
    if any(str(p) not in {'0', '1'} for p in parts):
        return None
    try:
        result = tuple(int(p) for p in parts)
    except (ValueError, TypeError):
        return None
    return result if 1 <= len(result) <= 2 and set(result) <= {0, 1} else None


def _called_probability(value, gt):
    """Select probability of actual GT, not maximum for another genotype."""
    if isinstance(value, str) and ',' in value:
        value = value.split(',')
    if isinstance(value, (list, tuple)):
        expected = 3 if gt and len(gt) == 2 else 2
        values = [_number(v) for v in value]
        if not gt or len(values) != expected or any(v is None or not 0 <= v <= 1 for v in values):
            return None
        if abs(sum(values) - 1) > .01:
            return None
        return values[sum(gt)]
    result = _number(value)
    return result if result is not None and 0 <= result <= 1 else None


def adjudicate_evidence(ffpe_status, imputed_gt, gp, info, read_alt_count,
                        read_dp, caller_filter, reference_match=True):
    """Adjudicate one exact allele; imputed_gt uses biallelic allele indices.

    accepted means retained by the FFPE selection rule only, not clinical/GT
    PASS. Missing imputation never establishes artifact or somatic origin.
    """
    ffpe = str(ffpe_status or 'NOT_EVALUATED').upper()
    flagged = ffpe in {'REJECTED', 'ARTIFACT', 'TRUE', '1', 'FFPERASE_ARTIFACT'}
    evaluated_real = ffpe in {'REAL', 'FALSE', '0'}
    gt = _gt(imputed_gt)
    posterior, quality = _called_probability(gp, gt), _number(info)
    alt, depth = _number(read_alt_count), _number(read_dp)
    valid_reads = (alt is not None and depth is not None and alt.is_integer()
                   and depth.is_integer() and 0 <= alt <= depth and depth > 0)
    imputed_alt = bool(gt and any(gt))
    quality_pass = (bool(gt) and reference_match and posterior is not None
            and posterior >= THRESHOLDS['minimum_gp'] and quality is not None
            and THRESHOLDS['minimum_info'] <= quality <= 1)
    high = imputed_alt and quality_pass
    read_support = (valid_reads and alt >= THRESHOLDS['minimum_alt_reads']
                    and depth >= THRESHOLDS['minimum_depth'])
    vaf = alt / depth if valid_reads else None
    compatible = bool(gt and valid_reads and (
        (sum(gt) == len(gt) and vaf >= THRESHOLDS['homozygous_alt_vaf_min']) or
        (len(gt) == 2 and sum(gt) == 1 and
         THRESHOLDS['heterozygous_vaf_min'] <= vaf <= THRESHOLDS['heterozygous_vaf_max'])))
    caller_pass = str(caller_filter) == 'PASS'
    reasons = []
    if not reference_match:
        reasons.append('reference_allele_mismatch')
    if not gt:
        reasons.append('imputation_unavailable_or_unresolved')
    elif not imputed_alt:
        reasons.append('imputed_reference_does_not_establish_somatic_origin')
    elif not high:
        reasons.append('imputation_below_rescue_threshold_or_quality_missing')
    if not read_support:
        reasons.append('insufficient_direct_read_support_for_rescue')
    elif gt and imputed_alt and not compatible:
        reasons.append('read_allele_fraction_disagrees_with_imputed_genotype')
    if not caller_pass:
        reasons.append('original_read_caller_not_pass')
    rescue = bool(flagged and high and read_support and compatible and caller_pass)
    if flagged:
        status = 'germline_rescue_candidate' if rescue else 'ffpe_filtered'
        reasons.append('ffperase_flag_retained_in_audit')
        if rescue:
            reasons.append('retained_for_germline_review_not_confirmed_somatic_rescue')
    elif not evaluated_real:
        status = 'ffpe_not_assessed'
        reasons.append('ffperase_missing_assessment_is_not_a_real_call')
    elif high:
        status = 'germline_supported'
    else:
        status = 'unconfirmed_origin'
    if not reference_match:
        status = 'reference_mismatch'
    return {'status': status, 'accepted': bool(reference_match and (not flagged or rescue)),
            'eligible_for_population': bool(reference_match and high and not flagged and not (read_support and not compatible and caller_pass)),
            'votes': {'ffperase': 'artifact' if flagged else 'not_flagged' if evaluated_real else 'not_assessed',
                      'glimpse2': 'alternate_supported' if high else 'reference_supported' if quality_pass and not imputed_alt else 'unresolved',
                      'reads': 'concordant_support' if read_support and compatible and caller_pass else 'insufficient_or_discordant'},
            'reasons': reasons, 'research_only': True, 'rescued': rescue}


FORMATS = {
    'GFSTATUS': ('String', 'FFPErase and GLIMPSE2 reconciliation; research evidence category'),
    'GFVOTES': ('String', 'Correlated FFPErase, GLIMPSE2 and read labels; not independent votes'),
    'GFREASON': ('String', 'Percent-encoded reconciliation reasons'),
    'GFACCEPT': ('Integer', '1 retained by FFPE rule only; not genotype PASS or clinical validity'),
    'GFPOP': ('Integer', '1 eligible by this FFPE/imputation rule only; other population QC still applies'),
    'GFRESCUE': ('Integer', '1 flagged allele retained as germline research candidate; original flags preserved'),
}


def _letter_gt(value, ref, alt):
    gt = _gt(value)
    if gt is not None:
        return gt
    parts = re.split(r'[/|]', str(value))
    return tuple(0 if p == ref else 1 for p in parts) if 1 <= len(parts) <= 2 and all(p in {ref, alt} for p in parts) else None


def _sample_evidence(fields):
    sample = dict(zip(fields[8].split(':'), fields[9].split(':')))
    info = dict(item.split('=', 1) if '=' in item else (item, True)
                for item in fields[7].split(';') if item != '.')
    # Unified primary GT may be missing: only original read depths count.
    if _number(sample.get('GDP')) is not None:
        dp, ad, caller = sample.get('GDP'), sample.get('GAD'), sample.get('GFT')
    elif _number(sample.get('SDP')) is not None:
        dp, ad, caller = sample.get('SDP'), sample.get('SAD'), sample.get('SFT')
    else:
        dp, ad, caller = sample.get('DP'), sample.get('AD'), fields[6]
        original = [v for v in str(caller).split(';') if v not in {'FFPERASE_REJECTED', 'FFPERASE_ARTIFACT', 'FFPERASE_NOT_EVALUATED'}]
        # Assessment cannot distinguish an original PASS from '.', so require
        # original_vcf to recover that original value for strict rescue.
        caller = ';'.join(original) if original else '.'
    ffpe = sample.get('FFPERASE_DECISION', info.get('FFPERASE_DECISION', 'NOT_EVALUATED'))
    if {'FFPERASE_REJECTED', 'FFPERASE_ARTIFACT'} & set(fields[6].split(';')):
        ffpe = 'REJECTED'
    ad = str(ad).split(',')
    return sample, ffpe, ad[1] if len(ad) == 2 else None, dp, caller


def apply_vote_annotations(input_vcf, output_dir, sample, imputed_paths=(), *, reference=None, original_vcf=None):
    """Write sample.ffpe_glimpse.{all,accepted}.vcf.gz, TSV and summary.

    Input: one normalized biallelic sample VCF, optionally unified IGT/IGP/IINFO
    fields. External imputed_paths require indexed VCF/BCFs; join exact alleles.
    reference checks REF when supplied. original_vcf restores caller FILTER for
    FFPErase-assessed VCFs and must have identical allele order. GT is unchanged.
    """
    import pysam
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not sample or re.search(r'[^A-Za-z0-9_.-]', sample):
        raise OncoTracerError('Invalid sample identifier')
    paths = {kind: output / f'{sample}.ffpe_glimpse.{kind}.vcf.gz' for kind in ('all', 'accepted')}
    if Path(input_vcf).resolve() in {p.resolve() for p in paths.values()}:
        raise OncoTracerError('Input VCF cannot be an output path')
    table = output / f'{sample}.ffpe_glimpse.evidence.tsv'
    temporary = tempfile.TemporaryDirectory(prefix='.ffpe-glimpse-', dir=output)
    stage = Path(temporary.name)
    staged_paths = {kind: stage / path.name for kind, path in paths.items()}
    staged_table = stage / table.name
    fasta = pysam.FastaFile(str(reference)) if reference else None
    imputed = [pysam.VariantFile(str(path)) for path in imputed_paths]
    originals = (f for _, f in records(original_vcf) if f) if original_vcf else None
    counts = Counter()
    embedded_imputation = False
    readers_by_chrom = {}
    try:
        for reader in imputed:
            if sample not in reader.header.samples:
                raise OncoTracerError(f'Imputation sample absent: {sample}')
        with pysam.BGZFile(str(staged_paths['all']), 'w') as all_out, pysam.BGZFile(str(staged_paths['accepted']), 'w') as accepted_out, staged_table.open('w') as evidence:
            writer = csv.writer(evidence, delimiter='\t')
            writer.writerow(['chrom', 'pos', 'ref', 'alt', 'status', 'accepted', 'population_eligible', 'rescued', 'votes', 'reasons'])
            for line, fields in records(input_vcf):
                if fields is None:
                    if line.startswith('##FORMAT=<ID=IGT,'):
                        embedded_imputation = True
                    if any(line.startswith(f'##FORMAT=<ID={tag},') for tag in FORMATS):
                        raise OncoTracerError('Input already contains FFPE/GLIMPSE annotations')
                    if line.startswith('#CHROM'):
                        if line.rstrip().split('\t')[9:] != [sample]:
                            raise OncoTracerError('Reconciliation requires exactly the requested sample')
                        for tag, (kind, description) in FORMATS.items():
                            header = f'##FORMAT=<ID={tag},Number=1,Type={kind},Description="{description}">\n'.encode()
                            all_out.write(header); accepted_out.write(header)
                        header = f'##oncotracer_ffpe_glimpse={VERSION}\n'.encode()
                        all_out.write(header); accepted_out.write(header)
                    all_out.write(line.encode()); accepted_out.write(line.encode())
                    continue
                if len(fields) != 10:
                    raise OncoTracerError('Expected a single-sample VCF')
                chrom, pos, ref, alt = fields[0], int(fields[1]), fields[3], fields[4]
                values, ffpe, alt_reads, dp, caller = _sample_evidence(fields)
                if originals is not None:
                    original = next(originals, None)
                    if original is None or (original[0], original[1], original[3], original[4]) != (chrom, fields[1], ref, alt):
                        raise OncoTracerError('Original and assessed allele/order mismatch')
                    caller = _sample_evidence(original)[4]
                gt = _letter_gt(values.get('IGT'), ref, alt)
                gp, info = values.get('IGP'), values.get('IINFO')
                matches = []
                # Unified IGT headers mean missing IGT is a meaningful absent
                # panel call. Never issue millions of redundant BCF fetches.
                if not embedded_imputation and chrom not in readers_by_chrom:
                    readers_by_chrom[chrom] = [r for r in imputed if chrom in r.header.contigs
                                              and next(r.fetch(chrom), None) is not None]
                for reader in (() if embedded_imputation else readers_by_chrom[chrom]):
                    for record in reader.fetch(chrom, pos - 1, pos):
                        if record.pos == pos and record.ref == ref and record.alts == (alt,):
                            call = record.samples[sample]
                            matches.append((call.get('GT'), call.get('GP'), record.info.get('INFO')))
                if len(matches) > 1:
                    raise OncoTracerError(f'Duplicate exact imputed allele: {chrom}:{pos}:{ref}:{alt}')
                if matches:
                    gt, gp, info = matches[0]
                reference_match = bool(fasta is None or (chrom in fasta.references and
                    fasta.fetch(chrom, pos - 1, pos - 1 + len(ref)).upper() == ref.upper()))
                decision = adjudicate_evidence(ffpe, gt, gp, info, alt_reads, dp, caller, reference_match)
                votes = '|'.join(f'{k}={v}' for k, v in decision['votes'].items())
                reason = quote('|'.join(decision['reasons']) or 'no_additional_limitations', safe='._-|')
                additions = [decision['status'], votes, reason, str(int(decision['accepted'])),
                             str(int(decision['eligible_for_population'])), str(int(decision['rescued']))]
                original_values = fields[9].split(':')
                original_values.extend(['.'] * (len(fields[8].split(':')) - len(original_values)))
                fields[8] += ':' + ':'.join(FORMATS)
                fields[9] = ':'.join(original_values + additions)
                rendered = ('\t'.join(fields) + '\n').encode()
                all_out.write(rendered)
                if decision['accepted']:
                    accepted_out.write(rendered); counts['accepted'] += 1
                counts[decision['status']] += 1
                counts['records'] += 1
                writer.writerow([chrom, pos, ref, alt, decision['status'], int(decision['accepted']),
                                 int(decision['eligible_for_population']), int(decision['rescued']), votes, reason])
        if originals is not None and next(originals, None) is not None:
            raise OncoTracerError('Original VCF contains extra records')
        for path in staged_paths.values():
            pysam.tabix_index(str(path), preset='vcf', force=True)
        for kind, path in paths.items():
            os.replace(staged_paths[kind], path)
            os.replace(str(staged_paths[kind]) + '.tbi', str(path) + '.tbi')
        os.replace(staged_table, table)
    finally:
        if fasta:
            fasta.close()
        for reader in imputed:
            reader.close()
        temporary.cleanup()
    result = {'version': VERSION, 'sample': sample, 'research_only': True, 'thresholds': THRESHOLDS,
              'counts': dict(counts), 'all_vcf': str(paths['all']), 'accepted_vcf': str(paths['accepted']),
              'evidence_tsv': str(table), 'sources': SOURCES,
              'limitations': ['Votes share reads and are not independent validation.',
                 'Imputation cannot establish somatic origin or prove a C>T artifact.',
                 'FFPErase publication used median FFPE coverage 51x; low-pass performance is not established.',
                 'Retained by FFPE rule does not establish genotype PASS or clinical validity.',
                 'All original caller filters, genotypes and annotation fields are retained.']}
    atomic_write_json(output / f'{sample}.ffpe_glimpse.summary.json', result)
    return result



def _read_candidate(fields):
    values = dict(zip(fields[8].split(':'), fields[9].split(':')))
    if any(tag in values for tag in ('GGT', 'SGT', 'GDP', 'SDP')):
        for prefix in ('G', 'S'):
            depth = _number(values.get(prefix + 'DP'))
            gt = _letter_gt(values.get(prefix + 'GT'), fields[3], fields[4])
            if depth is not None and depth > 0 and gt and any(gt):
                return True
        return False
    depth = _number(values.get('DP'))
    gt = _gt(values.get('GT'))
    return bool(depth is not None and depth > 0 and gt and any(gt))


def _file_signature(path, *, digest=False):
    path = Path(path).resolve()
    stat = path.stat()
    signature = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                 'inode': stat.st_ino}
    if digest:
        signature['sha256'] = sha256_file(path)
    return signature


def assess_and_vote(input_vcf, output_dir, sample, imputed_paths=(), *, bam,
                    reference, detection, tools):
    """Assess read-based candidates with existing FFPErase, then vote all alleles.

    Model is never run on imputation-only records. Cache checks SHA256 of source
    VCF, implementation, resources supplied by discover(), and output artifacts;
    BAM/reference reuse uses path, size, mtime_ns and inode identities (not a
    claim of hashing entire multi-GB BAM/reference files on each invocation).
    """
    from . import ffperase
    import pysam
    source, output = Path(input_vcf), Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    identity = {'version': VERSION, 'sample': sample,
                'source': _file_signature(source, digest=True),
                'bam': _file_signature(bam), 'reference': _file_signature(reference),
                'reference_index': _file_signature(str(reference) + '.fai', digest=True),
                'imputed': [_file_signature(p) for p in imputed_paths],
                'detection': detection, 'tools': tools,
                'implementation': _implementation_sha256('glimpse_ffpe.py'),
                'ffperase_implementation': _implementation_sha256('ffperase.py')}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    manifest = output / f'{sample}.ffpe_glimpse.resume.json'
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if saved.get('signature') == signature and all(
                Path(path).is_file() and sha256_file(Path(path)) == sha
                for path, sha in saved.get('outputs', {}).items()) and saved.get('outputs'):
            result = saved['result']
            result['reused'] = True
            return result
    # FFPErase caches BAM-wide metrics in directory.parent/ffperase_shared.
    # Scope that parent to this invocation so a changed BAM cannot inherit
    # another run's coverage, insert-size or damage metrics.
    invocation = Path(tempfile.mkdtemp(prefix='ffperase-', dir=output))
    work = invocation / 'assessment'
    work.mkdir()
    candidates = work / 'read_candidates.vcf.gz'
    selected = 0
    with pysam.BGZFile(str(candidates), 'w') as out:
        for line, fields in records(source):
            if fields is None:
                out.write(line.encode())
            elif _read_candidate(fields):
                out.write(line.encode()); selected += 1
    assessed = work / 'assessed.vcf'
    def run(label, command, **kwargs):
        with (work / f'{label}.stderr.log').open('w') as stderr:
            subprocess.run(list(map(str, command)), check=True, stderr=stderr, **kwargs)
    ffpe_summary = ffperase.assess(candidates, assessed, detection=detection,
                                  bam=Path(bam), reference=Path(reference),
                                  directory=work, run=run, tools=tools)
    # Store assessed subset on disk; no dictionary holding millions of alleles.
    database = sqlite3.connect(work / 'assessments.sqlite')
    database.execute('CREATE TABLE evidence (chrom TEXT,pos INTEGER,ref TEXT,alt TEXT,decision TEXT,score TEXT,PRIMARY KEY(chrom,pos,ref,alt)) WITHOUT ROWID')
    try:
        for _, fields in records(assessed):
            if fields is None:
                continue
            info = dict(v.split('=', 1) if '=' in v else (v, True) for v in fields[7].split(';') if v != '.')
            database.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?)',
                             (fields[0], int(fields[1]), fields[3], fields[4],
                              info.get('FFPERASE_DECISION', 'NOT_EVALUATED'), info.get('FFPERASE_SCORE', '.')))
        database.commit()
        joined = work / 'all_with_ffperase.vcf.gz'
        with pysam.BGZFile(str(joined), 'w') as out:
            for line, fields in records(source):
                if fields is None:
                    if line.startswith('##FORMAT=<ID=FFPERASE_'):
                        raise OncoTracerError('FFPErase FORMAT fields already exist; use apply_vote_annotations directly')
                    if line.startswith('#CHROM'):
                        out.write(b'##FORMAT=<ID=FFPERASE_DECISION,Number=1,Type=String,Description="Per-sample FFPErase decision; NOT_EVALUATED includes imputation-only records">\n')
                        out.write(b'##FORMAT=<ID=FFPERASE_SCORE,Number=1,Type=Float,Description="Raw FFPErase random-forest artifact score; not calibrated for low-pass data">\n')
                    out.write(line.encode()); continue
                prediction = database.execute('SELECT decision,score FROM evidence WHERE chrom=? AND pos=? AND ref=? AND alt=?',
                                              (fields[0], int(fields[1]), fields[3], fields[4])).fetchone()
                decision, score = prediction or ('NOT_EVALUATED', '.')
                values = fields[9].split(':')
                values.extend(['.'] * (len(fields[8].split(':')) - len(values)))
                fields[8] += ':FFPERASE_DECISION:FFPERASE_SCORE'
                fields[9] = ':'.join([*values, decision, score])
                out.write(('\t'.join(fields) + '\n').encode())
    finally:
        database.close()
    result = apply_vote_annotations(joined, output, sample, imputed_paths,
                                    reference=reference, original_vcf=source)
    result.update(ffperase=ffpe_summary, read_candidates=selected, reused=False,
                  assessment_directory=str(work), cache_input_identity=identity)
    summary = output / f'{sample}.ffpe_glimpse.summary.json'
    atomic_write_json(summary, result)
    artifacts = [Path(result[key]) for key in ('all_vcf', 'accepted_vcf', 'evidence_tsv')]
    artifacts += [Path(result[key] + '.tbi') for key in ('all_vcf', 'accepted_vcf')]
    artifacts += [summary, work/'ffperase.evidence.tsv']
    atomic_write_json(manifest, {'signature': signature, 'result': result,
                     'outputs': {str(path): sha256_file(path) for path in artifacts}})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vcf', required=True)
    parser.add_argument('--outdir', required=True)
    parser.add_argument('--sample', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--imputed', action='append', default=[])
    parser.add_argument('--run-ffperase', action='store_true')
    parser.add_argument('--bam')
    for name in ('root', 'models', 'prefix', 'sif'):
        parser.add_argument('--ffperase-' + name)
    parser.add_argument('--samtools', default='samtools')
    parser.add_argument('--gatk', default='gatk')
    args = parser.parse_args(argv)
    source, original = Path(args.vcf), None
    if args.run_ffperase:
        from . import ffperase
        if not args.bam or not args.reference:
            parser.error('--run-ffperase requires --bam and --reference')
        detection = ffperase.discover(args.ffperase_root, args.ffperase_models, args.ffperase_sif, args.ffperase_prefix)
        result = assess_and_vote(source, args.outdir, args.sample, args.imputed,
                                 bam=args.bam, reference=args.reference, detection=detection,
                                 tools={'samtools': args.samtools, 'gatk': args.gatk})
    else:
        result = apply_vote_annotations(source, args.outdir, args.sample, args.imputed, reference=args.reference)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
