#!/usr/bin/env python3
"""Scrape The Chatter archive from Substack and extract company quotes.

Outputs JSON files to data/:
- editions.json
- companies.json
- quotes.json

No external dependencies (stdlib only).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

BASE_URL = "https://thechatter.zerodha.com/"
SITEMAP_URL = urljoin(BASE_URL, "sitemap")
OUTPUT_DIR = "data"
ZERODHA_STOCKS_BASE = "https://zerodha.com/markets/stocks"
KITE_INSTRUMENTS_URL = "https://api.kite.trade/instruments"
MANUAL_MARKET_URLS_FILE = "manual_market_urls.json"
INDIAN_LISTED_MANDATORY_FILE = "indian_listed_mandatory.json"
LINK_AUDIT_REPORT_FILE = "link_audit_report.json"
COMPANY_MENTIONS_FILE = "company_mentions.json"
NON_COMPANY_RULES_FILE = "non_company_rules.json"
ZERODHA_NSE_INDEX_FILE = "zerodha_nse_stock_index.json"
ZERODHA_NSE_INSTRUMENTS_FILE = "zerodha_nse_instruments_index.json"
ALLOWED_EXCHANGES = {"NSE", "BSE"}
FINAL_MARKET_EXCHANGE = "NSE"

TITLE_INCLUDE = "the chatter"
TITLE_EXCLUDE = ["points and figures", "plotlines"]
SECTOR_HEADING_EXACT = {
    "banking and financial services",
    "capital goods and engineering",
    "cement and construction materials",
    "consumer appliances and retail",
    "food and beverage",
    "global",
    "hospitality and hotels",
    "new years edition",
    "pharmaceuticals and chemicals",
    "real estate",
    "textiles",
}
SECTOR_HEADING_TOKEN_SET = {
    "aerospace",
    "airlines",
    "appliances",
    "auto",
    "automobile",
    "automobiles",
    "banking",
    "beverage",
    "capital",
    "cement",
    "chemicals",
    "communication",
    "construction",
    "consumer",
    "defence",
    "engineering",
    "estate",
    "financial",
    "food",
    "goods",
    "healthcare",
    "hospitality",
    "hotels",
    "infrastructure",
    "insurance",
    "materials",
    "metals",
    "mining",
    "oil",
    "pharmaceuticals",
    "power",
    "real",
    "retail",
    "services",
    "telecom",
    "textiles",
    "transport",
    "utilities",
}
MARKET_LOOKUP_STOPWORDS = {
    "limited",
    "ltd",
    "inc",
    "corp",
    "corporation",
    "company",
    "co",
    "private",
    "pvt",
    "plc",
    "ag",
    "sa",
    "group",
    "holdings",
    "holding",
    "technologies",
    "technology",
    "india",
    "ind",
}
COMPANY_HINT_TOKENS = {
    "bank",
    "bancorp",
    "bancshares",
    "beverages",
    "bio",
    "biosciences",
    "capital",
    "chemicals",
    "company",
    "communications",
    "corp",
    "corporation",
    "energy",
    "engineering",
    "financial",
    "foods",
    "group",
    "holding",
    "holdings",
    "inc",
    "industries",
    "insurance",
    "international",
    "labs",
    "limited",
    "ltd",
    "motors",
    "pharma",
    "pharmaceuticals",
    "plc",
    "private",
    "pvt",
    "retail",
    "sa",
    "systems",
    "technologies",
    "technology",
}
SENTENCE_START_TOKENS = {
    "we",
    "we've",
    "our",
    "this",
    "that",
    "these",
    "those",
    "broader",
    "sectoral",
    "check",
    "have",
    "introducing",
    "given",
    "are",
}


@dataclass
class Edition:
    id: str
    title: str
    date: str
    url: str


@dataclass
class Company:
    id: str
    name: str
    url: Optional[str]


@dataclass
class Quote:
    id: str
    edition_id: str
    company_id: str
    sector: Optional[str]
    text: str
    context: Optional[str]
    speaker: Optional[str]
    source_url: str


@dataclass
class CompanyMention:
    id: str
    edition_id: str
    company_id: str
    sector: Optional[str]
    source_url: str
    mention_type: str


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "CompanyChatterBot/0.1"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def extract_links(html: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)


def parse_sitemap_years(html: str) -> list[str]:
    years = []
    for href in extract_links(html):
        if "/sitemap/" in href:
            years.append(urljoin(BASE_URL, href))
    return sorted(set(years))


def parse_sitemap_posts(html: str) -> list[str]:
    posts = []
    for href in extract_links(html):
        full = urljoin(BASE_URL, href)
        if urlparse(full).path.startswith("/p/"):
            posts.append(full)
    return sorted(set(posts))


def normalize_company_name(text: str) -> str:
    text = text.strip()
    for sep in [" — ", " - ", " | "]:
        if sep in text:
            text = text.split(sep, 1)[0].strip()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    return text


def parse_company_heading(text: str) -> tuple[str, Optional[str]]:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return "", None

    if "|" in cleaned:
        parts = [part.strip() for part in cleaned.split("|") if part.strip()]
        if parts:
            company_name = normalize_company_name(parts[0])
            sector_hint = parts[-1] if len(parts) >= 2 else None
            return company_name, sector_hint

    return normalize_company_name(cleaned), None


def normalize_company_url(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    return urljoin(BASE_URL, href).strip()


def canonicalize_zerodha_stock_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"zerodha.com", "thechatter.zerodha.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3:
        return None
    if parts[0].lower() != "markets" or parts[1].lower() != "stocks":
        return None

    # Zerodha stock URLs are expected in one of these shapes:
    # - /markets/stocks/<slug>/
    # - /markets/stocks/<exchange>/<symbol>/
    tail = parts[2:]
    safe_segment = re.compile(r"^[A-Za-z0-9._-]+$")
    safe_symbol = re.compile(r"^[A-Za-z0-9._&-]+$")
    normalized_tail: list[str]
    if len(tail) == 1:
        slug = tail[0]
        if not safe_segment.fullmatch(slug):
            return None
        lowered = slug.lower()
        if lowered in {"search"} or lowered.startswith(("http", "www")):
            return None
        normalized_tail = [slug]
    elif len(tail) == 2:
        exchange = tail[0].upper()
        symbol = tail[1].upper()
        if exchange not in ALLOWED_EXCHANGES:
            return None
        if not safe_symbol.fullmatch(symbol):
            return None
        normalized_tail = [exchange, symbol]
    else:
        return None

    normalized_path = "/" + "/".join(["markets", "stocks", *normalized_tail]) + "/"
    return urlunparse(("https", "zerodha.com", normalized_path, "", "", ""))


def parse_zerodha_stock_url_parts(url: Optional[str]) -> Optional[dict[str, str]]:
    canonical = canonicalize_zerodha_stock_url(url)
    if not canonical:
        return None

    parts = [part for part in urlparse(canonical).path.split("/") if part]
    if len(parts) not in {3, 4}:
        return None
    if parts[0].lower() != "markets" or parts[1].lower() != "stocks":
        return None

    if len(parts) == 3:
        return {
            "kind": "slug",
            "canonical_url": canonical,
            "slug": parts[2],
        }

    exchange = parts[2].upper()
    symbol = parts[3].upper()
    if exchange not in ALLOWED_EXCHANGES:
        return None
    if not re.fullmatch(r"[A-Z0-9._&-]+", symbol):
        return None
    return {
        "kind": "market",
        "canonical_url": canonical,
        "exchange": exchange,
        "symbol": symbol,
        "market_key": f"{exchange}:{symbol}",
    }


def market_key_from_zerodha_stock_url(url: Optional[str]) -> Optional[str]:
    parts = parse_zerodha_stock_url_parts(url)
    if not parts or parts["kind"] != "market":
        return None
    return parts["market_key"]


def stock_symbol_from_zerodha_stock_url(url: Optional[str]) -> Optional[str]:
    parts = parse_zerodha_stock_url_parts(url)
    if not parts or parts["kind"] != "market":
        return None
    return parts["symbol"]


def slug_query_from_zerodha_stock_url(url: Optional[str]) -> Optional[str]:
    parts = parse_zerodha_stock_url_parts(url)
    if not parts or parts["kind"] != "slug":
        return None
    slug = parts["slug"].strip()
    if not slug:
        return None
    return slug.replace("-", " ")


def nse_stock_url_for_symbol(symbol: str, nse_urls_by_symbol: Optional[dict[str, str]] = None) -> Optional[str]:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return None
    if not re.fullmatch(r"[A-Z0-9._&-]+", normalized_symbol):
        return None
    if nse_urls_by_symbol and normalized_symbol in nse_urls_by_symbol:
        return nse_urls_by_symbol[normalized_symbol]
    return canonicalize_zerodha_stock_url(f"{ZERODHA_STOCKS_BASE}/{FINAL_MARKET_EXCHANGE}/{normalized_symbol}/")


def is_nse_stock_url(url: Optional[str]) -> bool:
    parts = parse_zerodha_stock_url_parts(url)
    if not parts or parts["kind"] != "market":
        return False
    return parts["exchange"] == FINAL_MARKET_EXCHANGE


def is_market_stock_url(url: Optional[str]) -> bool:
    parts = parse_zerodha_stock_url_parts(url)
    if not parts or parts["kind"] != "market":
        return False
    return parts["exchange"] in ALLOWED_EXCHANGES


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_manual_market_urls() -> dict[str, dict[str, str]]:
    path = Path(OUTPUT_DIR) / MANUAL_MARKET_URLS_FILE
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object keyed by company id")

    parsed: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        company_key = str(key).strip()
        if not company_key:
            raise ValueError(f"{path} contains an empty company key")
        if not isinstance(value, dict):
            raise ValueError(f"{path} entry for '{company_key}' must be a JSON object")

        override_url = canonicalize_zerodha_stock_url(str(value.get("url") or "").strip())
        if not override_url:
            raise ValueError(f"{path} entry for '{company_key}' has invalid Zerodha stocks URL")

        reason = str(value.get("reason") or "").strip()
        verified_on = str(value.get("verified_on") or "").strip()
        if not reason or not verified_on:
            raise ValueError(f"{path} entry for '{company_key}' must include reason and verified_on")
        try:
            datetime.fromisoformat(verified_on)
        except ValueError as exc:
            raise ValueError(f"{path} entry for '{company_key}' has invalid verified_on date") from exc

        parsed[company_key] = {
            "url": override_url,
            "reason": reason,
            "verified_on": verified_on,
        }
    return parsed


def load_mandatory_indian_listed_links() -> dict[str, dict[str, str]]:
    path = Path(OUTPUT_DIR) / INDIAN_LISTED_MANDATORY_FILE
    raw = _read_json(path, [])
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array")

    parsed: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path} entry {index} must be a JSON object")
        company_key = str(item.get("company_key") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        expected_url = canonicalize_zerodha_stock_url(str(item.get("expected_url") or "").strip())

        if not company_key or not display_name or not expected_url:
            raise ValueError(
                f"{path} entry {index} must include company_key, display_name, and expected_url"
            )
        if company_key in parsed:
            raise ValueError(f"{path} has duplicate company_key '{company_key}'")

        parsed[company_key] = {
            "display_name": display_name,
            "expected_url": expected_url,
        }
    return parsed


def load_zerodha_nse_stock_index() -> dict[str, str]:
    path = Path(OUTPUT_DIR) / ZERODHA_NSE_INDEX_FILE
    if not path.exists():
        refresh_script = BASE_DIR / "scripts" / "refresh_zerodha_nse_index.py"
        if not refresh_script.exists():
            raise ValueError(f"{path} is missing and refresh script was not found at {refresh_script}")
        print(f"{path} missing; refreshing from {refresh_script.name}...")
        subprocess.run([sys.executable, str(refresh_script)], cwd=str(BASE_DIR), check=True)

    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object")
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain an 'entries' array")

    urls_by_symbol: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        raw_url = canonicalize_zerodha_stock_url(str(entry.get("url") or "").strip())
        if not symbol or not raw_url:
            continue
        parts = parse_zerodha_stock_url_parts(raw_url)
        if not parts or parts["kind"] != "market":
            continue
        if parts["exchange"] != FINAL_MARKET_EXCHANGE:
            continue
        if parts["symbol"] != symbol:
            continue
        urls_by_symbol[symbol] = parts["canonical_url"]

    if not urls_by_symbol:
        raise ValueError(f"{path} did not contain any valid NSE stock entries")
    return urls_by_symbol


def _parse_nse_instrument_candidates(rows: list[dict[str, object]], nse_urls_by_symbol: dict[str, str]) -> list[dict[str, str]]:
    deduped: dict[str, dict[str, str]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or symbol not in nse_urls_by_symbol:
            continue

        display_name = str(row.get("display_name") or "").strip()
        if not display_name:
            display_name = symbol

        existing = deduped.get(symbol)
        if existing and len(existing["display_name"]) >= len(display_name):
            continue
        deduped[symbol] = {
            "display_name": display_name,
            "exchange": FINAL_MARKET_EXCHANGE,
            "symbol": symbol,
            "slug": slugify(display_name).replace("-", " "),
        }

    return [deduped[symbol] for symbol in sorted(deduped)]


def load_nse_market_candidates(nse_urls_by_symbol: dict[str, str]) -> list[dict[str, str]]:
    cache_path = Path(OUTPUT_DIR) / ZERODHA_NSE_INSTRUMENTS_FILE
    parsed_rows: list[dict[str, object]] = []

    try:
        csv_payload = fetch(KITE_INSTRUMENTS_URL)
        csv_reader = csv.DictReader(csv_payload.splitlines())
        for row in csv_reader:
            exchange = str(row.get("exchange") or "").strip().upper()
            segment = str(row.get("segment") or "").strip().upper()
            instrument_type = str(row.get("instrument_type") or "").strip().upper()
            symbol = str(row.get("tradingsymbol") or "").strip().upper()
            name = str(row.get("name") or "").strip()
            if exchange != FINAL_MARKET_EXCHANGE:
                continue
            if segment != FINAL_MARKET_EXCHANGE:
                continue
            if instrument_type not in {"EQ", "BE"}:
                continue
            if not symbol:
                continue
            parsed_rows.append(
                {
                    "symbol": symbol,
                    "display_name": name or symbol,
                }
            )

        _write_json(
            cache_path,
            {
                "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "source_url": KITE_INSTRUMENTS_URL,
                "entry_count": len(parsed_rows),
                "entries": parsed_rows,
            },
        )
    except Exception:
        cached = _read_json(cache_path, {})
        entries = cached.get("entries", []) if isinstance(cached, dict) else []
        if isinstance(entries, list):
            for row in entries:
                if isinstance(row, dict):
                    parsed_rows.append(row)

    return _parse_nse_instrument_candidates(parsed_rows, nse_urls_by_symbol)


def _normalize_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def load_non_company_rules() -> dict[str, object]:
    path = Path(OUTPUT_DIR) / NON_COMPANY_RULES_FILE
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object")

    exact_names = raw.get("exact_names", [])
    name_patterns = raw.get("name_patterns", [])
    allow_names = raw.get("allow_names", [])
    if not isinstance(exact_names, list) or not isinstance(name_patterns, list) or not isinstance(allow_names, list):
        raise ValueError(f"{path} must contain list values for exact_names, name_patterns, and allow_names")

    compiled_patterns: list[re.Pattern[str]] = []
    for index, pattern in enumerate(name_patterns):
        pattern_text = str(pattern).strip()
        if not pattern_text:
            continue
        try:
            compiled_patterns.append(re.compile(pattern_text, flags=re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"{path} has invalid regex at name_patterns[{index}]") from exc

    return {
        "exact_name_keys": {_normalize_name_key(str(name)) for name in exact_names if str(name).strip()},
        "allow_name_keys": {_normalize_name_key(str(name)) for name in allow_names if str(name).strip()},
        "name_patterns": compiled_patterns,
    }


def canonical_company_id(name: str, href: Optional[str]) -> str:
    # Keep extraction-time IDs name-based so a noisy/bad heading URL does not
    # collapse different companies into a single identity.
    _ = href
    return slugify(name)


def _lookup_tokens(text: str) -> list[str]:
    normalized = text.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return [token for token in normalized.split() if token and token not in MARKET_LOOKUP_STOPWORDS]


def _normalize_display_name(raw: object) -> str:
    if isinstance(raw, list):
        return " ".join([str(item).strip() for item in raw if str(item).strip()])
    return str(raw or "").strip()


def _iter_zerodha_candidates(payload: object):
    if not isinstance(payload, dict):
        return
    for bucket in ("companies", "brands"):
        items = payload.get(bucket, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            display_name = _normalize_display_name(item.get("display_name") or item.get("name"))
            exchange = str(item.get("exchange") or "").strip().upper()
            symbol = str(item.get("symbol") or "").strip().upper()
            slug = str(item.get("slug") or "").strip()
            if display_name and symbol and exchange in ALLOWED_EXCHANGES:
                yield {
                    "display_name": display_name,
                    "exchange": exchange,
                    "symbol": symbol,
                    "slug": slug,
                }


def _common_prefix_len(left: str, right: str) -> int:
    if not left or not right:
        return 0
    prefix = 0
    for lch, rch in zip(left, right):
        if lch != rch:
            break
        prefix += 1
    return prefix


def _candidate_match_features(company_name: str, company_id: str, candidate: dict[str, str]) -> dict[str, object]:
    company_tokens = _lookup_tokens(company_name)
    company_fp = "".join(company_tokens) or re.sub(r"[^a-z0-9]+", "", company_id.lower())

    display_tokens = _lookup_tokens(candidate["display_name"])
    slug_tokens = _lookup_tokens(candidate.get("slug", ""))
    display_fp = "".join(display_tokens)
    slug_fp = "".join(slug_tokens)

    similarity_display = SequenceMatcher(None, company_fp, display_fp).ratio() if company_fp and display_fp else 0.0
    similarity_slug = SequenceMatcher(None, company_fp, slug_fp).ratio() if company_fp and slug_fp else 0.0
    token_overlap = len(set(company_tokens).intersection(set(display_tokens).union(slug_tokens)))

    return {
        "company_tokens": company_tokens,
        "company_fp": company_fp,
        "display_tokens": display_tokens,
        "display_fp": display_fp,
        "slug_fp": slug_fp,
        "token_overlap": token_overlap,
        "similarity_display": similarity_display,
        "similarity_slug": similarity_slug,
        "similarity": max(similarity_display, similarity_slug),
        "prefix_len": max(_common_prefix_len(company_fp, display_fp), _common_prefix_len(company_fp, slug_fp)),
        "exact_fp": bool(company_fp and (company_fp == display_fp or company_fp == slug_fp)),
    }


def _is_confident_market_match(features: dict[str, object]) -> bool:
    company_tokens = features["company_tokens"]
    company_token_count = len(company_tokens)
    token_overlap = int(features["token_overlap"])
    similarity = float(features["similarity"])
    prefix_len = int(features["prefix_len"])
    exact_fp = bool(features["exact_fp"])

    if exact_fp:
        return True
    if token_overlap >= 1 and similarity >= 0.63:
        return True
    # Handles compressed naming variants like "L&T Mindtree" -> "LTIMindtree".
    if company_token_count >= 2 and similarity >= 0.88 and prefix_len >= 2:
        return True
    # Single-token names must be very close to avoid bad matches (e.g. Netflix -> Nettlinx).
    if company_token_count == 1 and similarity >= 0.95 and prefix_len >= 5:
        return True
    return False


def _candidate_match_score(candidate: dict[str, str], features: dict[str, object]) -> int:
    company_tokens = features["company_tokens"]
    company_fp = str(features["company_fp"])
    display_tokens = features["display_tokens"]
    display_fp = str(features["display_fp"])
    slug_fp = str(features["slug_fp"])
    similarity_display = float(features["similarity_display"])
    similarity_slug = float(features["similarity_slug"])
    token_overlap = int(features["token_overlap"])
    exact_fp = bool(features["exact_fp"])

    score = 0
    if exact_fp and display_fp == company_fp:
        score += 140
    if exact_fp and slug_fp == company_fp:
        score += 130

    if company_fp and display_fp and (display_fp.startswith(company_fp) or company_fp.startswith(display_fp)):
        score += 70
    if company_fp and slug_fp and (slug_fp.startswith(company_fp) or company_fp.startswith(slug_fp)):
        score += 70

    score += int(similarity_display * 120)
    score += int(similarity_slug * 90)

    if company_tokens and display_tokens:
        score += token_overlap * 18
        if company_tokens[0] == display_tokens[0]:
            score += 25
        elif token_overlap == 0:
            score -= 20

    if candidate["exchange"] == "NSE":
        score += 8
    return score


def resolve_market_url_for_company(
    company: Company,
    nse_urls_by_symbol: dict[str, str],
    nse_market_candidates: list[dict[str, str]],
    slug_hint_url: Optional[str] = None,
    symbol_hint: Optional[str] = None,
) -> Optional[str]:
    if company.url and is_nse_stock_url(company.url):
        normalized_existing = canonicalize_zerodha_stock_url(company.url)
        if normalized_existing:
            return normalized_existing
    if company.id == "global":
        return None

    normalized_symbol_hint = str(symbol_hint or "").strip().upper()
    if normalized_symbol_hint and normalized_symbol_hint in nse_urls_by_symbol:
        return nse_urls_by_symbol[normalized_symbol_hint]

    best_score = -1
    best_url = None
    tried_name_hints = set()
    lookup_name_hints = [company.name, company.id.replace("-", " ")]
    slug_hint_query = slug_query_from_zerodha_stock_url(slug_hint_url)
    if slug_hint_query:
        lookup_name_hints.append(slug_hint_query)

    for name_hint in lookup_name_hints:
        hint_key = name_hint.strip().lower()
        if not hint_key or hint_key in tried_name_hints:
            continue
        tried_name_hints.add(hint_key)

        for candidate in nse_market_candidates:
            symbol = str(candidate["symbol"]).upper()
            if symbol not in nse_urls_by_symbol:
                continue
            features = _candidate_match_features(name_hint, company.id, candidate)
            if not _is_confident_market_match(features):
                continue
            score = _candidate_match_score(candidate, features) + (6 if hint_key == company.name.lower() else 0)
            candidate_url = nse_urls_by_symbol[symbol]
            if not candidate_url:
                continue
            if score > best_score:
                best_score = score
                best_url = candidate_url

    if best_url and best_score >= 120:
        return best_url
    return None


def enrich_company_urls(
    companies: dict[str, Company],
    manual_overrides: dict[str, dict[str, str]],
    mandatory_links: dict[str, dict[str, str]],
    nse_urls_by_symbol: dict[str, str],
    nse_market_candidates: list[dict[str, str]],
) -> dict[str, object]:
    existing_nse_kept = 0
    existing_bse_converted = 0
    existing_bse_removed = 0
    existing_slug_removed = 0
    resolved_auto = 0
    resolved_manual = 0
    unresolved = 0
    rejected_existing_urls: list[dict[str, str]] = []
    manual_overrides_applied: list[dict[str, str]] = []
    manual_overrides_missing_company: list[dict[str, str]] = []
    manual_overrides_invalid_target: list[dict[str, str]] = []

    for company in sorted(companies.values(), key=lambda c: c.id):
        slug_lookup_hint: Optional[str] = None
        symbol_lookup_hint: Optional[str] = None
        normalized_existing = canonicalize_zerodha_stock_url(company.url) if company.url else None
        if company.url and not normalized_existing:
            rejected_existing_urls.append(
                {
                    "company_key": company.id,
                    "display_name": company.name,
                    "existing_url": company.url,
                    "reason": "not_zerodha_stocks_url",
                }
            )
            company.url = None
        elif normalized_existing:
            existing_parts = parse_zerodha_stock_url_parts(normalized_existing)
            if existing_parts and existing_parts["kind"] == "market":
                existing_exchange = existing_parts["exchange"]
                existing_symbol = existing_parts["symbol"]
                symbol_lookup_hint = existing_symbol

                if existing_exchange == FINAL_MARKET_EXCHANGE:
                    company.url = existing_parts["canonical_url"]
                    existing_nse_kept += 1
                elif existing_exchange != FINAL_MARKET_EXCHANGE and existing_symbol in nse_urls_by_symbol:
                    company.url = nse_urls_by_symbol[existing_symbol]
                    existing_bse_converted += 1
                else:
                    company.url = None
                    if existing_exchange == "BSE":
                        existing_bse_removed += 1
                        rejected_existing_urls.append(
                            {
                                "company_key": company.id,
                                "display_name": company.name,
                                "existing_url": existing_parts["canonical_url"],
                                "reason": "bse_symbol_missing_in_nse_sitemap",
                            }
                        )
                    else:
                        rejected_existing_urls.append(
                            {
                                "company_key": company.id,
                                "display_name": company.name,
                                "existing_url": existing_parts["canonical_url"],
                                "reason": "nse_symbol_missing_in_nse_sitemap",
                            }
                        )
            else:
                # Slug-shaped stock URLs are stale; force upgrade via market lookup.
                slug_lookup_hint = normalized_existing
                company.url = None
                existing_slug_removed += 1

        override = manual_overrides.get(company.id)
        if override:
            override_url = canonicalize_zerodha_stock_url(override["url"])
            override_parts = parse_zerodha_stock_url_parts(override_url) if override_url else None
            if override_parts and override_parts["kind"] == "market":
                normalized_override_url = override_parts["canonical_url"]
                if company.url != normalized_override_url:
                    manual_overrides_applied.append(
                        {
                            "company_key": company.id,
                            "display_name": company.name,
                            "url": normalized_override_url,
                            "reason": override["reason"],
                            "verified_on": override["verified_on"],
                        }
                    )
                    resolved_manual += 1
                company.url = normalized_override_url
            else:
                manual_overrides_invalid_target.append(
                    {
                        "company_key": company.id,
                        "display_name": company.name,
                        "url": override["url"],
                        "reason": "override_url_not_market_symbol_format",
                    }
                )
                company.url = None

        if company.url:
            continue

        resolved_url = resolve_market_url_for_company(
            company,
            nse_urls_by_symbol,
            nse_market_candidates,
            slug_lookup_hint,
            symbol_lookup_hint,
        )
        if resolved_url and is_market_stock_url(resolved_url):
            company.url = resolved_url
            resolved_auto += 1
        else:
            company.url = None
            unresolved += 1
        time.sleep(0.05)

    for company_key, override in manual_overrides.items():
        if company_key not in companies:
            manual_overrides_missing_company.append(
                {
                    "company_key": company_key,
                    "reason": override["reason"],
                    "verified_on": override["verified_on"],
                }
            )

    mandatory_missing: list[dict[str, str]] = []
    mandatory_mismatched: list[dict[str, str]] = []
    for company_key, requirement in mandatory_links.items():
        company = companies.get(company_key)
        if not company:
            mandatory_missing.append(
                {
                    "company_key": company_key,
                    "display_name": requirement["display_name"],
                    "expected_url": requirement["expected_url"],
                    "reason": "company_not_found_in_latest_scrape",
                }
            )
            continue

        company_url = canonicalize_zerodha_stock_url(company.url)
        if not company_url or not is_nse_stock_url(company_url):
            mandatory_missing.append(
                {
                    "company_key": company_key,
                    "display_name": company.name,
                    "expected_url": requirement["expected_url"],
                    "reason": "resolved_without_nse_zerodha_url",
                }
            )
            continue

        if company_url != requirement["expected_url"]:
            mandatory_mismatched.append(
                {
                    "company_key": company_key,
                    "display_name": company.name,
                    "expected_url": requirement["expected_url"],
                    "actual_url": company_url,
                    "reason": "url_mismatch",
                }
            )

    linked = sum(1 for company in companies.values() if is_market_stock_url(company.url))
    non_mandatory_unlinked = [
        {"company_key": company.id, "display_name": company.name}
        for company in sorted(companies.values(), key=lambda c: c.name.lower())
        if not is_market_stock_url(company.url) and company.id not in mandatory_links
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "counts": {
            "total_companies": len(companies),
            "linked_companies": linked,
            "unlinked_companies": len(companies) - linked,
            "mandatory_total": len(mandatory_links),
            "mandatory_missing": len(mandatory_missing),
            "mandatory_mismatched": len(mandatory_mismatched),
            "existing_nse_links_kept": existing_nse_kept,
            "existing_bse_links_converted_to_nse": existing_bse_converted,
            "existing_bse_links_removed": existing_bse_removed,
            "existing_slug_links_removed": existing_slug_removed,
            "manual_overrides_applied": resolved_manual,
            "auto_resolved": resolved_auto,
            "unresolved_after_resolution": unresolved,
            "nse_sitemap_symbols": len(nse_urls_by_symbol),
        },
        "mandatory_missing": mandatory_missing,
        "mandatory_mismatched": mandatory_mismatched,
        "manual_overrides_applied": manual_overrides_applied,
        "manual_overrides_missing_company": manual_overrides_missing_company,
        "manual_overrides_invalid_target": manual_overrides_invalid_target,
        "rejected_existing_urls": rejected_existing_urls,
        "non_mandatory_unlinked": non_mandatory_unlinked,
    }

    print(f"Kept existing NSE links: {existing_nse_kept}")
    print(f"Converted BSE->NSE links: {existing_bse_converted}")
    print(f"Removed BSE-only links: {existing_bse_removed}")
    print(f"Removed slug-style links: {existing_slug_removed}")
    print(f"Applied manual overrides: {resolved_manual}")
    print(f"Resolved via market search: {resolved_auto}")
    print(f"Still without market URL: {unresolved}")
    print(f"Mandatory missing: {len(mandatory_missing)}")
    print(f"Mandatory mismatched: {len(mandatory_mismatched)}")

    return report


def write_link_audit_report(report: dict[str, object]) -> None:
    _write_json(Path(OUTPUT_DIR) / LINK_AUDIT_REPORT_FILE, report)


def _has_company_url_signal(href: Optional[str]) -> bool:
    return bool(canonicalize_zerodha_stock_url(normalize_company_url(href)))


def _is_edition_heading(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not normalized:
        return False
    if re.match(r"^edition\s*#?\s*\d+$", normalized):
        return True
    tokens = normalized.split()
    return "edition" in tokens and len(tokens) <= 4


def _is_sector_like_heading(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not normalized:
        return False

    if normalized in SECTOR_HEADING_EXACT:
        return True

    if _is_edition_heading(name):
        return True

    tokens = normalized.split()
    sector_tokens = [t for t in tokens if t not in {"and"}]
    if sector_tokens and all(t in SECTOR_HEADING_TOKEN_SET for t in sector_tokens):
        return True
    return False


def _is_listable_sector_heading(name: str) -> bool:
    return _is_sector_like_heading(name) and not _is_edition_heading(name)


def _has_company_hint(words: list[str]) -> bool:
    return any(token in COMPANY_HINT_TOKENS for token in words)


def _looks_like_topic_or_sentence(name: str) -> bool:
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9&'.-]+", name)]
    if not words:
        return False

    first_word = words[0]
    if first_word in SENTENCE_START_TOKENS and len(words) > 4:
        return True

    lowered = " ".join(words)
    if re.search(r"\bcomments?\s+on\b", lowered):
        return True

    if "on" in words and len(words) >= 4 and not _has_company_hint(words):
        return True

    if any(token in {"minister", "secretary"} for token in words) and "on" in words:
        return True
    return False


def _matches_non_company_rules(name: str, rules: dict[str, object]) -> bool:
    name_key = _normalize_name_key(name)
    allow_name_keys = rules.get("allow_name_keys", set())
    if name_key in allow_name_keys:
        return False

    exact_name_keys = rules.get("exact_name_keys", set())
    if name_key in exact_name_keys:
        return True

    for pattern in rules.get("name_patterns", []):
        if pattern.search(name):
            return True
    return False


def is_probable_company_name(name: str, href: Optional[str], non_company_rules: dict[str, object]) -> bool:
    if not name or len(name) < 2:
        return False
    if re.match(r"(?i)^edition\s*#?\d+", name):
        return False
    if _is_sector_like_heading(name):
        return False
    lowered = name.lower()
    if lowered in {"the chatter", "the chatter by zerodha"}:
        return False
    if lowered.startswith("the chatter"):
        return False
    if any(ch in name for ch in {"?", "!"}):
        return False
    words = re.findall(r"[A-Za-z0-9&'.-]+", name)
    if ":" in name and len(words) > 4:
        return False
    if _has_company_url_signal(href):
        return True
    if _looks_like_topic_or_sentence(name):
        return False
    if _matches_non_company_rules(name, non_company_rules):
        return False
    return True


def extract_meta_content(html: str, key: str) -> Optional[str]:
    patterns = [
        rf'<meta[^>]+property="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+property="{re.escape(key)}"',
        rf'<meta[^>]+name="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+name="{re.escape(key)}"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, flags=re.IGNORECASE)
        if m:
            return " ".join(m.group(1).split())
    return None


def extract_json_ld_date(html: str) -> Optional[str]:
    scripts = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw in scripts:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                continue
            date_value = item.get("datePublished") or item.get("dateCreated")
            if isinstance(date_value, str):
                try:
                    return datetime.fromisoformat(date_value.replace("Z", "+00:00")).date().isoformat()
                except ValueError:
                    continue
    return None


def title_from_url(url: str) -> str:
    slug = urlparse(url).path.rsplit("/", 1)[-1]
    slug = slug.replace("-", " ").strip()
    return " ".join([w.capitalize() for w in slug.split()]) if slug else ""


def split_quote_and_speaker(text: str) -> tuple[str, Optional[str]]:
    stripped = text.strip()

    def looks_like_speaker(value: str) -> bool:
        candidate = value.strip().strip('"“”').strip()
        if len(candidate) < 3 or len(candidate) > 90:
            return False
        if "%" in candidate:
            return False
        if not re.search(r"[A-Za-z]", candidate):
            return False
        if sum(ch.isdigit() for ch in candidate) > 1:
            return False

        lowered = candidate.lower()
        if any(lowered.startswith(prefix) for prefix in ["management", "ceo", "cfo", "md", "chairman", "analyst"]):
            return True
        if "," in candidate:
            return True

        words = re.findall(r"[A-Za-z][A-Za-z.&']*", candidate)
        if 1 <= len(words) <= 6:
            return all(word[0].isupper() or word.isupper() for word in words)
        return False

    attribution_patterns = [
        r"^(?P<quote>.+?)\s+[–—-]\s+(?P<speaker>.+)$",
        r'^(?P<quote>.+?[”"])\s*[–—-]\s*(?P<speaker>.+)$',
    ]
    for pattern in attribution_patterns:
        m = re.match(pattern, stripped)
        if not m:
            continue
        quote = m.group("quote").strip().strip('"“”').strip()
        speaker = m.group("speaker").strip().strip('"“”').strip()
        if quote and speaker and looks_like_speaker(speaker):
            return quote, speaker

    return stripped.strip('"“”'), None


def is_speaker_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.startswith(("-", "–", "—")):
        return True
    lowered = stripped.lower()
    for prefix in ["management", "ceo", "cfo", "md", "chairman", "analyst"]:
        if lowered.startswith(prefix):
            return True
    return False


def is_direct_quote_paragraph(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 25:
        return False
    return stripped.startswith(("“", '"', "‘", "'", "...", "…"))


class ContentExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[dict] = []
        self._current_tag: Optional[str] = None
        self._current_text: list[str] = []
        self._heading_link: Optional[str] = None
        self._blockquote_depth: int = 0
        self._list_item_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "blockquote":
            self._flush()
            self._blockquote_depth += 1
        if tag == "li":
            self._list_item_depth += 1
        if tag in {"h1", "h2", "h3", "p"}:
            self._flush()
            self._current_tag = tag
            self._current_text = []
            if tag in {"h1", "h2", "h3"}:
                self._heading_link = None
        if tag == "a" and self._current_tag in {"h1", "h2", "h3"}:
            for k, v in attrs:
                if k == "href" and v:
                    self._heading_link = v

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "p"}:
            self._flush()
        if tag == "blockquote":
            self._flush()
            self._blockquote_depth = max(0, self._blockquote_depth - 1)
        if tag == "li":
            self._list_item_depth = max(0, self._list_item_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._current_tag:
            self._current_text.append(data)

    def _flush(self) -> None:
        if not self._current_tag:
            return
        text = " ".join(" ".join(self._current_text).split())
        if text:
            node = {"tag": self._current_tag, "text": text}
            if self._current_tag in {"h1", "h2", "h3"} and self._heading_link:
                node["href"] = self._heading_link
            if self._current_tag == "p":
                node["in_blockquote"] = self._blockquote_depth > 0
                node["in_list_item"] = self._list_item_depth > 0
            self.nodes.append(node)
        self._current_tag = None
        self._current_text = []
        self._heading_link = None


def extract_published_date(html: str) -> Optional[str]:
    meta_date = extract_meta_content(html, "article:published_time")
    if meta_date:
        try:
            return datetime.fromisoformat(meta_date.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass

    m = re.search(r'property="article:published_time"\s+content="([^"]+)"', html)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    m = re.search(r'<time[^>]*datetime="([^"]+)"', html)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    json_ld_date = extract_json_ld_date(html)
    if json_ld_date:
        return json_ld_date
    return None


def extract_title(html: str) -> str:
    meta_title = extract_meta_content(html, "og:title") or extract_meta_content(html, "twitter:title")
    if meta_title:
        return re.sub(r"\s*\|\s*Substack\s*$", "", meta_title, flags=re.IGNORECASE).strip()

    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
    return ""


def is_target_post(title: str) -> bool:
    t = title.lower()
    if TITLE_INCLUDE not in t:
        return False
    for ex in TITLE_EXCLUDE:
        if ex in t:
            return False
    return True


def extract_article_html(html: str) -> str:
    m = re.search(r"<article[^>]*>(.*?)</article>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)
    return html


def parse_post(
    url: str,
    non_company_rules: dict[str, object],
) -> tuple[Optional[Edition], list[Company], list[Quote], list[CompanyMention]]:
    html = fetch(url)
    title = extract_title(html)
    if not is_target_post(title):
        return None, [], [], []

    date = extract_published_date(html) or ""
    edition_slug = urlparse(url).path.strip("/") or title
    edition_id = slugify(edition_slug)
    edition_title = title
    if title.lower() in {"the chatter by zerodha", "the chatter"}:
        fallback_title = title_from_url(url)
        if fallback_title:
            edition_title = fallback_title
    edition = Edition(id=edition_id, title=edition_title, date=date, url=url)

    article_html = extract_article_html(html)
    extractor = ContentExtractor()
    extractor.feed(article_html)

    companies: dict[str, Company] = {}
    quotes: list[Quote] = []
    mentions: list[CompanyMention] = []
    mention_index_by_company: dict[str, int] = {}
    quote_count_by_company: dict[str, int] = {}

    current_sector: Optional[str] = None
    current_company: Optional[Company] = None
    last_context: Optional[str] = None
    current_blockquote_parts: list[str] = []

    def register_company(name: str, company_url: Optional[str]) -> Optional[Company]:
        normalized_name = normalize_company_name(name)
        if not normalized_name:
            return None
        if not is_probable_company_name(normalized_name, company_url, non_company_rules):
            return None

        company_id = canonical_company_id(normalized_name, company_url)
        if company_id not in companies:
            companies[company_id] = Company(
                id=company_id,
                name=normalized_name,
                url=company_url,
            )
        else:
            if company_url and not companies[company_id].url:
                companies[company_id].url = company_url
            if len(normalized_name) < len(companies[company_id].name):
                companies[company_id].name = normalized_name
        return companies[company_id]

    def register_mention(company: Company, mention_type: str) -> None:
        index = mention_index_by_company.get(company.id)
        if index is None:
            mention = CompanyMention(
                id=slugify(f"{edition_id}-{company.id}-{mention_type}-{len(mentions)}"),
                edition_id=edition_id,
                company_id=company.id,
                sector=current_sector,
                source_url=url,
                mention_type=mention_type,
            )
            mentions.append(mention)
            mention_index_by_company[company.id] = len(mentions) - 1
            return

        existing = mentions[index]
        if existing.mention_type != "heading" and mention_type == "heading":
            existing.mention_type = "heading"
        if mention_type == "heading" and current_sector:
            existing.sector = current_sector
        elif not existing.sector and current_sector:
            existing.sector = current_sector

    def flush_blockquote_parts() -> None:
        nonlocal last_context, current_blockquote_parts
        if not current_company:
            current_blockquote_parts = []
            return

        parts = [part.strip() for part in current_blockquote_parts if part and part.strip()]
        current_blockquote_parts = []
        if not parts:
            return

        combined = " ".join(parts)
        quote_text, speaker = split_quote_and_speaker(combined)
        if not speaker and len(parts) > 1 and is_speaker_line(parts[-1]):
            speaker = parts[-1].lstrip("-–— ").strip()
            quote_text = " ".join(parts[:-1]).strip()

        emit_quote(quote_text, speaker)

    def emit_quote(quote_text: str, speaker: Optional[str]) -> None:
        nonlocal last_context
        if not current_company:
            return

        cleaned_quote = quote_text.strip().strip('"“”').strip()
        if not cleaned_quote:
            return

        quote_id = slugify(f"{edition_id}-{current_company.id}-{len(quotes)}")
        quotes.append(
            Quote(
                id=quote_id,
                edition_id=edition_id,
                company_id=current_company.id,
                sector=current_sector,
                text=cleaned_quote,
                context=last_context,
                speaker=speaker,
                source_url=url,
            )
        )
        quote_count_by_company[current_company.id] = quote_count_by_company.get(current_company.id, 0) + 1
        register_mention(current_company, "heading")
        last_context = None

    for node in extractor.nodes:
        tag = node["tag"]
        text = node["text"]
        in_blockquote = bool(node.get("in_blockquote", False))
        in_list_item = bool(node.get("in_list_item", False))

        if tag != "p" or not in_blockquote:
            flush_blockquote_parts()

        if tag in {"h1", "h2", "h3"}:
            heading_text = " ".join(text.split()).strip()
            company_name, sector_hint = parse_company_heading(heading_text)
            company_url = canonicalize_zerodha_stock_url(normalize_company_url(node.get("href")))

            structured_heading = bool(company_url) or ("|" in heading_text)
            if tag == "h3" and not structured_heading and company_name:
                structured_heading = is_probable_company_name(company_name, company_url, non_company_rules)
            company = register_company(company_name, company_url) if company_name and structured_heading else None
            if company:
                current_company = company
                if sector_hint and _is_sector_like_heading(sector_hint):
                    current_sector = sector_hint
                register_mention(company, "heading")
                last_context = None
                continue

            if _is_sector_like_heading(heading_text):
                current_sector = heading_text
            current_company = None
            last_context = None
            continue
        if tag == "p":
            if in_blockquote:
                if current_company:
                    current_blockquote_parts.append(text)
                continue

            if in_list_item:
                if current_sector and _is_listable_sector_heading(current_sector):
                    listed_company = register_company(text, None)
                    if listed_company:
                        register_mention(listed_company, "list")
                current_company = None
                last_context = None
                continue

            if current_company and is_speaker_line(text) and quotes:
                last_quote = quotes[-1]
                if last_quote.edition_id == edition_id and last_quote.company_id == current_company.id and not last_quote.speaker:
                    last_quote.speaker = text.lstrip("-–— ").strip()
                    continue
            if current_company and is_direct_quote_paragraph(text):
                quote_text, speaker = split_quote_and_speaker(text)
                emit_quote(quote_text, speaker)
                continue
            # Keep plain paragraphs as context only; actual quotes are emitted from blockquotes.
            last_context = text
            continue

    flush_blockquote_parts()

    companies_with_coverage = [
        c
        for cid, c in companies.items()
        if quote_count_by_company.get(cid, 0) > 0 or cid in mention_index_by_company
    ]
    return edition, companies_with_coverage, quotes, mentions


def apply_non_company_sanity_filter(
    companies: dict[str, Company],
    quotes: list[Quote],
    mentions: list[CompanyMention],
    non_company_rules: dict[str, object],
) -> tuple[dict[str, Company], list[Quote], list[CompanyMention], dict[str, object]]:
    blocked_company_ids = {
        company.id
        for company in companies.values()
        if _matches_non_company_rules(company.name, non_company_rules)
        or _looks_like_topic_or_sentence(company.name)
    }
    if not blocked_company_ids:
        return (
            companies,
            quotes,
            mentions,
            {
                "removed_company_ids": [],
                "removed_companies": 0,
                "removed_quote_rows": 0,
                "removed_mention_rows": 0,
            },
        )

    filtered_companies = {cid: company for cid, company in companies.items() if cid not in blocked_company_ids}
    filtered_quotes = [quote for quote in quotes if quote.company_id not in blocked_company_ids]
    filtered_mentions = [mention for mention in mentions if mention.company_id not in blocked_company_ids]
    removed_company_names = [companies[cid].name for cid in sorted(blocked_company_ids) if cid in companies]

    return (
        filtered_companies,
        filtered_quotes,
        filtered_mentions,
        {
            "removed_company_ids": sorted(blocked_company_ids),
            "removed_company_names": removed_company_names,
            "removed_companies": len(blocked_company_ids),
            "removed_quote_rows": len(quotes) - len(filtered_quotes),
            "removed_mention_rows": len(mentions) - len(filtered_mentions),
        },
    )


def main() -> None:
    manual_overrides = load_manual_market_urls()
    mandatory_links = load_mandatory_indian_listed_links()
    non_company_rules = load_non_company_rules()
    nse_urls_by_symbol = load_zerodha_nse_stock_index()
    nse_market_candidates = load_nse_market_candidates(nse_urls_by_symbol)
    print(f"NSE sitemap symbols loaded: {len(nse_urls_by_symbol)}")
    print(f"NSE market candidates loaded: {len(nse_market_candidates)}")

    sitemap_html = fetch(SITEMAP_URL)
    year_pages = parse_sitemap_years(sitemap_html) or [SITEMAP_URL]

    post_urls: list[str] = []
    for year_url in year_pages:
        year_html = fetch(year_url)
        post_urls.extend(parse_sitemap_posts(year_html))
        time.sleep(0.2)

    post_urls = sorted(set(post_urls))

    editions: dict[str, Edition] = {}
    companies: dict[str, Company] = {}
    quotes: list[Quote] = []
    mentions: list[CompanyMention] = []

    for idx, url in enumerate(post_urls, start=1):
        try:
            edition, comps, qs, ms = parse_post(url, non_company_rules)
        except Exception as exc:  # pragma: no cover
            print(f"Failed to parse {url}: {exc}")
            continue
        if not edition:
            continue
        editions[edition.id] = edition
        for c in comps:
            if c.id not in companies:
                companies[c.id] = c
        quotes.extend(qs)
        mentions.extend(ms)
        if idx % 10 == 0:
            print(f"Processed {idx}/{len(post_urls)} posts...")
        time.sleep(0.2)

    companies, quotes, mentions, sanity_report = apply_non_company_sanity_filter(
        companies,
        quotes,
        mentions,
        non_company_rules,
    )
    if sanity_report["removed_companies"]:
        print(
            "Removed non-company rows:"
            f" companies={sanity_report['removed_companies']},"
            f" quotes={sanity_report['removed_quote_rows']},"
            f" mentions={sanity_report['removed_mention_rows']}"
        )

    link_audit_report = enrich_company_urls(
        companies,
        manual_overrides,
        mandatory_links,
        nse_urls_by_symbol,
        nse_market_candidates,
    )
    write_link_audit_report(link_audit_report)

    with open(f"{OUTPUT_DIR}/editions.json", "w", encoding="utf-8") as f:
        json.dump([e.__dict__ for e in editions.values()], f, ensure_ascii=False, indent=2)
    with open(f"{OUTPUT_DIR}/companies.json", "w", encoding="utf-8") as f:
        json.dump([c.__dict__ for c in companies.values()], f, ensure_ascii=False, indent=2)
    with open(f"{OUTPUT_DIR}/quotes.json", "w", encoding="utf-8") as f:
        json.dump([q.__dict__ for q in quotes], f, ensure_ascii=False, indent=2)
    with open(f"{OUTPUT_DIR}/{COMPANY_MENTIONS_FILE}", "w", encoding="utf-8") as f:
        json.dump([m.__dict__ for m in mentions], f, ensure_ascii=False, indent=2)

    print(f"Editions: {len(editions)}")
    print(f"Companies: {len(companies)}")
    print(f"Quotes: {len(quotes)}")
    print(f"Mentions: {len(mentions)}")
    print(f"Link audit report: {OUTPUT_DIR}/{LINK_AUDIT_REPORT_FILE}")

    if link_audit_report["counts"]["mandatory_missing"] or link_audit_report["counts"]["mandatory_mismatched"]:
        raise SystemExit("Mandatory Indian-listed link validation failed; inspect link audit report.")


if __name__ == "__main__":
    main()
