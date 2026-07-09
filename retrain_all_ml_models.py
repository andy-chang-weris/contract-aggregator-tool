#!/usr/bin/env python3
"""
retrain_all_ml_models.py - Phase 4: periodic batch retraining.

Run this on a schedule (cron, systemd timer, etc.) to retrain the ML model
for every client who has enough new feedback since their last training run.
This is the "periodic" half of Phase 4 — the train-ml-model Flask endpoint
covers on-demand/admin-triggered retraining, this script covers the
unattended scheduled case.

Typical cron entry (nightly at 2am):
    0 2 * * * cd /path/to/app && /path/to/venv/bin/python3 retrain_all_ml_models.py >> /var/log/ml_retrain.log 2>&1

Behavior:
    - Iterates every row in `clients`.
    - Skips clients with fewer than --min-feedback-events feedback rows
      (cheap COUNT query, avoids loading full feedback for clients with no
      chance of training successfully).
    - Skips clients whose feedback count hasn't grown by --min-new-events
      since their existing model.pkl was trained, so a quiet client doesn't
      get needlessly retrained every night.
    - Logs one summary line per client to stdout; full per-client detail
      goes to stdout as well, redirect to a logfile via cron as shown above.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    from db import get_connection
except Exception:
    get_connection = None

from ml_training import train_client_model, DEFAULT_MODEL_DIR


def get_feedback_count(connection_factory, client_id: str) -> int:
    conn = connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM client_feedback WHERE client_id = %s",
            (client_id,),
        )
        return cursor.fetchone()[0]
    finally:
        cursor.close()
        conn.close()


def get_existing_model_event_count(model_path: Path) -> int | None:
    if not model_path.exists():
        return None
    try:
        with open(model_path, "rb") as fh:
            bundle = pickle.load(fh)
        return bundle.get("feedback_events")
    except Exception:
        # Corrupt or incompatible pickle (e.g. after a scikit-learn version
        # bump) — treat as "no usable existing model" so it gets retrained.
        return None


def list_clients(connection_factory) -> list[dict]:
    conn = connection_factory()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, name FROM clients ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def run(
    *,
    min_feedback_events: int = 50,
    min_new_events: int = 10,
    model_dir: Path = DEFAULT_MODEL_DIR,
    include_clicks: bool = True,
    dry_run: bool = False,
) -> dict:
    if get_connection is None:
        raise RuntimeError("Could not import db.get_connection")
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required to run this script against the database.")

    started_at = datetime.now(timezone.utc).isoformat()
    clients = list_clients(get_connection)

    summary = {
        "started_at": started_at,
        "total_clients": len(clients),
        "trained": [],
        "skipped_low_feedback": [],
        "skipped_no_new_data": [],
        "errors": [],
    }

    for client in clients:
        client_id = str(client["id"])
        name = client.get("name") or client_id

        feedback_count = get_feedback_count(get_connection, client_id)
        if feedback_count < min_feedback_events:
            summary["skipped_low_feedback"].append({
                "client_id": client_id, "name": name, "feedback_events": feedback_count,
            })
            print(f"[skip] {name} ({client_id}): {feedback_count} feedback events, "
                  f"below min_feedback_events={min_feedback_events}")
            continue

        model_path = model_dir / f"{client_id}.pkl"
        existing_count = get_existing_model_event_count(model_path)
        if existing_count is not None and (feedback_count - existing_count) < min_new_events:
            summary["skipped_no_new_data"].append({
                "client_id": client_id, "name": name,
                "feedback_events": feedback_count, "existing_model_events": existing_count,
            })
            print(f"[skip] {name} ({client_id}): only "
                  f"{feedback_count - existing_count} new events since last training "
                  f"(min_new_events={min_new_events})")
            continue

        if dry_run:
            print(f"[dry-run] would retrain {name} ({client_id}): "
                  f"{feedback_count} total feedback events")
            continue

        try:
            result = train_client_model(
                get_connection,
                client_id,
                min_feedback_events=min_feedback_events,
                include_clicks=include_clicks,
                model_dir=model_dir,
            )
            if result.get("status") == "ok":
                summary["trained"].append({
                    "client_id": client_id,
                    "name": name,
                    "train_accuracy": result.get("train_accuracy"),
                    "feedback_events": result.get("feedback_events"),
                })
                print(f"[ok] {name} ({client_id}): retrained, "
                      f"accuracy={result.get('train_accuracy')}, "
                      f"events={result.get('feedback_events')}")
            else:
                summary["errors"].append({
                    "client_id": client_id, "name": name, "result": result,
                })
                print(f"[warn] {name} ({client_id}): {result.get('status')} - "
                      f"{result.get('message') or result.get('error')}")
        except Exception as exc:
            summary["errors"].append({
                "client_id": client_id, "name": name, "error": str(exc),
            })
            print(f"[error] {name} ({client_id}): {exc}", file=sys.stderr)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrain ML relevance models for all clients with enough new feedback."
    )
    parser.add_argument("--min-feedback-events", type=int, default=50,
                         help="Minimum total feedback events before a client is trained at all.")
    parser.add_argument("--min-new-events", type=int, default=10,
                         help="Minimum new feedback events since last training before retraining.")
    parser.add_argument("--model-dir", type=str, default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--exclude-clicks", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be retrained without actually training.")
    parser.add_argument("--summary-json", type=str, default=None,
                         help="Optional path to write a JSON run summary, e.g. for monitoring.")
    args = parser.parse_args()

    summary = run(
        min_feedback_events=args.min_feedback_events,
        min_new_events=args.min_new_events,
        model_dir=Path(args.model_dir),
        include_clicks=not args.exclude_clicks,
        dry_run=args.dry_run,
    )

    print("\n--- Summary ---")
    print(f"Trained: {len(summary['trained'])}")
    print(f"Skipped (low feedback): {len(summary['skipped_low_feedback'])}")
    print(f"Skipped (no new data): {len(summary['skipped_no_new_data'])}")
    print(f"Errors: {len(summary['errors'])}")

    if args.summary_json:
        with open(args.summary_json, "w") as fh:
            json.dump(summary, fh, indent=2, default=str)


if __name__ == "__main__":
    main()