#!/usr/bin/env python3
"""run_sam_only.py — Re-populate SAM.gov contracts only."""

from datetime import datetime
from db import setup_database, store_postings, deduplicate
from sources.sam_gov import fetch_and_parse as fetch_sam

def run():
    setup_database()

    print("\nFetching SAM.gov...")
    postings = fetch_sam()
    print(f"  SAM.gov: {len(postings):,} fetched")

    today = datetime.now().strftime("%Y-%m-%d")
    before_filter = len(postings)
    postings = [p for p in postings if not p.get("deadline") or p.get("deadline") >= today]
    filtered_out = before_filter - len(postings)
    if filtered_out:
        print(f"Filtered out {filtered_out:,} already-expired contracts")

    unique = deduplicate(postings)
    inserted = store_postings(unique)
    print(f"\nDone. {inserted:,} SAM.gov postings inserted.")

if __name__ == "__main__":
    run()