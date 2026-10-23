# Run with base R for the gate; with QDNAseq installed also exercise real CBS.
source("bin/scripts/qdnaseq_post_normalization_qc.R")
x <- qdnaseq_classify_normalized(c("sparse_control", "neutral", "focal_loss", "empty", "zero_median", "patchy"),
  list(rep(1,1000), rep(1,1000), c(rep(1,900),rep(.25,100)), rep(0,1000),rep(0,1000),rep(1,1000)),
  list(rep(c(0,1,1),length.out=1000),rep(40,1000),c(rep(40,900),rep(10,100)),rep(0,1000),rep(40,1000),c(rep(40,900),rep(0,100))))
stopifnot(identical(qdnaseq_valid_indices(x), c(2L,3L)),
  x[[1]]$assessment == "not_assessable", x[[2]]$assessment == "assessable", x[[3]]$assessment == "assessable",
  x[[1]]$error == "low_coverage_at_selected_bin_size", x[[6]]$error == "excess_empty_bins_at_selected_bin_size")
f <- tempfile(fileext=".json"); invisible(write_qdnaseq_sample_status(f,x)); stopifnot(grepl('"not_assessable"',readLines(f))); unlink(f)
cat("CNA_COVERAGE_GATE_OK\n")
if (requireNamespace("QDNAseq", quietly=TRUE)) {
  suppressPackageStartupMessages(library(QDNAseq)); suppressPackageStartupMessages(library(Biobase))
  # Use real S4 structures and segmentation, with deterministic synthetic data.
  data(LGG150, package="QDNAseq")
  x <- LGG150[seq_len(2000),1]
  f <- fData(x); f$use <- TRUE; f$chromosome <- rep("1",nrow(f)); f$start <- (seq_len(nrow(f))-1L)*15000+1L; f$end<-seq_len(nrow(f))*15000
  fData(x) <- f
  original <- x
  values <- c(rep(1,800),rep(.25,400),rep(1,800)); values[c(20,45,1800)] <- 0
  x <- new("QDNAseqCopyNumbers", bins=featureData(original), copynumber=matrix(values,ncol=1,dimnames=dimnames(original)),
           phenodata=phenoData(original), experimentData=experimentData(original))
  x <- smoothOutlierBins(x,logTransform=FALSE)
  set.seed(1L); y<-segmentBins(x,transformFun="sqrt")
  segments<-assayDataElement(y,"segmented")
  stopifnot(all(is.finite(segments)), median(segments[1:700,])>.8,median(segments[950:1100,])<.5)
  z <- callBins(y,method="cutoff")
  calls<-assayDataElement(z,"calls")
  stopifnot(median(calls[1:700,]) == 0, median(calls[950:1100,]) < 0)
  # Test actual export functions, including empty bins and distinct segments.
  code<-readLines("bin/scripts/native_qdnaseq.R")
  first<-grep('^parse_bool <-',code)[1]; last<-grep('^options <- parse_args',code)[1]-1
  fail<-function(...) stop(paste0(...),call.=FALSE)
  eval(parse(text=code[first:last]))
  output<-tempfile(fileext=".seg"); export_samurai_seg(z,output,"neutral_with_focal_loss")
  seg<-read.delim(output); stopifnot(all(is.finite(seg$seg.mean)),min(seg$seg.mean)> -10, any(seg$seg.mean < -1))
  unlink(output)
  stopifnot(is.na(log2_adhoc(0)), log2_adhoc(.25)== -2)
  cat("CNA_ZERO_SAFE_SEGMENTATION_OK\n")
}
