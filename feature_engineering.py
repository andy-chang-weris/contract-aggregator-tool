#!/usr/bin/env python3
"""
feature_engineering.py - convert postings/feedback rows into numeric
feature vectors suitable for scikit-learn.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

MONEY_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
WORD_RE = re.compile(r"[a-z][a-z0-9&/-]{3,}")

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

CATEGORICAL_FIELDS = ["agency", "naics", "source_site", "contract_type", "set_aside"]
NUMERIC_FIELDS = ["days_since_posted", "contract_value"]

OTHER_TOKEN = "__OTHER__"
MISSING_TOKEN = "__MISSING__"


# ---------------------------------------------------------------------------
# Small helpers (kept dependency-free / consistent with preference_training.py)
# ---------------------------------------------------------------------------

def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_key(value: Any) -> str:
    return clean_string(value).upper()


def parse_money(value: Any) -> float | None:
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
        values = [float(m.replace(",", "")) for m in matches]
        return max(values) * multiplier
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    text = clean_string(value)
    if not text:
        return None
    # Try a handful of common formats seen across source sites before giving up.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 10], fmt).date()
        except ValueError:
            continue
    # Last resort: pull the date portion off an ISO-like timestamp.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def days_since_posted(posted_date_value: Any, reference: date | None = None) -> float | None:
    posted = parse_date(posted_date_value)
    if posted is None:
        return None
    ref = reference or date.today()
    return float((ref - posted).days)


def tokenize_keywords(title: str, description: str, max_terms: int = 40) -> list[str]:
    """Unigrams and adjacent bigrams from title/description, same approach as
    preference_training.py so keyword features are comparable across the two
    systems."""
    text = f"{title or ''} {description or ''}".lower()
    words = [w.strip("-/&") for w in WORD_RE.findall(text)]
    words = [w for w in words if len(w) >= 4 and w not in STOPWORDS and not w.isdigit()]

    terms: list[str] = list(dict.fromkeys(words[:max_terms]))  # de-dup, preserve order
    for left, right in zip(words, words[1:]):
        if left in STOPWORDS or right in STOPWORDS or left == right:
            continue
        bigram = f"{left} {right}"
        if bigram not in terms:
            terms.append(bigram)
        if len(terms) >= max_terms * 2:
            break
    return terms


def posting_value(row: dict[str, Any], snapshot: dict[str, Any], key: str) -> Any:
    """Prefer the live posting value, fall back to the feedback-time snapshot.
    Mirrors preference_training.py's posting_value() for consistency."""
    value = row.get(key)
    if value not in (None, ""):
        return value
    return snapshot.get(key)


def get_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = row.get("posting_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    return snapshot if isinstance(snapshot, dict) else {}


# ---------------------------------------------------------------------------
# Raw record extraction
# ---------------------------------------------------------------------------

@dataclass
class RawRecord:
    """One labeled training example before vectorization."""
    label: int  # 1 = interested, 0 = not interested
    agency: str
    naics: str
    source_site: str
    contract_type: str
    set_aside: str
    days_since_posted: float | None
    contract_value: float | None
    title: str
    description: str
    weight: float = 1.0  # optional sample weight, e.g. from rating


POSITIVE_ACTIONS = {"saved", "highly_relevant", "applied"}
NEGATIVE_ACTIONS = {"not_interested", "dismissed"}
NEUTRAL_ACTIONS = {"viewed"}  # excluded by default
CLICK_ACTION = "clicked"  # ambiguous; counted positive only if include_clicks=True


def label_for_action(action: str, rating: int | None, include_clicks: bool) -> int | None:
    """Map a client_feedback action (+ optional rating) to a binary label.
    Returns None if the action should be excluded from training."""
    if action in NEUTRAL_ACTIONS:
        return None
    if action in POSITIVE_ACTIONS:
        return 1
    if action in NEGATIVE_ACTIONS:
        return 0
    if action == CLICK_ACTION:
        if not include_clicks:
            return None
        # A bare click is weak positive signal unless contradicted by rating.
        if rating is not None and rating <= 2:
            return 0
        return 1
    return None


def feedback_rows_to_records(
    rows: list[dict[str, Any]],
    include_clicks: bool = True,
) -> list[RawRecord]:
    """Convert client_feedback rows (joined to postings, as already produced by
    preference_training.py's query) into RawRecord training examples.

    Expects each row to have the same shape used in preference_training.py:
    action, rating, posting_snapshot, and the joined postings columns
    (naics, agency, source_site, award_status, contract_type, acq_strategy,
    title, description).
    """
    records: list[RawRecord] = []
    for row in rows:
        action = clean_string(row.get("action")).lower()
        rating = row.get("rating")
        try:
            rating_int = int(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_int = None

        label = label_for_action(action, rating_int, include_clicks)
        if label is None:
            continue

        snapshot = get_snapshot(row)

        weight = 1.0
        if rating_int is not None:
            # Mild sample weighting: strong ratings count a bit more, same
            # spirit as the +/-0.5 adjustment in preference_training.py.
            if rating_int >= 4 or rating_int <= 2:
                weight = 1.5

        records.append(RawRecord(
            label=label,
            agency=clean_string(posting_value(row, snapshot, "agency")),
            naics=clean_string(posting_value(row, snapshot, "naics")),
            source_site=clean_string(posting_value(row, snapshot, "source_site")),
            contract_type=clean_string(
                posting_value(row, snapshot, "contract_type")
                or posting_value(row, snapshot, "award_status")
            ),
            set_aside=clean_string(posting_value(row, snapshot, "acq_strategy")),
            days_since_posted=days_since_posted(posting_value(row, snapshot, "posted_date")),
            contract_value=parse_money(posting_value(row, snapshot, "contract_value")),
            title=clean_string(posting_value(row, snapshot, "title")),
            description=clean_string(posting_value(row, snapshot, "description")),
            weight=weight,
        ))
    return records


# ---------------------------------------------------------------------------
# Vocabulary building (fit) and vectorization (transform)
# ---------------------------------------------------------------------------

@dataclass
class FeatureVocabulary:
    """Fixed vocabulary learned from training data. Must be saved with the
    model so prediction-time vectorization matches training-time exactly."""
    categorical_values: dict[str, list[str]] = field(default_factory=dict)  # field -> sorted category list (excl. OTHER)
    keyword_vocab: list[str] = field(default_factory=list)
    numeric_means: dict[str, float] = field(default_factory=dict)
    numeric_stds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "categorical_values": self.categorical_values,
            "keyword_vocab": self.keyword_vocab,
            "numeric_means": self.numeric_means,
            "numeric_stds": self.numeric_stds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeatureVocabulary":
        return cls(
            categorical_values=data.get("categorical_values", {}),
            keyword_vocab=data.get("keyword_vocab", []),
            numeric_means=data.get("numeric_means", {}),
            numeric_stds=data.get("numeric_stds", {}),
        )

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for f in CATEGORICAL_FIELDS:
            for val in self.categorical_values.get(f, []):
                names.append(f"{f}={val}")
            names.append(f"{f}={OTHER_TOKEN}")
            names.append(f"{f}={MISSING_TOKEN}")
        for kw in self.keyword_vocab:
            names.append(f"kw={kw}")
        for f in NUMERIC_FIELDS:
            names.append(f"num={f}")
        return names


def build_vocabulary(
    records: list[RawRecord],
    min_category_count: int = 2,
    max_keywords: int = 200,
    min_keyword_count: int = 2,
) -> FeatureVocabulary:
    """Phase 2, fit step: scan all training records once to decide which
    categorical values and keywords are common enough to get their own
    column. Values seen fewer than min_category_count times collapse into
    the OTHER bucket for that field; this directly addresses the
    one-hot-sparsity problem (e.g. an agency that appears once should not
    get its own dedicated, unstable weight)."""
    field_counts: dict[str, Counter] = {f: Counter() for f in CATEGORICAL_FIELDS}
    keyword_counts: Counter = Counter()
    numeric_values: dict[str, list[float]] = {f: [] for f in NUMERIC_FIELDS}

    for r in records:
        for f in CATEGORICAL_FIELDS:
            val = normalize_key(getattr(r, f))
            if val:
                field_counts[f][val] += 1
        for term in set(tokenize_keywords(r.title, r.description)):
            keyword_counts[term] += 1
        if r.days_since_posted is not None:
            numeric_values["days_since_posted"].append(r.days_since_posted)
        if r.contract_value is not None:
            numeric_values["contract_value"].append(r.contract_value)

    categorical_values = {
        f: sorted([val for val, count in counts.items() if count >= min_category_count])
        for f, counts in field_counts.items()
    }

    keyword_vocab = sorted(
        [term for term, count in keyword_counts.items() if count >= min_keyword_count],
        key=lambda t: (-keyword_counts[t], t),
    )[:max_keywords]

    numeric_means: dict[str, float] = {}
    numeric_stds: dict[str, float] = {}
    for f in NUMERIC_FIELDS:
        vals = numeric_values[f]
        if vals:
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = variance ** 0.5
        else:
            mean, std = 0.0, 1.0
        numeric_means[f] = mean
        numeric_stds[f] = std if std > 1e-9 else 1.0

    return FeatureVocabulary(
        categorical_values=categorical_values,
        keyword_vocab=keyword_vocab,
        numeric_means=numeric_means,
        numeric_stds=numeric_stds,
    )


def vectorize_record(record: RawRecord, vocab: FeatureVocabulary) -> list[float]:
    """Phase 2, transform step: turn one RawRecord into a numeric feature
    vector using a previously-fit vocabulary. This is also what
    relevance_ranking.py / proxy.py would call at prediction time on a live
    posting (after wrapping it as a RawRecord with label ignored)."""
    vec: list[float] = []

    for f in CATEGORICAL_FIELDS:
        raw_val = normalize_key(getattr(record, f))
        known_values = vocab.categorical_values.get(f, [])
        if not raw_val:
            row = [0.0] * len(known_values) + [0.0, 1.0]  # OTHER=0, MISSING=1
        elif raw_val in known_values:
            row = [1.0 if v == raw_val else 0.0 for v in known_values] + [0.0, 0.0]
        else:
            row = [0.0] * len(known_values) + [1.0, 0.0]  # unseen category -> OTHER
        vec.extend(row)

    record_terms = set(tokenize_keywords(record.title, record.description))
    for kw in vocab.keyword_vocab:
        vec.append(1.0 if kw in record_terms else 0.0)

    for f in NUMERIC_FIELDS:
        raw_val = getattr(record, f)
        mean = vocab.numeric_means.get(f, 0.0)
        std = vocab.numeric_stds.get(f, 1.0)
        if raw_val is None:
            vec.append(0.0)  # mean-imputed -> 0 after standardization
        else:
            vec.append((raw_val - mean) / std)

    return vec


def build_design_matrix(
    records: list[RawRecord],
    vocab: FeatureVocabulary,
) -> tuple[list[list[float]], list[int], list[float]]:
    """Returns (X, y, sample_weights) ready for sklearn."""
    X = [vectorize_record(r, vocab) for r in records]
    y = [r.label for r in records]
    weights = [r.weight for r in records]
    return X, y, weights


def posting_to_record(posting: dict[str, Any]) -> RawRecord:
    """Wrap a live `postings` row (e.g. from the ranked-opportunities query)
    as a RawRecord for prediction. label is unused at prediction time."""
    return RawRecord(
        label=0,
        agency=clean_string(posting.get("agency")),
        naics=clean_string(posting.get("naics")),
        source_site=clean_string(posting.get("source_site")),
        contract_type=clean_string(posting.get("contract_type") or posting.get("award_status")),
        set_aside=clean_string(posting.get("acq_strategy")),
        days_since_posted=days_since_posted(posting.get("posted_date")),
        contract_value=parse_money(posting.get("contract_value")),
        title=clean_string(posting.get("title")),
        description=clean_string(posting.get("description")),
    )