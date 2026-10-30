"""Disk-backed whole-genome assessment using the same fragment-linkage model.

All eligible pairs are selected before inspecting their BAM alleles. Windows
only bound memory: the BY family is the entire invocation, not each window.
"""
from collections import Counter
import csv
import gzip
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sqlite3

import haplotype_evidence as h

PAIR_FIELDS = ['variant_a','variant_b','chrom','distance_bp','fragments','ref_ref','ref_alt',
               'alt_ref','alt_alt','strata','mc_linkage_p','mc_ci95_low','mc_ci95_high',
               'mc_exceedances','mc_simulations','exact_linkage_p','linkage_q_by','status',
               'input_phase_expectation','input_phase_is_independent_truth',
               'ffpe_damage_context','germline_probability']
AUDIT_FIELDS = ['variant','chrom','pos','ref','alt','filter','status']


def bounded_windows(db,chrom,start,end,max_distance,max_pairs):
    """Split dense anchor intervals instead of dropping pairs or truncating a VCF."""
    pending=[(start,end)]
    while pending:
        left,right=pending.pop()
        selected=db.execute('SELECT id,data FROM variants WHERE eligible=1 AND chrom=? AND pos>=? AND pos<? ORDER BY pos',
                            (chrom,left,right+max_distance)).fetchall()
        ids=[ident for ident,_ in selected]
        variants=[json.loads(data) for _,data in selected]
        pairs=[]
        for i,first in enumerate(variants):
            if first['pos']>=right:break
            for j in range(i+1,len(variants)):
                if variants[j]['pos']-first['pos']>max_distance:break
                pairs.append((i,j))
                if len(pairs)>max_pairs:break
            if len(pairs)>max_pairs:break
        if len(pairs)>max_pairs:
            if right-left<=1:raise ValueError('One variant exceeds --max-pairs; increase the explicit memory limit')
            middle=(left+right)//2
            pending.extend([(middle,right),(left,middle)])
        elif pairs:
            yield ids,variants,pairs


def adjust_database(db):
    """Same BY correction as the small-region command, with a disk sort."""
    from scipy.special import digamma
    m = db.execute('SELECT count(*) FROM pairs WHERE p IS NOT NULL').fetchone()[0]
    harmonic = float(digamma(m+1)) + 0.5772156649015329 if m else 0.
    bound = 1.
    rank = m
    pending = []
    for ident, p in db.execute('SELECT id,p FROM pairs WHERE p IS NOT NULL ORDER BY p DESC,id DESC'):
        bound = min(bound, p*m*harmonic/rank)
        pending.append((bound, ident))
        rank -= 1
        if len(pending) == 10000:
            db.executemany('UPDATE pairs SET q=? WHERE id=?', pending)
            pending.clear()
    db.executemany('UPDATE pairs SET q=? WHERE id=?', pending)
    db.commit()


def run_genome(a, inputs, before, out):
    import numpy
    import pysam
    import scipy
    if a.varsome or a.llm_report:
        raise ValueError('Whole-genome linkage is offline; use the regional command for bounded online annotation')
    counts = Counter()
    with h.output_work(out) as work:
        db = sqlite3.connect(work/'assessment.sqlite')
        db.execute('PRAGMA cache_size=-16384')
        db.execute('PRAGMA temp_store=FILE')
        db.executescript('''
            CREATE TABLE variants(id INTEGER PRIMARY KEY, variant TEXT UNIQUE NOT NULL,
                chrom TEXT NOT NULL,pos INTEGER NOT NULL,eligible INTEGER NOT NULL,
                paired INTEGER DEFAULT 0, tested INTEGER DEFAULT 0,data TEXT NOT NULL);
            CREATE INDEX positions ON variants(eligible,chrom,pos);
            CREATE UNIQUE INDEX eligible_sites ON variants(chrom,pos) WHERE eligible=1;
            CREATE TABLE pairs(id INTEGER PRIMARY KEY,p REAL,q REAL,data TEXT NOT NULL);
            CREATE INDEX probabilities ON pairs(p,id);
        ''')
        with pysam.AlignmentFile(str(inputs[0]), 'rb', threads=a.threads) as bam, pysam.FastaFile(str(inputs[2])) as fasta:
            if not bam.has_index() or bam.header.to_dict().get('HD',{}).get('SO') != 'coordinate':
                raise ValueError('A coordinate-sorted BAM with a completed index is required')
            lengths = dict(zip(bam.references,bam.lengths))
            for n,row in enumerate(h.iter_variant_records(inputs[1],a.sample,lengths,fasta),1):
                try:
                    db.execute('INSERT INTO variants(variant,chrom,pos,eligible,data) VALUES(?,?,?,?,?)',
                               (row['variant'],row['chrom'],row['pos'],int(row['status']=='eligible'),json.dumps(row)))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('Duplicate variant or multiple eligible alleles at one site: '+row['variant']) from exc
                if n % 10000 == 0:
                    db.commit()
            db.commit()
            windows = db.execute('SELECT DISTINCT chrom, (pos-1)/100000 FROM variants WHERE eligible=1 ORDER BY chrom,pos').fetchall()
            batches=(batch for chrom,window in windows
                     for batch in bounded_windows(db,chrom,window*100000+1,(window+1)*100000+1,a.max_distance,a.max_pairs))
            for ids,variants,pairs in batches:
                tables = h.fragment_tables(bam,variants,pairs,a.min_mapq,a.min_baseq)
                rows = h.evaluate(variants,pairs,tables,simulations=a.simulations,seed=a.seed,
                                  min_fragments=a.min_fragments,ffpe=a.ffpe,adjust=False)
                paired,tested = set(),set()
                for (i,j),row in zip(pairs,rows):
                    db.execute('INSERT INTO pairs(p,data) VALUES(?,?)',(row['exact_linkage_p'],json.dumps(row)))
                    paired.update((ids[i],ids[j]))
                    if row['exact_linkage_p'] is not None:
                        tested.update((ids[i],ids[j]))
                db.executemany('UPDATE variants SET paired=1 WHERE id=?',((i,) for i in paired))
                db.executemany('UPDATE variants SET tested=1 WHERE id=?',((i,) for i in tested))
                db.commit()
        adjust_database(db)
        with gzip.open(work/f'{a.sample}.haplotype_pairs.tsv.gz','wt',newline='') as stream:
            writer = csv.DictWriter(stream,PAIR_FIELDS,delimiter='\t',extrasaction='ignore')
            writer.writeheader()
            for data,q in db.execute('SELECT data,q FROM pairs ORDER BY id'):
                row = json.loads(data)
                if q is not None:
                    row['linkage_q_by'] = q
                    row['status'] = 'cis_linkage_supported_under_null' if q<=.05 and row['alt_alt']>=2 else 'no_significant_cis_linkage'
                counts[row['status']] += 1
                writer.writerow(row)
        with gzip.open(work/f'{a.sample}.variant_assessability.tsv.gz','wt',newline='') as stream:
            writer = csv.DictWriter(stream,AUDIT_FIELDS,delimiter='\t',extrasaction='ignore')
            writer.writeheader()
            for data,paired,tested in db.execute('SELECT data,paired,tested FROM variants ORDER BY id'):
                row=json.loads(data)
                if row['status']=='eligible':
                    row['status']='has_assessable_pair' if tested else 'insufficient_fragment_evidence' if paired else 'no_linkable_variant_in_distance'
                writer.writerow(row)
        records,eligible=db.execute('SELECT count(*),coalesce(sum(eligible),0) FROM variants').fetchone()
        db.close()
        (work/'assessment.sqlite').unlink()
        summary=dict(schema=h.METHOD,sample=a.sample,status='complete',statistics_status='complete',
                     inputs=before,eligible_snvs=eligible,input_records=records,pairs=sum(counts.values()),
                     pair_status_counts=dict(counts),parameters=vars(a),limitation=h.LIMITATION,
                     software=dict(numpy=numpy.__version__,scipy=scipy.__version__,pysam=pysam.__version__,rng='PCG64'),
                     code_sha256={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
                                  for n in ('haplotype_genome.py','haplotype_evidence.py')},
                     multiple_testing='Benjamini-Yekutieli across all assessable candidate pairs in this invocation',
                     window_bp=100000,missing_linkage_is_not_evidence_against_variant=True,
                     germline_probability=None,calibrated_for_low_pass=False,online_annotation_status='offline')
        (work/f'{a.sample}.haplotype_evidence.json').write_text(json.dumps(summary,indent=2)+'\n')
        links=''.join(f'<li><a href="{html.escape(p.name)}">{html.escape(p.name)}</a></li>' for p in sorted(work.glob('*.tsv.gz')))
        (work/f'{a.sample}.haplotype_evidence.html').write_text('<!doctype html><meta charset="utf-8">'
            f'<title>{html.escape(a.sample)} — read-backed linkage</title><h1>{html.escape(a.sample)} — read-backed linkage</h1>'
            f'<p>{html.escape(h.LIMITATION)}</p><p>{records:,} input records; {eligible:,} eligible SNVs; '
            f'{sum(counts.values()):,} pairs. All records are retained in compressed TSV files.</p>'
            '<pre>'+html.escape(json.dumps(dict(counts),indent=2))+'</pre><ul>'+links+'</ul>'
            '<p>Insufficient evidence is not evidence against a variant. P-values test conditional linkage; '
            'they are not germline or pathogenicity probabilities. Correction spans all assessable pairs in this report.</p>')
        if before != [h.fingerprint(p,digest=p==inputs[1]) for p in inputs]:
            raise ValueError('An input changed during assessment; results were not published')
        artifacts={p.name:h.fingerprint(p,True)['sha256'] for p in work.iterdir() if p.is_file()}
        for path in work.iterdir():
            os.replace(path,out/path.name)
        (out/f'{a.sample}.manifest.json').write_text(json.dumps(dict(schema=h.METHOD,files=artifacts),indent=2)+'\n')
    print(json.dumps(dict(sample=a.sample,pairs=sum(counts.values()),statuses=dict(counts),outdir=str(out))))
    return 0
