"""
proxy.py — Flask API server for the GovContracts dashboard.

MODE DETECTION (automatic on startup):
  1. CSV MODE   — if sam_opportunities.csv exists in project folder
                  No database needed. Filters applied in Python.
  2. DB MODE    — if PostgreSQL credentials are set in .env
                  Requires db.py and run_all.py to have been run first.

Endpoints:
  GET /health                — status check, shows current mode
  GET /cache/clear           — clear in-memory cache
  GET /api/opportunities     — paginated contract listings with filters
"""

import os
import csv
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

LOCAL_CSV_FILE    = "sam_opportunities.csv"
CACHE_TTL_SECONDS = 3600

_cache        = {}
_csv_records  = []
_csv_mode     = False


# ── CSV loader (runs once on startup) ────────────────────────────────────────
def load_csv():
    global _csv_records, _csv_mode

    if not os.path.exists(LOCAL_CSV_FILE):
        return

    print(f"  [mode] Found {LOCAL_CSV_FILE} — loading CSV for local mode...")

    TYPE_LABELS = {
        "o": "Solicitation",       "a": "Award Notice",
        "p": "Pre-solicitation",   "r": "Sources Sought",
        "k": "Combined Synopsis",  "s": "Special Notice",
        "u": "Justification",
    }
    TYPE_CODES = {v.lower(): k for k, v in TYPE_LABELS.items()}

    def normalize_type(raw):
        raw = (raw or "").strip()
        code = TYPE_CODES.get(raw.lower())
        if code:
            return code, TYPE_LABELS[code]
        if raw.lower() in TYPE_LABELS:
            return raw.lower(), TYPE_LABELS[raw.lower()]
        return raw.lower()[:1], raw

    def parse_date(s):
        if not s:
            return None
        s = s.strip()
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        for fmt in ("%m/%d/%Y", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(s[:len(fmt)+6], fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return s[:10] if len(s) >= 10 else None

    records = []
    try:
        with open(LOCAL_CSV_FILE, "r", encoding="latin-1") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    _, type_label = normalize_type(row.get("Type", ""))
                    dept    = row.get("Department/Ind.Agency", "").strip()
                    subtier = row.get("Sub-Tier", "").strip()
                    office  = row.get("Office", "").strip()
                    agency  = ".".join(p for p in [dept, subtier, office] if p) or None
                    pop     = row.get("PopState", "").strip() or row.get("State", "").strip() or None

                    records.append({
                        "source_site":          "SAM.gov",
                        "external_id":          row.get("NoticeId", "").strip(),
                        "title":                row.get("Title", "").strip(),
                        "agency":               agency,
                        "organization":         subtier or None,
                        "naics":                row.get("NaicsCode", "").strip() or None,
                        "description":          row.get("Description", "").strip() or None,
                        "posted_date":          parse_date(row.get("PostedDate")),
                        "deadline":             parse_date(row.get("ResponseDeadLine")),
                        "award_date":           parse_date(row.get("AwardDate")),
                        "contract_value":       row.get("Award$", "").strip() or None,
                        "award_status":         type_label,
                        "contract_type":        None,
                        "acq_strategy":         row.get("SetASide", "").strip() or None,
                        "place_of_performance": pop,
                        "url":                  row.get("Link", "").strip() or None,
                        "date_scraped":         datetime.now().strftime("%Y-%m-%d"),
                    })
                except Exception:
                    continue

        _csv_records = records
        _csv_mode    = True
        print(f"  [mode] CSV MODE active — {len(_csv_records):,} records loaded.")
        print(f"         Filters will be applied in Python. No database needed.")

    except Exception as e:
        print(f"  [mode] Could not load CSV: {e}")


# ── CSV filter logic ──────────────────────────────────────────────────────────
def apply_csv_filters(records, agency, naics, contract_type, state, source):
    results = []
    for r in records:
        if agency and agency.lower() not in (r.get("agency") or "").lower():
            continue
        if naics and str(r.get("naics") or "") != naics:
            continue
        if contract_type and contract_type.lower() not in (r.get("award_status") or "").lower():
            continue
        if state and state.lower() not in (r.get("place_of_performance") or "").lower():
            continue
        if source and (r.get("source_site") or "") != source:
            continue
        results.append(r)
    return results


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

    if _csv_mode:
        return jsonify({
            "status":               "ok",
            "mode":                 "csv",
            "csv_file":             LOCAL_CSV_FILE,
            "csv_records_loaded":   len(_csv_records),
            "cache_entries_active": active_cache,
            "cache_ttl_seconds":    CACHE_TTL_SECONDS,
        })

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
    naics         = (request.args.get("naics")        or "").strip()
    contract_type = (request.args.get("contractType") or "").strip()
    state         = (request.args.get("state")        or "").strip()
    source        = (request.args.get("source")       or "").strip()

    # ── Cache key ─────────────────────────────────────────────────────────
    cache_key = json.dumps({
        "limit": limit, "offset": offset, "agency": agency,
        "naics": naics, "contractType": contract_type,
        "state": state, "source": source,
    }, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # ── CSV mode ──────────────────────────────────────────────────────────
    if _csv_mode:
        filtered = apply_csv_filters(
            _csv_records, agency, naics, contract_type, state, source
        )
        total = len(filtered)
        page  = filtered[offset: offset + limit]
        print(f"  [csv] {total:,} matched → returning {len(page)} (offset {offset})")

        result = {
            "total": total, "limit": limit,
            "offset": offset, "opportunities": page,
            "mode": "csv", "cached": False,
        }
        cache_set(cache_key, result)
        return jsonify(result)

    # ── Database mode ─────────────────────────────────────────────────────
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
    if source:
        conditions.append("source_site = %s")
        params.append(source)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        conn   = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute(f"SELECT COUNT(*) FROM postings {where}", params)
        total = cursor.fetchone()["count"]

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
        "total": total, "limit": limit, "offset": offset,
        "opportunities": [dict(row) for row in rows],
        "mode": "database", "cached": False,
    }
    cache_set(cache_key, result)
    return jsonify(result)


# ── Startup ───────────────────────────────────────────────────────────────────
load_csv()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  GovContracts proxy  → http://localhost:{port}")
    print(f"  Health              → http://localhost:{port}/health")
    print(f"  Mode: {'CSV (' + LOCAL_CSV_FILE + ')' if _csv_mode else 'PostgreSQL'}\n")
    app.run(debug=True, port=port)