#!/usr/bin/env python3
"""
train_and_score.py

Pools ALL labeled feedback (regardless of which CSV/dataset_split it came
from) to train and 5-fold cross-validate a logistic regression relevance
model. Then runs that trained model on every UNLABELED contract (no
feedback event at all) to produce a predicted like-probability for each,
so you can see what the model would recommend on contracts nobody has
reacted to yet.

There is no comparison against the old hand-weighted scoring here, since
that scoring wasn't saved for these contracts -- this script only reports
the new model's own CV performance and its predictions on unlabeled data.

Run this from inside your repo (same directory as feature_engineering.py)
so the import resolves.

Usage:
    python train_and_score.py \
        --feedback client_feedback_rows.json \
        --postings train_set.csv validation_set.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score

import feature_engineering as fe


def load_postings(csv_paths: list[Path]) -> dict[str, dict]:
    """id -> full posting row, merged across all given CSVs."""
    postings = {}
    for path in csv_paths:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                postings[str(row["id"])] = row
    return postings


def load_feedback(json_path: Path) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feedback", default="client_feedback_rows.json")
    parser.add_argument("--postings", nargs="+", default=["train_set.csv", "validation_set.csv"])
    parser.add_argument("--min-category-count", type=int, default=2)
    parser.add_argument("--max-keywords", type=int, default=200)
    parser.add_argument("--min-keyword-count", type=int, default=2)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=15,
                         help="How many top-predicted unlabeled contracts to print")
    args = parser.parse_args()

    postings = load_postings([Path(p) for p in args.postings])
    feedback_rows = load_feedback(Path(args.feedback))

    labeled_ids = set()
    matched_feedback, unmatched_feedback = [], []
    for row in feedback_rows:
        pid = str(row.get("posting_id"))
        if pid in postings:
            matched_feedback.append(row)
            labeled_ids.add(pid)
        else:
            unmatched_feedback.append(row)

    unlabeled_postings = {pid: p for pid, p in postings.items() if pid not in labeled_ids}

    print(f"Postings loaded: {len(postings)} across {len(args.postings)} file(s)")
    print(f"Feedback rows: {len(feedback_rows)} total")
    print(f"  -> matched to a posting: {len(matched_feedback)}")
    if unmatched_feedback:
        print(f"  -> unmatched (posting_id not found in any CSV): {len(unmatched_feedback)}")
    print(f"Unlabeled postings (no feedback at all): {len(unlabeled_postings)}")

    # --- Step 1: build labeled records from ALL matched feedback, pooled ---
    records = fe.feedback_rows_to_records(matched_feedback, include_clicks=True)
    n_pos = sum(1 for r in records if r.label == 1)
    n_neg = sum(1 for r in records if r.label == 0)

    print(f"\nLabeled training records: {len(records)} ({n_pos} positive / {n_neg} negative)")

    if len(set(r.label for r in records)) < 2:
        raise SystemExit(
            "Training data has only one class (all likes or all dislikes). "
            "Logistic regression needs both positive and negative examples."
        )
    if min(n_pos, n_neg) < args.folds:
        print(
            f"WARNING: the minority class has only {min(n_pos, n_neg)} example(s), "
            f"which is fewer than --folds={args.folds}. Some CV folds may end up "
            "with only one class present, producing unreliable or NaN metrics. "
            "Treat the numbers below as a rough smoke test, not a real estimate, "
            "until you have more feedback."
        )

    vocab = fe.build_vocabulary(
        records,
        min_category_count=args.min_category_count,
        max_keywords=args.max_keywords,
        min_keyword_count=args.min_keyword_count,
    )
    X, y, w = fe.build_design_matrix(records, vocab)
    print(f"Feature dimensions: {len(X[0]) if X else 0}")

    # --- Step 2: 5-fold cross-validation on the pooled labeled data ---
    n_splits = min(args.folds, min(n_pos, n_neg)) if min(n_pos, n_neg) >= 2 else 2
    if n_splits < args.folds:
        print(f"Reducing folds from {args.folds} to {n_splits} to fit the minority class size.")

    model = LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced")
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    cv_acc = cross_val_score(model, X, y, cv=skf, scoring="accuracy")
    print(f"\n{n_splits}-fold CV on pooled labeled data:")
    print(f"  accuracy: {cv_acc.mean():.4f} (+/- {cv_acc.std():.4f})  "
          f"per-fold: {[round(a, 3) for a in cv_acc]}")

    baseline_acc = max(n_pos, n_neg) / len(records)
    print(f"  (for reference, always predicting the majority class alone "
          f"gets {baseline_acc:.4f} accuracy here)")

    # --- Step 3: fit final model on ALL labeled data ---
    final_model = LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced")
    final_model.fit(X, y, sample_weight=w)

    # --- Step 4: score every unlabeled posting ---
    scored = []
    for pid, posting in unlabeled_postings.items():
        record = fe.posting_to_record(posting)
        x = fe.vectorize_record(record, vocab)
        prob = final_model.predict_proba([x])[0][1]
        scored.append((pid, prob, posting.get("title", "")))

    scored.sort(key=lambda t: -t[1])

    print(f"\nTop {min(args.top_n, len(scored))} predicted-relevant unlabeled contracts:")
    for pid, prob, title in scored[: args.top_n]:
        print(f"  {prob:.3f}  id={pid}  {title[:80]}")

    print(
        "\nNo comparison against the old hand-weighted scoring is included -- "
        "you mentioned that data wasn't saved for these contracts. If you "
        "start saving the old scoring's output for new contracts going "
        "forward, this script can be extended to diff the two side by side."
    )


if __name__ == "__main__":
    main()