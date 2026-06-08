#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/vendor/tinyuz-bridge"
CXX="${CXX:-c++}"

case "$(uname -s)" in
  Darwin)
    OUT="${1:-$ROOT/lianli_hydroshift/libtinyuz.dylib}"
    SHARED_FLAGS=(-dynamiclib -fPIC)
    ;;
  Linux)
    OUT="${1:-$ROOT/lianli_hydroshift/libtinyuz.so}"
    SHARED_FLAGS=(-shared -fPIC)
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "$OUT")"

SOURCES=(
  "$SRC/tuz_wrapper.cpp"
  "$SRC/tinyuz/compress/tuz_enc.cpp"
  "$SRC/tinyuz/compress/tuz_enc_private/tuz_enc_clip.cpp"
  "$SRC/tinyuz/compress/tuz_enc_private/tuz_enc_code.cpp"
  "$SRC/tinyuz/compress/tuz_enc_private/tuz_enc_match.cpp"
  "$SRC/tinyuz/compress/tuz_enc_private/tuz_sstring.cpp"
  "$SRC/HDiffPatch/libHDiffPatch/HDiff/private_diff/libdivsufsort/divsufsort.cpp"
)

"$CXX" \
  -std=c++11 \
  -O3 \
  -DNDEBUG \
  -D_IS_USED_MULTITHREAD=0 \
  "${SHARED_FLAGS[@]}" \
  -I"$SRC" \
  -I"$SRC/tinyuz" \
  -I"$SRC/HDiffPatch" \
  "${SOURCES[@]}" \
  -o "$OUT"

echo "Built $OUT"
