#!/usr/bin/env python3
"""
db.py — PostgreSQL database layer

Handles:
  - Table creation and safe column migrations
  - Batch inserts with deduplication
  - In-memory deduplication before storing
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
    setup_ml_tables()
    print("  [db] Database ready.")

def setup_ml_tables():
    """
    Creates MVP client preference and feedback tables.
    Safe to run repeatedly.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            organization TEXT,
            contact_email TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_preferences (
            client_id uuid PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,

            preferred_naics TEXT[] NOT NULL DEFAULT '{}',
            excluded_naics TEXT[] NOT NULL DEFAULT '{}',

            preferred_agencies TEXT[] NOT NULL DEFAULT '{}',
            excluded_agencies TEXT[] NOT NULL DEFAULT '{}',

            preferred_sources TEXT[] NOT NULL DEFAULT '{}',
            excluded_sources TEXT[] NOT NULL DEFAULT '{}',

            preferred_keywords TEXT[] NOT NULL DEFAULT '{}',
            disliked_keywords TEXT[] NOT NULL DEFAULT '{}',

            preferred_contract_types TEXT[] NOT NULL DEFAULT '{}',
            preferred_set_asides TEXT[] NOT NULL DEFAULT '{}',

            min_contract_value NUMERIC,
            max_contract_value NUMERIC,
            max_days_until_deadline INTEGER,

            naics_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
            agency_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
            keyword_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_weights JSONB NOT NULL DEFAULT '{}'::jsonb,

            profile_summary TEXT,
            last_trained_at TIMESTAMPTZ,

            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_feedback (
            id BIGSERIAL PRIMARY KEY,
            client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            posting_id INTEGER REFERENCES postings(id) ON DELETE SET NULL,
            posting_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,

            action TEXT NOT NULL CHECK (
                action IN (
                    'viewed',
                    'clicked',
                    'saved',
                    'not_interested',
                    'highly_relevant',
                    'applied',
                    'dismissed'
                )
            ),

            rating SMALLINT CHECK (rating BETWEEN 1 AND 5),
            notes TEXT,
            feedback_source TEXT NOT NULL DEFAULT 'web_app',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Safe migrations for installs that created client_feedback earlier.
    cursor.execute("""
        ALTER TABLE client_feedback
        ADD COLUMN IF NOT EXISTS posting_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
    """)

    cursor.execute("""
        ALTER TABLE client_feedback
        ALTER COLUMN posting_id DROP NOT NULL
    """)

    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'client_feedback'::regclass
                AND conname = 'client_feedback_posting_id_fkey'
            ) THEN
                ALTER TABLE client_feedback
                DROP CONSTRAINT client_feedback_posting_id_fkey;
            END IF;

            ALTER TABLE client_feedback
            ADD CONSTRAINT client_feedback_posting_id_fkey
            FOREIGN KEY (posting_id)
            REFERENCES postings(id)
            ON DELETE SET NULL;
        END $$;
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_client_feedback_client_time
        ON client_feedback(client_id, created_at DESC)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_client_feedback_posting
        ON client_feedback(posting_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_client_feedback_action
        ON client_feedback(action)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_client_preferences_naics
        ON client_preferences USING gin(preferred_naics)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_client_preferences_keywords
        ON client_preferences USING gin(preferred_keywords)
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("  [db] ML preference/feedback tables ready.")


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
    updated  = 0
    BATCH_SIZE = 500

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

            # Upsert: insert new rows, update mutable fields on conflict.
            # Stable identifiers (agency, naics, posted_date, title, etc.)
            # are intentionally excluded from the UPDATE to avoid overwriting
            # good data with a bad scrape.
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
                ON CONFLICT (url) DO UPDATE SET
                    naics          = EXCLUDED.naics,
                    award_status   = EXCLUDED.award_status,
                    acq_strategy   = EXCLUDED.acq_strategy,
                    deadline       = EXCLUDED.deadline,
                    award_date     = EXCLUDED.award_date,
                    contract_value = EXCLUDED.contract_value,
                    description    = EXCLUDED.description,
                    date_scraped   = EXCLUDED.date_scraped,
                    raw_response   = EXCLUDED.raw_response
            """, values)

            # rowcount with executemany reflects total rows affected (inserted
            # + updated). We track it as a combined "processed" count.
            batch_processed = cursor.rowcount
            inserted += batch_processed

            conn.commit()
            print(f"  [db] Batch {i // BATCH_SIZE + 1}: processed {batch_processed} / {len(batch)}")

    except Exception as e:
        conn.rollback()
        print(f"  [db] Batch failed, rolled back: {e}")
        raise

    finally:
        cursor.close()
        conn.close()

    print(f"  [db] Done. {inserted} rows inserted or updated.")
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