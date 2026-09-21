#!/usr/bin/env bash
# Build-only BASH_ENV for legacy Bioconda post-link downloads. Original
# package/version and post-link checksum validation remain unchanged.
# Mirrors listed at https://bioconductor.org/about/mirrors/.
curl() {
    local arg
    local -a mirror_args=()
    for arg in "$@"; do
        case "$arg" in
            https://bioconductor.org/packages/3.14/*)
                arg="https://bioconductor.statistik.tu-dortmund.de/${arg#https://bioconductor.org/}"
                ;;
            https://bioconductor.org/packages/*)
                arg="https://bioconductor.posit.co/${arg#https://bioconductor.org/}"
                ;;
        esac
        mirror_args+=("$arg")
    done
    command curl --fail --connect-timeout 30 --max-time 900 "${mirror_args[@]}"
}
