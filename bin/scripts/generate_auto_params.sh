#!/usr/bin/env bash
set -Eeuo pipefail
usage() {
  cat <<'USAGE'
Usage: bash generate_auto_params.sh --mode illumina|ont --reads-folder PATH --sample-table FILE [--config-dir PATH] [--outdir PATH] [classifier options]
Illumina table: sample_name,status. Each sample must have one exact
<sample>.fastq.gz/.fq.gz file, or one supported R1/R2 pair.
ONT table: barcode,sample_name,status (or sample_name,status, mapped to sorted barcode folders)

Classifier options:
  --run-cna-classifier true|false
  --cna-classifier-sample-set NAME
  --cna-classifier-profile NAME
  --pathology-use-biomed-models true|false
  --pathology-biomed-local-files-only true|false
USAGE
}
MODE= READS= TABLE= CONFIG_DIR= OUTDIR=
REFERENCE_ROOT= HG38_AUTO_DOWNLOAD= THREADS=
TABLE_PYTHON=
NO_OVERWRITE=false EXPLICIT_BARCODES=false OFFLINE_KNOWLEDGE=false
META= YAML_TMP= SHEET_TMP= MANIFEST_TMP=
RUN_CNA_CLASSIFIER=false
CNA_CLASSIFIER_SAMPLE_SET=broad_cancer
CNA_CLASSIFIER_PROFILE=conda
PATHOLOGY_USE_BIOMED_MODELS=true
PATHOLOGY_BIOMED_LOCAL_FILES_ONLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --reads-folder) READS="${2:-}"; shift 2 ;;
    --sample-table) TABLE="${2:-}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --reference-root) REFERENCE_ROOT="${2:-}"; shift 2 ;;
    --hg38-auto-download) HG38_AUTO_DOWNLOAD="${2:-}"; shift 2 ;;
    --threads) THREADS="${2:-}"; shift 2 ;;
    --table-python) TABLE_PYTHON="${2:-}"; shift 2 ;;
    --no-overwrite) NO_OVERWRITE=true; shift ;;
    --explicit-barcodes) EXPLICIT_BARCODES=true; shift ;;
    --offline-knowledge) OFFLINE_KNOWLEDGE=true; shift ;;
    --run-cna-classifier) RUN_CNA_CLASSIFIER="${2:-}"; shift 2 ;;
    --cna-classifier-sample-set) CNA_CLASSIFIER_SAMPLE_SET="${2:-}"; shift 2 ;;
    --cna-classifier-profile) CNA_CLASSIFIER_PROFILE="${2:-}"; shift 2 ;;
    --pathology-use-biomed-models) PATHOLOGY_USE_BIOMED_MODELS="${2:-}"; shift 2 ;;
    --pathology-biomed-local-files-only) PATHOLOGY_BIOMED_LOCAL_FILES_ONLY="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$MODE" == illumina || "$MODE" == ont ]] || { echo "ERROR: --mode must be illumina or ont" >&2; exit 2; }
[[ -d "$READS" ]] || { echo "ERROR: reads folder not found: $READS" >&2; exit 2; }
[[ -s "$TABLE" ]] || { echo "ERROR: sample table not found or empty: $TABLE" >&2; exit 2; }
READS="$(readlink -m "$READS")"; TABLE="$(readlink -m "$TABLE")"
CONFIG_DIR="$(readlink -m "${CONFIG_DIR:-$READS/oncotracer_config}")"
OUTDIR="$(readlink -m "${OUTDIR:-$READS/oncotracer_results}")"
ROOT="$READS"
path_is_within_root() {
  local path="$1" root="$2"
  [[ "$path" == "$root" ]] ||
    [[ "$root" == "/" && "$path" == /* ]] ||
    [[ "$path" == "$root/"* ]]
}
for required_path in "$CONFIG_DIR" "$OUTDIR"; do
  [[ -z "$REFERENCE_ROOT" ]] || break
  while ! path_is_within_root "$required_path" "$ROOT"; do
    parent="$(dirname "$ROOT")"
    [[ "$parent" != "$ROOT" ]] || {
      echo "ERROR: could not determine a common root for automatic setup paths" >&2
      exit 2
    }
    ROOT="$parent"
  done
done
ROOT="${REFERENCE_ROOT:-$ROOT}"
[[ "$ROOT" != "/" ]] || {
  echo "ERROR: reads, configuration, and output paths must share a project directory below /" >&2
  exit 2
}
mkdir -p "$CONFIG_DIR"
# Lock the directory itself: no persistent lock file or result directory is needed.
exec {config_lock}<"$CONFIG_DIR"
flock -x "$config_lock"
if [[ "$NO_OVERWRITE" == true ]]; then
  for target in "$CONFIG_DIR/${MODE}.auto.yml" "$CONFIG_DIR/illumina.samplesheet.csv" "$CONFIG_DIR/auto_params_manifest.tsv"; do
    [[ ! -e "$target" && ! -L "$target" ]] || {
      echo "ERROR: auto will not overwrite $target; edit the existing YAML or choose a new --config-dir. To resume, repeat oncotracer run, not auto." >&2
      exit 2
    }
  done
fi
cleanup_temporary_files() {
  local exit_status=$?
  trap - EXIT
  [[ -z "$META" ]] || rm -f -- "$META"
  [[ -z "$SHEET_TMP" ]] || rm -f -- "$SHEET_TMP"
  [[ -z "$YAML_TMP" ]] || rm -f -- "$YAML_TMP"
  [[ -z "$MANIFEST_TMP" ]] || rm -f -- "$MANIFEST_TMP"
  exit "$exit_status"
}
trap cleanup_temporary_files EXIT
META="$(mktemp "$CONFIG_DIR/.auto_params_metadata.XXXXXX")"
if [[ -n "$TABLE_PYTHON" ]]; then
  "$TABLE_PYTHON" - "$MODE" "$TABLE" > "$META" <<'PY'
import csv
import sys

mode, table = sys.argv[1:]
expected = ["sample_name", "status"] if mode == "illumina" else ["barcode", "sample_name", "status"]
try:
    with open(table, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = [cell.strip().lower() for cell in next(reader, [])]
        if mode == "illumina" and header == ["sample", "status"]:
            header[0] = "sample_name"
        if header != expected:
            raise ValueError("expected CSV header " + ",".join(expected) + "; use setup --samplesheet for a four-column Illumina table")
        for number, row in enumerate(reader, 2):
            if not row or all(not cell.strip() for cell in row):
                continue
            cells = [cell.strip() for cell in row]
            if len(cells) != len(expected) or not all(cells):
                raise ValueError(f"row {number} needs {len(expected)} nonempty fields: " + ",".join(expected))
            if any(any(char in cell for char in "\t\r\n") for cell in cells):
                raise ValueError(f"row {number} contains a tab or line break inside a field")
            print("\t".join(cells))
except (OSError, UnicodeError, csv.Error, ValueError) as error:
    raise SystemExit(f"ERROR: sample table {table}: {error}")
PY
else
  awk 'BEGIN{FS="[,\t ]+"; OFS="\t"} {gsub(/\r/, "")} NF==0 || $1 ~ /^#/ {next} NR==1 {h=tolower($1); if(h=="sample" || h=="sample_name" || h=="barcode") next} {print $1,$2,$3}' "$TABLE" > "$META"
fi
[[ -s "$META" ]] || { echo "ERROR: sample table contains no data rows" >&2; exit 2; }
normalize_status() { local value="${1,,}"; case "$value" in tumor|normal) printf %s "$value" ;; *) echo "ERROR: status must be TUMOR or NORMAL, found: $1" >&2; exit 2 ;; esac; }
normalize_bool() { local value="${1,,}"; case "$value" in true|t|1|yes|y|on) printf true ;; false|f|0|no|n|off) printf false ;; *) echo "ERROR: expected true or false, found: $1" >&2; exit 2 ;; esac; }
sample_ids=()
register_unique_sample_id() {
  local sample_id="$1" existing_id
  [[ "$sample_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    echo "ERROR: invalid sample ID '$sample_id'; use letters, digits, dot, underscore, or dash, starting with a letter or digit" >&2
    exit 2
  }
  for existing_id in "${sample_ids[@]}"; do
    [[ "$existing_id" != "$sample_id" ]] || {
      echo "ERROR: duplicate sample ID in sample table: $sample_id" >&2
      exit 2
    }
  done
  sample_ids+=("$sample_id")
}
join_by_comma() { local IFS=,; printf '%s' "$*"; }
yaml_text() {
  local value="$1"
  if [[ "$value" =~ ^[a-zA-Z0-9_./-]+$ ]]; then printf '%s' "$value"; return; fi
  value="${value//\\/\\\\}"; value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"; value="${value//$'\r'/\\r}"; value="${value//$'\t'/\\t}"
  printf '"%s"' "$value"
}
csv_text() {
  local value="$1"
  if [[ "$value" != *[,$'\n'$'\r'\"]* ]]; then printf '%s' "$value"; return; fi
  value="${value//\"/\"\"}"
  printf '"%s"' "$value"
}
RUN_CNA_CLASSIFIER="$(normalize_bool "$RUN_CNA_CLASSIFIER")"
PATHOLOGY_USE_BIOMED_MODELS="$(normalize_bool "$PATHOLOGY_USE_BIOMED_MODELS")"
PATHOLOGY_BIOMED_LOCAL_FILES_ONLY="$(normalize_bool "$PATHOLOGY_BIOMED_LOCAL_FILES_ONLY")"
[[ -n "$CNA_CLASSIFIER_SAMPLE_SET" ]] || { echo "ERROR: classifier sample set cannot be empty" >&2; exit 2; }
[[ -n "$CNA_CLASSIFIER_PROFILE" ]] || { echo "ERROR: classifier profile cannot be empty" >&2; exit 2; }
YAML="$CONFIG_DIR/${MODE}.auto.yml"
YAML_TMP="$(mktemp "$CONFIG_DIR/.${MODE}.auto.yml.tmp.XXXXXX")"
if [[ "$MODE" == illumina ]]; then
  SHEET="$CONFIG_DIR/illumina.samplesheet.csv"
  SHEET_TMP="$(mktemp "$CONFIG_DIR/.illumina.samplesheet.csv.tmp.XXXXXX")"
  printf "sample,fastq_1,fastq_2,status\n" > "$SHEET_TMP"
  sample_ids=(); tumor_count=0; normal_count=0
  while IFS=$'\t' read -r sample status unused; do
    [[ -n "$sample" && -n "$status" ]] || { echo "ERROR: Illumina rows require sample_name,status" >&2; exit 2; }
    register_unique_sample_id "$sample"
    status="$(normalize_status "$status")"
    if [[ "$status" == tumor ]]; then
      tumor_count=$((tumor_count + 1))
    else
      normal_count=$((normal_count + 1))
    fi
  done < "$META"

  detected_layout=""
  sample_index=0
  while IFS=$'\t' read -r sample status unused; do
    status="$(normalize_status "$status")"
    mapfile -t r1 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R1*.fastq.gz" -o -name "${sample}_R1*.fq.gz" -o -name "${sample}_1.fastq.gz" -o -name "${sample}_1.fq.gz" \) -print | sort -u)
    mapfile -t r2 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R2*.fastq.gz" -o -name "${sample}_R2*.fq.gz" -o -name "${sample}_2.fastq.gz" -o -name "${sample}_2.fq.gz" \) -print | sort -u)
    mapfile -t single < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}.fastq.gz" -o -name "${sample}.fq.gz" \) -print | sort -u)
    if [[ ${#r1[@]} -eq 1 && ${#r2[@]} -eq 1 && ${#single[@]} -eq 0 ]]; then
      sample_layout="paired"
      fastq_1="${r1[0]}"
      fastq_2="${r2[0]}"
    elif [[ ${#r1[@]} -eq 0 && ${#r2[@]} -eq 0 && ${#single[@]} -eq 1 ]]; then
      sample_layout="single"
      fastq_1="${single[0]}"
      fastq_2=""
    else
      echo "ERROR: expected either one exact single-end file or one R1/R2 pair for $sample in $READS; found ${#single[@]} single, ${#r1[@]} R1, and ${#r2[@]} R2" >&2
      exit 2
    fi
    if [[ -n "$detected_layout" && "$detected_layout" != "$sample_layout" ]]; then
      echo "ERROR: a single automatic Illumina setup cannot mix single-end and paired-end libraries" >&2
      exit 2
    fi
    detected_layout="$sample_layout"
    sample_index=$((sample_index + 1))
    echo "[$sample_index/${#sample_ids[@]}] Validating gzip FASTQ for Illumina sample $sample"
    validation_started=$SECONDS
    fastq_bytes="$(stat -c '%s' -- "$fastq_1")"
    if [[ "$sample_layout" == paired ]]; then
      fastq_bytes=$((fastq_bytes + $(stat -c '%s' -- "$fastq_2")))
      gzip -t "$fastq_1" "$fastq_2" || { echo "ERROR: corrupt or incomplete gzip FASTQ for $sample" >&2; exit 2; }
    else
      gzip -t "$fastq_1" || { echo "ERROR: corrupt or incomplete gzip FASTQ for $sample" >&2; exit 2; }
    fi
    validation_seconds=$((SECONDS - validation_started))
    echo "[$sample_index/${#sample_ids[@]}] gzip validation passed for Illumina sample $sample: $fastq_bytes bytes validated in ${validation_seconds}s"
    fastq_1="$(readlink -m "$fastq_1")"
    [[ -z "$fastq_2" ]] || fastq_2="$(readlink -m "$fastq_2")"
    printf "%s,%s,%s,%s\n" "$sample" "$(csv_text "$fastq_1")" "$(csv_text "$fastq_2")" "$status" >> "$SHEET_TMP"
  done < "$META"
  echo "Detected Illumina layout: ${detected_layout}-end"
  cat > "$YAML_TMP" <<EOF
mode: illumina
lpwgs_root: $(yaml_text "$ROOT")
outdir: $(yaml_text "$OUTDIR")
illumina_samplesheet: $(yaml_text "$SHEET")
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
EOF
else
  mapfile -t detected_barcodes < <(for directory in "$READS"/*; do [[ -d "$directory" ]] || continue; find "$directory" -maxdepth 1 -type f \( -name "*.fastq.gz" -o -name "*.fq.gz" -o -name "*.fastq" -o -name "*.fq" \) -print -quit | grep -q . && basename "$directory"; done | sort)
  [[ ${#detected_barcodes[@]} -gt 0 ]] || { echo "ERROR: no barcode directories found below $READS" >&2; exit 2; }
  sample_ids=(); barcode_ids=(); tumors=(); tumor_names=(); normals=(); normal_names=(); row=0
  while IFS=$'\t' read -r first second third; do
    if [[ "$EXPLICIT_BARCODES" == true && -z "$third" ]]; then
      echo "ERROR: ONT table needs barcode,sample_name,status. Name each barcode explicitly; folder order is not a safe sample mapping." >&2
      exit 2
    fi
    if [[ -n "$third" ]]; then barcode="$first"; sample="$second"; status="$third"; else sample="$first"; status="$second"; [[ $row -lt ${#detected_barcodes[@]} ]] || { echo "ERROR: more metadata rows than barcode folders" >&2; exit 2; }; barcode="${detected_barcodes[$row]}"; fi
    [[ -n "$sample" && -n "$status" ]] || { echo "ERROR: ONT rows require sample_name,status" >&2; exit 2; }
    register_unique_sample_id "$sample"
    [[ "$barcode" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || { echo "ERROR: barcode must be a folder name, not a path: $barcode" >&2; exit 2; }
    for existing_barcode in "${barcode_ids[@]}"; do
      [[ "$existing_barcode" != "$barcode" ]] || { echo "ERROR: duplicate ONT barcode in sample table: $barcode" >&2; exit 2; }
    done
    barcode_ids+=("$barcode")
    [[ -d "$READS/$barcode" ]] || { echo "ERROR: barcode folder not found: $READS/$barcode" >&2; exit 2; }
    mapfile -t barcode_fastqs < <(find "$READS/$barcode" -maxdepth 1 -type f \( -name "*.fastq.gz" -o -name "*.fq.gz" -o -name "*.fastq" -o -name "*.fq" \) -print)
    [[ ${#barcode_fastqs[@]} -gt 0 ]] || { echo "ERROR: no FASTQ found in $READS/$barcode" >&2; exit 2; }
    for fastq in "${barcode_fastqs[@]}"; do
      [[ -s "$fastq" ]] || { echo "ERROR: empty FASTQ: $fastq" >&2; exit 2; }
      if [[ "$fastq" == *.gz ]]; then gzip -t "$fastq" || { echo "ERROR: corrupt or incomplete gzip FASTQ: $fastq" >&2; exit 2; }; fi
    done
    status="$(normalize_status "$status")"; if [[ "$status" == tumor ]]; then tumors+=("$barcode"); tumor_names+=("$sample"); else normals+=("$barcode"); normal_names+=("$sample"); fi; row=$((row+1))
  done < "$META"
  [[ ${#tumors[@]} -gt 0 ]] || { echo "ERROR: ONT configuration requires at least one tumor sample" >&2; exit 2; }
  cat > "$YAML_TMP" <<EOF
mode: ont
lpwgs_root: $(yaml_text "$ROOT")
outdir: $(yaml_text "$OUTDIR")
ont_folder: $(yaml_text "$READS")
ont_barcodes: $(join_by_comma "${tumors[@]}")
ont_sample_names: $(join_by_comma "${tumor_names[@]}")
ont_analysis_type: $([[ ${#normals[@]} -gt 0 ]] && printf solid_biopsy || printf liquid_biopsy)
ont_caller: $([[ ${#normals[@]} -gt 0 ]] && printf qdnaseq || printf ichorcna)
ont_binsize_kb: $([[ ${#normals[@]} -gt 0 ]] && printf 100 || printf 500)
ont_min_age_minutes: 0
EOF
  if [[ ${#normals[@]} -gt 0 ]]; then
    cat >> "$YAML_TMP" <<EOF
ont_normal_folder: $(yaml_text "$READS")
ont_normal_barcodes: $(join_by_comma "${normals[@]}")
ont_normal_sample_names: $(join_by_comma "${normal_names[@]}")
EOF
  fi
fi
cat >> "$YAML_TMP" <<EOF
run_cna_classifier: $RUN_CNA_CLASSIFIER
cna_classifier_sample_set: $(yaml_text "$CNA_CLASSIFIER_SAMPLE_SET")
cna_classifier_profile: $(yaml_text "$CNA_CLASSIFIER_PROFILE")
pathology_csv: null
pathology_use_biomed_models: $PATHOLOGY_USE_BIOMED_MODELS
pathology_biomed_local_files_only: $PATHOLOGY_BIOMED_LOCAL_FILES_ONLY
force: false
EOF
if [[ -n "$HG38_AUTO_DOWNLOAD" ]]; then
  printf '# Reference choice; downloads/builds start only with oncotracer run.\nhg38_auto_download: %s\n' "$(normalize_bool "$HG38_AUTO_DOWNLOAD")" >> "$YAML_TMP"
fi
if [[ -n "$THREADS" ]]; then
  [[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: --threads must be positive" >&2; exit 2; }
  printf '# CPU worker threads requested.\nthreads: %s\n' "$THREADS" >> "$YAML_TMP"
fi
if [[ "$OFFLINE_KNOWLEDGE" == true ]]; then
  printf '# Report web/LLM enrichment is opt-in.\nknowledge_web: false\nknowledge_literature_llm: false\nknowledge_deep_literature: false\n' >> "$YAML_TMP"
fi
chmod 0644 "$YAML_TMP"
read -r yaml_sha256 yaml_hash_path < <(sha256sum "$YAML_TMP")
if [[ "$MODE" == illumina ]]; then
  chmod 0644 "$SHEET_TMP"
  read -r samplesheet_sha256 samplesheet_hash_path < <(sha256sum "$SHEET_TMP")
  manifest_tumor_count=$tumor_count
  manifest_normal_count=$normal_count
else
  samplesheet_sha256=NA
  manifest_tumor_count=${#tumors[@]}
  manifest_normal_count=${#normals[@]}
fi
MANIFEST="$CONFIG_DIR/auto_params_manifest.tsv"
MANIFEST_TMP="$(mktemp "$CONFIG_DIR/.auto_params_manifest.tsv.tmp.XXXXXX")"
printf 'mode\ttumor_count\tnormal_count\tyaml_sha256\tsamplesheet_sha256\n' > "$MANIFEST_TMP"
printf '%s\t%s\t%s\t%s\t%s\n' "$MODE" "$manifest_tumor_count" "$manifest_normal_count" "$yaml_sha256" "$samplesheet_sha256" >> "$MANIFEST_TMP"
chmod 0644 "$MANIFEST_TMP"
if [[ "$MODE" == illumina ]]; then
  mv -f -- "$SHEET_TMP" "$SHEET"
  SHEET_TMP=""
fi
mv -f -- "$MANIFEST_TMP" "$MANIFEST"
MANIFEST_TMP=""
mv -f -- "$YAML_TMP" "$YAML"
YAML_TMP=""
echo "Batch setup complete."
echo "Generated YAML: $YAML"
[[ "$MODE" == illumina ]] && echo "Generated samplesheet: $SHEET"
echo "Generated manifest: $MANIFEST"
echo "Selected samples: ${#sample_ids[@]} ($manifest_tumor_count TUMOR, $manifest_normal_count NORMAL)"
[[ "$MODE" != ont ]] || echo "ONT samples: $(join_by_comma "${tumor_names[@]}" "${normal_names[@]}"). NORMAL-containing tables select independent qDNAseq; tumor-only tables select ichorCNA."
if [[ "$HG38_AUTO_DOWNLOAD" == true ]]; then
  echo "Reference: prebuilt hg38 indexes download when run starts, into $ROOT"
elif [[ -n "$HG38_AUTO_DOWNLOAD" ]]; then
  echo "Reference: reuse prepared hg38 indexes, or build missing indexes on CPU at run time (more RAM, disk and time): $ROOT"
fi
echo "Review the sample mapping, then check and run (no analysis has started):"
printf 'oncotracer check --config %q\n' "$YAML"
printf 'oncotracer run --backend conda --config %q\n' "$YAML"
