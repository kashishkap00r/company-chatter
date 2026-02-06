#!/usr/bin/env bash
set -euo pipefail

PROJECT="${1:-company-chatter}"

python3 scripts/build_site.py
npx wrangler pages deploy site --project-name "${PROJECT}"
