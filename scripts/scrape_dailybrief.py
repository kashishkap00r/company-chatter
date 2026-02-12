#!/usr/bin/env python3
"""Fetch Daily Brief posts and extract story blocks for company matching.

Outputs JSON files to data/:
- dailybrief_posts.json
- dailybrief_fetch_state.json

Behavior:
- Full bootstrap on first run.
- Incremental refresh on later runs using sitemap <lastmod> values.
- If a post fetch fails, cached data is retained when available.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SITEMAP_URL = "https://thedailybrief.zerodha.com/sitemap.xml"
POSTS_FILE = DATA_DIR / "dailybrief_posts.json"
STATE_FILE = DATA_DIR / "dailybrief_fetch_state.json"
REQUEST_DELAY_SECONDS = 0.12

INTRO_MARKER_RE = re.compile(r"in today(?:'|\u2019)s edition", re.IGNORECASE)
META_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*Substack\s*$", re.IGNORECASE)

NON_STORY_HEADING_KEYS = {
    "tidbits",
    "the bottomline",
    "bottomline",
    "ready for more",
}

PROMO_HEADING_PREFIXES = (
    "we re now on reddit",
    "check out",
    "have you checked out",
    "subscribe to",
)


@dataclass
class Node:
    tag: str
    text: str
    level: int
    in_list_item: bool


class StoryNodeExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[Node] = []
        self._current_tag: str | None = None
        self._current_text: list[str] = []
        self._list_item_depth = 0
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._flush()
            self._ignored_depth += 1
            return

        if self._ignored_depth > 0:
            return

        if tag == "li":
            self._list_item_depth += 1
            return

        if tag in {"h1", "h2", "h3", "p"}:
            self._flush()
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth > 0:
            self._flush()
            self._ignored_depth -= 1
            return

        if self._ignored_depth > 0:
            return

        if tag == "li":
            self._flush()
            self._list_item_depth = max(0, self._list_item_depth - 1)
            return

        if tag in {"h1", "h2", "h3", "p"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        if self._current_tag:
            self._current_text.append(data)

    def _flush(self) -> None:
        if not self._current_tag:
            return
        text = " ".join(" ".join(self._current_text).split())
        if text:
            level = int(self._current_tag[1]) if self._current_tag.startswith("h") else 0
            self.nodes.append(
                Node(
                    tag=self._current_tag,
                    text=text,
                    level=level,
                    in_list_item=self._list_item_depth > 0,
                )
            )
        self._current_tag = None
        self._current_text = []


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "CompanyChatterBot/0.2",
            "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def normalize_key(text: str) -> str:
    normalized = text.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def extract_meta_content(html: str, key: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+property="{re.escape(key)}"',
        rf'<meta[^>]+name="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+name="{re.escape(key)}"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split())
    return None


def to_iso_date(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.strip()
    if not candidate:
        return ""

    for splitter in ("T", " "):
        if splitter in candidate and len(candidate.split(splitter, 1)[0]) == 10:
            date_part = candidate.split(splitter, 1)[0]
            try:
                return datetime.fromisoformat(date_part).date().isoformat()
            except ValueError:
                pass

    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def extract_json_ld_date(html: str) -> str:
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
            date_iso = to_iso_date(str(date_value) if date_value else "")
            if date_iso:
                return date_iso
    return ""


def extract_preload_payload(html: str) -> dict[str, object] | None:
    match = re.search(
        r"window\._preloads\s*=\s*JSON\.parse\(\"((?:\\.|[^\\\"])*)\"\)",
        html,
        flags=re.DOTALL,
    )
    if not match:
        return None

    encoded = match.group(1)
    try:
        decoded = json.loads(f'"{encoded}"')
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) else None


def extract_article_html(html: str) -> str:
    match = re.search(r"<article[^>]*>(.*?)</article>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    return ""


def is_non_story_heading(title: str, post_title: str) -> bool:
    key = normalize_key(title)
    if not key:
        return True
    if key == normalize_key(post_title):
        return True
    if key in NON_STORY_HEADING_KEYS:
        return True
    if any(key.startswith(prefix) for prefix in PROMO_HEADING_PREFIXES):
        return True
    if key.startswith("thank you for reading"):
        return True
    return False


def heading_match_score(left: str, right: str) -> float:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        return 0.92
    return SequenceMatcher(None, left_key, right_key).ratio()


def collect_intro_list_titles(nodes: list[Node]) -> list[str]:
    marker_index: int | None = None
    for index, node in enumerate(nodes):
        if node.tag == "p" and INTRO_MARKER_RE.search(node.text):
            marker_index = index
            break

    if marker_index is None:
        return []

    list_titles: list[str] = []
    started = False
    for node in nodes[marker_index + 1 :]:
        if node.tag in {"h1", "h2", "h3"} and started:
            break
        if node.tag != "p":
            continue
        if node.in_list_item:
            started = True
            cleaned = node.text.strip().strip("-\u2022 ").strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if cleaned:
                list_titles.append(cleaned)
        elif started:
            # List ended.
            break

    unique_titles: list[str] = []
    seen: set[str] = set()
    for title in list_titles:
        key = normalize_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_titles.append(title)
    return unique_titles


def dedupe_story_heads(story_heads: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for head in story_heads:
        key = normalize_key(str(head["title"]))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(head)
    return deduped


def select_story_heads(nodes: list[Node], post_title: str) -> list[dict[str, object]]:
    headings = [
        {"index": idx, "title": node.text, "level": node.level}
        for idx, node in enumerate(nodes)
        if node.tag in {"h1", "h2", "h3"}
    ]

    filtered_h1 = [
        head
        for head in headings
        if head["level"] == 1 and not is_non_story_heading(str(head["title"]), post_title)
    ]

    list_titles = collect_intro_list_titles(nodes)
    selected: list[dict[str, object]] = []
    used_heading_indexes: set[int] = set()

    if list_titles:
        candidates = [head for head in headings if not is_non_story_heading(str(head["title"]), post_title)]
        for list_title in list_titles:
            best_candidate: dict[str, object] | None = None
            best_score = 0.0
            for head in candidates:
                head_index = int(head["index"])
                if head_index in used_heading_indexes:
                    continue
                score = heading_match_score(list_title, str(head["title"]))
                if score > best_score:
                    best_score = score
                    best_candidate = head

            if best_candidate and best_score >= 0.55:
                used_heading_indexes.add(int(best_candidate["index"]))
                selected.append(
                    {
                        "title": str(best_candidate["title"]),
                        "anchor": int(best_candidate["index"]),
                        "source": "intro_list+heading",
                    }
                )
            else:
                selected.append(
                    {
                        "title": list_title,
                        "anchor": None,
                        "source": "intro_list",
                    }
                )

    if not selected and filtered_h1:
        selected = [
            {
                "title": str(head["title"]),
                "anchor": int(head["index"]),
                "source": "h1",
            }
            for head in filtered_h1
        ]

    if not selected:
        fallback_h2 = [
            head
            for head in headings
            if head["level"] == 2 and not is_non_story_heading(str(head["title"]), post_title)
        ]
        if fallback_h2:
            selected = [
                {
                    "title": str(fallback_h2[0]["title"]),
                    "anchor": int(fallback_h2[0]["index"]),
                    "source": "h2_fallback",
                }
            ]

    if selected:
        remaining_h1 = [head for head in filtered_h1 if int(head["index"]) not in used_heading_indexes]
        remaining_h1.sort(key=lambda item: int(item["index"]))
        remaining_cursor = 0
        for item in selected:
            if item["anchor"] is not None:
                continue
            if remaining_cursor >= len(remaining_h1):
                break
            item["anchor"] = int(remaining_h1[remaining_cursor]["index"])
            item["source"] = "intro_list+h1_fallback"
            remaining_cursor += 1

    selected = dedupe_story_heads(selected)
    selected.sort(
        key=lambda item: (
            10**9 if item["anchor"] is None else int(item["anchor"]),
            normalize_key(str(item["title"])),
        )
    )
    return selected


def is_hard_break_heading(text: str) -> bool:
    key = normalize_key(text)
    if key in {"tidbits", "the bottomline", "bottomline"}:
        return True
    if any(key.startswith(prefix) for prefix in PROMO_HEADING_PREFIXES):
        return True
    if key.startswith("thank you for reading"):
        return True
    return False


def segment_text(nodes: list[Node], start: int, end: int, title: str) -> str:
    parts = [title]
    for node in nodes[start + 1 : end]:
        if node.tag == "p" and node.text.strip():
            parts.append(node.text.strip())
    return "\n".join(parts).strip()


def full_text(nodes: list[Node], title: str) -> str:
    parts = [title]
    for node in nodes:
        if node.tag == "p" and node.text.strip():
            parts.append(node.text.strip())
    return "\n".join(parts).strip()


def extract_story_entries(nodes: list[Node], post_title: str, post_url: str) -> list[dict[str, object]]:
    story_heads = select_story_heads(nodes, post_title)
    if not story_heads:
        fallback_title = post_title.strip() or "Story"
        fallback_text = full_text(nodes, fallback_title)
        return [
            {
                "story_id": slugify(f"{urlparse(post_url).path}-{fallback_title}"),
                "title": fallback_title,
                "position": 1,
                "source": "single_fallback",
                "text": fallback_text,
            }
        ]

    stories: list[dict[str, object]] = []
    for index, head in enumerate(story_heads, start=1):
        title = str(head["title"]).strip()
        anchor = head["anchor"]
        source = str(head["source"])

        if anchor is None:
            text = full_text(nodes, title)
        else:
            next_anchor = None
            for later in story_heads[index:]:
                if later["anchor"] is not None:
                    next_anchor = int(later["anchor"])
                    break

            end = next_anchor if next_anchor is not None else len(nodes)
            for boundary_index in range(int(anchor) + 1, end):
                boundary_node = nodes[boundary_index]
                if boundary_node.tag in {"h1", "h2", "h3"} and is_hard_break_heading(boundary_node.text):
                    end = boundary_index
                    break

            text = segment_text(nodes, int(anchor), end, title)
            if len(text.split()) < 30:
                text = full_text(nodes, title)

        stories.append(
            {
                "story_id": slugify(f"{urlparse(post_url).path}-{index}-{title}"),
                "title": title,
                "position": index,
                "source": source,
                "text": text,
            }
        )

    return stories


def parse_sitemap_entries(xml: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for url_block in re.findall(r"<url>(.*?)</url>", xml, flags=re.DOTALL):
        loc_match = re.search(r"<loc>(.*?)</loc>", url_block)
        if not loc_match:
            continue
        url = loc_match.group(1).strip()
        if not url.startswith("https://thedailybrief.zerodha.com/p/"):
            continue
        lastmod_match = re.search(r"<lastmod>(.*?)</lastmod>", url_block)
        lastmod = to_iso_date(lastmod_match.group(1).strip() if lastmod_match else "")
        entries.append({"url": url, "lastmod": lastmod})

    entries.sort(key=lambda item: item["url"])
    return entries


def parse_post_html(url: str, html: str, sitemap_lastmod: str) -> dict[str, object]:
    payload = extract_preload_payload(html)

    post_title = ""
    post_date = ""
    body_html = ""

    if payload and isinstance(payload.get("post"), dict):
        post = payload["post"]
        post_title = str(post.get("title") or "").strip()
        post_date = to_iso_date(str(post.get("post_date") or ""))
        body_html = str(post.get("body_html") or "")

    if not post_title:
        meta_title = extract_meta_content(html, "og:title") or extract_meta_content(html, "twitter:title")
        if meta_title:
            post_title = META_TITLE_SUFFIX_RE.sub("", meta_title).strip()

    if not post_date:
        post_date = to_iso_date(extract_meta_content(html, "article:published_time") or "")
    if not post_date:
        post_date = extract_json_ld_date(html)
    if not post_date:
        post_date = sitemap_lastmod

    if not body_html:
        body_html = extract_article_html(html)

    extractor = StoryNodeExtractor()
    extractor.feed(body_html)
    nodes = extractor.nodes

    if not post_title:
        post_title = " ".join(urlparse(url).path.split("/")[-1].replace("-", " ").split()).title()

    stories = extract_story_entries(nodes, post_title, url)

    normalized_stories: list[dict[str, object]] = []
    for story in stories:
        text = str(story.get("text") or "").strip()
        normalized_stories.append(
            {
                "story_id": str(story.get("story_id") or ""),
                "title": str(story.get("title") or "").strip() or post_title,
                "position": int(story.get("position") or 0),
                "source": str(story.get("source") or ""),
                "text": text,
                "word_count": len(text.split()),
            }
        )

    body_hash = hashlib.sha1(body_html.encode("utf-8", errors="ignore")).hexdigest()

    return {
        "url": url,
        "title": post_title,
        "date": post_date,
        "sitemap_lastmod": sitemap_lastmod,
        "content_hash": body_hash,
        "fetched_at": now_iso(),
        "stories": normalized_stories,
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing_posts_raw = read_json(POSTS_FILE, [])
    existing_posts = {
        str(item.get("url")): item
        for item in existing_posts_raw
        if isinstance(item, dict) and str(item.get("url", "")).startswith("https://thedailybrief.zerodha.com/p/")
    }

    existing_state_raw = read_json(STATE_FILE, {})
    existing_state_posts = {}
    if isinstance(existing_state_raw, dict):
        state_posts = existing_state_raw.get("posts", {})
        if isinstance(state_posts, dict):
            existing_state_posts = {
                str(url): value
                for url, value in state_posts.items()
                if isinstance(value, dict)
            }

    sitemap_xml = fetch(SITEMAP_URL)
    sitemap_entries = parse_sitemap_entries(sitemap_xml)
    if not sitemap_entries:
        raise SystemExit("No Daily Brief post URLs found in sitemap.xml")

    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    reused_cached = 0
    refreshed = 0
    fallback_cached = 0

    for index, entry in enumerate(sitemap_entries, start=1):
        url = entry["url"]
        lastmod = entry["lastmod"]
        cached_post = existing_posts.get(url)
        cached_state = existing_state_posts.get(url, {})

        cached_lastmod = str(cached_state.get("lastmod") or "")
        if cached_post and cached_lastmod and cached_lastmod == lastmod:
            results.append(cached_post)
            reused_cached += 1
            continue

        try:
            html = fetch(url)
            parsed_post = parse_post_html(url, html, lastmod)
            results.append(parsed_post)
            refreshed += 1
        except Exception as exc:  # pragma: no cover - network/data drift
            if cached_post:
                results.append(cached_post)
                fallback_cached += 1
            failures.append({"url": url, "error": str(exc)})

        if index % 20 == 0:
            print(f"Daily Brief processed {index}/{len(sitemap_entries)} posts...")

        time.sleep(REQUEST_DELAY_SECONDS)

    sitemap_urls = {entry["url"] for entry in sitemap_entries}
    results = [post for post in results if str(post.get("url")) in sitemap_urls]

    def sort_key(post: dict[str, object]) -> tuple[str, str]:
        return (str(post.get("date") or ""), str(post.get("url") or ""))

    results.sort(key=sort_key, reverse=True)

    write_json(POSTS_FILE, results)

    lastmod_by_url = {entry["url"]: entry["lastmod"] for entry in sitemap_entries}
    state_posts = {}
    for post in results:
        url = str(post.get("url") or "")
        if not url:
            continue
        state_posts[url] = {
            "lastmod": lastmod_by_url.get(url, ""),
            "content_hash": str(post.get("content_hash") or ""),
            "story_count": len(post.get("stories") or []),
            "fetched_at": str(post.get("fetched_at") or ""),
        }

    write_json(
        STATE_FILE,
        {
            "generated_at": now_iso(),
            "sitemap_url": SITEMAP_URL,
            "post_count": len(results),
            "posts": state_posts,
            "failures": failures,
        },
    )

    total_stories = sum(len(post.get("stories") or []) for post in results)
    print(f"Daily Brief sitemap posts: {len(sitemap_entries)}")
    print(f"Daily Brief cached unchanged: {reused_cached}")
    print(f"Daily Brief refreshed: {refreshed}")
    print(f"Daily Brief fallback-to-cache: {fallback_cached}")
    print(f"Daily Brief post records written: {len(results)}")
    print(f"Daily Brief story records written: {total_stories}")
    print(f"Daily Brief failures: {len(failures)}")


if __name__ == "__main__":
    main()
