#!/usr/bin/env bash
set -Eeuo pipefail

# Unit/integration checks for the local qDNAseq PoN helpers. These tests use
# synthetic text artifacts only; they never open or create BAM files.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R_SCRIPT="$ROOT_DIR/bin/scripts/qdnaseq_local_pon.R"
WRAPPER="$ROOT_DIR/bin/scripts/run_qdnaseq_local_pon.sh"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/oncotracer-qdnaseq-local-pon.XXXXXX")"

cleanup() {
  rm -rf -- "$TEST_TMP"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -s "$R_SCRIPT" ]] || fail "missing qDNAseq local PoN R script: $R_SCRIPT"
[[ -s "$WRAPPER" ]] || fail "missing qDNAseq local PoN wrapper: $WRAPPER"

bash -n "$WRAPPER" || fail "Bash syntax validation failed: $WRAPPER"
bash -n "${BASH_SOURCE[0]}" || fail "Bash syntax validation failed: ${BASH_SOURCE[0]}"

if ! command -v Rscript >/dev/null 2>&1; then
  echo 'SKIP: Rscript is unavailable; skipped R parse, host mathematical self-test, artifact gate, and completion-stamp tests'
  echo 'PASS: qDNAseq local PoN Bash syntax tests (R-dependent tests skipped)'
  exit 0
fi

Rscript -e '
  arguments <- commandArgs(trailingOnly = TRUE)
  invisible(parse(file = arguments[[1L]]))
  cat("R_PARSE_OK\n")
' "$R_SCRIPT" > "$TEST_TMP/r_parse.log" 2>&1 || {
  cat "$TEST_TMP/r_parse.log" >&2
  fail "R syntax validation failed: $R_SCRIPT"
}
grep -Fqx 'R_PARSE_OK' "$TEST_TMP/r_parse.log" || fail 'R parse success marker is missing'

Rscript "$R_SCRIPT" --self-test > "$TEST_TMP/self_test.log" 2>&1 || {
  cat "$TEST_TMP/self_test.log" >&2
  fail 'host qDNAseq local PoN mathematical self-test failed'
}
grep -Fqx 'QDNASEQ_LOCAL_PON_SELF_TEST_OK' "$TEST_TMP/self_test.log" || {
  fail 'mathematical self-test success marker is missing'
}

Rscript - "$R_SCRIPT" "$TEST_TMP/artifact_gate" <<'RSCRIPT'
arguments <- commandArgs(trailingOnly = TRUE)
r_script <- arguments[[1L]]
outdir <- arguments[[2L]]
source(r_script, local = .GlobalEnv)

assert_true <- function(condition, message) {
  if (!isTRUE(condition)) stop(message, call. = FALSE)
}

expect_error <- function(expression, expected_pattern, label) {
  observed_error <- tryCatch(
    {
      force(expression)
      NULL
    },
    error = identity
  )
  if (is.null(observed_error)) {
    stop(label, " did not fail", call. = FALSE)
  }
  if (!grepl(expected_pattern, conditionMessage(observed_error), fixed = TRUE)) {
    stop(
      label, " failed with an unexpected error: ", conditionMessage(observed_error),
      call. = FALSE
    )
  }
  invisible(observed_error)
}

tumor_ids <- c("ONCO001", "ONCO002")
normal_ids <- c("CTRL001", "CTRL002")
pon_name <- "TEST_PON"
success_text <- "QDNASEQ_LOCAL_PON_SUCCESS"

dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
artifact_dirs <- file.path(outdir, c("bins", "segments", "plots", "pon", "qc", "rds"))
invisible(lapply(artifact_dirs, dir.create, recursive = TRUE, showWarnings = FALSE))

required_files <- c(
  file.path(
    outdir,
    c(
      "all_segments.seg",
      "all_calls.seg",
      "all_tumors.qdnaseq_pon_corrected_bins.tsv",
      "qdnaseq_local_pon_summary.tsv",
      "qdnaseq_local_pon_versions.tsv"
    )
  ),
  file.path(outdir, "pon", paste0(pon_name, ".reference_bins.tsv")),
  file.path(outdir, "qc", c("sample_qc.tsv", "normal_panel_sample_qc.tsv")),
  file.path(
    outdir,
    "rds",
    c(
      paste0(pon_name, ".all_samples.readCounts.rds"),
      paste0(pon_name, ".all_samples.qdnaseq_corrected.rds"),
      paste0(pon_name, ".tumors.qdnaseq_pon_corrected.rds")
    )
  ),
  file.path(outdir, "bins", paste0(tumor_ids, "_markdup_bins.bed")),
  file.path(
    outdir,
    "segments",
    c(paste0(tumor_ids, "_.seg"), paste0(tumor_ids, ".calls.seg"))
  ),
  file.path(
    outdir,
    "plots",
    paste0(tumor_ids, ".qdnaseq_pon_corrected_segment_plot.pdf")
  ),
  file.path(
    outdir,
    "rds",
    paste0(tumor_ids, ".qdnaseq_pon_corrected.segmented.rds")
  )
)
invisible(lapply(required_files, writeLines, text = "synthetic non-empty artifact"))

normal_manifest_path <- file.path(outdir, "pon", "normal_panel_manifest.tsv")
write_manifest <- function(ids = normal_ids) {
  write_tsv(
    data.frame(
      sample = ids,
      bam = paste0("/synthetic/", ids, ".bam"),
      status = "normal",
      stringsAsFactors = FALSE
    ),
    normal_manifest_path
  )
}
write_manifest()

completion_marker <- file.path(outdir, "qdnaseq_local_pon.done")
writeLines("STALE_SUCCESS", completion_marker)
observed_marker <- invalidate_completion_marker(outdir)
assert_true(identical(observed_marker, completion_marker), "completion-marker path changed")
assert_true(!file.exists(completion_marker), "stale completion marker was not invalidated")

# The exact expected artifact set and normal manifest must pass before success.
validate_required_outputs(outdir, pon_name, tumor_ids, normal_ids)

# A required artifact that exists but is empty must fail the gate and publish no stamp.
empty_artifact <- file.path(outdir, "all_calls.seg")
writeLines(character(), empty_artifact)
expect_error(
  {
    validate_required_outputs(outdir, pon_name, tumor_ids, normal_ids)
    publish_completion_marker(completion_marker)
  },
  "empty file(s)",
  "empty required artifact gate"
)
assert_true(!file.exists(completion_marker), "failure published a completion marker")
writeLines("synthetic non-empty artifact", empty_artifact)

# A stale per-tumor artifact makes the set larger than n_tumors and must fail.
stale_bin <- file.path(outdir, "bins", "STALE_markdup_bins.bed")
writeLines("stale artifact", stale_bin)
expect_error(
  {
    validate_required_outputs(outdir, pon_name, tumor_ids, normal_ids)
    publish_completion_marker(completion_marker)
  },
  "artifact set mismatch",
  "stale tumor-bin artifact gate"
)
assert_true(!file.exists(completion_marker), "stale-artifact failure published a completion marker")
unlink(stale_bin)

# The manifest must contain exactly n_normals with the expected IDs/status.
write_manifest(c(normal_ids, "CTRL_STALE"))
expect_error(
  {
    validate_required_outputs(outdir, pon_name, tumor_ids, normal_ids)
    publish_completion_marker(completion_marker)
  },
  "does not contain exactly the expected",
  "normal-manifest cardinality gate"
)
assert_true(!file.exists(completion_marker), "manifest failure published a completion marker")
write_manifest()

# Probe the success path: the verified temporary file must be renamed from the
# same directory while the final marker is absent, leaving no temporary stamp.
rename_probe <- new.env(parent = emptyenv())
rename_probe$called <- FALSE
assign(
  "file.rename",
  function(from, to) {
    assert_true(!file.exists(to), "final completion marker existed before atomic rename")
    assert_true(
      identical(
        normalizePath(dirname(from), mustWork = TRUE),
        normalizePath(dirname(to), mustWork = TRUE)
      ),
      "temporary and final completion markers are not in the same directory"
    )
    assert_true(identical(readLines(from, warn = FALSE), success_text), "temporary marker was not verified")
    rename_probe$called <- TRUE
    base::file.rename(from, to)
  },
  envir = .GlobalEnv
)
on.exit(
  if (exists("file.rename", envir = .GlobalEnv, inherits = FALSE)) {
    rm("file.rename", envir = .GlobalEnv)
  },
  add = TRUE
)

validate_required_outputs(outdir, pon_name, tumor_ids, normal_ids)
publish_completion_marker(completion_marker)
rm("file.rename", envir = .GlobalEnv)

assert_true(rename_probe$called, "completion marker was not published with file.rename")
assert_true(file.exists(completion_marker), "successful validation did not publish the completion marker")
assert_true(
  identical(readLines(completion_marker, warn = FALSE), success_text),
  "published completion marker has unexpected content"
)
temporary_markers <- list.files(
  outdir,
  pattern = "^[.]qdnaseq_local_pon[.]done[.]",
  all.files = TRUE,
  full.names = TRUE
)
assert_true(length(temporary_markers) == 0L, "atomic publication left a temporary completion marker")

cat("QDNASEQ_LOCAL_PON_ARTIFACT_GATE_OK\n")
RSCRIPT

echo 'PASS: qDNAseq local PoN syntax, mathematics, artifact gate, and completion-stamp tests'
