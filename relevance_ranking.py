#!/usr/bin/env python3
"""
relevance_ranking.py - MVP hybrid relevance scoring for contract opportunities.

Combines:
  - explicit client_preferences arrays
  - learned JSON weight maps from preference_training.py
  - simple contract hygiene rules such as deadline and contract value fit

The scorer is intentionally transparent: it returns a numeric score plus short
human-readable reasons that can be shown in the UI or email digest.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

WORD_RE = re.compile(r"[a-z][a-z0-9&/-]{3,}")
MONEY_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_key(value: Any) -> str:
    return clean_string(value).upper()


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_string(v) for v in value if clean_string(v)]
    if isinstance(value, tuple):
        return [clean_string(v) for v in value if clean_string(v)]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        # Be tolerant of JSON-ish strings when testing outside psycopg2.
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                return as_list(parsed)
            except json.JSONDecodeError:
                pass
        return [stripped]
    return [clean_string(value)] if clean_string(value) else []


def as_dict(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, dict):
        result: dict[str, float] = {}
        for key, raw in value.items():
            try:
                result[str(key)] = float(raw)
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            return as_dict(json.loads(stripped))
        except json.JSONDecodeError:
            return {}
    return {}


def contains_any(text: str, terms: list[str]) -> list[str]:
    text_lower = text.lower()
    matches: list[str] = []
    for term in terms:
        term_clean = clean_string(term)
        if term_clean and term_clean.lower() in text_lower:
            matches.append(term_clean)
    return matches


def contains_entity(value: str, entities: list[str]) -> str | None:
    value_upper = normalize_key(value)
    if not value_upper:
        return None
    for entity in entities:
        entity_upper = normalize_key(entity)
        if entity_upper and (entity_upper in value_upper or value_upper in entity_upper):
            return clean_string(entity)
    return None


def parse_date(value: Any) -> date | None:
    text = clean_string(value)
    if not text:
        return None
    for candidate in (text[:10], text):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    return None


def days_until(value: Any) -> int | None:
    parsed = parse_date(value)
    if not parsed:
        return None
    return (parsed - date.today()).days


def parse_money(value: Any) -> float | None:
    """Best-effort contract-value parser for strings like '$1,000,000'."""
    text = clean_string(value)
    if not text:
        return None
    lowered = text.lower()
    multiplier = 1.0
    if "billion" in lowered or lowered.endswith("b"):
        multiplier = 1_000_000_000.0
    elif "million" in lowered or lowered.endswith("m"):
        multiplier = 1_000_000.0
    elif "thousand" in lowered or lowered.endswith("k"):
        multiplier = 1_000.0

    matches = MONEY_RE.findall(text.replace("$", ""))
    if not matches:
        return None
    try:
        # Use the largest number when ranges are present.
        values = [float(m.replace(",", "")) for m in matches]
        return max(values) * multiplier
    except ValueError:
        return None


def add_reason(reasons: list[dict[str, Any]], label: str, points: float) -> None:
    if abs(points) < 0.001:
        return
    reasons.append({"label": label, "points": round(float(points), 2)})


def add_component(components: dict[str, float], name: str, points: float) -> None:
    components[name] = round(components.get(name, 0.0) + float(points), 2)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_posting(posting: dict[str, Any], prefs: dict[str, Any] | None) -> dict[str, Any]:
    """Return a transparent relevance score for one posting.

    Score is clamped to 0-100 for UI display, but the reasons/components retain
    detail about why the posting moved up or down.
    """
    prefs = prefs or {}
    score = 50.0
    components: dict[str, float] = {}
    reasons: list[dict[str, Any]] = []

    title = clean_string(posting.get("title"))
    description = clean_string(posting.get("description"))
    text = f"{title} {description}"

    naics = clean_string(posting.get("naics"))
    agency = clean_string(posting.get("agency"))
    source = clean_string(posting.get("source_site"))
    status_or_type = clean_string(posting.get("contract_type") or posting.get("award_status"))
    set_aside = clean_string(posting.get("acq_strategy"))

    preferred_naics = as_list(prefs.get("preferred_naics"))
    excluded_naics = as_list(prefs.get("excluded_naics"))
    preferred_agencies = as_list(prefs.get("preferred_agencies"))
    excluded_agencies = as_list(prefs.get("excluded_agencies"))
    preferred_sources = as_list(prefs.get("preferred_sources"))
    excluded_sources = as_list(prefs.get("excluded_sources"))
    preferred_keywords = as_list(prefs.get("preferred_keywords"))
    disliked_keywords = as_list(prefs.get("disliked_keywords"))
    preferred_contract_types = as_list(prefs.get("preferred_contract_types"))
    preferred_set_asides = as_list(prefs.get("preferred_set_asides"))

    # Explicit preference arrays.
    if naics and normalize_key(naics) in {normalize_key(x) for x in preferred_naics}:
        points = 20.0
        score += points
        add_component(components, "preferred_naics", points)
        add_reason(reasons, f"Preferred NAICS {naics}", points)

    if naics and normalize_key(naics) in {normalize_key(x) for x in excluded_naics}:
        points = -45.0
        score += points
        add_component(components, "excluded_naics", points)
        add_reason(reasons, f"Excluded NAICS {naics}", points)

    matched_agency = contains_entity(agency, preferred_agencies)
    if matched_agency:
        points = 16.0
        score += points
        add_component(components, "preferred_agency", points)
        add_reason(reasons, f"Preferred agency: {matched_agency}", points)

    matched_excluded_agency = contains_entity(agency, excluded_agencies)
    if matched_excluded_agency:
        points = -35.0
        score += points
        add_component(components, "excluded_agency", points)
        add_reason(reasons, f"Excluded agency: {matched_excluded_agency}", points)

    if source and normalize_key(source) in {normalize_key(x) for x in preferred_sources}:
        points = 8.0
        score += points
        add_component(components, "preferred_source", points)
        add_reason(reasons, f"Preferred source: {source}", points)

    if source and normalize_key(source) in {normalize_key(x) for x in excluded_sources}:
        points = -20.0
        score += points
        add_component(components, "excluded_source", points)
        add_reason(reasons, f"Excluded source: {source}", points)

    matched_type = contains_entity(status_or_type, preferred_contract_types)
    if matched_type:
        points = 8.0
        score += points
        add_component(components, "preferred_contract_type", points)
        add_reason(reasons, f"Preferred type: {matched_type}", points)

    matched_set_aside = contains_entity(set_aside, preferred_set_asides)
    if matched_set_aside:
        points = 8.0
        score += points
        add_component(components, "preferred_set_aside", points)
        add_reason(reasons, f"Preferred set-aside: {matched_set_aside}", points)

    liked_terms = contains_any(text, preferred_keywords)[:5]
    if liked_terms:
        points = min(24.0, 6.0 * len(liked_terms))
        score += points
        add_component(components, "preferred_keywords", points)
        add_reason(reasons, "Liked keywords: " + ", ".join(liked_terms[:3]), points)

    disliked_terms = contains_any(text, disliked_keywords)[:5]
    if disliked_terms:
        points = max(-36.0, -12.0 * len(disliked_terms))
        score += points
        add_component(components, "disliked_keywords", points)
        add_reason(reasons, "Disliked keywords: " + ", ".join(disliked_terms[:3]), points)

    # Learned weights from feedback. The multipliers intentionally make learned
    # signals useful but not so strong that one click dominates all rule scoring.
    naics_weights = as_dict(prefs.get("naics_weights"))
    agency_weights = as_dict(prefs.get("agency_weights"))
    source_weights = as_dict(prefs.get("source_weights"))
    keyword_weights = as_dict(prefs.get("keyword_weights"))

    learned_naics = naics_weights.get(normalize_key(naics), 0.0) if naics else 0.0
    if learned_naics:
        points = clamp(learned_naics * 7.0, -28.0, 28.0)
        score += points
        add_component(components, "learned_naics_weight", points)
        add_reason(reasons, f"Learned NAICS signal {naics}", points)

    agency_upper = normalize_key(agency)
    learned_agency = 0.0
    matched_learned_agency = ""
    for key, raw_weight in agency_weights.items():
        key_upper = normalize_key(key)
        if key_upper and agency_upper and (key_upper in agency_upper or agency_upper in key_upper):
            if abs(raw_weight) > abs(learned_agency):
                learned_agency = raw_weight
                matched_learned_agency = clean_string(key)
    if learned_agency:
        points = clamp(learned_agency * 5.0, -25.0, 25.0)
        score += points
        add_component(components, "learned_agency_weight", points)
        add_reason(reasons, f"Learned agency signal: {matched_learned_agency}", points)

    learned_source = source_weights.get(normalize_key(source), 0.0) if source else 0.0
    if learned_source:
        points = clamp(learned_source * 4.0, -16.0, 16.0)
        score += points
        add_component(components, "learned_source_weight", points)
        add_reason(reasons, f"Learned source signal: {source}", points)

    keyword_points = 0.0
    matched_learned_terms: list[str] = []
    text_lower = text.lower()
    for term, raw_weight in keyword_weights.items():
        term_clean = clean_string(term).lower()
        if not term_clean or term_clean not in text_lower:
            continue
        keyword_points += float(raw_weight) * 2.0
        matched_learned_terms.append(clean_string(term))
        if len(matched_learned_terms) >= 8:
            break
    if keyword_points:
        points = clamp(keyword_points, -24.0, 24.0)
        score += points
        add_component(components, "learned_keyword_weights", points)
        add_reason(reasons, "Learned keyword signal: " + ", ".join(matched_learned_terms[:3]), points)

    # Deadline and value fit.
    d_until = days_until(posting.get("deadline"))
    if d_until is not None:
        if d_until < 0:
            points = -100.0
            score += points
            add_component(components, "deadline", points)
            add_reason(reasons, "Deadline has passed", points)
        elif d_until <= 3:
            points = -15.0
            score += points
            add_component(components, "deadline", points)
            add_reason(reasons, "Deadline is very soon", points)
        elif d_until <= 7:
            points = -8.0
            score += points
            add_component(components, "deadline", points)
            add_reason(reasons, "Deadline is soon", points)
        elif d_until <= 30:
            points = 8.0
            score += points
            add_component(components, "deadline", points)
            add_reason(reasons, "Good response window", points)
        elif d_until <= 60:
            points = 4.0
            score += points
            add_component(components, "deadline", points)
            add_reason(reasons, "Moderate response window", points)

        max_days = prefs.get("max_days_until_deadline")
        if max_days not in (None, ""):
            try:
                max_days_int = int(max_days)
                if d_until > max_days_int:
                    points = -10.0
                    score += points
                    add_component(components, "deadline_preference", points)
                    add_reason(reasons, f"Beyond preferred deadline window ({max_days_int} days)", points)
            except (TypeError, ValueError):
                pass

    value = parse_money(posting.get("contract_value"))
    if value is not None:
        min_value = prefs.get("min_contract_value")
        max_value = prefs.get("max_contract_value")
        try:
            if min_value not in (None, "") and value < float(min_value):
                points = -10.0
                score += points
                add_component(components, "contract_value", points)
                add_reason(reasons, "Below preferred contract value", points)
        except (TypeError, ValueError):
            pass
        try:
            if max_value not in (None, "") and value > float(max_value):
                points = -10.0
                score += points
                add_component(components, "contract_value", points)
                add_reason(reasons, "Above preferred contract value", points)
        except (TypeError, ValueError):
            pass

    final_score = round(clamp(score, 0.0, 100.0), 2)

    # Keep the most meaningful positive/negative reasons for display.
    reasons.sort(key=lambda item: (-abs(item["points"]), item["label"]))
    reason_labels = [
        f"{r['label']} ({'+' if r['points'] > 0 else ''}{r['points']})"
        for r in reasons[:6]
    ]

    if final_score >= 75:
        bucket = "strong_match"
    elif final_score >= 55:
        bucket = "possible_match"
    elif final_score >= 35:
        bucket = "weak_match"
    else:
        bucket = "poor_match"

    return {
        "relevance_score": final_score,
        "relevance_bucket": bucket,
        "relevance_reasons": reason_labels,
        "relevance_components": components,
    }


def score_and_sort_postings(postings: list[dict[str, Any]], prefs: dict[str, Any] | None) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for posting in postings:
        item = dict(posting)
        item.update(score_posting(item, prefs))
        scored.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
        posted = clean_string(item.get("posted_date") or item.get("date_scraped"))
        deadline = clean_string(item.get("deadline"))
        return (float(item.get("relevance_score") or 0.0), posted, deadline)

    scored.sort(key=sort_key, reverse=True)
    return scored

# Backwards-friendly alias used by proxy.py.
def rank_postings(postings: list[dict[str, Any]], prefs: dict[str, Any] | None) -> list[dict[str, Any]]:
    return score_and_sort_postings(postings, prefs)


# Backwards-friendly alias name.
def score_and_rank(postings: list[dict[str, Any]], prefs: dict[str, Any] | None) -> list[dict[str, Any]]:
    return score_and_sort_postings(postings, prefs)
