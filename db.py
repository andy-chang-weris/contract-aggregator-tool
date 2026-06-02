"""
db.py — PostgreSQL database layer for GovContracts.

Handles:
  - Table creation and safe column migrations
  - Batch inserts with deduplication
  - In-memory deduplication before storing

Requires these environment variables in .env:
  DB_HOST      e.g. localhost
  DB_PORT      e.g. 5432
  DB_NAME      e.g. govcontracts
  DB_USER      e.g. postgres
  DB_PASSWORD  e.g. yourpassword
"""

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Connection ────────────────────────────────────────────────────────────────
def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST",     "localhost"),
        port=os.getenv("DB_PORT",     "5432"),
        dbname=os.getenv("DB_NAME",   "govcontracts"),
        user=os.getenv("DB_USER",     "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


# ── Setup ─────────────────────────────────────────────────────────────────────
def setup_database():
    """
    Creates the postings table if it doesn't exist.
    Safely adds any missing columns so re-running never breaks existing data.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    # Create table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS postings (
            id                   SERIAL PRIMARY KEY,
            source_site          TEXT,
            external_id          TEXT,
            url                  TEXT UNIQUE,

            -- Filter columns
            agency               TEXT,
            naics                TEXT,
            posted_date          TEXT,
            contract_type        TEXT,
            place_of_performance TEXT,

            -- Extra columns
            title                TEXT,
            organization         TEXT,
            description          TEXT,
            deadline             TEXT,
            award_date           TEXT,
            contract_value       TEXT,
            award_status         TEXT,
            acq_strategy         TEXT,
            source_listing_id    TEXT,

            -- Internal tracking
            date_scraped         TEXT,
            raw_response         TEXT
        )
    """)

    # Indexes for fast filtering
    indexes = {
        "idx_agency":    "CREATE INDEX IF NOT EXISTS idx_agency    ON postings(agency)",
        "idx_naics":     "CREATE INDEX IF NOT EXISTS idx_naics     ON postings(naics)",
        "idx_posted":    "CREATE INDEX IF NOT EXISTS idx_posted    ON postings(posted_date)",
        "idx_ctype":     "CREATE INDEX IF NOT EXISTS idx_ctype     ON postings(contract_type)",
        "idx_place":     "CREATE INDEX IF NOT EXISTS idx_place     ON postings(place_of_performance)",
        "idx_source":    "CREATE INDEX IF NOT EXISTS idx_source    ON postings(source_site)",
        "idx_scraped":   "CREATE INDEX IF NOT EXISTS idx_scraped   ON postings(date_scraped)",
    }
    for name, ddl in indexes.items():
        cursor.execute(ddl)

    # Safe column migration — adds any missing columns without touching existing data
    # Add new columns here as the schema grows
    columns_to_ensure = {
        "agency":               "TEXT",
        "naics":                "TEXT",
        "posted_date":          "TEXT",
        "contract_type":        "TEXT",
        "place_of_performance": "TEXT",
        "title":                "TEXT",
        "organization":         "TEXT",
        "description":          "TEXT",
        "deadline":             "TEXT",
        "award_date":           "TEXT",
        "contract_value":       "TEXT",
        "award_status":         "TEXT",
        "acq_strategy":         "TEXT",
        "source_listing_id":    "TEXT",
        "date_scraped":         "TEXT",
        "raw_response":         "TEXT",
    }

    # Fetch existing columns from PostgreSQL information schema
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'postings'
    """)
    existing = {row[0] for row in cursor.fetchall()}

    for col, col_type in columns_to_ensure.items():
        if col not in existing:
            print(f"  [db] Adding missing column: {col}")
            cursor.execute(f"ALTER TABLE postings ADD COLUMN {col} {col_type}")

    conn.commit()
    cursor.close()
    conn.close()
    print("  [db] Database ready.")


# ── Deduplication ─────────────────────────────────────────────────────────────
def deduplicate(postings):
    """
    Removes duplicates from a combined list of postings before storing.
    Uses (title, agency) as the cross-source duplicate key.
    The database UNIQUE constraint on url handles same-source duplicates.
    """
    seen   = set()
    unique = []

    for posting in postings:
        title  = (posting.get("title")  or "").lower().strip()
        agency = (posting.get("agency") or "").lower().strip()
        key    = (title, agency)

        if key not in seen:
            seen.add(key)
            unique.append(posting)
        else:
            print(f"  [dedup] Removed duplicate: {posting.get('title')} — {posting.get('source_site')}")

    print(f"  [dedup] {len(postings)} postings → {len(unique)} unique")
    return unique


# ── Store ─────────────────────────────────────────────────────────────────────
def store_postings(postings):
    """
    Inserts a list of normalized postings into PostgreSQL.
    Uses ON CONFLICT DO NOTHING to skip duplicates by url.
    All inserts run in a single transaction for performance.
    """
    if not postings:
        print("  [db] No postings to store.")
        return 0

    conn   = get_connection()
    cursor = conn.cursor()

    inserted = 0
    skipped  = 0

    try:
        for posting in postings:
            cursor.execute("""
                INSERT INTO postings (
                    source_site, external_id, url,
                    agency, naics, posted_date, contract_type, place_of_performance,
                    title, organization, description, deadline, award_date,
                    contract_value, award_status, acq_strategy, source_listing_id,
                    date_scraped, raw_response
                ) VALUES (
                    %(source_site)s, %(external_id)s, %(url)s,
                    %(agency)s, %(naics)s, %(posted_date)s, %(contract_type)s, %(place_of_performance)s,
                    %(title)s, %(organization)s, %(description)s, %(deadline)s, %(award_date)s,
                    %(contract_value)s, %(award_status)s, %(acq_strategy)s, %(source_listing_id)s,
                    %(date_scraped)s, %(raw_response)s
                )
                ON CONFLICT (url) DO NOTHING
            """, {
                "source_site":          posting.get("source_site"),
                "external_id":          posting.get("external_id"),
                "url":                  posting.get("url"),
                "agency":               posting.get("agency"),
                "naics":                posting.get("naics"),
                "posted_date":          posting.get("posted_date"),
                "contract_type":        posting.get("contract_type"),
                "place_of_performance": posting.get("place_of_performance"),
                "title":                posting.get("title"),
                "organization":         posting.get("organization"),
                "description":          posting.get("description"),
                "deadline":             posting.get("deadline"),
                "award_date":           posting.get("award_date"),
                "contract_value":       posting.get("contract_value"),
                "award_status":         posting.get("award_status"),
                "acq_strategy":         posting.get("acq_strategy"),
                "source_listing_id":    posting.get("source_listing_id"),
                "date_scraped":         posting.get("date_scraped"),
                "raw_response":         posting.get("raw_response"),
            })

            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"  [db] Batch failed, rolled back: {e}")
        raise

    finally:
        cursor.close()
        conn.close()

    print(f"  [db] Inserted: {inserted} | Skipped (duplicates): {skipped}")
    return inserted