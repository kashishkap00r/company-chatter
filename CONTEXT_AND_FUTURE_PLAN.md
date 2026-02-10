# Company Chatter: Progress Context and Future Plan

Last updated: 2026-02-10

## 1) Current Snapshot
- Branch: `main`
- Latest pushed commit: `fccdfc5` (`Align frontend styling to brand guidelines`)
- Previous major commit: `0e507a6` (`Redesign UI/UX and update market link mapping pipeline`)
- Latest production deploy (Cloudflare Pages): `https://95af8300.company-chatter.pages.dev` (commit `fccdfc5`)
- Validation status: passing
  - `canonical_companies: 602`
  - `market_conflicts: 32`
  - `quarantined_companies: 7`
  - `repeat_name_keys: 1`
  - `dropped_quote_rows: 0`

## 2) Progress Completed So Far

### Data and entity pipeline
- Built precision-first entity resolution with alias/block rules, conflict quarantine, and guardrail validation.
- Added hard-fail validator and CI enforcement for entity resolution regressions.
- Stabilized quote/context/speaker extraction and mention-only company coverage.

### Market URL mapping
- Added/manualized market URL resolution via Zerodha mapping + override file.
- User decisions applied:
  - Keep provided BSE links for selected companies (do not force NSE-only for these).
  - Keep many international companies unlinked.
  - Deduplicate repeated company representations where possible.

### UX/UI redesign
- Homepage search no longer shifts while typing (fixed input + overlay results pattern).
- Company page made minimal with improved quote readability.
- Reduced high-weight timeline metadata; switched to compact line.
- Date formatting standardized to `MMM'YY` (example: `Jun'25`).

### Brand guideline alignment
- Parsed `/home/kashish.kapoor/Downloads/Brand guidelines (2).pdf`.
- Applied strict brand vibe update:
  - Inter typography
  - Zerodha primary palette usage (`#387ED1`, `#FFA412`, `#424242`, white)
  - Minimal backgrounds, restrained shadows, guideline-consistent radii and card language.

## 3) Locked Product/Design Decisions
1. Homepage remains clean, minimal, and stable while searching.
2. Company timeline metadata stays low-emphasis and compact.
3. Dates should remain in `MMM'YY` format.
4. International names can remain without market links.
5. Selected user-provided BSE links should remain as-is.

## 4) Important Files to Know
- Core scripts: `scripts/scrape.py`, `scripts/build_site.py`, `scripts/validate_entity_resolution.py`
- URL overrides: `data/manual_market_urls.json`
- Resolution guardrails: `data/entity_resolution_baseline.json`, `data/entity_resolution_report.json`
- UI files: `templates/base.html`, `templates/index.html`, `templates/company.html`, `assets/styles.css`
- Ops: `scripts/deploy_pages.sh`, `wrangler.jsonc`

## 5) Current Local Working State (Not Fully Clean)
- Modified: `AGENTS.md`
- Modified (regenerated): `data/entity_resolution_report.json`
- Untracked: `tmp/` (PDF render intermediates)

## 6) Future Plan of Action

### Immediate (next session)
1. Decide whether to commit `AGENTS.md` update from latest guideline-writing task.
2. Clean or ignore `tmp/` render artifacts (`tmp/pdfs/...`).
3. Re-run quick sanity pass on mobile layout for homepage/company pages.

### Near-term
1. Add lightweight visual QA checklist for key pages before deployment.
2. Add deterministic smoke checks for search UX behavior in generated `site/index.html`.
3. Review and prune stale manual overrides in `data/manual_market_urls.json` quarterly.

### Ongoing
1. Keep running full pipeline before deploy:
   `python3 scripts/scrape.py && python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py`
2. Keep PRs small by concern (pipeline vs UI vs data).
3. If guardrail thresholds intentionally change, update baseline in same PR with rationale.

## 7) Deployment Runbook
- Build + validate + deploy:
  `./scripts/deploy_pages.sh company-chatter`
- Confirm latest production deployment:
  `npx wrangler pages deployment list --project-name company-chatter`
