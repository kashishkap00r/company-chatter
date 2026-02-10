# Repository Guidelines

## Project Structure & Module Organization
This repository builds a static “Company Chatter” site from The Chatter archive.

- `scripts/`: pipeline entry points.
  - `scrape.py` extracts editions, companies, quotes, and mentions.
  - `build_site.py` resolves entities and renders static HTML.
  - `validate_entity_resolution.py` enforces entity guardrails.
  - `refresh_zerodha_nse_index.py` refreshes NSE symbol mappings.
- `data/`: generated datasets and rule files (`entity_alias_rules.json`, `entity_block_rules.json`, baseline/report JSON).
- `templates/` + `assets/`: HTML templates and CSS.
- `site/`: generated output (deploy artifact).
- `.github/workflows/`: CI checks for scrape/build/validation.

## Build, Test, and Development Commands
- `python3 scripts/scrape.py`: refresh archive data in `data/`.
- `python3 scripts/build_site.py`: generate `site/` and entity report.
- `python3 scripts/validate_entity_resolution.py`: fail on duplicate/leakage regressions.
- `python3 scripts/scrape.py && python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py`: full local verification.
- `./scripts/preview.sh 8000`: preview locally.
- `./scripts/deploy_pages.sh company-chatter`: build, validate, deploy to Cloudflare Pages.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case`, small deterministic helpers, and type hints for new/changed code.
- IDs/slugs: lowercase kebab-style values (for example, `company-id`).
- Keep rule-driven behavior in `data/*.json` instead of hardcoding in multiple places.
- Frontend classes should remain readable and consistent with existing template/CSS patterns.

## Testing Guidelines
- No dedicated unit-test suite yet; the primary gate is `validate_entity_resolution.py`.
- For parser/resolver changes, run the full pipeline and inspect count deltas in `data/entity_resolution_report.json`.
- For UI changes, preview generated pages (`site/index.html`, representative `site/company/<slug>/index.html`) before pushing.

## Commit & Pull Request Guidelines
- Use short imperative commit subjects, matching repo history (for example, `Harden Zerodha URL mapping`).
- Keep commits focused by concern (scraper, resolver, UI, data).
- PRs should include:
  - what changed and why,
  - commands run and key outputs,
  - screenshots for UI changes,
  - notes on regenerated data files.

## Security & Configuration Tips
- Never commit secrets or API tokens.
- Treat large JSON diffs carefully; review for accidental churn before pushing.
