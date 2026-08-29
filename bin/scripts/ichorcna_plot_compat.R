# Runtime compatibility shim for ichorCNA 0.5.1 plotting.
#
# Some low-pass samples contain NA read-count bins. The upstream
# plotCorrectionGenomeWide() implementation computes quantile(copy, ...)
# without na.rm=TRUE, after the analytical outputs have already been written.
# This shim rewrites only those two calls in the loaded namespace. It refuses
# to patch an unexpected function shape and reports whether the package was
# patched or already safe.

oncotracer_ichorcna_is_copy_quantile <- function(node) {
  is.call(node) &&
    length(node) >= 3L &&
    identical(node[[1L]], as.name("quantile")) &&
    identical(node[[2L]], as.name("copy"))
}

oncotracer_ichorcna_collect_copy_quantiles <- function(node) {
  found <- list()
  walk <- function(current) {
    if (is.call(current)) {
      if (oncotracer_ichorcna_is_copy_quantile(current)) {
        found[[length(found) + 1L]] <<- current
      }
      for (part in as.list(current)) walk(part)
    } else if (is.expression(current) || is.pairlist(current)) {
      for (part in as.list(current)) walk(part)
    }
    invisible(NULL)
  }
  walk(node)
  found
}

oncotracer_ichorcna_rewrite_copy_quantiles <- function(node) {
  if (is.call(node)) {
    original_parts <- as.list(node)
    rewritten_parts <- lapply(original_parts, oncotracer_ichorcna_rewrite_copy_quantiles)
    names(rewritten_parts) <- names(original_parts)
    node <- as.call(rewritten_parts)
    if (oncotracer_ichorcna_is_copy_quantile(node)) {
      node[["na.rm"]] <- TRUE
    }
  } else if (is.expression(node)) {
    node <- as.expression(lapply(as.list(node), oncotracer_ichorcna_rewrite_copy_quantiles))
  } else if (is.pairlist(node)) {
    original_parts <- as.list(node)
    rewritten_parts <- lapply(original_parts, oncotracer_ichorcna_rewrite_copy_quantiles)
    names(rewritten_parts) <- names(original_parts)
    node <- as.pairlist(rewritten_parts)
  }
  node
}

oncotracer_patch_ichorcna_plot_correction <- function() {
  namespace <- asNamespace("ichorCNA")
  function_name <- "plotCorrectionGenomeWide"
  original <- get(function_name, envir = namespace, inherits = FALSE)
  metadata <- c(
    schema = "oncotracer-ichorcna-plot-compat-v1",
    status = "patched",
    package_version = as.character(utils::packageVersion("ichorCNA")),
    target_quantile_calls = "2",
    zero_median_plot_guard = "placeholder"
  )

  if (isTRUE(attr(original, "oncotracer_ichorcna_plot_compat_v1"))) {
    return(metadata)
  }

  calls <- oncotracer_ichorcna_collect_copy_quantiles(body(original))

  if (length(calls) != 2L) {
    stop(
      "Unexpected ichorCNA::plotCorrectionGenomeWide structure: expected exactly ",
      "two quantile(copy, ...) calls, found ", length(calls),
      call. = FALSE
    )
  }

  safe <- vapply(
    calls,
    function(call) !is.null(call[["na.rm"]]) && identical(call[["na.rm"]], TRUE),
    logical(1L)
  )
  if (all(safe)) {
    patched <- original
  } else if (any(safe)) {
    stop(
      "Unexpected partially patched ichorCNA::plotCorrectionGenomeWide function",
      call. = FALSE
    )
  } else {
    patched <- original
    body(patched) <- oncotracer_ichorcna_rewrite_copy_quantiles(body(original))
    environment(patched) <- environment(original)
  }

  verified_calls <- oncotracer_ichorcna_collect_copy_quantiles(body(patched))
  verified_safe <- vapply(
    verified_calls,
    function(call) !is.null(call[["na.rm"]]) && identical(call[["na.rm"]], TRUE),
    logical(1L)
  )
  if (length(verified_calls) != 2L || !all(verified_safe)) {
    stop("Failed to verify ichorCNA plotting compatibility patch", call. = FALSE)
  }

  guarded <- local({
    delegate <- patched
    function(correctOutput, seqinfo = NULL, chr = NULL, ...) {
      candidate <- correctOutput
      if (!is.null(chr)) {
        candidate <- candidate[
          as.character(GenomeInfoDb::seqnames(candidate)) == as.character(chr)
        ]
      }
      reads <- suppressWarnings(as.numeric(candidate$reads))
      valid <- !is.na(candidate$valid) & as.logical(candidate$valid)
      median_reads <- if (length(reads)) {
        stats::median(reads, na.rm = TRUE)
      } else {
        NA_real_
      }
      reason <- NULL
      if (!length(reads)) {
        reason <- "No read-count bins are available."
      } else if (!is.finite(median_reads)) {
        reason <- "The median raw read count is not finite."
      } else if (median_reads == 0) {
        reason <- "The median raw read count is zero."
      } else {
        copy <- reads / median_reads
        if (any(is.infinite(copy))) {
          reason <- "The uncorrected copy ratio contains infinite values."
        } else {
          limits <- stats::quantile(
            copy,
            c(0.01, 0.99),
            na.rm = TRUE,
            names = FALSE
          )
          selected <- valid & is.finite(copy) &
            copy >= limits[[1L]] & copy <= limits[[2L]]
          if (length(limits) != 2L || !all(is.finite(limits))) {
            reason <- "Finite correction-plot limits are unavailable."
          } else if (!any(selected)) {
            reason <- "No finite valid bins remain for the correction plot."
          }
        }
      }

      if (!is.null(reason)) {
        label <- if (is.null(chr)) "genome-wide" else paste("chromosome", chr)
        graphics::plot.new()
        graphics::title(main = paste("Correction diagnostic unavailable:", label))
        graphics::text(0.5, 0.56, reason)
        graphics::text(0.5, 0.44, "No CNA values were changed.", cex = 0.85)
        return(invisible(NULL))
      }

      delegate(correctOutput = correctOutput, seqinfo = seqinfo, chr = chr, ...)
    }
  })
  attr(guarded, "oncotracer_ichorcna_plot_compat_v1") <- TRUE
  utils::assignInNamespace(function_name, guarded, ns = "ichorCNA")

  verified <- get(function_name, envir = namespace, inherits = FALSE)
  if (!isTRUE(attr(verified, "oncotracer_ichorcna_plot_compat_v1"))) {
    stop("Failed to verify ichorCNA zero-median plotting guard", call. = FALSE)
  }

  metadata
}

oncotracer_write_ichorcna_plot_compat <- function(metadata, path) {
  if (is.null(names(metadata)) || any(!nzchar(names(metadata)))) {
    stop("Compatibility metadata must be named", call. = FALSE)
  }
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.table(
    data.frame(
      key = names(metadata),
      value = unname(as.character(metadata)),
      stringsAsFactors = FALSE
    ),
    file = path,
    sep = "\t",
    quote = FALSE,
    row.names = FALSE
  )
  invisible(normalizePath(path, mustWork = TRUE))
}
