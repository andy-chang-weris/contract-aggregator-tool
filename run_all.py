from db import setup_database, store_postings, deduplicate
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

    unique   = deduplicate(all_postings)
    inserted = store_postings(unique)
    print(f"\nDone. {inserted} new postings inserted.")

if __name__ == "__main__":
    run()