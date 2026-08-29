#!/usr/bin/env Rscript

fail <- function(...) stop(paste0(...), call. = FALSE)

script_path <- function() {
  file_args <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
  if (length(file_args) != 1L) fail("Unable to resolve test script path")
  normalizePath(sub("^--file=", "", file_args[[1L]]), mustWork = TRUE)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 1L) fail("Usage: test_ichorcna_plot_compat.R [OUTPUT.pdf]")
output <- if (length(args)) args[[1L]] else tempfile(fileext = ".pdf")
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)

root <- dirname(dirname(script_path()))
source(file.path(root, "bin/scripts/ichorcna_plot_compat.R"), local = TRUE)
suppressPackageStartupMessages({
  library(ichorCNA)
  library(GenomicRanges)
})

metadata <- oncotracer_patch_ichorcna_plot_correction()
metadata_again <- oncotracer_patch_ichorcna_plot_correction()
stopifnot(
  identical(metadata, metadata_again),
  identical(unname(metadata[["target_quantile_calls"]]), "2"),
  identical(unname(metadata[["zero_median_plot_guard"]]), "placeholder")
)

counts <- GenomicRanges::GRanges(
  seqnames = rep("chr10", 3L),
  ranges = IRanges::IRanges(start = c(1L, 500001L, 1000001L), width = 500000L)
)
counts$reads <- c(0, 0, 1)
counts$valid <- rep(TRUE, 3L)

grDevices::pdf(output, width = 7, height = 4)
ichorCNA::plotCorrectionGenomeWide(counts, chr = "chr10")
grDevices::dev.off()

if (!file.exists(output) || file.info(output)$size <= 0) {
  fail("Zero-median correction placeholder PDF is missing or empty: ", output)
}
cat("ICHORCNA_ZERO_MEDIAN_PLOT_GUARD_OK\n")
cat("output=", normalizePath(output, mustWork = TRUE), "\n", sep = "")
