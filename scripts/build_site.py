#!/usr/bin/env python3
"""Build a static site from the extracted JSON data.

Inputs:
- data/editions.json
- data/companies.json
- data/quotes.json
- data/company_mentions.json

Outputs:
- site/index.html
- site/company/<slug>/index.html
- site/assets/styles.css
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
SITE_DIR = BASE_DIR / "site"
ASSETS_DIR = BASE_DIR / "assets"
LEGAL_SUFFIX_TOKENS = {
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
}


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


def _name_tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def _has_legal_suffix(name: str) -> bool:
    tokens = _name_tokens(name)
    return bool(tokens) and tokens[-1] in LEGAL_SUFFIX_TOKENS


def _company_name_key(name: str) -> str:
    tokens = _name_tokens(name)
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _select_display_name(variants: list[dict]) -> str:
    def rank(c: dict) -> tuple[int, int, int, str]:
        return (
            1 if _has_legal_suffix(c["name"]) else 0,
            len(_name_tokens(c["name"])),
            len(c["name"]),
            c["name"].lower(),
        )

    return min(variants, key=rank)["name"]


def merge_company_variants(
    companies: list[dict], quotes: list[dict], mentions: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    quote_count_by_company: dict[str, int] = {}
    for q in quotes:
        quote_count_by_company[q["company_id"]] = quote_count_by_company.get(q["company_id"], 0) + 1

    mention_count_by_company: dict[str, int] = {}
    for m in mentions:
        mention_count_by_company[m["company_id"]] = mention_count_by_company.get(m["company_id"], 0) + 1

    groups: dict[str, list[dict]] = {}
    for c in companies:
        key = _company_name_key(c["name"]) or c["id"]
        groups.setdefault(key, []).append(c)

    alias_map: dict[str, str] = {}
    merged_companies: list[dict] = []

    for variants in groups.values():
        primary = max(
            variants,
            key=lambda c: (
                1 if c.get("url") else 0,
                quote_count_by_company.get(c["id"], 0),
                mention_count_by_company.get(c["id"], 0),
                0 if _has_legal_suffix(c["name"]) else 1,
                -len(c["name"]),
            ),
        )
        primary_id = primary["id"]
        display_name = _select_display_name(variants)
        canonical_url = primary.get("url") or next((c.get("url") for c in variants if c.get("url")), None)

        merged_companies.append(
            {
                "id": primary_id,
                "name": display_name,
                "url": canonical_url,
            }
        )
        for c in variants:
            alias_map[c["id"]] = primary_id

    merged_quotes: list[dict] = []
    for q in quotes:
        updated = dict(q)
        updated["company_id"] = alias_map.get(q["company_id"], q["company_id"])
        merged_quotes.append(updated)

    merged_mentions: list[dict] = []
    for m in mentions:
        updated = dict(m)
        updated["company_id"] = alias_map.get(m["company_id"], m["company_id"])
        merged_mentions.append(updated)

    return merged_companies, merged_quotes, merged_mentions


def build_index(companies: list[dict], editions: dict[str, dict], quotes: list[dict], mentions: list[dict]) -> None:
    quote_count_by_company: dict[str, int] = {}
    edition_ids_by_company: dict[str, set[str]] = {}
    latest_date_by_company: dict[str, str] = {}
    for q in quotes:
        company_id = q["company_id"]
        quote_count_by_company[company_id] = quote_count_by_company.get(company_id, 0) + 1
        edition_ids_by_company.setdefault(company_id, set()).add(q["edition_id"])

        edition_date = editions.get(q["edition_id"], {}).get("date", "")
        if edition_date and edition_date > latest_date_by_company.get(company_id, ""):
            latest_date_by_company[company_id] = edition_date

    for m in mentions:
        company_id = m["company_id"]
        edition_id = m["edition_id"]
        edition_ids_by_company.setdefault(company_id, set()).add(edition_id)
        edition_date = editions.get(edition_id, {}).get("date", "")
        if edition_date and edition_date > latest_date_by_company.get(company_id, ""):
            latest_date_by_company[company_id] = edition_date

    company_records = []
    for company in sorted(companies, key=lambda c: c["name"].lower()):
        slug = company["id"]
        quote_count = quote_count_by_company.get(slug, 0)
        edition_ids = edition_ids_by_company.get(slug, set())
        if quote_count == 0 and not edition_ids:
            continue
        company_records.append(
            {
                "slug": slug,
                "name": company["name"],
                "quote_count": quote_count,
                "edition_count": len(edition_ids),
                "latest_date": format_date(latest_date_by_company.get(slug, "")),
            }
        )

    company_data_json = json.dumps(company_records, ensure_ascii=False).replace("</", "<\\/")
    visible_companies = len(company_records)
    content = render_template(
        "index.html",
        {
            "company_data_json": company_data_json,
            "total_companies": str(visible_companies),
            "total_quotes": str(len(quotes)),
            "total_editions": str(len(editions)),
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


def build_company_pages(companies: list[dict], editions: dict[str, dict], quotes: list[dict], mentions: list[dict]) -> None:
    company_dir = SITE_DIR / "company"
    if company_dir.exists():
        shutil.rmtree(company_dir)
    ensure_dir(company_dir)

    quotes_by_company_edition: dict[str, dict[str, list[dict]]] = {}
    for q in quotes:
        quotes_by_company_edition.setdefault(q["company_id"], {}).setdefault(q["edition_id"], []).append(q)

    mentions_by_company_edition: dict[str, dict[str, list[dict]]] = {}
    for m in mentions:
        mentions_by_company_edition.setdefault(m["company_id"], {}).setdefault(m["edition_id"], []).append(m)

    for company in companies:
        slug = company["id"]
        quote_editions = set(quotes_by_company_edition.get(slug, {}).keys())
        mention_editions = set(mentions_by_company_edition.get(slug, {}).keys())
        covered_edition_ids = sorted(
            quote_editions.union(mention_editions),
            key=lambda edition_id: (
                editions.get(edition_id, {}).get("date", "") or "9999-12-31",
                edition_id,
            ),
        )
        if not covered_edition_ids:
            continue

        edition_sections = []
        dates = [editions.get(edition_id, {}).get("date", "") for edition_id in covered_edition_ids]
        company_quote_count = 0

        for edition_id in covered_edition_ids:
            edition_quotes = sorted(
                quotes_by_company_edition.get(slug, {}).get(edition_id, []),
                key=lambda q: q["id"],
            )
            edition_mentions = mentions_by_company_edition.get(slug, {}).get(edition_id, [])
            edition = editions.get(edition_id, {})
            edition_title = edition.get("title") or edition_id
            edition_date = edition.get("date", "")
            edition_source_url = (
                edition.get("url")
                or (edition_quotes[0].get("source_url", "") if edition_quotes else "")
                or (edition_mentions[0].get("source_url", "") if edition_mentions else "")
            )

            sectors = sorted(
                {
                    sector
                    for sector in (
                        [q.get("sector", "").strip() for q in edition_quotes]
                        + [m.get("sector", "").strip() for m in edition_mentions]
                    )
                    if sector
                }
            )
            sector_line = ""
            if sectors:
                sector_line = f'<p class="chapter-sector">{html_escape(" · ".join(sectors))}</p>'

            quote_cards = []
            chapter_meta = "Mentioned (no quote extracted)"
            if edition_quotes:
                chapter_meta = f"{len(edition_quotes)} quotes"
                company_quote_count += len(edition_quotes)
                for index, q in enumerate(edition_quotes, start=1):
                    quote_context = (q.get("context") or "").strip()
                    quote_speaker = (q.get("speaker") or "").strip()
                    story_parts = []

                    if quote_context:
                        story_parts.append(f'<p class="story-context">{html_escape(quote_context)}</p>')
                    story_parts.append(f'<blockquote class="story-quote">“{html_escape(q["text"])}”</blockquote>')
                    if quote_speaker:
                        story_parts.append(f'<p class="story-speaker">— {html_escape(quote_speaker)}</p>')
                    story_parts.append(
                        f'<a class="small-link" href="{html_escape(q["source_url"])}">Source</a>'
                    )

                    quote_cards.append(
                        "\n".join(
                            [
                                '<article class="story-card">',
                                f'  <span class="story-index">{index:02d}</span>',
                                f'  <div class="story-body">{"".join(story_parts)}</div>',
                                "</article>",
                            ]
                        )
                    )
            else:
                quote_cards.append(
                    "\n".join(
                        [
                            '<article class="story-card story-mention">',
                            '  <span class="story-index">--</span>',
                            '  <div class="story-body"><p class="mention-note">Featured in this edition, but no direct quote block was extracted.</p></div>',
                            "</article>",
                        ]
                    )
                )

            edition_link = ""
            if edition_source_url:
                edition_link = f'<a class="small-link" href="{html_escape(edition_source_url)}">Full edition</a>'

            edition_sections.append(
                "\n".join(
                    [
                        '<section class="edition-chapter">',
                        '  <div class="chapter-head">',
                        "    <div>",
                        f'      <p class="chapter-date">{html_escape(format_date(edition_date))}</p>',
                        f"      <h3>{html_escape(edition_title)}</h3>",
                        f'      <p class="chapter-meta">{chapter_meta}</p>',
                        f"      {sector_line}" if sector_line else "",
                        "    </div>",
                        f"    {edition_link}",
                        "  </div>",
                        f'  <div class="storyline">{"".join(quote_cards)}</div>',
                        "</section>",
                    ]
                )
            )

        company_name_link = html_escape(company["name"])
        if company.get("url"):
            company_name_link = (
                f'<a class="company-title-link" href="{html_escape(company["url"])}">'
                f"{html_escape(company['name'])}</a>"
            )

        meta = "Edition-by-edition storyline from The Chatter archive."
        content = render_template(
            "company.html",
            {
                "company_name_link": company_name_link,
                "company_meta": meta,
                "edition_sections": "\n".join(edition_sections) if edition_sections else "<p>No quotes yet.</p>",
                "quote_count": str(company_quote_count),
                "first_date": format_date(min([d for d in dates if d] or [""])),
                "last_date": format_date(max([d for d in dates if d] or [""])),
                "editions_count": str(len(covered_edition_ids)),
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
    mentions = read_json(DATA_DIR / "company_mentions.json")
    companies, quotes, mentions = merge_company_variants(companies, quotes, mentions)

    build_index(companies, editions, quotes, mentions)
    build_company_pages(companies, editions, quotes, mentions)

    print("Site build complete")


if __name__ == "__main__":
    main()
