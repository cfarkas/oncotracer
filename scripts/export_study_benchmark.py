#!/usr/bin/env python3
"""Export aggregate, validated study timings without patient identifiers or paths."""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import tarfile

GIB=1024**3


def sha(path):
    with Path(path).open('rb') as handle:
        digest=hashlib.sha256()
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
        return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def seconds(value):
    if value in (None,'','-'):return None
    text=str(value).strip()
    if text=='0':return 0.
    terms=re.findall(r'(\d+(?:\.\d+)?)\s*(ms|d|h|m|s)',text)
    if not terms:raise ValueError('Unrecognized duration: '+text)
    scales={'ms':.001,'s':1,'m':60,'h':3600,'d':86400}
    return sum(float(n)*scales[u] for n,u in terms)


def memory_bytes(value):
    if value in (None,'','-'):return None
    if str(value).strip()=='0':return 0.
    match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KMGT]?B)',str(value).strip())
    if not match:raise ValueError('Unrecognized memory: '+str(value))
    return float(match[1])*1024**(['B','KB','MB','GB','TB'].index(match[2]))


def timestamp(text):
    value=dt.datetime.fromisoformat(text.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Native trace lacks explicit timezone')
    return value.timestamp()


def interval_union(intervals):
    intervals=sorted(intervals)
    if not intervals:return 0.
    if any(b<a for a,b in intervals):raise ValueError('Negative stage duration')
    start,end=intervals[0];total=0.
    for a,b in intervals[1:]:
        if b<a:raise ValueError('Negative stage duration')
        if a>end:total+=end-start;start,end=a,b
        else:end=max(end,b)
    return total+end-start


def normalize_stage(stage,samples):
    for sample in sorted(samples,key=len,reverse=True):
        stage=re.sub(r'(^|-)'+re.escape(sample)+r'(?=-|$)',r'\1SAMPLE',stage)
    stage=stage.replace('-SAMPLE','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',stage):raise ValueError('Unsafe aggregate stage name')
    if any(re.search(r'(^|-)'+re.escape(s)+r'(-|$)',stage) for s in samples):
        raise ValueError('Sample identifier remains in stage')
    return stage


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')


def write_csv(path,rows,fields=None):
    fields=fields or list(rows[0])
    with Path(path).open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,lineterminator="\n");writer.writeheader();writer.writerows(rows)


def percentile(values,fraction):
    if not values:return None
    values=sorted(values);index=(len(values)-1)*fraction;lo=int(index);hi=math.ceil(index)
    return values[lo]+(values[hi]-values[lo])*(index-lo)


def aggregate_intervals(cohort,layer,rows,origin):
    groups=collections.defaultdict(list)
    for row in rows:groups[row['stage']].append(row)
    result=[]
    for stage,items in sorted(groups.items()):
        intervals=[(r['start'],r['end']) for r in items]
        lengths=[b-a for a,b in intervals]
        if min(lengths)<0:raise ValueError('Negative trace interval')
        result.append(dict(cohort=cohort,layer=layer,stage=stage,commands=len(items),
            returncode_zero=sum(r['returncode']==0 for r in items),nonzero_returncodes=sum(r['returncode']!=0 for r in items),
            first_start_hours=round((min(a for a,b in intervals)-origin)/3600,6),
            last_finish_hours=round((max(b for a,b in intervals)-origin)/3600,6),
            span_hours=round((max(b for a,b in intervals)-min(a for a,b in intervals))/3600,6),
            active_union_hours=round(interval_union(intervals)/3600,6),
            summed_command_hours=round(sum(lengths)/3600,6),
            median_command_seconds=round(statistics.median(lengths),3),
            p95_command_seconds=round(percentile(lengths,.95),3),max_command_seconds=round(max(lengths),3)))
    return result


def export(root,recording,out,gpu_model=None):
    root=Path(root).resolve();recording=Path(recording).resolve();out=Path(out).resolve()
    if root==out or root.is_relative_to(out):raise ValueError('Export directory overlaps study root')
    manifest=load(recording/'run_manifest.json');finished=load(root/'validation/final_status.json')
    if finished.get('status')!='all_study_outputs_validated' or finished.get('source_commit')!=manifest['source_commit']:
        raise ValueError('All study outputs must have passed validation for this source commit')
    resources=load(recording/'resource_summary.json')
    if resources['source_commit']!=manifest['source_commit']:raise ValueError('Resource/source mismatch')
    out.mkdir(parents=True,exist_ok=True)
    evidence=[]
    def record(path,role,cohort='all'):
        evidence.append(dict(cohort=cohort,role=role,source_sha256=sha(path),bytes=Path(path).stat().st_size))
    for path,role in [(recording/'run_manifest.json','run_manifest'),(recording/'resource_summary.json','resource_summary'),(root/'validation/final_status.json','final_validation')]:record(path,role)
    cohorts=[];stages=[];tasks=[];all_samples=[];qc={};validation=[]
    origin=timestamp(manifest['created_utc'])
    for job in manifest['jobs']:
        name=job['project'];samples=job['sample_names'];all_samples+=samples
        project=root/name
        proof=load(root/'validation'/(name+'.outputs.json'))
        if proof['status']!='passed' or proof['source_commit']!=manifest['source_commit'] or proof['source_sha256']!=manifest['source_sha256']:
            raise ValueError('Cohort validation/source mismatch: '+name)
        record(root/'validation'/(name+'.outputs.json'),'full_output_validation',name)
        summary_path=project/'results/06_workflow_summary/workflow_summary.json';summary=load(summary_path);record(summary_path,'workflow_summary',name)
        r=resources['studies'][name]
        if r['sample_count']!=len(samples):raise ValueError('Sample count differs between run and resource summary')
        primary=collections.Counter(summary.get('cna_sample_statuses',{}).values())
        rescues=collections.Counter(summary.get('cna_rescue_sample_statuses',{}).values())
        qc[name]=dict(primary_cna=dict(primary),cna_recovery=dict(rescues),
            variant_completed=len(summary.get('variant_completed_samples',[])),variant_failed=len(summary.get('variant_failed_samples',[])),
            methylation_completed=len(summary.get('methylation_completed_samples',[])),
            methylation_classifiers=(dict(collections.Counter(v['classifier'] for v in job.get('expected_methylation',{}).values()))))
        selected_path=project/'results/06_workflow_summary/selected_cna_assessment.json'
        if selected_path.exists():
            selected=load(selected_path);record(selected_path,'selected_cna_assessment',name)
            qc[name]['selected_cna_status']=dict(collections.Counter(s['status'] for s in selected.get('samples',[])))
        validation.append(dict(cohort=name,status=proof['status'],workflow_status=proof['workflow_status'],qc_limited=proof['qc_limited'],
            checked_files=proof['checked_file_count'],checked_bytes=proof['checked_bytes'],
            validator_sha256=proof['validator_sha256'],source_commit=proof['source_commit'],source_sha256=proof['source_sha256']))
        validation_seconds=timestamp(proof['finished_at'])-timestamp(proof['started_at'])
        cohorts.append(dict(cohort=name,samples=len(samples),backend=job['backend'],threads_budget=job['threads_budget'],parallel_samples=job['parallel_samples'],
            memory_budget_gib=job['memory_gib'],methylation_gpu=(name=='ONT_Fresh'),
            analysis_wall_hours=round(r['wall_seconds']/3600,4),cpu_core_hours=round(r['cpu_core_hours'],4),
            peak_anonymous_ram_gib=round(r['observed_peak_ram_anon_bytes_gib'],4),
            peak_cgroup_memory_including_cache_gib=round(r['observed_peak_memory_current_bytes_gib'],4),
            peak_project_allocated_disk_gib=round(r['observed_peak_project_allocated_bytes_gib'],4),
            workflow_exit_code=int(r['exit_status']),workflow_status=proof['workflow_status'],
            output_validation='passed',validation_hours=round(validation_seconds/3600,4),
            checked_output_files=proof['checked_file_count'],checked_output_bytes=proof['checked_bytes']))
        native=[];methyl=[]
        paths=[(project/'results/.oncotracer-native/trace.tsv','native')]
        paths += [(p,'methylation') for p in sorted((project/'results/07_methylation/samples').glob('*/trace.tsv'))]
        for path,layer in paths:
            if not path.exists():continue
            record(path,layer+'_trace',name)
            with path.open() as h:
                for row in csv.DictReader(h,delimiter='\t'):
                    item=dict(stage=normalize_stage(row['stage'],samples),start=timestamp(row['started_at']),end=timestamp(row['finished_at']),returncode=int(row['returncode']))
                    (native if layer=='native' else methyl).append(item)
        stages+=aggregate_intervals(name,'native_command',native,origin)
        stages+=aggregate_intervals(name,'methylation_command',methyl,origin)
        variant_path=project/'results/08_variants/variant_status.json';variants=load(variant_path);record(variant_path,'variant_status',name)
        if name.startswith('Illumina'):
            envelopes=[dict(stage='illumina-variant-workflow',start=timestamp(s['started_at']),end=timestamp(s['finished_at']),returncode=0 if s['status']=='complete' else 1) for s in variants['samples']]
            stages+=aggregate_intervals(name,'variant_sample_envelope',envelopes,origin)
        stages+=aggregate_intervals(name,'validation',[dict(stage='full-output-validation',start=timestamp(proof['started_at']),end=timestamp(proof['finished_at']),returncode=0)],origin)
        groups=collections.defaultdict(list)
        trace_paths=sorted((project/'results/08_variants/glimpse2/samples').glob('*/pipeline_info/trace-*.tsv'))
        seen=set()
        for path in trace_paths:
            record(path,'nextflow_task_trace',name)
            with path.open() as h:
                for row in csv.DictReader(h,delimiter='\t'):
                    # Repeated trace snapshots must not count an identical attempt twice.
                    key=(path.parents[1].name,row['task_id'],row['hash'],row['submit'],row['status'])
                    if key in seen:continue
                    seen.add(key)
                    process=row['name'].split(' (',1)[0]
                    if not re.fullmatch(r'[A-Z0-9_:]+',process):raise ValueError('Unexpected process name: '+process)
                    groups[process].append(row)
        for process,items in sorted(groups.items()):
            completed=[x for x in items if x['status']=='COMPLETED']
            real=[seconds(x['realtime']) for x in completed if seconds(x['realtime']) is not None]
            rss=[memory_bytes(x['peak_rss']) for x in completed if memory_bytes(x['peak_rss']) is not None]
            requested=[memory_bytes(x['memory']) for x in completed if memory_bytes(x['memory']) is not None]
            tasks.append(dict(cohort=name,process=process,attempt_rows=len(items),completed=len(completed),cached=sum(x['status']=='CACHED' for x in items),
                failed=sum(x['status']=='FAILED' for x in items),other_statuses=sum(x['status'] not in ['COMPLETED','CACHED','FAILED'] for x in items),
                summed_task_realtime_hours=round(sum(real)/3600,6),median_task_seconds=round(statistics.median(real),3) if real else None,
                p95_task_seconds=round(percentile(real,.95),3) if real else None,
                max_peak_rss_gib=round(max(rss)/GIB,4) if rss else None,max_requested_memory_gib=round(max(requested)/GIB,4) if requested else None,
                max_requested_cpus=max(int(x['cpus']) for x in items)))
    columns=['elapsed_hours','cohort','cpu_cores','anonymous_ram_gib','cgroup_memory_including_cache_gib','gpu_memory_mib','allocated_project_disk_gib']
    series=[];buckets={}
    resource_path=recording/'resources_normalized.csv';record(resource_path,'resource_time_series')
    with resource_path.open() as h:
        for row in csv.DictReader(h):
            bucket=int(float(row['elapsed_seconds'])//300);key=(row['project'],bucket)
            if row['state'] not in ['running','active']:continue
            def numeric(field,scale=1):return float(row[field])/scale if row.get(field) not in ('',None) else None
            item=dict(elapsed_hours=round(float(row['elapsed_seconds'])/3600,4),cohort=row['project'],cpu_cores=numeric('cpu_cores'),anonymous_ram_gib=numeric('ram_anon_bytes',GIB),cgroup_memory_including_cache_gib=numeric('memory_current_bytes',GIB),gpu_memory_mib=numeric('gpu_memory_mib'),allocated_project_disk_gib=numeric('project_allocated_bytes',GIB))
            buckets[key]=item  # Last observed sample per five-minute bucket, not a peak.
    series=list(buckets.values())
    cpu={x['field'].rstrip(':'):x['data'] for x in manifest['hardware']['lscpu']['lscpu']}
    total_kib=int(re.search(r'MemTotal:\s+(\d+)',manifest['hardware']['meminfo'])[1])
    report=dict(schema='oncotracer-study-benchmark-v1',status='all_study_outputs_validated',version='5.0.0',
        benchmark_source_commit=manifest['source_commit'],benchmark_source_sha256=manifest['source_sha256'],
        benchmark_local_image_id=manifest['docker_image_id'],benchmark_started_utc=manifest['created_utc'],validation_finished_utc=finished['updated_at'],
        fresh_analyses=manifest['recording_type']=='fresh_analyses',sample_count=sum(c['samples'] for c in cohorts),
        hardware=dict(cpu=cpu['Model name'],logical_cpus=int(cpu['CPU(s)']),ram_gib=round(total_kib/1024**2,3),gpu_model=gpu_model,gpu_model_source='operator-supplied hardware label' if gpu_model else 'not recorded'),
        concurrency='Three cohorts ran concurrently with separate CPU sets and memory limits; shared storage, reference caches and host background workloads.',
        scope='Operational completion, measured resources and output integrity; not truth-set sensitivity/specificity, clinical validation or a controlled v4.1 speed comparison.',
        timing_notes=['Analysis wall time includes run-time input/reference verification, analysis, reporting and finalization. Initial software installation and shared reference preparation occurred before timing.',
          'Validation wall time is reported separately after analysis exit. Output validation hashes complete files and checks VCF indexes/sample identities and report PDFs.',
          'Native trace spans include waits between commands; active unions exclude gaps. Summed command/task times overlap across parallel samples and must not be added to cohort wall time.',
          'Nextflow task timings cover the Illumina variant subworkflow, including recorded retries; per-process peak RSS is distinct from cohort cgroup memory including page cache.',
          'Resource time-series CSV retains the last sample in each five-minute bucket. Summary peaks use the full recorder, not the downsampled plot.',
          'GPU device utilization includes other host processes; no exclusive GPU-hours or CPU-versus-GPU speedup is claimed.',
          'Empty metrics mean unavailable, not zero.'],
        cohorts=cohorts,qc=qc,validation=validation)
    write_json(out/'benchmark_summary.json',report);write_json(out/'source_evidence.json',dict(evidence=evidence))
    write_csv(out/'cohort_summary.csv',cohorts);write_csv(out/'stage_timings.csv',stages)
    write_csv(out/'nextflow_processes.csv',tasks);write_csv(out/'resource_timeseries.csv',series,columns)
    # Public data consist only of allowlisted aggregate values. Source paths and
    # commands are never copied; sample names are stripped before aggregation.
    for path in out.glob('*.json'):
        text=path.read_text()
        if '/media/' in text or '/home/' in text:raise ValueError('Local path leaked into public data')
    for path in list(out.glob('*.json'))+list(out.glob('*.csv')):
        text=path.read_text()
        for sample in all_samples:
            if re.search(r'(?<![A-Za-z0-9])'+re.escape(sample)+r'(?![A-Za-z0-9])',text):raise ValueError('Sample identifier leaked: '+sample)
    return report


def bundle(directory,destination):
    directory=Path(directory);destination=Path(destination)
    paths=sorted(p for p in directory.iterdir() if p.is_file() and p.name!='SHA256SUMS')
    sums=''.join(f'{sha(p)}  {p.name}\n' for p in paths)
    (directory/'SHA256SUMS').write_text(sums)
    paths=sorted(paths+[directory/'SHA256SUMS'])
    with destination.open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w') as archive:
        for p in paths:
            if p.is_symlink():raise ValueError('Benchmark bundle refuses symlinks')
            data=p.read_bytes();info=tarfile.TarInfo('oncotracer-v5-study-benchmark/'+p.name);info.size=len(data);info.mode=0o644;info.mtime=0
            archive.addfile(info,io.BytesIO(data))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path);p.add_argument('--recording',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--bundle',type=Path)
    p.add_argument('--gpu-model',help='Optional hardware model label; otherwise unreported')
    a=p.parse_args()
    if a.root or a.recording:
        if not a.root or not a.recording:p.error('--root and --recording must be supplied together')
        report=export(a.root,a.recording,a.output,a.gpu_model)
        print(json.dumps(dict(status=report['status'],samples=report['sample_count'],cohorts=report['cohorts']),indent=2))
    if a.bundle:bundle(a.output,a.bundle)


if __name__=='__main__':main()
