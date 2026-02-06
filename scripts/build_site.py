#!/usr/bin/env python3
"""Build a static site from the extracted JSON data.

Inputs:
- data/editions.json
- data/companies.json
- data/quotes.json

Outputs:
- site/index.html
- site/company/<slug>/index.html
- site/assets/styles.css
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
SITE_DIR = BASE_DIR / "site"
ASSETS_DIR = BASE_DIR / "assets"


def read_json(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def render_template(template_name: str, context: dict[str, str]) -> str:
    template = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace("{{ " + key + " }}", value)
    return template


def wrap_base(title: str, content: str) -> str:
    return render_template(
        "base.html",
        {
            "title": title,
            "content": content,
            "updated": datetime.now(timezone.utc).date().isoformat(),
        },
    )


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_index(companies: list[dict], quotes: list[dict]) -> None:
    quote_count_by_company: dict[str, int] = {}
    for q in quotes:
        quote_count_by_company[q["company_id"]] = quote_count_by_company.get(q["company_id"], 0) + 1

    cards = []
    for company in sorted(companies, key=lambda c: c["name"].lower()):
        slug = company["id"]
        count = quote_count_by_company.get(slug, 0)
        cards.append(
            "\n".join(
                [
                    f'<a class="company-item" data-name="{html_escape(company["name"].lower())}" href="/company/{slug}/">',
                    f"  <strong>{html_escape(company['name'])}</strong>",
                    f"  <span>{count} quotes</span>",
                    "</a>",
                ]
            )
        )

    content = render_template(
        "index.html",
        {
            "company_cards": "\n".join(cards) if cards else "<p>No companies yet.</p>",
        },
    )
    html = wrap_base("Company Chatter", content)
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")


def format_date(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    try:
        return datetime.fromisoformat(date_str).strftime("%b %d, %Y")
    except ValueError:
        return date_str


def build_company_pages(companies: list[dict], editions: dict[str, dict], quotes: list[dict]) -> None:
    quotes_by_company: dict[str, list[dict]] = {}
    for q in quotes:
        quotes_by_company.setdefault(q["company_id"], []).append(q)

    for company in companies:
        slug = company["id"]
        company_quotes = sorted(
            quotes_by_company.get(slug, []),
            key=lambda q: (
                editions.get(q["edition_id"], {}).get("date", "") or "9999-12-31",
                q["edition_id"],
                q["id"],
            ),
        )

        quote_cards = []
        dates = []
        edition_ids = set()

        for q in company_quotes:
            edition = editions.get(q["edition_id"], {})
            date = edition.get("date", "")
            dates.append(date)
            edition_ids.add(q["edition_id"])

            meta_parts = [format_date(date)]
            if q.get("sector"):
                meta_parts.append(q["sector"])
            meta_parts.append(edition.get("title", ""))
            meta_line = " · ".join([html_escape(p) for p in meta_parts if p])

            quote_cards.append(
                "\n".join(
                    [
                        '<article class="quote-card">',
                        f'  <div class="quote-meta">{meta_line}</div>',
                        f'  <div class="quote-text">“{html_escape(q["text"])}”</div>',
                        f'  <div class="quote-context">{html_escape(q.get("context") or "")}</div>',
                        f'  <a class="small-link" href="{html_escape(q["source_url"])}">View source</a>',
                        "</article>",
                    ]
                )
            )

        company_link = ""
        if company.get("url"):
            company_link = f'<a class="small-link" href="{html_escape(company["url"])}">Company page</a>'

        meta = "Auto-mapped from The Chatter archive · Timeline oldest to newest"
        content = render_template(
            "company.html",
            {
                "company_name": html_escape(company["name"]),
                "company_meta": meta,
                "company_link": company_link,
                "quote_cards": "\n".join(quote_cards) if quote_cards else "<p>No quotes yet.</p>",
                "quote_count": str(len(company_quotes)),
                "first_date": format_date(min([d for d in dates if d] or [""])),
                "last_date": format_date(max([d for d in dates if d] or [""])),
                "editions_count": str(len(edition_ids)),
            },
        )
        html = wrap_base(f"{company['name']} | Company Chatter", content)
        out_dir = SITE_DIR / "company" / slug
        ensure_dir(out_dir)
        (out_dir / "index.html").write_text(html, encoding="utf-8")


def copy_assets() -> None:
    ensure_dir(SITE_DIR / "assets")
    for path in ASSETS_DIR.iterdir():
        if path.is_file():
            (SITE_DIR / "assets" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> None:
    ensure_dir(SITE_DIR)
    copy_assets()

    editions = {e["id"]: e for e in read_json(DATA_DIR / "editions.json")}
    companies = read_json(DATA_DIR / "companies.json")
    quotes = read_json(DATA_DIR / "quotes.json")

    build_index(companies, quotes)
    build_company_pages(companies, editions, quotes)

    print("Site build complete")


if __name__ == "__main__":
    main()
