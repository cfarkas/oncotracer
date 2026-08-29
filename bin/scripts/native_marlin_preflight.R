#!/usr/bin/env Rscript

# Fail closed before expensive POD5 processing. This preflight never installs,
# downloads, or discovers a Python environment; the exact executable is a
# required OncoTracer input.

fail <- function(...) stop(paste0(...), call. = FALSE)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 2L || arguments[[1L]] != "--python") {
  fail("Usage: native_marlin_preflight.R --python /absolute/path/to/python")
}
python <- normalizePath(arguments[[2L]], mustWork = TRUE)
configured <- Sys.getenv("RETICULATE_PYTHON", unset = "")
if (!nzchar(configured) || normalizePath(configured, mustWork = TRUE) != python) {
  fail("RETICULATE_PYTHON does not match --python")
}
if (!identical(Sys.getenv("RETICULATE_USE_MANAGED_VENV", unset = ""), "no")) {
  fail("RETICULATE_USE_MANAGED_VENV must be no")
}

required_r <- c("data.table", "keras", "openxlsx", "reticulate")
missing_r <- required_r[!vapply(required_r, requireNamespace, logical(1L), quietly = TRUE)]
if (length(missing_r)) fail("Missing MARLIN R packages: ", paste(missing_r, collapse = ", "))

suppressPackageStartupMessages(library(reticulate))
configuration <- py_config()
if (normalizePath(configuration$python, mustWork = TRUE) != python) {
  fail("reticulate selected a different Python: ", configuration$python)
}
required_python <- c("h5py", "numpy", "tensorflow")
missing_python <- required_python[
  !vapply(required_python, py_module_available, logical(1L))
]
if (length(missing_python)) {
  fail("Missing MARLIN Python modules: ", paste(missing_python, collapse = ", "))
}

cat("MARLIN_RUNTIME_OK\n")
