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

import argparse
import html
import json
import random
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape


DEFAULT_WEBSITE_FILE = Path("website.txt")
DEFAULT_OUTPUT = Path("outputs/scraper_test2_open_contracts.xlsx")
BASE_HOST = "mvendor.cgieva.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

APP_JS_RE = re.compile(r"""src=["']([^"']*AllOpportunitiesapp\.js[^"']*)["']""", re.IGNORECASE)
BLOCK_RE = re.compile(
    r"recaptcha|captcha|access\s+denied|forbidden|bot\s+detection|"
    r"awswaf|token\.awswaf\.com|challenge\.js",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
XML_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
EXCEL_MAX_CELL_CHARS = 32_767
XLSX_MAX_ROWS = 1_048_576

OPEN_QUERY_CANDIDATES = [
    ("*:*", ['status:"Open"']),
    ("*:*", ["status:Open"]),
    ('status:"Open"', []),
    ("status:Open", []),
    ("*:*", ["status:(Open)"]),
]

FACET_FIELDS = [
    "status",
    "agencyname",
    "doccddesc",
    "category",
    "setasideshortdesc",
    "pubdate",
    "closedate",
]

RECORD_COLUMNS = [
    "record_number",
    "source_url",
    "view_opportunity_url",
    "data_endpoint",
    "solr_q",
    "solr_fq",
    "id",
    "status",
    "app",
    "doccd",
    "doccddesc",
    "doctype",
    "shortdesc",
    "longdesc",
    "agency",
    "agencyname",
    "docdeptcd",
    "buyerdeptname",
    "buyername",
    "preparername",
    "prepareremail",
    "preparerphonenumber",
    "category",
    "categoryshortdesc",
    "commcode",
    "commdesc",
    "commlinedesc",
    "setaside",
    "setasideshortdesc",
    "g2gtrackingid",
    "g2gservicesprovidedbyid",
    "externalid",
    "internalid",
    "version",
    "pubdate",
    "closedate",
    "amenddate",
    "lastupdatedate",
    "openresponsesdate",
    "expirationdate",
    "workloc",
    "_version_",
    "extra_json",
]


class ScopeError(ValueError):
    """Raised when the scraper attempts to leave the approved page/app scope."""


@dataclass(frozen=True)
class Scope:
    target_url: str
    app_js_url: str
    solr_url: str
    allowed_paths: frozenset[str]


@dataclass(frozen=True)
class QuerySpec:
    q: str
    fqs: tuple[str, ...]

    @property
    def label(self) -> str:
        return "q=" + self.q + ("; fq=" + " AND ".join(self.fqs) if self.fqs else "")


@dataclass
class FetchEvent:
    timestamp: str
    url: str
    status: str
    content_type: str
    bytes_read: int
    elapsed_ms: int
    note: str = ""


@dataclass
class ScrapeStats:
    started_at: str = field(default_factory=lambda: now_iso())
    finished_at: str = ""
    expected_count: int = 526
    filtered_rows_reported: int = 0
    rows_written: int = 0
    pages_fetched: int = 0
    app_js_bytes: int = 0
    selected_query: QuerySpec | None = None
    candidate_results: list[list[Any]] = field(default_factory=list)
    facet_rows: list[list[Any]] = field(default_factory=list)
    errors: list[list[str]] = field(default_factory=list)
    fetch_log: list[FetchEvent] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(item for item in (clean_text(part) for part in value) if item)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = html.unescape(str(value))
    text = XML_CONTROL_RE.sub("", text)
    return SPACE_RE.sub(" ", text).strip()


def load_target_url(path: Path) -> str:
    target_url = path.read_text(encoding="utf-8").strip()
    if not target_url:
        raise ScopeError(f"{path} is empty")
    parsed = urllib.parse.urlparse(target_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != BASE_HOST:
        raise ScopeError(f"website.txt must point to https://{BASE_HOST}: {target_url}")
    if parsed.path != "/Vendor/public/AllOpportunities.jsp":
        raise ScopeError(f"website.txt must point to /Vendor/public/AllOpportunities.jsp: {target_url}")
    return target_url


def build_scope(target_url: str) -> Scope:
    app_js_url = urllib.parse.urljoin(target_url, "../public/AllOpportunitiesapp.js")
    solr_url = urllib.parse.urljoin(target_url, "solrconnect.jsp")
    allowed_paths = frozenset(
        urllib.parse.urlparse(url).path for url in [target_url, app_js_url, solr_url]
    )
    scope = Scope(
        target_url=target_url,
        app_js_url=app_js_url,
        solr_url=solr_url,
        allowed_paths=allowed_paths,
    )
    for url in [scope.target_url, scope.app_js_url, scope.solr_url]:
        assert_allowed_url(scope, url)
    return scope


def assert_allowed_url(scope: Scope, url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != BASE_HOST:
        raise ScopeError(f"Blocked out-of-scope host: {url}")
    if parsed.path not in scope.allowed_paths:
        raise ScopeError(f"Blocked non-target eVA path: {url}")


def resolve_target_asset(scope: Scope, page_html: str) -> str:
    match = APP_JS_RE.search(page_html)
    if not match:
        raise RuntimeError("Target page did not reference AllOpportunitiesapp.js")
    asset_url = urllib.parse.urljoin(scope.target_url, match.group(1))
    assert_allowed_url(scope, asset_url)
    return asset_url


def build_solr_url(
    scope: Scope,
    query: QuerySpec,
    *,
    rows: int,
    start: int = 0,
    facets: bool = False,
) -> str:
    params: list[tuple[str, str]] = [
        ("q", query.q),
        ("rows", str(rows)),
        ("start", str(start)),
        ("wt", "json"),
    ]
    params.extend(("fq", fq) for fq in query.fqs)
    if facets:
        params.extend(("facet.field", field_name) for field_name in FACET_FIELDS)
        params.extend(
            [
                ("facet", "on"),
                ("facet.mincount", "1"),
                ("facet.limit", "600"),
                ("facet.sort", "count"),
            ]
        )
    url = scope.solr_url + "?" + urllib.parse.urlencode(params)
    assert_allowed_url(scope, url)
    return url


class EvaClient:
    def __init__(self, scope: Scope, timeout: int, delay: float, retries: int) -> None:
        self.scope = scope
        self.timeout = timeout
        self.delay = max(delay, 0.0)
        self.retries = max(retries, 0)
        self.last_fetch_at = 0.0
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def fetch_text(self, url: str, *, accept: str, referer: str, stats: ScrapeStats) -> str:
        assert_allowed_url(self.scope, url)
        if self.delay:
            wait = self.delay + random.uniform(0, self.delay / 3)
            elapsed = time.time() - self.last_fetch_at
            if elapsed < wait:
                time.sleep(wait - elapsed)

        last_error = ""
        for attempt in range(self.retries + 1):
            started = time.time()
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": accept,
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": referer,
                    },
                )
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read()
                    elapsed_ms = int((time.time() - started) * 1000)
                    text = body.decode("utf-8", "replace")
                    status = str(response.status)
                    content_type = response.headers.get("content-type", "")
                    stats.fetch_log.append(
                        FetchEvent(
                            timestamp=now_iso(),
                            url=url,
                            status=status,
                            content_type=content_type,
                            bytes_read=len(body),
                            elapsed_ms=elapsed_ms,
                        )
                    )
                    self.last_fetch_at = time.time()
                    if not text.strip():
                        last_error = f"empty response body with HTTP {status}"
                        if attempt < self.retries:
                            time.sleep(1.5 * (attempt + 1))
                            continue
                        raise RuntimeError(last_error)
                    if BLOCK_RE.search(text[:5000]):
                        raise RuntimeError("anti-bot or AWS WAF challenge text detected")
                    return text
            except urllib.error.HTTPError as exc:
                elapsed_ms = int((time.time() - started) * 1000)
                body = exc.read() if exc.fp else b""
                last_error = f"HTTP {exc.code}"
                stats.fetch_log.append(
                    FetchEvent(
                        timestamp=now_iso(),
                        url=url,
                        status=str(exc.code),
                        content_type=exc.headers.get("content-type", "") if exc.headers else "",
                        bytes_read=len(body),
                        elapsed_ms=elapsed_ms,
                        note=last_error,
                    )
                )
                if exc.code in {403, 429}:
                    raise RuntimeError(last_error) from exc
            except Exception as exc:
                elapsed_ms = int((time.time() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                stats.fetch_log.append(
                    FetchEvent(
                        timestamp=now_iso(),
                        url=url,
                        status="error",
                        content_type="",
                        bytes_read=0,
                        elapsed_ms=elapsed_ms,
                        note=last_error,
                    )
                )
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")

    def fetch_json(self, url: str, *, referer: str, stats: ScrapeStats) -> dict[str, Any]:
        text = self.fetch_text(
            url,
            accept="application/json,text/plain,*/*",
            referer=referer,
            stats=stats,
        )
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON parse failed for {url}: {exc}") from exc


def candidate_specs() -> list[QuerySpec]:
    return [QuerySpec(q=q, fqs=tuple(fqs)) for q, fqs in OPEN_QUERY_CANDIDATES]


def response_num_found(payload: dict[str, Any]) -> int:
    return int(payload.get("response", {}).get("numFound", 0) or 0)


def select_open_query(client: EvaClient, stats: ScrapeStats, args: argparse.Namespace) -> QuerySpec:
    rejected: list[str] = []
    for spec in candidate_specs():
        url = build_solr_url(client.scope, spec, rows=0)
        try:
            payload = client.fetch_json(url, referer=client.scope.target_url, stats=stats)
            count = response_num_found(payload)
            stats.candidate_results.append([spec.q, " AND ".join(spec.fqs), count, "accepted_probe"])
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            rejected.append(f"{spec.label} -> {message}")
            stats.candidate_results.append([spec.q, " AND ".join(spec.fqs), "", message])
            continue

        if count <= 0:
            rejected.append(f"{spec.label} -> reported zero rows")
            continue
        if count > args.max_open_count:
            rejected.append(
                f"{spec.label} -> reported {count:,}, above max-open-count={args.max_open_count:,}"
            )
            continue
        stats.filtered_rows_reported = count
        stats.selected_query = spec
        return spec

    details = "; ".join(rejected) if rejected else "no candidate filters were tested"
    raise RuntimeError(f"No safe Open-status server-side filter was found: {details}")


def facet_pairs(payload: dict[str, Any]) -> list[list[Any]]:
    rows = [["field", "value", "count"]]
    fields = payload.get("facet_counts", {}).get("facet_fields", {})
    for field_name in FACET_FIELDS:
        values = fields.get(field_name, [])
        for idx in range(0, len(values), 2):
            value = values[idx]
            count = values[idx + 1] if idx + 1 < len(values) else ""
            rows.append([field_name, value, count])
    return rows


def build_view_opportunity_url(scope: Scope, doc: dict[str, Any]) -> str:
    internal_id = clean_text(doc.get("internalid"))
    version = clean_text(doc.get("version"))
    if not internal_id or not version:
        return ""
    page_title = clean_text(doc.get("doctype")) or "SO"
    params = [
        ("PageTitle", f"{page_title} Details"),
        ("rfp_id_lot", internal_id),
        ("rfp_id_round", version),
    ]
    return urllib.parse.urljoin(scope.target_url, "IVDetails.jsp") + "?" + urllib.parse.urlencode(
        params,
        quote_via=urllib.parse.quote,
    )


def normalize_record(scope: Scope, query: QuerySpec, doc: dict[str, Any], record_number: int) -> list[str]:
    known = set(RECORD_COLUMNS) - {
        "record_number",
        "source_url",
        "view_opportunity_url",
        "data_endpoint",
        "solr_q",
        "solr_fq",
        "extra_json",
    }
    extra = {key: doc[key] for key in sorted(doc) if key not in known}
    row: dict[str, Any] = {
        "record_number": record_number,
        "source_url": scope.target_url,
        "view_opportunity_url": build_view_opportunity_url(scope, doc),
        "data_endpoint": scope.solr_url,
        "solr_q": query.q,
        "solr_fq": " AND ".join(query.fqs),
        "extra_json": json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else "",
    }
    for column in known:
        row[column] = doc.get(column, "")
    return [clean_text(row.get(column, "")) for column in RECORD_COLUMNS]


def column_ref(index: int) -> str:
    index += 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_xml(value: Any, row_index: int, column_index: int, style_index: int = 0) -> str:
    ref = f"{column_ref(column_index)}{row_index}"
    text = clean_text(value)
    if len(text) > EXCEL_MAX_CELL_CHARS:
        text = text[:EXCEL_MAX_CELL_CHARS]
    style = f' s="{style_index}"' if style_index else ""
    if not text:
        return f'<c r="{ref}"{style}/>'
    escaped = xml_escape(text)
    space = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return f'<c r="{ref}"{style} t="inlineStr"><is><t{space}>{escaped}</t></is></c>'


def xml_attr(value: str) -> str:
    return xml_escape(value, {'"': "&quot;"})


class WorksheetWriter:
    def __init__(
        self,
        temp_dir: Path,
        name: str,
        freeze_header: bool = True,
        hyperlink_columns: set[int] | None = None,
    ) -> None:
        self.name = name[:31]
        self.path = temp_dir / f"{self.name.replace(' ', '_')}.xml"
        self.row_count = 0
        self.hyperlink_columns = hyperlink_columns or set()
        self.hyperlinks: list[tuple[str, str, str]] = []
        self.handle = self.path.open("w", encoding="utf-8", newline="")
        self.handle.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        worksheet_attrs = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        if self.hyperlink_columns:
            worksheet_attrs += ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        self.handle.write(f"<worksheet {worksheet_attrs}>")
        if freeze_header:
            self.handle.write(
                '<sheetViews><sheetView workbookViewId="0">'
                '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                "</sheetView></sheetViews>"
            )
        self.handle.write("<sheetData>")

    def write_row(self, values: Iterable[Any]) -> None:
        if self.row_count >= XLSX_MAX_ROWS:
            raise RuntimeError(f"Worksheet {self.name} exceeded Excel row limit")
        self.row_count += 1
        cell_parts: list[str] = []
        for idx, value in enumerate(values):
            text = clean_text(value)
            style_index = 1 if self.row_count > 1 and idx in self.hyperlink_columns and text else 0
            cell_parts.append(cell_xml(text, self.row_count, idx, style_index=style_index))
            if style_index:
                ref = f"{column_ref(idx)}{self.row_count}"
                rel_id = f"rId{len(self.hyperlinks) + 1}"
                self.hyperlinks.append((ref, rel_id, text))
        cells = "".join(cell_parts)
        self.handle.write(f'<row r="{self.row_count}">{cells}</row>')

    def close(self) -> None:
        if not self.handle.closed:
            self.handle.write("</sheetData>")
            if self.hyperlinks:
                links = "".join(
                    f'<hyperlink ref="{ref}" r:id="{rel_id}"/>' for ref, rel_id, _ in self.hyperlinks
                )
                self.handle.write(f"<hyperlinks>{links}</hyperlinks>")
            self.handle.write("</worksheet>")
            self.handle.close()

    def rels_xml(self) -> str:
        rels = "".join(
            '<Relationship '
            f'Id="{rel_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{xml_attr(url)}" '
            'TargetMode="External"/>'
            for _, rel_id, url in self.hyperlinks
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>"
        )


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{idx}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def content_types_xml(sheet_count: int) -> str:
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for idx in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{''.join(overrides)}</Types>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><u/><color rgb="FF0563C1"/><sz val="11"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        "</styleSheet>"
    )


def write_workbook(path: Path, worksheets: list[WorksheetWriter]) -> None:
    for sheet in worksheets:
        sheet.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml(len(worksheets)))
        zf.writestr("_rels/.rels", root_rels_xml())
        zf.writestr("xl/workbook.xml", workbook_xml([sheet.name for sheet in worksheets]))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(worksheets)))
        zf.writestr("xl/styles.xml", styles_xml())
        for idx, sheet in enumerate(worksheets, start=1):
            zf.write(sheet.path, f"xl/worksheets/sheet{idx}.xml")
            if sheet.hyperlinks:
                zf.writestr(f"xl/worksheets/_rels/sheet{idx}.xml.rels", sheet.rels_xml())


def verify_xlsx(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad:
                return False, f"corrupt zip member: {bad}"
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels",
                "xl/worksheets/sheet1.xml",
            }
            missing = sorted(required - set(zf.namelist()))
            if missing:
                return False, f"missing parts: {', '.join(missing)}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def write_static_rows(sheet: WorksheetWriter, rows: list[list[Any]]) -> None:
    for row in rows:
        sheet.write_row(row)


def build_summary_rows(scope: Scope, stats: ScrapeStats, args: argparse.Namespace, output_path: Path) -> list[list[Any]]:
    selected = stats.selected_query.label if stats.selected_query else ""
    count_note = "matched"
    if stats.expected_count and stats.filtered_rows_reported != stats.expected_count:
        count_note = f"expected {stats.expected_count}, got {stats.filtered_rows_reported}"
    return [
        ["metric", "value"],
        ["started_at_utc", stats.started_at],
        ["finished_at_utc", stats.finished_at],
        ["target_page", scope.target_url],
        ["app_js", scope.app_js_url],
        ["data_endpoint", scope.solr_url],
        ["scope_note", "Detail page URLs are derived for the workbook, but no detail pages, downloads, PublicSearch pages, login pages, or external sites are fetched."],
        ["filter_note", "Only records reported by the target page's own solrconnect.jsp with status Open are written."],
        ["selected_query", selected],
        ["expected_open_count_from_screenshot", stats.expected_count],
        ["filtered_rows_reported", stats.filtered_rows_reported],
        ["count_check", count_note],
        ["rows_written", stats.rows_written],
        ["page_size", args.page_size],
        ["max_records", "all" if args.max_records == 0 else args.max_records],
        ["max_open_count_guard", args.max_open_count],
        ["fetches_logged", len(stats.fetch_log)],
        ["errors_logged", len(stats.errors)],
        ["output_xlsx", str(output_path)],
    ]


def scrape(args: argparse.Namespace) -> Path:
    website_file = Path(args.website_file)
    output_path = Path(args.output)
    target_url = load_target_url(website_file)
    scope = build_scope(target_url)
    temp_dir = Path(tempfile.mkdtemp(prefix="scraper_test2_", dir=str(output_path.parent if output_path.parent else Path("."))))
    stats = ScrapeStats(expected_count=args.expected_count)
    worksheets: list[WorksheetWriter] = []
    client = EvaClient(scope=scope, timeout=args.timeout, delay=args.delay, retries=args.retries)

    try:
        target_html = client.fetch_text(
            scope.target_url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            referer=scope.target_url,
            stats=stats,
        )
        app_js_url = resolve_target_asset(scope, target_html)
        app_js = client.fetch_text(
            app_js_url,
            accept="application/javascript,text/javascript,*/*",
            referer=scope.target_url,
            stats=stats,
        )
        stats.app_js_bytes = len(app_js.encode("utf-8", "replace"))
        if "solrconnect.jsp" not in app_js:
            raise RuntimeError("AllOpportunitiesapp.js did not reference solrconnect.jsp")

        query = select_open_query(client, stats, args)
        facet_payload = client.fetch_json(
            build_solr_url(scope, query, rows=0, facets=True),
            referer=scope.target_url,
            stats=stats,
        )
        stats.filtered_rows_reported = response_num_found(facet_payload)
        stats.facet_rows = facet_pairs(facet_payload)

        total = stats.filtered_rows_reported
        if args.require_expected_count and total != args.expected_count:
            raise RuntimeError(f"Open count mismatch: expected {args.expected_count}, endpoint reported {total}")
        if total > args.max_open_count:
            raise RuntimeError(
                f"Filtered query reported {total:,} rows, above max-open-count={args.max_open_count:,}; refusing broad scrape."
            )
        if args.max_records:
            total = min(total, args.max_records)

        contracts_sheet = WorksheetWriter(temp_dir, "Contracts", hyperlink_columns={RECORD_COLUMNS.index("view_opportunity_url")})
        contracts_sheet.write_row(RECORD_COLUMNS)
        worksheets.append(contracts_sheet)

        start = 0
        while start < total:
            rows = min(args.page_size, total - start)
            payload = client.fetch_json(
                build_solr_url(scope, query, rows=rows, start=start),
                referer=scope.target_url,
                stats=stats,
            )
            response = payload.get("response", {})
            docs = response.get("docs", [])
            if not docs:
                stats.errors.append([now_iso(), scope.solr_url, "empty_page", f"No docs returned at start={start}"])
                break
            for offset, doc in enumerate(docs, start=1):
                if clean_text(doc.get("status")).lower() != "open":
                    stats.errors.append(
                        [
                            now_iso(),
                            scope.solr_url,
                            "non_open_record_skipped",
                            f"Record at start={start} offset={offset} had status={clean_text(doc.get('status'))!r}",
                        ]
                    )
                    continue
                contracts_sheet.write_row(normalize_record(scope, query, doc, start + offset))
                stats.rows_written += 1
            stats.pages_fetched += 1
            start += len(docs)
            print(f"wrote {stats.rows_written:,} / {total:,} open records", flush=True)
            if len(docs) < rows:
                break

        stats.finished_at = now_iso()
        summary_sheet = WorksheetWriter(temp_dir, "Summary")
        write_static_rows(summary_sheet, build_summary_rows(scope, stats, args, output_path))
        candidates_sheet = WorksheetWriter(temp_dir, "Filter Candidates")
        write_static_rows(candidates_sheet, [["q", "fq", "numFound", "result"]] + stats.candidate_results)
        facets_sheet = WorksheetWriter(temp_dir, "Facets")
        write_static_rows(facets_sheet, stats.facet_rows or [["field", "value", "count"]])
        log_sheet = WorksheetWriter(temp_dir, "Crawl Log")
        write_static_rows(
            log_sheet,
            [["timestamp", "url", "status", "content_type", "bytes_read", "elapsed_ms", "note"]]
            + [
                [
                    event.timestamp,
                    event.url,
                    event.status,
                    event.content_type,
                    event.bytes_read,
                    event.elapsed_ms,
                    event.note,
                ]
                for event in stats.fetch_log
            ],
        )
        errors_sheet = WorksheetWriter(temp_dir, "Errors")
        write_static_rows(errors_sheet, [["timestamp", "url", "error_type", "message"]] + stats.errors)
        worksheets.extend([summary_sheet, candidates_sheet, facets_sheet, log_sheet, errors_sheet])

        write_workbook(output_path, worksheets)
        ok, message = verify_xlsx(output_path)
        if not ok:
            raise RuntimeError(f"Workbook verification failed: {message}")
        return output_path
    finally:
        for sheet in worksheets:
            sheet.close()
        if temp_dir.exists() and not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape only Open records and derived View Opportunity links from the eVA AllOpportunities.jsp page listed in website.txt."
    )
    parser.add_argument("--website-file", default=str(DEFAULT_WEBSITE_FILE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-records", type=int, default=0, help="0 means all filtered Open records.")
    parser.add_argument("--expected-count", type=int, default=526, help="Screenshot count used for reporting and optional validation.")
    parser.add_argument("--require-expected-count", action="store_true", help="Fail if the endpoint does not report expected-count rows.")
    parser.add_argument("--max-open-count", type=int, default=10_000, help="Safety guard to prevent accidental broad 289k-record scrapes.")
    parser.add_argument("--delay", type=float, default=0.15, help="Base delay between requests, in seconds.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary worksheet XML for debugging.")
    args = parser.parse_args()
    args.page_size = max(1, min(args.page_size, 5000))
    args.max_records = max(0, args.max_records)
    args.expected_count = max(0, args.expected_count)
    args.max_open_count = max(1, args.max_open_count)
    return args


def main() -> int:
    args = parse_args()
    try:
        output_path = scrape(args)
    except ScopeError as exc:
        print(f"Scope error: {exc}")
        return 2
    except Exception as exc:
        print(f"Scrape failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
