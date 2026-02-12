# Repository Guidelines

## Project Structure & Module Organization
This repository builds a static “Company Chatter” site from The Chatter archive.

- `scripts/`: pipeline entry points (`scrape.py`, `build_site.py`, `validate_entity_resolution.py`, deploy/preview helpers).
- `templates/`: HTML templates (`base.html`, `index.html`, `company.html`).
- `assets/`: styles and UI library files (`styles.css`, `oat-theme-overrides.css`, `vendor/oat/*`).
- `data/`: extracted datasets and entity-resolution rules/reports.
- `site/`: generated output for local preview and Cloudflare deployment (build artifact).
- `output/playwright/`: visual QA screenshots when UI checks are run.

## Build, Test, and Development Commands
- `python3 scripts/scrape.py`: refresh source data into `data/`.
- `python3 scripts/build_site.py`: generate static site into `site/`.
- `python3 scripts/validate_entity_resolution.py`: enforce merge/no-leakage guardrails.
- `python3 scripts/scrape.py && python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py`: full local verification.
- `./scripts/preview.sh 8000`: build and serve locally at `http://localhost:8000`.
- `./scripts/deploy_pages.sh company-chatter`: build, validate, and deploy to Cloudflare Pages.
- `./scripts/update_oat.sh [ref]`: refresh vendored Oat UI assets (optional ref override).

## Coding Style & Naming Conventions
- Use Python with 4-space indentation and `snake_case` naming.
- Keep template logic lightweight; put transformations in `scripts/build_site.py`.
- Use lowercase kebab-case for IDs/slugs (example: `tata-capital`).
- Prefer rule-driven updates in `data/*.json` over hardcoded entity exceptions.
- For Python sanity checks, run `python3 -m py_compile scripts/*.py`.

## Testing Guidelines
- There is no dedicated unit-test suite; `validate_entity_resolution.py` is the primary quality gate.
- For parser/resolver changes, run full pipeline and inspect `data/entity_resolution_report.json` deltas.
- For UI changes, verify homepage search behavior and at least one company page on desktop and mobile.

## Commit & Pull Request Guidelines
- Use short imperative commit messages (example: `Harden entity conflict guardrails`).
- Keep commits scoped by concern (scraper, resolver, UI, data).
- PRs should include: summary, rationale, commands run, key outputs, and screenshots for UI changes.
- Clearly call out regenerated data artifacts to reduce review noise.

## Security & Configuration Tips
- Never commit secrets or tokens.
- Verify Cloudflare auth before deploy: `npx wrangler whoami`.
- Review large JSON diffs carefully to avoid accidental churn.
