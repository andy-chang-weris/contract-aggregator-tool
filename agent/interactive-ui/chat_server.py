"""Isolated local chat API for the interactive UI prototype.

Run from the agent directory:
    python interactive-ui/chat_server.py

This intentionally does not modify or import the parent dashboard proxy.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, jsonify, request

try:
    from flask_cors import CORS
except Exception:  # pragma: no cover - optional in some local installs
    CORS = None  # type: ignore[assignment]

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from rag import RagAgent  # noqa: E402

app = Flask(__name__)
if CORS is not None:
    CORS(app)

_agent: RagAgent | None = None
_agent_lock = Lock()


def get_agent() -> RagAgent:
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = RagAgent.create()
    return _agent


def serialize_source(source: Any) -> dict[str, Any]:
    metadata = dict(getattr(source, "metadata", {}) or {})
    return {
        "score": float(getattr(source, "score", 0.0) or 0.0),
        "metadata": metadata,
        "title": metadata.get("title"),
        "agency": metadata.get("agency") or metadata.get("organization"),
        "deadline": metadata.get("deadline"),
        "url": metadata.get("url"),
    }


@app.after_request
def add_cors_headers(response):
    if CORS is None:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "interactive-chat", "rag_agent_loaded": _agent is not None})


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        response = get_agent().ask(question)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "answer": response.answer,
        "index_source": response.index_source,
        "sources": [serialize_source(source) for source in response.sources],
    })


if __name__ == "__main__":
    port = int(os.getenv("CHAT_SERVER_PORT", "5055"))
    app.run(host="127.0.0.1", port=port, debug=False)
