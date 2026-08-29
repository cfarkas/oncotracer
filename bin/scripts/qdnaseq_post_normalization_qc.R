# Post-normalization sample validity helpers for native qDNAseq.
#
# This file intentionally uses only base R so the scientific gate can be tested
# without loading the qDNAseq stack.  Callers must pass only normalized copy-
# number values from bins marked usable by the QDNAseq object.

qdnaseq_json_escape <- function(value) {
  value <- gsub("\\\\", "\\\\\\\\", as.character(value), fixed = TRUE)
  value <- gsub('"', '\\\\"', value, fixed = TRUE)
  value <- gsub("\r", "\\\\r", value, fixed = TRUE)
  value <- gsub("\n", "\\\\n", value, fixed = TRUE)
  value <- gsub("\t", "\\\\t", value, fixed = TRUE)
  value
}

qdnaseq_json_string <- function(value) {
  if (is.null(value) || length(value) != 1L || is.na(value)) return("null")
  paste0('"', qdnaseq_json_escape(value), '"')
}

qdnaseq_json_number <- function(value) {
  if (length(value) != 1L || !is.finite(value)) return("null")
  format(as.numeric(value), scientific = FALSE, trim = TRUE, digits = 17L)
}

qdnaseq_classify_normalized <- function(sample_names, normalized_values) {
  if (length(sample_names) != length(normalized_values)) {
    stop("Sample names and normalized-value vectors differ in length", call. = FALSE)
  }
  lapply(seq_along(sample_names), function(index) {
    values <- as.numeric(normalized_values[[index]])
    finite <- values[is.finite(values)]
    usable_count <- length(finite)
    zero_count <- sum(finite == 0)
    normalized_median <- if (usable_count) stats::median(finite) else NA_real_
    valid <- usable_count > 0L && is.finite(normalized_median) && normalized_median > 0
    error <- NULL
    if (!valid) {
      error <- if (!usable_count || !is.finite(normalized_median)) {
        "no_usable_finite_normalized_copy_number_median"
      } else {
        "normalized_copy_number_median_non_positive"
      }
    }
    list(
      sample = as.character(sample_names[[index]]),
      status = if (valid) "pending" else "failed",
      stage = if (valid) "post_normalization_qc_passed" else "post_normalization_qc",
      error = error,
      usable_bin_count = as.integer(usable_count),
      zero_bin_count = as.integer(zero_count),
      normalized_median = normalized_median,
      bins = NULL,
      segments = NULL,
      calls = NULL
    )
  })
}

qdnaseq_valid_indices <- function(records) {
  which(vapply(records, function(record) record$status != "failed", logical(1L)))
}

qdnaseq_mark_complete <- function(record, bins, segments, calls) {
  record$status <- "complete"
  record$stage <- "complete"
  record$error <- NULL
  record$bins <- bins
  record$segments <- segments
  record$calls <- calls
  record
}

qdnaseq_status_payload <- function(records) {
  completed <- vapply(records[vapply(records, function(x) x$status == "complete", logical(1L))], `[[`, character(1L), "sample")
  failed <- vapply(records[vapply(records, function(x) x$status == "failed", logical(1L))], `[[`, character(1L), "sample")
  pending <- vapply(records[vapply(records, function(x) x$status == "pending", logical(1L))], `[[`, character(1L), "sample")
  overall <- if (length(pending)) {
    "in_progress"
  } else if (length(completed) && length(failed)) {
    "partial_failure"
  } else if (length(failed)) {
    "failed"
  } else {
    "complete"
  }
  list(
    schema = "oncotracer-native-qdnaseq-sample-status-v1",
    overall_status = overall,
    sample_count = length(records),
    completed_samples = unname(completed),
    failed_samples = unname(failed),
    pending_samples = unname(pending),
    samples = records,
    updated_at = format(Sys.time(), tz = "UTC", usetz = TRUE)
  )
}

qdnaseq_json_array <- function(values) {
  if (!length(values)) return("[]")
  paste0("[", paste(vapply(values, qdnaseq_json_string, character(1L)), collapse = ","), "]")
}

qdnaseq_record_json <- function(record) {
  paste0(
    "{",
    '"sample":', qdnaseq_json_string(record$sample), ",",
    '"status":', qdnaseq_json_string(record$status), ",",
    '"stage":', qdnaseq_json_string(record$stage), ",",
    '"error":', qdnaseq_json_string(record$error), ",",
    '"usable_bin_count":', as.integer(record$usable_bin_count), ",",
    '"zero_bin_count":', as.integer(record$zero_bin_count), ",",
    '"normalized_median":', qdnaseq_json_number(record$normalized_median), ",",
    '"bins":', qdnaseq_json_string(record$bins), ",",
    '"segments":', qdnaseq_json_string(record$segments), ",",
    '"calls":', qdnaseq_json_string(record$calls),
    "}"
  )
}

write_qdnaseq_sample_status <- function(path, records) {
  payload <- qdnaseq_status_payload(records)
  sample_json <- if (length(records)) {
    paste(vapply(records, qdnaseq_record_json, character(1L)), collapse = ",")
  } else {
    ""
  }
  json <- paste0(
    "{",
    '"schema":', qdnaseq_json_string(payload$schema), ",",
    '"overall_status":', qdnaseq_json_string(payload$overall_status), ",",
    '"sample_count":', as.integer(payload$sample_count), ",",
    '"completed_samples":', qdnaseq_json_array(payload$completed_samples), ",",
    '"failed_samples":', qdnaseq_json_array(payload$failed_samples), ",",
    '"pending_samples":', qdnaseq_json_array(payload$pending_samples), ",",
    '"samples":[', sample_json, "],",
    '"updated_at":', qdnaseq_json_string(payload$updated_at),
    "}"
  )
  temporary <- paste0(path, ".tmp.", Sys.getpid())
  writeLines(json, temporary, useBytes = TRUE)
  if (!file.rename(temporary, path)) {
    unlink(temporary, force = TRUE)
    stop("Could not atomically publish qDNAseq sample status: ", path, call. = FALSE)
  }
  payload
}
