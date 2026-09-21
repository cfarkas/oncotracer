#!/usr/bin/env bash
# Run the pinned upstream caller with its own libraries and bundled models.
set -euo pipefail
export CONDA_PREFIX=/opt/micromamba/envs/clairs-to
export PATH="${CONDA_PREFIX}/bin:/opt/bin:/usr/bin:/bin"
export PYTHONNOUSERSITE=1
export CUDA_VISIBLE_DEVICES=""
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
exec "${CONDA_PREFIX}/bin/python" /opt/bin/run_clairs_to --conda_prefix "$CONDA_PREFIX" "$@"
