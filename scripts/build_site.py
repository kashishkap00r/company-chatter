#!/usr/bin/env python3
"""Build a static site from the extracted JSON data.

Inputs:
- data/editions.json
- data/companies.json
- data/quotes.json
- data/company_mentions.json
- data/dailybrief_posts.json (optional)

Outputs:
- site/index.html
- site/company/<slug>/index.html
- site/assets/styles.css
- data/entity_resolution_report.json
- data/dailybrief_story_mentions.json
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

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
ACRONYM_EXPANSIONS = {
    "amc": ["asset", "management", "company"],
}
ACRONYM_SUFFIX_STRIP_TOKENS = LEGAL_SUFFIX_TOKENS - {"company", "co"}
MARKET_EXCHANGES = {"NSE", "BSE"}
ENTITY_ALIAS_RULES_FILE = DATA_DIR / "entity_alias_rules.json"
ENTITY_BLOCK_RULES_FILE = DATA_DIR / "entity_block_rules.json"
NON_COMPANY_RULES_FILE = DATA_DIR / "non_company_rules.json"
ENTITY_RESOLUTION_REPORT_FILE = DATA_DIR / "entity_resolution_report.json"
DAILYBRIEF_POSTS_FILE = DATA_DIR / "dailybrief_posts.json"
DAILYBRIEF_ALIAS_RULES_FILE = DATA_DIR / "dailybrief_alias_rules.json"
DAILYBRIEF_STORY_MENTIONS_FILE = DATA_DIR / "dailybrief_story_mentions.json"
TOKEN_EQUIVALENTS = {
    "tech": "technology",
    "technologies": "technology",
    "inds": "industries",
    "hathaway": "hathway",
    "prod": "products",
}
SOFT_TOKENS = {
    "india",
    "indian",
    "group",
    "global",
    "international",
    "holding",
    "holdings",
}
INITIALISM_IGNORED_TOKENS = {
    "and",
    "of",
    "the",
}
COMPANY_HINT_TOKENS = {
    "bank",
    "bancorp",
    "bancshares",
    "beverages",
    "bio",
    "biosciences",
    "capital",
    "chemicals",
    "company",
    "communications",
    "corp",
    "corporation",
    "energy",
    "engineering",
    "financial",
    "foods",
    "group",
    "holding",
    "holdings",
    "inc",
    "industries",
    "insurance",
    "international",
    "labs",
    "limited",
    "ltd",
    "motors",
    "pharma",
    "pharmaceuticals",
    "plc",
    "private",
    "pvt",
    "retail",
    "sa",
    "systems",
    "technologies",
    "technology",
}
SENTENCE_START_TOKENS = {
    "we",
    "we've",
    "our",
    "this",
    "that",
    "these",
    "those",
    "broader",
    "sectoral",
    "check",
    "have",
    "introducing",
    "given",
    "are",
}
DAILYBRIEF_VISIBLE_DEFAULT = 3
CHATTER_VISIBLE_QUOTES_DEFAULT = 2
FEATURED_COMPANY_SLUGS = [
    "hdfc-bank",
    "reliance-industries",
    "hindustan-unilever",
    "adani-green",
    "tata-motors",
]


def read_json(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _load_non_company_rules(path: Path) -> dict[str, object]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exact_name_keys": set(), "allow_name_keys": set(), "name_patterns": []}

    exact_names = payload.get("exact_names", [])
    allow_names = payload.get("allow_names", [])
    name_patterns = payload.get("name_patterns", [])
    if not isinstance(exact_names, list) or not isinstance(allow_names, list) or not isinstance(name_patterns, list):
        return {"exact_name_keys": set(), "allow_name_keys": set(), "name_patterns": []}

    compiled_patterns: list[re.Pattern[str]] = []
    for pattern in name_patterns:
        pattern_text = str(pattern).strip()
        if not pattern_text:
            continue
        try:
            compiled_patterns.append(re.compile(pattern_text, flags=re.IGNORECASE))
        except re.error:
            continue

    return {
        "exact_name_keys": {_normalize_name_key(str(name)) for name in exact_names if str(name).strip()},
        "allow_name_keys": {_normalize_name_key(str(name)) for name in allow_names if str(name).strip()},
        "name_patterns": compiled_patterns,
    }


def render_template(template_name: str, context: dict[str, str]) -> str:
    template = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace("{{ " + key + " }}", value)
    return template


def wrap_base(
    title: str,
    content: str,
    *,
    updated_iso: str,
    updated_relative: str,
    body_class: str = "",
    asset_version: str,
    header_search_html: str = "",
) -> str:
    return render_template(
        "base.html",
        {
            "title": title,
            "content": content,
            "updated_iso": updated_iso,
            "updated_relative": updated_relative,
            "body_class": body_class,
            "asset_version": asset_version,
            "header_search_html": header_search_html,
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
    raw_tokens = re.findall(r"[a-z0-9]+", name.lower())
    normalized = [TOKEN_EQUIVALENTS.get(token, token) for token in raw_tokens]
    return normalized


def _has_legal_suffix(name: str) -> bool:
    tokens = _name_tokens(name)
    return bool(tokens) and tokens[-1] in LEGAL_SUFFIX_TOKENS


def _strip_suffix_tokens(tokens: list[str], suffixes: set[str]) -> list[str]:
    stripped = list(tokens)
    while stripped and stripped[-1] in suffixes:
        stripped.pop()
    return stripped


def _expand_alias_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for index, token in enumerate(tokens):
        if token in ACRONYM_EXPANSIONS and index == len(tokens) - 1:
            expanded.extend(ACRONYM_EXPANSIONS[token])
        else:
            expanded.append(token)
    return expanded


def _normalized_name_tokens(name: str) -> list[str]:
    tokens = _expand_alias_tokens(_name_tokens(name))
    return _strip_suffix_tokens(tokens, LEGAL_SUFFIX_TOKENS)


def _company_name_key(name: str) -> str:
    return " ".join(_normalized_name_tokens(name))


def _rule_key(name: str) -> str:
    tokens = _normalized_name_tokens(name)
    return " ".join(tokens)


def _load_rule_pairs(path: Path, key: str) -> set[frozenset[str]]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        return set()

    raw_pairs = payload.get(key, [])
    if not isinstance(raw_pairs, list):
        return set()

    parsed: set[frozenset[str]] = set()
    for item in raw_pairs:
        if not isinstance(item, list) or len(item) != 2:
            continue
        left = _rule_key(str(item[0] or ""))
        right = _rule_key(str(item[1] or ""))
        if left and right and left != right:
            parsed.add(frozenset({left, right}))
    return parsed


def _matches_trailing_initialism(short_tokens: list[str], long_tokens: list[str]) -> bool:
    shared_prefix = 0
    for left, right in zip(short_tokens, long_tokens):
        if left != right:
            break
        shared_prefix += 1

    short_tail = short_tokens[shared_prefix:]
    long_tail = long_tokens[shared_prefix:]
    if len(short_tail) != 1 or len(long_tail) < 2:
        return False

    short_value = short_tail[0]
    initials = "".join(token[0] for token in long_tail if token)
    return len(short_value) >= 2 and short_value == initials


def _matches_full_initialism(short_tokens: list[str], long_tokens: list[str]) -> bool:
    if len(short_tokens) != 1 or len(long_tokens) < 2:
        return False

    short_value = short_tokens[0]
    initials = "".join(token[0] for token in long_tokens if token not in INITIALISM_IGNORED_TOKENS)
    return len(short_value) >= 2 and short_value == initials


def _is_soft_extension(short_tokens: list[str], long_tokens: list[str]) -> bool:
    if not short_tokens or len(short_tokens) > len(long_tokens):
        return False
    if long_tokens[: len(short_tokens)] != short_tokens:
        return False
    tail = long_tokens[len(short_tokens) :]
    return bool(tail) and all(token in SOFT_TOKENS for token in tail)


def _are_company_names_compatible(
    left_name: str,
    right_name: str,
    alias_pairs: set[frozenset[str]] | None = None,
    block_pairs: set[frozenset[str]] | None = None,
) -> bool:
    left_key = _rule_key(left_name)
    right_key = _rule_key(right_name)
    if not left_key or not right_key:
        return False

    pair_key = frozenset({left_key, right_key})
    if block_pairs and pair_key in block_pairs:
        return False
    if alias_pairs and pair_key in alias_pairs:
        return True

    left_normalized = _normalized_name_tokens(left_name)
    right_normalized = _normalized_name_tokens(right_name)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True
    if "".join(left_normalized) == "".join(right_normalized):
        return True

    similarity = SequenceMatcher(None, " ".join(left_normalized), " ".join(right_normalized)).ratio()
    if similarity >= 0.93:
        return True

    shorter, longer = (
        (left_normalized, right_normalized)
        if len(left_normalized) <= len(right_normalized)
        else (right_normalized, left_normalized)
    )
    if len(shorter) >= 3 and longer[: len(shorter)] == shorter:
        return True
    if len(shorter) == 1 and _is_soft_extension(shorter, longer):
        return True

    shorter_set = set(shorter)
    longer_set = set(longer)
    overlap = shorter_set.intersection(longer_set)
    if len(shorter) >= 2 and overlap == shorter_set:
        return True

    left_for_acronym = _strip_suffix_tokens(_name_tokens(left_name), ACRONYM_SUFFIX_STRIP_TOKENS)
    right_for_acronym = _strip_suffix_tokens(_name_tokens(right_name), ACRONYM_SUFFIX_STRIP_TOKENS)
    return (
        _matches_trailing_initialism(left_for_acronym, right_for_acronym)
        or _matches_trailing_initialism(right_for_acronym, left_for_acronym)
        or _matches_full_initialism(left_for_acronym, right_for_acronym)
        or _matches_full_initialism(right_for_acronym, left_for_acronym)
    )


def _has_company_hint(words: list[str]) -> bool:
    return any(token in COMPANY_HINT_TOKENS for token in words)


def _looks_like_topic_or_sentence(name: str) -> bool:
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9&'.-]+", name)]
    if not words:
        return False

    first_word = words[0]
    if first_word in SENTENCE_START_TOKENS and len(words) > 4:
        return True

    lowered = " ".join(words)
    if re.search(r"\bcomments?\s+on\b", lowered):
        return True

    if "on" in words and len(words) >= 4 and not _has_company_hint(words):
        return True

    if any(token in {"minister", "secretary"} for token in words) and "on" in words:
        return True
    return False


def _matches_non_company_rules(name: str, rules: dict[str, object]) -> bool:
    name_key = _normalize_name_key(name)
    allow_name_keys = rules.get("allow_name_keys", set())
    if name_key in allow_name_keys:
        return False

    exact_name_keys = rules.get("exact_name_keys", set())
    if name_key in exact_name_keys:
        return True

    for pattern in rules.get("name_patterns", []):
        if pattern.search(name):
            return True
    return False


def _market_key_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"zerodha.com", "thechatter.zerodha.com"}:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4:
        return None
    if parts[0].lower() != "markets" or parts[1].lower() != "stocks":
        return None

    exchange = parts[2].upper()
    symbol = parts[3].upper()
    if exchange not in MARKET_EXCHANGES:
        return None
    if not re.fullmatch(r"[A-Z0-9._&-]+", symbol):
        return None
    return f"{exchange}:{symbol}"


def _select_canonical_url(variants: list[dict]) -> str | None:
    urls = [str(c.get("url") or "").strip() for c in variants if str(c.get("url") or "").strip()]
    if not urls:
        return None
    for url in urls:
        if _market_key_from_url(url):
            return url
    return None


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
) -> tuple[list[dict], list[dict], list[dict], dict[str, object]]:
    alias_pairs = _load_rule_pairs(ENTITY_ALIAS_RULES_FILE, "aliases")
    block_pairs = _load_rule_pairs(ENTITY_BLOCK_RULES_FILE, "blocks")
    non_company_rules = _load_non_company_rules(NON_COMPANY_RULES_FILE)
    block_pairs.add(
        frozenset(
            {
                _rule_key("Reliance Consumer Products"),
                _rule_key("Reliance Industries"),
            }
        )
    )

    quote_count_by_company: dict[str, int] = {}
    for q in quotes:
        quote_count_by_company[q["company_id"]] = quote_count_by_company.get(q["company_id"], 0) + 1

    mention_count_by_company: dict[str, int] = {}
    for m in mentions:
        mention_count_by_company[m["company_id"]] = mention_count_by_company.get(m["company_id"], 0) + 1

    companies_by_id = {c["id"]: dict(c) for c in companies}
    market_key_by_company_id = {c["id"]: _market_key_from_url(c.get("url")) for c in companies}

    parent = {c["id"]: c["id"] for c in companies}
    quarantined_company_reason: dict[str, str] = {}
    for company in companies:
        company_name = str(company.get("name", "")).strip()
        if not company_name:
            continue
        if _matches_non_company_rules(company_name, non_company_rules) or _looks_like_topic_or_sentence(company_name):
            quarantined_company_reason[company["id"]] = "non_company_label"

    def find(company_id: str) -> str:
        root = company_id
        while parent[root] != root:
            root = parent[root]
        while parent[company_id] != company_id:
            next_id = parent[company_id]
            parent[company_id] = root
            company_id = next_id
        return root

    def union(left_id: str, right_id: str) -> None:
        left_root = find(left_id)
        right_root = find(right_id)
        if left_root == right_root:
            return
        parent[right_root] = left_root

    def _component_score(company_ids: list[str]) -> tuple[int, int]:
        quote_score = sum(quote_count_by_company.get(company_id, 0) for company_id in company_ids)
        mention_score = sum(mention_count_by_company.get(company_id, 0) for company_id in company_ids)
        return (quote_score * 10 + mention_score * 3, len(company_ids))

    # Explicit alias rules always merge when present.
    company_ids_by_rule_key: dict[str, list[str]] = {}
    for company in companies:
        company_ids_by_rule_key.setdefault(_rule_key(company["name"]), []).append(company["id"])

    for pair in alias_pairs:
        if len(pair) != 2:
            continue
        left_key, right_key = sorted(pair)
        for left_id in company_ids_by_rule_key.get(left_key, []):
            for right_id in company_ids_by_rule_key.get(right_key, []):
                if left_id != right_id:
                    union(left_id, right_id)

    market_conflicts: list[dict[str, object]] = []
    cross_bucket_merges: list[dict[str, object]] = []

    # Ticker-first: merge companies sharing the same exchange/symbol when names
    # are compatible. If a market group still has multiple incompatible
    # components, keep the strongest component and quarantine mention-only noise;
    # components with quote coverage are kept but detached from the market key.
    market_groups: dict[str, list[str]] = {}
    for company in companies:
        market_key = market_key_by_company_id[company["id"]]
        if market_key:
            market_groups.setdefault(market_key, []).append(company["id"])

    for group_ids in market_groups.values():
        for left_index, left_id in enumerate(group_ids):
            for right_id in group_ids[left_index + 1 :]:
                left_name = companies_by_id[left_id]["name"]
                right_name = companies_by_id[right_id]["name"]
                if _are_company_names_compatible(left_name, right_name, alias_pairs, block_pairs):
                    union(left_id, right_id)

    for market_key, group_ids in market_groups.items():
        component_map: dict[str, list[str]] = {}
        for company_id in group_ids:
            component_map.setdefault(find(company_id), []).append(company_id)
        if len(component_map) <= 1:
            continue

        primary_root = max(component_map.keys(), key=lambda root: _component_score(component_map[root]))
        conflict_components = []
        for root, component_ids in component_map.items():
            component_quote_count = sum(quote_count_by_company.get(company_id, 0) for company_id in component_ids)
            component_mention_count = sum(mention_count_by_company.get(company_id, 0) for company_id in component_ids)
            conflict_components.append(
                {
                    "root": root,
                    "is_primary": root == primary_root,
                    "quote_count": component_quote_count,
                    "mention_count": component_mention_count,
                    "members": [
                        {
                            "id": company_id,
                            "name": companies_by_id[company_id]["name"],
                        }
                        for company_id in sorted(component_ids)
                    ],
                }
            )

            if root == primary_root:
                continue
            if component_quote_count == 0:
                for company_id in component_ids:
                    quarantined_company_reason[company_id] = "market_key_conflict_mentions_only"
                continue

            for company_id in component_ids:
                companies_by_id[company_id]["url"] = None
                market_key_by_company_id[company_id] = None

        market_conflicts.append(
            {
                "market_key": market_key,
                "components": conflict_components,
            }
        )

    # Merge remaining variants by normalized name key, but do not force-merge
    # entities with conflicting explicit market identities.
    name_groups: dict[str, list[str]] = {}
    for company in companies:
        if company["id"] in quarantined_company_reason:
            continue
        name_key = _company_name_key(company["name"]) or company["id"]
        name_groups.setdefault(name_key, []).append(company["id"])

    for group_ids in name_groups.values():
        for left_index, left_id in enumerate(group_ids):
            for right_id in group_ids[left_index + 1 :]:
                if left_id in quarantined_company_reason or right_id in quarantined_company_reason:
                    continue
                left_market = market_key_by_company_id.get(left_id)
                right_market = market_key_by_company_id.get(right_id)
                left_name = companies_by_id[left_id]["name"]
                right_name = companies_by_id[right_id]["name"]
                pair_key = frozenset({_rule_key(left_name), _rule_key(right_name)})
                if pair_key in block_pairs:
                    continue
                if left_market and right_market and left_market != right_market and pair_key not in alias_pairs:
                    continue
                if _are_company_names_compatible(left_name, right_name, alias_pairs, block_pairs):
                    union(left_id, right_id)

    # Cross-bucket merge pass: catch acronym/full-name or slug/symbol variants
    # that are compatible but ended up in separate buckets (for example:
    # "SBI" vs "State Bank of India"), while still preventing market conflicts.
    def _current_components() -> dict[str, list[str]]:
        components: dict[str, list[str]] = {}
        for company in companies:
            company_id = company["id"]
            if company_id in quarantined_company_reason:
                continue
            components.setdefault(find(company_id), []).append(company_id)
        return components

    components = _current_components()
    component_roots = list(components.keys())
    component_market_keys: dict[str, set[str]] = {}
    component_anchor_id: dict[str, str] = {}
    for root, component_ids in components.items():
        component_market_keys[root] = {
            market_key_by_company_id.get(company_id)
            for company_id in component_ids
            if market_key_by_company_id.get(company_id)
        }
        component_anchor_id[root] = max(
            component_ids,
            key=lambda company_id: (
                quote_count_by_company.get(company_id, 0),
                mention_count_by_company.get(company_id, 0),
                1 if market_key_by_company_id.get(company_id) else 0,
                companies_by_id[company_id]["name"].lower(),
            ),
        )

    for left_index, left_root in enumerate(component_roots):
        left_anchor_id = component_anchor_id[left_root]
        left_anchor_name = companies_by_id[left_anchor_id]["name"]
        left_market_keys = component_market_keys[left_root]

        for right_root in component_roots[left_index + 1 :]:
            right_anchor_id = component_anchor_id[right_root]
            if find(left_anchor_id) == find(right_anchor_id):
                continue

            right_anchor_name = companies_by_id[right_anchor_id]["name"]
            right_market_keys = component_market_keys[right_root]
            pair_key = frozenset({_rule_key(left_anchor_name), _rule_key(right_anchor_name)})
            if pair_key in block_pairs:
                continue

            # Allow only when market identity is equal or absent on one side.
            if left_market_keys and right_market_keys and left_market_keys != right_market_keys:
                continue

            if not _are_company_names_compatible(left_anchor_name, right_anchor_name, alias_pairs, block_pairs):
                continue

            union(left_anchor_id, right_anchor_id)
            cross_bucket_merges.append(
                {
                    "left_root": left_root,
                    "right_root": right_root,
                    "left_anchor": {"id": left_anchor_id, "name": left_anchor_name},
                    "right_anchor": {"id": right_anchor_id, "name": right_anchor_name},
                    "left_market_keys": sorted(left_market_keys),
                    "right_market_keys": sorted(right_market_keys),
                }
            )

    grouped_company_ids: dict[str, list[str]] = {}
    for company in companies:
        if company["id"] in quarantined_company_reason:
            continue
        grouped_company_ids.setdefault(find(company["id"]), []).append(company["id"])

    # Enforce pairwise compatibility inside each merged component to prevent
    # transitive merges from pulling weakly related names together.
    refined_grouped_company_ids: dict[str, list[str]] = {}
    for root, component_ids in grouped_company_ids.items():
        if len(component_ids) <= 1:
            refined_grouped_company_ids[root] = component_ids
            continue

        sorted_component_ids = sorted(
            component_ids,
            key=lambda company_id: (
                quote_count_by_company.get(company_id, 0),
                mention_count_by_company.get(company_id, 0),
                1 if market_key_by_company_id.get(company_id) else 0,
                companies_by_id[company_id]["name"].lower(),
            ),
            reverse=True,
        )
        clusters: list[list[str]] = []
        for company_id in sorted_component_ids:
            company_name = companies_by_id[company_id]["name"]
            placed = False
            for cluster in clusters:
                if all(
                    _are_company_names_compatible(
                        company_name,
                        companies_by_id[other_company_id]["name"],
                        alias_pairs,
                        block_pairs,
                    )
                    for other_company_id in cluster
                ):
                    cluster.append(company_id)
                    placed = True
                    break
            if not placed:
                clusters.append([company_id])

        if len(clusters) == 1:
            refined_grouped_company_ids[root] = clusters[0]
            continue

        for cluster_index, cluster in enumerate(clusters):
            cluster_root = f"{root}#{cluster_index}"
            refined_grouped_company_ids[cluster_root] = cluster

    grouped_company_ids = refined_grouped_company_ids

    alias_map: dict[str, str] = {}
    merged_companies: list[dict] = []
    merged_groups: list[dict[str, object]] = []

    for component_ids in grouped_company_ids.values():
        variants = [companies_by_id[company_id] for company_id in component_ids]
        component_market_keys = {
            market_key_by_company_id.get(company_id)
            for company_id in component_ids
            if market_key_by_company_id.get(company_id)
        }
        primary = max(
            variants,
            key=lambda c: (
                1 if market_key_by_company_id.get(c["id"]) else 0,
                1 if c.get("url") else 0,
                quote_count_by_company.get(c["id"], 0),
                mention_count_by_company.get(c["id"], 0),
                0 if _has_legal_suffix(c["name"]) else 1,
                -len(c["name"]),
            ),
        )
        primary_id = primary["id"]
        display_name = _select_display_name(variants)
        canonical_url = _select_canonical_url(variants)
        identity_source = "single"
        identity_confidence = "medium"
        if len(component_ids) > 1 and component_market_keys:
            identity_source = "market_key+name"
            identity_confidence = "high" if len(component_market_keys) == 1 else "medium"
        elif len(component_ids) > 1:
            identity_source = "name"
        elif canonical_url:
            identity_source = "market_key"
            identity_confidence = "high"

        merged_companies.append(
            {
                "id": primary_id,
                "name": display_name,
                "url": canonical_url,
                "market_key": _market_key_from_url(canonical_url),
                "canonical_company_id": primary_id,
                "identity_confidence": identity_confidence,
                "identity_source": identity_source,
            }
        )
        for company_id in component_ids:
            alias_map[company_id] = primary_id
        if len(component_ids) > 1:
            merged_groups.append(
                {
                    "canonical_id": primary_id,
                    "canonical_name": display_name,
                    "members": [
                        {
                            "id": company_id,
                            "name": companies_by_id[company_id]["name"],
                        }
                        for company_id in sorted(component_ids)
                    ],
                    "market_keys": sorted(component_market_keys),
                }
            )

    merged_quotes: list[dict] = []
    dropped_quote_rows = 0
    for q in quotes:
        if q["company_id"] in quarantined_company_reason:
            dropped_quote_rows += 1
            continue
        updated = dict(q)
        updated["company_id"] = alias_map.get(q["company_id"], q["company_id"])
        merged_quotes.append(updated)

    merged_mentions: list[dict] = []
    dropped_mention_rows = 0
    for m in mentions:
        if m["company_id"] in quarantined_company_reason:
            dropped_mention_rows += 1
            continue
        updated = dict(m)
        updated["company_id"] = alias_map.get(m["company_id"], m["company_id"])
        merged_mentions.append(updated)

    resolution_report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "counts": {
            "input_companies": len(companies),
            "canonical_companies": len(merged_companies),
            "quarantined_companies": len(quarantined_company_reason),
            "merged_groups": len(merged_groups),
            "dropped_quote_rows": dropped_quote_rows,
            "dropped_mention_rows": dropped_mention_rows,
            "input_quotes": len(quotes),
            "output_quotes": len(merged_quotes),
            "input_mentions": len(mentions),
            "output_mentions": len(merged_mentions),
            "market_conflicts": len(market_conflicts),
            "cross_bucket_merges": len(cross_bucket_merges),
        },
        "quarantined_companies": [
            {
                "id": company_id,
                "name": companies_by_id[company_id]["name"],
                "reason": reason,
                "market_key": market_key_by_company_id.get(company_id),
                "quote_count": quote_count_by_company.get(company_id, 0),
                "mention_count": mention_count_by_company.get(company_id, 0),
            }
            for company_id, reason in sorted(quarantined_company_reason.items())
        ],
        "merged_groups": merged_groups,
        "market_conflicts": market_conflicts,
        "cross_bucket_merges": cross_bucket_merges,
    }

    return merged_companies, merged_quotes, merged_mentions, resolution_report


def build_company_records(
    companies: list[dict],
    quotes: list[dict],
    mentions: list[dict],
    story_mentions_count_by_company: dict[str, int],
) -> list[dict]:
    quote_count_by_company: dict[str, int] = {}
    edition_ids_by_company: dict[str, set[str]] = {}
    for q in quotes:
        company_id = q["company_id"]
        quote_count_by_company[company_id] = quote_count_by_company.get(company_id, 0) + 1
        edition_ids_by_company.setdefault(company_id, set()).add(q["edition_id"])

    for m in mentions:
        company_id = m["company_id"]
        edition_id = m["edition_id"]
        edition_ids_by_company.setdefault(company_id, set()).add(edition_id)

    company_records = []
    for company in sorted(companies, key=lambda c: c["name"].lower()):
        slug = company["id"]
        quote_count = quote_count_by_company.get(slug, 0)
        story_mentions_count = story_mentions_count_by_company.get(slug, 0)
        edition_ids = edition_ids_by_company.get(slug, set())
        if quote_count == 0 and story_mentions_count == 0 and not edition_ids:
            continue
        company_records.append(
            {
                "slug": slug,
                "name": company["name"],
                "quote_count": quote_count,
                "story_mentions_count": story_mentions_count,
            }
        )

    return company_records


def build_index(
    company_records: list[dict],
    quotes: list[dict],
    total_story_mentions: int,
    updated_iso: str,
    updated_relative: str,
    asset_version: str,
) -> None:
    company_data_json = json.dumps(company_records, ensure_ascii=False).replace("</", "<\\/")
    company_record_by_slug = {str(row["slug"]): row for row in company_records}
    featured_company_records = [
        company_record_by_slug[slug]
        for slug in FEATURED_COMPANY_SLUGS
        if slug in company_record_by_slug
    ]
    if len(featured_company_records) < 5:
        featured_slugs = {str(row["slug"]) for row in featured_company_records}
        fallback_ranked = sorted(
            company_records,
            key=lambda row: (
                -(int(row["quote_count"]) + int(row["story_mentions_count"])),
                -int(row["quote_count"]),
                -int(row["story_mentions_count"]),
                str(row["name"]).lower(),
            ),
        )
        for row in fallback_ranked:
            slug = str(row["slug"])
            if slug in featured_slugs:
                continue
            featured_company_records.append(row)
            featured_slugs.add(slug)
            if len(featured_company_records) == 5:
                break

    featured_company_data_json = json.dumps(featured_company_records, ensure_ascii=False).replace("</", "<\\/")
    visible_companies = len(company_records)
    content = render_template(
        "index.html",
        {
            "company_data_json": company_data_json,
            "featured_company_data_json": featured_company_data_json,
            "total_companies": str(visible_companies),
            "total_quotes": str(len(quotes)),
            "total_story_mentions": str(total_story_mentions),
        },
    )
    html = wrap_base(
        "Company Radar",
        content,
        updated_iso=updated_iso,
        updated_relative=updated_relative,
        body_class="body--home-fixed",
        asset_version=asset_version,
    )
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")


def format_date(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    try:
        return datetime.fromisoformat(date_str).strftime("%b'%y")
    except ValueError:
        return date_str


def format_story_date(date_str: str) -> str:
    if not date_str:
        return "Unknown date"
    try:
        return datetime.fromisoformat(date_str).strftime("%d %b %Y")
    except ValueError:
        return date_str


def parse_iso_date(date_str: str) -> date | None:
    value = date_str.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def build_update_metadata(editions: dict[str, dict], dailybrief_posts: list[dict]) -> tuple[str, str]:
    latest_dates: list[date] = []
    for edition in editions.values():
        edition_date = parse_iso_date(str(edition.get("date") or ""))
        if edition_date:
            latest_dates.append(edition_date)

    for post in dailybrief_posts:
        post_date = parse_iso_date(str(post.get("date") or ""))
        if post_date:
            latest_dates.append(post_date)

    today = datetime.now(timezone.utc).date()
    updated_date = max(latest_dates) if latest_dates else today
    delta_days = max((today - updated_date).days, 0)
    if delta_days == 0:
        relative = "today"
    elif delta_days == 1:
        relative = "1 day ago"
    else:
        relative = f"{delta_days} days ago"

    return updated_date.isoformat(), relative


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _normalize_alias_phrase(text: str) -> str:
    normalized = text.lower().replace("&", " and ")
    normalized = normalized.replace("’", "'")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _load_dailybrief_alias_rules(path: Path) -> dict[str, object]:
    payload = read_json(path)
    defaults: dict[str, object] = {
        "company_aliases": {},
        "alias_overrides": {},
        "blocked_aliases": set(),
        "strict_companies": set(),
        "company_blocked_aliases": {},
    }
    if not isinstance(payload, dict):
        return defaults

    company_aliases_payload = payload.get("company_aliases", {})
    alias_overrides_payload = payload.get("alias_overrides", {})
    blocked_aliases_payload = payload.get("blocked_aliases", [])
    strict_companies_payload = payload.get("strict_companies", [])
    company_blocked_aliases_payload = payload.get("company_blocked_aliases", {})

    parsed_company_aliases: dict[str, set[str]] = {}
    if isinstance(company_aliases_payload, dict):
        for company_id, aliases in company_aliases_payload.items():
            company_key = str(company_id).strip()
            if not company_key or not isinstance(aliases, list):
                continue
            parsed_aliases = {
                _normalize_alias_phrase(str(alias))
                for alias in aliases
                if _normalize_alias_phrase(str(alias))
            }
            if parsed_aliases:
                parsed_company_aliases[company_key] = parsed_aliases

    parsed_alias_overrides: dict[str, str] = {}
    if isinstance(alias_overrides_payload, dict):
        for alias, company_id in alias_overrides_payload.items():
            alias_key = _normalize_alias_phrase(str(alias))
            company_key = str(company_id).strip()
            if alias_key and company_key:
                parsed_alias_overrides[alias_key] = company_key

    parsed_blocked_aliases: set[str] = set()
    if isinstance(blocked_aliases_payload, list):
        for alias in blocked_aliases_payload:
            alias_key = _normalize_alias_phrase(str(alias))
            if alias_key:
                parsed_blocked_aliases.add(alias_key)

    parsed_strict_companies: set[str] = set()
    if isinstance(strict_companies_payload, list):
        for company_id in strict_companies_payload:
            company_key = str(company_id).strip()
            if company_key:
                parsed_strict_companies.add(company_key)

    parsed_company_blocked_aliases: dict[str, set[str]] = {}
    if isinstance(company_blocked_aliases_payload, dict):
        for company_id, aliases in company_blocked_aliases_payload.items():
            company_key = str(company_id).strip()
            if not company_key or not isinstance(aliases, list):
                continue
            parsed_aliases = {
                _normalize_alias_phrase(str(alias))
                for alias in aliases
                if _normalize_alias_phrase(str(alias))
            }
            if parsed_aliases:
                parsed_company_blocked_aliases[company_key] = parsed_aliases

    return {
        "company_aliases": parsed_company_aliases,
        "alias_overrides": parsed_alias_overrides,
        "blocked_aliases": parsed_blocked_aliases,
        "strict_companies": parsed_strict_companies,
        "company_blocked_aliases": parsed_company_blocked_aliases,
    }


def _merged_member_names_by_canonical_id(resolution_report: dict[str, object]) -> dict[str, set[str]]:
    groups = resolution_report.get("merged_groups", [])
    if not isinstance(groups, list):
        return {}

    members_by_canonical: dict[str, set[str]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        canonical_id = str(group.get("canonical_id") or "").strip()
        if not canonical_id:
            continue

        member_names: set[str] = set()
        canonical_name = str(group.get("canonical_name") or "").strip()
        if canonical_name:
            member_names.add(canonical_name)

        members = group.get("members", [])
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                member_name = str(member.get("name") or "").strip()
                if member_name:
                    member_names.add(member_name)

        if member_names:
            members_by_canonical.setdefault(canonical_id, set()).update(member_names)

    return members_by_canonical


def _company_symbol_from_url(url: str | None) -> str | None:
    market_key = _market_key_from_url(url)
    if not market_key:
        return None
    _, symbol = market_key.split(":", 1)
    normalized_symbol = _normalize_alias_phrase(symbol)
    if 1 < len(normalized_symbol) <= 12:
        return normalized_symbol
    return None


def _build_company_alias_map(
    companies: list[dict],
    resolution_report: dict[str, object],
    alias_rules: dict[str, object],
) -> dict[str, set[str]]:
    aliases_by_company: dict[str, set[str]] = {}
    merged_member_names = _merged_member_names_by_canonical_id(resolution_report)
    company_aliases_rules = alias_rules.get("company_aliases", {})
    alias_overrides = alias_rules.get("alias_overrides", {})
    blocked_aliases = alias_rules.get("blocked_aliases", set())
    strict_companies = alias_rules.get("strict_companies", set())
    company_blocked_aliases = alias_rules.get("company_blocked_aliases", {})

    if not isinstance(company_aliases_rules, dict):
        company_aliases_rules = {}
    if not isinstance(alias_overrides, dict):
        alias_overrides = {}
    if not isinstance(blocked_aliases, set):
        blocked_aliases = set()
    if not isinstance(strict_companies, set):
        strict_companies = set()
    if not isinstance(company_blocked_aliases, dict):
        company_blocked_aliases = {}

    for company in companies:
        company_id = str(company.get("id") or "").strip()
        if not company_id:
            continue

        aliases: set[str] = set()
        company_name = str(company.get("name") or "").strip()
        explicit_aliases: set[str] = set()
        for alias in company_aliases_rules.get(company_id, set()):
            alias_key = _normalize_alias_phrase(str(alias))
            if alias_key:
                explicit_aliases.add(alias_key)

        if company_id in strict_companies:
            aliases.update(explicit_aliases)
        else:
            normalized_name = _normalize_alias_phrase(company_name)
            if normalized_name:
                aliases.add(normalized_name)

            collapsed_name = _normalize_alias_phrase(" ".join(_normalized_name_tokens(company_name)))
            if collapsed_name:
                aliases.add(collapsed_name)

            for member_name in merged_member_names.get(company_id, set()):
                alias_key = _normalize_alias_phrase(member_name)
                if alias_key:
                    aliases.add(alias_key)

            aliases.update(explicit_aliases)

            symbol_alias = _company_symbol_from_url(company.get("url"))
            if symbol_alias:
                aliases.add(symbol_alias)

            for alias_key, override_company_id in alias_overrides.items():
                if override_company_id == company_id:
                    aliases.add(_normalize_alias_phrase(alias_key))

        company_specific_blocked = company_blocked_aliases.get(company_id, set())
        if not isinstance(company_specific_blocked, set):
            company_specific_blocked = set()

        aliases = {
            alias
            for alias in aliases
            if (
                alias
                and alias not in blocked_aliases
                and alias not in company_specific_blocked
                and len(alias) >= 2
                and not alias.isdigit()
            )
        }
        aliases_by_company[company_id] = aliases

    return aliases_by_company


def _compile_alias_pattern(alias: str) -> re.Pattern[str]:
    pattern_text = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){pattern_text}(?![a-z0-9])")


def _build_company_alias_specs(
    aliases_by_company: dict[str, set[str]],
    alias_rules: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    alias_to_companies: dict[str, set[str]] = {}
    for company_id, aliases in aliases_by_company.items():
        for alias in aliases:
            alias_to_companies.setdefault(alias, set()).add(company_id)

    alias_overrides = alias_rules.get("alias_overrides", {})
    if not isinstance(alias_overrides, dict):
        alias_overrides = {}
    blocked_aliases = alias_rules.get("blocked_aliases", set())
    if not isinstance(blocked_aliases, set):
        blocked_aliases = set()

    specs_by_company: dict[str, list[dict[str, object]]] = {}
    for company_id, aliases in aliases_by_company.items():
        specs: list[dict[str, object]] = []
        for alias in aliases:
            if alias in blocked_aliases:
                continue

            override_company = alias_overrides.get(alias)
            if override_company and override_company != company_id:
                continue
            if not override_company and len(alias_to_companies.get(alias, set())) > 1:
                continue

            first_token = alias.split()[0]
            specs.append(
                {
                    "alias": alias,
                    "first_token": first_token,
                    "pattern": _compile_alias_pattern(alias),
                }
            )

        specs.sort(key=lambda item: len(str(item["alias"])), reverse=True)
        specs_by_company[company_id] = specs

    return specs_by_company


def _count_story_mentions(normalized_story_text: str, alias_specs: list[dict[str, object]]) -> int:
    occupied_spans: list[tuple[int, int]] = []
    count = 0

    for spec in alias_specs:
        pattern = spec["pattern"]
        if not isinstance(pattern, re.Pattern):
            continue
        for match in pattern.finditer(normalized_story_text):
            start, end = match.span()
            overlaps = any(not (end <= used_start or start >= used_end) for used_start, used_end in occupied_spans)
            if overlaps:
                continue
            occupied_spans.append((start, end))
            count += 1

    return count


def build_dailybrief_story_mentions(
    companies: list[dict],
    resolution_report: dict[str, object],
    dailybrief_posts: list[dict],
) -> list[dict]:
    if not isinstance(dailybrief_posts, list):
        return []

    alias_rules = _load_dailybrief_alias_rules(DAILYBRIEF_ALIAS_RULES_FILE)
    aliases_by_company = _build_company_alias_map(companies, resolution_report, alias_rules)
    alias_specs_by_company = _build_company_alias_specs(aliases_by_company, alias_rules)
    company_ids = [str(company.get("id") or "").strip() for company in companies if str(company.get("id") or "").strip()]

    story_mentions: list[dict] = []
    seen_company_story: set[tuple[str, str]] = set()

    for post in dailybrief_posts:
        if not isinstance(post, dict):
            continue
        post_url = str(post.get("url") or "").strip()
        if not post_url:
            continue

        post_title = str(post.get("title") or "").strip()
        story_date = str(post.get("date") or post.get("sitemap_lastmod") or "").strip()
        stories = post.get("stories", [])
        if not isinstance(stories, list):
            continue

        for story in stories:
            if not isinstance(story, dict):
                continue
            story_title = str(story.get("title") or "").strip() or post_title or "Daily Brief story"
            story_id = str(story.get("story_id") or "").strip()
            if not story_id:
                story_id = slugify(f"{post_url}-{story.get('position', 0)}-{story_title}")

            story_text = str(story.get("text") or "").strip()
            normalized_story_text = _normalize_alias_phrase(story_text)
            if not normalized_story_text:
                continue

            story_tokens = set(normalized_story_text.split())
            matched_companies: list[tuple[str, int]] = []

            for company_id in company_ids:
                alias_specs = alias_specs_by_company.get(company_id, [])
                if not alias_specs:
                    continue
                if not any(str(spec.get("first_token") or "") in story_tokens for spec in alias_specs):
                    continue

                mention_count = _count_story_mentions(normalized_story_text, alias_specs)
                if mention_count > 0:
                    matched_companies.append((company_id, mention_count))

            if not matched_companies:
                continue

            for company_id, mention_count in matched_companies:
                dedupe_key = (company_id, story_id)
                if dedupe_key in seen_company_story:
                    continue
                seen_company_story.add(dedupe_key)
                story_mentions.append(
                    {
                        "company_id": company_id,
                        "story_id": story_id,
                        "story_title": story_title,
                        "story_url": post_url,
                        "post_title": post_title,
                        "story_date": story_date,
                        "story_position": int(story.get("position") or 0),
                        "story_source": str(story.get("source") or ""),
                        "mention_count": int(mention_count),
                    }
                )

    return story_mentions


def group_dailybrief_mentions_by_company(story_mentions: list[dict]) -> dict[str, list[dict]]:
    by_company: dict[str, list[dict]] = {}
    for row in story_mentions:
        company_id = str(row.get("company_id") or "").strip()
        if not company_id:
            continue
        by_company.setdefault(company_id, []).append(row)

    for rows in by_company.values():
        rows.sort(key=lambda row: str(row.get("story_title") or "").lower())
        rows.sort(key=lambda row: str(row.get("story_date") or ""), reverse=True)
        rows.sort(key=lambda row: int(row.get("mention_count") or 0), reverse=True)

    return by_company


def _render_dailybrief_story_item(row: dict) -> str:
    story_title = html_escape(str(row.get("story_title") or "Daily Brief story"))
    story_url = html_escape(str(row.get("story_url") or ""))
    story_date = html_escape(format_story_date(str(row.get("story_date") or "")))
    mention_count = int(row.get("mention_count") or 0)
    mention_label = "mention" if mention_count == 1 else "mentions"
    return "\n".join(
        [
            '<li class="headline-item">',
            (
                f'  <a class="headline-link" href="{story_url}" target="_blank"'
                ' rel="noopener noreferrer">'
                f"{story_title}</a>"
            ),
            f'  <p class="headline-meta">{story_date} · {mention_count} {mention_label}</p>',
            "</li>",
        ]
    )


def render_dailybrief_section(stories: list[dict]) -> str:
    visible_rows = stories[:DAILYBRIEF_VISIBLE_DEFAULT]
    hidden_rows = stories[DAILYBRIEF_VISIBLE_DEFAULT:]

    if visible_rows:
        list_html = "\n".join(_render_dailybrief_story_item(row) for row in visible_rows)
        body_html = f'<ol class="headline-list">{list_html}</ol>'
    else:
        body_html = '<p class="segment-empty">No Daily Brief story mentions for this company yet.</p>'

    hidden_html = ""
    if hidden_rows:
        hidden_list_html = "\n".join(_render_dailybrief_story_item(row) for row in hidden_rows)
        hidden_html = "\n".join(
            [
                '<details class="segment-dropdown" data-persist-key="dailybrief-more">',
                f'  <summary>Show {len(hidden_rows)} more stories</summary>',
                '  <div class="segment-dropdown-panel">',
                f'    <ol class="headline-list headline-list-more">{hidden_list_html}</ol>',
                "  </div>",
                "</details>",
            ]
        )

    summary = f"{len(stories)} story mentions · ranked by mention frequency"
    return "\n".join(
        [
            '<section class="segment-card brief-card card">',
            '  <div class="segment-header">',
            (
                '    <a class="segment-chip segment-chip-brief" href="https://thedailybrief.zerodha.com/"'
                ' target="_blank" rel="noopener noreferrer"><span class="segment-chip-icon">DB</span>Daily Brief</a>'
            ),
            "    <p class=\"segment-subtitle\">Quick market stories where this company is mentioned.</p>",
            f'    <p class="segment-meta">{html_escape(summary)}</p>',
            "  </div>",
            f"  {body_html}",
            hidden_html,
            "</section>",
        ]
    )


def render_chatter_section(
    visible_quote_cards: list[str],
    hidden_quote_cards: list[str],
    timeline_span: str,
    edition_count: int,
    quote_count: int,
) -> str:
    timeline_meta_parts = []
    if timeline_span:
        timeline_meta_parts.append(timeline_span)
    timeline_meta_parts.append(f"{edition_count} editions")
    timeline_meta_parts.append(f"{quote_count} quotes")
    timeline_meta = " · ".join(timeline_meta_parts)

    visible_html = "".join(visible_quote_cards)
    body_html = (
        f'  <div class="story-timeline">{visible_html}</div>'
        if visible_quote_cards
        else '  <p class="segment-empty">No direct The Chatter quotes for this company yet.</p>'
    )
    hidden_html = ""
    if hidden_quote_cards:
        hidden_cards = "".join(hidden_quote_cards)
        hidden_count = len(hidden_quote_cards)
        hidden_label = "quote" if hidden_count == 1 else "quotes"
        hidden_html = "\n".join(
            [
                '<details class="segment-dropdown" data-persist-key="chatter-more">',
                f'  <summary>Show {hidden_count} more {hidden_label}</summary>',
                '  <div class="segment-dropdown-panel">',
                f'    <div class="story-timeline story-timeline-more">{hidden_cards}</div>',
                "  </div>",
                "</details>",
            ]
        )

    return "\n".join(
        [
            '<section class="segment-card chatter-card card">',
            '  <div class="segment-header">',
            (
                '    <a class="segment-chip segment-chip-chatter" href="https://thechatterbyzerodha.substack.com/"'
                ' target="_blank" rel="noopener noreferrer"><span class="segment-chip-icon">CC</span>The Chatter</a>'
            ),
            "    <p class=\"segment-subtitle\">Deep management quotes and context from earnings calls.</p>",
            f'    <p class="segment-meta">{html_escape(timeline_meta)}</p>',
            "  </div>",
            body_html,
            hidden_html,
            "</section>",
        ]
    )


def build_company_pages(
    companies: list[dict],
    editions: dict[str, dict],
    quotes: list[dict],
    mentions: list[dict],
    dailybrief_mentions_by_company: dict[str, list[dict]],
    updated_iso: str,
    updated_relative: str,
    asset_version: str,
) -> None:
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
                editions.get(edition_id, {}).get("date", ""),
                edition_id,
            ),
            reverse=True,
        )
        if not covered_edition_ids:
            continue

        quote_card_sections: list[str] = []
        dates = [editions.get(edition_id, {}).get("date", "") for edition_id in covered_edition_ids]
        company_quote_count = 0
        company_quote_index = 1

        for edition_id in covered_edition_ids:
            edition_quotes = sorted(
                quotes_by_company_edition.get(slug, {}).get(edition_id, []),
                key=lambda q: q["id"],
            )
            edition = editions.get(edition_id, {})
            edition_title = edition.get("title") or edition_id
            edition_date = edition.get("date", "")
            if not edition_quotes:
                continue

            edition_date_label = format_date(edition_date)
            edition_label_parts = []
            if edition_date_label and edition_date_label != "Unknown":
                edition_label_parts.append(edition_date_label)
            if edition_title:
                edition_label_parts.append(str(edition_title))
            edition_label = " · ".join(edition_label_parts) or str(edition_title)

            for q in edition_quotes:
                company_quote_count += 1
                quote_context = (q.get("context") or "").strip()
                quote_speaker = (q.get("speaker") or "").strip()
                source_url = str(q.get("source_url") or "").strip()
                story_parts = [f'<p class="story-kicker">{html_escape(edition_label)}</p>']

                if quote_context:
                    story_parts.append(f'<p class="story-context">{html_escape(quote_context)}</p>')
                story_parts.append(f'<blockquote class="story-quote">“{html_escape(q["text"])}”</blockquote>')
                footer_parts = []
                if quote_speaker:
                    footer_parts.append(f'<p class="story-speaker">— {html_escape(quote_speaker)}</p>')
                if source_url:
                    footer_parts.append(f'<a class="small-link quote-source" href="{html_escape(source_url)}">Source</a>')
                if footer_parts:
                    story_parts.append(f'<div class="story-footer">{"".join(footer_parts)}</div>')

                quote_card_sections.append(
                    "\n".join(
                        [
                            '<article class="story-card">',
                            f'  <span class="story-index">{company_quote_index:02d}</span>',
                            f'  <div class="story-body">{"".join(story_parts)}</div>',
                            "</article>",
                        ]
                    )
                )
                company_quote_index += 1

        company_name_link = html_escape(company["name"])
        if company.get("url"):
            company_name_link = (
                f'<a class="company-title-link" href="{html_escape(company["url"])}">'
                f"{html_escape(company['name'])}</a>"
            )

        valid_dates = sorted([date_value for date_value in dates if date_value])
        timeline_span = ""
        if valid_dates:
            first_label = format_date(valid_dates[0])
            last_label = format_date(valid_dates[-1])
            timeline_span = first_label if first_label == last_label else f"{first_label} - {last_label}"

        dailybrief_stories = dailybrief_mentions_by_company.get(slug, [])
        company_story_mentions = len(dailybrief_stories)
        hero_meta = f"{company_quote_count} quotes · {company_story_mentions} story mentions"
        meta = "The Chatter gives depth. Daily Brief gives wider market context."

        visible_quote_cards = quote_card_sections[:CHATTER_VISIBLE_QUOTES_DEFAULT]
        hidden_quote_cards = quote_card_sections[CHATTER_VISIBLE_QUOTES_DEFAULT:]
        dailybrief_section = render_dailybrief_section(dailybrief_stories)
        chatter_section = render_chatter_section(
            visible_quote_cards,
            hidden_quote_cards,
            timeline_span,
            len(covered_edition_ids),
            company_quote_count,
        )
        content = render_template(
            "company.html",
            {
                "company_slug": slug,
                "company_name_link": company_name_link,
                "company_meta": meta,
                "company_timeline_meta": hero_meta,
                "dailybrief_section": dailybrief_section,
                "chatter_section": chatter_section,
            },
        )
        html = wrap_base(
            f"{company['name']} | Company Radar",
            content,
            updated_iso=updated_iso,
            updated_relative=updated_relative,
            body_class="body--company",
            asset_version=asset_version,
            header_search_html=render_template(
                "header_search.html",
                {
                    "current_company_slug": slug,
                },
            ),
        )
        out_dir = SITE_DIR / "company" / slug
        ensure_dir(out_dir)
        (out_dir / "index.html").write_text(html, encoding="utf-8")


def copy_assets() -> None:
    ensure_dir(SITE_DIR / "assets")
    for path in ASSETS_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(ASSETS_DIR)
        dest_path = SITE_DIR / "assets" / rel_path
        ensure_dir(dest_path.parent)
        shutil.copy2(path, dest_path)


def main() -> None:
    ensure_dir(SITE_DIR)
    copy_assets()

    editions = {e["id"]: e for e in read_json(DATA_DIR / "editions.json")}
    companies = read_json(DATA_DIR / "companies.json")
    quotes = read_json(DATA_DIR / "quotes.json")
    mentions = read_json(DATA_DIR / "company_mentions.json")
    companies, quotes, mentions, resolution_report = merge_company_variants(companies, quotes, mentions)
    dailybrief_posts = read_json(DAILYBRIEF_POSTS_FILE)
    dailybrief_story_mentions = build_dailybrief_story_mentions(companies, resolution_report, dailybrief_posts)
    DAILYBRIEF_STORY_MENTIONS_FILE.write_text(
        json.dumps(dailybrief_story_mentions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    dailybrief_mentions_by_company = group_dailybrief_mentions_by_company(dailybrief_story_mentions)
    story_mentions_count_by_company = {
        company_id: len(rows) for company_id, rows in dailybrief_mentions_by_company.items()
    }
    total_story_mentions = len(dailybrief_story_mentions)
    updated_iso, updated_relative = build_update_metadata(editions, dailybrief_posts)
    asset_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    company_records = build_company_records(companies, quotes, mentions, story_mentions_count_by_company)
    (SITE_DIR / "assets" / "company-search-index.json").write_text(
        json.dumps(company_records, ensure_ascii=False),
        encoding="utf-8",
    )

    build_index(
        company_records,
        quotes,
        total_story_mentions,
        updated_iso,
        updated_relative,
        asset_version,
    )
    build_company_pages(
        companies,
        editions,
        quotes,
        mentions,
        dailybrief_mentions_by_company,
        updated_iso,
        updated_relative,
        asset_version,
    )
    ENTITY_RESOLUTION_REPORT_FILE.write_text(
        json.dumps(resolution_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    matched_story_count = len({row["story_id"] for row in dailybrief_story_mentions})
    print(f"Daily Brief matched stories: {matched_story_count}")
    print(f"Daily Brief total story mentions: {total_story_mentions}")
    print(f"Daily Brief companies with matches: {len(dailybrief_mentions_by_company)}")
    print("Site build complete")


if __name__ == "__main__":
    main()
