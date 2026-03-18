# Coverage Tracker — Design Document

**Date:** 2026-03-18
**Status:** Approved for implementation
**Hosted at:** `https://chatteranalyst.kashishkapoor.com/tracker`

## Problem

During earnings season, The Chatter and Points & Figures aim to cover every large-cap and mid-cap company. In practice, busy weeks cause companies to slip through the cracks. Without a tracker, there's no visibility into what's been covered and what's pending. This leads to micro-cap companies filling editions while major companies go uncovered.

## Solution

Two automated, independent checklists — one for Chatter, one for Points & Figures — scoped to the NIFTY LargeMidcap 250. Companies get added when they release results. Companies get struck off when they appear in a published edition. The pending list tells you exactly what to cover next.

## Coverage Universe

**NIFTY LargeMidcap 250** — 100 large-cap + 150 mid-cap companies. Constituent list refreshed monthly from NSE.

## Quarter Definitions

Each quarter has a results window — the period when companies release results for that quarter:

| Quarter | Period | Results Window |
|---------|--------|----------------|
| Q3 FY26 | Oct–Dec 2025 | Jan 1 – Mar 31, 2026 |
| Q4 FY26 | Jan–Mar 2026 | Apr 1 – Jun 30, 2026 |
| Q1 FY27 | Apr–Jun 2026 | Jul 1 – Sep 30, 2026 |

The active quarter switches automatically based on today's date. A `tracker_quarters.json` config file stores the mappings.

Quarter inference from BSE filing dates uses the Indian FY release-date mapping:

| Filing month | Maps to |
|-------------|---------|
| Jan, Feb | Q3 of current FY |
| Mar, Apr, May | Q4 of current FY |
| Jun, Jul, Aug | Q1 of next FY |
| Sep, Oct, Nov | Q2 of next FY |
| Dec | Q3 of next FY |

## Two Independent Checklists

### Chatter Checklist
- **Trigger (eligible):** Concall transcript or recording becomes available on Screener.in
- **Completion (covered):** Company appears in a published Chatter edition with quotes from that quarter
- **Source for coverage detection:** Existing `quotes.json` from scrape.py

### Points & Figures Checklist
- **Trigger (eligible):** Investor presentation uploaded on BSE
- **Completion (covered):** Company appears in a published Points & Figures edition
- **Source for coverage detection:** New P&F scraper (Substack posts currently excluded by scrape.py)

## Data Sources

### Feed 1: NIFTY LargeMidcap 250 Universe
- **Source:** NSE index constituent CSV
- **Output:** `data/tracker_universe.json`
- **Fields per company:** NSE symbol, BSE scrip code, company name
- **Refresh:** Monthly

### Feed 2: BSE Announcements API (P&F eligibility)
- **Endpoint:** `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w`
- **Params:** `strCat=Result`, `strscrip=<bse_code>`, date range = results window
- **Filter:** `SUBCATNAME` contains "Investor Presentation" or "Presentation"
- **Rate limiting:** 0.15s delay between requests, paginate up to 20 pages
- Clean JSON API — no HTML scraping, no cookie management

### Feed 3: Screener.in (Chatter eligibility)
- **URL pattern:** `screener.in/company/<SYMBOL>/`
- **Detection:** Check for concall transcript/recording link dated within results window
- **Regex on link text:** `transcript|concall|conference call|con call`
- **No downloads** — existence check only
- **Rate limiting:** 0.5s delay

### Feed 4: Coverage Detection
- **Chatter:** Match companies from `quotes.json` against the universe for editions within the results window. Uses existing scrape infrastructure.
- **P&F:** New scrape pass for "points and figures" Substack posts. Extract company headings only (no quotes). Cached like Chatter posts.

## Company Matching (5 layers, no fuzzy matching)

1. **NSE symbol as canonical key** — All sources resolve to an NSE symbol. NIFTY list provides symbols. BSE scrip codes map to symbols. Screener URLs use symbols. Chatter coverage links to Zerodha market URLs containing symbols.

2. **Normalized name match** — Strip legal suffixes (Ltd, Limited, Inc, Corp, etc.), lowercase, collapse whitespace. Catches remaining cases where symbols aren't available.

3. **Acronym expansion map** (`data/tracker_acronyms.json`) — Deterministic map of ~30-40 well-known acronyms: SBI → State Bank of India, BPCL → Bharat Petroleum Corporation, etc.

4. **Manual alias file** (`data/tracker_aliases.json`) — For brand-name mismatches: Zomato → Eternal Ltd, Paytm → One 97 Communications, Nykaa → FSN E-Commerce Ventures. Bootstrapped from ~34 known aliases.

5. **Unmatched queue** — Companies that don't resolve through any layer surface in an "unmatched" section for manual alias addition. Zero false positives.

## File Structure

### company-chatter repo (data pipeline)
```
scripts/
  tracker_refresh.py          # Orchestrator — runs all feeds, updates state
  tracker_bse_scan.py         # BSE announcements API scanner
  tracker_screener_scan.py    # Screener concall detection
  tracker_universe.py         # NIFTY LargeMidcap 250 fetcher
  tracker_coverage.py         # Matches Chatter/P&F editions against universe
  tracker_pnf_scrape.py       # Scrapes Points & Figures editions from Substack

data/
  tracker_universe.json       # 250 companies: name, NSE symbol, BSE code
  tracker_state.json          # Master state — both checklists, all statuses
  tracker_aliases.json        # Manual name overrides
  tracker_acronyms.json       # Acronym expansion map
  tracker_quarters.json       # Quarter definitions and results windows
```

### chatter-analyst repo (frontend)
```
functions/api/tracker/state.ts    # GET endpoint — serves tracker_state.json
src/features/tracker/
  TrackerPage.tsx                  # Main page with two panels
  ChecklistPanel.tsx              # Reusable panel (Chatter & P&F)
  CompanyRow.tsx                  # Single company row
  QuarterSelector.tsx             # Quarter dropdown
```

## Master State File (`tracker_state.json`)

```json
{
  "active_quarter": "Q3 FY26",
  "results_window": ["2026-01-01", "2026-03-31"],
  "last_updated": "2026-03-18T08:00:00Z",
  "companies": [
    {
      "symbol": "HDFCBANK",
      "name": "HDFC Bank",
      "chatter": {
        "eligible": true,
        "eligible_since": "2026-01-22",
        "covered": true,
        "covered_in_edition": "The Chatter: The Blind Spots",
        "covered_date": "2026-02-06"
      },
      "pnf": {
        "eligible": true,
        "eligible_since": "2026-01-20",
        "covered": false,
        "covered_in_edition": null,
        "covered_date": null
      }
    }
  ],
  "unmatched": []
}
```

## Tracker Page Layout

**URL:** `/tracker`

**Top bar:** Quarter selector dropdown (Q3 FY26, Q4 FY26, etc.) + last updated timestamp + pending count

**Two side-by-side panels:**

### Left: Chatter Checklist
- Header: "Chatter · Q3 FY26 · 47/142 covered"
- **Pending** (expanded) — companies with concalls available but not yet covered. Sorted oldest-first (most overdue at top). Shows company name, date available, days waiting.
- **Covered** (collapsed) — struck-through, shows which edition covered them.
- **Not yet reported** (collapsed, greyed) — NIFTY 250 companies without concalls yet this quarter.

### Right: Points & Figures Checklist
- Same three-section structure
- Triggered by investor presentation availability

## GitHub Actions

### Workflow: `tracker-refresh.yml`

**Two triggers:**
1. **Daily schedule:** `cron: "0 2 * * *"` (7:30 AM IST) — scans BSE + Screener for new filings
2. **On push to main** when `data/editions.json` or `data/quotes.json` change — immediately updates coverage detection when a new edition is published

**Steps:**
1. Checkout
2. Setup Python
3. Restore caches (post cache, tracker state)
4. Run `python3 scripts/tracker_refresh.py`
5. Commit `data/tracker_state.json` if changed
6. Push `tracker_state.json` to chatter-analyst deployment (R2 bucket or direct repo push)

### Data flow:
```
BSE API + Screener  →  tracker_refresh.py  →  tracker_state.json  →  Cloudflare Function  →  React page
                              ↑
                    Chatter/P&F editions
                    (existing scraper)
```

## Why the data is decoupled from the app

The tracker data updates daily. The chatter-analyst React app deploys infrequently. By serving `tracker_state.json` from a Cloudflare Function (reading from R2 or KV), the tracker page stays fresh without rebuilding or redeploying the app. The pipeline writes the JSON; the app reads it.

## Quarter Switchover

When the results window ends (e.g., Mar 31 for Q3 FY26), the next run of `tracker_refresh.py` automatically:
1. Archives the Q3 FY26 state
2. Starts a fresh Q4 FY26 state
3. Resets all companies to `eligible: false, covered: false`
4. The quarter selector lets you view past quarters

`tracker_quarters.json` drives this — no hardcoded dates in code.
