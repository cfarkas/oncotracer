#!/usr/bin/env bash
set -euo pipefail

# Download a public hg38 GISTIC2 refgene file used by Broad/GDAC analyses.
# Usage:
#   download_gistic_hg38_refgene.sh /path/to/reference_dir

OUTDIR="${1:-gistic2_refgene}"
mkdir -p "$OUTDIR"
OUT="$OUTDIR/hg38.UCSC.add_miR.160920.refgene.mat"

URLS=(
  'https://gdac.broadinstitute.org/runs/CPTAC3_LSCC_DWG/CPTAC3-LSCC-v1/GISTIC2/gistic2.refgene.hg38.UCSC.add_miR.160920.mat'
  'https://gdac.broadinstitute.org/runs/awg_cptac-luad-v3.0/G5/GISTIC2/gistic2.refgene.hg38.UCSC.add_miR.160920.mat'
)

if [ -s "$OUT" ]; then
  echo "Already exists: $OUT"
  exit 0
fi

for url in "${URLS[@]}"; do
  echo "Trying: $url"
  if command -v curl >/dev/null 2>&1; then
    if curl -L --fail --retry 3 --connect-timeout 30 -o "$OUT.tmp" "$url"; then
      mv "$OUT.tmp" "$OUT"
      echo "Downloaded: $OUT"
      exit 0
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -O "$OUT.tmp" "$url"; then
      mv "$OUT.tmp" "$OUT"
      echo "Downloaded: $OUT"
      exit 0
    fi
  else
    echo 'ERROR: neither curl nor wget found.' >&2
    exit 1
  fi
done

rm -f "$OUT.tmp" || true
echo "ERROR: all downloads failed." >&2
exit 1
