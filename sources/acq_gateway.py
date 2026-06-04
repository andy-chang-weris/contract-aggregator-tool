import requests
import json
import re
from datetime import datetime
import time

BASE_URL = "https://ag-dashboard.acquisitiongateway.gov/api/v3.0/resources/forecast"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Sec-Ch-Ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Referer": "https://acquisitiongateway.gov/",
}

def clean_html(text):
    """Strip HTML tags from a string."""
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", text).strip()

def parse_listing(nid, entry):
    """
    Maps a single Acquisition Gateway listing to the normalized schema.
    Uses 'render' for human-readable values, 'values' for clean IDs.
    """
    render = entry.get("render", {})
    values = entry.get("values", {})

    # Period of performance is a date range — take the start date from values
    pop_start = values.get("field_period_of_performance")

    # Place of performance comes as HTML in render — strip the tags
    place_raw = render.get("field_place_of_performance", "")
    place = clean_html(place_raw) if isinstance(place_raw, str) else None

    return {
        "source_site":   "Acquisition Gateway Forecast",
        "external_id":   str(nid),
        "title":         values.get("title"),
        "agency":        render.get("field_result_id"),          # e.g. "General Services Administration"
        "organization":  render.get("field_organization"),       # e.g. "FAS-Federal Acquisition Service"
        "naics":         extract_naics_code(render.get("field_naics_code")),         # already a readable label in render — but HTML, so:
        "description":   clean_html(render.get("body", "")),     # strip <p> tags
        "award_date":    values.get("field_estimated_award_fy"), # fiscal year e.g. "2026"
        "deadline":      pop_start,                              # period of performance start date YYYY-MM-DD
        "contract_value": render.get("field_estimated_contract_v_max"), # e.g. "$1M - $1.9M"
        "award_status":  render.get("field_award_status"),       # e.g. "Solicitation Issued"
        "contract_type": render.get("field_contract_type"),      # e.g. "Firm Fixed Price"
        "acq_strategy":  render.get("field_acquisition_strategy"), # e.g. "Small Business"
        "place_of_performance": place,                           # e.g. "DC United States"
        "source_listing_id": values.get("field_source_listing_id"), # e.g. "GLhvHqY4N4ylPky"
        "url":           f"https://acquisitiongateway.gov/forecast/resources/{nid}",
        "date_scraped":  datetime.now().strftime("%Y-%m-%d"),
        "raw_response":  json.dumps(entry),
    }

def extract_naics_code(raw):
    """Pull just the numeric NAICS code from an HTML field."""
    if not raw:
        return None
    # Strip HTML tags first
    text = clean_html(raw)
    # NAICS codes are 6 digits — grab the first 6-digit number found
    import re
    match = re.search(r'\b\d{6}\b', text)
    return match.group(0) if match else text.strip() or None

def fetch_page(page_number):
    response = requests.get(
        BASE_URL,
        params={"page": page_number},
        headers=HEADERS
    )
    if response.status_code != 200:
        print(f"Non-200 on page {page_number}: {response.status_code}")
        return None, None
    data = response.json()
    return data, data.get("listing", {}).get("data", {})


def fetch_and_parse():
    """Fetch all pages and return normalized postings."""
    all_postings = []
    page = 1

    # Get page 1 to find total count
    data, listings = fetch_page(page)
    if data is None:
        return []

    total = int(data.get("total", 0))
    page_size = 25  # confirmed from response
    total_pages = -(-total // page_size)  # ceiling division
    print(f"Total listings: {total} across ~{total_pages} pages")

    while True:
        if listings is None or len(listings) == 0:
            print(f"No listings on page {page} — stopping.")
            break

        print(f"Page {page}/{total_pages} — {len(listings)} listings")

        for nid, entry in listings.items():
            try:
                posting = parse_listing(nid, entry)
                all_postings.append(posting)
            except Exception as e:
                print(f"  Parse error on nid {nid}: {e}")
                continue

        if page >= total_pages:
            print("Reached last page.")
            break

        page += 1
        time.sleep(1)
        _, listings = fetch_page(page)

    return all_postings