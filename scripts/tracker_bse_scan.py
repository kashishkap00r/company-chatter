#!/usr/bin/env python3
"""Scan BSE corporate filings API for investor presentations and transcripts."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json",
}

PRESENTATION_RE = re.compile(
    r"investor\s*presentation|presentation|analyst\s*/?\s*investor\s*meet",
    re.IGNORECASE,
)
TRANSCRIPT_RE = re.compile(r"transcript|earnings\s*call", re.IGNORECASE)


def _fetch_bse_announcements(bse_code: str, from_date: str, to_date: str) -> list[dict]:
    """Fetch all result-category announcements for a BSE scrip code within a date range."""
    all_announcements: list[dict] = []
    from_bse = from_date.replace("-", "")
    to_bse = to_date.replace("-", "")

    for page in range(1, 21):
        params = urlencode({
            "pageno": page,
            "strCat": "-1",
            "subcategory": "-1",
            "strPrevDate": from_bse,
            "strToDate": to_bse,
            "strscrip": bse_code,
            "strType": "C",
            "strSearch": "P",
        })
        url = f"{BSE_ANN_URL}?{params}"
        req = Request(url, headers=BSE_HEADERS)

        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, OSError, json.JSONDecodeError):
            break

        table = data.get("Table", [])
        if not table:
            break
        all_announcements.extend(table)

        table1 = data.get("Table1", [])
        total_rows = int(table1[0].get("ROWCNT", 0)) if table1 else 0
        if len(all_announcements) >= total_rows:
            break

        time.sleep(0.15)

    return all_announcements


def _parse_date(date_str: str) -> str | None:
    """Parse BSE date string like '2025-01-16T19:28:45.83' to 'YYYY-MM-DD'."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.split("T")[0])
        return dt.strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def scan_bse_filings(universe: list[dict], from_date: str, to_date: str) -> dict[str, dict]:
    """Scan BSE for investor presentations and transcripts.

    Returns: { symbol: { pnf_date: str|None, chatter_date: str|None } }
    """
    results: dict[str, dict] = {}
    scanned = 0
    skipped = 0

    for company in universe:
        symbol = company["symbol"]
        bse_code = company.get("bse_code")
        if not bse_code:
            skipped += 1
            continue

        announcements = _fetch_bse_announcements(bse_code, from_date, to_date)
        time.sleep(0.15)

        pnf_date = None
        chatter_date = None

        for ann in announcements:
            subcat = str(ann.get("SUBCATNAME", ""))
            news_dt = _parse_date(str(ann.get("NEWS_DT", "")))

            if PRESENTATION_RE.search(subcat) and news_dt:
                if not pnf_date or news_dt < pnf_date:
                    pnf_date = news_dt

            if TRANSCRIPT_RE.search(subcat) and news_dt:
                if not chatter_date or news_dt < chatter_date:
                    chatter_date = news_dt

        if pnf_date or chatter_date:
            results[symbol] = {"pnf_date": pnf_date, "chatter_date": chatter_date}

        scanned += 1
        if scanned % 25 == 0:
            print(f"BSE scan: {scanned}/{len(universe) - skipped} companies...")

    print(f"BSE scan complete: {scanned} scanned, {skipped} skipped (no BSE code)")
    print(f"  Presentations found: {sum(1 for r in results.values() if r.get('pnf_date'))}")
    print(f"  Transcripts found: {sum(1 for r in results.values() if r.get('chatter_date'))}")
    return results


if __name__ == "__main__":
    universe = json.loads((DATA_DIR / "tracker_universe.json").read_text())
    quarters = json.loads((DATA_DIR / "tracker_quarters.json").read_text())
    q = quarters["quarters"][0]
    print(f"Scanning BSE for {q['name']} ({q['results_window'][0]} to {q['results_window'][1]})")
    results = scan_bse_filings(universe, q["results_window"][0], q["results_window"][1])
    print(json.dumps(dict(list(results.items())[:5]), indent=2))
