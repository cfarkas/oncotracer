#!/usr/bin/env Rscript

# Native, argument-safe adaptation of MARLIN_realtime/2_process_pileup.R from
# hovestadt/MARLIN commit 37c9836cc325ff2edccbdff06736604163db2c15
# (MIT license). The upstream scientific transformation is preserved:
# Modkit valid/modified coverage is joined to reference probes, beta is
# sum(modified)/sum(valid), and uncovered probes remain NA.
# The complete upstream notice is distributed as MARLIN-MIT-LICENSE.txt.

suppressPackageStartupMessages(library(data.table))

fail <- function(...) stop(paste0(...), call. = FALSE)

parse_options <- function(arguments) {
  required <- c("--bedmethyl", "--probes", "--output")
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
for (key in c("bedmethyl", "probes")) {
  path <- options[[key]]
  if (!file.exists(path) || file.info(path)$size <= 0) {
    fail("Missing or empty ", key, ": ", path)
  }
}

probes <- fread(
  options$probes,
  header = FALSE,
  select = c(1L, 2L, 4L),
  showProgress = FALSE
)
if (ncol(probes) != 3L || !nrow(probes)) fail("MARLIN probe BED is empty or malformed")
setnames(probes, c("chrom", "ref_position", "probe_id"))
probes[, ref_position := as.integer(ref_position)]
if (anyNA(probes$ref_position) || any(!nzchar(probes$probe_id))) {
  fail("MARLIN probe BED has invalid position or probe identifiers")
}

calls <- fread(
  options$bedmethyl,
  header = FALSE,
  select = c(1L, 2L, 10L, 12L),
  showProgress = FALSE
)
if (ncol(calls) != 4L || !nrow(calls)) fail("Modkit bedMethyl contains no CpG rows")
setnames(calls, c("chrom", "ref_position", "cov_valid", "cov_mod"))
calls[, ref_position := as.integer(ref_position)]
calls[, cov_valid := as.numeric(cov_valid)]
calls[, cov_mod := as.numeric(cov_mod)]
if (
  anyNA(calls$ref_position) ||
  any(!is.finite(calls$cov_valid)) ||
  any(!is.finite(calls$cov_mod)) ||
  any(calls$cov_valid < 0) ||
  any(calls$cov_mod < 0) ||
  any(calls$cov_mod > calls$cov_valid)
) {
  fail("Modkit bedMethyl has invalid coverage values")
}

probe_coordinates <- probes[
  ,
  {
    chromosomes <- unique(chrom)
    if (length(chromosomes) != 1L) fail("Probe maps to multiple chromosomes: ", probe_id[[1L]])
    list(
      chrom = chromosomes[[1L]],
      chromStart = min(ref_position),
      chromEnd = max(ref_position) + 1L
    )
  },
  by = probe_id
]
mapped <- merge(calls, probes, by = c("chrom", "ref_position"))
if (!nrow(mapped)) {
  fail("No Modkit CpG rows overlap the supplied MARLIN probe coordinates")
}
betas <- mapped[
  ,
  {
    denominator <- sum(cov_valid)
    list(beta = if (denominator > 0) sum(cov_mod) / denominator else NA_real_)
  },
  by = probe_id
]
if (!any(is.finite(betas$beta))) fail("No covered MARLIN probes remain after mapping")

output <- merge(probe_coordinates, betas, by = "probe_id", all.x = TRUE)
output <- output[order(chrom, chromStart), .(chrom, chromStart, chromEnd, beta, probe_id)]
destination <- normalizePath(options$output, mustWork = FALSE)
dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
temporary <- paste0(destination, ".tmp.", Sys.getpid())
fwrite(output, temporary, sep = "\t", quote = FALSE, row.names = FALSE, col.names = FALSE, na = "NA")
if (!file.rename(temporary, destination)) {
  unlink(temporary)
  fail("Could not atomically publish MARLIN input: ", destination)
}
