#!/usr/bin/env python3
"""
preference_training.py - MVP preference learning from client feedback.

Reads client_feedback joined to postings, learns simple positive/negative
weights for NAICS, agencies, sources, contract types, set-asides, and keywords,
then writes the learned profile back to client_preferences.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter

import psycopg2.extras
from typing import Any, Callable

try:
    from db import get_connection
except Exception:  # pragma: no cover - lets proxy.py import with its own factory
    get_connection = None


POSITIVE_ACTION_WEIGHTS = {"like": 1.0}
NEGATIVE_ACTION_WEIGHTS = {"dislike": -1.0}
IGNORED_ACTIONS = set()  # nothing left to ignore

STOPWORDS = {
    "about", "above", "after", "again", "agency", "all", "also", "and",
    "any", "are", "award", "based", "between", "business", "contract",
    "contracts", "contractor", "contractors", "description", "details", "due",
    "each", "federal", "from", "government", "have", "include", "includes",
    "including", "into", "issue", "issued", "more", "must", "need", "needs",
    "notice", "offer", "offers", "open", "opportunity", "performance", "place",
    "posted", "procurement", "provide", "provides", "providing", "request",
    "required", "requirements", "response", "services", "shall", "should",
    "solicitation", "source", "sources", "state", "support", "technical",
    "that", "their", "there", "these", "this", "through", "under", "using",
    "vendor", "vendors", "virginia", "will", "with", "work", "year",
}

WORD_RE = re.compile(r"[a-z][a-z0-9&/-]{3,}")


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_key(value: Any) -> str:
    return clean_string(value).upper()


def get_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = row.get("posting_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    return snapshot if isinstance(snapshot, dict) else {}


def posting_value(row: dict[str, Any], snapshot: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    if value not in (None, ""):
        return value
    return snapshot.get(key)


def tokenize_keywords(title: str, description: str, max_terms: int = 80) -> list[str]:
    """Return normalized unigrams and short bigrams from title/description."""
    text = f"{title or ''} {description or ''}".lower()
    words = [w.strip("-/&") for w in WORD_RE.findall(text)]
    words = [w for w in words if len(w) >= 4 and w not in STOPWORDS and not w.isdigit()]

    terms: list[str] = []
    terms.extend(words[:max_terms])

    for left, right in zip(words, words[1:]):
        if left in STOPWORDS or right in STOPWORDS:
            continue
        if left == right:
            continue
        terms.append(f"{left} {right}")
        if len(terms) >= max_terms * 2:
            break

    return terms


def add_signal(counter: Counter[str], key: Any, weight: float) -> None:
    normalized = normalize_key(key)
    if normalized:
        counter[normalized] += weight


def add_keyword_signal(counter: Counter[str], title: str, description: str, weight: float) -> None:
    for term in tokenize_keywords(title, description):
        counter[term] += weight


def sorted_positive(counter: Counter[str], min_score: float, limit: int) -> list[str]:
    items = [(k, v) for k, v in counter.items() if v >= min_score]
    items.sort(key=lambda item: (-item[1], item[0]))
    return [k for k, _ in items[:limit]]


def sorted_negative(counter: Counter[str], min_score: float, limit: int) -> list[str]:
    items = [(k, v) for k, v in counter.items() if v <= min_score]
    items.sort(key=lambda item: (item[1], item[0]))
    return [k for k, _ in items[:limit]]


def weights_dict(counter: Counter[str], limit: int = 100) -> dict[str, float]:
    items = sorted(counter.items(), key=lambda item: (-abs(item[1]), item[0]))[:limit]
    return {k: round(float(v), 3) for k, v in items if abs(v) > 0.0001}


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_string(v) for v in value if clean_string(v)]
    if isinstance(value, tuple):
        return [clean_string(v) for v in value if clean_string(v)]
    return [clean_string(value)] if clean_string(value) else []


def merge_lists(existing: list[str], learned_positive: list[str], learned_negative: list[str], limit: int) -> list[str]:
    negative_set = {x.upper() for x in learned_negative}
    seen: set[str] = set()
    merged: list[str] = []
    for item in existing + learned_positive:
        key = item.upper()
        if not key or key in seen or key in negative_set:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def merge_exclusions(existing: list[str], learned_negative: list[str], learned_positive: list[str], limit: int) -> list[str]:
    positive_set = {x.upper() for x in learned_positive}
    seen: set[str] = set()
    merged: list[str] = []
    for item in existing + learned_negative:
        key = item.upper()
        if not key or key in seen or key in positive_set:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def build_profile_summary(
    event_count: int,
    positive_event_count: int,
    negative_event_count: int,
    learned: dict[str, list[str]],
) -> str:
    parts = [
        f"Trained on {event_count} feedback event(s)",
        f"{positive_event_count} positive",
        f"{negative_event_count} negative",
    ]

    highlights = []
    if learned.get("preferred_naics"):
        highlights.append("preferred NAICS: " + ", ".join(learned["preferred_naics"][:5]))
    if learned.get("excluded_naics"):
        highlights.append("excluded NAICS: " + ", ".join(learned["excluded_naics"][:5]))
    if learned.get("preferred_agencies"):
        highlights.append("preferred agencies: " + ", ".join(learned["preferred_agencies"][:3]))
    if learned.get("excluded_agencies"):
        highlights.append("excluded agencies: " + ", ".join(learned["excluded_agencies"][:3]))
    if learned.get("preferred_keywords"):
        highlights.append("liked keywords: " + ", ".join(learned["preferred_keywords"][:8]))
    if learned.get("disliked_keywords"):
        highlights.append("disliked keywords: " + ", ".join(learned["disliked_keywords"][:8]))

    if highlights:
        return ". ".join(parts) + ". " + "; ".join(highlights) + "."
    return ". ".join(parts) + ". No strong learned preferences yet."


def train_client_preferences(
    connection_factory: Callable[[], Any],
    client_id: str,
    *,
    min_feedback_events: int = 1,
    include_clicks: bool = True,
    positive_threshold: float = 1.0,
    negative_threshold: float = -2.0,
    max_profile_items: int = 25,
) -> dict[str, Any]:
    """Train/update one client's preference profile from feedback events."""
    conn = connection_factory()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cursor.execute("SELECT id FROM clients WHERE id = %s", (client_id,))
        if not cursor.fetchone():
            return {"status": "error", "error": "client_id does not exist", "client_id": client_id}

        cursor.execute("""
            INSERT INTO client_preferences (client_id)
            VALUES (%s)
            ON CONFLICT (client_id) DO NOTHING
        """, (client_id,))

        cursor.execute("""
            SELECT
                f.action,
                f.rating,
                f.feedback_source,
                f.metadata,
                f.posting_snapshot,
                p.naics,
                p.agency,
                p.source_site,
                p.award_status,
                p.contract_type,
                p.acq_strategy,
                p.title,
                p.description
            FROM client_feedback f
            LEFT JOIN postings p ON p.id = f.posting_id
            WHERE f.client_id = %s
            ORDER BY f.created_at ASC
        """, (client_id,))
        rows = [dict(row) for row in cursor.fetchall()]

        event_count = len(rows)
        if event_count < min_feedback_events:
            conn.commit()
            return {
                "status": "not_enough_feedback",
                "client_id": client_id,
                "feedback_events": event_count,
                "min_feedback_events": min_feedback_events,
                "message": "Not enough feedback events to train preferences yet.",
            }

        naics_scores: Counter[str] = Counter()
        agency_scores: Counter[str] = Counter()
        source_scores: Counter[str] = Counter()
        keyword_scores: Counter[str] = Counter()
        contract_type_scores: Counter[str] = Counter()
        set_aside_scores: Counter[str] = Counter()

        positive_event_count = 0
        negative_event_count = 0
        ignored_event_count = 0

        for row in rows:
            action = row.get("action")
            if action in IGNORED_ACTIONS:
                ignored_event_count += 1
                continue
            if action == "clicked" and not include_clicks:
                ignored_event_count += 1
                continue

            weight = POSITIVE_ACTION_WEIGHTS.get(action, NEGATIVE_ACTION_WEIGHTS.get(action, 0.0))
            rating = row.get("rating")
            if rating is not None:
                try:
                    rating_int = int(rating)
                    if rating_int >= 4:
                        weight += 0.5
                    elif rating_int <= 2:
                        weight -= 0.5
                except (TypeError, ValueError):
                    pass

            if weight > 0:
                positive_event_count += 1
            elif weight < 0:
                negative_event_count += 1
            else:
                ignored_event_count += 1
                continue

            snapshot = get_snapshot(row)
            title = clean_string(posting_value(row, snapshot, "title"))
            description = clean_string(posting_value(row, snapshot, "description"))

            add_signal(naics_scores, posting_value(row, snapshot, "naics"), weight)
            add_signal(agency_scores, posting_value(row, snapshot, "agency"), weight)
            add_signal(source_scores, posting_value(row, snapshot, "source_site"), weight)
            add_signal(contract_type_scores, posting_value(row, snapshot, "contract_type") or posting_value(row, snapshot, "award_status"), weight)
            add_signal(set_aside_scores, posting_value(row, snapshot, "acq_strategy"), weight)
            add_keyword_signal(keyword_scores, title, description, weight)

        learned = {
            "preferred_naics": sorted_positive(naics_scores, positive_threshold, max_profile_items),
            "excluded_naics": sorted_negative(naics_scores, negative_threshold, max_profile_items),
            "preferred_agencies": sorted_positive(agency_scores, positive_threshold, max_profile_items),
            "excluded_agencies": sorted_negative(agency_scores, negative_threshold, max_profile_items),
            "preferred_sources": sorted_positive(source_scores, positive_threshold, 10),
            "excluded_sources": sorted_negative(source_scores, negative_threshold, 10),
            "preferred_keywords": sorted_positive(keyword_scores, positive_threshold, max_profile_items),
            "disliked_keywords": sorted_negative(keyword_scores, negative_threshold, max_profile_items),
            "preferred_contract_types": sorted_positive(contract_type_scores, positive_threshold, 10),
            "preferred_set_asides": sorted_positive(set_aside_scores, positive_threshold, 10),
        }

        cursor.execute("SELECT * FROM client_preferences WHERE client_id = %s FOR UPDATE", (client_id,))
        prefs = dict(cursor.fetchone() or {})

        updated = {
            "preferred_naics": merge_lists(as_list(prefs.get("preferred_naics")), learned["preferred_naics"], learned["excluded_naics"], max_profile_items),
            "excluded_naics": merge_exclusions(as_list(prefs.get("excluded_naics")), learned["excluded_naics"], learned["preferred_naics"], max_profile_items),
            "preferred_agencies": merge_lists(as_list(prefs.get("preferred_agencies")), learned["preferred_agencies"], learned["excluded_agencies"], max_profile_items),
            "excluded_agencies": merge_exclusions(as_list(prefs.get("excluded_agencies")), learned["excluded_agencies"], learned["preferred_agencies"], max_profile_items),
            "preferred_sources": merge_lists(as_list(prefs.get("preferred_sources")), learned["preferred_sources"], learned["excluded_sources"], 10),
            "excluded_sources": merge_exclusions(as_list(prefs.get("excluded_sources")), learned["excluded_sources"], learned["preferred_sources"], 10),
            "preferred_keywords": merge_lists(as_list(prefs.get("preferred_keywords")), learned["preferred_keywords"], learned["disliked_keywords"], max_profile_items),
            "disliked_keywords": merge_exclusions(as_list(prefs.get("disliked_keywords")), learned["disliked_keywords"], learned["preferred_keywords"], max_profile_items),
            "preferred_contract_types": merge_lists(as_list(prefs.get("preferred_contract_types")), learned["preferred_contract_types"], [], 10),
            "preferred_set_asides": merge_lists(as_list(prefs.get("preferred_set_asides")), learned["preferred_set_asides"], [], 10),
        }

        summary = build_profile_summary(
            event_count,
            positive_event_count,
            negative_event_count,
            learned,
        )

        weights = {
            "naics_weights": weights_dict(naics_scores),
            "agency_weights": weights_dict(agency_scores),
            "source_weights": weights_dict(source_scores),
            "keyword_weights": weights_dict(keyword_scores),
        }

        cursor.execute("""
            UPDATE client_preferences
            SET preferred_naics = %s,
                excluded_naics = %s,
                preferred_agencies = %s,
                excluded_agencies = %s,
                preferred_sources = %s,
                excluded_sources = %s,
                preferred_keywords = %s,
                disliked_keywords = %s,
                preferred_contract_types = %s,
                preferred_set_asides = %s,
                naics_weights = %s::jsonb,
                agency_weights = %s::jsonb,
                source_weights = %s::jsonb,
                keyword_weights = %s::jsonb,
                profile_summary = %s,
                last_trained_at = now(),
                updated_at = now()
            WHERE client_id = %s
            RETURNING *
        """, (
            updated["preferred_naics"],
            updated["excluded_naics"],
            updated["preferred_agencies"],
            updated["excluded_agencies"],
            updated["preferred_sources"],
            updated["excluded_sources"],
            updated["preferred_keywords"],
            updated["disliked_keywords"],
            updated["preferred_contract_types"],
            updated["preferred_set_asides"],
            json.dumps(weights["naics_weights"]),
            json.dumps(weights["agency_weights"]),
            json.dumps(weights["source_weights"]),
            json.dumps(weights["keyword_weights"]),
            summary,
            client_id,
        ))

        updated_prefs = dict(cursor.fetchone())
        conn.commit()

        return {
            "status": "ok",
            "client_id": client_id,
            "feedback_events": event_count,
            "positive_events": positive_event_count,
            "negative_events": negative_event_count,
            "ignored_events": ignored_event_count,
            "thresholds": {
                "positive_threshold": positive_threshold,
                "negative_threshold": negative_threshold,
                "include_clicks": include_clicks,
            },
            "learned": learned,
            "weights": weights,
            "profile_summary": summary,
            "preferences": updated_prefs,
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Train one client's preference profile from feedback.")
    parser.add_argument("client_id", help="Client UUID")
    parser.add_argument("--min-feedback-events", type=int, default=1)
    parser.add_argument("--exclude-clicks", action="store_true", help="Ignore clicked events during training")
    parser.add_argument("--positive-threshold", type=float, default=1.0)
    parser.add_argument("--negative-threshold", type=float, default=-2.0)
    args = parser.parse_args()

    if get_connection is None:
        raise RuntimeError("Could not import db.get_connection")

    result = train_client_preferences(
        get_connection,
        args.client_id,
        min_feedback_events=args.min_feedback_events,
        include_clicks=not args.exclude_clicks,
        positive_threshold=args.positive_threshold,
        negative_threshold=args.negative_threshold,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
