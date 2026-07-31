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

test_tumor_only_disables_pon_and_reports_gzip_progress() {
  local case_dir="$TEST_TMP/tumor_only"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_A,TUMOR\n' > "$case_dir/samples.csv"
  make_fastq_gz "$case_dir/reads/TUMOR_A_R1.fastq.gz"
  make_fastq_gz "$case_dir/reads/TUMOR_A_R2.fastq.gz"

  run_illumina "$case_dir"

  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_build_pon: false'
  if grep -q '^illumina_pon_normal_samples:' "$case_dir/config/illumina.auto.yml"; then
    fail "tumor-only YAML must not publish a normal sample list"
  fi
  assert_contains "$case_dir/run.log" '[1/1] Validating gzip FASTQ for Illumina sample TUMOR_A'
  assert_matches "$case_dir/run.log" '^\[1/1\] gzip validation passed for Illumina sample TUMOR_A: [1-9][0-9]* bytes validated in [0-9]+s$'
  assert_illumina_manifest "$case_dir/config" 1 0
  [[ "$(wc -l < "$case_dir/config/illumina.samplesheet.csv")" -eq 2 ]] || fail "unexpected tumor-only samplesheet row count"
  assert_no_auto_temps "$case_dir/config"
}

test_one_normal_is_rejected_without_publication() {
  local case_dir="$TEST_TMP/one_normal"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_A,TUMOR\nCTRL_A,NORMAL\n' > "$case_dir/samples.csv"

  if run_illumina "$case_dir"; then
    fail "a single NORMAL sample should be rejected"
  fi

  assert_contains "$case_dir/run.log" 'requires either zero NORMAL samples or at least two; found 1'
  [[ ! -e "$case_dir/config/illumina.auto.yml" ]] || fail "failed setup published a YAML"
  [[ ! -e "$case_dir/config/illumina.samplesheet.csv" ]] || fail "failed setup published a samplesheet"
  [[ ! -e "$case_dir/config/auto_params_manifest.tsv" ]] || fail "failed setup published a manifest"
  assert_no_auto_temps "$case_dir/config"
}

test_two_normals_enable_reproducible_pon() {
  local case_dir="$TEST_TMP/two_normals"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nTUMOR_Z,TUMOR\nCTRL-2,NORMAL\nCTRL.1,NORMAL\n' > "$case_dir/samples.csv"
  make_fastq_gz "$case_dir/reads/TUMOR_Z.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL-2.fastq.gz"
  make_fastq_gz "$case_dir/reads/CTRL.1.fastq.gz"

  run_illumina "$case_dir"

  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_build_pon: true'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_normal_samples: "CTRL-2,CTRL.1"'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_min_normals: 2'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_name: CTRL-2_CTRL.1_PoN'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_min_mapq: 37'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1'
  assert_line "$case_dir/config/illumina.samplesheet.csv" "CTRL-2,$case_dir/reads/CTRL-2.fastq.gz,,normal"
  assert_line "$case_dir/config/illumina.samplesheet.csv" "CTRL.1,$case_dir/reads/CTRL.1.fastq.gz,,normal"
  assert_matches "$case_dir/run.log" '^\[3/3\] gzip validation passed for Illumina sample CTRL\.1: [1-9][0-9]* bytes validated in [0-9]+s$'
  assert_illumina_manifest "$case_dir/config" 1 2
  assert_no_auto_temps "$case_dir/config"
}

test_six_tumors_and_four_normals_match_quickstart_example_3() {
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

  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_build_pon: true'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_min_normals: 4'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_min_mapq: 37'
  assert_line "$case_dir/config/illumina.auto.yml" 'illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1'
  [[ "$(grep -c ',tumor$' "$case_dir/config/illumina.samplesheet.csv")" -eq 6 ]] ||
    fail "QuickStart Example 3 samplesheet must contain six tumor rows"
  [[ "$(grep -c ',normal$' "$case_dir/config/illumina.samplesheet.csv")" -eq 4 ]] ||
    fail "QuickStart Example 3 samplesheet must contain four normal rows"
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

test_at_least_one_tumor_is_required() {
  local case_dir="$TEST_TMP/no_tumor"
  mkdir -p "$case_dir/reads"
  printf 'sample_name,status\nCTRL_A,NORMAL\nCTRL_B,NORMAL\n' > "$case_dir/samples.csv"

  if run_illumina "$case_dir"; then
    fail "normal-only setup should be rejected"
  fi

  assert_contains "$case_dir/run.log" 'Illumina configuration requires at least one tumor sample'
  [[ ! -e "$case_dir/config/illumina.auto.yml" ]] || fail "normal-only setup published a YAML"
  [[ ! -e "$case_dir/config/illumina.samplesheet.csv" ]] || fail "normal-only setup published a samplesheet"
  [[ ! -e "$case_dir/config/auto_params_manifest.tsv" ]] || fail "normal-only setup published a manifest"
  assert_no_auto_temps "$case_dir/config"
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

test_tumor_only_disables_pon_and_reports_gzip_progress
test_one_normal_is_rejected_without_publication
test_two_normals_enable_reproducible_pon
test_six_tumors_and_four_normals_match_quickstart_example_3
test_duplicate_ids_are_rejected
test_invalid_ids_are_rejected
test_at_least_one_tumor_is_required
test_corrupt_gzip_preserves_previous_publication

echo "PASS: generate_auto_params Illumina PoN tests"
