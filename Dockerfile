# Pin the tested ONT callers and their original environment/model paths.
# No Docker socket, privileged execution, or nested Singularity is needed.
FROM hkubal/clairs-to:v0.4.4@sha256:4587ee6307575f68eec686f8cce23919a59b20b8f97c6d84d0084b1ef46a8c01 AS ont_callers

# Keep TensorFlow CUDA 12.2 separate from ClairS-TO's CUDA 12.4 PyTorch.
FROM ont_callers AS clair3_gpu
RUN /opt/micromamba/envs/clairs-to/bin/python -m venv --system-site-packages /opt/oncotracer-gpu/clair3 \
    && /opt/oncotracer-gpu/clair3/bin/python -m pip install --no-cache-dir 'tensorflow[and-cuda]==2.15.1'

# CNA uses the native controller; complete short variants use bundled Nextflow.
FROM condaforge/miniforge3:24.11.3-0

ARG SOURCE_COMMIT
ARG SOURCE_SHA256

LABEL org.opencontainers.image.title="OncoTracer" \
      org.opencontainers.image.version="2.2.0" \
      org.opencontainers.image.description="Native LP-WGS CNA and small-variant analysis" \
      org.opencontainers.image.source="https://github.com/cfarkas/oncotracer" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.source.sha256="${SOURCE_SHA256}" \
      org.opencontainers.image.licenses="MIT"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    ONCOTRACER_HOME=/opt/oncotracer \
    ONCOTRACER_CORE_PREFIX=/opt/conda \
    ONCOTRACER_QDNASEQ_PREFIX=/opt/oncotracer-envs/qdnaseq \
    ONCOTRACER_ICHORCNA_PREFIX=/opt/oncotracer-envs/ichorcna \
    ONCOTRACER_CLASSIFIER_PREFIX=/opt/oncotracer-envs/classifier \
    ONCOTRACER_GISTIC_PREFIX=/opt/oncotracer-envs/gistic \
    ONCOTRACER_VARIANTS_PREFIX=/opt/oncotracer-envs/variants \
    ONCOTRACER_STRELKA_PREFIX=/opt/oncotracer-envs/strelka2 \
    ONCOTRACER_FFPERASE_PREFIX=/opt/oncotracer-envs/ffperase \
    NXF_HOME=/tmp/nextflow \
    NXF_DIST=/opt/nextflow/framework \
    NXF_PLUGINS_DIR=/tmp/nextflow-plugins \
    NXF_VER=26.04.2 \
    NXF_OFFLINE=true \
    NXF_DISABLE_CHECK_LATEST=true \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/conda/bin:/usr/local/bin:$PATH

# Miniforge already provides the bootstrap shell, certificates, Git, curl,
# wget, tar, and gzip. Keeping the image Conda-only avoids dependence on an
# independently changing Ubuntu package mirror during reproducible builds.
WORKDIR ${ONCOTRACER_HOME}
COPY environments/ ${ONCOTRACER_HOME}/environments/

# Core tools live in base so the direct BAM-refinement helper can use the
# read-only-container fallback without creating another environment.
RUN conda config --system --set channel_priority strict \
    && conda env update --prefix /opt/conda --file environments/native-core.yml \
    && conda clean -afy

# Keep the pinned launcher and framework readable for arbitrary container UIDs.
# Runtime checks and workflow parsing work offline, without nested containers.
RUN mkdir -p /opt/nextflow/framework/26.04.2 \
    && curl -fsSL --retry 3 https://www.nextflow.io/releases/v26.04.2/nextflow -o /usr/local/bin/nextflow \
    && curl -fsSL --retry 3 https://www.nextflow.io/releases/v26.04.2/nextflow-26.04.2-one.jar -o /opt/nextflow/framework/26.04.2/nextflow-26.04.2-one.jar \
    && chmod 0755 /usr/local/bin/nextflow \
    && chmod -R a+rX /opt/nextflow \
    && NXF_HOME=/tmp/nextflow-build nextflow -version

COPY scripts/bioconductor_mirror.sh /opt/build-support/bioconductor_mirror.sh

# Cache completed environments separately; legacy annotation checksum checks stay.
RUN BASH_ENV=/opt/build-support/bioconductor_mirror.sh conda env create --prefix "${ONCOTRACER_QDNASEQ_PREFIX}" --file environments/native-qdnaseq.yml \
    && conda clean -afy
RUN BASH_ENV=/opt/build-support/bioconductor_mirror.sh conda env create --prefix "${ONCOTRACER_ICHORCNA_PREFIX}" --file environments/native-ichorcna.yml \
    && conda clean -afy
RUN conda env create --prefix "${ONCOTRACER_CLASSIFIER_PREFIX}" --file environments/native-classifier.yml \
    && conda clean -afy
RUN conda env create --prefix "${ONCOTRACER_GISTIC_PREFIX}" --file environments/native-gistic2.yml \
    && conda clean -afy
RUN conda env create --prefix "${ONCOTRACER_VARIANTS_PREFIX}" --file environments/native-variants.yml \
    && conda clean -afy
RUN conda env create --prefix "${ONCOTRACER_FFPERASE_PREFIX}" --file environments/native-ffperase.yml \
    && conda clean -afy

RUN conda env create --prefix "${ONCOTRACER_STRELKA_PREFIX}" --file environments/native-strelka2.yml \
    && conda clean -afy

# Preserve upstream absolute prefixes and licenses. The isolated wrappers keep
# ONT Python/TensorFlow/PyTorch libraries out of the core and short-read envs.
COPY --from=ont_callers /opt/bin/ /opt/bin/
COPY --from=ont_callers /opt/micromamba/ /opt/micromamba/
COPY --from=clair3_gpu /opt/oncotracer-gpu/clair3/ /opt/oncotracer-gpu/clair3/
COPY oncotracer_cli/gpu_callers.py /opt/build-support/gpu_callers.py
RUN /opt/conda/bin/python /opt/build-support/gpu_callers.py \
    --clair3-bin /opt/micromamba/envs/clairs-to/bin --clairsto-bin /opt/bin
COPY scripts/docker_run_clairs_to.sh /usr/local/bin/run_clairs_to
COPY scripts/docker_run_clair3.sh /usr/local/bin/run_clair3.sh
RUN chmod 0755 /usr/local/bin/run_clairs_to /usr/local/bin/run_clair3.sh \
    && /usr/local/bin/run_clairs_to --help >/dev/null \
    && /usr/local/bin/run_clair3.sh --help >/dev/null

# A bounded CUDA report runtime also serves Illumina projects. The normal
# classifier environment remains available for CPU analyses.
RUN conda env create --prefix /opt/oncotracer-envs/gpu-reports --file environments/native-gpu-reports.yml \
    && conda clean -afy
ENV ONCOTRACER_GPU_REPORTS_PREFIX=/opt/oncotracer-envs/gpu-reports

ENV ONCOTRACER_CONTAINER_RUNTIME=docker

# Authenticate the bundled launcher and JAR before publishing the image. Runtime
# preparation copies this read-only distribution into a verified generation.
RUN echo 'a73e0d796932b9455cbef207dda4937b98b06b687b049acda46c152d1d25f2c9  /usr/local/bin/nextflow' | sha256sum -c - \
    && echo 'a0fc1d8cadedd6c8aae0532da04da06a3b642d910bc9a15718ba263ac9013a2b  /opt/nextflow/framework/26.04.2/nextflow-26.04.2-one.jar' | sha256sum -c -

COPY . ${ONCOTRACER_HOME}/

RUN if [[ -z "${SOURCE_COMMIT}" && -z "${SOURCE_SHA256}" ]]; then \
        python scripts/build_native_binary.py \
          --root "${ONCOTRACER_HOME}" --output /usr/local/bin/oncotracer \
          --allow-unbound-development; \
    elif [[ -n "${SOURCE_COMMIT}" && -n "${SOURCE_SHA256}" ]]; then \
        python scripts/build_native_binary.py \
          --root "${ONCOTRACER_HOME}" --output /usr/local/bin/oncotracer \
          --source-commit "${SOURCE_COMMIT}" --source-sha256 "${SOURCE_SHA256}"; \
    else \
        echo "SOURCE_COMMIT and SOURCE_SHA256 must be supplied together" >&2; \
        exit 2; \
    fi \
    && chmod 0755 /usr/local/bin/oncotracer \
    && oncotracer --version \
    && test -s "${ONCOTRACER_HOME}/oncotracer_cli/lpwgs/main.nf" \
    && test -s "${ONCOTRACER_HOME}/oncotracer_cli/lpwgs/bin/bcftools_compat.py" \
    && NXF_HOME=/tmp/nextflow-build nextflow -version

ENTRYPOINT ["oncotracer"]
CMD ["--help"]
