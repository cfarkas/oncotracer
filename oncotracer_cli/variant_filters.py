"""Non-destructive joins for independent variant assessment tools."""
from __future__ import annotations

import csv
import gzip
from collections import Counter
from pathlib import Path

from .runtime import OncoTracerError


def records(path):
    with (gzip.open(path, 'rt') if str(path).endswith('.gz') else open(path)) as handle:
        for line in handle:
            if line.startswith('#'):
                yield line, None
            elif line.strip():
                fields = line.rstrip('\r\n').split('\t')
                if len(fields) < 8 or ',' in fields[4]:
                    raise OncoTracerError('Assessment requires normalized biallelic VCF records')
                yield line, fields


def key(fields):
    return (fields[0], int(fields[1]), fields[3], fields[4])


def add_assessment(source, destination, assessments, *, tag, description, table):
    """Keep every original allele, FILTER and sample field; append decisions.

    REAL means the assessment did not reject the allele. It never upgrades a
    caller's FILTER to PASS. Missing assessment is explicitly NOT_EVALUATED.
    """
    counts = Counter()
    with open(destination, 'w') as out, open(table, 'w', newline='') as evidence:
        writer = csv.writer(evidence, delimiter='\t')
        writer.writerow(['chrom', 'pos', 'ref', 'alt', 'decision', 'score', 'reason'])
        for line, fields in records(source):
            if fields is None:
                if line.startswith(f'##INFO=<ID={tag}_') or line.startswith(f'##FILTER=<ID={tag}_'):
                    raise OncoTracerError(f'{tag} annotations already exist on input')
                if line.startswith('#CHROM'):
                    out.write(f'##INFO=<ID={tag}_DECISION,Number=1,Type=String,Description="{description}; REAL does not establish somatic origin">\n')
                    out.write(f'##INFO=<ID={tag}_SCORE,Number=1,Type=Float,Description="Tool-specific score; see assessment evidence and provenance">\n')
                    for state in ('REJECTED', 'NOT_EVALUATED'):
                        out.write(f'##FILTER=<ID={tag}_{state},Description="{description}: {state}">\n')
                out.write(line)
                continue
            decision, score, reason = assessments.get(key(fields), ('NOT_EVALUATED', None, 'No matching assessment'))
            if decision not in {'REAL', 'REJECTED', 'NOT_EVALUATED'}:
                raise OncoTracerError(f'Invalid {tag} decision: {decision}')
            counts[decision] += 1
            info = [] if fields[7] == '.' else fields[7].split(';')
            info.append(f'{tag}_DECISION={decision}')
            if score is not None:
                info.append(f'{tag}_SCORE={score:.8g}')
            fields[7] = ';'.join(info)
            if decision != 'REAL':
                filters = [] if fields[6] in {'.', 'PASS'} else fields[6].split(';')
                fields[6] = ';'.join(dict.fromkeys([*filters, f'{tag}_{decision}']))
            out.write('\t'.join(fields) + '\n')
            writer.writerow([*key(fields), decision, score if score is not None else '.', reason])
    return dict(counts)


def evidence_identity(path):
    """Ignore assessment INFO/FILTER additions, never ignore GT or alleles."""
    import hashlib
    digest = hashlib.sha256()
    for _, fields in records(path):
        if fields:
            digest.update(('\t'.join(fields[:6] + fields[8:]) + '\n').encode())
    return digest.hexdigest()
