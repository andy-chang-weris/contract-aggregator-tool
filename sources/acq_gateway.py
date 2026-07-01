#!/usr/bin/env python3
"""
Acquisition Gateway Forecast parser — allowed types + allowed NAICS only.
"""

import requests
import json
import re
from datetime import datetime, timezone
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

# ── Allowed contract types ────────────────────────────────────────────────────
AG_ALLOWED_TYPES = {
    "acquisition planning",
    "solicitation issued",
    "drafting solicitation",
}

def is_allowed_type_ag(award_status: str | None) -> bool:
    if not award_status:
        return False
    s = award_status.lower().strip()
    return any(t in s for t in AG_ALLOWED_TYPES)

# ── Allowed NAICS codes ───────────────────────────────────────────────────────
# Only store contracts whose NAICS falls under one of these codes.
# Mirrors the filterable NAICS set exposed in the UI.
ALLOWED_NAICS = {
    "541611",  # Administrative management consulting
    "541618",  # Other management consulting
    "541690",  # Other scientific & technical consulting
    "541990",  # Other professional services
    "541330",  # Engineering services
    "541511",  # Custom computer programming
    "541512",  # Computer systems design
    "541513",  # Computer facilities management
    "541519",  # Other computer-related services
}

def is_allowed_naics(naics: str | None) -> bool:
    if not naics:
        return False
    return naics.strip() in ALLOWED_NAICS

def parse_unix_date(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def clean_html(text):
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_naics_code(raw):
    if not raw:
        return None
    text = clean_html(raw)
    match = re.search(r'\b\d{6}\b', text)
    return match.group(0) if match else text.strip() or None


def parse_listing(nid, entry):
    render = entry.get("render", {})
    values = entry.get("values", {})

    place_raw = render.get("field_place_of_performance", "")
    place     = clean_html(place_raw) if isinstance(place_raw, str) else None

    period_of_performance = values.get("field_period_of_performance")
    if period_of_performance and period_of_performance[:4] == "2099":
        period_of_performance = None

    return {
        "source_site":          "Acquisition Gateway Forecast",
        "external_id":          str(nid),
        "title":                values.get("title"),
        "agency":               clean_html(render.get("field_result_id")           or ""),
        "organization":         clean_html(render.get("field_organization")         or ""),
        "naics":                extract_naics_code(render.get("field_naics_code")),
        "description":          clean_html(render.get("body",                        "")),
        "award_date":           values.get("field_estimated_award_fy"),
        "deadline":             period_of_performance,
        "contract_value":       clean_html(render.get("field_estimated_contract_v_max") or ""),
        "award_status":         clean_html(render.get("field_award_status")         or ""),
        "contract_type":        clean_html(render.get("field_contract_type")        or ""),
        "acq_strategy":         clean_html(render.get("field_acquisition_strategy") or ""),
        "place_of_performance": place,
        "source_listing_id":    values.get("field_source_listing_id"),
        "url":                  f"https://acquisitiongateway.gov/forecast/resources/{nid}",
        "posted_date":          parse_unix_date(values.get("created")),
        "date_scraped":         datetime.now().strftime("%Y-%m-%d"),
        "raw_response":         json.dumps(entry),
    }


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
    """Fetch all pages, return only allowed-type + allowed-NAICS postings."""
    all_postings = []
    page = 1

    data, listings = fetch_page(page)
    if data is None:
        return []

    listing_meta = data.get("listing", {})
    total        = int(listing_meta.get("total") or 0)
    page_size    = 25
    total_pages  = -(-total // page_size)
    print(f"Total listings: {total} across ~{total_pages} pages")

    while True:
        if listings is None or len(listings) == 0:
            print(f"\nNo listings on page {page} — stopping.")
            break

        print(f"Page {page}/{total_pages} — {len(listings)} listings", end="\r")

        for nid, entry in listings.items():
            try:
                posting = parse_listing(nid, entry)
                all_postings.append(posting)
            except Exception as e:
                print(f"  Parse error on nid {nid}: {e}")
                continue

        if page >= total_pages:
            print(f"\nStopping at page {page}.")
            break

        page += 1
        time.sleep(0.2)
        _, listings = fetch_page(page)

    # Contract type filter
    before = len(all_postings)
    all_postings = [p for p in all_postings if is_allowed_type_ag(p.get("award_status"))]
    print(f"  [acq_gateway] Type filter: {before:,} → {len(all_postings):,} records.")

    # NAICS filter
    before = len(all_postings)
    all_postings = [p for p in all_postings if is_allowed_naics(p.get("naics"))]
    print(f"  [acq_gateway] NAICS filter: {before:,} → {len(all_postings):,} records.")

    return all_postings