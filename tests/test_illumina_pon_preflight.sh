#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$ROOT_DIR/bin/scripts/run_illumina_samurai_fastq.sh"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/oncotracer-illumina-preflight.XXXXXX")"
ORIGINAL_PATH="$PATH"
MOCK_NEXTFLOW_EXIT=86

cleanup() {
  rm -rf -- "$TEST_TMP"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  local file="$1" expected="$2"
  grep -Fq -- "$expected" "$file" || fail "missing text in $file: $expected"
}

assert_not_invoked() {
  local case_dir="$1"
  [[ ! -e "$case_dir/nextflow.log" ]] || fail "preflight failure invoked Nextflow: $case_dir"
}

MOCK_BIN="$TEST_TMP/mock_bin"
mkdir -p "$MOCK_BIN"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -u' \
  'printf "%s\n" "$*" >> "${MOCK_NEXTFLOW_LOG:?}"' \
  'exit "${MOCK_NEXTFLOW_EXIT_CODE:-86}"' \
  > "$MOCK_BIN/nextflow"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'echo "ERROR: preflight test unexpectedly invoked samtools: $*" >&2' \
  'exit 97' \
  > "$MOCK_BIN/samtools"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -Eeuo pipefail' \
  'if [[ "${1:-}" == "--vanilla" && "${2:-}" == "-" && $# -ge 4 ]]; then' \
  '  printf "mock qDNAseq annotation\n" > "${4:?missing mock RDS output}"' \
  'fi' \
  'exit 0' \
  > "$MOCK_BIN/Rscript"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'exit 0' \
  > "$MOCK_BIN/qpdf"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -Eeuo pipefail' \
  'output=""' \
  'while [[ $# -gt 0 ]]; do' \
  '  case "$1" in' \
  '    --output) output="${2:-}"; shift 2 ;;' \
  '    *) shift ;;' \
  '  esac' \
  'done' \
  '[[ -n "$output" ]] || { echo "ERROR: mock curl did not receive --output" >&2; exit 98; }' \
  'printf "mock QDNAseq.hg38 source\n" > "$output"' \
  > "$MOCK_BIN/curl"
chmod +x \
  "$MOCK_BIN/nextflow" \
  "$MOCK_BIN/samtools" \
  "$MOCK_BIN/Rscript" \
  "$MOCK_BIN/qpdf" \
  "$MOCK_BIN/curl"

LPWGS_ROOT="$TEST_TMP/lpwgs"
REF_FA="$LPWGS_ROOT/reference/genome.fa"
mkdir -p "$(dirname "$REF_FA")/bwa"
printf '>chr1\nA\n' > "$REF_FA"
printf 'chr1\t1\t6\t1\t2\n' > "${REF_FA}.fai"
printf '@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:1\n' > "${REF_FA%.fa}.dict"
for extension in amb ann bwt pac sa; do
  printf 'mock index\n' > "$(dirname "$REF_FA")/bwa/genome.$extension"
done

FASTQ_DIR="$TEST_TMP/fastq"
mkdir -p "$FASTQ_DIR"
for sample in TUMOR_A CTRL_A CTRL_B DUP; do
  printf '@read1\nACGT\n+\n!!!!\n' > "$FASTQ_DIR/$sample.fastq"
done

write_sheet() {
  local path="$1"
  shift
  printf 'sample,fastq_1,fastq_2,status\n' > "$path"
  printf '%s\n' "$@" >> "$path"
}

run_wrapper() {
  local case_dir="$1"
  shift
  PATH="$MOCK_BIN:$ORIGINAL_PATH" \
  MOCK_NEXTFLOW_LOG="$case_dir/nextflow.log" \
  MOCK_NEXTFLOW_EXIT_CODE="$MOCK_NEXTFLOW_EXIT" \
    bash "$WRAPPER" \
      --samplesheet "$case_dir/samples.csv" \
      --outdir "$case_dir/out" \
      --lpwgs-root "$LPWGS_ROOT" \
      --ref "$REF_FA" \
      --profile conda \
      "$@" \
      > "$case_dir/run.log" 2>&1
}

test_normal_rows_with_pon_off_fail() {
  local case_dir="$TEST_TMP/pon_off"
  mkdir -p "$case_dir"
  write_sheet "$case_dir/samples.csv" \
    "TUMOR_A,$FASTQ_DIR/TUMOR_A.fastq,,TUMOR" \
    "CTRL_A,$FASTQ_DIR/CTRL_A.fastq,,NORMAL"

  if run_wrapper "$case_dir"; then
    fail "NORMAL rows with PoN disabled should fail"
  fi

  assert_contains "$case_dir/run.log" 'samplesheet contains NORMAL rows but --build-pon is off; refusing to ignore controls'
  assert_not_invoked "$case_dir"
}

test_build_pon_with_mismatched_list_fails() {
  local case_dir="$TEST_TMP/mismatched_list"
  mkdir -p "$case_dir"
  write_sheet "$case_dir/samples.csv" \
    "TUMOR_A,$FASTQ_DIR/TUMOR_A.fastq,,TUMOR" \
    "CTRL_A,$FASTQ_DIR/CTRL_A.fastq,,NORMAL" \
    "CTRL_B,$FASTQ_DIR/CTRL_B.fastq,,NORMAL"

  if run_wrapper "$case_dir" --build-pon --pon-normal-samples CTRL_A,CTRL_MISSING --pon-min-normals 2; then
    fail "PoN normal list differing from NORMAL rows should fail"
  fi

  assert_contains "$case_dir/run.log" 'do not exactly match samplesheet NORMAL rows'
  assert_not_invoked "$case_dir"
}

test_duplicate_ids_fail() {
  local case_dir="$TEST_TMP/duplicate_ids"
  mkdir -p "$case_dir"
  write_sheet "$case_dir/samples.csv" \
    "DUP,$FASTQ_DIR/DUP.fastq,,TUMOR" \
    "DUP,$FASTQ_DIR/DUP.fastq,,TUMOR"

  if run_wrapper "$case_dir"; then
    fail "duplicate sample IDs should fail"
  fi

  assert_contains "$case_dir/run.log" 'duplicate sample ID in samplesheet: DUP'
  assert_not_invoked "$case_dir"
}

test_one_normal_below_minimum_fails() {
  local case_dir="$TEST_TMP/one_normal"
  mkdir -p "$case_dir"
  write_sheet "$case_dir/samples.csv" \
    "TUMOR_A,$FASTQ_DIR/TUMOR_A.fastq,,TUMOR" \
    "CTRL_A,$FASTQ_DIR/CTRL_A.fastq,,NORMAL"

  if run_wrapper "$case_dir" --build-pon --pon-normal-samples CTRL_A --pon-min-normals 2; then
    fail "one NORMAL with minimum two should fail"
  fi

  assert_contains "$case_dir/run.log" 'local Illumina PoN requires at least 2 NORMAL samples; found 1'
  assert_not_invoked "$case_dir"
}

test_valid_preflight_reaches_mock_nextflow_only() {
  local case_dir="$TEST_TMP/valid"
  local status
  mkdir -p "$case_dir"
  write_sheet "$case_dir/samples.csv" \
    "TUMOR_A,$FASTQ_DIR/TUMOR_A.fastq,,TUMOR" \
    "CTRL_A,$FASTQ_DIR/CTRL_A.fastq,,NORMAL" \
    "CTRL_B,$FASTQ_DIR/CTRL_B.fastq,,NORMAL"

  set +e
  run_wrapper "$case_dir" \
    --build-pon \
    --pon-normal-samples CTRL_A,CTRL_B \
    --pon-min-normals 2 \
    --pon-name TEST_PON
  status=$?
  set -e

  [[ $status -eq $MOCK_NEXTFLOW_EXIT ]] || fail "valid preflight returned $status instead of controlled mock exit $MOCK_NEXTFLOW_EXIT"
  assert_contains "$case_dir/run.log" 'Validated 1 TUMOR and 2 NORMAL Illumina sample(s)'
  assert_contains "$case_dir/run.log" 'Detected Illumina read layout: single-end'
  assert_contains "$case_dir/run.log" 'Using qDNAseq hg38 annotation:'
  assert_contains "$case_dir/nextflow.log" "run $LPWGS_ROOT/.oncotracer/samurai/v1.4.0"
  assert_contains "$case_dir/nextflow.log" "--input $case_dir/out/input/samplesheet.csv"
  assert_contains "$case_dir/nextflow.log" '--qdnaseq_bin_data'
  [[ "$(wc -l < "$case_dir/nextflow.log")" -eq 1 ]] || fail "valid preflight invoked Nextflow more than once"
  [[ -s "$case_dir/out/input/samplesheet.csv" ]] || fail "validated samplesheet was not published"
  [[ ! -d "$case_dir/out/alignment" ]] || fail "mocked preflight unexpectedly created alignment output"
  [[ ! -d "$case_dir/out/qdnaseq" ]] || fail "mocked preflight unexpectedly created qDNAseq output"
}

test_normal_rows_with_pon_off_fail
test_build_pon_with_mismatched_list_fails
test_duplicate_ids_fail
test_one_normal_below_minimum_fails
test_valid_preflight_reaches_mock_nextflow_only

echo 'PASS: Illumina PoN wrapper preflight tests'
