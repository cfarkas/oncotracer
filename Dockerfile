# OncoTracer v2 native runtime: no Java and no Nextflow.
FROM condaforge/miniforge3:24.11.3-0

LABEL org.opencontainers.image.title="OncoTracer" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.description="Native LP-WGS CNA analysis without Nextflow" \
      org.opencontainers.image.source="https://github.com/cfarkas/oncotracer" \
      org.opencontainers.image.licenses="MIT"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    ONCOTRACER_HOME=/opt/oncotracer \
    ONCOTRACER_CORE_PREFIX=/opt/conda \
    ONCOTRACER_QDNASEQ_PREFIX=/opt/oncotracer-envs/qdnaseq \
    ONCOTRACER_ICHORCNA_PREFIX=/opt/oncotracer-envs/ichorcna \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/conda/bin:/usr/local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git gzip less procps rsync tar unzip wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${ONCOTRACER_HOME}
COPY . ${ONCOTRACER_HOME}/

# Core tools live in base so the existing direct BAM-refinement helper can use
# the read-only-container fallback without creating another environment.
RUN conda env update --prefix /opt/conda --file environments/native-core.yml --prune \
    && conda env create --prefix "${ONCOTRACER_QDNASEQ_PREFIX}" --file environments/native-qdnaseq.yml \
    && conda env create --prefix "${ONCOTRACER_ICHORCNA_PREFIX}" --file environments/native-ichorcna.yml \
    && conda clean -afy

RUN python scripts/build_native_binary.py --root "${ONCOTRACER_HOME}" --output /usr/local/bin/oncotracer \
    && chmod 0755 /usr/local/bin/oncotracer \
    && find "${ONCOTRACER_HOME}" -type f \( -name '*.nf' -o -name 'nextflow.config' \) -delete \
    && oncotracer --version \
    && ! command -v nextflow

ENTRYPOINT ["oncotracer"]
CMD ["--help"]
