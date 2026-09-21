#!/usr/bin/env bash
# Clair3 1.2.0 is included in the pinned ClairS-TO runtime. Models stay explicit.
set -euo pipefail
export CONDA_PREFIX=/opt/micromamba/envs/clairs-to
export PATH="${CONDA_PREFIX}/bin:/opt/bin:/usr/bin:/bin"
export PYTHONNOUSERSITE=1
export CUDA_VISIBLE_DEVICES=""
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
exec "${CONDA_PREFIX}/bin/run_clair3.sh" "$@"
