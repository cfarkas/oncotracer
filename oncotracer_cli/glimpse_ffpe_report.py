"""Expose final FFPE decisions and visibly label reports produced beforehand."""
from __future__ import annotations
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from urllib.parse import quote
from .runtime import atomic_write_json, atomic_write_text, sha256_file

BEGIN = '<!-- ONCOTRACER_FFPE_REVIEW_BEGIN -->'
END = '<!-- ONCOTRACER_FFPE_REVIEW_END -->'
EXCLUDED = {'work', 'pipeline_info', 'resources', 'cache', 'logs', 'ffpe_review'}


def _relative(root, page, target):
    target = Path(os.path.abspath(target))
    # Paths remain local to this sample; an unrelated path is never linked.
    if not target.is_relative_to(root):
        return None
    return quote(os.path.relpath(target, page.parent), safe='/._-')


def _ordinary_parent(path, root):
    return not any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root))


def _cover_pdf(destination, lines):
    """Small standalone text-only PDF using standard Helvetica, no dependencies."""
    commands = ['BT', '/F1 17 Tf', '48 790 Td']
    first = True
    for line in lines:
        for wrapped in textwrap.wrap(str(line), width=86) or ['']:
            if not first:
                commands.extend(['0 -21 Td', '/F1 10 Tf'])
            escaped = wrapped.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append('(' + escaped + ') Tj'); first = False
    commands.append('ET')
    content = '\n'.join(commands).encode('ascii', 'replace')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    data = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += str(n).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start = len(data); data += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets[1:]: data += f'{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    destination.write_bytes(data)


def _mark_pdfs(root, directory, sample, vote, pdfs):
    binary = shutil.which('pdfunite')
    manifest = directory/'pdf_covers.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    saved = dict(previous); updated, unavailable = [], []
    signature = hashlib.sha256(json.dumps(vote.get('counts', {}), sort_keys=True).encode()).hexdigest()
    for pdf in pdfs:
        rel = str(pdf.relative_to(root))
        if not binary:
            unavailable.append({'path': rel, 'reason': 'pdfunite unavailable; PDF linked explicitly as previous report'})
            continue
        try:
            current = sha256_file(pdf)
            prior = previous.get(rel, {})
            if prior.get('covered_sha256') == current and prior.get('vote_signature') == signature:
                updated.append(rel); continue
            if prior.get('covered_sha256') == current and Path(prior.get('original_path', '')).is_file():
                original = Path(prior['original_path'])
                if sha256_file(original) != prior.get('original_sha256'):
                    raise ValueError('Stored original PDF checksum mismatch')
            else:
                originals = directory/'prior_pdf_sources'; originals.mkdir(exist_ok=True)
                original = originals/(hashlib.sha256(rel.encode()).hexdigest()[:12]+'-'+current[:12]+'.pdf')
                if not original.exists(): shutil.copy2(pdf, original)
            with tempfile.TemporaryDirectory(prefix='.pdf-cover-', dir=directory) as temporary:
                cover, merged = Path(temporary)/'cover.pdf', Path(temporary)/'covered.pdf'
                counts = vote.get('counts', {})
                _cover_pdf(cover, ['FFPE REVIEW REQUIRED', '', 'Sample: '+sample,
                    'This document was generated BEFORE the final FFPErase / GLIMPSE2 evidence adjudication.',
                    'It must not be used as the final variant acceptance report.', '',
                    'Final decisions: ffpe_review/START_HERE.html in the sample output folder.',
                    'All alleles, accepted candidates and evidence TSV are linked there.',
                    'FFPE-flagged excluded candidates: '+str(counts.get('ffpe_filtered', 0)),
                    'Retained germline research rescue candidates: '+str(counts.get('germline_rescue_candidate', 0)), '',
                    'Imputation is not independent confirmation and does not establish somatic origin.',
                    'Candidate rescue preserves FFPE flags and is not a clinical diagnosis.', '',
                    'The following pages are the previous report, retained for traceability.'])
                subprocess.run([binary, str(cover), str(original), str(merged)], check=True, capture_output=True)
                os.replace(merged, pdf)  # Replace local symlink; never modify its target.
            saved[rel] = {'original_path': str(original), 'original_sha256': sha256_file(original),
                          'covered_sha256': sha256_file(pdf), 'vote_signature': signature}
            updated.append(rel)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            unavailable.append({'path': rel, 'reason': str(error)[:400]})
    atomic_write_json(manifest, saved)
    return updated, unavailable


def render_ffpe_review(sample_output, sample, vote, *, annotate_existing=True):
    """Return review index and audit of HTML banners / PDF warning covers.

    All prior HTML is marked visibly. PDFs receive a warning cover when
    pdfunite is available, while their unmodified originals remain in the
    review audit folder. If PDF marking is unavailable the result reports it.
    """
    root = Path(os.path.abspath(sample_output))
    if root.is_symlink():
        raise ValueError('Sample output root must be an ordinary local directory')
    directory = root/'ffpe_review'
    if directory.is_symlink():
        raise ValueError('FFPE review directory must not be a symlink')
    directory.mkdir(parents=True, exist_ok=True)
    index = directory/'START_HERE.html'
    htmls, pdfs, skipped = [], [], []
    for suffix, selected in (('*.html', htmls), ('*.pdf', pdfs)):
        for path in root.rglob(suffix):
            if any(p in EXCLUDED for p in path.relative_to(root).parts[:-1]):
                continue
            if not _ordinary_parent(path, root):
                skipped.append(str(path.relative_to(root))); continue
            selected.append(path)
    annotated = []
    if annotate_existing:
        for path in htmls:
            link = _relative(root, path, index)
            banner = (BEGIN+'<aside id="oncotracer-ffpe-adjudication" role="note" style="padding:18px;margin:12px;'
                      'border:2px solid #a76700;background:#fff2cc;color:#382300;font:16px/1.5 sans-serif">'
                      '<strong>Informe previo a adjudicación FFPE.</strong> Consultar las '
                      f'<a href="{html.escape(link, quote=True)}">decisiones finales de FFPErase + GLIMPSE2</a>. '
                      'Los PDF enlazados conservan el informe previo y no sustituyen la revisión final de variantes.'
                      '</aside>'+END)
            content = path.read_text(encoding='utf-8')
            content = re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END), '', content, flags=re.S)
            match = re.search(r'<body\b[^>]*>', content, flags=re.I)
            if match:
                content = content[:match.end()] + banner + content[match.end():]
            else:
                content = banner + content
            atomic_write_text(path, content)
            annotated.append(str(path.relative_to(root)))
    covered, unresolved = _mark_pdfs(root, directory, sample, vote, pdfs) if annotate_existing else ([], [])
    counts = vote.get('counts', {})
    decisions = ''.join(f'<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>'
                        for k, v in sorted(counts.items()))
    links = []
    for key, title in [('accepted_vcf', 'Candidates retained by the FFPE rule (VCF)'),
                       ('all_vcf', 'All variants, including excluded candidates and flags (VCF)'),
                       ('evidence_tsv', 'Exact-allele decisions and evidence (TSV)')]:
        target = vote.get(key)
        link = _relative(root, index, target) if target else None
        links.append('<li>'+ (f'<a href="{html.escape(link, quote=True)}">{title}</a>' if link else html.escape(title+': outside this sample folder or unavailable'))+'</li>')
    prior = ''.join(f'<li><a href="{html.escape(_relative(root, index, p), quote=True)}">{html.escape(str(p.relative_to(root)))}</a> — report before FFPE adjudication</li>' for p in pdfs)
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(sample)} — final FFPE review</title>
<style>body{{font:17px/1.55 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#172b3a}}h1{{color:#13596d}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #c8d5dc;text-align:left}}.notice{{background:#fff2cc;border-left:5px solid #b67300;padding:18px}}a{{color:#075da4}}code{{overflow-wrap:anywhere}}</style></head><body>
<h1>{html.escape(sample)}: final FFPE evidence review</h1>
<p>This page contains the final FFPE selection decisions. Earlier interpretation reports have not been regenerated from the filtered set.</p>
<p class="notice">Retained means selected by this research rule. It does not establish a confirmed genotype, somatic origin, or a clinical diagnosis. GLIMPSE2 and FFPErase share sequencing evidence and are not independent validation.</p>
<h2>Variant files</h2><ul>{''.join(links)}</ul>
<h2>Decisions</h2><table><thead><tr><th>Evidence category</th><th>Records</th></tr></thead><tbody>{decisions}</tbody></table>
<h2>How the decision is made</h2>
<p>FFPErase-flagged variants are excluded from the retained set unless the exact alternate allele has an imputed genotype posterior of at least 0.99, imputation INFO of at least 0.90, at least 3 alternate reads at depth 8 or greater, a compatible read allele fraction, and an original caller PASS. Such rescues remain germline research candidates, keep the FFPE flag, and are excluded from the population-ready set.</p>
<p>Missing imputation or an imputed reference genotype does not demonstrate a somatic mutation or a sequencing artifact. Imputation-only records are not submitted to FFPErase. Missing FFPErase assessment remains explicit; it is not a real-variant prediction. Low-pass FFPE performance requires benchmarking; the FFPErase publication used a median FFPE depth of 51×.</p>
<h2>Previous reports</h2><p>The documents below were generated before final FFPE adjudication. Consult the final VCF and evidence table for inclusion or exclusion decisions.</p><ul>{prior or '<li>No prior PDF reports found.</li>'}</ul>
<h2>Methods</h2><p><a href="https://www.nature.com/articles/s41467-025-65654-7">FFPErase study</a> · <a href="https://www.nature.com/articles/s41588-023-01438-3">GLIMPSE2 study</a></p>
</body></html>'''
    atomic_write_text(index, document)
    result = {'index': str(index), 'annotated_html': annotated, 'pdfs_with_warning_cover': covered,
              'pdfs_prior_to_adjudication': [str(p.relative_to(root)) for p in pdfs],
              'pdf_warning_failures': unresolved, 'skipped_symlink_directories': skipped,
              'limitation': 'Prior narrative/PDF reports are visibly marked but not recomputed; final FFPE decisions are in this review and linked VCF/TSV.'}
    atomic_write_json(directory/'report_adjudication_audit.json', result)
    return result
