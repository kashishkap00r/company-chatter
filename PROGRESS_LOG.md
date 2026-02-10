# Company Chatter Progress Log

Last updated: 2026-02-10

## Summary
This project now has a precision-first entity resolution pipeline that:
- avoids cross-company quote leakage,
- removes duplicate company cards,
- tracks ambiguous matches via quarantine/reporting,
- hard-fails on future regressions using baseline guardrails.

## Major Fixes Completed

### 1) Quote/context extraction improvements
- Context paragraphs are separated from actual quote text.
- Indented/blockquote content is treated as quote; text above it as context.
- Speaker lines (`- Name`) are parsed into speaker metadata.
- Mention-only coverage is captured so company pages can include editions even when no direct quote block is extracted.

Key files:
- `scripts/scrape.py`
- `scripts/build_site.py`
- `data/company_mentions.json` (new data artifact)

### 2) Company identity and de-duplication
- Extraction-time company IDs are name-based (not URL-tail based) to avoid accidental identity collapse.
- Resolver merges with strict compatibility checks.
- Added alias and block rule files:
  - `data/entity_alias_rules.json`
  - `data/entity_block_rules.json`
- Added cross-bucket merge pass for acronym/full-name pairs (example: `SBI` + `State Bank of India`) with market-key conflict protection.
- Pairwise compatibility enforcement prevents transitive bad merges.

Key files:
- `scripts/build_site.py`
- `data/entity_alias_rules.json`
- `data/entity_block_rules.json`
- `data/entity_resolution_report.json`

### 3) Regression guardrails (hard fail)
- Baseline contract added:
  - `data/entity_resolution_baseline.json`
- New validator added:
  - `scripts/validate_entity_resolution.py`
- CI workflow added (runs scrape -> build -> validate):
  - `.github/workflows/entity-resolution-guard.yml`
- Deployment script now validates before deploy:
  - `scripts/deploy_pages.sh`

## Current Baseline (Accepted)
From `data/entity_resolution_report.json`:
- `input_companies`: 788
- `canonical_companies`: 609
- `market_conflicts`: 13
- `quarantined_companies`: 6
- `dropped_quote_rows`: 0
- `dropped_mention_rows`: 7
- `repeat_name_keys` in visible index: 0

Critical invariants enforced:
- `SBI` includes `p-the-chatter-between-seasons`.
- `Reliance Consumer Products` and `Reliance Industries` remain separate.
- Blocked pairs never merge.
- Required alias pairs always merge.

## Important Commits
- `e4bf336` Improve quote parsing and add mention-based company coverage
- `04b4f89` Add precision-first entity resolver and dedupe company identities
- `9c31ced` Add hard-fail entity resolution guardrails

## Standard Commands

### Full local pipeline
```bash
python3 scripts/scrape.py && python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py
```

### Build-only + validation
```bash
python3 scripts/build_site.py && python3 scripts/validate_entity_resolution.py
```

### Deploy (now includes validation)
```bash
./scripts/deploy_pages.sh company-chatter
```

## How to extend safely
1. Add/adjust safe merges in `data/entity_alias_rules.json`.
2. Add hard-blocked never-merge pairs in `data/entity_block_rules.json`.
3. Re-run full pipeline and check validator pass.
4. If intentionally changing accepted thresholds, update `data/entity_resolution_baseline.json` in the same PR with rationale.
