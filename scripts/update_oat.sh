#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/assets/vendor/oat"

# Pinned to current gh-pages commit for deterministic updates.
OAT_REF="${1:-12f5a7a9349b0892354cc6ce70c37ccff0b1ba33}"
BASE_URL="https://raw.githubusercontent.com/knadh/oat/$OAT_REF"

mkdir -p "$OUT_DIR"
curl -fsSL "$BASE_URL/oat.min.css" -o "$OUT_DIR/oat.min.css"
curl -fsSL "$BASE_URL/oat.min.js" -o "$OUT_DIR/oat.min.js"

echo "Updated Oat assets in $OUT_DIR (ref: $OAT_REF)"
