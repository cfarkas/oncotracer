#!/usr/bin/env bash
# Shared, sourceable helpers for public FASTQ examples.

validate_downloaded_fastq() {
  local out="$1" expected_bytes="$2" expected_md5="$3"
  [[ -s "$out" ]] || return 1
  [[ "$(stat -c %s "$out")" == "$expected_bytes" ]] || return 1
  [[ "$(md5sum "$out" | awk '{print $1}')" == "$expected_md5" ]] || return 1
  gzip -t "$out" >/dev/null 2>&1
}

download_validated_fastq() {
  local url="$1" out="$2" expected_bytes="$3" expected_md5="$4"
  local attempt current_bytes out_dir out_name
  mkdir -p "$(dirname "$out")"

  if validate_downloaded_fastq "$out" "$expected_bytes" "$expected_md5"; then
    echo "REUSE: $out (size, MD5, and gzip checks passed)"
    return 0
  fi

  if [[ -e "$out" && "$(stat -c %s "$out")" -ge "$expected_bytes" ]]; then
    echo "RESET: invalid complete file $out"
    rm -f "$out" "$out.aria2"
  fi

  if command -v aria2c >/dev/null 2>&1; then
    out_dir="$(dirname "$out")"
    out_name="$(basename "$out")"
    current_bytes=0
    [[ -e "$out" ]] && current_bytes="$(stat -c %s "$out")"
    echo "DOWNLOAD: $out ($current_bytes/$expected_bytes bytes present; aria2 multi-connection resume)"
    aria2c --continue=true --max-connection-per-server=8 --split=8 \
      --min-split-size=1M --file-allocation=none --auto-file-renaming=false \
      --allow-overwrite=true --max-tries=10 --retry-wait=5 --timeout=60 \
      --summary-interval=30 --console-log-level=warn \
      --dir="$out_dir" --out="$out_name" "$url" || true
    rm -f "$out.aria2"
    if validate_downloaded_fastq "$out" "$expected_bytes" "$expected_md5"; then
      echo "VALIDATED: $out"
      return 0
    fi
  fi

  for attempt in $(seq 1 20); do
    if [[ -e "$out" && "$(stat -c %s "$out")" -ge "$expected_bytes" ]]; then
      echo "RESET: downloaded file failed size, MD5, or gzip validation"
      rm -f "$out"
    fi
    current_bytes=0
    [[ -e "$out" ]] && current_bytes="$(stat -c %s "$out")"
    echo "DOWNLOAD attempt $attempt/20: $out ($current_bytes/$expected_bytes bytes present; curl resume)"
    curl --fail --location --continue-at - \
      --connect-timeout 30 --speed-time 60 --speed-limit 1024 \
      --retry 3 --retry-delay 3 --retry-all-errors \
      --output "$out" "$url" || true

    if validate_downloaded_fastq "$out" "$expected_bytes" "$expected_md5"; then
      echo "VALIDATED: $out"
      return 0
    fi
    sleep $(( attempt < 10 ? attempt * 2 : 20 ))
  done

  echo "ERROR: could not download and validate $url after resumable attempts" >&2
  echo "       Rerun the same command; a valid partial file will be resumed." >&2
  return 1
}
