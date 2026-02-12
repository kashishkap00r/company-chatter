# Company Chatter Progress Log

Last updated: 2026-02-11

## Summary
This project builds a static company-intelligence site from The Chatter archive, with a precision-first entity pipeline and a refined company-page reading experience.

## Major Progress Completed

### 1) Data extraction and entity resolution foundation
- Stabilized quote/context/speaker extraction and mention-only coverage.
- Implemented precision-first company resolution with:
  - alias rules (`data/entity_alias_rules.json`),
  - block rules (`data/entity_block_rules.json`),
  - conflict quarantine and reporting (`data/entity_resolution_report.json`).
- Added hard-fail validator and CI guardrails:
  - `scripts/validate_entity_resolution.py`
  - `.github/workflows/entity-resolution-guard.yml`

### 2) Company page and homepage UX improvements (latest session)
- Reduced visual imbalance between quote/context/speaker text.
- Toned down edition title emphasis.
- Removed industry line from company edition cards (now date + edition title + quote count only).
- Updated header subline to: `Know more about companies`.
- Fixed intermittent homepage dropdown click issue by switching to immediate pointer-based selection in `templates/index.html`.
- Final tile behavior:
  - edition chronology remains vertical (oldest to newest),
  - quote cards are shown in a 2-column grid **inside each edition** on larger screens,
  - responsive fallback to 1 column on smaller screens.

### 3) Deployment and verification
- Cloudflare Pages deployments completed during this session:
  - `https://325374d1.company-chatter.pages.dev`
  - `https://7ab69443.company-chatter.pages.dev`
  - `https://083320a1.company-chatter.pages.dev` (latest)
- Project URL verified:
  - `https://company-chatter.pages.dev` (HTTP 200)

## Current Validation Snapshot
From latest validation/build cycle:
- `input_companies`: 780
- `canonical_companies`: 602
- `market_conflicts`: 32
- `quarantined_companies`: 7
- `repeat_name_keys`: 1
- `dropped_quote_rows`: 0

## Key Files Touched in Recent Work
- `templates/index.html` (search reliability)
- `assets/styles.css` (editorial typography + tile layout)
- `scripts/build_site.py` (removed industry line rendering)
- `templates/base.html` (header subline + fonts)
- `AGENTS.md` (contributor guide refresh)

## Standard Commands
```bash
python3 scripts/build_site.py
python3 scripts/validate_entity_resolution.py
./scripts/preview.sh 8001
./scripts/deploy_pages.sh company-chatter
```

## Notes for Next Session
1. Decide whether to commit current working tree as one UX batch or split by concern.
2. Run quick manual smoke test on search click behavior across desktop + mobile touch.
3. Keep monitoring quote/context typography balance with real content outliers.
