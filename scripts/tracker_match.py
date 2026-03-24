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

# Words to ignore when tokenising names for fuzzy matching
STOP_WORDS = {"the", "a", "an", "of", "and", "&"}

# Common abbreviations found in P&F headings → full form in universe names
ABBREVIATIONS: dict[str, str] = {
    "inds": "industries",
    "ins": "insurance",
    "internatl": "international",
    "intl": "international",
    "fin": "finance",
    "invest": "investment",
    "engg": "engineering",
    "ent": "enterprises",
    "mfg": "manufacturing",
    "dev": "development",
    "mgmt": "management",
    "infra": "infrastructure",
    "hind": "hindustan",
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


def _build_auto_acronyms(universe: dict[str, dict]) -> dict[str, str]:
    """Auto-generate acronyms from universe company names.

    E.g. 'The Indian Hotels Company Limited' → 'IHCL'
    Skips articles; keeps legal suffixes (people include them in acronyms).
    Drops ambiguous acronyms that map to multiple symbols.
    """
    skip = STOP_WORDS | {"india"}
    counts: dict[str, list[str]] = {}
    for symbol, entry in universe.items():
        tokens = re.findall(r"[a-z0-9]+", entry.get("name", "").lower())
        significant = [t for t in tokens if t not in skip]
        if len(significant) < 2:
            continue
        acronym = "".join(t[0] for t in significant).upper()
        counts.setdefault(acronym, []).append(symbol)
    # Only keep unambiguous acronyms
    return {acr: syms[0] for acr, syms in counts.items() if len(syms) == 1}


def _token_set(name: str) -> set[str]:
    """Tokenise a name, stripping stop words and legal suffixes."""
    tokens = re.findall(r"[a-z0-9&]+", name.lower())
    return {t for t in tokens if t not in STOP_WORDS and t not in LEGAL_SUFFIXES}


def _tok_matches(input_tok: str, universe_tok: str) -> bool:
    """Check if an input token matches a universe token.

    Accepts: exact match, prefix (≥3 chars), or known abbreviation expansion.
    """
    if input_tok == universe_tok:
        return True
    # Prefix: 'hotel' matches 'hotels', 'exch' matches 'exchange'
    if len(input_tok) >= 3 and universe_tok.startswith(input_tok):
        return True
    # Reverse prefix: 'hotels' in heading, 'hotel' in universe (unlikely but safe)
    if len(universe_tok) >= 3 and input_tok.startswith(universe_tok):
        return True
    # Abbreviation expansion: 'inds' → 'industries'
    expanded = ABBREVIATIONS.get(input_tok)
    if expanded and expanded == universe_tok:
        return True
    return False


def _token_partial_match(
    name: str,
    universe: dict[str, dict],
) -> str | None:
    """Layer 6: token-based partial matching with prefix + abbreviation support.

    All input tokens must match some universe-name token.  Among matches,
    pick the tightest (fewest extra universe tokens).  Requires ≥ 2 input
    tokens to avoid spurious single-word hits.
    """
    inp_tokens = _token_set(name)
    if len(inp_tokens) < 2:
        return None

    best_symbol: str | None = None
    best_extra = float("inf")

    for symbol, entry in universe.items():
        uni_tokens = _token_set(entry.get("name", ""))
        if not uni_tokens:
            continue

        # Every input token must match at least one universe token
        all_match = True
        for itok in inp_tokens:
            if not any(_tok_matches(itok, utok) for utok in uni_tokens):
                all_match = False
                break

        if all_match:
            extra = len(uni_tokens) - len(inp_tokens)
            if extra < best_extra:
                best_extra = extra
                best_symbol = symbol
            elif extra == best_extra and best_symbol is not None:
                # Ambiguous — two companies with the same tightness
                best_symbol = None

    return best_symbol


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
    *,
    _auto_acronym_cache: dict[str, str] | None = None,
) -> str | None:
    """Resolve a company name to an NSE symbol using 7-layer matching.

    Layers:
    1. Direct symbol match (name IS a known symbol)
    2. Alias lookup (brand name → symbol)
    3. Acronym expansion (manual acronyms.json)
    4. Normalized exact name match against universe
    5. Auto-generated acronym match (derived from universe names)
    6. Token-based partial match (prefix + abbreviation support)
    7. None (unmatched)
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

    # Layer 3: Manual acronym expansion
    expanded = acronyms.get(name_upper)
    if expanded:
        expanded_norm = normalize_name(expanded)
        for symbol, entry in universe.items():
            if normalize_name(entry.get("name", "")) == expanded_norm:
                return symbol

    # Layer 4: Normalized exact name match
    name_norm = normalize_name(name_stripped)
    if not name_norm:
        return None
    for symbol, entry in universe.items():
        if normalize_name(entry.get("name", "")) == name_norm:
            return symbol

    # Layer 5: Auto-generated acronym match
    if _auto_acronym_cache is not None:
        auto_acrs = _auto_acronym_cache
    else:
        auto_acrs = _build_auto_acronyms(universe)
    auto_symbol = auto_acrs.get(name_upper)
    if auto_symbol and auto_symbol in universe:
        return auto_symbol

    # Layer 6: Token-based partial match
    token_symbol = _token_partial_match(name_stripped, universe)
    if token_symbol:
        return token_symbol

    return None
