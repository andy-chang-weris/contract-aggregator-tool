#!/usr/bin/env python3
"""
Scrape only currently open records from the public eVA All Opportunities page.

Scope is intentionally narrow. This script reads the target URL from
website.txt and fetches only:
  1. the target AllOpportunities.jsp page
  2. the AllOpportunitiesapp.js asset referenced by that page
  3. the in-page solrconnect.jsp endpoint

It derives the public View Opportunity URL for each row, but it does not fetch
detail pages, PublicSearch pages, downloads, login pages, or external sites.
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Config ────────────────────────────────────────────────────────────────────
BASE_HOST     = "mvendor.cgieva.com"
USER_AGENT    = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
PAGE_SIZE     = 500
MAX_OPEN      = 10_000   # safety guard against accidental full-site scrape
REQUEST_DELAY = 0.15     # seconds between requests

APP_JS_RE  = re.compile(r"""src=["']([^"']*AllOpportunitiesapp\.js[^"']*)["']""", re.I)
BLOCK_RE   = re.compile(
    r"recaptcha|captcha|access\s+denied|forbidden|bot\s+detection|awswaf",
    re.I,
)
SPACE_RE   = re.compile(r"\s+")

# Solr query candidates — tries each until one returns results
OPEN_QUERIES = [
    ("*:*",          ['status:"Open"']),
    ("*:*",          ["status:Open"]),
    ('status:"Open"', []),
    ("status:Open",  []),
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean(value: Any) -> str:
    """Strip HTML, collapse whitespace, return plain string."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(clean(v) for v in value if clean(v))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = html.unescape(str(value))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return SPACE_RE.sub(" ", text).strip()


def clean_code_list(value: Any) -> str:
    """
    Normalize a multi-valued code field (eVA commcode) into a
    deduplicated, order-preserving string. Solr returns commcode as
    multi-valued, so repeated line-item codes get duplicated
    (e.g. '7719; 7719; 7719; ...'). This collapses them to '7719'
    while preserving any genuinely distinct codes.
    """
    if isinstance(value, (list, tuple)):
        seen: list[str] = []
        for v in value:
            c = clean(v)
            if c and c not in seen:
                seen.append(c)
        return "; ".join(seen)
    return clean(value)


def parse_date(value: Any) -> str | None:
    """
    Convert eVA date strings to YYYY-MM-DD.
    eVA returns dates like '2026-05-30T00:00:00Z' or '05/30/2026'.
    """
    s = clean(value)
    if not s:
        return None
    # ISO format
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    # MM/DD/YYYY
    try:
        return datetime.strptime(s[:10], "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    return None


def load_target_url() -> str:
    url = os.getenv("EVA_URL", "").strip()
    if not url:
        raise ValueError(
            "EVA_URL not set in .env. Add:\n"
            "EVA_URL=https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp"
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != BASE_HOST:
        raise ValueError(f"EVA_URL must point to https://{BASE_HOST}")
    if parsed.path != "/Vendor/public/AllOpportunities.jsp":
        raise ValueError("EVA_URL must point to /Vendor/public/AllOpportunities.jsp")
    return url


# ── HTTP client ───────────────────────────────────────────────────────────────
class EvaClient:
    def __init__(self, target_url: str) -> None:
        self.target_url = target_url
        self.solr_url   = urllib.parse.urljoin(target_url, "solrconnect.jsp")
        self.last_fetch = 0.0
        self.opener     = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor()
        )

    def _fetch(self, url: str, accept: str) -> str:
        # Polite delay
        wait = REQUEST_DELAY + random.uniform(0, REQUEST_DELAY / 3)
        elapsed = time.time() - self.last_fetch
        if elapsed < wait:
            time.sleep(wait - elapsed)

        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent":      USER_AGENT,
                    "Accept":          accept,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer":         self.target_url,
                })
                with self.opener.open(req, timeout=60) as resp:
                    body = resp.read()
                    self.last_fetch = time.time()
                    text = body.decode("utf-8", "replace")
                    if not text.strip():
                        raise RuntimeError("Empty response body")
                    if BLOCK_RE.search(text[:5000]):
                        raise RuntimeError("Anti-bot challenge detected")
                    return text
            except urllib.error.HTTPError as e:
                if e.code in {403, 429}:
                    raise RuntimeError(f"HTTP {e.code} — blocked") from e
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except RuntimeError:
                raise
            except Exception as e:
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Fetch failed: {e}") from e
        raise RuntimeError(f"All retries exhausted for {url}")

    def fetch_html(self, url: str) -> str:
        return self._fetch(url, "text/html,application/xhtml+xml,*/*;q=0.8")

    def fetch_json(self, url: str) -> dict:
        text = self._fetch(url, "application/json,text/plain,*/*")
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            raise RuntimeError(f"JSON parse failed: {e}") from e

    def build_solr_url(self, q: str, fqs: list[str], rows: int, start: int = 0) -> str:
        params: list[tuple[str, str]] = [
            ("q",    q),
            ("rows", str(rows)),
            ("start", str(start)),
            ("wt",   "json"),
        ]
        params.extend(("fq", fq) for fq in fqs)
        return self.solr_url + "?" + urllib.parse.urlencode(params)

    def find_open_query(self) -> tuple[str, list[str], int]:
        """Try query candidates until one returns Open records. Returns (q, fqs, total)."""
        for q, fqs in OPEN_QUERIES:
            url = self.build_solr_url(q, fqs, rows=0)
            try:
                payload = self.fetch_json(url)
                count = int(payload.get("response", {}).get("numFound", 0) or 0)
                if 0 < count <= MAX_OPEN:
                    print(f"  [eva] Open query found: q={q} fq={fqs} → {count} records")
                    return q, fqs, count
            except Exception as e:
                print(f"  [eva] Query candidate failed: {e}")
                continue
        raise RuntimeError("No working Open-status query found for eVA")


# ── Normalize a Solr doc → schema ──────────────────────────────────────
def normalize_doc(doc: dict, target_url: str) -> dict:
    """
    Maps a single eVA Solr document to the agreed postings schema.

    eVA field mapping:
      id / internalid    → external_id
      agencyname         → agency
      buyerdeptname      → organization
      commcode           → naics (eVA uses commodity codes, not NAICS — stored as-is)
      pubdate            → posted_date
      closedate          → deadline
      shortdesc          → title
      longdesc           → description
      doccddesc          → award_status (contract type label)
      setasideshortdesc  → acq_strategy
      workloc            → place_of_performance
    """
    internal_id = clean(doc.get("internalid") or doc.get("id") or "")
    version     = clean(doc.get("version") or "")

    if internal_id and version:
        doctype = clean(doc.get("doctype")) or "SO"
        view_params = urllib.parse.urlencode([
            ("PageTitle",    f"{doctype} Details"),
            ("rfp_id_lot",   internal_id),
            ("rfp_id_round", version),
        ], quote_via=urllib.parse.quote)
        url = urllib.parse.urljoin(target_url, "IVDetails.jsp") + "?" + view_params
    else:
        url = target_url

    return {
        "source_site":          "Virginia eVA",
        "external_id":          internal_id or None,
        "url":                  url,

        # Filter columns
        "agency":               clean(doc.get("agencyname")) or None,
        "naics":                clean_code_list(doc.get("commcode")) or None,
        "posted_date":          parse_date(doc.get("pubdate")),
        "contract_type":        None,   # eVA doesn't have a direct contract type
        "place_of_performance": clean(doc.get("workloc")) or "Virginia",

        # Extra columns
        "title":                clean(doc.get("shortdesc")) or None,
        "organization":         clean(doc.get("buyerdeptname")) or None,
        "description":          clean(doc.get("longdesc")) or None,
        "deadline":             parse_date(doc.get("closedate")),
        "award_date":           parse_date(doc.get("amenddate")),
        "contract_value":       None,   # eVA doesn't expose estimated value publicly
        "award_status":         clean(doc.get("doccddesc")) or clean(doc.get("status")) or None,
        "acq_strategy":         clean(doc.get("setasideshortdesc")) or None,
        "source_listing_id":    clean(doc.get("externalid") or doc.get("g2gtrackingid")) or None,

        "date_scraped":         datetime.now().strftime("%Y-%m-%d"),
        "raw_response":         json.dumps(doc),
    }


# ── Main entry point ──────────────────────────────────────────────────────────
def fetch_and_parse() -> list[dict]:
    """
    Fetches all Open opportunities from eVA and returns
    a list of normalized postings ready for store_postings().
    """
    target_url = load_target_url()
    client     = EvaClient(target_url)

    # Step 1: Load page to establish session/cookies
    print("  [eva] Loading eVA AllOpportunities page...")
    page_html = client.fetch_html(target_url)

    # Step 2: Verify AllOpportunitiesapp.js is referenced (scope check)
    if not APP_JS_RE.search(page_html):
        raise RuntimeError("AllOpportunitiesapp.js not found on page — site may have changed")

    # Step 3: Find working Open query
    q, fqs, total = client.find_open_query()
    print(f"  [eva] Fetching {total} Open opportunities...")

    # Step 4: Paginate through all records
    all_postings = []
    start        = 0

    while start < total:
        rows    = min(PAGE_SIZE, total - start)
        url     = client.build_solr_url(q, fqs, rows=rows, start=start)
        payload = client.fetch_json(url)
        docs    = payload.get("response", {}).get("docs", [])

        if not docs:
            print(f"  [eva] Empty page at start={start} — stopping.")
            break

        for doc in docs:
            # Only include Open records (double-check server filter)
            if clean(doc.get("status")).lower() != "open":
                continue
            posting = normalize_doc(doc, target_url)
            all_postings.append(posting)

        start += len(docs)
        print(f"  [eva] Fetched {len(all_postings):,} / {total:,}", end="\r")

        if len(docs) < rows:
            break

    print(f"\n  [eva] Done. {len(all_postings):,} open opportunities fetched.")
    return all_postings