# Company Chatter

Turns The Chatter Substack archive into company-centric pages with a timeline of quotes.

## MVP Scope
- Scrape all historical posts titled "The Chatter".
- Extract company sections and quote blocks/quoted paragraphs.
- Store quotes with company mapping and source URL.
- Merge repeated company mentions across editions and render each company timeline oldest to newest.

## Run (No sudo required)
```bash
python3 scripts/scrape.py
```

Output JSON files will be written to `data/`:
- `data/editions.json`
- `data/companies.json`
- `data/quotes.json`

## Build Static Site
```bash
python3 scripts/build_site.py
```

Output will be in `site/`.

## Local Preview
```bash
./scripts/preview.sh
```

Default preview URL is `http://localhost:8787`.
Use a custom port with `./scripts/preview.sh 8000`.

## Cloudflare Pages
This repo includes `wrangler.jsonc` with `pages_build_output_dir = "site"`.

### One-time auth
```bash
npx wrangler whoami
# if not logged in:
npx wrangler login
```

### Deploy from local build
```bash
./scripts/deploy_pages.sh company-chatter
```

### Git-based deployment in Cloudflare dashboard
Use these build settings:
- Build command: `python3 scripts/build_site.py`
- Build output directory: `site`

If you want fresh archive data on every deploy, use:
- Build command: `python3 scripts/scrape.py && python3 scripts/build_site.py`
