#!/usr/bin/env python3
"""Refresh Zerodha NSE stock index from the markets sitemap."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_OUTPUT = DATA_DIR / "zerodha_nse_stock_index.json"
DEFAULT_SITEMAP_URL = "https://zerodha.com/markets/stocks/sitemap.xml"
NSE_STOCK_URL_RE = re.compile(r"^https://zerodha\.com/markets/stocks/NSE/([A-Z0-9._&-]+)/$")

PLAYWRIGHT_NODE_SCRIPT = r"""
const { chromium } = require("playwright");

const targetUrl = process.env.TARGET_URL;
const timeoutMs = Number(process.env.TARGET_TIMEOUT_MS || "120000");
const waitStepMs = 2500;
const maxSteps = Math.max(1, Math.floor(timeoutMs / waitStepMs));

function hasSitemapXml(text) {
  return typeof text === "string" && text.includes("<urlset") && text.includes("<loc>");
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    viewport: { width: 1366, height: 768 },
  });
  const page = await context.newPage();

  try {
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });

    for (let step = 0; step < maxSteps; step += 1) {
      const bodyText = await page.evaluate(() => (document.body ? document.body.innerText : ""));
      if (bodyText && !/Just a moment/i.test(bodyText)) {
        break;
      }
      await page.waitForTimeout(waitStepMs);
    }

    let xml = "";
    const fetched = await page.evaluate(async (url) => {
      try {
        const response = await fetch(url, { credentials: "include" });
        const body = await response.text();
        return {
          ok: true,
          status: response.status,
          contentType: response.headers.get("content-type") || "",
          body,
        };
      } catch (error) {
        return {
          ok: false,
          error: String(error),
        };
      }
    }, targetUrl);

    if (fetched.ok && hasSitemapXml(fetched.body)) {
      xml = fetched.body;
    }

    if (!xml) {
      const preText = await page.evaluate(() => {
        const pre = document.querySelector("pre");
        return pre ? pre.textContent || "" : "";
      });
      if (hasSitemapXml(preText)) {
        xml = preText;
      }
    }

    if (!xml) {
      const pageHtml = await page.content();
      if (hasSitemapXml(pageHtml)) {
        xml = pageHtml;
      }
    }

    if (!xml) {
      throw new Error("Unable to extract sitemap XML from browser response.");
    }
    process.stdout.write(xml);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def _extract_xml_payload(text: str) -> str:
    for marker in ("<?xml", "<urlset"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:].strip()
    return text.strip()


def _fetch_sitemap_via_http(url: str) -> str | None:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError):
        return None
    payload = _extract_xml_payload(payload)
    if "<urlset" not in payload or "<loc>" not in payload:
        return None
    return payload


def _fetch_sitemap_via_playwright(url: str) -> str:
    shell_script = (
        "NODE_PATH=$(echo \"$PATH\" | cut -d: -f1 | sed 's#/\\.bin##'); "
        "export NODE_PATH; "
        "node - <<'NODE'\n"
        f"{PLAYWRIGHT_NODE_SCRIPT}\n"
        "NODE"
    )
    env = os.environ.copy()
    env["TARGET_URL"] = url
    env["TARGET_TIMEOUT_MS"] = "120000"

    completed = subprocess.run(
        ["npm", "exec", "--yes", "--package=playwright", "--", "sh", "-c", shell_script],
        cwd=str(BASE_DIR),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown playwright error"
        raise RuntimeError(f"Playwright sitemap fetch failed: {message}")

    payload = _extract_xml_payload(completed.stdout)
    if "<urlset" not in payload or "<loc>" not in payload:
        raise RuntimeError("Playwright sitemap fetch returned non-XML payload.")
    return payload


def _parse_nse_entries(xml_payload: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_payload)
    nse_urls_by_symbol: dict[str, str] = {}
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag != "loc":
            continue
        loc = (elem.text or "").strip()
        match = NSE_STOCK_URL_RE.fullmatch(loc)
        if not match:
            continue
        symbol = match.group(1).upper()
        nse_urls_by_symbol[symbol] = loc

    return [{"symbol": symbol, "url": nse_urls_by_symbol[symbol]} for symbol in sorted(nse_urls_by_symbol)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Zerodha NSE stock index.")
    parser.add_argument("--source-url", default=DEFAULT_SITEMAP_URL, help="Zerodha stock sitemap URL")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON file path")
    parser.add_argument(
        "--force-playwright",
        action="store_true",
        help="Skip direct HTTP fetch and force browser-based fetch",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    sitemap_url = args.source_url.strip()
    if not sitemap_url:
        raise SystemExit("source URL must not be empty")

    xml_payload = None if args.force_playwright else _fetch_sitemap_via_http(sitemap_url)
    fetch_method = "http"
    if not xml_payload:
        xml_payload = _fetch_sitemap_via_playwright(sitemap_url)
        fetch_method = "playwright"

    entries = _parse_nse_entries(xml_payload)
    if not entries:
        raise SystemExit("No NSE stock entries found in sitemap payload.")

    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_url": sitemap_url,
        "fetch_method": fetch_method,
        "entry_count": len(entries),
        "entries": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    print(f"Wrote {len(entries)} NSE entries to {output_path}")
    print(f"Fetch method: {fetch_method}")


if __name__ == "__main__":
    main()
