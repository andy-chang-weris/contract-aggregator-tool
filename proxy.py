"""
proxy.py — Flask API server
Virginia contracts only — state filter removed.
"""

import os
import json
import time
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request
from flask_cors import CORS
from uuid import UUID
from preference_training import train_client_preferences
from relevance_ranking import rank_postings

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

CACHE_TTL_SECONDS = 3600
_cache = {}

VALID_FEEDBACK_ACTIONS = {
    "viewed",
    "clicked",
    "saved",
    "not_interested",
    "highly_relevant",
    "applied",
    "dismissed",
}

ACTION_WEIGHTS = {
    "viewed": 0.1,
    "clicked": 0.5,
    "saved": 1.0,
    "highly_relevant": 2.0,
    "applied": 3.0,
    "not_interested": -1.5,
    "dismissed": -1.0,
}


def parse_uuid(value, field_name):
    try:
        return str(UUID(str(value)))
    except Exception:
        raise ValueError(f"{field_name} must be a valid UUID")


def parse_int(value, field_name):
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field_name} must be an integer")


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


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

    # ── Filters — state removed, all records are Virginia ─────────────────
    agency        = (request.args.get("agency")       or "").strip()
    naics_raw     = request.args.get("naics")         or ""
    naics_list    = [n.strip() for n in naics_raw.split(",") if n.strip()]
    contract_type = (request.args.get("contractType") or "").strip()
    source        = (request.args.get("source")       or "").strip()

    sort_by  = (request.args.get("sortBy")  or "").strip()
    sort_dir = (request.args.get("sortDir") or "desc").strip().lower()

    ALLOWED_SORT_FIELDS = {"posted_date"}
    if sort_by not in ALLOWED_SORT_FIELDS:
        sort_by = None
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    # ── Cache key ─────────────────────────────────────────────────────────
    cache_key = json.dumps({
        "limit": limit, "offset": offset,
        "agency": agency, "naics": naics_raw,
        "contractType": contract_type, "source": source,
        "sortBy": sort_by, "sortDir": sort_dir,
    }, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # ── Build WHERE clause ────────────────────────────────────────────────
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
    if source:
        conditions.append("source_site = %s")
        params.append(source)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        conn   = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute(f"SELECT COUNT(*) FROM postings {where}", params)
        total = cursor.fetchone()["count"]

        order = f"ORDER BY posted_date {sort_dir.upper()}" if sort_by == "posted_date" \
                else "ORDER BY date_scraped DESC"

        cursor.execute(
            f"""
            SELECT
                id,
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

@app.route("/api/feedback", methods=["POST"])
def add_feedback():
    data = request.get_json(silent=True) or {}

    try:
        client_id = parse_uuid(data.get("client_id"), "client_id")
        posting_id = parse_int(data.get("posting_id"), "posting_id")

        action = (data.get("action") or "").strip()
        if action not in VALID_FEEDBACK_ACTIONS:
            return jsonify({
                "error": f"Invalid action. Must be one of: {sorted(VALID_FEEDBACK_ACTIONS)}"
            }), 400

        rating = data.get("rating")
        if rating is not None:
            rating = parse_int(rating, "rating")
            if rating < 1 or rating > 5:
                return jsonify({"error": "rating must be between 1 and 5"}), 400

        feedback_source = (data.get("feedback_source") or "web_app").strip()
        notes = data.get("notes")
        metadata = data.get("metadata") or {}

        if not isinstance(metadata, dict):
            return jsonify({"error": "metadata must be an object"}), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Make sure the client exists.
        cursor.execute(
            "SELECT id FROM clients WHERE id = %s",
            (client_id,)
        )
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"error": "client_id does not exist"}), 404

        # Make sure the posting exists and capture a snapshot for ML training.
        cursor.execute(
            """
            SELECT
                id, source_site, external_id, title, agency, organization,
                naics, description, posted_date, deadline, award_date,
                contract_value, award_status, contract_type, acq_strategy,
                place_of_performance, url, date_scraped
            FROM postings
            WHERE id = %s
            """,
            (posting_id,)
        )
        posting = cursor.fetchone()
        if not posting:
            cursor.close()
            conn.close()
            return jsonify({"error": "posting_id does not exist"}), 404

        posting_snapshot = {
            key: value for key, value in dict(posting).items()
            if value is not None
        }

        cursor.execute("""
            INSERT INTO client_feedback (
                client_id,
                posting_id,
                posting_snapshot,
                action,
                rating,
                notes,
                feedback_source,
                metadata
            )
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, client_id, posting_id, action, rating, created_at
        """, (
            client_id,
            posting_id,
            json.dumps(posting_snapshot),
            action,
            rating,
            notes,
            feedback_source,
            json.dumps(metadata)
        ))

        row = cursor.fetchone()
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({
            "status": "ok",
            "feedback": dict(row),
            "action_weight": ACTION_WEIGHTS.get(action, 0)
        })

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    
@app.route("/api/clients", methods=["POST"])
def create_client():
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    organization = (data.get("organization") or "").strip() or None
    contact_email = (data.get("contact_email") or "").strip() or None

    if not name:
        return jsonify({"error": "name is required"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            INSERT INTO clients (name, organization, contact_email)
            VALUES (%s, %s, %s)
            RETURNING id, name, organization, contact_email, created_at
        """, (name, organization, contact_email))

        client = cursor.fetchone()

        cursor.execute("""
            INSERT INTO client_preferences (client_id)
            VALUES (%s)
            ON CONFLICT (client_id) DO NOTHING
        """, (client["id"],))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "ok",
            "client": dict(client)
        })

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route("/api/clients/<client_id>/preferences", methods=["GET", "PUT"])
def client_preferences(client_id):
    try:
        client_id = parse_uuid(client_id, "client_id")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if request.method == "GET":
            cursor.execute("""
                SELECT *
                FROM client_preferences
                WHERE client_id = %s
            """, (client_id,))

            prefs = cursor.fetchone()
            cursor.close()
            conn.close()

            if not prefs:
                return jsonify({"error": "preferences not found"}), 404

            return jsonify({
                "client_id": client_id,
                "preferences": dict(prefs)
            })

        data = request.get_json(silent=True) or {}

        allowed_array_fields = {
            "preferred_naics",
            "excluded_naics",
            "preferred_agencies",
            "excluded_agencies",
            "preferred_sources",
            "excluded_sources",
            "preferred_keywords",
            "disliked_keywords",
            "preferred_contract_types",
            "preferred_set_asides",
        }

        updates = []
        params = []

        for field in allowed_array_fields:
            if field in data:
                value = data[field]
                if not isinstance(value, list):
                    return jsonify({"error": f"{field} must be a list"}), 400
                updates.append(f"{field} = %s")
                params.append(value)

        scalar_fields = {
            "min_contract_value",
            "max_contract_value",
            "max_days_until_deadline",
            "profile_summary",
        }

        for field in scalar_fields:
            if field in data:
                updates.append(f"{field} = %s")
                params.append(data[field])

        if not updates:
            cursor.close()
            conn.close()
            return jsonify({"error": "No valid preference fields provided"}), 400

        updates.append("updated_at = now()")
        params.append(client_id)

        cursor.execute(f"""
            UPDATE client_preferences
            SET {", ".join(updates)}
            WHERE client_id = %s
            RETURNING *
        """, params)

        prefs = cursor.fetchone()
        conn.commit()

        cursor.close()
        conn.close()

        if not prefs:
            return jsonify({"error": "preferences not found"}), 404

        return jsonify({
            "status": "ok",
            "preferences": dict(prefs)
        })

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@app.route("/api/clients/<client_id>/train-preferences", methods=["POST"])
def train_preferences_endpoint(client_id):
    try:
        client_id = parse_uuid(client_id, "client_id")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    data = request.get_json(silent=True) or {}

    try:
        min_feedback_events = parse_int(
            data.get("min_feedback_events", 1),
            "min_feedback_events"
        )
        positive_threshold = float(data.get("positive_threshold", 1.0))
        negative_threshold = float(data.get("negative_threshold", -2.0))
        max_profile_items = parse_int(
            data.get("max_profile_items", 25),
            "max_profile_items"
        )
        include_clicks = parse_bool(data.get("include_clicks"), default=True)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if min_feedback_events < 1:
        return jsonify({"error": "min_feedback_events must be at least 1"}), 400
    if max_profile_items < 1 or max_profile_items > 100:
        return jsonify({"error": "max_profile_items must be between 1 and 100"}), 400
    if positive_threshold <= 0:
        return jsonify({"error": "positive_threshold must be greater than 0"}), 400
    if negative_threshold >= 0:
        return jsonify({"error": "negative_threshold must be less than 0"}), 400

    try:
        result = train_client_preferences(
            get_db,
            client_id,
            min_feedback_events=min_feedback_events,
            include_clicks=include_clicks,
            positive_threshold=positive_threshold,
            negative_threshold=negative_threshold,
            max_profile_items=max_profile_items,
        )
        status_code = 200 if result.get("status") != "error" else 404
        return jsonify(result), status_code

    except Exception as e:
        return jsonify({"error": f"Training error: {str(e)}"}), 500

@app.route("/api/clients/<client_id>/ranked-opportunities")
def ranked_opportunities(client_id):
    try:
        client_id = parse_uuid(client_id, "client_id")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    limit  = max(1, min(int(request.args.get("limit", 20)), 1000))
    offset = max(0, int(request.args.get("offset", 0)))
    candidate_limit = max(1, min(int(request.args.get("candidateLimit", 1000)), 5000))
    exclude_negative = parse_bool(request.args.get("excludeNegativeFeedback"), default=False)

    agency        = (request.args.get("agency")       or "").strip()
    naics_raw     = request.args.get("naics")         or ""
    naics_list    = [n.strip() for n in naics_raw.split(",") if n.strip()]
    contract_type = (request.args.get("contractType") or "").strip()
    source        = (request.args.get("source")       or "").strip()

    conditions, params = [], []
    if agency:
        conditions.append("agency ILIKE %s"); params.append(f"%{agency}%")
    if naics_list:
        placeholders = ",".join(["%s"] * len(naics_list))
        conditions.append(f"naics IN ({placeholders})"); params.extend(naics_list)
    if contract_type:
        conditions.append("award_status ILIKE %s"); params.append(f"%{contract_type}%")
    if source:
        conditions.append("source_site = %s"); params.append(source)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        conn   = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Verify client exists
        cursor.execute("SELECT id FROM clients WHERE id = %s", (client_id,))
        if not cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({"error": "client_id does not exist"}), 404

        # Load preferences (may be empty defaults)
        cursor.execute("SELECT * FROM client_preferences WHERE client_id = %s", (client_id,))
        prefs = dict(cursor.fetchone() or {})

        # Optionally exclude postings the client already rejected
        excluded_ids = set()
        if exclude_negative:
            cursor.execute("""
                SELECT DISTINCT posting_id
                FROM client_feedback
                WHERE client_id = %s
                  AND posting_id IS NOT NULL
                  AND action IN ('not_interested', 'dismissed')
            """, (client_id,))
            excluded_ids = {r["posting_id"] for r in cursor.fetchall()}

        cursor.execute(f"""
            SELECT
                id, source_site, external_id, title, agency, organization,
                naics, description, posted_date, deadline, award_date,
                contract_value, award_status, contract_type, acq_strategy,
                place_of_performance, url, date_scraped
            FROM postings
            {where}
            ORDER BY date_scraped DESC
            LIMIT %s
        """, params + [candidate_limit])
        candidates = [dict(r) for r in cursor.fetchall()]
        cursor.close(); conn.close()

    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

    if excluded_ids:
        candidates = [c for c in candidates if c.get("id") not in excluded_ids]

    ranked = rank_postings(candidates, prefs)
    total  = len(ranked)
    page   = ranked[offset: offset + limit]

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "opportunities": page,
        "mode": "database",
        "ranked": True,
        "cached": False,
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  GovContracts proxy  → http://localhost:{port}")
    print(f"  Health              → http://localhost:{port}/health")
    print(f"  Mode: PostgreSQL (Virginia contracts only)\n")
    app.run(debug=True, port=port)
