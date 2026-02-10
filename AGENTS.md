# Repository Guidelines

## Project Structure & Module Organization
This repo converts The Chatter archive into company-level quote timelines.

- `scripts/`: core pipeline scripts.
  - `scrape.py` fetches and extracts editions, companies, quotes, and mentions.
  - `build_site.py` merges entity variants and renders static pages.
  - `validate_entity_resolution.py` enforces no-duplication/no-leakage invariants.
- `data/`: source-of-truth JSON and rule files (`entity_*_rules.json`, baseline/report files, generated datasets).
- `templates/` and `assets/`: HTML templates and CSS.
- `site/`: generated static output.
- `.github/workflows/entity-resolution-guard.yml`: CI pipeline (scrape, build, validate).

## Build, Test, and Development Commands
- `python3 scripts/scrape.py`: scrape and refresh `data/*.json`.
- `python3 scripts/build_site.py`: build `site/` and entity resolution report.
- `python3 scripts/validate_entity_resolution.py`: fail on duplicate/leakage regressions.
- `python3 scripts/scrape.py && python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py`: full local verification.
- `./scripts/preview.sh [port]`: local preview (`8787` default).
- `./scripts/deploy_pages.sh company-chatter`: build, validate, deploy to Cloudflare Pages.

## Coding Style & Naming Conventions
- Use Python 3 with 4-space indentation and type hints for new/changed functions.
- Prefer small, deterministic helpers; keep extraction and merge logic explicit.
- Use `snake_case` for variables/functions, lowercase slugs for IDs, and stable JSON schemas.
- Keep rule/config changes in `data/` (not hardcoded in multiple places) whenever possible.

## Testing Guidelines
- Primary gate is `scripts/validate_entity_resolution.py` plus CI workflow.
- After logic changes, run the full pipeline and confirm validation passes.
- If entity behavior changes, update corresponding rule/baseline files and regenerated reports.
- For UI-impacting changes, spot-check `site/index.html` and affected company pages.

## Commit & Pull Request Guidelines
- Follow existing commit style: short imperative subject lines (for example, `Add hard-fail entity resolution guardrails`).
- Keep commits focused by concern (scraper, resolver, templates, data).
- PRs should include:
  - problem statement and root cause,
  - files changed and why,
  - commands run with key outputs (counts/pass-fail),
  - screenshots for visible UI/template changes.
