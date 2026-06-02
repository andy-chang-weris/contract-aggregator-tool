"""
SAM.gov parser — fetch and normalize contract opportunities.

LOCAL MODE: reads from sam_opportunities.csv (downloaded from sam.gov/data-services)
            No API key needed. No rate limits. Filters applied in Python.

LIVE MODE:  calls SAM.gov API directly. Requires SAM_API_KEY in .env.
"""

import os
import csv
import json
import time
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SAM_BASE_URL   = "https://api.sam.gov/opportunities/v2/search"
LOCAL_CSV_FILE = "sam_opportunities.csv"
LOCAL_JSON_FILE = "sam_opportunities.json"

# ── Type mapping ──────────────────────────────────────────────────────────────
# SAM.gov CSV uses full strings ("Solicitation"), API uses codes ("o")
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
# SAM.gov CSV dates look like: "2026-05-26 23:17:21.597-04"
def parse_date_str(s):
    """
    Parse a messy date string into a clean YYYY-MM-DD string.
    Returns None if unparseable.
    """
    if not s:
        return None
    s = s.strip()
    # Fastest path: already in YYYY-MM-DD format
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # Try common formats
    for fmt in ("%m/%d/%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


# ── Normalize a CSV row → agreed schema ──────────────────────────────────────
# Actual SAM.gov CSV columns (verified):
#   NoticeId, Title, Sol#, Department/Ind.Agency, CGAC, Sub-Tier,
#   FPDS Code, Office, AAC Code, PostedDate, Type, BaseType,
#   ArchiveType, ArchiveDate, SetASideCode, SetASide, ResponseDeadLine,
#   NaicsCode, ClassificationCode, PopStreetAddress, PopCity, PopState,
#   PopZip, PopCountry, Active, AwardNumber, AwardDate, Award$, Awardee,
#   PrimaryContact, SecondaryContact, OrganizationType,
#   State, City, ZipCode, CountryCode, AdditionalInfoLink, Link, Description
def normalize_csv_record(row):
    _, type_label = normalize_type(row.get("Type", ""))

    dept    = row.get("Department/Ind.Agency", "").strip()
    subtier = row.get("Sub-Tier", "").strip()
    office  = row.get("Office", "").strip()
    agency  = ".".join(p for p in [dept, subtier, office] if p) or None

    pop_state = row.get("PopState", "").strip()
    off_state = row.get("State", "").strip()
    place     = pop_state or off_state or None

    return {
        # ── Agreed schema fields ──────────────────────────────────────────
        "source_site":          "SAM.gov",
        "external_id":          row.get("NoticeId", "").strip(),
        "title":                row.get("Title", "").strip(),
        "agency":               agency,
        "organization":         subtier or None,
        "naics":                row.get("NaicsCode", "").strip() or None,
        "description":          row.get("Description", "").strip() or None,
        "posted_date":          parse_date_str(row.get("PostedDate")),
        "deadline":             parse_date_str(row.get("ResponseDeadLine")),
        "award_date":           parse_date_str(row.get("AwardDate")),
        "contract_value":       row.get("Award$", "").strip() or None,
        "award_status":         type_label,
        "contract_type":        None,       # not available pre-award in SAM.gov
        "acq_strategy":         row.get("SetASide", "").strip() or None,
        "place_of_performance": place,
        "source_listing_id":    row.get("Sol#", "").strip() or None,
        "url":                  row.get("Link", "").strip() or None,
        "date_scraped":         datetime.now().strftime("%Y-%m-%d"),
        "raw_response":         json.dumps(dict(row)),
    }


# ── Normalize a JSON record (live API or JSON bulk) → agreed schema ───────────
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
        # ── Agreed schema fields ──────────────────────────────────────────
        "source_site":          "SAM.gov",
        "external_id":          opp.get("noticeId") or opp.get("NoticeId"),
        "title":                opp.get("title")    or opp.get("Title"),
        "agency":               agency,
        "organization":         subtier or None,
        "naics":                str(opp.get("naicsCode") or "").strip() or None,
        "description":          opp.get("description") or None,
        "posted_date":          parse_date_str(opp.get("postedDate")),
        "deadline":             parse_date_str(opp.get("responseDeadLine")),
        "award_date":           parse_date_str(
                                    (opp.get("award") or {}).get("date")
                                ),
        "contract_value":       str((opp.get("award") or {}).get("amount") or "") or None,
        "award_status":         type_label,
        "contract_type":        None,       # not available pre-award in SAM.gov
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


# ── Local file loader ─────────────────────────────────────────────────────────
def load_from_csv(filepath=LOCAL_CSV_FILE):
    """Load and normalize all records from a local SAM.gov CSV export."""
    print(f"  [sam_gov] Loading CSV: {filepath}")
    records = []
    try:
        with open(filepath, "r", encoding="latin-1") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    records.append(normalize_csv_record(row))
                except Exception as e:
                    print(f"  [sam_gov] Row error: {e}")
                    continue
        print(f"  [sam_gov] Loaded {len(records):,} records from CSV.")
    except Exception as e:
        print(f"  [sam_gov] Could not load CSV: {e}")
    return records


def load_from_json(filepath=LOCAL_JSON_FILE):
    """Load and normalize all records from a local SAM.gov JSON export."""
    print(f"  [sam_gov] Loading JSON: {filepath}")
    records = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = raw if isinstance(raw, list) else (
            raw.get("opportunitiesData") or raw.get("opportunities") or []
        )
        for item in items:
            try:
                records.append(normalize_json_record(item))
            except Exception as e:
                print(f"  [sam_gov] Record error: {e}")
                continue
        print(f"  [sam_gov] Loaded {len(records):,} records from JSON.")
    except Exception as e:
        print(f"  [sam_gov] Could not load JSON: {e}")
    return records


# ── Live API fetcher ──────────────────────────────────────────────────────────
def fetch_from_api(posted_from="01/01/2026", posted_to=None, limit=1000):
    """
    Fetch opportunities from the SAM.gov live API.
    Paginates automatically until all records are retrieved.
    Requires SAM_API_KEY in environment.
    """
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
            resp = requests.get(SAM_BASE_URL, params=params, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"  [sam_gov] API error: {e}")
            break

        data  = resp.json()
        items = data.get("opportunitiesData", [])
        total = int(data.get("totalRecords", 0))

        for item in items:
            try:
                all_records.append(normalize_json_record(item))
            except Exception as e:
                print(f"  [sam_gov] Record error: {e}")
                continue

        print(f"  [sam_gov] Got {len(items)} records (total: {total})")

        offset += limit
        if offset >= total:
            print(f"  [sam_gov] Done. {len(all_records):,} total records fetched.")
            break

        time.sleep(1)   # polite delay between pages

    return all_records


# ── Main entry point called by run_all.py ─────────────────────────────────────
def fetch_and_parse():
    """
    Returns a normalized list of postings from SAM.gov.
    Automatically uses local file if present, otherwise calls live API.
    """
    if os.path.exists(LOCAL_CSV_FILE):
        return load_from_csv(LOCAL_CSV_FILE)
    elif os.path.exists(LOCAL_JSON_FILE):
        return load_from_json(LOCAL_JSON_FILE)
    else:
        return fetch_from_api()