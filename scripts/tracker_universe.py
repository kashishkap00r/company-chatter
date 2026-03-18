#!/usr/bin/env python3
"""Fetch NIFTY LargeMidcap 250 constituents and resolve BSE scrip codes."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_PATH = DATA_DIR / "tracker_universe.json"

NSE_INDEX_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20LARGEMIDCAP%20250"
NSE_COOKIE_URL = "https://www.nseindia.com/get-quotes/equity?symbol=TCS"
BSE_SEARCH_URL = "https://api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json",
}


def _nse_fetch_with_cookies(url: str) -> dict:
    """Fetch from NSE API with cookie management."""
    cookie_req = Request(NSE_COOKIE_URL, headers=NSE_HEADERS)
    try:
        with urlopen(cookie_req, timeout=15) as resp:
            cookies = resp.headers.get("Set-Cookie", "")
    except Exception:
        cookies = ""

    cookie_str = "; ".join(
        part.split(";")[0] for part in cookies.split(", ")
        if "=" in part.split(";")[0]
    )

    headers = {**NSE_HEADERS, "Cookie": cookie_str}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _bse_search_scrip(query: str) -> str | None:
    """Search BSE for a scrip code by ISIN or company name."""
    import urllib.parse
    url = f"{BSE_SEARCH_URL}?Type=SS&text={urllib.parse.quote(query)}"
    req = Request(url, headers=BSE_HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except (HTTPError, OSError):
        return None

    try:
        data = json.loads(body)
        if isinstance(data, list) and data:
            for item in data:
                code = str(item.get("scrip_cd", "") or item.get("SCRIP_CD", "")).strip()
                if code and code.isdigit():
                    return code
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\b(\d{6})\b", body)
    return match.group(1) if match else None


def fetch_nifty_250() -> list[dict]:
    """Fetch NIFTY LargeMidcap 250 constituents from NSE API."""
    data = _nse_fetch_with_cookies(NSE_INDEX_URL)
    entries = data.get("data", [])

    companies = []
    for entry in entries:
        symbol = entry.get("symbol", "").strip()
        if not symbol or symbol == "NIFTY LARGEMIDCAP 250":
            continue
        meta = entry.get("meta", {})
        companies.append({
            "symbol": symbol,
            "name": meta.get("companyName", "").strip(),
            "isin": meta.get("isin", "").strip(),
            "industry": meta.get("industry", "").strip(),
        })

    return companies


def enrich_bse_codes(companies: list[dict]) -> list[dict]:
    """Add BSE scrip codes by searching BSE API with ISIN."""
    for idx, company in enumerate(companies):
        isin = company.get("isin", "")
        bse_code = None
        if isin:
            bse_code = _bse_search_scrip(isin)
            time.sleep(0.15)

        if not bse_code:
            bse_code = _bse_search_scrip(company["name"])
            time.sleep(0.15)

        company["bse_code"] = bse_code
        if (idx + 1) % 50 == 0:
            print(f"BSE codes resolved: {idx + 1}/{len(companies)}")

    resolved = sum(1 for c in companies if c.get("bse_code"))
    print(f"BSE codes: {resolved}/{len(companies)} resolved")
    return companies


def main() -> None:
    print("Fetching NIFTY LargeMidcap 250 constituents...")
    companies = fetch_nifty_250()
    print(f"Fetched {len(companies)} companies from NSE")

    print("Resolving BSE scrip codes...")
    companies = enrich_bse_codes(companies)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(companies)} companies to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
