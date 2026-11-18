"""Exact-source regression against the pinned Figure-1 native scientific baseline.

This is deliberately separate from the historical v1.1/Nextflow audit. It never
labels changes to the scientific method as full parity with that old method.
"""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SCHEMA='oncotracer-native-science-audit-v1'
BASELINE='4b8a1d320360c16df210843bce906d2116a1551b'
BASELINE_SOURCE='08a981f0148e79768def515426e113d3d98940dd34032ee48466196f3e40d921'
SAMPLES={'quickstart1':{'illumina':['ERR12341627'],'ont':['DRR165691']},
         'quickstart2':{'hcc1143':['HCC1143_BEZ235','HCC1143_DMSO','HCC1143_TRAMETINIB']}}


def require(ok,message):
    if not ok:raise ValueError(message)


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):value.update(data)
    return value.hexdigest()


def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def read(path):return json.loads(Path(path).read_text())


def verify(audit,suite,candidate_sha,source_sha256,binary_sha256,check_checksums=True,write_summary=False):
    audit=Path(audit);record=read(audit/'native_science.json')
    require(record['schema']==SCHEMA and record['suite']==suite,'Wrong native scientific audit')
    require(set(record['datasets'])==set(SAMPLES[suite]),'Incomplete public datasets')
    expected={'baseline':(BASELINE,BASELINE_SOURCE,'3.0.0'),
              'candidate':(candidate_sha,source_sha256,'5.0.0')}
    for name,(commit,source,version) in expected.items():
        identity=read(audit/name/'provenance.json')
        require(identity['source_commit']==commit and identity['source_sha256']==source and
                identity['oncotracer_version']==version and identity['source_tree_dirty'] is False,
                'Scientific comparator provenance mismatch: '+name)
        if name=='candidate':require(identity['binary_sha256']==binary_sha256,'Candidate executable differs')
    files=record['files']
    observed={str(p.relative_to(audit)) for p in audit.rglob('*') if p.is_file() and
              not p.relative_to(audit).parts[0] in {'comparisons','native_science.json','audit_summary.json','SHA256SUMS'}}
    require(set(files)==observed,'Scientific evidence inventory is incomplete')
    require(files and all(not Path(p).is_absolute() and '..' not in Path(p).parts for p in files),'Invalid inventory')
    for relative,sha in files.items():
        path=audit/relative
        require(path.is_file() and not path.is_symlink() and digest(path)==sha,'Changed evidence: '+relative)
    require(record['input_manifest_sha256']==digest(audit/'inputs.json'),'Input manifest changed')
    inputs=read(audit/'inputs.json')
    require(inputs and all(r['bytes']>0 and len(r['sha256'])==64 for r in inputs),'Missing public FASTQ inputs')
    reports={}
    for dataset,names in SAMPLES[suite].items():
        qc=[]
        for side in ('baseline','candidate'):
            root=audit/side/dataset
            summary=read(root/'06_workflow_summary/workflow_summary.json')
            require(summary['engine']=='native' and summary['nextflow_used'] is False,'Unexpected engine')
            require(set(summary.get('input_samples',[]) or summary.get('samples',[]) or
                        summary.get('completed_samples',[])+summary.get('failed_samples',[]))==set(names),
                    'Wrong sample identities: '+dataset)
            assessment=read(root/'06_workflow_summary/cna_assessment.json')
            require({r['sample'] for r in assessment['samples']}==set(names),'Missing sample QC')
            qc.append({r['sample']:{k:r.get(k) for k in ('assessment','error','reason','cna_profile','bin_size_kb','sample_role','median_reads_per_bin','zero_read_bin_fraction')}
                       for r in assessment['samples']})
            with (root/'.oncotracer-native/trace.tsv').open() as handle:
                trace=list(csv.DictReader(handle,delimiter='\t'))
            require(trace and all(str(r.get('returncode'))=='0' for r in trace),'Failed scientific command')
            require('nextflow' not in (root/'.oncotracer-native/trace.tsv').read_text().lower(),'Unexpected Nextflow execution')
            require(record['datasets'][dataset][side+'_exit_code'] in (0,2),'Unexpected command exit')
            if dataset=='ont':
                require(record['datasets'][dataset][side+'_exit_code']==2,'Sparse ONT fixture should report QC limitation')
                require(all(r['assessment']=='not_assessable' for r in assessment['samples']),'Sparse ONT was promoted to an accepted call')
                event_path=root/'03_cna_codification/cna_events.tsv'
                if event_path.exists():
                    with event_path.open() as handle:
                        require(not list(csv.DictReader(handle,delimiter='\t')),'QC-failed sample has CNA events')
            else:
                require(record['datasets'][dataset][side+'_exit_code']==0 and summary['workflow_status']=='complete','Dense public dataset incomplete')
                require(all(r['assessment']=='assessable' for r in assessment['samples']),'Unexpected dense sample exclusion')
        require(qc[0]==qc[1],'Quality-assessment regression: '+dataset)
        if dataset!='ont':
            def events(side):
                with (audit/side/dataset/'03_cna_codification/cna_events.tsv').open() as handle:
                    return sorted((r['sample'],r['state'],r['chrom'],int(r['start']),int(r['end']),float(r['mean_log2']))
                                  for r in csv.DictReader(handle,delimiter='\t'))
            require(events('baseline')==events('candidate'),'Accepted CNA events changed: '+dataset)
            # Recompute, never trust a pre-existing passed boolean. The numerical
            # cutoffs are tighter than the historical v1.1 comparison.
            script=Path(__file__).with_name('compare_native_parity.py')
            temporary = tempfile.TemporaryDirectory()
            destination=(audit/'comparisons'/dataset) if write_summary else Path(temporary.name)
            command=[sys.executable,str(script),'--v1',str(audit/'baseline'/dataset),'--v2',str(audit/'candidate'/dataset),
                     '--outdir',str(destination),'--label',suite+' / '+dataset+' / frozen native 3.0',
                     '--expected-samples',','.join(names),'--minimum-event-recall','0.9999',
                     '--minimum-event-precision','0.9999','--minimum-profile-correlation','0.9999',
                     '--maximum-profile-median-absolute-difference','0.0001','--minimum-shared-bin-fraction','0.9999']
            subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
            reports[dataset]=read(destination/'parity_report.json')
            temporary.cleanup()
        else:reports[dataset]={'status':'expected_quality_limitation','assessments':qc[1]}
    if suite=='quickstart2':
        figure=read(audit/'candidate/hcc1143/09_cohort_figure/06_manifest.json')
        require(figure['status']=='complete' and figure['sample_count']==3 and
                set(figure['samples'])==set(SAMPLES[suite]['hcc1143']),'Missing complete cohort figure')
        require((audit/'candidate/hcc1143/09_cohort_figure/04_cohort_figure.pdf').read_bytes().startswith(b'%PDF'),'Invalid cohort figure')
    summary={'schema':SCHEMA,'suite':suite,'passed':True,'candidate_sha':candidate_sha,
             'source_sha256':source_sha256,'binary_sha256':binary_sha256,'baseline_commit':BASELINE,
             'baseline_source_sha256':BASELINE_SOURCE,'input_manifest_sha256':record['input_manifest_sha256'],
             'reports':reports}
    if write_summary:write(audit/'audit_summary.json',summary)
    else:
        # Reports include portable archive paths; identities/checks are compared
        # rather than absolute paths of the publisher's extraction directory.
        previous=read(audit/'audit_summary.json')
        require(all(previous[k]==summary[k] for k in ('schema','suite','passed','candidate_sha','source_sha256','binary_sha256','baseline_commit','baseline_source_sha256','input_manifest_sha256')),'Audit summary changed')
        for key in reports:
            require(previous['reports'][key].get('checks')==reports[key].get('checks'),'Recomputed comparison changed')
    return summary
