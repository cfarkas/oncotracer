#!/usr/bin/env Rscript
# Legacy v1.1 source companion; native v2 packages native_qdnaseq_pon.R instead.


# Build and apply a cohort-local qDNAseq panel of normals (PoN).
#
# The input samplesheet has columns: sample,bam,status. All BAMs are counted
# together in one binReadCounts() call so tumors and normals use identical
# qDNAseq annotations, filters, MAPQ, and paired-end settings.

fail <- function(...) {
  stop(paste0(...), call. = FALSE)
}

parse_bool <- function(value, label) {
  normalized <- tolower(trimws(as.character(value)))
  if (normalized %in% c("true", "t", "1", "yes", "y", "on")) return(TRUE)
  if (normalized %in% c("false", "f", "0", "no", "n", "off")) return(FALSE)
  fail(label, " must be true or false; found: ", value)
}

parse_cli <- function(argv) {
  options <- list(
    samplesheet = NULL,
    outdir = NULL,
    binsize = 100L,
    genome = "hg38",
    min_mapq = 37L,
    min_normals = 2L,
    paired_ends = TRUE,
    pon_name = "Illumina_local_PoN",
    qdnaseq_bin_data = "",
    self_test = FALSE,
    help = FALSE
  )
  value_flags <- c(
    "--samplesheet" = "samplesheet",
    "--outdir" = "outdir",
    "--binsize" = "binsize",
    "--genome" = "genome",
    "--min-mapq" = "min_mapq",
    "--min-normals" = "min_normals",
    "--paired-ends" = "paired_ends",
    "--pon-name" = "pon_name",
    "--qdnaseq-bin-data" = "qdnaseq_bin_data"
  )

  index <- 1L
  while (index <= length(argv)) {
    flag <- argv[[index]]
    if (flag == "--self-test") {
      options$self_test <- TRUE
      index <- index + 1L
    } else if (flag %in% c("-h", "--help")) {
      options$help <- TRUE
      index <- index + 1L
    } else if (flag %in% names(value_flags)) {
      if (index == length(argv)) fail("Missing value for ", flag)
      options[[unname(value_flags[[flag]])]] <- argv[[index + 1L]]
      index <- index + 2L
    } else {
      fail("Unknown argument: ", flag)
    }
  }

  options$binsize <- suppressWarnings(as.integer(options$binsize))
  options$min_mapq <- suppressWarnings(as.integer(options$min_mapq))
  options$min_normals <- suppressWarnings(as.integer(options$min_normals))
  options$paired_ends <- parse_bool(options$paired_ends, "--paired-ends")
  options
}

usage <- function() {
  cat(paste(
    "Usage:",
    "  Rscript qdnaseq_local_pon.R --samplesheet FILE --outdir DIR [options]",
    "  Rscript qdnaseq_local_pon.R --self-test",
    "",
    "Required samplesheet columns: sample,bam,status",
    "Accepted status values: tumor, normal",
    "",
    "Options:",
    "  --binsize N                 qDNAseq bin size in kbp [100]",
    "  --genome NAME               qDNAseq genome annotation [hg38]",
    "  --min-mapq N                minimum mapping quality [37]",
    "  --min-normals N             required number of normals [2]",
    "  --paired-ends true|false    count paired-end reads [true]",
    "  --pon-name NAME             output/provenance name [Illumina_local_PoN]",
    "  --qdnaseq-bin-data PATH     optional QDNAseq bin annotation path",
    sep = "\n"
  ))
}

autosome_mask <- function(chromosomes) {
  normalized <- toupper(trimws(as.character(chromosomes)))
  normalized <- sub("^CHR", "", normalized)
  normalized %in% as.character(seq_len(22L))
}

row_stat <- function(matrix, function_name) {
  apply(matrix, 1L, function(values) {
    values <- values[is.finite(values)]
    if (length(values) == 0L) return(NA_real_)
    if (function_name == "median") return(stats::median(values))
    if (length(values) < 2L) return(0)
    stats::mad(values, constant = 1.4826)
  })
}

pon_correct_log2 <- function(normal_log2, tumor_log2) {
  if (!is.matrix(normal_log2)) normal_log2 <- as.matrix(normal_log2)
  if (!is.matrix(tumor_log2)) tumor_log2 <- as.matrix(tumor_log2)
  if (nrow(normal_log2) != nrow(tumor_log2)) {
    fail("Normal and tumor matrices must have the same number of bins")
  }
  reference <- row_stat(normal_log2, "median")
  corrected <- sweep(tumor_log2, 1L, reference, FUN = "-")
  list(reference = reference, corrected = corrected)
}

compute_loo_normal_qc <- function(normal_log2, normal_ids) {
  if (!is.matrix(normal_log2)) normal_log2 <- as.matrix(normal_log2)
  if (ncol(normal_log2) != length(normal_ids)) fail("Normal matrix/name count mismatch")
  if (ncol(normal_log2) < 2L) fail("Leave-one-out normal QC requires at least two normals")
  do.call(rbind, lapply(seq_along(normal_ids), function(index) {
    reference_index <- setdiff(seq_along(normal_ids), index)
    loo_reference <- row_stat(normal_log2[, reference_index, drop = FALSE], "median")
    residual <- normal_log2[, index] - loo_reference
    residual <- residual[is.finite(residual)]
    data.frame(
      sample = normal_ids[[index]],
      loo_median_residual_log2 = if (length(residual) > 0L) stats::median(residual) else NA_real_,
      loo_mad_residual_log2 = if (length(residual) > 1L) stats::mad(residual) else NA_real_,
      loo_n_reference_normals = length(reference_index),
      loo_n_finite_bins = length(residual),
      stringsAsFactors = FALSE
    )
  }))
}

run_self_test <- function() {
  normal_log2 <- matrix(
    c(0.0, 0.2, 1.0, 0.8, -1.0, -1.2),
    nrow = 3L,
    ncol = 2L,
    byrow = TRUE,
    dimnames = list(NULL, c("CTRL001", "CTRL002"))
  )
  tumor_log2 <- matrix(
    c(1.1, 0.4, -0.1),
    nrow = 3L,
    ncol = 1L,
    dimnames = list(NULL, "ONCO001")
  )
  result <- pon_correct_log2(normal_log2, tumor_log2)
  loo <- compute_loo_normal_qc(normal_log2, colnames(normal_log2))
  expected_reference <- c(0.1, 0.9, -1.1)
  expected_corrected <- matrix(c(1.0, -0.5, 1.0), ncol = 1L)

  stopifnot(
    isTRUE(all.equal(result$reference, expected_reference, tolerance = 1e-12)),
    isTRUE(all.equal(unname(result$corrected), expected_corrected, tolerance = 1e-12)),
    identical(
      autosome_mask(c("chr1", "22", "X", "chrY", "MT", "chrM", "25")),
      c(TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE)
    ),
    isTRUE(all.equal(2^result$corrected[, 1L], c(2, 2^(-0.5), 2), tolerance = 1e-12)),
    isTRUE(all.equal(loo$loo_median_residual_log2, c(0.2, -0.2), tolerance = 1e-12)),
    identical(loo$loo_n_reference_normals, c(1L, 1L))
  )
  cat("QDNASEQ_LOCAL_PON_SELF_TEST_OK\n")
}

safe_component <- function(value, label) {
  value <- trimws(as.character(value))
  if (!nzchar(value)) fail(label, " cannot be empty")
  if (!grepl("^[A-Za-z0-9][A-Za-z0-9_.-]*$", value)) {
    fail(label, " must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$: ", value)
  }
  value
}

resolve_existing_file <- function(value, base_dir, label) {
  value <- trimws(as.character(value))
  if (!nzchar(value)) fail(label, " cannot be empty")
  candidate <- if (grepl("^/", value)) value else file.path(base_dir, value)
  if (!file.exists(candidate)) fail(label, " does not exist: ", candidate)
  resolved <- normalizePath(candidate, mustWork = TRUE)
  info <- file.info(resolved)
  if (is.na(info$size) || info$size <= 0) fail(label, " is empty: ", resolved)
  resolved
}

validate_samplesheet <- function(path, min_normals) {
  if (!file.exists(path)) fail("Samplesheet not found: ", path)
  path <- normalizePath(path, mustWork = TRUE)
  sheet_dir <- dirname(path)
  samples <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("sample", "bam", "status")
  missing <- setdiff(required, names(samples))
  if (length(missing) > 0L) {
    fail("Samplesheet is missing column(s): ", paste(missing, collapse = ", "))
  }
  if (nrow(samples) == 0L) fail("Samplesheet has no data rows")

  samples <- samples[, required, drop = FALSE]
  samples$sample <- vapply(samples$sample, safe_component, character(1L), label = "sample ID")
  samples$status <- tolower(trimws(as.character(samples$status)))
  bad_status <- !samples$status %in% c("tumor", "normal")
  if (any(bad_status)) {
    fail(
      "Status must be tumor or normal; invalid row(s): ",
      paste(which(bad_status) + 1L, collapse = ", ")
    )
  }
  if (anyDuplicated(samples$sample)) {
    duplicate_ids <- unique(samples$sample[duplicated(samples$sample)])
    fail("Duplicate sample ID(s): ", paste(duplicate_ids, collapse = ", "))
  }

  samples$bam <- vapply(
    samples$bam,
    resolve_existing_file,
    character(1L),
    base_dir = sheet_dir,
    label = "BAM"
  )
  if (anyDuplicated(samples$bam)) {
    duplicate_bams <- unique(samples$bam[duplicated(samples$bam)])
    fail("A BAM cannot be assigned to multiple samples: ", paste(duplicate_bams, collapse = ", "))
  }

  missing_indexes <- vapply(samples$bam, function(bam) {
    bai_candidates <- c(paste0(bam, ".bai"), sub("\\.bam$", ".bai", bam, ignore.case = TRUE))
    !any(file.exists(bai_candidates) & file.info(bai_candidates)$size > 0)
  }, logical(1L))
  if (any(missing_indexes)) {
    fail(
      "Missing BAM index for sample(s): ",
      paste(samples$sample[missing_indexes], collapse = ", "),
      ". Expected <bam>.bai or a sibling .bai file."
    )
  }

  normal_count <- sum(samples$status == "normal")
  tumor_count <- sum(samples$status == "tumor")
  if (normal_count < min_normals) {
    fail("Only ", normal_count, " normal BAM(s) found; need at least ", min_normals)
  }
  if (tumor_count < 1L) fail("Samplesheet must contain at least one tumor")
  samples
}

write_tsv <- function(data, path) {
  utils::write.table(
    data,
    file = path,
    sep = "\t",
    quote = FALSE,
    row.names = FALSE,
    na = "NA"
  )
}

invalidate_completion_marker <- function(outdir) {
  dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
  if (!dir.exists(outdir)) fail("Could not create output directory: ", outdir)

  marker <- file.path(outdir, "qdnaseq_local_pon.done")
  if (dir.exists(marker)) {
    fail("Completion marker path is a directory; refusing to remove it: ", marker)
  }
  if (file.exists(marker)) {
    removed <- suppressWarnings(file.remove(marker))
    if (!isTRUE(removed) || file.exists(marker)) {
      fail("Could not invalidate previous completion marker: ", marker)
    }
    message("Invalidated previous qDNAseq local PoN completion marker: ", marker)
  }
  marker
}

publish_completion_marker <- function(marker) {
  marker_dir <- dirname(marker)
  temporary_marker <- tempfile(
    pattern = ".qdnaseq_local_pon.done.",
    tmpdir = marker_dir
  )
  on.exit(unlink(temporary_marker), add = TRUE)
  success_text <- "QDNASEQ_LOCAL_PON_SUCCESS"
  writeLines(success_text, temporary_marker, useBytes = TRUE)
  observed <- readLines(temporary_marker, warn = FALSE)
  if (!identical(observed, success_text)) {
    fail("Could not verify temporary completion marker: ", temporary_marker)
  }
  if (!file.rename(temporary_marker, marker) || !file.exists(marker)) {
    fail("Could not publish completion marker atomically: ", marker)
  }
  invisible(marker)
}

assert_nonempty_files <- function(paths, label) {
  missing <- paths[!file.exists(paths) | dir.exists(paths)]
  if (length(missing) > 0L) {
    fail(label, " missing file(s): ", paste(missing, collapse = ", "))
  }
  sizes <- file.info(paths)$size
  empty <- paths[is.na(sizes) | sizes <= 0]
  if (length(empty) > 0L) {
    fail(label, " empty file(s): ", paste(empty, collapse = ", "))
  }
  invisible(paths)
}

validate_exact_artifact_set <- function(directory, pattern, expected_names, label) {
  observed_names <- list.files(directory, pattern = pattern, full.names = FALSE)
  if (
    length(observed_names) != length(expected_names) ||
      !setequal(observed_names, expected_names)
  ) {
    fail(
      label, " artifact set mismatch; expected [",
      paste(sort(expected_names), collapse = ", "),
      "], observed [", paste(sort(observed_names), collapse = ", "), "]"
    )
  }
  assert_nonempty_files(file.path(directory, expected_names), label)
}

validate_required_outputs <- function(outdir, pon_name, tumor_ids, normal_ids) {
  required_files <- c(
    file.path(outdir, "all_segments.seg"),
    file.path(outdir, "all_calls.seg"),
    file.path(outdir, "all_tumors.qdnaseq_pon_corrected_bins.tsv"),
    file.path(outdir, "qdnaseq_local_pon_summary.tsv"),
    file.path(outdir, "qdnaseq_local_pon_versions.tsv"),
    file.path(outdir, "pon", paste0(pon_name, ".reference_bins.tsv")),
    file.path(outdir, "pon", "normal_panel_manifest.tsv"),
    file.path(outdir, "qc", "sample_qc.tsv"),
    file.path(outdir, "qc", "normal_panel_sample_qc.tsv"),
    file.path(outdir, "rds", paste0(pon_name, ".all_samples.readCounts.rds")),
    file.path(outdir, "rds", paste0(pon_name, ".all_samples.qdnaseq_corrected.rds")),
    file.path(outdir, "rds", paste0(pon_name, ".tumors.qdnaseq_pon_corrected.rds"))
  )
  assert_nonempty_files(required_files, "Required qDNAseq local PoN output")

  validate_exact_artifact_set(
    file.path(outdir, "bins"),
    "_markdup_bins[.]bed$",
    paste0(tumor_ids, "_markdup_bins.bed"),
    "Tumor bin"
  )
  validate_exact_artifact_set(
    file.path(outdir, "segments"),
    "_[.]seg$",
    paste0(tumor_ids, "_.seg"),
    "Tumor segment"
  )
  validate_exact_artifact_set(
    file.path(outdir, "segments"),
    "[.]calls[.]seg$",
    paste0(tumor_ids, ".calls.seg"),
    "Tumor call"
  )
  validate_exact_artifact_set(
    file.path(outdir, "plots"),
    "[.]qdnaseq_pon_corrected_segment_plot[.]pdf$",
    paste0(tumor_ids, ".qdnaseq_pon_corrected_segment_plot.pdf"),
    "Tumor plot"
  )
  validate_exact_artifact_set(
    file.path(outdir, "rds"),
    "[.]qdnaseq_pon_corrected[.]segmented[.]rds$",
    paste0(tumor_ids, ".qdnaseq_pon_corrected.segmented.rds"),
    "Tumor segmented RDS"
  )

  manifest_path <- file.path(outdir, "pon", "normal_panel_manifest.tsv")
  manifest <- utils::read.delim(
    manifest_path,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
  required_columns <- c("sample", "bam", "status")
  if (!all(required_columns %in% names(manifest))) {
    fail("Normal manifest lacks required columns: ", manifest_path)
  }
  manifest_ids <- as.character(manifest$sample)
  manifest_status <- tolower(trimws(as.character(manifest$status)))
  if (
    nrow(manifest) != length(normal_ids) ||
      anyDuplicated(manifest_ids) ||
      !setequal(manifest_ids, normal_ids) ||
      any(manifest_status != "normal")
  ) {
    fail(
      "Normal manifest does not contain exactly the expected ", length(normal_ids),
      " controls: ", paste(normal_ids, collapse = ",")
    )
  }
  invisible(TRUE)
}

combine_headered_files <- function(paths, destination) {
  if (length(paths) == 0L) fail("No files were provided for: ", destination)
  output <- character()
  for (index in seq_along(paths)) {
    if (!file.exists(paths[[index]])) fail("Expected export is missing: ", paths[[index]])
    lines <- readLines(paths[[index]], warn = FALSE)
    if (length(lines) == 0L) fail("Expected export is empty: ", paths[[index]])
    if (index > 1L) lines <- lines[-1L]
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
  raw <- utils::read.delim(
    raw_path,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
  required <- c("CHROMOSOME", "START", "STOP", "DATAPOINTS", "LOG2_RATIO_MEAN")
  missing <- setdiff(required, names(raw))
  if (length(missing) > 0L) {
    fail("Unexpected qDNAseq SEG columns; missing: ", paste(missing, collapse = ", "))
  }
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
    file = destination,
    sep = "\t",
    quote = TRUE,
    row.names = FALSE,
    col.names = TRUE,
    na = "NA"
  )
}

copy_feature_frame <- function(object) {
  data <- as.data.frame(Biobase::fData(object), stringsAsFactors = FALSE)
  preferred <- c(
    "chromosome", "start", "end", "use", "bases", "gc", "mappability",
    "blacklist", "residual"
  )
  data[, intersect(preferred, names(data)), drop = FALSE]
}

export_outputs <- function(
    copy_numbers_tumor,
    segmented,
    called,
    tumor_ids,
    tumor_log2_pon,
    pon_median,
    pon_mad,
    pon_n,
    outdir) {
  bins_dir <- file.path(outdir, "bins")
  segments_dir <- file.path(outdir, "segments")
  plots_dir <- file.path(outdir, "plots")
  rds_dir <- file.path(outdir, "rds")
  dir.create(bins_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(segments_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(plots_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(rds_dir, recursive = TRUE, showWarnings = FALSE)

  segment_files <- character(length(tumor_ids))
  call_files <- character(length(tumor_ids))

  for (index in seq_along(tumor_ids)) {
    sample <- tumor_ids[[index]]
    QDNAseq::exportBins(
      copy_numbers_tumor[, index],
      file = file.path(bins_dir, paste0(sample, "_markdup_bins.bed")),
      format = "bed",
      type = "copynumber",
      filter = TRUE,
      logTransform = TRUE
    )
    segment_files[[index]] <- file.path(segments_dir, paste0(sample, "_.seg"))
    export_samurai_seg(
      called[, index],
      destination = segment_files[[index]],
      sample = sample,
      type = "segments"
    )
    call_files[[index]] <- file.path(segments_dir, paste0(sample, ".calls.seg"))
    export_samurai_seg(
      called[, index],
      destination = call_files[[index]],
      sample = sample,
      type = "calls"
    )

    saveRDS(
      segmented[, index],
      file.path(rds_dir, paste0(sample, ".qdnaseq_pon_corrected.segmented.rds"))
    )
    grDevices::pdf(
      file.path(plots_dir, paste0(sample, ".qdnaseq_pon_corrected_segment_plot.pdf")),
      width = 14,
      height = 5
    )
    try(
      plot(segmented[, index], main = paste0(sample, " qDNAseq PoN-corrected")),
      silent = TRUE
    )
    grDevices::dev.off()
  }

  combine_headered_files(segment_files, file.path(outdir, "all_segments.seg"))
  combine_headered_files(call_files, file.path(outdir, "all_calls.seg"))

  wide <- copy_feature_frame(copy_numbers_tumor)
  wide$pon_median_log2 <- pon_median
  wide$pon_mad_log2 <- pon_mad
  wide$n_normals <- pon_n
  for (index in seq_along(tumor_ids)) {
    sample <- tumor_ids[[index]]
    wide[[paste0(sample, ".pon_log2")]] <- tumor_log2_pon[, index]
    mad_safe <- ifelse(is.finite(pon_mad) & pon_mad > 0, pon_mad, NA_real_)
    wide[[paste0(sample, ".pon_z")]] <- tumor_log2_pon[, index] / mad_safe
  }
  write_tsv(wide, file.path(outdir, "all_tumors.qdnaseq_pon_corrected_bins.tsv"))
}

run_analysis <- function(options) {
  if (is.null(options$samplesheet) || !nzchar(options$samplesheet)) {
    fail("--samplesheet is required")
  }
  if (is.null(options$outdir) || !nzchar(options$outdir)) fail("--outdir is required")
  if (is.na(options$binsize) || options$binsize < 1L) fail("--binsize must be a positive integer")
  if (is.na(options$min_mapq) || options$min_mapq < 0L) fail("--min-mapq must be a non-negative integer")
  if (is.na(options$min_normals) || options$min_normals < 2L) {
    fail("--min-normals must be an integer >= 2 for leave-one-out normal QC")
  }
  options$genome <- trimws(as.character(options$genome))
  if (!nzchar(options$genome)) fail("--genome cannot be empty")
  options$pon_name <- safe_component(options$pon_name, "PoN name")

  outdir <- normalizePath(options$outdir, mustWork = FALSE)
  completion_marker <- invalidate_completion_marker(outdir)

  suppressPackageStartupMessages({
    library(QDNAseq)
    library(Biobase)
  })

  samples <- validate_samplesheet(options$samplesheet, options$min_normals)
  pon_dir <- file.path(outdir, "pon")
  qc_dir <- file.path(outdir, "qc")
  rds_dir <- file.path(outdir, "rds")
  dir.create(pon_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(qc_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(rds_dir, recursive = TRUE, showWarnings = FALSE)

  if (nzchar(options$qdnaseq_bin_data)) {
    bin_data <- normalizePath(options$qdnaseq_bin_data, mustWork = TRUE)
    base::options("QDNAseq::binAnnotationPath" = bin_data)
  }
  if (tolower(options$genome) == "hg38") {
    if (!requireNamespace("QDNAseq.hg38", quietly = TRUE)) {
      fail("QDNAseq.hg38 is required for --genome hg38")
    }
  }

  message("qDNAseq local PoN")
  message("  samplesheet : ", normalizePath(options$samplesheet, mustWork = TRUE))
  message("  outdir     : ", outdir)
  message("  genome     : ", options$genome)
  message("  binsize    : ", options$binsize, " kbp")
  message("  min MAPQ   : ", options$min_mapq)
  message("  paired ends: ", tolower(as.character(options$paired_ends)))
  message("  normals    : ", paste(samples$sample[samples$status == "normal"], collapse = ","))
  message("  tumors     : ", paste(samples$sample[samples$status == "tumor"], collapse = ","))

  bin_annotations <- QDNAseq::getBinAnnotations(
    binSize = options$binsize,
    genome = options$genome
  )
  bamfiles <- samples$bam
  names(bamfiles) <- samples$sample
  read_counts <- QDNAseq::binReadCounts(
    bin_annotations,
    bamfiles = bamfiles,
    bamnames = samples$sample,
    minMapq = options$min_mapq,
    pairedEnds = options$paired_ends
  )
  saveRDS(
    read_counts,
    file.path(rds_dir, paste0(options$pon_name, ".all_samples.readCounts.rds"))
  )

  read_counts <- QDNAseq::applyFilters(
    read_counts,
    residual = TRUE,
    blacklist = TRUE,
    chromosomes = c("X", "Y", "MT")
  )
  chromosomes <- Biobase::fData(read_counts)$chromosome
  keep_autosomes <- autosome_mask(chromosomes)
  if (!any(keep_autosomes)) fail("No autosomal qDNAseq bins remain after filtering X/Y/MT")
  read_counts <- read_counts[keep_autosomes, ]

  read_counts <- QDNAseq::estimateCorrection(read_counts)
  copy_numbers <- QDNAseq::correctBins(read_counts)
  copy_numbers <- QDNAseq::normalizeBins(copy_numbers)
  copy_numbers <- QDNAseq::smoothOutlierBins(copy_numbers)
  saveRDS(
    copy_numbers,
    file.path(rds_dir, paste0(options$pon_name, ".all_samples.qdnaseq_corrected.rds"))
  )

  observed_names <- Biobase::sampleNames(copy_numbers)
  observed_order <- match(observed_names, samples$sample)
  if (anyNA(observed_order)) {
    fail(
      "Unexpected sample name(s) produced by qDNAseq: ",
      paste(observed_names[is.na(observed_order)], collapse = ", ")
    )
  }
  normal_ids <- samples$sample[samples$status == "normal"]
  tumor_ids <- samples$sample[samples$status == "tumor"]
  normal_index <- match(normal_ids, observed_names)
  tumor_index <- match(tumor_ids, observed_names)
  if (anyNA(normal_index)) {
    fail("Normal sample(s) disappeared during qDNAseq: ", paste(normal_ids[is.na(normal_index)], collapse = ", "))
  }
  if (anyNA(tumor_index)) {
    fail("Tumor sample(s) disappeared during qDNAseq: ", paste(tumor_ids[is.na(tumor_index)], collapse = ", "))
  }
  if (!"copynumber" %in% Biobase::assayDataElementNames(copy_numbers)) {
    fail(
      "qDNAseq object lacks the copynumber assay; found: ",
      paste(Biobase::assayDataElementNames(copy_numbers), collapse = ", ")
    )
  }

  copy_matrix <- Biobase::assayDataElement(copy_numbers, "copynumber")
  copy_matrix[!is.finite(copy_matrix) | copy_matrix <= 0] <- NA_real_
  log2_matrix <- log2(copy_matrix)
  correction <- pon_correct_log2(
    log2_matrix[, normal_index, drop = FALSE],
    log2_matrix[, tumor_index, drop = FALSE]
  )
  pon_median <- correction$reference
  pon_mad <- row_stat(log2_matrix[, normal_index, drop = FALSE], "mad")
  pon_n <- rowSums(is.finite(log2_matrix[, normal_index, drop = FALSE]))
  tumor_log2_pon <- correction$corrected
  if (!any(is.finite(tumor_log2_pon))) fail("PoN correction produced no finite tumor bins")

  copy_numbers_tumor <- copy_numbers[, tumor_index]
  corrected_ratio <- 2^tumor_log2_pon
  corrected_ratio[!is.finite(corrected_ratio)] <- NA_real_
  Biobase::assayDataElement(copy_numbers_tumor, "copynumber") <- corrected_ratio
  Biobase::sampleNames(copy_numbers_tumor) <- paste0(tumor_ids, "_markdup")
  saveRDS(
    copy_numbers_tumor,
    file.path(rds_dir, paste0(options$pon_name, ".tumors.qdnaseq_pon_corrected.rds"))
  )

  segmented <- QDNAseq::segmentBins(copy_numbers_tumor, transformFun = "log2")
  called <- QDNAseq::callBins(segmented, method = "cutoff")

  export_outputs(
    copy_numbers_tumor = copy_numbers_tumor,
    segmented = segmented,
    called = called,
    tumor_ids = tumor_ids,
    tumor_log2_pon = tumor_log2_pon,
    pon_median = pon_median,
    pon_mad = pon_mad,
    pon_n = pon_n,
    outdir = outdir
  )

  features <- copy_feature_frame(copy_numbers_tumor)
  reference <- features
  reference$pon_median_log2 <- pon_median
  reference$pon_mad_log2 <- pon_mad
  reference$n_normals_observed <- pon_n
  reference$n_normals_expected <- length(normal_ids)
  write_tsv(reference, file.path(pon_dir, paste0(options$pon_name, ".reference_bins.tsv")))

  normal_manifest <- samples[samples$status == "normal", c("sample", "bam", "status"), drop = FALSE]
  write_tsv(normal_manifest, file.path(pon_dir, "normal_panel_manifest.tsv"))

  sample_qc <- data.frame(
    sample = observed_names,
    status = samples$status[observed_order],
    bam = samples$bam[observed_order],
    n_finite_autosomal_bins = colSums(is.finite(log2_matrix)),
    median_log2_before_pon = apply(log2_matrix, 2L, function(x) stats::median(x, na.rm = TRUE)),
    mad_log2_before_pon = apply(log2_matrix, 2L, function(x) stats::mad(x, na.rm = TRUE)),
    stringsAsFactors = FALSE
  )
  write_tsv(sample_qc, file.path(qc_dir, "sample_qc.tsv"))

  normal_log2 <- log2_matrix[, normal_index, drop = FALSE]
  normal_qc <- compute_loo_normal_qc(normal_log2, normal_ids)
  write_tsv(normal_qc, file.path(qc_dir, "normal_panel_sample_qc.tsv"))

  summary <- data.frame(
    pon_applied = "true",
    pon_name = options$pon_name,
    pon_method = "median_normal_log2",
    normal_qc_method = "leave_one_out_median_normal_log2",
    segmentation_transform = "log2",
    genome = options$genome,
    binsize_kbp = options$binsize,
    min_mapq = options$min_mapq,
    paired_ends = tolower(as.character(options$paired_ends)),
    qdnaseq_filter_residual = "true",
    qdnaseq_filter_blacklist = "true",
    exported_chromosomes = "autosomes_1_22",
    excluded_chromosomes = "X;Y;MT;non_autosomal",
    tumor_only_exports = "true",
    exported_status = "tumor",
    n_normals = length(normal_ids),
    normals = paste(normal_ids, collapse = ";"),
    n_tumors = length(tumor_ids),
    tumors = paste(tumor_ids, collapse = ";"),
    n_autosomal_bins = nrow(copy_numbers_tumor),
    generated_at_utc = format(Sys.time(), tz = "UTC", usetz = TRUE),
    stringsAsFactors = FALSE
  )
  write_tsv(summary, file.path(outdir, "qdnaseq_local_pon_summary.tsv"))

  versions <- data.frame(
    component = c("R", "QDNAseq"),
    version = c(as.character(getRversion()), as.character(utils::packageVersion("QDNAseq"))),
    stringsAsFactors = FALSE
  )
  write_tsv(versions, file.path(outdir, "qdnaseq_local_pon_versions.tsv"))
  validate_required_outputs(outdir, options$pon_name, tumor_ids, normal_ids)
  publish_completion_marker(completion_marker)
  message("qDNAseq local PoN completed: ", outdir)
}

main <- function() {
  options <- parse_cli(commandArgs(trailingOnly = TRUE))
  if (options$help) {
    usage()
    return(invisible(NULL))
  }
  if (options$self_test) {
    run_self_test()
    return(invisible(NULL))
  }
  run_analysis(options)
}

if (sys.nframe() == 0L) {
  tryCatch(
    main(),
    error = function(error) {
      message("ERROR: ", conditionMessage(error))
      quit(save = "no", status = 1L, runLast = FALSE)
    }
  )
}
