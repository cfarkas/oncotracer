# OncoTracer Docker image
# Primary use: run the bundled Nextflow workflow from inside Docker.
# Conda remains available inside the image because the BAM-refinement and
# classifier subworkflows use Conda environments.

FROM condaforge/miniforge3:24.11.3-0

LABEL org.opencontainers.image.title="OncoTracer" \
      org.opencontainers.image.description="Reproducible LP-WGS CNA analysis workflow with Nextflow, Docker, and Conda fallback" \
      org.opencontainers.image.source="https://github.com/cfarkas/oncotracer" \
      org.opencontainers.image.licenses="Research-use"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    ONCOTRACER_HOME=/opt/OncoTracer \
    NXF_HOME=/opt/nextflow \
    NXF_VER=26.04.2 \
    PATH=/opt/conda/bin:/opt/OncoTracer/bin:/usr/local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      gzip \
      less \
      openjdk-17-jre-headless \
      procps \
      rsync \
      tar \
      unzip \
      wget \
      pigz \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

# Core tools used by BAM refinement and plotting. The classifier keeps its own
# Nextflow Conda environments under bin/cna_classifier_nf/envs/.
RUN conda config --set channel_priority flexible \
    && conda install -y -c conda-forge -c bioconda \
      nextflow=${NXF_VER} \
      python=3.11 \
      pandas \
      numpy \
      scipy \
      pysam \
      openpyxl \
      matplotlib \
      scikit-learn \
      jinja2 \
      requests \
      reportlab \
      pypdf \
      samtools \
      minimap2 \
      git \
      pip \
    && conda clean -afy

WORKDIR ${ONCOTRACER_HOME}
COPY . ${ONCOTRACER_HOME}/

# Convenience entrypoints. `oncoTracer` runs the main workflow. `oncoTracer-shell`
# opens a shell in the same environment for debugging.
RUN chmod +x ${ONCOTRACER_HOME}/main.nf || true \
    && cat > /usr/local/bin/oncoTracer <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
cd "${ONCOTRACER_HOME:-/opt/OncoTracer}"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
OncoTracer Docker entrypoint

Usage:
  docker run --rm -v /data:/data -v /results:/results carlosfarkas/oncotracer:latest \
    -profile local -params-file /data/my_run.yml -resume

This command expands to:
  nextflow run /opt/OncoTracer/main.nf <arguments>

Mount all input/output paths used in the YAML file into the container.
HELP
  exit 0
fi
exec nextflow run "${ONCOTRACER_HOME:-/opt/OncoTracer}/main.nf" "$@"
EOF
RUN chmod +x /usr/local/bin/oncoTracer \
    && cat > /usr/local/bin/oncoTracer-shell <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
cd "${ONCOTRACER_HOME:-/opt/OncoTracer}"
exec /bin/bash "$@"
EOF
RUN chmod +x /usr/local/bin/oncoTracer-shell

ENTRYPOINT ["oncoTracer"]
CMD ["--help"]
