#!/usr/bin/env Rscript

# Native qDNAseq local panel-of-normals stage for OncoTracer v2.0.0.
# This is a direct R execution path and does not invoke Nextflow.

fail <- function(...) stop(paste0(...), call. = FALSE)

parse_bool <- function(value, label) {
  normalized <- tolower(trimws(as.character(value)))
  if (normalized %in% c("true", "t", "1", "yes", "y", "on")) return(TRUE)
  if (normalized %in% c("false", "f", "0", "no", "n", "off")) return(FALSE)
  fail(label, " must be true or false: ", value)
}

parse_args <- function(argv) {
  options <- list(
    samplesheet = NULL,
    outdir = NULL,
    binsize = 100L,
    min_mapq = 37L,
    min_normals = 2L,
    paired_ends = TRUE,
    pon_name = "illumina_local_PoN",
    bin_data = ""
  )
  keys <- c(
    "--samplesheet" = "samplesheet",
    "--outdir" = "outdir",
    "--binsize" = "binsize",
    "--min-mapq" = "min_mapq",
    "--min-normals" = "min_normals",
    "--paired-ends" = "paired_ends",
    "--pon-name" = "pon_name",
    "--bin-data" = "bin_data"
  )
  i <- 1L
  while (i <= length(argv)) {
    flag <- argv[[i]]
    if (flag %in% c("-h", "--help")) {
      cat("native_qdnaseq_pon.R --samplesheet FILE --outdir DIR --bin-data RDS [options]\n")
      quit(save = "no", status = 0L)
    }
    if (!flag %in% names(keys)) fail("Unknown option: ", flag)
    if (i == length(argv)) fail("Missing value for ", flag)
    options[[unname(keys[[flag]])]] <- argv[[i + 1L]]
    i <- i + 2L
  }
  options$binsize <- suppressWarnings(as.integer(options$binsize))
  options$min_mapq <- suppressWarnings(as.integer(options$min_mapq))
  options$min_normals <- suppressWarnings(as.integer(options$min_normals))
  options$paired_ends <- parse_bool(options$paired_ends, "--paired-ends")
  options
}

safe_id <- function(value) {
  value <- trimws(as.character(value))
  if (!grepl("^[A-Za-z0-9][A-Za-z0-9_.-]*$", value)) fail("Invalid sample ID: ", value)
  value
}

read_samplesheet <- function(path, min_normals) {
  if (is.null(path) || !file.exists(path) || file.info(path)$size <= 0) fail("Invalid samplesheet: ", path)
  sheet <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("sample", "bam", "status")
  missing <- setdiff(required, names(sheet))
  if (length(missing)) fail("Missing samplesheet column(s): ", paste(missing, collapse = ", "))
  if (!nrow(sheet)) fail("Samplesheet contains no rows")
  sheet <- sheet[, required, drop = FALSE]
  sheet$sample <- vapply(sheet$sample, safe_id, character(1L))
  sheet$status <- tolower(trimws(as.character(sheet$status)))
  if (any(!sheet$status %in% c("tumor", "normal"))) fail("status must be tumor or normal")
  if (anyDuplicated(sheet$sample)) fail("Duplicate sample IDs in samplesheet")
  sheet$bam <- vapply(sheet$bam, function(value) normalizePath(value, mustWork = TRUE), character(1L))
  if (sum(sheet$status == "normal") < min_normals) fail("At least ", min_normals, " normal samples are required")
  if (!any(sheet$status == "tumor")) fail("At least one tumor sample is required")
  sheet
}

row_median <- function(values) {
  values <- values[is.finite(values)]
  if (!length(values)) return(NA_real_)
  stats::median(values)
}

combine_headered <- function(paths, destination) {
  output <- character()
  header <- NULL
  for (i in seq_along(paths)) {
    lines <- readLines(paths[[i]], warn = FALSE)
    if (!length(lines)) fail("Empty export: ", paths[[i]])
    if (is.null(header)) header <- lines[[1L]]
    if (!identical(lines[[1L]], header)) fail("SEG headers differ: ", paths[[i]])
    if (i > 1L) lines <- lines[-1L]
    output <- c(output, lines)
  }
  writeLines(output, destination, useBytes = TRUE)
}

export_samurai_seg <- function(object, destination, sample, type = "segments") {
  raw_path <- tempfile(pattern = ".qdnaseq_export_", tmpdir = dirname(destination), fileext = ".seg")
  on.exit(unlink(raw_path), add = TRUE)
  QDNAseq::exportBins(object, file = raw_path, format = "seg", type = type, filter = TRUE, logTransform = TRUE)
  raw <- utils::read.delim(raw_path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("CHROMOSOME", "START", "STOP", "DATAPOINTS", "LOG2_RATIO_MEAN")
  missing <- setdiff(required, names(raw))
  if (length(missing)) fail("Unexpected qDNAseq SEG columns: ", paste(missing, collapse = ", "))
  chromosome <- as.character(raw$CHROMOSOME)
  chromosome <- ifelse(grepl("^chr", chromosome, ignore.case = TRUE), chromosome, paste0("chr", chromosome))
  result <- data.frame(
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
    value <- suppressWarnings(as.numeric(result$seg.mean))
    result$call <- ifelse(value < -2, -2L, ifelse(value < -0.42, -1L, ifelse(value > 2.32, 2L, ifelse(value > 0.32, 1L, 0L))))
  }
  utils::write.table(result, destination, sep = "\t", quote = TRUE, row.names = FALSE, col.names = TRUE, na = "NA")
}

write_tsv <- function(value, path) {
  utils::write.table(value, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "NA")
}

run_analysis <- function(options) {
  if (is.null(options$samplesheet) || is.null(options$outdir)) fail("--samplesheet and --outdir are required")
  if (!nzchar(options$bin_data) || !file.exists(options$bin_data)) fail("--bin-data RDS is required")
  if (is.na(options$binsize) || options$binsize < 1L) fail("--binsize must be positive")
  if (is.na(options$min_mapq) || options$min_mapq < 0L) fail("--min-mapq must be non-negative")
  if (is.na(options$min_normals) || options$min_normals < 2L) fail("--min-normals must be >= 2")

  suppressPackageStartupMessages({ library(QDNAseq); library(Biobase) })
  samples <- read_samplesheet(options$samplesheet, options$min_normals)
  outdir <- normalizePath(options$outdir, mustWork = FALSE)
  dirs <- c("bins", "segments", "plots", "rds", "pon", "qc")
  for (name in dirs) dir.create(file.path(outdir, name), recursive = TRUE, showWarnings = FALSE)

  annotations <- readRDS(normalizePath(options$bin_data, mustWork = TRUE))
  bamfiles <- samples$bam
  names(bamfiles) <- samples$sample
  read_counts <- QDNAseq::binReadCounts(
    annotations,
    bamfiles = bamfiles,
    bamnames = samples$sample,
    minMapq = options$min_mapq,
    pairedEnds = options$paired_ends
  )
  saveRDS(read_counts, file.path(outdir, "rds", paste0(options$pon_name, ".all_samples.readCounts.rds")))
  read_counts <- QDNAseq::applyFilters(read_counts, residual = TRUE, blacklist = TRUE, chromosomes = c("X", "Y", "MT"))
  copy_numbers <- QDNAseq::smoothOutlierBins(QDNAseq::normalizeBins(QDNAseq::correctBins(QDNAseq::estimateCorrection(read_counts))))
  saveRDS(copy_numbers, file.path(outdir, "rds", paste0(options$pon_name, ".all_samples.qdnaseq_corrected.rds")))

  observed <- Biobase::sampleNames(copy_numbers)
  normal_ids <- samples$sample[samples$status == "normal"]
  tumor_ids <- samples$sample[samples$status == "tumor"]
  normal_index <- match(normal_ids, observed)
  tumor_index <- match(tumor_ids, observed)
  if (anyNA(normal_index) || anyNA(tumor_index)) fail("qDNAseq sample names do not match the samplesheet")
  copy_matrix <- Biobase::assayDataElement(copy_numbers, "copynumber")
  copy_matrix[!is.finite(copy_matrix) | copy_matrix <= 0] <- NA_real_
  log2_matrix <- log2(copy_matrix)
  reference <- apply(log2_matrix[, normal_index, drop = FALSE], 1L, row_median)
  corrected <- sweep(log2_matrix[, tumor_index, drop = FALSE], 1L, reference, FUN = "-")
  corrected_ratio <- 2^corrected
  corrected_ratio[!is.finite(corrected_ratio)] <- NA_real_

  tumors <- copy_numbers[, tumor_index]
  Biobase::assayDataElement(tumors, "copynumber") <- corrected_ratio
  Biobase::sampleNames(tumors) <- paste0(tumor_ids, "_markdup")
  saveRDS(tumors, file.path(outdir, "rds", paste0(options$pon_name, ".tumors.qdnaseq_pon_corrected.rds")))
  segmented <- QDNAseq::segmentBins(tumors, transformFun = "log2")
  called <- QDNAseq::callBins(segmented, method = "cutoff")

  segment_files <- character(length(tumor_ids))
  call_files <- character(length(tumor_ids))
  for (i in seq_along(tumor_ids)) {
    sample <- tumor_ids[[i]]
    QDNAseq::exportBins(
      tumors[, i],
      file = file.path(outdir, "bins", paste0(sample, "_markdup_bins.bed")),
      format = "bed", type = "copynumber", filter = TRUE, logTransform = TRUE
    )
    segment_files[[i]] <- file.path(outdir, "segments", paste0(sample, "_.seg"))
    call_files[[i]] <- file.path(outdir, "segments", paste0(sample, ".calls.seg"))
    export_samurai_seg(segmented[, i], segment_files[[i]], sample, "segments")
    export_samurai_seg(called[, i], call_files[[i]], sample, "calls")
    saveRDS(segmented[, i], file.path(outdir, "rds", paste0(sample, ".qdnaseq_pon_corrected.segmented.rds")))
    grDevices::pdf(file.path(outdir, "plots", paste0(sample, ".qdnaseq_pon_corrected_segment_plot.pdf")), width = 14, height = 5)
    try(plot(segmented[, i], main = paste0(sample, " qDNAseq PoN-corrected")), silent = TRUE)
    grDevices::dev.off()
  }
  combine_headered(segment_files, file.path(outdir, "all_segments.seg"))
  combine_headered(call_files, file.path(outdir, "all_calls.seg"))

  features <- as.data.frame(Biobase::fData(tumors), stringsAsFactors = FALSE)
  features$pon_median_log2 <- reference
  for (i in seq_along(tumor_ids)) features[[paste0(tumor_ids[[i]], ".pon_log2")]] <- corrected[, i]
  write_tsv(features, file.path(outdir, "all_tumors.qdnaseq_pon_corrected_bins.tsv"))
  manifest <- samples[samples$status == "normal", c("sample", "bam", "status"), drop = FALSE]
  write_tsv(manifest, file.path(outdir, "pon", "normal_panel_manifest.tsv"))
  write_tsv(
    data.frame(
      pon_applied = "true", pon_name = options$pon_name, n_normals = length(normal_ids),
      normals = paste(normal_ids, collapse = ";"), n_tumors = length(tumor_ids),
      tumors = paste(tumor_ids, collapse = ";"), genome = "hg38", binsize_kbp = options$binsize,
      min_mapq = options$min_mapq, paired_ends = tolower(as.character(options$paired_ends)),
      generated_at_utc = format(Sys.time(), tz = "UTC", usetz = TRUE), stringsAsFactors = FALSE
    ),
    file.path(outdir, "qdnaseq_local_pon_summary.tsv")
  )
  writeLines("QDNASEQ_LOCAL_PON_SUCCESS", file.path(outdir, "qdnaseq_local_pon.done"))
  cat("Native qDNAseq local PoN completed: ", outdir, "\n", sep = "")
}

options <- parse_args(commandArgs(trailingOnly = TRUE))
tryCatch(run_analysis(options), error = function(error) {
  message("ERROR: ", conditionMessage(error))
  quit(save = "no", status = 1L, runLast = FALSE)
})
