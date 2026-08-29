#!/usr/bin/env Rscript

# Native, argument-safe adaptation of MARLIN_realtime/3_marlin_predictions_live.R
# from hovestadt/MARLIN commit 37c9836cc325ff2edccbdff06736604163db2c15
# (MIT license). Its scientific preprocessing is unchanged: ordered feature
# beta values are binarized at 0.5 to 1/-1, uncovered values become 0, and the
# supplied upstream Keras model produces the class scores.
# The complete upstream notice is distributed as MARLIN-MIT-LICENSE.txt.

configured_python <- Sys.getenv("RETICULATE_PYTHON", unset = "")
if (!nzchar(configured_python) || !file.exists(configured_python)) {
  stop("RETICULATE_PYTHON must name the explicit MARLIN Python executable", call. = FALSE)
}
if (!identical(Sys.getenv("RETICULATE_USE_MANAGED_VENV", unset = ""), "no")) {
  stop("RETICULATE_USE_MANAGED_VENV must be no; automatic environments are forbidden", call. = FALSE)
}

suppressPackageStartupMessages({
  library(data.table)
  library(keras)
  library(openxlsx)
})

fail <- function(...) stop(paste0(...), call. = FALSE)

parse_options <- function(arguments) {
  required <- c(
    "--input", "--features", "--model", "--classes", "--sample", "--output"
  )
  values <- list()
  index <- 1L
  while (index <= length(arguments)) {
    key <- arguments[[index]]
    if (!(key %in% required) || index == length(arguments)) {
      fail("Unknown or incomplete argument: ", key)
    }
    values[[substring(key, 3L)]] <- arguments[[index + 1L]]
    index <- index + 2L
  }
  missing <- substring(required, 3L)[!substring(required, 3L) %in% names(values)]
  if (length(missing)) fail("Missing arguments: ", paste(missing, collapse = ", "))
  values
}

options <- parse_options(commandArgs(trailingOnly = TRUE))
for (key in c("input", "features", "model", "classes")) {
  path <- options[[key]]
  if (!file.exists(path) || file.info(path)$size <= 0) {
    fail("Missing or empty ", key, ": ", path)
  }
}
if (!nzchar(options$sample)) fail("Sample name must not be empty")

feature_environment <- new.env(parent = emptyenv())
loaded <- load(options$features, envir = feature_environment)
if (!("betas_sub_names" %in% loaded)) {
  fail("MARLIN features file does not define betas_sub_names")
}
feature_names <- get("betas_sub_names", envir = feature_environment)
if (!length(feature_names) || anyDuplicated(feature_names)) {
  fail("MARLIN feature names are empty or duplicated")
}

input <- fread(options$input, header = FALSE, showProgress = FALSE)
if (ncol(input) < 5L || !nrow(input)) fail("MARLIN input BED is empty or malformed")
ordered_beta <- input[[4L]][match(feature_names, input[[5L]])]
transformed <- ifelse(ordered_beta >= 0.5, 1, -1)
transformed[is.na(transformed)] <- 0
covered <- sum(transformed != 0)
if (covered == 0L) fail("No covered MARLIN model features are available")

model <- load_model_hdf5(options$model)
prediction <- model %>% predict(t(matrix(transformed)))
if (!is.matrix(prediction) || nrow(prediction) != 1L) {
  fail("MARLIN model returned an unexpected prediction shape")
}
if (any(!is.finite(prediction))) {
  fail("MARLIN model returned non-finite prediction values")
}

class_annotation <- read.xlsx(options$classes)
required_columns <- c("model_id", "class_name_current")
if (!all(required_columns %in% names(class_annotation))) {
  fail("MARLIN class annotation lacks model_id/class_name_current")
}
if (
  anyNA(class_annotation$model_id) ||
  anyNA(class_annotation$class_name_current) ||
  any(!nzchar(trimws(class_annotation$class_name_current))) ||
  anyDuplicated(class_annotation$model_id) ||
  anyDuplicated(class_annotation$class_name_current)
) {
  fail("MARLIN class annotation has missing or duplicated identifiers")
}
class_annotation <- class_annotation[order(class_annotation$model_id), ]
if (ncol(prediction) != nrow(class_annotation)) {
  fail(
    "MARLIN model/class count mismatch: ",
    ncol(prediction), " predictions versus ", nrow(class_annotation), " classes"
  )
}
colnames(prediction) <- class_annotation$class_name_current
result <- data.frame(
  sample = options$sample,
  as.data.frame(prediction, check.names = FALSE),
  cov_cpgs = covered,
  time = format(Sys.time(), tz = "UTC", usetz = TRUE),
  check.names = FALSE
)

destination <- normalizePath(options$output, mustWork = FALSE)
dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
temporary <- paste0(destination, ".tmp.", Sys.getpid())
write.table(result, temporary, sep = "\t", quote = FALSE, row.names = FALSE)
if (!file.rename(temporary, destination)) {
  unlink(temporary)
  fail("Could not atomically publish MARLIN predictions: ", destination)
}
