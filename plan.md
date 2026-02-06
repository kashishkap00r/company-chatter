# Company Chatter Plan

Date: 2026-02-06

## Goal
Build a free, public website that turns the existing Substack archive of The Chatter into company-centric pages. Each company page should show a timeline of highlighted quotes pulled from past editions. MVP is quotes only (no pitch deck images yet). The site will be hosted on Cloudflare and the code in GitHub.

## Current Decisions
- Audience: all teams (public audience)
- Access: free
- Source: Substack archive at `https://thechatterbyzerodha.substack.com/`
- History: all editions
- MVP: quotes only, company pages with a timeline

## Open Questions
- None about stack; assistant will choose a beginner-friendly stack.

## Next Steps
- Inspect Substack archive structure and plan ingestion.
- Choose stack and outline repo structure.
- Build a scraper/extractor and a static site generator.

## Discovery Notes (2026-02-06)
- The Chatter uses a custom domain `thechatter.zerodha.com` with a sitemap at `/sitemap` and year pages like `/sitemap/2025`. These pages list all posts as links and can be used to enumerate the archive.
- Individual post pages (e.g. `The Chatter: Echoes from the Boardroom`) include the full text content in HTML, with quote blocks that can be extracted.

## Proposed Stack (MVP)
- Python scraper + extractor
- SQLite for storage during processing
- Static site output (HTML generated via Jinja2 templates)
- Deploy via Cloudflare Pages

## Parsing Approach (MVP)
- Use year sitemap pages (e.g., `/sitemap/2025`, `/sitemap/2026`) to enumerate all posts.
- For each post:
  - Extract title, date, and main content HTML.
  - Parse sections where:
    - `H2` = industry/sector heading.
    - `H3` = company heading (company name + meta like market cap + sector).
    - Quote blocks (`blockquote`) following the company header are the primary quote units.
    - Preceding paragraph lines (not blockquote) can be treated as context/summary.
  - Map company by the `H3` title text (auto-mapping based on exact header).
- Output quote records with company, date, edition title, quote text, speaker line (if present), and source URL.

## MVP Scope (Quotes Only)
- Company pages with a chronological timeline of quotes.
- No pitch deck images yet.
- No manual curation required in v1 (auto-mapping via H3 headers).

## Environment Constraint (2026-02-06)
- No sudo privileges; cannot install pip/venv packages.
- Scraper rewritten to use only Python stdlib (no external dependencies).

## Static Site Generator (2026-02-06)
- Added `scripts/build_site.py` to generate a static site from JSON outputs.
- Added templates in `templates/` and CSS in `assets/styles.css`.
- Site output goes to `site/` (index + per-company pages).

## Status Summary (2026-02-06)
- Created project directory: `/home/kashish.kapoor/company-chatter`.
- Scraper built at `scripts/scrape.py` (stdlib only, no pip/venv). It crawls sitemap, filters “The Chatter” posts only, extracts H2/H3/blockquote sections, and preserves company link from H3.
- Build generator built at `scripts/build_site.py` to generate static HTML pages in `site/`.
- Templates and styles added in `templates/` and `assets/`.
- Network access currently failing in this environment (DNS resolution error), so scraper cannot run here; run it when network works.

## Run Commands
- Scrape:
  `cd /home/kashish.kapoor/company-chatter && python3 scripts/scrape.py`
- Build site:
  `cd /home/kashish.kapoor/company-chatter && python3 scripts/build_site.py`

## Next Steps (Tomorrow)
1. Re-run scraper when network access works.
2. Inspect JSON output in `data/`.
3. Run `scripts/build_site.py` and check output in `site/`.
4. Add preview server + Cloudflare Pages config.
