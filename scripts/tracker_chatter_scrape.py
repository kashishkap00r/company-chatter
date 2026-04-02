#!/usr/bin/env python3
"""Scrape Chatter editions from Substack for coverage tracking.

Mirrors tracker_pnf_scrape.py: discovers posts via sitemap, extracts company
names from H3 headings, caches HTML to avoid re-fetching. Runs as part of
the daily tracker refresh so coverage detection always has fresh edition data.
"""

from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_PATH = DATA_DIR / "tracker_chatter_cache.json"
OUTPUT_PATH = DATA_DIR / "tracker_chatter_editions.json"

BASE_URL = "https://thechatter.zerodha.com/"
SITEMAP_URL = urljoin(BASE_URL, "sitemap")

CHATTER_TITLE_INCLUDE = "the chatter"
CHATTER_TITLE_EXCLUDE = ["points and figures", "points & figures", "points &amp; figures", "plotlines"]


def _fetch(url: str, max_retries: int = 4) -> str:
    req = Request(url, headers={"User-Agent": "CompanyChatterBot/0.1"})
    for attempt in range(max_retries + 1):
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                delay = 2 ** attempt + 1
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"Exhausted retries for {url}")


def _discover_post_urls() -> list[str]:
    """Discover all Substack post URLs from sitemap."""
    sitemap_html = _fetch(SITEMAP_URL)
    year_urls = sorted(set(
        urljoin(BASE_URL, href)
        for href in re.findall(r'href="([^"]+)"', sitemap_html)
        if "/sitemap/" in href
    )) or [SITEMAP_URL]

    post_urls: list[str] = []
    for year_url in year_urls:
        year_html = _fetch(year_url)
        for href in re.findall(r'href="([^"]+)"', year_html):
            full = urljoin(BASE_URL, href)
            if urlparse(full).path.startswith("/p/"):
                post_urls.append(full)
        time.sleep(0.3)

    return sorted(set(post_urls))


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_date(html: str) -> str:
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)[:10]
    match = re.search(r'<time[^>]*datetime="([^"]+)"', html)
    if match:
        return match.group(1)[:10]
    return ""


def _is_chatter_post(title: str) -> bool:
    """Check if a post title is a Chatter edition (not P&F or Plotline)."""
    t = title.lower()
    if CHATTER_TITLE_INCLUDE not in t:
        return False
    for excl in CHATTER_TITLE_EXCLUDE:
        if excl in t:
            return False
    return True


class CompanyHeadingExtractor(HTMLParser):
    """Extract company names from H3 headings in Chatter articles.

    Chatter articles use H3 headings in pipe-delimited format:
    'Company Name | Cap Size | Sector'
    H2 headings are sector headers and are ignored.
    """

    def __init__(self) -> None:
        super().__init__()
        self.companies: list[str] = []
        self._in_heading = False
        self._heading_text: list[str] = []
        self._in_article = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self._in_article = True
        if tag in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return
        if self._in_article and tag == "h3":
            self._in_heading = True
            self._heading_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth > 0:
            self._ignored_depth -= 1
            return
        if tag == "h3" and self._in_heading:
            text = " ".join(" ".join(self._heading_text).split()).strip()
            if text and len(text) < 150 and "|" in text:
                self.companies.append(text)
            self._in_heading = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        if self._in_heading:
            self._heading_text.append(data)


def parse_chatter_post(html: str) -> dict | None:
    """Parse a Chatter post, extracting title, date, and company headings."""
    title = _extract_title(html)
    if not _is_chatter_post(title):
        return None

    date = _extract_date(html)
    extractor = CompanyHeadingExtractor()
    extractor.feed(html)

    if not extractor.companies:
        return None

    return {
        "title": title,
        "date": date,
        "companies": extractor.companies,
    }


def scrape_chatter_editions() -> list[dict]:
    """Scrape all Chatter editions, using cache for previously fetched posts."""
    post_cache: dict[str, str] = {}
    if CACHE_PATH.exists():
        try:
            post_cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            post_cache = {}

    post_urls = _discover_post_urls()
    cache_hits = 0
    editions: list[dict] = []

    for url in post_urls:
        try:
            cached_html = post_cache.get(url)
            if cached_html:
                cache_hits += 1
            else:
                cached_html = _fetch(url)
                post_cache[url] = cached_html
                time.sleep(1)

            result = parse_chatter_post(cached_html)
            if result:
                result["url"] = url
                editions.append(result)
        except Exception as exc:
            print(f"Failed to parse {url}: {exc}")
            continue

    print(f"Chatter scrape: {cache_hits} cache hits, {len(post_urls) - cache_hits} fetched")
    print(f"Chatter editions found: {len(editions)}")

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(post_cache, f, ensure_ascii=False)

    return editions


def main() -> None:
    editions = scrape_chatter_editions()

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(editions, f, ensure_ascii=False, indent=2)

    total_companies = sum(len(e["companies"]) for e in editions)
    print(f"Wrote {len(editions)} Chatter editions ({total_companies} total company mentions)")

    for ed in editions[:3]:
        print(f"\n  {ed['date']} — {ed['title']}")
        for c in ed["companies"][:5]:
            print(f"    - {c}")


if __name__ == "__main__":
    main()
