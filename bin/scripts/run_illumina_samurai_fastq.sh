#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: run_illumina_samurai_fastq.sh --samplesheet FILE --outdir DIR [options]

Run the upstream SAMURAI/qDNAseq Illumina LP-WGS step from FASTQ input.
The samplesheet is a CSV with columns:
  sample,fastq_1,fastq_2,status
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
RUN_ROOT="$(readlink -m "$OUTDIR")"
REF_DIR_DEFAULT="$LPWGS_ROOT/references/samurai_hg38"
[[ -n "$REF_FA" ]] || REF_FA="$REF_DIR_DEFAULT/genome.fa"
REF_FA="$(readlink -m "$REF_FA")"
REF_FAI="${REF_FA}.fai"
DICT="${REF_FA%.fa}.dict"
LOCAL_CONFIG="$RUN_ROOT/samurai_hg38.config"
RUN_SAMPLESHEET="$RUN_ROOT/input/samplesheet.csv"

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

python3 - "$SAMPLESHEET" "$RUN_SAMPLESHEET" <<'PY_VALIDATE'
import csv, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:3])
required = ['sample', 'fastq_1', 'fastq_2', 'status']
with src.open(newline='') as handle:
    reader = csv.DictReader(handle)
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"ERROR: samplesheet is missing column(s): {', '.join(missing)}")
    rows = []
    for row in reader:
        sample = (row.get('sample') or '').strip()
        fq1 = (row.get('fastq_1') or '').strip()
        fq2 = (row.get('fastq_2') or '').strip()
        status = (row.get('status') or 'tumor').strip() or 'tumor'
        if not sample:
            raise SystemExit('ERROR: samplesheet contains a row with empty sample')
        if not fq1 or not fq2:
            raise SystemExit(f'ERROR: sample {sample} must have fastq_1 and fastq_2')
        if not Path(fq1).exists():
            raise SystemExit(f'ERROR: fastq_1 does not exist for {sample}: {fq1}')
        if not Path(fq2).exists():
            raise SystemExit(f'ERROR: fastq_2 does not exist for {sample}: {fq2}')
        rows.append({'sample': sample, 'fastq_1': fq1, 'fastq_2': fq2, 'status': status})
if not rows:
    raise SystemExit('ERROR: samplesheet has no samples')
with dst.open('w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=required)
    writer.writeheader()
    writer.writerows(rows)
print(f'Validated {len(rows)} Illumina FASTQ sample(s): {dst}')
PY_VALIDATE

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

cat > "$LOCAL_CONFIG" <<EOF_CONFIG
params {
  genomes {
    hg38 {
      fasta     = "${REF_FA}"
      fasta_fai = "${REF_FAI}"
      dict      = "${DICT}"
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
export NXF_WORK="$RUN_ROOT/work"
export NXF_SINGULARITY_CACHEDIR="$LPWGS_ROOT/.singularity_cache"
unset DISPLAY

if [[ "$FORCE" == "true" ]]; then
  rm -f "$RUN_ROOT/logs/samurai_illumina_done.txt"
fi

cd "$RUN_ROOT/nextflow_launch"
nextflow run dincalcilab/samurai -latest \
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
  --index_genome true \
  --run_fastp false \
  -resume

if ! compgen -G "$RUN_ROOT/alignment/*.bam" >/dev/null; then
  echo "WARN: SAMURAI FASTQ mode did not publish alignment; falling back to host bwa/samtools and SAMURAI BAM mode" >&2
  command -v bwa >/dev/null 2>&1 || { echo "ERROR: bwa is required for Illumina FASTQ fallback alignment" >&2; exit 1; }
  command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools is required for Illumina FASTQ fallback alignment" >&2; exit 1; }
  mkdir -p "$RUN_ROOT/alignment"
  BAM_SAMPLESHEET="$RUN_ROOT/input/bam.samplesheet.csv"
  python3 - "$RUN_SAMPLESHEET" "$BAM_SAMPLESHEET" "$RUN_ROOT/alignment" "$REF_FA" <<'PY_BAM_FALLBACK'
import csv, subprocess, sys
from pathlib import Path
fastq_sheet, bam_sheet, align_dir, ref = sys.argv[1:5]
align_dir = Path(align_dir)
index_prefix = align_dir.parent / "genome_index" / "bwa" / "genome"
if Path(str(index_prefix) + ".bwt").exists():
    ref = str(index_prefix)
rows = []
with open(fastq_sheet, newline='') as handle:
    for row in csv.DictReader(handle):
        sample = row['sample']
        status = row.get('status') or 'tumor'
        fq1 = row['fastq_1']
        fq2 = row['fastq_2']
        bam = align_dir / f'{sample}.bam'
        bai = Path(str(bam) + '.bai')
        if not bam.exists() or bam.stat().st_size == 0:
            read_group = f'@RG\\tID:{sample}\\tPU:1\\tSM:{sample}\\tLB:{sample}\\tPL:Illumina'
            cmd = f'bwa mem -t 8 -R "{read_group}" {ref} {fq1} {fq2} | samtools sort -@ 4 -o {bam} -'
            subprocess.run(cmd, shell=True, check=True, executable='/bin/bash')
        if not bai.exists() or bai.stat().st_size == 0:
            subprocess.run(['samtools', 'index', str(bam)], check=True)
        rows.append({'sample': sample, 'bam': str(bam), 'status': status})
with open(bam_sheet, 'w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['sample', 'bam', 'status'])
    writer.writeheader()
    writer.writerows(rows)
print(f'Prepared {len(rows)} BAM sample(s): {bam_sheet}')
PY_BAM_FALLBACK

  nextflow run dincalcilab/samurai -latest \
    -c "$LOCAL_CONFIG" \
    -profile "$SAMURAI_PROFILE" \
    -work-dir "$NXF_WORK" \
    --input "$BAM_SAMPLESHEET" \
    --outdir "$RUN_ROOT" \
    --genome hg38 \
    --analysis_type "$ANALYSIS_TYPE" \
    --caller "$CALLER" \
    --binsize "$BINSIZE" \
    --index_genome false \
    -resume
fi

[[ -d "$RUN_ROOT/qdnaseq" ]] || { echo "ERROR: SAMURAI qdnaseq output not found: $RUN_ROOT/qdnaseq" >&2; exit 1; }
[[ -d "$RUN_ROOT/alignment" ]] || { echo "ERROR: SAMURAI alignment output not found: $RUN_ROOT/alignment" >&2; exit 1; }
[[ -s "$RUN_ROOT/qdnaseq/all_segments.seg" ]] || { echo "ERROR: SAMURAI segment table missing: $RUN_ROOT/qdnaseq/all_segments.seg" >&2; exit 1; }

echo "Illumina SAMURAI completed: $RUN_ROOT" > "$RUN_ROOT/logs/samurai_illumina_done.txt"
