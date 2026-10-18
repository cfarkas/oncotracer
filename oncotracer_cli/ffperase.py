"""Run the pinned external FFPErase interfaces without Nextflow or downloads.

Upstream code/models are supplied by the user and retain their upstream terms.
Coverage is measured, never raised to make a low-pass library fit the model.
"""
from __future__ import annotations
import csv
import math
import os
import shutil
import subprocess
import json
from pathlib import Path
from .runtime import OncoTracerError, atomic_write_json, require_file, sha256_file
from .variant_filters import add_assessment, records, key

REVISION = 'b0dd56cbd0a939896a966b9ce30c4d719b158170'

# Discovery uses these package records only as hints. Supplied custom runtimes
# are checked functionally; model compatibility is confirmed by preflight_models.
RUNTIME_PACKAGES = {
    'python': '3.8', 'numpy': '1.19.5', 'pandas': '1.1.5', 'scipy': '1.5.3',
    'scikit-learn': '0.24.1', 'joblib': '1.1.1', 'pysam': '0.19.1',
    'imbalanced-learn': '0.8.1',
}
RUNTIME_PROBE_MARKER = 'ONCOTRACER_FFPERASE_RUNTIME_READY '
RUNTIME_PROBE_CODE = """import io,json,sys
import numpy as np,pandas as pd,scipy,sklearn,joblib,pysam,imblearn
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.ensemble import RandomForestClassifier
from imblearn.pipeline import Pipeline
frame=pd.DataFrame({'depth':[10.,20.,30.,40.],'context':['A','C','A','C']})
features=ColumnTransformer([('num',StandardScaler(),['depth']),('cat',OneHotEncoder(handle_unknown='ignore'),['context'])])
model=Pipeline([('preprocess',features),('classifier',RandomForestClassifier(n_estimators=2,n_jobs=1,random_state=0))])
model.fit(frame,[0,1,0,1])
buffer=io.BytesIO();joblib.dump(model,buffer);buffer.seek(0)
model=joblib.load(buffer);prob=model.predict_proba(frame.iloc[:1]);prediction=model.predict(frame.iloc[:1])
assert prob.shape==(1,2) and np.isfinite(prob).all() and (prob>=0).all() and (prob<=1).all()
assert prediction.shape==(1,)
versions={'python':'.'.join(map(str,sys.version_info[:3])),'numpy':np.__version__,'pandas':pd.__version__,'scipy':scipy.__version__,'scikit-learn':sklearn.__version__,'joblib':joblib.__version__,'pysam':pysam.__version__,'imbalanced-learn':imblearn.__version__}
print('ONCOTRACER_FFPERASE_RUNTIME_READY '+json.dumps({'functional_probe':True,'versions':versions}))
"""


def _runtime_environment():
    environment = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONPYCACHEPREFIX',
                'LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_LIBRARY_PATH_MCR'):
        environment.pop(key, None)
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    return environment


def preflight_runtime(prefix=None, sif=None):
    """Exercise dependencies without loading external models or fetching assets."""
    prefix = prefix or os.environ.get('ONCOTRACER_FFPERASE_PREFIX')
    sif = sif or os.environ.get('ONCOTRACER_FFPERASE_SIF')
    if sif:
        image = Path(sif).expanduser()
        runtime = shutil.which('apptainer') or shutil.which('singularity')
        if not image.is_file() or not runtime:
            raise OncoTracerError('FFPERASE runtime requires an existing variant_ffperase_sif and Apptainer/Singularity')
        command = [runtime, 'exec', '--cleanenv', '--no-home', str(image),
                   'python3', '-I', '-B', '-c', RUNTIME_PROBE_CODE]
    else:
        python = Path(prefix).expanduser() / 'bin/python' if prefix else None
        if python is None or not python.is_file() or not os.access(python, os.X_OK):
            raise OncoTracerError('FFPERASE requires a compatible variant_ffperase_prefix runtime. '
                                 'Use Install with Conda in the browser to prepare the dedicated FFPERASE environment, '
                                 'or choose Docker; a Python or Picard-only environment is insufficient.')
        command = [str(python), '-I', '-B', '-c', RUNTIME_PROBE_CODE]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                                env=_runtime_environment())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OncoTracerError('FFPERASE runtime dependency check failed: ' + str(error)) from error
    line = next((line for line in result.stdout.splitlines() if line.startswith(RUNTIME_PROBE_MARKER)), None)
    if result.returncode or line is None:
        detail = (result.stderr or result.stdout)[-1600:].strip()
        raise OncoTracerError('FFPERASE runtime is incompatible: ' + str(sif or prefix) + '. '
                             'The selected environment must provide numpy, pandas, scipy, scikit-learn, joblib, pysam and imbalanced-learn. '
                             'Use Install with Conda for the dedicated FFPERASE environment or select Docker. '
                             'No resources were downloaded. ' + detail)
    try:
        report = json.loads(line[len(RUNTIME_PROBE_MARKER):])
        if report['functional_probe'] is not True or set(report['versions']) != set(RUNTIME_PACKAGES):
            raise ValueError('incomplete dependency report')
    except (ValueError, KeyError, TypeError) as error:
        raise OncoTracerError('FFPERASE runtime returned an invalid dependency report') from error
    return report


def discover(root=None, models=None, sif=None, prefix=None):
    root = root or os.environ.get('ONCOTRACER_FFPERASE_ROOT')
    models = models or os.environ.get('ONCOTRACER_FFPERASE_MODELS')
    sif = sif or os.environ.get('ONCOTRACER_FFPERASE_SIF')
    prefix = prefix or os.environ.get('ONCOTRACER_FFPERASE_PREFIX')
    if not root:
        cached = Path.home()/'.nextflow/assets/.repos/papaemmelab/nf-ffperase/clones'/REVISION
        root = cached if cached.is_dir() else None
    if not root or not models:
        from .variant_model_assets import ffperase_missing_message
        missing = [name for name, value in (('ffperase_source', root), ('ffperase_models', models)) if not value]
        raise OncoTracerError(ffperase_missing_message(missing))
    root, models = Path(root).resolve(), Path(models).resolve()
    files = [root/'bin'/n for n in ('annotate_w_pileup','annotate_variants.py','classify_w_random_forest.py','microrep_python3.py')]
    files += [models/f'model.{kind}.joblib' for kind in ('snvs','indels')]
    for f in files:
        require_file(f, 'FFPErase resource')
    runtime = None
    if sif:
        sif = require_file(Path(sif), 'FFPErase SIF').resolve()
        runtime = shutil.which('apptainer') or shutil.which('singularity')
        if not runtime:
            raise OncoTracerError('FFPErase SIF requires Apptainer/Singularity')
    elif not prefix or not (Path(prefix)/'bin/python').is_file():
        raise OncoTracerError('Supply variant_ffperase_sif or a compatible variant_ffperase_prefix environment')
    identity = {str(f): {'size':f.stat().st_size, 'sha256':sha256_file(f)} for f in files}
    if prefix:
        native = Path(prefix)
        runtime_files = [native/'bin/python'] + [p for pattern in ('python-*.json','numpy-*.json','pandas-*.json','scikit-learn-*.json','scipy-*.json','joblib-*.json','imbalanced-learn-*.json','pysam-*.json') for p in (native/'conda-meta').glob(pattern)]
        for f in runtime_files:
            identity[str(f)] = {'size':f.stat().st_size,'mtime_ns':f.stat().st_mtime_ns,'sha256':sha256_file(f)}
    if sif:
        identity[str(sif)] = {'size':sif.stat().st_size,'mtime_ns':sif.stat().st_mtime_ns}
    return {'root':str(root), 'models':str(models), 'sif':str(sif) if sif else None,
            'prefix':str(Path(prefix).resolve()) if prefix else None, 'runtime':runtime,
            'resources':identity, 'expected_interface_revision':REVISION}



def preflight_models(detection):
    """Load the selected models and predict synthetic rows before heavy analysis."""
    code = """import json,sys
import numpy as np,pandas as pd,joblib,sklearn
result={}
for kind in ('snvs','indels'):
    model=joblib.load(sys.argv[1]+'/model.'+kind+'.joblib')
    preprocess=model.named_steps['preprocess']
    row={}
    for name,transformer,columns in preprocess.transformers_:
        if name=='cat':
            for col,values in zip(columns,transformer.categories_):row[col]=values[0]
        elif name=='num':
            for col in columns:row[col]=0.0
    row.update(VAF=.5,DEPTH=20.0,VARIANT_READS=10.0)
    columns=getattr(preprocess,'_feature_names_in',None)
    if columns is None:columns=getattr(preprocess,'feature_names_in_',list(row))
    table=pd.DataFrame([row],columns=columns);prediction=model.predict(table);prob=model.predict_proba(table)
    assert prob.shape==(1,2) and np.isfinite(prob).all() and (prob>=0).all() and (prob<=1).all()
    result[kind]={'loaded':True,'synthetic_prediction':bool(prediction[0]),'synthetic_probability':float(prob[0,1])}
print('ONCOTRACER_FFPERASE_READY '+json.dumps({'models':result,'sklearn':sklearn.__version__}))
"""
    command=[str(Path(detection['prefix'])/'bin/python') if detection.get('prefix') and not detection.get('sif') else 'python3',
             '-I','-B','-c',code,detection['models']]
    if detection.get('sif'):
        bind=[]
        for value in (detection['root'],detection['models']):
            if any(c in value for c in ':,\n\r'):raise OncoTracerError('FFPErase bind path contains a separator')
            bind.extend(['--bind',value+':'+value+':ro'])
        command=[detection['runtime'],'exec','--cleanenv',*bind,detection['sif'],*command]
    env=_runtime_environment()
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=120,env=env)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise OncoTracerError('FFPErase model runtime is not ready: '+str(error)) from error
    marker='ONCOTRACER_FFPERASE_READY '
    line=next((x for x in result.stdout.splitlines() if x.startswith(marker)),None)
    if result.returncode or line is None:
        raise OncoTracerError('FFPErase model runtime is not ready: '+(result.stderr or result.stdout)[-1600:])
    return json.loads(line[len(marker):])


def _command(detection, work, bam, reference, command):
    if not detection['sif']:
        if command[0] == 'python3':
            command = [str(Path(detection['prefix'])/'bin/python'), *command[1:]]
        return command
    writable = work.resolve()
    # Bind exact input files so an input beside the work directory cannot turn
    # the writable output tree read-only through duplicate parent bind mounts.
    readonly = {Path(detection['root']), Path(detection['models']), bam.resolve(), reference.resolve()}
    for source, suffixes in ((bam, ('.bai','.csi')), (reference, ('.fai',))):
        for suffix in suffixes:
            index=Path(str(source)+suffix)
            if index.is_file():readonly.add(index.resolve())
    args = [detection['runtime'],'exec','--cleanenv']
    for path, access in [(writable,'rw')] + [(p,'ro') for p in sorted(readonly, key=str)]:
        if any(c in str(path) for c in ':,\n\r'):
            raise OncoTracerError('FFPErase bind path contains a separator')
        args += ['--bind', f'{path}:{path}:{access}']
    return [*args,detection['sif'],*command]


def _clean_metrics(source, output):
    lines = [line for line in source.read_text().splitlines() if line.strip() and not line.startswith('#')]
    if not lines or 'ERROR_RATE' not in lines[0].split('\t'):
        raise OncoTracerError('Missing Picard ERROR_RATE metrics')
    output.write_text('\n'.join(lines)+'\n')


def parse_classifications(path):
    result = {}
    with open(path) as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            k = (row['CHR'], int(row['START']), row['REF'], row['ALT'])
            score = float(row['oncotracer_raw_predicts'])
            prediction = row['oncotracer_predicts'].lower()
            if not math.isfinite(score) or not 0 <= score <= 1 or prediction not in {'true','false','1','0'}:
                raise OncoTracerError('Invalid FFPErase model prediction')
            value = ('REJECTED' if prediction in {'true','1'} else 'REAL',score,'Random-forest artifact classification')
            if k in result and value != result[k]:
                raise OncoTracerError('Conflicting FFPErase allele classifications')
            result[k] = value
    return result


def _context_valid(fields, reference, index):
    chrom, pos, ref, alt = key(fields)
    if chrom not in index:
        return False
    length, offset, bases, width = index[chrom]
    margin = 1 if len(ref) == len(alt) == 1 else 25 + max(len(ref),len(alt))
    start, end = pos-1-margin, pos-1+len(ref)+margin
    if start < 0 or end > length:
        return False
    with reference.open('rb') as handle:
        first = offset + start//bases*width + start%bases
        last = offset + (end-1)//bases*width + (end-1)%bases + 1
        handle.seek(first)
        context = handle.read(last-first).replace(b'\n',b'').replace(b'\r',b'').upper()
    return len(context) == end-start and set(context) <= set(b'ACGT')


def assess(source, destination, *, detection, bam, reference, directory, run, tools,
           min_mapping_quality=20, min_base_quality=20):
    directory.mkdir(parents=True, exist_ok=True)
    table = directory/'ffperase.evidence.tsv'
    header, variants = [], []
    for line, fields in records(source):
        if fields is None:
            header.append(line)
        else:
            variants.append(fields)
    summary = {'status':'complete','score_scale':'Raw random-forest artifact probability',
               'resources':detection, 'counts':{}}
    if not variants:
        summary.update(status='not_applicable', reason='Empty candidate set')
        summary['counts'] = add_assessment(source,destination,{},tag='FFPERASE',description='FFPErase artifact assessment',table=table)
        return summary
    # Cache BAM-wide coverage/insert metrics across callers in this invocation.
    shared = directory.parent/'ffperase_shared'
    shared.mkdir(exist_ok=True)
    coverage_file = shared/'coverage.tsv'
    stats_file = shared/'stats.txt'
    if not coverage_file.exists():
        with coverage_file.open('w') as out:
            run('ffperase-coverage',[tools['samtools'],'coverage','-q',str(min_mapping_quality),'-Q',str(min_base_quality),bam],stdout=out)
    with coverage_file.open() as handle:
        rows = list(csv.DictReader(handle,delimiter='\t'))
    genome_bases = sum(int(l.split('\t')[1]) for l in Path(str(reference)+'.fai').read_text().splitlines())
    covered_depth = sum((int(r['endpos'])-int(r['startpos'])+1)*float(r['meandepth']) for r in rows)
    coverage = covered_depth/genome_bases
    coverage_integer = int(coverage)
    summary.update(mean_genome_depth=coverage, upstream_integer_coverage=coverage_integer)
    if coverage_integer <= 1:
        summary.update(status='not_assessed', reason='Measured genome-wide coverage truncates to <=1; upstream log-depth feature is undefined. No imputation or coverage inflation applied.')
        assessments = {key(f):('NOT_EVALUATED',None,summary['reason']) for f in variants}
        summary['counts'] = add_assessment(source,destination,assessments,tag='FFPERASE',description='FFPErase artifact assessment',table=table)
        atomic_write_json(directory/'ffperase.summary.json',summary)
        return summary
    if not stats_file.exists():
        with stats_file.open('w') as out:
            run('ffperase-stats',[tools['samtools'],'stats',bam],stdout=out)
    inserts = [(int(f[1]),int(f[2])) for l in stats_file.read_text().splitlines() if (f:=l.split('\t'))[0]=='IS' and len(f)>2]
    total = sum(n for _,n in inserts)
    cumulative, median = 0, None
    for size,n in sorted(inserts):
        cumulative += n
        if cumulative >= (total+1)//2 and total:
            median = size
            break
    if not median or median <= 1:
        raise OncoTracerError('FFPErase requires a measured paired-end insert-size median >1')
    summary['median_insert_size'] = median
    # FFPERASE context encoding expects uppercase sequence. Originals stay read-only.
    ref = shared/'uppercase.fa'
    if not ref.exists():
        with reference.open() as inp, ref.open('w') as out:
            for line in inp:
                out.write(line if line.startswith('>') else line.upper())
        run('ffperase-faidx',[tools['samtools'],'faidx',ref])
        run('ffperase-dict',[tools['gatk'],'CreateSequenceDictionary','-R',ref,'-O',shared/'uppercase.dict'])
    metrics = shared/'artifact'
    pa, bb = shared/'pre_adapter_metrics.tsv',shared/'bait_bias_metrics.tsv'
    if not pa.exists() or not bb.exists():
        run('ffperase-picard',[tools['gatk'],'CollectSequencingArtifactMetrics','-I',bam,'-R',ref,'-O',metrics,
            '--MINIMUM_MAPPING_QUALITY',str(min_mapping_quality),'--MINIMUM_QUALITY_SCORE',str(min_base_quality)])
        _clean_metrics(Path(str(metrics)+'.pre_adapter_detail_metrics'),pa)
        _clean_metrics(Path(str(metrics)+'.bait_bias_detail_metrics'),bb)
    bin_dir = Path(detection['root'])/'bin'
    assessed = {}
    index = {f[0]:tuple(map(int,f[1:5])) for line in Path(str(ref)+'.fai').read_text().splitlines() if (f:=line.split('\t'))}
    for kind in ('snvs','indels'):
        selected = [f for f in variants if set(f[3]+f[4]) <= set('ACGT') and _context_valid(f,ref,index) and
                    ((len(f[3])==len(f[4])==1) if kind=='snvs' else ((len(f[3])==1) != (len(f[4])==1)))]
        if not selected:
            continue
        sub = directory/f'ffperase_{kind}'
        sub.mkdir()
        vcf, pileup = sub/'input.vcf',sub/'pileup.tsv'
        vcf.write_text(''.join(header)+''.join('\t'.join(f)+'\n' for f in selected))
        def external(label, args):
            run(label,_command(detection,directory.parent,bam,ref,args),cwd=sub)
        external(f'ffperase-{kind}-pileup',[str(bin_dir/'annotate_w_pileup'),bam,ref,vcf,pileup,
                  '--mapq',str(min_mapping_quality),'--baseq',str(min_base_quality),'--depth','1','--snvs','true' if kind=='snvs' else 'false'])
        # No usable observations is an explicit unassessed result, not a model failure.
        with pileup.open() as f:
            if sum(1 for _ in f) <= 1:
                continue
        external(f'ffperase-{kind}-features',['python3',str(bin_dir/'annotate_variants.py'),
                 '--pileup',pileup,'--picard_preadapter',pa,'--picard_baitbias',bb,'--reference',ref,
                 '--coverage',str(coverage_integer),'--median_insert',str(median),'--mutation_type',kind,'--outdir',sub])
        external(f'ffperase-{kind}-classify',['python3',str(bin_dir/'classify_w_random_forest.py'),
                 '--features',sub/'features.tsv','--model',Path(detection['models'])/f'model.{kind}.joblib',
                 '--model-name','oncotracer','--mutation-type',kind,'--outdir',sub])
        classified = sub/'classify'/f'classified_df_{kind}.tsv'
        assessed.update(parse_classifications(classified))
        shutil.copy2(classified,directory/f'ffperase.{kind}.classified.tsv')
    if not set(assessed) <= {key(f) for f in variants}:
        raise OncoTracerError('FFPErase emitted unexpected alleles')
    summary['counts'] = add_assessment(source,destination,assessed,tag='FFPERASE',description='FFPErase artifact assessment',table=table)
    if not assessed:
        summary.update(status='not_assessed',reason='No candidate had usable FFPErase features')
    atomic_write_json(directory/'ffperase.summary.json',summary)
    return summary
