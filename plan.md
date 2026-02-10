# Company Chatter Plan

Last updated: 2026-02-06
Workspace: `/home/kashish.kapoor/company-chatter`

## 1) Product Goal (Current)
Build a public company-intelligence site from The Chatter archive.

Current UX direction:
- Homepage is minimal and search-first (Google-like), with cream/off-white visual tone.
- Company pages are storyline-based by edition.
- Quote rendering prioritizes readability and clean narrative flow.
- Company title should link to Zerodha market page when a reliable URL exists.

## 2) Current Production State

### Live URLs
- Production domain: `https://company-chatter.pages.dev`
- Latest successful deployment URL: `https://1790257e.company-chatter.pages.dev`

### GitHub
- Repo: `git@github.com:kashishkap00r/company-chatter.git`
- Branch: `main`
- Latest pushed commit: `85c1b90` (`Simplify homepage and tighten market URL matching`)

### Cloudflare Pages Project
- Project name: `company-chatter`
- Build command: `python3 scripts/build_site.py`
- Output directory: `site`
- Config file: `wrangler.jsonc`

## 3) Data Snapshot (Current)
From current JSON files after latest scrape:
- Editions: `45` (`data/editions.json`)
- Raw companies: `349` (`data/companies.json`)
- Quotes: `1886` (`data/quotes.json`)
- Merged companies at build layer: `330`
- Zero-quote merged companies: `0`

Market-link coverage:
- Raw companies without URL in `data/companies.json`: `27`
- Generated company pages with linked title: `304 / 330`
- Generated company pages without linked title: `26 / 330`

Interpretation:
- Unlinked entities are mostly non-listed/global/editorial names (e.g. Netflix, Alibaba, Maersk, SEBI Chairman), and are intentionally left unlinked when no confident Zerodha match is found.

## 4) What Was Done This Session (Chronological)

### A) Removed noise/non-company cards and pages
Commit: `4ee587f`

What changed:
- `scripts/build_site.py`
  - filtered zero-quote companies from index rendering.
  - skipped company page generation for zero-quote companies.
  - clears `site/company/` on each build to remove stale pages.
- `scripts/scrape.py`
  - tightened company-heading heuristics to reject sector/editorial headings.
  - only emits company records that actually receive quotes in that edition.

Why:
- User-reported bad cards like `Global`, `Edition # 7`, sector labels.

Result:
- Noise cards removed in generated and deployed output.

### B) Major visual redesign + edition-grouped company timelines
Commit: `a24d29b`

What changed:
- `scripts/build_site.py`
  - company pages grouped quotes by edition (chapter-style blocks).
  - structured quote display improved.
- `templates/base.html`, `templates/index.html`, `templates/company.html`, `assets/styles.css`
  - full visual refresh.

Why:
- Improve visual quality and information hierarchy.

### C) Rework UX to minimalist search-first homepage and title-link behavior
Commit: `94465b6`

What changed:
- Homepage redesigned to search-forward aesthetic with background/floating concepts.
- Company title link behavior updated to use Zerodha URL when present.
- Added market URL enrichment logic in scraper based on Zerodha stocks search endpoint.

Why:
- Move toward cleaner UX and better link coverage.

### D) Final simplification + correctness tightening
Commit: `85c1b90`

What changed:
- `templates/index.html`, `assets/styles.css`, `scripts/build_site.py`
  - removed floating names.
  - restored minimal Google-like homepage:
    - cream background,
    - centered search,
    - no default results until user types,
    - compact result cards on query.
- `assets/styles.css`
  - improved company page readability:
    - better contrast,
    - cleaner body typography,
    - higher line-height,
    - simplified visual noise.
- `scripts/scrape.py`
  - stricter confidence gating for Zerodha URL matches to prevent false positives.
  - fixed known bad mapping: `Netflix` no longer maps to `NETTLINX`.

Why:
- User feedback: previous homepage became ugly; wanted simple/clean front page.
- User bug report: incorrect Netflix link must be prevented.

Result:
- Homepage now minimal and cleaner.
- Netflix page has no wrong market link.
- Valid listed names (e.g., Dixon, Pfizer, Signature Global) still link correctly.

## 5) Current Architecture (Post-Changes)

### Scrape Layer (`scripts/scrape.py`)
- Discovers posts via sitemap year pages.
- Filters target posts by title (`the chatter`) and excludes non-target series.
- Extracts content from `h2`, `h3`, `p`, `blockquote`.
- Parses quote/speaker with safeguards (prevents percentage-range mis-splits).
- Rejects heading-like pseudo-companies.
- Adds market URL enrichment using Zerodha search endpoint:
  - `https://zerodha.com/markets/stocks/search/?q=...`
- Applies strict confidence checks to avoid wrong links.

### Build Layer (`scripts/build_site.py`)
- Reads `data/editions.json`, `data/companies.json`, `data/quotes.json`.
- Merges naming variants before rendering.
- Homepage outputs minimal search UI backed by JSON dataset.
- Company pages render storyline chapters grouped by edition.
- Company title links to Zerodha only if URL exists.
- Clears stale company output on each rebuild.

### Templates/CSS
- `templates/index.html`: minimal search-first page.
- `templates/company.html`: storyline layout and metrics.
- `assets/styles.css`: cream-toned design and readability-first company typography.

## 6) Validations Performed (Latest)

Commands run repeatedly during this session:
- `python3 -m py_compile scripts/scrape.py scripts/build_site.py`
- `python3 scripts/scrape.py`
- `python3 scripts/build_site.py`
- `./scripts/deploy_pages.sh company-chatter`
- `npx wrangler pages deployment list --project-name company-chatter`

Key verified outcomes:
- Homepage:
  - `home-minimal` layout present,
  - no floating company background,
  - results hidden by default,
  - results appear on search query.
- Noise entries absent from searchable company dataset:
  - `Global`, `Edition # 7`, `Zomato`.
- `Netflix`:
  - no `company-title-link` (correct, because no confident market mapping),
  - no `Nettlinx` contamination.
- `Dixon`:
  - linked company title present,
  - readable storyline quote rendering present.

## 7) Important Operational Notes

### Cloudflare deployment behavior
- Running `./scripts/deploy_pages.sh` deploys the local built `site/` directly.
- Duplicate deployments may appear if deploy command is re-run quickly.

### requirements.txt caveat
- `requirements.txt` currently contains `requests==2.32.3` despite comments saying stdlib-only.
- This did not block latest deployment, but previous history showed dependency installs can break Pages builds when heavy compiled deps are listed.

### Local working tree context
- Existing local-only files not part of production logic may appear:
  - `plan.md` modified by design (this file)
  - `AGENTS.md` may remain untracked locally

## 8) Known Gaps / Pending Decisions

1. URL enrichment is intentionally conservative now.
- Prevents false mappings, but leaves more companies unlinked.
- Current unlinked set includes many global or editorial entities unlikely to exist on Zerodha.

2. Some quoted contexts in source are sparse.
- Rendering handles missing context/speaker gracefully.

3. Optional future enhancement:
- Add explicit allow/deny mapping file for special-company URL overrides.
- This can increase link coverage while preserving correctness.

## 9) Next Session Starting Point (Actionable)

If continuing immediately, do this in order:
1. Decide whether to keep strict URL matching or add curated overrides for selected unlinked India-listed names.
2. If adding overrides, create a small manual mapping table in scraper/build layer and re-scrape.
3. Run validation sweep:
   - ensure no false matches (especially Netflix-like errors),
   - verify mapped overrides are correct.
4. Rebuild + deploy.
5. Update this plan with:
   - new commit hash,
   - updated linked/unlinked counts,
   - deployment URL.

## 10) Runbook

### Scrape
```bash
cd /home/kashish.kapoor/company-chatter
python3 scripts/scrape.py
```

### Build
```bash
cd /home/kashish.kapoor/company-chatter
python3 scripts/build_site.py
```

### Preview local
```bash
cd /home/kashish.kapoor/company-chatter
./scripts/preview.sh
```

### Deploy
```bash
cd /home/kashish.kapoor/company-chatter
./scripts/deploy_pages.sh company-chatter
```

### Check deployments
```bash
cd /home/kashish.kapoor/company-chatter
npx wrangler pages deployment list --project-name company-chatter
```
