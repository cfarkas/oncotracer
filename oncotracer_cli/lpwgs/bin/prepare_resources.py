#!/usr/bin/env python3
"""Download PUBLIC resources only; patient data never leaves this machine."""
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size:
        print(f'Using {dest}', flush=True)
        return dest
    partial = dest.with_name(dest.name + '.part')
    print(f'Downloading public resource: {url}', flush=True)
    subprocess.run(['curl', '--silent', '--show-error', '--fail', '--location', '--retry', '3', '--connect-timeout', '30',
                    '--max-time', '1800', '--output', str(partial), url], check=True)
    partial.replace(dest)
    h = hashlib.sha256()
    with dest.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    dest.with_name(dest.name + '.source.json').write_text(json.dumps({
        'url': url, 'retrieved_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sha256': h.hexdigest(), 'bytes': dest.stat().st_size}, indent=2) + '\n')
    return dest


def prepare_ancestry(directory):
    directory = Path(directory)
    binary = download('https://github.com/brentp/somalier/releases/download/v0.3.5/somalier', directory/'somalier')
    binary.chmod(0o755)
    download('https://github.com/brentp/somalier/files/3412456/sites.hg38.vcf.gz', directory/'sites.hg38.vcf.gz')
    download('https://raw.githubusercontent.com/brentp/somalier/v0.3.5/scripts/ancestry-labels-1kg.tsv', directory/'labels.tsv')
    archive = download('https://zenodo.org/records/3479773/files/1kg.somalier.tar.gz?download=1', directory/'1kg.somalier.tar.gz')
    target = directory/'reference'
    if not (target/'complete.json').exists():
        target.mkdir(exist_ok=True)
        count = 0
        with tarfile.open(archive) as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith('.somalier'): continue
                dest = target/Path(member.name).name
                with tar.extractfile(member) as source, dest.open('wb') as out: shutil.copyfileobj(source, out)
                count += 1
        if count < 100: raise ValueError('Reference archive has fewer than 100 sketches')
        (target/'complete.json').write_text(json.dumps({'sketches': count, 'archive': str(archive)}))


def prepare_gwas(directory):
    directory = Path(directory)
    archive = download('https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/gwas-catalog-associations_ontology-annotated-full.zip', directory/'catalog.zip')
    target = directory/'associations.GRCh38.tsv.gz'
    if target.exists(): return
    fields = ['chrom','pos','rsid','trait','reported_risk_allele','pvalue','reported_or_beta',
              'confidence_interval','pubmed','study','study_population','mapped_trait','allele_orientation']
    total = kept = 0
    with zipfile.ZipFile(archive) as z:
        files = [n for n in z.namelist() if not n.endswith('/') and not n.startswith('__MACOSX')]
        if len(files) != 1: raise ValueError(f'Unexpected catalog archive members: {files}')
        with z.open(files[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig') as source, gzip.open(target.with_suffix('.tmp'), 'wt') as out:
            reader = csv.DictReader(source, delimiter='\t')
            required = {'CHR_ID','CHR_POS','DISEASE/TRAIT','P-VALUE','STRONGEST SNP-RISK ALLELE'}
            if not required.issubset(reader.fieldnames): raise ValueError(f'Unexpected GWAS headers: {reader.fieldnames}')
            writer = csv.DictWriter(out, fieldnames=fields, delimiter='\t'); writer.writeheader()
            for row in reader:
                total += 1
                chrom, pos = row['CHR_ID'].strip(), row['CHR_POS'].strip()
                try: pvalue = float(row['P-VALUE'])
                except ValueError: continue
                # Single autosomal locus; multi-SNP/haplotype associations cannot be interpreted here.
                if chrom not in {str(n) for n in range(1,23)} or not pos.isdigit() or int(pos)<1 or pvalue>5e-8: continue
                risk = row['STRONGEST SNP-RISK ALLELE'].strip()
                rsid = row.get('SNPS','').strip()
                if not rsid.startswith('rs') or not rsid[2:].isdigit(): continue
                writer.writerow(dict(zip(fields, [chrom,pos,rsid,row['DISEASE/TRAIT'],risk,
                    row['P-VALUE'],row.get('OR or BETA',''),row.get('95% CI (TEXT)',''),row.get('PUBMEDID',''),
                    row.get('STUDY ACCESSION',''),row.get('INITIAL SAMPLE SIZE',''),row.get('MAPPED_TRAIT',''),
                    'unverified_catalog_allele_do_not_infer_dosage'])))
                kept += 1
    target.with_suffix('.tmp').replace(target)
    (directory/'catalog_summary.json').write_text(json.dumps({'assembly':'GRCh38','input_rows':total,
        'retained_associations':kept,'pvalue_max':5e-8,'source_archive':str(archive),
        'interpretation':'Locus associations only. Reported alleles are not harmonized; no risk score or effect dosage.'},indent=2)+'\n')
    print(f'GWAS: retained {kept:,} single-locus genome-wide-significant associations.',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gwas',action='store_true'); p.add_argument('--ancestry',action='store_true')
    p.add_argument('--directory',type=Path,default=ROOT/'resources')
    a=p.parse_args()
    if not a.gwas and not a.ancestry: p.error('Select --gwas and/or --ancestry')
    if a.gwas: prepare_gwas(a.directory/'gwas')
    if a.ancestry: prepare_ancestry(a.directory/'ancestry')

if __name__=='__main__': main()
