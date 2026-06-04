"""
proxy.py — Flask API server
"""

import os
import json
import time
import psycopg2
import psycopg2.extras
from datetime import datetime
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

_cache        = {}

# ── Cache helpers ─────────────────────────────────────────────────────────────
def cache_get(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        return entry["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}


# ── PostgreSQL connection ─────────────────────────────────────────────────────
def get_db():
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
    active_cache = sum(
        1 for e in _cache.values()
        if (time.time() - e["ts"]) < CACHE_TTL_SECONDS
    )

    total = 0
    db_ok = False
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

    return jsonify({
        "status":               "ok",
        "mode":                 "database",
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

    # ── Filters (no date filters) ─────────────────────────────────────────
    agency        = (request.args.get("agency")       or "").strip()
    naics_raw = request.args.get("naics") or ""
    naics_list = [n.strip() for n in naics_raw.split(",") if n.strip()]
    contract_type = (request.args.get("contractType") or "").strip()
    state         = (request.args.get("state")        or "").strip()
    source        = (request.args.get("source")       or "").strip()

    sort_by  = (request.args.get("sortBy")  or "").strip()   # "posted_date" or ""
    sort_dir = (request.args.get("sortDir") or "desc").strip().lower()

    # Whitelist allowed sort fields — never pass raw user input to SQL
    ALLOWED_SORT_FIELDS = {"posted_date"}
    if sort_by not in ALLOWED_SORT_FIELDS:
        sort_by = None
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    # ── Cache key ─────────────────────────────────────────────────────────
    cache_key = json.dumps({
        "limit": limit, "offset": offset, "agency": agency,
        "naics": naics_raw, "contractType": contract_type,
        "state": state, "source": source,
        "sortBy":  sort_by,
        "sortDir": sort_dir,
    }, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # ── Database mode ─────────────────────────────────────────────────────
    conditions = []
    params     = []

    if agency:
        conditions.append("agency ILIKE %s")
        params.append(f"%{agency}%")
    if naics_list:
        placeholders = ",".join(["%s"] * len(naics_list))
        conditions.append(f"naics IN ({placeholders})")
        params.extend(naics_list)
    if contract_type:
        conditions.append("award_status ILIKE %s")
        params.append(f"%{contract_type}%")
    if state:
        conditions.append("place_of_performance ILIKE %s")
        params.append(f"%{state}%")
    if source:
        conditions.append("source_site = %s")
        params.append(source)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        conn   = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute(f"SELECT COUNT(*) FROM postings {where}", params)
        total = cursor.fetchone()["count"]

        # Build ORDER BY clause safely using whitelist
        if sort_by == "posted_date":
            order = f"ORDER BY posted_date {sort_dir.upper()}"
        else:
            order = "ORDER BY date_scraped DESC"  # default — newest scraped first

        cursor.execute(
            f"""
            SELECT
                source_site, external_id, title, agency, organization,
                naics, description, posted_date, deadline, award_date,
                contract_value, award_status, contract_type, acq_strategy,
                place_of_performance, url, date_scraped
            FROM postings
            {where}
            {order}
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
        "total": total, "limit": limit, "offset": offset,
        "opportunities": [dict(row) for row in rows],
        "mode": "database", "cached": False,
    }
    cache_set(cache_key, result)
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  GovContracts proxy  → http://localhost:{port}")
    print(f"  Health              → http://localhost:{port}/health")
    print(f"  Mode: PostgreSQL\n")
    app.run(debug=True, port=port)