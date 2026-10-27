#!/usr/bin/env python3
"""Patient FASTQ entry point for the local Nextflow workflow."""
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
# Find the existing installation when running from a nested workflow copy.
LOCAL=next((p for p in ROOT.parents if (p/'references/samurai_hg38/genome.fa').is_file()), ROOT.parent)
SUFFIX=re.compile(r'\.(?:fastq|fq)(?:\.gz)?$',re.I)
PAIR=re.compile(r'^(.*?)([._-])(?:R)?([12])((?:_\d{3})?)$',re.I)
SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
PON_HELP='''Panel of normals examples (choose one):
  --panel-of-normals /path/to/somatic_pon.hg38.vcf.gz
      Use an existing somatic PoN VCF with a .tbi or .csi index.
  --panel-of-normals /path/to/normals
      Build a PoN from FASTQs in /path/to/normals/CTRL001/, CTRL002/,
      CTRL004/, etc. Every immediate subfolder is one independent normal;
      its FASTQs/lane folders are searched recursively. Folder names are
      sample IDs. Use a folder containing ONLY the intended normals.
  --panel-of-normals /data/CTRL001,/data/CTRL002,/data/CTRL004
      Build from only these sample folders. Use one comma-separated value
      (quote the whole value if paths contain spaces), or a JSON/YAML list
      in -params-file. Separate positional paths belong to --fastq.

Directory mode aligns and marks duplicates separately for each normal,
runs Mutect2 on each, and builds a panel of sites recurring in at least
2 normals. At least 2 independent normals are required; 3 are accepted
with a small-panel warning (GATK recommends aiming for at least 40).
Use technically similar samples. Including the analyzed sample or reusing
its FASTQs fails preflight. Repeated lanes are not independent normals.
The generated VCF/index are saved under <output>/panel_of_normals/ as
somatic_pon.hg38.vcf.gz[.tbi] and used automatically by Mutect2. Building
uses the same reference, --regions and --test-reads settings as the run;
panels made with --test-reads are smoke-test artifacts, not reusable panels.
--preflight-only validates the folders without building or aligning.
'''


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def checked_path(value):
    p=Path(value).expanduser().resolve()
    if any(x in str(p) for x in "\n\r\t'"):
        raise ValueError(f'Paths cannot contain tabs, newlines, or single quotes: {p}')
    if not p.exists(): raise ValueError(f'Missing path: {p}')
    return p


def python_executable(value):
    """Keep virtual-environment executable links intact while validating shell use."""
    if not isinstance(value,str) or not value.strip() or any(c in value for c in "\n\r\t'"):
        raise ValueError('--python must be an executable name or path without tabs, newlines, or single quotes')
    executable=shutil.which(os.path.expanduser(value))
    if not executable: raise ValueError(f'Python executable not found or not executable: {value}')
    # Resolving the final symlink can turn venv/bin/python into the system Python
    # and silently remove the environment containing the reporting dependencies.
    return str(Path(executable).absolute())


def file_meta(path):
    p=Path(path)
    st=p.stat()
    return {'path':str(p.resolve()),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns}


def fastq_name(p):
    name=SUFFIX.sub('',Path(p).name)
    return name[:-3] if name.endswith('.gz') else name


def looks_like_fastq(p):
    if not p.is_file(): return False
    if SUFFIX.search(p.name): return True
    try:
        with p.open('rb') as f: compressed=f.read(2)==b'\x1f\x8b'
        with (gzip.open(p,'rt') if compressed else p.open()) as f:
            lines=[f.readline(65536) for _ in range(4)]
        return lines[0].startswith('@') and lines[2].startswith('+') and len(lines[1].strip())==len(lines[3].strip()) and bool(lines[3])
    except (OSError,UnicodeError): return False


def validate_trait_panel(path):
    required={'chrom','pos','rsid','ref','alt','effect_allele','trait','model','association','limitation','pubmed'}
    with open(path) as f:
        reader=csv.DictReader(f,delimiter='\t')
        if not required.issubset(reader.fieldnames or []): raise ValueError('Trait panel is missing required columns; follow data/traits.GRCh38.tsv')
        rows=list(reader)
    if not rows: raise ValueError('Trait panel is empty')
    for r in rows:
        if r['model'] not in {'dominant','recessive','association_only'}: raise ValueError('Trait panel supports dominant/recessive single-SNP models only')
        if r['ref'] not in 'ACGT' or len(r['ref'])!=1 or any(len(x)!=1 or x not in 'ACGT' or x==r['ref'] for x in r['alt'].split(',')): raise ValueError('Trait REF/ALT must be distinct single DNA bases')
        if r['model']!='association_only' and (',' in r['alt'] or r['effect_allele'] not in {r['ref'],r['alt']}): raise ValueError('Trait effect allele must equal REF or ALT')
        if not r['pos'].isdigit() or int(r['pos'])<1 or r['chrom'].removeprefix('chr') not in {str(i) for i in range(1,23)}: raise ValueError('Trait panel requires positive 1-based autosomal coordinates')
    return len(rows)


def discover_fastqs(inputs, single_end=False):
    paths=[]
    for inp in inputs:
        p=checked_path(inp)
        if p.is_dir():
            found=sorted(x for x in p.rglob('*') if x.is_file() and looks_like_fastq(x))
            if not found: raise ValueError(f'No .fastq/.fq[.gz] files in {p}')
            paths.extend(x.resolve() for x in found)
        elif looks_like_fastq(p): paths.append(p)
        else: raise ValueError(f'Not a FASTQ file or folder: {p}')
    if len(paths)!=len(set(paths)): raise ValueError('Duplicate FASTQs found through overlapping inputs/symlinks; supply each lane once.')
    groups={}
    if single_end:
        return [(fastq_name(p),[p]) for p in sorted(paths)]
    # A single named mate is accepted when its counterpart is in the same directory.
    if len(paths)==1:
        p=paths[0]; m=PAIR.match(fastq_name(p))
        if m:
            for candidate in p.parent.iterdir():
                cm=PAIR.match(fastq_name(candidate)) if looks_like_fastq(candidate) else None
                if cm and cm.group(1,2,4)==m.group(1,2,4) and cm.group(3)!=m.group(3):
                    paths.append(candidate.resolve())
    for p in paths:
        m=PAIR.match(fastq_name(p))
        if not m: raise ValueError(f'Cannot identify mate for {p.name}. Use *_R1/*_R2 or *_1/*_2; genuine single-end data needs --single-end.')
        key=(str(p.parent),m.group(1),m.group(4))
        group=groups.setdefault(key,{})
        mate=m.group(3)
        if mate in group: raise ValueError(f'Duplicate R{mate} for {key}')
        group[mate]=p
    lanes=[]
    for key,g in sorted(groups.items()):
        if set(g)!={'1','2'}: raise ValueError(f'Missing mate for {next(iter(g.values()))}; provide both FASTQs or the patient folder.')
        lanes.append((key[1]+key[2],[g['1'],g['2']]))
    return lanes


def derive_name(inputs, lanes):
    if len(inputs)==1 and Path(inputs[0]).is_dir(): name=Path(inputs[0]).resolve().name
    elif len(lanes)==1: name=lanes[0][0]
    else:
        prefix=os.path.commonprefix([x[0] for x in lanes]).rstrip('_.-')
        name=re.sub(r'_(?:S\d+_)?L\d*$', '',prefix) or Path(inputs[0]).resolve().parent.name
    return re.sub(r'[^A-Za-z0-9_.-]+','_',name).strip('_.-') or 'sample'


def prepare_panel(value, patient_sample, patient_lanes, single_end=False, library='lib1'):
    """Resolve a ready VCF or discover independent normal FASTQ folders; no analysis."""
    panel={'mode':'none','vcf':None,'index':None,'normals':[],'min_sample_count':2}
    if value is None: return None,panel,[]
    if isinstance(value,str):
        if value.lstrip().startswith('['): values=json.loads(value)
        elif ',' in value and not Path(value).expanduser().exists(): values=[x.strip() for x in value.split(',')]
        else: values=[value]
    else: values=value
    if not isinstance(values,(list,tuple)) or not values or any(not isinstance(x,str) or not x.strip() for x in values):
        raise ValueError('--panel-of-normals requires a VCF, a parent folder, or a comma-separated list of normal folders')
    paths=[checked_path(x) for x in values]
    source=str(paths[0]) if len(paths)==1 else [str(p) for p in paths]
    if len(paths)==1 and paths[0].is_file():
        path=paths[0]
        if not path.name.endswith('.vcf.gz'): raise ValueError('--panel-of-normals must be a .vcf.gz file or normal FASTQ folders')
        index=next((Path(str(path)+ext) for ext in ['.tbi','.csi'] if Path(str(path)+ext).is_file()),None)
        if index is None: raise ValueError('--panel-of-normals VCF requires a .tbi or .csi index')
        panel.update(mode='vcf',vcf=str(path),index=str(index))
        return source,panel,[]
    if any(not p.is_dir() for p in paths):
        raise ValueError('--panel-of-normals: supply one indexed VCF OR normal FASTQ folders; do not mix files and folders')
    if len(paths)==1:
        parent=paths[0]
        if any(looks_like_fastq(p) for p in parent.iterdir() if p.is_file()):
            raise ValueError('--panel-of-normals: a single directory must contain one subfolder per normal, not FASTQs directly; use a comma-separated list to select sample folders')
        folders=sorted(p for p in parent.iterdir() if p.is_dir() and not p.name.startswith('.'))
    else: folders=paths
    if len(folders)<2: raise ValueError('--panel-of-normals requires at least 2 independent normal sample folders')
    patient_reads={p.resolve() for _,reads in patient_lanes for p in reads}
    patient_inodes={(p.stat().st_dev,p.stat().st_ino) for p in patient_reads}
    patient_ids={patient_sample}
    patient_ids.update(p.parent.name for p in patient_reads)
    ids=set(); seen=set(); seen_inodes=set(); rows=[]
    for folder in folders:
        sample=folder.name
        if not SAFE.fullmatch(sample): raise ValueError(f'Normal folder name must be a valid sample ID (letters, digits, _, ., -): {folder}')
        if sample in ids: raise ValueError(f'Duplicate normal sample ID: {sample}')
        if sample in patient_ids: raise ValueError(f'Panel of normals includes the analyzed sample {sample}; select only other normal folders')
        lanes=discover_fastqs([str(folder)],single_end)
        prefixes={re.split(r'[_ .-]',name)[0] for name,_ in lanes}
        if len(prefixes)>1:
            raise ValueError(f'Normal folder {folder} contains multiple sample prefixes {sorted(prefixes)}; each folder must contain one individual')
        reads=[p for _,pair in lanes for p in pair]
        resolved={p.resolve() for p in reads}
        inodes={(p.stat().st_dev,p.stat().st_ino) for p in reads}
        if resolved & patient_reads or inodes & patient_inodes:
            raise ValueError(f'Panel of normals reuses FASTQs from the analyzed sample in {folder}')
        if resolved & seen or inodes & seen_inodes or len(inodes)!=len(reads):
            raise ValueError(f'Duplicate FASTQs across normal folders/lanes: {folder}; each normal must be independent')
        ids.add(sample); seen.update(resolved); seen_inodes.update(inodes)
        panel['normals'].append({'sample':sample,'directory':str(folder.resolve()),'lanes':len(lanes),
                                 'inputs':[file_meta(p) for p in reads]})
        for n,(_,pair) in enumerate(lanes,1):
            rows.append({'role':'normal','sample':sample,'lane':f'lane{n:03d}','library':library,
                         'fastq_1':str(pair[0]),'fastq_2':str(pair[1]) if len(pair)>1 else ''})
    panel['mode']='build'
    return source,panel,rows


def parse_memory(value):
    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(GB|G|GIB|MB|M|MIB|TB|T|TIB)?\s*',value,re.I)
    if not m: raise argparse.ArgumentTypeError('Use a memory budget such as 32GB, 32, or 64000MB')
    unit=(m.group(2) or 'GB').upper()
    gb=float(m.group(1))*(1024 if unit.startswith('T') else 1/1024 if unit.startswith('M') else 1)
    if gb<2: raise argparse.ArgumentTypeError('At least 2GB is required; hg38 alignment normally needs 8GB or more')
    return gb


def find_tool(name, prefix=None):
    options=[Path(prefix)/'bin'/name] if prefix else []
    # An explicitly selected caller must not fall back to a different runtime.
    if not (prefix and name == 'bcftools'):
        options += [LOCAL/'tools/oncotracer-variants-env/bin'/name]
        options += [Path(shutil.which(name))] if shutil.which(name) else []
    for p in options:
        if p.is_file() and os.access(p,os.X_OK): return str(p.absolute())
    raise ValueError(f'Missing executable {name}; install it or set --tool-prefix (environment with bin/{name}).')


def choose_annotation(args):
    if not args.annovar and not args.vep: return {'engine':'none','message':'Gene annotation was not requested; ClinVar matching remains enabled.'}
    if args.annovar:
        candidates=[Path(args.annovar_dir).expanduser()] if args.annovar_dir else []
        for k in ['ANNOVAR_HOME','ANNOVAR_DIR']:
            if os.getenv(k): candidates.append(Path(os.environ[k]))
        if shutil.which('table_annovar.pl'): candidates.append(Path(shutil.which('table_annovar.pl')).parent)
        candidates += [Path.home()/'annovar',LOCAL/'tools/annovar']
        for d in candidates:
            db=Path(args.annovar_db or os.getenv('ANNOVAR_DB') or d/'humandb').expanduser()
            scripts=['table_annovar.pl','annotate_variation.pl','convert2annovar.pl','coding_change.pl']
            if not all((d/s).is_file() for s in scripts): continue
            for gene in ['refGeneWithVer','refGene']:
                if all((db/f'hg38_{gene}{suffix}').is_file() and (db/f'hg38_{gene}{suffix}').stat().st_size for suffix in ['.txt','Mrna.fa']):
                    if re.search(r'[^A-Za-z0-9_./-]',str(d)+str(db)): continue
                    return {'engine':'annovar','directory':str(d.resolve()),'database':str(db.resolve()),'protocol':gene,
                            'message':f'Using local ANNOVAR hg38 {gene}: {db}'}
        print('WARNING: ANNOVAR or complete hg38 gene databases unavailable; falling back to offline VEP.',file=sys.stderr)
    vep=args.vep_path or shutil.which('vep')
    cache=Path(args.vep_cache or os.getenv('VEP_CACHE') or Path.home()/'.vep').expanduser()
    matches=sorted((cache/'homo_sapiens').glob('*_GRCh38')) if cache.exists() else []
    if vep and matches:
        if not os.access(vep,os.X_OK): raise ValueError(f'VEP is not executable: {vep}')
        versions=sorted([p.name.split('_')[0] for p in matches],key=int)
        version=str(args.vep_cache_version or versions[-1])
        if not (cache/'homo_sapiens'/f'{version}_GRCh38').is_dir(): raise ValueError(f'Missing VEP cache {version}_GRCh38')
        return {'engine':'vep','executable':str(checked_path(vep)),'cache':str(cache.resolve()),'cache_version':version,
                'message':f'Using offline VEP with GRCh38 cache {version}'}
    raise ValueError('ANNOVAR fallback: offline VEP executable/cache unavailable. Install Ensembl VEP plus a matching homo_sapiens GRCh38 cache and supply --vep-path /path/vep --vep-cache /path/cache --vep-cache-version N. No patient variants are sent to online services.')


def parser():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=HelpFormatter,epilog=PON_HELP)
    p.add_argument('--python',default=sys.executable,help='Python executable for preflight, workflow tasks and reports; preserves the selected environment')
    p.add_argument('--fastq',nargs='+',action='extend',default=[],help='One or more paired FASTQs and/or patient folders (recursive). A named single mate discovers its sibling.')
    p.add_argument('--annovar',action='store_true',help='Prefer local ANNOVAR hg38; warn and fall back to offline VEP if unavailable')
    p.add_argument('--gwas',action='store_true',help='Annotate GWAS Catalog loci; requires prepared public catalog')
    p.add_argument('--output','--outdir',type=Path,help='Output folder; default results/<sequencing or input-folder name>')
    p.add_argument('--threads',type=int,default=8,help='Total CPU budget across concurrent Nextflow tasks')
    p.add_argument('--memory',type=parse_memory,default=32,help='Total memory budget, e.g. 32GB or 64000MB')
    p.add_argument('--sample',help='Pseudonymous sample ID; inferred from input if omitted')
    p.add_argument('--library',default='lib1',help='Library ID; lanes are deduplicated together')
    p.add_argument('--single-end','--single_end',action='store_true')
    p.add_argument('--reference',help='GRCh38 FASTA with .fai and .dict; detects local hg38 by default')
    p.add_argument('--bwa-index',help='BWA index prefix; defaults to reference FASTA')
    p.add_argument('--tool-prefix',help='Existing environment with bin/gatk, bin/bcftools, etc.')
    p.add_argument('--clinvar',help='Local GRCh38 ClinVar VCF; detects existing project resource')
    p.add_argument('--annovar-dir'); p.add_argument('--annovar-db')
    p.add_argument('--vep',action='store_true',help='Request offline VEP directly')
    p.add_argument('--vep-path'); p.add_argument('--vep-cache'); p.add_argument('--vep-cache-version',type=int)
    p.add_argument('--traits-panel',help='Curated GRCh38 forward-strand trait TSV; default expanded 50-marker panel')
    p.add_argument('--gwas-catalog',help='Prepared associations.GRCh38.tsv.gz from bin/prepare_resources.py')
    p.add_argument('--resource-dir',type=Path,default=ROOT/'resources')
    p.add_argument('--no-ancestry',action='store_true',help='Explicitly disable ancestry; otherwise reference resources are required')
    p.add_argument('--ancestry-explorer',action=argparse.BooleanOptionalAction,default=True,help='Visual QC, broad admixture projection, mitochondrial lineage and Neanderthal marker evidence; disabled by --no-ancestry')
    p.add_argument('--ancestry-explorer-manifest',help='Pinned GRCh38 public ancestry resource manifest')
    p.add_argument('--ancestry-explorer-cache',help='Persistent public ancestry/Haplogrep resource cache')
    p.add_argument('--ancestry-min-sites',type=int,default=1000,help='Minimum confident sites; low-data estimates are withheld')
    p.add_argument('--germline-resource',help='Indexed GRCh38 population AF VCF for Mutect2')
    p.add_argument('--panel-of-normals',metavar='VCF_OR_NORMAL_FOLDERS',
        help='Existing indexed somatic .vcf.gz, parent folder of normal FASTQ sample subfolders, or comma-separated sample folders. Folder inputs build and apply a PoN automatically; see examples below')
    p.add_argument('--contamination-sites',help='Indexed common biallelic SNP AF VCF for GetPileupSummaries/CalculateContamination')
    p.add_argument('--regions',help='Optional BED (0-based half-open) restricting variant and trait calling; alignment/QC remain genome-wide')
    p.add_argument('--min-depth',type=int,default=10,help='Minimum genotype DP for trait/pathogenic review candidates')
    p.add_argument('--min-gq',type=int,default=20)
    p.add_argument('--min-mapping-quality',type=int,default=30)
    p.add_argument('--min-base-quality',type=int,default=20)
    p.add_argument('--test-reads',type=int,default=0,help='Smoke test: first N paired records PER LANE; 0 means all reads. Never a biological result.')
    p.add_argument('--allow-test-reference',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--preflight-only',action='store_true',help='Write manifest and dependency/resource report, without sequencing analysis')
    p.add_argument('--continue',dest='continue_run',action='store_true',help='Reuse verified completed work in this output folder, including across this trait-only upgrade')
    p.add_argument('--reuse-from',type=Path,help='Reuse verified completed tasks from another output folder; requires --continue')
    p.add_argument('--reference-reuse-from',type=Path,help='Reuse verified GLIMPSE reference preparation from another sample; never reuse its genotypes')
    p.add_argument('--matched-normal-bam',help='Explicit matched normal BAM for Mutect2; never inferred from unrelated control IDs')
    p.add_argument('--matched-normal-sample',help='Exact SM of the explicitly paired normal')
    p.add_argument('--bam',help='Reuse an existing sorted BAM with an exact sample SM')
    p.add_argument('--germline-vcf',help='Reuse a direct germline callset with --bam')
    p.add_argument('--somatic-vcf',help='Reuse a filtered somatic candidate callset with --bam')
    p.add_argument('--glimpse2',action=argparse.BooleanOptionalAction,default=True,help='Impute autosomal SNPs with GLIMPSE2; automatically provision missing public GRCh38 references/maps/tools')
    p.add_argument('--glimpse-reference-dir',help='Optional directory with chrN.bcf/chrN.vcf.gz and matching GRCh38 genetic maps')
    p.add_argument('--glimpse-bin',help='Optional directory containing the four GLIMPSE2 executables')
    p.add_argument('--glimpse-chromosomes',default='all',help='Autosomes to impute: all, or comma-separated numbers such as 21,22')
    p.add_argument('--confidence_gwas','--confidence-gwas',choices=['strict','medium','exploratory'],help='Trait/GWAS genotype evidence preset; affects these reports only, not clinical or PGS filters')
    p.add_argument('--trait-mode',choices=['strict','exploratory'],default='exploratory')
    p.add_argument('--trait-min-depth',type=int,default=2)
    p.add_argument('--trait-min-gq',type=float,default=5.0)
    p.add_argument('--imputation-min-gp',type=float,default=0.90)
    p.add_argument('--imputation-min-info',type=float,default=0.30)
    p.add_argument('--imputed-clinvar',action=argparse.BooleanOptionalAction,default=False,help='Annotate every imputed SNP with ClinVar; requires GLIMPSE2')
    p.add_argument('--polygenic',action='store_true',help='Calculate the published broad trait/PGS model panel from imputed dosages')
    p.add_argument('--pgs-manifest',help='GRCh38 model manifest; defaults to data/pgs.broad.GRCh38.json')
    p.add_argument('--pgs-cache',help='Persistent public PGS scoring-file download cache')
    p.add_argument('--patients',help='Patient TSV: sample_id, name, age, sex; missing age/sex accepted')
    p.add_argument('--patient-database',help='Patient .xlsx workbook or directory; exports pseudonymous name/age/sex TSV')
    p.add_argument('--pgs-reference-stats',action=argparse.BooleanOptionalAction,default=False,help='Compare exact matched PGS terms with public reference individuals')
    p.add_argument('--pgs-reference-population',default='auto',choices=['auto','AMR','EUR','AFR','EAS','SAS'],help='Exploratory reference group for percentile display; not risk calibration')
    p.add_argument('--pgs-priority-focus',default='',help='Comma-separated trait/category terms to prioritize for review; does not alter genetic scores')
    p.add_argument('--homozygosity-report',action=argparse.BooleanOptionalAction,default=True,help='Autosomal homozygosity and exploratory ROH from complete imputed genotypes')
    p.add_argument('--cancer-report',action=argparse.BooleanOptionalAction,default=True,help='BRCA1/BRCA2 and focused cancer-gene review when integrated interpretation is enabled')
    p.add_argument('--cardiomyopathy-report',action=argparse.BooleanOptionalAction,default=True,help='Dedicated cardiomyopathy review when integrated interpretation is enabled')
    p.add_argument('--unified-vcf',action=argparse.BooleanOptionalAction,default=True,help='Export a normalized per-sample VCF with sample-specific evidence fields for cohort merging')
    p.add_argument('--pgs-min-coverage',type=float,default=0.75,help='Minimum retained fraction for the partial-score completeness flag')
    p.add_argument('--pgs-min-weight-coverage',type=float,default=0.75,help='Minimum retained absolute effect-weight fraction')
    p.add_argument('--explain-findings',action=argparse.BooleanOptionalAction,default=True,help='Explain all trait, score and pathogenic-label findings with versioned public evidence')
    p.add_argument('--explanation-evidence',help='GRCh38 curated evidence JSON; applies across samples by exact allele')
    p.add_argument('--literature-cache',help='Shared cache of public ClinVar and PubMed evidence')
    p.add_argument('--literature-release',default='2026-10',help='Public evidence cache release; change to refresh sources and explanation reports')
    p.add_argument('--literature-offline',action='store_true',help='Use cached literature only; label unavailable sources explicitly')
    p.add_argument('--interpretation-report',action=argparse.BooleanOptionalAction,default=False,help='Integrated interpretation, variant support scores and orderly report deliverables')
    p.add_argument('--report-llm',action=argparse.BooleanOptionalAction,default=False,help='Generate cited narrative drafts with a local model; implies --interpretation-report')
    p.add_argument('--gpu-usage',action=argparse.BooleanOptionalAction,default=False,help='Use reserved GPU memory for local report generation')
    p.add_argument('--report-pdf',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--llm-model',default=__import__('llm_reporting').DEFAULT_MODEL,help='Cached pinned model ID or local Safetensors directory; no hosted API')
    p.add_argument('--llm-python',help='Python environment with torch/transformers and, for PDF, reportlab')
    p.add_argument('--llm-threads',type=int,default=4)
    p.add_argument('--llm-max-new-tokens',type=int,default=512)
    p.add_argument('--resume',action='store_true',help='Resume the Nextflow run in this output folder')
    p.add_argument('--work-dir',type=Path,help='Nextflow work folder; default <output>/work')
    return p


def prepare(a):
    """Validate a run and write stable inputs; never invoke Nextflow or analyse reads."""
    if a.resume:a.continue_run=True
    selected_python=python_executable(a.python)
    if a.report_llm:a.interpretation_report=True
    if a.pgs_reference_stats and not (a.polygenic and a.glimpse2):raise ValueError('--pgs-reference-stats requires --polygenic and --glimpse2')
    if a.threads < 1: raise ValueError('--threads must be positive')
    if min(a.min_depth,a.min_gq,a.ancestry_min_sites)<1 or min(a.test_reads,a.min_mapping_quality,a.min_base_quality)<0:
        raise ValueError('Invalid negative/zero threshold')
    import continuation
    if (a.polygenic or a.imputed_clinvar) and not a.glimpse2: raise ValueError('--polygenic and --imputed-clinvar require --glimpse2')
    if not 0<a.pgs_min_coverage<=1 or not 0<a.pgs_min_weight_coverage<=1: raise ValueError('PGS coverage thresholds must be in (0,1]')
    if a.trait_min_depth<1 or not 0<=a.trait_min_gq<=999: raise ValueError('Invalid trait thresholds')
    if not 0<a.imputation_min_gp<=1 or not 0<=a.imputation_min_info<=1: raise ValueError('Imputation thresholds must be probabilities')
    chromosomes=list(range(1,23)) if a.glimpse_chromosomes=='all' else [int(x.removeprefix('chr')) for x in a.glimpse_chromosomes.split(',')]
    if not chromosomes or len(set(chromosomes))!=len(chromosomes) or any(x not in range(1,23) for x in chromosomes): raise ValueError('GLIMPSE2 supports unique autosomes 1-22')
    if bool(a.bam) == bool(a.fastq): raise ValueError('Supply exactly one of --bam or --fastq')
    if a.bam and not a.sample: raise ValueError('--bam requires --sample matching BAM SM')
    if not a.glimpse2: raise ValueError('OncoTracer short variants requires GLIMPSE2')
    lanes=[] if a.bam else discover_fastqs(a.fastq,a.single_end)
    sample=a.sample or derive_name(a.fastq,lanes)
    if not SAFE.fullmatch(sample) or not SAFE.fullmatch(a.library): raise ValueError('Sample and library IDs must contain only letters, digits, _, ., - and start with a letter/digit.')
    if not a.sample:
        # Avoid silently combining a cohort root into a single patient.
        ids={re.split(r'[_ .-]',name)[0] for name,_ in lanes}
        if len(ids)>1: raise ValueError(f'Multiple sample prefixes {sorted(ids)}. Supply one patient folder, or --sample to explicitly pool these files.')
    output=(a.output or ROOT/'results'/sample).expanduser().resolve()
    active=continuation.active_work(output)
    if active: raise ValueError(f'Output has an active task (PID {active}); wait for the existing run to finish or stop it before --continue')
    output.mkdir(parents=True,exist_ok=True)
    info=output/'pipeline_info'; info.mkdir(exist_ok=True)
    prior_path=info/'runtime.json'
    previous=json.loads(prior_path.read_text()) if prior_path.exists() else None
    runtime=vars(a).copy(); runtime.update(sample=sample,output=str(output),memory_gb=a.memory,root=str(ROOT),python=selected_python)
    # Persist absolute user input paths so saved configurations work from any
    # Nextflow launch directory, including the optional launcher's output folder.
    runtime['fastq']=[str(Path(value).expanduser().resolve()) for value in a.fastq]
    for key in ['reference','bwa_index','tool_prefix','clinvar','annovar_dir','annovar_db',
                'vep_path','vep_cache','traits_panel','gwas_catalog','germline_resource',
                'contamination_sites','regions','glimpse_reference_dir','glimpse_bin','bam','germline_vcf','somatic_vcf','matched_normal_bam']:
        if runtime.get(key): runtime[key]=str(Path(runtime[key]).expanduser().resolve())
    for k,v in list(runtime.items()):
        if isinstance(v,Path): runtime[k]=str(v.expanduser().resolve())
    import patient_metadata
    for key in ['patients','patient_database']:
        if runtime.get(key):runtime[key]=str(Path(runtime[key]).expanduser().resolve())
    patient_metadata.prepare(runtime)
    warnings=[]; errors=[]
    if not a.allow_test_reference and a.memory<8: errors.append('The hg38 alignment requires a memory budget of at least 8GB.')
    def check(fn):
        try: return fn()
        except (ValueError,OSError,subprocess.TimeoutExpired) as e: errors.append(str(e)); return None
    ref=check(lambda: checked_path(a.reference or LOCAL/'references/samurai_hg38/genome.fa'))
    if ref:
        runtime['reference']=str(ref)
        for path in [Path(str(ref)+'.fai'),ref.with_suffix('.dict')]: check(lambda path=path: checked_path(path))
        if Path(str(ref)+'.fai').exists():
            lengths={x.split('\t')[0]:int(x.split('\t')[1]) for x in Path(str(ref)+'.fai').read_text().splitlines()}
            if not a.allow_test_reference and lengths.get('chr1',lengths.get('1'))!=248956422: errors.append('Reference does not have the expected GRCh38 chromosome 1 length (248956422).')
            runtime['chrom_prefix']='chr' if 'chr1' in lengths else ''
        idx=a.bwa_index or (str(ref.parent/'bwa/genome') if (ref.parent/'bwa/genome.bwt').exists() else str(ref))
        runtime['bwa_index']=str(Path(idx).expanduser().resolve())
        if not a.bam:
            for ext in ['.amb','.ann','.bwt','.pac','.sa']: check(lambda ext=ext: checked_path(runtime['bwa_index']+ext))
    names=['samtools','bcftools','gatk','perl'] if a.bam else ['bwa','samtools','bcftools','gatk','fastp','perl']
    runtime['tools']={name:check(lambda name=name: find_tool(name,a.tool_prefix)) for name in names}
    versions={};version_outputs={}
    for name,cmd in [('bwa',[]),('samtools',['--version']),('bcftools',['--version']),('fastp',['--version']),('gatk',['--version'])]:
        executable=runtime['tools'].get(name)
        if not executable: continue
        try:
            proc=subprocess.run([executable,*cmd],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
            version_outputs[name]=proc.stdout
            lines=proc.stdout.splitlines()
            versions[name]=next((ln for ln in lines if re.search(r'Version:|Genome Analysis Toolkit|^samtools |^bcftools |^fastp ',ln)), '\n'.join(lines[:3]))
            if proc.returncode!=0 and name!='bwa': errors.append(f'{name} exists but its version probe failed: {versions[name]}')
        except (OSError,subprocess.TimeoutExpired) as e: errors.append(f'{name} cannot run: {e}')
    from bcftools_compat import require_version, trait_call_options, version_diagnostic
    if runtime['tools'].get('bcftools'):
        caller_runtime=require_version(version_outputs.get('bcftools',''),runtime['tools']['bcftools'])
        versions['bcftools_htslib']=caller_runtime['htslib']
        print(version_diagnostic(caller_runtime),flush=True)
    runtime['versions']=versions
    trait_options=check(lambda: trait_call_options(runtime['tools']['bcftools']))
    if trait_options:
        print('Traits caller compatibility verified: '+versions.get('bcftools','bcftools')+
              ' ('+' '.join(trait_options)+')',flush=True)
    if a.bam:
        import import_alignment
        check(lambda: import_alignment.validate_bam(runtime['bam'],sample,runtime['reference'],runtime['tools']['samtools']))
    if bool(a.matched_normal_bam) != bool(a.matched_normal_sample): errors.append('Provide both --matched-normal-bam and --matched-normal-sample')
    if a.matched_normal_bam:
        import import_alignment
        if a.matched_normal_sample==sample:errors.append('Tumor and normal sample IDs must differ')
        check(lambda:import_alignment.validate_bam(runtime['matched_normal_bam'],a.matched_normal_sample,runtime['reference'],runtime['tools']['samtools']))
    for kind in ['germline','somatic']:
        value=runtime.get(kind+'_vcf')
        if value:
            if not a.bam: errors.append('Imported callsets require --bam')
            check(lambda value=value: checked_path(value))
    source_hash=hashlib.sha256()
    for source in [ROOT/'main.nf',ROOT/'nextflow.config',*sorted((ROOT/'bin').glob('*.py'))]:
        source_hash.update(str(source.relative_to(ROOT)).encode())
        source_hash.update(source.read_bytes())
    runtime['pipeline_source_sha256']=source_hash.hexdigest()
    runtime['annotation']=check(lambda: choose_annotation(a))
    if runtime['annotation']: print(runtime['annotation']['message'],flush=True)
    clinical=check(lambda: checked_path(a.clinvar or LOCAL/'resources/clinvar/clinvar_GRCh38.vcf.gz'))
    runtime['clinvar']=str(clinical) if clinical else None
    if clinical and ref and Path(str(ref)+'.fai').is_file():
        import clinvar_resource
        check(lambda: clinvar_resource.inspect(clinical,ref,allow_test_reference=a.allow_test_reference))
    for key in ['germline_resource','contamination_sites','regions']:
        value=getattr(a,key)
        runtime[key]=str(check(lambda value=value: checked_path(value))) if value else None
        if value and key!='regions' and not any(Path(value+ext).exists() for ext in ['.tbi','.csi']): errors.append(f'{key} requires a .tbi or .csi index')
    normal_rows=[]
    panel_result=check(lambda: prepare_panel(a.panel_of_normals,sample,lanes,a.single_end,a.library))
    if panel_result:
        runtime['panel_of_normals'],runtime['pon'],normal_rows=panel_result
        if runtime['pon']['mode']=='build':
            count=len(runtime['pon']['normals'])
            print(f'Panel of normals: build from {count} samples: '+', '.join(n['sample'] for n in runtime['pon']['normals']),flush=True)
            if count<40: warnings.append(f'Small somatic panel of normals: {count} samples; sites must recur in at least 2. GATK recommends aiming for at least 40; artifact filtering is limited, especially at low coverage.')
    if not a.germline_resource: warnings.append('Mutect2 has no population AF resource; unpaired somatic/germline separation is limited.')
    if not a.panel_of_normals: warnings.append('Mutect2 has no somatic panel of normals.')
    if not a.contamination_sites: warnings.append('Contamination estimation is not configured.')
    runtime['traits_panel']=str(check(lambda: checked_path(a.traits_panel or ROOT/'data/traits.expanded.GRCh38.tsv')))
    if runtime['traits_panel']!='None': check(lambda: validate_trait_panel(runtime['traits_panel']))
    if a.gwas:
        gwas=check(lambda: checked_path(a.gwas_catalog or a.resource_dir/'gwas/associations.GRCh38.tsv.gz'))
        runtime['gwas_catalog']=str(gwas) if gwas else None
    if not a.no_ancestry:
        anc=a.resource_dir/'ancestry'
        runtime['ancestry_dir']=str(anc.resolve())
        for name in ['somalier','sites.hg38.vcf.gz','labels.tsv','reference/complete.json']: check(lambda name=name: checked_path(anc/name))
    if (a.gwas or not a.no_ancestry) and any('Missing path' in x and str(a.resource_dir) in x for x in errors):
        errors.append('Prepare public resources with: /usr/bin/python3 bin/prepare_resources.py --gwas --ancestry')
    runtime['ancestry_explorer']=a.ancestry_explorer and not a.no_ancestry
    if runtime['ancestry_explorer']:
        runtime['ancestry_explorer_manifest']=str(checked_path(a.ancestry_explorer_manifest or ROOT/'data/ancestry_explorer.GRCh38.json'))
        runtime['ancestry_explorer_cache']=str(Path(a.ancestry_explorer_cache or ROOT/'resources/ancestry_explorer').expanduser().resolve())
        import importlib.util
        for package in ['numpy','scipy','pysam']:
            if importlib.util.find_spec(package) is None: errors.append('Missing ancestry Python package '+package)
        if not shutil.which('java'):errors.append('Java is required for mitochondrial lineage classification')
    if a.explain_findings:
        runtime['explanation_evidence']=str(checked_path(a.explanation_evidence or ROOT/'data/finding_evidence.GRCh38.json'))
        evidence=json.loads(Path(runtime['explanation_evidence']).read_text())
        if evidence.get('genome_build')!='GRCh38': errors.append('Explanation evidence must use GRCh38')
        runtime['literature_cache']=str(Path(a.literature_cache or ROOT/'resources/literature').expanduser().resolve())
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',a.literature_release): errors.append('Literature release must be a simple version label')
    import llm_reporting
    check(lambda:llm_reporting.prepare(runtime))
    if a.polygenic or a.imputed_clinvar or a.explain_findings:
        import importlib.util
        for package in ['pysam','openpyxl']:
            if importlib.util.find_spec(package) is None: errors.append('Missing Python package '+package+' for extended annotation/scoring')
    if a.polygenic:
        import polygenic
        runtime['pgs_manifest']=str(checked_path(a.pgs_manifest or ROOT/'data/pgs.broad.GRCh38.json'))
        models=check(lambda:polygenic.load_manifest(runtime['pgs_manifest']))
        runtime['pgs_ids']=[m['id'] for m in models['models']] if models else []
        runtime['pgs_source_files']=[file_meta(m['scoring_file']) for m in models['models'] if m.get('scoring_file')] if models else []
        runtime['pgs_cache']=str(Path(a.pgs_cache or ROOT/'resources/pgs').expanduser().resolve())
    runtime['glimpse_chromosomes']=[runtime.get('chrom_prefix','chr')+str(x) for x in chromosomes]
    runtime['core_pipeline_sha256']=continuation.core_digest(ROOT/'bin/pipeline.py')
    if a.glimpse_reference_dir: check(lambda: checked_path(a.glimpse_reference_dir))
    if a.glimpse_bin: check(lambda: checked_path(a.glimpse_bin))
    runtime['test_mode']=bool(a.test_reads or a.allow_test_reference)
    if runtime['test_mode']: warnings.append('SMOKE TEST ONLY: subset reads or synthetic reference. Do not interpret as patient findings.')
    runtime['warnings']=warnings
    rows=[]; provenance=[]
    for n,(name,reads) in enumerate(lanes,1):
        lane=f'lane{n:03d}'
        rows.append({'role':'patient','sample':sample,'lane':lane,'library':a.library,'fastq_1':str(reads[0]),'fastq_2':str(reads[1]) if len(reads)>1 else ''})
        provenance.extend(file_meta(x) for x in reads)
    runtime['inputs']=provenance
    if a.bam:
        runtime['inputs']=[file_meta(runtime['bam'])]
        rows=[{'role':'patient','sample':sample,'lane':'imported','library':a.library,'fastq_1':'','fastq_2':''}]
    for key in ['germline_vcf','somatic_vcf','matched_normal_bam']:
        if runtime.get(key):runtime['inputs'].append(file_meta(runtime[key]))
    for key in ['resume','continue_run','preflight_only','work_dir','reuse_from','reference_reuse_from']:
        runtime.pop(key,None)
    # Resources and input mtimes are part of the runtime and thus the task cache key.
    resource_meta=[]
    for key in ['reference','clinvar','gwas_catalog','germline_resource','panel_of_normals','contamination_sites','regions','traits_panel','pgs_manifest','explanation_evidence','ancestry_explorer_manifest']:
        value=runtime.get(key)
        if isinstance(value,str) and Path(value).is_file(): resource_meta.append(file_meta(value))
    if runtime.get('pon',{}).get('index'): resource_meta.append(file_meta(runtime['pon']['index']))
    ann=runtime.get('annotation') or {}
    if ann.get('engine')=='annovar':
        for suffix in ['.txt','Mrna.fa']: resource_meta.append(file_meta(Path(ann['database'])/f"hg38_{ann['protocol']}{suffix}"))
    if not a.no_ancestry:
        for name in ['somalier','sites.hg38.vcf.gz','labels.tsv','reference/complete.json']:
            path=a.resource_dir/'ancestry'/name
            if path.is_file(): resource_meta.append(file_meta(path))
    runtime['resources']=resource_meta
    with (info/'input_manifest.tsv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['role','sample','lane','library','fastq_1','fastq_2'],delimiter='\t'); w.writeheader(); w.writerows(rows+normal_rows)
    (info/'preflight.json').write_text(json.dumps({'sample':sample,'lanes':len(lanes),'errors':errors,'warnings':warnings,'runtime':runtime},indent=2)+'\n')
    for w in warnings: print('WARNING: '+w,file=sys.stderr)
    if errors:
        raise ValueError('Preflight failed:\n'+'\n'.join(errors))
    import reuse_outputs
    source_output = a.reuse_from.expanduser().resolve() if a.reuse_from else output
    if a.reuse_from and not a.continue_run:
        raise ValueError('--reuse-from requires --continue')
    if a.reuse_from and not (source_output/'pipeline_info/runtime.json').is_file():
        raise ValueError('--reuse-from must point to an existing pipeline output folder')
    if source_output != output and continuation.active_work(source_output):
        raise ValueError('The source run is still active; cannot adopt changing results')
    source_reused = continuation.collect(runtime,None,ROOT,source_output) if a.continue_run and source_output != output else {}
    reused = {**source_reused, **(continuation.collect(runtime,previous,ROOT,output,a.work_dir) if a.continue_run else {})}
    reuse_outputs.annotation_outputs(reused,sample)
    from reuse_validation import Validator, summary
    validator=Validator()
    validator.core(reused,sample)
    import glimpse_continuation
    # Validate shared panels before collecting phases: a source sample may have
    # borrowed every reference, leaving no reference tasks in its own work tree.
    shared={'references':{}}
    if a.continue_run and a.glimpse2:
        from shared_reference import collect_shared_references
        donors=list(dict.fromkeys([p for p in [a.reference_reuse_from,source_output,output] if p]))
        shared=collect_shared_references(runtime,ROOT,donors)
        (info/'shared_reference_reuse.json').write_text(json.dumps(shared,indent=2)+'\n')
    imputation_reused = (glimpse_continuation.collect(runtime,ROOT,output,reused,a.work_dir,reference_reused=shared['references'])
                         if a.continue_run and a.glimpse2 else {'references':{},'phases':{},'ligated':{}})
    if a.continue_run and a.glimpse2 and source_output != output:
        source_imputation = glimpse_continuation.collect(runtime,ROOT,source_output,reused,reference_reused=shared['references'])
        for kind, values in source_imputation.items():
            imputation_reused[kind] = {**values, **imputation_reused[kind]}
    validator.imputation(imputation_reused,sample)
    reuse_outputs.publish_reused(output,sample,reused,imputation_reused)
    import analysis_continuation
    analysis_reused=analysis_continuation.collect(runtime,ROOT,list(dict.fromkeys([source_output,output])),reused,imputation_reused,publish=False) if a.continue_run else {k:{} for k in analysis_continuation.CODE}
    validator.analyses(analysis_reused,sample)
    analysis_continuation.publish_reused(runtime,analysis_reused)
    cached_trait_files=analysis_continuation.trait_inputs(runtime,ROOT,list(dict.fromkeys([source_output,output])),reused,imputation_reused) if a.continue_run else []
    if cached_trait_files and not validator.group('trait_inputs',sample,cached_trait_files,sample):cached_trait_files=[]
    reuse_check=summary(runtime,reused,imputation_reused,analysis_reused,validator,[source_output,output],a.work_dir)
    (info/'reuse_validation.json').write_text(json.dumps(reuse_check,indent=2)+'\n')
    (info/'analysis_continuation.json').write_text(json.dumps(analysis_reused,indent=2)+'\n')
    print('Continuation: downstream analyses '+', '.join(k+'='+str(len(v)) for k,v in analysis_reused.items()),flush=True)
    (info/'continuation.json').write_text(json.dumps({'enabled':a.continue_run,'reused':reused,
                                                    'imputation_reused':imputation_reused},indent=2)+'\n')
    print(f'Continuation: {len(reused)} verified core tasks available',flush=True)
    if a.glimpse2:
        print(f'Continuation: {len(imputation_reused["references"])} verified GLIMPSE references / '
              f'{len(imputation_reused["phases"])} completed imputation chunks / '
              f'{len(imputation_reused["ligated"])} ligated chromosomes available',flush=True)
    data=json.dumps(runtime,indent=2)+'\n'
    runtime_dir=info/'runtimes';runtime_dir.mkdir(exist_ok=True)
    runtime_path=runtime_dir/(hashlib.sha256(data.encode()).hexdigest()+'.json')
    if not runtime_path.exists(): runtime_path.write_text(data)
    if not prior_path.exists() or prior_path.read_text()!=data: prior_path.write_text(data)
    code_dir=info/'code'/runtime['pipeline_source_sha256']/'bin'
    code_dir.mkdir(parents=True,exist_ok=True)
    for source in sorted((ROOT/'bin').glob('*.py')):
        dest=code_dir/source.name
        if not dest.exists(): shutil.copy2(source,dest)
        elif dest.read_bytes()!=source.read_bytes(): raise ValueError(f'Immutable code snapshot changed: {dest}')
    nf={'manifest':str(info/'input_manifest.tsv'),'runtime':str(runtime_path),'output':str(output),
        'threads':a.threads,'memory_gb':a.memory,'python':runtime['python']}
    (info/'nextflow_params.json').write_text(json.dumps(nf,indent=2)+'\n')
    print(f'Preflight passed: {len(lanes)} lane(s); {info}/preflight.json',flush=True)
    return {**nf,'reuse_validation':reuse_check,'bam':runtime.get('bam'),'germline_vcf':runtime.get('germline_vcf'),'somatic_vcf':runtime.get('somatic_vcf'),'code':str(code_dir),'sample':sample,'lane_rows':rows+normal_rows,'reused':reused,'imputation_reused':imputation_reused,'analysis_reused':analysis_reused,'ancestry_explorer':runtime['ancestry_explorer'],'cached_trait_files':cached_trait_files,'explain_findings':a.explain_findings,'interpretation_report':a.interpretation_report,'llm_threads':a.llm_threads,'glimpse2':a.glimpse2,'glimpse_chromosomes':runtime['glimpse_chromosomes'],'polygenic':a.polygenic,'imputed_clinvar':a.imputed_clinvar,'pgs_ids':runtime.get('pgs_ids',[]),'pgs_reference_stats':a.pgs_reference_stats,'unified_vcf':a.unified_vcf,'homozygosity_report':a.homozygosity_report,'cardiomyopathy_report':a.cardiomyopathy_report,'cancer_report':a.cancer_report,'pon_mode':runtime['pon']['mode'],'pon_vcf':runtime['pon']['vcf'],'pon_index':runtime['pon']['index']}


def main(argv=None):
    a=parser().parse_args(argv)
    try:
        nf=prepare(a)
        if a.preflight_only: return 0
        if not shutil.which('nextflow'): raise ValueError('Nextflow not found on PATH')
        output=Path(nf['output']); info=output/'pipeline_info'
        work=(a.work_dir or output/'work').resolve()
        command=['nextflow','-log',str(info/'nextflow.log'),'run',str(ROOT/'main.nf'),
                 '-params-file',str(info/'nextflow_params.json'),'-work-dir',str(work),'-ansi-log','false']
        if a.resume or a.continue_run: command.append('-resume')
        if a.continue_run: command.append('--continue')
        print(f'Running with total budget {a.threads} CPUs / {a.memory:g} GB; output {output}',flush=True)
        env=os.environ.copy()
        env.update(NXF_OPTS='-Xms128m -Xmx1g',NXF_OFFLINE='true',NXF_DISABLE_CHECK_LATEST='true')
        return subprocess.call(command,cwd=output,env=env)
    except (ValueError,OSError,subprocess.CalledProcessError) as e:
        print(f'ERROR: {e}',file=sys.stderr); return 2

if __name__=='__main__': sys.exit(main())
