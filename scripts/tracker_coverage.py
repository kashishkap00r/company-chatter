#!/usr/bin/env python3
"""Match published Chatter and P&F editions against the tracking universe."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow imports from scripts/ when run standalone
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracker_match import (
    _build_auto_acronyms,
    extract_symbol_from_zerodha_url,
    load_aliases,
    load_acronyms,
    load_universe,
    normalize_name,
    resolve_symbol,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _in_window(date: str, from_date: str, to_date: str) -> bool:
    """Check if a date string falls within a results window (inclusive)."""
    return bool(date) and from_date <= date <= to_date


def _extract_company_from_heading(heading: str) -> str:
    """Extract company name from P&F heading, stripping pipe-delimited suffixes.

    E.g., 'PCBL Chemical | Mid Cap | Chemicals' -> 'PCBL Chemical'
    """
    return heading.split("|")[0].strip()


def detect_chatter_coverage(
    universe: dict[str, dict],
    aliases: dict[str, str],
    acronyms: dict[str, str],
    from_date: str,
    to_date: str,
) -> dict[str, dict]:
    """Detect which universe companies are covered in Chatter editions.

    Returns: { symbol: { edition_title, edition_date } }
    """
    editions_raw = json.loads((DATA_DIR / "editions.json").read_text())
    quotes_raw = json.loads((DATA_DIR / "quotes.json").read_text())
    companies_raw = json.loads((DATA_DIR / "companies.json").read_text())

    # Build edition lookup filtered to results window
    editions = {}
    for ed in editions_raw:
        if _in_window(ed.get("date", ""), from_date, to_date):
            editions[ed["id"]] = {"title": ed["title"], "date": ed["date"]}

    # Build company_id -> symbol mapping using Zerodha URLs first, then name matching
    auto_acrs = _build_auto_acronyms(universe)
    company_to_symbol: dict[str, str | None] = {}
    for comp in companies_raw:
        comp_id = comp["id"]
        symbol = extract_symbol_from_zerodha_url(comp.get("url"))
        if not symbol or symbol not in universe:
            symbol = resolve_symbol(
                comp.get("name", ""), universe, aliases, acronyms,
                _auto_acronym_cache=auto_acrs,
            )
        company_to_symbol[comp_id] = symbol

    # Find covered companies
    covered: dict[str, dict] = {}
    for quote in quotes_raw:
        edition_id = quote.get("edition_id", "")
        company_id = quote.get("company_id", "")
        if edition_id not in editions:
            continue
        symbol = company_to_symbol.get(company_id)
        if not symbol or symbol not in universe:
            continue
        if symbol not in covered:
            covered[symbol] = {
                "edition_title": editions[edition_id]["title"],
                "edition_date": editions[edition_id]["date"],
            }

    return covered


def detect_pnf_coverage(
    universe: dict[str, dict],
    aliases: dict[str, str],
    acronyms: dict[str, str],
    from_date: str,
    to_date: str,
) -> dict[str, dict]:
    """Detect which universe companies are covered in P&F editions.

    Returns: { symbol: { edition_title, edition_date } }
    """
    pnf_path = DATA_DIR / "tracker_pnf_editions.json"
    if not pnf_path.exists():
        return {}

    pnf_editions = json.loads(pnf_path.read_text())
    auto_acrs = _build_auto_acronyms(universe)
    covered: dict[str, dict] = {}

    for edition in pnf_editions:
        date = edition.get("date", "")
        if not _in_window(date, from_date, to_date):
            continue

        title = edition.get("title", "")
        for heading in edition.get("companies", []):
            company_name = _extract_company_from_heading(heading)
            symbol = resolve_symbol(
                company_name, universe, aliases, acronyms,
                _auto_acronym_cache=auto_acrs,
            )
            if symbol and symbol in universe and symbol not in covered:
                covered[symbol] = {
                    "edition_title": title,
                    "edition_date": date,
                }

    return covered


def _determine_edition_quarter(
    symbols: list[str],
    eligible_by_quarter: dict[str, set[str]],
) -> str | None:
    """Determine which quarter an edition belongs to based on its companies.

    Each edition covers ONE quarter. We look at which quarter the edition's
    companies are eligible in and take the majority (should be unanimous).
    Returns None if no eligible companies found.
    """
    quarter_votes: dict[str, int] = {}
    for sym in symbols:
        for qname, eligible_set in eligible_by_quarter.items():
            if sym in eligible_set:
                quarter_votes[qname] = quarter_votes.get(qname, 0) + 1
    if not quarter_votes:
        return None
    return max(quarter_votes, key=quarter_votes.get)


def detect_chatter_coverage_multi(
    universe: dict[str, dict],
    aliases: dict[str, str],
    acronyms: dict[str, str],
    eligible_by_quarter: dict[str, set[str]],
    from_date: str,
) -> dict[str, dict[str, dict]]:
    """Detect Chatter coverage with per-edition quarter attribution.

    Args:
        eligible_by_quarter: { quarter_name: set of eligible symbols }
        from_date: earliest date to consider editions from

    Returns: { quarter_name: { symbol: { edition_title, edition_date } } }
    """
    editions_raw = json.loads((DATA_DIR / "editions.json").read_text())
    quotes_raw = json.loads((DATA_DIR / "quotes.json").read_text())
    companies_raw = json.loads((DATA_DIR / "companies.json").read_text())

    # Filter editions: date >= from_date (no upper bound)
    editions = {}
    for ed in editions_raw:
        date = ed.get("date", "")
        if date and date >= from_date:
            editions[ed["id"]] = {"title": ed["title"], "date": date}

    # Build company_id -> symbol mapping
    auto_acrs = _build_auto_acronyms(universe)
    company_to_symbol: dict[str, str | None] = {}
    for comp in companies_raw:
        comp_id = comp["id"]
        symbol = extract_symbol_from_zerodha_url(comp.get("url"))
        if not symbol or symbol not in universe:
            symbol = resolve_symbol(
                comp.get("name", ""), universe, aliases, acronyms,
                _auto_acronym_cache=auto_acrs,
            )
        company_to_symbol[comp_id] = symbol

    # Group quotes by edition
    edition_symbols: dict[str, list[str]] = {}
    edition_quote_detail: dict[str, dict[str, str]] = {}  # edition_id -> {symbol: company_id}
    for quote in quotes_raw:
        edition_id = quote.get("edition_id", "")
        company_id = quote.get("company_id", "")
        if edition_id not in editions:
            continue
        symbol = company_to_symbol.get(company_id)
        if not symbol or symbol not in universe:
            continue
        edition_symbols.setdefault(edition_id, []).append(symbol)
        edition_quote_detail.setdefault(edition_id, {})[symbol] = company_id

    # Attribute each edition to a quarter, then record coverage
    covered: dict[str, dict[str, dict]] = {qname: {} for qname in eligible_by_quarter}
    for edition_id, symbols in edition_symbols.items():
        quarter = _determine_edition_quarter(symbols, eligible_by_quarter)
        if not quarter:
            continue
        ed_info = editions[edition_id]
        for sym in symbols:
            if sym in eligible_by_quarter[quarter] and sym not in covered[quarter]:
                covered[quarter][sym] = {
                    "edition_title": ed_info["title"],
                    "edition_date": ed_info["date"],
                }

    return covered


def detect_pnf_coverage_multi(
    universe: dict[str, dict],
    aliases: dict[str, str],
    acronyms: dict[str, str],
    eligible_by_quarter: dict[str, set[str]],
    from_date: str,
) -> dict[str, dict[str, dict]]:
    """Detect P&F coverage with per-edition quarter attribution.

    Args:
        eligible_by_quarter: { quarter_name: set of eligible symbols }
        from_date: earliest date to consider editions from

    Returns: { quarter_name: { symbol: { edition_title, edition_date } } }
    """
    pnf_path = DATA_DIR / "tracker_pnf_editions.json"
    if not pnf_path.exists():
        return {qname: {} for qname in eligible_by_quarter}

    pnf_editions = json.loads(pnf_path.read_text())
    auto_acrs = _build_auto_acronyms(universe)
    covered: dict[str, dict[str, dict]] = {qname: {} for qname in eligible_by_quarter}

    for edition in pnf_editions:
        date = edition.get("date", "")
        if not date or date < from_date:
            continue

        title = edition.get("title", "")
        # Resolve all companies in this edition
        symbols: list[str] = []
        for heading in edition.get("companies", []):
            company_name = _extract_company_from_heading(heading)
            symbol = resolve_symbol(
                company_name, universe, aliases, acronyms,
                _auto_acronym_cache=auto_acrs,
            )
            if symbol and symbol in universe:
                symbols.append(symbol)

        quarter = _determine_edition_quarter(symbols, eligible_by_quarter)
        if not quarter:
            continue

        for sym in symbols:
            if sym in eligible_by_quarter[quarter] and sym not in covered[quarter]:
                covered[quarter][sym] = {
                    "edition_title": title,
                    "edition_date": date,
                }

    return covered


if __name__ == "__main__":
    universe = load_universe()
    aliases = load_aliases()
    acronyms = load_acronyms()
    quarters = json.loads((DATA_DIR / "tracker_quarters.json").read_text())
    q = quarters["quarters"][0]
    from_date, to_date = q["results_window"]

    print(f"Detecting coverage for {q['name']}...")
    chatter = detect_chatter_coverage(universe, aliases, acronyms, from_date, to_date)
    pnf = detect_pnf_coverage(universe, aliases, acronyms, from_date, to_date)

    print(f"Chatter covered: {len(chatter)} companies")
    print(f"P&F covered: {len(pnf)} companies")

    for sym, info in list(chatter.items())[:5]:
        print(f"  Chatter: {sym} in '{info['edition_title']}' ({info['edition_date']})")
    for sym, info in list(pnf.items())[:5]:
        print(f"  P&F: {sym} in '{info['edition_title']}' ({info['edition_date']})")
