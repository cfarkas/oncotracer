#!/usr/bin/env Rscript

# Native qDNAseq stage for OncoTracer v2.0.0.
# This reproduces the SAMURAI v1.4.0 qDNAseq sequence without Nextflow.

fail <- function(...) stop(paste0(...), call. = FALSE)

parse_bool <- function(value) {
  value <- tolower(trimws(as.character(value)))
  if (value %in% c("true", "t", "1", "yes", "y", "on")) return(TRUE)
  if (value %in% c("false", "f", "0", "no", "n", "off")) return(FALSE)
  fail("Expected true or false, found: ", value)
}

parse_args <- function(argv) {
  options <- list(
    samplesheet = NULL,
    outdir = NULL,
    binsize = 100L,
    min_mapq = 37L,
    paired_ends = TRUE,
    bin_data = ""
  )
  keys <- c(
    "--samplesheet" = "samplesheet",
    "--outdir" = "outdir",
    "--binsize" = "binsize",
    "--min-mapq" = "min_mapq",
    "--paired-ends" = "paired_ends",
    "--bin-data" = "bin_data"
  )
  i <- 1L
  while (i <= length(argv)) {
    flag <- argv[[i]]
    if (flag %in% c("-h", "--help")) {
      cat("native_qdnaseq.R --samplesheet FILE --outdir DIR [--binsize 100] [--bin-data FILE]\n")
      quit(save = "no", status = 0L)
    }
    if (!flag %in% names(keys)) fail("Unknown option: ", flag)
    if (i == length(argv)) fail("Missing value for ", flag)
    options[[unname(keys[[flag]])]] <- argv[[i + 1L]]
    i <- i + 2L
  }
  options$binsize <- suppressWarnings(as.integer(options$binsize))
  options$min_mapq <- suppressWarnings(as.integer(options$min_mapq))
  options$paired_ends <- parse_bool(options$paired_ends)
  options
}

safe_sample <- function(value) {
  value <- trimws(as.character(value))
  if (!grepl("^[A-Za-z0-9][A-Za-z0-9_.-]*$", value)) {
    fail("Invalid sample ID: ", value)
  }
  value
}

read_samplesheet <- function(path) {
  if (!file.exists(path) || file.info(path)$size <= 0) fail("Samplesheet is missing: ", path)
  rows <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("sample", "bam", "status")
  missing <- setdiff(required, names(rows))
  if (length(missing)) fail("Samplesheet is missing: ", paste(missing, collapse = ", "))
  if (!nrow(rows)) fail("Samplesheet has no rows")
  rows$sample <- vapply(rows$sample, safe_sample, character(1L))
  rows$status <- tolower(trimws(as.character(rows$status)))
  if (any(rows$status != "tumor")) {
    fail("Native no-PoN qDNAseq accepts tumor rows only; use the local-PoN stage for controls")
  }
  if (anyDuplicated(rows$sample)) fail("Duplicate sample IDs in samplesheet")
  rows$bam <- normalizePath(rows$bam, mustWork = TRUE)
  if (any(file.info(rows$bam)$size <= 0)) fail("One or more BAM files are empty")
  rows
}

write_tsv <- function(data, path) {
  utils::write.table(data, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "NA")
}

combine_headered <- function(paths, destination) {
  if (!length(paths)) fail("No files to combine into ", destination)
  output <- character()
  expected_header <- NULL
  for (i in seq_along(paths)) {
    lines <- readLines(paths[[i]], warn = FALSE)
    if (!length(lines)) fail("Empty export: ", paths[[i]])
    if (is.null(expected_header)) expected_header <- lines[[1L]]
    if (!identical(lines[[1L]], expected_header)) fail("SEG headers differ: ", paths[[i]])
    if (i > 1L) lines <- lines[-1L]
    output <- c(output, lines)
  }
  writeLines(output, destination, useBytes = TRUE)
}

export_samurai_seg <- function(object, destination, sample, type = "segments") {
  raw_path <- tempfile(pattern = ".qdnaseq_export_", tmpdir = dirname(destination), fileext = ".seg")
  on.exit(unlink(raw_path), add = TRUE)
  QDNAseq::exportBins(
    object,
    file = raw_path,
    format = "seg",
    type = type,
    filter = TRUE,
    logTransform = TRUE
  )
  raw <- utils::read.delim(raw_path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("CHROMOSOME", "START", "STOP", "DATAPOINTS", "LOG2_RATIO_MEAN")
  missing <- setdiff(required, names(raw))
  if (length(missing)) fail("Unexpected qDNAseq SEG columns: ", paste(missing, collapse = ", "))
  chromosome <- as.character(raw$CHROMOSOME)
  chromosome <- ifelse(grepl("^chr", chromosome, ignore.case = TRUE), chromosome, paste0("chr", chromosome))
  samurai <- data.frame(
    ID = rep(paste0(sample, "_markdup"), nrow(raw)),
    chrom = chromosome,
    start = raw$START,
    end = raw$STOP,
    `num.mark` = raw$DATAPOINTS,
    `seg.mean` = raw$LOG2_RATIO_MEAN,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (type == "calls") {
    value <- suppressWarnings(as.numeric(samurai$seg.mean))
    samurai$call <- ifelse(
      value < -2,
      -2L,
      ifelse(value < -0.42, -1L, ifelse(value > 2.32, 2L, ifelse(value > 0.32, 1L, 0L)))
    )
  }
  utils::write.table(
    samurai,
    destination,
    sep = "\t",
    quote = TRUE,
    row.names = FALSE,
    col.names = TRUE,
    na = "NA"
  )
}

run_analysis <- function(options) {
  if (is.null(options$samplesheet) || is.null(options$outdir)) {
    fail("--samplesheet and --outdir are required")
  }
  if (is.na(options$binsize) || options$binsize < 1L) fail("--binsize must be positive")
  if (is.na(options$min_mapq) || options$min_mapq < 0L) fail("--min-mapq must be non-negative")

  suppressPackageStartupMessages({
    library(QDNAseq)
    library(Biobase)
  })

  samples <- read_samplesheet(options$samplesheet)
  outdir <- normalizePath(options$outdir, mustWork = FALSE)
  bins_dir <- file.path(outdir, "bins")
  segments_dir <- file.path(outdir, "segments")
  plots_dir <- file.path(outdir, "plots")
  rds_dir <- file.path(outdir, "rds")
  dir.create(bins_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(segments_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(plots_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(rds_dir, recursive = TRUE, showWarnings = FALSE)

  if (nzchar(options$bin_data)) {
    bin_annotations <- readRDS(normalizePath(options$bin_data, mustWork = TRUE))
  } else {
    bin_annotations <- QDNAseq::getBinAnnotations(binSize = options$binsize, genome = "hg38")
  }
  bamfiles <- samples$bam
  bamnames <- paste0(samples$sample, "_markdup")
  names(bamfiles) <- bamnames

  read_counts <- QDNAseq::binReadCounts(
    bin_annotations,
    bamfiles = bamfiles,
    bamnames = bamnames,
    minMapq = options$min_mapq,
    pairedEnds = options$paired_ends
  )
  saveRDS(read_counts, file.path(rds_dir, "all_samples.readCounts.rds"))

  read_counts <- QDNAseq::applyFilters(
    read_counts,
    residual = TRUE,
    blacklist = TRUE,
    chromosomes = c("X", "Y", "MT")
  )
  read_counts <- QDNAseq::estimateCorrection(read_counts)
  copy_numbers <- QDNAseq::correctBins(read_counts)
  copy_numbers <- QDNAseq::normalizeBins(copy_numbers)
  copy_numbers <- QDNAseq::smoothOutlierBins(copy_numbers)
  saveRDS(copy_numbers, file.path(rds_dir, "all_samples.corrected.rds"))

  segmented <- QDNAseq::segmentBins(copy_numbers, transformFun = "log2")
  called <- QDNAseq::callBins(segmented, method = "cutoff")
  saveRDS(segmented, file.path(rds_dir, "all_samples.segmented.rds"))
  saveRDS(called, file.path(rds_dir, "all_samples.called.rds"))

  segment_files <- character(nrow(samples))
  call_files <- character(nrow(samples))
  summary_rows <- vector("list", nrow(samples))

  for (i in seq_len(nrow(samples))) {
    sample <- samples$sample[[i]]
    prefix <- paste0(sample, "_markdup")
    QDNAseq::exportBins(
      copy_numbers[, i],
      file = file.path(bins_dir, paste0(prefix, "_bins.bed")),
      format = "bed",
      type = "copynumber",
      filter = TRUE,
      logTransform = TRUE
    )
    segment_files[[i]] <- file.path(segments_dir, paste0(sample, "_.seg"))
    call_files[[i]] <- file.path(segments_dir, paste0(sample, ".calls.seg"))
    export_samurai_seg(segmented[, i], segment_files[[i]], sample, "segments")
    export_samurai_seg(called[, i], call_files[[i]], sample, "calls")

    grDevices::pdf(file.path(plots_dir, paste0(prefix, "_segment_plot.pdf")), width = 14, height = 5)
    try(plot(segmented[, i], main = paste0(sample, " qDNAseq")), silent = TRUE)
    grDevices::dev.off()

    assay <- Biobase::assayDataElement(copy_numbers[, i], "copynumber")
    finite <- assay[is.finite(assay) & assay > 0]
    summary_rows[[i]] <- data.frame(
      sample = sample,
      n_bins = length(finite),
      median_copy_number = if (length(finite)) stats::median(finite) else NA_real_,
      mad_copy_number = if (length(finite) > 1L) stats::mad(finite) else NA_real_,
      stringsAsFactors = FALSE
    )
  }

  combine_headered(segment_files, file.path(outdir, "all_segments.seg"))
  combine_headered(call_files, file.path(outdir, "all_calls.seg"))
  write_tsv(do.call(rbind, summary_rows), file.path(outdir, "qdnaseq_summary_mqc.txt"))
  write_tsv(
    data.frame(
      component = c("R", "QDNAseq"),
      version = c(as.character(getRversion()), as.character(utils::packageVersion("QDNAseq"))),
      stringsAsFactors = FALSE
    ),
    file.path(outdir, "qdnaseq_native_versions.tsv")
  )

  required <- c(
    file.path(outdir, "all_segments.seg"),
    file.path(outdir, "all_calls.seg"),
    file.path(outdir, "qdnaseq_summary_mqc.txt"),
    file.path(bins_dir, paste0(samples$sample, "_markdup_bins.bed"))
  )
  bad <- required[!file.exists(required) | file.info(required)$size <= 0]
  if (length(bad)) fail("Missing native qDNAseq output(s): ", paste(bad, collapse = ", "))
  cat("Native qDNAseq completed: ", outdir, "\n", sep = "")
}

options <- parse_args(commandArgs(trailingOnly = TRUE))
tryCatch(
  run_analysis(options),
  error = function(error) {
    message("ERROR: ", conditionMessage(error))
    quit(save = "no", status = 1L, runLast = FALSE)
  }
)
