#!/usr/bin/env python3
"""
ml_training.py - Train a logistic regression relevance model per
client from client_feedback, using feature_engineering.py (Phase 2) to build
the design matrix.

Why min_feedback_events defaults to 50:
    One-hot encoding agency/NAICS/source/contract_type/set_aside can easily
    produce 30-80+ columns once OTHER/MISSING buckets are added. Fitting a
    model with far fewer examples than columns risks unstable, overfit
    weights (see the sparsity discussion: a category seen once can dominate
    the fit). 50 is a starting point, not a hard rule; tune per how
    concentrated a client's feedback categories are.
"""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feature_engineering as fe

try:
    import psycopg2.extras
except ImportError:  # pragma: no cover - allows import/testing without psycopg2 installed
    psycopg2 = None

try:
    from db import get_connection
except Exception:  # pragma: no cover - allows import without a live DB for testing
    get_connection = None

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "scikit-learn is required for ml_training.py. "
        "Install with: pip install scikit-learn --break-system-packages"
    ) from exc


DEFAULT_MODEL_DIR = Path("./models")


# ---------------------------------------------------------------------------
# Data loading (same query shape as preference_training.py for consistency)
# ---------------------------------------------------------------------------

def load_feedback_rows(connection_factory, client_id: str) -> list[dict[str, Any]]:
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required to load feedback from the database.")
    conn = connection_factory()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id FROM clients WHERE id = %s", (client_id,))
        if not cursor.fetchone():
            return []

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
                p.description,
                p.posted_date,
                p.contract_value
            FROM client_feedback f
            LEFT JOIN postings p ON p.id = f.posting_id
            WHERE f.client_id = %s
            ORDER BY f.created_at ASC
        """, (client_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_logistic_model(
    records: list[fe.RawRecord],
    *,
    min_category_count: int = 2,
    max_keywords: int = 200,
    min_keyword_count: int = 2,
    C: float = 1.0,
    max_iter: int = 1000,
) -> dict[str, Any]:
    """Fit vocabulary + logistic regression on a list of RawRecords.
    Returns a bundle dict ready to pickle."""
    vocab = fe.build_vocabulary(
        records,
        min_category_count=min_category_count,
        max_keywords=max_keywords,
        min_keyword_count=min_keyword_count,
    )
    X, y, sample_weight = fe.build_design_matrix(records, vocab)

    if len(set(y)) < 2:
        raise ValueError(
            "Training data has only one class (all positive or all negative feedback). "
            "Logistic regression needs both positive and negative examples."
        )

    model = LogisticRegression(
        C=C,
        max_iter=max_iter,
        class_weight="balanced",  # guards against a client who mostly clicks "saved"
    )
    model.fit(X, y, sample_weight=sample_weight)

    preds = model.predict(X)
    train_accuracy = accuracy_score(y, preds)

    feature_names = vocab.feature_names()
    coefficients = {
        name: round(float(coef), 4)
        for name, coef in zip(feature_names, model.coef_[0])
    }

    return {
        "model": model,
        "vocabulary": vocab,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feedback_events": len(records),
        "positive_events": sum(1 for label in y if label == 1),
        "negative_events": sum(1 for label in y if label == 0),
        "train_accuracy": round(float(train_accuracy), 4),
        "feature_names": feature_names,
        "coefficients": coefficients,
        "intercept": round(float(model.intercept_[0]), 4),
    }


def train_client_model(
    connection_factory,
    client_id: str,
    *,
    min_feedback_events: int = 50,
    include_clicks: bool = True,
    model_dir: Path = DEFAULT_MODEL_DIR,
    min_category_count: int = 2,
    max_keywords: int = 200,
    min_keyword_count: int = 2,
) -> dict[str, Any]:
    """End-to-end: load feedback, build records, fit model, save to disk.
    Mirrors the return-shape style of preference_training.train_client_preferences
    so callers/tests can handle both consistently."""
    rows = load_feedback_rows(connection_factory, client_id)
    event_count = len(rows)

    if event_count < min_feedback_events:
        return {
            "status": "not_enough_feedback",
            "client_id": client_id,
            "feedback_events": event_count,
            "min_feedback_events": min_feedback_events,
            "message": (
                f"Need at least {min_feedback_events} feedback events to train "
                f"a reliable ML model; have {event_count}. The existing "
                "rule-based/weighted scoring will keep being used until then."
            ),
        }

    records = fe.feedback_rows_to_records(rows, include_clicks=include_clicks)
    if len(records) < min_feedback_events:
        return {
            "status": "not_enough_labeled_feedback",
            "client_id": client_id,
            "feedback_events": event_count,
            "labeled_events": len(records),
            "min_feedback_events": min_feedback_events,
            "message": "Most feedback events were 'viewed' or otherwise unlabeled.",
        }

    try:
        bundle = train_logistic_model(
            records,
            min_category_count=min_category_count,
            max_keywords=max_keywords,
            min_keyword_count=min_keyword_count,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "client_id": client_id,
            "error": str(exc),
        }

    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{client_id}.pkl"
    with open(model_path, "wb") as fh:
        pickle.dump(bundle, fh)

    return {
        "status": "ok",
        "client_id": client_id,
        "model_path": str(model_path),
        "feedback_events": bundle["feedback_events"],
        "positive_events": bundle["positive_events"],
        "negative_events": bundle["negative_events"],
        "train_accuracy": bundle["train_accuracy"],
        "top_features": sorted(
            bundle["coefficients"].items(), key=lambda kv: -abs(kv[1])
        )[:15],
    }


def predict_proba(posting: dict[str, Any], model_path: Path) -> float:
    """Load a saved model bundle and score one live posting. Used by
    relevance_ranking.py once an ML model exists for a client."""
    with open(model_path, "rb") as fh:
        bundle = pickle.load(fh)
    model = bundle["model"]
    vocab = bundle["vocabulary"]
    record = fe.posting_to_record(posting)
    x = fe.vectorize_record(record, vocab)
    return float(model.predict_proba([x])[0][1])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train a per-client logistic regression relevance model from feedback."
    )
    parser.add_argument("client_id", help="Client UUID")
    parser.add_argument("--min-feedback-events", type=int, default=50)
    parser.add_argument("--exclude-clicks", action="store_true")
    parser.add_argument("--model-dir", type=str, default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--min-category-count", type=int, default=2)
    parser.add_argument("--max-keywords", type=int, default=200)
    parser.add_argument("--min-keyword-count", type=int, default=2)
    args = parser.parse_args()

    if get_connection is None:
        raise RuntimeError("Could not import db.get_connection")

    result = train_client_model(
        get_connection,
        args.client_id,
        min_feedback_events=args.min_feedback_events,
        include_clicks=not args.exclude_clicks,
        model_dir=Path(args.model_dir),
        min_category_count=args.min_category_count,
        max_keywords=args.max_keywords,
        min_keyword_count=args.min_keyword_count,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()