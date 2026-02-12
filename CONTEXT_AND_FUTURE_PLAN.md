# Company Chatter: Context and Future Plan

Last updated: 2026-02-11

## 1) Current Snapshot
- Branch: `main`
- Latest live deploy: `https://083320a1.company-chatter.pages.dev`
- Project domain: `https://company-chatter.pages.dev`
- Validation status: passing (`canonical_companies: 602`, `market_conflicts: 32`, `quarantined_companies: 7`, `repeat_name_keys: 1`, `dropped_quote_rows: 0`)

## 2) What Is Now Locked

### Product/UX decisions
1. Homepage search result clicks must navigate instantly and reliably.
2. Company page should not show the industry line under each edition.
3. Edition title emphasis is toned down compared with earlier builds.
4. Quote text remains emphasized but closer in scale to context/speaker.
5. Header subline remains: `Know more about companies`.

### Tile ordering/layout decision (important)
- Editions are still rendered chronologically in a vertical flow (oldest first).
- Two-column layout applies to quote cards **within one edition**, not across editions.
- On smaller screens, quote cards collapse to one column.

## 3) Recent Changes by Area

### Search reliability (`templates/index.html`)
- Reworked result interaction to pointer-based navigation to avoid blur timeout race.
- Kept keyboard support (`ArrowUp/Down`, `Enter`, `Escape`) intact.

### Company page typography/layout (`assets/styles.css`)
- Introduced editorial font pairing and reduced oversized quote dominance.
- Added per-edition quote grid (2-column desktop, 1-column responsive).
- Preserved readability for context and speaker metadata.

### Generation/template updates
- Removed sector/industry line output from `scripts/build_site.py`.
- Updated header text and font imports in `templates/base.html`.

## 4) Operational Context
- Deployment path remains:
  `./scripts/deploy_pages.sh company-chatter`
- This script runs:
  1. `python3 scripts/build_site.py`
  2. `python3 scripts/validate_entity_resolution.py`
  3. `npx wrangler pages deploy site --project-name company-chatter`

## 5) Recommended Next Steps
1. Commit current changes with a clear message covering search reliability + company page UX.
2. Add a lightweight UI smoke checklist (homepage search click, company page order/layout, mobile responsiveness) before every deploy.
3. Optionally add an automated browser check for search result click-through reliability.

## 6) Working Tree Reminder
Current work includes updates to:
- `AGENTS.md`
- `assets/styles.css`
- `scripts/build_site.py`
- `templates/base.html`
- `templates/index.html`
- `PROGRESS_LOG.md`
- `CONTEXT_AND_FUTURE_PLAN.md`
