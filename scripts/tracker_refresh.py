#!/usr/bin/env python3
"""Coverage tracker orchestrator — runs all feeds and builds tracker_state.json.

Supports multiple active quarters simultaneously. A quarter is active if today
is within its results_window (current quarter) OR today is past the window but
before its coverage_deadline (carryover quarter with ongoing editorial work).
"""

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


def determine_active_quarters(quarters_config: dict) -> list[dict]:
    """Find all quarters that should be actively tracked.

    A quarter is active if:
    1. Today is within its results_window (the current quarter), OR
    2. Today is past its results_window but before its coverage_deadline
       (a carryover quarter — editorial coverage work still ongoing).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    active: list[dict] = []
    for q in quarters_config.get("quarters", []):
        window = q.get("results_window", [])
        deadline = q.get("coverage_deadline", "")
        if len(window) != 2:
            continue
        # Current quarter: today is within results_window
        if window[0] <= today <= window[1]:
            active.append(q)
        # Carryover quarter: results_window ended, coverage_deadline not yet reached
        elif window[1] < today and deadline and today <= deadline:
            active.append(q)
    # Sort so carryover quarters come first (earlier results_window)
    active.sort(key=lambda q: q["results_window"][0])
    return active


def _load_existing_by_quarter(existing_state: dict) -> dict[str, dict[str, dict]]:
    """Load existing state into a per-quarter lookup, handling both formats.

    Returns: { quarter_name: { symbol: company_dict } }
    """
    existing: dict[str, dict[str, dict]] = {}

    # New multi-quarter format (schema_version >= 2)
    if "quarters" in existing_state:
        for qstate in existing_state["quarters"]:
            qname = qstate.get("name", "")
            if qname:
                existing[qname] = {
                    c["symbol"]: c for c in qstate.get("companies", [])
                }
        return existing

    # Old single-quarter format (backward compat)
    qname = existing_state.get("active_quarter", "")
    if qname:
        existing[qname] = {
            c["symbol"]: c for c in existing_state.get("companies", [])
        }

    return existing


def _reconstruct_eligibility(
    existing_companies: dict[str, dict],
) -> tuple[dict[str, dict], dict[str, str | None]]:
    """Rebuild BSE/Screener-like results from preserved state data.

    For carryover quarters whose results_window has ended, we skip expensive
    HTTP scans and reconstruct eligibility from the saved eligible_since dates.

    Returns: (bse_results, screener_results) matching the shapes the scanners return.
    """
    bse_results: dict[str, dict] = {}
    screener_results: dict[str, str | None] = {}

    for symbol, comp in existing_companies.items():
        chatter_es = (comp.get("chatter") or {}).get("eligible_since")
        pnf_es = (comp.get("pnf") or {}).get("eligible_since")
        if pnf_es or chatter_es:
            bse_results[symbol] = {
                "pnf_date": pnf_es,
                "chatter_date": chatter_es,
            }
        if chatter_es:
            screener_results[symbol] = chatter_es

    return bse_results, screener_results


def build_quarter_state(
    quarter: dict,
    universe_dict: dict[str, dict],
    bse_results: dict[str, dict],
    screener_results: dict[str, str | None],
    chatter_covered: dict[str, dict],
    pnf_covered: dict[str, dict],
    existing_companies: dict[str, dict],
) -> dict:
    """Build the state dict for a single quarter."""
    from_date, to_date = quarter["results_window"]
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

    return {
        "name": quarter["name"],
        "results_window": [from_date, to_date],
        "coverage_deadline": quarter.get("coverage_deadline", ""),
        "is_primary": False,  # set by caller
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


def main() -> None:
    quarters_config = _read_json(QUARTERS_PATH)
    active_quarters = determine_active_quarters(quarters_config)
    if not active_quarters:
        print("No active quarters found for today's date. Check tracker_quarters.json.")
        sys.exit(1)

    print(f"Active quarters: {[q['name'] for q in active_quarters]}")
    for q in active_quarters:
        window = q["results_window"]
        deadline = q.get("coverage_deadline", "?")
        print(f"  {q['name']}: results {window[0]}–{window[1]}, deadline {deadline}")
    print()

    # Step 1: Load universe
    if not UNIVERSE_PATH.exists():
        print("Universe not found. Run: python3 scripts/tracker_universe.py")
        sys.exit(1)

    universe_list = _read_json(UNIVERSE_PATH, [])
    universe_dict = {c["symbol"]: c for c in universe_list if c.get("symbol")}
    print(f"Universe: {len(universe_dict)} companies")

    # Step 2: Load existing state (handles both old and new formats)
    existing_state = _read_json(STATE_PATH, {})
    existing_by_quarter = _load_existing_by_quarter(existing_state)

    # Step 3: Build eligibility per quarter
    today = datetime.now().strftime("%Y-%m-%d")
    quarter_eligibility: dict[str, tuple[dict, dict]] = {}

    for q in active_quarters:
        qname = q["name"]
        from_date, to_date = q["results_window"]

        if to_date < today and qname in existing_by_quarter:
            # Carryover quarter: reuse existing eligibility (skip expensive scans)
            print(f"\n--- {qname}: Reusing existing eligibility (window closed) ---")
            bse_results, screener_results = _reconstruct_eligibility(
                existing_by_quarter[qname]
            )
        else:
            # Current quarter: run scans
            print(f"\n--- {qname}: BSE Scan ---")
            from tracker_bse_scan import scan_bse_filings
            bse_results = scan_bse_filings(universe_list, from_date, to_date)

            print(f"\n--- {qname}: Screener Scan ---")
            from tracker_screener_scan import scan_screener_concalls
            screener_results = scan_screener_concalls(universe_list, from_date, to_date)

        quarter_eligibility[qname] = (bse_results, screener_results)

    # Step 4: P&F scrape (shared across quarters)
    print("\n--- P&F Scrape ---")
    from tracker_pnf_scrape import scrape_pnf_editions
    pnf_editions = scrape_pnf_editions()
    _write_json(DATA_DIR / "tracker_pnf_editions.json", pnf_editions)

    # Step 5: Build eligibility sets for coverage attribution
    # These tell the coverage detector which symbols belong to which quarter
    chatter_eligible_by_q: dict[str, set[str]] = {}
    pnf_eligible_by_q: dict[str, set[str]] = {}

    for q in active_quarters:
        qname = q["name"]
        bse_results, screener_results = quarter_eligibility[qname]
        existing_companies = existing_by_quarter.get(qname, {})

        chatter_set: set[str] = set()
        pnf_set: set[str] = set()

        for symbol in universe_dict:
            bse = bse_results.get(symbol, {})
            screener_date = screener_results.get(symbol)

            chatter_es = screener_date or bse.get("chatter_date")
            pnf_es = bse.get("pnf_date")

            # Also check preserved eligible_since from existing state
            existing = existing_companies.get(symbol, {})
            if not chatter_es:
                chatter_es = (existing.get("chatter") or {}).get("eligible_since")
            if not pnf_es:
                pnf_es = (existing.get("pnf") or {}).get("eligible_since")

            if chatter_es:
                chatter_set.add(symbol)
            if pnf_es:
                pnf_set.add(symbol)

        chatter_eligible_by_q[qname] = chatter_set
        pnf_eligible_by_q[qname] = pnf_set

    # Step 6: Coverage detection with per-edition quarter attribution
    print("\n--- Coverage Detection (multi-quarter) ---")
    from tracker_coverage import detect_chatter_coverage_multi, detect_pnf_coverage_multi
    from tracker_match import load_aliases, load_acronyms

    aliases = load_aliases()
    acronyms = load_acronyms()

    earliest_start = min(q["results_window"][0] for q in active_quarters)

    chatter_covered_by_q = detect_chatter_coverage_multi(
        universe_dict, aliases, acronyms, chatter_eligible_by_q, earliest_start,
    )
    pnf_covered_by_q = detect_pnf_coverage_multi(
        universe_dict, aliases, acronyms, pnf_eligible_by_q, earliest_start,
    )

    # Step 7: Build state for each quarter
    print("\n--- Building State ---")
    quarter_states: list[dict] = []

    for q in active_quarters:
        qname = q["name"]
        bse_results, screener_results = quarter_eligibility[qname]
        existing_companies = existing_by_quarter.get(qname, {})

        qstate = build_quarter_state(
            q, universe_dict, bse_results, screener_results,
            chatter_covered_by_q.get(qname, {}),
            pnf_covered_by_q.get(qname, {}),
            existing_companies,
        )
        quarter_states.append(qstate)

    # Step 8: Determine primary quarter
    # Carryover quarter with pending work is primary; else the current quarter
    for qs in quarter_states:
        chatter_pending = qs["summary"]["chatter_eligible"] - qs["summary"]["chatter_covered"]
        pnf_pending = qs["summary"]["pnf_eligible"] - qs["summary"]["pnf_covered"]
        window_ended = qs["results_window"][1] < today
        qs["is_primary"] = window_ended and (chatter_pending > 0 or pnf_pending > 0)

    if not any(qs["is_primary"] for qs in quarter_states):
        # No carryover quarter has pending work — make the current quarter primary
        for qs in quarter_states:
            if qs["results_window"][0] <= today <= qs["results_window"][1]:
                qs["is_primary"] = True
                break

    state = {
        "schema_version": 2,
        "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "quarters": quarter_states,
    }

    _write_json(STATE_PATH, state)

    print(f"\nTracker state built ({len(quarter_states)} quarter(s)):")
    for qs in quarter_states:
        s = qs["summary"]
        primary = " [PRIMARY]" if qs["is_primary"] else ""
        print(f"  {qs['name']}{primary}:")
        print(f"    Chatter: {s['chatter_covered']}/{s['chatter_eligible']} covered")
        print(f"    P&F: {s['pnf_covered']}/{s['pnf_eligible']} covered")
    print(f"  Written to: {STATE_PATH}")


if __name__ == "__main__":
    main()
