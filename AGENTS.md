# Repository Guidelines

## Project Structure & Module Organization
- `scripts/`: core pipeline and operations (`scrape.py`, `scrape_dailybrief.py`, `build_site.py`, `validate_entity_resolution.py`) plus helpers (`preview.sh`, `deploy_pages.sh`, `update_oat.sh`).
- `templates/`: HTML templates for shared layout and pages (`base.html`, `index.html`, `company.html`, `header_search.html`).
- `assets/`: CSS, fonts, icons, and vendored UI assets (`assets/vendor/oat/`).
- `data/`: scraped inputs, rule files, and generated reports (for example `entity_resolution_report.json`).
- `site/`: generated static output used for local preview and Pages deploys.
- `.github/workflows/`: CI checks and scheduled refresh automation.

## Build, Test, and Development Commands
- `python3 scripts/scrape.py`: refresh The Chatter data and Daily Brief cache.
- `python3 scripts/scrape_dailybrief.py`: refresh only Daily Brief data.
- `python3 scripts/build_site.py`: generate the static site into `site/`.
- `python3 scripts/validate_entity_resolution.py`: enforce entity-resolution guardrails.
- `./scripts/preview.sh 8787`: build and serve locally at `http://localhost:8787`.
- `./scripts/deploy_pages.sh company-chatter`: build, validate, and deploy to Cloudflare Pages.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case`, deterministic helper functions.
- Keep parsing and resolution logic in `scripts/`; keep templates declarative.
- Frontend is vanilla HTML/CSS/JS; avoid adding framework dependencies.
- Use kebab-case for slugs, CSS classes, and IDs (example: `reliance-industries`, `header-search-results`).
- Prefer updates to JSON rule files in `data/` over hardcoded one-off logic.

## Testing Guidelines
- There is no separate unit-test suite; the required quality gate is build + validation.
- Before merge, run:
  - `python3 scripts/build_site.py`
  - `python3 scripts/validate_entity_resolution.py`
- For UI changes, manually verify homepage and company-page search behavior on desktop and mobile.
- Review generated `data/` diffs to confirm regressions are intentional.

## Commit & Pull Request Guidelines
- Use concise imperative commit messages (example: `Add company-page header search`).
- Keep commits scoped by concern (scrape logic, resolver rules, UI, deploy).
- PRs should include: change summary, commands run, relevant issue/context, and screenshots for visible UI changes.

## Security & Configuration Notes
- Do not commit tokens, credentials, or environment secrets.
- Confirm Cloudflare auth with `npx wrangler whoami`.
- `wrangler.jsonc` is configured with `pages_build_output_dir = "site"`.
