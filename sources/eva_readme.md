# scraper_test2.py Open Contracts Scraper With View Opportunity Links

## Scope

Target page is read from:

`website.txt`

Current target:

`https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp`

The scraper is intentionally limited to the public app loaded by that page. It fetches only:

- `AllOpportunities.jsp`
- `AllOpportunitiesapp.js`, because that script is referenced by the target page
- `solrconnect.jsp`, because that endpoint is referenced by `AllOpportunitiesapp.js`

It also derives the public `View Opportunity` URL for each Open row and writes it to the workbook. It does not fetch opportunity detail pages, contract search pages, document downloads, login pages, or external sites.

## Research Notes

User workflow:
1) the All Opportunities app, the website landing page
2) the split-second `View Opportunity` URL
3) the served detail page once you click View Opportunity

The rough link shape at step 2:

`https://mvendor.cgieva.com/Vendor/public/IVDetails.jsp?PageTitle=SO%20Details&rfp_id_lot=121995&rfp_id_round=0`

The Solr rows already expose the needed identifiers:

- `internalid` maps to `rfp_id_lot`
- `version` maps to `rfp_id_round`
- `doctype` maps to the `PageTitle` prefix, currently `SO` for the Open rows

The implementation derives the link from those fields. It does not open the detail page to collect or verify extra data.

## Page Behavior

`AllOpportunities.jsp` renders a JavaScript app, not a static table of all results.

The app references:

`../public/AllOpportunitiesapp.js`

That script references:

`solrconnect.jsp`

The scraper validates this chain before querying data. It then probes a small list of Open-status Solr query shapes and accepts only a candidate that returns a positive count under the configured safety cap.

The successful query shape for the completed run was:

`q=*:*`

`fq=status:"Open"`

## Implementation

File:

`scraper_test2.py`

Default output:

`outputs/scraper_test2_open_contracts.xlsx`

Behavior:

- Reads the target page from `website.txt`.
- Enforces URL scope for every fetch.
- Bootstraps a cookie-aware session from `AllOpportunities.jsp`.
- Confirms that the target page references `AllOpportunitiesapp.js`.
- Confirms that the script references `solrconnect.jsp`.
- Selects a safe server-side Open-status query.
- Requires the filtered result count to be below `--max-open-count`, default `10000`.
- Optionally requires the filtered result count to equal `--expected-count`, default `526`.
- Paginates only the filtered Open records.
- Skips any returned row whose `status` is not exactly `Open`.
- Adds `view_opportunity_url` to the `Contracts` worksheet.
- Writes the `view_opportunity_url` cells as clickable external hyperlinks in the workbook.
- Writes sheets for `Contracts`, `Summary`, `Filter Candidates`, `Facets`, `Crawl Log`, and `Errors`.
- Verifies that the `.xlsx` package is structurally valid after writing.

Usage:

```powershell
python .\scraper_test2.py --output outputs\scraper_test2_open_contracts.xlsx --require-expected-count
```

Fast smoke test:

```powershell
python .\scraper_test2.py --output outputs\scraper_test2_smoke.xlsx --max-records 1 --require-expected-count
```

If the live site count changes after the screenshot, omit `--require-expected-count` but keep the broad-scrape guard:

```powershell
python .\scraper_test2.py --output outputs\scraper_test2_open_contracts.xlsx --max-open-count 10000
```

## Testing Plan

Static checks:

- Compile `scraper_test2.py`.
- Verify that URL scope rejects non-target fetch paths.
- Verify that the workbook ZIP package contains required XLSX parts.
- Verify that the `Contracts` sheet has a worksheet relationship file for clickable hyperlinks.

Live checks:

- Run a capped scrape with `--max-records 1 --require-expected-count`.
- Run the full scrape with `--require-expected-count`.
- Verify the workbook ZIP integrity.
- Verify that the `Contracts` worksheet has exactly 526 data rows.
- Verify that the `Contracts` worksheet has exactly 526 `view_opportunity_url` hyperlinks and no missing URL values.

## Completed Run

Smoke command used:

```powershell
python .\scraper_test2.py --output outputs\scraper_test2_smoke.xlsx --max-records 1 --require-expected-count --timeout 20 --retries 1
```

Smoke result:

- Output workbook: `outputs/scraper_test2_smoke.xlsx`
- Open records reported by filtered endpoint: `526`
- `Contracts` worksheet data rows verified: `1`
- `Contracts` worksheet hyperlink relationships verified: `1`
- Workbook ZIP integrity check: passed

Full scrape command used:

```powershell
python .\scraper_test2.py --output outputs\scraper_test2_open_contracts.xlsx --require-expected-count --timeout 30 --retries 2
```

Full scrape result:

- Output workbook: `outputs/scraper_test2_open_contracts.xlsx`
- Workbook size: `278,731` bytes
- Open records reported by filtered endpoint: `526`
- `Contracts` worksheet data rows verified: `526`
- `view_opportunity_url` missing values: `0`
- `Contracts` worksheet hyperlink relationships verified: `526`
- Workbook ZIP integrity check: passed

Verification performed:

```powershell
python -m py_compile .\scraper_test2.py
```

```powershell
python -c "import zipfile,xml.etree.ElementTree as ET; p='outputs/scraper_test2_open_contracts.xlsx'; z=zipfile.ZipFile(p); print('bad=', z.testzip()); names=set(z.namelist()); sheet=z.read('xl/worksheets/sheet1.xml').decode('utf-8'); print('data_rows=', sheet.count('<row ')-1); print('hyperlinks=', sheet.count('<hyperlink ')); print('sheet_rels=', 'xl/worksheets/_rels/sheet1.xml.rels' in names); relroot=ET.fromstring(z.read('xl/worksheets/_rels/sheet1.xml.rels')); print('rels=', len(relroot)); print('first_target=', relroot[0].attrib['Target']); print('last_target=', relroot[-1].attrib['Target'])"
```

Verification result:

- `bad=None`
- `data_rows=526`
- `hyperlinks=526`
- `sheet_rels=True`
- `rels=526`
- First target: `https://mvendor.cgieva.com/Vendor/public/IVDetails.jsp?PageTitle=SO%20Details&rfp_id_lot=170594&rfp_id_round=1`
- Last target: `https://mvendor.cgieva.com/Vendor/public/IVDetails.jsp?PageTitle=SO%20Details&rfp_id_lot=120393&rfp_id_round=1`

## How To Read And Debug scraper_test2.py

Start at `main()`.

- `parse_args()` defines runtime controls such as output path, expected count, max open count, page size, retry count, and timeout.
- `scrape()` is the orchestration function. Read this first for the full control flow.
- `load_target_url()` and `build_scope()` enforce that `website.txt` points to only the approved All Opportunities page.
- `assert_allowed_url()` is the hard fetch-scope guard.
- `EvaClient.fetch_text()` and `EvaClient.fetch_json()` handle HTTP, cookies, retries, anti-bot/challenge detection, and crawl logging.
- `select_open_query()` probes safe Open-status query candidates and refuses broad result sets.
- `build_solr_url()` is where Solr request parameters are assembled.
- `build_view_opportunity_url()` derives the clickable public detail URL from `internalid`, `version`, and `doctype`.
- `normalize_record()` maps Solr document fields into the `Contracts` worksheet columns and preserves unexpected fields in `extra_json`.
- `WorksheetWriter`, `write_workbook()`, and `verify_xlsx()` are the dependency-free XLSX writer.

Useful debugging commands:

```powershell
python -m py_compile .\scraper_test2.py
```

```powershell
python .\scraper_test2.py --output outputs\debug_scraper_test2.xlsx --max-records 5 --keep-temp
```

```powershell
python .\scraper_test2.py --output outputs\debug_scraper_test2.xlsx --max-records 1 --require-expected-count
```

Debugging notes:

- If the script fails with `anti-bot or AWS WAF challenge text detected`, the site returned a challenge page instead of the public app. Rerun later or from an environment/session that can reach the public page.
- If it fails with `Open count mismatch`, the live website count no longer equals the screenshot count of 526. Confirm the page count manually, then rerun without `--require-expected-count` if the new count is expected.
- If it fails with `above max-open-count`, the query became too broad. Do not raise the cap until the `Filter Candidates` sheet or logs prove that the selected query is still Open-only.
- If rows are missing, inspect the `Errors` sheet for `non_open_record_skipped` or `empty_page`.
- If `view_opportunity_url` values are missing, inspect whether `internalid` or `version` is missing in the `Contracts` sheet.
- If field names change, inspect the `extra_json` column before changing `RECORD_COLUMNS`.

## Important Constraint

This scraper treats the target page as the source of truth. It captures only the Open records exposed by `AllOpportunities.jsp` through its own `solrconnect.jsp` endpoint. It derives View Opportunity links from list-row fields but does not enrich records from detail pages or from the separate eVA contract transparency search.
