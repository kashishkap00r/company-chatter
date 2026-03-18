#!/usr/bin/env python3
"""Check Screener.in for concall transcript availability."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SCREENER_BASE = "https://www.screener.in/company"
SCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "text/html",
}

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b", re.IGNORECASE)
TRANSCRIPT_RE = re.compile(r"transcript", re.IGNORECASE)


class ConcallParser(HTMLParser):
    """Parse Screener.in company page to extract concall transcript dates."""

    def __init__(self) -> None:
        super().__init__()
        self.transcript_dates: list[str] = []
        self._in_concall_section = False
        self._current_date: str | None = None
        self._in_link = False
        self._link_text: list[str] = []
        self._text_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        # Detect concall section by id or heading
        if attr_dict.get("id") == "concall":
            self._in_concall_section = True
        # Also detect by class containing "concall"
        cls = attr_dict.get("class", "") or ""
        if "concall" in cls.lower() and tag in {"div", "section"}:
            self._in_concall_section = True

        if tag == "a":
            self._in_link = True
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            link_text = " ".join(self._link_text).strip()
            if TRANSCRIPT_RE.search(link_text) and self._current_date:
                self.transcript_dates.append(self._current_date)
            self._in_link = False
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._link_text.append(data)

        # Track dates anywhere — Screener shows "Jan 2026" etc.
        for match in DATE_RE.finditer(data):
            month_str = match.group(1).lower()[:3]
            year = int(match.group(2))
            month = MONTH_MAP.get(month_str)
            if month:
                try:
                    self._current_date = datetime(year, month, 1).strftime("%Y-%m-%d")
                except ValueError:
                    pass


def _fetch_screener_page(symbol: str) -> str | None:
    """Fetch a company page from Screener.in."""
    url = f"{SCREENER_BASE}/{symbol}/"
    req = Request(url, headers=SCREENER_HEADERS)
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except (HTTPError, OSError):
        return None


def _find_concall_dates(html: str, from_date: str, to_date: str) -> str | None:
    """Find the earliest concall transcript date within the results window."""
    parser = ConcallParser()
    parser.feed(html)

    earliest: str | None = None
    for date_str in parser.transcript_dates:
        if from_date <= date_str <= to_date:
            if not earliest or date_str < earliest:
                earliest = date_str

    return earliest


def scan_screener_concalls(universe: list[dict], from_date: str, to_date: str) -> dict[str, str]:
    """Scan Screener.in for concall transcript availability.

    Returns: { symbol: earliest_transcript_date }
    """
    results: dict[str, str] = {}
    scanned = 0
    errors = 0

    for company in universe:
        symbol = company["symbol"]
        html = _fetch_screener_page(symbol)
        time.sleep(0.5)

        if not html:
            errors += 1
            scanned += 1
            continue

        earliest = _find_concall_dates(html, from_date, to_date)
        if earliest:
            results[symbol] = earliest

        scanned += 1
        if scanned % 25 == 0:
            print(f"Screener scan: {scanned}/{len(universe)} companies...")

    print(f"Screener scan complete: {scanned} scanned, {errors} errors")
    print(f"  Concall transcripts found: {len(results)}")
    return results


if __name__ == "__main__":
    universe = json.loads((DATA_DIR / "tracker_universe.json").read_text())
    quarters = json.loads((DATA_DIR / "tracker_quarters.json").read_text())
    q = quarters["quarters"][0]
    print(f"Scanning Screener for {q['name']} ({q['results_window'][0]} to {q['results_window'][1]})")
    results = scan_screener_concalls(universe[:10], q["results_window"][0], q["results_window"][1])
    print(f"\nSample results:")
    for sym, date in results.items():
        print(f"  {sym}: {date}")
