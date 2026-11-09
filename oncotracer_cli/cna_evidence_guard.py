"""Auditable read-support heuristics for control and sparse-sample CNA calls.

This corroborates calls with their underlying counts; it is not an independent
assay or a calibrated false-discovery estimate. It never converts a rejected
call into evidence of normal copy number.
"""
from collections import defaultdict
import csv
import gzip
import hashlib
import html
import io
import json
import math
from pathlib import Path
from statistics import median

from .broad_cna import prepare_bins, _mad
from .runtime import atomic_write_json, atomic_write_text, OncoTracerError

POLICY = {
    "schema": "cna-read-support-v1", "minimum_total_reads": 5000,
    "minimum_region_observed_reads": 100, "minimum_region_expected_reads": 200,
    "minimum_region_bins": 10, "minimum_reference_chromosomes": 8,
    "minimum_matched_fraction": .8, "minimum_covered_fraction": .6,
    "log2_uncertainty_floor": .15, "interval_z": 2.576,
    "minimum_log2_margin": .15, "maximum_approximate_q": .01,
    "minimum_split_reads": 20, "minimum_supporting_blocks": 3, "consistency_blocks": 4,
}


class ReadSupport:
    def __init__(self, rows):
        self.bins = prepare_bins(rows)
        self.total = sum(b['reads'] for b in self.bins)
        self.chromosomes = {b['chromosome'] for b in self.bins}
        self.groups = defaultdict(lambda: [0., 0., 0])
        for b in self.bins:
            v = self.groups[b['chromosome'], b['stratum']]
            v[0] += b['reads']; v[1] += b['exposure']; v[2] += 1
        self.cache = {}

    def matched(self, chrom):
        if chrom in self.cache:
            return self.cache[chrom]
        targets = [b for b in self.bins if b['chromosome'] == chrom]
        expectations = {}
        for stratum in {b['stratum'] for b in targets}:
            refs = [v for (c, s), v in self.groups.items()
                    if c != chrom and s == stratum and v[0] >= 20 and v[2] >= 10]
            if len(refs) < POLICY['minimum_reference_chromosomes'] or sum(v[0] for v in refs) < 500:
                continue
            rates = [v[0] / v[1] for v in refs]
            rate = median(rates)
            expectations[stratum] = (rate, max(POLICY['log2_uncertainty_floor'],
                                               _mad([math.log2(r/rate) for r in rates])))
        matched = [dict(b, expected=b['exposure']*expectations[b['stratum']][0],
                        uncertainty=expectations[b['stratum']][1])
                   for b in targets if b['stratum'] in expectations]
        residuals, observed, expected = [], 0., 0.
        for b in matched:
            observed += b['reads']; expected += b['expected']
            if expected >= 100:
                residuals.append((observed-expected)/math.sqrt(expected)); observed, expected = 0., 0.
        dispersion = max(1., _mad(residuals)**2) if len(residuals) >= 10 else 4.
        self.cache[chrom] = targets, matched, dispersion
        return self.cache[chrom]

    def interval(self, event):
        result = {'decision': 'rejected', 'reason': 'insufficient_genome_wide_read_support',
                  'observed_reads': None, 'expected_reads': None, 'usable_bins': 0,
                  'log2_ratio': None, 'lower_99': None, 'upper_99': None,
                  'approximate_p': None, 'approximate_q': None,
                  'covered_fraction': None, 'odd_log2': None, 'even_log2': None, 'supporting_blocks': None}
        if self.total < POLICY['minimum_total_reads'] or len(self.bins) < 100 or len(self.chromosomes) < 10:
            return result
        chrom = str(event['chrom']).removeprefix('chr')
        start, end = int(event['start']), int(event['end'])
        if not 0 < start <= end:
            raise OncoTracerError('Invalid CNA event interval')
        targets, matched, dispersion = self.matched(chrom)
        # Fully contained bins avoid assigning reads outside the event to it.
        targets = [b for b in targets if start <= b['start'] and b['end'] <= end]
        values = [b for b in matched if start <= b['start'] and b['end'] <= end]
        observed = sum(b['reads'] for b in values)
        expected = sum(b['expected'] for b in values)
        covered = sum(b['end']-b['start']+1 for b in values)/(end-start+1)
        result.update(observed_reads=observed, expected_reads=expected, usable_bins=len(values), covered_fraction=covered)
        if len(values) < POLICY['minimum_region_bins']:
            result['reason'] = 'too_few_usable_region_bins'; return result
        if len(values)/max(1, len(targets)) < POLICY['minimum_matched_fraction'] or covered < POLICY['minimum_covered_fraction']:
            result['reason'] = 'poor_reference_or_genomic_coverage'; return result
        if observed < POLICY['minimum_region_observed_reads'] or expected < POLICY['minimum_region_expected_reads']:
            result['reason'] = 'insufficient_region_read_support'; return result
        ratio = math.log2(observed/expected)
        uncertainty = sum(b['expected']*b['uncertainty'] for b in values)/expected
        se = math.sqrt(dispersion/observed/math.log(2)**2 + uncertainty**2)
        low, high = ratio-POLICY['interval_z']*se, ratio+POLICY['interval_z']*se
        result.update(log2_ratio=ratio, lower_99=low, upper_99=high,
                      approximate_p=math.erfc(abs(ratio)/se/math.sqrt(2)))
        sign = 1 if event['state'] in ('gain', 'amplification') else -1
        if event['state'] not in ('gain', 'amplification', 'loss', 'deep_loss'):
            raise OncoTracerError('Unsupported CNA event state')
        margin = POLICY['minimum_log2_margin']
        if (low <= margin if sign > 0 else high >= -margin):
            result['reason'] = 'uncertain_or_discordant_read_depth'; return result
        # Interleaved subsets must support the same direction; a few isolated
        # extreme bins must not suffice. These are consistency checks, not replicates.
        for label, subset in [('odd_log2', values[::2]), ('even_log2', values[1::2])]:
            obs, exp = sum(b['reads'] for b in subset), sum(b['expected'] for b in subset)
            if min(obs, exp) < POLICY['minimum_split_reads']:
                result['reason'] = 'insufficient_split_bin_support'; return result
            result[label] = math.log2(obs/exp)
            if sign*result[label] <= margin:
                result['reason'] = 'inconsistent_split_bin_direction'; return result
        supported = 0
        for index in range(POLICY['consistency_blocks']):
            subset = values[index*len(values)//4:(index+1)*len(values)//4]
            obs, exp = sum(b['reads'] for b in subset), sum(b['expected'] for b in subset)
            if min(obs, exp) >= POLICY['minimum_split_reads'] and sign*math.log2(obs/exp) > margin:
                supported += 1
        result['supporting_blocks'] = supported
        if supported < POLICY['minimum_supporting_blocks']:
            result['reason'] = 'localized_or_inconsistent_read_support'; return result
        result.update(decision='pending_multiple_comparison_check', reason='')
        return result

    def evaluate(self, events):
        records = [self.interval(event) for event in events]
        tested = sorted([r for r in records if r['approximate_p'] is not None], key=lambda r:r['approximate_p'])
        q = 1.
        for rank in range(len(tested), 0, -1):
            r = tested[rank-1]; q = min(q, r['approximate_p']*len(tested)/rank); r['approximate_q'] = q
        for record in records:
            if record['decision'] == 'pending_multiple_comparison_check':
                if record['approximate_q'] <= POLICY['maximum_approximate_q']:
                    record.update(decision='retained', reason='supported_by_raw_read_depth')
                else:
                    record.update(decision='rejected', reason='multiple_candidate_evidence_insufficient')
        return records


def raw_sample_rows(counts_dir, caller, names):
    wide = counts_dir/'broad_cna_counts.tsv.gz'
    if caller == 'qdnaseq' and wide.is_file():
        with gzip.open(wide, 'rt') as handle:
            table = list(csv.DictReader(handle, delimiter='\t'))
        for name in names:
            yield name, [dict(r, reads=r['sample:'+name]) for r in table] if table and 'sample:'+name in table[0] else []
    else:
        for name in names:
            path = counts_dir/'diagnostics/broad_counts'/(name+'.tsv.gz')
            if path.is_file():
                with gzip.open(path, 'rt') as handle:
                    yield name, list(csv.DictReader(handle, delimiter='\t'))
            else:
                yield name, []


def _tsv(path, rows, fields):
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows); atomic_write_text(path, buffer.getvalue())



def requires_read_support(event, sample):
    """Protect controls, sparse profiles, and calls admitted by relaxed cutoffs."""
    if (sample.get('sample_role') == 'normal'
        or (sample.get('median_reads_per_bin') or 0) < 20
        or (sample.get('zero_read_bin_fraction') or 0) > .05):
        return True
    from .cna_options import resolve_policy
    standard = resolve_policy()['calling']
    try:
        size = float(event.get('size_mb', (int(event['end'])-int(event['start'])+1)/1e6))
        effect = float(event['mean_log2'])
    except (KeyError, TypeError, ValueError):
        return True
    if not math.isfinite(size) or not math.isfinite(effect):
        return True
    return (size < standard['minimum_event_mb']
            or (effect < standard['gain'] if event['state'] in ('gain', 'amplification')
                else effect > standard['loss']))


def publish_guard(raw_dir, output_dir, counts_dir, caller, assessment, policy):
    """Publish accepted event/notation tables; retain raw inputs and all decisions."""
    raw_events = raw_dir/'cna_events.tsv'
    with raw_events.open() as handle:
        reader = csv.DictReader(handle, delimiter='\t'); fields = reader.fieldnames; events = list(reader)
    with (raw_dir/'cna_cytogenomic_notation.tsv').open() as handle:
        reader = csv.DictReader(handle, delimiter='\t'); notation_fields = reader.fieldnames; notation = list(reader)
    samples = {s['sample']:s for s in assessment['samples']}
    if any(e['sample'] not in samples for e in events):
        raise OncoTracerError('CNA event sample is absent from the coverage assessment')
    if any(s.get('sample_role') not in ('normal', 'tumor') for s in samples.values()):
        raise OncoTracerError('CNA evidence checks require explicit tumor/control roles')
    required_indices = {i for i, e in enumerate(events) if requires_read_support(e, samples[e['sample']])}
    required = {events[i]['sample'] for i in required_indices}
    decisions = {}
    for name, rows in raw_sample_rows(counts_dir, caller, sorted(required)):
        indices = [i for i,e in enumerate(events) if e['sample'] == name and i in required_indices]
        if indices:
            for i, decision in zip(indices, ReadSupport(rows).evaluate([events[i] for i in indices])):
                decisions[i] = decision
    accepted, audit = [], []
    for i, event in enumerate(events):
        decision = decisions.get(i, {'decision':'retained', 'reason':'standard_coverage_study_sample'})
        if decision['decision'] == 'retained': accepted.append(event)
        audit.append(dict(event, sample_role=samples[event['sample']].get('sample_role','unknown'), **decision))
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_fields = list(fields)+['sample_role','decision','reason','observed_reads','expected_reads','usable_bins',
                                'log2_ratio','lower_99','upper_99','approximate_p','approximate_q',
                                'covered_fraction','odd_log2','even_log2','supporting_blocks']
    _tsv(output_dir/'cna_evidence_decisions.tsv', audit, audit_fields)
    _tsv(output_dir/'cna_events.tsv', accepted, fields)
    for row in notation:
        selected = [e for e in accepted if e['sample']==row['sample']]
        prefix = row['molecular_cytogenomic_notation'].split(' ',1)[0]
        row.update(n_cna_events=len(selected),
                   molecular_cytogenomic_notation=prefix+' '+('; '.join(e['molecular_piece'] for e in selected) or 'no CNA retained after evidence checks'),
                   cna_shorthand=', '.join(e['cna_shorthand'] for e in selected) or 'no retained CNA; see evidence checks')
    _tsv(output_dir/'cna_cytogenomic_notation.tsv', notation, notation_fields)
    for name in ('input_cna_files.tsv','input_bed_files.tsv'):
        if (raw_dir/name).is_file(): atomic_write_text(output_dir/name,(raw_dir/name).read_text())
    counts = {name:{'retained':sum(e['sample']==name for e in accepted),
                   'rejected':sum(r['sample']==name and r['decision']=='rejected' for r in audit)} for name in samples}
    receipt = {'schema':POLICY['schema'],'cna_profile':policy['profile'],'policy':POLICY,
               'notice':'Heuristic support from the same reads; not independent confirmation or calibrated false-discovery control. Rejection does not establish a normal genome.',
               'raw_events_sha256':hashlib.sha256(raw_events.read_bytes()).hexdigest(),
               'samples':counts,'checked_samples':sorted(required),'raw_event_count':len(events),'retained_event_count':len(accepted)}
    atomic_write_json(output_dir/'cna_evidence_summary.json',receipt)
    body = '<!doctype html><meta charset="utf-8"><title>CNA read-support checks</title><h1>CNA read-support checks</h1><p>'+receipt['notice']+'</p><p><a href="cna_evidence_decisions.tsv">Every decision and reason</a> · <a href="diagnostics/unfiltered/cna_events.tsv">Unfiltered events</a></p><table><tr><th>Sample</th><th>Retained</th><th>Rejected</th></tr>'
    for name, values in counts.items():
        body += '<tr><td>'+html.escape(name)+'</td><td>'+str(values['retained'])+'</td><td>'+str(values['rejected'])+'</td></tr>'
    atomic_write_text(output_dir/'cna_evidence.html',body+'</table>')
    return receipt
