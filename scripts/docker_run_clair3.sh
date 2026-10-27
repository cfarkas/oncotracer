#!/usr/bin/env bash
# Clair3 1.2.0 is included in the pinned ClairS-TO runtime. Models stay explicit.
set -euo pipefail
export CONDA_PREFIX=/opt/micromamba/envs/clairs-to
export PATH="${CONDA_PREFIX}/bin:/opt/bin:/usr/bin:/bin"
export PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
if [[ "${ONCOTRACER_GPU_ENABLED:-0}" == 1 ]]; then
    export PATH="/opt/oncotracer-gpu/clair3/bin:${PATH}"
    # NVIDIA driver libraries still come from the host container runtime.
    export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64"
    for library in /opt/oncotracer-gpu/clair3/lib/python*/site-packages/nvidia/*/lib; do
        export LD_LIBRARY_PATH="${library}:${LD_LIBRARY_PATH}"
    done
else
    export CUDA_VISIBLE_DEVICES=""
fi
exec "${CONDA_PREFIX}/bin/run_clair3.sh" "$@"
