#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8787}"

python3 scripts/build_site.py
python3 -m http.server "${PORT}" --directory site
