#!/usr/bin/env bash
set -euo pipefail

PROJECT="${1:-company-chatter}"

python3 scripts/build_site.py
python3 scripts/validate_entity_resolution.py
npx wrangler pages deploy site --project-name "${PROJECT}"
