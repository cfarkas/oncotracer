#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR="$ROOT_DIR/bin/scripts/generate_auto_params.sh"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/oncotracer-auto-params.XXXXXX")"

cleanup() {
  rm -rf -- "$TEST_TMP"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_line() {
  local file="$1" expected="$2"
  grep -Fqx -- "$expected" "$file" || fail "missing line in $file: $expected"
}

assert_contains() {
  local file="$1" expected="$2"
  grep -Fq -- "$expected" "$file" || fail "missing text in $file: $expected"
}

assert_matches() {
  local file="$1" expected_regex="$2"
  grep -Eq -- "$expected_regex" "$file" || fail "missing pattern in $file: $expected_regex"
}

assert_illumina_manifest() {
  local config_dir="$1" expected_tumors="$2" expected_normals="$3"
  local yaml_sha256 samplesheet_sha256 ignored_path expected_row
  read -r yaml_sha256 ignored_path < <(sha256sum "$config_dir/illumina.auto.yml")
  read -r samplesheet_sha256 ignored_path < <(sha256sum "$config_dir/illumina.samplesheet.csv")
  assert_line "$config_dir/auto_params_manifest.tsv" $'mode\ttumor_count\tnormal_count\tyaml_sha256\tsamplesheet_sha256'
  expected_row="$(printf 'illumina\t%s\t%s\t%s\t%s' \
    "$expected_tumors" \
    "$expected_normals" \
    "$yaml_sha256" \
    "$samplesheet_sha256")"
  assert_line "$config_dir/auto_params_manifest.tsv" "$expected_row"
}

assert_no_auto_temps() {
  local config_dir="$1" leftover
  [[ -d "$config_dir" ]] || return 0
  leftover="$(find "$config_dir" -maxdepth 1 -type f \( -name '.auto_params_metadata.*' -o -name '.*.tmp.*' \) -print -quit)"
  [[ -z "$leftover" ]] || fail "temporary automatic-setup file was not cleaned: $leftover"
}

make_fastq_gz() {
  local path="$1"
  printf '@read1\nACGT\n+\n!!!!\n' | gzip -n > "$path"
}

run_illumina() {
  local case_dir="$1"
  bash "$GENERATOR" \
    --mode illumina \
    --reads-folder "$case_dir/reads" \
    --sample-table "$case_dir/samples.csv" \
    --config-dir "$case_dir/config" \
    --outdir "$case_dir/results" \
    > "$case_dir/run.log" 2>&1
}

test_tumor_only_generates_independent_samples() {
  local case_dir="$TEST_TMP/tumor_only"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_A,TUMOR\n' > "$case_dir/samples.csv"
  make_fastq_gz "$case_dir/reads/TUMOR_A_R1.fastq.gz"
  make_fastq_gz "$case_dir/reads/TUMOR_A_R2.fastq.gz"

  run_illumina "$case_dir"

  if grep -Eqi 'pon|panel.of.normals' "$case_dir/config/illumina.auto.yml"; then
    fail "automatic YAML must not contain a local panel setting"
  fi
  assert_contains "$case_dir/run.log" "oncotracer run --config $case_dir/config/illumina.auto.yml"
  if grep -qi 'nextflow run' "$case_dir/run.log"; then
    fail "Automatic Setup printed an obsolete Nextflow launch command"
  fi
  assert_contains "$case_dir/run.log" '[1/1] Validating gzip FASTQ for Illumina sample TUMOR_A'
  assert_matches "$case_dir/run.log" '^\[1/1\] gzip validation passed for Illumina sample TUMOR_A: [1-9][0-9]* bytes validated in [0-9]+s$'
  assert_illumina_manifest "$case_dir/config" 1 0
  [[ "$(wc -l < "$case_dir/config/illumina.samplesheet.csv")" -eq 2 ]] || fail "unexpected tumor-only samplesheet row count"
  assert_no_auto_temps "$case_dir/config"
}

test_one_normal_is_an_independent_sample() {
  local case_dir="$TEST_TMP/one_normal"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_A,TUMOR\nCTRL_A,NORMAL\n' > "$case_dir/samples.csv"

  make_fastq_gz "$case_dir/reads/TUMOR_A.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL_A.fastq.gz"
  run_illumina "$case_dir"
  assert_line "$case_dir/config/illumina.samplesheet.csv" "CTRL_A,$case_dir/reads/CTRL_A.fastq.gz,,normal"
  ! grep -Eqi 'pon|panel.of.normals' "$case_dir/config/illumina.auto.yml" ||
    fail "one NORMAL must not create a panel"
  assert_illumina_manifest "$case_dir/config" 1 1
}

test_two_normals_are_independent_samples() {
  local case_dir="$TEST_TMP/two_normals"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_Z,TUMOR\nCTRL-2,NORMAL\nCTRL.1,NORMAL\n' > "$case_dir/samples.csv"
  make_fastq_gz "$case_dir/reads/TUMOR_Z.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL-2.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL.1.fastq.gz"

  run_illumina "$case_dir"

  ! grep -Eqi 'pon|panel.of.normals' "$case_dir/config/illumina.auto.yml" ||
    fail "NORMAL samples must never create a panel"
  assert_line "$case_dir/config/illumina.samplesheet.csv" "CTRL-2,$case_dir/reads/CTRL-2.fastq.gz,,normal"
  assert_line "$case_dir/config/illumina.samplesheet.csv" "CTRL.1,$case_dir/reads/CTRL.1.fastq.gz,,normal"
  assert_matches "$case_dir/run.log" '^\[3/3\] gzip validation passed for Illumina sample CTRL\.1: [1-9][0-9]* bytes validated in [0-9]+s$'
  assert_illumina_manifest "$case_dir/config" 1 2
  assert_no_auto_temps "$case_dir/config"
}

test_six_tumors_and_four_normals_are_ten_independent_samples() {
  local case_dir="$TEST_TMP/six_tumors_four_normals"
  local sample
  mkdir -p "$case_dir/reads"
  {
    printf 'sample_name,status\n'
    for sample in ONCO001 ONCO002 ONCO003 ONCO004 ONCO005 ONCO006; do
      printf '%s,TUMOR\n' "$sample"
    done
    for sample in CTRL001 CTRL002 CTRL003 CTRL004; do
      printf '%s,NORMAL\n' "$sample"
    done
  } > "$case_dir/samples.csv"
  for sample in \
    ONCO001 ONCO002 ONCO003 ONCO004 ONCO005 ONCO006 \
    CTRL001 CTRL002 CTRL003 CTRL004; do
    make_fastq_gz "$case_dir/reads/${sample}_R1.fastq.gz"
    make_fastq_gz "$case_dir/reads/${sample}_R2.fastq.gz"
  done

  run_illumina "$case_dir"

  ! grep -Eqi 'pon|panel.of.normals' "$case_dir/config/illumina.auto.yml" ||
    fail "ONCO/CTRL example must not create a panel"
  [[ "$(grep -c ',tumor$' "$case_dir/config/illumina.samplesheet.csv")" -eq 6 ]] ||
    fail "ONCO/CTRL samplesheet must contain six tumor rows"
  [[ "$(grep -c ',normal$' "$case_dir/config/illumina.samplesheet.csv")" -eq 4 ]] ||
    fail "ONCO/CTRL samplesheet must contain four independent normal rows"
  [[ "$(find "$case_dir/reads" -maxdepth 1 -type f -name '*.fastq.gz' | wc -l)" -eq 20 ]] ||
    fail "QuickStart Example 3 fixture must contain 20 paired FASTQs"
  assert_illumina_manifest "$case_dir/config" 6 4
  assert_no_auto_temps "$case_dir/config"
}

test_duplicate_ids_are_rejected() {
  local case_dir="$TEST_TMP/duplicate"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nSAME,TUMOR\nSAME,TUMOR\n' > "$case_dir/samples.csv"

  if run_illumina "$case_dir"; then
    fail "duplicate sample IDs should be rejected"
  fi

  assert_contains "$case_dir/run.log" 'duplicate sample ID in sample table: SAME'
  [[ ! -e "$case_dir/config/illumina.auto.yml" ]] || fail "duplicate-ID setup published a YAML"
  [[ ! -e "$case_dir/config/illumina.samplesheet.csv" ]] || fail "duplicate-ID setup published a samplesheet"
  [[ ! -e "$case_dir/config/auto_params_manifest.tsv" ]] || fail "duplicate-ID setup published a manifest"
  assert_no_auto_temps "$case_dir/config"
}

test_invalid_ids_are_rejected() {
  local case_dir="$TEST_TMP/invalid_id"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nBAD+ID,TUMOR\n' > "$case_dir/samples.csv"

  if run_illumina "$case_dir"; then
    fail "sample IDs outside the wrapper grammar should be rejected"
  fi

  assert_contains "$case_dir/run.log" "invalid sample ID 'BAD+ID'"
  [[ ! -e "$case_dir/config/illumina.auto.yml" ]] || fail "invalid-ID setup published a YAML"
  [[ ! -e "$case_dir/config/illumina.samplesheet.csv" ]] || fail "invalid-ID setup published a samplesheet"
  [[ ! -e "$case_dir/config/auto_params_manifest.tsv" ]] || fail "invalid-ID setup published a manifest"
  assert_no_auto_temps "$case_dir/config"
}

test_normal_only_samples_are_analyzed_independently() {
  local case_dir="$TEST_TMP/no_tumor"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nCTRL_A,NORMAL\nCTRL_B,NORMAL\n' > "$case_dir/samples.csv"

  make_fastq_gz "$case_dir/reads/CTRL_A.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL_B.fastq.gz"
  run_illumina "$case_dir"
  [[ "$(grep -c ',normal$' "$case_dir/config/illumina.samplesheet.csv")" -eq 2 ]] ||
    fail "normal-only samplesheet must retain both NORMAL rows"
  ! grep -Eqi 'pon|panel.of.normals' "$case_dir/config/illumina.auto.yml" ||
    fail "normal-only setup must not create a panel"
  assert_illumina_manifest "$case_dir/config" 0 2
}

test_corrupt_gzip_preserves_previous_publication() {
  local case_dir="$TEST_TMP/corrupt"
  mkdir -p "$case_dir/reads" "$case_dir/config"
  printf 'sample_name,status\nTUMOR_A,TUMOR\nCTRL_A,NORMAL\nCTRL_B,NORMAL\n' > "$case_dir/samples.csv"
  make_fastq_gz "$case_dir/reads/TUMOR_A.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL_A.fastq.gz"
  printf 'not a gzip stream\n' > "$case_dir/reads/CTRL_B.fastq.gz"
  printf 'previous yaml\n' > "$case_dir/config/illumina.auto.yml"
  printf 'previous samplesheet\n' > "$case_dir/config/illumina.samplesheet.csv"
  printf 'previous manifest\n' > "$case_dir/config/auto_params_manifest.tsv"

  if run_illumina "$case_dir"; then
    fail "corrupt gzip should fail automatic setup"
  fi

  assert_contains "$case_dir/run.log" 'corrupt or incomplete gzip FASTQ for CTRL_B'
  assert_line "$case_dir/config/illumina.auto.yml" 'previous yaml'
  assert_line "$case_dir/config/illumina.samplesheet.csv" 'previous samplesheet'
  assert_line "$case_dir/config/auto_params_manifest.tsv" 'previous manifest'
  assert_no_auto_temps "$case_dir/config"
}

test_mixed_ont_roles_select_independent_qdnaseq() {
  local case_dir="$TEST_TMP/mixed_ont_roles"
  mkdir -p "$case_dir/reads/barcode01" "$case_dir/reads/barcode02"
  make_fastq_gz "$case_dir/reads/barcode01/reads.fastq.gz"
  make_fastq_gz "$case_dir/reads/barcode02/reads.fastq.gz"
  cat > "$case_dir/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Tumor_A,TUMOR
barcode02,Control_A,NORMAL
CSV
  bash "$GENERATOR" \
    --mode ont \
    --reads-folder "$case_dir/reads" \
    --sample-table "$case_dir/samples.csv" \
    --config-dir "$case_dir/config" \
    --outdir "$case_dir/results" > "$case_dir/run.log" 2>&1
  assert_line "$case_dir/config/ont.auto.yml" 'ont_analysis_type: solid_biopsy'
  assert_line "$case_dir/config/ont.auto.yml" 'ont_caller: qdnaseq'
  assert_line "$case_dir/config/ont.auto.yml" 'ont_binsize_kb: 100'
  assert_line "$case_dir/config/ont.auto.yml" 'ont_barcodes: barcode01'
  assert_line "$case_dir/config/ont.auto.yml" 'ont_sample_names: Tumor_A'
  assert_line "$case_dir/config/ont.auto.yml" "ont_normal_folder: $case_dir/reads"
  assert_line "$case_dir/config/ont.auto.yml" 'ont_normal_barcodes: barcode02'
  assert_line "$case_dir/config/ont.auto.yml" 'ont_normal_sample_names: Control_A'
  ! grep -Eqi 'build.pon|local.pon' "$case_dir/config/ont.auto.yml" ||
    fail "mixed ONT YAML must not contain local-panel settings"
}

test_duplicate_ont_barcode_is_rejected() {
  local case_dir="$TEST_TMP/duplicate_ont_barcode"
  mkdir -p "$case_dir/reads/barcode01"
  make_fastq_gz "$case_dir/reads/barcode01/reads.fastq.gz"
  cat > "$case_dir/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Tumor_A,TUMOR
barcode01,Control_A,NORMAL
CSV
  if bash "$GENERATOR" \
    --mode ont \
    --reads-folder "$case_dir/reads" \
    --sample-table "$case_dir/samples.csv" \
    --config-dir "$case_dir/config" \
    --outdir "$case_dir/results" > "$case_dir/run.log" 2>&1; then
    fail "duplicate ONT barcode should be rejected"
  fi
  assert_contains "$case_dir/run.log" 'duplicate ONT barcode in sample table: barcode01'
  [[ ! -e "$case_dir/config/ont.auto.yml" ]] || fail "duplicate barcode published YAML"
}

test_tumor_only_generates_independent_samples
test_one_normal_is_an_independent_sample
test_two_normals_are_independent_samples
test_six_tumors_and_four_normals_are_ten_independent_samples
test_duplicate_ids_are_rejected
test_invalid_ids_are_rejected
test_normal_only_samples_are_analyzed_independently
test_corrupt_gzip_preserves_previous_publication
test_duplicate_ont_barcode_is_rejected
test_mixed_ont_roles_select_independent_qdnaseq

echo "PASS: generate_auto_params independent NORMAL sample tests"
