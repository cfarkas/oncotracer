#!/usr/bin/env python3
"""Rockfish R10.4.1 signal inference and reference-CpG adapters.

Map forward-read predictions through their matching primary alignments. No
missing CpG is imputed. Rockfish has a binary 5mC output, no separate 5hmC channel.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, hashlib, json, math, os
from pathlib import Path
import sqlite3, subprocess, sys

def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for part in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()

def file_info(path, hash_file=True):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            **({'sha256': sha256(path)} if hash_file else {})}

def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')

def preflight(args):
    import pod5
    import pysam
    counts = collections.Counter()
    read_ids = set()
    with pysam.AlignmentFile(args.bam, 'rb', check_sq=False) as bam:
        for read in bam:
            counts['bam_records'] += 1
            if read.is_secondary or read.is_supplementary:
                counts['non_primary_records'] += 1
                continue
            counts['primary_records'] += 1
            if read.query_name in read_ids:
                raise ValueError(f'Duplicate primary BAM read ID: {read.query_name}')
            read_ids.add(read.query_name)
            if not read.has_tag('mv') or not read.has_tag('ts'):
                counts['primary_records_missing_mv_or_ts'] += 1
            else:
                mv = read.get_tag('mv')
                if not mv or mv[0] <= 0:
                    counts['invalid_move_stride'] += 1
                else:
                    counts[f'move_stride_{mv[0]}'] += 1
                if any(v not in (0, 1) for v in mv[1:]):
                    counts['move_table_values_not_binary'] += 1
                if sum(mv[1:]) != read.query_length:
                    counts['move_count_sequence_length_mismatch'] += 1
    if not counts['primary_records']:
        raise ValueError('The input BAM has no primary records.')
    for key in ['primary_records_missing_mv_or_ts', 'invalid_move_stride',
                'move_table_values_not_binary', 'move_count_sequence_length_mismatch']:
        if counts[key]:
            raise ValueError(f'Rockfish prerequisites failed: {key}={counts[key]}. '
                             'Use a matched SUP basecall with --emit-moves.')
    pod5_paths = sorted(args.pod5.rglob('*.pod5')) if args.pod5.is_dir() else [args.pod5]
    if not pod5_paths:
        raise ValueError('No POD5 input files found.')
    pod5_info = []
    sample_rates = collections.Counter()
    for path in pod5_paths:
        with pod5.Reader(path) as reader:
            n = 0
            for record in reader.reads():
                sample_rates[str(record.run_info.sample_rate)] += 1
                n += 1
            pod5_info.append({**file_info(path), 'reads': n})
    if set(sample_rates) != {'5000'}:
        raise ValueError(f'Official model is 5kHz; input sample rates: {dict(sample_rates)}')
    return {'bam_counts': dict(counts), 'pod5_files': pod5_info,
            'pod5_sample_rates': dict(sample_rates)}

def export_predictions(args, prediction_path, outdir):
    import pysam
    database = outdir / 'rockfish_mapping.sqlite'
    if database.exists():
        database.unlink()
    conn = sqlite3.connect(database)
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    conn.execute('CREATE TABLE predictions (read_id TEXT, pos INTEGER, prob REAL, '
                 'mapped INTEGER DEFAULT 0, PRIMARY KEY(read_id,pos)) WITHOUT ROWID')
    counts = collections.Counter()
    with open(prediction_path) as handle:
        reader = csv.DictReader(handle, delimiter='\t')
        if reader.fieldnames != ['read_id', 'pos', 'prob']:
            raise ValueError(f'Unexpected Rockfish columns: {reader.fieldnames}')
        batch = []
        for row in reader:
            p = float(row['prob'])
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError(f'Invalid probability for read {row["read_id"]}')
            batch.append((row['read_id'], int(row['pos']), p))
            counts['raw_predictions'] += 1
            if len(batch) == 50000:
                conn.executemany('INSERT INTO predictions(read_id,pos,prob) VALUES(?,?,?)', batch)
                batch.clear()
        conn.executemany('INSERT INTO predictions(read_id,pos,prob) VALUES(?,?,?)', batch)
    conn.commit()
    conn.execute('CREATE TABLE qualified (contig TEXT, cpg_start INTEGER, strand TEXT, '
                 'prob REAL, confidence REAL, read_id TEXT)')
    fields = ['sample','method','read_id','parent_read_id','contig','position_0based',
              'canonical_cpg_start_0based','strand','read_position_0based',
              'aligned_query_position_0based','prob_methylated','prob_unmodified',
              'mapq','baseq','confidence','high_confidence','methylated_at_0_5',
              'passes_core_qc','exclusion_reason']
    calls_path = outdir / 'rockfish_per_read_calls.tsv.gz'
    read_audit_path = outdir / 'rockfish_read_audit.tsv.gz'
    reasons = collections.Counter()
    with pysam.FastaFile(str(args.reference)) as ref, \
         pysam.AlignmentFile(args.bam, 'rb', check_sq=False) as bam, \
         gzip.open(calls_path, 'wt') as out, gzip.open(read_audit_path, 'wt') as aud:
        writer = csv.DictWriter(out, delimiter='\t', fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        audit = csv.writer(aud, delimiter='\t', lineterminator='\n')
        audit.writerow(['sample','read_id','parent_read_id','raw_predictions','core_qc_predictions',
                        'high_confidence_predictions','read_status'])
        seen = set()
        for read in bam:
            if read.is_secondary or read.is_supplementary:
                continue
            rid = read.query_name
            if rid in seen:
                raise ValueError(f'Duplicate primary BAM read ID: {rid}')
            seen.add(rid)
            rows = conn.execute('SELECT pos,prob FROM predictions WHERE read_id=? ORDER BY pos', (rid,)).fetchall()
            parent_id = read.get_tag('pi') if read.has_tag('pi') else rid
            forward = read.get_forward_sequence() or ''
            qmap = read.get_reference_positions(full_length=True) if not read.is_unmapped else []
            qualities = read.query_qualities
            passed, confident = 0, 0
            for pos, prob in rows:
                qpos = len(forward) - 1 - pos if read.is_reverse else pos
                rpos = qmap[qpos] if 0 <= qpos < len(qmap) else None
                cpg = (rpos - 1 if read.is_reverse else rpos) if rpos is not None else None
                baseq = int(qualities[qpos]) if qualities is not None and 0 <= qpos < len(qualities) else None
                failures = []
                if read.flag & args.exclude_flags:
                    failures.append('excluded_alignment_flags')
                if read.mapping_quality < args.mapq:
                    failures.append('low_mapping_quality')
                if baseq is None or baseq < args.baseq:
                    failures.append('low_or_missing_base_quality')
                if forward[pos:pos+2].upper() != 'CG' or pos < 0:
                    failures.append('not_forward_read_CG')
                if rpos is None:
                    failures.append('no_reference_position')
                elif cpg < 0 or ref.fetch(read.reference_name, cpg, cpg+2).upper() != 'CG':
                    failures.append('not_reference_CpG')
                confidence = max(prob, 1-prob)
                hc = confidence >= args.confidence
                passed += not failures
                confident += not failures and hc
                for reason in failures:
                    reasons[reason] += 1
                writer.writerow(dict(zip(fields, [args.sample,'Rockfish',rid,parent_id,
                    read.reference_name or '',rpos if rpos is not None else '',
                    cpg if cpg is not None else '', '-' if read.is_reverse else '+',pos,qpos,
                    f'{prob:.9g}',f'{1-prob:.9g}',read.mapping_quality,
                    baseq if baseq is not None else '',f'{confidence:.9g}',int(hc),int(prob>=0.5),
                    int(not failures),';'.join(failures)])))
                if not failures:
                    conn.execute('INSERT INTO qualified VALUES(?,?,?,?,?,?)',
                                 (read.reference_name,cpg,'-' if read.is_reverse else '+',prob,confidence,rid))
            if rows:
                counts['reads_with_predictions'] += 1
                conn.execute('UPDATE predictions SET mapped=1 WHERE read_id=?', (rid,))
            else:
                counts['primary_reads_without_predictions'] += 1
            counts['core_qc_predictions'] += passed
            counts['high_confidence_predictions'] += confident
            counts['mapped_predictions'] += len(rows)
            audit.writerow([args.sample,rid,parent_id,len(rows),passed,confident,
                            'predicted' if rows else 'no_prediction'])
        conn.commit()
    orphan = outdir / 'rockfish_orphan_predictions.tsv.gz'
    with gzip.open(orphan, 'wt') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['sample','read_id','read_position_0based','prob_methylated'])
        for rid,pos,prob in conn.execute('SELECT read_id,pos,prob FROM predictions WHERE mapped=0'):
            writer.writerow([args.sample,rid,pos,prob])
            counts['predictions_without_primary_alignment'] += 1
    conn.execute('CREATE INDEX qualified_site ON qualified(contig,cpg_start)')
    site_path = outdir / 'rockfish_cpg_sites.tsv.gz'
    with gzip.open(site_path, 'wt') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['sample','method','contig','cpg_start_0based','cpg_end_0based',
                         'n_read_calls','n_high_confidence_calls','n_methylated_high_confidence',
                         'mean_probability','methylation_fraction_high_confidence','n_strands'])
        sql = ('SELECT contig,cpg_start,count(*),sum(confidence>=?), '
               'sum(confidence>=? AND prob>=0.5),avg(prob),count(DISTINCT strand) '
               'FROM qualified GROUP BY contig,cpg_start ORDER BY contig,cpg_start')
        for ctg,pos,n,nh,nm,mean,ns in conn.execute(sql,(args.confidence,args.confidence)):
            writer.writerow([args.sample,'Rockfish',ctg,pos,pos+2,n,nh,nm,f'{mean:.9g}',
                             f'{nm/nh:.9g}' if nh else '',ns])
            counts['distinct_observed_core_qc_cpg_dyads'] += 1
            counts['distinct_high_confidence_cpg_dyads'] += nh > 0
    counts['raw_predicted_reads'] = conn.execute('SELECT count(DISTINCT read_id) FROM predictions').fetchone()[0]
    conn.close()
    if not args.keep_database:
        database.unlink()
    result = {'counts':dict(counts), 'exclusion_reason_counts_nonexclusive':dict(reasons),
              'coordinate_convention':'0-based genomic coordinates; opposite strands collapsed to reference CG start',
              'probability_definition':'Binary 5mC probability from pretrained Rockfish; no separate 5hmC channel',
              'threshold': args.confidence, 'min_mapq':args.mapq, 'min_baseq':args.baseq,
              'exclude_flags':args.exclude_flags,
              'site_recovery_definition':'Distinct reference CpG dyads with >=1 measured, quality-qualified read prediction',
              'files':[file_info(p) for p in [calls_path,read_audit_path,orphan,site_path]]}
    write_json(outdir / 'rockfish_export_summary.json',result)
    return result


def export_native(args, output):
    """Retain reference coordinates and observed high-confidence read counts."""
    import pysam
    with gzip.open(output / 'rockfish_cpg_sites.tsv.gz', 'rt') as source, args.bedmethyl.open('w') as bed:
        for row in csv.DictReader(source, delimiter='\t'):
            n = int(row['n_high_confidence_calls'])
            if not n:
                continue
            m = int(row['n_methylated_high_confidence'])
            start = int(row['cpg_start_0based'])
            # Both strands collapse to the forward C; count each measured molecule once.
            values = [row['contig'], start, start + 1, 'm', min(n, 1000), '.',
                      start, start + 1, '0,0,0', n, 100 * m / n, m, n - m, 0, 0, 0, 0, 0]
            bed.write('\t'.join(map(str, values)) + '\n')
    with gzip.open(output / 'rockfish_per_read_calls.tsv.gz', 'rt') as source, \
         args.extract.open('w') as out, pysam.FastaFile(str(args.reference)) as ref:
        writer = csv.writer(out, delimiter='\t', lineterminator='\n')
        writer.writerow(['chrom','ref_position','mod_qual','mod_code','canonical_base',
                         'modified_primary_base','ref_kmer','ref_mod_strand'])
        for row in csv.DictReader(source, delimiter='\t'):
            if row['passes_core_qc'] != '1' or row['high_confidence'] != '1':
                continue
            pos = int(row['position_0based']); contig = row['contig']
            lo, hi = max(0, pos - 2), min(ref.get_reference_length(contig), pos + 3)
            kmer = 'N' * max(0, 2 - pos) + ref.fetch(contig, lo, hi).upper()
            kmer = kmer.ljust(5, 'N')
            writer.writerow([contig, pos, row['prob_methylated'], 'm', 'C', 'C', kmer, row['strand']])


def selected_signal_audit(args):
    """Every selected BAM read must have exactly one raw signal parent."""
    import pysam, pod5
    selected = set()
    with pysam.AlignmentFile(str(args.bam), 'rb', check_sq=False) as bam:
        for read in bam:
            if not read.is_secondary and not read.is_supplementary:
                selected.add(read.get_tag('pi') if read.has_tag('pi') else read.query_name)
    seen = set(); files = sorted({p for root in args.pod5 for p in root.rglob('*.pod5')})
    for path in files:
        with pod5.Reader(path) as source:
            for record in source.reads():
                rid = str(record.read_id)
                if rid not in selected:
                    continue
                if rid in seen:
                    raise ValueError('Duplicate selected raw read across POD5 batches: ' + rid)
                product = record.run_info.flow_cell_product_code.upper()
                if not product.startswith(('FLO-MIN114', 'FLO-PRO114', 'FLO-FLG114')):
                    raise ValueError('Pinned Rockfish model requires an R10.4.1 flow cell; found ' + product)
                if record.run_info.sample_rate != 5000:
                    raise ValueError('Pinned Rockfish model requires R10.4.1 signal sampled at 5000 Hz')
                seen.add(rid)
    if seen != selected:
        raise ValueError(f'Missing raw POD5 signals for {len(selected - seen)} selected read parents')
    return {'selected_signal_parents': len(selected), 'matched_signal_parents': len(seen)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['sample', 'bam', 'reference', 'model', 'source', 'output', 'bedmethyl', 'extract']:
        p.add_argument('--' + key, type=str if key == 'sample' else Path, required=True)
    p.add_argument('--pod5', type=Path, action='append', required=True)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--gpu', action='store_true')
    p.add_argument('--export-only', type=Path)
    args = p.parse_args()
    args.confidence, args.mapq, args.baseq, args.exclude_flags = .8, 20, 20, 3844
    args.keep_database = False
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = args.output / 'run.json'
    data = {'status': 'running', 'started_utc': utc_now(), 'model': file_info(args.model),
            'probability_definition': 'Binary 5mC; no separate 5hmC channel',
            'confidence_threshold': args.confidence, 'commands': []}
    write_json(receipt, data)
    try:
        # Check BAM moves once, and raw signal identity across all selected roots.
        data['signal_audit'] = selected_signal_audit(args)
        data['preflight'] = []
        for root in args.pod5:
            scoped = argparse.Namespace(**{**vars(args), 'pod5': root})
            data['preflight'].append(preflight(scoped))
        data['upstream_feature_error_messages'] = 0
        predictions = args.export_only or args.output / 'predictions.tsv'
        if args.export_only is None:
            env = dict(os.environ, PYTHONPATH=str(args.source), TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1')
            with predictions.open('w') as merged:
                merged.write('read_id\tpos\tprob\n')
                for index, root in enumerate(args.pod5):
                    part = args.output / f'predictions.{index}.tsv'
                    part.unlink(missing_ok=True)
                    command = [sys.executable, '-m', 'rockfish', 'inference', '-i', str(root), '-r',
                               '--bam_path', str(args.bam), '--model_path', str(args.model),
                               '-t', str(max(0, args.workers)), '-b', str(args.batch_size), '-o', str(part)]
                    if args.gpu:
                        command.extend(['-d', '0'])
                    data['commands'].append(command)
                    with (args.output / f'inference.{index}.log').open('w') as log:
                        subprocess.run(command, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
                    with (args.output / f'inference.{index}.log').open(errors='replace') as log:
                        data['upstream_feature_error_messages'] += sum(line.count('Cannot process read ') + line.count('Cannot extract read ') for line in log)
                    with part.open() as rows:
                        if rows.readline().rstrip() != 'read_id\tpos\tprob':
                            raise ValueError('Unexpected Rockfish output format')
                        for line in rows:
                            merged.write(line)
        data['export'] = export_predictions(args, predictions, args.output)
        if data['export']['counts'].get('predictions_without_primary_alignment', 0):
            raise ValueError('Rockfish emitted predictions outside the selected primary alignments')
        if data['upstream_feature_error_messages'] and not data['export']['counts'].get('raw_predictions', 0):
            raise ValueError('Rockfish returned no predictions after feature extraction errors; inspect inference logs')
        export_native(args, args.output)
        data.update(status='complete', finished_utc=utc_now(),
                    predictions=file_info(predictions), bedmethyl=file_info(args.bedmethyl), extract=file_info(args.extract))
    except Exception as error:
        data.update(status='failed', error=str(error))
        raise
    finally:
        write_json(receipt, data)


if __name__ == '__main__':
    main()
