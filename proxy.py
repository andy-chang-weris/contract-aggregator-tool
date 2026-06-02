"""
proxy.py — Flask API server

Reads from PostgreSQL (populated by run_all.py) and serves data to the frontend.
No direct API calls are made here — all fetching happens in sources/*.py via run_all.py.

Requires these environment variables in .env:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

Endpoints:
  GET /health                  — status check
  GET /cache/clear             — clear in-memory cache
  GET /api/opportunities       — paginated contract listings with filters
"""

import os
import json
import time
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

CACHE_TTL_SECONDS = 3600
_cache = {}


# ── Cache helpers ─────────────────────────────────────────────────────────────
def cache_get(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        return entry["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}


# ── Database helper ───────────────────────────────────────────────────────────
def get_db():
    """Returns a psycopg2 connection using env vars from .env"""
    return psycopg2.connect(
        host=os.getenv("DB_HOST",     "localhost"),
        port=os.getenv("DB_PORT",     "5432"),
        dbname=os.getenv("DB_NAME",   "govcontracts"),
        user=os.getenv("DB_USER",     "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    total  = 0
    db_ok  = False

    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM postings")
        total  = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        db_ok  = True
    except Exception as e:
        print(f"  [health] DB error: {e}")

    active_cache = sum(
        1 for e in _cache.values()
        if (time.time() - e["ts"]) < CACHE_TTL_SECONDS
    )

    return jsonify({
        "status":               "ok",
        "db_connected":         db_ok,
        "total_postings_in_db": total,
        "cache_entries_active": active_cache,
        "cache_ttl_seconds":    CACHE_TTL_SECONDS,
    })


@app.route("/cache/clear")
def cache_clear():
    _cache.clear()
    return jsonify({"status": "ok", "message": "Cache cleared."})


@app.route("/api/opportunities")
def opportunities():
    # ── Pagination ────────────────────────────────────────────────────────
    limit  = max(1, min(int(request.args.get("limit",  20)), 1000))
    offset = max(0, int(request.args.get("offset", 0)))

    # ── Filters ───────────────────────────────────────────────────────────
    agency        = (request.args.get("agency")       or "").strip()
    naics         = (request.args.get("naics")        or "").strip()
    contract_type = (request.args.get("contractType") or "").strip()
    state         = (request.args.get("state")        or "").strip()
    posted_from   = (request.args.get("postedFrom")   or "").strip()  # YYYY-MM-DD
    posted_to     = (request.args.get("postedTo")     or "").strip()  # YYYY-MM-DD
    source        = (request.args.get("source")       or "").strip()  # e.g. "SAM.gov"

    # ── Cache key ─────────────────────────────────────────────────────────
    cache_key = json.dumps({
        "limit": limit, "offset": offset,
        "agency": agency, "naics": naics,
        "contractType": contract_type, "state": state,
        "postedFrom": posted_from, "postedTo": posted_to,
        "source": source,
    }, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # ── Build WHERE clause ────────────────────────────────────────────────
    # PostgreSQL uses %s placeholders — NOT ? like SQLite
    # ILIKE = case-insensitive LIKE in PostgreSQL
    conditions = []
    params     = []

    if agency:
        conditions.append("agency ILIKE %s")
        params.append(f"%{agency}%")

    if naics:
        conditions.append("naics = %s")
        params.append(naics)

    if contract_type:
        conditions.append("contract_type ILIKE %s")
        params.append(f"%{contract_type}%")

    if state:
        conditions.append("place_of_performance ILIKE %s")
        params.append(f"%{state}%")

    if posted_from:
        conditions.append("posted_date >= %s")
        params.append(posted_from)

    if posted_to:
        conditions.append("posted_date <= %s")
        params.append(posted_to)

    if source:
        conditions.append("source_site = %s")
        params.append(source)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        conn   = get_db()
        # RealDictCursor makes rows behave like dicts automatically
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Total count with filters applied
        cursor.execute(f"SELECT COUNT(*) FROM postings {where}", params)
        total = cursor.fetchone()["count"]

        # Paginated results
        cursor.execute(
            f"""
            SELECT
                source_site, external_id, title, agency, organization,
                naics, description, posted_date, deadline, award_date,
                contract_value, award_status, contract_type, acq_strategy,
                place_of_performance, url, date_scraped
            FROM postings
            {where}
            ORDER BY posted_date DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset]
        )
        rows = cursor.fetchall()

        cursor.close()
        conn.close()

    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

    result = {
        "total":         total,
        "limit":         limit,
        "offset":        offset,
        "opportunities": [dict(row) for row in rows],
        "cached":        False,
    }

    cache_set(cache_key, result)
    return jsonify(result)


# ── Future source endpoints ───────────────────────────────────────────────────
# @app.route("/api/eva/opportunities")
# def eva_opportunities(): pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  GovContracts proxy  → http://localhost:{port}")
    print(f"  Health check        → http://localhost:{port}/health")
    print(f"  Opportunities       → http://localhost:{port}/api/opportunities\n")
    app.run(debug=True, port=port)