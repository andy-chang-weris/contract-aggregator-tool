from datetime import datetime
from db import setup_database, store_postings, deduplicate, remove_expired
from sources.sam_gov import fetch_and_parse as fetch_sam
from sources.acq_gateway import fetch_and_parse as fetch_acq

def run():
    setup_database()

    all_postings = []

    sources = [
        ("SAM.gov",             fetch_sam),
        ("Acquisition Gateway", fetch_acq),
    ]

    for name, fetch_fn in sources:
        print(f"\nFetching {name}...")
        try:
            postings = fetch_fn()
            print(f"  {name}: {len(postings)} fetched")
            all_postings.extend(postings)
        except Exception as e:
            print(f"  {name} failed: {e}")
            continue

    today = datetime.now().strftime("%Y-%m-%d")
    before_filter = len(all_postings)
    all_postings = [
        p for p in all_postings
        if not p.get("deadline") or p.get("deadline") >= today
    ]
    print(f"\nFiltered out {before_filter - len(all_postings)} expired contracts")

    unique   = deduplicate(all_postings)
    inserted = store_postings(unique)
    print(f"\nDone. {inserted} new postings inserted.")

    remove_expired(days_grace=1) # keeps contracts for 1 day after deadline

if __name__ == "__main__":
    run()