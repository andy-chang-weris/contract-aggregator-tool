#!/usr/bin/env python3
"""
run_all.py — Daily pipeline entry point.

Fetches contracts from all sources, deduplicates, filters expired,
and stores new records in the PostgreSQL database.
"""

from datetime import datetime
from db import setup_database, store_postings, deduplicate, remove_expired
from sources.sam_gov     import fetch_and_parse as fetch_sam
from sources.acq_gateway import fetch_and_parse as fetch_acq
from sources.eva         import fetch_and_parse as fetch_eva


def run():
    setup_database()
    all_postings = []

    sources = [
        ("SAM.gov",             fetch_sam),
        ("Acquisition Gateway", fetch_acq),
        ("Virginia eVA",        fetch_eva),
    ]

    # Step 1: Fetch and parse all sources
    for name, fetch_fn in sources:
        print(f"\nFetching {name}...")
        try:
            postings = fetch_fn()
            print(f"  {name}: {len(postings):,} fetched")
            all_postings.extend(postings)
        except Exception as e:
            print(f"  {name} failed: {e}")
            continue

    # Step 2: Filter expired out of memory before touching the database
    today = datetime.now().strftime("%Y-%m-%d")
    before_filter = len(all_postings)
    all_postings = [
        p for p in all_postings
        if not p.get("deadline") or p.get("deadline") >= today
    ]
    filtered_out = before_filter - len(all_postings)
    if filtered_out:
        print(f"\nFiltered out {filtered_out:,} already-expired contracts")

    # Step 3: Deduplicate and store
    unique   = deduplicate(all_postings)
    inserted = store_postings(unique)
    print(f"\nDone. {inserted:,} new postings inserted.")

    # Step 4: Remove contracts that expired since last run
    print("\nCleaning up expired contracts...")
    remove_expired(days_grace=1)


if __name__ == "__main__":
    run()