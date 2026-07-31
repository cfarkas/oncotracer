#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: run_illumina_samurai_fastq.sh --samplesheet FILE --outdir DIR [options]

Run the pinned SAMURAI/qDNAseq Illumina LP-WGS step from FASTQ input.
The samplesheet is a CSV with columns:
  sample,fastq_1,fastq_2,status
For single-end data, keep the fastq_2 column and leave its value empty.
Optional extra columns such as gender are ignored for the upstream SAMURAI FASTQ run.

Required:
  --samplesheet FILE       Illumina FASTQ samplesheet CSV.
  --outdir DIR             SAMURAI output directory used by OncoTracer.

Options:
  --analysis_type VALUE    SAMURAI analysis type [solid_biopsy]
  --caller VALUE           SAMURAI CNA caller [qdnaseq]
  --binsize INT            qDNAseq bin size in kb [100]
  --aligner VALUE          SAMURAI FASTQ aligner [bwamem]
  --ref FILE               hg38 FASTA. Defaults to $LPWGS_ROOT/references/samurai_hg38/genome.fa
  --lpwgs-root DIR         Project/data root used for references and caches [/media/server/STORAGE/LPWGS_2025]
  --profile NAME           SAMURAI Nextflow profile: docker, singularity, or conda [singularity]
  --build-pon              Build and apply a local qDNAseq PoN from NORMAL rows.
  --pon-normal-samples CSV Exact NORMAL sample IDs required for the PoN.
  --pon-min-normals INT    Minimum number of NORMAL samples [2].
  --pon-name NAME          Reproducible PoN identifier [illumina_local_PoN].
  --pon-min-mapq INT       qDNAseq BAM MAPQ threshold [37].
  --pon-r-container URI    qDNAseq R container image.
                           [docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1]
  --force                  Let SAMURAI resume/recompute as needed.
  -h, --help               Show this help.
EOF_USAGE
}

LPWGS_ROOT="${LPWGS_ROOT:-/media/server/STORAGE/LPWGS_2025}"
SAMPLESHEET=""
OUTDIR=""
ANALYSIS_TYPE="solid_biopsy"
CALLER="qdnaseq"
BINSIZE="100"
ALIGNER="bwamem"
REF_FA=""
FORCE="false"
SAMURAI_PROFILE="singularity"
BUILD_PON="false"
PON_NORMAL_SAMPLES=""
PON_MIN_NORMALS="2"
PON_NAME="illumina_local_PoN"
PON_MIN_MAPQ="37"
PON_R_CONTAINER="docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --samplesheet) SAMPLESHEET="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --analysis_type) ANALYSIS_TYPE="$2"; shift 2 ;;
    --caller) CALLER="$2"; shift 2 ;;
    --binsize) BINSIZE="$2"; shift 2 ;;
    --aligner) ALIGNER="$2"; shift 2 ;;
    --ref) REF_FA="$2"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --profile) SAMURAI_PROFILE="$2"; shift 2 ;;
    --force) FORCE="true"; shift ;;
    --build-pon) BUILD_PON="true"; shift ;;
    --pon-normal-samples) PON_NORMAL_SAMPLES="$2"; shift 2 ;;
    --pon-min-normals) PON_MIN_NORMALS="$2"; shift 2 ;;
    --pon-name) PON_NAME="$2"; shift 2 ;;
    --pon-min-mapq) PON_MIN_MAPQ="$2"; shift 2 ;;
    --pon-r-container) PON_R_CONTAINER="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$SAMPLESHEET" ]] || { echo "ERROR: --samplesheet is required" >&2; exit 2; }
[[ -n "$OUTDIR" ]] || { echo "ERROR: --outdir is required" >&2; exit 2; }
[[ -s "$SAMPLESHEET" ]] || { echo "ERROR: samplesheet not found or empty: $SAMPLESHEET" >&2; exit 1; }
[[ "$SAMURAI_PROFILE" == "docker" || "$SAMURAI_PROFILE" == "singularity" || "$SAMURAI_PROFILE" == "conda" ]] || { echo "ERROR: --profile must be docker, singularity, or conda" >&2; exit 1; }
command -v nextflow >/dev/null 2>&1 || { echo "ERROR: nextflow is required to launch SAMURAI" >&2; exit 1; }
command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools is required to prepare the hg38 reference index" >&2; exit 1; }

LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
[[ "$PON_MIN_NORMALS" =~ ^[0-9]+$ && "$PON_MIN_NORMALS" -ge 2 ]] || { echo "ERROR: --pon-min-normals must be an integer >= 2" >&2; exit 1; }
[[ "$PON_MIN_MAPQ" =~ ^[0-9]+$ ]] || { echo "ERROR: --pon-min-mapq must be a non-negative integer" >&2; exit 1; }
[[ "$PON_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "ERROR: --pon-name must contain only letters, digits, dot, underscore, and dash" >&2; exit 1; }
[[ -n "$PON_R_CONTAINER" ]] || { echo "ERROR: --pon-r-container cannot be empty" >&2; exit 1; }
[[ "$BUILD_PON" != "true" || "$CALLER" == "qdnaseq" ]] || { echo "ERROR: Illumina PoN construction requires --caller qdnaseq" >&2; exit 1; }
RUN_ROOT="$(readlink -m "$OUTDIR")"
REF_DIR_DEFAULT="$LPWGS_ROOT/references/samurai_hg38"
[[ -n "$REF_FA" ]] || REF_FA="$REF_DIR_DEFAULT/genome.fa"
REF_FA="$(readlink -m "$REF_FA")"
REF_FAI="${REF_FA}.fai"
DICT="${REF_FA%.fa}.dict"
LOCAL_CONFIG="$RUN_ROOT/samurai_hg38.config"
RUN_SAMPLESHEET="$RUN_ROOT/input/samplesheet.csv"
BWA_INDEX_DIR="$(dirname "$REF_FA")/bwa"

bwa_index_valid() {
  local index_dir="$1" extension
  for extension in amb ann bwt pac sa; do
    [[ -s "$index_dir/genome.$extension" ]] || return 1
  done
}

persist_bwa_index() (
  local source_dir="$1" destination_dir="$2" destination_parent temp_dir=""
  destination_parent="$(dirname "$destination_dir")"

  cleanup_bwa_index_temp() {
    [[ -z "$temp_dir" ]] || rm -rf -- "$temp_dir"
  }
  trap cleanup_bwa_index_temp EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  bwa_index_valid "$destination_dir" && return 0
  mkdir -p "$destination_parent" || return 1
  temp_dir="$(mktemp -d "${destination_dir}.tmp.XXXXXX")" || return 1

  if ! cp -al "$source_dir/." "$temp_dir/" 2>/dev/null; then
    rm -rf -- "$temp_dir" || return 1
    temp_dir="$(mktemp -d "${destination_dir}.tmp.XXXXXX")" || return 1
    cp -a --reflink=auto "$source_dir/." "$temp_dir/" || return 1
  fi
  bwa_index_valid "$temp_dir" || return 1

  if mv -T "$temp_dir" "$destination_dir" 2>/dev/null; then
    temp_dir=""
    return 0
  fi

  # A concurrent run may have published the same cache first.
  bwa_index_valid "$destination_dir"
)


download_samurai_hg38_reference() {
  local ref_dir="$1" fasta="$1/genome.fa" fai="$1/genome.fa.fai" dict="$1/genome.dict"
  local s3="s3://ngi-igenomes/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
  local https="https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
  mkdir -p "$ref_dir"
  [[ -s "$fasta" && -s "$fai" && -s "$dict" ]] && return 0
  echo "Downloading SAMURAI/iGenomes UCSC hg38 reference to $ref_dir"
  if command -v aws >/dev/null 2>&1; then
    [[ -s "$fasta" ]] || aws s3 cp --no-sign-request "$s3/genome.fa" "$fasta"
    [[ -s "$fai" ]] || aws s3 cp --no-sign-request "$s3/genome.fa.fai" "$fai"
    [[ -s "$dict" ]] || aws s3 cp --no-sign-request "$s3/genome.dict" "$dict"
  else
    [[ -s "$fasta" ]] || curl -L -o "$fasta" "$https/genome.fa"
    [[ -s "$fai" ]] || curl -L -o "$fai" "$https/genome.fa.fai"
    [[ -s "$dict" ]] || curl -L -o "$dict" "$https/genome.dict"
  fi
}

[[ "$REF_FA" == "$REF_DIR_DEFAULT/genome.fa" ]] && download_samurai_hg38_reference "$REF_DIR_DEFAULT"

mkdir -p "$RUN_ROOT"/{input,logs,work,tmp,nextflow_launch}
mkdir -p "$LPWGS_ROOT/.singularity_cache"

python3 - "$SAMPLESHEET" "$RUN_SAMPLESHEET" "$BUILD_PON" "$PON_NORMAL_SAMPLES" "$PON_MIN_NORMALS" <<'PY_VALIDATE'
import csv, re, sys
from pathlib import Path

src, dst = map(Path, sys.argv[1:3])
build_pon = sys.argv[3] == 'true'
requested_normals = [item.strip() for item in sys.argv[4].split(',') if item.strip()]
min_normals = int(sys.argv[5])
required = ['sample', 'fastq_1', 'fastq_2', 'status']
with src.open(newline='') as handle:
    reader = csv.DictReader(handle)
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"ERROR: samplesheet is missing column(s): {', '.join(missing)}")
    rows = []
    seen = set()
    for row in reader:
        sample = (row.get('sample') or '').strip()
        fq1 = (row.get('fastq_1') or '').strip()
        fq2 = (row.get('fastq_2') or '').strip()
        status = ((row.get('status') or 'tumor').strip() or 'tumor').lower()
        if not sample:
            raise SystemExit('ERROR: samplesheet contains a row with empty sample')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', sample):
            raise SystemExit(f'ERROR: invalid sample ID {sample!r}; use letters, digits, dot, underscore, or dash')
        if sample in seen:
            raise SystemExit(f'ERROR: duplicate sample ID in samplesheet: {sample}')
        seen.add(sample)
        if not fq1:
            raise SystemExit(f'ERROR: sample {sample} must have fastq_1')
        if not Path(fq1).is_file() or Path(fq1).stat().st_size == 0:
            raise SystemExit(f'ERROR: fastq_1 is missing or empty for {sample}: {fq1}')
        if fq2 and (not Path(fq2).is_file() or Path(fq2).stat().st_size == 0):
            raise SystemExit(f'ERROR: fastq_2 is missing or empty for {sample}: {fq2}')
        if status not in {'tumor', 'normal'}:
            raise SystemExit(f'ERROR: status for {sample} must be tumor or normal, found: {status}')
        rows.append({'sample': sample, 'fastq_1': fq1, 'fastq_2': fq2, 'status': status})
if not rows:
    raise SystemExit('ERROR: samplesheet has no samples')
tumors = [row['sample'] for row in rows if row['status'] == 'tumor']
normals = [row['sample'] for row in rows if row['status'] == 'normal']
if not tumors:
    raise SystemExit('ERROR: Illumina analysis requires at least one TUMOR sample')
if len(requested_normals) != len(set(requested_normals)):
    raise SystemExit('ERROR: --pon-normal-samples contains duplicate sample IDs')
if build_pon:
    if len(normals) < min_normals:
        raise SystemExit(f'ERROR: local Illumina PoN requires at least {min_normals} NORMAL samples; found {len(normals)}')
    if not requested_normals:
        raise SystemExit('ERROR: --build-pon requires --pon-normal-samples with explicit IDs')
    if set(requested_normals) != set(normals) or len(requested_normals) != len(normals):
        raise SystemExit(f"ERROR: requested PoN normals {requested_normals} do not exactly match samplesheet NORMAL rows {normals}")
elif normals:
    raise SystemExit('ERROR: samplesheet contains NORMAL rows but --build-pon is off; refusing to ignore controls')
elif requested_normals:
    raise SystemExit('ERROR: --pon-normal-samples was provided while --build-pon is off')

tmp = dst.with_name(dst.name + '.tmp')
try:
    with tmp.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=required)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(dst)
finally:
    tmp.unlink(missing_ok=True)
print(f'Validated {len(tumors)} TUMOR and {len(normals)} NORMAL Illumina sample(s): {dst}')
PY_VALIDATE

READ_LAYOUT="$(python3 - "$RUN_SAMPLESHEET" <<'PY_LAYOUT'
import csv, sys
with open(sys.argv[1], newline='') as handle:
    paired = [bool((row.get('fastq_2') or '').strip()) for row in csv.DictReader(handle)]
if all(paired):
    print('paired')
elif not any(paired):
    print('single')
else:
    raise SystemExit('ERROR: a single run cannot mix single-end and paired-end Illumina libraries')
PY_LAYOUT
)"
if [[ "$READ_LAYOUT" == "single" ]]; then
  QDNASEQ_LAYOUT_ARGS=(--qdnaseq_paired_ends false)
else
  QDNASEQ_LAYOUT_ARGS=(--qdnaseq_paired_ends true)
fi
echo "Detected Illumina read layout: $READ_LAYOUT-end"

if [[ ! -s "$REF_FA" ]]; then
  echo "ERROR: missing reference FASTA: $REF_FA" >&2
  echo "       Install the SAMURAI hg38 reference or pass --ref /path/to/genome.fa" >&2
  exit 1
fi

[[ -s "$REF_FAI" ]] || samtools faidx "$REF_FA"
if [[ ! -s "$DICT" ]]; then
  if command -v picard >/dev/null 2>&1; then
    picard CreateSequenceDictionary R="$REF_FA" O="$DICT"
  else
    samtools dict "$REF_FA" > "$DICT"
  fi
fi

INDEX_ARGS=(--index_genome true)
if bwa_index_valid "$BWA_INDEX_DIR"; then
  echo "Reusing persistent BWA index: $BWA_INDEX_DIR"
  INDEX_ARGS=(--aligner_index "$BWA_INDEX_DIR" --index_genome false)
else
  echo "No persistent BWA index found; SAMURAI will build it once."
fi

cat > "$LOCAL_CONFIG" <<EOF_CONFIG
params {
  genomes {
    hg38 {
      fasta     = "${REF_FA}"
      fasta_fai = "${REF_FAI}"
      dict      = "${DICT}"
      bwa       = "${BWA_INDEX_DIR}"
    }
  }
}
report { overwrite = true }
timeline { overwrite = true }
EOF_CONFIG

export NXF_SYNTAX_PARSER="v1"
export NXF_HOME="$RUN_ROOT/.nextflow"
mkdir -p "$NXF_HOME" "$NXF_HOME/plugins"
export NXF_PLUGINS_DIR="$NXF_HOME/plugins"
export NXF_ASSETS="$LPWGS_ROOT/.oncotracer/nxf-assets"
mkdir -p "$NXF_ASSETS"
export NXF_WORK="$RUN_ROOT/work"
export NXF_SINGULARITY_CACHEDIR="$LPWGS_ROOT/.singularity_cache"
unset DISPLAY

if [[ "$FORCE" == "true" ]]; then
  rm -f "$RUN_ROOT/logs/samurai_illumina_done.txt"
fi

cd "$RUN_ROOT/nextflow_launch"
nextflow run dincalcilab/samurai -r v1.4.0 \
  -c "$LOCAL_CONFIG" \
  -profile "$SAMURAI_PROFILE" \
  -work-dir "$NXF_WORK" \
  --input "$RUN_SAMPLESHEET" \
  --outdir "$RUN_ROOT" \
  --genome hg38 \
  --analysis_type "$ANALYSIS_TYPE" \
  --caller "$CALLER" \
  --binsize "$BINSIZE" \
  --aligner "$ALIGNER" \
  "${INDEX_ARGS[@]}" \
  "${QDNASEQ_LAYOUT_ARGS[@]}" \
  --run_fastp false \
  -resume

if ! bwa_index_valid "$BWA_INDEX_DIR" && bwa_index_valid "$RUN_ROOT/genome_index/bwa"; then
  echo "Saving the BWA index for later runs: $BWA_INDEX_DIR"
  if ! persist_bwa_index "$RUN_ROOT/genome_index/bwa" "$BWA_INDEX_DIR"; then
    echo "WARN: could not persist the BWA index; this completed run remains valid." >&2
  fi
fi

if ! compgen -G "$RUN_ROOT/alignment/*.bam" >/dev/null; then
  echo "WARN: SAMURAI FASTQ mode did not publish alignment; falling back to host bwa/samtools and SAMURAI BAM mode" >&2
  command -v bwa >/dev/null 2>&1 || { echo "ERROR: bwa is required for Illumina FASTQ fallback alignment" >&2; exit 1; }
  command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools is required for Illumina FASTQ fallback alignment" >&2; exit 1; }
  mkdir -p "$RUN_ROOT/alignment"
  BAM_SAMPLESHEET="$RUN_ROOT/input/bam.samplesheet.csv"
  python3 - "$RUN_SAMPLESHEET" "$BAM_SAMPLESHEET" "$RUN_ROOT/alignment" "$REF_FA" "$BWA_INDEX_DIR" <<'PY_BAM_FALLBACK'
import csv, subprocess, sys
from pathlib import Path
fastq_sheet, bam_sheet, align_dir, ref, persistent_index = sys.argv[1:6]
align_dir = Path(align_dir)
run_index = align_dir.parent / "genome_index" / "bwa" / "genome"
persistent_index = Path(persistent_index) / "genome"

def bwa_index_exists(prefix):
    return all(
        (path := Path(str(prefix) + suffix)).is_file() and path.stat().st_size > 0
        for suffix in ('.amb', '.ann', '.bwt', '.pac', '.sa')
    )

if bwa_index_exists(persistent_index):
    ref = str(persistent_index)
elif bwa_index_exists(run_index):
    ref = str(run_index)
rows = []
with open(fastq_sheet, newline='') as handle:
    for row in csv.DictReader(handle):
        sample = row['sample']
        status = row.get('status') or 'tumor'
        fq1 = row['fastq_1']
        fq2 = (row.get('fastq_2') or '').strip()
        bam = align_dir / f'{sample}.bam'
        bai = Path(str(bam) + '.bai')
        if not bam.exists() or bam.stat().st_size == 0:
            # BWA parses escaped ``\t`` separators in the -R argument and
            # rejects literal tab bytes.
            read_group = rf'@RG\tID:{sample}\tPU:1\tSM:{sample}\tLB:{sample}\tPL:Illumina'
            reads = [fq1] + ([fq2] if fq2 else [])
            align = subprocess.Popen(
                ['bwa', 'mem', '-t', '8', '-R', read_group, ref, *reads],
                stdout=subprocess.PIPE,
            )
            try:
                subprocess.run(
                    ['samtools', 'sort', '-@', '4', '-o', str(bam), '-'],
                    stdin=align.stdout,
                    check=True,
                )
            finally:
                if align.stdout is not None:
                    align.stdout.close()
            if align.wait() != 0:
                raise subprocess.CalledProcessError(align.returncode, align.args)
        if not bai.exists() or bai.stat().st_size == 0:
            subprocess.run(['samtools', 'index', str(bam)], check=True)
        rows.append({'sample': sample, 'bam': str(bam), 'status': status})
with open(bam_sheet, 'w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['sample', 'bam', 'status'])
    writer.writeheader()
    writer.writerows(rows)
print(f'Prepared {len(rows)} BAM sample(s): {bam_sheet}')
PY_BAM_FALLBACK

  nextflow run dincalcilab/samurai -r v1.4.0 \
    -c "$LOCAL_CONFIG" \
    -profile "$SAMURAI_PROFILE" \
    -work-dir "$NXF_WORK" \
    --input "$BAM_SAMPLESHEET" \
    --outdir "$RUN_ROOT" \
    --genome hg38 \
    --analysis_type "$ANALYSIS_TYPE" \
    --caller "$CALLER" \
    --binsize "$BINSIZE" \
    "${QDNASEQ_LAYOUT_ARGS[@]}" \
    --index_genome false \
    -resume
fi

# Rebuild every published alignment index because a prior run can leave a stale BAI beside a new BAM.
for bam in "$RUN_ROOT"/alignment/*.bam; do
  [[ -s "$bam" ]] || continue
  bai="${bam}.bai"
  tmp_bai="${bai}.tmp.$$"
  rm -f "$tmp_bai"
  if ! samtools index -@ 4 "$bam" "$tmp_bai"; then
    rm -f "$tmp_bai"
    echo "ERROR: could not index published BAM: $bam" >&2
    exit 1
  fi
  mv -f "$tmp_bai" "$bai"
done

if [[ "$BUILD_PON" == "true" ]]; then
  PON_ALIGNMENT_DIR="$RUN_ROOT/pon_alignment"
  PON_BAM_SHEET="$RUN_ROOT/input/pon.bam.samplesheet.csv"
  PON_ALIGNMENT_MANIFEST="$RUN_ROOT/logs/pon_alignment_manifest.tsv"

  python3 - "$RUN_SAMPLESHEET" "$RUN_ROOT" "$PON_ALIGNMENT_DIR" "$PON_BAM_SHEET" "$PON_ALIGNMENT_MANIFEST" <<'PY_PREPARE_PON'
import csv, os, subprocess, sys
from pathlib import Path

sheet, run_root, pon_dir, pon_sheet, alignment_manifest = map(Path, sys.argv[1:6])
with sheet.open(newline='') as handle:
    rows = list(csv.DictReader(handle))
if not rows:
    raise SystemExit('ERROR: validated samplesheet is empty while preparing the PoN')

stages = [
    ('markduplicates', lambda sample: run_root / 'markduplicates' / f'{sample}_markdup.bam'),
    ('alignment', lambda sample: run_root / 'alignment' / f'{sample}.bam'),
]
selected_stage = None
selected = None
for stage, resolver in stages:
    candidate = {row['sample']: resolver(row['sample']) for row in rows}
    if all(path.is_file() and path.stat().st_size > 0 for path in candidate.values()):
        selected_stage = stage
        selected = candidate
        break
if selected is None:
    details = []
    for stage, resolver in stages:
        missing = [row['sample'] for row in rows if not resolver(row['sample']).is_file() or resolver(row['sample']).stat().st_size == 0]
        details.append(f"{stage} missing: {','.join(missing) or 'none'}")
    raise SystemExit('ERROR: no single BAM stage contains every tumor and normal; ' + '; '.join(details))

for sample, bam in selected.items():
    check = subprocess.run(['samtools', 'quickcheck', '-v', str(bam)], text=True, capture_output=True)
    if check.returncode != 0:
        detail = (check.stdout + check.stderr).strip()
        raise SystemExit(f'ERROR: invalid BAM for {sample}: {bam}: {detail}')
    bai = Path(str(bam) + '.bai')
    if not bai.is_file() or bai.stat().st_size == 0 or bai.stat().st_mtime_ns < bam.stat().st_mtime_ns:
        tmp_bai = Path(str(bai) + f'.tmp.{os.getpid()}')
        tmp_bai.unlink(missing_ok=True)
        try:
            subprocess.run(['samtools', 'index', '-b', '-@', '4', str(bam), str(tmp_bai)], check=True)
            os.replace(tmp_bai, bai)
        finally:
            tmp_bai.unlink(missing_ok=True)

pon_dir.mkdir(parents=True, exist_ok=True)
expected_names = {f"{row['sample']}.bam" for row in rows} | {f"{row['sample']}.bam.bai" for row in rows}
unexpected = sorted(path.name for path in pon_dir.iterdir() if path.name not in expected_names)
if unexpected:
    raise SystemExit(f"ERROR: unexpected files in PoN BAM directory {pon_dir}: {', '.join(unexpected)}")

pon_rows = []
manifest_rows = []
for row in rows:
    sample = row['sample']
    bam = selected[sample].resolve()
    bai = Path(str(bam) + '.bai').resolve()
    bam_link = pon_dir / f'{sample}.bam'
    bai_link = pon_dir / f'{sample}.bam.bai'
    for link, target in ((bam_link, bam), (bai_link, bai)):
        if os.path.lexists(link):
            if not link.is_symlink():
                raise SystemExit(f'ERROR: refusing to replace non-symlink PoN input: {link}')
            link.unlink()
        link.symlink_to(target)
    pon_rows.append({'sample': sample, 'bam': str(bam_link), 'status': row['status']})
    manifest_rows.append({
        'sample': sample,
        'status': row['status'],
        'source_stage': selected_stage,
        'source_bam': str(bam),
        'analysis_bam': str(bam_link),
    })

def atomic_table(path, fieldnames, data):
    tmp = path.with_name(path.name + f'.tmp.{os.getpid()}')
    try:
        with tmp.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter='\t' if path.suffix == '.tsv' else ',')
            writer.writeheader()
            writer.writerows(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

atomic_table(pon_sheet, ['sample', 'bam', 'status'], pon_rows)
atomic_table(alignment_manifest, ['sample', 'status', 'source_stage', 'source_bam', 'analysis_bam'], manifest_rows)
print(f'Prepared {len(rows)} coherent PoN BAM(s) from stage: {selected_stage}')
PY_PREPARE_PON

  if [[ "$READ_LAYOUT" == "paired" ]]; then
    PON_PAIRED_ENDS="true"
  else
    PON_PAIRED_ENDS="false"
  fi
  PON_HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/run_qdnaseq_local_pon.sh"
  [[ -s "$PON_HELPER" ]] || { echo "ERROR: local qDNAseq PoN helper not found: $PON_HELPER" >&2; exit 1; }
  bash "$PON_HELPER" \
    --samplesheet "$PON_BAM_SHEET" \
    --outdir "$RUN_ROOT/qdnaseq_local_pon" \
    --binsize "$BINSIZE" \
    --genome hg38 \
    --min-mapq "$PON_MIN_MAPQ" \
    --min-normals "$PON_MIN_NORMALS" \
    --paired-ends "$PON_PAIRED_ENDS" \
    --pon-name "$PON_NAME" \
    --profile "$SAMURAI_PROFILE" \
    --lpwgs-root "$LPWGS_ROOT" \
    --container "$PON_R_CONTAINER"

  [[ -s "$RUN_ROOT/qdnaseq_local_pon/pon/normal_panel_manifest.tsv" ]] || { echo "ERROR: local PoN normal manifest is missing" >&2; exit 1; }
  manifest_tmp="$RUN_ROOT/logs/normal_panel_manifest.tsv.tmp.$$"
  cp "$RUN_ROOT/qdnaseq_local_pon/pon/normal_panel_manifest.tsv" "$manifest_tmp"
  mv -f "$manifest_tmp" "$RUN_ROOT/logs/normal_panel_manifest.tsv"

  python3 - "$RUN_SAMPLESHEET" "$RUN_ROOT/qdnaseq_local_pon" <<'PY_VERIFY_PON'
import csv, sys
from pathlib import Path

sheet, outdir = map(Path, sys.argv[1:3])
with sheet.open(newline='') as handle:
    samples = list(csv.DictReader(handle))
tumors = [row['sample'] for row in samples if row['status'] == 'tumor']
normals = [row['sample'] for row in samples if row['status'] == 'normal']

def read_tsv(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f'ERROR: required PoN artifact is missing or empty: {path}')
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle, delimiter='\t'))

summary_rows = read_tsv(outdir / 'qdnaseq_local_pon_summary.tsv')
if len(summary_rows) != 1 or summary_rows[0].get('pon_applied', '').lower() != 'true':
    raise SystemExit('ERROR: local qDNAseq summary does not assert pon_applied=true')
summary = summary_rows[0]
if set(filter(None, summary.get('normals', '').split(';'))) != set(normals):
    raise SystemExit('ERROR: local qDNAseq summary NORMAL IDs do not match the samplesheet')
if set(filter(None, summary.get('tumors', '').split(';'))) != set(tumors):
    raise SystemExit('ERROR: local qDNAseq summary TUMOR IDs do not match the samplesheet')
manifest = read_tsv(outdir / 'pon' / 'normal_panel_manifest.tsv')
if {row.get('sample') for row in manifest} != set(normals) or len(manifest) != len(normals):
    raise SystemExit('ERROR: normal_panel_manifest.tsv does not contain exactly the requested NORMAL samples')
observed_bins = {path.name.removesuffix('_markdup_bins.bed') for path in (outdir / 'bins').glob('*_markdup_bins.bed')}
if observed_bins != set(tumors):
    raise SystemExit(f'ERROR: PoN-corrected bins must contain only tumors; expected {tumors}, found {sorted(observed_bins)}')
segments = read_tsv(outdir / 'all_segments.seg')
if not segments:
    raise SystemExit('ERROR: all_segments.seg has no tumor segments')
id_column = 'ID' if 'ID' in segments[0] else next(iter(segments[0]))
segment_ids = {row[id_column].removesuffix('_markdup') for row in segments}
if segment_ids != set(tumors):
    raise SystemExit(f'ERROR: all_segments.seg must contain only tumors; expected {tumors}, found {sorted(segment_ids)}')
done = outdir / 'qdnaseq_local_pon.done'
if done.read_text().strip() != 'QDNASEQ_LOCAL_PON_SUCCESS':
    raise SystemExit('ERROR: local qDNAseq PoN completion stamp is invalid')
print(f'Validated local PoN outputs: {len(tumors)} TUMOR, {len(normals)} NORMAL')
PY_VERIFY_PON
fi

[[ -d "$RUN_ROOT/qdnaseq" ]] || { echo "ERROR: SAMURAI qdnaseq output not found: $RUN_ROOT/qdnaseq" >&2; exit 1; }
[[ -d "$RUN_ROOT/alignment" ]] || { echo "ERROR: SAMURAI alignment output not found: $RUN_ROOT/alignment" >&2; exit 1; }
[[ -s "$RUN_ROOT/qdnaseq/all_segments.seg" ]] || { echo "ERROR: SAMURAI segment table missing: $RUN_ROOT/qdnaseq/all_segments.seg" >&2; exit 1; }

echo "Illumina SAMURAI completed: $RUN_ROOT" > "$RUN_ROOT/logs/samurai_illumina_done.txt"
