#!/usr/bin/env bash
# Sourced by ci_native_parity.sh; defining this function has no host side effects.

ensure_frozen_comparator_prerequisites() {
  local prefix command_name
  local -a missing=()
  local -a required=(samtools bwa minimap2 pigz curl wget git)

  for prefix in "$@"; do
    [[ -n "$prefix" && -d "$prefix" ]] || continue
    case ":$PATH:" in
      *":$prefix:"*) ;;
      *) PATH="$prefix:$PATH" ;;
    esac
  done
  export PATH

  for command_name in "${required[@]}"; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done
  if (( ${#missing[@]} == 0 )); then
    printf 'PARITY_PREREQUISITES_PREINSTALLED\n'
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    printf 'ERROR: missing parity prerequisites with no passwordless sudo: %s\n' \
      "${missing[*]}" >&2
    printf 'Install the listed commands or expose their preinstalled prefix on PATH; the parity job will not mutate this host.\n' >&2
    return 1
  fi
  sudo -n apt-get update
  sudo -n apt-get install -y --no-install-recommends \
    samtools bwa minimap2 pigz curl wget git

  missing=()
  for command_name in "${required[@]}"; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done
  if (( ${#missing[@]} != 0 )); then
    printf 'ERROR: prerequisite installation completed but commands remain missing: %s\n' \
      "${missing[*]}" >&2
    return 1
  fi
  printf 'PARITY_PREREQUISITES_INSTALLED\n'
}
