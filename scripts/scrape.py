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
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://thechatter.zerodha.com/"
SITEMAP_URL = urljoin(BASE_URL, "sitemap")
OUTPUT_DIR = "data"

TITLE_INCLUDE = "the chatter"
TITLE_EXCLUDE = ["points and figures", "plotlines"]
QUOTE_START_CHARS = ('"', "“", "‘", "'")
QUOTE_END_CHARS = ('"', "”", "’", "'")
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


def normalize_company_url(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    return urljoin(BASE_URL, href).strip()


def canonical_company_id(name: str, href: Optional[str]) -> str:
    company_url = normalize_company_url(href)
    if company_url:
        parsed = urlparse(company_url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            last = parts[-1]
            if last and last not in {"markets", "stocks"}:
                return slugify(last)
    return slugify(name)


def _has_company_url_signal(href: Optional[str]) -> bool:
    company_url = normalize_company_url(href)
    if not company_url:
        return False
    parsed = urlparse(company_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return False
    return parts[-1].lower() not in {"markets", "stocks"}


def _is_sector_like_heading(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not normalized:
        return False

    if normalized in SECTOR_HEADING_EXACT:
        return True

    if re.match(r"^edition\s*#?\s*\d+$", normalized):
        return True

    tokens = normalized.split()
    if "edition" in tokens and len(tokens) <= 4:
        return True

    sector_tokens = [t for t in tokens if t not in {"and"}]
    if sector_tokens and all(t in SECTOR_HEADING_TOKEN_SET for t in sector_tokens):
        return True
    return False


def is_probable_company_name(name: str, href: Optional[str]) -> bool:
    if not name or len(name) < 2:
        return False
    if re.match(r"(?i)^edition\s*#?\d+", name):
        return False
    if name.lower() in {"the chatter", "the chatter by zerodha"}:
        return False
    if _has_company_url_signal(href):
        return True
    if _is_sector_like_heading(name):
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
    # Common attribution formats: "Quote..." - Speaker
    m = re.match(r'^(.*?)[\s]*(?:[-–—]\s*|"\s*[-–—]\s*)([^-–—"].{1,80})$', stripped)
    if m:
        quote = m.group(1).strip().strip('"“”')
        speaker = m.group(2).strip()
        if quote and speaker:
            return quote, speaker
    return stripped.strip('"“”'), None


def is_quote_text(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 25:
        return False
    if stripped.startswith(QUOTE_START_CHARS):
        return True
    if stripped.endswith(QUOTE_END_CHARS):
        return True
    if ('"' in stripped and stripped.count('"') >= 2) or ("“" in stripped and "”" in stripped):
        return True
    return False


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


class ContentExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[dict] = []
        self._current_tag: Optional[str] = None
        self._current_text: list[str] = []
        self._current_link: Optional[str] = None
        self._h3_link: Optional[str] = None
        self._inside_h3: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in {"h2", "h3", "p", "blockquote"}:
            self._flush()
            self._current_tag = tag
            self._current_text = []
            if tag == "h3":
                self._inside_h3 = True
                self._h3_link = None
        if tag == "a" and self._inside_h3:
            for k, v in attrs:
                if k == "href" and v:
                    self._h3_link = v

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3", "p", "blockquote"}:
            self._flush()
        if tag == "h3":
            self._inside_h3 = False

    def handle_data(self, data: str) -> None:
        if self._current_tag:
            self._current_text.append(data)

    def _flush(self) -> None:
        if not self._current_tag:
            return
        text = " ".join(" ".join(self._current_text).split())
        if text:
            node = {"tag": self._current_tag, "text": text}
            if self._current_tag == "h3" and self._h3_link:
                node["href"] = self._h3_link
            self.nodes.append(node)
        self._current_tag = None
        self._current_text = []
        self._current_link = None


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


def parse_post(url: str) -> tuple[Optional[Edition], list[Company], list[Quote]]:
    html = fetch(url)
    title = extract_title(html)
    if not is_target_post(title):
        return None, [], []

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
    quote_count_by_company: dict[str, int] = {}

    current_sector: Optional[str] = None
    current_company: Optional[Company] = None
    last_context: Optional[str] = None

    for node in extractor.nodes:
        tag = node["tag"]
        text = node["text"]
        if tag == "h2":
            current_sector = text
            continue
        if tag == "h3":
            name = normalize_company_name(text)
            company_url = normalize_company_url(node.get("href"))
            if not is_probable_company_name(name, company_url):
                current_company = None
                continue
            company_id = canonical_company_id(name, company_url)
            if company_id not in companies:
                companies[company_id] = Company(
                    id=company_id,
                    name=name,
                    url=company_url,
                )
            else:
                if company_url and not companies[company_id].url:
                    companies[company_id].url = company_url
                if len(name) < len(companies[company_id].name):
                    companies[company_id].name = name
            current_company = companies[company_id]
            last_context = None
            continue
        if tag == "p":
            if current_company and is_speaker_line(text) and quotes:
                last_quote = quotes[-1]
                if last_quote.edition_id == edition_id and last_quote.company_id == current_company.id and not last_quote.speaker:
                    last_quote.speaker = text.lstrip("-–— ").strip()
                    continue
            if current_company and is_quote_text(text):
                quote_text, speaker = split_quote_and_speaker(text)
                if quote_text:
                    quote_id = slugify(f"{edition_id}-{current_company.id}-{len(quotes)}")
                    quotes.append(
                        Quote(
                            id=quote_id,
                            edition_id=edition_id,
                            company_id=current_company.id,
                            sector=current_sector,
                            text=quote_text,
                            context=last_context,
                            speaker=speaker,
                            source_url=url,
                        )
                    )
                    quote_count_by_company[current_company.id] = quote_count_by_company.get(current_company.id, 0) + 1
                    last_context = None
                    continue
            last_context = text
            continue
        if tag == "blockquote" and current_company:
            quote_id = slugify(f"{edition_id}-{current_company.id}-{len(quotes)}")
            quotes.append(
                Quote(
                    id=quote_id,
                    edition_id=edition_id,
                    company_id=current_company.id,
                    sector=current_sector,
                    text=text,
                    context=last_context,
                    speaker=None,
                    source_url=url,
                )
            )
            quote_count_by_company[current_company.id] = quote_count_by_company.get(current_company.id, 0) + 1
            last_context = None

    companies_with_quotes = [c for cid, c in companies.items() if quote_count_by_company.get(cid, 0) > 0]
    return edition, companies_with_quotes, quotes


def main() -> None:
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

    for idx, url in enumerate(post_urls, start=1):
        try:
            edition, comps, qs = parse_post(url)
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
        if idx % 10 == 0:
            print(f"Processed {idx}/{len(post_urls)} posts...")
        time.sleep(0.2)

    with open(f"{OUTPUT_DIR}/editions.json", "w", encoding="utf-8") as f:
        json.dump([e.__dict__ for e in editions.values()], f, ensure_ascii=False, indent=2)
    with open(f"{OUTPUT_DIR}/companies.json", "w", encoding="utf-8") as f:
        json.dump([c.__dict__ for c in companies.values()], f, ensure_ascii=False, indent=2)
    with open(f"{OUTPUT_DIR}/quotes.json", "w", encoding="utf-8") as f:
        json.dump([q.__dict__ for q in quotes], f, ensure_ascii=False, indent=2)

    print(f"Editions: {len(editions)}")
    print(f"Companies: {len(companies)}")
    print(f"Quotes: {len(quotes)}")


if __name__ == "__main__":
    main()
