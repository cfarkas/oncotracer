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
    return(c(
      schema = "oncotracer-ichorcna-plot-compat-v1",
      status = "upstream-safe",
      package_version = as.character(utils::packageVersion("ichorCNA")),
      target_quantile_calls = "2"
    ))
  }
  if (any(safe)) {
    stop(
      "Unexpected partially patched ichorCNA::plotCorrectionGenomeWide function",
      call. = FALSE
    )
  }

  patched <- original
  body(patched) <- oncotracer_ichorcna_rewrite_copy_quantiles(body(original))
  environment(patched) <- environment(original)
  utils::assignInNamespace(function_name, patched, ns = "ichorCNA")

  verified <- get(function_name, envir = asNamespace("ichorCNA"), inherits = FALSE)
  verified_calls <- oncotracer_ichorcna_collect_copy_quantiles(body(verified))
  verified_safe <- vapply(
    verified_calls,
    function(call) !is.null(call[["na.rm"]]) && identical(call[["na.rm"]], TRUE),
    logical(1L)
  )
  if (length(verified_calls) != 2L || !all(verified_safe)) {
    stop("Failed to verify ichorCNA plotting compatibility patch", call. = FALSE)
  }

  c(
    schema = "oncotracer-ichorcna-plot-compat-v1",
    status = "patched",
    package_version = as.character(utils::packageVersion("ichorCNA")),
    target_quantile_calls = "2"
  )
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
