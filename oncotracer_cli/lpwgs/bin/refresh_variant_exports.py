#!/usr/bin/env python3
"""Rebuild ClinVar exports from an unchanged, completed full variant table."""
import argparse
from collections import Counter
import csv
from datetime import datetime,timezone
import hashlib
import html
import json
from pathlib import Path
import tempfile
import gzip
import variant_evidence as v


def refresh(folder,sample):
    folder=Path(folder)
    summary_path=folder/f'{sample}.variant_evidence.json'
    summary=json.loads(summary_path.read_text())
    if summary['version']!=v.VERSION or summary['sample']!=sample:
        raise ValueError('Variant table sample/version does not match the export implementation')
    original_summary=hashlib.sha256(summary_path.read_bytes()).hexdigest()
    table=folder/f'{sample}.all_variants.tsv.gz'
    categories=Counter();clinvar=Counter();pathogenic=0;total=0;no_classification=0
    with tempfile.TemporaryDirectory(prefix='.export-',dir=folder) as tmp:
        tmp=Path(tmp)
        book=v.Workbook(tmp/f'{sample}.variant_review.xlsx',sample)
        with gzip.open(table,'rt',newline='') as source,gzip.open(tmp/f'{sample}.clinvar.tsv.gz','wt',newline='',compresslevel=1) as dest:
            reader=csv.DictReader(source,delimiter='\t')
            if reader.fieldnames!=v.FIELDS:raise ValueError('Unexpected full variant table schema')
            writer=csv.DictWriter(dest,fieldnames=v.FIELDS,delimiter='\t');writer.writeheader()
            for row in reader:
                total+=1;categories[row['category']]+=1
                if v.has_clinvar(row):
                    clinvar[row['category']]+=1;writer.writerow(row)
                    if row['clinvar_classification'] in {'','.'}:no_classification+=1
                    book.add('ClinVar_all',row)
                    book.add({'germline_supported':'Germline_supported','somatic_candidate':'Somatic_candidates','unconfirmed':'Unconfirmed'}[row['category']],row)
                if row['pathogenic_label']=='yes':pathogenic+=1;book.add('Pathogenic_LP',row)
        if total!=summary['unique_called_variants'] or dict(categories)!=summary['category_counts'] or pathogenic!=summary['pathogenic_label_variants']:
            raise ValueError('Full variant table and completed evidence summary disagree')
        summary['clinvar_by_category']=dict(clinvar)
        book.close(summary)
        (tmp/summary_path.name).write_text(json.dumps(summary,indent=2)+'\n')
        (tmp/f'{sample}.variant_evidence.html').write_text('<!doctype html><html><meta charset="utf-8"><title>Variant evidence</title><body><h1>'+html.escape(sample)+' variant evidence</h1><p>'+html.escape(summary['interpretation'])+'</p><pre>'+html.escape(json.dumps(summary,indent=2))+'</pre></body></html>')
        for file in tmp.iterdir():file.replace(folder/file.name)
    record={'updated_utc':datetime.now(timezone.utc).isoformat(),'operation':'Re-export every ClinVar-matched allele, including AlleleID matches without a germline clinical-significance label, from the completed full table. Genotype evidence, categories and pathogenic labels were not recomputed or changed.',
            'sample':sample,'all_variant_table':str(table.resolve()),'unique_called_variants':total,'category_counts':dict(categories),'clinvar_by_category':dict(clinvar),'clinvar_without_classification':no_classification,'pathogenic_labels':pathogenic,
            'original_summary_sha256':original_summary,'updated_summary_sha256':hashlib.sha256(summary_path.read_bytes()).hexdigest(),'export_implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'variant_evidence_implementation_sha256':hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()}
    (folder/f'{sample}.export_refresh.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',required=True,type=Path);parser.add_argument('--sample',required=True)
    args=parser.parse_args();print(json.dumps(refresh(args.evidence_dir,args.sample),indent=2))

if __name__=='__main__':main()
