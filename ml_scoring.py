#!/usr/bin/env python3
"""
ml_scoring.py - loads a per-client logistic regression model (produced by
ml_training.train_client_model) and scores postings with it.

Kept separate from relevance_ranking.py so relevance_ranking stays
dependency-free of pickle/sklearn if the ML model is ever unavailable
(missing file, corrupt pickle, sklearn not installed) -- it should always
degrade gracefully to the hand-weighted score alone.
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any

import feature_engineering as fe

DEFAULT_MODEL_DIR = Path("./models")

# In-process cache: client_id -> (bundle, mtime, loaded_at)
# Avoids re-reading + re-unpickling the model file on every single request.
_MODEL_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SECONDS = 300  # re-check the file every 5 minutes in case it was retrained


def _model_path(client_id: str, model_dir: Path = DEFAULT_MODEL_DIR) -> Path:
    return model_dir / f"{client_id}.pkl"


def load_model_bundle(client_id: str, model_dir: Path = DEFAULT_MODEL_DIR) -> dict | None:
    """Returns the pickled bundle dict for a client, or None if no model
    exists yet or it failed to load. Never raises -- a missing/broken model
    should just mean "fall back to hand-weighted scoring", not a 500."""
    path = _model_path(client_id, model_dir)
    if not path.exists():
        return None

    cached = _MODEL_CACHE.get(client_id)
    mtime = path.stat().st_mtime
    if cached is not None:
        bundle, cached_mtime = cached
        if cached_mtime == mtime:
            return bundle

    try:
        with open(path, "rb") as fh:
            bundle = pickle.load(fh)
    except Exception:
        # Corrupt pickle, sklearn version mismatch, etc. -- degrade silently.
        return None

    _MODEL_CACHE[client_id] = (bundle, mtime)
    return bundle


def score_posting_ml(posting: dict[str, Any], bundle: dict) -> float | None:
    """Returns a like-probability in [0, 1] for one posting, or None if
    scoring failed for any reason."""
    try:
        model = bundle["model"]
        vocab = bundle["vocabulary"]
        record = fe.posting_to_record(posting)
        x = fe.vectorize_record(record, vocab)
        return float(model.predict_proba([x])[0][1])
    except Exception:
        return None