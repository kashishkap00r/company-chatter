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
- data/entity_resolution_report.json
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
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
ENTITY_RESOLUTION_REPORT_FILE = DATA_DIR / "entity_resolution_report.json"
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
    return urls[0]


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
    companies, quotes, mentions, resolution_report = merge_company_variants(companies, quotes, mentions)

    build_index(companies, editions, quotes, mentions)
    build_company_pages(companies, editions, quotes, mentions)
    ENTITY_RESOLUTION_REPORT_FILE.write_text(
        json.dumps(resolution_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Site build complete")


if __name__ == "__main__":
    main()
