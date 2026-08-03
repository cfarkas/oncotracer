#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: prepare_samurai_source.sh --lpwgs-root DIR [--revision TAG]

Create or reuse a local checkout of the pinned SAMURAI workflow and print its
absolute directory. The checkout uses Git's HTTPS transport, not the GitHub
REST API, so repeated OncoTracer runs do not depend on an unauthenticated API
rate limit.
EOF
}

LPWGS_ROOT=""
REVISION="v1.4.0"
REPOSITORY_URL="https://github.com/DIncalciLab/samurai.git"
CACHE_FORMAT="2"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$LPWGS_ROOT" ]] || { echo "ERROR: --lpwgs-root is required" >&2; exit 2; }
[[ "$REVISION" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: invalid SAMURAI revision: $REVISION" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "ERROR: git is required to prepare SAMURAI" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required to prepare SAMURAI" >&2; exit 1; }

LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
CACHE_ROOT="$LPWGS_ROOT/.oncotracer/samurai"
TARGET="$CACHE_ROOT/$REVISION"
LOCK_FILE="$CACHE_ROOT/.${REVISION}.lock"
mkdir -p "$CACHE_ROOT"

target_valid() {
  [[ -s "$TARGET/main.nf" && -d "$TARGET/.git" && -s "$TARGET/.oncotracer-source" ]] || return 1
  grep -qx "cache_format=$CACHE_FORMAT" "$TARGET/.oncotracer-source"
}

if target_valid; then
  printf '%s\n' "$TARGET"
  exit 0
fi

# Serialize first-time preparation when multiple analyses share one project root.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock 9
fi

if target_valid; then
  printf '%s\n' "$TARGET"
  exit 0
fi

if [[ -e "$TARGET" ]]; then
  echo "Removing incompatible or incomplete SAMURAI cache: $TARGET" >&2
  rm -rf -- "$TARGET"
fi

TMP="$(mktemp -d "$CACHE_ROOT/.${REVISION}.tmp.XXXXXX")"
cleanup() {
  [[ -z "${TMP:-}" ]] || rm -rf -- "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Preparing SAMURAI $REVISION under $TARGET" >&2
GIT_TERMINAL_PROMPT=0 git clone \
  --depth 1 \
  --branch "$REVISION" \
  --single-branch \
  "$REPOSITORY_URL" \
  "$TMP" >&2

[[ -s "$TMP/main.nf" ]] || { echo "ERROR: cloned SAMURAI source has no main.nf" >&2; exit 1; }

# Apply the pinned v1.4.0 sequence-dictionary typo correction before the cache
# is published, so every concurrent Docker, Poetry, Conda, or Singularity run
# sees the same immutable prepared source tree.
python3 - "$TMP/main.nf" <<'PY_PATCH'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old = "dict = params.dict ? channel.fromPath(params.fai).map { it -> [[id: it.baseName], it] }.collect() : channel.empty()"
new = "dict = params.dict ? channel.fromPath(params.dict).map { it -> [[id: it.baseName], it] }.collect() : channel.empty()"
text = path.read_text(encoding="utf-8")
if new in text:
    pass
elif old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
else:
    raise SystemExit(f"ERROR: expected SAMURAI dictionary pattern was not found in {path}")
PY_PATCH

RESOLVED_COMMIT="$(git -C "$TMP" rev-parse HEAD)"
printf 'cache_format=%s\nrevision=%s\ncommit=%s\nrepository=%s\n' \
  "$CACHE_FORMAT" "$REVISION" "$RESOLVED_COMMIT" "$REPOSITORY_URL" \
  > "$TMP/.oncotracer-source"

if mv -T "$TMP" "$TARGET" 2>/dev/null; then
  TMP=""
elif target_valid; then
  # Another process completed the same atomic cache while this one cloned.
  rm -rf -- "$TMP"
  TMP=""
else
  echo "ERROR: could not publish the SAMURAI cache: $TARGET" >&2
  exit 1
fi

printf '%s\n' "$TARGET"
