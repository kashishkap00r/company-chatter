#!/usr/bin/env python3
"""Coverage tracker orchestrator — runs all feeds and builds tracker_state.json."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "tracker_state.json"
UNIVERSE_PATH = DATA_DIR / "tracker_universe.json"
QUARTERS_PATH = DATA_DIR / "tracker_quarters.json"


def _read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def determine_active_quarter(quarters_config: dict) -> dict | None:
    """Find the quarter whose results window contains today's date."""
    today = datetime.now().strftime("%Y-%m-%d")
    for q in quarters_config.get("quarters", []):
        window = q.get("results_window", [])
        if len(window) == 2 and window[0] <= today <= window[1]:
            return q
    return None


def main() -> None:
    quarters_config = _read_json(QUARTERS_PATH)
    active_quarter = determine_active_quarter(quarters_config)
    if not active_quarter:
        print("No active quarter found for today's date. Check tracker_quarters.json.")
        sys.exit(1)

    quarter_name = active_quarter["name"]
    from_date, to_date = active_quarter["results_window"]
    print(f"Active quarter: {quarter_name}")
    print(f"Results window: {from_date} to {to_date}")
    print()

    # Step 1: Load universe
    if not UNIVERSE_PATH.exists():
        print("Universe not found. Run: python3 scripts/tracker_universe.py")
        sys.exit(1)

    universe_list = _read_json(UNIVERSE_PATH, [])
    universe_dict = {c["symbol"]: c for c in universe_list if c.get("symbol")}
    print(f"Universe: {len(universe_dict)} companies")

    # Step 2: Load existing state (for preserving eligible_since dates)
    existing_state = _read_json(STATE_PATH, {})
    existing_companies = {}
    if existing_state.get("active_quarter") == quarter_name:
        for comp in existing_state.get("companies", []):
            existing_companies[comp["symbol"]] = comp

    # Step 3: BSE scan
    print("\n--- BSE Scan ---")
    from tracker_bse_scan import scan_bse_filings
    bse_results = scan_bse_filings(universe_list, from_date, to_date)

    # Step 4: Screener scan
    print("\n--- Screener Scan ---")
    from tracker_screener_scan import scan_screener_concalls
    screener_results = scan_screener_concalls(universe_list, from_date, to_date)

    # Step 5: P&F scrape
    print("\n--- P&F Scrape ---")
    from tracker_pnf_scrape import scrape_pnf_editions
    pnf_editions = scrape_pnf_editions()
    _write_json(DATA_DIR / "tracker_pnf_editions.json", pnf_editions)

    # Step 6: Coverage detection
    print("\n--- Coverage Detection ---")
    from tracker_coverage import detect_chatter_coverage, detect_pnf_coverage
    from tracker_match import load_aliases, load_acronyms

    aliases = load_aliases()
    acronyms = load_acronyms()
    chatter_covered = detect_chatter_coverage(universe_dict, aliases, acronyms, from_date, to_date)
    pnf_covered = detect_pnf_coverage(universe_dict, aliases, acronyms, from_date, to_date)

    # Step 7: Merge all signals into state
    print("\n--- Building State ---")
    companies_state: list[dict] = []

    for symbol, entry in sorted(universe_dict.items()):
        bse = bse_results.get(symbol, {})
        screener_date = screener_results.get(symbol)

        # Chatter eligibility: Screener transcript OR BSE transcript
        chatter_eligible_since = screener_date or bse.get("chatter_date")
        chatter_covered_info = chatter_covered.get(symbol)

        # P&F eligibility: BSE investor presentation
        pnf_eligible_since = bse.get("pnf_date")
        pnf_covered_info = pnf_covered.get(symbol)

        # Preserve existing eligible_since dates (don't overwrite with None on re-run)
        existing = existing_companies.get(symbol, {})
        if not chatter_eligible_since:
            chatter_eligible_since = (existing.get("chatter") or {}).get("eligible_since")
        if not pnf_eligible_since:
            pnf_eligible_since = (existing.get("pnf") or {}).get("eligible_since")

        companies_state.append({
            "symbol": symbol,
            "name": entry.get("name", ""),
            "chatter": {
                "eligible": chatter_eligible_since is not None,
                "eligible_since": chatter_eligible_since,
                "covered": chatter_covered_info is not None,
                "covered_in_edition": chatter_covered_info["edition_title"] if chatter_covered_info else None,
                "covered_date": chatter_covered_info["edition_date"] if chatter_covered_info else None,
            },
            "pnf": {
                "eligible": pnf_eligible_since is not None,
                "eligible_since": pnf_eligible_since,
                "covered": pnf_covered_info is not None,
                "covered_in_edition": pnf_covered_info["edition_title"] if pnf_covered_info else None,
                "covered_date": pnf_covered_info["edition_date"] if pnf_covered_info else None,
            },
        })

    state = {
        "active_quarter": quarter_name,
        "results_window": [from_date, to_date],
        "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "total_companies": len(companies_state),
            "chatter_eligible": sum(1 for c in companies_state if c["chatter"]["eligible"]),
            "chatter_covered": sum(1 for c in companies_state if c["chatter"]["covered"]),
            "pnf_eligible": sum(1 for c in companies_state if c["pnf"]["eligible"]),
            "pnf_covered": sum(1 for c in companies_state if c["pnf"]["covered"]),
        },
        "companies": companies_state,
        "unmatched": [],
    }

    _write_json(STATE_PATH, state)

    s = state["summary"]
    print(f"\nTracker state built for {quarter_name}:")
    print(f"  Companies: {s['total_companies']}")
    print(f"  Chatter: {s['chatter_covered']}/{s['chatter_eligible']} covered")
    print(f"  P&F: {s['pnf_covered']}/{s['pnf_eligible']} covered")
    print(f"  Written to: {STATE_PATH}")


if __name__ == "__main__":
    main()
