#!/usr/bin/env python3
"""
SAM.gov parser — allowed agencies + allowed types + allowed NAICS only.

Live API mode only — calls SAM.gov API directly.
"""

import os
import json
import time
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SAM_BASE_URL = "https://api.sam.gov/opportunities/v2/search"

# ── Allowed agencies ──────────────────────────────────────────────────────────
# Only store contracts posted by one of these agencies (matched against the
# combined agency/organization text, case-insensitive substring match).
# Mirrors the agency filter exposed in the UI.
AGENCY_KEYWORDS = {
    "DOT":   ["DEPARTMENT OF TRANSPORTATION", "TRANSPORTATION, DEPARTMENT OF"],
    "DHS":   ["DEPARTMENT OF HOMELAND SECURITY", "HOMELAND SECURITY"],
    "FHWA":  ["FEDERAL HIGHWAY"],
    "FRA":   ["FEDERAL RAILROAD"],
    "FMCSA": ["FEDERAL MOTOR CARRIER SAFETY"],
    "FAA":   ["FEDERAL AVIATION"],
    "CBP":   ["CUSTOMS AND BORDER PROTECTION", "CUSTOMS & BORDER PROTECTION"],
    "NHTSA": ["NATIONAL HIGHWAY TRAFFIC SAFETY"],
    "TSA":   ["TRANSPORTATION SECURITY"],
    "FEMA":  ["FEDERAL EMERGENCY MANAGEMENT"],
}

def is_allowed_agency(agency: str | None, organization: str | None = None) -> bool:
    text = f"{agency or ''} {organization or ''}".upper()
    if not text.strip():
        return False
    return any(
        keyword in text
        for keywords in AGENCY_KEYWORDS.values()
        for keyword in keywords
    )

# ── Allowed contract types ────────────────────────────────────────────────────
# Only store these types — all others skipped at parse time
SAM_ALLOWED_TYPES = {
    "combined synopsis",   # covers "Combined Synopsis/Solicitation"
    "solicitation",        # covers "Solicitation"
    "sources sought",      # covers "Sources Sought"
    "pre-solicitation",    # covers "Pre-solicitation"
    "presolicitation",     # alternate spelling
}

def is_allowed_type_sam(award_status: str | None) -> bool:
    if not award_status:
        return False
    s = award_status.lower()
    return any(t in s for t in SAM_ALLOWED_TYPES)

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

# ── Type mapping ──────────────────────────────────────────────────────────────
TYPE_LABELS = {
    "o": "Solicitation",
    "a": "Award Notice",
    "p": "Pre-solicitation",
    "r": "Sources Sought",
    "k": "Combined Synopsis",
    "s": "Special Notice",
    "u": "Justification",
}
TYPE_CODES = {v.lower(): k for k, v in TYPE_LABELS.items()}


def normalize_type(raw):
    raw = (raw or "").strip()
    code = TYPE_CODES.get(raw.lower())
    if code:
        return code, TYPE_LABELS[code]
    if raw.lower() in TYPE_LABELS:
        return raw.lower(), TYPE_LABELS[raw.lower()]
    return raw.lower()[:1], raw


# ── Date parsing ──────────────────────────────────────────────────────────────
def parse_date_str(s):
    if not s:
        return None
    s = s.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


# ── Normalize a JSON record → schema ─────────────────────────────────────────
def normalize_json_record(opp):
    _, type_label = normalize_type(opp.get("type") or opp.get("Type") or "")

    place_obj = opp.get("placeOfPerformance") or {}
    pop_state = (
        place_obj.get("state", {}).get("name")
        or place_obj.get("state", {}).get("code")
        or opp.get("state") or None
    )

    dept    = opp.get("department", "")
    subtier = opp.get("subtierAgency", {}).get("name", "") if isinstance(opp.get("subtierAgency"), dict) else ""
    agency  = opp.get("fullParentPathName") or ".".join(p for p in [dept, subtier] if p) or None

    return {
        "source_site":          "SAM.gov",
        "external_id":          opp.get("noticeId") or opp.get("NoticeId"),
        "title":                opp.get("title")    or opp.get("Title"),
        "agency":               agency,
        "organization":         subtier or None,
        "naics":                str(opp.get("naicsCode") or "").strip() or None,
        "description":          opp.get("description") or None,
        "posted_date":          parse_date_str(opp.get("postedDate")),
        "deadline":             parse_date_str(opp.get("responseDeadLine")),
        "award_date":           parse_date_str((opp.get("award") or {}).get("date")),
        "contract_value":       str((opp.get("award") or {}).get("amount") or "") or None,
        "award_status":         type_label,
        "contract_type":        None,
        "acq_strategy":         opp.get("typeOfSetAsideDescription") or None,
        "place_of_performance": pop_state,
        "source_listing_id":    opp.get("solicitationNumber") or None,
        "url":                  (
                                    f"https://sam.gov/opp/{opp.get('noticeId')}/view"
                                    if opp.get("noticeId") else None
                                ),
        "date_scraped":         datetime.now().strftime("%Y-%m-%d"),
        "raw_response":         json.dumps(opp),
    }


# ── Live API fetcher ──────────────────────────────────────────────────────────
def fetch_from_api(posted_from="01/01/2026", posted_to=None, limit=1000):
    api_key = os.environ.get("SAM_API_KEY")
    if not api_key:
        print("  [sam_gov] ERROR: SAM_API_KEY not set in .env")
        return []

    if not posted_to:
        posted_to = datetime.now().strftime("%m/%d/%Y")

    all_records = []
    offset      = 0

    while True:
        params = {
            "api_key":    api_key,
            "postedFrom": posted_from,
            "postedTo":   posted_to,
            "limit":      limit,
            "offset":     offset,
        }

        print(f"  [sam_gov] Fetching offset {offset}...")
        try:
            resp = requests.get(SAM_BASE_URL, params=params, timeout=60)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"  [sam_gov] API error: {e}")
            break

        data  = resp.json()
        items = data.get("opportunitiesData", [])
        total = int(data.get("totalRecords", 0))

        for item in items:
            try:
                record = normalize_json_record(item)
                if (is_allowed_agency(record.get("agency"), record.get("organization"))
                        and is_allowed_type_sam(record.get("award_status"))
                        and is_allowed_naics(record.get("naics"))):
                    all_records.append(record)
            except Exception as e:
                print(f"  [sam_gov] Record error: {e}")
                continue

        print(f"  [sam_gov] Got {len(items)} records (total: {total})")

        offset += limit
        if offset >= total:
            break

        time.sleep(1)

    print(f"  [sam_gov] Done. {len(all_records):,} records fetched.")
    print(f"SAM.gov: {len(all_records):,} fetched")

    return all_records


# ── Main entry point ──────────────────────────────────────────────────────────
def fetch_and_parse():
    return fetch_from_api()