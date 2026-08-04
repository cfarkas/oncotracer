#!/usr/bin/env Rscript

# Native ichorCNA stage for OncoTracer v2.0.0.
# Arguments and defaults mirror SAMURAI v1.4.0.

fail <- function(...) stop(paste0(...), call. = FALSE)

parse_args <- function(argv) {
  options <- list(
    wig = NULL,
    sample = NULL,
    outdir = NULL,
    gc_wig = NULL,
    map_wig = NULL,
    centromere = "",
    reptime = "",
    normal_panel = "",
    cores = 2L
  )
  keys <- c(
    "--wig" = "wig",
    "--sample" = "sample",
    "--outdir" = "outdir",
    "--gc-wig" = "gc_wig",
    "--map-wig" = "map_wig",
    "--centromere" = "centromere",
    "--reptime" = "reptime",
    "--normal-panel" = "normal_panel",
    "--cores" = "cores"
  )
  i <- 1L
  while (i <= length(argv)) {
    flag <- argv[[i]]
    if (flag %in% c("-h", "--help")) {
      cat("native_ichorcna.R --wig FILE --sample ID --outdir DIR --gc-wig FILE --map-wig FILE\n")
      quit(save = "no", status = 0L)
    }
    if (!flag %in% names(keys)) fail("Unknown option: ", flag)
    if (i == length(argv)) fail("Missing value for ", flag)
    options[[unname(keys[[flag]])]] <- argv[[i + 1L]]
    i <- i + 2L
  }
  options$cores <- suppressWarnings(as.integer(options$cores))
  options
}

required_file <- function(value, label) {
  if (is.null(value) || !nzchar(value) || !file.exists(value) || file.info(value)$size <= 0) {
    fail(label, " is missing or empty: ", value)
  }
  normalizePath(value, mustWork = TRUE)
}

run_analysis <- function(options) {
  options$wig <- required_file(options$wig, "Tumor WIG")
  options$gc_wig <- required_file(options$gc_wig, "GC WIG")
  options$map_wig <- required_file(options$map_wig, "Mappability WIG")
  if (!is.null(options$centromere) && nzchar(options$centromere)) {
    options$centromere <- required_file(options$centromere, "Centromere file")
  }
  if (!is.null(options$reptime) && nzchar(options$reptime)) {
    options$reptime <- required_file(options$reptime, "Replication timing WIG")
  }
  if (!is.null(options$normal_panel) && nzchar(options$normal_panel)) {
    options$normal_panel <- required_file(options$normal_panel, "Normal panel")
  }
  if (is.null(options$sample) || !grepl("^[A-Za-z0-9][A-Za-z0-9_.-]*$", options$sample)) {
    fail("Invalid --sample: ", options$sample)
  }
  if (is.null(options$outdir) || !nzchar(options$outdir)) fail("--outdir is required")
  if (is.na(options$cores) || options$cores < 1L) fail("--cores must be positive")

  dir.create(options$outdir, recursive = TRUE, showWarnings = FALSE)
  options$outdir <- normalizePath(options$outdir, mustWork = TRUE)
  old <- setwd(options$outdir)
  on.exit(setwd(old), add = TRUE)

  suppressPackageStartupMessages(library(ichorCNA))

  run_ichorCNA(
    tumor_wig = options$wig,
    id = options$sample,
    cores = options$cores,
    gcWig = options$gc_wig,
    normal_wig = NULL,
    normal_panel = if (nzchar(options$normal_panel)) options$normal_panel else NULL,
    mapWig = options$map_wig,
    centromere = if (nzchar(options$centromere)) options$centromere else NULL,
    repTimeWig = if (nzchar(options$reptime)) options$reptime else NULL,
    maxCN = 5,
    chrTrain = paste0("chr", 1:22),
    chrs = paste0("chr", 1:22),
    chrNormalize = paste0("chr", 1:22),
    txnE = 0.9999,
    txnStrength = 10000,
    minMapScore = 0.75,
    fracReadsInChrYForMale = 0.001,
    minSegmentBins = 50,
    maxFracCNASubclone = 0.5,
    includeHOMD = FALSE,
    altFracThreshold = 0.05,
    genomeStyle = "UCSC",
    plotFileType = "pdf",
    plotYLim = c(-2, 4),
    normal = c(0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99),
    genomeBuild = "hg38",
    estimateNormal = TRUE,
    ploidy = c(2, 3, 4, 5),
    estimatePloidy = TRUE,
    estimateScPrevalence = FALSE,
    scStates = c(),
    outDir = "."
  )

  required <- c(
    paste0(options$sample, ".RData"),
    paste0(options$sample, ".seg.txt"),
    paste0(options$sample, ".cna.seg"),
    paste0(options$sample, ".correctedDepth.txt"),
    paste0(options$sample, ".params.txt")
  )
  bad <- required[!file.exists(required) | file.info(required)$size <= 0]
  if (length(bad)) fail("Missing ichorCNA output(s): ", paste(bad, collapse = ", "))

  versions <- data.frame(
    component = c("R", "ichorCNA"),
    version = c(as.character(getRversion()), as.character(utils::packageVersion("ichorCNA"))),
    stringsAsFactors = FALSE
  )
  utils::write.table(
    versions,
    paste0(options$sample, ".ichorcna_native_versions.tsv"),
    sep = "\t",
    quote = FALSE,
    row.names = FALSE
  )
  cat("Native ichorCNA completed: ", options$outdir, "\n", sep = "")
}

options <- parse_args(commandArgs(trailingOnly = TRUE))
tryCatch(
  run_analysis(options),
  error = function(error) {
    message("ERROR: ", conditionMessage(error))
    quit(save = "no", status = 1L, runLast = FALSE)
  }
)
