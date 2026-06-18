# test_acq.py
from sources.acq_gateway import fetch_and_parse

postings = fetch_and_parse()
print(f"\nTotal returned: {len(postings)}")
if postings:
    print("Sample:", postings[0])