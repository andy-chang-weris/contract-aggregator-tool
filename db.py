"""
db.py — PostgreSQL database layer

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
    if not postings:
        print("  [db] No postings to store.")
        return 0

    conn   = get_connection()
    cursor = conn.cursor()

    inserted = 0
    skipped  = 0
    BATCH_SIZE = 500  # insert 500 rows per round trip instead of 1

    try:
        for i in range(0, len(postings), BATCH_SIZE):
            batch = postings[i: i + BATCH_SIZE]

            # Build values list for this batch
            values = []
            for p in batch:
                values.append((
                    p.get("source_site"),   p.get("external_id"),
                    p.get("url"),           p.get("agency"),
                    p.get("naics"),         p.get("posted_date"),
                    p.get("contract_type"), p.get("place_of_performance"),
                    p.get("title"),         p.get("organization"),
                    p.get("description"),   p.get("deadline"),
                    p.get("award_date"),    p.get("contract_value"),
                    p.get("award_status"),  p.get("acq_strategy"),
                    p.get("source_listing_id"),
                    p.get("date_scraped"),  p.get("raw_response"),
                ))

            # executemany sends the whole batch in one round trip
            cursor.executemany("""
                INSERT INTO postings (
                    source_site, external_id, url,
                    agency, naics, posted_date, contract_type, place_of_performance,
                    title, organization, description, deadline, award_date,
                    contract_value, award_status, acq_strategy, source_listing_id,
                    date_scraped, raw_response
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (url) DO NOTHING
            """, values)

            batch_inserted = cursor.rowcount
            inserted += batch_inserted
            skipped  += len(batch) - batch_inserted

            conn.commit()
            print(f"  [db] Batch {i // BATCH_SIZE + 1}: inserted {batch_inserted} / {len(batch)}")

    except Exception as e:
        conn.rollback()
        print(f"  [db] Batch failed, rolled back: {e}")
        raise

    finally:
        cursor.close()
        conn.close()

    print(f"  [db] Inserted: {inserted} | Skipped (duplicates): {skipped}")
    return inserted

def remove_expired(days_grace=0):
    """
    Deletes postings whose deadline has passed.
    days_grace: how many days after deadline before deleting.
                0 = delete same day it expires
                1 = keep for 1 day after deadline, then delete
    """
    conn   = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            DELETE FROM postings
            WHERE deadline IS NOT NULL
            AND deadline < (CURRENT_DATE - %s * INTERVAL '1 day')::TEXT
        """, (days_grace,))

        deleted = cursor.rowcount
        conn.commit()
        print(f"  [db] Removed {deleted} expired postings.")
        return deleted

    except Exception as e:
        conn.rollback()
        print(f"  [db] Cleanup failed: {e}")
        raise

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    print("  [db] Connecting to database...")
    setup_database()