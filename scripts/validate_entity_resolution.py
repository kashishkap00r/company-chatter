#!/usr/bin/env python3
"""Validate entity-resolution invariants and fail fast on regressions."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SITE_DIR = BASE_DIR / "site"
BASELINE_PATH = DATA_DIR / "entity_resolution_baseline.json"
REPORT_PATH = DATA_DIR / "entity_resolution_report.json"
COMPANIES_PATH = DATA_DIR / "companies.json"
QUOTES_PATH = DATA_DIR / "quotes.json"
MENTIONS_PATH = DATA_DIR / "company_mentions.json"
INDEX_PATH = SITE_DIR / "index.html"
NON_COMPANY_RULES_PATH = DATA_DIR / "non_company_rules.json"
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


def _load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_name_key(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _normalize_raw_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _load_non_company_rules(path: Path) -> dict[str, object]:
    payload = _load_json(path)
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
        "exact_name_keys": {_normalize_raw_name_key(str(name)) for name in exact_names if str(name).strip()},
        "allow_name_keys": {_normalize_raw_name_key(str(name)) for name in allow_names if str(name).strip()},
        "name_patterns": compiled_patterns,
    }


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
    name_key = _normalize_raw_name_key(name)
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


def _parse_company_rows(index_path: Path) -> list[dict]:
    html = index_path.read_text(encoding="utf-8")
    match = re.search(r'<script id="companyData" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        raise ValueError("Missing companyData script payload in site/index.html")
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid companyData payload: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("companyData payload is not a list")
    return parsed


def _load_build_site_module():
    build_site_path = BASE_DIR / "scripts" / "build_site.py"
    spec = importlib.util.spec_from_file_location("build_site_runtime", build_site_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _pair_is_merged(pair: list[str], merged_name_sets: list[set[str]]) -> bool:
    left, right = pair
    return any(left in names and right in names for names in merged_name_sets)


def _pair_visible_together(pair: list[str], visible_names: set[str]) -> bool:
    left, right = pair
    return left in visible_names and right in visible_names


def main() -> int:
    errors: list[str] = []

    try:
        baseline = _load_json(BASELINE_PATH)
        persisted_report = _load_json(REPORT_PATH)
        raw_companies = _load_json(COMPANIES_PATH)
        raw_quotes = _load_json(QUOTES_PATH)
        raw_mentions = _load_json(MENTIONS_PATH)
        index_rows = _parse_company_rows(INDEX_PATH)
        non_company_rules = _load_non_company_rules(NON_COMPANY_RULES_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    build_site = _load_build_site_module()
    merged_companies, merged_quotes, merged_mentions, computed_report = build_site.merge_company_variants(
        raw_companies, raw_quotes, raw_mentions
    )

    persisted_counts = persisted_report.get("counts", {})
    computed_counts = computed_report.get("counts", {})
    if persisted_counts != computed_counts:
        errors.append(
            "Persisted entity resolution report is stale. Re-run `python3 scripts/build_site.py` "
            "to refresh data/entity_resolution_report.json."
        )

    thresholds = baseline.get("thresholds", {})
    threshold_map = {
        "max_market_conflicts": "market_conflicts",
        "max_quarantined_companies": "quarantined_companies",
        "max_dropped_quote_rows": "dropped_quote_rows",
        "max_dropped_mention_rows": "dropped_mention_rows",
    }
    for threshold_key, count_key in threshold_map.items():
        if threshold_key not in thresholds:
            continue
        limit = int(thresholds[threshold_key])
        actual = int(computed_counts.get(count_key, 0))
        if actual > limit:
            errors.append(f"{count_key} regressed: {actual} > allowed {limit}")

    by_name_key: dict[str, list[str]] = defaultdict(list)
    for row in index_rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        by_name_key[_normalize_name_key(name)].append(name)
    repeat_name_keys = sum(1 for names in by_name_key.values() if len(names) > 1)
    max_repeat_name_keys = int(thresholds.get("max_repeat_name_keys", 0))
    if repeat_name_keys > max_repeat_name_keys:
        errors.append(f"repeat_name_keys regressed: {repeat_name_keys} > allowed {max_repeat_name_keys}")

    merged_name_sets = [
        {str(member.get("name", "")).strip() for member in group.get("members", []) if str(member.get("name", "")).strip()}
        for group in computed_report.get("merged_groups", [])
    ]
    visible_names = {str(row.get("name", "")).strip() for row in index_rows if str(row.get("name", "")).strip()}
    raw_names = {str(item.get("name", "")).strip() for item in raw_companies if str(item.get("name", "")).strip()}

    for pair in baseline.get("must_keep_blocked_pairs_separate", []):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        if all(name in raw_names for name in pair) and _pair_is_merged(pair, merged_name_sets):
            errors.append(f"blocked pair merged unexpectedly: {pair[0]} + {pair[1]}")

    for pair in baseline.get("must_keep_alias_pairs_merged", []):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        if all(name in raw_names for name in pair) and not _pair_is_merged(pair, merged_name_sets):
            errors.append(f"alias pair not merged: {pair[0]} + {pair[1]}")

    for pair in baseline.get("must_not_coexist_in_index", []):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        if _pair_visible_together(pair, visible_names):
            errors.append(f"duplicate visible pair detected in index: {pair[0]} + {pair[1]}")

    must_exclude_name_keys = {
        _normalize_raw_name_key(str(name))
        for name in baseline.get("must_exclude_company_names", [])
        if str(name).strip()
    }
    for visible_name in sorted(visible_names):
        visible_name_key = _normalize_raw_name_key(visible_name)
        if visible_name_key in must_exclude_name_keys:
            errors.append(f"blocked non-company label visible in index: {visible_name}")

    allowed_suspicious_name_keys = {
        _normalize_raw_name_key(str(name))
        for name in baseline.get("allowed_suspicious_null_url_names", [])
        if str(name).strip()
    }
    for company in merged_companies:
        company_name = str(company.get("name", "")).strip()
        if not company_name:
            continue

        name_key = _normalize_raw_name_key(company_name)
        if _matches_non_company_rules(company_name, non_company_rules) and name_key not in allowed_suspicious_name_keys:
            errors.append(f"non-company rule label present in merged companies: {company_name}")
            continue

        if not company.get("url") and _looks_like_topic_or_sentence(company_name) and name_key not in allowed_suspicious_name_keys:
            errors.append(f"suspicious null-url company label detected: {company_name}")

    merged_name_by_id = {str(company["id"]): str(company["name"]) for company in merged_companies}
    edition_ids_by_company_id: dict[str, set[str]] = defaultdict(set)
    for quote in merged_quotes:
        edition_ids_by_company_id[str(quote["company_id"])].add(str(quote["edition_id"]))
    for mention in merged_mentions:
        edition_ids_by_company_id[str(mention["company_id"])].add(str(mention["edition_id"]))

    for expectation in baseline.get("critical_company_expectations", []):
        if not isinstance(expectation, dict):
            continue
        target_name = str(expectation.get("company_name", "")).strip()
        if not target_name:
            continue

        matching_ids = [company_id for company_id, name in merged_name_by_id.items() if name == target_name]
        if not matching_ids:
            errors.append(f"missing critical company in merged output: {target_name}")
            continue
        company_id = matching_ids[0]
        edition_ids = edition_ids_by_company_id.get(company_id, set())
        min_editions = int(expectation.get("min_edition_count", 0))
        if len(edition_ids) < min_editions:
            errors.append(f"{target_name} edition_count regressed: {len(edition_ids)} < required {min_editions}")

        for required_edition in expectation.get("required_edition_ids", []):
            edition_id = str(required_edition).strip()
            if edition_id and edition_id not in edition_ids:
                errors.append(f"{target_name} missing required edition: {edition_id}")

    quote_id_to_company_ids: dict[str, set[str]] = defaultdict(set)
    for quote in merged_quotes:
        quote_id = str(quote.get("id", "")).strip()
        company_id = str(quote.get("company_id", "")).strip()
        if quote_id and company_id:
            quote_id_to_company_ids[quote_id].add(company_id)
    leaked_quote_ids = [quote_id for quote_id, company_ids in quote_id_to_company_ids.items() if len(company_ids) > 1]
    if leaked_quote_ids:
        preview = ", ".join(leaked_quote_ids[:10])
        errors.append(f"quote leakage detected (same quote id mapped to multiple companies): {preview}")

    if errors:
        print("[FAIL] Entity-resolution validation failed:")
        for issue in errors:
            print(f" - {issue}")
        return 1

    print("[PASS] Entity-resolution validation succeeded.")
    print(f" - canonical_companies: {computed_counts.get('canonical_companies', 0)}")
    print(f" - market_conflicts: {computed_counts.get('market_conflicts', 0)}")
    print(f" - quarantined_companies: {computed_counts.get('quarantined_companies', 0)}")
    print(f" - repeat_name_keys: {repeat_name_keys}")
    print(f" - dropped_quote_rows: {computed_counts.get('dropped_quote_rows', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
