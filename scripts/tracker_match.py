#!/usr/bin/env python3
"""Shared company name matching utilities for the coverage tracker."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Allow imports from scripts/ when run standalone
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

LEGAL_SUFFIXES = {
    "limited", "ltd", "inc", "corp", "corporation", "company",
    "co", "private", "pvt", "plc", "sa", "nv", "ag",
}


def _read_json(path: Path, default: object = None) -> object:
    if not path.exists():
        return default if default is not None else {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_universe() -> dict[str, dict]:
    """Load tracker universe keyed by NSE symbol."""
    raw = _read_json(DATA_DIR / "tracker_universe.json", [])
    if isinstance(raw, list):
        return {entry["symbol"]: entry for entry in raw if entry.get("symbol")}
    return {}


def load_aliases() -> dict[str, str]:
    """Load common-name → NSE symbol aliases."""
    return _read_json(DATA_DIR / "tracker_aliases.json", {})


def load_acronyms() -> dict[str, str]:
    """Load acronym → full company name map."""
    return _read_json(DATA_DIR / "tracker_acronyms.json", {})


def normalize_name(name: str) -> str:
    """Normalize company name: lowercase, strip legal suffixes, collapse whitespace."""
    tokens = re.findall(r"[a-z0-9&]+", name.lower())
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def extract_symbol_from_zerodha_url(url: str | None) -> str | None:
    """Extract NSE symbol from a Zerodha market URL like zerodha.com/markets/stocks/NSE/HDFCBANK/."""
    if not url:
        return None
    match = re.search(r"/markets/stocks/NSE/([A-Z0-9&-]+)/?", url)
    return match.group(1) if match else None


def resolve_symbol(
    name: str,
    universe: dict[str, dict],
    aliases: dict[str, str],
    acronyms: dict[str, str],
) -> str | None:
    """Resolve a company name to an NSE symbol using 5-layer matching.

    Layers:
    1. Direct symbol match (name IS a known symbol)
    2. Alias lookup (brand name → symbol)
    3. Acronym expansion → then match expanded name
    4. Normalized name match against universe company names
    5. None (unmatched)
    """
    name_stripped = name.strip()
    name_upper = name_stripped.upper()
    name_lower = name_stripped.lower()

    # Layer 1: Direct symbol match
    if name_upper in universe:
        return name_upper

    # Layer 2: Alias lookup
    alias_symbol = aliases.get(name_lower)
    if alias_symbol and alias_symbol in universe:
        return alias_symbol

    # Layer 3: Acronym expansion
    expanded = acronyms.get(name_upper)
    if expanded:
        expanded_norm = normalize_name(expanded)
        for symbol, entry in universe.items():
            if normalize_name(entry.get("name", "")) == expanded_norm:
                return symbol

    # Layer 4: Normalized name match
    name_norm = normalize_name(name_stripped)
    if not name_norm:
        return None
    for symbol, entry in universe.items():
        if normalize_name(entry.get("name", "")) == name_norm:
            return symbol

    return None
