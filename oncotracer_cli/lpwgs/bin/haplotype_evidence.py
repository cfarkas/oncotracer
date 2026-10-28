"""Exploratory fragment linkage; never a germline or pathogenicity classifier.

The conditional null randomizes allele pairing within read-group/strand strata,
preserving the observed allele counts. It tests linkage, not sequencing-error
rates. Monte Carlo uncertainty and an exact conditional cross-check are emitted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

METHOD = 'fragment-linkage-research-v1'
LIMITATION = ('Exploratory linkage evidence, not a probability that a variant is true, '
              'germline, inherited, or pathogenic. Somatic variants can also share a '
              'haplotype. Correlated errors, unmarked duplicates, FFPE damage, mapping '
              'artifacts and copy-number changes can violate the model. Sparse evidence '
              'is unassessable. Input calls, filters and genotypes are never changed.')


@contextmanager
def output_work(out):
    out.mkdir(parents=True,exist_ok=True)
    lock = out/'.haplotype-running'
    try:
        handle = lock.open('x')
    except FileExistsError as error:
        raise ValueError('Another assessment owns this output directory') from error
    try:
        handle.write(str(os.getpid()));handle.close()
        if any(p.name != lock.name for p in out.iterdir()):
            raise ValueError('Output directory became nonempty; existing results are preserved')
        with tempfile.TemporaryDirectory(prefix='.haplotype-',dir=out) as temporary:
            yield Path(temporary)
    finally:
        handle.close()
        lock.unlink()


def fingerprint(path, digest=False):
    path = Path(path).resolve()
    st = path.stat()
    result = dict(path=str(path), device=st.st_dev, inode=st.st_ino,
                  bytes=st.st_size, mtime_ns=st.st_mtime_ns)
    if digest:
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024**2), b''):
                h.update(block)
        result['sha256'] = h.hexdigest()
    return result


def key(chrom, pos, ref, alt):
    return f'{chrom}:{pos}:{ref}:{alt}'


def linkage_test(strata, simulations, seed):
    """Tables are [REF/REF, REF/ALT, ALT/REF, ALT/ALT] per stratum."""
    import numpy as np
    from scipy.stats import hypergeom
    rng = np.random.default_rng(seed)
    draws = np.zeros(simulations, dtype=np.int64)
    exact = np.array([1.0])
    offset = observed = 0
    for n00, n01, n10, n11 in strata:
        n, a, b = n00 + n01 + n10 + n11, n10 + n11, n01 + n11
        if not n:
            continue
        observed += n11
        lo, hi = max(0, a + b - n), min(a, b)
        if lo == hi:
            draws += lo
            offset += lo
            continue
        draws += rng.hypergeometric(a, n - a, b, simulations)
        exact = np.convolve(exact, hypergeom.pmf(np.arange(lo, hi + 1), n, a, b))
        offset += lo
    exceedances = int((draws >= observed).sum())
    # Plus-one correction prevents zero Monte Carlo p-values. The interval is
    # Wilson uncertainty for the simulated tail frequency, not biological truth.
    phat, z = exceedances / simulations, 1.959963984540054
    denominator = 1 + z*z / simulations
    centre = (phat + z*z/(2*simulations)) / denominator
    radius = z * math.sqrt(phat*(1-phat)/simulations + z*z/(4*simulations**2)) / denominator
    tail = float(exact[max(0, observed - offset):].sum())
    return dict(mc_linkage_p=(exceedances+1)/(simulations+1), mc_exceedances=exceedances,
                mc_simulations=simulations, mc_ci95_low=max(0., centre-radius),
                mc_ci95_high=min(1., centre+radius), exact_linkage_p=min(1., max(0., tail)))


def adjust_by(rows):
    """Benjamini–Yekutieli: overlapping variant pairs need not be independent."""
    tested = sorted((r for r in rows if r.get('exact_linkage_p') is not None),
                    key=lambda r: r['exact_linkage_p'])
    m = len(tested)
    harmonic = sum(1/i for i in range(1, m+1))
    bound = 1.
    for rank in range(m, 0, -1):
        row = tested[rank-1]
        bound = min(bound, row['exact_linkage_p'] * m * harmonic / rank)
        row['linkage_q_by'] = min(1., bound)


def load_variants(path, sample, bam_lengths, fasta):
    import pysam
    variants, audit, seen = [], [], set()
    with pysam.VariantFile(str(path)) as source:
        names = list(source.header.samples)
        if sample not in names:
            raise ValueError('--sample must exactly match a VCF sample; found: ' + ', '.join(names))
        for record in source:
            alts = record.alts or ()
            row = dict(variant=key(record.contig, record.pos, record.ref, ','.join(alts)),
                       chrom=record.contig, pos=record.pos, ref=record.ref,
                       alt=','.join(alts), filter=';'.join(record.filter), status='eligible')
            if len(alts) != 1:
                row['status'] = 'not_assessed_multiallelic'
            elif len(record.ref) != 1 or len(alts[0]) != 1 or not set(record.ref + alts[0]) <= set('ACGT'):
                row['status'] = 'not_assessed_non_snv'
            elif set(record.filter) != {'PASS'}:
                row['status'] = 'not_assessed_filter_not_pass'
            elif record.samples[sample].get('GT') not in ((0, 1), (1, 0)):
                row['status'] = 'not_assessed_non_heterozygous_or_missing_gt'
            if row['variant'] in seen:
                raise ValueError('Duplicate variant record: ' + row['variant'])
            seen.add(row['variant'])
            audit.append(row)
            if row['status'] != 'eligible':
                continue
            chrom = record.contig
            if chrom not in bam_lengths or chrom not in fasta.references:
                raise ValueError('VCF, BAM and reference must use identical contig names: ' + chrom)
            if bam_lengths[chrom] != fasta.get_reference_length(chrom):
                raise ValueError('BAM/reference contig length mismatch: ' + chrom)
            if fasta.fetch(chrom, record.start, record.stop).upper() != record.ref:
                raise ValueError('VCF REF does not match the supplied reference: ' + row['variant'])
            call = record.samples[sample]
            row['gt'] = list(call['GT'])
            row['phase_set'] = str(call.get('PS', '')) if call.phased else ''
            row['phased'] = bool(call.phased)
            variants.append(row)
    variants.sort(key=lambda r: (r['chrom'], r['pos'], r['ref'], r['alt']))
    return variants, audit


def candidate_pairs(variants, distance, limit):
    """Choose pairs before looking at BAM alleles; do not select by ALT support."""
    pairs = []
    for i, first in enumerate(variants):
        for j in range(i+1, len(variants)):
            second = variants[j]
            if second['chrom'] != first['chrom'] or second['pos'] - first['pos'] > distance:
                break
            if second['pos'] == first['pos']:
                raise ValueError('Multiple eligible alleles at one site require explicit normalization')
            pairs.append((i, j))
            if len(pairs) > limit:
                raise ValueError('Candidate-pair limit exceeded; supply a smaller VCF/region or increase --max-pairs explicitly')
    return pairs


def fragment_tables(bam, variants, pairs, min_mapq, min_baseq):
    """Fetch bounded windows, collapse mates, reject conflicting observations."""
    groups = defaultdict(list)
    for i, j in pairs:
        groups[(variants[i]['chrom'], (variants[i]['pos']-1)//100000)].append((i, j))
    tables = {}
    for (chrom, window), group in groups.items():
        needed = sorted({x for pair in group for x in pair}, key=lambda i: variants[i]['pos'])
        positions = [variants[i]['pos']-1 for i in needed]
        lookup = {variants[i]['pos']-1: i for i in needed}
        fragments = {}
        for read in bam.fetch(chrom, positions[0], positions[-1]+1):
            if (read.is_unmapped or read.is_secondary or read.is_supplementary or read.is_duplicate
                    or read.is_qcfail or read.mapping_quality < min_mapq or read.mapping_quality == 255
                    or not read.query_sequence or read.query_qualities is None):
                continue
            start = bisect_left(positions, read.reference_start)
            end = bisect_left(positions, read.reference_end)
            if start == end:
                continue
            rg = read.get_tag('RG') if read.has_tag('RG') else ''
            paired = read.is_paired and read.is_proper_pair and not read.mate_is_unmapped
            paired = paired and read.next_reference_id == read.reference_id
            # Discordant/improper mates cannot join otherwise separate loci.
            fid = (rg, read.query_name, 'pair' if paired else ('r2' if read.is_read2 else 'r1'))
            reverse = (read.mate_is_reverse if paired and read.is_read2 else read.is_reverse)
            stratum = (rg, 'reverse' if reverse else 'forward')
            observations, stored_stratum = fragments.setdefault(fid, ({}, stratum))
            if len(fragments) > 100000:
                raise ValueError('More than 100,000 fragments in an assessment window; use a smaller region')
            if stored_stratum != stratum:
                observations.clear()
                fragments[fid] = (observations, ('inconsistent', 'discard'))
                continue
            for q, r in read.get_aligned_pairs(matches_only=True):
                if r not in lookup or read.query_qualities[q] < min_baseq:
                    continue
                idx = lookup[r]
                base = read.query_sequence[q].upper()
                allele = 0 if base == variants[idx]['ref'] else 1 if base == variants[idx]['alt'] else None
                if idx in observations and observations[idx] != allele:
                    allele = None
                observations[idx] = allele
        for i, j in group:
            strata = defaultdict(lambda: [0, 0, 0, 0])
            for observations, stratum in fragments.values():
                a, b = observations.get(i), observations.get(j)
                if a is not None and b is not None and stratum != ('inconsistent', 'discard'):
                    strata[stratum][2*a+b] += 1
            tables[(i, j)] = [strata[k] for k in sorted(strata)]
        print(f'Haplotype evidence: {chrom} window {window+1}, {len(group)} pairs checked', file=sys.stderr, flush=True)
    return tables


def evaluate(variants, pairs, tables, *, simulations, seed, min_fragments, ffpe):
    rows = []
    for i, j in pairs:
        a, b = variants[i], variants[j]
        strata = tables[(i, j)]
        counts = [sum(s[k] for s in strata) for k in range(4)]
        n00, n01, n10, n11 = counts
        n = sum(counts)
        phase = 'unknown'
        if a['phased'] and b['phased'] and a['phase_set'] not in ('', 'None') and a['phase_set'] == b['phase_set']:
            phase = 'cis' if a['gt'] == b['gt'] else 'trans'
        row = dict(variant_a=a['variant'], variant_b=b['variant'], chrom=a['chrom'],
                   distance_bp=b['pos']-a['pos'], fragments=n, ref_ref=n00, ref_alt=n01,
                   alt_ref=n10, alt_alt=n11, strata=len(strata), input_phase_expectation=phase,
                   input_phase_is_independent_truth=False, germline_probability=None,
                   status='insufficient_evidence', exact_linkage_p=None, linkage_q_by=None,
                   ffpe_damage_context=bool(ffpe and any((v['ref'],v['alt']) in {('C','T'),('G','A')} for v in (a,b))))
        if n >= min_fragments and min(n10+n11, n01+n11, n00+n01, n00+n10) >= 2:
            derived = int.from_bytes(hashlib.sha256(f'{seed}:{a["variant"]}:{b["variant"]}'.encode()).digest()[:8], 'little')
            row.update(linkage_test(strata, simulations, derived), status='tested')
        rows.append(row)
    adjust_by(rows)
    for row in rows:
        if row['status'] == 'tested':
            row['status'] = ('cis_linkage_supported_under_null' if row['linkage_q_by'] <= .05 and row['alt_alt'] >= 2
                             else 'no_significant_cis_linkage')
    return rows


def write_tsv(path, rows, fields):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fields, delimiter='\t', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('bam', 'vcf', 'reference', 'sample', 'outdir'):
        p.add_argument('--'+flag, required=True)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--simulations', type=int, default=10000)
    p.add_argument('--seed', type=int, default=17)
    p.add_argument('--max-distance', type=int, default=5000)
    p.add_argument('--max-pairs', type=int, default=100000)
    p.add_argument('--min-fragments', type=int, default=6)
    p.add_argument('--min-mapq', type=int, default=30)
    p.add_argument('--min-baseq', type=int, default=20)
    p.add_argument('--ffpe', action='store_true')
    p.add_argument('--build', choices=('hg38','hg19'), default='hg38')
    p.add_argument('--varsome', action='store_true', help='Explicitly allow bounded online coordinate/allele queries')
    p.add_argument('--max-online-variants', type=int, default=20)
    p.add_argument('--llm-report', action='store_true')
    p.add_argument('--llm-python')
    p.add_argument('--llm-model')
    p.add_argument('--gpu', action='store_true')
    return p


def main(argv=None):
    import pysam
    a = parser().parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', a.sample):
        raise ValueError('Sample name must be a safe filename and match the VCF sample')
    if not (1 <= a.threads <= 128 and 1000 <= a.simulations <= 1000000 and 1 <= a.max_distance <= 1000000
            and 1 <= a.max_pairs <= 1000000 and 6 <= a.min_fragments <= 100000
            and 0 <= a.min_mapq <= 60 and 0 <= a.min_baseq <= 60 and 1 <= a.max_online_variants <= 1000):
        raise ValueError('Invalid resource, simulation or evidence limits; see --help')
    if a.llm_report and not a.varsome:
        raise ValueError('--llm-report requires --varsome; the model only selects sourced annotation facts')
    if Path(a.outdir).is_symlink():
        raise ValueError('Output directory must not be a symlink')
    out = Path(a.outdir).resolve()
    inputs = [Path(a.bam).resolve(), Path(a.vcf).resolve(), Path(a.reference).resolve()]
    if any(out == p or out in p.parents for p in inputs):
        raise ValueError('Output directory must not contain any input file')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose an empty --outdir; existing results are preserved')
    pysam.quickcheck(str(inputs[0]))
    if not Path(str(inputs[2])+'.fai').is_file():
        raise ValueError('A pre-indexed reference FASTA (.fai) is required')
    before = [fingerprint(p, digest=p == inputs[1]) for p in inputs]
    with pysam.AlignmentFile(str(inputs[0]), 'rb', threads=a.threads) as bam, pysam.FastaFile(str(inputs[2])) as fasta:
        if not bam.has_index() or bam.header.to_dict().get('HD',{}).get('SO') != 'coordinate':
            raise ValueError('A coordinate-sorted BAM with a completed index is required')
        lengths = dict(zip(bam.references, bam.lengths))
        if a.varsome:
            chr1 = next((c for c in ('chr1','1') if c in fasta.references), None)
            expected = 248956422 if a.build == 'hg38' else 249250621
            if not chr1 or fasta.get_reference_length(chr1) != expected:
                raise ValueError('Online annotation requires a matching full human reference assembly (--build)')
        variants, audit = load_variants(inputs[1], a.sample, lengths, fasta)
        pairs = candidate_pairs(variants, a.max_distance, a.max_pairs)
        tables = fragment_tables(bam, variants, pairs, a.min_mapq, a.min_baseq)
    rows = evaluate(variants, pairs, tables, simulations=a.simulations, seed=a.seed,
                    min_fragments=a.min_fragments, ffpe=a.ffpe)
    linked = defaultdict(list)
    for row in rows:
        linked[row['variant_a']].append(row['status'])
        linked[row['variant_b']].append(row['status'])
    for row in audit:
        if row['status'] == 'eligible':
            states = linked[row['variant']]
            row['status'] = ('no_linkable_variant_in_distance' if not states else
                             'insufficient_fragment_evidence' if all(s == 'insufficient_evidence' for s in states)
                             else 'has_assessable_pair')
    counts = Counter(r['status'] for r in rows)
    with output_work(out) as work:
        from haplotype_annotations import annotate, render_report, select_annotations
        annotations = annotate(variants, rows, work, a.build, online=a.varsome, limit=a.max_online_variants, sample=a.sample)
        selection = select_annotations(annotations, rows, work, a)
        import numpy, scipy
        incomplete = annotations['status'] == 'partial' or selection.get('status','').startswith('llm_')
        summary = dict(schema=METHOD, sample=a.sample, status='partial' if incomplete else 'complete', statistics_status='complete', inputs=before,
                       software=dict(numpy=numpy.__version__,scipy=scipy.__version__,pysam=pysam.__version__,rng='PCG64'),
                       code_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                    for name in ('haplotype_evidence.py','haplotype_annotations.py','haplotype_annotation_selector.py')},
                       eligible_snvs=len(variants), input_records=len(audit), pairs=len(pairs),
                       pair_status_counts=dict(counts), parameters=vars(a), limitation=LIMITATION,
                       multiple_testing='Benjamini-Yekutieli across all assessable candidate pairs in this invocation',
                       missing_linkage_is_not_evidence_against_variant=True,
                       germline_probability=None, calibrated_for_low_pass=False,
                       online_annotation_status=annotations['status'], annotation_selection=selection)
        fields = ['variant_a','variant_b','chrom','distance_bp','fragments','ref_ref','ref_alt','alt_ref','alt_alt',
                  'strata','mc_linkage_p','mc_ci95_low','mc_ci95_high','mc_exceedances','mc_simulations',
                  'exact_linkage_p','linkage_q_by','status','input_phase_expectation',
                  'input_phase_is_independent_truth','ffpe_damage_context','germline_probability']
        write_tsv(work/f'{a.sample}.haplotype_pairs.tsv', rows, fields)
        write_tsv(work/f'{a.sample}.variant_assessability.tsv', audit,
                  ['variant','chrom','pos','ref','alt','filter','status'])
        (work/f'{a.sample}.haplotype_evidence.json').write_text(json.dumps(summary, indent=2)+'\n')
        (work/f'{a.sample}.haplotype_evidence.html').write_text(render_report(summary, rows, annotations, selection))
        if before != [fingerprint(p, digest=p == inputs[1]) for p in inputs]:
            raise ValueError('An input changed during assessment; results were not published')
        artifacts = {p.name: fingerprint(p, True)['sha256'] for p in work.iterdir() if p.is_file()}
        for path in work.iterdir():
            os.replace(path, out/path.name)
        (out/f'{a.sample}.manifest.json').write_text(json.dumps(dict(schema=METHOD, files=artifacts), indent=2)+'\n')
    print(json.dumps(dict(sample=a.sample, pairs=len(rows), statuses=dict(counts), outdir=str(out),
                          online_annotation_status=annotations['status'], germline_probability=None)))
    return 2 if incomplete else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print('ERROR:', error, file=sys.stderr)
        raise SystemExit(2)
